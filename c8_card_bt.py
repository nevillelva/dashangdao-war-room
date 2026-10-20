"""【2026-10-20 老闆：戰卡偏多 vs 查8 做空的實際成績拆開看】純函式，可離線測試。
事件＝查8 當沖空（昨日漲>5%＋今天續漲＋隔天開盤比昨收高 ≥1%），訊號日收盤後的戰卡評分（market_signal_snapshot.score，與隔夜掃描同一套 classify_score）
分組：偏多（≥2）／中立（−1~1）／偏空（≤−2）；另列開高≥3%（現行門檻）。報酬＝開盤空、當天收盤回補，扣 0.435%。
限制：評分歷史只有約 41 個交易日（2026-08-20 起）、單一盤勢，事件數少，僅供觀察，不能定版；日K 看不到盤中路徑。"""
from __future__ import annotations
from statistics import mean

import numpy as np

COST_DT = 0.435


def bucket(score):
    if score is None:
        return None
    return "偏多(≥2)" if score >= 2 else ("偏空(≤-2)" if score <= -2 else "中立(-1~1)")


def build_events(sym, df, score_by_date):
    if df is None or len(df) < 10:
        return []
    O, C = (df[x].to_numpy(dtype=float) for x in ("Open", "Close"))
    dates = df.index.strftime("%Y-%m-%d")
    out = []
    for i in range(2, len(df) - 1):
        sc = score_by_date.get(dates[i])
        if sc is None or not (C[i - 2] > 0 and C[i - 1] > 0 and C[i] > 0 and O[i + 1] > 0):
            continue
        g1, g2 = (C[i - 1] / C[i - 2] - 1) * 100, (C[i] / C[i - 1] - 1) * 100
        gap = (O[i + 1] / C[i] - 1) * 100
        if g1 > 5 and g2 > 0 and gap >= 1:
            out.append({"sym": sym, "date": dates[i], "score": sc, "grp": bucket(sc), "gap": round(gap, 2),
                        "r": round((O[i + 1] - C[i + 1]) / O[i + 1] * 100 - COST_DT, 3)})
    return out


def _st(xs):
    if not xs:
        return {"n": 0}
    a = np.array(xs, dtype=float)
    return {"n": len(a), "avg": round(float(a.mean()), 3), "win": round(float((a > 0).mean() * 100), 1), "min": round(float(a.min()), 2)}


def analyze(events):
    res = {"all": _st([e["r"] for e in events]), "gap3": _st([e["r"] for e in events if e["gap"] >= 3]), "by_grp": {}, "by_grp_gap3": {}}
    for g in ("偏多(≥2)", "中立(-1~1)", "偏空(≤-2)"):
        res["by_grp"][g] = _st([e["r"] for e in events if e["grp"] == g])
        res["by_grp_gap3"][g] = _st([e["r"] for e in events if e["grp"] == g and e["gap"] >= 3])
    res["days"] = len({e["date"] for e in events})
    res["events"] = [{k: e[k] for k in ("sym", "date", "score", "gap", "r")} for e in sorted(events, key=lambda x: x["date"])][:300]
    return res
