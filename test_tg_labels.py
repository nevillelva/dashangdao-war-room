"""【2026-10-15】Telegram 訊息輔助：股名標示。"""
import sys
import system_scheduler as ss

fails = []


def check(n, c):
    print(("PASS: " if c else "FAIL: ") + n)
    if not c:
        fails.append(n)


m = {"1605": "華新", "2330": "台積電", "9999": "9999"}
check("有名稱 → 華新(1605)", ss._tg_label(m, "1605") == "華新(1605)")
check("沒名稱 → 只顯示代號", ss._tg_label(m, "0000") == "0000")
check("名稱等於代號 → 不重複顯示", ss._tg_label(m, "9999") == "9999")
check("對照表為 None 不當機", ss._tg_label(None, "2330") == "2330")
if fails:
    print(f"\n{len(fails)} 項失敗"); sys.exit(1)
print("\n全部通過")
