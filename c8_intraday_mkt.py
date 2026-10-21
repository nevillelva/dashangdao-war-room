"""【2026-10-21 老闆：大盤大漲時做當沖空明顯有問題】查8 當沖空的「盤中版」：用 5 年 5 分K 快取看 09:30／10:00 時的大盤與個股狀態，
能不能在 10:00 前後提早平倉（或開盤就不做），避開「大盤大漲＋個股被拖到漲停鎖死」的虧損。純函式，可離線測試。
事件＝查8（昨日漲>5%＋今天續漲＋隔天開盤比昨收高≥MIN_GAP），開盤空、13:20 回補（現行規則），扣 0.435%。
大盤代理＝同一天快取內所有股票「10:00 價／開盤價−1」的平均（母體＝成交值前80＋隨機120＋產業龍頭，約 200 檔，不是加權指數，僅能當盤中強弱代理；當天有資料的股票<30 檔就不算）。
個股距離漲停＝(昨收×1.1−10:00價)/昨收（%）。
鎖死判斷：13:20 價距漲停<0.3% 視為鎖死、回補不了 → 留到隔天開盤回補（carry）；10:00 價距漲停<0.2% 視為已鎖、10:00 也平不了倉。
限制：快取只涵蓋部分股票年段（覆蓋率標在報告）、事件數少、5 分K 價位用收盤價近似、母體偏大型股、多重比較；結論需用大樣本再驗證。IS ≤2024-03-31／OOS 之後。"""
from __future__ import annotations

import c8_mkt_bt as M

COST_DT = M.COST_DT
MIN_PROXY_STOCKS = 30
LOCK_1320_PCT = 0.3
LOCK_1000_PCT = 0.2
LU_RATIO = 1.1
WHY = {"open_mismatch": 0, "price_out_of_range": 0}
MISMATCH_SAMPLES = []


def market_proxy(per, at="p1000"):
    """per：{sym: {date_str: day_stats}} → {date_str: 平均(at 價/開盤−1)*100}。"""
    acc = {}
    for sym, days in per.items():
        for d, s in days.items():
            o, p = s.get("open"), s.get(at)
            if o and p and o > 0 and p > 0:
                acc.setdefault(d, []).append((p / o - 1) * 100)
    return {d: sum(v) / len(v) for d, v in acc.items() if len(v) >= MIN_PROXY_STOCKS}


def build_events(sym, df, per_sym, m1000, m0930, min_gap=1.0):
    if df is None or len(df) < 6 or not per_sym:
        return []
    O, H, C = (df[x].to_numpy(dtype=float) for x in ("Open", "High", "Close"))
    dates = df.index.strftime("%Y-%m-%d")
    out = []
    for i in range(2, len(C) - 2):
        if not (C[i - 2] > 0 and C[i - 1] > 0 and C[i] > 0 and O[i + 1] > 0 and C[i + 1] > 0 and O[i + 2] > 0):
            continue
        g1, g2 = (C[i - 1] / C[i - 2] - 1) * 100, (C[i] / C[i - 1] - 1) * 100
        gap = (O[i + 1] / C[i] - 1) * 100
        if not (g1 > 5 and g2 > 0 and gap >= min_gap):
            continue
        d3 = dates[i + 1]
        if g1 > M.LIM or g2 > M.LIM or gap > M.LIM or abs((C[i + 1] / C[i] - 1) * 100) > M.LIM or abs((O[i + 2] / C[i + 1] - 1) * 100) > M.LIM:
            M.REJECTED["n"] += 1
            continue
        st = per_sym.get(d3)
        if not st or d3 not in m1000:
            continue
        p1000, p1320, p0930 = st.get("p1000"), st.get("p1320"), st.get("p0930")
        if not (p1000 and p1320 and p0930):
            continue
        # 【2026-10-21 查證】日K（yfinance）遇股票股利／減資會整段等比例調整，盤中（永豐）是實際成交價 → 用同一天開盤與收盤兩個比例
        # 確認是「整段等比例」才換算成實際價；兩個比例不一致才算資料對不上、退回。
        k = st.get("open", 0) / O[i + 1] if O[i + 1] > 0 else 0
        k2 = (st.get("close") or 0) / C[i + 1] if C[i + 1] > 0 else 0
        if abs(k - 1) > 0.02:
            if 0.3 < k < 3 and k2 > 0 and abs(k2 / k - 1) < 0.01:
                WHY["rescaled"] = WHY.get("rescaled", 0) + 1
            else:
                M.REJECTED["n"] += 1      # 盤中價與日K 對不上 → 退回
                WHY["open_mismatch"] += 1
                if len(MISMATCH_SAMPLES) < 60:
                    MISMATCH_SAMPLES.append({"sym": sym, "date": d3, "daily_open": round(float(O[i + 1]), 3), "ih_open": round(float(st.get("open") or 0), 3),
                                             "k2": round(k2, 4), "prev_close": round(float(C[i]), 3)})
                continue
        else:
            k = 1.0
        cI, o, o2 = C[i] * k, O[i + 1] * k, O[i + 2] * k
        if any(not (0.89 * cI <= p <= 1.11 * cI) for p in (p1000, p1320, p0930)):
            M.REJECTED["n"] += 1
            WHY["price_out_of_range"] += 1
            continue
        lim = cI * LU_RATIO
        dist10 = (lim - p1000) / cI * 100
        dist1320 = (lim - p1320) / cI * 100
        lock1320 = bool(dist1320 < LOCK_1320_PCT)
        lock1000 = bool(dist10 < LOCK_1000_PCT)
        r_hold = (o - p1320) / o * 100 - COST_DT                              # 現行：13:20 回補（鎖死則回補不了）
        r_carry = (o - o2) / o * 100 - COST_DT if lock1320 else r_hold  # 鎖死 → 隔天開盤回補
        r10 = (o - p1000) / o * 100 - COST_DT                                 # 10:00 平倉
        # 確認式進場：09:30／10:00 時價格已低於開盤才空（進場價＝當時價），13:20 回補（鎖死則 carry）
        def _v2(pe):
            if not pe or pe >= o:
                return None
            return ((pe - o2) / pe * 100 if lock1320 else (pe - p1320) / pe * 100) - COST_DT
        out.append({"sym": sym, "date": d3, "gap": gap, "r": r_hold, "r_carry": r_carry, "r10": r10,
                    "lock": lock1320, "touch": bool(st.get("hi1315") and st["hi1315"] >= lim * 0.9985) if st.get("hi1315") else False,
                    "r_v2_1000": _v2(p1000), "r_v2_0930": _v2(p0930), "lock1000": lock1000, "dist10": dist10, "sret10": (p1000 / o - 1) * 100,
                    "m1000": m1000[d3], "m0930": m0930.get(d3), "mgap": 0.0})
    return out


def _exit_rule(e, cond):
    """cond(e) 為真 → 10:00 平倉（若 10:00 已鎖死平不了，退回持有到 13:20 的 carry 結果）；否則持有到 13:20（鎖死則 carry）。"""
    if cond(e) and not e["lock1000"]:
        return e["r10"]
    return e["r_carry"]


RULES = {
    "現行：持有到13:20": lambda e: e["r_carry"],
    "10:00 大盤代理≥+0.7% 就平倉": lambda e: _exit_rule(e, lambda x: x["m1000"] >= 0.7),
    "10:00 大盤代理≥+1.0% 就平倉": lambda e: _exit_rule(e, lambda x: x["m1000"] >= 1.0),
    "10:00 個股距漲停≤1% 就平倉": lambda e: _exit_rule(e, lambda x: x["dist10"] <= 1.0),
    "10:00 個股距漲停≤2% 就平倉": lambda e: _exit_rule(e, lambda x: x["dist10"] <= 2.0),
    "10:00 個股已漲回高於開盤就平倉": lambda e: _exit_rule(e, lambda x: x["sret10"] > 0),
    "大盤≥+0.7% 或 距漲停≤1% 就平倉": lambda e: _exit_rule(e, lambda x: x["m1000"] >= 0.7 or x["dist10"] <= 1.0),
}
GROUPS = {
    "m1000": [("大盤代理10:00<0%", lambda e: e["m1000"] < 0), ("0~0.5%", lambda e: 0 <= e["m1000"] < 0.5),
              ("0.5~1%", lambda e: 0.5 <= e["m1000"] < 1), ("≥1%", lambda e: e["m1000"] >= 1)],
    "dist10": [("10:00距漲停≤1%", lambda e: e["dist10"] <= 1), ("1~3%", lambda e: 1 < e["dist10"] <= 3),
               ("3~6%", lambda e: 3 < e["dist10"] <= 6), (">6%", lambda e: e["dist10"] > 6)],
    "sret10": [("10:00個股低於開盤", lambda e: e["sret10"] < 0), ("高於開盤0~2%", lambda e: 0 <= e["sret10"] < 2), ("高於開盤≥2%", lambda e: e["sret10"] >= 2)],
}


def _rule_stats(rows, fn):
    sub = [dict(e, r=fn(e), r_carry=fn(e)) for e in rows]
    return M._split(sub, "r")


def analyze(events):
    res = {}
    for tag, sel in (("gap1", lambda e: True), ("gap3", lambda e: e["gap"] >= 3)):
        ev = [e for e in events if sel(e)]
        blk = {"n": len(ev), "days": len({e["date"] for e in ev}), "all": M._g(ev), "groups": {}, "rules": {}}
        for gk, items in GROUPS.items():
            blk["groups"][gk] = {name: M._g([e for e in ev if f(e)]) for name, f in items}
        for name, fn in RULES.items():
            blk["rules"][name] = _rule_stats(ev, fn)
        # 確認式進場 vs 開盤進場（同一批事件比較；都扣 0.435%，鎖死用 carry）
        blk["confirm"] = {}
        for tag2, key in (("10:00價<開盤才空(10:00進場)", "r_v2_1000"), ("09:30價<開盤才空(09:30進場)", "r_v2_0930")):
            sub = [e for e in ev if e.get(key) is not None]
            blk["confirm"][tag2] = {"confirm_entry": M._split([dict(e, r=e[key]) for e in sub], "r"),
                                    "same_events_open_entry": M._split([dict(e, r=e["r_carry"]) for e in sub], "r"),
                                    "skipped_events": len(ev) - len(sub)}
        res[tag] = blk
    return res
