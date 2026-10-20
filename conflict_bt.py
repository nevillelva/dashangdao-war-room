"""【2026-10-20 老闆：查8 做空與強勢特徵打架（佳大），要找出所有類似問題】衝突／否決條件回測（純函式，可離線測試）。
做法：對三套實際在用的規則（A 查8 當沖空＝開盤比昨收高>1%才空；B 查7 漲停收盤買→隔日開盤賣；C 動能多方：量比≥2＋創20日新高、持有 5 日），
各自在訊號日用「收盤後已知」的特徵（連漲天數、乖離、均線排列、KD、第2天是否也漲停…）切開，看哪些特徵出現時成績明顯變差，
且 IS（≤2024-03-31）與 OOS 同向、樣本≥MIN_N，才列為「否決候選」。成本：當沖 0.435%、其餘 0.585%。
限制：日K 看不到盤中路徑；母體＝目前成交值前 N 檔（存活者偏誤）；漲停買不到／券源未考慮；特徵多重比較，候選需再用大樣本複驗。"""
from __future__ import annotations
from statistics import mean

import numpy as np

COST_DT, COST_LONG = 0.435, 0.585
MIN_N = 40
IS_END = "2024-03-31"
FEATS = {
    "consec4": "連漲≥4天", "consec6": "連漲≥6天", "ext15": "收盤高於MA5 15%以上", "ext25": "收盤高於MA5 25%以上",
    "maalign": "均線多頭排列(MA5>10>20)", "kd80": "KD的K值≥80", "lu2": "第2天也漲停(≥9.5%)", "locked": "第2天收在最高且≥9.5%(鎖漲停型)",
    "vol2": "第2天量比≥2", "gap3": "隔天開高≥3%", "gap6": "隔天開高≥6%", "mkt_bull": "大盤多頭", "mkt_bear": "大盤空頭",
}


def _kd(H, L, C, n=9):
    k = d = 50.0
    out = np.full(len(C), np.nan)
    for i in range(len(C)):
        if i < n - 1:
            continue
        hh, ll = H[i - n + 1:i + 1].max(), L[i - n + 1:i + 1].min()
        rsv = 50.0 if hh == ll else (C[i] - ll) / (hh - ll) * 100
        k = k * 2 / 3 + rsv / 3
        d = d * 2 / 3 + k / 3
        out[i] = k
    return out


def build_rows(sym, df, mkt_bull=None):
    if df is None or len(df) < 80:
        return []
    O, H, L, C, V = (df[x].to_numpy(dtype=float) for x in ("Open", "High", "Low", "Close", "Volume"))
    n = len(C)
    ma = lambda w: np.convolve(C, np.ones(w) / w, mode="full")[:n]  # noqa: E731
    m5, m10, m20 = ma(5), ma(10), ma(20)
    v5 = np.convolve(V, np.ones(5) / 5, mode="full")[:n]
    K = _kd(H, L, C)
    dates = df.index.strftime("%Y-%m-%d")
    up = np.zeros(n, dtype=int)
    for i in range(1, n):
        up[i] = up[i - 1] + 1 if C[i] > C[i - 1] else 0
    out = []
    for i in range(25, n - 6):
        if not (C[i - 1] > 0 and C[i] > 0 and O[i + 1] > 0 and m5[i] > 0):
            continue
        g1 = (C[i - 1] / C[i - 2] - 1) * 100
        g2 = (C[i] / C[i - 1] - 1) * 100
        gap = (O[i + 1] / C[i] - 1) * 100
        mb = mkt_bull.get(dates[i]) if mkt_bull else None
        f = set()
        if up[i] >= 4: f.add("consec4")
        if up[i] >= 6: f.add("consec6")
        if C[i] / m5[i] - 1 >= 0.15: f.add("ext15")
        if C[i] / m5[i] - 1 >= 0.25: f.add("ext25")
        if m5[i] > m10[i] > m20[i]: f.add("maalign")
        if K[i] == K[i] and K[i] >= 80: f.add("kd80")
        if g2 >= 9.5: f.add("lu2")
        if g2 >= 9.5 and C[i] >= H[i] * 0.9995: f.add("locked")
        if v5[i - 1] > 0 and V[i] / v5[i - 1] >= 2: f.add("vol2")
        if gap >= 3: f.add("gap3")
        if gap >= 6: f.add("gap6")
        if mb is True: f.add("mkt_bull")
        if mb is False: f.add("mkt_bear")
        r = {"sym": sym, "date": dates[i], "f": f, "rules": {}}
        if g1 > 5 and g2 > 0 and gap > 1:                       # A 查8 當沖空
            r["rules"]["A"] = (O[i + 1] - C[i + 1]) / O[i + 1] * 100 - COST_DT
            r["rules"]["A_s3"] = (-3.0 if H[i + 1] >= O[i + 1] * 1.03 else (O[i + 1] - C[i + 1]) / O[i + 1] * 100) - COST_DT
        if g2 >= 9.5 and C[i] >= H[i] * 0.9995 and up[i] >= 2:   # B 查7 漲停收盤買→隔日開盤賣
            r["rules"]["B"] = gap - COST_LONG
        if v5[i - 1] > 0 and V[i] / v5[i - 1] >= 2 and C[i] >= H[i - 20:i].max():   # C 動能多方 持有5日
            r["rules"]["C"] = (C[i + 5] / O[i + 1] - 1) * 100 - COST_LONG
        if r["rules"]:
            out.append(r)
    return out


def _st(xs):
    if not xs:
        return {"n": 0}
    a = np.array(xs, dtype=float)
    return {"n": len(a), "avg": round(float(a.mean()), 3), "win": round(float((a > 0).mean() * 100), 1),
            "p5": round(float(np.percentile(a, 5)), 2)}


def analyze(rows, min_n=MIN_N, is_end=IS_END):
    res = {}
    for rule in ("A", "A_s3", "B", "C"):
        sub = [r for r in rows if rule in r["rules"]]
        base = [r["rules"][rule] for r in sub]
        e = {"all": _st(base), "feats": {}, "veto": []}
        b_avg = mean(base) if base else 0.0
        for fk, label in FEATS.items():
            has = [r for r in sub if fk in r["f"]]
            no = [r for r in sub if fk not in r["f"]]
            if not has:
                continue
            h_is = [r["rules"][rule] for r in has if r["date"] <= is_end]
            h_oos = [r["rules"][rule] for r in has if r["date"] > is_end]
            n_is = [r["rules"][rule] for r in no if r["date"] <= is_end]
            n_oos = [r["rules"][rule] for r in no if r["date"] > is_end]
            e["feats"][fk] = {"label": label, "has": _st([r["rules"][rule] for r in has]), "not": _st([r["rules"][rule] for r in no]),
                              "has_IS": round(mean(h_is), 3) if h_is else None, "has_OOS": round(mean(h_oos), 3) if h_oos else None}
            # 否決候選：出現時平均比沒出現差 ≥0.5 個百分點、IS／OOS 都比沒出現差、樣本夠
            if (len(has) >= min_n and h_is and h_oos and n_is and n_oos and mean([r["rules"][rule] for r in has]) <= mean([r["rules"][rule] for r in no]) - 0.5
                    and mean(h_is) < mean(n_is) and mean(h_oos) < mean(n_oos)):
                e["veto"].append({"feat": fk, "label": label, "n": len(has), "avg_has": round(mean([r["rules"][rule] for r in has]), 3),
                                  "avg_not": round(mean([r["rules"][rule] for r in no]), 3)})
        e["veto"].sort(key=lambda x: x["avg_has"] - x["avg_not"])
        res[rule] = e
    return res
