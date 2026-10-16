"""【2026-10-16】隔夜掃描的查N 展開區依編號排序（含兩位數、無編號排最後）。"""
import re, sys
src = open("dashangdao.py", encoding="utf-8").read()
a = src.index("            def _cmd_sort_key(item):")
b = src.index("            for cmd, rows in sorted(_by_command.items(), key=_cmd_sort_key):")
code = "\n".join(l[12:] for l in src[a:b].splitlines())
ns = {}
exec(code, ns)
k = ns["_cmd_sort_key"]
names = ["查13.情報雷達：其他", "查11.高殖利率(≥4.5%)", "查2.魚頭慢伏支撐", "查10.籌碼沉澱量縮潛伏", "查1.主升段突擊", "情報黃金交叉", "查9.均線糾結爆量突破"]
got = [n for n, _ in sorted([(n, []) for n in names], key=k)]
want = ["查1.主升段突擊", "查2.魚頭慢伏支撐", "查9.均線糾結爆量突破", "查10.籌碼沉澱量縮潛伏", "查11.高殖利率(≥4.5%)", "查13.情報雷達：其他", "情報黃金交叉"]
print(("PASS" if got == want else "FAIL") + ": 依編號排序（10 排在 9 後、無編號最後）")
if got != want:
    print(got); sys.exit(1)
