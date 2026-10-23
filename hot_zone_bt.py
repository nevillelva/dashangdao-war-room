"""【2026-10-23 老闆：只挑熱門股前 100 檔，搭配籌碼（投信／外資買超）、技術面、美股，找「勝率真的比較高」的區域】純函式，可離線測試。

母體：全上市普通股（與 cmd_playbook_bt 同一份 5 年快取，約 1,089 檔），每天依「近 20 日平均成交值」排名取前 100（當天收盤後已知、不前視）；
      另以「訊號日當天成交值前 100」做對照。成交值用還原價×成交量近似（不同股票的還原幅度不同，排名會有小誤差）。
條件旗標（訊號日收盤後已知；美股＝進場日前一晚；進場日旗標只用在「進場日收盤（13:20 代理）進場」）：見 FLAGS。
進場：E0＝隔日開盤；E1＝隔日收盤（日K 沒有 13:20 價，用收盤近似 13:20 尾盤進場）。漲停鎖死（開盤／收盤即漲停且最高＝該價）視為買不到、不計。
出場（扣做多來回成本 0.585%）：X1 隔天開盤賣、H1/H3/H5/H10/H20 抱 N 日收盤、S5/S10/S20 抱 N 日＋−5% 觸價停損（跳空以開盤價）、
      B10 抱 10 日內 +5% 停利／−5% 停損（同一天都碰到保守算停損）。
挑選：只用樣本內（≤2024-03-31）挑，樣本外驗證；且勝率、平均都要贏「同母體、同出場」的熱門股基準；另跑「安慰劑」（條件打亂）估計純運氣會過幾組。
限制：存活者偏誤（只有現在仍上市的股票）、樣本外是強多頭、E1 用收盤近似 13:20、八大行庫沒有 5 年免費歷史（另做短樣本試算）。"""
from __future__ import annotations

from itertools import combinations

import numpy as np

COST = 0.585
IS_END = "2024-03-31"
LU = 0.094

# (代碼, 中文, 類別)  類別：stock＝個股、date＝同一天全部相同（大盤／美股）、entry＝進場日盤中資訊（只配 E1）
FLAGS = [
    ("t1", "投信買超", "stock"),
    ("t3", "投信連買≥3日", "stock"),
    ("tfirst", "投信首買（前10日沒買）", "stock"),
    ("tbig", "投信買超≥當日量5%", "stock"),
    ("t5", "投信5日累計買超", "stock"),
    ("f1", "外資買超", "stock"),
    ("f3", "外資連買≥3日", "stock"),
    ("fbig", "外資買超≥當日量10%", "stock"),
    ("ft", "外資投信同買（土洋）", "stock"),
    ("d1", "自營商買超", "stock"),
    ("mdec", "融資減少", "stock"),
    ("up20", "站上月線且月線上揚", "stock"),
    ("ab60", "站上季線", "stock"),
    ("hi20", "收盤創20日新高", "stock"),
    ("vr15", "量比≥1.5", "stock"),
    ("vlow", "量縮（量比≤0.7）", "stock"),
    ("red3", "收紅K且漲≥3%", "stock"),
    ("pull3", "季線上3日回檔≥3%", "stock"),
    ("kdx", "KD黃金交叉（K<80）", "stock"),
    ("bias_ok", "乖離月線0~5%", "stock"),
    ("bias_hi", "乖離月線≥10%", "stock"),
    ("lu", "收漲停", "stock"),
    ("rs20", "近20日漲≥10%", "stock"),
    ("tw20", "加權在月線上", "date"),
    ("tw60", "加權在季線上", "date"),
    ("sox_up", "費半前一晚≥+1%", "date"),
    ("sox_dn", "費半前一晚≤−1%", "date"),
    ("sox20", "費半在月線上", "date"),
    ("tsm_up", "台積電ADR前一晚≥+1%", "date"),
    ("ndx_up", "那斯達克前一晚收漲", "date"),
    ("gap_up", "進場日開高≥1%", "entry"),
    ("gap_dn", "進場日開低≤−1%", "entry"),
    ("e_red", "進場日收盤≥開盤（尾盤在開盤上）", "entry"),
    ("e_up1", "進場日漲≥1%", "entry"),
    ("e_hipos", "進場日收在當日高檔（≥7成）", "entry"),
]
FIDX = {k: i for i, (k, _l, _c) in enumerate(FLAGS)}
ENTRY_MASK = sum(1 << i for i, (_k, _l, c) in enumerate(FLAGS) if c == "entry")
DATE_FLAGS = [i for i, (_k, _l, c) in enumerate(FLAGS) if c == "date"]
EXITS = ["X1", "H1", "H3", "H5", "H10", "H20", "S5", "S10", "S20", "B10"]
EXIT_LABEL = {"X1": "隔天開盤賣", "H1": "抱1日", "H3": "抱3日", "H5": "抱5日", "H10": "抱10日", "H20": "抱20日",
              "S5": "抱5日＋−5%停損", "S10": "抱10日＋−5%停損", "S20": "抱20日＋−5%停損", "B10": "10日內+5%停利／−5%停損"}
ENTRIES = ["E0", "E1"]
ENTRY_LABEL = {"E0": "隔日開盤進", "E1": "隔日13:20（收盤近似）進"}
COLS = [f"{e}|{x}" for e in ENTRIES for x in EXITS]
E0_COLS = [k for k, c in enumerate(COLS) if c.startswith("E0|")]


# ───────────────────────── 個股特徵 ─────────────────────────
def _roll_mean(a, n):
    out = np.full(len(a), np.nan)
    if len(a) >= n:
        cs = np.concatenate([[0.0], np.cumsum(np.nan_to_num(a))])
        out[n - 1:] = (cs[n:] - cs[:-n]) / n
    return out


def _roll_max(a, n):
    out = np.full(len(a), np.nan)
    if len(a) >= n:
        from numpy.lib.stride_tricks import sliding_window_view as sw
        out[n - 1:] = sw(a, n).max(axis=1)
    return out


def _shift(a, k, fill=np.nan):
    out = np.full(len(a), fill, dtype=float)
    if k > 0:
        out[k:] = a[:-k]
    elif k < 0:
        out[:k] = a[-k:]
    else:
        out[:] = a
    return out


def _kd(h, l, c):
    from numpy.lib.stride_tricks import sliding_window_view as sw
    n = len(c)
    K, D = np.full(n, np.nan), np.full(n, np.nan)
    if n < 9:
        return K, D
    lo, hi = sw(l, 9).min(axis=1), sw(h, 9).max(axis=1)
    rsv = (c[8:] - lo) / (hi - lo + 1e-9) * 100
    k, d = 50.0, 50.0
    for j, r in enumerate(rsv):
        k = k * 2 / 3 + r / 3
        d = d * 2 / 3 + k / 3
        K[j + 8], D[j + 8] = k, d
    return K, D


def _streak_pos(x, k):
    """x 連續 k 天 >0（含當天）。"""
    ok = x > 0
    out = ok.copy()
    for s in range(1, k):
        out &= _shift(ok.astype(float), s, 0.0) > 0
    return out


def _exit_returns(O, H, L, C, j, P, from_open):
    """單一進場：j＝進場日索引、P＝進場價、from_open＝開盤進場（進場當天的低點也算停損範圍）。回傳 EXITS 順序的報酬%（未扣成本），缺資料 NaN。"""
    n = len(C)
    out = np.full(len(EXITS), np.nan)
    if not (P > 0):
        return out
    if j + 1 < n and O[j + 1] > 0:
        out[0] = (O[j + 1] / P - 1) * 100
    for q, k in ((1, 1), (2, 3), (3, 5), (4, 10), (5, 20)):
        if j + k < n and C[j + k] > 0:
            out[q] = (C[j + k] / P - 1) * 100
    stop = P * 0.95
    for q, k in ((6, 5), (7, 10), (8, 20)):
        if j + k >= n:
            continue
        px = None
        t0 = j if from_open else j + 1
        for t in range(t0, j + k + 1):
            if t > j and O[t] <= stop:
                px = O[t]
                break
            if L[t] <= stop:
                px = stop
                break
        out[q] = ((px if px is not None else C[j + k]) / P - 1) * 100
    if j + 10 < n:
        tp, px = P * 1.05, None
        t0 = j if from_open else j + 1
        for t in range(t0, j + 11):
            if t > j and O[t] <= stop:
                px = O[t]
                break
            if t > j and O[t] >= tp:
                px = O[t]
                break
            if L[t] <= stop:
                px = stop
                break
            if H[t] >= tp:
                px = tp
                break
        out[9] = ((px if px is not None else C[j + 10]) / P - 1) * 100
    return out


def stock_arrays(dates, O, H, L, C, V, inst=None, tw=None, us=None):
    """dates：list[str]；O/H/L/C/V：np.array（還原價、股數）；inst：{f,t,d,m: np.array 對齊 dates（張，缺 NaN）}；
    tw：{日期: {bull20, bull60}}；us：{進場日: {sox, tsm, ndx, sox_up20}}。
    回傳 dict(sig_idx, tv20, tv1, flags, R)；每列＝訊號日 i（i+1 為進場日），R 已扣成本。"""
    O, H, L, C, V = (np.asarray(x, dtype=float) for x in (O, H, L, C, V))
    n = len(C)
    tw, us = tw or {}, us or {}
    inst = inst or {}
    nanv = np.full(n, np.nan)
    f, t, d, m = (np.asarray(inst.get(k, nanv), dtype=float) for k in ("f", "t", "d", "m"))
    ma20, ma60 = _roll_mean(C, 20), _roll_mean(C, 60)
    ma20_5 = _shift(ma20, 5)
    hi20 = _roll_max(C, 20)
    v5p = _shift(_roll_mean(V, 5), 1)
    vr = np.where(v5p > 0, V / np.where(v5p > 0, v5p, 1), np.nan)
    c1, c3, c20 = _shift(C, 1), _shift(C, 3), _shift(C, 20)
    chg = np.where(c1 > 0, C / np.where(c1 > 0, c1, 1) - 1, np.nan)
    K, D = _kd(H, L, C)
    K1, D1 = _shift(K, 1), _shift(D, 1)
    bias = np.where(ma20 > 0, C / np.where(ma20 > 0, ma20, 1) - 1, np.nan)
    with np.errstate(invalid="ignore"):
        tprev10 = np.zeros(n, dtype=bool)
        for s in range(1, 11):
            tprev10 |= _shift(t, s, 0.0) > 0
        fl = {
            "t1": t > 0, "t3": _streak_pos(np.nan_to_num(t), 3), "tfirst": (t > 0) & ~tprev10,
            "tbig": (t * 1000 >= 0.05 * V) & (t > 0), "t5": _roll_mean(np.nan_to_num(t), 5) > 0,
            "f1": f > 0, "f3": _streak_pos(np.nan_to_num(f), 3), "fbig": (f * 1000 >= 0.10 * V) & (f > 0),
            "ft": (f > 0) & (t > 0), "d1": d > 0, "mdec": m < 0,
            "up20": (C > ma20) & (ma20 > ma20_5), "ab60": C > ma60, "hi20": C >= hi20,
            "vr15": vr >= 1.5, "vlow": vr <= 0.7, "red3": (C > O) & (chg >= 0.03),
            "pull3": (C / np.where(c3 > 0, c3, np.nan) - 1 <= -0.03) & (C > ma60),
            "kdx": (K > D) & (K1 <= D1) & (K < 80), "bias_ok": (bias >= 0) & (bias <= 0.05), "bias_hi": bias >= 0.10,
            "lu": chg >= LU, "rs20": C / np.where(c20 > 0, c20, np.nan) - 1 >= 0.10,
        }
    flags = np.zeros(n, dtype=np.int64)
    for k, arr in fl.items():
        flags |= (np.nan_to_num(np.asarray(arr, dtype=float)) > 0).astype(np.int64) << FIDX[k]
    tv1 = C * V
    tv20 = _roll_mean(tv1, 20)
    R = np.full((n, len(COLS)), np.nan, dtype=np.float32)
    keep = np.zeros(n, dtype=bool)
    for i in range(60, n - 1):
        j = i + 1
        di, de = dates[i], dates[j]
        tw_i, us_e = tw.get(di) or {}, us.get(de) or {}
        b = 0
        if tw_i.get("bull20"):
            b |= 1 << FIDX["tw20"]
        if tw_i.get("bull60"):
            b |= 1 << FIDX["tw60"]
        sx = us_e.get("sox")
        if sx is not None:
            b |= (1 << FIDX["sox_up"]) if sx >= 1 else 0
            b |= (1 << FIDX["sox_dn"]) if sx <= -1 else 0
        if us_e.get("sox_up20"):
            b |= 1 << FIDX["sox20"]
        if (us_e.get("tsm") or 0) >= 1:
            b |= 1 << FIDX["tsm_up"]
        if us_e.get("ndx") is not None and us_e["ndx"] > 0:
            b |= 1 << FIDX["ndx_up"]
        if C[i] > 0 and O[j] > 0:
            g = O[j] / C[i] - 1
            b |= (1 << FIDX["gap_up"]) if g >= 0.01 else 0
            b |= (1 << FIDX["gap_dn"]) if g <= -0.01 else 0
            if C[j] >= O[j]:
                b |= 1 << FIDX["e_red"]
            if C[j] / C[i] - 1 >= 0.01:
                b |= 1 << FIDX["e_up1"]
            if H[j] > L[j] and (C[j] - L[j]) / (H[j] - L[j]) >= 0.7:
                b |= 1 << FIDX["e_hipos"]
        flags[i] |= b
        if not (C[i] > 0 and O[j] > 0 and C[j] > 0):
            continue
        open_locked = O[j] / C[i] - 1 >= LU and O[j] >= H[j] - 1e-9
        close_locked = C[j] / C[i] - 1 >= LU and C[j] >= H[j] - 1e-9
        if not open_locked:
            R[i, :len(EXITS)] = _exit_returns(O, H, L, C, j, O[j], True) - COST
        if not close_locked:
            R[i, len(EXITS):] = _exit_returns(O, H, L, C, j, C[j], False) - COST
        keep[i] = True
    idx = np.nonzero(keep & np.isfinite(tv20))[0]
    return {"sig_idx": idx, "tv20": tv20[idx], "tv1": tv1[idx], "flags": flags[idx], "R": R[idx]}


# ───────────────────────── 全母體彙整、熱門排名 ─────────────────────────
def day_rank(day, val):
    """day：每列的交易日序號；val：排名依據（大到小）。回傳每列在當天的名次（1 起算）。"""
    order = np.lexsort((-val, day))
    rank = np.empty(len(day), dtype=np.int32)
    d_sorted = day[order]
    start = np.r_[0, np.nonzero(np.diff(d_sorted))[0] + 1]
    pos = np.arange(len(day)) - np.repeat(start, np.diff(np.r_[start, len(day)]))
    rank[order] = pos + 1
    return rank


class Panel:
    def __init__(self, day, flags, R, rank20, rank1, dates):
        self.day, self.flags, self.R, self.rank20, self.rank1 = day, flags, R, rank20, rank1
        self.dates = dates
        self.is_ = np.array([dates[k] <= IS_END for k in day])
        self.year = np.array([int(dates[k][:4]) for k in day])

    def subset(self, mask):
        p = Panel.__new__(Panel)
        for k in ("day", "flags", "R", "rank20", "rank1", "is_", "year"):
            setattr(p, k, getattr(self, k)[mask])
        p.dates = self.dates
        return p


def build_panel(per_symbol, all_dates):
    """per_symbol：[(dates_of_symbol, stock_arrays 結果)]；all_dates：全部交易日（排序）。"""
    pos = {d: k for k, d in enumerate(all_dates)}
    days, fl, Rs, t20, t1 = [], [], [], [], []
    for ds, a in per_symbol:
        if a is None or not len(a["sig_idx"]):
            continue
        days.append(np.array([pos[ds[i]] for i in a["sig_idx"]], dtype=np.int32))
        fl.append(a["flags"])
        Rs.append(a["R"])
        t20.append(a["tv20"])
        t1.append(a["tv1"])
    day = np.concatenate(days)
    tv20, tv1 = np.concatenate(t20), np.concatenate(t1)
    return Panel(day, np.concatenate(fl), np.concatenate(Rs), day_rank(day, tv20), day_rank(day, tv1), list(all_dates))


# ───────────────────────── 統計與搜尋 ─────────────────────────
def col_stats(R, is_, day):
    """R：(n, cols)。回傳每欄 IS/OOS 的 n、勝率、平均，以及有樣本的天數。"""
    v = np.isfinite(R)
    w = (R > 0) & v
    Rz = np.where(v, R, 0.0)
    out = {}
    for tag, sel in (("is", is_), ("oos", ~is_)):
        vv, ww, rr = v[sel], w[sel], Rz[sel]
        nn = vv.sum(0)
        out["n_" + tag] = nn
        with np.errstate(invalid="ignore", divide="ignore"):
            out["win_" + tag] = np.where(nn > 0, ww.sum(0) / np.maximum(nn, 1) * 100, np.nan)
            out["avg_" + tag] = np.where(nn > 0, rr.sum(0) / np.maximum(nn, 1), np.nan)
        out["days_" + tag] = len(np.unique(day[sel]))
    return out


def combo_list(n_flags, max_k=3):
    out = []
    for k in range(1, max_k + 1):
        out += list(combinations(range(n_flags), k))
    return out


def search(P, base, min_is=200, min_oos=150, min_days_is=60, min_days_oos=40, win_floor=55.0, win_edge=5.0, avg_edge=0.3,
           max_k=3, flags=None, keep_top=400):
    """P：Panel（已篩熱門母體）；base：col_stats(全部熱門) 基準。回傳 (passes, n_tested)。
    通過＝樣本內外各自：n、天數達門檻；勝率≥win_floor 且比基準高≥win_edge；平均>0 且比基準高≥avg_edge。依樣本內勝率排序。"""
    F = P.flags if flags is None else flags
    passes, tested = [], 0
    for cmb in combo_list(len(FLAGS), max_k):
        m = 0
        for b in cmb:
            m |= 1 << b
        sel = (F & m) == m
        cnt = int(sel.sum())
        if cnt < min_is + min_oos:
            tested += len(COLS) if not (m & ENTRY_MASK) else len(COLS) - len(E0_COLS)
            continue
        idx = np.nonzero(sel)[0]
        st = col_stats(P.R[idx], P.is_[idx], P.day[idx])
        for c in range(len(COLS)):
            if (m & ENTRY_MASK) and c in E0_COLS:
                continue
            tested += 1
            if st["n_is"][c] < min_is or st["n_oos"][c] < min_oos:
                continue
            if st["days_is"] < min_days_is or st["days_oos"] < min_days_oos:
                continue
            ok = True
            for tag in ("is", "oos"):
                wv, av = st["win_" + tag][c], st["avg_" + tag][c]
                if not (wv >= win_floor and wv >= base["win_" + tag][c] + win_edge and av > 0 and av >= base["avg_" + tag][c] + avg_edge):
                    ok = False
                    break
            if ok:
                passes.append({"combo": cmb, "col": c, "n_is": int(st["n_is"][c]), "n_oos": int(st["n_oos"][c]),
                               "win_is": round(float(st["win_is"][c]), 1), "win_oos": round(float(st["win_oos"][c]), 1),
                               "avg_is": round(float(st["avg_is"][c]), 3), "avg_oos": round(float(st["avg_oos"][c]), 3)})
    passes.sort(key=lambda r: (-r["win_is"], -r["avg_is"]))
    return passes[:keep_top], tested, len(passes)


def placebo_flags(P, seed):
    """條件打亂：個股條件在「同一天內」打亂（保留當天的市場漲跌）、大盤／美股條件在「日期之間」打亂。"""
    rng = np.random.default_rng(seed)
    F = P.flags.copy()
    order = np.argsort(P.day, kind="stable")
    d_sorted = P.day[order]
    start = np.r_[0, np.nonzero(np.diff(d_sorted))[0] + 1]
    end = np.r_[start[1:], len(order)]
    stock_bits = sum(1 << i for i, (_k, _l, c) in enumerate(FLAGS) if c in ("stock", "entry"))
    newF = F & ~np.int64(stock_bits)
    src = F & np.int64(stock_bits)
    for s, e in zip(start, end):
        rows = order[s:e]
        newF[rows] |= src[rows[rng.permutation(len(rows))]]
    # 日期層級：把每個日期的 date 旗標整組換成另一個隨機日期的
    udays = np.unique(P.day)
    day_bits = sum(1 << i for i in DATE_FLAGS)
    dval = {}
    for s, e in zip(start, end):
        dval[int(d_sorted[s])] = int(F[order[s]] & day_bits)
    perm = dict(zip(udays.tolist(), rng.permutation(udays).tolist()))
    newF = newF & ~np.int64(day_bits)
    newF |= np.array([dval.get(perm[int(d)], 0) for d in P.day], dtype=np.int64)
    return newF


def detail(P, cmb, c, dates):
    """單一組合＋出場的細節：逐年勝率、日聚合 t（樣本外）、比同日熱門平均的超額、平均賺／賠、最差 5%。"""
    m = 0
    for b in cmb:
        m |= 1 << b
    sel = ((P.flags & m) == m) & np.isfinite(P.R[:, c])
    r, dy, isx, yr = P.R[sel, c], P.day[sel], P.is_[sel], P.year[sel]
    allv = np.isfinite(P.R[:, c])
    dm = {}
    if allv.any():
        s = np.bincount(P.day[allv], weights=P.R[allv, c], minlength=len(dates))
        k = np.bincount(P.day[allv], minlength=len(dates))
        dm = np.where(k > 0, s / np.maximum(k, 1), np.nan)
    ex = r - dm[dy] if len(dm) else np.full(len(r), np.nan)
    years = {}
    for y in sorted(set(yr.tolist())):
        q = r[yr == y]
        years[str(y)] = {"n": int(len(q)), "win": round(float((q > 0).mean() * 100), 1), "avg": round(float(q.mean()), 2)}

    def _t(sel2):
        if sel2.sum() < 5:
            return None
        s = np.bincount(dy[sel2], weights=r[sel2], minlength=len(dates))
        k = np.bincount(dy[sel2], minlength=len(dates))
        dmean = s[k > 0] / k[k > 0]
        if len(dmean) < 5 or dmean.std(ddof=1) == 0:
            return None
        return round(float(dmean.mean() / (dmean.std(ddof=1) / np.sqrt(len(dmean)))), 2)
    wins, losses = r[r > 0], r[r <= 0]
    return {"years": years, "t_is": _t(isx), "t_oos": _t(~isx), "ex_is": round(float(np.nanmean(ex[isx])), 3) if isx.any() else None,
            "ex_oos": round(float(np.nanmean(ex[~isx])), 3) if (~isx).any() else None,
            "avg_win": round(float(wins.mean()), 2) if len(wins) else None, "avg_loss": round(float(losses.mean()), 2) if len(losses) else None,
            "p5": round(float(np.percentile(r, 5)), 2) if len(r) else None, "days": int(len(np.unique(dy)))}


def combo_name(cmb):
    return "＋".join(FLAGS[b][1] for b in cmb)


def single_flag_table(P, base, cols=("E1|X1", "E1|H5", "E1|H20", "E0|H10", "E1|S20")):
    """每個條件單獨看（熱門母體內）：關鍵出場的樣本內外勝率／平均 vs 基準。"""
    out = {}
    ci = [COLS.index(c) for c in cols]
    for b, (k, lab, kind) in enumerate(FLAGS):
        sel = (P.flags >> b) & 1 == 1
        if sel.sum() < 50:
            continue
        st = col_stats(P.R[sel][:, ci], P.is_[sel], P.day[sel])
        row = {}
        for j, c in enumerate(cols):
            if kind == "entry" and c.startswith("E0"):
                continue
            row[c] = {"n": int(st["n_is"][j] + st["n_oos"][j]), "win_is": _r1(st["win_is"][j]), "win_oos": _r1(st["win_oos"][j]),
                      "avg_is": _r3(st["avg_is"][j]), "avg_oos": _r3(st["avg_oos"][j]),
                      "d_win_is": _r1(st["win_is"][j] - base["win_is"][COLS.index(c)]),
                      "d_win_oos": _r1(st["win_oos"][j] - base["win_oos"][COLS.index(c)])}
        out[lab] = row
    return out


def _r1(x):
    return None if x is None or not np.isfinite(x) else round(float(x), 1)


def _r3(x):
    return None if x is None or not np.isfinite(x) else round(float(x), 3)


def base_table(base):
    return {COLS[c]: {"n_is": int(base["n_is"][c]), "n_oos": int(base["n_oos"][c]), "win_is": _r1(base["win_is"][c]),
                      "win_oos": _r1(base["win_oos"][c]), "avg_is": _r3(base["avg_is"][c]), "avg_oos": _r3(base["avg_oos"][c])}
            for c in range(len(COLS))}


def analyze_universe(P, label, placebo_rounds=2, top_detail=40, **kw):
    """P：已篩母體 Panel。回傳報告 dict。"""
    base = col_stats(P.R, P.is_, P.day)
    passes, tested, n_pass = search(P, base, **kw)
    plac = []
    for s in range(placebo_rounds):
        _p, _t, n_p = search(P, base, flags=placebo_flags(P, 1000 + s), keep_top=0, **kw)
        plac.append(n_p)
    rows = []
    for k, r in enumerate(passes):
        x = {"條件": combo_name(r["combo"]), "k": len(r["combo"]), "進場": ENTRY_LABEL[COLS[r["col"]][:2]],
             "出場": EXIT_LABEL[COLS[r["col"]][3:]], "col": COLS[r["col"]],
             **{kk: r[kk] for kk in ("n_is", "n_oos", "win_is", "win_oos", "avg_is", "avg_oos")}}
        if k < top_detail:
            x.update(detail(P, r["combo"], r["col"], P.dates))
        rows.append(x)
    # 每個條件數（1／2／3 個條件）各自最好的
    best_k = {}
    for x in rows:
        best_k.setdefault(str(x["k"]), [])
        if len(best_k[str(x["k"])]) < 10:
            best_k[str(x["k"])].append({kk: x[kk] for kk in ("條件", "進場", "出場", "n_is", "n_oos", "win_is", "win_oos", "avg_is", "avg_oos")})
    return {"label": label, "events": int(len(P.day)), "days": int(len(np.unique(P.day))), "base": base_table(base),
            "single": single_flag_table(P, base), "tested": int(tested), "passed": int(n_pass), "placebo_passed": plac,
            "top": rows, "best_by_k": best_k}
