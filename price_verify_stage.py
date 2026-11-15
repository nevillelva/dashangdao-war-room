"""【2026-11-15】價格查證階段（獨立模組；純函式＋薄 I/O，可離線測試）。

輸入：股票代號清單、日期區間。流程（死規則 1：先驗證再使用；證交所 www 直連被組織代理 403，不繞路）：
  ① 先讀資料庫 twse_market_snapshot（trade_date, symbol, close_price）；
  ② 缺的再讀回測快取 bt_cache（daily_raw/{代號}_{年數}.pkl，未還原收盤價）；
  ③ 兩邊都沒有的 (代號, 日期) 只「標記待補」，交給 GitHub 執行器（system_scheduler 階段）補抓，本模組不直接連外網。
兩邊都有值時交叉比對，差距 > TOL_PCT 記為「衝突」（不默默擇一）。
輸出：只印數量；完整計畫（含待補清單，上限 PENDING_CAP）寫私有表 ui_selftest_reports（summary=price_verify）。
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta

TOL_PCT = 0.5            # 快照與快取收盤價相差超過 0.5% 視為衝突
PENDING_CAP = 2000       # 報告內最多寫入的待補筆數（超過只記總數）
SUMMARY = "price_verify"
_CHUNK = 100             # in_() 一次最多代號數


def _iso(d) -> str:
    if isinstance(d, datetime):
        return d.strftime("%Y-%m-%d")
    if isinstance(d, date):
        return d.isoformat()
    s = str(d or "").strip()
    datetime.strptime(s, "%Y-%m-%d")      # 格式不對直接丟 ValueError（驗證輸入）
    return s


def expand_dates(start, end, is_trading_day=None):
    """start～end（含）的日期清單 YYYY-MM-DD。預設只排除週六日；可傳 is_trading_day(iso)->bool 進一步排除休市日。"""
    s, e = datetime.strptime(_iso(start), "%Y-%m-%d").date(), datetime.strptime(_iso(end), "%Y-%m-%d").date()
    if e < s:
        raise ValueError("end 早於 start")
    out, d = [], s
    while d <= e:
        iso = d.isoformat()
        if d.weekday() < 5 and (is_trading_day is None or is_trading_day(iso)):
            out.append(iso)
        d += timedelta(days=1)
    return out


def clean_symbols(symbols):
    """去空白、去重、保序；只留 4～6 碼英數代號（避免亂碼進查詢）。"""
    out = []
    for s in symbols or []:
        s = str(s or "").strip().upper()
        if 4 <= len(s) <= 6 and s.isalnum() and s not in out:
            out.append(s)
    return out


def _pg_all(make_query, page=1000, cap=50000):
    out, off = [], 0
    while off < cap:
        rows = make_query().range(off, off + page - 1).execute().data or []
        out += rows
        if len(rows) < page:
            break
        off += page
    return out


def read_snapshot(sb, symbols, start, end):
    """twse_market_snapshot → {(symbol, date): close}；close 為空或 <=0 的列不算有資料。"""
    out = {}
    for i in range(0, len(symbols), _CHUNK):
        chunk = symbols[i:i + _CHUNK]
        rows = _pg_all(lambda c=chunk: sb.table("twse_market_snapshot").select("symbol,trade_date,close_price")
                       .in_("symbol", c).gte("trade_date", start).lte("trade_date", end)
                       .order("trade_date").order("symbol"))
        for r in rows:
            try:
                v = float(r.get("close_price"))
            except (TypeError, ValueError):
                continue
            if v > 0:
                out[(r["symbol"], str(r["trade_date"])[:10])] = v
    return out


def read_cache(symbols, dates, years=(5,), cache_dir=None):
    """回測快取 daily_raw → {(symbol, date): Close}。只讀、不抓、不寫；檔案不存在或壞掉就略過。"""
    import bt_cache as _BC
    wanted = set(dates)
    out = {}
    old_dir = _BC.DIR
    if cache_dir:
        _BC.DIR = cache_dir
    try:
        for sym in symbols:
            for y in years:
                path = _BC._p("daily_raw", f"{sym}_{y}")
                if not os.path.exists(path):
                    continue
                df = _BC._read(path)
                try:
                    closes = df["Close"].dropna()
                    for idx, v in closes.items():
                        iso = idx.strftime("%Y-%m-%d")
                        if iso in wanted and float(v) > 0 and (sym, iso) not in out:
                            out[(sym, iso)] = float(v)
                except Exception:  # noqa: BLE001
                    continue
    finally:
        _BC.DIR = old_dir
    return out


def build_plan(symbols, dates, snapshot, cache, tol_pct=TOL_PCT):
    """每個 (代號, 日期) 分類：snapshot（快照有）／cache（只有快取有）／pending（都沒有，待 GitHub 執行器補抓）。
    兩邊皆有時比對差距，> tol_pct 記入 conflicts。回傳 dict（含各數量與清單）。"""
    pending, conflicts = [], []
    n_snap = n_cache = 0
    for sym in symbols:
        for d in dates:
            a, b = snapshot.get((sym, d)), cache.get((sym, d))
            if a is not None:
                n_snap += 1
                if b is not None and abs(b / a - 1) * 100 > tol_pct:
                    conflicts.append({"symbol": sym, "date": d, "snapshot": a, "cache": b})
            elif b is not None:
                n_cache += 1
            else:
                pending.append({"symbol": sym, "date": d})
    total = len(symbols) * len(dates)
    return {"total": total, "from_snapshot": n_snap, "from_cache": n_cache, "pending": len(pending),
            "conflicts": len(conflicts), "pending_list": pending[:PENDING_CAP], "conflict_list": conflicts[:200],
            "pending_truncated": len(pending) > PENDING_CAP}


def run_stage(sb, symbols, start, end, is_trading_day=None, years=(5,), cache_dir=None, write=True, run_id=None):
    """跑完整查證階段。回傳 (counts, plan)。counts 只含數量，可安全印到公開日誌。"""
    symbols = clean_symbols(symbols)
    if not symbols:
        raise ValueError("沒有有效的股票代號")
    dates = expand_dates(start, end, is_trading_day)
    if not dates:
        raise ValueError("日期區間內沒有交易日")
    snap = read_snapshot(sb, symbols, dates[0], dates[-1])
    cache = read_cache(symbols, dates, years=years, cache_dir=cache_dir)
    plan = build_plan(symbols, dates, snap, cache)
    counts = {k: plan[k] for k in ("total", "from_snapshot", "from_cache", "pending", "conflicts")}
    counts.update({"symbols": len(symbols), "days": len(dates)})
    if write:
        report = {"_meta": {"kind": SUMMARY, "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            "start": dates[0], "end": dates[-1], "tol_pct": TOL_PCT},
                  "counts": counts, "plan": plan}
        sb.table("ui_selftest_reports").insert({"run_id": run_id if run_id is not None else os.environ.get("GITHUB_RUN_ID", ""),
                                                "summary": SUMMARY, "report": report}).execute()
    print("[價格查證] " + "、".join(f"{k} {v}" for k, v in counts.items()))   # 公開日誌只印數量
    return counts, plan


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="價格查證階段（先快照、再快取，缺漏標記待補）")
    ap.add_argument("--symbols", required=True, help="逗號分隔代號")
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    a = ap.parse_args(argv)
    from supabase import create_client
    sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
    run_stage(sb, a.symbols.split(","), a.start, a.end)


if __name__ == "__main__":
    main()
