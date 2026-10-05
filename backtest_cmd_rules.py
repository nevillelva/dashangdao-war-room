#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
backtest_cmd_rules.py —— 「查指令篩出的股票」＋進出場規則的規則級回測（2026-10-05，跑在 GitHub Actions）

【目的】使用者要求：「目前查的指令篩選出的股票，系統自動進出場的規則做一系列調整，善用回測，讓每個篩選後的股票進出場要有 5 成勝率以上」。
這支腳本把「價格面」的查指令（查1/查2/查8/查9/查10量縮部分/查12 K線型態）用與系統相同的定義（warroom_core.evaluate_single_condition、
_filter_backtest_one_stock）向量化，再疊上幾種已知有效的「濾網調整」（大盤寬度、3日回檔、站上MA60、官網式分數≥12），
搭配停利/停損/持有天數網格，並用與 backtest_winrate50.py 完全相同的嚴格標準判定：
    樣本內外勝率皆 >50% ∧ 期望值皆 >0（已扣來回成本0.585%）∧ 樣本外期望與勝率都贏過『同出場的隨機進場基準』∧ 逐年勝率穩定。
籌碼/營收/股利類的查指令（查3/4/5/6/10融資/11）日K無法還原歷史，不在此回測（改走前瞻紙上追蹤）。

【多重檢定】報告會列出總測試組數 n_tests；通過者要自行扣掉「純運氣」份量（預期假陽性數≈ n_tests × 假陽性率）。
【誠實聲明】母體只含現在仍在市場的大成交值個股（存活者偏誤）；樣本外段偏多頭；日K同日停利停損視為先停損；進場=訊號隔日開盤。

【輸出】ui_selftest_reports(summary='backtest_cmd_rules'+tag, report=JSON)；不寫公開分支、不上傳 artifact。
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

NO = 9.99
THRESH = float(os.environ.get("WIN_TARGET") or 0.50)
MIN_N_IS, MIN_N_OOS = int(os.environ.get("BT_MIN_N_IS") or 100), int(os.environ.get("BT_MIN_N_OOS") or 50)
BASE = "隨機進場(基準)"

CMD_NAMES = {
    "c1": "查1 主升段突擊",
    "c2": "查2 魚頭慢伏支撐",
    "c8": "查8 昨日強勢動能延續",
    "c9": "查9 均線糾結爆量突破",
    "c10": "查10 量縮沉澱(僅量縮部分)",
    "c12a": "查12 長紅K/吞噬",
    "c12b": "查12 紅三兵",
    "c12c": "查12 壓縮盤整",
}
OVERLAYS = ["", " +寬度≥50%", " +3日回檔≥3%", " +3日回檔≥5%", " +站上MA60", " +分數≥12", " +回檔≥3%且寬度≥50%"]


def _kd(df):
    low9, high9 = df["Low"].rolling(9).min(), df["High"].rolling(9).max()
    rsv = (df["Close"] - low9) / (high9 - low9 + 1e-9) * 100
    k = rsv.bfill().ffill().ewm(com=2, adjust=False).mean()
    d = k.ewm(com=2, adjust=False).mean()
    return k.values, d.values


def _atr14(df):
    c1 = df["Close"].shift(1)
    tr = pd.concat([df["High"] - df["Low"], (df["High"] - c1).abs(), (df["Low"] - c1).abs()], axis=1).max(axis=1)
    return tr.rolling(14).mean().values


def command_signals(df):
    """回傳 {代碼: 布林陣列}（訊號在該日收盤後成立）。定義與 warroom_core.evaluate_single_condition / _filter_backtest_one_stock 一致。"""
    o, h, l, c, v = (df[k].values.astype(float) for k in ("Open", "High", "Low", "Close", "Volume"))
    n = len(df)
    s = pd.Series
    atr = _atr14(df)
    ma60 = s(c).rolling(60).mean().values
    vr = (s(v) / s(v).rolling(5).mean()).values           # 與 _filter_backtest_one_stock 同：當日量/含當日的5日均量
    K, D = _kd(df)
    body = np.abs(c - o)
    c1, o1, c2, o2 = np.r_[np.nan, c[:-1]], np.r_[np.nan, o[:-1]], np.r_[np.nan, np.nan, c[:-2]], np.r_[np.nan, np.nan, o[:-2]]
    sig = (body > atr * 0.5)
    first_red = (c > o) & (c1 < o1) & sig
    out = {}
    out["c1"] = first_red & (vr >= 2.0) & (K > D) & (K > 50)
    out["c2"] = (c > ma60) & (vr >= 1.2)
    prev_gain = (c1 / c2 - 1)
    out["c8"] = prev_gain > 0.05
    out["c9"] = vr >= 2.0
    out["c10"] = (vr > 0) & (vr <= 0.6)
    out["c12a"] = (c > o) & sig
    avg_body3 = (body + np.abs(c1 - o1) + np.abs(c2 - o2)) / 3.0
    out["c12b"] = (c > o) & (c1 > o1) & (c2 > o2) & (c > c1) & (c1 > c2) & (avg_body3 > atr * 0.3)
    rng5 = s(h).rolling(5).max().values - s(l).rolling(5).min().values
    avg20 = (s(h) - s(l)).rolling(20).mean().values
    out["c12c"] = (~sig) & (rng5 < avg20 * 2.2)
    for k_ in out:
        out[k_] = np.nan_to_num(out[k_].astype(float), nan=0.0).astype(bool)
    return out


def families(df, breadth, max_hold):
    c = df["Close"]
    n = len(df)
    s15 = br.official_score_series(c).values
    liq = br.liq_ok_array(df)
    ret3 = (c / c.shift(3) - 1).values
    ma60 = c.rolling(60).mean().values
    b = np.nan_to_num(breadth.reindex(df.index).values, nan=0.0)
    base = liq & np.isfinite(s15)
    pull3, pull5 = ret3 <= -0.03, ret3 <= -0.05
    ov = {
        "": np.ones(n, bool),
        " +寬度≥50%": b >= 0.5,
        " +3日回檔≥3%": pull3,
        " +3日回檔≥5%": pull5,
        " +站上MA60": c.values > ma60,
        " +分數≥12": s15 >= 12,
        " +回檔≥3%且寬度≥50%": pull3 & (b >= 0.5),
    }
    sigs = command_signals(df)
    F = {BASE: base}
    for code, nm in CMD_NAMES.items():
        for suf, m in ov.items():
            F[nm + suf] = base & sigs[code] & np.nan_to_num(m.astype(float)).astype(bool)
    # 參考：系統目前上線的「穿山惡龍 MA60/前漲40% 且寬度≥40%」與「回檔進場」
    F["【參考】分數≥12 且3日回檔≥5%"] = base & (s15 >= 12) & pull5
    out = {k: np.where(m)[0] for k, m in F.items()}
    ev = br.find_chuan_e_events(df, 60, 0.4, 0.03, 3, 10, entries_only=True)
    idx = np.array(sorted({e[0] - 1 for e in ev}), dtype=int)
    out["【參考】穿山惡龍 MA60/前漲40% 且寬度≥40%"] = idx[b[idx] >= 0.4] if len(idx) else idx
    res = {}
    for k, v_ in out.items():
        v_ = np.asarray(v_, dtype=int)
        v_ = v_[(v_ >= 0) & (v_ + 1 + max_hold < n)]
        res[k] = bt.apply_cooldown(v_)
    return res


def exit_label(tp, sl, hold):
    if tp >= NO and sl >= NO:
        return f"自然出場(持有{hold}日)"
    t = "不設" if tp >= NO else f"{tp:.0%}"
    s = "不設" if sl >= NO else f"{sl:.0%}"
    return f"停利{t}/停損{s}/{hold}日"


def run_symbol_stats(prices, rule="pullback_burst", tp=0.12, sl=0.15, hold=20):
    """每檔股票在『爆量回檔』規則下、用實際上線出場(停利12%/停損15%/20日，已扣成本)的歷史戰績 → entry_rule_symbol_stats。
    訊號用 bt_strategy.pullback_burst_mask（與排程 live 同一支函式），冷卻20日與回測家族相同。"""
    import bt_strategy as bs
    bt.TP_GRID, bt.SL_GRID, bt.HOLD_GRID, bt.MAX_HOLD = (tp,), (sl,), (hold,), hold
    rows = []
    for sym, df in prices.items():
        m = bs.pullback_burst_mask(df)
        idx = np.where(m)[0]
        idx = idx[(idx >= 0) & (idx + 1 + hold < len(df))]
        idx = bt.apply_cooldown(idx)
        if len(idx) == 0:
            continue
        res, _dates = bt.simulate_family(df, idx)
        arr = res[(tp, sl, hold)]
        rows.append({"symbol": str(sym), "rule": rule, "n": int(len(arr)), "wins": int((arr > 0).sum()),
                     "avg_pct": round(float(arr.mean()) * 100, 3),
                     "source": f"backtest_cmd_rules(n={len(prices)},TP{int(tp*100)}/SL{int(sl*100)}/{hold}d)",
                     "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=int(os.environ.get("BT_N") or 300))
    ap.add_argument("--years", type=int, default=int(os.environ.get("BT_YEARS") or 6))
    ap.add_argument("--tps", default=os.environ.get("BT_TPS") or "0.05,0.08,0.10,0.12,0.15,0.20,9.99")
    ap.add_argument("--sls", default=os.environ.get("BT_SLS") or "0.08,0.10,0.12,0.15,9.99")
    ap.add_argument("--holds", default=os.environ.get("BT_HOLDS") or "5,10,20")
    ap.add_argument("--tag", default=os.environ.get("BT_TAG") or "")
    ap.add_argument("--synthetic", action="store_true")
    ap.add_argument("--no-upload", action="store_true")
    ap.add_argument("--symbol-stats", action="store_true", help="只產生『爆量回檔』規則的單檔歷史戰績並寫入 entry_rule_symbol_stats")
    a = ap.parse_args()
    t0 = time.time()
    bt.TP_GRID = tuple(float(x) for x in a.tps.split(","))
    bt.SL_GRID = tuple(float(x) for x in a.sls.split(","))
    bt.HOLD_GRID = tuple(int(x) for x in a.holds.split(","))
    bt.MAX_HOLD = max(bt.HOLD_GRID)
    prices = br.synthetic_prices() if a.synthetic else br.download_prices(br.load_universe(a.n), a.years)
    if len(prices) < 5:
        print("❌ 有效股票太少")
        sys.exit(1)
    if a.symbol_stats:
        rows = run_symbol_stats(prices)
        tn, tw = sum(r["n"] for r in rows), sum(r["wins"] for r in rows)
        print(f"單檔戰績：{len(rows)} 檔有訊號，合計 {tn} 筆、{tw} 勝（{(tw / tn * 100 if tn else 0):.1f}%）；耗時 {time.time()-t0:.0f}s")
        if not a.no_upload and rows:
            from supabase import create_client
            sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
            for i in range(0, len(rows), 200):
                sb.table("entry_rule_symbol_stats").upsert(rows[i:i + 200], on_conflict="symbol,rule").execute()
            print("✅ 已寫入 entry_rule_symbol_stats")
        return
    split = br.pick_split_date(prices)
    breadth = bt.market_breadth(prices)
    print(f"母體 {len(prices)} 檔；樣本外切點 {split.date()}；格點 TP{bt.TP_GRID} SL{bt.SL_GRID} HOLD{bt.HOLD_GRID}")

    acc = {}
    for si, (s, df) in enumerate(prices.items()):
        for fam, t_idx in families(df, breadth, bt.MAX_HOLD).items():
            if len(t_idx) == 0:
                continue
            res, dates = bt.simulate_family(df, t_idx)
            for key, arr in res.items():
                acc.setdefault((fam,) + key, []).append((dates, arr))
        if (si + 1) % 50 == 0:
            print(f"  進度 {si + 1}/{len(prices)}  {time.time()-t0:.0f}s")

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

    for r in rows:
        r["label"] = exit_label(r["tp"], r["sl"], r["hold"])
        r["ok_n"] = ok_n(r)
        r["passed"] = passed(r)
    n_tests = sum(1 for r in rows if r["ok_n"])
    good = sorted([r for r in rows if r["passed"]], key=lambda r: -r["OOS"]["exp_pct"])

    # 每個「進場家族」摘要：可評估出場數 / 勝率皆>50% 數 / 期望皆>0 數 / 全條件通過數 / 通過者中樣本外期望最高者
    fams = sorted({r["family"] for r in rows})
    per_family = []
    for f in fams:
        rr = [r for r in rows if r["family"] == f and r["ok_n"]]
        w = [r for r in rr if r["IS"]["win"] > THRESH and r["OOS"]["win"] > THRESH]
        e = [r for r in w if r["IS"]["exp_pct"] > 0 and r["OOS"]["exp_pct"] > 0]
        p = [r for r in rr if r["passed"]]
        best = max(p, key=lambda r: r["OOS"]["exp_pct"]) if p else None
        nat = [r for r in rr if r["tp"] >= NO and r["sl"] >= NO]
        per_family.append({
            "family": f, "n_exits": len(rr), "win_both_gt50": len(w), "exp_both_gt0": len(e), "passed": len(p),
            "best": None if not best else {k: best[k] for k in ("label", "IS", "OOS", "base_OOS", "by_year")},
            "natural": [{k: r[k] for k in ("label", "IS", "OOS", "base_OOS")} for r in sorted(nat, key=lambda x: x["hold"])],
        })
    # 指定的「使用者問的比例」：停利/停損組合在每個家族的表現（供直接對照）
    asked = []
    for r in rows:
        if r["ok_n"] and (r["tp"], r["sl"]) in ((0.20, 0.10), (0.15, 0.10), (0.12, 0.15), (0.12, 0.12), (0.10, 0.10)):
            asked.append({k: r[k] for k in ("family", "label", "IS", "OOS", "base_OOS", "passed")})
    report = {"kind": "backtest_cmd_rules", "tag": a.tag, "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
              "n_symbols": len(prices), "split": str(split.date()), "cost_round_trip": br.COST_ROUND_TRIP,
              "grid": {"tp": list(bt.TP_GRID), "sl": list(bt.SL_GRID), "hold": list(bt.HOLD_GRID)},
              "n_tests": n_tests, "n_passed": len(good), "threshold": THRESH,
              "passed_top": [{k: r[k] for k in ("family", "label", "IS", "OOS", "base_OOS", "by_year")} for r in good[:60]],
              "per_family": per_family, "asked_ratios": asked[:400]}
    print(f"總測試 {n_tests} 組，全條件通過 {len(good)} 組；耗時 {time.time()-t0:.0f}s")
    if os.environ.get("BT_VERBOSE") == "1":   # 預設不印各家族明細：公開 repo 的 Actions 日誌任何人都看得到，策略結果只存私有表
        for pf in per_family:
            print(f"{pf['family']:<34} 可評估{pf['n_exits']:>3} 勝率雙>50%:{pf['win_both_gt50']:>3} 期望雙>0:{pf['exp_both_gt0']:>3} 通過:{pf['passed']:>3}")
    summ = os.environ.get("GITHUB_STEP_SUMMARY")
    if summ:
        with open(summ, "a", encoding="utf-8") as f:   # 只寫精簡統計（公開頁面會看到，不放個別策略細節）
            f.write(f"回測完成：測試 {n_tests} 組、通過 {len(good)} 組（細節存私有表）\n")
    if a.no_upload:
        return
    from supabase import create_client
    sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
    sb.table("ui_selftest_reports").insert({"run_id": os.environ.get("GITHUB_RUN_ID", ""),
                                            "summary": "backtest_cmd_rules" + (f":{a.tag}" if a.tag else ""),
                                            "report": report}).execute()
    print("✅ 已寫入 Supabase ui_selftest_reports")


if __name__ == "__main__":
    main()
