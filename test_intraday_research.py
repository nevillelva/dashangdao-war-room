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
sys.exit(0 if ok else 1)
