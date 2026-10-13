"""【2026-10-13】網頁貼上表格餵資料：純函式解析與驗證（不連網、不寫庫）。

來源：管理員從 ETF／籌碼 K 線 App 或網頁複製的表格（Tab 或逗號分隔，第一列為表頭）。
驗證規則（不過就退回，不默默吃進去）：
- 必須有代號欄（代號／股票代號／symbol／code）；代號需為台股格式（4～6 碼，ETF 可帶結尾英文字母）。
- 若有日期欄，需為真實日期（YYYY-MM-DD、YYYY/MM/DD、YYYYMMDD），範圍 2000-01-01 ～ 今天+1。
- 每列欄數需與表頭一致；同一份貼上內 (代號, 日期) 重複即退回。
- 數字欄：去千分位、去 %；空白或「-」視為缺值；無法解析的保留為原文字並計入「非數字」統計。
- 筆數上限 2000 列、字數上限 200,000。
- 回傳 content_hash（正規化內容的 SHA-256），用來防止同一份資料重複寫入。
"""
import csv
import hashlib
import io
import json
import re
from datetime import date, datetime

MAX_ROWS = 2000
MAX_CHARS = 200_000
SYMBOL_ALIASES = {"代號", "股票代號", "代碼", "證券代號", "symbol", "code", "stock_id"}
DATE_ALIASES = {"日期", "資料日期", "交易日", "trade_date", "date"}
SYMBOL_RE = re.compile(r"^[0-9]{4,6}[A-Z]?$")
DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d")
MIN_DATE = date(2000, 1, 1)


def _norm_header(h):
    return re.sub(r"\s+", "", (h or "").replace("﻿", "")).lower()


def _to_number(s):
    """回傳 (值, 是否為數字)。空白與「-」視為缺值 (None, True)。"""
    t = (s or "").strip().replace(",", "").replace("%", "")
    if t in ("", "-", "—", "N/A", "n/a"):
        return None, True
    try:
        return float(t), True
    except ValueError:
        return s.strip(), False


def _parse_date(s, today):
    t = (s or "").strip()
    for fmt in DATE_FORMATS:
        try:
            d = datetime.strptime(t, fmt).date()
        except ValueError:
            continue
        if d < MIN_DATE or (d - today).days > 1:
            return None, f"日期超出範圍：{t}"
        return d.isoformat(), None
    return None, f"日期格式錯誤：{t}"


def _sniff_delimiter(first_line):
    if "\t" in first_line:
        return "\t"
    if "," in first_line:
        return ","
    return None


def parse_pasted(text, today=None):
    """解析貼上的表格。回傳 dict：ok、message、header、rows、errors、non_numeric、content_hash。"""
    today = today or date.today()
    out = {"ok": False, "message": "", "header": [], "rows": [], "errors": [],
           "non_numeric": 0, "content_hash": None}
    raw = (text or "").strip("\r\n")
    if not raw.strip():
        out["message"] = "沒有內容"
        return out
    if len(raw) > MAX_CHARS:
        out["message"] = f"內容過長（超過 {MAX_CHARS} 字），請分批貼上"
        return out
    lines = [ln for ln in raw.splitlines() if ln.strip()]
    delim = _sniff_delimiter(lines[0])
    if delim is None:
        out["message"] = "無法辨識欄位分隔，請用 Tab 或逗號分隔，第一列需為表頭"
        return out
    reader = list(csv.reader(io.StringIO("\n".join(lines)), delimiter=delim))
    header = [h.strip().replace("﻿", "") for h in reader[0]]
    norm = [_norm_header(h) for h in header]
    sym_idx = next((i for i, h in enumerate(norm) if h in SYMBOL_ALIASES), None)
    if sym_idx is None:
        out["message"] = "表頭缺少代號欄（需有「代號」或「股票代號」）"
        return out
    date_idx = next((i for i, h in enumerate(norm) if h in DATE_ALIASES), None)
    if len(set(norm)) != len(norm):
        out["message"] = "表頭有重複欄名，請修正後再貼"
        return out
    body = reader[1:]
    if not body:
        out["message"] = "只有表頭，沒有資料列"
        return out
    if len(body) > MAX_ROWS:
        out["message"] = f"資料列超過上限 {MAX_ROWS} 列，請分批貼上"
        return out

    rows, seen, errors, non_numeric = [], set(), [], 0
    for ln_no, cells in enumerate(body, start=2):
        if not any(c.strip() for c in cells):
            continue
        if len(cells) != len(header):
            errors.append(f"第 {ln_no} 列欄數 {len(cells)} 與表頭 {len(header)} 不一致")
            continue
        sym = cells[sym_idx].strip()
        if not SYMBOL_RE.match(sym):
            errors.append(f"第 {ln_no} 列代號格式錯誤：{sym or '（空白）'}")
            continue
        rec = {"symbol": sym}
        if date_idx is not None:
            d, err = _parse_date(cells[date_idx], today)
            if err:
                errors.append(f"第 {ln_no} 列{err}")
                continue
            rec["trade_date"] = d
        key = (sym, rec.get("trade_date"))
        if key in seen:
            errors.append(f"第 {ln_no} 列與前面重複（代號 {sym}{'、' + rec['trade_date'] if rec.get('trade_date') else ''}）")
            continue
        seen.add(key)
        for i, h in enumerate(header):
            if i in (sym_idx, date_idx):
                continue
            val, is_num = _to_number(cells[i])
            if not is_num:
                non_numeric += 1
            rec[h] = val
        rows.append(rec)

    out["header"] = header
    out["errors"] = errors
    out["non_numeric"] = non_numeric
    if not rows:
        out["message"] = "沒有可用的資料列" + (f"（{len(errors)} 列被退回）" if errors else "")
        return out
    if errors:
        out["message"] = f"有 {len(errors)} 列未通過驗證，整份不寫入；請修正後再貼"
        return out
    canon = json.dumps(rows, ensure_ascii=False, sort_keys=True)
    out["rows"] = rows
    out["content_hash"] = hashlib.sha256(canon.encode("utf-8")).hexdigest()
    out["ok"] = True
    out["message"] = f"通過驗證：{len(rows)} 列、{len(header)} 欄"
    return out
