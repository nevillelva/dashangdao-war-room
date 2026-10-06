#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_etf_planner_ui.py —— ETF 改版後的「🎯 領息規劃器」畫面：用貼近實盤的假資料（0056/00878/00919 季配錯開、00929 月配、0050 半年配）
跑 AppTest：預設（本金→每月領多少）、反推本金、月配、自己挑、不同稅率、零股，每一種都不得丟例外，且關鍵數字合理。"""
import sys
from streamlit.testing.v1 import AppTest

HARNESS = '''
import datetime as dt
import streamlit as st

TPE = dt.timezone(dt.timedelta(hours=8))
TODAY = dt.datetime.now(TPE).date()

def _evs(sym, months, cash, years=3):
    """往回 years 年、每年在 months 這幾個月的 15 日除息（只取 今天-3天 以前）；發放日＝除息後 25 天。"""
    out = []
    for y in range(TODAY.year - years, TODAY.year + 1):
        for m in months:
            ex = dt.date(y, m, 15)
            if ex <= TODAY - dt.timedelta(days=3) and ex >= TODAY - dt.timedelta(days=365 * years):
                out.append({"symbol": sym, "ex_date": ex.isoformat(), "cash_per_unit": cash, "pay_date": (ex + dt.timedelta(days=25)).isoformat()})
    return out

MASTER = [
    {"symbol": "0056", "name": "元大高股息", "last_price": 38.0, "price_1y": 36.0, "listed_date": "2007-12-26", "mdd_1y": -12.0, "vol_1y": 14.0, "sharpe_1y": 0.9, "price_date": TODAY.isoformat()},
    {"symbol": "00878", "name": "國泰永續高股息", "last_price": 23.0, "price_1y": 21.5, "listed_date": "2020-07-20", "mdd_1y": -9.0, "vol_1y": 11.0, "sharpe_1y": 1.1, "price_date": TODAY.isoformat()},
    {"symbol": "00919", "name": "群益台灣精選高息", "last_price": 24.0, "price_1y": 25.0, "listed_date": "2022-10-20", "mdd_1y": -14.0, "vol_1y": 15.0, "sharpe_1y": 0.4, "price_date": TODAY.isoformat()},
    {"symbol": "00929", "name": "復華台灣科技優息", "last_price": 19.0, "price_1y": 18.0, "listed_date": "2023-06-09", "mdd_1y": -11.0, "vol_1y": 13.0, "sharpe_1y": 0.7, "price_date": TODAY.isoformat()},
    {"symbol": "0050", "name": "元大台灣50", "last_price": 62.0, "price_1y": 48.0, "listed_date": "2003-06-30", "mdd_1y": -18.0, "vol_1y": 20.0, "sharpe_1y": 1.4, "price_date": TODAY.isoformat()},
    {"symbol": "00679B", "name": "元大美債20年", "last_price": 28.0, "price_1y": 27.0, "listed_date": "2017-01-11", "price_date": TODAY.isoformat()},
]
EVENTS = (_evs("0056", [1, 4, 7, 10], 1.2) + _evs("00878", [2, 5, 8, 11], 0.5) + _evs("00919", [3, 6, 9, 12], 0.55)
          + _evs("00929", list(range(1, 13)), 0.12) + _evs("0050", [1, 7], 1.5) + _evs("00679B", [1, 4, 7, 10], 0.3))
DATA = {"etf_master": MASTER, "etf_dividend_events": EVENTS, "etf_trades": [], "etf_holdings": []}

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

bad = 0


def check(ok, msg):
    global bad
    print("✅" if ok else "❌", msg)
    bad += (not ok)


def run(at, label):
    at.run()
    ok = not at.exception
    check(ok, f"{label}：無例外" + ("" if ok else f" → {[e.value for e in at.exception]}"))
    return ok


def metrics(at):
    return {m.label: m.value for m in at.metric}


at = AppTest.from_string(HARNESS, default_timeout=120)
run(at, "預設（本金 70 萬 → 季配三檔錯開）")
mt = metrics(at)
check("實際投入" in mt and "平均每月實領（稅前）" in mt and "平均每月（再扣綜所稅後）" in mt and "稅後年化殖利率" in mt, f"結果四個主要指標都有：{list(mt)[:8]}")
tabs = [t.label for t in at.tabs]
check(tabs[:4] == ["🎯 領息規劃器", "📦 我的持倉與領息", "📒 買賣紀錄", "🔎 ETF 一覽與持股"], f"分頁精簡為 4 個且規劃器在首頁：{tabs[:4]}")


def num(s):
    return float(str(s).replace(",", "").replace("%", "").replace("$", "").strip() or 0)


inv = num(mt.get("實際投入", "0"))
check(500_000 < inv <= 700_000, f"實際投入不超過本金 70 萬（{inv:,.0f}）")
mon = num(mt.get("平均每月實領（稅前）", "0"))
# 0056 年 4.8 元/38 元=12.6%；00878 2.0/23=8.7%；00919 2.2/24=9.2% → 毛殖利率約 10%；70 萬 → 約 5,800 /月（稅前）
check(3000 < mon < 9000, f"月領數量級合理（{mon:,.0f}；70 萬、三檔平均殖利率約 10%）")
ys = num(mt.get("稅後年化殖利率", "0"))
check(4 < ys < 14, f"稅後年化殖利率合理（{ys:.2f}%）")

# 反推本金
at.radio(key="etfp_mode").set_value("🎯 我要每月領 X，要準備多少本金")
if run(at, "反推本金（稅後月領 2 萬）"):
    mt2 = metrics(at)
    mon2 = num(mt2.get("平均每月（再扣綜所稅後）", "0"))
    check(mon2 >= 20000 - 1, f"稅後月均 ≥ 目標 2 萬（{mon2:,.0f}）")
    check(num(mt2.get("實際投入", "0")) > 1_000_000, f"需要的本金大於 100 萬（{num(mt2.get('實際投入', '0')):,.0f}）")

# 回到正向、改稅率與零股
at.radio(key="etfp_mode").set_value("💰 我有本金，每月能領多少")
at.select_slider(key="etfp_bracket").set_value(30)
at.radio(key="etfp_lot").set_value("零股(1股)")
if run(at, "30% 級距＋零股"):
    mt3 = metrics(at)
    check(num(mt3.get("實際投入", "0")) > inv, f"零股後實際投入更接近本金（{num(mt3.get('實際投入', '0')):,.0f} > 整張 {inv:,.0f}）")

# 月配
at.radio(key="etfp_pickmode").set_value("📅 月配")
run(at, "月配模式")
# 自己挑
at.radio(key="etfp_pickmode").set_value("✋ 自己挑（任意 1~8 檔）")
run(at, "自己挑（未選）")
ms = at.multiselect(key="etfp_free")
opts = list(ms.options)
check(any(o.startswith("0050") for o in opts) and any(o.startswith("0056") for o in opts) and not any(o.startswith("00679B") for o in opts),
      "自選清單含高股息與市值型(0050)、不含債券ETF")
ms.set_value([o for o in opts if o.startswith("0050") or o.startswith("00878")])
run(at, "自己挑 0050＋00878（市值型＋高股息混搭）")
check(len(at.dataframe) >= 2, "結果表格有渲染")

# 類型：把債券也納入、取消高股息
at.multiselect(key="etfp_kinds").set_value(["cap", "bond"])
run(at, "只選市值型＋債券")

# 預設狀態切到全類型空選（沒有候選）不得當掉
at.multiselect(key="etfp_kinds").set_value([])
run(at, "類型全取消")

print(f"\n{'全部通過' if not bad else str(bad) + ' 項失敗'}")
sys.exit(1 if bad else 0)
