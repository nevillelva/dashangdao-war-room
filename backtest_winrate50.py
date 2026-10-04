#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
backtest_winrate50.py —— 「勝率是否超過 5 成？不到就調整條件看能不能拉過 5 成」總檢查（R99新增，跑在 Actions）

【和 backtest_winrate_tuning.py 的差別】
  1. 出場網格加入「自然出場」(不設停利停損、只持有 5/10/20 日)：這才是在測『進場訊號本身』有沒有用，
     不被停利停損結構灌水。
  2. 進場訊號擴充成「條件調整」版本：分數門檻、回檔深度、大盤寬度、量能、RSI、穿山惡龍/底部型態參數，
     看每一種收緊條件能不能讓勝率過 5 成。
  3. 勝率 > 50% 本身不是證據——隨機進場在同一出場結構下也常有 50% 以上勝率，所以每一列都附『同出場隨機基準』，
     最後只認：樣本內外勝率都 >50% ∧ 樣本內外期望值都 >0 ∧ 樣本外期望值與勝率都贏過基準 ∧ 逐年勝率穩定。
  4. 多重檢定揭露：報告會寫出總共試了幾組，通過者要自己扣掉「純粹運氣」的份量。
【限制】日K同日停利停損視為先停損；母體有存活者偏誤；樣本外是多頭；同日大盤事件會讓訊號高度集中（有效樣本比 n 小）。
"""
import os
import sys
import json
import argparse
import datetime as dt

import numpy as np
import pandas as pd

import backtest_rules as br
import backtest_winrate_tuning as bt

NO = 9.99                                   # 代表「不設」
bt.TP_GRID = (0.03, 0.05, 0.08, 0.12, NO)
bt.SL_GRID = (0.04, 0.06, 0.08, 0.12, NO)
bt.HOLD_GRID = (5, 10, 20)
bt.MAX_HOLD = 20
MIN_N_IS, MIN_N_OOS = int(os.environ.get("BT_MIN_N_IS") or 150), int(os.environ.get("BT_MIN_N_OOS") or 60)
THRESH = float(os.environ.get("WIN_TARGET") or 0.50)
BASE = "隨機進場(基準)"


def rsi14(c):
    d = c.diff()
    up = d.clip(lower=0).rolling(14).mean()
    dn = (-d.clip(upper=0)).rolling(14).mean()
    return (100 - 100 / (1 + up / dn.replace(0, np.nan))).values


def families(df, breadth):
    c, v = df["Close"], df["Volume"]
    n = len(df)
    s15 = br.official_score_series(c).values
    liq = br.liq_ok_array(df)
    ret3 = (c / c.shift(3) - 1).values
    b = np.nan_to_num(breadth.reindex(df.index).values, nan=0.0)
    rsi = rsi14(c)
    vr = (v / v.rolling(20).mean()).values
    ma60 = c.rolling(60).mean().values
    base = liq & np.isfinite(s15)
    F = {BASE: base}
    for th in (8, 10, 12, 13, 14, 15):
        F[f"分數≥{th}"] = base & (s15 >= th)
    pull3, pull5 = ret3 <= -0.03, ret3 <= -0.05
    F["分數≥10 且3日回檔≥5%"] = base & (s15 >= 10) & pull5
    F["分數≥12 且3日回檔≥3%"] = base & (s15 >= 12) & pull3
    F["分數≥12 且3日回檔≥5%"] = base & (s15 >= 12) & pull5
    F["分數≥12 且大盤寬度≥60%"] = base & (s15 >= 12) & (b >= 0.6)
    F["分數≥12 回檔≥3% 且寬度≥50%"] = base & (s15 >= 12) & pull3 & (b >= 0.5)
    F["分數≥8 回檔≥5% 且寬度≥50%"] = base & (s15 >= 8) & pull5 & (b >= 0.5)
    F["分數≥12 且量比≥1.5"] = base & (s15 >= 12) & (vr >= 1.5)
    F["RSI≤30 且站上MA60"] = base & (rsi <= 30) & (c.values > ma60)
    F["RSI≤30 且寬度≥50%"] = base & (rsi <= 30) & (b >= 0.5)
    F["寬度≥60% 單純進場"] = base & (b >= 0.6)
    out = {}
    for k, m in F.items():
        out[k] = np.where(m)[0]

    def ev_idx(ev):
        return np.array(sorted({e[0] - 1 for e in ev}), dtype=int)
    for ma, rally in ((20, 0.3), (20, 0.4), (60, 0.3), (60, 0.4), (60, 0.5)):
        idx = ev_idx(br.find_chuan_e_events(df, ma, rally, 0.03, 3, 5 if ma == 20 else 10, entries_only=True))
        out[f"穿山惡龍 MA{ma}/前漲{int(rally*100)}%"] = idx
        if (ma, rally) in ((20, 0.3), (60, 0.4)) and len(idx):
            out[f"穿山惡龍 MA{ma}/前漲{int(rally*100)}% 且寬度≥50%"] = idx[b[idx] >= 0.5]
    for vol in (1.0, 2.0):
        idx = ev_idx(br.find_bottom_events(df, 3, "hs", vol, 0.0, "left_shoulder", entries_only=True))
        out[f"頭肩底 量比≥{vol:g}"] = idx
        if vol == 1.0 and len(idx):
            out["頭肩底 量比≥1 且寬度≥50%"] = idx[b[idx] >= 0.5]
    for vol in (1.0, 2.0):
        idx = ev_idx(br.find_bottom_events(df, 8, "w", vol, 0.03, "any", entries_only=True))
        out[f"W底 量比≥{vol:g}"] = idx
        if vol == 1.0 and len(idx):
            out["W底 量比≥1 且寬度≥50%"] = idx[b[idx] >= 0.5]
    res = {}
    for k, v_ in out.items():
        v_ = np.asarray(v_, dtype=int)
        v_ = v_[(v_ >= 0) & (v_ + 1 + bt.MAX_HOLD < n)]
        res[k] = bt.apply_cooldown(v_)
    return res


def fmt(s):
    if not s or not s.get("n"):
        return "n=0"
    return f"n={s['n']} 勝{s['win']*100:.1f}% 期望{s['exp_pct']}% 盈虧比{s['payoff']}"


def exit_label(tp, sl, hold):
    if tp >= NO and sl >= NO:
        return f"自然出場(持有{hold}日)"
    t = "不設" if tp >= NO else f"{tp:.0%}"
    s = "不設" if sl >= NO else f"{sl:.0%}"
    return f"停利{t}/停損{s}/{hold}日"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=int(os.environ.get("BT_N") or 300))
    ap.add_argument("--years", type=int, default=int(os.environ.get("BT_YEARS") or 6))
    ap.add_argument("--synthetic", action="store_true")
    ap.add_argument("--out", default="tuning_out")
    a = ap.parse_args()
    prices = br.synthetic_prices() if a.synthetic else br.download_prices(br.load_universe(a.n), a.years)
    if len(prices) < 5:
        print("❌ 有效股票太少")
        sys.exit(1)
    split = br.pick_split_date(prices)
    breadth = bt.market_breadth(prices)

    acc = {}
    for si, (s, df) in enumerate(prices.items()):
        for fam, t_idx in families(df, breadth).items():
            if len(t_idx) == 0:
                continue
            res, dates = bt.simulate_family(df, t_idx)
            for key, arr in res.items():
                acc.setdefault((fam,) + key, []).append((dates, arr))
        if (si + 1) % 50 == 0:
            print(f"  進度 {si + 1}/{len(prices)}")

    table = {}
    for key, parts in acc.items():
        dates = np.concatenate([p[0].values for p in parts])
        rets = np.concatenate([p[1] for p in parts])
        is_m = dates < np.datetime64(split)
        yrs = pd.DatetimeIndex(dates).year
        table[key] = {"IS": bt.stats(rets[is_m]), "OOS": bt.stats(rets[~is_m]),
                      "by_year": {int(y): bt.stats(rets[yrs == y]) for y in sorted(set(yrs))}}
    rows = []
    for key, v in table.items():
        fam, tp, sl, hold = key
        if fam == BASE:
            continue
        bo = table.get((BASE, tp, sl, hold))
        rows.append({"family": fam, "tp": tp, "sl": sl, "hold": hold, **v,
                     "base_IS": bo["IS"] if bo else None, "base_OOS": bo["OOS"] if bo else None})
    ok_n = lambda r: r["IS"].get("n", 0) >= MIN_N_IS and r["OOS"].get("n", 0) >= MIN_N_OOS

    def stable(r):
        ys = [v for v in r["by_year"].values() if v.get("n", 0) >= 20]
        return bool(ys) and sum(1 for v in ys if v["win"] > THRESH) >= 0.7 * len(ys)

    def passed(r):
        i, o, bo = r["IS"], r["OOS"], r["base_OOS"] or {}
        return (ok_n(r) and i["win"] > THRESH and o["win"] > THRESH and i["exp_pct"] > 0 and o["exp_pct"] > 0
                and o["exp_pct"] > bo.get("exp_pct", 1e9) and o["win"] > bo.get("win", 1.0) and stable(r))

    good = sorted([r for r in rows if passed(r)], key=lambda r: -r["OOS"]["exp_pct"])
    n_tests = sum(1 for r in rows if ok_n(r))

    def line(r):
        yr = " ".join(f"{y}:{v['win']*100:.0f}%" for y, v in r["by_year"].items() if v.get("n", 0) >= 20)
        return (f"| {r['family']} | {exit_label(r['tp'], r['sl'], r['hold'])} | {fmt(r['IS'])} | {fmt(r['OOS'])} | "
                f"{fmt(r['base_OOS'])} | {yr} |")
    head = "| 進場訊號 | 出場 | 樣本內 | 樣本外 | 樣本外基準(同出場隨機進場) | 各年勝率 |\n|---|---|---|---|---|---|"

    L = [f"# 勝率 > {THRESH:.0%} 總檢查（{dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}）", "",
         f"- 母體 {len(prices)} 檔；樣本外切點 {split.date()}；成本來回 {br.COST_ROUND_TRIP*100:.3f}%；進場=訊號隔日開盤",
         f"- 總共評估 {n_tests} 組(進場條件×出場)；通過全部條件 {len(good)} 組。純運氣下，通過數預期約為 {n_tests} 乘以通過機率，"
         "所以通過者要打折看，尤其是只差一點點的。", "",
         "## 一、自然出場（不設停利停損）：進場訊號本身的勝率", "",
         "｜勝率超過 5 成不代表有用：看『同期隨機進場』的勝率。OOS 是多頭，隨機進場本身就常超過 5 成。", "",
         "| 進場訊號 | 持有 | 樣本內勝率 | 樣本外勝率 | 樣本外期望 | 隨機基準樣本外勝率/期望 | 樣本外勝率−基準 |", "|---|---|---|---|---|---|---|"]
    nat = [r for r in rows if r["tp"] >= NO and r["sl"] >= NO and ok_n(r)]
    nat.sort(key=lambda r: (r["family"], r["hold"]))
    for r in nat:
        bo = r["base_OOS"] or {}
        L.append(f"| {r['family']} | {r['hold']}日 | {r['IS']['win']*100:.1f}% (n={r['IS']['n']}) | {r['OOS']['win']*100:.1f}% (n={r['OOS']['n']}) | "
                 f"{r['OOS']['exp_pct']}% | {bo.get('win', 0)*100:.1f}% / {bo.get('exp_pct')}% | {(r['OOS']['win']-bo.get('win', 0))*100:+.1f}pt |")
    L += ["", f"## 二、通過全部條件（勝率>{THRESH:.0%} 且期望值>0 且贏基準且逐年穩定）：{len(good)} 組", ""]
    L += ([head] + [line(r) for r in good[:40]]) if good else ["沒有任何組合通過。"]
    L += ["", "## 三、每個進場訊號：勝率>50% 的出場結構數 / 其中期望值為正且贏基準的數量", "",
          "| 進場訊號 | 可評估出場數 | 樣本內外勝率皆>50% | 其中期望值皆>0 | 其中全條件通過 |", "|---|---|---|---|---|"]
    fams = sorted({r["family"] for r in rows})
    for f in fams:
        rr = [r for r in rows if r["family"] == f and ok_n(r)]
        w = [r for r in rr if r["IS"]["win"] > THRESH and r["OOS"]["win"] > THRESH]
        e = [r for r in w if r["IS"]["exp_pct"] > 0 and r["OOS"]["exp_pct"] > 0]
        p = [r for r in rr if passed(r)]
        L.append(f"| {f} | {len(rr)} | {len(w)} | {len(e)} | {len(p)} |")
    rep = "\n".join(L)
    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, "winrate50_report.md"), "w", encoding="utf-8") as f:
        f.write(rep)
    with open(os.path.join(a.out, "winrate50_results.json"), "w", encoding="utf-8") as f:
        json.dump({"split": str(split.date()), "n_symbols": len(prices), "n_tests": n_tests, "passed": good[:80],
                   "natural": nat}, f, ensure_ascii=False, default=str)
    print(rep)
    summ = os.environ.get("GITHUB_STEP_SUMMARY")
    if summ:
        with open(summ, "a", encoding="utf-8") as f:
            f.write(rep)


if __name__ == "__main__":
    main()
