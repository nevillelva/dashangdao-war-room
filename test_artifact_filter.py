#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_artifact_filter.py —— is_instant_exit_artifact：只擋「波段、進出價相同、報酬 0」的假單。"""
import sys
from warroom_core import is_instant_exit_artifact as f
cases = [
    ({"trade_type": "swing", "entry_price": 16.85, "exit_price": 16.85, "realized_roi": 0.0}, True),
    ({"trade_type": "swing", "entry_price": 16.85, "exit_price": 16.85, "realized_roi": 0}, True),
    ({"trade_type": "swing", "entry_price": 16.85, "exit_price": 17.0, "realized_roi": 0.89}, False),
    ({"trade_type": "swing", "entry_price": 16.85, "exit_price": 16.85, "realized_roi": 0.3}, False),   # 進出價同但有報酬(不合理資料)→不擋
    ({"trade_type": "intraday", "entry_price": 10, "exit_price": 10, "realized_roi": 0}, False),
    ({"trade_type": "swing_bt", "entry_price": 10, "exit_price": 10, "realized_roi": 0}, False),
    ({"trade_type": "swing", "entry_price": None, "exit_price": 10, "realized_roi": 0}, False),
    ({"trade_type": "swing", "entry_price": "x", "exit_price": 10, "realized_roi": 0}, False),
    ({"entry_price": 5, "exit_price": 5, "realized_roi": 0}, True),    # 缺 trade_type 視為舊波段單
]
ok = True
for r, want in cases:
    got = f(r)
    if got != want:
        ok = False
        print("❌", r, "got", got, "want", want)
print("✅ 假單判定正確" if ok else "❌ 失敗")
sys.exit(0 if ok else 1)
