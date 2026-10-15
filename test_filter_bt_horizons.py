"""【2026-10-15】查N回測：5/10/20 日勝率彙總；20 日缺值（訊號太新）不當 0% 輸。"""
import sys
import numpy as np
import warroom_core as wc

fails = []


def check(n, c):
    print(("PASS: " if c else "FAIL: ") + n)
    if not c:
        fails.append(n)


rows = []
for i in range(40):
    rows.append({"stock": f"S{i}", "date": f"2026-0{1 + i % 9}-1{i % 9}", "filter": "查6.測試",
                 "future_5d_ret": 1.0 if i % 2 == 0 else -1.0,          # 勝率 50%
                 "future_10d_ret": 2.0 if i % 4 != 0 else -2.0,         # 勝率 75%
                 "future_20d_ret": (3.0 if i % 5 != 0 else -3.0) if i < 30 else None})   # 前 30 筆有值：24/30 勝 = 80%
sm = wc.summarize_filter_backtest(rows).iloc[0]
check("5日勝率 50%", sm["5日勝率%"] == 50.0)
check("10日勝率 75%、樣本 40", sm["10日勝率%"] == 75.0 and sm["10日樣本數"] == 40)
check("20日勝率只算有資料的 30 筆＝80%（缺值不當輸）", sm["20日勝率%"] == 80.0 and sm["20日樣本數"] == 30)
check("20日平均報酬以有資料筆數計", abs(sm["20日平均報酬%"] - (24 * 3 - 6 * 3) / 30) < 1e-9)
old = [{"stock": "A", "date": "2026-01-01", "filter": "f", "future_5d_ret": 1.0, "future_10d_ret": 1.0}] * 12
so = wc.summarize_filter_backtest(old).iloc[0]
check("舊格式（沒有 20 日欄）不當機、20日為 None", so["20日勝率%"] is None and so["20日樣本數"] == 0)
if fails:
    print(f"\n{len(fails)} 項失敗"); sys.exit(1)
print("\n全部通過")
