import sys
from datetime import datetime, timedelta, date
import intraday_research as R
ok = True
def chk(n, c):
    global ok
    print(("PASS " if c else "FAIL ") + n)
    ok = ok and c
def mkday(d, prices):
    out = []
    t0 = datetime(d.year, d.month, d.day, 9, 0)
    for i, p in enumerate(prices):
        out.append((t0 + timedelta(minutes=i), p, p * 1.001, p * 0.999, p, 100.0))
    return out
days = [date(2026, 1, 5) + timedelta(days=i) for i in range(6)]
raw = {}
# D0 平 100；D1 13:15 漲到 107；D2 續漲到 110；D3 開高 112 後跌到 105
paths = [[100] * 270, [100 + 7 * i / 255 for i in range(270)], [107 + 3 * i / 269 for i in range(270)],
         [112 - 7 * i / 269 for i in range(270)], [105] * 270, [105] * 270]
raw["X"] = {d: mkday(d, p) for d, p in zip(days, paths)}
per = {"X": {d: R.day_stats(v) for d, v in raw["X"].items()}}
chk("price_at 10:00 取 09:59", abs(R.price_at(raw["X"][days[1]], "10:00") - (100 + 7 * 59 / 255)) < 1e-9)
b = R.part_b(per, raw)
chk("有買進網格", any(k.startswith("買D1") for k in b["long"]))
k = "空D3_09:05（D3開高>1%）"
chk("D3 開高放空有樣本且賺錢", k in b["short"] and b["short"][k]["mean"] > 0)
c = R.part_c(raw)
chk("時段波動有 09:00 桶", "09:00" in c["abs_ret_by_30min"])
chk("day_stats 有 v2 欄位", all(k in per["X"][days[1]] for k in ("vol", "or5_high", "vwap1315", "lo1000", "orb5_1000")))
d = R.part_d(per)
chk("查8 v2 網格有 D1 10:00 以外的買點", any(k.startswith("買D2_") for k in d["grid"]))
chk("查8 v2：D1 13:15 漲 7% 有買點", any(k.startswith("買D1_13:15") for k in d["grid"]))
# 40 天緩漲 + 每 7 天一次尾盤拉 4%
import math
dl = [date(2026, 2, 2) + timedelta(days=i) for i in range(45)]
raw2, px = {}, 100.0
for i, dd_ in enumerate(dl):
    if i % 7 == 6:
        path = [px * (1 + 0.04 * j / 269) for j in range(270)]
    else:
        path = [px * (1 + 0.004 * j / 269) for j in range(270)]
    raw2[dd_] = mkday(dd_, path)
    px = path[-1]
per2 = {"Y": {d_: R.day_stats(v) for d_, v in raw2.items()}}
e = R.part_e(per2)
chk("隔日沖選股有樣本", e["n"] > 0 and e["全部"]["D2_09:05"]["n"] > 0)
f = R.part_f(per2)
chk("多方型態有列且有 hold_open", f["n_rows"] > 0 and f["hold_open"]["yes"]["n"] > 0)
sys.exit(0 if ok else 1)
