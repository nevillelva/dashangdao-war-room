"""【2026-10-13】ETF 組合回測純函式測試（離線）。"""
import sys
from etf_combo import validate_weights, align, backtest

fails = []


def check(name, cond):
    print(("PASS: " if cond else "FAIL: ") + name)
    if not cond:
        fails.append(name)


def series(n, daily):
    """從 2020-01-01 起產生 n 天、每天乘上 daily 的價格（日期只用序號，格式 YYYY-MM-DD 以年份切）。"""
    out, p = [], 100.0
    year, day = 2020, 1
    for i in range(n):
        out.append((f"{year + i // 245:04d}-{1 + (i % 245) // 21:02d}-{1 + (i % 21):02d}", p))
        p *= daily
    return out


check("權重加總 100 通過", validate_weights({"A": 60, "B": 40})[0])
check("權重加總不為 100 擋下", not validate_weights({"A": 60, "B": 30})[0])
check("超過 5 檔擋下", not validate_weights({"A": 20, "B": 20, "C": 20, "D": 20, "E": 10, "F": 10})[0])
check("權重 0 擋下", not validate_weights({"A": 100, "B": 0})[0])

a = series(500, 1.001)
b = series(500, 0.999)
dates, closes = align({"A": a, "B": b})
check("內連接：共同日期數正確", len(dates) == 500)

r = backtest({"A": a, "B": b}, {"A": 50, "B": 50}, rebalance="annual")
check("回測成功", r["ok"])
check("回測結果標示未含息", "未含息" in r["note"])
check("最大回撤非負", r["mdd_pct"] >= 0)

single = backtest({"A": a}, {"A": 100})
check("單一 ETF：年化報酬約等於個股（正向上漲）", single["ok"] and single["cagr_pct"] > 0)

short = backtest({"A": series(30, 1.001)}, {"A": 100})
check("資料不足（<60 天）拒絕計算", not short["ok"])

bad = backtest({"A": a, "B": [(d, 0.0) for d, _ in b]}, {"A": 50, "B": 50})
check("價格含 0 拒絕計算", not bad["ok"])

mismatch = backtest({"A": a}, {"A": 50, "B": 50})
check("代號與權重不一致拒絕計算", not mismatch["ok"])

if fails:
    print(f"\n{len(fails)} 項失敗")
    sys.exit(1)
print("\n全部通過")
