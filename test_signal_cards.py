"""【2026-10-13】即時訊號卡純函式測試（離線）。"""
import sys
from signal_cards import build_card, filter_cards, power_series

fails = []


def check(name, cond):
    print(("PASS: " if cond else "FAIL: ") + name)
    if not cond:
        fails.append(name)


def bar(t, o, c, v, h=None, l=None):
    return {"bar_time": t, "open": o, "high": h or max(o, c), "low": l or min(o, c), "close": c, "volume": v}


bars = [bar("09:00", 100, 101, 1000), bar("09:05", 101, 100, 500), bar("09:10", 100, 102, 2000), bar("09:15", 102, 102, 300)]

ps = power_series(bars)
check("K線法：收>開為正、收<開為負、平盤為0", ps == [("09:00", 1000.0), ("09:05", -500.0), ("09:10", 2000.0), ("09:15", 0.0)])

gate = {"gate1_verdict": "strong_bull", "direction": "long", "overall_label": "前兩關合格", "detail": {"k": 1}}
c = build_card("2330", "台積電", bars, gate, tags=("持倉",))
check("有資料：時間取最後一根", c["time"] == "09:15" and c["last"] == 102.0)
check("漲跌幅＝最後收／首開−1", c["change_pct"] == 2.0)
check("多空力道比例＝淨力道／總量", c["power_ratio"] == round((1000 - 500 + 2000) / 3800, 3))
check("方向與第一關中文標籤", ("候選方向", "多方") in c["chips"] and ("第一關", "強勢多方") in c["chips"])
check("標記出現在 chips", ("標記", "持倉") in c["chips"])
check("迷你走勢點數＝K 棒數", len(c["closes"]) == 4)

empty = build_card("9999", "", [], None, tags=("雷達",))
check("無 K 棒：標示空卡、不丟例外", empty["empty"] and empty["time"] is None and empty["power_ratio"] is None)
check("無判讀時方向為 None", empty["side"] is None)

g_short = {"gate1_verdict": "weak_bear", "direction": None, "overall_label": "", "detail": None}
s = build_card("2317", "鴻海", bars, g_short)
check("無 direction 欄時由第一關推算方向（弱勢空方→空方）", s["side"] == "short")

unsorted_bars = [bar("09:15", 102, 102, 300), bar("09:00", 100, 101, 1000)]
u = build_card("2454", "聯發科", unsorted_bars, None)
check("K 棒亂序輸入會排序：首開取 09:00", u["change_pct"] == round((102 / 100 - 1) * 100, 2))

cards = [
    build_card("A", "", bars, gate, tags=("持倉",)),
    build_card("B", "", bars, g_short, tags=("雷達",)),
]
cards[0]["time"], cards[1]["time"] = "09:20", "10:05"
check("依時間新到舊排序", [x["symbol"] for x in filter_cards(cards)] == ["B", "A"])
check("方向篩選：只看多方", [x["symbol"] for x in filter_cards(cards, side="long")] == ["A"])
check("標記篩選：只看雷達", [x["symbol"] for x in filter_cards(cards, tag="雷達")] == ["B"])
check("第一關篩選：強勢多方", [x["symbol"] for x in filter_cards(cards, g1_group="strong_bull")] == ["A"])

if fails:
    print(f"\n{len(fails)} 項失敗")
    sys.exit(1)
print("\n全部通過")

# 【2026-10-15】卡片顯示第二關晶片；不適用＝本身即龍頭
_g = {"gate1_verdict": "weak_bull", "gate2_verdict": "not_applicable", "direction": "long", "overall_label": "x", "detail": {}}
_c = build_card("1605", "華新", [{"bar_time": "09:00", "open": 10, "high": 11, "low": 9, "close": 10.5, "volume": 100}], _g)
print("PASS" if ("第二關", "不適用（本身即龍頭）") in _c["chips"] and _c["name"] == "華新" else "FAIL: 第二關晶片/股名")
assert ("第二關", "不適用（本身即龍頭）") in _c["chips"] and _c["name"] == "華新"

# --- 2026-10-15：多空兩筆都保留、只看通過 ---
import signal_cards as _sc2
_rows = [  # 已依 created_at 新到舊
    {"symbol": "2368", "direction": "short", "gate1_verdict": "weak_bear", "gate2_verdict": "pass", "overall_verdict": "pass", "overall_label": "ok", "detail": None},
    {"symbol": "2368", "direction": "long", "gate1_verdict": "weak_bear", "gate2_verdict": None, "overall_verdict": "fail", "overall_label": "x", "detail": None},
    {"symbol": "2368", "direction": "short", "gate1_verdict": "unclear", "gate2_verdict": "pass", "overall_verdict": "fail", "overall_label": "舊的", "detail": None},
]
_g = _sc2.latest_gate_by_direction(_rows)
assert set(_g["2368"]) == {"short", "long"}, "多空兩筆都應保留"
assert _g["2368"]["short"]["overall_label"] == "ok", "同方向取最新一筆"
_cards2 = [_sc2.build_card("2368", "金像電", None, x) for x in _g["2368"].values()] + [_sc2.build_card("1101", "台泥", None, None)]
assert len(_sc2.filter_cards(_cards2)) == 3
_p = _sc2.filter_cards(_cards2, only_pass=True)
assert len(_p) == 1 and _p[0]["side"] == "short", "只看通過：只剩整體通過的那張"
assert ("整體", "通過") in _p[0]["chips"]
print("PASS: 多空兩筆保留／同方向取最新／只看通過")
