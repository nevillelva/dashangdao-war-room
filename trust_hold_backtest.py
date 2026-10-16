"""投信持股占比（SITCA 季持股明細彙總）× 後續報酬回測。
【2026-10-16】訊號：季底 Q 的「主動型基金持有該股金額 ÷ 該股市值」。公布約季底後 10 個營業日 → 回測一律用 Q+20 日曆天後才可用。
視窗：W1＝可用日後 20 交易日；W2＝下一季「季底前31天→季底」（作帳窗口）；W3＝可用日→下一季底。
超額＝個股報酬 − 同視窗池內平均。限制：①只含基金淨值占比≥1%的部位（低估、且偏重大型股）；②市值用目前股數×當時價（股數變動忽略）；
③池＝目前 SITCA 持股金額前N大（存活者偏誤）；④季數有限（≤20）、同季樣本高度相關；⑤未扣成本。
"""
from __future__ import annotations
from bisect import bisect_right
from datetime import date, timedelta
from statistics import mean

AVAIL_DAYS = 20


def _d(s):
    return date.fromisoformat(s)


def px_at(dates, px, d: date):
    """d 當天或之前最近交易日收盤；無則 None。"""
    i = bisect_right(dates, d.isoformat()) - 1
    return (dates[i], px[i]) if i >= 0 else (None, None)


def ret(dates, px, d0: date, d1: date):
    a = px_at(dates, px, d0)
    b = px_at(dates, px, d1)
    if a[1] is None or b[1] is None or a[0] == b[0] or a[1] <= 0:
        return None
    return b[1] / a[1] - 1


def quintile_edges(vals):
    s = sorted(vals)
    n = len(s)
    return s[int(n * 0.2)], s[int(n * 0.8)] if n >= 10 else (None, None)


def build_rows(per, holds, quarters):
    """per: {sym:{dates,px,shares}}；holds: {qdate_str:{sym:{amt_active,amt_all}}}；quarters: 已排序 qdate 字串。
    回傳 rows: dict(win, q, sym, ratio, dratio, ex)。"""
    rows = []
    prev_ratio = {}
    for k, q in enumerate(quarters):
        qd = _d(q)
        t0 = qd + timedelta(days=AVAIL_DAYS)
        ratio = {}
        for sym, p in per.items():
            d_, c = px_at(p["dates"], p["px"], qd)
            if c is None or not p.get("shares"):
                continue
            amt = (holds.get(q, {}).get(sym) or {}).get("amt_active", 0.0)
            ratio[sym] = amt / (c * p["shares"])
        nxt = _d(quarters[k + 1]) if k + 1 < len(quarters) else None
        wins = {}
        wins["W1"] = (t0, t0 + timedelta(days=28))
        if nxt:
            wins["W2"] = (nxt - timedelta(days=31), nxt)
            wins["W3"] = (t0, nxt)
        for w, (a, b) in wins.items():
            rets = {s: ret(per[s]["dates"], per[s]["px"], a, b) for s in ratio}
            rets = {s: r for s, r in rets.items() if r is not None}
            if len(rets) < 20:
                continue
            m = mean(rets.values())
            for s, r in rets.items():
                rows.append({"win": w, "q": q, "sym": s, "ratio": ratio[s],
                             "dratio": (ratio[s] - prev_ratio[s]) if s in prev_ratio else None, "ex": r - m})
        prev_ratio = ratio
    return rows


def analyze(rows):
    out = {}
    for w in ("W1", "W2", "W3"):
        rw = [r for r in rows if r["win"] == w]
        grp = {"持股占比前20%": [], "持股占比後20%(含0)": [], "持股占比=0": [], "占比季增前20%": []}
        for q in sorted({r["q"] for r in rw}):
            rq = [r for r in rw if r["q"] == q]
            pos = sorted([r["ratio"] for r in rq if r["ratio"] > 0])
            if len(pos) >= 10:
                hi = pos[int(len(pos) * 0.8)]
                lo = pos[int(len(pos) * 0.2)]
                grp["持股占比前20%"] += [r["ex"] for r in rq if r["ratio"] >= hi]
                grp["持股占比後20%(含0)"] += [r["ex"] for r in rq if r["ratio"] <= lo]
            grp["持股占比=0"] += [r["ex"] for r in rq if r["ratio"] == 0]
            dd = sorted([r["dratio"] for r in rq if r["dratio"] is not None])
            if len(dd) >= 10:
                th = dd[int(len(dd) * 0.8)]
                grp["占比季增前20%"] += [r["ex"] for r in rq if r["dratio"] is not None and r["dratio"] >= th]
        out[w] = {g: {"n": len(v), "mean_ex": round(mean(v) * 100, 3) if v else None,
                      "win_rate": round(sum(1 for x in v if x > 0) / len(v) * 100, 1) if v else None}
                  for g, v in grp.items()}
        out[w]["n_quarters"] = len({r["q"] for r in rw})
    return out
