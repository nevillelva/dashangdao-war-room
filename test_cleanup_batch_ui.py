"""【2026-10-15】測試殘留清理批次 UI（全選／刪除勾選／忽略勾選）離線渲染測試。"""
import sys
import textwrap
from streamlit.testing.v1 import AppTest

SRC = open("dashangdao.py", encoding="utf-8").read()
a = SRC.index("                # 【2026-10-15 老闆指示】一筆一筆按太沒效率")
b = SRC.index("    # 【R97續21新增，總指揮官要求：脆弱性要有監控")
BLOCK = textwrap.dedent(SRC[a:b])

HARNESS = '''
import streamlit as st
from datetime import datetime, timezone

LOG = st.session_state.setdefault("LOG", [])
ADMIN = st.session_state.setdefault("ADMIN", True)

class Q:
    def __init__(self, t): self.t, self.op, self.eqs = t, None, []
    def delete(self): self.op = "delete"; return self
    def update(self, d): self.op = "upd:" + str(d.get("status")); return self
    def eq(self, k, v): self.eqs.append((k, v)); return self
    def execute(self):
        LOG.append((self.t, self.op, tuple(self.eqs)))
class Conn:
    def table(self, t): return Q(t)
SUPABASE_CONN = Conn()
def is_admin(): return ADMIN
def _cf_age_days(r): return 3
_cf_rows = [
    {"id": 1, "table_name": "route2_watchlist", "trade_date": "2026-08-24", "row_count": 7, "median_count": 55, "reason": "r1"},
    {"id": 2, "table_name": "intraday_5min_bars", "trade_date": "2026-09-02", "row_count": 17, "median_count": 90, "reason": "r2"},
    {"id": 3, "table_name": "intraday_gate_results", "trade_date": "2026-08-25", "row_count": 1, "median_count": 15, "reason": "r3"},
]
''' + BLOCK

def by_key(at, k):
    for kind in (at.checkbox, at.button):
        for w in kind:
            if w.key == k:
                return w
    return None

fails = []
def check(n, c):
    print(("PASS: " if c else "FAIL: ") + n)
    if not c:
        fails.append(n)

at = AppTest.from_string(HARNESS, default_timeout=30).run()
check("渲染不丟例外", not at.exception)
check("刪除按鈕一開始停用（沒勾選）", by_key(at, "cf_batch_del").disabled)
by_key(at, "cf_sel_all").check().run()
check("全選後 3 筆都被勾", all(by_key(at, f"cf_pick_{i}").value for i in (1, 2, 3)))
check("勾了但沒確認前，刪除仍停用", by_key(at, "cf_batch_del").disabled)
by_key(at, "cf_confirm_del").check().run()
check("確認後刪除可按", not by_key(at, "cf_batch_del").disabled)
by_key(at, "cf_batch_del").click().run()
LOG = at.session_state["LOG"]
check("3 筆資料都被刪（各表各日期）", sum(1 for x in LOG if x[1] == "delete") == 3)
check("3 筆旗標都標成 deleted", sum(1 for x in LOG if x[1] == "upd:deleted") == 3)
check("刪除完不丟例外", not at.exception)

# 只勾一筆
at = AppTest.from_string(HARNESS, default_timeout=30).run()
by_key(at, "cf_pick_2").check().run()
by_key(at, "cf_confirm_del").check().run()
by_key(at, "cf_batch_del").click().run()
LOG = at.session_state["LOG"]
check("只勾一筆時只刪那一筆", [x for x in LOG if x[1] == "delete"] == [("intraday_5min_bars", "delete", (("trade_date", "2026-09-02"),))])

# 忽略勾選
at = AppTest.from_string(HARNESS, default_timeout=30).run()
by_key(at, "cf_sel_all").check().run()
by_key(at, "cf_batch_dismiss").click().run()
LOG = at.session_state["LOG"]
check("忽略勾選：只更新旗標、不刪資料", sum(1 for x in LOG if x[1] == "delete") == 0 and sum(1 for x in LOG if x[1] == "upd:dismissed") == 3)

# 非管理員
at = AppTest.from_string(HARNESS.replace('ADMIN = st.session_state.setdefault("ADMIN", True)', 'ADMIN = False'), default_timeout=30).run()
by_key(at, "cf_sel_all").check().run()
check("非管理員：刪除與忽略按鈕都停用", by_key(at, "cf_batch_del").disabled and by_key(at, "cf_batch_dismiss").disabled)
if fails:
    print(f"\n{len(fails)} 項失敗"); sys.exit(1)
print("\n全部通過")
