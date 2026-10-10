"""【2026-10-10 老闆指示：投信官網不行就找備案】證交所 ETF e添富『收益分配』公告掃描。
來源：https://www.twse.com.tw/zh/ETFortune/announcementList?type=distribution（全部投信的收益分配公告，分頁，新到舊）
每則公告內頁有『預估收益分配組成占比資訊』：股利所得／利息所得／收益平準金／已實現資本利得／其他所得 占比。
• 這是證交所公開揭露、各投信依規定申報的公告（官方單一來源）；占比為投信『預估』，實際以收益分配通知書為準。
• 解析函式為純函式，可離線測試；抓取前先過 robots.txt（由 etf_issuer_scan.robots_allows 處理）。
python3 test_etf_twse_scan.py"""
import html as _html
import re

BASE = "https://www.twse.com.tw/zh/ETFortune/"
LIST_URL = BASE + "announcementList?max=10&offset={offset}&type=distribution"

_LINK_RE = re.compile(r'href\s*=\s*["\']([^"\']*ETFortune/announcement\?[^"\']+)["\']', re.I)
_PCT_RE = re.compile(r"占比\s*[:：]?\s*(\d{1,3}(?:\.\d+)?)\s*%")


def extract_announcement_links(page_html, base=BASE):
    """回傳 [(絕對網址, 代號)]；只收 fund= 代號合法的收益分配公告內頁。"""
    from urllib.parse import urljoin, urlparse, parse_qs
    out, seen = [], set()
    for href in _LINK_RE.findall(page_html or ""):
        u = urljoin(base, _html.unescape(href))
        q = parse_qs(urlparse(u).query)
        fund = (q.get("fund") or [""])[0]
        if not re.fullmatch(r"[0-9A-Za-z]{4,8}", fund or ""):
            continue
        if u in seen:
            continue
        seen.add(u)
        out.append((u, fund))
    return out


def html_to_text(page_html):
    t = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", page_html or "")
    t = re.sub(r"<[^>]+>", " ", t)
    t = _html.unescape(t)
    return re.sub(r"\s+", " ", t).strip()


def parse_composition(text):
    """解析『預估收益分配組成占比』。回傳 dict(div,int,eq,gain,prem,oth 皆 0~1) 或 None。
    必須有『股利所得占比』，且所有占比加總約 100%（98~102），否則視為不可信。"""
    t = text or ""
    anchor = t.find("預估收益分配組成占比")
    if anchor < 0:
        anchor = t.find("股利所得占比")
    if anchor < 0:
        return None
    seg = t[anchor:anchor + 1500]
    out, prev = {}, 0
    for m in _PCT_RE.finditer(seg):
        label = seg[prev:m.start()][-45:]
        prev = m.end()
        v = float(m.group(1)) / 100.0
        if "股利所得" in label:
            k = "div"
        elif "利息所得" in label:
            k = "int"
        elif "平準金" in label:
            k = "eq"
        elif "其他所得" in label:
            k = "oth"
        elif "權利金" in label and "不含" not in label:
            k = "prem"
        elif "資本利得" in label:
            k = "gain"
        else:
            continue
        if k not in out:
            out[k] = v
    if "div" not in out or len(out) < 3:
        return None
    if not (0.98 <= sum(out.values()) <= 1.02):
        return None
    return out


def parse_ex_date(text):
    m = re.search(r"除息(?:交易)?日\s*[:：]?\s*(\d{2,4})\s*[/年.\-]\s*(\d{1,2})\s*[/月.\-]\s*(\d{1,2})", text or "")
    if not m:
        return None
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if y < 1911:
        y += 1911
    if not (1 <= mo <= 12 and 1 <= d <= 31):
        return None
    return f"{y:04d}-{mo:02d}-{d:02d}"


def parse_amount(text):
    m = re.search(r"配發金額\s*(?:為|:|：)?\s*(?:新臺幣|新台幣)?\s*(\d+(?:\.\d+)?)\s*元", text or "")
    return float(m.group(1)) if m else None


def parse_announcement(page_html, fund, url=""):
    """回傳 (result|None, 原因)。result 格式與 etf_issuer_scan 相同：{symbol, ex_date, amount, composition, url}。"""
    text = html_to_text(page_html)
    comp = parse_composition(text)
    if comp is None:
        return None, "公告沒有可信的預估組成占比（或加總不是100%）"
    ex = parse_ex_date(text)
    if not ex:
        return None, "找不到除息交易日"
    return {"symbol": fund, "ex_date": ex, "amount": parse_amount(text), "composition": comp,
            "url": url, "kind": "twse_estimate"}, None
