import sys

import numpy as np
import pandas as pd

import chuan_stop_bt as CB

ok = True


def chk(n, c, extra=None):
    global ok
    print(("✅ " if c else "❌ ") + n + ("" if c else f"  → {extra}"))
    ok = ok and c


def arr(*xs):
    return np.array(xs, dtype=float)


nan = float("nan")
# 進場 e=0 價 100；第 2 天最低 84（−16%）
o, h, l, c = arr(100, 100, 100, 100), arr(101, 101, 101, 101), arr(99, 84, 99, 99), arr(100, 90, 100, 100)
ma = arr(nan, nan, nan, nan)
r, i = CB.simulate(o, h, l, c, ma, 0, "sl15")
chk("sl15 觸價：−15 − 0.585", abs(r + 15.585) < 1e-6 and i == 1, (r, i))
r, i = CB.simulate(o, h, l, c, ma, 0, "sl20")
chk("sl20 不觸價：續抱到最後一天收盤", abs(r - (0 - 0.585)) < 1e-6 and i == 3, (r, i))
r, _ = CB.simulate(o, h, l, c, ma, 0, "sl10")
chk("sl10 觸價：−10 − 0.585", abs(r + 10.585) < 1e-6, r)
# 跳空
o2 = arr(100, 80, 80, 80)
r, i = CB.simulate(o2, arr(101, 81, 81, 81), arr(99, 79, 79, 79), arr(100, 80, 80, 80), ma, 0, "sl15")
chk("跳空跌破停損：以開盤價出", abs(r + 20.585) < 1e-6 and i == 1, (r, i))
# 停利：第 2 天最高 115
r, i = CB.simulate(arr(100, 100, 100), arr(101, 115, 101), arr(99, 99, 99), arr(100, 110, 100), arr(nan, nan, nan), 0, "none")
chk("停利 12%：+12 − 0.585", abs(r - 11.415) < 1e-6 and i == 1, (r, i))
# 同日停利與停損都觸及 → 先停損
r, _ = CB.simulate(arr(100, 100), arr(101, 115), arr(99, 80), arr(100, 100), arr(nan, nan), 0, "sl15")
chk("同日兩者皆觸及先停損", r < 0, r)
# ma60：收盤跌破 MA60，隔日開盤出
o3, h3, l3, c3 = arr(100, 100, 95, 90), arr(101, 101, 96, 91), arr(99, 99, 94, 89), arr(100, 98, 94, 90)
r, i = CB.simulate(o3, h3, l3, c3, arr(95, 99, 99, 99), 0, "ma60")
chk("跌破 MA60：隔日開盤出", i == 2 and abs(r - (95 / 100 - 1) * 100 + 0.585) < 1e-6, (r, i))

# 前一晚美股報酬
sd = ["2026-10-20", "2026-10-21", "2026-10-22"]
sc = [100.0, 100.0, 93.2]
chk("費半前一晚 −6.8%：台股 10/23 看到的是 10/22 那根", abs(CB.prev_ret(sd, sc, "2026-10-23") + 6.8) < 1e-6)
chk("同日不算（嚴格早於進場日）", CB.prev_ret(sd, sc, "2026-10-22") == 0.0)
chk("資料不足回 None", CB.prev_ret(["2026-10-20"], [100.0], "2026-10-23") is None)
g = CB.gates_for("2026-10-23", 108, 100, ["2026-10-21", "2026-10-22"], [100.0, 97.0], sd, sc)
chk("三個閘門判定", g["sox"] and g["twii"] and g["chase"], g)
g2 = CB.gates_for("2026-10-23", 101, 100, ["2026-10-21", "2026-10-22"], [100.0, 99.5], sd, [100.0, 100.0, 99.0])
chk("平靜日：全部不命中", not (g2["sox"] or g2["twii"] or g2["chase"]), g2)

# events_for_stock：冷卻期與固定事件
n = 120
idx = pd.bdate_range("2023-01-02", periods=n)
px = np.linspace(100, 130, n)
df = pd.DataFrame({"Open": px, "High": px * 1.01, "Low": px * 0.99, "Close": px}, index=idx)
ev = CB.events_for_stock(df, lambda d: [(70, "fast"), (75, "fast"), (100, "slow")], [], [], [], [])
chk("20 日冷卻：70 與 100 保留、75 略過", [e["date"] for e in ev] == [idx[70].strftime("%Y-%m-%d"), idx[100].strftime("%Y-%m-%d")], ev)
chk("每個事件有 5 種停損變體報酬", all(set(e["rets"]) == set(CB.VARIANTS) for e in ev))

# 統計與保守結論：構造 sl10 明顯較差、閘門命中明顯較差
evs = []
for k in range(400):
    d = "2023-06-01" if k < 200 else "2025-06-01"
    bad = (k % 4 == 0)
    evs.append({"date": d, "kind": "fast", "rets": {"sl10": -5.0 if bad else 1.0, "sl15": -2.0 if bad else 1.0, "sl20": 1.0, "none": 1.0, "ma60": 1.0},
                "gates": {"sox": bad, "twii": False, "chase": False}})
sm = CB.summarize(evs)
chk("摘要：事件數與樣本內外分段", sm["n_events"] == 400 and sm["variants"]["sl15"]["is"]["n"] == 200 and sm["variants"]["sl15"]["oos"]["n"] == 200, sm["variants"]["sl15"])
vd = CB.verdict(sm)
chk("結論：sl10 樣本內外都差 → 不採用", any("sl10" in x and "不採用" in x for x in vd), vd)
chk("結論：費半大跌命中且明顯差 → 建議暫停", any("前晚費半" in x and "建議該情境暫停進場" in x for x in vd), vd)
chk("結論：沒命中的追高 → 樣本不足、不下結論", any("開盤高於前收" in x and "不足以下結論" in x for x in vd), vd)
chk("樣本少一律不下結論", all("不足" in x or "沒有一致差異" in x for x in CB.verdict(CB.summarize(evs[:10]))[:4]))
sys.exit(0 if ok else 1)
