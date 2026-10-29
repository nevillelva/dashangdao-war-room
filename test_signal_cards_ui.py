"""【2026-10-13】即時訊號卡區塊離線渲染測試（Streamlit AppTest + 假資料庫，不連網）。
只抽出 dashangdao.py 裡 _fetch_all_rows／render_signal_cards_section 兩個函式來跑，確認不丟例外、有卡片與篩選。"""
import ast
import sys
from streamlit.testing.v1 import AppTest

SRC = open("dashangdao.py", encoding="utf-8").read()
tree = ast.parse(SRC)
wanted = [n for n in tree.body if isinstance(n, ast.FunctionDef)
          and n.name in ("_fetch_all_rows", "render_signal_cards_section")]
assert len(wanted) == 2, "找不到要測的兩個函式"
FUNC_SRC = "\n\n".join(ast.get_source_segment(SRC, n) for n in wanted)

HARNESS = '''
import pandas as pd
import streamlit as st
import signal_cards  # noqa: F401  (函式內 import 也會用到，這裡先確認可載入)

BARS = [
    {"symbol": "2330", "bar_time": "09:00", "open": 100, "high": 101, "low": 99, "close": 101, "volume": 1000},
    {"symbol": "2330", "bar_time": "09:05", "open": 101, "high": 102, "low": 100, "close": 100, "volume": 500},
    {"symbol": "2317", "bar_time": "09:00", "open": 50, "high": 51, "low": 49, "close": 49, "volume": 800},
]
GATES = [
    {"symbol": "2330", "gate1_verdict": "strong_bull", "direction": "long", "overall_label": "前兩關合格",
     "detail": {"k": 1}, "created_at": "2026-10-13T01:00:00Z"},
    {"symbol": "2317", "gate1_verdict": "weak_bear", "direction": "short", "overall_label": "第一關弱勢空方",
     "detail": None, "created_at": "2026-10-13T01:00:00Z"},
]
POOL = [{"symbol": "2317"}]

class _Q:
    def __init__(self, rows):
        self.rows = rows
        self.a, self.b = 0, 999
    def select(self, *_a, **_k): return self
    def eq(self, *_a, **_k): return self
    def in_(self, *_a, **_k): return self
    def order(self, *_a, **_k): return self
    def range(self, a, b):
        self.a, self.b = a, b
        return self
    def execute(self):
        class R: pass
        r = R(); r.data = self.rows[self.a:self.b + 1]
        return r

class FakeSB:
    def table(self, name):
        return _Q({"intraday_5min_bars": BARS, "intraday_gate_results": GATES,
                   "intraday_candidate_pool": POOL}.get(name, []))

SUPABASE_CONN = FakeSB()
def get_current_or_last_trading_date():
    return "2026-10-13"

st.session_state.setdefault("portfolio", {"2330": {}})
st.session_state.setdefault("pinned_stocks", {})

''' + FUNC_SRC + '''

render_signal_cards_section()
'''

at = AppTest.from_string(HARNESS, default_timeout=60)
at.run()
fails = []


def check(name, cond):
    print(("PASS: " if cond else "FAIL: ") + name)
    if not cond:
        fails.append(name)


check("區塊執行無例外", not at.exception)
md_text = "\n".join(m.value for m in at.markdown) + "\n" + "\n".join(c.value for c in at.caption)
check("卡片標題含 2330", "2330" in md_text)
check("標記出現（持倉、觀察）", "持倉" in md_text and "觀察" in md_text)
check("一句話結論出現（通過／沒通過／等資料／尚無判讀 其中之一）", any(k in md_text for k in ("通過", "沒通過", "等資料", "尚無判讀")))
check("三關細節與長串標籤不再顯示", "三關細節" not in md_text and "第一關" not in md_text and "第二關" not in md_text and "K 線推算" not in md_text)
check("有篩選控制項（方向、標記）", len(at.radio) >= 2)

if fails:
    print(f"\n{len(fails)} 項失敗")
    sys.exit(1)
print("\n全部通過")
