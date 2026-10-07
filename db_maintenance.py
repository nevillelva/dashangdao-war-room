"""
資料庫維護（2026-10-07）：保留期限 + 容量報告 + 休市日曆發布。

背景（Supabase 免費方案 500MB 上限，超過就唯讀、所有寫入失敗）。2026-10-07 查證的成長根因：
  1. overnight_scan_results（37MB／2 萬列）：每天把約 900 檔「整份評分卡 card_snapshot（約 1.4KB）」全存進去，
     但網頁只讀 symbol/scan_date/matched_commands/price/score/name、且畫面只顯示到「隔天 13:30」→ 寫入端已改存 7 欄摘要，並只留 14 天。
  2. broker_flows：每天約 6,600 列（228 檔×約 29 家券商，含 5 個索引約 250B/列），原設定保留 365 天 → 一年約 600MB，必爆。改保留 90 天。
  3. 其餘每日快照表（market_signal_snapshot、factor_snapshot、inst_holding、intraday_5min_bars…）完全沒有保留期限，只增不減。
  4. data_source_health_log：網頁端每次重新整理都寫一筆 Finnhub 健康紀錄（已節流 30 分鐘）。
本模組每天跟著 bundle_late 跑一次；刪除一律「一天一天」刪（避免單次刪太多逾時），每次最多處理 MAX_DAYS_PER_TABLE 天的積壓。
"""
import json
from datetime import datetime, timedelta

# (表名, 日期欄位, 保留天數)。日期欄位可為 date / 'YYYY-MM-DD' 文字 / timestamptz。
RETENTION = [
    ("overnight_scan_results", "scan_date", 14),
    ("data_source_health_log", "log_date", 30),
    ("perf_log", "created_at", 30),
    ("warcard_cache", "trade_date", 14),
    ("market_signal_snapshot", "trade_date", 60),
    ("factor_snapshot", "trade_date", 90),
    ("intraday_5min_bars", "trade_date", 45),
    ("intraday_gate_results", "trade_date", 180),
    ("route2_watchlist", "trade_date", 120),
    ("broker_flows", "log_date", 90),
    ("broker_style_daily", "log_date", 120),
    ("inst_holding", "date", 180),
    ("twse_market_snapshot", "trade_date", 400),
    ("system_run_log", "run_date", 365),
    ("ui_selftest_reports", "created_at", 45),
]
MIN_KEEP_DAYS = 7          # 防呆：任何表都不允許保留少於 7 天
MAX_DAYS_PER_TABLE = 40    # 每次最多清掉某表 40 天的積壓（首次上線會分幾天清完）
WARN_BYTES = 400 * 1024 * 1024
SOFT_LIMIT_BYTES = 500 * 1024 * 1024


def cutoff_for(days, today=None):
    """保留 days 天：回傳 YYYY-MM-DD，小於該日期的資料可刪。"""
    today = today or datetime.utcnow() + timedelta(hours=8)
    days = max(int(days), MIN_KEEP_DAYS)
    return (today - timedelta(days=days)).strftime("%Y-%m-%d")


def _next_day(day_s):
    return (datetime.strptime(day_s, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")


def prune_table(sb, table, col, days, today=None, dry_run=False, max_days=MAX_DAYS_PER_TABLE):
    """逐日刪除 col < cutoff 的資料。回傳 {"table","cutoff","days_cleaned","error"}。任何失敗只記錄、不拋例外。"""
    cutoff = cutoff_for(days, today)
    out = {"table": table, "cutoff": cutoff, "days_cleaned": 0, "error": None, "dry_run": dry_run}
    try:
        for _ in range(max_days):
            r = sb.table(table).select(col).order(col).limit(1).execute().data or []
            if not r:
                break
            oldest = str(r[0][col])[:10]
            if oldest >= cutoff:
                break
            bound = min(_next_day(oldest), cutoff)
            if bound <= oldest:
                break
            if not dry_run:
                sb.table(table).delete().lt(col, bound).execute()
            out["days_cleaned"] += 1
            if dry_run:
                break   # dry-run 只回報「還有積壓」，不逐日模擬
    except Exception as e:  # noqa: BLE001
        out["error"] = f"{type(e).__name__}: {str(e)[:160]}"
    return out


def run_retention(sb, today=None, dry_run=False):
    return [prune_table(sb, t, c, d, today=today, dry_run=dry_run) for t, c, d in RETENTION]


def size_report(sb):
    """呼叫 public.db_size_report()（SQL 函式）。回傳 dict 或 None（無權限/函式不存在）。"""
    try:
        res = sb.rpc("db_size_report", {}).execute()
        data = res.data
        if isinstance(data, str):
            data = json.loads(data)
        return data if isinstance(data, dict) else None
    except Exception as e:  # noqa: BLE001
        print(f"[DB維護] 讀取資料庫容量失敗：{type(e).__name__}: {str(e)[:120]}")
        return None


def fmt_mb(b):
    return f"{(b or 0) / 1024 / 1024:.0f}MB"


def build_report(rep, prune_results, today=None):
    """組出給 Telegram／system_run_log 的文字。回傳 (text, level)，level ∈ ok/warn/critical。"""
    lines = []
    level = "ok"
    if rep and rep.get("db_bytes"):
        b = int(rep["db_bytes"])
        pct = b / SOFT_LIMIT_BYTES * 100
        if b >= SOFT_LIMIT_BYTES * 0.9:
            level = "critical"
        elif b >= WARN_BYTES:
            level = "warn"
        lines.append(f"💾 資料庫 {fmt_mb(b)} / 500MB（{pct:.0f}%）")
        top = (rep.get("tables") or [])[:6]
        if top:
            lines.append("最大表：" + "、".join(f"{t['t']} {fmt_mb(t['bytes'])}" for t in top))
    cleaned = [r for r in prune_results if r.get("days_cleaned")]
    errs = [r for r in prune_results if r.get("error")]
    if cleaned:
        lines.append("🧹 保留期限清理：" + "、".join(f"{r['table']}（{r['days_cleaned']}天）" for r in cleaned))
    if errs:
        lines.append("⚠️ 清理失敗：" + "、".join(f"{r['table']}（{r['error'][:60]}）" for r in errs[:4]))
    return "\n".join(lines), level
