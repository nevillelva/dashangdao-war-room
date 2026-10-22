"""【2026-10-22 老闆：勝率與回撤一直上不去，三關可能不用了，改成多時點判斷——09:30 高於開盤代表可能可以進場、11:00 看盤中有沒有干擾、
12:30 再看一次決定要不要尾盤進場或尾盤賣出；用上百種組合排列測試，找出能讓每個做多「查」指令勝率至少五成的組合】

多時點條件 × 進場時點 × 出場規則 全組合 5 年回測（純函式，可離線測試）。

事件：各指令訊號日的「隔天」（進場日），與 intraday_timing_bt.long_events 同一套訊號與日K／盤中同日確認。
進場日檢查點（價格＝該時點前一根 5 分K 收盤；均價＝當日至該時點的成交量加權均價）：
  A 09:30：A0 不看、A1 09:30 價 > 開盤、A2 09:30 價 ≥ 開盤 +0.5%、A3 09:30 價 > 開盤且 ≥ 均價
  B 11:00（盤中干擾）：B0 不看、B1 09:30～11:00 最低 ≥ 開盤（沒跌破開盤）、B2 11:00 價 ≥ 09:30 價、B3 11:00 價 ≥ 均價、B4 11:00 價 > 開盤
  C 12:30（尾盤判斷）：C0 不看、C1 12:30 價 > 開盤、C2 12:30 價 ≥ 均價、C3 12:30 價 ≥ 11:00 價、C4 11:00～12:30 最低 ≥ 早盤（11:00 前）最低、C5 12:30 價距當日高 ≤1%
進場時點：開盤、09:30、11:00、12:30、13:20（不得早於用到的最後一個檢查點）。
出場：持有 H 日收盤（進場日之後第 H 個交易日；1／3／5／10／20，查3 另加 60）×停利（無／3／5／8／12%）×停損（無／5／10%）
      ×「持有中某天 12:30 轉弱（12:30 價低於當天開盤且低於前一天收盤）就 13:20 賣」開／關。
      日內觸價順序：開盤跳空先判斷 → 停損（保守：同一天停損停利都碰到算停損）→ 停利 → 12:30 轉弱 → 到期收盤。進場當天剩餘時段不判斷停損停利。
成本：做多 0.585%。勝＝扣成本後 > 0。
選法（避免挑到巧合）：只用樣本內（≤2024-03-31）挑：樣本內勝率 ≥50%、平均 >0、且贏「隨機基準」同一組合；樣本外只拿來驗證。
另報「樣本內合格的組合裡，有多少在樣本外也合格」＝存活率，用來看是不是過度配適。
限制：盤中 5 分K 快取 193 檔偏大型；5 分K 收盤近似成交價；漲停排隊買不到未模擬；組合數多（每指令約 4 萬），存活率與樣本外才是重點。
"""
from __future__ import annotations

import numpy as np

COST = 0.585
IS_END = "2024-03-31"
ENTRY = ["開盤", "09:30", "11:00", "12:30", "13:20"]
E_LEVEL = {"開盤": 0, "09:30": 1, "11:00": 2, "12:30": 3, "13:20": 4}
A_NAMES = ["A0 09:30 不看", "A1 09:30>開盤", "A2 09:30≥開盤+0.5%", "A3 09:30>開盤且≥均價"]
B_NAMES = ["B0 11:00 不看", "B1 09:30~11:00 不破開盤", "B2 11:00≥09:30價", "B3 11:00≥均價", "B4 11:00>開盤"]
C_NAMES = ["C0 12:30 不看", "C1 12:30>開盤", "C2 12:30≥均價", "C3 12:30≥11:00價", "C4 午盤不破早盤低", "C5 12:30距當日高≤1%"]
TPS = [None, 3, 5, 8, 12]
SLS = [None, 5, 10]
WS = [False, True]
KEYS = ("c32_and", "c3", "c6", "c2", "m1", "c4", "base")
LABEL = {"c32_and": "做多主力(查3∧查2)", "c3": "查3 價值", "c6": "查6 營收雙增", "c2": "查2 拉回守線", "m1": "動能(量比≥2＋創20日新高)",
         "c4": "查4 投信買超", "base": "隨機基準"}
DEFAULT_HOLD = {"c32_and": 20, "c3": 60, "c6": 20, "c2": 10, "m1": 5, "c4": 10, "base": 10}


def holds_for(key):
    return [1, 3, 5, 10, 20] + ([60] if key == "c3" else [])


def exits_for(key):
    return [(h, tp, sl, w) for h in holds_for(key) for tp in TPS for sl in SLS for w in WS]


def exit_label(x):
    h, tp, sl, w = x
    s = f"抱{h}日"
    s += f"／停利{tp}%" if tp else ""
    s += f"／停損{sl}%" if sl else ""
    s += "／12:30轉弱尾盤賣" if w else ""
    return s


def _m(hhmm):
    return int(hhmm[:2]) * 60 + int(hhmm[3:])


def _wlo(pf, t1, t2):
    """t1（不含）到 t2（含）之間各段最低的最小值；沒有資料回 None。"""
    m1, m2 = _m(t1), _m(t2)
    xs = [v for T, v in pf.get("slo", {}).items() if m1 < _m(T) <= m2]
    return min(xs) if xs else None


def flags(pf):
    """回傳 (a, b, c) 三個位元遮罩；資料不足的條件視為不成立（0 號「不看」永遠成立）。"""
    op = pf["open"]
    p, vw = pf["p"], pf["vw"]
    p930, p11, p1230 = p.get("09:30"), p.get("11:00"), p.get("12:30")
    v930, v11, v1230 = vw.get("09:30"), vw.get("11:00"), vw.get("12:30")
    a = 1
    if p930:
        a |= (p930 > op) << 1
        a |= (p930 >= op * 1.005) << 2
        a |= (p930 > op and v930 is not None and p930 >= v930) << 3
    b = 1
    if p11:
        w = _wlo(pf, "09:30", "11:00")
        b |= (w is not None and w >= op) << 1
        b |= (p930 is not None and p11 >= p930) << 2
        b |= (v11 is not None and p11 >= v11) << 3
        b |= (p11 > op) << 4
    c = 1
    if p1230:
        c |= (p1230 > op) << 1
        c |= (v1230 is not None and p1230 >= v1230) << 2
        c |= (p11 is not None and p1230 >= p11) << 3
        w2, lo11 = _wlo(pf, "11:00", "12:30"), pf["lo"].get("11:00")
        c |= (w2 is not None and lo11 is not None and w2 >= lo11) << 4
        hi = pf["hi"].get("12:30")
        c |= (hi is not None and p1230 >= hi * 0.99) << 5
    return int(a), int(b), int(c)


def _same_day(pf, o, c):
    return o > 0 and c > 0 and pf["open"] > 0 and pf["close"] > 0 and abs((pf["open"] / o) / (pf["close"] / c) - 1) < 0.01


def combo_events(sym, df, rows, prof, keys=KEYS, bull=None):
    """回傳 {key: [event]}；event＝{d, bull, e(5,), f(a,b,c), D(H,4), wf(H,), wx(H,)}，價格皆相對進場日日K 開盤。"""
    out = {}
    if df is None or not rows or not prof:
        return out
    O, Hh, L, C = (df[k].to_numpy(dtype=float) for k in ("Open", "High", "Low", "Close"))
    ds = list(df.index.strftime("%Y-%m-%d"))
    idx = {d: k for k, d in enumerate(ds)}
    n = len(ds)
    for r in rows:
        i = idx.get(r["date"])
        if i is None or i + 2 >= n:
            continue
        hits = set(r["hits"]) & set(keys)
        if "base" in hits and i % 10:
            hits.discard("base")
        if not hits:
            continue
        e0 = i + 1
        pf = prof.get(ds[e0])
        if not pf or not _same_day(pf, O[e0], C[e0]):
            continue
        ent = np.array([1.0] + [(pf["p"].get(T) / pf["open"]) if pf["p"].get(T) else np.nan for T in ENTRY[1:]], dtype=np.float32)
        fl = flags(pf)
        hmax = 60 if ("c3" in hits or "base" in hits) else 20        # 隨機基準也留 60 日，供查3 的 60 日組合比較
        D = np.full((hmax, 4), np.nan, dtype=np.float32)
        wf = np.zeros(hmax, dtype=bool)
        wx = np.full(hmax, np.nan, dtype=np.float32)
        base = O[e0]
        for k in range(1, hmax + 1):
            j = e0 + k
            if j >= n:
                break
            D[k - 1] = (O[j] / base, Hh[j] / base, L[j] / base, C[j] / base)
            pk = prof.get(ds[j])
            if pk and _same_day(pk, O[j], C[j]) and pk["p"].get("12:30") and pk["p"].get("13:20"):
                sc = O[j] / pk["open"] / base                      # 盤中價 → 相對進場日開盤
                p1230, p1320 = pk["p"]["12:30"] * sc, pk["p"]["13:20"] * sc
                wf[k - 1] = bool(p1230 < O[j] / base and p1230 < C[j - 1] / base)
                wx[k - 1] = p1320
        for key in hits:
            hk = 60 if key in ("c3", "base") else 20
            out.setdefault(key, []).append({"d": ds[e0], "bull": (bull or {}).get(ds[e0]), "e": ent, "f": fl,
                                            "D": D[:hk], "wf": wf[:hk], "wx": wx[:hk]})
    return out


def simulate(e, D, wf, wx, h, tp, sl, w):
    """向量化出場模擬。e:(n,) 進場價（相對進場日開盤）；D:(n,H,4)；回傳 (n,) 扣成本報酬%（資料不足為 nan）。"""
    n = len(e)
    ret = np.full(n, np.nan, dtype=np.float32)
    alive = ~np.isnan(e) & ~np.isnan(D[:, h - 1, 3])
    for k in range(h):
        o, hi, lo, c = D[:, k, 0], D[:, k, 1], D[:, k, 2], D[:, k, 3]
        if sl:
            lv = e * (1 - sl / 100)
            g = alive & (o <= lv)
            ret[g] = o[g] / e[g]
            alive &= ~g
            t = alive & (lo <= lv)
            ret[t] = 1 - sl / 100
            alive &= ~t
        if tp:
            tv = e * (1 + tp / 100)
            g = alive & (o >= tv)
            ret[g] = o[g] / e[g]
            alive &= ~g
            t = alive & (hi >= tv)
            ret[t] = 1 + tp / 100
            alive &= ~t
        if w:
            t = alive & wf[:, k] & ~np.isnan(wx[:, k])
            ret[t] = wx[t, k] / e[t]
            alive &= ~t
        if k == h - 1:
            ret[alive] = c[alive] / e[alive]
            alive[:] = False
    return (ret - 1) * 100 - COST


def _stack(evs, hk):
    e = np.stack([x["e"] for x in evs])
    D = np.stack([x["D"][:hk] for x in evs])
    wf = np.stack([x["wf"][:hk] for x in evs])
    wx = np.stack([x["wx"][:hk] for x in evs])
    f = np.array([x["f"] for x in evs], dtype=np.int64)
    d = np.array([x["d"] for x in evs])
    return e, D, wf, wx, f, d


def mask_list():
    out = []
    for a in range(len(A_NAMES)):
        for b in range(len(B_NAMES)):
            for c in range(len(C_NAMES)):
                lvl = 3 if c else (2 if b else (1 if a else 0))
                out.append((a, b, c, lvl))
    return out


def grid_stats(evs, key):
    """回傳 dict：exits、masks、以及每個進場時點 E 的 (n, win, avg) 陣列 [IS/OOS]，形狀 (masks, exits)。"""
    hk = 60 if key == "c3" else 20
    e, D, wf, wx, f, d = _stack(evs, hk)
    ex = exits_for(key)
    ml = mask_list()
    M = np.zeros((len(ml), len(evs)), dtype=np.float32)
    for mi, (a, b, c, _l) in enumerate(ml):
        M[mi] = ((f[:, 0] >> a) & 1) & ((f[:, 1] >> b) & 1) & ((f[:, 2] >> c) & 1)
    is_m = (d <= IS_END).astype(np.float32)
    splits = {"is": M * is_m, "oos": M * (1 - is_m)}
    res = {"exits": ex, "masks": ml, "E": {}}
    for ei, E in enumerate(ENTRY):
        R = np.stack([simulate(e[:, ei], D, wf, wx, *x) for x in ex], axis=1)      # (n, exits)
        V = (~np.isnan(R)).astype(np.float32)
        Rv = np.nan_to_num(R, nan=0.0)
        P = (R > 0).astype(np.float32)
        out = {}
        for sp, Ms in splits.items():
            nn = Ms @ V
            with np.errstate(invalid="ignore", divide="ignore"):
                out[sp] = {"n": nn, "avg": (Ms @ Rv) / nn, "win": 100 * (Ms @ P) / nn}
        res["E"][E] = out
        res.setdefault("_R", {})[E] = R
    res["_d"], res["_f"] = d, f
    return res


def _t_day(dates, x):
    ok = ~np.isnan(x)
    if ok.sum() < 30:
        return None
    dd, xx = dates[ok], x[ok]
    u, inv = np.unique(dd, return_inverse=True)
    s = np.bincount(inv, weights=xx)
    c = np.bincount(inv)
    m = s / c
    if len(m) < 5:
        return None
    sd = m.std() or 1e-9
    return round(float(m.mean() / sd * len(m) ** 0.5), 2)


def analyze_key(G, key, Gb=None, min_is=200, min_oos=100, top=15):
    """G＝grid_stats 結果；Gb＝隨機基準的 grid_stats（同一組合比較）。"""
    ex, ml = G["exits"], G["masks"]
    total = pass_is = pass_both = 0
    cand = []
    dflt = None
    for E, st in G["E"].items():
        lvlE = E_LEVEL[E]
        n_is, n_oos = st["is"]["n"], st["oos"]["n"]
        a_is, a_oos, w_is, w_oos = st["is"]["avg"], st["oos"]["avg"], st["is"]["win"], st["oos"]["win"]
        valid_m = np.array([lvl <= lvlE for (_a, _b, _c, lvl) in ml])[:, None]
        ok = valid_m & (n_is >= min_is) & (n_oos >= min_oos)
        total += int(ok.sum())
        pis = ok & (w_is >= 50) & (a_is > 0)
        if Gb is not None:
            b_is = Gb["E"][E]["is"]["avg"]
            pis &= np.nan_to_num(a_is - b_is, nan=-1e9) > 0
        pboth = pis & (w_oos >= 50) & (a_oos > 0)
        pass_is += int(pis.sum())
        pass_both += int(pboth.sum())
        for mi, xi in zip(*np.nonzero(pis)):
            cand.append((float(a_is[mi, xi]), E, int(mi), int(xi)))
    cand.sort(reverse=True)

    def row(E, mi, xi):
        st = G["E"][E]
        a, b, c, _l = ml[mi]
        r = {"條件": f"{A_NAMES[a]}｜{B_NAMES[b]}｜{C_NAMES[c]}", "進場": E, "出場": exit_label(ex[xi])}
        for sp in ("is", "oos"):
            r[f"n_{sp}"] = int(st[sp]["n"][mi, xi])
            r[f"win_{sp}"] = round(float(st[sp]["win"][mi, xi]), 1)
            r[f"avg_{sp}"] = round(float(st[sp]["avg"][mi, xi]), 3)
        if Gb is not None:
            for sp in ("is", "oos"):
                v = Gb["E"][E][sp]["avg"][mi, xi]
                r[f"base_{sp}"] = None if v != v else round(float(v), 3)
        R, d, f = G["_R"][E][:, xi], G["_d"], G["_f"]
        m = ((f[:, 0] >> a) & 1) & ((f[:, 1] >> b) & 1) & ((f[:, 2] >> c) & 1)
        x = np.where(m.astype(bool), R, np.nan)
        r["t_is"] = _t_day(d, np.where(d <= IS_END, x, np.nan))
        r["t_oos"] = _t_day(d, np.where(d > IS_END, x, np.nan))
        return r

    out = {"label": LABEL.get(key, key), "combos": total, "pass_is": pass_is, "pass_both": pass_both,
           "survival_pct": round(100 * pass_both / pass_is, 1) if pass_is else None,
           "top_by_is_avg": [row(E, mi, xi) for _v, E, mi, xi in cand[:top]]}
    # 以樣本內勝率排序（平均仍須 >0）
    cw = sorted(cand, key=lambda t: -float(G["E"][t[1]]["is"]["win"][t[2], t[3]]))[:top]
    out["top_by_is_win"] = [row(E, mi, xi) for _v, E, mi, xi in cw]
    # 現行做法：開盤進、不看條件、抱預設天數、無停利停損
    h0 = DEFAULT_HOLD.get(key, 10)
    x0 = ex.index((h0, None, None, False)) if (h0, None, None, False) in ex else None
    if x0 is not None:
        out["現行(開盤進、抱預設天數)"] = row("開盤", 0, x0)
        # 只看時點條件、出場不變：13:20／12:30 進場，各遮罩相對「同時點、不看條件」的差
        rows_m = []
        for E in ("12:30", "13:20"):
            st = G["E"][E]
            for mi, (a, b, c, _l) in enumerate(ml):
                if st["is"]["n"][mi, x0] >= min_is and st["oos"]["n"][mi, x0] >= min_oos:
                    rows_m.append((float(st["is"]["avg"][mi, x0]), E, mi))
        rows_m.sort(reverse=True)
        out["只換進場條件_出場同現行_top"] = [row(E, mi, x0) for _v, E, mi in rows_m[:10]]
    return out


def analyze(events_by_key):
    """events_by_key：{key: [event]}。回傳各 key 的結果（含與隨機基準比較）。"""
    res, Gb = {}, None
    if events_by_key.get("base"):
        Gb = grid_stats(events_by_key["base"], "base")
        res["base"] = analyze_key(Gb, "base")
    for key in KEYS:
        if key == "base" or not events_by_key.get(key):
            continue
        G = grid_stats(events_by_key[key], key)
        Gbk = Gb if (Gb is not None and key != "c3") else None      # 查3 的出場網格多一個 60 日，與基準欄位不同 → 另算
        if key == "c3" and events_by_key.get("base"):
            Gbk = _base_for(events_by_key["base"], "c3")
        res[key] = analyze_key(G, key, Gbk)
        del G
    res["_note"] = ("只用樣本內（≤2024-03-31）挑：勝率≥50%、平均>0、且贏同組合的隨機基準；樣本外驗證。"
                    "survival_pct＝樣本內合格組合中樣本外也合格（勝率≥50%、平均>0）的比例。成本 0.585%。")
    return res


def _base_for(base_events, key):
    """查3 需要 60 日出場網格：隨機基準事件本來就保留 60 日路徑，直接用查3 的網格算。"""
    return grid_stats(base_events, key)
