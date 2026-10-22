"""【2026-10-21 老闆：10:00 只能當過濾，那 11:00、12:45 或其他時點是否更適合進場？查7 13:15 不採用，要不要改別的時點（例如 12:45）？
不要只測網路上常說的時間點，要自己驗證給出最佳答案】盤中進場時點「全網格」5 年回測（純函式，可離線測試）。

資料：盤中 5 年 5 分K 快取（intraday_hist，約 168 檔＝成交值前 80＋隨機 120＋產業龍頭；覆蓋 87%），日K 訊號沿用 cmd_deep_bt（與線上同一套）。
時點網格 GRID：09:05～13:25 共 14 個（「12:45」＝12:40～12:45 那根 5 分K 的收盤價）；開盤價＝當天第一根 5 分K 開盤。
四個研究：
  A 多方各指令（做多主力、查3、查6、查2、動能、查4、隨機基準）隔天「何時進場」：在時點 T 買、持有到該指令的最佳天數收盤；
    另測確認條件（T 之前守住開盤、站上均價線、拉回到開盤下 1%、強於開盤 1%）。報酬以「比例」組合（日K 開盤→出場 × 盤中開盤/T 價），
    不受日K 還原價影響。比較基準＝同一批事件開盤就買。
  B 查7 型（隔日沖）：在時點 T、當天漲幅落在某區間且「還沒鎖漲停」的股票買進，隔天開盤（或隔天各時點）賣——找有沒有正報酬的時點。
  C 查7 已持有者「隔天何時賣」：漲停鎖死收盤的股票，隔天 09:00 開盤～13:25 各時點賣出比較。
  D 尾盤時段資金流向（含 12:45 說法）：各時段（11:00～11:30、12:00～12:30、12:30～13:00、12:45～13:15、12:45～13:25、13:00～13:25、13:25～收盤）
    的漲跌，能不能預測「隔天」報酬（同一天跨股票排序，比較最強 1/5 與最弱 1/5 的隔天報酬差；扣掉當天母體平均）。
  另附：成交量時段分布（每 5 分鐘佔全日比例）。
成本：做多 0.585%（買賣手續費＋證交稅）。IS ≤2024-03-31／OOS 之後；t 值按日聚合。
限制：母體偏大型（快取 168 檔）、5 分K 收盤價近似成交價、漲停排隊買不到未完全模擬（B 已排除 T 時已鎖漲停者）、多重比較（14 時點×多條件），
結論要看「樣本內外一致」且差距夠大才採用。"""
from __future__ import annotations

from statistics import mean, pstdev

import numpy as np

COST = 0.585
IS_END = "2024-03-31"
GRID = ["09:05", "09:15", "09:30", "10:00", "10:30", "11:00", "11:30", "12:00", "12:30", "12:45", "13:00", "13:15", "13:20", "13:25"]
WINDOWS = [("11:00", "11:30"), ("12:00", "12:30"), ("12:30", "13:00"), ("12:45", "13:15"), ("12:45", "13:25"), ("13:00", "13:25"), ("13:25", "close")]
HOLD = {"c32_and": 20, "c3": 60, "c6": 20, "c2": 10, "m1": 5, "c4": 10, "base": 10}
LABEL = {"c32_and": "做多主力(查3∧查2) 持20日", "c3": "查3 價值 持60日", "c6": "查6 營收雙增 持20日", "c2": "查2 拉回守線 持10日",
         "m1": "動能(量比≥2＋創20日新高) 持5日", "c4": "查4 投信買超 持10日", "base": "隨機基準 持10日"}


def _m(hhmm):
    return int(hhmm[:2]) * 60 + int(hhmm[3:])


GRID_M = [_m(x) for x in GRID]


def day_profiles(data):
    """intraday_hist 5 分K arrays → {日期字串: profile}。profile：open、close、vol、p/lo/hi/vw/cv（各 GRID 時點之前）、bars（(分鐘, 量)）。"""
    out = {}
    if data is None or not len(data["t"]):
        return out
    t = np.asarray(data["t"], dtype=np.int64)
    day = t // 1440
    mins = t % 1440
    o, h, l, c, v = (np.asarray(data[k], dtype=np.float64) for k in ("o", "h", "l", "c", "v"))
    bounds = np.flatnonzero(np.diff(day)) + 1
    starts = np.concatenate([[0], bounds])
    ends = np.concatenate([bounds, [len(t)]])
    import datetime as _dt
    for s, e in zip(starts, ends):
        mm = mins[s:e]
        if not len(mm) or mm[0] > 9 * 60 + 5:          # 第一根不是 09:00／09:05 → 開盤價不可靠，略過
            continue
        oo, hh, ll, cc, vv = o[s:e], h[s:e], l[s:e], c[s:e], v[s:e]
        if not (oo[0] > 0 and cc[-1] > 0):
            continue
        d = (_dt.date(1970, 1, 1) + _dt.timedelta(days=int(day[s]))).strftime("%Y-%m-%d")
        pf = {"open": float(oo[0]), "close": float(cc[-1]), "vol": float(vv.sum()), "p": {}, "lo": {}, "hi": {}, "vw": {}, "cv": {},
              "slo": {}, "shi": {}, "bars": [(int(x), float(y)) for x, y in zip(mm, vv)]}
        kprev = 0
        for T, TM in zip(GRID, GRID_M):
            k = int(np.searchsorted(mm, TM, side="left"))       # 起始時間 < T 的 K 棒
            if k > kprev:                                       # 2026-10-22：上一個網格點到 T 之間這段的最低／最高（多時點條件用）
                pf["slo"][T] = float(ll[kprev:k].min())
                pf["shi"][T] = float(hh[kprev:k].max())
            kprev = max(kprev, k)
            if k == 0:
                continue
            pf["p"][T] = float(cc[k - 1])
            pf["lo"][T] = float(ll[:k].min())
            pf["hi"][T] = float(hh[:k].max())
            cvv = float(vv[:k].sum())
            pf["cv"][T] = cvv
            pf["vw"][T] = float((cc[:k] * vv[:k]).sum() / cvv) if cvv > 0 else None
        out[d] = pf
    return out


def price_at(pf, T):
    if T == "open":
        return pf["open"]
    if T == "close":
        return pf["close"]
    return pf["p"].get(T)


# ───────────────────────── A：多方進場時點 ─────────────────────────
CONDS = {
    "無條件": lambda pf, T, p: True,
    "守住開盤(T前最低≥開盤×0.995且T價>開盤)": lambda pf, T, p: pf["lo"].get(T, 0) >= pf["open"] * 0.995 and p > pf["open"],
    "站上均價線(T價≥T前均價)": lambda pf, T, p: pf["vw"].get(T) is not None and p >= pf["vw"][T],
    "拉回(T價≤開盤×0.99)": lambda pf, T, p: p <= pf["open"] * 0.99,
    "強於開盤1%(T價≥開盤×1.01)": lambda pf, T, p: p >= pf["open"] * 1.01,
}


def long_events(sym, df, rows, prof, keys=tuple(HOLD)):
    """rows：cmd_deep_bt.build_rows（含 hits）；df：同一份日K；prof：day_profiles。
    回傳事件：{k, date(訊號日), ed(進場日), r_open(開盤買→出場，扣成本), ratio[T]=開盤/T 價, cond[T]=通過的條件集合}。"""
    if df is None or not rows or not prof:
        return []
    O, C = df["Open"].to_numpy(dtype=float), df["Close"].to_numpy(dtype=float)
    ds = list(df.index.strftime("%Y-%m-%d"))
    idx = {d: k for k, d in enumerate(ds)}
    out = []
    for r in rows:
        i = idx.get(r["date"])
        if i is None:
            continue
        hits = set(r["hits"]) & set(keys)
        if "base" in hits and i % 10:
            hits.discard("base")
        if not hits or i + 1 >= len(ds):
            continue
        pf = prof.get(ds[i + 1])
        if not pf or not (O[i + 1] > 0):
            continue
        # 盤中開盤與日K 開盤（還原價）比例要與收盤比例一致，才確認是同一天（等比例調整可接受）
        k1, k2 = pf["open"] / O[i + 1], pf["close"] / C[i + 1] if C[i + 1] > 0 else 0
        if not (k2 > 0 and abs(k1 / k2 - 1) < 0.01):
            continue
        ratio, cond = {}, {}
        for T in GRID:
            p = pf["p"].get(T)
            if not p or p <= 0:
                continue
            ratio[T] = pf["open"] / p
            cond[T] = {cn for cn, f in CONDS.items() if f(pf, T, p)}
        for key in hits:
            h = HOLD[key]
            if i + h >= len(C) or not C[i + h] > 0:
                continue
            gross = C[i + h] / O[i + 1]                # 開盤買→第 h 日收盤
            out.append({"k": key, "date": r["date"], "ed": ds[i + 1], "g": gross, "ratio": ratio, "cond": cond})
    return out


def _t_day(pairs):
    byd = {}
    for d, x in pairs:
        byd.setdefault(d, []).append(x)
    dm = [mean(v) for v in byd.values()]
    if len(dm) < 5:
        return None
    sd = pstdev(dm) or 1e-9
    return round(mean(dm) / sd * len(dm) ** 0.5, 2)


def _st(pairs):
    if not pairs:
        return {"n": 0}
    xs = np.array([x for _, x in pairs])
    isx = [x for d, x in pairs if d <= IS_END]
    osx = [x for d, x in pairs if d > IS_END]
    return {"n": len(xs), "avg": round(float(xs.mean()), 3), "win": round(float((xs > 0).mean() * 100), 1), "t": _t_day(pairs),
            "is": round(mean(isx), 3) if isx else None, "oos": round(mean(osx), 3) if osx else None}


def analyze_long(events):
    res = {}
    for key in HOLD:
        ev = [e for e in events if e["k"] == key]
        if not ev:
            continue
        base = [(e["date"], (e["g"] - 1) * 100 - COST) for e in ev]
        blk = {"label": LABEL[key], "open_all": _st(base), "grid": {}}
        for T in GRID:
            row = {}
            for cn in CONDS:
                take = [e for e in ev if T in e["ratio"] and cn in e["cond"].get(T, set())]
                rT = [(e["date"], (e["g"] * e["ratio"][T] - 1) * 100 - COST) for e in take]
                ro = [(e["date"], (e["g"] - 1) * 100 - COST) for e in take]          # 同一批事件若開盤就買
                diff = [(d, a - b) for (d, a), (_, b) in zip(rT, ro)]
                s = _st(rT)
                s["coverage_pct"] = round(100 * len(take) / len(ev), 1)
                s["vs_open_same"] = _st(diff)                   # 時點效果（同一批股票，晚一點買 vs 開盤買）
                s["vs_open_all"] = round(s["avg"] - blk["open_all"]["avg"], 3) if s.get("n") else None   # 條件＋時點的總效果
                row[cn] = s
            blk["grid"][T] = row
        res[key] = blk
    return res


# ───────────────────────── B：查7 型盤中買進、隔天賣 ─────────────────────────
BANDS = [(3, 5), (5, 7), (7, 8.5), (8.5, 9.4)]


def consecutive(prof_days, cal_next):
    """只保留「前後都是連續交易日」的三元組索引 k（prof_days[k-1], [k], [k+1]）。cal_next：{交易日: 下一個交易日}。"""
    ok = set()
    for k in range(1, len(prof_days) - 1):
        a, b, c = prof_days[k - 1][0], prof_days[k][0], prof_days[k + 1][0]
        if cal_next.get(a) == b and cal_next.get(b) == c:
            ok.add(k)
    return ok


def overnight_events(sym, prof_days, bull=None, ok=None):
    """prof_days：依日期排序的 [(日期, profile)]（需連續交易日：前一天收盤＝昨收）。
    對每個時點 T：漲幅 up＝T 價/昨收−1 落在 BANDS 且 T 價<昨收×1.1×0.998（未鎖漲停）→ 買進；
    出場：隔天開盤／隔天 09:30／隔天收盤。另記錄「當天最後收在漲停鎖死」（事後，只做診斷）。"""
    out = []
    for k in range(1, len(prof_days) - 1):
        if ok is not None and k not in ok:
            continue
        d0, p0 = prof_days[k - 1]
        d1, p1 = prof_days[k]
        d2, p2 = prof_days[k + 1]
        pc = p0["close"]
        if not pc > 0:
            continue
        lim = pc * 1.1
        if p1["open"] / pc - 1 > 0.106 or p2["open"] / p1["close"] - 1 > 0.106 or p2["open"] / p1["close"] - 1 < -0.106:
            continue                                            # 價格不連續（減資等）
        lock_close = p1["close"] >= lim * 0.997 and p1["close"] >= max(p1["hi"].values() or [0]) * 0.999
        for T in ("12:00", "12:30", "12:45", "13:00", "13:15", "13:20", "13:25"):
            p = p1["p"].get(T)
            if not p:
                continue
            up = (p / pc - 1) * 100
            band = next((f"{a}~{b}%" for a, b in BANDS if a <= up < b), None)
            if band is None or p >= lim * 0.998:
                continue
            near_hi = p >= p1["hi"].get(T, p) * 0.99
            out.append({"date": d1, "T": T, "band": band, "near_hi": near_hi, "bull": (bull or {}).get(d0),
                        "lock_close": lock_close,
                        "r_open": (p2["open"] / p - 1) * 100 - COST,
                        "r_0930": ((p2["p"].get("09:30") or p2["open"]) / p - 1) * 100 - COST,
                        "r_close2": (p2["close"] / p - 1) * 100 - COST,
                        "r_close1": (p1["close"] / p - 1) * 100})   # 當天 T→收盤（不扣成本，看買進後當天走勢）
    return out


def analyze_overnight(events):
    res = {}
    for T in ("12:00", "12:30", "12:45", "13:00", "13:15", "13:20", "13:25"):
        evT = [e for e in events if e["T"] == T]
        blk = {}
        for band in [f"{a}~{b}%" for a, b in BANDS] + ["全部"]:
            for nh in (None, True):
                sub = [e for e in evT if (band == "全部" or e["band"] == band) and (nh is None or e["near_hi"])]
                key = band + ("｜貼近當日高點" if nh else "")
                if not sub:
                    continue
                blk[key] = {"賣隔天開盤": _st([(e["date"], e["r_open"]) for e in sub]),
                            "賣隔天09:30": _st([(e["date"], e["r_0930"]) for e in sub]),
                            "賣隔天收盤": _st([(e["date"], e["r_close2"]) for e in sub]),
                            "大盤多頭_賣隔天開盤": _st([(e["date"], e["r_open"]) for e in sub if e["bull"] is True]),
                            "【事後】當天收漲停鎖死比例%": round(100 * sum(e["lock_close"] for e in sub) / len(sub), 1),
                            "【事後】若收漲停_賣隔天開盤": _st([(e["date"], e["r_open"]) for e in sub if e["lock_close"]]),
                            "【事後】若沒收漲停_賣隔天開盤": _st([(e["date"], e["r_open"]) for e in sub if not e["lock_close"]])}
        res[T] = blk
    return res


# ───────────────────────── C：查7 已持有者隔天何時賣 ─────────────────────────
def hold_exit_events(prof_days, ok=None):
    """前一天收在漲停鎖死（收盤≥昨收×1.1×0.997 且收在最高）→ 假設以收盤價持有，隔天各時點賣出的報酬（扣成本）。"""
    out = []
    for k in range(1, len(prof_days) - 1):
        if ok is not None and k not in ok:
            continue
        d0, p0 = prof_days[k - 1]
        d1, p1 = prof_days[k]
        d2, p2 = prof_days[k + 1]
        if not p0["close"] > 0:
            continue
        lim = p0["close"] * 1.1
        hi1 = max(p1["hi"].values()) if p1["hi"] else p1["close"]
        if not (p1["close"] >= lim * 0.997 and p1["close"] >= hi1 * 0.999):
            continue
        if abs(p2["open"] / p1["close"] - 1) > 0.106:
            continue
        rec = {"date": d1, "開盤": (p2["open"] / p1["close"] - 1) * 100 - COST, "收盤": (p2["close"] / p1["close"] - 1) * 100 - COST}
        for T in GRID:
            p = p2["p"].get(T)
            if p:
                rec[T] = (p / p1["close"] - 1) * 100 - COST
        rec["open_gap"] = (p2["open"] / p1["close"] - 1) * 100
        out.append(rec)
    return out


def analyze_hold_exit(events):
    res = {}
    for col in ["開盤"] + GRID + ["收盤"]:
        res[col] = _st([(e["date"], e[col]) for e in events if col in e])
    # 依開盤跳空分組：開高很多 vs 開平
    for gname, f in (("開盤跳空≥5%", lambda e: e["open_gap"] >= 5), ("開盤跳空0~5%", lambda e: 0 <= e["open_gap"] < 5), ("開盤跳空<0", lambda e: e["open_gap"] < 0)):
        sub = [e for e in events if f(e)]
        res[gname] = {col: _st([(e["date"], e[col]) for e in sub if col in e]) for col in ("開盤", "09:05", "09:15", "09:30", "10:00", "收盤")}
    return res


# ───────────────────────── D：尾盤時段資金流向 → 隔天 ─────────────────────────
def window_rows(sym, prof_days, cal_next=None):
    """每檔每天：各時段漲跌、時段量占全日比；隔天：隔夜（收→開）與隔天收盤（收→收）。"""
    out = []
    for k in range(len(prof_days) - 1):
        if cal_next is not None and cal_next.get(prof_days[k][0]) != prof_days[k + 1][0]:
            continue
        d1, p1 = prof_days[k]
        d2, p2 = prof_days[k + 1]
        if not (p1["close"] > 0 and p2["open"] > 0) or abs(p2["open"] / p1["close"] - 1) > 0.106:
            continue
        w = {}
        for a, b in WINDOWS:
            pa, pb = price_at(p1, a), price_at(p1, b)
            if not (pa and pb):
                continue
            ma, mb = _m(a), (_m(b) if b != "close" else 24 * 60)
            vol = sum(v for m, v in p1["bars"] if ma <= m < mb)
            w[f"{a}-{b}"] = ((pb / pa - 1) * 100, vol / p1["vol"] if p1["vol"] > 0 else None)
        out.append({"sym": sym, "date": d1, "w": w, "on": (p2["open"] / p1["close"] - 1) * 100, "nx": (p2["close"] / p1["close"] - 1) * 100})
    return out


def analyze_windows(rows):
    """每天跨股票排序（至少 30 檔）：最強 1/5 減最弱 1/5 的隔天報酬（已扣當天母體平均）；另看「量大（時段量占比前 1/3）」時。"""
    byd = {}
    for r in rows:
        byd.setdefault(r["date"], []).append(r)
    res = {}
    for a, b in WINDOWS:
        key = f"{a}-{b}"
        sp_on, sp_nx, sp_nx_vol = [], [], []
        for d, rs in byd.items():
            xs = [r for r in rs if key in r["w"]]
            if len(xs) < 30:
                continue
            m_on = mean(r["on"] for r in xs)
            m_nx = mean(r["nx"] for r in xs)
            xs.sort(key=lambda r: r["w"][key][0])
            q = len(xs) // 5
            lo, hi = xs[:q], xs[-q:]
            sp_on.append((d, mean(r["on"] - m_on for r in hi) - mean(r["on"] - m_on for r in lo)))
            sp_nx.append((d, mean(r["nx"] - m_nx for r in hi) - mean(r["nx"] - m_nx for r in lo)))
            vv = sorted(r["w"][key][1] or 0 for r in xs)
            cut = vv[2 * len(vv) // 3]
            hv = [r for r in xs if (r["w"][key][1] or 0) >= cut]
            if len(hv) >= 10:
                hv.sort(key=lambda r: r["w"][key][0])
                q2 = len(hv) // 5 or 1
                sp_nx_vol.append((d, mean(r["nx"] - m_nx for r in hv[-q2:]) - mean(r["nx"] - m_nx for r in hv[:q2])))
        res[key] = {"強減弱_隔夜(收→開)": _st(sp_on), "強減弱_隔天收盤(收→收)": _st(sp_nx), "量大時_強減弱_隔天收盤": _st(sp_nx_vol)}
    return res


def volume_profile(prof_iter):
    """每 5 分鐘量占全日比例的平均（%）。"""
    acc, n = {}, 0
    for pf in prof_iter:
        if not pf["vol"] > 0:
            continue
        n += 1
        for m, v in pf["bars"]:
            acc[m] = acc.get(m, 0.0) + v / pf["vol"]
    return {f"{m // 60:02d}:{m % 60:02d}": round(100 * s / n, 2) for m, s in sorted(acc.items())} if n else {}


# ───────────────────────── E：查8 空單何時進場 ─────────────────────────
SHORT_GRID = ["open", "09:05", "09:15", "09:30", "10:00", "10:30", "11:00", "12:00"]
COST_DT = 0.435


def short_events(sym, prof_days, cal_next):
    """查8：d−1 漲>5%、d0 續漲、d1 開盤比 d0 收盤高 3～7% → d1 在時點 T 放空、13:20 回補；13:20 已鎖漲停（補不了）→ d2 開盤回補。"""
    out = []
    for k in range(2, len(prof_days) - 1):
        (dm, pm), (d0, p0), (d1, p1), (d2, p2) = prof_days[k - 2], prof_days[k - 1], prof_days[k], prof_days[k + 1]
        if not (cal_next.get(dm) == d0 and cal_next.get(d0) == d1 and cal_next.get(d1) == d2):
            continue
        if k - 3 < 0:
            continue
        dmm, pmm = prof_days[k - 3]
        if cal_next.get(dmm) != dm or not (pmm["close"] > 0 and pm["close"] > 0 and p0["close"] > 0):
            continue
        g1, g2 = (pm["close"] / pmm["close"] - 1) * 100, (p0["close"] / pm["close"] - 1) * 100
        gap = (p1["open"] / p0["close"] - 1) * 100
        if not (5 < g1 <= 10.6 and 0 < g2 <= 10.6 and 3 <= gap < 7):
            continue
        lim = p0["close"] * 1.1
        p1320 = p1["p"].get("13:20")
        if not p1320:
            continue
        lock = p1320 >= lim * 0.997
        cover = p2["open"] if lock else p1320
        rec = {"date": d1, "lock": lock}
        for T in SHORT_GRID:
            p = price_at(p1, T)
            if not p or p >= lim * 0.998:       # 已在漲停價放不了空
                continue
            rec[T] = (p - cover) / p * 100 - COST_DT
        out.append(rec)
    return out


def analyze_short(events):
    return {T: _st([(e["date"], e[T]) for e in events if T in e]) for T in SHORT_GRID} | {
        "n_events": len(events), "lock_pct": round(100 * sum(e["lock"] for e in events) / len(events), 1) if events else None}


# ───────────────────────── F：當沖多方（2026-10-22 新增）─────────────────────────
# 起因：10/22 系統 10:06 自動做多 8 檔（短線動能）、13:25 強制平倉，8 檔全虧（平均 −2.65%）。
# 線上實際做法＝「訊號日隔天 10:00 進場、13:25 出場」的當沖，但先前回測都是持有多日，這個做法沒有被回測過。
# 事件：每個 (股票, 日期) 一筆，g＝13:25 價／10:00 價（5 分K 收盤近似成交價）。做多報酬 (g−1)×100−成本；做空反向。
# 分組：k＝訊號（各指令的訊號日隔天；"ALL"＝無訊號基準，母體所有交易日）。拆「10:00 價是否低於開盤」「大盤 60 日線上／下」。
DT_COST = 0.435          # 當沖來回成本（手續費 0.1425%×2＋當沖證交稅 0.15%）
DT_IN, DT_OUT = "10:00", "13:25"


def _dt_row(pf, bull_flag):
    p1, p2 = pf["p"].get(DT_IN), pf["p"].get(DT_OUT)
    if not p1 or not p2 or p1 <= 0 or p2 <= 0 or not pf["open"] > 0:
        return None
    # 2026-10-22：進場後（10:00～13:25）最低／最高，供當沖停損回測
    m1, m2 = _m(DT_IN), _m(DT_OUT)
    segs = [T for T in GRID if m1 < _m(T) <= m2]
    los = [pf.get("slo", {}).get(T) for T in segs]
    his = [pf.get("shi", {}).get(T) for T in segs]
    lo = min([x for x in los if x], default=min(p1, p2))
    hi = max([x for x in his if x], default=max(p1, p2))
    return (p2 / p1, p1 < pf["open"], bull_flag, lo / p1, hi / p1)


def daytrade_baseline(prof, bull=None):
    """無訊號基準：母體所有交易日各一筆（key=ALL）。回傳 [(k, date, g, below_open, bull)]。"""
    out = []
    for d, pf in prof.items():
        r = _dt_row(pf, (bull or {}).get(d))
        if r:
            out.append(("ALL", d, *r))
    return out


def daytrade_events(sym, df, rows, prof, bull=None, keys=tuple(HOLD)):
    """各指令訊號日的「隔天」當沖事件；日K 與盤中同一天的確認方式同 long_events（開盤／收盤比例一致）。"""
    if df is None or not rows or not prof:
        return []
    O, C = df["Open"].to_numpy(dtype=float), df["Close"].to_numpy(dtype=float)
    ds = list(df.index.strftime("%Y-%m-%d"))
    idx = {d: k for k, d in enumerate(ds)}
    out = []
    for r in rows:
        i = idx.get(r["date"])
        if i is None or i + 1 >= len(ds):
            continue
        hits = set(r["hits"]) & set(keys)
        if "base" in hits and i % 10:
            hits.discard("base")
        if not hits:
            continue
        pf = prof.get(ds[i + 1])
        if not pf or not (O[i + 1] > 0 and C[i + 1] > 0):
            continue
        k1, k2 = pf["open"] / O[i + 1], pf["close"] / C[i + 1]
        if not (k2 > 0 and abs(k1 / k2 - 1) < 0.01):
            continue
        row = _dt_row(pf, (bull or {}).get(ds[i + 1]))
        if row:
            for key in hits:
                out.append((key, ds[i + 1], *row))
    return out


def _dt_block(ev, side):
    sgn = 1 if side == "long" else -1
    pairs = [(e[1], sgn * (e[2] - 1) * 100 - DT_COST) for e in ev]
    return _st(pairs)


DT_STOPS = (1.5, 2, 3, 4, 5)


def _dt_stop(ev, side, sp):
    """進場後觸及停損價就以停損價出（5 分K 高低判斷，不看先後；觸及即出場）。統計同 _st，另附停損觸發比例。"""
    pairs, hit = [], 0
    for e in ev:
        if side == "long":
            if e[5] <= 1 - sp / 100:
                r, hit = -sp - DT_COST, hit + 1
            else:
                r = (e[2] - 1) * 100 - DT_COST
        else:
            if e[6] >= 1 + sp / 100:
                r, hit = -sp - DT_COST, hit + 1
            else:
                r = -(e[2] - 1) * 100 - DT_COST
        pairs.append((e[1], r))
    st = _st(pairs)
    st["stop_pct"] = round(100 * hit / len(ev), 1) if ev else None
    return st


def analyze_daytrade(events):
    """events：[(k, date, g, below_open, bull)]。每個 k 輸出：整體、10:00 低於／不低於開盤、大盤 60 日線上／下，各做多與做空。"""
    res = {}
    for key in ["ALL"] + list(HOLD):
        ev = [e for e in events if e[0] == key]
        if not ev:
            continue
        label = "無訊號基準（母體所有交易日）" if key == "ALL" else LABEL[key]
        groups = {"全部": ev, "10:00 低於開盤": [e for e in ev if e[3]], "10:00 不低於開盤": [e for e in ev if not e[3]],
                  "大盤60日線上": [e for e in ev if e[4] is True], "大盤60日線下": [e for e in ev if e[4] is False]}
        res[key] = {"label": label, "groups": {gn: {"long": _dt_block(g, "long"), "short": _dt_block(g, "short")}
                                                  for gn, g in groups.items() if g}}
        if len(ev[0]) >= 7:
            res[key]["stops"] = {f"{side}_停損{sp}%": _dt_stop(ev, side, sp) for side in ("long", "short") for sp in DT_STOPS}
    res["_note"] = f"進場＝{DT_IN}（09:55 那根收盤）、出場＝{DT_OUT}（13:20 那根收盤），成本 {DT_COST}%；5 分K 收盤近似成交價；母體約 168 檔偏大型"
    return res
