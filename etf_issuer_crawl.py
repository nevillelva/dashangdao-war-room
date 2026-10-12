"""【2026-10-10 老闆指示：全部一起做｜投信公告列表爬取】
• 從各投信『公告列表』頁抓出 PDF 連結（只收收益分配相關標題），逐份交給 etf_issuer_scan 解析。
• 每個 PDF 內容只能對到『恰好一檔』我們追蹤的 ETF 代號才採用（多檔合併公告不採用，避免張冠李戴）。
• 每一次抓取都先過 robots.txt（由 etf_issuer_scan.robots_allows 處理）。
純函式（連結擷取、標題過濾、代號對應）可離線測試。"""
import re
from urllib.parse import urljoin, unquote

KEYWORDS = ("收益分配", "配息", "期後公告", "期前公告", "分配金額", "實際配發")
# 公告列表來源（已於 2026-10-10 從網站實測，見 claude/投信來源地圖_20261010.md）。{page} 代表分頁。
# 【2026-10-12 死規則5 條款查核（瀏覽器實際渲染閱讀）】
#  • 富邦 websys.fsit.com.tw/FubonETF：頁尾只有「客戶資料共享管理隱私權政策、洗錢防制宣導、消費者保護、
#    Fubon.com 個資告知、投資人資訊安全說明、隱私權聲明、個資法應告知事項」，內容皆為個資／資安說明，
#    沒有禁止自動化存取、重製或轉載的條款 → 可用（低頻、每晚一次、遵守 robots.txt）。
#  • 第一金 www.fsitc.com.tw/FooterLink.aspx?ID=1007「使用本站政策」：禁止的是未經授權轉載…「用以賺取利益」；
#    本系統為老闆個人教育、非商業用途，不轉載散布 → 可用。若日後有營利或對外提供，須先取得書面授權。
#  • 【2026-10-12 54C 續】台新投信 www.tsit.com.tw（頁尾僅隱私權聲明／個資保密／金融友善服務，無禁止自動化條款）、
#    凱基投信 www.kgifund.com.tw（頁尾僅隱私權保護須知與資料共享揭露，無禁止自動化條款）→ 可用（低頻、每晚一次、遵守 robots.txt）。
#    中信投信 /LegalDisclaimers 僅投資警語，無禁止條款，但公告列表為 JS 渲染（一般請求取不到 PDF 連結）→ 暫不納入，待改用瀏覽器渲染。
#    群益投信 www.capitalfund.com.tw 的 robots.txt 不允許（實測 robots_ok=False）→ 不抓，改走人工貼上（P3）。
#    Actions 實測（diag_issuer_lists）：台新 /ETF 靜態頁含收益分配 PDF；凱基首頁靜態含最新 5 份公告 PDF（網址為百分比編碼）。
LISTING_SOURCES = [
    {"name": "台新", "url": "https://www.tsit.com.tw/ETF", "max_pages": 1},
    {"name": "凱基", "url": "https://www.kgifund.com.tw/", "max_pages": 1},
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
        # 台新的連結用反斜線（\\files\\...）；瀏覽器會視為 /，這裡同樣先正規化再合成絕對網址
        out.append((urljoin(base_url, href.strip().replace("\\", "/")), title))
    return out


def is_dividend_notice(title, url=""):
    """標題或網址含收益分配相關關鍵字才收（排除經理人異動、非營業日、TISA 等）。"""
    s = f"{title} {unquote(url or '')}"   # 凱基網址為百分比編碼，須先解碼才看得到『收益分配』
    return any(k in s for k in KEYWORDS)


def match_single_code(text, universe):
    """回傳公告內『恰好一檔』追蹤代號；0 檔或多檔都回 None（不採用）。代號前後不可緊接英數字，避免 0056 誤中 00560。"""
    found = set()
    for code in universe:
        if re.search(r"(?<![0-9A-Za-z])" + re.escape(code) + r"(?![0-9A-Za-z])", text or ""):
            found.add(code)
    return next(iter(found)) if len(found) == 1 else None


def match_single_name(text, name_map):
    """【2026-10-10】代號對不到時，改用基金名稱對應（etf_master.name）。
    規則：找出公告內出現的名稱；若某名稱是另一個已命中名稱的子字串（例如『元大台灣50』在『元大台灣50正2』內）則剔除；
    剩下『恰好一檔』才採用，多檔或零檔都不採用。name_map: {代號: 名稱}。"""
    t = text or ""
    hits = {sym: nm for sym, nm in (name_map or {}).items() if nm and len(nm) >= 3 and nm in t}
    if not hits:
        return None
    kept = [s for s, n in hits.items() if not any(n != m and n in m for m in hits.values())]
    return kept[0] if len(kept) == 1 else None
