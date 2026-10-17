"""【2026-10-17】查8「昨日強勢＋今天續漲」當空方候選的短天期回測（老闆：這比較像隔日沖）。
問題：跌是集中在「隔天」（隔日沖賣壓）還是慢慢跌？扣掉放空成本後還剩多少？
進場：訊號日（第2天）隔天開盤放空。出場：當天收盤（當沖）、第1/2/3/5/10 個交易日收盤。
報酬以「放空」方向計（股價跌＝賺）；另列扣成本淨報酬：當沖 0.435%（手續費 0.285%＋當沖證交稅 0.15%），
非當沖 0.665%（手續費 0.285%＋證交稅 0.3%＋融券／借券費估 0.08%）。超額＝扣同日全池平均。
分組：原查8（昨漲>5%）、新查8（＋今天續漲）、第1天漲停（≥9.5%）＋第2天續漲（最像隔日沖）、新查8＋第2天爆量（量比≥2）。
限制：未考慮券源、平盤下放空限制、漲停無法回補等；母體＝目前成交值前 N 檔（存活者偏誤）；純函式。"""
from __future__ import annotations
from statistics import mean, pstdev

import numpy as np

COST_DT, COST_ON = 0.435, 0.665
GROUPS = {"old": "查8 舊：昨漲>5%", "new": "查8 新：昨漲>5%＋今天續漲",
          "lu": "第1天漲停(≥9.5%)＋第2天續漲（最像隔日沖）", "new_vol": "查8 新＋第2天量比≥2",
          "new_gapfade": "查8 新＋第3天開高（>前收1%）", "base": "隨機基準"}
for _g in ("old", "new", "lu"):
    GROUPS[_g + "_bull"] = GROUPS[_g] + "｜大盤多頭（加權>MA60）"
    GROUPS[_g + "_bear"] = GROUPS[_g] + "｜大盤空頭"
HOLDS = ("d0", "d0_s2", "d0_s3", "d0_s5", "d1", "d2", "d3", "d5", "d10")


def build_rows(sym, df, mkt_bull=None):
    if df is None or len(df) < 40:
        return []
    O, H, L, C, V = (df[x].to_numpy(dtype=float) for x in ("Open", "High", "Low", "Close", "Volume"))
    v5 = np.convolve(V, np.ones(5) / 5, mode="full")[:len(V)]
    dates = df.index.strftime("%Y-%m-%d")
    out = []
    for i in range(6, len(df) - 11):
        if not (C[i - 2] > 0 and C[i - 1] > 0 and O[i + 1] > 0):
            continue
        g1 = (C[i - 1] / C[i - 2] - 1) * 100
        g2 = (C[i] / C[i - 1] - 1) * 100
        grp = {"base"}
        if g1 > 5:
            grp.add("old")
            if g2 > 0:
                grp.add("new")
                if v5[i - 1] > 0 and V[i] / v5[i - 1] >= 2:
                    grp.add("new_vol")
                if O[i + 1] > C[i] * 1.01:
                    grp.add("new_gapfade")
            if g1 >= 9.5 and g2 > 0:
                grp.add("lu")
        if mkt_bull:
            _b = mkt_bull.get(dates[i])
            if _b is not None:
                for _g in ("old", "new", "lu"):
                    if _g in grp:
                        grp.add(_g + ("_bull" if _b else "_bear"))
        e = O[i + 1]
        r = {"d0": (e - C[i + 1]) / e * 100}
        # 當沖加停損：當天最高價碰到進場價 +s% 就以 −s% 出場（日K 無法知道先後，碰到即視為停損，偏保守）
        for s_ in (2, 3, 5):
            r[f"d0_s{s_}"] = -float(s_) if H[i + 1] >= e * (1 + s_ / 100) else r["d0"]
        for h in (1, 2, 3, 5, 10):
            r[f"d{h}"] = (e - C[i + 1 + h]) / e * 100
        mae = (max(H[i + 1: i + 11]) / e - 1) * 100   # 放空的最大不利＝往上衝多少
        out.append({"sym": sym, "date": dates[i], "grp": grp, **r, "mae_up10": mae})
    return out


def _tday(pairs):
    byd = {}
    for d, x in pairs:
        byd.setdefault(d, []).append(x)
    dm = [mean(v) for v in byd.values()]
    if len(dm) < 5:
        return None
    sd = pstdev(dm) or 1e-9
    return round(mean(dm) / sd * len(dm) ** 0.5, 2)


def _net(xs, cost):
    return round(float(mean(xs)) - cost, 3) if xs else None


def analyze(rows, is_end="2024-03-31"):
    base = {h: {} for h in HOLDS}
    for r in rows:
        for h in HOLDS:
            base[h].setdefault(r["date"], []).append(r[h])
    bm = {h: {d: mean(v) for d, v in base[h].items()} for h in HOLDS}
    res = {}
    for g in GROUPS:
        sub = [r for r in rows if g in r["grp"]]
        if not sub:
            res[g] = {"label": GROUPS[g], "n": 0}
            continue
        e = {"label": GROUPS[g], "n": len(sub), "mae_up10_mean": round(float(mean(r["mae_up10"] for r in sub)), 2)}
        for h in HOLDS:
            raw = [r[h] for r in sub]
            ex = [r[h] - bm[h][r["date"]] for r in sub]
            cost = COST_DT if h.startswith("d0") else COST_ON
            e[h] = {"raw": round(float(mean(raw)), 3), "ex": round(float(mean(ex)), 3), "net": round(float(mean(raw)) - cost, 3),
                    "win_net": round(100 * sum(1 for x in raw if x > cost) / len(raw), 1),
                    "t_ex": _tday([(r["date"], r[h] - bm[h][r["date"]]) for r in sub]),
                    "IS_net": _net([r[h] for r in sub if r["date"] <= is_end], cost),
                    "OOS_net": _net([r[h] for r in sub if r["date"] > is_end], cost)}
        res[g] = e
    return res
