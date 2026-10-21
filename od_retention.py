"""【2026-10-21 老闆：official_open_data 保留期】分級保留期 ＋ 先封存到 Supabase Storage 私有桶再刪除（純函式＋假 sb 可離線測試）。

設計：
- 只動名單內的資料集（OD_RETENTION）；sitca_trust_hold（官方季資料，永久）與名單外資料集一律不動。
- 每個資料集永遠保留「最新一個資料日」（讀取端只拿最新值；同步中斷時警示不會憑空消失）。
- 刪除前：逐日完整讀出（分頁、與 count 對帳）→ CSV gzip → 上傳 archive 桶 → 下載回來比 sha256 與列數 → 全部一致才刪。
- 任一步驟不一致就不刪、回報原因（死規則 1：驗證不過就退回）。
- dry_run 只數數量、不寫不刪。每次每資料集最多處理 MAX_DAYS 個資料日，積壓分幾天清完。
"""
from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
from datetime import datetime, timedelta

TABLE = "official_open_data"
BUCKET = "archive"
MIN_KEEP_DAYS = 7
MAX_DAYS = 40
PAGE = 1000   # PostgREST 單次回傳上限 1000 列
OD_RETENTION = {
    # 每檔每天一列、量最大、讀取端只看最新值
    "pledge": 30, "qfii_otc": 20, "sbl_otc": 20,
    "margin_otc": 45,                      # 融資趨勢讀最近 30 筆
    "daytrade_flag": 60, "short_suspend": 60, "trade_status": 60, "attention_note": 60,
    "material_news": 60, "insider_transfer": 60,
    "trust_hold_est": 90,                  # 之後拿來對照下一季 SITCA
    "exdiv_upcoming": 30,                  # 只刪已過期；未來日期不動
    # 整體市場指標：一天不到 15 列，回測要用
    "mkt_total_inst": 400, "mkt_opt_inst": 400, "mkt_fut_inst": 400, "mkt_total_margin": 400, "mkt_pc_ratio": 400,
}
KEEP_FOREVER = ("sitca_trust_hold",)
COLS = ["dataset", "data_date", "symbol", "payload", "source_url"]


def cutoff_for(days, today=None):
    today = today or (datetime.utcnow() + timedelta(hours=8))
    days = max(int(days), MIN_KEEP_DAYS)
    return (today - timedelta(days=days)).strftime("%Y-%m-%d")


def _first(sb, ds, desc=False):
    r = (sb.table(TABLE).select("data_date").eq("dataset", ds).order("data_date", desc=desc).limit(1).execute().data) or []
    return str(r[0]["data_date"])[:10] if r else None


def _count(sb, ds, day):
    return sb.table(TABLE).select("id", count="exact").eq("dataset", ds).eq("data_date", day).limit(1).execute().count


def _read_day(sb, ds, day):
    rows, off = [], 0
    while True:
        part = (sb.table(TABLE).select(",".join(COLS)).eq("dataset", ds).eq("data_date", day)
                .order("symbol").range(off, off + PAGE - 1).execute().data) or []
        rows += part
        if len(part) < PAGE:
            break
        off += PAGE
    return rows


def _to_csv_gz(rows):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(COLS)
    for r in rows:
        pl = r.get("payload")
        w.writerow([r.get("dataset"), str(r.get("data_date"))[:10], r.get("symbol"),
                    pl if isinstance(pl, str) else json.dumps(pl, ensure_ascii=False, separators=(",", ":")), r.get("source_url")])
    raw = buf.getvalue().encode("utf-8")
    return gzip.compress(raw, 9)


def _ensure_bucket(sb):
    try:
        sb.storage.create_bucket(BUCKET, options={"public": False})
    except Exception:  # noqa: BLE001  已存在也會到這裡
        pass


def prune_dataset(sb, ds, days, today=None, dry_run=False, max_days=MAX_DAYS):
    """回傳 {"dataset","cutoff","days","rows","archived","verified","deleted_days","error"}。不拋例外。"""
    out = {"dataset": ds, "days": 0, "rows": 0, "archived": None, "verified": None, "deleted_days": 0, "error": None, "dry_run": dry_run}
    try:
        if ds in KEEP_FOREVER:
            out["error"] = "永久保留，拒絕處理"
            return out
        cutoff = cutoff_for(days, today)
        latest = _first(sb, ds, desc=True)
        if latest is None:
            return out
        cutoff = min(cutoff, latest)           # 永遠保留最新一個資料日
        out["cutoff"] = cutoff
        # 找出要處理的資料日（由舊到新，每次只問「目前最舊的一天」）
        day_rows, seen = {}, set()
        cur = _first(sb, ds)
        while cur and cur < cutoff and len(day_rows) < max_days and cur not in seen:
            seen.add(cur)
            n = _count(sb, ds, cur)
            if dry_run:
                day_rows[cur] = n or 0
                # dry-run 不逐日走完（不刪就取不到「下一天」）：只回報最舊一天與其列數
                break
            rows = _read_day(sb, ds, cur)
            if len(rows) != n:
                out["error"] = f"{cur} 讀出 {len(rows)} 列與 count {n} 不符，停止"
                return out
            day_rows[cur] = rows
            # 讀完這天先不刪，改用「游標」找下一個更晚的資料日
            nxt = (sb.table(TABLE).select("data_date").eq("dataset", ds).gt("data_date", cur)
                   .order("data_date").limit(1).execute().data) or []
            cur = str(nxt[0]["data_date"])[:10] if nxt else None
        if not day_rows:
            return out
        out["days"] = len(day_rows)
        if dry_run:
            out["rows"] = sum(day_rows.values())
            return out
        allrows = [r for d in sorted(day_rows) for r in day_rows[d]]
        out["rows"] = len(allrows)
        first, last = min(day_rows), max(day_rows)
        gz = _to_csv_gz(allrows)
        sha = hashlib.sha256(gz).hexdigest()
        name = f"official_open_data/{ds}/{first}_{last}.csv.gz"
        _ensure_bucket(sb)
        sb.storage.from_(BUCKET).upload(name, gz, {"content-type": "application/gzip", "upsert": "true"})
        back = sb.storage.from_(BUCKET).download(name)
        rows_back = sum(1 for _ in csv.reader(io.StringIO(gzip.decompress(back).decode("utf-8")))) - 1
        ok = hashlib.sha256(back).hexdigest() == sha and rows_back == len(allrows)
        out.update(archived=name, verified=bool(ok), gz_bytes=len(gz))
        if not ok:
            out["error"] = "封存驗證不一致，不刪除"
            return out
        for d in sorted(day_rows):
            sb.table(TABLE).delete().eq("dataset", ds).eq("data_date", d).execute()
            out["deleted_days"] += 1
    except Exception as e:  # noqa: BLE001
        out["error"] = f"{type(e).__name__}: {str(e)[:160]}"
    return out


def run(sb, today=None, dry_run=False, retention=None):
    ret = retention or OD_RETENTION
    return [prune_dataset(sb, ds, d, today=today, dry_run=dry_run) for ds, d in ret.items()]


def summarize(results):
    done = [r for r in results if r.get("deleted_days")]
    errs = [r for r in results if r.get("error")]
    pend = [r for r in results if r.get("dry_run") and r.get("days")]
    parts = []
    if done:
        parts.append("封存並清理：" + "、".join(f"{r['dataset']}（{r['deleted_days']}天/{r['rows']}列）" for r in done))
    if pend:
        parts.append("待清（試跑）：" + "、".join(r["dataset"] for r in pend))
    if errs:
        parts.append("失敗：" + "、".join(f"{r['dataset']}（{r['error'][:50]}）" for r in errs[:4]))
    return "｜".join(parts)
