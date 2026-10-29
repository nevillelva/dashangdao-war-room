"""【2026-10-29 老闆：全部照建議落實】穿山惡龍「停損寬度」與「大盤／美股大跌不進場」「追高」的 5 年回測（純函式，可離線測試）。
問題來源：10/5～10/8 進場的穿山惡龍 6 筆停損（−15.6～−16.7%）5 筆 0 勝；其中 GIS-KY 被洗出場後反彈 +15%，另 4 筆停損後仍續跌。
要回答：①停損 −10／−15／−20%、不設、跌破 MA60 出，哪個較好（樣本內外、最差 5%）；②「前晚費半 ≤−3%」「訊號日加權 ≤−2%」進場是否特別差
（若特別差，就是『進場時機』問題，而不是停損寬窄）；③「進場日開盤比前收高 ≥5%」追高是否較差。
口徑與實盤 bt_strategy 相同：訊號＝backtest_rules.find_chuan_e_events（MA60／前漲≥40%／實體≥3%）、隔日開盤進場、停利 12%（盤中最高觸及）、
停損（盤中最低觸及，同日兩者皆觸及先停損；跳空越過以開盤價成交）、最長 20 日收盤出、成本 0.585%、同一檔 20 日內不重複進場。
限制（結論要一併標註）：母體只有現在還上市的股票（存活者偏誤）；不含實盤的寬度閘門與族群閘門；事件之間高度相關（同日大盤）；樣本內 ≤2024-03-31。
"""
from __future__ import annotations

import bisect
from statistics import mean, median

COST = 0.585
IS_END = "2024-03-31"
TP, HOLD, COOLDOWN = 0.12, 20, 20
VARIANTS = ["sl10", "sl15", "sl20", "none", "ma60"]   # ma60＝不設價格停損、收盤跌破 MA60 隔日開盤出（同時仍有停利與 20 日上限）


def simulate(o, h, l, c, ma, e, variant, tp=TP, hold=HOLD):
    """從進場日 e（開盤價進）逐日檢查。回傳淨報酬（%，已扣成本）、出場 index；進場價無效回 None。"""
    n = len(o)
    E = float(o[e])
    if not E > 0:
        return None
    sl = {"sl10": 0.10, "sl15": 0.15, "sl20": 0.20}.get(variant)
    last = min(e + hold - 1, n - 1)
    for i in range(e, last + 1):
        if sl is not None:
            lv = E * (1 - sl)
            if o[i] <= lv and i > e:
                return (o[i] / E - 1) * 100 - COST, i
            if l[i] <= lv:
                return -sl * 100 - COST, i
        if i > e and h[i] >= E * (1 + tp):
            px = max(E * (1 + tp), o[i]) if o[i] >= E * (1 + tp) else E * (1 + tp)
            return (px / E - 1) * 100 - COST, i
        if variant == "ma60" and ma[i] == ma[i] and c[i] < ma[i] and i < n - 1:
            ex = i + 1
            return (o[ex] / E - 1) * 100 - COST, ex
    return (c[last] / E - 1) * 100 - COST, last


def prev_ret(dates, closes, d, offset=0):
    """dates（升冪字串）、closes：d「之前」最後一個交易日相對再前一日的報酬（%）。offset=0 → 嚴格早於 d。無資料回 None。"""
    k = bisect.bisect_left(dates, d) - 1 - offset
    if k < 1:
        return None
    a, b = closes[k - 1], closes[k]
    return (b / a - 1) * 100 if a and a > 0 else None


def gates_for(date_str, entry_open, prev_close, tw_dates, tw_close, sox_dates, sox_close):
    """回傳 {sox, twii, chase} 三個布林（資料缺＝False 並記 missing）。"""
    sox_r = prev_ret(sox_dates, sox_close, date_str)          # 台股進場日之前最後一個美股交易日（前一晚）
    # 訊號日＝進場日前一個台股交易日：它的日報酬
    tw_r = prev_ret(tw_dates, tw_close, date_str)
    chase = (entry_open / prev_close - 1) * 100 if prev_close and prev_close > 0 else None
    return {"sox": bool(sox_r is not None and sox_r <= -3.0), "twii": bool(tw_r is not None and tw_r <= -2.0),
            "chase": bool(chase is not None and chase >= 5.0),
            "missing": [k for k, v in (("sox", sox_r), ("twii", tw_r), ("chase", chase)) if v is None]}


def events_for_stock(df, find_events, tw_dates, tw_close, sox_dates, sox_close):
    """df：日K（Open/High/Low/Close）。find_events(df) → [(entry_i, kind)]。回傳事件 list：{date, kind, rets{variant}, gates}。"""
    o, h, l, c = (df[k].values for k in ("Open", "High", "Low", "Close"))
    ma = df["Close"].rolling(60).mean().values
    ds = list(df.index.strftime("%Y-%m-%d"))
    out, last_e = [], -10 ** 9
    for e, kind in sorted(find_events(df), key=lambda x: x[0]):
        if e - last_e < COOLDOWN or e >= len(o):
            continue
        rets = {}
        for v in VARIANTS:
            r = simulate(o, h, l, c, ma, e, v)
            if r is None:
                rets = None
                break
            rets[v] = round(r[0], 3)
        if rets is None:
            continue
        last_e = e
        g = gates_for(ds[e], float(o[e]), float(c[e - 1]) if e >= 1 else None, tw_dates, tw_close, sox_dates, sox_close)
        out.append({"date": ds[e], "kind": kind, "rets": rets, "gates": g})
    return out


def _stats(xs):
    if not xs:
        return {"n": 0}
    s = sorted(xs)
    p5 = s[max(0, int(len(s) * 0.05) - 1)] if len(s) >= 20 else None
    return {"n": len(xs), "win": round(100 * sum(1 for x in xs if x > 0) / len(xs), 1), "avg": round(mean(xs), 3),
            "med": round(median(xs), 3), "p5": round(p5, 2) if p5 is not None else None}


def summarize(events):
    """events：events_for_stock 的輸出合併。回傳 {variants: {v: 全部／樣本內／樣本外}, gates: {g: {kept/dropped × variant}}}。"""
    out = {"n_events": len(events), "variants": {}, "gates": {}}
    for v in VARIANTS:
        allr = [e["rets"][v] for e in events]
        out["variants"][v] = {"all": _stats(allr),
                              "is": _stats([e["rets"][v] for e in events if e["date"] <= IS_END]),
                              "oos": _stats([e["rets"][v] for e in events if e["date"] > IS_END])}
    for g in ("sox", "twii", "chase"):
        hit = [e for e in events if e["gates"].get(g)]
        rest = [e for e in events if not e["gates"].get(g)]
        out["gates"][g] = {"hit_share_pct": round(100 * len(hit) / len(events), 1) if events else None,
                           "hit": {v: _stats([e["rets"][v] for e in hit]) for v in ("sl15", "none")},
                           "rest": {v: _stats([e["rets"][v] for e in rest]) for v in ("sl15", "none")},
                           "hit_oos": _stats([e["rets"]["sl15"] for e in hit if e["date"] > IS_END])}
    any_g = [e for e in events if e["gates"].get("sox") or e["gates"].get("twii")]
    none_g = [e for e in events if not (e["gates"].get("sox") or e["gates"].get("twii"))]
    out["gates"]["market_any"] = {"hit": _stats([e["rets"]["sl15"] for e in any_g]), "rest": _stats([e["rets"]["sl15"] for e in none_g])}
    return out


def verdict(summary, min_n=100):
    """把統計轉成保守的文字結論：只有「樣本內外同方向、n 足夠、差距≥0.5 個百分點」才說有差，否則一律『沒有證據』。"""
    notes = []
    v = summary["variants"]
    base = v["sl15"]
    for name in ("sl10", "sl20", "none", "ma60"):
        a, b = v[name], base
        parts = []
        for seg in ("is", "oos"):
            if a[seg].get("n", 0) >= min_n and b[seg].get("n", 0) >= min_n:
                parts.append(a[seg]["avg"] - b[seg]["avg"])
        if len(parts) == 2 and all(x >= 0.5 for x in parts):
            notes.append(f"{name} 比現行 sl15 樣本內外都好（+{parts[0]:.2f}／+{parts[1]:.2f} 點）→ 值得考慮")
        elif len(parts) == 2 and all(x <= -0.5 for x in parts):
            notes.append(f"{name} 比現行 sl15 樣本內外都差（{parts[0]:.2f}／{parts[1]:.2f} 點）→ 不採用")
        else:
            notes.append(f"{name} 與現行 sl15 沒有一致差異（樣本內外方向不同、差距不足 0.5 點或樣本不足）→ 不改")
    for g, label in (("sox", "前晚費半≤−3%"), ("twii", "訊號日加權≤−2%"), ("chase", "進場日開盤高於前收≥5%")):
        gg = summary["gates"][g]
        h, r, ho = gg["hit"]["sl15"], gg["rest"]["sl15"], gg["hit_oos"]
        if h.get("n", 0) >= 30 and r.get("n", 0) >= min_n and ho.get("n", 0) >= 15:
            d = h["avg"] - r["avg"]
            d_oos = ho["avg"] - (summary["variants"]["sl15"]["oos"].get("avg") or 0)
            if d <= -1.0 and d_oos <= -0.5:
                notes.append(f"{label}：進場後平均差 {d:.2f} 點（樣本外 {d_oos:.2f} 點，n={h['n']}）→ 有證據，建議該情境暫停進場")
            else:
                notes.append(f"{label}：差 {d:+.2f} 點（樣本外 {d_oos:+.2f} 點，n={h['n']}）→ 證據不足，不加閘門")
        else:
            notes.append(f"{label}：命中樣本只有 {h.get('n', 0)} 筆（樣本外 {ho.get('n', 0)} 筆）→ 不足以下結論")
    return notes
