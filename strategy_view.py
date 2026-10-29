"""【2026-10-29 老闆：初選 10 檔經第二階段篩選剩 3～4 檔後，直接套 AI 戰略推演顯示在上面，說明為什麼能買／不能買】
AI 戰略推演（純函式，可離線測試；LLM 由呼叫端注入，本模組不讀取、不印出任何金鑰）。

定位（誠實標示）：
  • 只對「篩選後剩下的買／小量」做推演，結果只當參考顯示，**不改系統判定、不下單**。
  • 證據包只放決策當下已知的欄位（價格、規則依據、停損、大盤），要求 AI 不得引用證據包以外的事實。
  • AI 輸出必須是合法 JSON 且欄位齊全，否則整筆退回、不顯示（死規則 1：驗證不過不默默吃進去）。
  • 反方審查（counter_review）的歷史回放驗證尚未有有效結論，所以這裡的 AI 判斷標示「未經回測驗證」。
"""
import json

STANCES = ("可買", "小量", "暫不買")
UNVERIFIED = "AI 推演未經回測驗證，僅供參考，系統判定不變"

SYSTEM_PROMPT = (
    "你是台股尾盤戰情分析員。使用者給你一檔已通過系統規則篩選的候選股證據包，請做戰略推演："
    "說明為什麼現在能買、為什麼可能不能買、關鍵價位與執行計畫。"
    "只根據證據包判斷，不可引用證據包以外的新聞、財報或價格；價位只能使用證據包出現過的數字或由它們直接計算。"
    "只回傳一個 JSON 物件，欄位：thesis（一句話論點）、can_buy（目前能買的理由，60 字內）、"
    "cannot_buy（不能買或最大風險，60 字內）、key_level（關鍵價位一句話）、plan（進場、停損、持有的執行計畫一句話）、"
    "stance（只能是 可買／小量／暫不買 其中之一）。不要輸出 JSON 以外的文字。"
)


def funnel_counts(cards):
    """初選 → 篩選後的漏斗統計。cards：tail_card.decide 的輸出 list。"""
    total = len(cards)
    kept = [c for c in cards if c.get("decision") in ("buy", "small")]
    observe = [c for c in cards if c.get("decision") == "skip" and any("僅觀察" in w for w in (c.get("why") or []))]
    out = {"total": total, "kept": len(kept), "observe": len(observe), "dropped": total - len(kept) - len(observe)}
    return out


def funnel_text(cards):
    f = funnel_counts(cards)
    if not f["total"]:
        return ""
    parts = [f"初選 {f['total']} 檔 → 篩選後可買／小量 {f['kept']} 檔"]
    if f["dropped"]:
        parts.append(f"規則或限制擋下 {f['dropped']} 檔")
    if f["observe"]:
        parts.append(f"僅觀察 {f['observe']} 檔")
    return "｜".join(parts)


def build_pack(card, market_line="", funnel=""):
    why = "；".join(card.get("why") or []) or "無"
    lines = [
        f"股票：{card.get('symbol')}（來源：{card.get('source')}，軌道：{card.get('track') or '無'}）",
        f"現價：{card.get('px')}；昨收：{card.get('ref')}",
        f"命中熱門區域：{', '.join(card.get('zones') or []) or '無'}",
        f"規則依據：{card.get('basis') or '無'}",
        f"系統判定：{card.get('decision')}；備註：{why}",
        f"系統給的進場：{card.get('entry') or '無'}",
        f"停損：{card.get('stop_px') if card.get('stop_px') else '不設'}"
        + (f"（−{card.get('stop_pct'):g}%）" if card.get("stop_pct") else ""),
        f"停利：{card.get('tp_px') if card.get('tp_px') else '不設'}",
        f"持有：{card.get('hold') or '無'} 個交易日；隔天動作：{card.get('next_day') or '無'}",
        f"大盤：{market_line or '無'}",
        f"今日漏斗：{funnel or '無'}",
    ]
    return "\n".join(lines)


def parse(text):
    """嚴格解析；欄位不齊、型別錯、stance 不在清單 → None（整筆退回）。"""
    if not isinstance(text, str):
        return None
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`")
        if t.lower().startswith("json"):
            t = t[4:]
    try:
        obj = json.loads(t.strip())
    except ValueError:
        return None
    if not isinstance(obj, dict):
        return None
    out = {}
    for k in ("thesis", "can_buy", "cannot_buy", "key_level", "plan"):
        v = obj.get(k)
        if not isinstance(v, str) or not v.strip():
            return None
        out[k] = v.strip()
    if obj.get("stance") not in STANCES:
        return None
    out["stance"] = obj["stance"]
    return out


def run(cards, market_line, llm_call, funnel=None):
    """對買／小量卡做推演。llm_call(system, user) → (ok, text)。回傳 {symbol: 結果或 {"error": ...}}；任何失敗都不中斷。"""
    fun = funnel if funnel is not None else funnel_text(cards)
    out = {}
    for c in cards:
        if c.get("decision") not in ("buy", "small"):
            continue
        sym = str(c.get("symbol"))
        try:
            ok, text = llm_call(SYSTEM_PROMPT, build_pack(c, market_line, fun))
        except Exception as e:  # noqa: BLE001
            out[sym] = {"error": type(e).__name__}
            continue
        if not ok:
            out[sym] = {"error": str(text)[:80]}
            continue
        res = parse(text)
        out[sym] = res if res is not None else {"error": "輸出格式不合（退回）"}
    return out


def disagree(card, ai):
    """AI 立場與系統判定不同：系統買但 AI 暫不買；或系統小量但 AI 說可買（較不嚴重，不標）。"""
    return bool(ai and "stance" in ai and card.get("decision") in ("buy", "small") and ai["stance"] == "暫不買")


def lines_for(card, ai):
    """顯示用文字行；ai 無效（含 error）就回空 list（不顯示壞資料）。"""
    if not ai or "stance" not in ai:
        return []
    mark = {"可買": "🟢", "小量": "🟡", "暫不買": "🔴"}[ai["stance"]]
    out = [f"   🤖 AI 戰略推演 {mark}{ai['stance']}：{ai['thesis']}",
           f"      能買：{ai['can_buy']}",
           f"      不能買／風險：{ai['cannot_buy']}",
           f"      關鍵價位：{ai['key_level']}",
           f"      計畫：{ai['plan']}"]
    if disagree(card, ai):
        out.append("      ⚠️ AI 與系統判定不同（系統仍判買，請自行權衡）")
    out.append(f"      （{UNVERIFIED}）")
    return out
