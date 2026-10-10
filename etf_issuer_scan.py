"""【2026-10-10 老闆指示：投信公告夜間自動掃描】
從投信『分配收益公告』PDF 讀出：除息日、每單位配發金額、配息組成（股利所得／利息所得／收益平準金／已實現資本利得）。
• 解析函式為純函式（可離線測試）。
• 抓取前一律檢查該網站 robots.txt；不允許、或 robots.txt 讀取失敗 → 不抓（不繞過）。
• 讀出的結果一律是『單一獨立來源』（投信官方），仍走 etf_core.apply_sourced 驗證。
python3 test_etf_issuer_scan.py"""
import io
import re
import urllib.robotparser
from urllib.parse import urlparse

import requests

AGENT = "warroom-etf-research"
_LABELS = {"div": "股利所得", "int": "利息所得", "eq": "收益平準金", "gain": "已實現資本利得"}


def parse_composition(text):
    """回傳 {'div','int','eq','gain'} 占比（0~1）。股利所得必須讀得到；四項合計需約 100%，否則視為不可信回 None。"""
    t = text or ""
    out = {}
    for k, label in _LABELS.items():
        m = re.search(label + r"[^0-9%\n]{0,12}?(\d{1,3}(?:\.\d+)?)\s*%", t)
        if m:
            out[k] = float(m.group(1)) / 100.0
    if "div" not in out:
        return None
    if len(out) >= 3 and not (0.98 <= sum(out.values()) <= 1.02):
        return None
    if not (0.0 <= out["div"] <= 1.0):
        return None
    return out


def parse_ex_date(text):
    """找『除息』後面的日期；民國年（<1911）自動 +1911。回傳 YYYY-MM-DD 或 None。"""
    m = re.search(r"除息[^0-9]{0,12}(\d{2,4})\s*[/年.\-]\s*(\d{1,2})\s*[/月.\-]\s*(\d{1,2})", text or "")
    if not m:
        return None
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if y < 1911:
        y += 1911
    if not (1 <= mo <= 12 and 1 <= d <= 31):
        return None
    return f"{y:04d}-{mo:02d}-{d:02d}"


def parse_amount(text):
    m = re.search(r"每受益權單位[^0-9]{0,20}(\d+\.\d+)\s*元", text or "")
    return float(m.group(1)) if m else None


def robots_allows(url, fetch=requests.get):
    """robots.txt 規則檢查。無 robots.txt（404）視為允許；其他任何讀取失敗一律不允許。"""
    p = urlparse(url)
    try:
        r = fetch(f"{p.scheme}://{p.netloc}/robots.txt", timeout=15)
    except requests.RequestException:
        return False
    if r.status_code == 404:
        return True
    if r.status_code != 200:
        return False
    rp = urllib.robotparser.RobotFileParser()
    rp.parse(r.text.splitlines())
    return rp.can_fetch(AGENT, url) or rp.can_fetch("*", url)


def pdf_text(data):
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(data))
    return "\n".join((pg.extract_text() or "") for pg in reader.pages)


def scan_pdf(url, symbol, fetch=requests.get):
    """抓一份公告並解析。回傳 dict 或 (None, 原因)。symbol 必須出現在公告文字中（避免張冠李戴）。"""
    if not url.lower().startswith("https://"):
        return None, "非 https"
    if not robots_allows(url, fetch=fetch):
        return None, "robots.txt 不允許或無法讀取（不抓）"
    try:
        r = fetch(url, timeout=60)
    except requests.RequestException as e:
        return None, f"下載失敗 {type(e).__name__}"
    if r.status_code != 200:
        return None, f"HTTP {r.status_code}"
    try:
        text = pdf_text(r.content)
    except Exception as e:  # noqa: BLE001
        return None, f"PDF 解析失敗 {type(e).__name__}"
    if symbol not in text:
        return None, "公告內容找不到此代號"
    comp = parse_composition(text)
    ex = parse_ex_date(text)
    if comp is None:
        return None, "找不到可信的配息組成（或四項未合計100%）"
    if not ex:
        return None, "找不到除息日"
    return {"symbol": symbol, "ex_date": ex, "amount": parse_amount(text), "composition": comp, "url": url}, None


def scan_pdf_any(url, universe, fetch=requests.get):
    """不指定代號：從公告文字找出『恰好一檔』追蹤代號（由 etf_issuer_crawl.match_single_code），再解析。回傳 (result|None, 原因)。"""
    import etf_issuer_crawl as C
    if not url.lower().startswith("https://"):
        return None, "非 https"
    if not robots_allows(url, fetch=fetch):
        return None, "robots.txt 不允許或無法讀取（不抓）"
    try:
        r = fetch(url, timeout=60)
    except requests.RequestException as e:
        return None, f"下載失敗 {type(e).__name__}"
    if r.status_code != 200:
        return None, f"HTTP {r.status_code}"
    try:
        text = pdf_text(r.content)
    except Exception as e:  # noqa: BLE001
        return None, f"PDF 解析失敗 {type(e).__name__}"
    sym = C.match_single_code(text, universe)
    if not sym:
        hits = [c for c in universe if c in (text or "")]
        return None, f"公告未對到恰好一檔追蹤代號（文字{len(text or '')}字、命中{len(hits)}檔：{','.join(hits[:3])}）"
    comp = parse_composition(text)
    ex = parse_ex_date(text)
    if comp is None:
        return None, "找不到可信的配息組成（或四項未合計100%）"
    if not ex:
        return None, "找不到除息日"
    return {"symbol": sym, "ex_date": ex, "amount": parse_amount(text), "composition": comp, "url": url}, None
