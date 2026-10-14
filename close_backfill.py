"""【2026-10-14】收盤價回補：證交所 OpenAPI STOCK_DAY_ALL 只有「最新交易日」，而且當天晚上 22:00 的選股同步抓到的常常還是前一天
（日期不符時 sync_twse_market_snapshot 不寫價量），結果 10/9、10/12、10/13 三天 twse_market_snapshot.close_price 全是空的。

做法：任何時候執行，只要 OpenAPI 最新資料日 D 的收盤價在資料庫還是空的，就補寫 D（只寫 close_price／trading_value／trading_volume，
不動其他欄位）。補寫前先驗證（死規則 1）：
  ① 筆數 ≥ MIN_ROWS；② D 必須是交易日、且不得晚於今天；③ D 在資料庫必須已有該日的快照列（不憑空新增日期）；
  ④ 與資料庫中 D 的前一個有收盤價的交易日比對，單日漲跌幅 ≤ MAX_MOVE 的比例 ≥ MIN_OK（排除錯日、錯欄位、整批亂碼）。
驗證不過就退回（回傳 status=rejected 與原因），不默默寫入。純函式＋薄薄的 I/O，可離線測試。
"""
import re

import twse_openapi as to

MIN_ROWS = 800
MAX_MOVE = 0.12      # 台股單日漲跌幅限制 10%，留一點除權息參考價與四捨五入空間
MIN_OK = 0.97

_PLAIN = re.compile(r"^(?!00)\d{4}$")


def is_plain_code(code):
    return bool(code) and isinstance(code, str) and bool(_PLAIN.match(code))


def latest_date(rows):
    """OpenAPI 回應中最新的資料日（iso）；無則 ''。"""
    ds = {to.roc_to_iso(r.get("Date")) for r in (rows or []) if isinstance(r, dict)} - {""}
    return max(ds) if ds else ""


def validate(out, prev_closes, min_rows=MIN_ROWS, max_move=MAX_MOVE, min_ok=MIN_OK):
    """out={代號:{close,...}}、prev_closes={代號:前一交易日收盤}。回傳 (ok, info)。"""
    if len(out) < min_rows:
        return False, {"reason": "too_few_rows", "n": len(out)}
    both = [(s, v["close"], prev_closes[s]) for s, v in out.items() if prev_closes.get(s)]
    if len(both) < min_rows * 0.8:
        return False, {"reason": "too_few_overlap", "n": len(both)}
    ok = sum(1 for _, c, p in both if abs(c / p - 1) <= max_move)
    ratio = ok / len(both)
    if ratio < min_ok:
        return False, {"reason": "move_check_failed", "ratio": round(ratio, 4), "n": len(both)}
    return True, {"ratio": round(ratio, 4), "n": len(both), "rows": len(out)}


def _pg_all(make_query, page=1000, cap=5000):
    out, off = [], 0
    while off < cap:
        rows = make_query().range(off, off + page - 1).execute().data or []
        out += rows
        if len(rows) < page:
            break
        off += page
    return out


def backfill_latest(sb, fetch_rows, today_iso, is_trading_day, dry=False):
    """fetch_rows()：回傳 OpenAPI STOCK_DAY_ALL 的 list（呼叫端負責 HTTP）。
    回傳 dict(status, date, written, ...)；status：filled／already_ok／rejected／no_data。任何例外由呼叫端接。"""
    rows = fetch_rows()
    d = latest_date(rows)
    if not d:
        return {"status": "no_data"}
    if d > today_iso or not is_trading_day(d):
        return {"status": "rejected", "date": d, "reason": "bad_date"}
    out, _ = to.parse_stock_day_all(rows, d, is_plain_code)
    # ③ 該日快照列必須已存在、且還缺收盤價
    have = _pg_all(lambda: sb.table("twse_market_snapshot").select("symbol,close_price").eq("trade_date", d).order("symbol"))
    if not have:
        return {"status": "rejected", "date": d, "reason": "no_snapshot_rows"}
    missing = {r["symbol"] for r in have if not r.get("close_price")}
    if not missing:
        return {"status": "already_ok", "date": d}
    # ④ 前一個有收盤價的交易日
    prev = sb.table("twse_market_snapshot").select("trade_date").lt("trade_date", d).not_.is_("close_price", "null") \
        .order("trade_date", desc=True).limit(1).execute().data or []
    prev_closes = {}
    if prev:
        pd_ = prev[0]["trade_date"]
        prev_closes = {r["symbol"]: float(r["close_price"]) for r in _pg_all(
            lambda: sb.table("twse_market_snapshot").select("symbol,close_price").eq("trade_date", pd_).order("symbol"))
            if r.get("close_price")}
    ok, info = validate(out, prev_closes)
    if not ok:
        return dict(info, status="rejected", date=d)
    todo = [{"trade_date": d, "symbol": s, "close_price": v["close"], "trading_value": v["trading_value"],
             "trading_volume": v["trading_volume"]} for s, v in out.items() if s in missing]
    if dry:
        return {"status": "dry", "date": d, "would_write": len(todo)}
    written = 0
    for i in range(0, len(todo), 500):
        chunk = todo[i:i + 500]
        sb.table("twse_market_snapshot").upsert(chunk, on_conflict="trade_date,symbol").execute()
        written += len(chunk)
    return {"status": "filled", "date": d, "written": written, **info}
