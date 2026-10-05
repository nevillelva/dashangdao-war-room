#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_bt_strategy.py —— bt_strategy.py 純函式單元測試（不連網、不碰資料庫）。
執行：python test_bt_strategy.py   （全部通過 exit 0）"""
import sys
import numpy as np
import pandas as pd

import backtest_rules as br
import bt_strategy as bs

FAILS = []


def check(name, cond, extra=""):
    if cond:
        print(f"  ✅ {name}")
    else:
        print(f"  ❌ {name} {extra}")
        FAILS.append(name)


def mk_rows(ohlc, start="2026-01-05"):
    idx = pd.bdate_range(start, periods=len(ohlc))
    return pd.DataFrame(ohlc, index=idx, columns=["Open", "High", "Low", "Close"])


def test_evaluate_exit():
    print("evaluate_exit")
    cost = br.COST_ROUND_TRIP
    # 停利：第 2 天最高 +13%
    rows = mk_rows([(100, 101, 99, 100), (100, 113, 100, 112)])
    r = bs.evaluate_exit(rows, 100, 0.12, 0.15, 20)
    check("TP 觸發", r and r["reason"] == "take_profit" and abs(r["gross_ret"] - 0.12) < 1e-9, str(r))
    check("TP 淨報酬已扣成本", r and abs(r["net_ret"] - (0.12 - cost)) < 1e-9)
    check("TP 持有天數=2", r and r["days_held"] == 2)
    # 停損：第 3 天最低 -16%
    rows = mk_rows([(100, 101, 99, 100), (100, 102, 98, 99), (99, 99, 84, 90)])
    r = bs.evaluate_exit(rows, 100, 0.12, 0.15, 20)
    check("SL 觸發", r and r["reason"] == "stop_loss" and abs(r["gross_ret"] + 0.15) < 1e-9, str(r))
    # 同日最高/最低都觸及 → 先停損
    rows = mk_rows([(100, 120, 80, 100)])
    r = bs.evaluate_exit(rows, 100, 0.12, 0.15, 20)
    check("同日兩者皆觸及 → 停損優先", r and r["reason"] == "stop_loss", str(r))
    # 跳空跌破停損：開盤 -20% → 以開盤價成交（比 -15% 更差）
    rows = mk_rows([(100, 101, 99, 100), (80, 82, 78, 79)])
    r = bs.evaluate_exit(rows, 100, 0.12, 0.15, 20)
    check("向下跳空以開盤價成交", r and r["reason"] == "stop_loss" and abs(r["gross_ret"] + 0.20) < 1e-9, str(r))
    # 跳空越過停利：開盤 +15% → 以開盤價成交（比 +12% 更好）
    rows = mk_rows([(100, 101, 99, 100), (115, 118, 114, 116)])
    r = bs.evaluate_exit(rows, 100, 0.12, 0.15, 20)
    check("向上跳空以開盤價成交", r and r["reason"] == "take_profit" and abs(r["gross_ret"] - 0.15) < 1e-9, str(r))
    # 進場日(k=0)：開盤=進場價，不套用跳空
    rows = mk_rows([(100, 100, 83, 90)])
    r = bs.evaluate_exit(rows, 100, 0.12, 0.15, 20)
    check("進場日當天最低觸及停損 → -15%", r and abs(r["gross_ret"] + 0.15) < 1e-9, str(r))
    # 時間停損：20 根沒觸發，第 20 根收盤出
    rows = mk_rows([(100, 105, 95, 101)] * 25)
    r = bs.evaluate_exit(rows, 100, 0.12, 0.15, 20)
    check("滿 20 日收盤出", r and r["reason"] == "time_stop" and r["days_held"] == 20 and abs(r["gross_ret"] - 0.01) < 1e-9, str(r))
    # 尚未出場
    rows = mk_rows([(100, 105, 95, 101)] * 5)
    check("未達條件 → None", bs.evaluate_exit(rows, 100, 0.12, 0.15, 20) is None)
    # 與回測 sim 一致性：同一批隨機路徑，逐筆比對 backtest_entry_final.sim_cfg（若可用）
    try:
        import backtest_entry_final as bef
        rng = np.random.default_rng(7)
        diffs = 0
        tested = 0
        for _ in range(300):
            n = 40
            ret = rng.normal(0, 0.03, n)
            close = 100 * np.cumprod(1 + ret)
            op = close * (1 + rng.normal(0, 0.01, n))
            hi = np.maximum(op, close) * (1 + np.abs(rng.normal(0, 0.012, n)))
            lo = np.minimum(op, close) * (1 - np.abs(rng.normal(0, 0.012, n)))
            df = pd.DataFrame({"Open": op, "High": hi, "Low": lo, "Close": close},
                              index=pd.bdate_range("2025-01-02", periods=n))
            # entry_i=1：隔日開盤進場（第 1 根 K 的 Open）
            E = float(df["Open"].iloc[1])
            mine = bs.evaluate_exit(df.iloc[1:], E, 0.12, 0.15, 20)
            if not hasattr(bef, "sim_cfg"):
                raise AttributeError("no sim_cfg")
            ref_ret, ref_days, _e = bef.sim_cfg(df, np.array([0]), 0.12, 0.15, 20)
            tested += 1
            if mine is None:
                # 回測以 hold 根收盤強制出場；live 版 40 根資料一定出得了場 → 不該為 None
                diffs += 1
            elif abs(mine["net_ret"] - float(ref_ret[0])) > 1e-9 or mine["days_held"] != int(ref_days[0]):
                diffs += 1
        check(f"與 backtest_entry_final.sim_cfg 逐筆一致（{tested} 筆）", diffs == 0, f"diffs={diffs}")
    except Exception as e:  # sim_cfg 簽名不同或不存在 → 只提示，不當成失敗
        print(f"  ⚠️ 略過與 sim_cfg 的逐筆比對：{type(e).__name__}: {e}")


def test_pick_and_cfg():
    print("pick_new_entries / merge_cfg")
    sigs = [{"symbol": s, "score15": 15 - i} for i, s in enumerate(["A", "B", "C", "D", "E"])]
    check("名額 10、已持 8、每日上限 3 → 只挑 2", [x["symbol"] for x in bs.pick_new_entries(sigs, 8, 10, 3)] == ["A", "B"])
    check("每日上限 3", len(bs.pick_new_entries(sigs, 0, 10, 3)) == 3)
    check("名額滿 → 0", bs.pick_new_entries(sigs, 10, 10, 3) == [])
    check("冷卻/已持有的標的略過", [x["symbol"] for x in bs.pick_new_entries(sigs, 0, 10, 3, recently_traded={"A", "C"})] == ["B", "D", "E"])
    cfg = bs.merge_cfg({"tp": 0.1, "unknown_key": 1, "sl": None})
    check("merge_cfg 覆蓋已知鍵、忽略未知鍵與 None", cfg["tp"] == 0.1 and "unknown_key" not in cfg and cfg["sl"] == bs.DEFAULT_CFG["sl"])
    check("merge_cfg(None) = 預設", bs.merge_cfg(None) == bs.DEFAULT_CFG)
    check("舊規則做多預設停用", bs.DEFAULT_CFG["old_long_enabled"] is False)


def _synthetic_chuan_e(n=420, seed=1, gap=2, drift=0.002):
    """決定性合成：長期盤整 → 40 日暴漲(~+75%) → 連跌跌破 MA60 → 盤整 gap 日 → 大紅 K(≥3%)站回 MA60。
    gap<=3 為『快進』型；gap 大且之後維持在 MA60 之上為『慢進』型。"""
    rng = np.random.default_rng(seed)
    c = [50.0]
    for _ in range(1, 250):
        c.append(c[-1] * (1 + rng.normal(0, 0.004)))
    for _ in range(40):
        c.append(c[-1] * 1.014)
    state, wait = "down", 0
    while len(c) < n:
        ma = pd.Series(c).rolling(60).mean().iloc[-1]
        if state == "down":
            c.append(c[-1] * 0.985)
            ma2 = pd.Series(c).rolling(60).mean().iloc[-1]
            if c[-1] < ma2 and c[-2] >= ma:
                state, wait = "wait", gap - 1
        elif state == "wait":
            c.append(c[-1] * (1 + rng.normal(-0.001, 0.002)))
            wait -= 1
            if wait <= 0:
                state = "jump"
        elif state == "jump":
            ma = pd.Series(c).rolling(60).mean().iloc[-1]
            c.append(max(ma * 1.01, c[-1] * 1.04))
            state = "after"
        else:
            c.append(c[-1] * (1 + rng.normal(drift, 0.006)))
    c = np.array(c[:n])
    o = np.r_[c[0], c[:-1]]
    up = c > np.r_[c[0], c[:-1]] * 1.03
    o = np.where(up, c / 1.045, o)
    hi = np.maximum(o, c) * 1.004
    lo = np.minimum(o, c) * 0.996
    return pd.DataFrame({"Open": o, "High": hi, "Low": lo, "Close": c, "Volume": np.full(n, 5e6)},
                        index=pd.bdate_range("2024-06-03", periods=n))


def test_find_signals_matches_backtest():
    print("find_signals 與 backtest_rules.find_chuan_e_events 一致")
    cfg = bs.merge_cfg({"breadth_min": 0.0})
    total_events = 0
    mismatch = 0
    for seed, gap in [(1, 1), (1, 2), (1, 3), (2, 2), (3, 3), (4, 5), (5, 8)]:
        df = _synthetic_chuan_e(seed=seed, gap=gap, drift=0.004)
        evs = br.find_chuan_e_events(df, cfg["ma_n"], cfg["rally_min"], cfg["body_min"], cfg["fast_days"],
                                     cfg["slow_wait"], entries_only=True)
        total_events += len(evs)
        # 對每一個事件：把資料截到「訊號日」(= entry_i-1)，live 版必須在該日找到
        for entry_i, _kind in evs:
            sig_i = entry_i - 1
            cut = df.iloc[: sig_i + 1]
            if len(cut) < 300:
                continue
            breadth = pd.Series(1.0, index=cut.index)
            sigs, info = bs.find_signals({"TEST": cut}, cfg, breadth=breadth, as_of=cut.index[-1])
            if not sigs:
                mismatch += 1
        # 反向：沒有事件的日子（隨機抽幾天）live 版不得出訊號
        ev_sig_days = {e[0] - 1 for e in evs}
        for sig_i in (320, 350, 380, 410):
            if sig_i in ev_sig_days or sig_i >= len(df):
                continue
            cut = df.iloc[: sig_i + 1]
            breadth = pd.Series(1.0, index=cut.index)
            sigs, _ = bs.find_signals({"TEST": cut}, cfg, breadth=breadth, as_of=cut.index[-1])
            if sigs:
                mismatch += 1
    print(f"  （合成資料共 {total_events} 個回測事件）")
    check("live 訊號日與回測事件 1:1 對應（無漏無多）", mismatch == 0, f"mismatch={mismatch}")
    check("合成資料確實產生了回測事件（避免測試空轉）", total_events >= 3, f"events={total_events}")


def test_breadth_gate():
    print("大盤寬度閘門")
    df = _synthetic_chuan_e(seed=5)
    cfg = bs.merge_cfg({"breadth_min": 0.40})
    cut = df.iloc[:380]
    breadth = pd.Series(0.2, index=cut.index)
    sigs, info = bs.find_signals({"TEST": cut}, cfg, breadth=breadth, as_of=cut.index[-1])
    check("寬度 0.2 < 0.4 → 閘門擋下，無訊號", sigs == [] and info["gated"] is True and info["breadth"] == 0.2, str(info))
    breadth = pd.Series(float("nan"), index=cut.index)
    sigs, info = bs.find_signals({"TEST": cut}, cfg, breadth=breadth, as_of=cut.index[-1])
    check("寬度缺值 → 保守擋下", sigs == [] and info["gated"] is True)
    sigs, info = bs.find_signals({}, cfg)
    check("空母體不當機", sigs == [] and info["n_scanned"] == 0)


def test_mark_to_market():
    print("mark_to_market")
    check("未實現報酬已扣來回成本", abs(bs.mark_to_market(100, 110) - (0.10 - br.COST_ROUND_TRIP)) < 1e-12)


if __name__ == "__main__":
    test_evaluate_exit()
    test_pick_and_cfg()
    test_find_signals_matches_backtest()
    test_breadth_gate()
    test_mark_to_market()
    if FAILS:
        print(f"\n❌ {len(FAILS)} 項失敗：{FAILS}")
        sys.exit(1)
    print("\n✅ bt_strategy 全部通過")
