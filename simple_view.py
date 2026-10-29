"""【2026-10-29 老闆：即時訊號卡判讀太複雜、三關細節不顯示；主戰清單的盤前理由／盤中判斷要精簡、能迅速看懂】
純顯示用的白話濃縮（不改任何判斷邏輯、不連網）。濃縮不了的一律截短，不編造內容。"""
import re

_BOILER = ("才當沖空", "才空", "收盤前回補", "離漲停太近", "才買")


def _cut(t, n):
    t = str(t or "").strip()
    return t if len(t) <= n else t[:n - 1] + "…"


def short_why(why, limit=3):
    """盤前理由：去掉價位規則的長篇說明（價位與動作欄已經寫了），只留「為什麼入選」的幾個重點。"""
    segs = [s.strip() for s in str(why or "").split("｜") if s.strip()]
    keep = [s for s in segs if not any(b in s for b in _BOILER)] or segs[:1]
    return "　".join(_cut(s, 26) for s in keep[:limit])


def short_reason(reason):
    """盤中判斷：一句白話結論。"""
    r = str(reason or "").strip()
    if not r:
        return ""
    if r.startswith("還沒有開盤價"):
        return "等開盤價（09:00 後出來）"
    m = re.search(r"離漲停只剩\s*([\d.]+)%", r)
    if r.startswith("⚠️") and m:
        return f"⚠️ 離漲停只剩 {m.group(1)}%：沒空的不要進，持有者處理"
    m = re.search(r"高\s*([\d.]+)%（≥", r)
    if m:
        return f"開盤高 {m.group(1)}%，太接近漲停：不做"
    m = re.search(r"高\s*([\d.]+)%：開盤當沖空", r)
    if m:
        return f"開盤高 {m.group(1)}%：可空，收盤前回補"
    m = re.search(r"只比昨收高\s*([+\-\d.]+)%（未達", r)
    if m:
        return f"開盤只高 {m.group(1)}%，不夠：不做"
    if r.startswith("已持有→看開盤"):
        return "持有者看開盤賣；沒持有不追買"
    m = re.match(r"已持有（開盤[^）]*）→\s*(.+?)；沒持有不追買", r)
    if m:
        out = f"持有者：{_cut(m.group(1), 24)}；沒持有不追買"
        if "可另做開盤當沖空" in r:
            out += "｜同時可開盤當沖空"
        return out
    if r.startswith("空方第三關合格"):
        return "空方確認過關"
    if r.startswith("空方第三關不合格"):
        return "空方沒過關（反彈有力）：不空"
    if r.startswith("空方第三關資料不足"):
        return "空方資料不足：人工確認"
    if "13:18" in r or "13:20" in r or "12:30" in r:
        m = re.search(r"參考：(.+)$", r)
        return "13:18 確認才買" + (f"（10:00 狀態：{_cut(m.group(1), 16)}）" if m else "")
    return _cut(r, 40)


def signal_verdict(card):
    """即時訊號卡的一句話結論：(圖示, 結論, 補充)。只看整體判讀與方向，三關細節不顯示。"""
    side = {"long": "多方", "short": "空方"}.get(card.get("side"), "")
    ov = card.get("overall")
    if ov == "pass":
        v = ("✅", f"{side}通過" if side else "通過", "")
    elif ov == "fail":
        v = ("❌", "沒通過：今天不做" + (f"（{side}）" if side else ""), "")
    elif ov == "pending":
        v = ("⏳", "等資料", "")
    elif ov == "stale":
        v = ("⚪", "已過判斷時間", "")
    else:
        v = ("⚪", "尚無判讀", "")
    warn = ""
    cp = card.get("change_pct")
    if cp is not None and side == "空方" and cp >= 0.5:
        warn = "⚠️ 價格比開盤高，與空方相反"
    elif cp is not None and side == "多方" and cp <= -0.5:
        warn = "⚠️ 價格比開盤低，與多方相反"
    return v[0], v[1], warn
