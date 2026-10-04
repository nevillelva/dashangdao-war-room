# -*- coding: utf-8 -*-
"""
etf_sync.py —— ETF 清單 / 配息事件 / 現價 同步到 Supabase（R99新增，system_scheduler.py 的 etf_dividend_sync 階段呼叫）

資料來源（2026-10 實測驗證，見 validate_etf_sources.py 與 reports-etf 分支）：
  - ETF 清單、名稱、市場別：FinMind TaiwanStockInfo（1 次請求）
  - 除息日＋每單位現金配息：yfinance（不耗額度，涵蓋所有 ETF）
  - 發放日(CashDividendPaymentDate)：FinMind TaiwanStockDividend（逐檔，耗額度）。
    官方 TWSE/TPEx 公開 API 只有除權息預告，沒有發放日，所以發放日只能靠 FinMind；
    抓不到的事件 pay_date 留空，網頁端以「該檔歷史除息→發放天數中位數」估算並標示為估算。
  - 現價：yfinance 收盤價（twse_market_snapshot 不含 ETF）
【額度控管】FinMind 只對「還沒有發放日的近期/未來事件」或「第一次同步」的 ETF 抓，優先順序：
  使用者持有 > 近12月有配息 > 其餘；額度用盡就停（沿用 validate_etf_sources.fm_get 的多 token 輪替）。
【原則】已存在的 pay_date 不被空值覆蓋；任何一段失敗不影響其他段。
"""
import os
import time
import datetime as dt

import etf_core as E
import validate_etf_sources as V

MAX_FM_PER_RUN = int(os.environ.get("ETF_FM_MAX") or "450")


def _chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def fetch_prices(etfs):
    """yfinance 批次抓最近收盤價；先試 .TW，缺的再試 .TWO。回傳 {symbol: (price, date)}。"""
    import yfinance as yf
    out = {}
    syms = [e["stock_id"] for e in etfs]

    def run(suffix, targets):
        for grp in _chunks(targets, 80):
            tick = [s + suffix for s in grp]
            try:
                df = yf.download(tick, period="10d", interval="1d", progress=False, auto_adjust=False,
                                 group_by="ticker", threads=True)
            except Exception as e:
                print(f"  [價格] yfinance 批次失敗({suffix}): {type(e).__name__}: {e}")
                continue
            for s, t in zip(grp, tick):
                try:
                    sub = df[t] if len(tick) > 1 else df
                    ser = sub["Close"].dropna()
                    if len(ser) > 0:
                        out[s] = (float(ser.iloc[-1]), ser.index[-1].date())
                except Exception:
                    continue
            time.sleep(0.5)

    run(".TW", syms)
    miss = [s for s in syms if s not in out]
    if miss:
        run(".TWO", miss)
    return out


def _existing_events(sb):
    rows, off = [], 0
    while True:
        r = (sb.table("etf_dividend_events").select("symbol,ex_date,pay_date")
             .range(off, off + 999).execute())
        rows += r.data or []
        if len(r.data or []) < 1000:
            break
        off += 1000
    return rows


def _held_symbols(sb):
    try:
        r = sb.table("etf_trades").select("symbol").execute()
        return {x["symbol"] for x in (r.data or [])}
    except Exception:
        return set()


def run_sync(sb, today=None):
    today = today or dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).date()
    token = os.environ.get("FINMIND_TOKEN", "").strip()
    summary = {}

    # 1) ETF 清單
    etfs, meta = V.get_etf_list(token)
    if not etfs:
        try:
            r = sb.table("etf_master").select("symbol,name,market").execute()
            etfs = [{"stock_id": x["symbol"], "name": x.get("name") or "", "market": x.get("market") or ""}
                    for x in (r.data or [])]
        except Exception:
            etfs = []
        summary["list_source"] = "db_fallback"
    else:
        summary["list_source"] = "finmind"
    if not etfs:
        return "ETF 清單取得失敗且資料庫無備援，本次不更新。", summary
    summary["etf_count"] = len(etfs)
    held = _held_symbols(sb)
    existing = _existing_events(sb)
    have_pay = {}   # symbol -> 是否已有任何 pay_date
    need_pay = set()
    cutoff = today - dt.timedelta(days=120)
    for r in existing:
        s = r["symbol"]
        if r.get("pay_date"):
            have_pay[s] = True
        else:
            exd = E.to_date(r["ex_date"])
            if exd and exd >= cutoff:
                need_pay.add(s)

    # 2) yfinance 配息（全部 ETF）
    one_year_ago = today - dt.timedelta(days=365)
    yf_ev = {}
    for i, e in enumerate(etfs):
        sid = e["stock_id"]
        evs = V.yf_dividends(sid, str(e.get("market", "")).lower())
        evs = [(d, a) for d, a in evs if d >= today - dt.timedelta(days=800) and a > 0]
        if evs:
            yf_ev[sid] = evs
        if (i + 1) % 100 == 0:
            print(f"  [yfinance配息] {i + 1}/{len(etfs)}")
    summary["with_yf_dividends"] = len(yf_ev)

    # 3) FinMind 發放日（依優先順序、受額度限制）
    def prio(sid):
        recent = any(d >= one_year_ago for d, _ in yf_ev.get(sid, []))
        return (0 if sid in held else 1, 0 if (sid in need_pay or not have_pay.get(sid)) else 1,
                0 if recent else 1, sid)
    order = sorted([e["stock_id"] for e in etfs if (e["stock_id"] in yf_ev)
                    and (e["stock_id"] in need_pay or not have_pay.get(e["stock_id"]) or e["stock_id"] in held)],
                   key=prio)[:MAX_FM_PER_RUN]
    fm_ev = {}
    fm_calls = 0
    for sid in order:
        start = (today - dt.timedelta(days=800)).strftime("%Y-%m-%d")
        rows, err = V.fm_get("TaiwanStockDividend", token, data_id=sid, start_date=start)
        fm_calls += 1
        if err and "rate_limited" in str(err):
            print(f"  [FinMind發放日] 額度用盡，停止（已處理 {fm_calls - 1}/{len(order)} 檔）")
            break
        lst = []
        for r in rows:
            exd = V.parse_date(r.get("CashExDividendTradingDate"))
            amt = V.fm_cash_per_unit(r)
            payd = V.parse_date(r.get("CashDividendPaymentDate"))
            if exd and amt > 0:
                lst.append((exd, amt, payd))
        if lst:
            fm_ev[sid] = lst
        time.sleep(0.25 if V.split_tokens(token) else 1.3)
    summary["finmind_calls"] = fm_calls
    summary["finmind_etfs_with_rows"] = len(fm_ev)

    # 4) 合併：以 yfinance 的除息日/金額為準，FinMind 提供發放日；FinMind 獨有的事件也收
    merged = {}   # (sid, ex_date) -> dict
    for sid, evs in yf_ev.items():
        for d, a in evs:
            merged[(sid, d)] = {"symbol": sid, "ex_date": d.isoformat(), "cash_per_unit": round(a, 6),
                                "source": "yfinance"}
    for sid, lst in fm_ev.items():
        for exd, amt, payd in lst:
            hit = None
            for dd in (0, 1, -1, 2, -2, 3, -3):
                k = (sid, exd + dt.timedelta(days=dd))
                if k in merged:
                    hit = k
                    break
            if hit is None:
                merged[(sid, exd)] = {"symbol": sid, "ex_date": exd.isoformat(), "cash_per_unit": round(amt, 6),
                                      "source": "finmind"}
                hit = (sid, exd)
            if payd and 0 <= (payd - V.parse_date(merged[hit]["ex_date"])).days <= 90:
                merged[hit]["pay_date"] = payd.isoformat()
                merged[hit]["pay_date_estimated"] = False
                merged[hit]["source"] = "yfinance+finmind" if merged[hit]["source"] == "yfinance" else "finmind"
    with_pay = [v for v in merged.values() if v.get("pay_date")]
    without_pay = [v for v in merged.values() if not v.get("pay_date")]
    now = dt.datetime.utcnow().isoformat()
    for v in merged.values():
        v["updated_at"] = now
    wrote = 0
    for batch_rows in (with_pay, without_pay):
        for grp in _chunks(batch_rows, 500):
            try:
                sb.table("etf_dividend_events").upsert(grp, on_conflict="symbol,ex_date").execute()
                wrote += len(grp)
            except Exception as ex:
                print(f"  [寫入配息事件] 失敗: {type(ex).__name__}: {ex}")
    summary["events_written"] = wrote
    summary["events_with_paydate_in_batch"] = len(with_pay)

    # 5) 價格 + etf_master
    prices = fetch_prices([e for e in etfs if e["stock_id"] in yf_ev or e["stock_id"] in held])
    summary["prices"] = len(prices)
    master = []
    for e in etfs:
        sid = e["stock_id"]
        evs_norm = E.norm_events([{"symbol": sid, "ex_date": d, "cash_per_unit": a} for d, a in yf_ev.get(sid, [])])
        px = prices.get(sid)
        row = {"symbol": sid, "name": e.get("name") or "", "market": str(e.get("market", "")),
               "freq": E.classify_frequency(evs_norm, today), "updated_at": now}
        if px:
            row["last_price"] = round(px[0], 4)
            row["price_date"] = px[1].isoformat()
        master.append(row)
    # 沒有價格的列不能帶 last_price 欄位（避免把舊價蓋成空值）→ 分兩批
    m_px = [m for m in master if "last_price" in m]
    m_nopx = [m for m in master if "last_price" not in m]
    for rows_ in (m_px, m_nopx):
        for grp in _chunks(rows_, 500):
            try:
                sb.table("etf_master").upsert(grp, on_conflict="symbol").execute()
            except Exception as ex:
                print(f"  [寫入etf_master] 失敗: {type(ex).__name__}: {ex}")
    summary["master_rows"] = len(master)
    msg = (f"ETF同步完成：清單{summary['etf_count']}檔、有配息資料{summary['with_yf_dividends']}檔、"
           f"配息事件寫入{wrote}筆（含發放日{len(with_pay)}筆）、FinMind請求{fm_calls}次、現價{len(prices)}檔。")
    return msg, summary
