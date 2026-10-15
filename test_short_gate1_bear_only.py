"""【2026-10-15】空單第一關必須明確偏空才進場（空方無第三關）；多方行為不變。"""
import sys
import pandas as pd
import warroom_core as wc

fails = []


def check(n, c):
    print(("PASS: " if c else "FAIL: ") + n)
    if not c:
        fails.append(n)


times = [f"09:{m:02d}" for m in range(0, 60, 5)]


def mk(px, vol_scale=1.0):
    return [{"bar_time": t, "open": p, "high": p + 0.3, "low": p - 0.3, "close": p + 0.1, "volume": 1000 * vol_scale}
            for t, p in zip(times, px)]


flat = mk([100.0] * 12)
# 逐一以假的 gate1 驗證空方分支（直接替換 evaluate_930_gate1 的回傳）
orig = wc.evaluate_930_gate1


def run(verdict, direction):
    wc.evaluate_930_gate1 = lambda df, end_label=False: {"verdict": verdict, "label": verdict}
    try:
        return wc.evaluate_930_three_gate(flat, None, direction=direction, stock_day_open=100.0, end_label=True,
                                          self_is_leader=True)
    finally:
        wc.evaluate_930_gate1 = orig


for v in ("unclear",):
    r = run(v, "short")
    check("空方 + 第一關方向不明 → fail", r["overall_verdict"] == "fail")
    check("空方 + 方向不明 → 標籤說明須明確偏空", "明確偏空" in r["overall_label"])
for v in ("weak_bull", "strong_bull"):
    check(f"空方 + {v} → 仍 fail（原行為）", run(v, "short")["overall_verdict"] == "fail")
for v in ("weak_bear", "strong_bear"):
    r = run(v, "short")
    check(f"空方 + {v} → 繼續往下、不被第一關擋（自己是龍頭→第二關不適用→pass）", r["overall_verdict"] == "pass")
r = run("unclear", "long")
check("多方 + 方向不明 → 不被這次修改影響（不因第一關 fail）", r["overall_verdict"] != "fail" or "明確偏空" not in r["overall_label"])
if fails:
    print(f"\n{len(fails)} 項失敗"); sys.exit(1)
print("\n全部通過")
