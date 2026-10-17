"""【2026-10-15】三關第二關：自己就是產業龍頭 → 不適用（不再『缺龍頭資料』、不卡 pending）。"""
import sys
import warroom_core as wc

fails = []


def check(n, c):
    print(("PASS: " if c else "FAIL: ") + n)
    if not c:
        fails.append(n)


times = [f"09:{m:02d}" for m in range(0, 60, 5)]
px = [100, 100.4, 100.8, 101.0, 101.4, 101.6, 101.9, 102.0, 102.2, 102.3, 102.5, 102.6]
bars = [{"bar_time": t, "open": p, "high": p + 0.3, "low": p - 0.3, "close": p + 0.2, "volume": 1000 + 50 * i}
        for i, (t, p) in enumerate(zip(times, px))]

wc.LONG_GATE23_VETO = True
no_leader = wc.evaluate_930_three_gate(bars, None, stock_day_open=100.0, end_label=True)
check("（開關打開時）沒龍頭資料且不是龍頭 → 第二關 unknown、pending（原防呆）",
      (no_leader["gate2"] or {}).get("verdict") == "unknown" and no_leader["overall_verdict"] == "pending")
wc.LONG_GATE23_VETO = False
no_leader2 = wc.evaluate_930_three_gate(bars, None, stock_day_open=100.0, end_label=True)
check("（2026-10-17 預設）多方第二關缺資料只當參考，不卡 pending，且有標註",
      no_leader2["overall_verdict"] != "pending" and "不否決" in (no_leader2.get("gate23_advisory") or "") and "回測" in (no_leader2.get("gate23_advisory") or ""))
selfl = wc.evaluate_930_three_gate(bars, None, stock_day_open=100.0, end_label=True, self_is_leader=True)
check("自己就是龍頭 → 第二關 not_applicable", (selfl["gate2"] or {}).get("verdict") == "not_applicable")
check("自己就是龍頭 → 不再卡 pending，標籤不含『缺龍頭資料』", selfl["overall_verdict"] != "pending" and "缺龍頭" not in selfl["overall_label"])
shortl = wc.evaluate_930_three_gate(bars, None, direction="short", stock_day_open=100.0, end_label=True, self_is_leader=True)
check("空方且自己是龍頭 → 不報缺龍頭資料", "缺龍頭" not in shortl["overall_label"])
check("固定龍頭判斷：華新是電器電纜龍頭", wc.is_fixed_industry_leader("1605", {"1605": "電器電纜", "1608": "電器電纜"}))
check("非龍頭不誤判", not wc.is_fixed_industry_leader("1608", {"1605": "電器電纜", "1608": "電器電纜"}))
if fails:
    print(f"\n{len(fails)} 項失敗"); sys.exit(1)
print("\n全部通過")
