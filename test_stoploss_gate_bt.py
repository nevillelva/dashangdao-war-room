#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_stoploss_gate_bt.py —— stoploss_gate_bt.py／stoploss_gate_bt_run.py 單元測試（合成資料、不連網、不碰資料庫）。
執行：python test_stoploss_gate_bt.py   （全部通過 exit 0）"""
import json
import os
import sys
import tempfile

import numpy as np
import pandas as pd

import backtest_rules as br
import bt_cache
import bt_strategy as bts
import stoploss_gate_bt as G
import stoploss_gate_bt_run as R

FAILS = []


def check(name, cond, extra=""):
    print(("  ✅ " if cond else "  ❌ ") + name + ("" if cond else f" {extra}"))
    if not cond:
        FAILS.append(name)


def mk(ohlc, start="2024-01-02"):
    idx = pd.bdate_range(start, periods=len(ohlc))
    return pd.DataFrame(ohlc, index=idx, columns=["Open", "High", "Low", "Close"])


def arrs(df):
    return tuple(df[k].values for k in ("Open", "High", "Low", "Close"))


def test_cost():
    print("成本假設")
    check("預設來回 0.585%（與 backtest_rules 一致）", abs(G.round_trip_cost_pct() - br.COST_ROUND_TRIP * 100) < 1e-9, G.round_trip_cost_pct())
    check("手續費 6 折：2×0.1425%×0.6+0.3%", abs(G.round_trip_cost_pct(0.6) - (2 * 0.1425 * 0.6 + 0.3)) < 1e-9)
    check("滑價單邊 0.1% → 來回 +0.2", abs(G.round_trip_cost_pct(1.0, 0.1) - 0.785) < 1e-9)


def test_simulate_hand():
    print("simulate_trade 手算情境")
    c0 = G.round_trip_cost_pct()
    o, h, l, c = arrs(mk([(100, 101, 99, 100), (100, 113, 100, 112)]))
    r = G.simulate_trade(o, h, l, c, 0, 0.12, 0.15, 20, c0)
    check("停利 +12% 扣成本", r and r[2] == "take_profit" and abs(r[0] - (12 - c0)) < 1e-9, r)
    o, h, l, c = arrs(mk([(100, 120, 80, 100)]))
    r = G.simulate_trade(o, h, l, c, 0, 0.12, 0.15, 20, c0)
    check("同日兩者都觸及 → 先停損", r and r[2] == "stop_loss", r)
    o, h, l, c = arrs(mk([(100, 101, 99, 100), (80, 82, 78, 79)]))
    r = G.simulate_trade(o, h, l, c, 0, 0.12, 0.15, 20, c0)
    check("向下跳空以開盤價成交（-20%）", r and abs(r[0] - (-20 - c0)) < 1e-9, r)
    o, h, l, c = arrs(mk([(100, 100, 92, 95)]))
    r = G.simulate_trade(o, h, l, c, 0, 0.11, 0.07, 20, c0)
    check("固定 7% 停損", r and r[2] == "stop_loss" and abs(r[0] - (-7 - c0)) < 1e-9, r)
    o, h, l, c = arrs(mk([(100, 105, 95, 101)] * 25))
    r = G.simulate_trade(o, h, l, c, 0, 0.12, 0.15, 20, c0)
    check("滿 20 日收盤出", r and r[2] == "time_stop" and r[1] == 19 and abs(r[0] - (1 - c0)) < 1e-9, r)
    o, h, l, c = arrs(mk([(100, 105, 95, 101)] * 10))
    check("資料不足 20 日且未觸發 → None（不計入）", G.simulate_trade(o, h, l, c, 0, 0.12, 0.15, 20, c0) is None)
    check("進場價無效 → None", G.simulate_trade(*arrs(mk([(0, 1, 1, 1)] * 3)), 0, 0.1, 0.1) is None)


def test_simulate_parity():
    print("simulate_trade 與 bt_strategy.evaluate_exit 逐筆一致（隨機資料）")
    rng = np.random.default_rng(11)
    bad = tot = 0
    for _ in range(300):
        n = 40
        gap = rng.normal(0, 0.015, n)
        intra = rng.normal(0, 0.03, n)
        o = 100 * np.cumprod(1 + gap)
        c = o * (1 + intra)
        h = np.maximum(o, c) * (1 + np.abs(rng.normal(0, 0.01, n)))
        l = np.minimum(o, c) * (1 - np.abs(rng.normal(0, 0.01, n)))
        df = pd.DataFrame({"Open": o, "High": h, "Low": l, "Close": c}, index=pd.bdate_range("2024-01-02", periods=n))
        e = int(rng.integers(0, 15))
        for tp, sl in ((0.12, 0.15), (0.11, 0.07), (0.11, 0.04)):
            ref = bts.evaluate_exit(df.iloc[e:], o[e], tp, sl, 20)
            mine = G.simulate_trade(o, h, l, c, e, tp, sl, 20)
            tot += 1
            if ref is None:
                bad += mine is not None
            else:
                bad += not (mine is not None and abs(mine[0] / 100 - ref["net_ret"]) < 1e-9 and mine[2] == ref["reason"] and mine[1] - e + 1 == ref["days_held"])
    check(f"{tot} 筆比對全一致", bad == 0, f"不一致 {bad}")


def test_stats():
    print("group_stats：勝率／賺賠比／打平勝率")
    xs = [10.0] * 4 + [-5.0] * 6            # 勝率 40%，平均賺 10、平均賠 -5
    s = G.group_stats(xs, min_n=5)
    check("n=10、勝率 40%", s["n"] == 10 and s["win_pct"] == 40.0, s)
    check("賺賠比 2.0、打平勝率 33.33%", s["payoff"] == 2.0 and abs(s["breakeven_win_pct"] - 33.33) < 0.01, s)
    check("平均 = 0.4×10 − 0.6×5 = 1.0", abs(s["avg"] - 1.0) < 1e-9, s)
    check("勝率高於打平勝率 → edge>0", s["edge_pp"] > 0, s)
    rng = np.random.default_rng(3)
    ok = True
    for _ in range(50):
        r = list(rng.normal(0.5, 5, 80))
        t = G.group_stats(r, 30)
        be = t["breakeven_win_pct"] / 100
        w = t["win_pct"] / 100
        ok &= abs((w - be) * (t["avg_win"] - t["avg_loss"]) - t["avg"]) < 0.01   # avg = (w−be)×(W+L)
    check("恆等式 avg=(勝率−打平勝率)×(W+L) 成立", ok)
    check("n<30 → 不下結論", G.group_stats([1.0] * 29)["verdict"] == "不下結論" and not G.group_stats([1.0] * 29)["enough"])
    check("n=30 → 可描述", G.group_stats([1.0, -1.0] * 15)["verdict"] == "可描述")
    check("全勝：賺賠比／打平勝率為 None（不除以 0）", G.group_stats([2.0] * 40)["payoff"] is None)
    check("空樣本不當機", G.group_stats([])["n"] == 0)
    check("NaN 被濾掉", G.group_stats([1.0, float("nan"), -1.0])["n"] == 2)


def test_variants():
    print("停損變體")
    check("固定停損直接回傳", G.sl_for_variant(G.VARIANTS["fixed7"], None) == 0.07)
    check("ATR 2× 夾下限 3%", G.sl_for_variant(G.VARIANTS["atr2"], 0.005) == 0.03)
    check("ATR 3× 夾上限 15%", G.sl_for_variant(G.VARIANTS["atr3"], 0.08) == 0.15)
    check("ATR 2× 中間值", abs(G.sl_for_variant(G.VARIANTS["atr2"], 0.025) - 0.05) < 1e-12)
    check("無 ATR → None", G.sl_for_variant(G.VARIANTS["atr2"], None) is None and G.sl_for_variant(G.VARIANTS["atr2"], float("nan")) is None)
    check("現行規則預設值未被本模組更動", bts.DEFAULT_CFG["tp"] == 0.12 and bts.DEFAULT_CFG["sl"] == 0.15 and bts.DEFAULT_CFG["breadth_min"] == 0.40)
    check("現行變體＝正式預設", G.VARIANTS["current"]["tp"] == bts.DEFAULT_CFG["tp"] and G.VARIANTS["current"]["sl"] == bts.DEFAULT_CFG["sl"])


def test_adjust():
    print("還原價重建")
    raw = pd.DataFrame({"Open": [100, 90.0], "High": [101, 91.0], "Low": [99, 89.0], "Close": [100, 90.0], "Adj Close": [95.0, 90.0]},
                       index=pd.bdate_range("2024-01-02", periods=2))
    a = G.adjust_from_raw(raw)
    check("除息前一日還原：O/H/L/C 皆 ×0.95", np.allclose(a.iloc[0][["Open", "High", "Low", "Close"]].values, [95, 95.95, 94.05, 95]), a.iloc[0].tolist())
    check("除息日後不變、無 Adj Close 欄", np.allclose(a.iloc[1][["Close"]].values, [90]) and "Adj Close" not in a.columns)


def flat_df(n=200, px=100.0):
    return mk([(px, px * 1.005, px * 0.995, px)] * n)


def test_build_events():
    print("build_events：無前視、冷卻、還原／未還原")
    df = flat_df(200)
    finder = {"chuan_e": lambda d: [(100, "t"), (110, "t"), (125, "t")]}
    idx = df.index
    reg = pd.Series(["bear"] * 100 + ["bull"] * 100, index=idx, dtype=object)   # 進場日(100)當天才轉多
    brd = pd.Series(np.linspace(0.2, 0.8, 200), index=idx)
    ev = G.build_events("1111", df, None, "半導體", reg, brd, finders=finder)
    check("冷卻 20 日：100 進、110 略、125 進", [e["date"] for e in ev] == [str(idx[100].date()), str(idx[125].date())], [e["date"] for e in ev])
    check("多空取訊號日（進場前一日 99＝bear），不取進場日", ev[0]["regime"] == "bear" and ev[1]["regime"] == "bull", [e["regime"] for e in ev])
    check("寬度取訊號日值", abs(ev[0]["breadth"] - brd.iloc[99]) < 1e-12)
    check("四個變體都有報酬、平盤→ 時間停損 −成本", set(ev[0]["rets"]) == set(G.VARIANTS) and abs(ev[0]["rets"]["current"] + G.round_trip_cost_pct()) < 1e-3, ev[0]["rets"])
    check("族群帶入", ev[0]["sector"] == "半導體")
    check("無未還原價 → rets_raw 為 None", ev[0]["rets_raw"] is None)
    # 尾端：進場後不足 20 日且未觸發 → 不計入
    ev2 = G.build_events("1111", df, None, "x", None, None, finders={"chuan_e": lambda d: [(190, "t")]})
    check("尾端未跑滿的交易不計入", ev2 == [])
    ev3 = G.build_events("1111", df, None, "x", None, None, finders={"chuan_e": lambda d: [(100, "t")]})
    check("沒有大盤／寬度資料 → regime／breadth 為 None（不亂填）", ev3 and ev3[0]["regime"] is None and ev3[0]["breadth"] is None)


def test_basis():
    print("還原價 vs 未還原顯示價")
    n = 200
    adj = flat_df(n)
    raw = adj.copy()
    raw.iloc[103:, :] = raw.iloc[103:, :]                  # 除息日 103：未還原價前面較高（除息前不還原）
    raw.iloc[:103, :] = raw.iloc[:103, :] * (1 / 0.90)     # 除息前價格比還原價高 11.1%，除息日起與還原價相同 → 未還原價在 103 出現 −10% 假跳空
    ev = G.build_events("2222", adj, raw, "x", None, None, finders={"chuan_e": lambda d: [(100, "t")]})
    check("有事件且帶未還原結果", len(ev) == 1 and ev[0]["rets_raw"])
    e = ev[0]
    check("還原價：平盤、時間停損（−成本）", e["why"]["fixed7"] == "time_stop", e["why"])
    check("未還原價：除息缺口被當下跌，固定 7% 停損誤觸 → 報酬明顯較差", e["rets_raw"]["fixed7"] < e["rets"]["fixed7"] - 5, (e["rets"]["fixed7"], e["rets_raw"]["fixed7"]))
    bc = G.basis_check(G.add_all_strategy(ev))
    check("basis_check 計出差異且排除 ALL 重複", bc["fixed7"]["n"] == 1 and bc["fixed7"]["avg_diff_pp"] < -5 and bc["fixed7"]["n_diff_over_0_5pp"] == 1, bc["fixed7"])


def synth_events(n_each=40):
    evs = []
    for strat in ("chuan_e", "pullback_burst"):
        for i in range(n_each):
            d = str((pd.Timestamp("2023-01-02") + pd.Timedelta(days=7 * i)).date())
            low = i % 4 == 0
            evs.append({"sym": f"{1000 + i}", "strategy": strat, "date": d, "sector": "A" if i % 2 else "B",
                        "regime": "bull" if i % 3 else "bear", "breadth": 0.2 if low else 0.7, "atr_pct": 2.0,
                        "rets": {"current": -8.0 if low else 5.0, "fixed7": -7.5 if low else 4.0, "atr2": -4.0 if low else 3.0, "atr3": -6.0 if low else 4.5},
                        "why": {}, "rets_raw": None})
    return evs


def test_matrix():
    print("矩陣：樣本不足標不下結論、閘門 keep／drop")
    evs = G.add_all_strategy(synth_events())
    split = G.default_split([e for e in evs if e["strategy"] != G.ALL])
    check("ALL 同檔同日去重：1000..1039 各一次 → 40 筆", sum(1 for e in evs if e["strategy"] == G.ALL) == 40)
    rows = G.stoploss_matrix(evs, split)
    base = next(r for r in rows if r["strategy"] == "chuan_e" and r["variant"] == "current" and r["sector"] is None and r["regime"] is None)
    check("總體 n=40 足夠", base["n"] == 40 and base["enough"], base)
    sec_bear = next(r for r in rows if r["strategy"] == "chuan_e" and r["variant"] == "current" and r["sector"] == "A" and r["regime"] == "bear")
    check("族群×多空小格 n<30 → 不下結論", sec_bear["verdict"] == "不下結論" and sec_bear["n"] < 30, sec_bear)
    check("所有 n<30 的列一律不下結論", all(r["verdict"] == "不下結論" for r in rows if r["n"] < 30))
    check("每列都有賺賠比／打平勝率欄位", all(("payoff" in r and "breakeven_win_pct" in r) for r in rows if r["n"] > 0 and r.get("avg_loss") is not None))
    check("含 不重複進場日數 與 樣本內外", "days" in base and "is" in base and "oos" in base)
    br_rows = G.breadth_matrix(evs, split=split)
    r0 = next(r for r in br_rows if r["strategy"] == "chuan_e" and r["breadth_min"] == 0.0 and r["variant"] == "current")
    r4 = next(r for r in br_rows if r["strategy"] == "chuan_e" and r["breadth_min"] == 0.4 and r["variant"] == "current")
    check("門檻 0 全放行", r0["keep"]["n"] == 40 and r0["drop"]["n"] == 0)
    check("門檻 0.4：低寬度 10 筆被擋、30 筆放行", r4["keep"]["n"] == 30 and r4["drop"]["n"] == 10, (r4["keep"]["n"], r4["drop"]["n"]))
    check("被擋組 n=10<30 → 不下結論", r4["drop"]["verdict"] == "不下結論")
    check("被擋組平均 −8、放行組平均 +5（閘門擋掉壞交易可見）", abs(r4["drop"]["avg"] + 8) < 1e-9 and abs(r4["keep"]["avg"] - 5) < 1e-9)
    notes = G.conclusions(rows, br_rows)
    check("寬度結論因被擋組 n<30 而不下結論", any("寬度<40%" in x and "不下結論" in x for x in notes), notes)
    # 缺寬度值
    e2 = [dict(e, breadth=None) for e in synth_events()]
    r = next(x for x in G.breadth_matrix(e2) if x["strategy"] == "chuan_e" and x["breadth_min"] == 0.4 and x["variant"] == "current")
    check("寬度缺值：門檻>0 時不放行也不算被擋，記 missing", r["keep"]["n"] == 0 and r["drop"]["n"] == 0 and r["missing_breadth"] == 40)
    big = synth_events(120)
    rows_b = G.stoploss_matrix(G.add_all_strategy(big), G.default_split(big))
    notes_b = G.conclusions(rows_b, G.breadth_matrix(G.add_all_strategy(big), split=G.default_split(big)))
    check("樣本充足時結論有描述句且帶「非因果／未做顯著性」免責", any("僅為描述" in x for x in notes_b), notes_b)


def test_signals_and_regime():
    print("訊號函式與大盤多空")
    adj, _ = R.synthetic_pair(n=3, days=700)
    df = next(iter(adj.values()))
    a, b = G.find_chuan_e(df), G.find_pullback(df)
    check("真實 finder 在合成資料上可執行、回傳 (index, kind) 清單", isinstance(a, list) and isinstance(b, list))
    s = pd.Series(np.r_[np.linspace(100, 150, 80), np.linspace(150, 100, 80)], index=pd.bdate_range("2023-01-02", periods=160))
    r = G.market_regime(s)
    check("MA60 前為 None；上升段 bull；下跌段後期 bear", r.iloc[10] is None and r.iloc[70] == "bull" and r.iloc[-1] == "bear", (r.iloc[10], r.iloc[70], r.iloc[-1]))


def test_runner():
    print("執行階段（合成端到端、快取掃描）")
    with tempfile.TemporaryDirectory() as td:
        out = os.path.join(td, "r.json")
        rc = R.main(["--synthetic", "--json-out", out])
        check("合成端到端 exit 0", rc == 0)
        rep = json.load(open(out, encoding="utf-8"))
        check("報告含成本假設與價格口徑", rep["assumptions"]["cost_round_trip_pct"] == 0.585 and "還原價" in rep["assumptions"]["price_basis"])
        check("報告含停損矩陣／寬度矩陣／未還原對照／結論", all(k in rep for k in ("stoploss_matrix", "breadth_matrix", "basis_check", "conclusions")))
        check("合成報告標記 synthetic", rep["meta"]["synthetic"] is True)
        check("未還原對照有資料（合成股息）", rep["basis_check"]["fixed7"]["n"] > 0)
        old = bt_cache.DIR
        try:
            bt_cache.DIR = os.path.join(td, "cache")
            for sym in ("2330", "2317_O", "abcd"):
                os.makedirs(os.path.join(bt_cache.DIR, "daily_adj"), exist_ok=True)
                open(os.path.join(bt_cache.DIR, "daily_adj", f"{sym}_6.pkl"), "wb").close()
            check("symbols_from_cache：4 位數代號、上櫃後綴還原、忽略雜檔", R.symbols_from_cache(6) == ["2317", "2330"], R.symbols_from_cache(6))
            bt_cache.DIR = os.path.join(td, "none")
            check("無快取目錄 → 空清單", R.symbols_from_cache(6) == [])
        finally:
            bt_cache.DIR = old


if __name__ == "__main__":
    for fn in (test_cost, test_simulate_hand, test_simulate_parity, test_stats, test_variants, test_adjust, test_build_events,
               test_basis, test_matrix, test_signals_and_regime, test_runner):
        fn()
    print(f"\n{'全部通過' if not FAILS else '失敗：' + str(FAILS)}")
    sys.exit(1 if FAILS else 0)
