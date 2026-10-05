#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_sector_panel_ui.py —— 「🏷️ 依族群看勝率」面板（Streamlit AppTest、無需金鑰）：有/無參考表、有/無實績都不丟例外，數字出現在表格。"""
import os
os.environ.setdefault("SUPABASE_URL", "http://x")
os.environ.setdefault("SUPABASE_KEY", "x")
from streamlit.testing.v1 import AppTest

HARNESS = '''
import dashangdao_helpers as H
W = lambda n, w, e: {"n": n, "win": w, "exp_pct": e}
REF = {"live_exit": "停利12%/停損15%/20日", "window": {"eval_start": "2024-10-05", "split": "2025-12-16", "end": "2026-10-05"},
       "market_context": {"eqw_IS_pct": 8.4, "eqw_OOS_pct": 65.7}, "cost": {"long": 0.585, "short": 0.685},
       "long": {"sectors": {"金融保險": {"n_symbols": 26, "random": {"IS": W(286, .566, .37), "OOS": W(197, .65, 2.76)},
                  "live_rules": {"pullback_burst": {"IS": W(40, .6, 1.0), "OOS": W(25, .64, 2.0), "gate_ok": True, "n_enough": True}},
                  "best_exits": [{"kind": "random_entry", "label": "停利8%/停損15%/20日", "IS": W(286, .6, .5), "OOS": W(197, .7, 3.0)}]}}},
       "short": {"sectors": {}}}
BY = {"old": [{"sector": "半導體業", "side": "long", "n": 12, "wins": 3, "win_pct": 25.0, "avg_roi_pct": -0.66},
              {"sector": "半導體業", "side": "short", "n": 4, "wins": 3, "win_pct": 75.0, "avg_roi_pct": 0.4}],
      "bt": [{"sector": "金融保險", "side": "long", "n": 2, "wins": 2, "win_pct": 100.0, "avg_roi_pct": 5.0}]}
mode = MODE
if mode == "full":
    H.render_sector_winrate_panel(BY, REF)
elif mode == "noref":
    H.render_sector_winrate_panel(BY, {})
else:
    H.render_sector_winrate_panel({}, {})
'''
ok = True
def check(c, m):
    global ok
    if not c:
        ok = False
        print("❌", m)
    else:
        print("✅", m)

for mode in ("full", "noref", "empty"):
    at = AppTest.from_string(HARNESS.replace("MODE", repr(mode)), default_timeout=30).run()
    check(not at.exception, f"{mode}：無例外 {[e.value for e in at.exception]}")
    check(len(at.expander) == 1, f"{mode}：只有一個 expander（沒有巢狀）")
    txt = " ".join(str(m.value) for m in at.markdown) + " ".join(str(c.value) for c in at.caption) + " ".join(str(i.value) for i in at.info)
    if mode == "full":
        check(len(at.dataframe) >= 3, f"full：實績表＋表1＋表2 共 {len(at.dataframe)} 張")
        check("半導體業" in str(at.dataframe[0].value) and "⚠️太少" in str(at.dataframe[0].value), "full：實績表含族群與『太少』標示")
        check(len(at.radio) == 1, "full：有方向切換")
        at.radio[0].set_value("🔵 做空").run()
        check(not at.exception and any("沒有回測資料" in str(c.value) for c in at.caption), "full：切到做空且無資料 → 提示")
    elif mode == "noref":
        check("尚未產生族群回測參考表" in txt, "noref：提示尚未產生")
    else:
        check("沒有已實現交易" in txt, "empty：提示沒有實績")
print("OK" if ok else "FAIL")
sys_exit = 0 if ok else 1
import sys; sys.exit(sys_exit)
