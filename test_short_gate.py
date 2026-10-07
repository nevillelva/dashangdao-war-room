#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_short_gate.py —— 舊評分做空三道防線 + 總經閘門區間 + 5 年版族群閘門。純函式，不連網。"""
import ast
import short_gate as sg
import sector_map as sm
import bt_strategy as bts

bad = 0
def chk(n, c, e=""):
    global bad; print("✅" if c else "❌", n, "" if c else e); bad += (not c)

# --- 盤勢 ---
ok, _ = sg.short_regime_ok({"asof": "2026-10-06", "flags_true": ["up20", "calm", "no_stress"]}, "2026-10-06")
chk("calm → 放行", ok)
ok, m = sg.short_regime_ok({"asof": "2026-10-06", "flags_true": ["up20", "wild"]}, "2026-10-06")
chk("wild → 不放行", not ok, m)
ok, m = sg.short_regime_ok({"asof": "2026-10-06", "flags_true": ["calm", "shock5"]}, "2026-10-06")
chk("calm 但急殺 shock5 → 不放行", not ok, m)
ok, m = sg.short_regime_ok({"asof": "2026-10-01", "flags_true": ["calm"]}, "2026-10-06")
chk("狀態過期 → 不放行", not ok, m)
chk("沒有狀態 → 不放行", sg.short_regime_ok({}, "2026-10-06")[0] is False and sg.short_regime_ok(None)[0] is False)

# --- 弱勢族群 ---
cards = {}
secmap = {}
for i in range(6):   # 弱勢族群 A：收盤都在 MA20 下方 3%
    cards[f"A{i}"] = {"price": 97, "ma20": 100}; secmap[f"A{i}"] = "A"
for i in range(6):   # 強勢族群 B
    cards[f"B{i}"] = {"price": 105, "ma20": 100}; secmap[f"B{i}"] = "B"
for i in range(3):   # 樣本不足族群 C（<5 檔）
    cards[f"C{i}"] = {"price": 100, "ma20": 100}; secmap[f"C{i}"] = "C"
weak = sg.weak_sector_set(cards, secmap)
chk("只有 A 是弱勢（C 樣本不足、B 強勢）", weak == {"A"}, weak)
kept, dropped = sg.filter_short_candidates([{"symbol": "A1"}, {"symbol": "B1"}, {"symbol": "ZZ"}], secmap, weak)
chk("弱勢族群過濾", [k["symbol"] for k in kept] == ["A1"] and len(dropped) == 2)
k2, d2 = sg.filter_short_candidates([{"symbol": "A1"}], secmap, set())
chk("沒有弱勢族群 → 全擋（今天不做空）", k2 == [] and len(d2) == 1)

# --- 硬性停損 ---
chk("空單 +6% 觸發", sg.short_hard_stop_hit(100, 106, 6) and sg.short_hard_stop_hit(100, 110, 6))
chk("空單 +5.9% 不觸發", not sg.short_hard_stop_hit(100, 105.9, 6))
chk("壞資料不觸發", not sg.short_hard_stop_hit(None, 100) and not sg.short_hard_stop_hit(0, 100))

# --- decide_exit_reason / classify_gate_mode（從 system_scheduler 用 ast 抽出）---
src = open("system_scheduler.py", encoding="utf-8").read()
tree = ast.parse(src)
nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in ("decide_exit_reason", "classify_gate_mode")]
ns = {"_sg": sg}
exec(compile(ast.Module(nodes, []), "x", "exec"), ns)
d = ns["decide_exit_reason"]
chk("空單硬停損優先於 support", d("short", 106.5, 100, 100, 200, 0.5, entry=100, short_hard_stop_pct=6) == "hard_stop")
chk("空單未達停損維持原規則 ma_reclaim", d("short", 103, 100, 100, 50, 1.5, entry=100, short_hard_stop_pct=6) == "ma_reclaim")
chk("不給 entry 時向下相容", d("short", 103, 100, 100, 50, 1.5) == "ma_reclaim")
chk("多單不受影響", d("long", 99, 100, 100, 50, 1.0, entry=100, short_hard_stop_pct=6) == "ma_break")
g = ns["classify_gate_mode"]
for sox, tsm, bull, want in [(-1.95, None, False, "hedge"), (-1.9, None, False, "hedge"), (-2.0, None, False, "panic"),
                             (-0.5, None, False, "hedge"), (-0.4, None, False, "bull"), (-1.95, None, True, "bull"),
                             (None, -2.5, True, "panic")]:
    chk(f"閘門 sox={sox} tsm={tsm} bull={bull} → {want}", g(sox, tsm, bull)[0] == want, g(sox, tsm, bull))

# --- 5 年版族群閘門 ---
ref = {"long": {"sectors": {
    "電子零組件業": {"rules": {"pullback_burst": {"all": {"IS": {"n": 92, "win": 0.609, "exp_pct": 2.1}, "OOS": {"n": 105, "win": 0.533, "exp_pct": 0.3}}}}},
    "生技醫療業": {"rules": {"pullback_burst": {"all": {"IS": {"n": 51, "win": 0.372, "exp_pct": -3.2}, "OOS": {"n": 35, "win": 0.257, "exp_pct": -5.6}}}}},
    "食品工業": {"rules": {"pullback_burst": {"all": {"IS": {"n": 10, "win": 0.2, "exp_pct": -3.0}, "OOS": {"n": 8, "win": 0.3, "exp_pct": -1.0}}}}},
}}}
chk("電子零組件 pass", sm.sector_gate_status_5y(ref, "電子零組件業", "pullback_burst")[0] == "pass")
chk("生技 fail", sm.sector_gate_status_5y(ref, "生技醫療業", "pullback_burst")[0] == "fail")
chk("食品 樣本內不足 → nodata", sm.sector_gate_status_5y(ref, "食品工業", "pullback_burst")[0] == "nodata")
chk("穿山惡龍不分族群 → pass", sm.sector_gate_status_5y(ref, "生技醫療業", "chuan_e_ma60_40")[0] == "pass")
chk("沒有參考表 → noref", sm.sector_gate_status_5y(None, "x", "pullback_burst")[0] == "noref")
sigs = [{"symbol": "1", "rule": "pullback_burst"}, {"symbol": "2", "rule": "pullback_burst"}, {"symbol": "3", "rule": "chuan_e_ma60_40"},
        {"symbol": "4", "rule": "pullback_burst"}]
smap = {"1": "電子零組件業", "2": "生技醫療業", "3": "生技醫療業", "4": "食品工業"}
kept, dropped = bts.apply_sector_gate_5y(sigs, "soft", smap, ref)
chk("soft：只擋生技的爆量回檔", [k["symbol"] for k in kept] == ["1", "3", "4"] and [d_[0]["symbol"] for d_ in dropped] == ["2"], (kept, dropped))
kept, dropped = bts.apply_sector_gate_5y(sigs, "strict", smap, ref)
chk("strict：nodata 的爆量回檔也擋", [k["symbol"] for k in kept] == ["1", "3"], kept)
kept, dropped = bts.apply_sector_gate_5y(sigs, "off", smap, ref)
chk("off：全放行", len(kept) == 4 and not dropped)
raise SystemExit(1 if bad else 0)
