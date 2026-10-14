"""【2026-10-14】自助回測引擎測試（離線、合成資料；真值已知）。"""
import sys
import numpy as np
import pandas as pd
import bt_lab as B
from bt_lab_testdata import mk_panel

fails = []


def check(name, cond):
    print(("PASS: " if cond else "FAIL: ") + name)
    if not cond:
        fails.append(name)


# ---- 小例子：手算 ----
d = pd.bdate_range("2024-01-01", periods=40)
c = np.full(40, 100.0)
c[10:] = 100.0
o = c.copy(); h = c.copy() * 1.0; l = c.copy() * 1.0
sc = np.zeros(40); sc[9] = 7; sc[10] = 7   # 第 9 日首次跨越；第 10 日仍高（不是 first_cross）
o[10] = 101.0                               # 第 10 日開盤 101 → 進場價
c[15:] = 110.0; o[15:] = 110.0; h[15:] = 110.0; l[15:] = 110.0
P = pd.DataFrame({"date": d, "sym": "A", "open": o, "high": h, "low": l, "close": c, "score": sc})
D = B.prepare(P)
sp = B.Spec(score_min=6, mode="first_cross", entry_at="next_open", exits=[{"type": "hold", "n": 5}])
ent = B.find_entries(D, sp)
check("first_cross 只抓第一天（索引 9），第 10 日不重複", ent == [(0, 9)])
cost = B.round_trip_cost(sp)
tr, open_n = B.run_exit(D, ent, {"type": "hold", "n": 5}, sp, cost)
check("進場價＝訊號日隔日『開盤』101、持有 5 日後收盤 110", abs(tr["gross"].iloc[0] - (110 / 101 - 1)) < 1e-9)
check("成本＝來回手續費＋證交稅", abs(cost - (2 * 0.001425 + 0.003)) < 1e-12 and abs(tr["net"].iloc[0] - (tr["gross"].iloc[0] - cost)) < 1e-12)
sp2 = B.Spec(score_min=6, mode="every_day", entry_at="next_open")
check("every_day 同檔持有中不重複進場", len(B.run_exit(D, B.find_entries(D, sp2), {"type": "hold", "n": 5}, sp2, cost)[0]) == 1)
sp3 = B.Spec(score_min=6, mode="first_cross", entry_at="next_close", exits=[])
tr3, _ = B.run_exit(D, B.find_entries(D, sp3), {"type": "hold", "n": 5}, sp3, cost)
check("next_close：進場價＝隔日收盤 100", abs(tr3["gross"].iloc[0] - (110 / 100 - 1)) < 1e-9)
tr4, op = B.run_exit(D, ent, {"type": "hold", "n": 60}, sp, cost)
check("資料不夠長的部位不算已出場（不用期末價硬算）", tr4.empty and op == 1)

# 停利停損：同日同時碰到 → 先停損；跳空用開盤價
h2, l2, o2, c2 = h.copy(), l.copy(), o.copy(), c.copy()
h2[11] = 101 * 1.2; l2[11] = 101 * 0.8; c2[11] = 101.0
P2 = pd.DataFrame({"date": d, "sym": "A", "open": o2, "high": h2, "low": l2, "close": c2, "score": sc})
D2 = B.prepare(P2)
r = B.simulate_one(D2, 0, 10, 101.0, {"type": "tpsl", "tp": 0.1, "sl": 0.1, "n": 20}, sp)
check("同日同時碰停利停損 → 判停損", r[2] == "sl" and abs(r[1] - 101 * 0.9) < 1e-9)
o3 = o.copy(); o3[12] = 85.0; l3 = l.copy(); l3[12] = 84.0
P3 = pd.DataFrame({"date": d, "sym": "A", "open": o3, "high": h, "low": l3, "close": c, "score": sc})
r = B.simulate_one(B.prepare(P3), 0, 10, 101.0, {"type": "tpsl", "tp": 0.1, "sl": 0.1, "n": 20}, sp)
check("向下跳空越過停損 → 以開盤價成交", r[2] == "sl" and abs(r[1] - 85.0) < 1e-9)

# 分數出場：決定在 k 日收盤後，k+1 日開盤成交
sc4 = sc.copy(); sc4[9:13] = 7.0; sc4[13] = 1.0
P4 = pd.DataFrame({"date": d, "sym": "A", "open": o, "high": h, "low": l, "close": c, "score": sc4})
r = B.simulate_one(B.prepare(P4), 0, 10, 101.0, {"type": "score_below", "y": 2}, sp)
check("分數跌破 → 隔日開盤成交（不偷看當天）", r[0] == 14 and r[2] == "score")

# ---- 隨機資料：沒有效果不該通過；有效果應通過 ----
for eff, name in ((0.0, "無效果"), (0.006, "有效果")):
    Pn = mk_panel(effect=eff, seed=3)
    Dn = B.prepare(Pn)
    res = B.run(Dn, B.Spec(score_min=6, exits=[{"type": "hold", "n": 10}]))
    v = res["results"]["持有10日"]
    s = v["summary"]
    print(f"   [{name}] n={s['n']} 訊號日={s['signal_days']} 平均淨={s['mean_net'] * 100:.2f}% 勝率={s['win'] * 100:.0f}% 判定={v['verdict']} t={s['t_cluster']:.2f}")
    if eff == 0.0:
        check("無效果：不應判『通過』", v["verdict"] != "通過")
        check("無效果：樣本外淨報酬應 ≤0（成本拖累）", s["oos"]["mean_net"] <= 0.003)
    else:
        check("有效果：判『通過』", v["verdict"] == "通過")
        check("有效果：勝過隨機挑", s["excess_random"] > 0.02)
        check("有效果：有最大回撤等權曲線", s["equity"] is not None and s["equity"]["mdd"] < 0)

# 樣本不足
Ps = mk_panel(n_sym=5, n_days=120, effect=0.01, seed=5)
vs = B.run(B.prepare(Ps), B.Spec(score_min=6, exits=[{"type": "hold", "n": 5}]))["results"]["持有5日"]
check("樣本太少 → 待觀察而非通過", vs["verdict"] == "待觀察")
check("所有出場類型都能跑完", all(B.run(B.prepare(mk_panel(n_sym=20, n_days=200, effect=0.004, seed=9)),
      B.Spec(exits=[{"type": "hold", "n": 5}, {"type": "tpsl", "tp": 0.12, "sl": 0.1, "n": 20}, {"type": "ma20"}, {"type": "atr", "k": 3}, {"type": "score_below", "y": 2}]))["results"].values()))

if fails:
    print(f"\n{len(fails)} 項失敗"); sys.exit(1)
print("\n全部通過")
