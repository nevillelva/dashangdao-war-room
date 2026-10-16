"""【2026-10-16】除息前觀察清單離線渲染測試（AppTest + 假資料庫）。"""
import ast
from streamlit.testing.v1 import AppTest

SRC = open("dashangdao.py", encoding="utf-8").read()
tree = ast.parse(SRC)
fn = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "render_exdiv_watchlist"]
assert fn, "找不到 render_exdiv_watchlist"
FUNC_SRC = ast.get_source_segment(SRC, fn[0])

HARNESS = '''
import pandas as pd
import streamlit as st
from datetime import datetime, date, timedelta, timezone
TAIPEI_TZ = timezone(timedelta(hours=8))
_d = (datetime.now(TAIPEI_TZ).date() + timedelta(days=5)).isoformat()
DATA = {"official_open_data": [{"symbol": "2884", "data_date": _d, "payload": {"cash": 0.5, "kind": "息", "name": "玉山金", "market": "twse"}},
                               {"symbol": "6548", "data_date": _d, "payload": {"cash": None, "kind": "除息", "name": "長科", "market": "tpex"}}],
        "twse_market_snapshot": [{"symbol": "2884", "trade_date": "2026-10-15", "close_price": 25.0}],
        "overnight_scan_results": [], "system_config": []}
class _Q:
    def __init__(self, rows): self.rows = rows
    def __getattr__(self, n): return lambda *a, **k: self
    def execute(self):
        class R: pass
        r = R(); r.data = self.rows; return r
class FakeSB:
    def table(self, name): return _Q(DATA.get(name, []))
SUPABASE_CONN = FakeSB()
TW_STOCK_NAMES = {"2884": "玉山金"}
''' + FUNC_SRC + '''
render_exdiv_watchlist()
'''

at = AppTest.from_string(HARNESS, default_timeout=60).run()
fails = []
def check(name, ok):
    print(("PASS: " if ok else "FAIL: ") + name)
    if not ok: fails.append(name)
check("無例外", not at.exception)
check("標題含 2 檔", any("2 檔" in e.label for e in at.expander))
df = at.dataframe[0].value if at.dataframe else None
check("有表格", df is not None and len(df) == 2)
check("殖利率=2.0%", df is not None and float(df.loc[df["代號"] == "2884", "殖利率%"].iloc[0]) == 2.0)
check("未公告股利照實顯示", df is not None and df.loc[df["代號"] == "6548", "現金股利"].iloc[0] == "未公告")
check("標示非買進訊號", any("不是買進訊號" in c.value for c in at.caption))
print(f"\n{len(fails)} 項失敗" if fails else "\n全部通過")
raise SystemExit(1 if fails else 0)
