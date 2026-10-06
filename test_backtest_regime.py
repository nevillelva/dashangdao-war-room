#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_backtest_regime.py —— 近 5 年盤勢回測（backtest_regime.py）：進場時機變體的成交邏輯、年度穩定、遮罩、單檔檢定、端到端結構。離線。"""
import os
import sys
os.environ["BT_LIQ"] = "0"
import json
import runpy
import numpy as np
import pandas as pd

import backtest_rules as br
import backtest_sector as bs
import backtest_regime as bg
import regime as rg

bad = 0


def check(ok, msg):
    global bad
    print("✅" if ok else "❌", msg)
    bad += (not ok)


def mk_df(rows):
    """rows: [(o,h,l,c)]；補足長度到 40 天（尾端平盤），讓 MAX_HOLD 夠用。"""
    rows = list(rows) + [(rows[-1][3],) * 4] * (40 - len(rows))
    idx = pd.bdate_range("2026-01-05", periods=len(rows))
    a = np.array(rows, dtype=float)
    return pd.DataFrame({"Open": a[:, 0], "High": a[:, 1], "Low": a[:, 2], "Close": a[:, 3], "Volume": 1e6}, index=idx)


J = {e: i for i, e in enumerate(bs.EXITS)}
COST = bs.COST["long"]
NO = bs.NO

# ---------- 1) open 模式與既有引擎逐筆一致
px = br.synthetic_prices(n=2, days=500, seed=9)
df = next(iter(px.values()))
t = np.array([50, 80, 120, 200, 300])
R0, d0 = bs.simulate(df, t, "long")
R1, d1, keep, E = bg.simulate_entry(df, t, "open")
check(np.array_equal(R0, R1) and bool(keep.all()) and (d0 == d1).all(), "open 模式＝backtest_sector.simulate（逐筆一致）")

# ---------- 2) 限價低接：盤中成交（開盤高於限價、當日最低碰到）
rows = [(100, 100, 100, 100)] * 3
rows += [(100, 100, 100, 100)]                     # t=3：訊號日收盤 100
rows += [(100.5, 101, 98.5, 100)]                  # e=4：開盤 100.5 > L=99；最低 98.5 ≤ 99 → 以 99 成交；收盤 100
rows += [(100, 103.5, 99.5, 103)]                  # 次日最高 103.5 ≥ 99×1.03=101.97 → 停利
df = mk_df(rows)
R, dts, keep, E = bg.simulate_entry(df, np.array([3]), "limit", 0.01)
check(bool(keep[0]) and abs(E[0] - 99.0) < 1e-9, "限價單盤中觸及：以限價 99 成交")
j = J[(0.03, NO, 5)]
# 進場當天停利只認收盤：收盤 100/99-1=1.01% <3% 不算；次日最高 103.5/99-1=4.5% ≥3% → 以停利 3% 出場（開盤 100/99-1=1.01%<3% 不跳空）
check(abs(R[0, j] - (0.03 - COST)) < 1e-6, "進場當天最高價不計停利、次日觸及才算（保守）→ 報酬＝3%−成本")

# 同樣情境但「進場當天最高價就很高」：限價成交時不得因當天最高價就停利（不知道先後）
rows2 = list(rows)
rows2[4] = (100.5, 104, 98.5, 100)
df2 = mk_df(rows2[:5] + [(100, 100, 99.8, 100)] * 3)
R2, _, _, _ = bg.simulate_entry(df2, np.array([3]), "limit", 0.01)
check(R2[0, j] < 0.03 - COST - 1e-9, "限價盤中成交：進場當天最高價 104 也不算停利（只認收盤）")

# ---------- 3) 限價低接：開盤就跳空低於限價 → 以開盤成交；沒碰到 → 不成交
rows3 = [(100, 100, 100, 100)] * 4 + [(98.0, 99.5, 97.5, 99.0)] + [(99, 99, 99, 99)] * 5
df3 = mk_df(rows3)
R3, _, keep3, E3 = bg.simulate_entry(df3, np.array([3]), "limit", 0.01)
check(bool(keep3[0]) and abs(E3[0] - 98.0) < 1e-9, "開盤 98 已低於限價 99 → 以開盤 98 成交（不會買得比開盤貴）")
rows4 = [(100, 100, 100, 100)] * 4 + [(100.2, 101, 99.6, 100.5)] + [(100, 100, 100, 100)] * 5
df4 = mk_df(rows4)
R4, _, keep4, _ = bg.simulate_entry(df4, np.array([3]), "limit", 0.01)
check(not bool(keep4[0]) and len(R4) == 0, "最低 99.6 沒碰到限價 99 → 不成交、不留報酬")

# ---------- 4) 次日收盤確認
rows5 = [(100, 100, 100, 100)] * 4 + [(100, 102, 99.5, 101.5)] + [(101.5, 106, 101, 105)] + [(105, 105, 105, 105)] * 4
df5 = mk_df(rows5)
R5, d5, keep5, E5 = bg.simulate_entry(df5, np.array([3]), "confirm")
check(bool(keep5[0]) and abs(E5[0] - 101.5) < 1e-9, "收紅(101.5≥開100)且不低於訊號日收盤(100) → 以當日收盤 101.5 進場")
j2 = J[(0.03, NO, 5)]
check(abs(R5[0, j2] - (0.03 - COST)) < 1e-6, "從次日起算出場：次日最高 106 ≥ 101.5×1.03 → 停利 3%")
rows6 = [(100, 100, 100, 100)] * 4 + [(101, 101.2, 99, 99.5)] + [(100, 100, 100, 100)] * 5
R6, _, keep6, _ = bg.simulate_entry(mk_df(rows6), np.array([3]), "confirm")
check(not bool(keep6[0]) and len(R6) == 0, "收黑(99.5<開101) → 不進場")
rows7 = [(100, 100, 100, 100)] * 4 + [(98, 100, 97.5, 99)] + [(100, 100, 100, 100)] * 5
R7, _, keep7, _ = bg.simulate_entry(mk_df(rows7), np.array([3]), "confirm")
check(not bool(keep7[0]), "收紅但收盤(99)低於訊號日收盤(100) → 不進場")

# ---------- 5) 年度穩定
es = np.datetime64("2021-01-01", "ns").astype(np.int64)
yr = bg.YEAR_NS
rng = np.random.default_rng(1)
dts = np.concatenate([es + y * yr + np.arange(40) * 86400 * 10**9 * 3 for y in range(5)])
good = np.concatenate([np.where(rng.random(40) < 0.7, 0.01, -0.01) for _ in range(5)])
ok, okq, totq = bg.year_ok(dts, good, es)
check(ok and totq == 5, f"每年勝率約 70% → 年度穩定（{okq}/{totq}）")
badr = np.concatenate([np.where(rng.random(40) < (0.7 if y < 2 else 0.3), 0.01, -0.01) for y in range(5)])
ok2, okq2, totq2 = bg.year_ok(dts, badr, es)
check(not ok2, f"只有 2/5 個年度勝率>50% → 不算穩定（{okq2}/{totq2}）")
few = dts[:30]
ok3, _, tot3 = bg.year_ok(few, good[:30], es)
check(not ok3 and tot3 < 3, "有效年度不足 3 個 → 不算穩定")

# ---------- 6) 遮罩（族群×盤勢；not: 前綴）
sec = np.array([0, 0, 1, 1, 0], dtype=np.int16)
bits = np.array([1 | (1 << rg.REGIME_BIT["up60"]), 1, 1 | (1 << rg.REGIME_BIT["up60"]), 1, 1 | (1 << rg.REGIME_BIT["up60"])], dtype=np.uint32)
cat = {("long", "F"): (sec, np.arange(5, dtype=np.int64), np.zeros((5, len(bs.EXITS)), np.float32), bits, np.arange(5, dtype=np.int32), np.zeros(5, np.float32))}
m, *_ = bg.msk(cat, ("long", "F"), 0, "up60")
check(m.tolist() == [True, False, False, False, True], "族群0 且 up60：第 1、5 筆")
m, *_ = bg.msk(cat, ("long", "F"), 0, "not:up60")
check(m.tolist() == [False, True, False, False, False], "族群0 且 up60 不成立：第 2 筆")
m, *_ = bg.msk(cat, ("long", "F"), bg.ALL_ID, None)
check(bool(m.all()), "全體、不分盤勢：全部")

# ---------- 7) 單檔檢定：樣本內好的個股 → 樣本外有優勢才會被看出來
rng = np.random.default_rng(5)
n_sym, per = 60, 24
symi, d, r = [], [], []
skill = rng.random(n_sym) < 0.5          # 一半個股「真的」勝率 65%，另一半 35%
sp = es + 3 * yr
for s in range(n_sym):
    for k in range(per):
        symi.append(s)
        d.append(es + int((k / per) * 5 * yr))
        r.append(0.01 if rng.random() < (0.65 if skill[s] else 0.35) else -0.01)
symi, d, r = np.array(symi), np.array(d, dtype=np.int64), np.array(r)
p = bg.symbol_persistence(d, r, symi, es, sp, min_is=6)
check(p is not None and p["is_good"]["win_oos"] > p["is_bad"]["win_oos"] + 0.05, "個股有真實差異時：樣本內好的那群，樣本外勝率明顯較高（單檔閘門有意義）")
r_flat = np.where(rng.random(len(r)) < 0.5, 0.01, -0.01)
p2 = bg.symbol_persistence(d, r_flat, symi, es, sp, min_is=6)
check(p2 is None or abs(p2["is_good"]["win_oos"] - p2["is_bad"]["win_oos"]) < 0.08, "個股其實沒差異時：兩群樣本外勝率接近（沒有假優勢）")
check(bg.symbol_persistence(d[:50], r[:50], symi[:50], es, sp) is None, "個股太少 → 回 None（不亂下結論）")

# ---------- 8) 端到端（小母體合成資料）：結構完整、build_ref 可序列化
orig = br.synthetic_prices
br.synthetic_prices = lambda: orig(n=14, days=900, seed=4)
out = "/tmp/_bg_test.json"
sys.argv = ["backtest_regime.py", "--synthetic", "--null-k", "3", "--json-out", out]
runpy.run_module("backtest_regime", run_name="__main__")
br.synthetic_prices = orig
rep = json.load(open(out))
for side in ("long", "short"):
    for k in ("pairs", "regime_lift_base_all", "best_by_sector", "live_rules", "random_sector_regime", "symbol_persistence", "regime_table_base_all"):
        check(k in rep[side], f"報告[{side}] 含 {k}")
check("entry_timing" in rep and len(rep["entry_timing"]["modes"]) >= 4, "報告含進場時機比較（各家族×盤勢）")
modes = next(iter(rep["entry_timing"]["modes"].values()))
check(modes[0]["mode"].startswith("隔日開盤") and modes[0]["fill_rate"]["IS"] == 1.0, "第一列是現行隔日開盤、成交率 100%")
lim = [x for x in modes if x["mode"].startswith("限價低接 -1%")]
check(bool(lim) and 0 < lim[0]["fill_rate"]["IS"] < 1.0, "限價低接有成交率（<100%）")
ref = bg.build_ref(rep)
js = json.dumps(ref, ensure_ascii=False)
check(len(js) < 400_000 and "long" in ref and "sectors" in ref["long"], f"精簡參考表可序列化且大小合理（{len(js) // 1000} KB）")
check(set(rep["regimes"]) == set(rg.REGIME_NAMES), "報告含全部盤勢定義")

print(f"\n{'全部通過' if not bad else str(bad) + ' 項失敗'}")
raise SystemExit(1 if bad else 0)
