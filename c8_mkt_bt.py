"""【2026-10-21 老闆：大盤大漲、個股接近漲停時做當沖空明顯有問題，回測要把大盤也考慮進去】查8 當沖空 × 大盤當日走勢（純函式，可離線測試）。
事件＝查8（昨日漲>5%＋今天續漲＋隔天開盤比昨收高≥1%），開盤空、當天收盤回補，扣 0.435%（與 conflict_bt 同口徑）。
大盤條件分兩類（關鍵是避免前視）：
  ・事前可知（09:00 前後就知道，可以進規則）：大盤開盤跳空 mgap、大盤前一日漲幅 mprev、前一日收盤是否在 60 日線上 bull、個股跳空相對大盤的超額 rel＝gap−mgap。
  ・事後才知（收盤後）：大盤當日開→收 moc、當日漲幅 mcc——**不能進規則**，只用來診斷「如果當天能預知大盤大漲，空單會差多少」，當作盤中大盤資訊的效益上限。
鎖漲停回補不了：個股當日收在漲停（≥9.4% 且收在最高）→ 日K 回測把虧損記在收盤價，但實際買不回來；
  r_carry 模擬「回補不了、留到隔天開盤回補」（仍乘以空單方向），比較帳面與實際。
母體＝成交值前 N 檔（存活者偏誤）；日K 看不到盤中路徑；多重比較；同一天的事件高度相關，所以另列「按日聚合」的 t 值。IS ≤2024-03-31／OOS 之後。"""
from __future__ import annotations

import numpy as np

COST_DT = 0.435
IS_END = "2024-03-31"
LU = 9.4          # 近似漲停（含跳動單位捨入）
MIN_N = 40


def build_mkt(df):
    """加權指數日K → {日期: {gap, prev, oc, cc, bull}}。bull＝前一日收盤>其 60 日均線（當日 09:00 已知）。"""
    if df is None or len(df) < 70:
        return {}
    O, C = df["Open"].to_numpy(dtype=float), df["Close"].to_numpy(dtype=float)
    dates = df.index.strftime("%Y-%m-%d")
    ma60 = np.full(len(C), np.nan)
    for i in range(59, len(C)):
        ma60[i] = C[i - 59:i + 1].mean()
    out = {}
    for i in range(2, len(C)):
        if not (O[i] > 0 and C[i] > 0 and C[i - 1] > 0 and C[i - 2] > 0):
            continue
        out[dates[i]] = {
            "gap": (O[i] / C[i - 1] - 1) * 100, "prev": (C[i - 1] / C[i - 2] - 1) * 100,
            "oc": (C[i] / O[i] - 1) * 100, "cc": (C[i] / C[i - 1] - 1) * 100,
            "bull": (bool(C[i - 1] > ma60[i - 1]) if ma60[i - 1] == ma60[i - 1] else None),
        }
    return out


def build_events(sym, df, mkt):
    if df is None or len(df) < 6:
        return []
    O, H, C = (df[x].to_numpy(dtype=float) for x in ("Open", "High", "Close"))
    dates = df.index.strftime("%Y-%m-%d")
    out = []
    for i in range(2, len(C) - 2):
        if not (C[i - 2] > 0 and C[i - 1] > 0 and C[i] > 0 and O[i + 1] > 0 and C[i + 1] > 0 and O[i + 2] > 0 and H[i + 1] > 0):
            continue
        g1, g2 = (C[i - 1] / C[i - 2] - 1) * 100, (C[i] / C[i - 1] - 1) * 100
        gap = (O[i + 1] / C[i] - 1) * 100
        if not (g1 > 5 and g2 > 0 and gap >= 1):
            continue
        d3 = dates[i + 1]
        m = mkt.get(d3)
        if m is None:
            continue
        r = (O[i + 1] - C[i + 1]) / O[i + 1] * 100 - COST_DT
        top = (C[i + 1] / C[i] - 1) * 100
        lock = bool(top >= LU and C[i + 1] >= H[i + 1] * 0.9995)
        touch = bool((H[i + 1] / C[i] - 1) * 100 >= LU)
        r_carry = (O[i + 1] - O[i + 2]) / O[i + 1] * 100 - COST_DT if lock else r
        sig_lu = bool(g2 >= LU and C[i] >= H[i] * 0.9995)      # 訊號日（昨日）收在漲停最高
        out.append({"sym": sym, "date": d3, "gap": gap, "r": r, "r_carry": r_carry, "lock": lock, "touch": touch,
                    "sig_lu": sig_lu, "mgap": m["gap"], "mprev": m["prev"], "moc": m["oc"], "mcc": m["cc"], "bull": m["bull"], "rel": gap - m["gap"]})
    return out


def _st(rows, key="r"):
    xs = np.array([e[key] for e in rows], dtype=float)
    if len(xs) == 0:
        return {"n": 0}
    days = {}
    for e in rows:
        days.setdefault(e["date"], []).append(e[key])
    dm = np.array([np.mean(v) for v in days.values()])
    t = float(dm.mean() / (dm.std(ddof=1) / np.sqrt(len(dm)))) if len(dm) > 2 and dm.std(ddof=1) > 0 else None
    return {"n": int(len(xs)), "days": len(days), "avg": round(float(xs.mean()), 3), "win": round(float((xs > 0).mean() * 100), 1),
            "med": round(float(np.median(xs)), 3), "p5": round(float(np.percentile(xs, 5)), 2), "t_day": (round(t, 2) if t is not None else None)}


def _split(rows, key="r"):
    a = [e for e in rows if e["date"] <= IS_END]
    b = [e for e in rows if e["date"] > IS_END]
    s = _st(rows, key)
    s["is"] = _st(a, key).get("avg") if a else None
    s["oos"] = _st(b, key).get("avg") if b else None
    s["n_is"], s["n_oos"] = len(a), len(b)
    return s


GROUPS = {
    "mgap": [("大盤開低≤-0.5%", lambda e: e["mgap"] <= -0.5), ("大盤開盤平(-0.5~0.5)", lambda e: -0.5 < e["mgap"] < 0.5),
             ("大盤開高0.5~1%", lambda e: 0.5 <= e["mgap"] < 1), ("大盤開高≥1%", lambda e: e["mgap"] >= 1)],
    "mprev": [("大盤前日跌", lambda e: e["mprev"] < 0), ("大盤前日漲0~1%", lambda e: 0 <= e["mprev"] < 1), ("大盤前日漲≥1%", lambda e: e["mprev"] >= 1)],
    "gap": [("個股開高1~3%", lambda e: e["gap"] < 3), ("開高3~5%", lambda e: 3 <= e["gap"] < 5), ("開高5~7%", lambda e: 5 <= e["gap"] < 7),
            ("開高≥7%（離漲停≤3%）", lambda e: e["gap"] >= 7)],
    "sig_lu": [("昨日收漲停最高", lambda e: e["sig_lu"]), ("昨日非漲停鎖", lambda e: not e["sig_lu"])],
    "bull": [("前收在60日線上", lambda e: e["bull"] is True), ("前收在60日線下", lambda e: e["bull"] is False)],
    "rel": [("個股跳空−大盤跳空<3%", lambda e: e["rel"] < 3), ("3~6%", lambda e: 3 <= e["rel"] < 6), ("≥6%", lambda e: e["rel"] >= 6)],
    "oracle_moc": [("【事後】大盤開→收≤-1%", lambda e: e["moc"] <= -1), ("【事後】-1~0%", lambda e: -1 < e["moc"] <= 0),
                   ("【事後】0~1%", lambda e: 0 < e["moc"] < 1), ("【事後】≥1%", lambda e: e["moc"] >= 1)],
    "oracle_mcc": [("【事後】大盤當日漲≥1%", lambda e: e["mcc"] >= 1), ("【事後】大盤當日跌≤-1%", lambda e: e["mcc"] <= -1)],
}
VETOS = {   # 候選否決（皆為事前可知）；oracle 另列、不當候選
    "大盤開高≥0.5%": lambda e: e["mgap"] >= 0.5, "大盤開高≥1%": lambda e: e["mgap"] >= 1,
    "大盤前日漲≥1%": lambda e: e["mprev"] >= 1, "前收在60日線上": lambda e: e["bull"] is True,
    "個股跳空超額<3%": lambda e: e["rel"] < 3,
}
ORACLE_VETOS = {"【事後】大盤開→收≥+0.5%": lambda e: e["moc"] >= 0.5, "【事後】大盤當日漲≥1%": lambda e: e["mcc"] >= 1}


def _g(rows):
    """同一組事件的兩種算法：booked＝帳面（收盤價回補）、carry＝鎖漲停回補不了、留到隔天開盤回補。"""
    n = len(rows)
    return {"booked": _split(rows, "r"), "carry": _split(rows, "r_carry"),
            "lock_pct": round(100 * sum(e["lock"] for e in rows) / n, 1) if n else None,
            "touch_pct": round(100 * sum(e["touch"] for e in rows) / n, 1) if n else None}


def _cand(vs, ks):
    return bool(vs["n_is"] >= MIN_N and vs["n_oos"] >= MIN_N and ks["n"] >= MIN_N and None not in (vs["is"], vs["oos"], ks["is"], ks["oos"])
                and vs["is"] <= ks["is"] - 0.5 and vs["oos"] <= ks["oos"] - 0.5)


RULES = {   # 提案規則（保留條件）：事前可知
    "現行（開高≥3%）": lambda e: True,
    "R1 昨日漲停鎖死不做": lambda e: not e["sig_lu"],
    "R2 開高≥7%不做": lambda e: e["gap"] < 7,
    "R3 R1+R2": lambda e: (not e["sig_lu"]) and e["gap"] < 7,
    "R4 開高3~7%且大盤開高<1%": lambda e: e["gap"] < 7 and e["mgap"] < 1,
}


def analyze(events):
    res = {}
    for tag, sel in (("gap1", lambda e: True), ("gap3", lambda e: e["gap"] >= 3)):
        ev = [e for e in events if sel(e)]
        blk = {"all": _g(ev), "groups": {}, "vetos": {}, "oracle_vetos": {}, "rules": {}}
        for gk, items in GROUPS.items():
            blk["groups"][gk] = {name: _g([e for e in ev if f(e)]) for name, f in items}
        for table, name_to_f in ((blk["vetos"], VETOS), (blk["oracle_vetos"], ORACLE_VETOS)):
            for name, f in name_to_f.items():
                v, k = _g([e for e in ev if f(e)]), _g([e for e in ev if not f(e)])
                table[name] = {"veto": v, "keep": k, "candidate": _cand(v["booked"], k["booked"]), "candidate_carry": _cand(v["carry"], k["carry"])}
        for name, f in RULES.items():
            blk["rules"][name] = _g([e for e in ev if f(e)])
        res[tag] = blk
    return res
