"""空方第三關「反彈無力確認」硬條件版：10:00 前後收盤須低於當天開盤。"""
import sys
from warroom_core import evaluate_short_gate3_rebound

fails = []
def check(n, c):
    print(("PASS: " if c else "FAIL: ") + n)
    if not c: fails.append(n)

TIMES = [f"09:{m:02d}" for m in range(0, 60, 5)]
def mk(opens_closes, times=TIMES):
    return [{"bar_time": t, "open": o, "high": max(o, c), "low": min(o, c), "close": c, "volume": 100}
            for t, (o, c) in zip(times, opens_closes)]

# 開盤 100，最後收 99 → 通過
bars = mk([(100, 99.5)] + [(99.5, 99.4)] * 10 + [(99.4, 99.0)])
r = evaluate_short_gate3_rebound(bars)
check("收盤<開盤 → pass", r["verdict"] == "pass" and r["change_pct"] == -1.0)
# 最後收 100（=開盤）→ fail（反彈收復起點）
bars = mk([(100, 99)] + [(99, 99.5)] * 10 + [(99.5, 100)])
check("收盤=開盤 → fail", evaluate_short_gate3_rebound(bars)["verdict"] == "fail")
# 收 101 → fail
bars = mk([(100, 99)] + [(99, 100)] * 10 + [(100, 101)])
r = evaluate_short_gate3_rebound(bars)
check("收盤>開盤 → fail 且標籤說明", r["verdict"] == "fail" and "空單不進場" in r["label"])
# 資料不足一律 unknown（不擋單）
check("空資料 → unknown", evaluate_short_gate3_rebound([])["verdict"] == "unknown")
check("None → unknown", evaluate_short_gate3_rebound(None)["verdict"] == "unknown")
check("<6 根 → unknown", evaluate_short_gate3_rebound(mk([(100, 99)] * 5, TIMES[:5]))["verdict"] == "unknown")
late = [t for t in TIMES[3:]]  # 從 09:15 起，第一根不是開盤
check("第一根>09:05 → unknown", evaluate_short_gate3_rebound(mk([(100, 99)] * len(late), late))["verdict"] == "unknown")
early = TIMES[:6]  # 只到 09:25
check("最後一根<09:45 → unknown", evaluate_short_gate3_rebound(mk([(100, 99)] * 6, early))["verdict"] == "unknown")
# 缺值（open None）的列被跳過
bars = mk([(100, 99)] + [(99, 98.9)] * 10 + [(98.9, 98.5)])
bars[3]["open"] = None
check("缺值列被跳過仍能判斷", evaluate_short_gate3_rebound(bars)["verdict"] == "pass")
# 亂序輸入
bars = mk([(100, 99)] + [(99, 98.9)] * 10 + [(98.9, 98.5)])
bars.reverse()
check("亂序輸入先排序再判斷", evaluate_short_gate3_rebound(bars)["verdict"] == "pass")
# 排程端確實接線
src = open("system_scheduler.py", encoding="utf-8").read()
check("stage_intraday_execute 有呼叫第三關", "evaluate_short_gate3_rebound(_bars_by_sym" in src)
check("scheduler 有 import", "evaluate_930_three_gate, evaluate_short_gate3_rebound" in src)
if fails:
    print(f"\n{len(fails)} 項失敗"); sys.exit(1)
print("\n全部通過")
