"""【2026-10-12｜B4 盤中 15／60 分 MACD 回補式回測】（死規則 4：能回測就先回測，不等影子紀錄慢慢累積）
資料來源：永豐金 Shioaji `api.kbars()` 的歷史 1 分K（只查行情、不碰下單）。純函式、不碰網路，可離線測試。

流程：1 分K（ts=該分鐘結束時間）→ 合成 15／60 分K（以 09:00 對齊，標記 K 棒結束時間）→ 在「跨日連續」序列上算 MACD(12,26,9)
→ 找金叉／死叉（含「零軸上金叉」）→ 計算訊號 K 棒收盤後到「當日收盤」與「次一交易日收盤」的報酬 → 扣當日全體同時段 K 棒平均（去市場漂移）。
限制（寫進報告）：樣本只有近 ~30 個交易日、同一段盤勢；同日多檔訊號並非獨立；不含成本（來回約 0.585%）。"""
from datetime import datetime, timedelta
from statistics import mean, pstdev

MARKET_CLOSE = (13, 25)   # 最後一根 1 分K 的起點（13:25～13:30）；ts 標結束時間時為 13:30 左右


def _to_dt(x):
    if isinstance(x, datetime):
        return x
    if isinstance(x, (int, float)) and not isinstance(x, bool):
        import pandas as pd
        return pd.to_datetime(int(x)).to_pydatetime()
    import pandas as pd
    return pd.to_datetime(x).to_pydatetime()


def minute_bars(rows):
    """rows: [{ts,Open,High,Low,Close,Volume}]，ts 為該分鐘『結束』時間（Shioaji 慣例）。
    回傳 [(起始 datetime, o, h, l, c, v)]，依時間排序；09:00 前、13:30 後、量為 0 或價格缺漏者略過。"""
    out = []
    for r in rows or []:
        try:
            o, h, l, c = float(r["Open"]), float(r["High"]), float(r["Low"]), float(r["Close"])
            v = float(r.get("Volume") or 0)
            t = _to_dt(r["ts"]) - timedelta(minutes=1)
        except Exception:  # noqa: BLE001
            continue
        if min(o, h, l, c) <= 0 or v <= 0:
            continue
        hm = t.hour * 60 + t.minute
        if hm < 9 * 60 or hm >= 13 * 60 + 30:
            continue
        out.append((t, o, h, l, c, v))
    out.sort(key=lambda x: x[0])
    return out


def resample(bars1, minutes):
    """1 分K → N 分K（以 09:00 對齊）。回傳 [{'date','end','o','h','l','c','v','full'}]，end 為 K 棒結束時間 datetime。
    full=False 表示該根不足 N 分鐘（例如 60 分K 的 13:00～13:30），不拿來當訊號。"""
    buckets = {}
    for t, o, h, l, c, v in bars1:
        mins = t.hour * 60 + t.minute - 9 * 60
        key = (t.date(), mins // minutes)
        b = buckets.get(key)
        if b is None:
            buckets[key] = {"date": t.date(), "idx": mins // minutes, "o": o, "h": h, "l": l, "c": c, "v": v, "n": 1}
        else:
            b["h"] = max(b["h"], h)
            b["l"] = min(b["l"], l)
            b["c"] = c
            b["v"] += v
            b["n"] += 1
    out = []
    for key in sorted(buckets):
        b = buckets[key]
        start = datetime(b["date"].year, b["date"].month, b["date"].day, 9, 0) + timedelta(minutes=b["idx"] * minutes)
        b["end"] = start + timedelta(minutes=minutes)
        b["full"] = b["n"] >= minutes - max(1, minutes // 15)   # 容許少數分鐘無成交
        out.append(b)
    return out


def ema(values, n):
    k = 2.0 / (n + 1)
    out, e = [], None
    for x in values:
        e = x if e is None else (x - e) * k + e
        out.append(e)
    return out


def macd(closes, fast=12, slow=26, sig=9):
    ef, es = ema(closes, fast), ema(closes, slow)
    dif = [a - b for a, b in zip(ef, es)]
    dea = ema(dif, sig)
    hist = [d - e for d, e in zip(dif, dea)]
    return dif, dea, hist


def find_signals(bars, warmup=35, window=("09:45", "12:00")):
    """bars: resample 的輸出（單一股票、跨日連續）。回傳 [{'i','kind','end','date'}]。
    kind：gc（柱體由 ≤0 翻 >0 的金叉）、gc0（金叉且 DIF>0＝零軸上）、dc（死叉）、dc0（死叉且 DIF<0）。
    只在 K 棒完整、結束時間落在 window 內、且序列已暖機 warmup 根之後才算（避免 EMA 起算誤差）。"""
    if len(bars) < warmup + 2:
        return []
    dif, dea, hist = macd([b["c"] for b in bars])
    w0 = int(window[0][:2]) * 60 + int(window[0][3:5])
    w1 = int(window[1][:2]) * 60 + int(window[1][3:5])
    out = []
    for i in range(warmup, len(bars)):
        b = bars[i]
        if not b["full"]:
            continue
        hm = b["end"].hour * 60 + b["end"].minute
        if hm < w0 or hm > w1:
            continue
        if hist[i - 1] <= 0 < hist[i]:
            out.append({"i": i, "kind": "gc", "end": b["end"], "date": b["date"]})
            if dif[i] > 0:
                out.append({"i": i, "kind": "gc0", "end": b["end"], "date": b["date"]})
        elif hist[i - 1] >= 0 > hist[i]:
            out.append({"i": i, "kind": "dc", "end": b["end"], "date": b["date"]})
            if dif[i] < 0:
                out.append({"i": i, "kind": "dc0", "end": b["end"], "date": b["date"]})
    return out


def day_closes(bars):
    """{date: 當日最後一根收盤}。"""
    d = {}
    for b in bars:
        d[b["date"]] = b["c"]
    return d


def forward_returns(bars, i, closes_by_day, days_sorted):
    """訊號 K 棒 i 收盤後：到當日收盤的報酬 %、到下一交易日收盤的報酬 %（沒有下一日回 None）。"""
    c0 = bars[i]["c"]
    d = bars[i]["date"]
    eod = (closes_by_day[d] / c0 - 1) * 100
    nxt = None
    k = days_sorted.index(d)
    if k + 1 < len(days_sorted):
        nxt = (closes_by_day[days_sorted[k + 1]] / c0 - 1) * 100
    return eod, nxt


def evaluate(per_symbol_bars, minutes_label, window=("09:45", "12:00")):
    """per_symbol_bars: {symbol: resample 輸出}。回傳 {kind: {n, eod:{mean,excess,win,t}, nd:{...}}}。
    excess＝訊號報酬 − 同一天『同時段所有股票所有完整K棒』的平均報酬（去市場漂移）。"""
    w0 = int(window[0][:2]) * 60 + int(window[0][3:5])
    w1 = int(window[1][:2]) * 60 + int(window[1][3:5])
    base_eod, base_nd = {}, {}      # date -> [ret...]
    sig_rows = []                    # (kind, date, eod, nd)
    for sym, bars in per_symbol_bars.items():
        if len(bars) < 40:
            continue
        cbd = day_closes(bars)
        days = sorted(cbd)
        for i, b in enumerate(bars):
            hm = b["end"].hour * 60 + b["end"].minute
            if not b["full"] or hm < w0 or hm > w1 or i < 35:
                continue
            eod, nd = forward_returns(bars, i, cbd, days)
            base_eod.setdefault(b["date"], []).append(eod)
            if nd is not None:
                base_nd.setdefault(b["date"], []).append(nd)
        for s in find_signals(bars, window=window):
            eod, nd = forward_returns(bars, s["i"], cbd, days)
            sig_rows.append((s["kind"], s["date"], eod, nd))
    bm_eod = {d: mean(v) for d, v in base_eod.items() if v}
    bm_nd = {d: mean(v) for d, v in base_nd.items() if v}

    def stats(vals, ex):
        if len(vals) < 2:
            return {"n": len(vals)}
        sd = pstdev(ex) or 1e-9
        return {"n": len(vals), "mean": round(mean(vals), 4), "excess": round(mean(ex), 4),
                "win": round(100.0 * sum(1 for x in ex if x > 0) / len(ex), 1),
                "t": round(mean(ex) / sd * (len(ex) ** 0.5), 2)}

    out = {"timeframe": minutes_label,
           "baseline": {"n_eod": sum(len(v) for v in base_eod.values()), "mean_eod": round(mean([x for v in base_eod.values() for x in v]), 4) if base_eod else None}}
    for kind in ("gc", "gc0", "dc", "dc0"):
        rows = [r for r in sig_rows if r[0] == kind]
        eod_v = [r[2] for r in rows]
        eod_x = [r[2] - bm_eod.get(r[1], 0.0) for r in rows]
        nd_rows = [r for r in rows if r[3] is not None]
        nd_v = [r[3] for r in nd_rows]
        nd_x = [r[3] - bm_nd.get(r[1], 0.0) for r in nd_rows]
        out[kind] = {"eod": stats(eod_v, eod_x), "nd": stats(nd_v, nd_x)}
    return out
