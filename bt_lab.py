"""【2026-10-14】自助回測引擎（純函式，不碰網路／資料庫，可離線測試）。

用途：總指揮官自己輸入「進場條件（戰卡分數）＋出場方式＋成本」，程式自動回測並給「通過／待觀察／不通過」判定，
不必每次請 Claude 分析。參考 FinLab／XQ／TradingView 的報告欄位（勝率、平均與中位報酬、獲利因子、最大回撤、平均持有、
夏普），再加上它們沒有的防呆：樣本內外分開、贏不贏同日隨機挑、日期聚類 t 值、少數大贏家集中度、反覆試驗警示。

資料（panel）：長表 DataFrame，欄位 date, sym, open, high, low, close, score（分數是收盤後算出的）。
時序規則（避免偷看未來）：分數在第 t 日收盤後才知道 → 最快 t+1 日開盤進場（entry_at='next_open'）或 t+1 日收盤（'next_close'）。
出場同理：分數類出場在 k 日收盤後決定，依 entry_at 的同一慣例於 k+1 日成交。
尚未出場的部位不納入統計（不以期末價計價，避免高估），另外回報筆數。
"""
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

FEE = 0.001425
TAX = 0.003


@dataclass
class Spec:
    score_min: float = 6.0
    score_max: float = None
    mode: str = "first_cross"          # first_cross＝分數由低於門檻升到門檻以上的第一天；every_day＝每天符合都算（同檔持有中不重複進場）
    entry_at: str = "next_open"        # next_open｜next_close
    start: str = None
    end: str = None
    fee_disc: float = 1.0              # 手續費折扣（1.0＝無折）
    slip_bps: float = 0.0              # 單邊滑價（基點）
    max_hold: int = 60
    exits: list = field(default_factory=lambda: [{"type": "hold", "n": 5}, {"type": "hold", "n": 10},
                                                 {"type": "hold", "n": 20}, {"type": "hold", "n": 30}])
    is_frac: float = 0.6               # 前 60% 進場日為樣本內
    seed: int = 7


def round_trip_cost(spec):
    return 2 * FEE * spec.fee_disc + TAX + 2 * spec.slip_bps / 1e4


def exit_label(ex):
    t = ex["type"]
    if t == "hold":
        return f"持有{ex['n']}日"
    if t == "tpsl":
        return f"停利{ex['tp'] * 100:g}%／停損{ex['sl'] * 100:g}%／最長{ex.get('n', 20)}日"
    if t == "ma20":
        return "收盤跌破 MA20"
    if t == "atr":
        return f"ATR {ex['k']:g} 倍移動停損"
    if t == "score_below":
        return f"分數跌破 {ex['y']:g}"
    return t


# ------------------------------------------------------------------ 資料整理

def prepare(panel):
    """長表 → 以『日期×股票』對齊的 2D 陣列（含 MA20、ATR14）。回傳 dict。"""
    p = panel.sort_values(["sym", "date"]).copy()
    g = p.groupby("sym", sort=False)
    p["ma20"] = g["close"].transform(lambda s: s.rolling(20, min_periods=20).mean())
    pc = g["close"].shift(1)
    tr = pd.concat([p["high"] - p["low"], (p["high"] - pc).abs(), (p["low"] - pc).abs()], axis=1).max(axis=1)
    p["atr"] = tr.groupby(p["sym"], sort=False).transform(lambda s: s.rolling(14, min_periods=14).mean())
    dates = np.array(sorted(p["date"].unique()))
    syms = np.array(sorted(p["sym"].unique()))
    di = {d: i for i, d in enumerate(dates)}
    si = {s: i for i, s in enumerate(syms)}
    shape = (len(syms), len(dates))
    A = {k: np.full(shape, np.nan) for k in ("open", "high", "low", "close", "score", "ma20", "atr")}
    ii = p["sym"].map(si).to_numpy()
    jj = p["date"].map(di).to_numpy()
    for k in A:
        A[k][ii, jj] = p[k].to_numpy(dtype=float)
    return {"dates": dates, "syms": syms, **A}


# ------------------------------------------------------------------ 進場

def find_entries(D, spec):
    """回傳 list[(si, ti)]：ti 為訊號日索引（分數收盤後才知道）。"""
    S = D["score"]
    ok = S >= spec.score_min
    if spec.score_max is not None:
        ok &= S <= spec.score_max
    ok &= np.isfinite(S)
    if spec.mode == "first_cross":
        prev = np.concatenate([np.full((S.shape[0], 1), np.nan), S[:, :-1]], axis=1)
        ok &= np.isfinite(prev) & (prev < spec.score_min)
    dates = D["dates"]
    lo = 0 if spec.start is None else int(np.searchsorted(dates, np.datetime64(spec.start)))
    hi = len(dates) if spec.end is None else int(np.searchsorted(dates, np.datetime64(spec.end), side="right"))
    out = []
    for si in range(S.shape[0]):
        busy_until = -1
        for ti in np.flatnonzero(ok[si]):
            if ti < lo or ti >= hi or ti + 1 >= len(dates):
                continue
            if spec.mode == "every_day" and ti <= busy_until:
                continue
            out.append((si, int(ti)))
            busy_until = ti          # 簡化：同檔同日只進一次；every_day 的重疊在出場後才允許（見 run_exit 後的過濾）
    return out


# ------------------------------------------------------------------ 出場模擬

def _entry_px(D, si, e, spec):
    return D["open"][si, e] if spec.entry_at == "next_open" else D["close"][si, e]


def _fill(D, si, k, spec):
    """決定在 k 日收盤後 → 依慣例於 k+1 日成交；回傳 (idx, price) 或 None。"""
    j = k + 1
    n = D["close"].shape[1]
    if j >= n:
        return None
    px = D["open"][si, j] if spec.entry_at == "next_open" else D["close"][si, j]
    return (j, px) if np.isfinite(px) else None


def simulate_one(D, si, e, p0, ex, spec):
    """單筆：回傳 (exit_idx, exit_px, reason) 或 None（尚未出場／資料不足）。e＝進場日索引。"""
    n = D["close"].shape[1]
    H, L, O, C = D["high"][si], D["low"][si], D["open"][si], D["close"][si]
    cap = min(e + spec.max_hold, n - 1)
    t = ex["type"]
    first = e if spec.entry_at == "next_open" else e + 1
    if t == "hold":
        x = e + ex["n"]
        if x > n - 1 or not np.isfinite(C[x]):
            return None
        return x, C[x], "hold"
    if t == "tpsl":
        tp, sl = p0 * (1 + ex["tp"]), p0 * (1 - ex["sl"])
        last = min(e + ex.get("n", 20), n - 1)
        for k in range(first, last + 1):
            if not (np.isfinite(H[k]) and np.isfinite(L[k])):
                continue
            hit_sl, hit_tp = L[k] <= sl, H[k] >= tp
            if hit_sl:                                   # 同日兩者都碰到 → 先算停損（保守）
                px = O[k] if (k > e and np.isfinite(O[k]) and O[k] <= sl) else sl
                return k, px, "sl"
            if hit_tp:
                px = O[k] if (k > e and np.isfinite(O[k]) and O[k] >= tp) else tp
                return k, px, "tp"
        if e + ex.get("n", 20) > n - 1 or not np.isfinite(C[last]):
            return None
        return last, C[last], "time"
    if t in ("ma20", "atr", "score_below"):
        hi_close = C[e] if np.isfinite(C[e]) else p0
        for k in range(e + 1, cap + 1):
            if not np.isfinite(C[k]):
                continue
            hi_close = max(hi_close, C[k - 1] if np.isfinite(C[k - 1]) else hi_close)
            trig = False
            if t == "ma20":
                trig = k >= e + 2 and np.isfinite(D["ma20"][si, k]) and C[k] < D["ma20"][si, k]
            elif t == "atr":
                a = D["atr"][si, e] if np.isfinite(D["atr"][si, e]) else np.nan
                trig = np.isfinite(a) and C[k] < hi_close - ex["k"] * a
            else:
                s = D["score"][si, k]
                trig = np.isfinite(s) and s < ex["y"]
            if trig:
                if t == "score_below":
                    f = _fill(D, si, k, spec)
                    if f is None:
                        return None
                    return f[0], f[1], "score"
                return k, C[k], t
        if cap < e + spec.max_hold:        # 資料到頭仍未觸發 → 尚未出場
            return None
        return cap, C[cap], "timeout"
    raise ValueError(f"未知出場方式：{t}")


def run_exit(D, entries, ex, spec, cost):
    """回傳 trades DataFrame（只含已出場）與未出場筆數。"""
    rows, open_n = [], 0
    last_exit = {}
    dates = D["dates"]
    for si, ti in entries:
        e = ti + 1
        p0 = _entry_px(D, si, e, spec)
        if not np.isfinite(p0) or p0 <= 0:
            continue
        if spec.mode == "every_day" and last_exit.get(si, -1) >= e:
            continue                       # 同檔持有中，不重複進場
        r = simulate_one(D, si, e, p0, ex, spec)
        if r is None:
            open_n += 1
            continue
        x, px, why = r
        last_exit[si] = x
        gross = px / p0 - 1
        rows.append((dates[ti], si, e, x, gross, gross - cost, x - e, why))
    df = pd.DataFrame(rows, columns=["signal_date", "si", "e", "x", "gross", "net", "hold", "why"])
    return df, open_n


# ------------------------------------------------------------------ 基準：等權大盤、隨機同日同檔數

def market_index(D):
    C = D["close"]
    with np.errstate(invalid="ignore"):
        r = C[:, 1:] / C[:, :-1] - 1
    r = np.where(np.isfinite(r) & (np.abs(r) < 0.5), r, np.nan)
    dr = np.nanmean(r, axis=0)
    dr = np.nan_to_num(dr, nan=0.0)
    return np.concatenate([[1.0], np.cumprod(1 + dr)])


def random_trades(D, entries, ex, spec, cost, rng):
    """對每筆訊號在『同一訊號日、其他股票』隨機挑一檔，用同一出場規則（同日同檔數基準）。"""
    ok = np.isfinite(D["close"]) & np.isfinite(D["open"])
    ent = []
    by_day = {}
    for si, ti in entries:
        by_day.setdefault(ti, []).append(si)
    for ti, sis in by_day.items():
        if ti + 1 >= ok.shape[1]:
            continue
        cand = np.flatnonzero(ok[:, ti] & ok[:, ti + 1])
        cand = np.setdiff1d(cand, sis)
        if len(cand) == 0:
            continue
        pick = rng.choice(cand, size=len(sis), replace=len(cand) < len(sis))
        ent += [(int(s), ti) for s in pick]
    sp2 = Spec(**{**spec.__dict__, "mode": "first_cross"})
    df, _ = run_exit(D, ent, ex, sp2, cost)
    return df


# ------------------------------------------------------------------ 統計

def _pf(x):
    pos, neg = x[x > 0].sum(), -x[x < 0].sum()
    return float("inf") if neg == 0 and pos > 0 else (pos / neg if neg > 0 else float("nan"))


def cluster_t(tr, col="net"):
    """以『訊號日』為單位先平均（同一天的訊號高度相關，不能當獨立樣本），再對日平均做 t。"""
    if tr.empty:
        return float("nan"), 0
    g = tr.groupby("signal_date")[col].mean()
    if len(g) < 3 or g.std(ddof=1) == 0:
        return float("nan"), len(g)
    return float(g.mean() / (g.std(ddof=1) / np.sqrt(len(g)))), len(g)


def equity_stats(D, tr):
    """等權持有所有訊號部位的日報酬曲線：每天的報酬＝當天持有中各部位日報酬平均。回傳 (MDD, 年化報酬, 夏普)；部位太少回 None。"""
    if tr.empty:
        return None
    C = D["close"]
    n = C.shape[1]
    s, c = np.zeros(n), np.zeros(n)
    for si, e, x in zip(tr["si"], tr["e"], tr["x"]):
        if x <= e:
            continue
        seg = C[si, e:x + 1]
        with np.errstate(invalid="ignore"):
            r = seg[1:] / seg[:-1] - 1
        r = np.where(np.isfinite(r) & (np.abs(r) < 0.5), r, 0.0)
        s[e + 1:x + 1] += r
        c[e + 1:x + 1] += 1
    m = c > 0
    if m.sum() < 20:
        return None
    dr = s[m] / c[m]
    eq = np.cumprod(1 + dr)
    mdd = float((eq / np.maximum.accumulate(eq) - 1).min())
    yrs = m.sum() / 245.0
    cagr = float(eq[-1] ** (1 / yrs) - 1) if yrs > 0 else float("nan")
    sharpe = float(dr.mean() / dr.std(ddof=1) * np.sqrt(245)) if dr.std(ddof=1) > 0 else float("nan")
    return {"mdd": mdd, "cagr": cagr, "sharpe": sharpe, "days": int(m.sum())}


def summarize(D, tr, rnd, mkt, spec, open_n):
    """彙總一組出場方式的結果。"""
    if tr.empty:
        return {"n": 0, "open_n": open_n}
    split = np.sort(tr["signal_date"].unique())
    cut = split[int(len(split) * spec.is_frac)] if len(split) > 1 else split[0]
    is_, oos = tr[tr["signal_date"] < cut], tr[tr["signal_date"] >= cut]
    out = {
        "n": len(tr), "open_n": open_n, "signal_days": int(tr["signal_date"].nunique()),
        "win": float((tr["net"] > 0).mean()), "mean_net": float(tr["net"].mean()), "median_net": float(tr["net"].median()),
        "mean_gross": float(tr["gross"].mean()), "pf": _pf(tr["net"].to_numpy()), "avg_hold": float(tr["hold"].mean()),
        "worst": float(tr["net"].min()), "best": float(tr["net"].max()),
    }
    out["is"] = {"n": len(is_), "mean_net": float(is_["net"].mean()) if len(is_) else float("nan"), "win": float((is_["net"] > 0).mean()) if len(is_) else float("nan")}
    out["oos"] = {"n": len(oos), "mean_net": float(oos["net"].mean()) if len(oos) else float("nan"), "win": float((oos["net"] > 0).mean()) if len(oos) else float("nan")}
    yr = tr.groupby(pd.to_datetime(tr["signal_date"]).dt.year)["net"].agg(["count", "mean"])
    out["years"] = {int(y): {"n": int(r["count"]), "mean_net": float(r["mean"])} for y, r in yr.iterrows()}
    out["t_cluster"], out["t_days"] = cluster_t(tr)
    # 同期等權大盤
    mk = np.array([mkt[x] / mkt[e] - 1 for e, x in zip(tr["e"], tr["x"])])
    out["mean_market"] = float(np.mean(mk))
    out["excess_market"] = float(tr["gross"].mean() - np.mean(mk))
    if rnd is not None and len(rnd):
        out["random_net"] = float(rnd["net"].mean())
        out["excess_random"] = float(tr["net"].mean() - rnd["net"].mean())
        r_oos = rnd[rnd["signal_date"] >= cut]
        out["excess_random_oos"] = float(oos["net"].mean() - r_oos["net"].mean()) if len(oos) and len(r_oos) else float("nan")
    # 集中度：獲利最大的 5 筆占全部獲利的比例
    pos = tr["net"][tr["net"] > 0].sort_values(ascending=False)
    out["top5_share"] = float(pos.head(5).sum() / pos.sum()) if pos.sum() > 0 else float("nan")
    out["equity"] = equity_stats(D, tr)
    return out


MIN_TRADES = 200
MIN_DAYS = 30


def verdict(s, n_tried=1):
    """回傳 (等級, [理由], [警示])。等級：通過／待觀察／不通過。門檻事先固定，不看結果調整。"""
    reasons, warns = [], []
    if s.get("n", 0) == 0:
        return "待觀察", ["沒有符合條件的已出場交易"], []
    if s["n"] < MIN_TRADES or s["signal_days"] < MIN_DAYS:
        return "待觀察", [f"樣本不足（{s['n']} 筆、{s['signal_days']} 個訊號日；至少 {MIN_TRADES} 筆、{MIN_DAYS} 個訊號日）"], _warns(s, n_tried)
    oos = s["oos"]["mean_net"]
    if not (oos == oos) or oos <= 0:
        reasons.append(f"樣本外扣成本後平均 {oos * 100:+.2f}%（≤0）")
        return "不通過", reasons, _warns(s, n_tried)
    ok = True
    ex_r = s.get("excess_random_oos", float("nan"))
    if not (ex_r == ex_r) or ex_r <= 0:
        ok = False
        reasons.append("樣本外沒有贏過『同日隨機挑同檔數』")
    if not (s["t_cluster"] == s["t_cluster"]) or s["t_cluster"] < 2:
        ok = False
        reasons.append(f"以訊號日為單位的 t 值 {s['t_cluster']:.2f}（<2）" if s["t_cluster"] == s["t_cluster"] else "t 值無法計算")
    ys = [v["mean_net"] for v in s["years"].values() if v["n"] >= 30]
    if len(ys) >= 3 and sum(1 for v in ys if v > 0) < 0.6 * len(ys):
        ok = False
        reasons.append(f"逐年只有 {sum(1 for v in ys if v > 0)}/{len(ys)} 年為正")
    if ok:
        return "通過", ["樣本外為正、贏過隨機、t≥2、逐年多數為正"], _warns(s, n_tried)
    return "待觀察", reasons, _warns(s, n_tried)


def _warns(s, n_tried):
    w = []
    if s.get("median_net") is not None and s["mean_net"] > 0 and s["median_net"] <= 0:
        w.append("平均為正但中位數 ≤0：獲利靠少數大贏家")
    if s.get("top5_share") == s.get("top5_share") and s.get("top5_share", 0) > 0.3:
        w.append(f"最大 5 筆獲利占全部獲利 {s['top5_share'] * 100:.0f}%（集中）")
    if n_tried >= 10:
        w.append(f"這次連線已試了 {n_tried} 組條件：越常試，越容易挑到只是剛好好看的組合（多重檢定），請用沒看過的期間再驗證")
    w.append("分數為 5 年重建版（部分因子無法重建，視為不觸發），與網頁即時戰卡分數不完全相同")
    w.append("母體為近期成交值大的股票，有存活者偏誤；出場價以收盤／開盤近似")
    return w


# ------------------------------------------------------------------ 入口

def run(D, spec, n_tried=1):
    """n_tried：這次連線已試過幾組條件（越多越要小心多重檢定）。回傳 dict：{exit_label: {summary, verdict, reasons, warnings}}、entries 數。"""
    cost = round_trip_cost(spec)
    entries = find_entries(D, spec)
    rng = np.random.default_rng(spec.seed)
    mkt = market_index(D)
    res = {}
    for ex in spec.exits:
        tr, open_n = run_exit(D, entries, ex, spec, cost)
        rnd = random_trades(D, entries, ex, spec, cost, rng) if len(entries) else None
        s = summarize(D, tr, rnd, mkt, spec, open_n)
        v, why, warns = verdict(s, n_tried=n_tried)
        res[exit_label(ex)] = {"summary": s, "verdict": v, "reasons": why, "warnings": warns}
    return {"n_entries": len(entries), "cost": cost, "results": res}
