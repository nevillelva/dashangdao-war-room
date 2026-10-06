#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_regime_gate.py —— bt_strategy 的盤勢閘門（regime_gate_status / apply_regime_gate）：判定邏輯與模式切換。離線。"""
import bt_strategy as bts

bad = 0


def check(ok, msg):
    global bad
    print("✅" if ok else "❌", msg)
    bad += (not ok)


def cell(isw, oow, ok, enough=True):
    return {"IS": {"n": 300, "win": isw, "exp_pct": 1.0 if ok else -1.0}, "OOS": {"n": 300, "win": oow, "exp_pct": 1.0 if ok else -1.0},
            "gate_ok": ok, "n_enough": enough}


REF = {"regimes": {"dn60": "指數<MA60", "up60": "指數>MA60", "up20": "指數>MA20", "calm": "波動低於近一年中位", "wild": "波動高於近一年中位",
                   "dd10_deep": "指數距120日高≥10%(修正中)", "b50": "寬度≥50%"},
       "long": {"sectors": {bts.REGIME_REF_SECTOR: {"rules": {
           "pullback_burst": {"all": cell(.587, .575, True), "dn60": cell(.72, .69, True), "dd10_deep": cell(.86, .81, True), "wild": cell(.67, .63, True),
                              "up60": cell(.47, .44, False), "up20": cell(.48, .45, False), "calm": cell(.45, .41, False), "b50": cell(.46, .43, False, enough=False)},
           "chuan_e_ma60_40": {"all": cell(.50, .54, True), "up60": cell(.50, .54, True), "calm": cell(.49, .53, False), "wild": cell(.52, .54, False)}}}}}}

SIG = [{"symbol": "1101", "rule": "pullback_burst", "score15": 10}, {"symbol": "1102", "rule": "chuan_e_ma60_40", "score15": 9}]

# 今天（10/6 實況）：站上 MA60、站上 MA20、低波動 → 爆量回檔不利
today = {"all": True, "up20": True, "up60": True, "calm": True, "b50": True}
st, note = bts.regime_gate_status(REF, "pullback_burst", today)
check(st == "fail" and "盤勢不利" in note and "指數>MA60" in note, f"多頭偏強＋低波動：爆量回檔 → fail（{note}）")
st, note = bts.regime_gate_status(REF, "chuan_e_ma60_40", today)
check(st == "pass", f"同樣盤勢：穿山惡龍 → pass（up60 達標；calm 不達標也不擋）：{note}")

# 大盤修正：爆量回檔有利
weak = {"all": True, "dn60": True, "dd10_deep": True, "wild": True}
st, note = bts.regime_gate_status(REF, "pullback_burst", weak)
check(st == "pass" and "盤勢有利" in note, f"修正中＋高波動：爆量回檔 → pass（{note}）")

# 好壞並存：只要有一個達標旗標就放行（不被同時成立的壞旗標否決）
mixed = {"all": True, "up20": True, "wild": True}
check(bts.regime_gate_status(REF, "pullback_burst", mixed)[0] == "pass", "好壞並存（up20 不利、wild 有利）→ pass")

# 只有樣本不足的旗標 → nodata；沒有任何旗標 → nodata
check(bts.regime_gate_status(REF, "pullback_burst", {"all": True, "b50": True})[0] == "nodata", "只有樣本不足的旗標 → nodata（不擋）")
check(bts.regime_gate_status(REF, "pullback_burst", {"all": True})[0] == "nodata", "只有 all → nodata")
check(bts.regime_gate_status({}, "pullback_burst", today)[0] == "noref" and bts.regime_gate_status(None, "x", today)[0] == "noref", "沒有參考表 → noref")
check(bts.regime_gate_status(REF, "unknown_rule", today)[0] == "noref", "參考表沒有這條規則 → noref")

# apply
kept, dropped = bts.apply_regime_gate(SIG, "soft", today, REF)
check([s["symbol"] for s in kept] == ["1102"] and [d[0]["symbol"] for d in dropped] == ["1101"] and dropped[0][1] == "fail", "soft：擋掉爆量回檔、放行穿山惡龍")
check(kept[0]["regime_gate"] == "pass" and "regime_note" in kept[0], "放行的訊號記錄 regime_gate 狀態")
kept, dropped = bts.apply_regime_gate(SIG, "off", today, REF)
check(len(kept) == 2 and not dropped, "off：全放行")
kept, dropped = bts.apply_regime_gate(SIG, "soft", today, {})
check(len(kept) == 2 and not dropped, "沒有參考表：soft 全放行（缺表不讓系統停擺）")
kept, dropped = bts.apply_regime_gate(SIG, "soft", {}, REF)
check(len(kept) == 2 and not dropped, "沒有旗標資料：全放行")
kept, dropped = bts.apply_regime_gate(SIG, "strict", {"all": True, "b50": True}, REF)
check(len(kept) == 0 and len(dropped) == 2, "strict：nodata 也擋")
kept, dropped = bts.apply_regime_gate(SIG, "soft", {"all": True, "b50": True}, REF)
check(len(kept) == 2 and not dropped, "soft：nodata 放行")
check(bts.merge_cfg({})["regime_gate"] == "soft" and bts.merge_cfg({"regime_gate": "off"})["regime_gate"] == "off", "設定預設 soft、可由 bt_strategy_config 改成 off")

print(f"\n{'全部通過' if not bad else str(bad) + ' 項失敗'}")
raise SystemExit(1 if bad else 0)
