#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
backtest_winrate_tuning.py —— 以「實際回測數字」調校出場結構與進場條件，尋找勝率 70%~80% 的組合（R99新增，跑在 Actions）

【總指揮官要求】參考資料只是經驗，要用回測的實際數字調整；目標勝率 70%~80%。

【這支腳本怎麼做、以及誠實的限制】
1. 勝率主要由「出場結構」決定：停利很小、停損很寬，連「隨機進場」都能有七成勝率，但期望值常常是負的。
   所以每一組(進場訊號 × 停利 × 停損 × 持有天數)都同時算：
     勝率、平均獲利、平均虧損、盈虧比、期望值(每筆淨報酬，已扣手續費+證交稅)，
     以及「同一組出場參數、隨機進場」的基準 → 看進場訊號有沒有真的帶來額外勝率/期望值。
2. 防過度配適：樣本內(前70%)挑參數、樣本外(後30%)驗證；另外列出每年的勝率看穩定度(含空頭年)。
3. 只有同時滿足下列條件才叫「通過」：樣本內與樣本外勝率都 ≥ 門檻、樣本內外期望值都 > 0、
   樣本外期望值 > 同出場參數的隨機基準、樣本數足夠。找不到就如實回報「找不到」，不硬湊。
4. 日K限制：同一天同時碰到停利與停損無法知道先後，一律當作「先停損」(保守)；跳空依開盤價成交。
5. 母體仍有存活者偏誤，OOS又是多頭行情，數字偏樂觀，僅用於比較。
"""
import os
import sys
import json
import argparse
import itertools
import datetime as dt

import numpy as np
import pandas as pd

import backtest_rules as br

TP_GRID = (0.03, 0.05, 0.08, 0.12)
SL_GRID = (0.04, 0.06, 0.08, 0.12)
HOLD_GRID = (5, 10, 20)
MAX_HOLD = max(HOLD_GRID)
COOLDOWN = 20
WIN_TARGET = float(os.environ.get("WIN_TARGET") or 0.70)
MIN_N_IS = 150
MIN_N_OOS = 60


def apply_cooldown(idx, cd=COOLDOWN):
    out, last = [], -10 ** 9
    for t in idx:
        if t - last >= cd:
            out.append(t)
            last = t
    return np.array(out, dtype=int)


def market_breadth(prices):
    """市場寬度：全母體中收盤站上MA60的比例（只用當日以前資料）。"""
    flags = {}
    for s, df in prices.items():
        c = df["Close"]
        flags[s] = (c > c.rolling(60).mean()).astype(float).where(c.rolling(60).mean().notna())
    return pd.DataFrame(flags).mean(axis=1)


def signal_families(df, breadth):
    """回傳 {家族名: 訊號日index陣列}，訊號在該日收盤後成立，隔日開盤進場。"""
    c = df["Close"]
    n = len(df)
    s15 = br.official_score_series(c).values
    s6 = br.ma_score_series(c).values
    liq = br.liq_ok_array(df)
    ret3 = (c / c.shift(3) - 1).values
    b = breadth.reindex(df.index).values
    base = liq & np.isfinite(s15)
    fam = {"隨機進場(基準)": np.where(base)[0]}
    for th in (10, 12, 14):
        fam[f"官網式分數≥{th}"] = np.where(base & (s15 >= th))[0]
    fam["6均線全站上"] = np.where(base & (s6 == 6))[0]
    pull = ret3 <= -0.03
    fam["分數≥10且3日回檔≥3%"] = np.where(base & (s15 >= 10) & pull)[0]
    fam["分數≥12且3日回檔≥3%"] = np.where(base & (s15 >= 12) & pull)[0]
    bull = np.nan_to_num(b, nan=0.0) >= 0.5
    fam["分數≥12且大盤寬度≥50%"] = np.where(base & (s15 >= 12) & bull)[0]
    fam["分數≥10回檔≥3%且大盤寬度≥50%"] = np.where(base & (s15 >= 10) & pull & bull)[0]
    # 型態類
    ev = br.find_chuan_e_events(df, 20, 0.3, 0.03, 3, 5, entries_only=True)
    fam["穿山惡龍(附件預設MA20/前漲30%)"] = np.array(sorted({e[0] - 1 for e in ev}), dtype=int)
    ev = br.find_chuan_e_events(df, 60, 0.4, 0.03, 3, 10, entries_only=True)
    fam["穿山惡龍(MA60/前漲40%)"] = np.array(sorted({e[0] - 1 for e in ev}), dtype=int)
    ev = br.find_bottom_events(df, 3, "hs", 1.0, 0.0, "left_shoulder", entries_only=True)
    fam["頭肩底(帶量站上頸線)"] = np.array(sorted({e[0] - 1 for e in ev}), dtype=int)
    ev = br.find_bottom_events(df, 8, "w", 1.0, 0.03, "any", entries_only=True)
    fam["W底(帶量站上頸線)"] = np.array(sorted({e[0] - 1 for e in ev}), dtype=int)
    out = {}
    for k, v in fam.items():
        v = v[(v >= 0) & (v + 1 + MAX_HOLD < n)]
        out[k] = apply_cooldown(v)
    return out


def simulate_family(df, t_idx):
    """對一組訊號日，回傳 {(tp,sl,hold): net報酬陣列} 與進場日期。"""
    o, h, l, c = (df[k].values for k in ("Open", "High", "Low", "Close"))
    e = t_idx + 1
    E = o[e]
    K = np.arange(MAX_HOLD)
    rows = e[:, None] + K[None, :]
    Hm = h[rows] / E[:, None] - 1
    Lm = l[rows] / E[:, None] - 1
    Om = o[rows] / E[:, None] - 1
    Cm = c[rows] / E[:, None] - 1
    res = {}
    for tp, sl, hold in itertools.product(TP_GRID, SL_GRID, HOLD_GRID):
        H, L, O = Hm[:, :hold], Lm[:, :hold], Om[:, :hold]
        tp_hit, sl_hit = H >= tp, L <= -sl
        any_tp, any_sl = tp_hit.any(axis=1), sl_hit.any(axis=1)
        f_tp = np.where(any_tp, tp_hit.argmax(axis=1), hold + 1)
        f_sl = np.where(any_sl, sl_hit.argmax(axis=1), hold + 1)
        ret = Cm[:, hold - 1].copy()                       # 都沒觸發：持有到期收盤出
        use_sl = any_sl & (f_sl <= f_tp)                   # 同日兩者皆觸發→先停損(保守)
        use_tp = any_tp & ~use_sl
        k_sl = np.clip(f_sl, 0, hold - 1)
        k_tp = np.clip(f_tp, 0, hold - 1)
        gap_sl = O[np.arange(len(e)), k_sl]
        gap_tp = O[np.arange(len(e)), k_tp]
        ret = np.where(use_sl, np.minimum(-sl, np.where(f_sl > 0, gap_sl, -sl)), ret)
        ret = np.where(use_tp, np.maximum(tp, np.where(f_tp > 0, gap_tp, tp)), ret)
        res[(tp, sl, hold)] = ret - br.COST_ROUND_TRIP
    return res, df.index[e]


def stats(a):
    a = np.asarray(a, dtype=float)
    n = len(a)
    if n == 0:
        return {"n": 0}
    win = a > 0
    aw = a[win].mean() if win.any() else 0.0
    al = a[~win].mean() if (~win).any() else 0.0
    gl = -a[~win].sum()
    return {"n": n, "win": round(float(win.mean()), 4), "exp_pct": round(float(a.mean()) * 100, 3),
            "avg_win_pct": round(float(aw) * 100, 2), "avg_loss_pct": round(float(al) * 100, 2),
            "payoff": round(float(aw / -al), 2) if al < 0 else None,
            "pf": round(float(a[win].sum() / gl), 2) if gl > 0 else None}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=int(os.environ.get("BT_N") or 300))
    ap.add_argument("--years", type=int, default=int(os.environ.get("BT_YEARS") or 6))
    ap.add_argument("--synthetic", action="store_true")
    ap.add_argument("--out", default="tuning_out")
    a = ap.parse_args()

    if a.synthetic:
        prices = br.synthetic_prices()
    else:
        prices = br.download_prices(br.load_universe(a.n), a.years)
    if len(prices) < 5:
        print("❌ 有效股票太少")
        sys.exit(1)
    split = br.pick_split_date(prices)
    breadth = market_breadth(prices)

    acc = {}   # (family, tp, sl, hold) -> list of (date, ret)
    for si, (s, df) in enumerate(prices.items()):
        fams = signal_families(df, breadth)
        for fam, t_idx in fams.items():
            if len(t_idx) == 0:
                continue
            res, dates = simulate_family(df, t_idx)
            for key, arr in res.items():
                acc.setdefault((fam,) + key, []).append((dates, arr))
        if (si + 1) % 50 == 0:
            print(f"  進度 {si + 1}/{len(prices)}")

    table = {}
    for key, parts in acc.items():
        dates = np.concatenate([p[0].values for p in parts])
        rets = np.concatenate([p[1] for p in parts])
        is_m = dates < np.datetime64(split)
        years = pd.DatetimeIndex(dates).year
        table[key] = {"IS": stats(rets[is_m]), "OOS": stats(rets[~is_m]),
                      "by_year": {int(y): stats(rets[years == y]) for y in sorted(set(years))}}

    base_key = "隨機進場(基準)"
    rows = []
    for key, v in table.items():
        fam, tp, sl, hold = key
        if fam == base_key:
            continue
        b = table.get((base_key, tp, sl, hold))
        rows.append({"family": fam, "tp": tp, "sl": sl, "hold": hold, **v,
                     "base_IS": b["IS"] if b else None, "base_OOS": b["OOS"] if b else None})

    def passed(r):
        i, o, bo = r["IS"], r["OOS"], r["base_OOS"] or {}
        return (i.get("n", 0) >= MIN_N_IS and o.get("n", 0) >= MIN_N_OOS
                and i["win"] >= WIN_TARGET and o["win"] >= WIN_TARGET
                and i["exp_pct"] > 0 and o["exp_pct"] > 0
                and o["exp_pct"] > bo.get("exp_pct", 1e9))

    ok = sorted([r for r in rows if passed(r)], key=lambda r: -r["OOS"]["exp_pct"])
    hi_win = sorted([r for r in rows if r["IS"].get("n", 0) >= MIN_N_IS and r["OOS"].get("n", 0) >= MIN_N_OOS
                     and r["IS"]["win"] >= WIN_TARGET and r["OOS"]["win"] >= WIN_TARGET],
                    key=lambda r: -r["OOS"]["exp_pct"])
    pos_exp = sorted([r for r in rows if r["IS"].get("n", 0) >= MIN_N_IS and r["OOS"].get("n", 0) >= MIN_N_OOS
                      and r["IS"]["exp_pct"] > 0 and r["OOS"]["exp_pct"] > 0
                      and r["OOS"]["exp_pct"] > (r["base_OOS"] or {}).get("exp_pct", 1e9)],
                     key=lambda r: -r["OOS"]["win"])

    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, "tuning_results.json"), "w", encoding="utf-8") as f:
        json.dump({"split": str(split.date()), "n_symbols": len(prices), "passed": ok[:50],
                   "hi_win_only": hi_win[:50], "pos_exp_only": pos_exp[:50],
                   "all_rows": rows}, f, ensure_ascii=False, default=str)

    def fmt(s):
        if not s or not s.get("n"):
            return "n=0"
        return (f"n={s['n']} 勝{s['win']*100:.1f}% 期望{s['exp_pct']}% 均賺{s['avg_win_pct']}% "
                f"均賠{s['avg_loss_pct']}% 盈虧比{s['payoff']}")

    def line(r):
        yr = " ".join(f"{y}:{v['win']*100:.0f}%" for y, v in r["by_year"].items() if v.get("n", 0) >= 20)
        return (f"| {r['family']} | 停利{r['tp']:.0%}/停損{r['sl']:.0%}/{r['hold']}日 | {fmt(r['IS'])} | {fmt(r['OOS'])} | "
                f"{fmt(r['base_OOS'])} | {yr} |")

    head = "| 進場訊號 | 出場結構 | 樣本內 | 樣本外 | 樣本外基準(隨機進場同出場) | 各年勝率 |\n|---|---|---|---|---|---|"
    L = [f"# 勝率調校回測（{dt.datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}）", "",
         f"- 母體 {len(prices)} 檔；樣本外切點 {split.date()}；目標勝率 ≥ {WIN_TARGET:.0%}；成本來回 {br.COST_ROUND_TRIP*100:.3f}%",
         "- 通過條件＝樣本內外勝率皆達標 ∧ 樣本內外期望值皆>0 ∧ 樣本外期望值>同出場隨機基準 ∧ 樣本數足夠",
         "- 規則：同日同時碰到停利與停損視為先停損；跳空依開盤價成交；進場=訊號隔日開盤。", ""]
    L += [f"## 一、通過全部條件的組合：{len(ok)} 組", ""]
    if ok:
        L += [head] + [line(r) for r in ok[:20]]
    else:
        L += ["**沒有任何組合同時達到「勝率≥目標 且 真有正期望值（贏過隨機基準）」。**"]
    L += ["", f"## 二、只看勝率達標（不論期望值）前15組：{len(hi_win)} 組", "",
          "（勝率高但期望值≤0 或沒贏過隨機基準 = 只是出場結構造成的高勝率，不代表進場訊號有效）", ""]
    L += [head] + [line(r) for r in hi_win[:15]]
    L += ["", f"## 三、有真正正期望值（樣本內外>0 且贏過基準）按勝率排序前15組：{len(pos_exp)} 組", ""]
    L += [head] + [line(r) for r in pos_exp[:15]]
    L += ["", "## 各進場訊號最佳出場（以樣本外期望值）", "", head]
    best = {}
    for r in rows:
        if r["IS"].get("n", 0) >= MIN_N_IS and r["OOS"].get("n", 0) >= MIN_N_OOS:
            if r["family"] not in best or r["OOS"]["exp_pct"] > best[r["family"]]["OOS"]["exp_pct"]:
                best[r["family"]] = r
    L += [line(r) for r in best.values()]
    rep = "\n".join(L)
    with open(os.path.join(a.out, "tuning_report.md"), "w", encoding="utf-8") as f:
        f.write(rep)
    print(rep)
    summ = os.environ.get("GITHUB_STEP_SUMMARY")
    if summ:
        with open(summ, "a", encoding="utf-8") as f:
            f.write(rep)


if __name__ == "__main__":
    main()
