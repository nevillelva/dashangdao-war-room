"""【P4】定期定額達標試算離線測試。python3 test_etf_dca.py"""
import etf_dca as D

FAIL = []


def check(name, cond, extra=""):
    if cond:
        print("✅", name)
    else:
        FAIL.append(name)
        print("❌", name, extra)


# 1) 逐月市值：零殖利率、每月 10,000 → 12 個月約 119,829（扣 0.1425% 買進費）
rows = D.simulate(0, 10000, 0, reinvest=True, months=12)
check("12 個月列數正確", len(rows) == 12)
check("零殖利率第 12 月市值約 119,829", abs(rows[-1]["value"] - 12 * 10000 * (1 - D.BUY_FEE_RATE)) < 1, rows[-1])
check("零殖利率無配息", all(r["net"] == 0 for r in rows))

# 2) 本金目標：零殖利率、每月 10,000、目標 50,000 → 第 6 月（第 5 月 49,928.75 不足）
r = D.months_to_target("capital", 50000, 0, 10000, 0, reinvest=True, horizon_years=5)
check("本金目標 5 萬 → 第 6 月", r["months"] == 6, r)

# 3) 月領目標：1,000,000 本金、6% 殖利率 → 月毛息 5,000，扣 10 元匯費後 4,990
r = D.months_to_target("income", 4000, 1_000_000, 0, 6.0, reinvest=True)
check("月領 4,000 → 第 1 月即達標", r["months"] == 1 and r["net_at"] == 4990, r)
r = D.months_to_target("income", 5000, 1_000_000, 0, 6.0, reinvest=True, horizon_years=30)
check("月領 5,000 需再投入後才達標（非第 1 月）", r["months"] is not None and r["months"] > 1, r)

# 4) 再投入 vs 不再投入：再投入應更快達到本金目標
a = D.months_to_target("capital", 1_200_000, 500_000, 10000, 6.0, reinvest=True)
b = D.months_to_target("capital", 1_200_000, 500_000, 10000, 6.0, reinvest=False)
check("再投入較快達到本金目標", a["months"] is not None and b["months"] is not None and a["months"] < b["months"], (a, b))

# 5) 超過期限回傳 None
r = D.months_to_target("income", 1_000_000, 0, 0, 0, horizon_years=5)
check("期限內未達 → months=None 並說明", r["months"] is None and "未達到" in r["reason"], r)

# 6) 目標非正數
check("目標 ≤0 → 不計算", D.months_to_target("capital", 0, 0, 0, 5)["months"] is None)
try:
    D.months_to_target("bad", 1, 0, 0, 5)
    check("未知目標類型拋錯", False)
except ValueError:
    check("未知目標類型拋錯", True)

# 7) 三種殖利率口徑：由 candidate 欄位算出
cand = {"price": 20.0, "yield_pct": 5.0, "annual_latest": 1.2, "annual_peak": 1.6}
yb = D.yield_bases(cand)
check("保守口徑＝近12月實際 5%", yb["近12月實際（保守）"] == 5.0, yb)
check("中性口徑＝1.2/20＝6%", abs(yb["最近一次×年配次數（中性）"] - 6.0) < 1e-6, yb)
check("樂觀口徑＝1.6/20＝8%", abs(yb["單次最高×年配次數（樂觀）"] - 8.0) < 1e-6, yb)
yb2 = D.yield_bases({"price": 0, "yield_pct": None})
check("缺價格時中性/樂觀為 None", yb2["最近一次×年配次數（中性）"] is None and yb2["單次最高×年配次數（樂觀）"] is None)

# 8) range_table：三列、保守最慢、樂觀最快（同一目標）
rt = D.range_table(cand, "capital", 1_000_000, 300_000, 20000, reinvest=True, horizon_years=30)
years = [x["達標年數"] for x in rt]
check("區間表三列", len(rt) == 3)
check("殖利率越高達標越快", years[0] is not None and years[0] >= years[1] >= years[2], years)

print("\n全部通過" if not FAIL else f"\n失敗 {FAIL}")
raise SystemExit(1 if FAIL else 0)
