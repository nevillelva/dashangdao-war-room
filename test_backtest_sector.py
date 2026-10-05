import os
os.environ.setdefault("BT_LIQ", "0")
os.environ.setdefault("BT_MIN_N_IS", "10")
os.environ.setdefault("BT_MIN_N_OOS", "5")
import numpy as np
import pandas as pd
import backtest_rules as br
import backtest_winrate_tuning as bt
import backtest_sector as bs

ok = 0
def check(c, m):
    global ok
    assert c, m
    ok += 1
    print("✅", m)

# 1) 做多：與既有（已驗證）引擎 bt.simulate_family 逐筆一致
px = br.synthetic_prices(n=2, days=400, seed=3)
df = list(px.values())[0]
idx = np.array([50, 90, 130, 200, 260], dtype=int)
bt.TP_GRID, bt.SL_GRID, bt.HOLD_GRID, bt.MAX_HOLD = bs.TP_GRID, bs.SL_GRID, bs.HOLD_GRID, bs.MAX_HOLD
ref, ref_dates = bt.simulate_family(df, idx)
R, dts = bs.simulate(df, idx, "long")
bad = 0
for j, key in enumerate(bs.EXITS):
    if not np.allclose(R[:, j], ref[key].astype(np.float32), atol=1e-6):
        bad += 1
check(bad == 0 and R.shape == (5, len(bs.EXITS)) and list(dts) == list(ref_dates), "做多成交模擬與既有引擎 105 組出場逐筆一致")

# 2) 做空：手算
def mk(rows):
    return pd.DataFrame(rows, columns=["Open", "High", "Low", "Close", "Volume"],
                        index=pd.bdate_range("2026-01-02", periods=len(rows)))
flat = [[100, 101, 99, 100, 1000]] * 30
def run(day_rows, tp, sl, hold, side="short"):
    rows = [list(r) for r in flat]
    # 訊號日=index 0；進場=index 1 開盤 100
    for k, r in day_rows.items():
        rows[k] = r
    d = mk(rows)
    old = bs.EXITS
    bs.EXITS = [(tp, sl, hold)]
    try:
        R, _ = bs.simulate(d, np.array([0]), side)
    finally:
        bs.EXITS = old
    return float(R[0, 0])
c_s = bs.COST["short"]; c_l = bs.COST["long"]
# 做空停利：第3天最低 94(跌6%)、最高 101 → 停利5%
check(abs(run({3: [100, 101, 94, 95, 1000]}, 0.05, 0.08, 10) - (0.05 - c_s)) < 1e-6, "做空：跌破停利價 → +5% 扣成本")
# 做空停損：最高 109(漲9%) → 停損8%
check(abs(run({3: [100, 109, 99, 108, 1000]}, 0.05, 0.08, 10) - (-0.08 - c_s)) < 1e-6, "做空：漲到停損價 → -8% 扣成本")
# 做空跳空停損：開盤直接 112 > 停損價108 → 以開盤價成交 -12%
check(abs(run({3: [112, 113, 111, 112, 1000]}, 0.05, 0.08, 10) - (-0.12 - c_s)) < 1e-6, "做空：向上跳空越過停損 → 以開盤價(-12%)成交")
# 同日兩者皆觸及 → 先停損
check(abs(run({3: [100, 109, 94, 100, 1000]}, 0.05, 0.08, 10) - (-0.08 - c_s)) < 1e-6, "做空：同日同時觸及停利停損 → 先停損(保守)")
# 持有到期：第5天收 97 → 做空 +3%
rows = {k: [100, 100.5, 99.5, 100, 1000] for k in range(1, 12)}
rows[5] = [100, 100.5, 96.5, 97, 1000]
check(abs(run(rows, 9.99, 9.99, 5) - (0.03 - c_s)) < 1e-6, "做空：持有5日到期收盤 97 → +3% 扣成本")
# 做多對照：同樣停利/停損
check(abs(run({3: [100, 106, 99, 105, 1000]}, 0.05, 0.08, 10, "long") - (0.05 - c_l)) < 1e-6, "做多：漲到停利 → +5% 扣成本")
check(c_s > c_l and abs(c_s - c_l - bs.SHORT_EXTRA_COST) < 1e-12, "做空成本 = 做多成本 + 融券額外成本")

# 3) 統計
check(abs(bs.wilson_lo(50, 100) - 0.4038) < 0.002 and bs.wilson_lo(0, 0) == 0.0, "Wilson 下界")
n, w, e = bs.win_stats(np.array([[0.1, -0.1], [0.2, -0.2], [-0.1, 0.3]]))
check(n == 3 and abs(w[0] - 2 / 3) < 1e-9 and abs(e[0] - 0.2 / 3) < 1e-9 and abs(e[1]) < 1e-9, "win_stats 向量化正確")

# 4) 空方訊號只用過去資料（因果性）：截斷未來後，同一天的訊號不變
px = br.synthetic_prices(n=1, days=500, seed=11)
d = list(px.values())[0]
a = bs.short_signals(d)
b = bs.short_signals(d.iloc[:400])
same = all(np.array_equal(a[k][:400], b[k]) for k in a)
check(same, "空方訊號因果（截斷未來資料後前 400 天訊號不變）")
fa = bt.market_breadth({"x": d})
la = bs.long_families(d, fa)
lb = bs.long_families(d.iloc[:420], bt.market_breadth({"x": d.iloc[:420]}))
# 只比較「離截點夠遠」的訊號索引
cut = 420 - 40
same_l = all(set(i for i in la[k] if i < cut - 30) == set(i for i in lb[k] if i < cut - 30) for k in la if k in lb)
check(same_l, "多方家族訊號因果（截斷未來資料後較早的訊號不變）")

# 5) 端到端（合成資料，小母體）：報告結構完整、虛無家族有被計算
px = br.synthetic_prices(n=14, days=1000, seed=5)
syms = list(px)
names = ["甲", "乙"]
sid = {s: i % 2 for i, s in enumerate(syms)}
breadth = bt.market_breadth(px)
import time
class A: tag = ""; eval_years = 2; top = 50
acc = bs.build_acc(px, sid, breadth, ["long", "short"], time.time())
end = max(x.index[-1] for x in px.values())
es = end - pd.Timedelta(days=730)
days = sorted({x for x in {d for df_ in px.values() for d in df_.index} if x >= es})
sp = days[int(len(days) * 0.6)]
rep = bs.build_report(acc, px, names, sid, es, sp, A, "synthetic", time.time())
for side in ("long", "short"):
    r = rep[side]
    check(all(k in r for k in ("n_tests", "n_passed", "pairs", "null_calibration", "selected_oos_signals", "passed_top", "tierB_top", "sector_ref")), f"{side} 報告欄位齊全")
    check(r["pairs"]["null_tested"] > 0, f"{side} 虛無家族有被評估（{r['pairs']['null_tested']} 組）")
    check(any(x["sector"] == bs.ALL_NAME for x in r["sector_ref"]), f"{side} 含全體市場對照列")
for side in ("long", "short"):
    r = rep[side]
    check(all(k in r for k in ("family_summary", "live_rules", "best_exits")), f"{side} 含規則層級/實盤規則/可行出場")
    check(r["family_summary"]["null_rate_pct"]["n_families"] > 0, f"{side} 虛無家族分布有算出")
ref = bs.build_ref(rep)
check("long" in ref and "short" in ref and ref["live_exit"] == "停利12%/停損15%/20日", "精簡參考表結構")
first = next(iter(ref["long"]["sectors"].values()))
check(set(first) == {"n_symbols", "random", "live_rules", "best_exits"}, "參考表族群欄位")
import json as _j
check(len(_j.dumps(ref, ensure_ascii=False)) < 200000, "參考表大小合理")
check(rep["window"]["eval_years"] == 2 and rep["cost"]["short"] > rep["cost"]["long"], "視窗/成本資訊")
print(f"\n{ok} 項通過")
