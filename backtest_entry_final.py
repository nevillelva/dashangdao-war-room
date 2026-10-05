#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
backtest_entry_final.py —— 進場訊號最終回測（2026-10-05，跑在 GitHub Actions，結果只寫進 Supabase 私有表）

【目的】把 10/4「勝率5成檢查」篩出的候選進場規則（回檔進場／穿山惡龍）加上「可實際執行的出場結構」
再驗證一次，並補上先前沒做的三件事：
  1. 停損網格加寬（8/10/12/15%/不設）+ 單筆最差虧損 / 5% 分位報酬 / 平均持有天數；
  2. 「同日訊號叢集」：同一天很多檔同時觸發，有效樣本比 n 小 → 以訊號日為單位再算一次勝率；
  3. 投組層級模擬：最多同時 K 檔、每日最多新進 M 檔、每檔等額，算出實際可得的勝率、期望、最大回撤、逐年報酬。
  另外輸出：市場寬度分桶（寬度<40%/40~60%/>60%）下的表現，決定要不要加「寬度≥50%」閘門。

【誠實聲明】母體只含「現在仍在市場上的近期成交值大檔」→ 存活者偏誤；樣本外段是多頭 → 偏樂觀；日K同日
停利/停損皆觸發一律視為先停損；進場=訊號隔日開盤；成本=來回0.585%（不計券商折讓）。

【輸出】ui_selftest_reports(summary='backtest_entry_final', report=JSON)；不寫公開分支、不上傳 artifact。
"""
import os
import sys
import json
import itertools
import time

import numpy as np
import pandas as pd

import backtest_rules as br
import backtest_winrate_tuning as bt
import backtest_winrate50 as w50

NO = 9.99
TP_GRID = (0.08, 0.10, 0.12, 0.15, NO)
SL_GRID = (0.08, 0.10, 0.12, 0.15, NO)
HOLD_GRID = (10, 20)
bt.TP_GRID, bt.SL_GRID, bt.HOLD_GRID, bt.MAX_HOLD = TP_GRID, SL_GRID, HOLD_GRID, max(HOLD_GRID)

P5 = "分數≥12 且3日回檔≥5%"
P3 = "分數≥12 且3日回檔≥3%"
P10 = "分數≥10 且3日回檔≥5%"
D = "穿山惡龍 MA60/前漲40%"
DB = "穿山惡龍 MA60/前漲40% 且寬度≥50%"
U = "回檔(≥5%)∪穿山惡龍"
BASE = w50.BASE
FAMS = [P5, P3, P10, D, DB, U]

PORT_CFGS = [  # (family, tp, sl, hold)
    (P5, 0.12, NO, 20), (P5, 0.12, 0.12, 20), (P5, 0.12, 0.15, 20), (P5, 0.10, 0.10, 20), (P5, NO, NO, 20),
    (D, 0.12, NO, 20), (D, 0.12, 0.12, 20), (D, 0.12, 0.15, 20), (D, 0.10, 0.10, 20), (D, NO, NO, 20),
    (DB, 0.12, 0.12, 20), (DB, 0.12, NO, 20),
    (U, 0.12, NO, 20), (U, 0.12, 0.12, 20), (U, 0.12, 0.15, 20), (U, 0.10, 0.10, 20), (U, 0.08, 0.12, 20),
]
K_SLOTS, MAX_NEW_PER_DAY = 10, 3


def sim_cfg(df, t_idx, tp, sl, hold):
    """與 bt.simulate_family 同一套成交規則，但多回傳『持有天數』與出場原因。"""
    o, h, l, c = (df[k].values for k in ("Open", "High", "Low", "Close"))
    e = t_idx + 1
    E = o[e]
    K = np.arange(hold)
    rows = e[:, None] + K[None, :]
    Hm, Lm, Om, Cm = (h[rows] / E[:, None] - 1, l[rows] / E[:, None] - 1,
                      o[rows] / E[:, None] - 1, c[rows] / E[:, None] - 1)
    tp_hit, sl_hit = Hm >= tp, Lm <= -sl
    any_tp, any_sl = tp_hit.any(axis=1), sl_hit.any(axis=1)
    f_tp = np.where(any_tp, tp_hit.argmax(axis=1), hold + 1)
    f_sl = np.where(any_sl, sl_hit.argmax(axis=1), hold + 1)
    ret = Cm[:, hold - 1].copy()
    use_sl = any_sl & (f_sl <= f_tp)
    use_tp = any_tp & ~use_sl
    k_sl, k_tp = np.clip(f_sl, 0, hold - 1), np.clip(f_tp, 0, hold - 1)
    ar = np.arange(len(e))
    ret = np.where(use_sl, np.minimum(-sl, np.where(f_sl > 0, Om[ar, k_sl], -sl)), ret)
    ret = np.where(use_tp, np.maximum(tp, np.where(f_tp > 0, Om[ar, k_tp], tp)), ret)
    days = np.where(use_sl, f_sl + 1, np.where(use_tp, f_tp + 1, hold))
    return ret - br.COST_ROUND_TRIP, days, e


def stats_ext(a):
    s = bt.stats(a)
    if s.get("n"):
        a = np.asarray(a, dtype=float)
        s["worst_pct"] = round(float(a.min()) * 100, 2)
        s["p5_pct"] = round(float(np.percentile(a, 5)) * 100, 2)
    return s


def exit_label(tp, sl, hold):
    return w50.exit_label(tp, sl, hold)


def portfolio(trades, split):
    """trades: DataFrame[entry_date, exit_date, ret, rank, symbol]。最多 K_SLOTS 檔、每日最多新進 MAX_NEW_PER_DAY，每檔 1/K 資金（不複利）。"""
    if trades.empty:
        return {}
    trades = trades.sort_values(["entry_date", "rank"]).reset_index(drop=True)
    open_pos, taken = [], []
    last_day, day_new = None, 0
    for r in trades.itertuples():
        open_pos = [x for x in open_pos if x > r.entry_date]
        if r.entry_date != last_day:
            last_day, day_new = r.entry_date, 0
        if len(open_pos) >= K_SLOTS or day_new >= MAX_NEW_PER_DAY:
            continue
        open_pos.append(r.exit_date)
        day_new += 1
        taken.append(r)
    if not taken:
        return {}
    t = pd.DataFrame(taken)
    out = {}
    for lab, sub in (("ALL", t), ("IS", t[t.entry_date < split]), ("OOS", t[t.entry_date >= split])):
        if sub.empty:
            continue
        eq = (sub.groupby("exit_date")["ret"].sum() / K_SLOTS).sort_index().cumsum()
        mdd = float((eq - eq.cummax()).min()) if len(eq) else 0.0
        yrs = max((sub.exit_date.max() - sub.entry_date.min()).days / 365.25, 0.25)
        s = stats_ext(sub["ret"].values)
        s.update({"total_ret_pct": round(float(eq.iloc[-1]) * 100, 2), "per_year_pct": round(float(eq.iloc[-1]) / yrs * 100, 2),
                  "mdd_pct": round(mdd * 100, 2), "years": round(yrs, 2),
                  "by_year_pct": {int(y): round(float(g["ret"].sum() / K_SLOTS) * 100, 2) for y, g in sub.groupby(sub.exit_date.dt.year)},
                  "by_year_win": {int(y): round(float((g["ret"] > 0).mean()) * 100, 1) for y, g in sub.groupby(sub.exit_date.dt.year)}})
        out[lab] = s
    return out


def main():
    t0 = time.time()
    n = int(os.environ.get("BT_N") or 300)
    years = int(os.environ.get("BT_YEARS") or 6)
    prices = br.download_prices(br.load_universe(n), years)
    if len(prices) < 20:
        print("❌ 有效股票太少")
        sys.exit(1)
    split = br.pick_split_date(prices)
    breadth = bt.market_breadth(prices)
    print(f"[資料] {len(prices)} 檔，樣本外切點 {split.date()}，耗時 {time.time()-t0:.0f}s")

    acc = {}                       # (fam,tp,sl,hold) -> list[(dates, rets)]
    sig_store = {f: {} for f in FAMS}   # fam -> sym -> t_idx
    breadth_ret = {}               # (fam,cfg,bucket) -> list rets
    for si, (sym, df) in enumerate(prices.items()):
        fam_idx = w50.families(df, breadth)
        fam_idx[U] = bt.apply_cooldown(np.array(sorted(set(fam_idx.get(P5, [])) | set(fam_idx.get(D, []))), dtype=int))
        for fam in [BASE] + FAMS:
            t_idx = fam_idx.get(fam)
            if t_idx is None or len(t_idx) == 0:
                continue
            t_idx = np.asarray(t_idx, dtype=int)
            t_idx = t_idx[(t_idx >= 0) & (t_idx + 1 + bt.MAX_HOLD < len(df))]
            if len(t_idx) == 0:
                continue
            if fam in sig_store:
                sig_store[fam][sym] = t_idx
            res, dates = bt.simulate_family(df, t_idx)
            for key, arr in res.items():
                acc.setdefault((fam,) + key, []).append((dates, arr))
        if (si + 1) % 50 == 0:
            print(f"  進度 {si + 1}/{len(prices)}  {time.time()-t0:.0f}s")

    # ---- 網格結果
    table = {}
    for key, parts in acc.items():
        dates = np.concatenate([p[0].values for p in parts])
        rets = np.concatenate([p[1] for p in parts])
        is_m = dates < np.datetime64(split)
        yrs = pd.DatetimeIndex(dates).year
        # 同日叢集：以訊號(進場)日為單位平均
        ser = pd.Series(rets, index=pd.DatetimeIndex(dates))
        cl = ser.groupby(level=0).mean()
        cl_oos = cl[cl.index >= split]
        table[key] = {"IS": stats_ext(rets[is_m]), "OOS": stats_ext(rets[~is_m]),
                      "by_year": {int(y): bt.stats(rets[yrs == y]) for y in sorted(set(yrs))},
                      "cluster_OOS": {"days": int(len(cl_oos)), "win_day": round(float((cl_oos > 0).mean()), 3) if len(cl_oos) else None,
                                      "mean_pct": round(float(cl_oos.mean()) * 100, 3) if len(cl_oos) else None}}
    rows = []
    for key, v in table.items():
        fam, tp, sl, hold = key
        if fam == BASE:
            continue
        b = table.get((BASE, tp, sl, hold))
        rows.append({"family": fam, "exit": exit_label(tp, sl, hold), "tp": tp, "sl": sl, "hold": hold, **v,
                     "base_OOS": b["OOS"] if b else None})

    # ---- 寬度分桶（只看 20 日、停利12%/停損12%）
    bucket = {}
    for fam in FAMS:
        for (tp, sl, hold) in ((0.12, 0.12, 20), (0.12, NO, 20)):
            vals = {"<40%": [], "40~60%": [], ">60%": []}
            for sym, t_idx in sig_store[fam].items():
                df = prices[sym]
                ret, _, e = sim_cfg(df, t_idx, tp, sl, hold)
                bv = breadth.reindex(df.index).values[t_idx]
                dts = df.index[e]
                m_oos = dts >= split
                for r_, b_, o_ in zip(ret, bv, m_oos):
                    k = "<40%" if b_ < 0.4 else ("40~60%" if b_ < 0.6 else ">60%")
                    vals[k].append(r_)
            bucket[f"{fam}|{exit_label(tp, sl, hold)}"] = {k: bt.stats(v) for k, v in vals.items()}

    # ---- 投組模擬
    ports = {}
    for fam, tp, sl, hold in PORT_CFGS:
        recs = []
        for sym, t_idx in sig_store[fam].items():
            df = prices[sym]
            ret, days, e = sim_cfg(df, t_idx, tp, sl, hold)
            s15 = br.official_score_series(df["Close"]).values
            for i in range(len(t_idx)):
                recs.append((df.index[e[i]], df.index[min(e[i] + int(days[i]) - 1, len(df) - 1)], float(ret[i]),
                             -float(s15[t_idx[i]]) if np.isfinite(s15[t_idx[i]]) else 0.0, sym))
        tr = pd.DataFrame(recs, columns=["entry_date", "exit_date", "ret", "rank", "symbol"])
        ports[f"{fam}|{exit_label(tp, sl, hold)}"] = portfolio(tr, split)

    report = {"kind": "backtest_entry_final", "ts": time.strftime("%Y-%m-%d %H:%M:%S"), "universe": len(prices),
              "split": str(split.date()), "cost": br.COST_ROUND_TRIP, "K_SLOTS": K_SLOTS, "MAX_NEW_PER_DAY": MAX_NEW_PER_DAY,
              "grid": rows, "breadth_buckets": bucket, "portfolio": ports}
    from supabase import create_client
    sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
    sb.table("ui_selftest_reports").insert({"run_id": os.environ.get("GITHUB_RUN_ID", ""), "summary": "backtest_entry_final",
                                            "report": report}).execute()
    print(f"✅ 已寫入 Supabase（grid {len(rows)} 列、portfolio {len(ports)} 組），總耗時 {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
