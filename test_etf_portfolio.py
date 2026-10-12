"""【P5】股債配置試算離線測試（合成資料，數字可手算）。python3 test_etf_portfolio.py"""
import etf_portfolio as P

FAIL = []


def check(name, cond, extra=""):
    if cond:
        print("✅", name)
    else:
        FAIL.append(name)
        print("❌", name, extra)


URL = "https://www.finmindtrade.com/example"
SYMS = ["00679B", "0050"]

# 1) 解析：正常（含標題列）
ok, res = P.parse_price_csv("symbol,date,close\n0050,2026/10/01,100\n0050,20261002,101.5\n00679B,2026-10-01,20", SYMS, URL)
check("解析成功", ok, res)
check("日期正規化與排序", ok and res["0050"] == [("2026-10-01", 100.0), ("2026-10-02", 101.5)], res)


# 1b) FinMind 原生欄位（標題名稱對應，不依位置）
FM = ("date,stock_id,Trading_Volume,Trading_money,open,max,min,close,spread,Trading_turnover\n"
      "2026-10-01,0050,12345,1000,99,101,98,100,1,0.5\n"
      "2026-10-02,0050,11111,1000,100,102,99,101.5,1.5,0.4")
ok, res = P.parse_price_csv(FM, SYMS, URL)
check("FinMind 原生欄位可解析", ok and res["0050"] == [("2026-10-01", 100.0), ("2026-10-02", 101.5)], res)

# 2) 解析：各種退回
cases = [
    ("非 https", P.parse_price_csv("0050,2026-10-01,100", SYMS, "http://x"), "https"),
    ("空白", P.parse_price_csv("  ", SYMS, URL), "沒有貼上"),
    ("代號不在清單", P.parse_price_csv("9999,2026-10-01,100", SYMS, URL), "不在 ETF 清單"),
    ("日期錯誤", P.parse_price_csv("0050,2026-13-01,100", SYMS, URL), "日期格式"),
    ("收盤非數字", P.parse_price_csv("0050,2026-10-01,abc", SYMS, URL), "不是數字"),
    ("收盤 ≤0", P.parse_price_csv("0050,2026-10-01,0", SYMS, URL), "大於 0"),
    ("重複列", P.parse_price_csv("0050,2026-10-01,100\n0050,2026-10-01,101", SYMS, URL), "重複"),
    ("欄位不足", P.parse_price_csv("0050,2026-10-01", SYMS, URL), "欄位不足"),
]
for name, (okk, msg), key in cases:
    check(f"退回：{name}", (not okk) and any(key in m for m in msg), msg)

# 3) 交叉比對：偏差 >5% 退回
ok, prices = P.parse_price_csv("0050,2026-10-01,100", ["0050"], URL)
check("交叉比對：與現價相符通過", P.cross_check(prices, {"0050": 101})[0])
okc, probs = P.cross_check(prices, {"0050": 110})
check("交叉比對：偏差 >5% 退回", (not okc) and probs and "超過" in probs[0], probs)
check("交叉比對：沒有現價也退回", not P.cross_check(prices, {})[0])

# 4) 含息日報酬：除息日 (收盤＋股利)/前日收盤−1
rows = [("2026-01-02", 100.0), ("2026-01-05", 102.0), ("2026-01-06", 101.0)]
tr = dict(P.total_return_daily(rows, [("2026-01-05", 2.0)]))
check("除息日報酬含股利 (102+2)/100−1＝4%", abs(tr["2026-01-05"] - 0.04) < 1e-9, tr)
check("非除息日報酬為價差", abs(tr["2026-01-06"] - (101 / 102 - 1)) < 1e-9, tr)
# 除息日為週末：歸到之後第一個交易日
tr2 = dict(P.total_return_daily(rows, [("2026-01-03", 1.0)]))
check("週末除息歸到次一交易日 (102+1)/100−1＝3%", abs(tr2["2026-01-05"] - 0.03) < 1e-9 and "2026-01-03" not in tr2, tr2)

# 4c) 公司行為斷點：1 拆 4（100→25）不得被當成 −75% 虧損；正常 −3% 仍計入
split_rows = [("2026-01-02", 100.0), ("2026-01-05", 25.0), ("2026-01-06", 24.25)]
sr = dict(P.total_return_daily(split_rows, []))
check("分割日報酬視為 0（不當虧損）", sr["2026-01-05"] == 0.0, sr)
check("正常跌 3% 仍計入", abs(sr["2026-01-06"] - (24.25 / 25.0 - 1)) < 1e-9, sr)
check("斷點數＝1", P.count_breaks(split_rows) == 1)
check("一般波動不算斷點", P.count_breaks([("a", 100.0), ("b", 95.0), ("c", 99.0)]) == 0)

# 5) 月報酬複利：兩日 +10%、+10% → 當月 +21%
mm = P.monthly_returns([("2026-01-05", 0.10), ("2026-01-06", 0.10), ("2026-02-02", -0.5)])
check("同月複利 1.1×1.1−1", abs(mm["2026-01"] - 0.21) < 1e-9, mm)

# 6) 最大回撤：+10% 後 −20% → MDD −20%
m = P._metrics([0.10, -0.20])
check("最大回撤 −20%", m["mdd_pct"] == -20.0, m)
check("樣本不足標示", m["sample_ok"] is False)

# 7) 組合：w=1 等於純股票；w=0 等於純債；w=0.5 介於兩者之間
stock = {f"2024-{i:02d}": 0.02 for i in range(1, 13)}
stock.update({f"2025-{i:02d}": 0.01 for i in range(1, 13)})
bond = {f"2024-{i:02d}": 0.005 for i in range(1, 13)}
bond.update({f"2025-{i:02d}": 0.003 for i in range(1, 13)})
r1 = P.mix_backtest(stock, bond, 1.0)
r0 = P.mix_backtest(stock, bond, 0.0)
rh = P.mix_backtest(stock, bond, 0.5)
check("w=1 等於純股票 CAGR", r1["mix"]["cagr_pct"] == r1["stock_only"]["cagr_pct"], (r1["mix"], r1["stock_only"]))
check("w=0 等於純債 CAGR", r0["mix"]["cagr_pct"] == r0["bond_only"]["cagr_pct"])
check("w=0.5 介於兩者", r0["mix"]["cagr_pct"] < rh["mix"]["cagr_pct"] < r1["mix"]["cagr_pct"], (r0["mix"], rh["mix"], r1["mix"]))
check("期間與月數", rh["period"] == {"start": "2024-01", "end": "2025-12"} and rh["mix"]["months"] == 24)
try:
    P.mix_backtest(stock, bond, 1.5)
    check("權重超出範圍拋錯", False)
except ValueError:
    check("權重超出範圍拋錯", True)
empty = P.mix_backtest({}, bond, 0.5)
check("無重疊月份 → 空結果", empty["period"] is None and empty["mix"]["months"] == 0)

print("\n全部通過" if not FAIL else f"\n失敗 {FAIL}")
raise SystemExit(1 if FAIL else 0)
