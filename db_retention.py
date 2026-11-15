#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
db_retention.py —— Supabase 容量保留期命令列工具（預設乾跑，不連線、不刪除）。

背景（2026-11-15 唯讀數據）：資料庫 407MB，免費方案 500MB，超過即唯讀。
本工具涵蓋三類動作（TableSpec.action）：
  prune          依保留天數清舊列：inst_holding、broker_flows、twse_market_snapshot、market_signal_snapshot、
                 telegram_inbox、telegram_inbox_images、manual_feed_batches、big_holder_weekly（日期欄位皆有倉庫或唯讀 SQL 證據）
  export_only    backup_* 備份表：只整張匯出並校驗，「不 DROP」（表名內日期距今 ≥ BACKUP_MIN_AGE_DAYS 才處理）；
                 備份表由使用者自行在 Supabase SQL 編輯器手動 DROP（見 docs/DB_RETENTION.md）
  od_slim_report official_open_data 欄位瘦身：只產生乾跑報告，不做任何修改（刪除仍由 od_retention.py 負責）

用法：
  python db_retention.py [--today YYYY-MM-DD] [--stats f.json]   乾跑（預設，不連線）
  python db_retention.py --od-slim-report                         只印 official_open_data 瘦身報告
  python db_retention.py --export [--export-dir D]                 唯讀匯出本機 gzip 副本（選用）
  RETENTION_CONFIRM=I_HAVE_EXPORTED python db_retention.py --execute
      → 逐表：①匯出到 Supabase Storage 私有 bucket（db_archive.py：Parquet/jsonl.gz、分片、sha256 回讀校驗）
              ②校驗通過 ③刪除前再核對列數 ④才刪除（prune 表）；備份表只匯出、不 DROP。任一步失敗 → 該表不動。
      Storage 設定缺漏時整體拒絕。適合在 GitHub Actions 單一步驟內執行（.github/workflows/db_retention.yml）。

安全原則（專案死規則：資料完整優先，刪除前必須先匯出並驗證）：
  - 日期欄位未查到的表標示 UNKNOWN，一律排除在執行清單外。
  - 保留期 None（待使用者決定）的表不進執行清單。
  - 本工具不 DROP 任何表、不呼叫任何 RPC；備份表只匯出，DROP 由使用者手動執行。
  - 任何表保留期不得少於 MIN_KEEP_DAYS 天。
  - 測試時不會呼叫 _make_client()，也不會呼叫任何寫入或刪除方法。
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import db_archive
import db_maintenance

MIN_KEEP_DAYS = 7
BACKUP_MIN_AGE_DAYS = 30        # backup_* 表名結尾日期距今至少 30 天才匯出（不 DROP）
_BACKUP_RE = re.compile(r"^backup_[a-z0-9_]+_(\d{8})$")
CONFIRM_ENV = "RETENTION_CONFIRM"
CONFIRM_VALUE = "I_HAVE_EXPORTED"
PAGE = 1000
MAX_EXPORT_ROWS = 400000       # 單表單次匯出列數上限（記憶體保護）；超過就中止，請用 --tables 分批或先調整保留期
TZ_TAIPEI = timezone(timedelta(hours=8))


@dataclass(frozen=True)
class TableSpec:
    name: str
    date_col: Optional[str]          # None = UNKNOWN（倉庫中查不到日期欄位）
    keep_days: Optional[int]         # None = 待使用者決定，不進執行清單
    size_mb: float                   # 2026-11-15 背景數據（唯讀）
    rows: Optional[int]              # 2026-11-15 背景數據（約略）
    category: str                    # hot-snapshot / market-history / removed-feature / backup
    source: str                      # 日期欄位的證據位置（檔案:行）
    managed_by: Optional[str] = None # 已有其他模組負責者，本工具排除
    note: str = ""
    action: str = "prune"            # prune / export_only / od_slim_report


# 日期欄位只採信倉庫內的證據（grep 結果）。keep_days 有既存設定者沿用 db_maintenance.py。
TABLES: list[TableSpec] = [
    TableSpec("inst_holding", "date", db_maintenance.INST_HOLDING_KEEP_DAYS, 80, 159000, "hot-snapshot",
              "dashangdao.py:3466；dashangdao_helpers.py:3488 on_conflict=date,symbol",
              note="保留 90 天（2026-11-15 使用者核准，180→90），沿用 db_maintenance.py"),
    TableSpec("broker_flows", "log_date", db_maintenance.BROKER_FLOWS_KEEP_DAYS, 73, 309000, "hot-snapshot",
              "dashangdao.py:1271-1275 on_conflict=symbol,log_date,broker_name",
              note="保留 60 天（2026-11-15 使用者核准，90→60），沿用 db_maintenance.py"),
    TableSpec("etf_price_daily", "trade_date", None, 33, None, "market-history",
              "supabase_migration_etf_price_daily.sql:5",
              note="組合回測仰賴長期價格（etf_tab.py:979），保留期待決定"),
    TableSpec("official_open_data", "data_date", None, 31, 59000, "market-history",
              "supabase_migration_official_open_data.sql:5（text 型別）",
              managed_by="od_retention.py", action="od_slim_report",
              note="已有 od_retention.py 分級保留＋封存；本工具只產生欄位瘦身乾跑報告（見 OD_SLIM_BASELINE）"),
    TableSpec("twse_market_snapshot", "trade_date", 400, 26, 107000, "market-history",
              "backfill_twse_snapshot.py:72、94 on_conflict=trade_date,symbol",
              note="保留 400 天沿用 db_maintenance.py"),
    TableSpec("big_holder_weekly", "week_date", db_maintenance.BIG_HOLDER_WEEKLY_KEEP_DAYS, 19, None, "market-history",
              "dashangdao_helpers.py:3979 on_conflict=symbol,week_date",
              note="週資料，保留 26 週＝182 天（2026-11-15 使用者核准）；穩定排序 week_date,id（PK=id）"),
    TableSpec("mops_financial_snapshot", None, None, 15, None, "market-history",
              "UNKNOWN：寫入鍵為 symbol/year_roc/season（dashangdao.py:3399），無日曆日期欄位",
              note="需保留 10 季歷史（dashangdao_helpers.py:2737），不可用日期裁切"),
    TableSpec("market_signal_snapshot", "trade_date", 60, 14, None, "hot-snapshot",
              "system_scheduler.py:8668-8669 on_conflict=trade_date,symbol",
              note="保留 60 天沿用 db_maintenance.py；回測若需更長期需先決定"),
    TableSpec("backup_holiday_derived_20261014", None, None, 4.2, 9685, "backup",
              "2026-11-15 唯讀 SQL：表存在、無 PK、無保留用日期欄位；倉庫內無參照", action="export_only",
              note="整表只匯出校驗、不 DROP（使用者手動 DROP）；表名日期距今須 ≥ 30 天"),
    TableSpec("backup_holiday_intraday_bars_20261005", None, None, 0.07, 333, "backup",
              "2026-11-15 唯讀 SQL：表存在、無 PK、無保留用日期欄位；倉庫內無參照", action="export_only",
              note="整表只匯出校驗、不 DROP（使用者手動 DROP）；表名日期距今須 ≥ 30 天"),
    TableSpec("backup_holiday_twse_snapshot_20261005", None, None, 0.47, 3264, "backup",
              "2026-11-15 唯讀 SQL：表存在、無 PK、無保留用日期欄位；倉庫內無參照", action="export_only",
              note="整表只匯出校驗、不 DROP（使用者手動 DROP）；表名日期距今須 ≥ 30 天"),
    TableSpec("backup_instant_exit_artifacts_20261005", None, None, 0.5, 596, "backup",
              "2026-11-15 唯讀 SQL：表存在、無 PK、無保留用日期欄位；倉庫內無參照", action="export_only",
              note="整表只匯出校驗、不 DROP（使用者手動 DROP）；表名日期距今須 ≥ 30 天"),
    TableSpec("telegram_inbox", "created_at", 30, 0.06, 0, "removed-feature",
              "supabase_migration_telegram_inbox.sql:14（索引欄位）；2026-11-15 唯讀 SQL 確認 0 列",
              note="功能已移除；保留 30 天，清舊列（計畫檔沿用 30 天）"),
    TableSpec("telegram_inbox_images", "created_at", 30, 0.05, 0, "removed-feature",
              "supabase_migration_telegram_inbox_photo.sql:13；2026-11-15 唯讀 SQL 確認 0 列",
              note="含 base64 圖片，清舊列前先匯出"),
    TableSpec("manual_feed_batches", "created_at", 30, 0.02, 0, "removed-feature",
              "2026-11-15 唯讀 SQL（information_schema）：有 created_at timestamptz；倉庫內無參照；0 列",
              note="保留 30 天清舊列"),
]


def cutoff_for(keep_days: int, today: date) -> str:
    """保留 keep_days 天：回傳 YYYY-MM-DD，日期小於它的列可封存。keep_days 低於 MIN_KEEP_DAYS 時套用下限。"""
    if keep_days is None:
        raise ValueError("keep_days 為 None（待決定），無法計算截止日")
    days = max(int(keep_days), MIN_KEEP_DAYS)
    return (today - timedelta(days=days)).isoformat()


def backup_age_days(name: str, today: date) -> Optional[int]:
    """backup_* 表名結尾 YYYYMMDD 距今天數；格式不符回 None。"""
    m = _BACKUP_RE.match(name)
    if not m:
        return None
    try:
        d = datetime.strptime(m.group(1), "%Y%m%d").date()
    except ValueError:
        return None
    return (today - d).days


def exec_eligible(spec: TableSpec, today: Optional[date] = None) -> tuple[bool, str]:
    """是否進入執行清單，以及不進入的原因。"""
    if spec.managed_by:
        return False, f"由 {spec.managed_by} 負責"
    if spec.action == "export_only":
        age = backup_age_days(spec.name, today or datetime.now(TZ_TAIPEI).date())
        if age is None:
            return False, "表名無可解析的日期，不處理"
        if age < BACKUP_MIN_AGE_DAYS:
            return False, f"備份僅 {age} 天，未滿 {BACKUP_MIN_AGE_DAYS} 天"
        return True, ""
    if spec.date_col is None:
        return False, "UNKNOWN 日期欄位"
    if spec.keep_days is None:
        return False, "保留期待使用者決定"
    return True, ""


def estimate_freed_bytes(bytes_total: int, rows_total: int, rows_before_cutoff: int) -> int:
    """依列數比例估算可釋放位元組（粗估，假設列的大小大致均勻）。"""
    if rows_total <= 0 or rows_before_cutoff <= 0:
        return 0
    rows_before_cutoff = min(rows_before_cutoff, rows_total)
    return int(bytes_total * rows_before_cutoff / rows_total)


def sql_estimate_lines(spec: TableSpec, cutoff: str) -> list[str]:
    """只產生 SQL 字串，供使用者自行複製到 Supabase SQL 編輯器；本工具不執行。"""
    t = f"public.{spec.name}"
    if spec.action == "export_only":
        return [f"SELECT count(*) AS total FROM {t};",
                f"SELECT pg_size_pretty(pg_total_relation_size('{t}'));"]
    c = spec.date_col or "<UNKNOWN>"
    return [
        f"SELECT count(*) AS total, min({c}) AS oldest, max({c}) AS newest FROM {t};",
        f"SELECT count(*) AS before_cutoff FROM {t} WHERE {c} < '{cutoff}';",
        f"SELECT pg_size_pretty(pg_total_relation_size('{t}'));",
    ]


def load_stats(path: Optional[str]) -> dict:
    """統計檔格式：{表名: {"rows": int, "rows_before_cutoff": int, "bytes": int}}，由使用者自行查詢後填入。"""
    if not path:
        return {}
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("stats 檔必須是 JSON 物件")
    return data


def plan(today: date, stats: dict, tables: Optional[list[str]] = None) -> list[dict]:
    """組出每張表的計畫（純計算，不連線）。"""
    out = []
    for spec in TABLES:
        if tables and spec.name not in tables:
            continue
        ok, why = exec_eligible(spec, today)
        item = {"name": spec.name, "date_col": spec.date_col, "keep_days": spec.keep_days,
                "eligible": ok, "reason": why, "cutoff": None, "freed_bytes": None,
                "category": spec.category, "source": spec.source, "action": spec.action}
        if spec.keep_days is not None and spec.date_col is not None:
            item["cutoff"] = cutoff_for(spec.keep_days, today)
        s = stats.get(spec.name)
        if s and ok and spec.action == "export_only":
            item["freed_bytes"] = int(s.get("bytes", 0))      # 使用者手動 DROP 後才會釋放整張表
        elif s and item["cutoff"]:
            total_bytes = int(s.get("bytes", 0))
            item["freed_bytes"] = estimate_freed_bytes(
                total_bytes, int(s.get("rows", 0)), int(s.get("rows_before_cutoff", 0)))
        out.append(item)
    return out


def _fmt_mb(b: Optional[int]) -> str:
    return "N/A" if b is None else f"{b / 1024 / 1024:.1f}MB"


def print_dry_run(today: date, stats: dict) -> None:
    print(f"[db_retention] 乾跑 today={today.isoformat()}（不連線、不寫入、不刪除）")
    total_freed = 0
    for it in plan(today, stats):
        flag = (f"執行清單[{it['action']}]" if it["eligible"] else f"排除（{it['reason']}）")
        print(f"- {it['name']} | 日期欄位={it['date_col'] or 'UNKNOWN'} | 保留={it['keep_days']} | "
              f"截止={it['cutoff'] or 'N/A'} | {flag} | 預估釋放={_fmt_mb(it['freed_bytes'])}")
        print(f"    證據：{it['source']}")
        if it["cutoff"] and it["date_col"] or (it["eligible"] and it["action"] == "export_only"):
            for line in sql_estimate_lines(next(s for s in TABLES if s.name == it["name"]), it["cutoff"]):
                print(f"    SQL（僅供複製，未執行）：{line}")
        if it["eligible"] and it["freed_bytes"]:
            total_freed += it["freed_bytes"]
    print(f"合計預估釋放（僅執行清單、需 --stats）：{_fmt_mb(total_freed)}")
    print("備註：備份表（backup_*）只匯出並校驗、不會被刪除；刪表請由使用者自行處理（見 docs/DB_RETENTION.md）。")
    print()
    print_od_slim_report()


# ---------- official_open_data 欄位瘦身：只產生乾跑報告（不修改任何資料或程式） ----------

# 2026-11-15 唯讀 SQL 實測（pg_column_size）。61,945 列、表 32MB（heap 20MB、索引 10MB）。
OD_SLIM_BASELINE = {
    "rows": 61945,
    "source_url_bytes": 3546760,          # 21 種不同 URL、平均 56 字元，每列各存一份
    "source_url_distinct": 21,
    "payload_bytes_all": 11866000,        # 全部 dataset payload 合計（約值，含 sitca_trust_hold 約 1.0MB）
    "const_market_key_bytes": 198081 + 162669 + 137800,   # margin_otc／qfii_otc／sbl_otc 的 "market" 值（逐列重複）
    "material_news_items_bytes": 843492,  # 內容資料，不屬瘦身範圍
}

OD_SLIM_CANDIDATES = [
    ("source_url 去重", "source_url_bytes",
     "每列各存一份，但全表只有 21 種 URL → 可改存「dataset→URL」對照（config 或小表），列內留空或存短代碼。"
     "風險：讀取端若直接用 source_url 顯示出處，需同步改。"),
    ("payload.market 常數鍵", "const_market_key_bytes",
     "margin_otc／qfii_otc／sbl_otc 每列都存 market（值幾乎固定）→ 可由 symbol 所屬市場推導。風險：要先確認讀取端沒用此鍵。"),
]
OD_SLIM_VERIFY_SQL = [
    "SELECT dataset, count(*) rows, sum(pg_column_size(payload)) payload_bytes FROM public.official_open_data GROUP BY 1 ORDER BY 3 DESC;",
    "SELECT count(*) rows, count(DISTINCT source_url) distinct_urls, sum(pg_column_size(source_url)) url_bytes FROM public.official_open_data;",
    "SELECT dataset, count(DISTINCT payload->>'market') FROM public.official_open_data WHERE payload ? 'market' GROUP BY 1;",
]


def od_slim_report(baseline: Optional[dict] = None) -> list[str]:
    """回傳報告行（純計算）。上限估計＝各候選位元組加總；實際釋放需 VACUUM FULL 後才反映到磁碟。"""
    b = baseline or OD_SLIM_BASELINE
    lines = ["[official_open_data 欄位瘦身乾跑報告] 僅報告，不修改資料、不修改程式"]
    total = 0
    for name, key, why in OD_SLIM_CANDIDATES:
        n = int(b.get(key, 0))
        total += n
        lines.append(f"- {name}：可省約 {_fmt_mb(n)}｜{why}")
    pct = total / (500 * 1024 * 1024) * 100
    lines.append(f"合計上限約 {_fmt_mb(total)}（約佔 500MB 額度 {pct:.1f}%）；"
                 "改寫列後空間仍需 VACUUM FULL 才會還給系統，且會鎖表。內容類欄位（如 material_news.items）不在瘦身範圍。")
    lines.append("驗證用 SQL（僅供複製，未執行）：")
    lines += [f"    {q}" for q in OD_SLIM_VERIFY_SQL]
    return lines


def print_od_slim_report() -> None:
    for line in od_slim_report():
        print(line)


# ---------- 以下為需要 Supabase client 的路徑（測試不會走到） ----------

def _make_client():
    """讀環境變數建立 client。不印出任何金鑰值。"""
    from supabase import create_client  # 延遲匯入
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def export_table(client, spec: TableSpec, cutoff: str, out_dir: Path) -> dict:
    """唯讀：把 date_col < cutoff 的列分頁讀出，寫成 gzip JSONL，並以 count=exact 對帳。"""
    rows_written = 0
    path = out_dir / f"{spec.name}.before_{cutoff}.jsonl.gz"
    with gzip.open(path, "wt", encoding="utf-8") as gz:
        start = 0
        while True:
            batch = (client.table(spec.name).select("*").lt(spec.date_col, cutoff)
                     .order(spec.date_col).range(start, start + PAGE - 1).execute().data or [])
            for r in batch:
                gz.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
            rows_written += len(batch)
            if len(batch) < PAGE:
                break
            start += PAGE
    expected = (client.table(spec.name).select("*", count="exact")
                .lt(spec.date_col, cutoff).limit(1).execute().count)
    if expected != rows_written:
        raise RuntimeError(f"{spec.name} 匯出列數 {rows_written} 與伺服器計數 {expected} 不符，中止")
    return {"table": spec.name, "cutoff": cutoff, "rows": rows_written,
            "file": path.name, "sha256": _sha256(path)}


def delete_table_rows(client, spec: TableSpec, cutoff: str, expected_rows: int) -> int:
    """真正刪除。刪除前再次對帳：仍在截止日前的列數必須等於已驗證的匯出列數，否則不刪。"""
    now_count = db_archive.server_count(client, spec.name, spec.date_col, cutoff)
    if now_count != expected_rows:
        raise RuntimeError(f"{spec.name} 刪除前列數 {now_count} != 已驗證 {expected_rows}，中止")
    client.table(spec.name).delete().lt(spec.date_col, cutoff).execute()
    return now_count


def run_export(today: date, out_dir: Path, tables: Optional[list[str]] = None) -> dict:
    """選用：唯讀匯出本機 gzip 副本（與 --execute 的 Storage 匯出無關）。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    client = _make_client()
    manifest = {"created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "entries": []}
    for it in plan(today, {}, tables):
        if not it["eligible"] or it["action"] != "prune":
            continue
        spec = next(s for s in TABLES if s.name == it["name"])
        entry = export_table(client, spec, it["cutoff"], out_dir)
        manifest["entries"].append(entry)
        print(f"[匯出] {spec.name} 截止 {it['cutoff']} 列數 {entry['rows']}")
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def execute_item(client, store, it: dict) -> int:
    """單一表：匯出到 Storage（校驗）→ 刪前對帳 → 刪除（備份表只匯出）。回傳處理列數；任何失敗丟例外（該表不動）。"""
    spec = next(s for s in TABLES if s.name == it["name"])
    col = spec.date_col if it["action"] == "prune" else None
    bound = it["cutoff"] if it["action"] == "prune" else None
    res = db_archive.archive_before(client, store, spec.name, col, bound, max_rows=MAX_EXPORT_ROWS)
    if not res.get("verified"):
        raise RuntimeError("匯出未通過校驗")
    print(f"[匯出] {spec.name} {res['rows']} 列 → Storage {res.get('prefix')}（{res.get('format')}，{res['parts']} 片）")
    if it["action"] == "export_only":
        return res["rows"]      # 備份表：只匯出並校驗，不 DROP
    if res["rows"] == 0:
        return 0
    return delete_table_rows(client, spec, bound, res["rows"])


def run_execute(today: date, tables: Optional[list[str]] = None, client=None, store=None) -> int:
    """前提已在 main() 檢查：環境變數正確。先確認 Storage 可用（否則整體拒絕），再逐表處理，單表失敗不影響其他表。"""
    try:
        store = store or db_archive.storage_from_env()
    except Exception as e:  # noqa: BLE001
        print(f"拒絕：Storage 不可用，無法先匯出：{type(e).__name__}: {str(e)[:100]}")
        return 2
    client = client or _make_client()
    rc = 0
    for it in plan(today, {}, tables):
        if not it["eligible"] or it["action"] not in ("prune", "export_only"):
            continue
        try:
            n = execute_item(client, store, it)
            if it["action"] == "export_only":
                print(f"[完成] {it['name']} 已匯出並校驗 {n} 列；本工具不 DROP，"
                      f"請自行在 Supabase SQL 編輯器手動 DROP：DROP TABLE IF EXISTS public.{it['name']};")
            else:
                print(f"[完成] {it['name']} 刪除（截止 {it['cutoff']}）{n} 列")
        except Exception as e:  # noqa: BLE001  匯出失敗／校驗失敗／對帳不符 → 該表不動
            print(f"[中止] {it['name']}：{type(e).__name__}: {str(e)[:160]}")
            rc = 1
    return rc


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Supabase 保留期工具（預設乾跑）")
    ap.add_argument("--today", help="以此日期為今天（YYYY-MM-DD），預設台北時間今天")
    ap.add_argument("--stats", help="使用者自行查詢後填入的統計 JSON，用來估算釋放空間")
    ap.add_argument("--tables", help="逗號分隔表名，只處理這些表")
    ap.add_argument("--od-slim-report", action="store_true", help="只印 official_open_data 欄位瘦身乾跑報告")
    ap.add_argument("--export", action="store_true", help="唯讀匯出待清理列到本機目錄（選用，需 SUPABASE 環境變數）")
    ap.add_argument("--execute", action="store_true",
                    help="真的處理：先匯出到 Storage 並校驗，才刪除（需 RETENTION_CONFIRM=I_HAVE_EXPORTED）")
    ap.add_argument("--export-dir", default=os.path.expanduser("~/db_retention_exports"),
                    help="--export 的本機目錄，請放在倉庫之外")
    args = ap.parse_args(argv)

    today = date.fromisoformat(args.today) if args.today else datetime.now(TZ_TAIPEI).date()
    tables = [t.strip() for t in args.tables.split(",")] if args.tables else None

    if args.od_slim_report:
        print_od_slim_report()
        return 0
    if args.execute:
        if os.environ.get(CONFIRM_ENV) != CONFIRM_VALUE:
            print(f"拒絕執行：未設定環境變數 {CONFIRM_ENV}={CONFIRM_VALUE}。")
            return 2
        return run_execute(today, tables)
    if args.export:
        run_export(today, Path(args.export_dir), tables)
        return 0
    print_dry_run(today, load_stats(args.stats))
    return 0


if __name__ == "__main__":
    sys.exit(main())
