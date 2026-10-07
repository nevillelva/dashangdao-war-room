"""backtest_oldscore 單元測試（純函式＋合成資料，不連網）。python3 test_backtest_oldscore.py"""
import numpy as np
import pandas as pd
import backtest_oldscore as bo

FAIL = []


def check(name, cond, extra=""):
    if not cond:
        FAIL.append(name)
        print("❌", name, extra)
    else:
        print("✅", name)


def mk(n=120, seed=1, drift=0.0):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2024-01-01", periods=n)
    c = 100 * np.exp(np.cumsum(rng.normal(drift, 0.015, n)))
    o = c * (1 + rng.normal(0, 0.004, n))
    h = np.maximum(o, c) * 1.005
    l = np.minimum(o, c) * 0.995
    v = rng.uniform(1e6, 2e6, n)
    return pd.DataFrame({"Open": o, "High": h, "Low": l, "Close": c, "Volume": v}, index=idx)


# --- 因子：站穩多頭 +2（價>MA5>MA20）；跌破 5MA -2
up = mk(120, drift=0.01)
F = bo.build_symbol_frame(up)
t = F.index[-1]
check("上升趨勢：站穩多頭(+2)", F.loc[t, "f_ma"] == 2 and F.loc[t, "close"] > F.loc[t, "ma5"] > F.loc[t, "ma20"])
dn = mk(120, drift=-0.01)
Fd = bo.build_symbol_frame(dn)
check("下降趨勢：跌破5MA(-2)", Fd["f_ma"].iloc[-1] == -2)
check("暖機期(MA20 未成形)不給分", (F["f_ma"].iloc[:19] == 0).all())

# --- 外資連3日買超 → 法人持續性 +2；外資+投信同買 → 共振 +2；缺資料不觸發
chip = pd.DataFrame({"f_buy": np.full(120, -1.0), "t_buy": np.full(120, -1.0)}, index=up.index)
chip.loc[up.index[-3:], "f_buy"] = 5.0
chip.loc[up.index[-1], "t_buy"] = 2.0
F2 = bo.build_symbol_frame(up, chip=chip)
check("連3日外資買超 → 持續性+2", F2["f_pers"].iloc[-1] == 2 and F2["f_pers"].iloc[-2] == 0)
check("外資+投信同日買超 → 共振+2", F2["f_inst"].iloc[-1] == 2 and F2["f_inst"].iloc[-2] == 0)
check("無籌碼資料：外資/共振/持續性不觸發", (F["f_fb"] == 0).all() and (F["f_inst"] == 0).all() and (F["f_pers"] == 0).all())

# --- 營收雙增（次月 11 日起可用）
rows = [(2023, m, 100.0) for m in range(1, 13)] + [(2024, m, 100.0 + 10 * m) for m in range(1, 13)]
F3 = bo.build_symbol_frame(up, rev_rows=rows)
d_before, d_after = pd.Timestamp("2024-02-09"), pd.Timestamp("2024-02-12")
check("營收：公告日前不可用、之後才觸發", F3.loc[d_before, "f_rev"] == 0 and F3.loc[d_after, "f_rev"] == 1, (F3.loc[d_before, "f_rev"], F3.loc[d_after, "f_rev"]))

# --- 覆蓋規則順序（與 warroom_core.apply_override_rules 一致）
s = pd.Series([6.0, 7.0, 8.0, 7.0, 7.0, 5.0], index=range(6))
mb = np.array([False, False, False, True, True, True])
dump = np.array([False] * 6)
over = np.array([False, False, False, True, False, False])
rev = np.array([False, False, False, False, True, False])
gate = np.array([False, False, False, False, False, True])
o = bo.apply_overrides(s, mb, dump, over, rev, gate).values
check("大盤破MA20：6~7 降為 5，>=8 不動", o[0] == 5 and o[1] == 5 and o[2] == 8)
check("布林過熱封頂 3、攻擊熄燈 -2、趨勢閘門 <= -7", o[3] == 3 and o[4] == 5 and o[5] == -7)

# --- 沒有未來函數：改動未來資料，過去的分數/因子不變
base = mk(150, seed=5)
fut = base.copy()
fut.iloc[100:, :] = fut.iloc[100:, :] * 1.3
fut["Volume"] = base["Volume"]
Fa, Fb = bo.build_symbol_frame(base), bo.build_symbol_frame(fut)
cols = ["score_old", "f_ma", "f_comp", "f_consec", "f_vol", "overheated", "gate", "reversal"]
check("改動未來資料不影響過去分數", all(np.allclose(Fa[c].iloc[:99].astype(float).fillna(-99), Fb[c].iloc[:99].astype(float).fillna(-99)) for c in cols))
check("未來報酬欄：進場=隔日收盤", abs(Fa["r5"].iloc[10] - (base["Close"].iloc[16] / base["Close"].iloc[11] - 1)) < 1e-12)

# --- 校準：負係數→0、最大=1
w = bo.coefs_to_weights(np.array([0.5, -0.2, 0.25, 0, 0, 0, 0, 0, 0, 0.1]))
check("負係數→0、最大權重=1", w["fb"] == 0 and w["ma"] == 1.0 and abs(w["vol"] - 0.5) < 1e-9 and abs(w["rev"] - 0.2) < 1e-9, w)
# 嶺迴歸能還原真實關係（y = 0.01*f_ma + 雜訊）
rng = np.random.default_rng(0)
X = rng.integers(-2, 3, size=(8000, 10)).astype(float)
y = 0.01 * X[:, 0] + rng.normal(0, 0.02, 8000)
coef = bo.ridge_fit(X, y)
check("嶺迴歸還原主因子", coef[0] > 0.007 and abs(coef[1:]).max() < 0.003, coef)

# --- 選股：每日前 k、同分固定亂數可重現、matched 檔數
P = pd.DataFrame({"date": np.repeat(pd.bdate_range("2024-01-01", periods=3), 5), "score": [5, 5, 5, 1, 0] * 3})
i1, i2 = bo.select(P, "score", 2), bo.select(P, "score", 2)
check("每日前2檔且可重現", len(i1) == 6 and list(i1) == list(i2))
check("min_score 過濾", len(bo.select(P, "score", 3, min_score=5)) == 9)
m = bo._matched(P, "score", {P["date"].iloc[0]: 1, P["date"].iloc[5]: 3})
check("同檔數對齊", len(m) == 4)

# --- 出場
n = 60
idx = pd.bdate_range("2025-01-01", periods=n)
flat = pd.DataFrame({"Open": 100.0, "High": 100.0, "Low": 100.0, "Close": 100.0, "Volume": 1.0}, index=idx)
A = bo.Arrays({"X": flat})
r = bo.exit_trade(A, "X", idx[0], ("hold", 5))
check("持有5日：平盤只扣成本", r is not None and abs(r[0] + bo.COST) < 1e-12 and r[1] == 5)
up2 = flat.copy(); up2.loc[idx[4]:, ["Open", "High", "Low", "Close"]] = 110.0     # 進場(idx1收100)後第3天(idx4)起漲 10%
A2 = bo.Arrays({"X": up2})
r = bo.exit_trade(A2, "X", idx[0], ("tpsl", 0.08, 0.08, 10))
check("停利：跳空以開盤價 110 成交", r is not None and abs(r[0] - (0.10 - bo.COST)) < 1e-9 and r[2] == "tp", r)
dn2 = flat.copy(); dn2.loc[idx[4]:, ["Open", "High", "Low", "Close"]] = 90.0
A3 = bo.Arrays({"X": dn2})
r = bo.exit_trade(A3, "X", idx[0], ("tpsl", 0.08, 0.08, 10))
check("停損：跳空以開盤價 90 成交", r is not None and abs(r[0] - (-0.10 - bo.COST)) < 1e-9 and r[2] == "sl", r)
r = bo.exit_trade(A3, "X", idx[0], ("live",))
check("實盤出場：收盤破 5/10MA 即出場(ma_break)", r is not None and r[2] == "ma_break" and r[1] == 3, r)
r = bo.exit_trade(A2, "X", idx[0], ("live",))
check("實盤出場：早盤開盤>=+5% 即出場(spike)", r is not None and r[2] == "spike", r)
check("資料不足回 None", bo.exit_trade(A, "X", idx[-2], ("hold", 5)) is None)

# --- 整合：合成資料跑一輪不報錯、沒有任何穩定優勢（隨機資料）
prices, chips, revs = bo.synthetic(n=30, seed=2)
rep = bo.run_backtest(prices, chips, revs, eval_years=3, reps=50)
check("整合跑完且產出關鍵欄位", all(k in rep for k in ("buckets_old", "factor_lift_x10", "wf_weights_by_year", "sets", "ic_old")))
check("隨機資料：舊評分 IC 不顯著(|t|<3)", rep["ic_old"]["all"]["t"] is not None and abs(rep["ic_old"]["all"]["t"]) < 3, rep["ic_old"])

print("\nFAILED:" if FAIL else "\n全部通過", FAIL if FAIL else "")
raise SystemExit(1 if FAIL else 0)
