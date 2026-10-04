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
    """yfinance 批次抓約14個月日收盤；先試 .TW，缺的再試 .TWO。
    回傳 {symbol: (最新收盤, 日期, 約一年前收盤|None, 日期|None, 風險指標dict)}。一年前價格用來算『近1年價差＋配息＝含息總報酬』。"""
    import yfinance as yf
    import numpy as np
    out = {}
    syms = [e["stock_id"] for e in etfs]
    bench = None
    try:      # 以 0050 當市場基準算 Beta
        bdf = yf.download("0050.TW", period="14mo", interval="1d", progress=False, auto_adjust=False)
        bench = bdf["Adj Close"].squeeze().dropna().pct_change().dropna()
    except Exception as e:
        print(f"  [價格] 0050 基準抓取失敗，Beta 略過: {type(e).__name__}: {e}")

    def risk_stats(sub, ser):
        """近約一年(最多252個交易日)、以還原收盤(含息)計算：年化波動%、最大回撤%、Sharpe(無風險利率1.5%)、對0050的Beta。"""
        try:
            adj = sub["Adj Close"].dropna() if "Adj Close" in sub else ser
            r = adj.pct_change().dropna().iloc[-252:]
            if len(r) < 120:
                return {}
            vol = float(r.std() * np.sqrt(252))
            cum = (1 + r).cumprod()
            mdd = float((cum / cum.cummax() - 1).min())
            ann = float(cum.iloc[-1] ** (252 / len(r)) - 1)
            d = {"vol_1y": round(vol * 100, 2), "mdd_1y": round(mdd * 100, 2),
                 "sharpe_1y": round((ann - 0.015) / vol, 2) if vol > 0 else None}
            if bench is not None:
                j = r.to_frame("a").join(bench.rename("b"), how="inner").dropna()
                if len(j) >= 120 and j["b"].var() > 0:
                    d["beta_1y"] = round(float(j["a"].cov(j["b"]) / j["b"].var()), 2)
            return d
        except Exception:
            return {}

    def run(suffix, targets):
        for grp in _chunks(targets, 60):
            tick = [s + suffix for s in grp]
            try:
                df = yf.download(tick, period="14mo", interval="1d", progress=False, auto_adjust=False,
                                 group_by="ticker", threads=True)
            except Exception as e:
                print(f"  [價格] yfinance 批次失敗({suffix}): {type(e).__name__}: {e}")
                continue
            for s, t in zip(grp, tick):
                try:
                    sub = df[t] if len(tick) > 1 else df
                    ser = sub["Close"].dropna()
                    if len(ser) == 0:
                        continue
                    last_d = ser.index[-1].date()
                    ago = ser[ser.index <= (ser.index[-1] - dt.timedelta(days=365))]
                    p1 = (float(ago.iloc[-1]), ago.index[-1].date()) if len(ago) else (None, None)
                    out[s] = (float(ser.iloc[-1]), last_d, p1[0], p1[1], risk_stats(sub, ser))
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


def _fetch_master_basic(sb):
    rows, off = [], 0
    while True:
        r = sb.table("etf_master").select("symbol,name,active,first_seen").range(off, off + 999).execute()
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
    # 【新增/下市偵測】每次同步都用「今天的官方清單」對照資料庫既有清單；清單來源失敗(db_fallback)時不做下市判斷
    try:
        prev = {r["symbol"]: r for r in _fetch_master_basic(sb)}
    except Exception:
        prev = {}
    cur_ids = {e["stock_id"] for e in etfs}
    new_etfs = sorted(cur_ids - set(prev)) if prev else []      # 資料庫還是空的(首次同步)不算新增
    gone_etfs = sorted(s_ for s_, r in prev.items() if s_ not in cur_ids and r.get("active") is not False) \
        if (prev and summary["list_source"] == "finmind") else []
    if len(gone_etfs) > max(10, int(len(prev) * 0.2)):   # 一次消失太多＝清單來源異常(被截斷)的可能性遠大於真的大量下市，不標記
        print(f"  [下市偵測] 一次有 {len(gone_etfs)} 檔消失，疑似清單來源異常，本次不標記下市。")
        summary["gone_suspect"] = len(gone_etfs)
        gone_etfs = []
    summary["new_etfs"] = new_etfs
    summary["gone_etfs"] = gone_etfs
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
    prices = fetch_prices(etfs)   # 全部ETF都抓價：剛上市還沒配息的新ETF也要有價格與上市資訊
    summary["prices"] = len(prices)
    master = []
    for e in etfs:
        sid = e["stock_id"]
        evs_norm = E.norm_events([{"symbol": sid, "ex_date": d, "cash_per_unit": a} for d, a in yf_ev.get(sid, [])])
        px = prices.get(sid)
        row = {"symbol": sid, "name": e.get("name") or "", "market": str(e.get("market", "")),
               "freq": E.classify_frequency(evs_norm, today), "updated_at": now, "active": True}
        # 注意：FinMind TaiwanStockInfo 的 date 欄位是資料更新日、不是上市日(實測 430 檔全是同一天)，所以不寫入 listed_date；
        # 「上市未滿1年」改由價格歷史長度(一年前沒有收盤價)與首次配息時間判斷，見 etf_core.candidate_table。
        if sid in new_etfs:
            row["first_seen"] = today.isoformat()
        if px:
            row["last_price"] = round(px[0], 4)
            row["price_date"] = px[1].isoformat()
            if px[2]:
                row["price_1y"] = round(px[2], 4)
                row["price_1y_date"] = px[3].isoformat()
            if len(px) > 4 and px[4]:
                row.update(px[4])
        master.append(row)
    # 沒有價格的列不能帶 last_price 欄位（避免把舊價蓋成空值）→ 分兩批
    sigs = {}
    for m in master:
        sigs.setdefault(tuple(sorted(m.keys())), []).append(m)
    for rows_ in sigs.values():
        for grp in _chunks(rows_, 500):
            try:
                sb.table("etf_master").upsert(grp, on_conflict="symbol").execute()
            except Exception as ex:
                print(f"  [寫入etf_master] 失敗: {type(ex).__name__}: {ex}")
    summary["master_rows"] = len(master)
    if gone_etfs:
        try:
            for grp in _chunks(gone_etfs, 100):
                sb.table("etf_master").update({"active": False, "updated_at": now}).in_("symbol", grp).execute()
        except Exception as ex:
            print(f"  [標記下市] 失敗: {type(ex).__name__}: {ex}")
    msg = (f"ETF同步完成：清單{summary['etf_count']}檔、有配息資料{summary['with_yf_dividends']}檔、"
           f"配息事件寫入{wrote}筆（含發放日{len(with_pay)}筆）、FinMind請求{fm_calls}次、現價{len(prices)}檔。")
    if new_etfs:
        names = {e["stock_id"]: e.get("name") or "" for e in etfs}
        msg += " 新增ETF：" + "、".join(f"{x}{names.get(x, '')}" for x in new_etfs[:15]) + ("…" if len(new_etfs) > 15 else "")
    if gone_etfs:
        msg += " 清單消失(視為下市/改名)：" + "、".join(gone_etfs[:15])
    return msg, summary
