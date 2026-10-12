"""【2026-10-12】證交所 OpenAPI 解析（政府資料開放授權，可程式取用），取代 www.twse.com.tw 直連（使用條款第6條禁止自動化下載）。

  • STOCK_DAY_ALL：上市個股日成交（最新交易日）→ 收盤價、成交金額、成交股數
    實證：2026-10-08 OpenAPI 1,083 檔收盤價總和 198,606.73，與既有 twse_market_snapshot（www MI_INDEX 寫入）完全相同。
  • MI_INDEX（大盤統計資訊，最新交易日）→ 發行量加權股價指數 收盤、漲跌點數（含正負號）、漲跌百分比

兩個端點都只有「最新交易日」；呼叫端要求的日期與回應日期不同時一律回空結果，絕不把別天的資料當成該日。純函式，可離線測試。
"""
import json
import re

from tw_calendar import roc_to_iso

_BAD_ESCAPE = re.compile(r'\\(?![\"\\/bfnrtu])')


def loads_lenient(text):
    """先用標準 JSON 解析；遇到不合法跳脫字元（例如名稱「國巨\\*」）才修正後再解析，避免整批資料因一個字元失敗。"""
    try:
        return json.loads(text)
    except ValueError:
        return json.loads(_BAD_ESCAPE.sub(r"\\\\", text), strict=False)


STOCK_DAY_ALL_URL = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
MI_INDEX_URL = "https://openapi.twse.com.tw/v1/exchangeReport/MI_INDEX"


def _num(s):
    s = re.sub(r"<[^>]+>", "", str(s if s is not None else "")).replace(",", "").strip()
    if s in ("", "--", "-", "X"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def parse_stock_day_all(rows, want_date_iso, is_code_ok):
    """回傳 (out, info)。out＝{代號: {close, trading_value, trading_volume}}；
    收盤價缺或 ≤0 的整筆略過（與原 www 版規則相同）。日期不符回 ({}, {"date_mismatch": [...]})。"""
    rows = [r for r in (rows or []) if isinstance(r, dict)]
    dates = sorted({roc_to_iso(r.get("Date")) for r in rows} - {""})
    if want_date_iso not in dates:
        return {}, {"date_mismatch": dates}
    out = {}
    for r in rows:
        if roc_to_iso(r.get("Date")) != want_date_iso:
            continue
        code = str(r.get("Code") or "").strip()
        if not is_code_ok(code):
            continue
        close = _num(r.get("ClosingPrice"))
        if close is None or close <= 0:
            continue
        out[code] = {"close": close, "trading_value": _num(r.get("TradeValue")) or 0.0,
                     "trading_volume": _num(r.get("TradeVolume")) or 0.0}
    return out, {"date": want_date_iso, "rows": len(out)}


def parse_taiex(rows):
    """回傳 {date, close, change_pt(含正負號), change_pct} 或 None。"""
    for r in rows or []:
        if not isinstance(r, dict) or "發行量加權股價指數" not in str(r.get("指數") or ""):
            continue
        close = _num(r.get("收盤指數"))
        pts = _num(r.get("漲跌點數"))
        pct = _num(r.get("漲跌百分比"))
        date = roc_to_iso(r.get("日期"))
        if close is None or not date:
            return None
        sign = -1.0 if str(r.get("漲跌") or "").strip() == "-" else 1.0
        change_pt = sign * abs(pts) if pts is not None else 0.0
        if pct is None:
            prev = close - change_pt
            pct = round(change_pt / prev * 100, 2) if prev else 0.0
        return {"date": date, "close": close, "change_pt": change_pt, "change_pct": pct}
    return None
