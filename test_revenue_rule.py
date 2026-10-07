"""營收動能實盤規則（bt_strategy 的純函式部分）單元測試。python3 test_revenue_rule.py"""
import numpy as np
import pandas as pd
import bt_strategy as bts

FAIL = []


def check(name, cond, extra=""):
    if not cond:
        FAIL.append(name)
        print("❌", name, extra)
    else:
        print("✅", name)


check("目標營收月份：10/12 → 9 月、10/7 → 8 月、1/5 → 前年 11 月",
      bts.revenue_target_month("2026-10-12") == (2026, 9) and bts.revenue_target_month("2026-10-07") == (2026, 8)
      and bts.revenue_target_month("2027-01-05") == (2026, 11) and bts.revenue_target_month("2027-01-11") == (2026, 12))

days = pd.bdate_range("2025-01-01", "2026-10-30")
sd = bts.revenue_signal_day(days, "2026-10-11")
check("訊號日＝基準日(週日)之後第一個交易日 10/12", str(sd.date()) == "2026-10-12")
ok0, _ = bts.revenue_window_ok(days, "2026-10-12", 2026, 9, 2)
ok2, _ = bts.revenue_window_ok(days, "2026-10-14", 2026, 9, 2)
ok3, _ = bts.revenue_window_ok(days, "2026-10-15", 2026, 9, 2)
okb, _ = bts.revenue_window_ok(days, "2026-10-09", 2026, 9, 2)
check("時窗：訊號日、+2 日內可進場；+3 日、訊號日前不行", ok0 and ok2 and not ok3 and not okb, (ok0, ok2, ok3, okb))

f = lambda **k: dict({"yoy": 0.0, "yoy3": None, "mom": None, "new_high": False}, **k)
check("條件A：新高且年增>=20%", bts.revenue_pass(f(new_high=True, yoy=0.2)) and not bts.revenue_pass(f(new_high=True, yoy=0.19)))
check("條件A：不是新高不算", not bts.revenue_pass(f(new_high=False, yoy=0.9)))
check("條件B：累計3月年增>=30%且月增>0", bts.revenue_pass(f(yoy3=0.3, mom=0.01)) and not bts.revenue_pass(f(yoy3=0.3, mom=0.0)) and not bts.revenue_pass(f(yoy3=0.29, mom=0.5)))
check("缺值不通過", not bts.revenue_pass(None) and not bts.revenue_pass({}))

# 整合：兩檔，一檔營收爆發、一檔持平
idx = pd.bdate_range(end="2026-10-12", periods=420)
rng = np.random.default_rng(1)


def mkdf(seed):
    r = np.random.default_rng(seed)
    c = 100 * np.exp(np.cumsum(r.normal(0.0003, 0.012, len(idx))))
    return pd.DataFrame({"Open": c, "High": c * 1.01, "Low": c * 0.99, "Close": c, "Volume": r.uniform(1e6, 2e6, len(idx))}, index=idx)


prices = {"1111": mkdf(1), "2222": mkdf(2)}
rows_hot = [(2024, m, 100.0) for m in range(1, 13)] + [(2025, m, 100.0) for m in range(1, 13)] + [(2026, m, 100.0 + 5 * m) for m in range(1, 10)]
rows_flat = [(y, m, 100.0) for y in (2024, 2025) for m in range(1, 13)] + [(2026, m, 100.0) for m in range(1, 10)]
cfg = dict(bts.DEFAULT_CFG)
sigs, info = bts.find_revenue_signals(prices, {"1111": rows_hot, "2222": rows_flat}, cfg, "2026-10-12")
check("訊號日當天：只有營收爆發的那檔出訊號", [s["symbol"] for s in sigs] == ["1111"] and sigs[0]["rule"] == bts.RULE_REVENUE, (sigs, info))
check("訊號帶營收資料與月份", sigs and sigs[0]["rev_ym"] == "2026-09" and sigs[0]["rev"]["yoy"] > 0.3 and sigs[0]["rev"]["new_high"] is True, sigs)
sigs2, info2 = bts.find_revenue_signals({k: v[v.index <= "2026-10-08"] for k, v in prices.items()}, {"1111": rows_hot}, cfg, "2026-10-08")
check("訊號日之前不出訊號", sigs2 == [] and info2["window"] is False, info2)
sigs3, info3 = bts.find_revenue_signals({k: v[v.index <= "2026-10-08"] for k, v in prices.items()}, {"1111": rows_hot}, cfg, "2026-10-08", preview=True)
check("preview(乾跑)忽略時窗、用目前最新已過基準日的月份驗證管線", info3["rev_ym"] == "2026-08" and info3["window"] is False, info3)
# 與 find_signals 合併、去重（同一檔被兩條規則選中只留優先序高者）
base_sigs, binfo = bts.find_signals(prices, dict(cfg, rules=["revenue_momentum"]), as_of="2026-10-12", extra=sigs)
check("find_signals 吃 extra 並計數", binfo["by_rule"].get("revenue_momentum") == 1 and any(s["symbol"] == "1111" for s in base_sigs), binfo)
check("預設規則清單包含營收動能", "revenue_momentum" in bts.DEFAULT_CFG["rules"] and "revenue_momentum" in bts.RULE_LABELS)

print("\nFAILED:" if FAIL else "\n全部通過", FAIL if FAIL else "")
raise SystemExit(1 if FAIL else 0)
