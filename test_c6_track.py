import sys

import pandas as pd

import c6_track as C6

ok = True


def chk(n, c, extra=None):
    global ok
    print(("✅ " if c else "❌ ") + n + ("" if c else f"  → {extra}"))
    ok = ok and c


def mk(days, opens, lows, closes):
    idx = pd.to_datetime(days)
    return pd.DataFrame({"Open": opens, "Low": lows, "High": closes, "Close": closes}, index=idx)


# 掃描日 2026-10-14；下一個交易日 10-15 開盤進
days = ["2026-10-14", "2026-10-15", "2026-10-16", "2026-10-19"]
df = mk(days, [100, 100, 99, 98], [99, 98, 95, 96], [100, 99, 97, 105])
s = C6._slice_after(df, "2026-10-14")
chk("進場日＝掃描日的下一個交易日", s.index[0].strftime("%Y-%m-%d") == "2026-10-15" and len(s) == 3)
r = C6.settle_all(df, "2026-10-14")
chk("天數不足 20：進行中，附未實現報酬", r["none"]["status"] == "open" and abs(r["none"]["mtm"] - 5.0) < 1e-6, r)
chk("不設停損、未觸價：進行中", r["sl10"]["status"] == "open", r)

# 觸及 −10%：最低 86 → 以 −10% 計並扣成本
df2 = mk(["2026-10-14", "2026-10-15", "2026-10-16"], [100, 100, 99], [99, 98, 86], [100, 99, 90])
r2 = C6.settle_all(df2, "2026-10-14")
chk("−10% 停損觸價：r = −10 − 0.585、已停損", r2["sl10"]["status"] == "done" and r2["sl10"]["stopped"] and abs(r2["sl10"]["r"] + 10.585) < 1e-6, r2)
chk("−15% 停損未觸價", r2["sl15"]["status"] == "open", r2)
# 跳空開盤低於停損線：以開盤價出
df3 = mk(["2026-10-14", "2026-10-15", "2026-10-16"], [100, 100, 80], [99, 98, 78], [100, 99, 79])
r3 = C6.settle_all(df3, "2026-10-14")
chk("跳空跌破停損：以開盤價出（−20 − 0.585）", abs(r3["sl10"]["r"] + 20.585) < 1e-6 and abs(r3["sl15"]["r"] + 20.585) < 1e-6, r3)
chk("沒有後續資料：回 None", C6.settle_all(df, "2026-10-19") is None)

# 滿 20 日
idx = pd.bdate_range("2026-09-01", periods=30)
big = pd.DataFrame({"Open": [100.0] * 30, "Low": [99.0] * 30, "High": [111.0] * 30, "Close": [110.0] * 30}, index=idx)
r4 = C6.settle_all(big, idx[0].strftime("%Y-%m-%d"))
chk("滿 20 日：完成，r = 10 − 0.585", r4["none"]["status"] == "done" and abs(r4["none"]["r"] - 9.415) < 1e-6 and r4["none"]["days"] == 20, r4)

rows = [{"cohort": "查6", "sym": "a", "d": "x", "res": r4}, {"cohort": "查6", "sym": "b", "d": "x", "res": r2},
        {"cohort": "非查6", "sym": "c", "d": "x", "res": r3}]
ag = C6.aggregate(rows)
chk("彙總：查6 兩筆、不設停損完成 1 筆、進行中 1 筆", ag["查6"]["n"] == 2 and ag["查6"]["none"]["done"] == 1 and ag["查6"]["none"]["open"] == 1, ag)
chk("彙總：停損 −10% 觸價筆數", ag["查6"]["sl10"]["stopped"] == 1, ag["查6"]["sl10"])
chk("樣本不足 30 標示小樣本", ag["查6"]["none"]["small_sample"] is True)
sys.exit(0 if ok else 1)
