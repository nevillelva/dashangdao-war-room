"""【2026-10-17】查指令名實相符版規則：逐條正反例。"""
import sys
import numpy as np
import pandas as pd
import warroom_core as W
W.get_threshold = lambda k: W.DEFAULT_THRESHOLDS[k]
ok = True
def chk(n, c):
    global ok
    print(("PASS " if c else "FAIL ") + n)
    ok = ok and c
E = W.evaluate_single_condition
base = {"price": 100, "ma60": 90, "vol_ratio": 2.5, "is_first_red": True, "k_val": 60, "kd_cross_today": True, "uptrend": True}
chk("查1 全條件成立", E("查1.", base))
chk("查1 K>D 但不是當天交叉 → 不過", not E("查1.", dict(base, kd_cross_today=False)))
chk("查1 不在上升趨勢 → 不過", not E("查1.", dict(base, uptrend=False)))
c2 = {"price": 100, "ma60": 90, "vol_ratio": 0.8, "ma60_rising": True, "near_ma20_pct": 2.0}
chk("查2 貼MA20量縮", E("查2.", c2))
chk("查2 量增 → 不過", not E("查2.", dict(c2, vol_ratio=1.3)))
chk("查2 離MA20太遠 → 不過", not E("查2.", dict(c2, near_ma20_pct=8.0)))
c3 = {"value_score": 70, "landmine": False, "rev_cycle_up": True}
chk("查3 價值＋循環回升", E("查3.", c3))
chk("查3 沒有循環回升 → 不過", not E("查3.", dict(c3, rev_cycle_up=False)))
chk("查5 缺融資資料 → 不過", not E("查5.", {"f_buy": 100, "has_margin": False, "margin_diff": 0}))
chk("查5 外資買＋融資減", E("查5.", {"f_buy": 100, "has_margin": True, "margin_diff": -5}))
c6 = {"rev_yoy": 30, "rev_mom": 5}
chk("查6 雙增", E("查6.", c6))
chk("查6 月增≤0 → 不過", not E("查6.", dict(c6, rev_mom=-1)))
chk("查6 年增不到 20% → 不過", not E("查6.", dict(c6, rev_yoy=15)))
chk("角色標示", W.cmd_display_name("查3.價值投資與循環") == "查3.價值投資與循環（做多主力）" and W.cmd_display_name("查8.昨日強勢動能延續").endswith("（隔日沖倒貨・第3天開高當沖空）") and W.cmd_display_name("查5.x") == "查5.x")
chk("查8 昨強今續漲", E("查8.", {"is_yesterday_strong": True, "today_up": True}))
chk("查8 今天沒續漲 → 不過", not E("查8.", {"is_yesterday_strong": True, "today_up": False}))
c9 = {"ma_converge_prev": True, "vol_ratio": 2.2, "above_all_ma": True, "is_red": True}
chk("查9 糾結＋爆量＋站上三線收紅", E("查9.", c9))
chk("查9 只有爆量 → 不過", not E("查9.", {"vol_ratio": 3.0}))
chk("查10 缺融資資料 → 不過", not E("查10.", {"vol_ratio": 0.5, "has_margin": False}))
W.DEFAULT_THRESHOLDS["div_yield_min"] = 6.0
chk("查11 門檻可調：5% 在 6% 門檻下不過", not E("查11.", {"div_yield": 5.0}))
W.DEFAULT_THRESHOLDS["div_yield_min"] = 4.5
chk("查11 預設 4.5%", E("查11.", {"div_yield": 5.0}))
chk("循環：近3月比前3月改善≥5點", W.rev_cycle_up([-10, -8, -6, 0, 2, 4]) is True)
chk("循環：不足6期回 None", W.rev_cycle_up([1, 2, 3]) is None)
idx = pd.bdate_range("2025-01-01", periods=120)
c = pd.Series(np.linspace(100, 130, 120), index=idx)
h = pd.DataFrame({"Open": c * 0.995, "High": c * 1.01, "Low": c * 0.99, "Close": c, "Volume": 1000.0}, index=idx)
f = W.compute_rule_features(h)
chk("特徵：穩定上漲 → 上升趨勢、站上三線、續漲", f["uptrend"] and f["above_all_ma"] and f["today_up"])
chk("特徵：資料不足 → 全 None", W.compute_rule_features(h.tail(30))["uptrend"] is None)
sys.exit(0 if ok else 1)
