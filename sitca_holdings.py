"""投信投顧公會(SITCA)公開統計「基金投資明細-季占基金淨資產價值1%以上」抓取與彙總。
【2026-10-16】用途：取得各投信基金持有國內個股的金額，彙總成「投信合計持股市值」，當投信持股比率的免費替代。
限制（務必標註）：只含基金淨值占比≥1%的部位（季）或月前十大 → 低估；公布時間落後約10個營業日；
含被動 ETF 與指數基金（非主動作帳）；金額為市值，不是股數。
合規：公開頁、無登入／驗證碼；低頻率（每請求間隔 ≥1.5 秒）、每個年月×公司一次；只在 Actions 執行。
"""
from __future__ import annotations
import re
import time
from lxml import html as LH

URL = "https://www.sitca.org.tw/ROC/Industry/IN2630.aspx?pid=IN22601_05"
P = "ctl00$ContentPlaceHolder1$"


def hidden_fields(doc) -> dict:
    out = {}
    for el in doc.xpath('//input[@type="hidden"]'):
        n = el.get("name")
        if n:
            out[n] = el.get("value") or ""
    return out


def select_options(doc, name_suffix: str) -> list[tuple[str, str]]:
    sel = doc.xpath(f'//select[contains(@name,"{name_suffix}")]')
    if not sel:
        return []
    return [(o.get("value") or "", (o.text or "").strip()) for o in sel[0].xpath(".//option")]


def parse_rows(doc) -> list[list[str]]:
    """回傳結果表（含 header 列）。只看沒有巢狀 table 的最內層表，且表頭要有「標的代號」與「占基金淨資產」。"""
    for t in doc.xpath("//table[not(.//table)]"):
        rows = []
        for tr in t.xpath(".//tr"):
            cells = [re.sub(r"\s+", " ", c.text_content()).strip()[:60] for c in tr.xpath("./td|./th")]
            if cells:
                rows.append(cells)
        if any(any("標的代號" in c for c in r) and any("占基金淨資產" in c for c in r) for r in rows[:3]):
            return rows
    return []


def to_num(s: str):
    s = (s or "").replace(",", "").replace("%", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def aggregate(rows: list[list[str]]) -> dict[str, dict]:
    """rows → {symbol: {'amt': 金額合計, 'funds': 基金檔數}}；只取標的種類含「國內」且代號為 4~6 碼數字/英數。"""
    if not rows:
        return {}
    hdr = None
    for i, r in enumerate(rows):
        if any("標的代號" in c for c in r):
            hdr = i
            break
    if hdr is None:
        return {}
    h = rows[hdr]
    ix_kind = next((i for i, c in enumerate(h) if "標的種類" in c), None)
    ix_code = next((i for i, c in enumerate(h) if "標的代號" in c), None)
    ix_amt = next((i for i, c in enumerate(h) if c.startswith("金額")), None)
    if None in (ix_kind, ix_code, ix_amt):
        return {}
    out: dict[str, dict] = {}
    for r in rows[hdr + 1:]:
        if len(r) <= max(ix_kind, ix_code, ix_amt):
            continue
        kind, code, amt = r[ix_kind], r[ix_code].strip(), to_num(r[ix_amt])
        if "國內" not in kind or amt is None or not re.fullmatch(r"[0-9A-Z]{4,6}", code):
            continue
        d = out.setdefault(code, {"amt": 0.0, "funds": 0})
        d["amt"] += amt
        d["funds"] += 1
    return out


def fetch(session, ym: str, comid: str, sleep_s: float = 1.5):
    """回傳 (rows, info)。info 只含計數與狀態，不含內容。"""
    r0 = session.get(URL, timeout=40)
    doc0 = LH.fromstring(r0.text)
    form = hidden_fields(doc0)
    form.update({P + "ddlQ_YM": ym, P + "rdo1": "rbComid", P + "ddlQ_Comid": comid, P + "BtnQuery": "查詢"})
    time.sleep(sleep_s)
    r1 = session.post(URL, data=form, timeout=60)
    doc1 = LH.fromstring(r1.text)
    rows = parse_rows(doc1)
    pager = bool(doc1.xpath('//a[contains(@href,"Page$")]'))
    return rows, {"status": r1.status_code, "bytes": len(r1.text), "rows": len(rows), "pager": pager, "doc": doc1}
