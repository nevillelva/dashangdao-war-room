"""【2026-10-17】盤中 5 年快取（intraday_hist.py）。"""
import sys, tempfile
from datetime import datetime, timedelta
import intraday_hist as H
import intraday_research as IR
ok = True
def chk(n, c):
    global ok
    print(("PASS: " if c else "FAIL: ") + n)
    ok = ok and c
def day(d, base):
    t0 = datetime(d.year, d.month, d.day, 9, 0)
    return [(t0 + timedelta(minutes=i), base + i * 0.01, base + i * 0.01 + 0.05, base + i * 0.01 - 0.05, base + i * 0.01 + 0.02, 10.0) for i in range(270)]
b1 = day(datetime(2024, 1, 2), 100) + day(datetime(2024, 1, 3), 103)
d5 = H.bars5_from_1m(b1)
chk("5 分K 每天 54 根", len(d5["t"]) == 108)
chk("第一根 09:00 量＝5 分鐘加總", float(d5["v"][0]) == 50.0)
d5b = H.bars5_from_1m(day(datetime(2024, 1, 3), 200) + day(datetime(2024, 1, 4), 201))
m = H.merge(d5, d5b)
chk("合併後去重（3 天）", len(m["t"]) == 162)
irm = H.to_ir_days(m)
chk("新資料覆蓋同時間舊資料", abs(irm[datetime(2024, 1, 3).date()][0][1] - 200) < 1e-3)
with tempfile.TemporaryDirectory() as td:
    H.save(td, "1234", m)
    back = H.load(td, "1234")
    chk("存取一致", len(back["t"]) == 162)
    H.save_manifest(td, {"pool": ["1234"], "coverage": {"1234": [["2024-01-01", "2024-12-30"]]}})
    chk("manifest 存取", H.load_manifest(td)["pool"] == ["1234"])
ch = H.year_chunks("2021-10-18", "2026-10-17")
chk("5 年切 6 段以內且連續", len(ch) <= 6 and ch[0][0] == "2021-10-18" and ch[-1][1] == "2026-10-17")
chk("缺少區段", H.missing_chunks([list(ch[0])], "2021-10-18", "2026-10-17") == ch[1:])
st = IR.day_stats(irm[datetime(2024, 1, 2).date()])
chk("day_stats 可用 5 分K（10:00 價＝09:55 那根收盤）", abs(st["p1000"] - (100 + 59 * 0.01 + 0.02)) < 1e-3)
gb = H.to_gate_bars(irm)
chk("第二三關用 K 棒：每天 12 根＋收盤一根", len([b for b in gb if b["date"] == datetime(2024, 1, 2).date()]) == 13)
sys.exit(0 if ok else 1)
