#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_bt_panel_ui.py —— 用 Streamlit AppTest 在假 Supabase 上實際跑 dashangdao.py 內「回測規則面板」與「單檔歷史勝率」兩段程式，
確認不丟例外、沒有巢狀 expander、數字合理。（只抽出這兩段，不需要整個戰情室與任何金鑰。）"""
import sys
from streamlit.testing.v1 import AppTest

src = open("dashangdao.py", encoding="utf-8").read()
a = src.index('@st.cache_data(ttl=300, show_spinner=False)\ndef _load_bt_rule_panel_data')
b = src.index('if nav_section == "策略回測":\n    if _lazy_panel(\'📊 勝率報表')
panel = src[a:b]

HARNESS = '''
import streamlit as st
import pandas as pd
import warroom_core as _wc

ROWS = [
  {"symbol":"2340","name":"台亞","status":"pending","entry_date":"2026-10-02","entry_price":45.75,"take_profit":51.24,"def_line":38.89,"shares":2.1858,"capital":100000,"strategy_tag":"chuan_e_ma60_40"},
  {"symbol":"6442","name":"光聖","status":"pending","entry_date":"2026-10-02","entry_price":120.0,"take_profit":134.4,"def_line":102.0,"shares":0.8,"capital":100000,"strategy_tag":"pullback_burst"},
  {"symbol":"2302","name":"麗正","status":"holding","entry_date":"2026-10-05","entry_price":46.5,"take_profit":52.08,"def_line":39.53,"shares":2.1505,"capital":100000},
  {"symbol":"1101","name":"台泥","status":"closed","entry_date":"2026-09-01","exit_date":"2026-09-10","entry_price":30,"exit_price":33.6,"exit_reason":"take_profit","realized_roi":11.4,"realized_pnl":11400,"shares":3.3,"capital":100000,"strategy_tag":"chuan_e_ma60_40"},
  {"symbol":"1102","name":"亞泥","status":"closed","entry_date":"2026-09-01","exit_date":"2026-09-12","entry_price":30,"exit_price":25.5,"exit_reason":"stop_loss","realized_roi":-15.6,"realized_pnl":-15600,"shares":3.3,"capital":100000,"strategy_tag":"pullback_burst"},
]
STATS = [{"symbol":"2340","rule":"chuan_e_ma60_40","n":2,"wins":1,"avg_pct":3.2},{"symbol":"2302","rule":"chuan_e_ma60_40","n":1,"wins":0,"avg_pct":-4.0},
         {"symbol":"1101","rule":"chuan_e_ma60_40","n":6,"wins":4,"avg_pct":5.1},{"symbol":"6442","rule":"pullback_burst","n":3,"wins":2,"avg_pct":4.4}]
SCAN = {"as_of":"2026-10-02","breadth":0.82,"gated":False,"n_scanned":297,"n_candidates":5,"picked":["2340","2302","6442"],"by_rule":{"chuan_e_ma60_40":3,"pullback_burst":2}}

class _Q:
    def __init__(self, t): self.t = t
    def select(self, *a, **k): return self
    def eq(self, *a, **k): return self
    def in_(self, *a, **k): return self
    def limit(self, *a, **k): return self
    def order(self, *a, **k): return self      # 2026-10-07：sb_fetch_all 分頁會用到
    def range(self, *a, **k): return self
    def execute(self):
        import json
        if self.t == "system_portfolio": d = ROWS
        elif self.t == "entry_rule_symbol_stats": d = STATS
        elif self.t == "system_config": d = [{"config_value": json.dumps(SCAN)}]
        else: d = []
        return type("R", (), {"data": d})()

class _C:
    def table(self, t): return _Q(t)

def _lazy_panel(*a, **k):
    return True

SUPABASE_CONN = _C()
nav_section = "策略回測"
''' 

at = AppTest.from_string(HARNESS + "\n" + panel, default_timeout=60)
at.run()
ok = True
if at.exception:
    ok = False
    print("❌ 例外：", [e.value for e in at.exception])
txt = " ".join(m.value for m in at.markdown) + " " + " ".join(c.value for c in at.caption)
for need in ["最近一次掃描", "大盤寬度 82%", "明日", "持倉中", "已出場 2 筆", "穿山惡龍 3", "爆量回檔 2"]:
    if need not in txt:
        ok = False
        print("❌ 畫面缺少：", need)
dfs = [d.value for d in at.dataframe]
print("dataframes:", len(dfs), [list(d.columns)[:4] for d in dfs])
if len(dfs) < 3:
    ok = False
    print("❌ 預期至少 3 張表（掛單/持倉/單檔勝率）")
# 切到「爆量回檔」規則：單檔表只該出現該規則的回測檔(6442)＋該規則的模擬倉實測檔(1102)，不含穿山惡龍的 2340/2302/1101
try:
    at.radio(key="bt_sym_rule").set_value("爆量回檔").run()
    if at.exception:
        ok = False
        print("❌ 切換規則後例外：", [e.value for e in at.exception])
    _last = [d.value for d in at.dataframe][-1]
    if set(_last["代號"]) != {"6442", "1102"}:
        ok = False
        print("❌ 切到爆量回檔後單檔表應只有 6442/1102，實際：", list(_last["代號"]))
except Exception as e:
    ok = False
    print("❌ 規則切換測試失敗：", type(e).__name__, e)
print("expanders:", [e.label for e in at.expander])
print("✅ 面板在假資料下可正常渲染" if ok else "❌ 失敗")
sys.exit(0 if ok else 1)
