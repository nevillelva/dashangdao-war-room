"""【2026-10-13】ETF 組合回測純函式測試（離線）。"""
import sys
from etf_combo import validate_weights, align, backtest, adjust_corporate_actions

fails = []


def check(name, cond):
    print(("PASS: " if cond else "FAIL: ") + name)
    if not cond:
        fails.append(name)


def series(n, daily):
    """從 2020-01-01 起產生 n 個連續平日（跳過週六日，日期間隔最多 3 天，不製造假空檔）、每天乘上 daily 的價格。"""
    from datetime import date, timedelta
    out, p, d = [], 100.0, date(2020, 1, 1)
    while len(out) < n:
        if d.weekday() < 5:
            out.append((d.isoformat(), p))
            p *= daily
        d += timedelta(days=1)
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

check("回傳走勢曲線（與日期數一致）", len(r["curve"]) == r["n_days"] and r["curve"][0][0] == r["from"])

single = backtest({"A": a}, {"A": 100})
check("單一 ETF：年化報酬約等於個股（正向上漲）", single["ok"] and single["cagr_pct"] > 0)

short = backtest({"A": series(30, 1.001)}, {"A": 100})
check("資料不足（<60 天）拒絕計算", not short["ok"])

bad = backtest({"A": a, "B": [(d, 0.0) for d, _ in b]}, {"A": 50, "B": 50})
check("價格含 0 拒絕計算", not bad["ok"])

mismatch = backtest({"A": a}, {"A": 50, "B": 50})
check("代號與權重不一致拒絕計算", not mismatch["ok"])


# 分割推算：4:1 分割（未還原：分割前 400、分割日起 100，實際當日只動 +1%）
split_raw = [(f"2024-01-{i+1:02d}" if i < 28 else f"2024-02-{i-27:02d}", 400.0) for i in range(40)]
split_raw = [(d, c if i < 20 else 101.0) for i, (d, c) in enumerate(split_raw)]
adj_rows, ev, pb = adjust_corporate_actions(split_raw)
check("4:1 分割被推算出來（事件數 1、比例約 4）", not pb and len(ev) == 1 and abs(ev[0]["factor"] - 4.0) < 1e-6)
check("分割後調整：分割前價格除以 4，與分割後接續", abs(adj_rows[0][1] - 100.0) < 1e-6 and abs(adj_rows[19][1] - 100.0) < 1e-6)
rets = [adj_rows[i][1] / adj_rows[i-1][1] - 1 for i in range(1, len(adj_rows))]
check("調整後不再有超過漲跌幅限制的單日跳動", max(abs(x) for x in rets) <= 0.105)

unexplained = [(f"2024-03-{i+1:02d}", 100.0 if i < 10 else 150.0) for i in range(30)]
check("無法解釋的 +50% 跳動整檔退回", not backtest({"X": unexplained + [("2024-04-01", 150.0)] * 40}, {"X": 100})["ok"])

# 【2026-10-13】缺資料空檔：超過 14 天的空檔整檔退回（死規則 1，不把缺資料當連續價格）
from etf_combo import find_gaps
check("空檔偵測：春節級休市（10 天）不算缺資料", find_gaps([("2024-02-01", 1), ("2024-02-12", 1)]) == [])
check("空檔偵測：超過 14 天標出", find_gaps([("2021-11-23", 1), ("2024-09-23", 1)]) == [("2021-11-23", "2024-09-23", 1035)])
gap_rows = series(120, 1.001)
shifted = [("2024-12-31" if i == 60 else d, p) for i, (d, p) in enumerate(gap_rows)]  # 第 60 列直接跳到 2024-12-31，中間缺資料
gr = backtest({"G": shifted}, {"G": 100})
check("有缺資料空檔的整檔退回並說明", (not gr["ok"]) and "缺資料" in gr["message"])
bad_fmt = [("2024-02-30", 100.0)] + series(80, 1.001)
br = backtest({"H": bad_fmt}, {"H": 100})
check("無效日期（2 月 30 日）退回而非例外", (not br["ok"]) and "日期格式" in br["message"])

if fails:
    print(f"\n{len(fails)} 項失敗")
    sys.exit(1)
print("\n全部通過")
