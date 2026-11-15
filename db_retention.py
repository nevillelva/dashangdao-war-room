#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
db_retention.py —— Supabase 容量保留期命令列工具（預設乾跑，不連線、不刪除）。

背景（2026-11-15 唯讀數據）：資料庫 407MB，免費方案 500MB，超過即唯讀。
本工具只做三件事：
  1. 依 TABLES 常數表算出每張表的保留截止日（cutoff）與可封存範圍。
  2. 產生 SQL 估算語句「字串」（本工具不執行），並可依 --stats 檔估算可釋放空間。
  3. --export 把待清理列匯出到本機（唯讀）；--execute 必須同時：
       - 環境變數 RETENTION_CONFIRM=I_HAVE_EXPORTED
       - 匯出 manifest 存在，且匯出檔 sha256 與列數都對得上
     才會真的刪除。刪除前會再次對帳，不一致就中止該表。

安全原則（專案死規則：資料完整優先，刪除前必須先匯出並驗證）：
  - 日期欄位未在倉庫中查到的表標示 UNKNOWN，一律排除在執行清單外。
  - 保留期 None（待使用者決定）的表不進執行清單。
  - 備份表（backup_*）不在本工具處理範圍；DROP 只寫在 supabase_migration_retention_plan.sql（全部註解）。
  - 任何表保留期不得少於 MIN_KEEP_DAYS 天。
  - 測試時不會呼叫 _make_client()，也不會呼叫任何寫入或刪除方法。
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

MIN_KEEP_DAYS = 7
CONFIRM_ENV = "RETENTION_CONFIRM"
CONFIRM_VALUE = "I_HAVE_EXPORTED"
PAGE = 1000
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


# 日期欄位只採信倉庫內的證據（grep 結果）。keep_days 有既存設定者沿用 db_maintenance.py。
TABLES: list[TableSpec] = [
    TableSpec("inst_holding", "date", 180, 80, 159000, "hot-snapshot",
              "dashangdao.py:3466；dashangdao_helpers.py:3488 on_conflict=date,symbol",
              note="保留 180 天沿用 db_maintenance.py"),
    TableSpec("broker_flows", "log_date", 90, 73, 309000, "hot-snapshot",
              "dashangdao.py:1271-1275 on_conflict=symbol,log_date,broker_name",
              note="保留 90 天沿用 db_maintenance.py"),
    TableSpec("etf_price_daily", "trade_date", None, 33, None, "market-history",
              "supabase_migration_etf_price_daily.sql:5",
              note="組合回測仰賴長期價格（etf_tab.py:979），保留期待決定"),
    TableSpec("official_open_data", "data_date", None, 31, 59000, "market-history",
              "supabase_migration_official_open_data.sql:5（text 型別）",
              managed_by="od_retention.py", note="已有 od_retention.py 分級保留＋封存，本工具排除"),
    TableSpec("twse_market_snapshot", "trade_date", 400, 26, 107000, "market-history",
              "backfill_twse_snapshot.py:72、94 on_conflict=trade_date,symbol",
              note="保留 400 天沿用 db_maintenance.py"),
    TableSpec("big_holder_weekly", "week_date", None, 19, None, "market-history",
              "dashangdao_helpers.py:3979 on_conflict=symbol,week_date",
              note="週資料，保留期待決定"),
    TableSpec("mops_financial_snapshot", None, None, 15, None, "market-history",
              "UNKNOWN：寫入鍵為 symbol/year_roc/season（dashangdao.py:3399），無日曆日期欄位",
              note="需保留 10 季歷史（dashangdao_helpers.py:2737），不可用日期裁切"),
    TableSpec("market_signal_snapshot", "trade_date", 60, 14, None, "hot-snapshot",
              "system_scheduler.py:8668-8669 on_conflict=trade_date,symbol",
              note="保留 60 天沿用 db_maintenance.py；回測若需更長期需先決定"),
    TableSpec("backup_holiday_derived_20261014", None, None, 0, None, "backup",
              "UNKNOWN：倉庫內無此表參照"),
    TableSpec("backup_holiday_intraday_bars_20261005", None, None, 0, None, "backup",
              "UNKNOWN：倉庫內無此表參照"),
    TableSpec("backup_holiday_twse_snapshot_20261005", None, None, 0, None, "backup",
              "UNKNOWN：倉庫內無此表參照"),
    TableSpec("backup_instant_exit_artifacts_20261005", None, None, 0, None, "backup",
              "UNKNOWN：倉庫內無此表參照"),
    TableSpec("telegram_inbox", "created_at", None, 0, None, "removed-feature",
              "supabase_migration_telegram_inbox.sql:14（索引欄位）",
              note="功能已移除，是否刪除待使用者決定"),
    TableSpec("telegram_inbox_images", "created_at", None, 0, None, "removed-feature",
              "supabase_migration_telegram_inbox_photo.sql:13",
              note="功能已移除，是否刪除待使用者決定"),
    TableSpec("manual_feed_batches", None, None, 0, None, "removed-feature",
              "UNKNOWN：倉庫內無此表參照"),
]


def cutoff_for(keep_days: int, today: date) -> str:
    """保留 keep_days 天：回傳 YYYY-MM-DD，日期小於它的列可封存。keep_days 低於 MIN_KEEP_DAYS 時套用下限。"""
    if keep_days is None:
        raise ValueError("keep_days 為 None（待決定），無法計算截止日")
    days = max(int(keep_days), MIN_KEEP_DAYS)
    return (today - timedelta(days=days)).isoformat()


def exec_eligible(spec: TableSpec) -> tuple[bool, str]:
    """是否進入執行清單，以及不進入的原因。"""
    if spec.managed_by:
        return False, f"由 {spec.managed_by} 負責"
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
        ok, why = exec_eligible(spec)
        item = {"name": spec.name, "date_col": spec.date_col, "keep_days": spec.keep_days,
                "eligible": ok, "reason": why, "cutoff": None, "freed_bytes": None,
                "category": spec.category, "source": spec.source}
        if spec.keep_days is not None and spec.date_col is not None:
            item["cutoff"] = cutoff_for(spec.keep_days, today)
        s = stats.get(spec.name)
        if s and item["cutoff"]:
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
        flag = "執行清單" if it["eligible"] else f"排除（{it['reason']}）"
        print(f"- {it['name']} | 日期欄位={it['date_col'] or 'UNKNOWN'} | 保留={it['keep_days']} | "
              f"截止={it['cutoff'] or 'N/A'} | {flag} | 預估釋放={_fmt_mb(it['freed_bytes'])}")
        print(f"    證據：{it['source']}")
        if it["cutoff"] and it["date_col"]:
            for line in sql_estimate_lines(next(s for s in TABLES if s.name == it["name"]), it["cutoff"]):
                print(f"    SQL（僅供複製，未執行）：{line}")
        if it["eligible"] and it["freed_bytes"]:
            total_freed += it["freed_bytes"]
    print(f"合計預估釋放（僅執行清單、需 --stats）：{_fmt_mb(total_freed)}")


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


def verify_manifest_entry(out_dir: Path, entry: dict) -> None:
    """重新驗證匯出檔：sha256 與列數都要對得上，否則拋例外。"""
    path = out_dir / entry["file"]
    if not path.exists():
        raise RuntimeError(f"找不到匯出檔 {entry['file']}")
    if _sha256(path) != entry["sha256"]:
        raise RuntimeError(f"{entry['table']} 匯出檔 sha256 不符，中止")
    with gzip.open(path, "rt", encoding="utf-8") as gz:
        n = sum(1 for _ in gz)
    if n != entry["rows"]:
        raise RuntimeError(f"{entry['table']} 匯出檔列數 {n} 與 manifest {entry['rows']} 不符，中止")


def delete_table_rows(client, spec: TableSpec, cutoff: str, expected_rows: int) -> int:
    """真正刪除。刪除前再次對帳：仍在截止日前的列數必須等於已驗證的匯出列數，否則不刪。"""
    now_count = (client.table(spec.name).select("*", count="exact")
                 .lt(spec.date_col, cutoff).limit(1).execute().count)
    if now_count != expected_rows:
        raise RuntimeError(f"{spec.name} 刪除前列數 {now_count} != 已驗證 {expected_rows}，中止")
    client.table(spec.name).delete().lt(spec.date_col, cutoff).execute()
    return now_count


def run_export(today: date, out_dir: Path, tables: Optional[list[str]] = None) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    client = _make_client()
    manifest = {"created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "entries": []}
    for it in plan(today, {}, tables):
        if not it["eligible"]:
            continue
        spec = next(s for s in TABLES if s.name == it["name"])
        entry = export_table(client, spec, it["cutoff"], out_dir)
        manifest["entries"].append(entry)
        print(f"[匯出] {spec.name} 截止 {it['cutoff']} 列數 {entry['rows']}")
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def run_execute(today: date, out_dir: Path, tables: Optional[list[str]] = None) -> int:
    """前提已在 main() 檢查：環境變數正確。這裡再檢查 manifest 與匯出檔，再逐表刪除。"""
    mpath = out_dir / "manifest.json"
    if not mpath.exists():
        print("拒絕：找不到 manifest.json，請先用 --export 匯出並保存。")
        return 2
    manifest = json.loads(mpath.read_text(encoding="utf-8"))
    by_name = {e["table"]: e for e in manifest.get("entries", [])}
    client = _make_client()
    rc = 0
    for it in plan(today, {}, tables):
        if not it["eligible"]:
            continue
        entry = by_name.get(it["name"])
        if entry is None or entry["cutoff"] != it["cutoff"]:
            print(f"[略過] {it['name']}：manifest 無對應的匯出（截止日需為 {it['cutoff']}）")
            rc = 1
            continue
        try:
            verify_manifest_entry(out_dir, entry)
            spec = next(s for s in TABLES if s.name == it["name"])
            n = delete_table_rows(client, spec, it["cutoff"], entry["rows"])
            print(f"[刪除] {spec.name} 截止 {it['cutoff']} 已刪 {n} 列")
        except Exception as e:  # noqa: BLE001
            print(f"[中止] {it['name']}：{type(e).__name__}: {str(e)[:160]}")
            rc = 1
    return rc


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Supabase 保留期工具（預設乾跑）")
    ap.add_argument("--today", help="以此日期為今天（YYYY-MM-DD），預設台北時間今天")
    ap.add_argument("--stats", help="使用者自行查詢後填入的統計 JSON，用來估算釋放空間")
    ap.add_argument("--tables", help="逗號分隔表名，只處理這些表")
    ap.add_argument("--export", action="store_true", help="唯讀匯出待清理列到本機目錄（需 SUPABASE 環境變數）")
    ap.add_argument("--execute", action="store_true", help="真的刪除（需 RETENTION_CONFIRM=I_HAVE_EXPORTED 與 manifest）")
    ap.add_argument("--export-dir", default=os.path.expanduser("~/db_retention_exports"),
                    help="匯出目錄，請放在倉庫之外")
    args = ap.parse_args(argv)

    today = date.fromisoformat(args.today) if args.today else datetime.now(TZ_TAIPEI).date()
    tables = [t.strip() for t in args.tables.split(",")] if args.tables else None
    out_dir = Path(args.export_dir)

    if args.execute:
        if os.environ.get(CONFIRM_ENV) != CONFIRM_VALUE:
            print(f"拒絕執行：未設定環境變數 {CONFIRM_ENV}={CONFIRM_VALUE}。")
            return 2
        return run_execute(today, out_dir, tables)
    if args.export:
        run_export(today, out_dir, tables)
        return 0
    print_dry_run(today, load_stats(args.stats))
    return 0


if __name__ == "__main__":
    sys.exit(main())
