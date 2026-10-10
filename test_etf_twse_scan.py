"""證交所 ETF e添富收益分配公告解析的離線測試（文字取自 2026-10-10 實際公告）。python3 test_etf_twse_scan.py"""
import etf_twse_scan as T

FAIL = []


def check(name, cond, extra=""):
    if cond:
        print("✅", name)
    else:
        FAIL.append(name)
        print("❌", name, extra)


# 00939 統一（含百分比、無冒號）
H_939 = """<table><tr><td>說明</td><td>2.每受益權單位配發金額:新臺幣0.125元。 6.除息交易日:115/10/05 7.收益分配發放日:115/10/29
9.預估收益分配組成占比資訊:預估收益分配組成占比(備註:計算公式為：【預估各組成項目之收益】/【預估收益分配金額】)
(1)股利所得占比43.2% (2)利息所得占比0% (3)收益平準金占比0% (4)已實現資本利得占比56.8% (5)其他所得占比0%
(6)警語：因本基金初級市場之現金申購</td></tr></table>"""
r, why = T.parse_announcement(H_939, "00939", "u1")
check("00939 股利所得 43.2%", r and abs(r["composition"]["div"] - 0.432) < 1e-9, why)
check("00939 資本利得 56.8%", r and abs(r["composition"]["gain"] - 0.568) < 1e-9)
check("00939 除息日 2026-10-05", r and r["ex_date"] == "2026-10-05", r)
check("00939 金額 0.125", r and r["amount"] == 0.125, r)

# 00940 元大
H_940 = """每受益權單位配發金額:新臺幣0.055元。 6.除息交易日:115/10/07
9.預估收益分配組成占比資訊: (1)股利所得占比20.00% (2)利息所得占比0.00% (3)收益平準金占比0.00% (4)已實現資本利得占比80.00% (5)其他所得占比0.00%"""
r, why = T.parse_announcement(H_940, "00940", "u2")
check("00940 股利所得 20%", r and abs(r["composition"]["div"] - 0.20) < 1e-9, why)

# 00946 群益（有冒號、句點）
H_946 = """每受益權單位實際配發金額為新臺幣0.058元。 6.除息交易日:115/10/07
預估收益分配組成占比(備註:計算公式為:【預估各組成項目分配之收益】/【預估收益分配金額】) 1. 股利所得占比:0.00%。 2. 利息所得占比:0.00%。 3. 收益平準金占比:0.00%。 4. 已實現資本利得占比:100.00%。 5. 其他所得占比:0.00%。"""
r, why = T.parse_announcement(H_946, "00946", "u3")
check("00946 股利所得 0", r and r["composition"]["div"] == 0.0, why)
check("00946 金額 0.058", r and r["amount"] == 0.058, r)

# 00400A 國泰（資本利得不含權利金＋權利金）
H_400 = """每受益權單位配發金額:新臺幣0.12元。 6.除息交易日:115/10/08
9.預估收益分配組成占比資訊: (1)股利所得占比0.00% (2)利息所得占比0.00% (3)收益平準金占比0.00%
(4)已實現資本利得(不含賣出選擇權權利金)占比100.00% 已實現資本利得-賣出選擇權權利金占比0.00% (5)其他所得占比0.00%"""
r, why = T.parse_announcement(H_400, "00400A", "u4")
check("00400A 資本利得100%（不含權利金）", r and r["composition"]["gain"] == 1.0, why)
check("00400A 權利金0", r and r["composition"]["prem"] == 0.0, r)

# 00406A 中信（權利金 100%）
H_406 = """每受益權單位配發金額:新臺幣0.138元整。 6.除息交易日:115/10/05
預估收益分配組成占比 (1)股利所得占比0.00% (2)利息所得占比0.00% (3)收益平準金占比0.00%
(4)已實現資本利得(不含賣出選擇權權利金)占比0.00% 已實現資本利得-賣出選擇權權利金占比100.00% (5)其他所得占比0.00%"""
r, why = T.parse_announcement(H_406, "00406A", "u5")
check("00406A 權利金100%", r and r["composition"]["prem"] == 1.0 and r["composition"]["div"] == 0.0, why)
check("00406A 金額 0.138（元整）", r and r["amount"] == 0.138, r)

# 負面：0056 評價結果（沒有組成）
r, why = T.parse_announcement("每受益權單位預估配發金額為新臺幣1.72元 6.除息交易日:115/10/22", "0056", "u6")
check("無組成占比 → 不採用", r is None, why)

# 負面：占比加總不是 100%
bad = "除息交易日:115/10/05 預估收益分配組成占比 股利所得占比50% 利息所得占比0% 收益平準金占比0% 已實現資本利得占比20% 其他所得占比0%"
r, why = T.parse_announcement(bad, "00001", "u7")
check("加總不是100% → 不採用", r is None, why)

# 連結擷取
LIST = '''<a href="/zh/ETFortune/announcement?company=A00005&amp;date=20261005&amp;seq=1&amp;fund=00940&amp;type=distribution">x</a>
<a href="/zh/ETFortune/announcement?company=A00005&amp;date=20261005&amp;seq=1&amp;fund=00940&amp;type=distribution">dup</a>
<a href="/zh/ETFortune/announcementList?max=10&amp;offset=10&amp;type=distribution">next</a>
<a href="/zh/ETFortune/announcement?company=A00009&amp;date=20261001&amp;seq=1&amp;fund=00939&amp;type=distribution">y</a>'''
links = T.extract_announcement_links(LIST)
check("連結：去重且只收公告內頁", [f for _, f in links] == ["00940", "00939"], links)
check("連結：轉成絕對網址並還原 &amp;", links[0][0].startswith("https://www.twse.com.tw/zh/ETFortune/announcement?company=A00005&date="), links[0][0])

print("\n全部通過" if not FAIL else f"\n失敗 {len(FAIL)} 項：{FAIL}")
raise SystemExit(1 if FAIL else 0)
