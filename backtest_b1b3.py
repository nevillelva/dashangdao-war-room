#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
backtest_b1b3.py —— 2026-10-11 老闆接受預設規格：B1 混合出場、B2 個股 MA60 斜率濾網、B3 日線 MACD 因子。
（B4 盤中 MACD 無歷史資料，只做前瞻影子紀錄，不在此回測。）

【規格（預設）】
 B1 出場：現行（停利12%/停損10%/20日）vs ATR（停利2×ATR14、停損1.5×ATR14、時間停損10日，先到先出）
          vs MA20（停損10%＋收盤連2日跌破MA20出場＋最長40日）。皆扣來回成本，同日同時觸發視為先停損，跳空依開盤價。
 B2 濾網：進場訊號日 MA60 近5日斜率>0 且收盤>MA60  vs 不設限。
 B3 濾網：日線 MACD(12,26,9) 柱體由負翻正（訊號日柱>0、前一日柱≤0）或3日內金叉  vs 不設限。
 進場家族：隨機基準（流動性合格）、官網式分數≥12、爆量回檔（bt_strategy.pullback_burst_mask）。進場＝訊號隔日開盤，冷卻20日。
 比較指標：勝率、平均報酬(期望值)、最大回撤（依進場日排序的逐筆累積損益）、樣本內(前70%)/樣本外(後30%)、對同出場基準的 lift。
【誠實聲明】存活者偏誤、OOS 偏多頭；通過不代表可上實盤；結果只存私有表 ui_selftest_reports，公開日誌只印數量。
"""
import os
import sys
import json
import time
import argparse

import numpy as np
import pandas as pd

import backtest_rules as br
import backtest_winrate_tuning as bt

EXITS = {
    "現行 TP12/SL10/20日": {"kind": "fixed", "tp": 0.12, "sl": 0.10, "hold": 20},
    "B1 ATR(TP2×/SL1.5×/10日)": {"kind": "atr", "tpk": 2.0, "slk": 1.5, "hold": 10},
    "B1 MA20(SL10%/破MA20滿2日/40日)": {"kind": "ma20", "sl": 0.10, "hold": 40, "days": 2},
}
OVERLAYS = ["", " +B2 MA60上彎站上", " +B3 MACD柱翻正/金叉", " +B2+B3"]
MAXH = 40


def macd_hist(c):
    ema12, ema26 = c.ewm(span=12, adjust=False).mean(), c.ewm(span=26, adjust=False).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=9, adjust=False).mean()
    return dif.values, dea.values, (dif - dea).values


def atr14(df):
    c1 = df["Close"].shift(1)
    tr = pd.concat([df["High"] - df["Low"], (df["High"] - c1).abs(), (df["Low"] - c1).abs()], axis=1).max(axis=1)
    return tr.rolling(14).mean().values


def masks(df):
    c = df["Close"]
    ma60 = c.rolling(60).mean()
    b2 = ((ma60 > ma60.shift(5)) & (c > ma60)).values
    dif, dea, h = macd_hist(c)
    h1 = np.r_[np.nan, h[:-1]]
    flip = (h > 0) & (h1 <= 0)
    cross = np.zeros(len(h), dtype=bool)
    for k in range(3):   # 3日內金叉：DIF 上穿 DEA
        d0, e0 = np.roll(dif, k), np.roll(dea, k)
        d1, e1 = np.roll(dif, k + 1), np.roll(dea, k + 1)
        cross |= (d0 > e0) & (d1 <= e1)
    cross[:40] = False
    b3 = np.nan_to_num(flip.astype(float)).astype(bool) | cross
    return b2, b3


def sim_trade(df, e, ex, atr, ma20):
    """回傳單筆淨報酬（小數，已扣成本）。e=進場日 index（開盤進）。"""
    o, h, l, c = df["Open"].values, df["High"].values, df["Low"].values, df["Close"].values
    E = o[e]
    hold = ex["hold"]
    if ex["kind"] == "fixed":
        tp, sl = ex["tp"], ex["sl"]
    elif ex["kind"] == "atr":
        a = atr[e - 1]
        if not np.isfinite(a) or a <= 0:
            return None
        tp, sl = ex["tpk"] * a / E, ex["slk"] * a / E
    else:
        tp, sl = None, ex["sl"]
    below = 0
    for k in range(hold):
        i = e + k
        if i >= len(c):
            return None
        lo, hi = l[i] / E - 1, h[i] / E - 1
        # 停損先（保守）
        if lo <= -sl:
            gap = o[i] / E - 1
            r = min(-sl, gap) if k > 0 else -sl
            return r - br.COST_ROUND_TRIP
        if tp is not None and hi >= tp:
            gap = o[i] / E - 1
            r = max(tp, gap) if k > 0 else tp
            return r - br.COST_ROUND_TRIP
        if ex["kind"] == "ma20":
            below = below + 1 if (np.isfinite(ma20[i]) and c[i] < ma20[i]) else 0
            if below >= ex["days"]:
                j = min(i + 1, len(c) - 1)       # 收盤確認後，隔日開盤出
                return o[j] / E - 1 - br.COST_ROUND_TRIP
    return c[min(e + hold - 1, len(c) - 1)] / E - 1 - br.COST_ROUND_TRIP


def mdd(rets_sorted):
    if len(rets_sorted) == 0:
        return 0.0
    eq = np.cumsum(rets_sorted)
    peak = np.maximum.accumulate(np.r_[0.0, eq])[1:]
    return float((eq - peak).min())


def fam_signals(df, breadth):
    import bt_strategy as bs
    c = df["Close"]
    s15 = br.official_score_series(c).values
    liq = br.liq_ok_array(df)
    base = liq & np.isfinite(s15)
    return {"隨機基準": base, "官網式分數≥12": base & (s15 >= 12), "爆量回檔": bs.pullback_burst_mask(df)}


def stat_pack(events, split):
    """events: list of (date, ret)。回傳 IS / OOS 統計＋最大回撤。"""
    out = {}
    for name, sel in (("IS", lambda d: d < split), ("OOS", lambda d: d >= split)):
        ev = sorted([(d, r) for d, r in events if sel(d)], key=lambda x: x[0])
        a = np.array([r for _, r in ev], dtype=float)
        s = bt.stats(a)
        s["mdd_pct"] = round(mdd(a) * 100, 1)
        out[name] = s
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=int(os.environ.get("BT_N") or 300))
    ap.add_argument("--years", type=int, default=int(os.environ.get("BT_YEARS") or 5))
    ap.add_argument("--tag", default=os.environ.get("BT_TAG") or "")
    ap.add_argument("--synthetic", action="store_true")
    ap.add_argument("--no-upload", action="store_true")
    a = ap.parse_args()
    t0 = time.time()
    prices = br.synthetic_prices() if a.synthetic else br.download_prices(br.load_universe(a.n), a.years)
    if len(prices) < 5:
        print("❌ 有效股票太少")
        sys.exit(1)
    split = br.pick_split_date(prices)
    breadth = bt.market_breadth(prices)
    acc = {}
    for si, (sym, df) in enumerate(prices.items()):
        n = len(df)
        if n < 320:
            continue
        b2, b3 = masks(df)
        atr, ma20 = atr14(df), df["Close"].rolling(20).mean().values
        fams = fam_signals(df, breadth)
        ov = {"": np.ones(n, bool), OVERLAYS[1]: b2, OVERLAYS[2]: b3, OVERLAYS[3]: b2 & b3}
        for fam, m in fams.items():
            for oname, om in ov.items():
                idx = np.where(m & om)[0]
                idx = idx[(idx >= 60) & (idx + 1 + MAXH < n)]
                idx = bt.apply_cooldown(idx)
                for xname, ex in EXITS.items():
                    for t in idx:
                        r = sim_trade(df, t + 1, ex, atr, ma20)
                        if r is not None:
                            acc.setdefault((fam, oname, xname), []).append((df.index[t + 1], r))
        if (si + 1) % 50 == 0:
            print(f"  進度 {si + 1}/{len(prices)}  {time.time()-t0:.0f}s")
    table = {k: stat_pack(v, split) for k, v in acc.items()}
    rows = []
    for (fam, oname, xname), v in table.items():
        base_ov = table.get((fam, "", xname))
        rnd = table.get(("隨機基準", "", xname))
        rows.append({"family": fam, "overlay": oname.strip() or "無", "exit": xname, **v,
                     "same_exit_no_overlay_OOS": base_ov["OOS"] if base_ov else None,
                     "random_OOS": rnd["OOS"] if rnd else None})
    MIN_IS, MIN_OOS = 100, 50

    def ok(r):
        return r["IS"].get("n", 0) >= MIN_IS and r["OOS"].get("n", 0) >= MIN_OOS

    def lift(r):
        b = r["same_exit_no_overlay_OOS"] or {}
        return round(r["OOS"].get("exp_pct", 0) - b.get("exp_pct", 0), 3) if b else None

    for r in rows:
        r["ok_n"] = ok(r)
        r["oos_exp_lift_vs_no_overlay"] = lift(r)
        r["pass_both_exp_gt0"] = bool(r["ok_n"] and r["IS"]["exp_pct"] > 0 and r["OOS"]["exp_pct"] > 0)
    ev = [r for r in rows if r["ok_n"]]
    report = {"kind": "backtest_b1b3", "tag": a.tag, "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
              "n_symbols": len(prices), "split": str(split.date()), "cost_round_trip": br.COST_ROUND_TRIP,
              "n_tests": len(ev), "n_exp_gt0_both": sum(1 for r in ev if r["pass_both_exp_gt0"]),
              "rows": rows}
    print(f"母體 {len(prices)} 檔；評估 {len(ev)} 組，樣本內外期望值皆>0：{report['n_exp_gt0_both']} 組；耗時 {time.time()-t0:.0f}s")
    if a.no_upload:
        if os.environ.get("BT_VERBOSE") == "1":
            for r in sorted(ev, key=lambda r: (r["family"], r["exit"], r["overlay"])):
                print(r["family"], r["overlay"], r["exit"], r["IS"], r["OOS"], r["oos_exp_lift_vs_no_overlay"])
        return
    from supabase import create_client
    sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
    sb.table("ui_selftest_reports").insert({"run_id": os.environ.get("GITHUB_RUN_ID", ""),
                                            "summary": "backtest_b1b3" + (f":{a.tag}" if a.tag else ""),
                                            "report": report}).execute()
    print("✅ 已寫入 Supabase ui_selftest_reports")


if __name__ == "__main__":
    main()
