"""【2026-10-13】新聞→個股快速卡測試（離線）。"""
import sys
from news_card import build_card, format_cards

fails = []


def check(name, cond):
    print(("PASS: " if cond else "FAIL: ") + name)
    if not cond:
        fails.append(name)


row = {"trade_date": "2026-10-12", "total_score_default_weight": 6.5,
       "f_ma_position": 1.2, "f_volume_ratio": 0.8, "f_foreign_buy": None,
       "f_institutional_resonance": -0.1, "f_institutional_persistence": 0.3,
       "f_revenue_momentum": 0.5, "f_buffer_pct": 2.0}
c = build_card("2317", "337調查 廠商捲入", row, own=True)
check("有戰卡資料：標題與日期正確", "2026-10-12" in " ".join(c["lines"]))
check("缺欄位計數：外資買超為 None 計 1", c["missing"] == 1)
check("方向不用總分：判讀寫需人工判讀", any("需人工判讀" in l for l in c["lines"]))
check("持倉標記出現", "你的持倉" in c["lines"][0])

n = build_card("9999", "某新聞", None)
check("無戰卡資料：明確寫資料不足", any("資料不足" in l for l in n["lines"]))
check("無戰卡資料：不會丟例外", n["missing"] == 7)

txt = format_cards([c, n])
check("格式化：含免責與段落標題", "不是投資建議" in txt and "快速卡" in txt)
check("空清單回空字串", format_cards([]) == "")

if fails:
    print(f"\n{len(fails)} 項失敗")
    sys.exit(1)
print("\n全部通過")
