"""【2026-10-12】證交所休市日：改用 OpenAPI（政府資料開放授權，可程式取用），取代 www.twse.com.tw 直連（使用條款第6條禁止自動化下載）。

端點：GET https://openapi.twse.com.tw/v1/holidaySchedule/holidaySchedule（只回當年度）
欄位：Name、Date（民國 yyyMMdd，例 1150101）、Weekday、Description。
判斷規則（與原本相同）：說明含「放假」或「補假」，或 名稱／說明含「無交易」→ 休市；
「開始交易日」「最後交易日」是交易日，不算休市。純函式，可離線測試。
"""
OPENAPI_HOLIDAY_URL = "https://openapi.twse.com.tw/v1/holidaySchedule/holidaySchedule"


def roc_to_iso(s):
    """'1150101' → '2026-01-01'；格式不對回 ''。"""
    s = str(s or "").strip()
    if not s.isdigit() or len(s) not in (6, 7):
        return ""
    y, m, d = int(s[:-4]) + 1911, int(s[-4:-2]), int(s[-2:])
    if not (1 <= m <= 12 and 1 <= d <= 31):
        return ""
    return f"{y:04d}-{m:02d}-{d:02d}"


def is_closed_row(name, note):
    name, note = str(name or ""), str(note or "")
    return "放假" in note or "補假" in note or "無交易" in name or "無交易" in note


def parse_closed(rows, year):
    """rows：OpenAPI 回傳的 list[dict]。回傳 (該年度休市日集合, 該年度列數)。列數 0 代表 API 沒有這一年的資料。"""
    closed, n = set(), 0
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        iso = roc_to_iso(r.get("Date"))
        if not iso or int(iso[:4]) != int(year):
            continue
        n += 1
        if is_closed_row(r.get("Name"), r.get("Description")):
            closed.add(iso)
    return closed, n
