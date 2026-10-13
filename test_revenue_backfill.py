#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自結營收：公告沒寫年增率時，用證交所月營收彙總補判方向。不需網路。"""
import sys
from types import SimpleNamespace
sys.path.insert(0, ".")
import premarket as pm
import premarket_stage as ps

FAIL = []


def check(name, cond, extra=""):
    if not cond:
        FAIL.append(name)
        print("❌", name, extra)
    else:
        print("✅", name)


for subj, d, want in (("公告本公司2026年9月合併營業額", "2026-10-12", "202609"),
                      ("發布本公司115年09月份業績資料", "2026-10-12", "202609"),
                      ("公告遠傳電信股份有限公司民國一一五年九月份營運成績", "2026-10-12", "202609"),
                      ("公告本公司12月營收", "2027-01-08", "202612"),
                      ("公告本公司十二月份營收", "2027-01-08", "202612"),
                      ("公告本公司營收", "2026-10-12", None)):
    check(f"資料年月 {subj[5:15]}→{want}", pm.revenue_month_of(subj, d) == want, pm.revenue_month_of(subj, d))

snap = {"1303": {"202608": 46.7, "202609": 31.2}, "2412": {"202608": 25.6}, "5871": {"202609": -22.0}}
ev = {"category": "自結營收", "growth_pct": None, "subject": "公告本公司2026年9月合併營業額", "ev_date": "2026-10-12",
      "symbol": "1303", "flags": ["未能從公告解析年增率，請看原文"], "direction": 0, "importance": 3}
check("對得上 9 月 → 補判 +2", pm.apply_revenue_yoy(ev, snap) and ev["direction"] == 2 and ev["importance"] == 4
      and ev["growth_pct"] == 31.2 and not any("未能" in f for f in ev["flags"]), ev)
ev2 = {"category": "自結營收", "growth_pct": None, "subject": "中華電信公佈115年9月營運績效", "ev_date": "2026-10-12", "symbol": "2412", "flags": []}
check("只有 8 月資料 → 不拿舊月份冒充", not pm.apply_revenue_yoy(ev2, snap) and ev2.get("direction") is None)
ev3 = {"category": "自結損益", "growth_pct": None, "subject": "公告本公司115年9月自結合併損益", "ev_date": "2026-10-12", "symbol": "5871"}
check("自結損益不套用營收年增率", not pm.apply_revenue_yoy(ev3, snap))
ev4 = dict(ev, growth_pct=12.0, direction=1)
check("公告已有年增率就不覆蓋", not pm.apply_revenue_yoy(ev4, snap) and ev4["direction"] == 1)


class Q:
    def __init__(s, db, t): s.db, s.t, s.f, s.upd = db, t, {}, None
    def select(s, *a): return s
    def eq(s, k, v): s.f[k] = v; return s
    def gte(s, *a): return s
    def in_(s, k, v): s.f[k] = set(v); return s
    def order(s, *a): return s
    def range(s, a, b): return s
    def update(s, d): s.upd = d; return s
    def execute(s):
        if s.upd is not None:
            s.db["updates"].append((s.f.get("id"), s.upd)); return SimpleNamespace(data=[])
        if s.t == "mops_events":
            return SimpleNamespace(data=[r for r in s.db["events"] if r["category"] == s.f.get("category") and r["direction"] == s.f.get("direction")])
        if s.t == "twse_market_snapshot":
            return SimpleNamespace(data=[r for r in s.db["snap"] if r["symbol"] in s.f.get("symbol", set())])
        return SimpleNamespace(data=[])


class SB:
    def __init__(s, db): s.db = db
    def table(s, t): return Q(s.db, t)


db = {"updates": [],
      "events": [{"id": 1, "symbol": "1303", "subject": "公告本公司2026年9月合併營業額", "ev_date": "2026-10-12", "category": "自結營收", "direction": 0},
                 {"id": 2, "symbol": "2412", "subject": "中華電信公佈115年9月營運績效", "ev_date": "2026-10-12", "category": "自結營收", "direction": 0},
                 {"id": 3, "symbol": "1301", "subject": "公告本公司2026年9月合併營業額", "ev_date": "2026-10-12", "category": "自結營收", "direction": 0}],
      "snap": [{"symbol": "1303", "trade_date": "2026-10-09", "revenue_ym": "202608", "rev_yoy": 46.7},
               {"symbol": "1303", "trade_date": "2026-10-14", "revenue_ym": "202609", "rev_yoy": 31.2},
               {"symbol": "2412", "trade_date": "2026-10-09", "revenue_ym": "202608", "rev_yoy": 25.6},
               {"symbol": "1301", "trade_date": "2026-10-14", "revenue_ym": "202609", "rev_yoy": 2.0}]}
n = ps.backfill_revenue_direction(SB(db), "2026-10-15")
check("回補只寫有新方向的列（1303 +2；2412 沒 9 月、1301 年增 2% 仍中性）", n == 1 and db["updates"] == [(1, {"direction": 2, "importance": 4})], db["updates"])

print("\n失敗：", FAIL if FAIL else "無")
sys.exit(1 if FAIL else 0)
