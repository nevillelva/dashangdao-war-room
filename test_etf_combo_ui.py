#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_etf_combo_ui.py —— 組合歷史回測頁的「先確認、再標記」介面測試（Streamlit AppTest + 假 Supabase）。
驗證：未查證的大跳動 ETF 不開放選用、只標「資料待確認」（不出現「異常」）；有官方分割紀錄的可選且被還原。"""
import sys
from streamlit.testing.v1 import AppTest

HARNESS = '''
import json
import datetime as dt
import streamlit as st


def _days(start, n, p0, g, skip=None):
    out, d, p = [], dt.date.fromisoformat(start), p0
    while len(out) < n:
        if d.weekday() < 5:
            out.append((d.isoformat(), p))
            p *= g
        d += dt.timedelta(days=1)
    return out


def _with_split(rows, at_idx, factor, gap=8):
    head = rows[:at_idx]
    last = dt.date.fromisoformat(head[-1][0])
    d, tail = last + dt.timedelta(days=gap), []
    for i in range(at_idx, len(rows)):
        while d.weekday() >= 5:
            d += dt.timedelta(days=1)
        tail.append((d.isoformat(), rows[i][1] / factor))
        d += dt.timedelta(days=1)
    return head + tail


A = _days("2024-01-02", 120, 20.0, 1.001)
B = _with_split(_days("2024-01-02", 120, 30.0, 1.001), 60, 4.0)   # 停牌後變 1/4，沒有任何公告 → 待確認
C = _with_split(_days("2024-01-02", 120, 40.0, 1.001), 60, 5.0)   # 同樣形狀，但有官方分割紀錄
C_DATE = C[60][0]
_D1 = _days("2024-01-02", 70, 10.0, 1.0005)
_D2 = _days((dt.date.fromisoformat(_D1[-1][0]) + dt.timedelta(days=40)).isoformat(), 50, 10.1, 1.0005)
D = _D1 + _D2                                                     # 中間 40 天沒成交，且已由 etf_no_trade_v1 確認 → 成交稀少
D_FROM, D_TO = _D1[-1][0], _D2[0][0]
PRICES = [{"symbol": s, "trade_date": d, "close": c} for s, rows in (("A", A), ("B", B), ("C", C), ("D", D)) for d, c in rows]


def _jumps():
    out = []
    for s, rows in (("A", A), ("B", B), ("C", C)):
        for (d0, p), (d1, c) in zip(rows, rows[1:]):
            if abs(c / p - 1) > 0.11:
                out.append({"symbol": s, "prev_date": d0, "jump_date": d1, "prev_close": p, "close": c})
    return out


CFG = [{"config_key": "etf_corp_actions_v1", "config_value": json.dumps({"splits": [
    {"symbol": "C", "date": C_DATE, "factor": 5.0, "type": "分割"}]})},
       {"config_key": "etf_no_trade_v1", "config_value": json.dumps({"D": {"segments": [
           {"from": D_FROM, "to": D_TO, "days": 40, "placeholder_days": 28}], "checked": "2026-10-13", "source": "測試"}})}]
MASTER = [{"symbol": "A", "name": "國內A", "last_price": 20, "price_date": "2026-10-02", "freq": "quarterly"},
          {"symbol": "B", "name": "國內B", "last_price": 30, "price_date": "2026-10-02", "freq": "quarterly"},
          {"symbol": "C", "name": "國內C", "last_price": 40, "price_date": "2026-10-02", "freq": "quarterly"},
          {"symbol": "D", "name": "冷門D", "last_price": 10, "price_date": "2026-10-02", "freq": "quarterly"}]
DATA = {"etf_master": MASTER, "etf_price_daily": PRICES, "etf_price_jumps": _jumps(), "system_config": CFG,
        "etf_price_qc": [], "etf_dividend_result": []}


class _Q:
    def __init__(self, t):
        self.t, self.f, self.r = t, [], None
    def select(self, *a, **k): return self
    def order(self, *a, **k): return self
    def limit(self, *a, **k): return self
    def eq(self, c, v):
        self.f.append(lambda x: str(x.get(c)) == str(v)); return self
    def in_(self, c, vs):
        self.f.append(lambda x: x.get(c) in vs); return self
    def range(self, a, b):
        self.r = (a, b); return self
    def execute(self):
        rows = [x for x in DATA.get(self.t, []) if all(f(x) for f in self.f)]
        if self.t == "etf_price_daily":
            rows = sorted(rows, key=lambda x: x["trade_date"])
        if self.r:
            rows = rows[self.r[0]:self.r[1] + 1]
        return type("R", (), {"data": rows})()


class _C:
    def table(self, t): return _Q(t)


from etf_tab import _render_combo_backtest
_render_combo_backtest(_C(), MASTER, {m["symbol"]: m["name"] for m in MASTER})
'''

ok = True
at = AppTest.from_string(HARNESS, default_timeout=90)
at.run()
if at.exception:
    ok = False
    print("❌ 例外：", [e.value for e in at.exception])
ms = [m for m in at.multiselect if m.key == "etf_bt_codes"]
if not ms:
    ok = False
    print("❌ 找不到選擇清單")
else:
    opts = [str(o).split()[0] for o in ms[0].options]
    print("可選：", opts)
    if "B" in opts or "D" in opts or "A" not in opts or "C" not in opts:
        ok = False
        print("❌ 選項不符預期（B 待確認、D 成交稀少都應被排除，A、C 應可選）")
labels = [e.label for e in at.expander]
print("expander：", labels)
if not any("資料待確認" in l for l in labels):
    ok = False
    print("❌ 沒有『資料待確認』說明區")
if not any("成交稀少" in l for l in labels):
    ok = False
    print("❌ 沒有『成交稀少』說明區")
texts = " ".join(str(getattr(x, "value", "")) for x in list(at.caption) + list(at.warning) + list(at.error) + list(at.markdown) + list(at.dataframe))
if "異常" in texts:
    ok = False
    print("❌ 畫面出現『異常』字樣")
if "資料缺漏" in texts or "補齊" in texts:
    ok = False
    print("❌ 已確認沒成交的 ETF 不應再說資料缺漏／要補齊")
# 選 A + C：C 因官方分割紀錄被還原，應算得出結果
if ms:
    ms[0].set_value(["A", "C"])
    at.run()
    if at.exception:
        ok = False
        print("❌ 選 A、C 後例外：", [e.value for e in at.exception])
    cap = " ".join(str(c.value) for c in at.caption)
    if "還原" not in cap:
        ok = False
        print("❌ 選到有分割的 C，畫面沒有說明已還原")
    if at.error:
        ok = False
        print("❌ 不應出現錯誤：", [e.value for e in at.error])
    mets = [m.label for m in at.metric]
    if not any("年化報酬" in l for l in mets):
        ok = False
        print("❌ 沒有算出年化報酬")
print("✅ 組合回測頁：待確認的不開放、有依據的可選且還原" if ok else "❌ 失敗")
sys.exit(0 if ok else 1)
