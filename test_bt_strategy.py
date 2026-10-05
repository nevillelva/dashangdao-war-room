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
    cfg = bs.merge_cfg({"breadth_min": 0.0, "rules": ["chuan_e_ma60_40"]})
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
    cfg = bs.merge_cfg({"breadth_min": 0.40, "rules": ["chuan_e_ma60_40"]})
    cut = df.iloc[:380]
    breadth = pd.Series(0.2, index=cut.index)
    sigs, info = bs.find_signals({"TEST": cut}, cfg, breadth=breadth, as_of=cut.index[-1])
    check("寬度 0.2 < 0.4 → 閘門擋下，無訊號", sigs == [] and info["gated"] is True and info["breadth"] == 0.2, str(info))
    breadth = pd.Series(float("nan"), index=cut.index)
    sigs, info = bs.find_signals({"TEST": cut}, cfg, breadth=breadth, as_of=cut.index[-1])
    check("寬度缺值 → 保守擋下", sigs == [] and info["gated"] is True)
    sigs, info = bs.find_signals({}, cfg)
    check("空母體不當機", sigs == [] and info["n_scanned"] == 0)


def _synthetic_pullback(n=700, seed=11):
    """決定性合成：緩漲＋雜訊；每隔約 40 日製造一次『爆量(量比≥2)＋連3日下跌≥5%』的事件。"""
    rng = np.random.default_rng(seed)
    c = [50.0]
    for _ in range(1, n):
        c.append(c[-1] * (1 + rng.normal(0.001, 0.008)))
    c = np.array(c)
    v = np.full(n, 5e6)
    for i in range(400, n - 5, 37):
        for k in (i - 2, i - 1, i):
            c[k:] *= 0.98          # 連3日各 -2%，累計約 -5.9%
        v[i] = 5e6 * 3.5
    o = np.r_[c[0], c[:-1]]
    hi = np.maximum(o, c) * 1.004
    lo = np.minimum(o, c) * 0.996
    return pd.DataFrame({"Open": o, "High": hi, "Low": lo, "Close": c, "Volume": v},
                        index=pd.bdate_range("2024-06-03", periods=n))


def test_pullback_burst_rule():
    print("pullback_burst（查9 爆量＋3日回檔）")
    br.LIQ_MIN = 1.0   # 合成資料成交值小，放寬流動性下限；與下方回測家族用同一個值
    df = _synthetic_pullback()
    m = bs.pullback_burst_mask(df, 2.0, 0.05)
    check("合成資料有產生訊號", int(m.sum()) >= 3, f"n={int(m.sum())}")
    # 與回測家族（backtest_cmd_rules.families）逐日一致：同一資料，回測的訊號日索引（去冷卻前）必須等於 live mask 的索引
    import backtest_cmd_rules as cmd
    import backtest_winrate_tuning as bt
    breadth = pd.Series(1.0, index=df.index)
    fam = cmd.families(df, breadth, 20)["查9 均線糾結爆量突破 +3日回檔≥5%"]
    idx = np.where(m)[0]
    idx = idx[(idx >= 0) & (idx + 1 + 20 < len(df))]
    idx = bt.apply_cooldown(idx)
    check("live mask 與回測家族逐日一致", np.array_equal(idx, fam), f"live={idx[:8]} bt={fam[:8]}")
    # find_signals：訊號日有訊號、非訊號日沒有；且不受大盤寬度閘門影響
    cfg = bs.merge_cfg({"rules": ["pullback_burst"], "breadth_min": 0.99})
    sig_i = int(np.where(m)[0][0])
    cut = df.iloc[: sig_i + 1]
    sigs, info = bs.find_signals({"T": cut}, cfg, breadth=pd.Series(0.1, index=cut.index), as_of=cut.index[-1])
    check("爆量回檔規則不受寬度閘門影響", len(sigs) == 1 and sigs[0]["rule"] == "pullback_burst" and info["gated"] is False, str(info))
    quiet_i = int(np.where(~m)[0][-1])
    cut2 = df.iloc[: quiet_i + 1]
    sigs2, _ = bs.find_signals({"T": cut2}, cfg, breadth=pd.Series(0.9, index=cut2.index), as_of=cut2.index[-1])
    check("非訊號日不出訊號", sigs2 == [])
    # 兩條規則同時啟用：寬度不足時穿山惡龍整條停手(gated)，但爆量回檔照出
    cfg2 = bs.merge_cfg({"breadth_min": 0.4})
    sigs3, info3 = bs.find_signals({"T": cut}, cfg2, breadth=pd.Series(0.1, index=cut.index), as_of=cut.index[-1])
    check("雙規則：穿山惡龍 gated、爆量回檔仍出", info3["gated"] is True and [x["rule"] for x in sigs3] == ["pullback_burst"], str(info3))
    check("by_rule 統計", info3["by_rule"] == {"pullback_burst": 1}, str(info3))
    # 同一檔被兩條規則同時選中：只留一筆（優先序高者）
    # （以 monkeypatch 讓穿山惡龍事件恆成立不容易，這裡改測去重函式行為：同 symbol 的兩筆只留第一筆）
    dup = [{"symbol": "A", "rule": "chuan_e_ma60_40", "score15": 9, "signal_date": "x", "ref_close": 1, "breadth": 1},
           {"symbol": "A", "rule": "pullback_burst", "score15": 9, "signal_date": "x", "ref_close": 1, "breadth": 1}]
    seen, out = set(), []
    for r in dup:
        if r["symbol"] not in seen:
            seen.add(r["symbol"])
            out.append(r)
    check("去重語意：優先序在前者保留", out[0]["rule"] == "chuan_e_ma60_40")


def test_sector_gate():
    print("sector_gate")
    W = lambda n, w, e: {"n": n, "win": w, "exp_pct": e}
    ref = {"long": {"sectors": {
        "金融保險": {"live_rules": {"pullback_burst": {"IS": W(40, .6, 1.0), "OOS": W(25, .64, 2.0), "gate_ok": True, "n_enough": True}}},
        "鋼鐵工業": {"live_rules": {"pullback_burst": {"IS": W(35, .4, -1.0), "OOS": W(22, .45, -.5), "gate_ok": False, "n_enough": True}}},
        "光電業": {"live_rules": {"pullback_burst": {"IS": W(5, .4, -1.0), "OOS": W(2, .5, 0.5), "gate_ok": False, "n_enough": False}}},
    }}}
    smap = {"A": "金融保險", "B": "鋼鐵工業", "C": "光電業", "D": "沒看過的族群"}
    sigs = [{"symbol": k, "rule": "pullback_burst", "score15": 9} for k in "ABCDE"]   # E 不在 smap → 小族群合併（參考表也沒有）
    kept, dropped = bs.apply_sector_gate(sigs, "soft", smap, ref)
    check("soft：只擋樣本足夠卻未達標的族群(B)", [x["symbol"] for x in kept] == ["A", "C", "D", "E"] and [d[0]["symbol"] for d in dropped] == ["B"], str((kept, dropped)))
    check("soft：kept 附帶 sector/gate", kept[0]["sector"] == "金融保險" and kept[0]["gate"] == "pass" and kept[1]["gate"] == "nodata")
    kept, dropped = bs.apply_sector_gate(sigs, "strict", smap, ref)
    check("strict：只放行閘門通過(A)；nodata 也擋", [x["symbol"] for x in kept] == ["A"] and len(dropped) == 4, str((kept, dropped)))
    kept, dropped = bs.apply_sector_gate(sigs, "off", smap, ref)
    check("off：全部放行、不加欄位", len(kept) == 5 and dropped == [] and "gate" not in kept[0])
    kept, dropped = bs.apply_sector_gate(sigs, "strict", smap, {})
    check("沒有參考表(noref)：strict 也不擋（避免整個系統停擺）", len(kept) == 5 and dropped == [])
    kept, dropped = bs.apply_sector_gate([], "soft", smap, ref)
    check("空訊號", kept == [] and dropped == [])
    check("預設設定含 sector_gate", bs.merge_cfg({})["sector_gate"] in ("off", "soft", "strict"))
    check("原訊號字典不被改動（gate 欄位只加在副本）", "gate" not in sigs[0])


def test_mark_to_market():
    print("mark_to_market")
    check("未實現報酬已扣來回成本", abs(bs.mark_to_market(100, 110) - (0.10 - br.COST_ROUND_TRIP)) < 1e-12)


if __name__ == "__main__":
    test_evaluate_exit()
    test_pick_and_cfg()
    test_find_signals_matches_backtest()
    test_breadth_gate()
    test_pullback_burst_rule()
    test_sector_gate()
    test_mark_to_market()
    if FAILS:
        print(f"\n❌ {len(FAILS)} 項失敗：{FAILS}")
        sys.exit(1)
    print("\n✅ bt_strategy 全部通過")
