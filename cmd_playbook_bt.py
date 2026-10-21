"""【2026-10-21 老闆：重寫回測模組——全部查X 指令 × 台股大盤 × 美股，5 年；每種指令要有自己的規則，不能全部套用同一套；
投信作帳類（查4）要回測「季底前一個月」與「季底當月」並看大盤與美股】純函式，可離線測試。

一、為什麼每個指令要用不同的進出場（依指令本身的邏輯＋公開資料查證）：
  查1 追高突擊（首根攻擊紅K＋爆量＋KD金叉）：突破型 → 隔日開盤進；A 持 5 日、B 收盤跌破訊號日最低價就隔日開盤出（最多 10 日）。
  查2 拉回守線（季線上揚、貼月線、量縮）：支撐型 → A 持 10 日、B 收盤跌破 MA20×0.97 就出（最多 10 日）。
  查3 價值轉強（價值分＋營收循環回升）：基本面中長線 → A 持 20 日、B 持 60 日。
  查4 投信買超（投信作帳）：作帳集中在季底當月、年底最強、其次半年；中小型股較明顯；季底後常見結帳賣壓
      （CMoney／StockFeel 文章，皆為作者經驗、非嚴格研究）→ A 持 10 日、B 持有到「季底最後交易日」收盤、
      C 季底前第 3 個交易日收盤出；另量「季底後 10 日」（結帳賣壓）；分組：季初月／季底前一月／季底當月 × 年底／半年／Q1Q3 × 規模。
  查5 外資買超＋融資減少（籌碼）：A 持 10 日。
  查6 營收雙增（年增>20%＋月增＋創20日新高＋量比）：營收動能 → A 持 20 日、B 收盤跌破 MA20 就出（最多 20 日）。
  查7 漲停隔日賣：漲停收盤買（13:25，鎖死可能買不到）→ A 隔日開盤賣、B 隔日收盤賣、
      C「美股前一晚費半≥+1% 才抱到收盤，否則開盤賣」（09:00 前已知的美股資訊）。
  查8 開高放空：A 隔日開盤比昨收高 3~7% 才空、收盤回補（現行）；收漲停（補不了）→ 隔天開盤回補（carry）；
      B 再加「美股前一晚費半≥+1% 不空」。
  查9 糾結爆量（前日均線糾結＋爆量＋站上三線收紅）：突破型 → A 持 10 日、B 收盤跌破 MA20 就出（最多 10 日）。
  查10 量縮籌碼穩：A 持 10 日、B 持 20 日。
  查12 看漲K線：短線型態 → A 持 5 日。
  做多主力（查3∧查2）：系統主力波段 → A 持 10 日、B 持 20 日。動能（量比≥2＋創20日新高）：A 持 5 日。
  查11 高殖利率：歷史殖利率需要「當時已公告的股利」，用現在股利回推會前視 → 本模組不測（10/16 已用除權息預告表另測）。

二、大盤與美股（避免前視）：
  台股大盤（訊號日收盤後已知）：加權收盤>MA60、>MA20、近 5 日漲跌。
  大盤開盤（進場日 09:00 試撮已知）：用 0050 開盤／前收（加權指數的「開盤」是用未成交股票的昨收算，會低估跳空，不能用）。
  美股（進場日 09:00 前已收盤）：前一個美股交易日的費半(^SOX)、那斯達克(^IXIC)、S&P500(^GSPC)、台積電ADR(TSM) 漲跌，
      以及費半是否在 20 日線上。公開研究（iThome 系列，2006~2026 約 5,050 日）：美股前一晚對台股「開盤跳空」相關約 0.4、
      對「開盤後到收盤」幾乎 0 → 預期：隔夜持有的策略（查7）受美股影響大、開盤進場的策略受影響小。本模組用資料驗證這點。

三、統計：報酬扣成本（做多來回 0.585%、當沖 0.435%）；超額＝個股報酬−同時段 0050（含息還原）報酬；勝率＝扣成本後報酬>0；
  t 值按「日」聚合（同日多檔不獨立）；IS ≤2024-03-31／OOS 之後；另列「不重疊」（同一檔持有中不重複進場）。
  篩選條件「有效」＝排除該條件後，樣本內外的超額都改善≥0.5 個百分點，且兩段各≥40 筆（多重比較風險仍在，需實盤再驗證）。
  限制：母體存活者偏誤、還原價、漲停買不到／跌停賣不掉未完全模擬、FinMind 資料缺漏處條件不成立。"""
from __future__ import annotations

from statistics import mean, pstdev

import numpy as np

COST_LONG, COST_DT = 0.585, 0.435
IS_END = "2024-03-31"
MIN_N = 40
LU = 9.4


# ───────────────────────── 大盤／美股特徵 ─────────────────────────
def build_tw(twii, etf):
    """twii：^TWII 日K；etf：0050 日K（還原）。回傳 {日期: {...}} 與 0050 價格索引（算超額用）。"""
    out = {}
    if twii is not None and len(twii) > 70:
        c = twii["Close"].to_numpy(dtype=float)
        ds = twii.index.strftime("%Y-%m-%d")
        for i in range(60, len(c)):
            if not c[i] > 0:
                continue
            m60, m20 = c[i - 59:i + 1].mean(), c[i - 19:i + 1].mean()
            out[ds[i]] = {"bull60": bool(c[i] > m60), "bull20": bool(c[i] > m20),
                          "ret5": (c[i] / c[i - 5] - 1) * 100 if c[i - 5] > 0 else None}
    px = {}
    if etf is not None and len(etf) > 2:
        o, c = etf["Open"].to_numpy(dtype=float), etf["Close"].to_numpy(dtype=float)
        ds = etf.index.strftime("%Y-%m-%d")
        for i in range(len(c)):
            px[ds[i]] = (o[i], c[i])
            if i > 0 and c[i - 1] > 0 and o[i] > 0:
                out.setdefault(ds[i], {})["gap0050"] = (o[i] / c[i - 1] - 1) * 100
    return out, px


def build_us(dfs, tw_dates):
    """dfs：{"sox": df, "ndx": df, "spx": df, "tsm": df}；對每個台股日期 D 取「D 之前最後一個美股交易日」的漲跌（台北早上已收盤）。"""
    series = {}
    for k, df in (dfs or {}).items():
        if df is None or len(df) < 25:
            continue
        c = df["Close"].to_numpy(dtype=float)
        ds = list(df.index.strftime("%Y-%m-%d"))
        rows = []
        for i in range(20, len(c)):
            if c[i] > 0 and c[i - 1] > 0:
                rows.append((ds[i], (c[i] / c[i - 1] - 1) * 100, bool(c[i] > c[i - 19:i + 1].mean())))
        series[k] = rows
    out = {}
    for k, rows in series.items():
        j = 0
        for d in sorted(tw_dates):
            while j + 1 < len(rows) and rows[j + 1][0] < d:
                j += 1
            if rows and rows[j][0] < d:
                o = out.setdefault(d, {})
                o[k] = rows[j][1]
                if k == "sox":
                    o["sox_up20"] = rows[j][2]
    return out


# ───────────────────────── 單筆交易模擬 ─────────────────────────
class Px:
    def __init__(self, df):
        self.O, self.H, self.L, self.C = (df[x].to_numpy(dtype=float) for x in ("Open", "High", "Low", "Close"))
        self.d = list(df.index.strftime("%Y-%m-%d"))
        self.n = len(self.C)
        c = df["Close"].astype(float)
        self.ma20 = c.rolling(20).mean().to_numpy()


def _mkt(px0050, d0, kind0, d1, kind1):
    a, b = px0050.get(d0), px0050.get(d1)
    if not a or not b:
        return None
    p0 = a[0] if kind0 == "o" else a[1]
    p1 = b[0] if kind1 == "o" else b[1]
    return (p1 / p0 - 1) * 100 if p0 > 0 and p1 > 0 else None


def _trade(p, px0050, e_idx, e_kind, x_idx, x_kind, cost=COST_LONG, side=1):
    """回傳 dict(r, ex, hold, mae) 或 None。side=-1 為放空。"""
    if x_idx is None or e_idx < 0 or x_idx >= p.n or x_idx < e_idx:
        return None
    ep = p.O[e_idx] if e_kind == "o" else p.C[e_idx]
    xp = p.O[x_idx] if x_kind == "o" else p.C[x_idx]
    if not (ep > 0 and xp > 0):
        return None
    raw = (xp / ep - 1) * 100 * side
    m = _mkt(px0050, p.d[e_idx], e_kind, p.d[x_idx], x_kind)
    s0 = e_idx + 1 if e_kind == "c" else e_idx            # 收盤進場：當天盤中價不算
    s1 = x_idx if x_kind == "c" else x_idx - 1           # 開盤出場：當天盤中價不算
    if s1 >= s0:
        seg = p.L[s0:s1 + 1] if side == 1 else p.H[s0:s1 + 1]
        worst = float(np.nanmin(seg)) if side == 1 else float(np.nanmax(seg))
        worst = min(worst, xp) if side == 1 else max(worst, xp)
    else:
        worst = xp
    mae = ((worst / ep - 1) * 100) if side == 1 else (-(worst / ep - 1) * 100)
    r = raw - cost
    return {"r": r, "ex": (r - (m * side if m is not None else 0)) if m is not None else None, "hold": x_idx - e_idx + 1, "mae": mae,
            "ed": p.d[e_idx], "ek": e_kind, "xd": p.d[x_idx], "xk": x_kind, "sd": side}


def _stop_exit(p, i, max_hold, level_fn):
    """隔日開盤進場；每天收盤若 < level_fn(t) 就隔天開盤出，否則第 max_hold 天收盤出。回傳 (x_idx, kind)。"""
    e = i + 1
    last = i + max_hold
    if last >= p.n:
        return None, None
    for t in range(e, last):
        lv = level_fn(t)
        if lv is not None and p.C[t] < lv:
            return t + 1, "o"
    return last, "c"


def _qe_last_idx(p, i):
    """訊號日所在季度的最後一個交易日 index（資料內）。"""
    y, m = int(p.d[i][:4]), int(p.d[i][5:7])
    qm = ((m - 1) // 3 + 1) * 3
    key = f"{y:04d}-{qm:02d}"
    j = None
    for t in range(i, min(p.n, i + 80)):
        if p.d[t][:7] == key:
            j = t
        elif p.d[t][:7] > key:
            break
    return j


def month_pos(date):
    m = int(date[5:7])
    return "季底當月" if m % 3 == 0 else ("季底前一月" if m % 3 == 2 else "季初月")


def quarter_kind(date):
    qm = ((int(date[5:7]) - 1) // 3 + 1) * 3
    return "年底(Q4)" if qm == 12 else ("半年(Q2)" if qm == 6 else "Q1/Q3")


def playbook(key, p, i, px0050, us_e):
    """依指令回傳 {變體名: trade}。i＝訊號日 index。us_e：進場日（i+1）09:00 前已知的美股特徵。"""
    T = lambda ei, ek, xi, xk, **kw: _trade(p, px0050, ei, ek, xi, xk, **kw)  # noqa: E731
    e = i + 1
    v = {}
    if e >= p.n:
        return v
    if key == "c1":
        v["A 持5日"] = T(e, "o", i + 5, "c")
        lo = p.L[i]
        xi, xk = _stop_exit(p, i, 10, lambda t: lo)
        v["B 跌破訊號日低點就出(≤10日)"] = T(e, "o", xi, xk) if xi else None
    elif key == "c2":
        v["A 持10日"] = T(e, "o", i + 10, "c")
        xi, xk = _stop_exit(p, i, 10, lambda t: p.ma20[t] * 0.97 if p.ma20[t] == p.ma20[t] else None)
        v["B 跌破MA20×0.97就出(≤10日)"] = T(e, "o", xi, xk) if xi else None
    elif key == "c3":
        v["A 持20日"] = T(e, "o", i + 20, "c")
        v["B 持60日"] = T(e, "o", i + 60, "c") if i + 60 < p.n else None
    elif key in ("c4", "c4v", "c4w"):
        v["A 持10日"] = T(e, "o", i + 10, "c")
        mp = month_pos(p.d[i])
        q = _qe_last_idx(p, i)
        if mp != "季初月" and q is not None and q > e:
            v["B 抱到季底最後交易日"] = T(e, "o", q, "c")
            v["C 季底前第3個交易日出"] = T(e, "o", q - 3, "c") if q - 3 > e else None
            if q + 10 < p.n:
                v["D【觀察】季底後10日(結帳賣壓)"] = T(q, "c", q + 10, "c", cost=0.0)
    elif key == "c5":
        v["A 持10日"] = T(e, "o", i + 10, "c")
    elif key == "c6":
        v["A 持20日"] = T(e, "o", i + 20, "c")
        xi, xk = _stop_exit(p, i, 20, lambda t: p.ma20[t] if p.ma20[t] == p.ma20[t] else None)
        v["B 跌破MA20就出(≤20日)"] = T(e, "o", xi, xk) if xi else None
    elif key == "c7":
        v["A 漲停收盤買→隔日開盤賣"] = T(i, "c", e, "o")
        v["B 漲停收盤買→隔日收盤賣"] = T(i, "c", e, "c")
        sox = (us_e or {}).get("sox")
        if sox is not None:
            v["C 費半前晚≥+1%抱到收盤，否則開盤賣"] = T(i, "c", e, "c" if sox >= 1 else "o")
    elif key == "c8":
        if p.C[i] > 0 and p.O[e] > 0:
            gap = (p.O[e] / p.C[i] - 1) * 100
            if 3 <= gap < 7:
                lock = (p.C[e] / p.C[i] - 1) * 100 >= LU and p.C[e] >= p.H[e] * 0.9995
                tr = T(e, "o", (e + 1) if lock else e, "o" if lock else "c", cost=COST_DT, side=-1)
                v["A 開高3~7%開盤空、收盤補(補不了隔天開盤補)"] = tr
                sox = (us_e or {}).get("sox")
                v["B A＋費半前晚≥+1%不空"] = tr if (sox is not None and sox < 1) else None
    elif key == "c9":
        v["A 持10日"] = T(e, "o", i + 10, "c")
        xi, xk = _stop_exit(p, i, 10, lambda t: p.ma20[t] if p.ma20[t] == p.ma20[t] else None)
        v["B 跌破MA20就出(≤10日)"] = T(e, "o", xi, xk) if xi else None
    elif key == "c10":
        v["A 持10日"] = T(e, "o", i + 10, "c")
        v["B 持20日"] = T(e, "o", i + 20, "c")
    elif key == "c12":
        v["A 持5日"] = T(e, "o", i + 5, "c")
    elif key == "c32_and":
        v["A 持10日"] = T(e, "o", i + 10, "c")
        v["B 持20日"] = T(e, "o", i + 20, "c")
    elif key == "m1":
        v["A 持5日"] = T(e, "o", i + 5, "c")
    elif key == "base":
        v["A 持10日"] = T(e, "o", i + 10, "c")
    return {k: x for k, x in v.items() if x is not None}


KEYS = {"c1": "查1 追高突擊", "c2": "查2 拉回守線", "c3": "查3 價值轉強", "c4": "查4 投信買超", "c4v": "查4 投信連買3日",
        "c4w": "查4 投信連買3日且距季底≤31天", "c5": "查5 外資買超＋融資減", "c6": "查6 營收雙增", "c7": "查7 漲停隔日賣",
        "c8": "查8 開高放空", "c9": "查9 糾結爆量", "c10": "查10 量縮籌碼穩", "c12": "查12 看漲K線",
        "c32_and": "做多主力（查3∧查2）", "m1": "動能（量比≥2＋創20日新高）", "base": "隨機基準（每 10 天取 1 天）"}


def events_for_symbol(sym, df, rows, tw, px0050, us, base_every=10):
    """rows：cmd_deep_bt.build_rows 的結果（含 hits）。回傳事件 list（每筆：指令、日期、各變體交易、條件）。"""
    if df is None or not rows:
        return []
    p = Px(df)
    idx = {d: k for k, d in enumerate(p.d)}
    out = []
    for r in rows:
        i = idx.get(r["date"])
        if i is None or i + 1 >= p.n:
            continue
        d_e = p.d[i + 1]
        twi, twe, us_e = tw.get(r["date"], {}), tw.get(d_e, {}), us.get(d_e, {})
        hits = set(r["hits"]) & set(KEYS)
        if "base" in hits and i % base_every:
            hits.discard("base")
        for key in hits:
            v = playbook(key, p, i, px0050, us_e)
            if not v:
                continue
            out.append({"k": key, "sym": sym, "date": r["date"], "i": i, "v": v, "tv20": r.get("tv20"),
                        "bull60": twi.get("bull60"), "bull20": twi.get("bull20"), "tw5": twi.get("ret5"),
                        "gap0050": twe.get("gap0050"), "sox": us_e.get("sox"), "ndx": us_e.get("ndx"), "spx": us_e.get("spx"),
                        "tsm": us_e.get("tsm"), "sox_up20": us_e.get("sox_up20"),
                        "mpos": month_pos(r["date"]), "qkind": quarter_kind(r["date"])})
    return out


# ───────────────────────── 統計 ─────────────────────────
def _t_day(pairs):
    byd = {}
    for d, x in pairs:
        byd.setdefault(d, []).append(x)
    dm = [mean(v) for v in byd.values()]
    if len(dm) < 5:
        return None
    sd = pstdev(dm) or 1e-9
    return round(mean(dm) / sd * len(dm) ** 0.5, 2)


def build_ew(acc):
    """acc：{日期: [隔夜報酬和, 隔夜筆數, 盤中報酬和, 盤中筆數]} → 等權母體指數 {日期: (開盤點位, 收盤點位)}（算「比同母體」超額用）。"""
    px, lv = {}, 1.0
    for d in sorted(acc):
        on_s, on_n, in_s, in_n = acc[d]
        o = lv * (1 + (on_s / on_n if on_n else 0.0) / 100)
        c = o * (1 + (in_s / in_n if in_n else 0.0) / 100)
        px[d] = (o, c)
        lv = c
    return px


def ew_accumulate(acc, df):
    """把一檔的每日隔夜／盤中報酬加進等權母體（單日超過 ±10.6% 視為價格不連續，略過）。"""
    O, C = df["Open"].to_numpy(dtype=float), df["Close"].to_numpy(dtype=float)
    ds = df.index.strftime("%Y-%m-%d")
    for t in range(1, len(C)):
        a = acc.setdefault(ds[t], [0.0, 0, 0.0, 0])
        if C[t - 1] > 0 and O[t] > 0:
            on = (O[t] / C[t - 1] - 1) * 100
            if abs(on) <= 10.6:
                a[0] += on
                a[1] += 1
        if O[t] > 0 and C[t] > 0:
            it = (C[t] / O[t] - 1) * 100
            if abs(it) <= 10.6:
                a[2] += it
                a[3] += 1


def attach_ew(events, px_ew):
    for e in events:
        for t in e["v"].values():
            m = _mkt(px_ew, t["ed"], t["ek"], t["xd"], t["xk"]) if "ed" in t else None
            t["ew"] = (t["r"] - m * t["sd"]) if m is not None else None


def _part(tr, key):
    xs = [(d, t[key]) for d, t in tr if t.get(key) is not None]
    if not xs:
        return {}
    xa = np.array([x for _, x in xs])
    isx = [x for d, x in xs if d <= IS_END]
    osx = [x for d, x in xs if d > IS_END]
    return {"mean": round(float(xa.mean()), 3), "win": round(float((xa > 0).mean() * 100), 1), "t": _t_day(xs),
            "is": round(mean(isx), 3) if isx else None, "oos": round(mean(osx), 3) if osx else None, "n_is": len(isx), "n_oos": len(osx)}


def stat(evs, var):
    """avg／win＝扣成本後實際報酬；ex＝比 0050（含息）；ew＝比同母體等權平均（扣掉大盤與母體漂移，看選股本身）。"""
    tr = [(e["date"], e["v"][var]) for e in evs if var in e["v"]]
    if not tr:
        return {"n": 0}
    rs = [t["r"] for _, t in tr]
    a = np.array(rs)
    o = {"n": len(rs), "days": len({d for d, _ in tr}), "avg": round(float(a.mean()), 3), "win": round(float((a > 0).mean() * 100), 1),
         "p5": round(float(np.percentile(a, 5)), 2), "hold": round(mean(t["hold"] for _, t in tr), 1),
         "mae": round(mean(t["mae"] for _, t in tr), 2)}
    r = _part(tr, "r")
    o.update(t_raw=r.get("t"), avg_is=r.get("is"), avg_oos=r.get("oos"), n_is=r.get("n_is", 0), n_oos=r.get("n_oos", 0))
    o["win_oos"] = round(100 * sum(1 for d, t in tr if d > IS_END and t["r"] > 0) / max(1, sum(1 for d, _ in tr if d > IS_END)), 1)
    x = _part(tr, "ex")
    if x:
        o.update(ex=x["mean"], ex_win=x["win"], t_ex=x["t"], ex_is=x["is"], ex_oos=x["oos"])
    w = _part(tr, "ew")
    if w:
        o.update(ew=w["mean"], ew_win=w["win"], t_ew=w["t"], ew_is=w["is"], ew_oos=w["oos"])
    return o


def nonoverlap(evs, var):
    """同一檔持有期間不重複進場（較接近實際操作）。"""
    keep = []
    last = {}
    for e in sorted(evs, key=lambda x: (x["sym"], x["date"])):
        t = e["v"].get(var)
        if t is None:
            continue
        if e["i"] + 1 <= last.get(e["sym"], -1):
            continue
        keep.append(e)
        last[e["sym"]] = e["i"] + t["hold"]
    return keep


def tercile_cuts(tv_by_date):
    """{日期: [當天母體各檔 tv20]} → {日期: (1/3, 2/3 分位)}。"""
    cut = {}
    for d, vs in tv_by_date.items():
        vs = sorted(x for x in vs if x)
        cut[d] = (vs[len(vs) // 3], vs[2 * len(vs) // 3]) if len(vs) >= 3 else None
    return cut


def size_terciles(evs, cut=None):
    """依同一天母體（cut＝tercile_cuts 結果）的 tv20 切三等分；沒給 cut 就用事件本身近似。"""
    if cut is None:
        byd = {}
        for e in evs:
            if e.get("tv20"):
                byd.setdefault(e["date"], []).append(e["tv20"])
        cut = tercile_cuts(byd)
    for e in evs:
        c = cut.get(e["date"])
        t = e.get("tv20")
        e["size"] = None if (not c or not t) else ("小型" if t < c[0] else ("中型" if t < c[1] else "大型"))


def _b(x, cuts, labels):
    if x is None:
        return None
    for c, lab in zip(cuts, labels):
        if x < c:
            return lab
    return labels[-1]


CONDS = {
    "台股大盤60日線": lambda e: None if e["bull60"] is None else ("線上" if e["bull60"] else "線下"),
    "台股大盤20日線": lambda e: None if e["bull20"] is None else ("線上" if e["bull20"] else "線下"),
    "大盤近5日": lambda e: _b(e["tw5"], (-3, 0, 3), ("≤-3%", "-3~0%", "0~3%", "≥3%")),
    "0050開盤跳空(試撮已知)": lambda e: _b(e["gap0050"], (-1, -0.3, 0.3, 1), ("≤-1%", "-1~-0.3%", "平", "0.3~1%", "≥1%")),
    "費半前一晚": lambda e: _b(e["sox"], (-2, -0.5, 0.5, 2), ("≤-2%", "-2~-0.5%", "平", "0.5~2%", "≥2%")),
    "那斯達克前一晚": lambda e: _b(e["ndx"], (-1.5, -0.3, 0.3, 1.5), ("≤-1.5%", "-1.5~-0.3%", "平", "0.3~1.5%", "≥1.5%")),
    "台積電ADR前一晚": lambda e: _b(e["tsm"], (-2, -0.5, 0.5, 2), ("≤-2%", "-2~-0.5%", "平", "0.5~2%", "≥2%")),
    "費半20日線": lambda e: None if e["sox_up20"] is None else ("線上" if e["sox_up20"] else "線下"),
    "規模": lambda e: e.get("size"),
    "季內位置": lambda e: e["mpos"],
    "季別": lambda e: e["qkind"],
}
C4_EXTRA = {"季內位置×季別": lambda e: f"{e['mpos']}·{e['qkind']}", "季內位置×規模": lambda e: (f"{e['mpos']}·{e['size']}" if e.get("size") else None),
            "季內位置×台股60日線": lambda e: (f"{e['mpos']}·{'線上' if e['bull60'] else '線下'}" if e["bull60"] is not None else None),
            "季內位置×費半前一晚": lambda e: (f"{e['mpos']}·{'費半漲' if e['sox'] >= 0 else '費半跌'}" if e["sox"] is not None else None)}


def _filters(evs, var, conds, metric="avg"):
    """每個條件值若「排除它」能讓樣本內與樣本外的 metric（avg＝實際報酬、ew＝比同母體）都改善≥0.5 且兩段各≥MIN_N → 列為有效排除條件。"""
    ki, ko = (metric + "_is", metric + "_oos") if metric != "avg" else ("avg_is", "avg_oos")
    base = stat(evs, var)
    out = []
    if base.get(ki) is None or base.get(ko) is None:
        return out
    for cname, f in conds.items():
        vals = {f(e) for e in evs} - {None}
        for val in vals:
            keep = [e for e in evs if f(e) != val]
            ks = stat(keep, var)
            if ks.get("n_is", 0) >= MIN_N and ks.get("n_oos", 0) >= MIN_N and ks.get(ki) is not None and ks.get(ko) is not None \
                    and ks[ki] >= base[ki] + 0.5 and ks[ko] >= base[ko] + 0.5:
                out.append({"排除": f"{cname}={val}", "保留後": {k: ks.get(k) for k in ("n", "avg", "win", "avg_is", "avg_oos", "ew", "ew_is", "ew_oos")}})
    return out


def analyze(events, size_cut=None):
    size_terciles(events, size_cut)
    res = {}
    for key, label in KEYS.items():
        ev = [e for e in events if e["k"] == key]
        if not ev:
            res[key] = {"label": label, "n": 0}
            continue
        vars_ = sorted({v for e in ev for v in e["v"]})
        blk = {"label": label, "n": len(ev), "variants": {}}
        for var in vars_:
            vb = {"all": stat(ev, var), "nonoverlap": stat(nonoverlap(ev, var), var), "by": {}}
            conds = dict(CONDS, **(C4_EXTRA if key.startswith("c4") else {}))
            if not key.startswith("c4"):
                conds.pop("季內位置", None)
                conds.pop("季別", None)
            for cname, f in conds.items():
                g = {}
                for e in ev:
                    val = f(e)
                    if val is not None and var in e["v"]:
                        g.setdefault(val, []).append(e)
                vb["by"][cname] = {val: stat(sub, var) for val, sub in sorted(g.items())}
            if not var.startswith("D") and key != "base":
                _fc = {k: f for k, f in conds.items() if k not in ("季內位置×季別", "季內位置×規模", "季內位置×台股60日線", "季內位置×費半前一晚")}
                vb["filters"] = _filters(ev, var, _fc, "avg")
                vb["filters_ew"] = _filters(ev, var, _fc, "ew")
            blk["variants"][var] = vb
        # 依樣本內（IS）超額挑變體，再看樣本外（OOS）——避免用樣本外挑選
        cand = [(var, blk["variants"][var]["all"]) for var in vars_ if not var.startswith("D") and blk["variants"][var]["all"].get("avg_is") is not None]
        if cand:
            best = max(cand, key=lambda x: x[1]["avg_is"])
            b_ = best[1]
            blk["pick_by_IS"] = {"variant": best[0], "avg_is": b_["avg_is"], "avg_oos": b_.get("avg_oos"), "win_oos": b_.get("win_oos"),
                                 "ew_is": b_.get("ew_is"), "ew_oos": b_.get("ew_oos"), "t_raw": b_.get("t_raw"), "n": b_["n"]}
        res[key] = blk
    return res
