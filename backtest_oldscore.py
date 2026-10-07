#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
backtest_oldscore.py —— 「舊評分波段做多」5 年診斷＋治本修復回測（2026-10-07；跑在 GitHub Actions；結果只寫私有表 ui_selftest_reports）

【使用者要求（10/7）】舊評分波段問題：找出原先修正規則(R42 重新校準從來沒做)，要治本，不是單純停止進場。
【這支腳本做什麼】
  1. 以 5 年日K＋外資/投信買賣超＋月營收，重建舊評分（warroom_core 因子註冊表 + apply_override_rules 的同一套規則，
     向量化；無法重建 5 年歷史的因子＝基本面地雷/財務體質/分點代理/隔日沖，視為不觸發，與實盤缺資料時行為相同）。
  2. 診斷：分數分桶的未來報酬、逐因子「有／無」的市場中性超額報酬（日期聚類 t 值、樣本內/外、逐年同號）、評分 IC。
  3. 校準（R42 補做）：以「逐年走前(walk-forward)」嶺迴歸估計各因子權重（負係數→0，因為實盤權重倍率不允許負值），
     樣本外逐年檢驗；再疊加預先指定的少數閘門/防追高濾網（盤勢、乖離、短期漲幅）。
  4. 出場比較：實盤現行(跌破5/10MA、時間停損、早盤+5%、過熱) vs 持有N日、停利停損、跌破MA20、ATR 移動停損……
     同一批進場、同成本下比較，並與『同日隨機挑同樣檔數』的基準比。
【誠實限制】存活者偏誤(母體=近期成交值大者)、yfinance 還原價與實盤用價有差、進場近似為『選股日隔天收盤』(尾盤 13:20 進場)、
  出場只用收盤/開盤價判斷(盤中觸發以收盤近似)、同分排序用固定亂數、因子基本面地雷等不可重建、訊號互相關使有效樣本小於筆數。
【判定】全部以樣本外(OOS：後 40%；或走前逐年)為準；樣本內只用來挑，不用來宣稱。
"""
import os
import sys
import json
import time
import math
import argparse
import itertools
import datetime as dt

import numpy as np
import pandas as pd

import backtest_rules as br
import backtest_revenue as brv
import regime as rg

COST = br.COST_ROUND_TRIP            # 0.585%
HORIZONS = (3, 5, 10, 20)
IS_FRACTION = 0.60
TOPK = 10
FACTORS = ["ma", "fb", "vol", "ohcl", "buf", "comp", "consec", "inst", "pers", "rev"]
# 對應 warroom_core.ADDITIVE_FACTORS 名稱（實盤權重倍率用這些名稱）
FACTOR_NAME = {"ma": "ma_position", "fb": "foreign_buy", "vol": "volume_ratio", "ohcl": "open_high_close_low",
               "buf": "buffer_pct", "comp": "ma_compression_breakout", "consec": "consecutive_breakout",
               "inst": "institutional_resonance", "pers": "institutional_persistence", "rev": "revenue_momentum"}
MIN_DAY_SYMS = 50


# ------------------------------------------------------------------ 逐檔特徵（純函式）
def _rolling_run(flag):
    """連續 True 的長度（含當日）。flag: bool ndarray。"""
    out = np.zeros(len(flag), dtype=int)
    c = 0
    for i, f in enumerate(flag):
        c = c + 1 if f else 0
        out[i] = c
    return out


def build_symbol_frame(df, chip=None, rev_rows=None, market_bull=None):
    """df：OHLCV(DatetimeIndex)。chip：DataFrame(index=日期, 欄 f_buy,t_buy) 或 None。rev_rows：[(year,month,revenue)] 或 None。
    market_bull：Series(index=日期, bool) 或 None（None→視為多頭）。回傳逐日特徵 DataFrame（含未來報酬欄，只供回測用）。"""
    c, o, h, l, v = (df[k].astype(float) for k in ("Close", "Open", "High", "Low", "Volume"))
    F = pd.DataFrame(index=df.index)
    F["close"], F["open"], F["high"], F["low"], F["vol"] = c, o, h, l, v
    ma5, ma10, ma20, ma60 = (c.rolling(n).mean() for n in (5, 10, 20, 60))
    F["ma5"], F["ma10"], F["ma20"], F["ma60"] = ma5, ma10, ma20, ma60
    prev = c.shift(1)
    gain = (c - prev) / prev * 100
    F["gain"] = gain
    vr = v / v.rolling(20).mean()
    F["vr"] = vr
    tr = pd.concat([h - l, (h - prev).abs(), (l - prev).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean()
    F["atr"] = atr
    # --- 因子（與 warroom_core 的因子函式同一套規則）
    f_ma = np.where((c > ma5) & (ma5 > ma20), 2, np.where(c > ma5, 1, np.where(c < ma5, -2, 0)))
    f_ma = np.where(ma5.isna() | ma20.isna(), 0, f_ma)
    fb = chip["f_buy"].reindex(df.index) if chip is not None and "f_buy" in chip else pd.Series(np.nan, index=df.index)
    tb = chip["t_buy"].reindex(df.index) if chip is not None and "t_buy" in chip else pd.Series(np.nan, index=df.index)
    F["f_buy"], F["t_buy"] = fb, tb
    f_fb = np.where(fb > 0, 1, np.where(fb < 0, -1, 0))
    f_vol = np.where(vr < 0.6, -1, np.where(vr > 2.0, 1, 0))
    is_ohcl = (o > prev) & (c < o)
    f_ohcl = np.where(is_ohcl, -2, 0)
    def_line = ma5 - 0.5 * atr
    buffer_pct = (c - def_line) / c * 100
    f_buf = np.where(buffer_pct < 1.0, -1, 0)
    mx = pd.concat([ma5, ma20, ma60], axis=1).max(axis=1)
    mn = pd.concat([ma5, ma20, ma60], axis=1).min(axis=1)
    compression = (mx - mn) / mn
    comp_ok = ma60.notna() & vr.notna()
    f_comp = np.where(comp_ok & (compression < 0.05) & (vr >= 1.5) & (vr <= 2.5), 2,
                      np.where(comp_ok & (gain > 0) & (vr < 0.8), -1, 0))
    hh = ((h > h.shift(1)) & (l > l.shift(1))).astype(int)
    consec = (hh.rolling(5).sum() >= 2) & h.rolling(6).count().eq(6)
    f_consec = np.where(consec, 1, 0)
    f_inst = np.where((fb > 0) & (tb > 0), 2, 0)
    pos = (fb > 0).astype(float).where(fb.notna())
    streak3 = (pos.rolling(3).sum() == 3)
    f_pers = np.where(streak3, 2, 0)
    # 營收（次月 11 日起可用）
    rev_ok = pd.Series(False, index=df.index)
    if rev_rows:
        feats = brv.revenue_features(rev_rows)
        if feats:
            rf = pd.DataFrame(feats)
            rf["avail"] = pd.to_datetime(rf["sig_date"])
            rf = rf.sort_values("avail")[["avail", "yoy", "mom"]]
            left = pd.DataFrame({"d": df.index})
            m = pd.merge_asof(left, rf, left_on="d", right_on="avail")
            ok = (m["yoy"] > 0) & (m["mom"] > 0)
            rev_ok = pd.Series(ok.fillna(False).values, index=df.index)
    f_rev = np.where(rev_ok, 1, 0)
    for k, a in zip(FACTORS, (f_ma, f_fb, f_vol, f_ohcl, f_buf, f_comp, f_consec, f_inst, f_pers, f_rev)):
        F["f_" + k] = np.asarray(a, dtype=float)
    # --- 覆蓋規則
    std20 = c.rolling(20).std()
    overheated = (c > ma20 + 3 * std20) & (std20 > 0)
    day_range = h - l
    close_near_low = (day_range > 0) & ((c - l) / day_range <= 0.35)
    dump = (vr >= 2.0) & (c < o) & (gain < -1.0) & close_near_low
    below = (c < ma20)
    gate = below & below.shift(1, fill_value=False) & below.shift(2, fill_value=False) & ma20.shift(2).notna()
    vol5 = v.shift(1).rolling(5).mean()
    attack = (v >= vol5 * 1.5) & (c > o) & (vol5 > 0)
    run = pd.Series(_rolling_run(attack.values), index=df.index)
    reversal = (run.shift(1) >= 2) & (~attack)
    F["overheated"], F["dump"], F["gate"], F["reversal"] = overheated, dump, gate, reversal
    if market_bull is None:
        mb = pd.Series(True, index=df.index)
    else:
        mb = market_bull.reindex(df.index).fillna(True).astype(bool)
    F["market_bull"] = mb
    F["score_raw"] = sum(F["f_" + k] for k in FACTORS)
    F["score_old"] = apply_overrides(F["score_raw"], mb, dump, overheated, reversal, gate)
    # --- 輔助
    F["dist20"] = c / ma20 - 1
    F["run5"] = c / c.shift(5) - 1
    # --- 未來報酬（進場＝選股日隔天收盤；尾盤 13:20 進場的近似）
    E = c.shift(-1)
    for hz in HORIZONS:
        F[f"r{hz}"] = c.shift(-1 - hz) / E - 1
    return F


def apply_overrides(score, market_bull, dump, overheated, reversal, gate):
    """與 warroom_core.apply_override_rules 同順序（不含末日熔斷與隔日沖；無資料）。"""
    s = pd.Series(np.asarray(score, dtype=float), index=score.index if hasattr(score, "index") else None)
    mb = np.asarray(market_bull, dtype=bool)
    s = pd.Series(np.where(~mb & (s >= 6) & (s < 8), 5, s), index=s.index)
    s = pd.Series(np.where(np.asarray(dump), np.minimum(s, -3), s), index=s.index)
    s = pd.Series(np.where(np.asarray(overheated), np.minimum(s, 3), s), index=s.index)
    s = pd.Series(np.where(np.asarray(reversal), s - 2, s), index=s.index)
    s = pd.Series(np.where(np.asarray(gate), np.minimum(s, -7), s), index=s.index)
    return s


# ------------------------------------------------------------------ 面板
def build_panel(prices, chips, revs, eval_start):
    """prices: {sym: df}；chips: {sym: DataFrame}；revs: {sym: rows}。回傳 (長表 P, regime_flags DataFrame)。"""
    Fm = rg.regime_frame(prices)
    flags = rg.regime_flags(Fm)
    mbull = flags["up20"]
    parts = []
    for s, df in prices.items():
        F = build_symbol_frame(df, chips.get(s), revs.get(s), mbull)
        F["sym"] = s
        parts.append(F)
    P = pd.concat(parts)
    P.index.name = "date"
    P = P.reset_index()
    P = P[P["date"] >= pd.Timestamp(eval_start)].copy()
    cnt = P.groupby("date")["close"].transform("count")
    P = P[cnt >= min(MIN_DAY_SYMS, max(5, int(0.5 * len(prices))))].copy()
    for hz in HORIZONS:                               # 市場中性：減去同日橫斷面平均
        P[f"x{hz}"] = P[f"r{hz}"] - P.groupby("date")[f"r{hz}"].transform("mean")
    for nm in ("up20", "no_stress", "b50_up60", "stress"):
        P["rg_" + nm] = P["date"].map(flags[nm]).fillna(False).astype(bool)
    return P, flags


def split_date(P, frac=IS_FRACTION):
    ds = np.sort(P["date"].unique())
    return pd.Timestamp(ds[int(len(ds) * frac)])


# ------------------------------------------------------------------ 診斷
def _t(series, h):
    s = series.dropna()
    if len(s) < 5 or s.std() == 0:
        return None
    return float(s.mean() / s.std() * math.sqrt(len(s) / max(h, 1)))


def lift_table(P, mask, col, split, min_per_day=3, h=10):
    """mask 成立的列，逐日平均「市場中性超額報酬」→ 日期聚類。回傳 dict（IS/OOS/逐年）。"""
    sub = P.loc[mask & P[col].notna(), ["date", col]]
    g = sub.groupby("date")[col].agg(["mean", "count"])
    g = g[g["count"] >= min_per_day]
    out = {"n": int(g["count"].sum()), "days": int(len(g))}
    for nm, sel in (("IS", g.index < split), ("OOS", g.index >= split)):
        x = g.loc[sel, "mean"]
        out[nm] = {"days": int(len(x)), "lift_pct": None if len(x) == 0 else round(float(x.mean()) * 100, 3), "t": _t(x, h)}
    yrs = {}
    for y, x in g["mean"].groupby(g.index.year):
        if len(x) >= 20:
            yrs[int(y)] = round(float(x.mean()) * 100, 3)
    out["years"] = yrs
    _sgn = (out["IS"]["lift_pct"] or 0) > 0
    out["same_sign_years"] = (None if not yrs else f"{sum(1 for v in yrs.values() if (v > 0) == _sgn)}/{len(yrs)}")
    return out


def factor_flags(P):
    f = {}
    f["站穩多頭(+2)"] = P["f_ma"] == 2
    f["僅站上5MA(+1)"] = P["f_ma"] == 1
    f["跌破5MA(-2)"] = P["f_ma"] == -2
    f["外資買超(+1)"] = P["f_fb"] == 1
    f["外資賣超(-1)"] = P["f_fb"] == -1
    f["爆量>2倍(+1)"] = P["f_vol"] == 1
    f["量縮<0.6倍(-1)"] = P["f_vol"] == -1
    f["開高走低(-2)"] = P["f_ohcl"] == -2
    f["緩衝<1%(-1)"] = P["f_buf"] == -1
    f["均線糾結+爆量突破(+2)"] = P["f_comp"] == 2
    f["上漲量縮(-1)"] = P["f_comp"] == -1
    f["連續高低點遞增(+1)"] = P["f_consec"] == 1
    f["法人共振(+2)"] = P["f_inst"] == 2
    f["法人持續性(+2)"] = P["f_pers"] == 2
    f["營收雙增(+1)"] = P["f_rev"] == 1
    f["布林過熱"] = P["overheated"]
    f["爆量下殺"] = P["dump"]
    f["趨勢閘門(連3日破月線)"] = P["gate"]
    f["連續攻擊熄燈"] = P["reversal"]
    f["【追價檢驗】當日漲幅>3%"] = P["gain"] > 3
    f["【追價檢驗】距MA20>10%"] = P["dist20"] > 0.10
    f["【追價檢驗】5日漲幅>10%"] = P["run5"] > 0.10
    f["【回檔檢驗】5日跌幅>5%且仍在MA60上"] = (P["run5"] < -0.05) & (P["close"] > P["ma60"])
    return f


def score_buckets(P, col="score_old", split=None):
    out = {}
    bins = [("<=-2", P[col] <= -2), ("-1~1", (P[col] > -2) & (P[col] < 2)), ("2-3", (P[col] >= 2) & (P[col] < 4)),
            ("4-5", (P[col] >= 4) & (P[col] < 6)), (">=6", P[col] >= 6), (">=8", P[col] >= 8)]
    for nm, m in bins:
        sub = P[m]
        d = {"n": int(len(sub))}
        for hz in (5, 10):
            r = sub[f"r{hz}"].dropna()
            d[f"r{hz}_net_pct"] = None if len(r) == 0 else round(float(r.mean() - COST) * 100, 3)
            d[f"r{hz}_win"] = None if len(r) == 0 else round(float((r > COST).mean()), 4)
            x = sub[f"x{hz}"].dropna()
            d[f"x{hz}_pct"] = None if len(x) == 0 else round(float(x.mean()) * 100, 3)
        if split is not None:
            d["x10_IS"] = _mean_pct(sub[sub["date"] < split]["x10"])
            d["x10_OOS"] = _mean_pct(sub[sub["date"] >= split]["x10"])
        out[nm] = d
    return out


def _mean_pct(s):
    s = s.dropna()
    return None if len(s) == 0 else round(float(s.mean()) * 100, 3)


def daily_ic(P, score_col, ret_col="x10", min_n=30):
    """逐日 Spearman(score, 未來報酬)，回傳 (平均IC, t, 天數)。"""
    vals = []
    for d, g in P[["date", score_col, ret_col]].dropna().groupby("date"):
        if len(g) >= min_n and g[score_col].nunique() > 1:
            vals.append(g[score_col].rank().corr(g[ret_col].rank()))
    s = pd.Series(vals).dropna()
    if len(s) < 5:
        return {"ic": None, "t": None, "days": int(len(s))}
    return {"ic": round(float(s.mean()), 4), "t": round(_t(s, 10) or 0, 2), "days": int(len(s))}


# ------------------------------------------------------------------ 校準（走前嶺迴歸）
def ridge_fit(X, y, alpha=None):
    X = np.asarray(X, dtype=float)
    y = np.asarray(y, dtype=float)
    ok = np.isfinite(X).all(axis=1) & np.isfinite(y)
    X, y = X[ok], y[ok]
    if len(y) < 500:
        return None
    Xm, ym = X.mean(axis=0), y.mean()
    Xc, yc = X - Xm, y - ym
    a = alpha if alpha is not None else 0.05 * len(y)
    A = Xc.T @ Xc + a * np.eye(X.shape[1]) * np.mean(np.diag(Xc.T @ Xc) / max(len(y), 1))
    coef = np.linalg.solve(A, Xc.T @ yc)
    return coef


def coefs_to_weights(coef):
    """負係數→0（實盤倍率不允許負值）；其餘等比例縮放使最大權重＝1。回傳 {因子簡稱: 權重}。"""
    if coef is None:
        return {k: 1.0 for k in FACTORS}
    pos = np.maximum(coef, 0.0)
    mx = pos.max()
    if mx <= 0:
        return {k: 0.0 for k in FACTORS}
    return {k: round(float(p / mx), 3) for k, p in zip(FACTORS, pos)}


def weighted_score(P, weights):
    s = np.zeros(len(P), dtype=float)
    for k in FACTORS:
        s = s + P["f_" + k].values * weights.get(k, 1.0)
    s = pd.Series(s, index=P.index)
    return apply_overrides(s, P["market_bull"].values, P["dump"].values, P["overheated"].values,
                           P["reversal"].values, P["gate"].values)


def walk_forward_scores(P, first_test_year, target="x10", alpha=None):
    """逐年走前：用『該年以前』的資料擬合權重，套用到該年。回傳 (score Series[與 P 同 index], {年: weights})。"""
    score = pd.Series(np.nan, index=P.index)
    wmap = {}
    years = P["date"].dt.year
    for y in sorted(years.unique()):
        if y < first_test_year:
            continue
        train = P[(years < y) & P[target].notna()]
        if len(train) < 2000:
            continue
        # 訓練窗只用「報酬已實現」的列：訓練列日期 + 目標視窗 < 測試年起點（避免跨年洩漏）
        cutoff = pd.Timestamp(f"{y}-01-01") - pd.Timedelta(days=20)
        train = train[train["date"] < cutoff]
        coef = ridge_fit(train[["f_" + k for k in FACTORS]].values, train[target].values, alpha)
        w = coefs_to_weights(coef)
        wmap[int(y)] = w
        sel = years == y
        score.loc[sel] = weighted_score(P[sel], w).values
    return score, wmap


# ------------------------------------------------------------------ 選股與出場
def select(P, score_col, k=TOPK, min_score=None, mask=None, seed=11):
    """每日取 score 最高的 k 檔（同分用固定亂數排序）。回傳 index 列表。"""
    rng = np.random.default_rng(seed)
    Q = P[["date", score_col]].copy()
    Q["_r"] = rng.random(len(Q))
    ok = Q[score_col].notna()
    if min_score is not None:
        ok &= Q[score_col] >= min_score
    if mask is not None:
        ok &= mask
    Q = Q[ok].sort_values(["date", score_col, "_r"], ascending=[True, False, True])
    return Q.groupby("date").head(k).index


def random_pick(P, counts_by_date, seed=5):
    """基準：每天隨機挑與實際相同檔數。"""
    rng = np.random.default_rng(seed)
    idx = []
    for d, g in P.groupby("date"):
        n = counts_by_date.get(d, 0)
        if n > 0 and len(g) >= n:
            idx.extend(rng.choice(g.index.values, size=n, replace=False).tolist())
    return pd.Index(idx)


class Arrays:
    """每檔股票的 numpy 陣列，供出場模擬（避免反覆 DataFrame 取值）。"""
    def __init__(self, prices):
        self.d = {}
        for s, df in prices.items():
            c = df["Close"].astype(float)
            self.d[s] = {
                "idx": df.index, "o": df["Open"].values.astype(float), "h": df["High"].values.astype(float),
                "l": df["Low"].values.astype(float), "c": c.values,
                "ma5": c.rolling(5).mean().values, "ma10": c.rolling(10).mean().values, "ma20": c.rolling(20).mean().values,
                "atr": (pd.concat([df["High"] - df["Low"], (df["High"] - c.shift()).abs(), (df["Low"] - c.shift()).abs()], axis=1)
                        .max(axis=1).rolling(14).mean().values),
                "over": ((c > c.rolling(20).mean() + 3 * c.rolling(20).std())).values,
                "pos": {t: i for i, t in enumerate(df.index)},
            }


def exit_trade(A, sym, date, rule):
    """以『選股日 date 的隔天收盤』為進場價，依 rule 出場。回傳 (淨報酬, 持有日數, 出場原因) 或 None（資料不足）。
    rule：('live',) | ('hold', n) | ('tpsl', tp, sl, hold) | ('ma20', minhold, maxhold) | ('atr', mult, maxhold) | ('ma_conf', minhold, maxhold)"""
    a = A.d[sym]
    i = a["pos"].get(date)
    if i is None:
        return None
    e = i + 1
    n = len(a["c"])
    kind = rule[0]
    maxh = {"live": 20, "hold": rule[1] if kind == "hold" else 0, "tpsl": rule[3] if kind == "tpsl" else 0,
            "ma20": rule[2] if kind == "ma20" else 0, "atr": rule[2] if kind == "atr" else 0,
            "ma_conf": rule[2] if kind == "ma_conf" else 0}[kind]
    if e + maxh >= n:
        return None
    E = a["c"][e]
    if not (E > 0):
        return None
    dts = a["idx"]
    top_close = E
    below_run = 0
    for k in range(1, maxh + 1):
        j = e + k
        c, o, hi, lo = a["c"][j], a["o"][j], a["h"][j], a["l"][j]
        reason, px = None, None
        if kind == "live":
            if o >= E * 1.05:
                reason, px = "spike", o
            elif c < a["ma5"][j] or c < a["ma10"][j]:
                reason, px = "ma_break", c
            elif (dts[j] - dts[e]).days >= 3 and c < E:
                reason, px = "time_stop", c
            elif a["over"][j]:
                reason, px = "resistance", c
        elif kind == "hold":
            if k == rule[1]:
                reason, px = "hold", c
        elif kind == "tpsl":
            _, tp, sl, hold = rule
            if lo <= E * (1 - sl):
                reason, px = "sl", min(E * (1 - sl), o)
            elif hi >= E * (1 + tp):
                reason, px = "tp", max(E * (1 + tp), o)
            elif k == hold:
                reason, px = "hold", c
        elif kind == "ma20":
            if k >= rule[1] and c < a["ma20"][j]:
                reason, px = "ma20_break", c
        elif kind == "atr":
            top_close = max(top_close, a["c"][j - 1])
            at = a["atr"][j - 1]
            if k >= 2 and np.isfinite(at) and c < top_close - rule[1] * at:
                reason, px = "atr_trail", c
        elif kind == "ma_conf":
            below_run = below_run + 1 if c < a["ma10"][j] else 0
            if k >= rule[1] and below_run >= 2:
                reason, px = "ma_break_conf", c
        if reason:
            return px / E - 1 - COST, k, reason
        if k == maxh:
            return c / E - 1 - COST, k, "max_hold"
    return None


def eval_trades(A, P, idx, rule, split):
    """對 idx 列（P 的列）逐筆出場。回傳 dict：n、win、exp、PF、IS/OOS、逐年、出場原因分布。"""
    rets, dates, reasons, holds = [], [], [], []
    sub = P.loc[idx, ["date", "sym"]]
    for d, s in zip(sub["date"].values, sub["sym"].values):
        r = exit_trade(A, s, pd.Timestamp(d), rule)
        if r is None:
            continue
        rets.append(r[0]); dates.append(pd.Timestamp(d)); holds.append(r[1]); reasons.append(r[2])
    if not rets:
        return {"n": 0}
    R = np.array(rets)
    D = pd.DatetimeIndex(dates)
    out = {"n": int(len(R)), "win": round(float((R > 0).mean()), 4), "exp_pct": round(float(R.mean()) * 100, 3),
           "avg_hold": round(float(np.mean(holds)), 1)}
    pos, neg = R[R > 0].sum(), -R[R < 0].sum()
    out["profit_factor"] = None if neg == 0 else round(float(pos / neg), 3)
    for nm, sel in (("IS", D < split), ("OOS", D >= split)):
        x = R[sel]
        out[nm] = {"n": int(len(x)), "win": None if len(x) == 0 else round(float((x > 0).mean()), 4),
                   "exp_pct": None if len(x) == 0 else round(float(x.mean()) * 100, 3)}
    yr = {}
    for y in sorted(set(D.year)):
        x = R[D.year == y]
        if len(x) >= 15:
            yr[int(y)] = {"n": int(len(x)), "win": round(float((x > 0).mean()), 3), "exp_pct": round(float(x.mean()) * 100, 3)}
    out["years"] = yr
    rc = pd.Series(reasons).value_counts()
    out["reasons"] = {k: {"n": int(v), "exp_pct": round(float(R[np.array(reasons) == k].mean()) * 100, 2)} for k, v in rc.items()}
    return out


def block_bootstrap_diff(P, idx_a, idx_b, col="x10", reps=400, seed=3):
    """日期區塊自助法：A 組 vs B 組逐日平均超額報酬差的 95% 區間。回傳 (diff_pct, lo, hi)。"""
    a = P.loc[idx_a].groupby("date")[col].mean().dropna()
    b = P.loc[idx_b].groupby("date")[col].mean().dropna()
    ds = a.index.intersection(b.index)
    if len(ds) < 30:
        return None
    d = (a.loc[ds] - b.loc[ds]).values
    rng = np.random.default_rng(seed)
    bs = [d[rng.integers(0, len(d), len(d))].mean() for _ in range(reps)]
    return [round(float(d.mean()) * 100, 3), round(float(np.percentile(bs, 2.5)) * 100, 3), round(float(np.percentile(bs, 97.5)) * 100, 3)]


# ------------------------------------------------------------------ 主流程
EXIT_RULES = {
    "實盤現行(破5/10MA+時間停損+早盤+5%)": ("live",),
    "持有5日": ("hold", 5),
    "持有10日": ("hold", 10),
    "持有20日": ("hold", 20),
    "停利8%/停損8%/10日": ("tpsl", 0.08, 0.08, 10),
    "停利12%/停損10%/20日": ("tpsl", 0.12, 0.10, 20),
    "跌破MA20(滿2日後)": ("ma20", 2, 30),
    "ATR3倍移動停損": ("atr", 3.0, 30),
    "連2日收破MA10(滿3日後)": ("ma_conf", 3, 30),
}
GATES = {"無": None, "指數>MA20": "rg_up20", "市場平穩(no_stress)": "rg_no_stress", "寬度>=50%且指數>MA60": "rg_b50_up60"}


def antichase_mask(P):
    return (P["gain"] <= 4.0) & (P["dist20"] <= 0.10) & (~P["overheated"]) & (P["run5"] <= 0.12)


def run_backtest(prices, chips, revs, eval_years=5, reps=400):
    t0 = time.time()
    last = max(df.index[-1] for df in prices.values())
    eval_start = last - pd.DateOffset(years=eval_years)
    P, flags = build_panel(prices, chips, revs, eval_start)
    split = split_date(P)
    rep = {"n_symbols": int(P["sym"].nunique()), "n_rows": int(len(P)), "date_range": [str(P["date"].min())[:10], str(P["date"].max())[:10]],
           "split": str(split)[:10], "n_with_chip": int(sum(1 for s in prices if chips.get(s) is not None and len(chips.get(s)) > 0)),
           "n_with_rev": int(sum(1 for s in prices if revs.get(s)))}
    # 1) 分桶 + IC
    rep["buckets_old"] = score_buckets(P, "score_old", split)
    rep["ic_old"] = {"all": daily_ic(P, "score_old"), "IS": daily_ic(P[P["date"] < split], "score_old"),
                     "OOS": daily_ic(P[P["date"] >= split], "score_old")}
    # 2) 逐因子
    ff = factor_flags(P)
    rep["factor_lift_x10"] = {nm: lift_table(P, m, "x10", split, h=10) for nm, m in ff.items()}
    rep["factor_lift_x5"] = {nm: lift_table(P, m, "x5", split, h=5) for nm, m in ff.items()}
    # 3) 走前校準
    first_year = int(P["date"].dt.year.min()) + 2
    P["score_wf"], wmap = walk_forward_scores(P, first_year)
    rep["wf_weights_by_year"] = wmap
    coef_all = ridge_fit(P[P["x10"].notna()][["f_" + k for k in FACTORS]].values, P[P["x10"].notna()]["x10"].values)
    rep["weights_full_sample"] = coefs_to_weights(coef_all)
    rep["coef_full_sample_pct_per_point"] = None if coef_all is None else {k: round(float(c) * 100, 4) for k, c in zip(FACTORS, coef_all)}
    # 去追突破：只把兩個追突破因子倍率設 0（預先指定，不看結果挑）
    w_dechase = {k: (0.0 if k in ("comp", "consec") else 1.0) for k in FACTORS}
    P["score_dechase"] = weighted_score(P, w_dechase)
    wtest = P["score_wf"].notna()
    PT = P[wtest]                                                   # 走前可檢驗的期間
    rep["wf_period"] = [str(PT["date"].min())[:10], str(PT["date"].max())[:10]]
    rep["ic_wf"] = daily_ic(PT, "score_wf")
    rep["ic_old_same_period"] = daily_ic(PT, "score_old")
    rep["ic_dechase"] = daily_ic(PT, "score_dechase")
    # 4) 進場組合 × 出場
    A = Arrays(prices)
    sets = {}
    old_idx = select(PT, "score_old", TOPK, min_score=6)
    sets["S0 舊規則(評分>=6，每日前10)"] = old_idx
    cnt = PT.loc[old_idx].groupby("date").size().to_dict()
    sets["R0 同日隨機同檔數(基準)"] = random_pick(PT, cnt)
    sets["S1 去追突破因子後，同檔數"] = _matched(PT, "score_dechase", cnt)
    sets["S2 走前校準權重，同檔數"] = _matched(PT, "score_wf", cnt)
    sets["S3 走前校準，每日前10(分數>0)"] = select(PT, "score_wf", TOPK, min_score=0.01)
    ac = antichase_mask(PT)
    for gname, gcol in GATES.items():
        if gcol is None:
            continue
        sets[f"S4 走前校準前10＋閘門[{gname}]"] = select(PT, "score_wf", TOPK, min_score=0.01, mask=PT[gcol])
    sets["S5 走前校準前10＋防追高"] = select(PT, "score_wf", TOPK, min_score=0.01, mask=ac)
    for gname, gcol in GATES.items():
        if gcol is None:
            continue
        sets[f"S6 走前校準前10＋防追高＋閘門[{gname}]"] = select(PT, "score_wf", TOPK, min_score=0.01, mask=ac & PT[gcol])
    rep["sets"] = {}
    for nm, idx in sets.items():
        x = PT.loc[idx]
        item = {"n_picks": int(len(idx)), "days": int(x["date"].nunique()),
                "x10_IS": _mean_pct(x[x["date"] < split]["x10"]), "x10_OOS": _mean_pct(x[x["date"] >= split]["x10"]),
                "r10_net_pct": None if x["r10"].dropna().empty else round(float(x["r10"].dropna().mean() - COST) * 100, 3),
                "exits": {}}
        rules = EXIT_RULES if (nm.startswith(("S0", "R0", "S2", "S3")) or "閘門" in nm or "防追高" in nm) else {k: EXIT_RULES[k] for k in list(EXIT_RULES)[:3]}
        for en, rule in rules.items():
            item["exits"][en] = eval_trades(A, PT, idx, rule, split)
        rep["sets"][nm] = item
    # 5) 與舊規則/隨機的差距（區塊自助法）
    rep["bootstrap_x10_diff_vs_old"] = {}
    for nm in sets:
        if nm.startswith("S") and not nm.startswith("S0"):
            rep["bootstrap_x10_diff_vs_old"][nm] = block_bootstrap_diff(PT, sets[nm], old_idx, "x10", reps)
    rep["bootstrap_x10_diff_old_vs_random"] = block_bootstrap_diff(PT, old_idx, sets["R0 同日隨機同檔數(基準)"], "x10", reps)
    rep["elapsed_s"] = round(time.time() - t0)
    return rep


def _matched(P, score_col, counts_by_date, seed=11):
    """每日挑與舊規則同檔數、score 最高者（天數與檔數都對齊，只比『排序品質』）。"""
    rng = np.random.default_rng(seed)
    Q = P[["date", score_col]].copy()
    Q["_r"] = rng.random(len(Q))
    Q = Q[Q[score_col].notna()].sort_values(["date", score_col, "_r"], ascending=[True, False, True])
    Q["_rk"] = Q.groupby("date").cumcount()
    Q["_n"] = Q["date"].map(counts_by_date).fillna(0)
    return Q[Q["_rk"] < Q["_n"]].index


# ------------------------------------------------------------------ 資料取得
def fetch_chips(symbols, token, years=6, sleep=0.15, diag=None):
    """FinMind TaiwanStockInstitutionalInvestorsBuySell 逐檔抓 → {sym: DataFrame(index=日期, f_buy, t_buy)}（張）。token 可為逗號分隔多組。"""
    import requests
    diag = diag if diag is not None else {}
    tokens = [t.strip() for t in (token or "").split(",") if t.strip()] or [None]
    ti = 0
    start = (dt.date.today() - dt.timedelta(days=int(365.25 * years))).strftime("%Y-%m-%d")
    out = {}
    for i, s in enumerate(symbols):
        while True:
            try:
                params = {"dataset": "TaiwanStockInstitutionalInvestorsBuySell", "data_id": s, "start_date": start}
                if tokens[ti]:
                    params["token"] = tokens[ti]
                r = requests.get("https://api.finmindtrade.com/api/v4/data", params=params, timeout=40)
                js = {}
                try:
                    js = r.json()
                except ValueError:
                    pass
                msg = str(js.get("msg", ""))
                limited = r.status_code in (402, 429) or "limit" in msg.lower() or "level" in msg.lower()
                if limited and ti + 1 < len(tokens):
                    ti += 1
                    continue
                if r.status_code == 200 and js.get("data"):
                    d = pd.DataFrame(js["data"])
                    d["net"] = (pd.to_numeric(d["buy"], errors="coerce").fillna(0) - pd.to_numeric(d["sell"], errors="coerce").fillna(0)) / 1000.0
                    piv = d.pivot_table(index="date", columns="name", values="net", aggfunc="sum")
                    piv.index = pd.to_datetime(piv.index)
                    out[s] = pd.DataFrame({"f_buy": piv["Foreign_Investor"] if "Foreign_Investor" in piv else pd.Series(np.nan, index=piv.index),
                                           "t_buy": piv["Investment_Trust"] if "Investment_Trust" in piv else pd.Series(np.nan, index=piv.index)},
                                          index=piv.index)
                else:
                    key = f"HTTP{r.status_code}:{msg[:50]}"
                    diag[key] = diag.get(key, 0) + 1
                    if limited:
                        print(f"[籌碼] 額度問題，已取得 {len(out)} 檔，停止")
                        return out
                break
            except Exception as e:  # noqa: BLE001
                diag[type(e).__name__] = diag.get(type(e).__name__, 0) + 1
                break
        time.sleep(sleep)
        if (i + 1) % 100 == 0:
            print(f"[籌碼] {i + 1}/{len(symbols)}，成功 {len(out)}")
    return out


def synthetic(n=40, seed=3):
    """離線自測：價格隨機漫步、籌碼/營收隨機且與報酬無關 → 任何規則都不該有穩定正期望。"""
    prices = br.synthetic_prices(n=n, days=1700, seed=seed)
    rng = np.random.default_rng(seed)
    chips, revs = {}, {}
    for s, df in prices.items():
        chips[s] = pd.DataFrame({"f_buy": rng.normal(0, 1000, len(df)), "t_buy": rng.normal(0, 200, len(df))}, index=df.index)
        rows, base = [], 1e8
        for y in range(df.index[0].year, df.index[-1].year + 1):
            for m in range(1, 13):
                base *= float(np.exp(rng.normal(0.005, 0.12)))
                rows.append((y, m, base))
        revs[s] = rows
    return prices, chips, revs


def clean(o):
    """遞迴轉成可 JSON 序列化的純 Python 物件（NaN/inf → None，numpy 型別 → 原生型別，鍵一律字串）。"""
    if isinstance(o, dict):
        return {str(k): clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    if isinstance(o, (np.floating, float)):
        f = float(o)
        return f if math.isfinite(f) else None
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, (pd.Timestamp, dt.date, dt.datetime)):
        return str(o)[:19]
    return o


def public_summary(rep):
    if rep.get("error"):
        return "舊評分回測失敗：" + rep["error"]
    return (f"舊評分回測完成：{rep['n_symbols']} 檔／{rep['n_rows']} 列，籌碼 {rep['n_with_chip']} 檔、營收 {rep['n_with_rev']} 檔；"
            f"組合 {len(rep['sets'])} 組（細節存私有表）")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=int(os.environ.get("BT_N") or 300))
    ap.add_argument("--years", type=int, default=int(os.environ.get("BT_YEARS") or 6))
    ap.add_argument("--tag", default=os.environ.get("BT_TAG", ""))
    ap.add_argument("--synthetic", action="store_true")
    ap.add_argument("--no-upload", action="store_true")
    ap.add_argument("--json-out", default="")
    args = ap.parse_args()
    t0 = time.time()
    diag = {}
    if args.synthetic:
        prices, chips, revs = synthetic()
        eval_years = 4
    else:
        cache = os.environ.get("BT_CACHE", "")
        syms = br.load_universe(args.n)
        prices = br.download_prices(syms, args.years)
        tok = os.environ.get("FINMIND_TOKEN", "")
        chips, n1 = brv.cached_fetch("chip_rows", list(prices), lambda m: fetch_chips(m, tok, years=args.years, diag=diag), cache)
        revs, n2 = brv.cached_fetch("revenue_rows", list(prices),
                                    lambda m: brv.fetch_revenue(m, tok, start=(dt.date.today() - dt.timedelta(days=int(365.25 * (args.years + 1)))).strftime("%Y-%m-%d"), diag=diag), cache)
        print(f"[資料] 籌碼 {len(chips)}/{len(prices)}（新抓 {n1}）、營收 {len(revs)}/{len(prices)}（新抓 {n2}）")
        if len(chips) < 0.7 * len(prices) or len(revs) < 0.7 * len(prices):
            # 額度不足 → 不要用殘缺資料跑出一份誤導的結果；已抓到的已存快取，等額度重置後重跑即可
            print("籌碼/營收資料不足（FinMind 額度），本次不計算；已快取，稍後重跑會接續")
            try:
                from supabase import create_client
                sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
                sb.table("ui_selftest_reports").insert({"run_id": os.environ.get("GITHUB_RUN_ID", ""), "summary": "backtest_oldscore_waiting_quota",
                                                        "report": {"n_prices": len(prices), "n_chips": len(chips), "n_revs": len(revs), "diag": diag}}).execute()
            except Exception:  # noqa: BLE001
                pass
            return
        eval_years = 5
    try:
        rep = run_backtest(prices, chips, revs, eval_years=eval_years, reps=int(os.environ.get("BT_BOOT") or 400))
    except Exception as e:  # noqa: BLE001  公開日誌看不到細節 → 把完整錯誤寫進私有表
        import traceback
        print(f"回測失敗：{type(e).__name__}（細節存私有表）")
        if not (args.no_upload or args.synthetic):
            try:
                from supabase import create_client
                sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
                sb.table("ui_selftest_reports").insert({"run_id": os.environ.get("GITHUB_RUN_ID", ""), "summary": "backtest_oldscore_error",
                                                        "report": {"error": f"{type(e).__name__}: {e}", "trace": traceback.format_exc()[-6000:],
                                                                   "n_prices": len(prices), "n_chips": len(chips), "n_revs": len(revs)}}).execute()
            except Exception as e2:  # noqa: BLE001
                print(f"錯誤報告寫入失敗：{type(e2).__name__}")
        raise
    rep = clean(rep)
    rep["fetch_diag"] = diag
    rep["ts"] = dt.datetime.now(dt.timezone.utc).isoformat()
    rep["total_elapsed_s"] = round(time.time() - t0)
    print(public_summary(rep))
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False)
    if args.no_upload or args.synthetic:
        return
    from supabase import create_client
    sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
    sb.table("ui_selftest_reports").insert({"run_id": os.environ.get("GITHUB_RUN_ID", ""),
                                            "summary": "backtest_oldscore" + (f":{args.tag}" if args.tag else ""),
                                            "report": rep}).execute()
    print("✅ 已寫入 Supabase ui_selftest_reports")


if __name__ == "__main__":
    main()
