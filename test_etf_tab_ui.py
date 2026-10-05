#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_etf_tab_ui.py —— 用 Streamlit AppTest 在假 Supabase 上跑 etf_tab.render_etf_tab：
預設（兩個重分頁關閉）與打開「ETF 配息一覽」「持股重疊與規模」開關兩種情況都不得丟例外。"""
import sys
from streamlit.testing.v1 import AppTest

HARNESS = '''
import datetime as dt
import streamlit as st

def _ev(sym, months, cash):
    out = []
    for i in months:
        ex = dt.date(2025, 10, 1) + dt.timedelta(days=30 * i)
        out.append({"symbol": sym, "ex_date": ex.isoformat(), "cash_per_unit": cash, "pay_date": (ex + dt.timedelta(days=25)).isoformat()})
    return out

MASTER = [{"symbol": "M1", "name": "月配A", "last_price": 10, "price_date": "2026-10-02", "freq": "monthly"},
          {"symbol": "Q1", "name": "季A", "last_price": 20, "price_date": "2026-10-02", "freq": "quarterly"}]
EVENTS = _ev("M1", range(0, 13), 0.1) + _ev("Q1", [0, 3, 6, 9, 12], 0.5)
HOLD = [{"symbol": "M1", "as_of": "2026-09-30", "stock_code": "2330", "stock_name": "台積電", "weight": 10.0, "shares": 1000, "source": "t"},
        {"symbol": "Q1", "as_of": "2026-09-30", "stock_code": "2330", "stock_name": "台積電", "weight": 8.0, "shares": 800, "source": "t"}]
DATA = {"etf_master": MASTER, "etf_dividend_events": EVENTS, "etf_trades": [], "etf_holdings": HOLD}

class _Q:
    def __init__(self, t): self.t = t
    def __getattr__(self, name):
        return lambda *a, **k: self
    def execute(self):
        return type("R", (), {"data": DATA.get(self.t, [])})()

class _C:
    def table(self, t): return _Q(t)

from etf_tab import render_etf_tab
render_etf_tab(_C())
'''

at = AppTest.from_string(HARNESS, default_timeout=90)
at.run()
ok = True
if at.exception:
    ok = False
    print("❌ 預設狀態例外：", [e.value for e in at.exception])
labels = [t.label for t in at.toggle]
print("toggles:", labels)
for need in ("etf_lz_scan", "etf_lz_hold"):
    if not any(t.key == need for t in at.toggle):
        ok = False
        print("❌ 找不到開關", need)
for t in at.toggle:
    if t.key in ("etf_lz_scan", "etf_lz_hold"):
        t.set_value(True)
at.run()
if at.exception:
    ok = False
    print("❌ 打開開關後例外：", [e.value for e in at.exception])
print("✅ ETF 分頁在假資料下可正常渲染（開關關/開皆無例外）" if ok else "❌ 失敗")
sys.exit(0 if ok else 1)
