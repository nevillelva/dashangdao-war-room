"""【2026-10-23】熱門股高勝率區域回測（hot_zone_bt.py）離線測試。"""
import sys

import numpy as np

import hot_zone_bt as H

ok = True


def chk(n, c, extra=None):
    global ok
    print(("✅ " if c else "❌ ") + n + ("" if c else f"  → {extra}"))
    ok = ok and c


# 1. 出場：開盤進 100，第 1 天低點 94 → 停損 95；+5% 停利
O = np.array([100, 100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 110, 111], dtype=float)
C = O + 0.5
Hh = O + 1.0
L = O - 1.0
L[1] = 94.0
r = H._exit_returns(O, Hh, L, C, 1, 100.0, True)
chk("開盤進場：當天低點 94 觸發 −5% 停損（S5）", abs(r[H.EXITS.index("S5")] - (-5.0)) < 1e-9, r)
chk("隔天開盤賣（X1）＝101/100", abs(r[0] - 1.0) < 1e-9, r)
chk("抱 3 日＝第 4 根收盤 103.5", abs(r[H.EXITS.index("H3")] - 3.5) < 1e-9, r)
r2 = H._exit_returns(O, Hh, L, C, 1, C[1], False)
chk("收盤進場：進場當天的低點不算停損", abs(r2[H.EXITS.index("S5")] - (C[6] / C[1] - 1) * 100) < 1e-9, r2)
O2 = O.copy(); O2[3] = 90.0; L2 = L.copy(); L2[1] = 99.0; L2[3] = 89.0
r3 = H._exit_returns(O2, Hh, L2, C, 1, 100.0, True)
chk("跳空開在停損下：以開盤 90 出", abs(r3[H.EXITS.index("S5")] - (-10.0)) < 1e-9, r3)
Hb = Hh.copy(); Hb[2] = 106.0
r4 = H._exit_returns(O, Hb, L2, C, 1, 100.0, True)
chk("10 日 +5% 停利：第 2 天高點 106 觸發 +5%", abs(r4[H.EXITS.index("B10")] - 5.0) < 1e-9, r4)

# 2. 個股旗標：投信 day 70~72 連買、day 72 首買判斷；外資投信同買
n = 120
rng = np.random.default_rng(1)
Cs = 100 * np.cumprod(1 + rng.normal(0.001, 0.01, n))
Os, Hs, Ls, Vs = Cs * 0.999, Cs * 1.01, Cs * 0.99, np.full(n, 1_000_000.0)
t = np.zeros(n); t[70:73] = 100.0
f = np.zeros(n); f[72] = 50.0
dates = [f"2023-{1 + k // 28:02d}-{1 + k % 28:02d}" for k in range(n)]
a = H.stock_arrays(dates, Os, Hs, Ls, Cs, Vs, inst={"f": f, "t": t, "d": np.zeros(n), "m": np.zeros(n)})
row = {int(i): int(fl) for i, fl in zip(a["sig_idx"], a["flags"])}
bit = lambda k: 1 << H.FIDX[k]  # noqa: E731
chk("day 72：投信連買≥3日", row[72] & bit("t3"), bin(row[72]))
chk("day 70：投信首買；day 72 不是首買", (row[70] & bit("tfirst")) and not (row[72] & bit("tfirst")))
chk("day 72：外資投信同買", row[72] & bit("ft"))
chk("day 72：投信買超 100 張 ≥ 當日量 1,000 張的 5%", row[72] & bit("tbig"))
chk("day 75：沒有投信買超", not (row[75] & bit("t1")))
chk("R 欄位數＝2 進場 × 12 出場", a["R"].shape[1] == 24, a["R"].shape)
chk("R 已扣成本（隔天開盤賣 ≈ 漲跌 − 0.585）", abs(a["R"][list(a["sig_idx"]).index(80), 0] - ((Os[82] / Os[81] - 1) * 100 - H.COST)) < 1e-4)

# 3. 漲停鎖死不計
Cl = Cs.copy(); Ol = Os.copy(); Hl = Hs.copy()
Ol[91] = Cl[90] * 1.1; Hl[91] = Ol[91]; Cl[91] = Ol[91]
a2 = H.stock_arrays(dates, Ol, Hl, Ls, Cl, Vs)
i90 = list(a2["sig_idx"]).index(90)
chk("開盤即鎖漲停：開盤進、收盤進都不計", np.isnan(a2["R"][i90]).all(), a2["R"][i90])

# 4. 名次
day = np.array([0, 0, 0, 1, 1])
val = np.array([5.0, 9.0, 1.0, 3.0, 7.0])
chk("每天依成交值排名", H.day_rank(day, val).tolist() == [2, 1, 3, 2, 1], H.day_rank(day, val))

# 5. 搜尋：植入「投信首買→報酬+2%」的效果，應該被找到；安慰劑應該很少
N, ND = 40000, 1000
rng = np.random.default_rng(7)
dd = rng.integers(0, ND, N).astype(np.int32)
dts = [f"20{22 + k // 400}-{1 + (k % 400) // 34:02d}-{1 + k % 28:02d}" for k in range(ND)]
dts = sorted(dts)
Fx = np.zeros(N, dtype=np.int64)
for b in range(len(H.FLAGS)):
    Fx |= (rng.random(N) < 0.2).astype(np.int64) << b
R = rng.normal(-0.3, 4, (N, len(H.COLS))).astype(np.float32)
eff = (Fx >> H.FIDX["tfirst"]) & 1 == 1
R[eff] += 3.0
P = H.Panel(dd, Fx, R, np.ones(N, dtype=np.int32), np.ones(N, dtype=np.int32), dts)
res = H.analyze_universe(P, "測試", placebo_rounds=1, top_detail=3, max_k=2)
names = {x["條件"] for x in res["top"]}
chk("植入的投信首買被找到", any("投信首買" in s for s in names), list(names)[:5])
chk("通過的組合都含投信首買（其他是雜訊）", all("投信首買" in x["條件"] for x in res["top"]), [x["條件"] for x in res["top"] if "投信首買" not in x["條件"]][:5])
chk("安慰劑通過數很少", res["placebo_passed"][0] <= 2, res["placebo_passed"])
chk("細節含逐年與 t 值", "years" in res["top"][0] and res["top"][0].get("t_oos") is not None, res["top"][0])
chk("進場日旗標只配 E1", all(not (x["col"].startswith("E0") and any(k in x["條件"] for k in ("進場日",))) for x in res["top"]))
sys.exit(0 if ok else 1)
