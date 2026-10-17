"""【2026-10-17】隔日沖（2天1夜）選股 5 年日K回測：收盤前買進、隔天開盤賣出（老闆：設計一個隔日沖的選股查指令）。
日K近似：以「當天收盤價」當 13:25 買進價（實際 13:15~13:25 買進會有些差距），隔天開盤價賣出；另列隔天收盤、第 2 天開盤。
母體條件：當天漲 3%~9.4%（未鎖漲停、買得到）。特徵：漲幅區間、收在當日高點 1% 內、量比（÷前 5 日均量）、
日線上升趨勢（前收在上揚的 MA20 上）、收紅K、大盤多頭（加權>MA60，選填）。
成本：來回 0.585%（手續費 0.285%＋證交稅 0.3%）。超額＝扣同日所有母體股票的平均（扣盤勢）。
t 值以「日」計；樣本內 ≤2024-03-31、樣本外之後；另列逐年。限制：母體＝目前成交值前 N 檔（存活者偏誤）、
未考慮漲停買不到／跌停賣不掉；純函式，可離線測試。"""
from __future__ import annotations
from statistics import mean, pstdev

import numpy as np

COST = 0.585
IS_END = "2024-03-31"
EXITS = ("o1", "c1", "o2")


def build_rows(sym, df, mkt_bull=None):
    if df is None or len(df) < 60:
        return []
    O, H, L, C, V = (df[x].to_numpy(dtype=float) for x in ("Open", "High", "Low", "Close", "Volume"))
    dates = df.index.strftime("%Y-%m-%d")
    ma20 = np.convolve(C, np.ones(20) / 20, mode="full")[:len(C)]
    out = []
    for i in range(26, len(df) - 2):
        if not (C[i - 1] > 0 and C[i] > 0 and O[i + 1] > 0 and O[i + 2] > 0 and V[i] > 0):
            continue
        g = (C[i] / C[i - 1] - 1) * 100
        if not 3 <= g < 9.4:
            continue
        v5 = V[i - 5:i].mean()
        vr = V[i] / v5 if v5 > 0 else 0
        up = bool(C[i - 1] > ma20[i - 1] and ma20[i - 1] > ma20[i - 6])
        r = {"sym": sym, "date": dates[i],
             "gb": "3~5%" if g < 5 else "5~7%" if g < 7 else "7~9.4%",
             "near_hi": bool(C[i] >= H[i] * 0.99),
             "vr": "量比<1" if vr < 1 else "量比1~2" if vr < 2 else "量比≥2",
             "up": up, "red": bool(C[i] > O[i]),
             "bull": (bool(mkt_bull.get(dates[i])) if mkt_bull else None),
             "o1": (O[i + 1] / C[i] - 1) * 100, "c1": (C[i + 1] / C[i] - 1) * 100, "o2": (O[i + 2] / C[i] - 1) * 100}
        out.append(r)
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


GROUPS = {
    "全部": lambda r: True,
    "漲3~5%": lambda r: r["gb"] == "3~5%", "漲5~7%": lambda r: r["gb"] == "5~7%", "漲7~9.4%": lambda r: r["gb"] == "7~9.4%",
    "收在高點1%內": lambda r: r["near_hi"], "離高點>1%": lambda r: not r["near_hi"],
    "量比<1": lambda r: r["vr"] == "量比<1", "量比1~2": lambda r: r["vr"] == "量比1~2", "量比≥2": lambda r: r["vr"] == "量比≥2",
    "上升趨勢": lambda r: r["up"], "非上升趨勢": lambda r: not r["up"],
    "大盤多頭": lambda r: r["bull"] is True, "大盤空頭": lambda r: r["bull"] is False,
    "組合A：收高＋上升趨勢": lambda r: r["near_hi"] and r["up"],
    "組合B：收高＋上升趨勢＋量比≥2": lambda r: r["near_hi"] and r["up"] and r["vr"] == "量比≥2",
    "組合C：漲5~9.4%＋收高＋上升趨勢": lambda r: r["gb"] != "3~5%" and r["near_hi"] and r["up"],
    "組合D：收高＋上升趨勢＋大盤多頭": lambda r: r["near_hi"] and r["up"] and r["bull"] is True,
    "組合E：漲5~9.4%＋收高＋上升趨勢＋量比≥2＋大盤多頭": lambda r: r["gb"] != "3~5%" and r["near_hi"] and r["up"]
    and r["vr"] == "量比≥2" and r["bull"] is True,
}


def analyze(rows):
    base = {h: {} for h in EXITS}
    for r in rows:
        for h in EXITS:
            base[h].setdefault(r["date"], []).append(r[h])
    bm = {h: {d: mean(v) for d, v in base[h].items()} for h in EXITS}
    years = sorted({r["date"][:4] for r in rows})
    res = {"n_rows": len(rows)}
    for name, pred in GROUPS.items():
        sub = [r for r in rows if pred(r)]
        if not sub:
            res[name] = {"n": 0}
            continue
        e = {"n": len(sub), "days": len({r["date"] for r in sub})}
        for h in EXITS:
            raw = [r[h] for r in sub]
            e[h] = {"net": round(mean(raw) - COST, 3), "win_net": round(100 * sum(1 for x in raw if x > COST) / len(raw), 1),
                    "ex": round(mean(r[h] - bm[h][r["date"]] for r in sub), 3),
                    "t_net": _tday([(r["date"], r[h] - COST) for r in sub]),
                    "IS_net": (round(mean([r[h] for r in sub if r["date"] <= IS_END]) - COST, 3)
                               if any(r["date"] <= IS_END for r in sub) else None),
                    "OOS_net": (round(mean([r[h] for r in sub if r["date"] > IS_END]) - COST, 3)
                                if any(r["date"] > IS_END for r in sub) else None)}
        e["by_year_o1_net"] = {y: (round(mean([r["o1"] for r in sub if r["date"][:4] == y]) - COST, 3)
                                   if any(r["date"][:4] == y for r in sub) else None) for y in years}
        res[name] = e
    return res


# ───────── 【2026-10-17 老闆：查指令也要有隔日沖】T 日強勢股 → T+1 各種應對方式（日K 5 年）─────────
# T 日選股（盤後）：漲停收盤（漲≥9.5% 且收在最高價）、爆量強勢（漲≥5% 且量比≥2）、兩者兼具、再加上升趨勢；選填「隔日沖分點買超佔比≥20%」。
# T+1 動作（日K 可算的）：
#   short_oc：開盤放空、收盤回補（當沖，成本 0.435%）；short_oc_gap：只有開盤比 T 收高>1% 才空
#   short_oc_s3：開盤空、盤中漲到開盤 +3% 就停損（日K 碰到即算停損，偏保守）
#   long_oc：開盤買、收盤賣（當沖做多，成本 0.435%）；long_oc_s2：開盤買、跌到開盤 −2% 停損
#   hold_sell_open：T 日收盤買、T+1 開盤賣（2天1夜，成本 0.585%）；hold_sell_close：T+1 收盤賣
# 「開盤 20~30 分鐘沒續強就出場」需要盤中資料，放在盤中 5 年回測（intraday_research.part_g）。
COST_DT = 0.435
T1_ACTIONS = ("short_oc", "short_oc_gap", "short_oc_s3", "long_oc", "long_oc_s2", "hold_sell_open", "hold_sell_close")


def build_rows_t1(sym, df, mkt_bull=None, dt_pct=None):
    if df is None or len(df) < 60:
        return []
    O, H, L, C, V = (df[x].to_numpy(dtype=float) for x in ("Open", "High", "Low", "Close", "Volume"))
    dates = df.index.strftime("%Y-%m-%d")
    ma20 = np.convolve(C, np.ones(20) / 20, mode="full")[:len(C)]
    out = []
    for i in range(26, len(df) - 1):
        if not (C[i - 1] > 0 and C[i] > 0 and O[i + 1] > 0 and V[i] > 0):
            continue
        g = (C[i] / C[i - 1] - 1) * 100
        if g < 5:
            continue
        v5 = V[i - 5:i].mean()
        vr = V[i] / v5 if v5 > 0 else 0
        lu = g >= 9.5 and C[i] >= H[i] * 0.999
        surge = vr >= 2
        up = bool(C[i - 1] > ma20[i - 1] and ma20[i - 1] > ma20[i - 6])
        grp = set()
        if lu:
            grp.add("lu")
        if surge:
            grp.add("surge")
        if lu and surge:
            grp.add("lu_surge")
        if lu and up:
            grp.add("lu_up")
        if not grp:
            continue
        p = (dt_pct or {}).get(dates[i])
        if p is not None:
            grp.add("dt20" if p >= 20 else "dt_low")
            if lu and p >= 20:
                grp.add("lu_dt20")
        b = (bool(mkt_bull.get(dates[i])) if mkt_bull else None)
        for gg in list(grp):
            if b is not None:
                grp.add(gg + ("_bull" if b else "_bear"))
        o1, h1, l1, c1 = O[i + 1], H[i + 1], L[i + 1], C[i + 1]
        r = {"sym": sym, "date": dates[i], "grp": grp, "gap": (o1 / C[i] - 1) * 100,
             "short_oc": (o1 - c1) / o1 * 100 - COST_DT,
             "short_oc_gap": ((o1 - c1) / o1 * 100 - COST_DT) if o1 > C[i] * 1.01 else None,
             "short_oc_s3": (-3.0 - COST_DT) if h1 >= o1 * 1.03 else ((o1 - c1) / o1 * 100 - COST_DT),
             "long_oc": (c1 / o1 - 1) * 100 - COST_DT,
             "long_oc_s2": (-2.0 - COST_DT) if l1 <= o1 * 0.98 else ((c1 / o1 - 1) * 100 - COST_DT),
             "hold_sell_open": (o1 / C[i] - 1) * 100 - COST,
             "hold_sell_close": (c1 / C[i] - 1) * 100 - COST}
        out.append(r)
    return out


T1_LABELS = {"lu": "T日漲停收盤", "surge": "T日漲≥5%＋量比≥2", "lu_surge": "漲停＋爆量", "lu_up": "漲停＋上升趨勢",
             "dt20": "隔日沖分點買超≥20%", "dt_low": "隔日沖分點<20%", "lu_dt20": "漲停＋隔日沖分點≥20%"}


def analyze_t1(rows):
    keys = sorted({g for r in rows for g in r["grp"]})
    res = {}
    for k in keys:
        sub = [r for r in rows if k in r["grp"]]
        base = k.replace("_bull", "").replace("_bear", "")
        e = {"label": T1_LABELS.get(base, base) + ("｜大盤多頭" if k.endswith("_bull") else "｜大盤空頭" if k.endswith("_bear") else ""),
             "n": len(sub), "days": len({r["date"] for r in sub}), "gap_mean": round(mean(r["gap"] for r in sub), 3)}
        for a in T1_ACTIONS:
            xs = [(r["date"], r[a]) for r in sub if r[a] is not None]
            if not xs:
                e[a] = {"n": 0}
                continue
            v = [x for _, x in xs]
            e[a] = {"n": len(v), "net": round(mean(v), 3), "win": round(100 * sum(1 for x in v if x > 0) / len(v), 1),
                    "t": _tday(xs),
                    "IS": round(mean([x for d, x in xs if d <= IS_END]), 3) if any(d <= IS_END for d, _ in xs) else None,
                    "OOS": round(mean([x for d, x in xs if d > IS_END]), 3) if any(d > IS_END for d, _ in xs) else None}
        res[k] = e
    return res
