#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_symbol_winrate.py —— 單檔勝率純函式（warroom_core.symbol_winrate_table 等）。"""
import sys
import warroom_core as wc

F = []


def check(n, c, e=""):
    print(("  ✅ " if c else "  ❌ ") + n + ("" if c else f" {e}"))
    if not c:
        F.append(n)


lo, hi = wc.wilson_interval(0, 0)
check("n=0 → (0,1)", (lo, hi) == (0.0, 1.0))
lo, hi = wc.wilson_interval(2, 2)
check("2/2 的 Wilson 下界 <0.5（樣本少不可靠）", lo < 0.5 and hi == 1.0, f"{lo},{hi}")
lo, hi = wc.wilson_interval(60, 100)
check("60/100 區間在 0.5~0.7 之間", 0.49 < lo < 0.6 < hi < 0.70, f"{lo},{hi}")

tr = [{"symbol": "2330", "ret_pct": 5}, {"symbol": "2330", "ret_pct": -2}, {"symbol": "2330", "ret_pct": 3},
      {"symbol": "2317", "ret_pct": 4}, {"symbol": "2317", "ret_pct": 6},
      {"symbol": "1101", "ret_pct": -4}, {"symbol": "", "ret_pct": 9}, {"symbol": "9999", "ret_pct": None}]
rows = wc.symbol_winrate_table(tr, prior_rate=0.5, prior_strength=5)
d = {r["symbol"]: r for r in rows}
check("略過空代號/無報酬", set(d) == {"2330", "2317", "1101"})
check("2330: 2/3 勝、平均 +2%", d["2330"]["n"] == 3 and d["2330"]["wins"] == 2 and abs(d["2330"]["avg_pct"] - 2.0) < 1e-9)
check("2317 原始 100% 但收縮後 <100%（(2+2.5)/7=64.3%）", d["2317"]["win_rate"] == 100.0 and abs(d["2317"]["shrunk_win_rate"] - 64.3) < 0.06, str(d["2317"]))
check("1101 原始 0% 收縮後 >0%（2.5/6=41.7%）", d["1101"]["win_rate"] == 0.0 and abs(d["1101"]["shrunk_win_rate"] - 41.7) < 0.06)
check("依筆數排序", [r["symbol"] for r in rows][0] == "2330")
check("可信度標籤", wc.reliability_label(1).startswith("⚪") and wc.reliability_label(3).startswith("🟡")
      and wc.reliability_label(5) == "🟢 可參考" and wc.reliability_label(10) == "🟢🟢 樣本充足")
check("部位倍數：同整體 → 1.0", wc.position_scale_from_winrate(55, 55) == 1.0)
check("部位倍數：上限 1.5／下限 0.5", wc.position_scale_from_winrate(100, 50) == 1.5 and wc.position_scale_from_winrate(0, 50) == 0.5)
check("部位倍數：缺資料 → 1.0", wc.position_scale_from_winrate(None, 50) == 1.0)
check("空輸入", wc.symbol_winrate_table([]) == [])
if F:
    print("\n❌ 失敗：", F)
    sys.exit(1)
print("\n✅ 單檔勝率純函式全部通過")
