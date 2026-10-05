#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_perf_batch.py —— log_perf 批次寫入：<8 筆不寫、第 8 筆一次寫入 8 筆、超過 90 秒也會寫出。"""
import time, sys, types
try:
    import openai  # noqa: F401
except ImportError:   # 本機沒裝 openai 時用空殼（log_perf 與它無關）
    _m = types.ModuleType("openai"); _m.OpenAI = object; sys.modules["openai"] = _m
import dashangdao_helpers as h

calls = []
class _T:
    def insert(self, rows):
        calls.append(rows); return self
    def execute(self): return None
class _C:
    def table(self, name): assert name == "perf_log"; return _T()
h.SUPABASE_ENABLED = True
h.SUPABASE_CONN = _C()
ok = True
for i in range(7):
    h.log_perf("m", 1.0 * i, n_items=i, detail="d")
time.sleep(0.2)
if calls: ok = False; print("❌ 7 筆就寫了", len(calls))
h.log_perf("m", 7.0)
time.sleep(0.3)
if len(calls) != 1 or len(calls[0]) != 8: ok = False; print("❌ 第 8 筆應一次寫 8 筆", [len(c) for c in calls])
# 距上次 ≥90 秒 → 第 1 筆就觸發
h._PERF_LAST_FLUSH[0] = time.time() - 100
h.log_perf("late", 5.0)
time.sleep(0.3)
if len(calls) != 2 or len(calls[1]) != 1: ok = False; print("❌ 逾時應觸發寫入", [len(c) for c in calls])
# 寫入失敗不拋例外
class _Bad:
    def table(self, n): raise RuntimeError("boom")
h.SUPABASE_CONN = _Bad()
h._PERF_LAST_FLUSH[0] = time.time() - 100
h.log_perf("x", 1.0); time.sleep(1.5)
print("✅ log_perf 批次寫入正確" if ok else "❌ 失敗")
sys.exit(0 if ok else 1)
