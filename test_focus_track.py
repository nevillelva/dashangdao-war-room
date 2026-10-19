"""【2026-10-19】主戰清單結果追蹤（focus_track.py）。"""
import sys
import pandas as pd
import focus_track as T
ok = True


def chk(n, c):
    global ok
    print(("PASS: " if c else "FAIL: ") + n)
    ok = ok and c


idx = pd.to_datetime(["2026-10-19", "2026-10-20", "2026-10-21", "2026-10-22", "2026-10-23", "2026-10-26"])
df = pd.DataFrame({"Open": [100, 102, 103, 104, 105, 106], "Close": [101, 103, 104, 105, 106, 110]}, index=idx)
tw = pd.DataFrame({"Open": [1000.0] * 6, "Close": [1000, 1010, 1010, 1010, 1010, 1020.0]}, index=idx)
r = T.settle_item({"symbol": "A", "track": "波段主力", "direction": "long"}, "2026-10-19", df, tw)
chk("做多 1 日：(101/100-1)-0.585 = 0.415", abs(r["r1"] - 0.415) < 1e-6)
chk("做多 5 日有結果、10 日待算", "r5" in r and "r10" not in r and r["status"] == "done")
chk("超額＝個股 − 大盤（5日：106/100 vs 1010/1000）", abs(r["ex5"] - ((106 / 100 - 1) * 100 - 0.585 - 1.0)) < 1e-6)
s1 = T.settle_item({"symbol": "S", "track": "空方觀察", "direction": "short", "ref_close": 98}, "2026-10-19", df)
chk("查8 開盤 100 比昨收 98 高 2% → 做空：(100-101)/100-0.435 = -1.435", s1["status"] == "done" and abs(s1["r1"] + 1.435) < 1e-6)
s2 = T.settle_item({"symbol": "S", "track": "空方觀察", "direction": "short", "ref_close": 99.5}, "2026-10-19", df)
chk("查8 沒開高>1% → skipped", s2["status"] == "skipped")
chk("清單日沒有資料 → no_data", T.settle_item({"symbol": "A", "direction": "long"}, "2026-10-18", df)["status"] == "no_data")
ag = T.aggregate([dict(r, track="波段主力"), s1, s2])
chk("彙總：波段 1 筆、查8 skipped 1", ag["波段主力"]["h1"]["n"] == 1 and ag["空方觀察"]["skipped"] == 1)
chk("樣本<30 標註", ag["波段主力"]["h1"]["small_sample"] is True)
sys.exit(0 if ok else 1)
