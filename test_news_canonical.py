"""【2026-10-13】新聞網址規範化測試（離線）：同一則新聞不因追蹤參數重複存、重複推播。"""
import sys
import premarket as pm

fails = []


def check(name, cond):
    print(("PASS: " if cond else "FAIL: ") + name)
    if not cond:
        fails.append(name)


base = "https://www.moneydj.com/kmdj/news/newsviewer.aspx?a=bba6493b-1766-4009-8496-49141096a5b1"
u1 = base + "&amp;utm_source=185957185957665957961826&amp;utm_medium=RSS"
u2 = base + "&amp;utm_source=37165796376857961837576718&amp;utm_medium=RSS"
check("HTML 實體 &amp; 還原為 &", "&amp;" not in pm.canonical_url(u1) and "a=bba6493b" in pm.canonical_url(u1))
check("utm 參數去除後，同一則新聞的規範網址相同", pm.canonical_url(u1) == pm.canonical_url(u2))
check("同一則新聞雜湊相同（不再重複存）", pm.news_hash("moneydj", u1) == pm.news_hash("moneydj", u2))
check("不同文章（a 參數不同）雜湊不同", pm.news_hash("moneydj", base) != pm.news_hash("moneydj", base.replace("bba6", "ccc6")))
check("非追蹤參數保留", "a=" in pm.canonical_url(u1))
check("去除 #片段", pm.canonical_url("https://x.com/a?b=1#frag") == "https://x.com/a?b=1")
check("空值不丟例外", pm.canonical_url("") == "" and pm.canonical_url(None) == "")
check("來源不同視為不同（保留來源維度）", pm.news_hash("moneydj", u1) != pm.news_hash("yahoo_tw", u1))

if fails:
    print(f"\n{len(fails)} 項失敗")
    sys.exit(1)
print("\n全部通過")
