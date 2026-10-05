#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_broker_style_ui.py —— 戰卡「🏦 分點型態」區塊 + 速覽欄位 + 批次讀取（假 Supabase）。"""
import os
import sys

os.environ.setdefault("SUPABASE_URL", "http://x")
os.environ.setdefault("SUPABASE_KEY", "x")
import dashangdao_helpers as H  # noqa: E402

ok = True


def check(cond, msg):
    global ok
    if not cond:
        ok = False
        print("❌", msg)


class Res:
    def __init__(self, data):
        self.data = data


class Q:
    def __init__(self, tbl, calls):
        self.tbl, self.calls = tbl, calls
        self.codes, self.since = None, None

    def select(self, *_a, **_k):
        return self

    def order(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    def in_(self, col, vals):
        self.codes = set(vals)
        return self

    def gte(self, col, v):
        self.since = v
        return self

    def execute(self):
        self.calls.append(1)
        if self.codes is None:           # 查全市場最新日
            return Res([{"log_date": "2026-10-05"}])
        return Res([r for r in ROWS if r["symbol"] in self.codes and r["log_date"] >= (self.since or "")])


class FakeConn:
    def __init__(self):
        self.calls = []

    def table(self, name):
        assert name == "broker_style_daily", name
        return Q(name, self.calls)


def rec(sym, d, verdict, flip, build, foreign, hist=7, **kw):
    top = flip + build + foreign + kw.get("other", 0)
    return {"symbol": sym, "log_date": d, "verdict": verdict, "top15_buy": top,
            "flip_buy": flip, "build_buy": build, "foreign_buy": foreign, "other_buy": kw.get("other", 0),
            "flip_pct": round(flip * 100 / top, 1) if top else 0, "build_pct": round(build * 100 / top, 1) if top else 0,
            "foreign_pct": round(foreign * 100 / top, 1) if top else 0,
            "flip_sell": kw.get("flip_sell", 0), "build_sell": kw.get("build_sell", 0), "build_net_win": kw.get("win", 0),
            "hist_days": hist,
            "detail": {"flip": [{"b": "凱基-台北", "n": flip, "k": 1, "w": 0, "f": 2, "h": 0, "why": "實測"}] if flip else [],
                       "build": [{"b": "元富-建倉", "n": build, "k": 4, "w": 400, "f": 0, "h": 3, "why": "實測"}] if build else [],
                       "foreign": [], "unknown": [], "cnt": {"flip": 1 if flip else 0, "build": 1 if build else 0, "foreign": 0, "unknown": 0},
                       "window": 7}}


ROWS = [
    rec("AAA", "2026-10-05", "build", 100, 400, 0, win=690, build_sell=30),
    rec("AAA", "2026-10-02", "flip", 500, 50, 0),
    rec("AAA", "2026-10-01", "mixed", 200, 200, 0),
    rec("BBB", "2026-10-05", "flip", 800, 100, 0, hist=4, flip_sell=120),
    rec("CCC", "2026-10-02", "build", 0, 300, 0),          # 落後全市場最新日 → stale
    rec("OLD", "2026-09-01", "build", 0, 300, 0),          # 超出 since → 不會被讀到
]

conn = FakeConn()
H.SUPABASE_CONN = conn
H._BSTYLE_CACHE.update({"key": None, "ts": 0.0, "data": {}})
m = H.load_broker_style_map(["AAA", "BBB", "CCC", "OLD", "ZZZ"])
check(set(m) == {"AAA", "BBB", "CCC"}, f"應只讀到近期有資料的 AAA/BBB/CCC：{set(m)}")
check(m["AAA"]["latest"]["log_date"] == "2026-10-05" and len(m["AAA"]["hist"]) == 3, f"AAA hist：{m['AAA']['hist']}")
check(m["CCC"]["stale"] is True and m["AAA"]["stale"] is False, "stale 標記")
n_calls = len(conn.calls)
H.load_broker_style_map(["AAA", "BBB", "CCC", "OLD", "ZZZ"])
check(len(conn.calls) == n_calls, "5 分鐘內同一批代號應走快取")

# 區塊
c = {"broker_style": m["AAA"], "vol": 5000, "price_date": "10/05"}
html = H._fmt_broker_style_block(c)
check("建倉主導" in html and "🏗️" in html, "AAA 應顯示建倉主導")
check("連買4日" in html and "凱基-台北" in html and "(實測)" in html, "應列出代表分點與依據")
check("占" not in html or "約占當日成交量 2.0%" in html, f"隔日沖 100張/5000張=2.0%：{html[:0]}")
check("約占當日成交量 2.0%" in html, "價格資料日=分點資料日 → 應顯示占成交量")
check("建倉型今日賣超 30張" in html, "建倉者出貨警示")
check("近3日" in html and "10/01" in html, "近日趨勢")
check("+690張" in html, "建倉型累計淨買")
html2 = H._fmt_broker_style_block({"broker_style": m["AAA"], "vol": 5000, "price_date": "10/06"})
check("約占當日成交量" not in html2, "日期不同不應顯示占比")
html3 = H._fmt_broker_style_block({"broker_style": m["BBB"]})
check("隔日沖主導" in html3 and "僅累積4個資料日" in html3 and "倒貨中" in html3, "BBB：隔日沖主導＋資料日偏少＋倒貨提示")
html4 = H._fmt_broker_style_block({"broker_style": m["CCC"]})
check("非最新資料日" in html4, "CCC 應標非最新資料日")
html5 = H._fmt_broker_style_block({"broker_style": None})
check("暫無資料" in html5, "無資料顯示灰字")
check(H.broker_style_short_text({"broker_style": m["AAA"]}).startswith("🏗️建倉主導 建80/沖20/外0"), H.broker_style_short_text({"broker_style": m["AAA"]}))
check(H.broker_style_short_text({}) == "—" and H.broker_style_short_text({"broker_style": None}) == "—", "速覽無資料顯示 —")

# 讀取失敗 → {}（不拋）
class Boom:
    def table(self, *_a):
        raise RuntimeError("db down")
H.SUPABASE_CONN = Boom()
H._BSTYLE_CACHE.update({"key": None, "ts": 0.0, "data": {}})
check(H.load_broker_style_map(["AAA"]) == {}, "連線失敗應回 {}")

print("✅ test_broker_style_ui 全部通過" if ok else "❌ test_broker_style_ui 失敗")
sys.exit(0 if ok else 1)
