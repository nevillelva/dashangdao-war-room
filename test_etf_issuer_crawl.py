"""投信公告列表爬取的離線測試。python3 test_etf_issuer_crawl.py"""
import etf_issuer_crawl as C

FAIL = []


def check(name, cond, extra=""):
    if cond:
        print("✅", name)
    else:
        FAIL.append(name)
        print("❌", name, extra)


HTML = """<ul>
<li><a href="/case/news/fund_service/20261005A.pdf">富邦系列基金115年09月收益分配公告</a></li>
<li><a href="Files/EDM/a.pdf"><span>美優債 期後公告</span></a></li>
<li><a href="/news/detail.aspx?id=1">非PDF</a></li>
<li><a href="https://etrade.fsit.com.tw/x.pdf">經理人異動</a></li>
</ul>"""
links = C.extract_links(HTML, "https://websys.fsit.com.tw/FubonETF/Case/Announcement.aspx")
check("只擷取 PDF 連結", len(links) == 3, links)
check("相對網址轉絕對", links[0][0] == "https://websys.fsit.com.tw/case/news/fund_service/20261005A.pdf", links[0][0])
check("標題去標籤", links[1][1] == "美優債 期後公告", links[1][1])
check("收益分配標題收錄", C.is_dividend_notice(links[0][1]))
check("經理人異動不收", not C.is_dividend_notice(links[2][1], links[2][0]))
U = ["0056", "00919", "00878", "00400A"]
check("單一代號命中", C.match_single_code("元大台灣高股息 0056 除息", U) == "0056")
check("0056 不誤中 00560", C.match_single_code("代號 00560 配息", U) is None)
check("多檔合併公告不採用", C.match_single_code("0056 與 00919 合併公告", U) is None)
check("無追蹤代號不採用", C.match_single_code("沒有代號", U) is None)
NM = {"0056": "元大高股息", "00631L": "元大台灣50正2", "0050": "元大台灣50", "00919": "群益台灣精選高息"}
check("名稱單一命中", C.match_single_name("群益台灣精選高息基金 收益分配", NM) == "00919")
check("子字串不誤判（50 vs 50正2）", C.match_single_name("元大台灣50正2 分配", NM) == "00631L", C.match_single_name("元大台灣50正2 分配", NM))
check("名稱多檔不採用", C.match_single_name("元大高股息與群益台灣精選高息", NM) is None)
check("名稱零檔不採用", C.match_single_name("沒有基金名稱", NM) is None)
# 【2026-10-12 54C】台新反斜線連結、凱基百分比編碼網址
_tl = C.extract_links('<a href="ETF\\Images\\ETF_ARTICLE\\ART1.pdf">公告收益分配評價結果</a>', "https://www.tsit.com.tw/ETF")
check("反斜線正規化為 /", _tl and _tl[0][0] == "https://www.tsit.com.tw/ETF/Images/ETF_ARTICLE/ART1.pdf", _tl)
_kl = C.extract_links('<a href="/Upload/Files/%E5%87%B1%E5%9F%BA_%E6%94%B6%E7%9B%8A%E5%88%86%E9%85%8D%E6%9C%9F%E5%89%8D%E5%85%AC%E5%91%8A.pdf"></a>', "https://www.kgifund.com.tw/")
check("百分比編碼網址可辨識收益分配", C.is_dividend_notice(_kl[0][1], _kl[0][0]), _kl)
check("百分比編碼的非配息公告仍不收", not C.is_dividend_notice("", "https://x/%E7%B6%93%E7%90%86%E4%BA%BA%E7%95%B0%E5%8B%95.pdf"))
check("來源含台新與凱基", {"台新", "凱基"} <= {x["name"] for x in C.LISTING_SOURCES})
check("群益不在來源（robots 不允許）", "群益" not in {x["name"] for x in C.LISTING_SOURCES})
# 凱基：標題與網址為 HTML 數字實體
_hl = C.extract_links('<a href="/Upload/Files/00945B &#x51F1;&#x57FA;_10&#x6B21;&#x6536;&#x76CA;&#x5206;&#x914D;&#x671F;&#x524D;&#x516C;&#x544A;_20261001.pdf">00945B &#x51F1;&#x57FA;&#x6536;&#x76CA;&#x5206;&#x914D;</a>', "https://www.kgifund.com.tw/")
check("HTML 實體解碼後標題可讀", _hl and "凱基收益分配" in _hl[0][1], _hl)
check("HTML 實體解碼後可辨識收益分配", _hl and C.is_dividend_notice(_hl[0][1], _hl[0][0]), _hl)
_al = C.extract_links('<a href="/Upload/Files/114KGISITE_AML&amp;CFT.pdf">聲明</a>', "https://www.kgifund.com.tw/")
check("&amp; 還原為 &", _al and _al[0][0].endswith("AML&CFT.pdf"), _al)
print("\n全部通過" if not FAIL else f"\n失敗 {len(FAIL)} 項：{FAIL}")
raise SystemExit(1 if FAIL else 0)
