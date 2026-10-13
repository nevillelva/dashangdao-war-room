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
check("方向與第一關中文標籤", ("方向", "多方") in c["chips"] and ("第一關", "強勢多方") in c["chips"])
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
