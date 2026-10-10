"""【2026-10-10 老闆指示：全部一起做｜投信公告列表爬取】
• 從各投信『公告列表』頁抓出 PDF 連結（只收收益分配相關標題），逐份交給 etf_issuer_scan 解析。
• 每個 PDF 內容只能對到『恰好一檔』我們追蹤的 ETF 代號才採用（多檔合併公告不採用，避免張冠李戴）。
• 每一次抓取都先過 robots.txt（由 etf_issuer_scan.robots_allows 處理）。
純函式（連結擷取、標題過濾、代號對應）可離線測試。"""
import re
from urllib.parse import urljoin

KEYWORDS = ("收益分配", "配息", "期後公告", "期前公告", "分配金額", "實際配發")
# 公告列表來源（已於 2026-10-10 從網站實測，見 claude/投信來源地圖_20261010.md）。{page} 代表分頁。
LISTING_SOURCES = [
    {"name": "富邦", "url": "https://websys.fsit.com.tw/FubonETF/Case/Announcement.aspx?type=DividendBoard&page={page}", "max_pages": 10},
    {"name": "第一金", "url": "https://www.fsitc.com.tw/ImportantNotice.aspx", "max_pages": 1},
]
_A_RE = re.compile(r'<a\b[^>]*href\s*=\s*["\']([^"\']+)["\'][^>]*>(.*?)</a>', re.I | re.S)


def extract_links(html, base_url):
    """回傳 [(絕對網址, 標題)]，只保留 PDF 連結。標題取 <a> 的文字（去標籤、去空白）。"""
    out = []
    for href, inner in _A_RE.findall(html or ""):
        if ".pdf" not in href.lower():
            continue
        title = re.sub(r"<[^>]+>", " ", inner)
        title = re.sub(r"\s+", " ", title).strip()
        out.append((urljoin(base_url, href.strip()), title))
    return out


def is_dividend_notice(title, url=""):
    """標題或網址含收益分配相關關鍵字才收（排除經理人異動、非營業日、TISA 等）。"""
    s = f"{title} {url}"
    return any(k in s for k in KEYWORDS)


def match_single_code(text, universe):
    """回傳公告內『恰好一檔』追蹤代號；0 檔或多檔都回 None（不採用）。代號前後不可緊接英數字，避免 0056 誤中 00560。"""
    found = set()
    for code in universe:
        if re.search(r"(?<![0-9A-Za-z])" + re.escape(code) + r"(?![0-9A-Za-z])", text or ""):
            found.add(code)
    return next(iter(found)) if len(found) == 1 else None
