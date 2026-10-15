"""【2026-10-15】測試殘留資料清理：批次處理（全選／刪除勾選／忽略勾選）的純邏輯。

規則：
- 表名只接受清理排程自己會標記的那幾張（白名單），其他一律拒絕，不碰。
- 刪除的範圍跟原本單筆「確認刪除」完全一樣：該表在該 trade_date 的資料；刪完才把 cleanup_flags 標成 deleted。
- 逐筆處理、逐筆回報成功／失敗；某一筆失敗不影響其他筆。
"""
from datetime import datetime, timezone

ALLOWED_TABLES = frozenset({
    "smart_money_candidates", "route2_watchlist", "twse_market_snapshot",
    "intraday_candidate_pool", "intraday_gate_results", "intraday_5min_bars",
})


def _now():
    return datetime.now(timezone.utc).isoformat()


def delete_flagged(conn, flags):
    """永久刪除勾選項目的資料並把旗標標成 deleted。回傳 (成功清單, 失敗清單[(flag, 原因)])。"""
    ok, bad = [], []
    for f in flags:
        table, tdate = f.get("table_name"), f.get("trade_date")
        if table not in ALLOWED_TABLES:
            bad.append((f, f"資料表 {table!r} 不在可清理清單，已拒絕"))
            continue
        if not tdate:
            bad.append((f, "缺少 trade_date，已拒絕"))
            continue
        try:
            conn.table(table).delete().eq("trade_date", tdate).execute()
            conn.table("cleanup_flags").update(
                {"status": "deleted", "resolved_at": _now()}).eq("id", f["id"]).execute()
            ok.append(f)
        except Exception as e:  # noqa: BLE001
            bad.append((f, f"{type(e).__name__}: {str(e)[:120]}"))
    return ok, bad


def dismiss_flagged(conn, flags):
    """把勾選項目標成已看過（資料保留）。回傳 (成功清單, 失敗清單)。"""
    ok, bad = [], []
    for f in flags:
        try:
            conn.table("cleanup_flags").update(
                {"status": "dismissed", "resolved_at": _now()}).eq("id", f["id"]).execute()
            ok.append(f)
        except Exception as e:  # noqa: BLE001
            bad.append((f, f"{type(e).__name__}: {str(e)[:120]}"))
    return ok, bad
