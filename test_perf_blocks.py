#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_perf_blocks.py —— 速覽分區塊計時（2026-11-15）：BlockTimer 行為 + dashangdao.py 埋點結構檢查。"""
import ast, sys, time, types
try:
    import openai  # noqa: F401
except ImportError:
    _m = types.ModuleType("openai"); _m.OpenAI = object; sys.modules["openai"] = _m
import dashangdao_helpers as h

ok_all = True
def chk(n, c, extra=""):
    global ok_all
    print(("✅ " if c else "❌ ") + n + ("" if c or not extra else f"  [{extra}]")); ok_all = ok_all and bool(c)

bt = h.BlockTimer()
time.sleep(0.03); bt.mark("a")
time.sleep(0.02); bt.mark("b"); bt.mark("a")
chk("區塊依序記錄、同名累加", list(bt.blocks) == ["a", "b"] and bt.blocks["a"] >= 25 and bt.blocks["b"] >= 15, bt.blocks)
chk("總耗時 >= 各區塊加總", bt.total_ms() + 1 >= sum(bt.blocks.values()))
chk("detail 格式 k=ms,…", bt.detail().startswith("a=") and ",b=" in bt.detail(), bt.detail())
big = h.BlockTimer()
for i in range(200): big.mark(f"block_{i}")
chk("detail 超長會截斷在上限內（perf_log.detail 上限 300）", len(big.detail()) <= 290)
chk("不拋例外（壞輸入）", h.BlockTimer().mark(None) is None)

src = open("dashangdao.py", encoding="utf-8").read()
tree = ast.parse(src)
fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "render_quick_overview")
body = ast.get_source_segment(src, fn)
marks = [m for m in ("cache_read", "compute", "persist_write", "live_quotes", "rows_build_group", "df_build", "fmt_defs",
                     "delete_ui", "styler", "dataframe_render", "caption_button", "pick_card")]
chk("render_quick_overview 內 12 個區塊都有 mark", all(f'_qo_bt.mark("{m}")' in body for m in marks), [m for m in marks if f'_qo_bt.mark("{m}")' not in body])
chk("兩個結尾（empty / ok）都會寫 qo_blocks", '_qo_log_blocks("empty")' in body and '_qo_log_blocks("ok")' in body)
chk("寫入為單筆 perf_log qo_blocks", 'log_perf("qo_blocks"' in body)
chk("龍頭補列有計時", 'log_perf("qo_leader_lookup"' in src)
# 行為不變：函式回傳點數量不變（原本 2 個 return results：空清單與結尾）
rets = [n for n in ast.walk(fn) if isinstance(n, ast.Return) and isinstance(n.value, ast.Name) and n.value.id == "results"]
chk("render_quick_overview 的 return results 仍為 2 處", len(rets) == 2, len(rets))
print("全部通過" if ok_all else "有失敗項目")
sys.exit(0 if ok_all else 1)
