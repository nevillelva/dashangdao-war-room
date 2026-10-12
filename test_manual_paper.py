"""【2026-10-13】手動模擬倉純函式測試（離線）。"""
import sys
from manual_paper import validate_trade, pnl, stop_hit, target_hit

fails = []


def check(name, cond):
    print(("PASS: " if cond else "FAIL: ") + name)
    if not cond:
        fails.append(name)


ok, _ = validate_trade("買", 100, 1)
check("合法買進通過", ok)
check("方向錯誤擋下", not validate_trade("多", 100, 1)[0])
check("價格 0 擋下", not validate_trade("買", 0, 1)[0])
check("張數非正整數擋下", not validate_trade("買", 100, 0)[0])
check("買進停損高於買價擋下", not validate_trade("買", 100, 1, stop=101)[0])
check("放空停損低於放空價擋下", not validate_trade("賣", 100, 1, stop=99)[0])
check("買進目標低於買價擋下", not validate_trade("買", 100, 1, target=99)[0])
check("放空目標高於放空價擋下", not validate_trade("賣", 100, 1, target=101)[0])

r = pnl("買", 100, 110, 2)
check("買進漲 10% 扣成本後 9.415%", abs(r["pct"] - 9.415) < 1e-6)
check("買進金額 = 進場金額 × 報酬率（2 張 = 20 萬）", abs(r["amount"] - 200000 * 0.09415) < 1)
r2 = pnl("賣", 100, 90, 1)
check("放空跌 10% 扣成本後 11.111-0.585 ≈ 10.526%", abs(r2["pct"] - ((100 / 90 - 1) * 100 - 0.585)) < 1e-3)
check("買進平盤仍扣成本為負", pnl("買", 100, 100, 1)["pct"] < 0)
check("停損觸發（買）", stop_hit("買", 95, 96))
check("停損未觸發（買）", not stop_hit("買", 97, 96))
check("停損觸發（賣）", stop_hit("賣", 105, 104))
check("目標觸發（買）", target_hit("買", 111, 110))
check("無停損不觸發", not stop_hit("買", 1, None))

if fails:
    print(f"\n{len(fails)} 項失敗")
    sys.exit(1)
print("\n全部通過")
