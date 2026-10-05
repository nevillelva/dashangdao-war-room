#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_parallel_map.py —— system_scheduler._parallel_map：順序、例外隔離、序列模式一致、超時。（ast 抽出，不需套件）"""
import ast, sys, time, concurrent.futures
src = open("system_scheduler.py", encoding="utf-8").read()
node = [n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name == "_parallel_map"][0]
ns = {"concurrent": concurrent}
exec(compile(ast.Module([node], []), "pm", "exec"), ns)
pm = ns["_parallel_map"]
F = []
def check(n, c, e=""):
    print(("  ✅ " if c else "  ❌ ") + n + ("" if c else f" {e}"))
    if not c: F.append(n)

def f(x):
    if x % 7 == 0: raise ValueError("boom")
    time.sleep(0.01); return x * 2
items = list(range(50))
seq = pm(f, items, max_workers=1)
par = pm(f, items, max_workers=8)
check("並行結果順序與輸入一致", [r[0] for r in par] == items)
check("並行與序列的結果/例外完全一致", [(r[0], r[1], type(r[2]).__name__ if r[2] else None) for r in par] == [(r[0], r[1], type(r[2]).__name__ if r[2] else None) for r in seq])
check("單項例外不影響其他", sum(1 for r in par if r[2] is not None) == 8 and all(r[1] == r[0] * 2 for r in par if r[2] is None))
t = time.time(); pm(lambda x: time.sleep(0.2), range(24), max_workers=8); dt = time.time() - t
check("24 個 0.2s 任務、8 條並行 ≈0.6s（<1.5s）", dt < 1.5, f"{dt:.2f}s")
t = time.time(); out = pm(lambda x: time.sleep(5 if x == 0 else 0.01) or x, range(4), max_workers=2, timeout=0.5); dt = time.time() - t
check("整體超時：卡住的項目標記 TimeoutError 且不拖住呼叫端", isinstance(out[0][2], TimeoutError) and dt < 2, f"{dt:.2f}s {out}")
check("空輸入", pm(f, [], max_workers=4) == [])
if F: print("❌", F); sys.exit(1)
print("✅ _parallel_map 全部通過")
