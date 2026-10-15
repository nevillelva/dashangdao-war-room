"""【2026-10-15】查N 指令旁的勝率字串：5日／10日／20日（含舊資料列沒有新欄位、樣本太少的情況）。"""
import sys
import textwrap

SRC = open("dashangdao.py", encoding="utf-8").read()
a = SRC.index("            def _win_rate_badge(cmd):")
b = SRC.index("                return _txt\n", a) + len("                return _txt\n")
CODE = textwrap.dedent(SRC[a:b])

fails = []
def check(n, c):
    print(("PASS: " if c else "FAIL: ") + n)
    if not c: fails.append(n)

def mk(row):
    ns = {"_win_rate_map": {"查6.營收雙增爆發突破": row}}
    exec(CODE, ns)
    return ns["_win_rate_badge"]("查6.營收雙增爆發突破")

# 2026-10-15 14:10 實際寫入資料庫的查6
real = {"sample_count": 4344, "win_rate_3d": 50.2, "avg_return_3d": 1.17,
        "win_rate_10d": 52.7, "sample_count_10d": 4344, "win_rate_20d": 53.1, "sample_count_20d": 4144}
t = mk(real)
print(t)
check("5日勝率仍在", "5日勝率50.2%（4344筆，均報酬+1.17%）" in t)
check("顯示10日勝率52.7%（4344筆）", "10日勝率52.7%（4344筆）" in t)
check("顯示20日勝率53.1%（4144筆）", "20日勝率53.1%（4144筆）" in t)
old = {"sample_count": 4344, "win_rate_3d": 50.2, "avg_return_3d": 1.17}
t = mk(old)
check("舊資料列顯示『待下次回測』，不填0", "10日待下次回測" in t and "20日待下次回測" in t and "勝率0" not in t)
small = dict(real, sample_count_20d=5)
check("20日樣本<10筆時誠實標註", "20日樣本僅5筆" in mk(small))
if fails:
    print(f"\n{len(fails)} 項失敗"); sys.exit(1)
print("\n全部通過")
