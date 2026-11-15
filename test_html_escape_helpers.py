#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_html_escape_helpers.py —— dashangdao_helpers 的 _fmt_* 與 dashangdao.py 的 _note_cell 的 HTML 跳脫（2026-11-15）。
把 <script>/事件屬性 payload 塞進每個可能來自外部/DB 的欄位，確認輸出裡沒有未跳脫的標籤，且程式寫死的樣式標籤仍在
（沒有把版面一起跳脫掉）。判定表見專案文件「HTML跳脫審計_helpers_20261115」（不在公開倉庫）。"""
import ast, sys, types, textwrap
try:
    import openai  # noqa: F401
except ImportError:
    _m = types.ModuleType("openai"); _m.OpenAI = object; sys.modules["openai"] = _m
import dashangdao_helpers as h
from safe_html import esc

ok_all = True
def chk(name, cond, extra=""):
    global ok_all
    print(("✅ " if cond else "❌ ") + name + ("" if cond or not extra else f"  [{extra}]"))
    ok_all = ok_all and bool(cond)

X = '<script>alert(1)</script>'
IMG = '<img src=x onerror=alert(2)>'
def clean(out, label):
    chk(f"{label}：無原始 <script>/<img", "<script" not in out and "<img" not in out and "onerror=alert" not in out.replace("&lt;img src=x onerror=alert(2)&gt;", ""), out[:200])
def has_escaped(out, label, payload=X):
    chk(f"{label}：payload 以跳脫形式保留", esc(payload) in out, out[:200])

def ev(verdict="strong", **kw):
    d = {"verdict": verdict, "label": X, "detail": IMG}
    d.update(kw); return d

# --- 一般判斷區塊：label / detail / 百分比 ---
for fn, key, extra in [
    (h._fmt_closing_strength, "closing_strength", {"pct": X, "has_long_upper_shadow": True}),
    (h._fmt_volume_followthrough, "volume_followthrough", {"ratio_pct": X}),
    (h._fmt_pullback_health, "pullback_health", {"price_pct": X}),
    (h._fmt_rebound_health, "rebound_health", {"vol_ratio_pct": X}),
    (h._fmt_order_book_pressure, "order_book", {"depth_ratio": X, "is_thickening": True, "data_completeness": "full"}),
    (h._fmt_today_liquidity, "liquidity", {"pct_of_avg": X}),
]:
    out = fn({key: ev(**extra)})
    clean(out, fn.__name__); has_escaped(out, fn.__name__)
    chk(f"{fn.__name__}：版面標籤仍在", '<div style="font-size:12px;' in out and "<strong" in out)
    if key != "liquidity":   # 流動性 unknown 不畫
        out = fn({key: ev(verdict="unknown", **extra)})
        clean(out, fn.__name__ + "(unknown)")

out = h._fmt_day_trader_and_margin({"day_trader_ratio": ev(ratio_pct=X), "margin_regime": ev()})
clean(out, "_fmt_day_trader_and_margin"); has_escaped(out, "_fmt_day_trader_and_margin")
out = h._fmt_vwap_position({"vwap_position": ev(deviation_pct=1.5, vwap=X)})
clean(out, "_fmt_vwap_position"); has_escaped(out, "_fmt_vwap_position")
out = h._fmt_daytrade_verdict_banner({"daytrade_recommendation": {"verdict": "aggressive", "label": X, "detail": IMG,
                                                                  "veto_reason": X, "score": 3}})
clean(out, "_fmt_daytrade_verdict_banner"); has_escaped(out, "_fmt_daytrade_verdict_banner")
chk("_fmt_daytrade_verdict_banner：橫幅樣式保留", "display:flex" in out and "⚡ 當沖建議" in out)

# --- 趨勢徽章：全硬編碼，未知 regime 不輸出 ---
chk("_fmt_trend_regime_tag：未知 regime（外部字串）→ 空字串", h._fmt_trend_regime_tag({"trend_regime": X}) == "")
chk("_fmt_trend_regime_tag：合法值正常輸出", "趨勢股" in h._fmt_trend_regime_tag({"trend_regime": "trending"}))

# --- 主力成本、VWAP 成本 ---
out = h._fmt_main_force_cost({"mf_cost": {"vwap20": X, "dev_vwap20": 1.0, "vwap60": 10.0, "dev_vwap60": -1.0,
                                          "heavy_vwap": 11.0, "dev_heavy": 0.0, "heavy_days": X}})
clean(out, "_fmt_main_force_cost"); has_escaped(out, "_fmt_main_force_cost")
out = h._fmt_vwap({"f_vwap": {"vwap": 10.0, "side": X, "days": IMG, "lots": 5}, "price": 11}, "f_vwap", X, "#fff")
clean(out, "_fmt_vwap"); has_escaped(out, "_fmt_vwap", X)
out = h._fmt_vwap({"price": 11}, "f_vwap", X, "#fff")
clean(out, "_fmt_vwap(無資料)")

# --- 官方公開資料（alerts 本就跳脫；日期欄位）---
_orig = h._load_official_chip_extras
h._load_official_chip_extras = lambda sym: {"alerts": [("warn", X)], "margin": {"ratio": 1.0, "use_pct": 2.0, "date": IMG}, "qfii_pct": 3.0}
out = h._fmt_official_open_data({"code": "2330"})
clean(out, "_fmt_official_open_data"); has_escaped(out, "_fmt_official_open_data")
h._load_official_chip_extras = _orig

# --- 事件區塊 ---
out = h._fmt_events_block({"recent_events": {"flags": [X], "events": [{"date": IMG, "time": X, "direction": 1, "category": X, "subject": IMG}]}})
clean(out, "_fmt_events_block"); has_escaped(out, "_fmt_events_block")

# --- 分點型態 ---
rec = {"verdict": "flip", "hist_days": 3, "log_date": X, "flip_pct": 30, "build_pct": 20, "foreign_pct": 10,
       "flip_buy": 100, "flip_sell": 5, "build_buy": 10, "build_sell": 3, "build_net_win": 7, "foreign_buy": 1, "other_buy": 1,
       "detail": {"window": X, "cnt": {"flip": X, "build": X, "foreign": X, "unknown": X},
                  "flip": [{"b": X, "n": 5, "why": IMG}], "build": [{"b": X, "n": 5, "k": X}], "foreign": [{"b": IMG, "n": 1}]}}
try:
    out = h._fmt_broker_style_block({"broker_style": {"latest": rec, "stale": False, "hist": [rec, dict(rec, log_date=IMG)]}})
except ValueError:
    # build 的 k 若為非數字，原本就會在 int() 轉型拋錯（不會輸出 HTML）；改用數字字串驗證其餘欄位
    rec["detail"]["build"][0]["k"] = 3
    out = h._fmt_broker_style_block({"broker_style": {"latest": rec, "stale": False, "hist": [rec, dict(rec, log_date=IMG)]}})
clean(out, "_fmt_broker_style_block"); has_escaped(out, "_fmt_broker_style_block")
chk("_fmt_broker_style_block：圖條與卡片外框保留", "display:flex; height:10px" in out and 'border-radius:6px' in out)

# --- 當沖摘要（intraday_gate.overall_label 來自 DB）---
out = h._fmt_daytrade_summary({"intraday_gate": {"overall_verdict": "pass", "overall_label": X},
                               "order_book": ev(depth_ratio=1.2), "vwap_position": ev(deviation_pct=0.5),
                               "rebound_health": ev(), "liquidity": ev(verdict="adequate")})
clean(out, "_fmt_daytrade_summary"); has_escaped(out, "_fmt_daytrade_summary")

# --- dashangdao.py 巢狀函式 _note_cell：以 AST 取出原始碼單獨執行 ---
tree = ast.parse(open("dashangdao.py", encoding="utf-8").read())
fn_src = None
for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef) and node.name == "_note_cell":
        fn_src = ast.get_source_segment(open("dashangdao.py", encoding="utf-8").read(), node)
chk("找到 dashangdao.py 的 _note_cell", fn_src is not None)
ns = {"esc": esc}
exec(textwrap.dedent("    " + fn_src) if not fn_src.startswith("def") else fn_src, ns)
note = ns["_note_cell"]
o = note({"tv_rank": X, "hot": True, "flip_pct": 25, "up_days": 4})
clean(o, "_note_cell(熱門)"); has_escaped(o, "_note_cell(熱門)")
o = note({"tv_rank": X, "hot": False, "track": "短線動能", "flip_pct": 5, "up_days": 1})
clean(o, "_note_cell(非熱門)"); has_escaped(o, "_note_cell(非熱門)")
chk("_note_cell：缺值回空字串", note({}) == "")
chk("_note_cell：正常數值輸出不變", "第3名" in note({"tv_rank": 3, "hot": True}) and "連漲 4 天" in note({"up_days": 4}))

print("全部通過" if ok_all else "有失敗項目")
sys.exit(0 if ok_all else 1)
