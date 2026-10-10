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
print("\n全部通過" if not FAIL else f"\n失敗 {len(FAIL)} 項：{FAIL}")
raise SystemExit(1 if FAIL else 0)
