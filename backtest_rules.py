#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
backtest_rules.py —— 日K規則回測（R99新增，跑在 GitHub Actions，見 .github/workflows/backtest_rules.yml）

【為什麼存在】總指揮官要求：附件(A2~A8)只拿「規則」、不能把案例數字寫進程式，參數要靠回溯驗證決定。
本機環境連不到 Yahoo/TWSE，所以回測在 Actions 上跑；這支腳本不修改任何正式資料表，
只輸出 backtest_out/results.json 與 backtest_out/report.md（並寫進 GITHUB_STEP_SUMMARY）。

【測試的規則，全部只取自附件的「規則文字」，門檻/週期都是「作者預設值→當作網格的一個候選」，
  最後採用什麼參數由樣本外(OOS)結果決定】
  R1 均線分數：站上 MA5/10/20/60/120/240 各 +1（滿分6）；分數分桶的未來報酬；
     「常客榜」= 近20日有幾天在前段班、近5日有幾天在前段班（前段班門檻作者沒給，列入網格）。
  R2 穿山惡龍：先有一波漲幅 → 跌破均線(破惡) → 實體紅K站回均線(穿惡)；
     快速站回(≤N日)直接買、較晚站回則等站穩再買；跌破均線出場。（目標價公式附件沒給，不測）
  R3 底部型態：頭肩底、W底；站上頸線且帶量 → 進場；破左肩/破頭/破底 → 出場。

【回測方法（誠實版）】
  - 訊號日收盤後才知道，進場一律用「隔日開盤價」；扣手續費+證交稅(來回約0.585%)。
  - 資料切成前70%(樣本內，選參數)與後30%(樣本外，驗證)。報表兩段都列，結論以樣本外為準。
  - 日K用 yfinance auto_adjust 還原價；母體為近期成交值最大的N檔(存活者偏誤：只包含現在仍在市場的股票，
    會高估報酬，因此只用來「比較參數/規則之間的相對好壞」，絕對報酬數字不要直接當績效預期)。
  - 同一檔股票持有期間不重複進場；事件之間仍有相關性(同一波行情多檔同時觸發)，R1逐日取樣的報酬互相重疊，t值已除以sqrt(持有日數)粗略校正，仍僅供參考。
"""
import os
import re
import sys
import json
import math
import argparse
import itertools
import datetime as dt

import numpy as np
import pandas as pd

COST_ROUND_TRIP = 0.001425 * 2 + 0.003   # 手續費買賣各0.1425% + 賣出證交稅0.3%（不計券商折讓，偏保守）
OOS_FRACTION = 0.30
MIN_N = 30   # 樣本數低於此值的參數組合不參與「最佳」排名


# ------------------------------------------------------------------ 資料
def load_universe(n):
    """母體：優先環境變數UNIVERSE；否則用 Supabase twse_market_snapshot 近期平均成交值前N檔（4位數代號）。"""
    env_u = os.environ.get("UNIVERSE", "").strip()
    if env_u:
        return [s.strip() for s in env_u.split(",") if s.strip()]
    from supabase import create_client
    sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
    # PostgREST 單次最多回1000列，必須分頁；只看「有成交值」的日期（假日/未收盤日 trading_value 為空）。
    dates, offset = [], 0
    while len(dates) < 10 and offset < 20000:
        page = (sb.table("twse_market_snapshot").select("trade_date")
                .gt("trading_value", 0).order("trade_date", desc=True)
                .range(offset, offset + 999).execute().data) or []
        for r in page:
            if r["trade_date"] not in dates:
                dates.append(r["trade_date"])
        if len(page) < 1000:
            break
        offset += 1000
    dates = dates[:10]
    print(f"[母體] 使用成交值日期：{dates}")
    agg = {}
    for d in dates:
        got, offset = [], 0
        while True:
            page = (sb.table("twse_market_snapshot").select("symbol,trading_value")
                    .eq("trade_date", d).range(offset, offset + 999).execute().data) or []
            got += page
            if len(page) < 1000:
                break
            offset += 1000
        for r in got:
            sym = str(r.get("symbol") or "").strip()
            tv = r.get("trading_value")
            if re.fullmatch(r"[1-9]\d{3}", sym) and tv:
                agg.setdefault(sym, []).append(float(tv))
    ranked = sorted(agg.items(), key=lambda kv: -np.mean(kv[1]))
    return [s for s, _ in ranked[:n]]


def download_prices(symbols, years):
    import yfinance as yf
    out = {}
    for suffix in (".TW", ".TWO"):
        todo = [s for s in symbols if s not in out]
        for i in range(0, len(todo), 40):
            chunk = todo[i:i + 40]
            try:
                df = yf.download([s + suffix for s in chunk], period=f"{years}y", auto_adjust=True,
                                 group_by="ticker", threads=True, progress=False)
            except Exception as e:
                print(f"[download] {suffix} chunk失敗：{type(e).__name__}: {e}")
                continue
            if not isinstance(df.columns, pd.MultiIndex):
                continue
            for s in chunk:
                try:
                    d = df[s + suffix].dropna(subset=["Close"])
                except KeyError:
                    continue
                if len(d) >= 300:
                    out[s] = d[["Open", "High", "Low", "Close", "Volume"]].astype(float)
    print(f"[download] 取得 {len(out)}/{len(symbols)} 檔")
    return out


def synthetic_prices(n=40, days=1200, seed=7):
    """離線自測用：幾何隨機漫步（沒有任何可預測性，用來確認程式不會崩潰、且大致不會憑空產生超額報酬）。"""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2021-01-01", periods=days)
    out = {}
    for k in range(n):
        gap = rng.normal(0, 0.006, days)           # 隔夜跳空
        intra = rng.normal(0.0003, 0.018, days)    # 盤中（開→收）
        c = np.empty(days); o = np.empty(days)
        prev = 50.0
        for d in range(days):
            o[d] = prev * (1 + gap[d])
            c[d] = o[d] * np.exp(intra[d])
            prev = c[d]
        h = np.maximum(o, c) * (1 + np.abs(rng.normal(0, 0.006, days)))
        l = np.minimum(o, c) * (1 - np.abs(rng.normal(0, 0.006, days)))
        v = rng.lognormal(10, 0.4, days)
        out[f"S{k:04d}"] = pd.DataFrame({"Open": o, "High": h, "Low": l, "Close": c, "Volume": v}, index=idx)
    return out


# ------------------------------------------------------------------ 共用統計
def summarize(rets, overlap=1):
    """rets: 每筆交易/每個訊號的淨報酬(小數)。overlap：相鄰訊號的持有期重疊天數(例如10日報酬逐日取樣
    → 約10)，t值除以sqrt(overlap)做粗略校正；交易型(持有期不重疊)用1。"""
    a = np.asarray([x for x in rets if x is not None and np.isfinite(x)], dtype=float)
    n = len(a)
    if n == 0:
        return {"n": 0}
    mean = float(a.mean())
    sd = float(a.std(ddof=1)) if n > 1 else float("nan")
    t = mean / (sd / math.sqrt(n)) if n > 1 and sd and sd > 0 else float("nan")
    if overlap > 1 and not math.isnan(t):
        t = t / math.sqrt(overlap)
    return {"n": n, "mean_pct": round(mean * 100, 3), "median_pct": round(float(np.median(a)) * 100, 3),
            "win_pct": round(float((a > 0).mean()) * 100, 1), "t": None if math.isnan(t) else round(t, 2)}


def split_by_date(events, split_date):
    """events: list of (date, ret)。回傳 (IS rets, OOS rets)。"""
    ins = [r for d, r in events if d < split_date]
    oos = [r for d, r in events if d >= split_date]
    return ins, oos


def pick_split_date(prices):
    allidx = sorted({d for df in prices.values() for d in df.index})
    return allidx[int(len(allidx) * (1 - OOS_FRACTION))]


# ------------------------------------------------------------------ R1 均線分數 / 常客榜
MA_PERIODS = (5, 10, 20, 60, 120, 240)


def ma_score_series(close):
    score = pd.Series(0, index=close.index)
    for p in MA_PERIODS:
        score = score + (close > close.rolling(p).mean()).astype(int)
    score[close.rolling(max(MA_PERIODS)).mean().isna()] = np.nan   # 不足240日不評分
    return score


NEWHIGH_WINDOWS = (5, 10, 20, 60, 120, 360)


def official_score_series(close):
    """
    「官網式」均線分數（滿分15）。定義取自公開資料（thunghan1228-maker/HanStock PR#207 對照某官網排行驗證，
    整數一致率約81%、差1分內約91%，差異來自還原價）：
      ① 收盤站上 MA5/10/20/60/120/240，各 +1（最多6）
      ② 近 5/10/20/60/120/360 日的最高收盤價落在「最近3個交易日內」，各 +1（最多6）
      ③ 多頭排列：MA20>MA60、MA60>MA120、MA120>MA240，各 +1（最多3）
    """
    score = ma_score_series(close).copy()
    valid = score.notna()
    for w in NEWHIGH_WINDOWS:
        roll_max = close.rolling(w).max()
        recent_max = close.rolling(3).max()
        score = score + ((recent_max >= roll_max) & roll_max.notna()).astype(int)
    ma = {p_: close.rolling(p_).mean() for p_ in (20, 60, 120, 240)}
    score = score + (ma[20] > ma[60]).astype(int) + (ma[60] > ma[120]).astype(int) + (ma[120] > ma[240]).astype(int)
    score[~valid] = np.nan
    score[close.rolling(max(NEWHIGH_WINDOWS)).max().isna()] = np.nan
    return score


def run_r1(prices, split_date, horizons=(5, 10, 20), scorefn=None, max_score=6, tiers=(4, 5, 6)):
    scorefn = scorefn or ma_score_series
    scores, fwd = {}, {h: {} for h in horizons}
    for s, df in prices.items():
        sc = scorefn(df["Close"])
        scores[s] = sc
        for h in horizons:
            # 訊號日收盤後才知道 → 隔日開盤進、第 t+h 日收盤出
            fwd[h][s] = df["Close"].shift(-h) / df["Open"].shift(-1) - 1 - COST_ROUND_TRIP
    S = pd.DataFrame(scores)
    out = {"score_buckets": {}, "regulars": []}
    for h in horizons:
        F = pd.DataFrame(fwd[h])
        F_ex = F.sub(F.mean(axis=1), axis=0)          # 當日橫斷面超額報酬（扣掉市場平均）
        rows = {}
        for sc_val in range(0, max_score + 1):
            mask = (S == sc_val) & F.notna()
            stacked = F_ex.where(mask).stack()
            raw = F.where(mask).stack()
            ev = [(i[0], v) for i, v in stacked.items()]
            ins, oos = split_by_date(ev, split_date)
            raw_ev = [(i[0], v) for i, v in raw.items()]
            rins, roos = split_by_date(raw_ev, split_date)
            rows[str(sc_val)] = {"excess_IS": summarize(ins, h), "excess_OOS": summarize(oos, h),
                                 "raw_OOS": summarize(roos, h)}
        out["score_buckets"][f"{h}d"] = rows

    # 常客榜：前段班=score>=T；20日內上榜天數>=A 且 近5日上榜天數>=B
    h = 10
    F = pd.DataFrame(fwd[h])
    F_ex = F.sub(F.mean(axis=1), axis=0)
    for T, A, B in itertools.product(tiers, (5, 10, 15), (0, 1, 3, 5)):
        top = (S >= T).astype(float).where(S.notna())
        c20 = top.rolling(20).sum()
        c5 = top.rolling(5).sum()
        if B == 0:
            sig = (c20 >= A) & (c5 == 0)      # 驗證「最近5天0/5=已掉榜」這個說法
            label = "掉榜(5日0次)"
        else:
            sig = (c20 >= A) & (c5 >= B)
            label = "在榜"
        stacked = F_ex.where(sig & F_ex.notna()).stack()
        ev = [(i[0], v) for i, v in stacked.items()]
        ins, oos = split_by_date(ev, split_date)
        out["regulars"].append({"top_score_T": T, "appear20_ge": A, "last5_ge": B, "kind": label,
                                "excess10d_IS": summarize(ins, 10), "excess10d_OOS": summarize(oos, 10)})
    return out


# ------------------------------------------------------------------ 交易模擬共用
def simulate_exit(df, entry_i, stop_fn, max_hold, target_price=None):
    """
    從 entry_i（進場日，用當日開盤價進）起逐日檢查。
    stop_fn(i) -> True 代表 i 日收盤觸發出場（隔日開盤出）。target_price 以盤中最高價觸及即以目標價出。
    回傳 (淨報酬, 出場index) 。
    """
    o, h, c = df["Open"].values, df["High"].values, df["Close"].values
    n = len(df)
    entry = o[entry_i]
    if not np.isfinite(entry) or entry <= 0:
        return None, entry_i
    last = min(entry_i + max_hold, n - 1)
    for i in range(entry_i, last + 1):
        if target_price is not None and i > entry_i and h[i] >= target_price:
            return target_price / entry - 1 - COST_ROUND_TRIP, i
        if stop_fn(i):
            ex_i = min(i + 1, n - 1)
            return o[ex_i] / entry - 1 - COST_ROUND_TRIP, ex_i
    return c[last] / entry - 1 - COST_ROUND_TRIP, last


# ------------------------------------------------------------------ R2 穿山惡龍
def find_chuan_e_events(df, ma_n, rally_min, body_min, fast_days, slow_wait, rally_lookback=60,
                        slow_max=40, max_hold=60, k_target=0.0):
    c, o = df["Close"].values, df["Open"].values
    ma = df["Close"].rolling(ma_n).mean().values
    n = len(df)
    dates = df.index
    trades = []
    i = max(ma_n, rally_lookback) + 2
    blocked_until = -1
    while i < n - 3:
        # 破惡：由上而下跌破均線
        if i > blocked_until and np.isfinite(ma[i]) and c[i] < ma[i] and c[i - 1] >= ma[i - 1]:
            lo = max(0, i - rally_lookback)
            peak = lo + int(np.argmax(c[lo:i]))
            tr_lo = max(0, peak - rally_lookback)
            trough = float(np.min(c[tr_lo:peak + 1]))
            rally = c[peak] / trough - 1 if trough > 0 else 0
            if rally >= rally_min:
                # 找穿惡：之後第一次收盤站回均線且為實體紅K≥body_min
                j_end = min(n - 2, i + slow_max)
                u = None
                for j in range(i + 1, j_end + 1):
                    if np.isfinite(ma[j]) and c[j] > ma[j] and c[j - 1] <= ma[j - 1]:
                        if c[j] > o[j] and (c[j] - o[j]) / o[j] >= body_min:
                            u = j
                        break          # 第一次站回就判定（不符合實體條件就放棄這次）
                if u is not None:
                    gap = u - i
                    entry_i = None
                    if gap <= fast_days:
                        entry_i = u + 1
                        kind = "fast"
                    else:
                        k = u + slow_wait
                        if k < n - 1 and all(c[m] > ma[m] for m in range(u, k + 1)):
                            entry_i = k + 1
                            kind = "slow"
                    if entry_i is not None and entry_i < n - 1:
                        # 第二波目標價（附件沒給公式 → 把常見的「漲幅滿足」當候選，用回測決定）：
                        # 目標 = 穿惡低點(破到穿之間的最低價) + k × 第一波漲幅(起漲低→起漲高)
                        target = None
                        if k_target > 0:
                            dip_low = float(df["Low"].values[i:u + 1].min())
                            tgt = dip_low + k_target * (c[peak] - trough)
                            if tgt > o[entry_i] * 1.01:
                                target = tgt
                        ret, ex_i = simulate_exit(df, entry_i, lambda x: c[x] < ma[x], max_hold,
                                                  target_price=target)
                        if ret is not None:
                            trades.append((dates[entry_i], ret, kind))
                            blocked_until = ex_i
                            i = ex_i
        i += 1
    return trades


def run_r2(prices, split_date):
    grid = list(itertools.product((20, 60), (0.2, 0.3, 0.4), (0.02, 0.03, 0.05), (3, 5), (5, 10), (0.0, 0.618, 1.0)))
    results = []
    for ma_n, rally, body, fast_days, slow_wait, k in grid:
        ev_fast, ev_slow = [], []
        for s, df in prices.items():
            for d, r, kind in find_chuan_e_events(df, ma_n, rally, body, fast_days, slow_wait, k_target=k):
                (ev_fast if kind == "fast" else ev_slow).append((d, r))
        for kind, ev in (("fast", ev_fast), ("slow", ev_slow)):
            ins, oos = split_by_date(ev, split_date)
            results.append({"ma": ma_n, "rally_min": rally, "body_min": body, "fast_days": fast_days,
                            "slow_wait": slow_wait, "k_target": k, "kind": kind,
                            "IS": summarize(ins), "OOS": summarize(oos)})
    return results


# ------------------------------------------------------------------ R3 底部型態
def zigzag_pivots(df, w):
    """回傳 [(idx, 'H'/'L', price, confirm_idx)]，高低點交錯；pivot需要右側w根確認，confirm_idx=idx+w（避免偷看未來）。"""
    hi, lo = df["High"].values, df["Low"].values
    n = len(df)
    raw = []
    for i in range(w, n - w):
        if hi[i] >= hi[i - w:i + w + 1].max():
            raw.append((i, "H", hi[i], i + w))
        if lo[i] <= lo[i - w:i + w + 1].min():
            raw.append((i, "L", lo[i], i + w))
    raw.sort(key=lambda x: (x[0], x[1]))
    piv = []
    for p in raw:
        if piv and piv[-1][1] == p[1]:
            if (p[1] == "H" and p[2] > piv[-1][2]) or (p[1] == "L" and p[2] < piv[-1][2]):
                piv[-1] = p
        else:
            piv.append(p)
    return piv


def find_bottom_events(df, w, kind, vol_mult, tol, stop_mode, max_span=100, wait_max=40, max_hold=40,
                       target_mult=1.0):
    c, v = df["Close"].values, df["Volume"].values
    n = len(df)
    dates = df.index
    piv = zigzag_pivots(df, w)
    trades = []
    blocked_until = -1
    seen_entry = set()
    if kind == "hs":
        need = 5
    else:
        need = 3
    for k in range(len(piv) - need + 1):
        seq = piv[k:k + need]
        if kind == "hs":
            if [p[1] for p in seq] != ["L", "H", "L", "H", "L"]:
                continue
            L1, H1, L2, H2, L3 = seq
            if not (L2[2] < L1[2] and L2[2] < L3[2]):
                continue                      # 頭要最低，右肩高於頭
            if L3[0] - L1[0] > max_span:
                continue
            first_t = L3[3]                   # 右肩確認後才可能判斷
            stop_level = L1[2] if stop_mode == "left_shoulder" else L2[2]
            head = L2[2]

            def neck(t, H1=H1, H2=H2):
                return H1[2] + (H2[2] - H1[2]) * (t - H1[0]) / max(1, (H2[0] - H1[0]))
        else:
            if [p[1] for p in seq] != ["L", "H", "L"]:
                continue
            L1, H1, L2 = seq
            if L2[2] < L1[2] * (1 - tol):
                continue                      # 第二腳不可明顯破第一腳
            if L2[0] - L1[0] > max_span:
                continue
            first_t = L2[3]
            stop_level = min(L1[2], L2[2])
            head = min(L1[2], L2[2])

            def neck(t, H1=H1):
                return H1[2]
        # 找第一個「帶量站上頸線」的日子
        for t in range(max(first_t, 21), min(n - 2, first_t + wait_max) + 1):
            if t <= blocked_until:
                continue
            if c[t] < stop_level:
                break                         # 型態先失敗
            nl = neck(t)
            if c[t] > nl and c[t - 1] <= neck(t - 1):
                avg_v = v[t - 20:t].mean()
                if avg_v > 0 and v[t] >= vol_mult * avg_v:
                    entry_i = t + 1
                    key = (entry_i,)
                    if key in seen_entry:
                        break
                    seen_entry.add(key)
                    target = c[t] + (nl - head) * target_mult
                    ret, ex_i = simulate_exit(df, entry_i, lambda x: c[x] < stop_level, max_hold,
                                              target_price=target)
                    if ret is not None:
                        trades.append((dates[entry_i], ret))
                        blocked_until = ex_i
                break
    return trades


def run_r3(prices, split_date):
    results = []
    grid_hs = list(itertools.product((3, 5, 8), (1.0, 1.5, 2.0), ("left_shoulder", "head")))
    for w, vm, stop_mode in grid_hs:
        ev = []
        for s, df in prices.items():
            ev += find_bottom_events(df, w, "hs", vm, 0.0, stop_mode)
        ins, oos = split_by_date(ev, split_date)
        results.append({"pattern": "頭肩底", "pivot_w": w, "vol_mult": vm, "tol": None,
                        "stop": stop_mode, "IS": summarize(ins), "OOS": summarize(oos)})
    grid_w = list(itertools.product((3, 5, 8), (1.0, 1.5, 2.0), (0.0, 0.03, 0.05)))
    for w, vm, tol in grid_w:
        ev = []
        for s, df in prices.items():
            ev += find_bottom_events(df, w, "w", vm, tol, "any")
        ins, oos = split_by_date(ev, split_date)
        results.append({"pattern": "W底", "pivot_w": w, "vol_mult": vm, "tol": tol,
                        "stop": "破底", "IS": summarize(ins), "OOS": summarize(oos)})
    return results


# ------------------------------------------------------------------ 報表
def baseline(prices, split_date, h=20):
    ev = []
    for s, df in prices.items():
        r = (df["Close"].shift(-h) / df["Open"].shift(-1) - 1 - COST_ROUND_TRIP).dropna()
        ev += [(d, x) for d, x in r.items()]
    ins, oos = split_by_date(ev, split_date)
    return {"horizon_days": h, "IS": summarize(ins, h), "OOS": summarize(oos, h)}


def fmt(s):
    if not s or s.get("n", 0) == 0:
        return "n=0"
    return f"n={s['n']} 均{s['mean_pct']}% 中位{s['median_pct']}% 勝{s['win_pct']}% t={s['t']}"


def best_rows(rows, key_fields, top=5):
    ok = [r for r in rows if r["IS"].get("n", 0) >= MIN_N]
    ok.sort(key=lambda r: -r["IS"]["mean_pct"])
    return ok[:top]


def build_report(res):
    L = [f"# 日K規則回測報告（{res['meta']['generated']}）", "",
         f"- 母體：{res['meta']['n_symbols']} 檔；資料 {res['meta']['years']} 年；樣本外切點 {res['meta']['split_date']}",
         f"- 交易成本：來回 {COST_ROUND_TRIP*100:.3f}%；進場=訊號隔日開盤",
         f"- 基準（不做任何篩選、隨機持有20日）：樣本內 {fmt(res['baseline']['IS'])}；樣本外 {fmt(res['baseline']['OOS'])}",
         "- ⚠️ 存活者偏誤：只含「現在還在市場」的股票，絕對報酬偏高；請只比較規則/參數之間的相對好壞，並以樣本外為準。", ""]
    r1 = res["r1"]
    L += ["## R1 均線分數（站上幾條 MA，滿分6）— 10日未來報酬（超額=扣當日全體平均）", "",
          "| 分數 | 樣本內超額 | 樣本外超額 | 樣本外原始 |", "|---|---|---|---|"]
    for k, v in r1["score_buckets"]["10d"].items():
        L.append(f"| {k} | {fmt(v['excess_IS'])} | {fmt(v['excess_OOS'])} | {fmt(v['raw_OOS'])} |")
    L += ["", "（5日/20日明細見 results.json）", "", "## R1 常客榜（近20日在榜天數 × 近5日在榜天數）— 10日超額",
          "", "樣本內最佳5組與其樣本外表現：", "", "| 前段班T | 20日≥ | 5日≥ | 型態 | 樣本內 | 樣本外 |", "|---|---|---|---|---|---|"]
    rg = [r for r in r1["regulars"] if r["excess10d_IS"].get("n", 0) >= MIN_N]
    rg.sort(key=lambda r: -r["excess10d_IS"]["mean_pct"])
    for r in rg[:5]:
        L.append(f"| {r['top_score_T']} | {r['appear20_ge']} | {r['last5_ge']} | {r['kind']} | "
                 f"{fmt(r['excess10d_IS'])} | {fmt(r['excess10d_OOS'])} |")
    L += ["", "「掉榜(5日0次)」組（驗證附件說法：最近5天0/5不算強）：", "",
          "| 前段班T | 20日≥ | 樣本內 | 樣本外 |", "|---|---|---|---|"]
    for r in r1["regulars"]:
        if r["kind"].startswith("掉榜") and r["excess10d_IS"].get("n", 0) >= MIN_N:
            L.append(f"| {r['top_score_T']} | {r['appear20_ge']} | {fmt(r['excess10d_IS'])} | {fmt(r['excess10d_OOS'])} |")
    r15 = res.get("r1_15")
    if r15:
        L += ["", "## R1b 官網式均線分數（滿分15＝6均線＋6天期創新高＋3多頭排列）— 10日超額", "",
              "| 分數 | 樣本內超額 | 樣本外超額 | 樣本外原始 |", "|---|---|---|---|"]
        for k, v in r15["score_buckets"]["10d"].items():
            L.append(f"| {k} | {fmt(v['excess_IS'])} | {fmt(v['excess_OOS'])} | {fmt(v['raw_OOS'])} |")
        rg15 = [r for r in r15["regulars"] if r["excess10d_IS"].get("n", 0) >= MIN_N]
        rg15.sort(key=lambda r: -r["excess10d_IS"]["mean_pct"])
        L += ["", "官網式常客榜（前段班T=總分門檻）樣本內最佳5組與其樣本外：", "",
              "| T | 20日≥ | 5日≥ | 型態 | 樣本內 | 樣本外 |", "|---|---|---|---|---|---|"]
        for r in rg15[:5]:
            L.append(f"| {r['top_score_T']} | {r['appear20_ge']} | {r['last5_ge']} | {r['kind']} | "
                     f"{fmt(r['excess10d_IS'])} | {fmt(r['excess10d_OOS'])} |")
    L += ["", "## R2 穿山惡龍（破均線→實體紅K站回）", "",
          "樣本內平均報酬最佳5組（n≥%d）與其樣本外表現：" % MIN_N, "",
          "| MA | 前漲≥ | 實體≥ | 快速日數 | 慢速等待 | 目標k | 類型 | 樣本內 | 樣本外 |", "|---|---|---|---|---|---|---|---|---|"]
    for r in best_rows(res["r2"], None):
        L.append(f"| {r['ma']} | {r['rally_min']:.0%} | {r['body_min']:.0%} | {r['fast_days']} | {r['slow_wait']} | "
                 f"{r['k_target']} | {r['kind']} | {fmt(r['IS'])} | {fmt(r['OOS'])} |")
    L += ["", "（目標k=0 代表不設目標價、只靠跌破均線出場；k>0 為「穿惡低＋k×第一波漲幅」的停利價。）", "", "## R3 底部型態（頭肩底 / W底，帶量站上頸線）", "",
          "各型態樣本內最佳5組與其樣本外表現：", ""]
    for pat in ("頭肩底", "W底"):
        L += [f"**{pat}**", "", "| 轉折視窗 | 量比≥ | 容許 | 停損 | 樣本內 | 樣本外 |", "|---|---|---|---|---|---|"]
        for r in best_rows([x for x in res["r3"] if x["pattern"] == pat], None):
            L.append(f"| {r['pivot_w']} | {r['vol_mult']} | {r['tol']} | {r['stop']} | {fmt(r['IS'])} | {fmt(r['OOS'])} |")
        L.append("")
    L += ["## 怎麼讀這份報告", "",
          "1. 規則有沒有用：看樣本外是否仍優於基準（且 n 夠大、t 值 > 2 才算有訊號）；樣本內好、樣本外差 = 過度配適。",
          "2. 參數怎麼選：挑「樣本內前幾名在樣本外也沒崩」的參數區間，不要只挑單一最高點。",
          "3. 這份報告不會自動改任何正式設定；採用哪組參數由人確認後再寫進系統。", ""]
    return "\n".join(L)


# ------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=int(os.environ.get("BT_N") or "300"), help="母體檔數")
    ap.add_argument("--years", type=int, default=int(os.environ.get("BT_YEARS") or "6"))
    ap.add_argument("--only", default=os.environ.get("BT_ONLY") or "r1,r2,r3", help="r1,r2,r3 子集")
    ap.add_argument("--synthetic", action="store_true", help="離線自測，用隨機漫步資料")
    ap.add_argument("--out", default="backtest_out")
    a = ap.parse_args()

    if a.synthetic:
        prices = synthetic_prices()
    else:
        syms = load_universe(a.n)
        print(f"[母體] {len(syms)} 檔")
        prices = download_prices(syms, a.years)
    if len(prices) < 5:
        print("❌ 有效股票太少，停止。")
        sys.exit(1)
    split_date = pick_split_date(prices)
    only = {x.strip() for x in a.only.split(",")}
    res = {"meta": {"generated": dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
                    "n_symbols": len(prices), "years": a.years, "split_date": str(split_date.date()),
                    "synthetic": bool(a.synthetic)},
           "baseline": baseline(prices, split_date), "r1": {"score_buckets": {"10d": {}}, "regulars": []},
           "r1_15": None, "r2": [], "r3": []}
    if "r1" in only:
        print("[R1] 均線分數…")
        res["r1"] = run_r1(prices, split_date)
        print("[R1b] 官網式15分…")
        res["r1_15"] = run_r1(prices, split_date, horizons=(10,), scorefn=official_score_series,
                              max_score=15, tiers=(6, 9, 12))
    if "r2" in only:
        print("[R2] 穿山惡龍…")
        res["r2"] = run_r2(prices, split_date)
    if "r3" in only:
        print("[R3] 底部型態…")
        res["r3"] = run_r3(prices, split_date)
    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, "results.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1, default=str)
    rep = build_report(res)
    with open(os.path.join(a.out, "report.md"), "w", encoding="utf-8") as f:
        f.write(rep)
    print(rep)
    summ = os.environ.get("GITHUB_STEP_SUMMARY")
    if summ:
        with open(summ, "a", encoding="utf-8") as f:
            f.write(rep)


if __name__ == "__main__":
    main()
