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
STANCES_SHORT = ("可空", "小量", "暫不空")
UNVERIFIED = "AI 推演未經回測驗證，僅供參考，系統判定不變"

SYSTEM_PROMPT = (
    "你是台股尾盤戰情分析員。使用者給你一檔已通過系統規則篩選的候選股證據包，請做戰略推演："
    "說明為什麼現在能買、為什麼可能不能買、關鍵價位與執行計畫。"
    "只根據證據包判斷（含證據包列出的當天新聞與重大訊息），不可引用證據包以外的新聞、財報或價格；價位只能使用證據包出現過的數字或由它們直接計算。"
    "必須把新聞／重大訊息與戰卡資料一起考慮：有明確利空新聞或重大訊息就要在風險中說明；證據包寫「查無相關新聞」時不得自行編造新聞。"
    "只回傳一個 JSON 物件，欄位：thesis（一句話論點）、can_buy（目前能買的理由，60 字內）、"
    "cannot_buy（不能買或最大風險，60 字內）、news（新聞面判斷一句話：利多／利空／無關，並點出哪則；沒有新聞就寫「查無相關新聞」）、"
    "key_level（關鍵價位一句話）、plan（進場、停損、持有的執行計畫一句話）、"
    "stance（只能是 可買／小量／暫不買 其中之一）。不要輸出 JSON 以外的文字。"
)

SYSTEM_PROMPT_SHORT = (
    "你是台股開盤做空戰情分析員。使用者給你一檔已通過系統規則篩選的空方候選股證據包（開盤價已確認比昨收高到可放空的區間），"
    "請做戰略推演：說明為什麼現在能空、為什麼可能不能空（例如開高後續漲、鎖漲停回補不了）、關鍵價位與執行計畫。"
    "只根據證據包判斷（含證據包列出的當天新聞與重大訊息），不可引用證據包以外的新聞、財報或價格；價位只能使用證據包出現過的數字或由它們直接計算。"
    "必須把新聞／重大訊息與戰卡資料一起考慮：有明確利多新聞（例如法說、接單、營收大增）會增加空單被軋風險，要在風險中說明；證據包寫「查無相關新聞」時不得自行編造新聞。"
    "只回傳一個 JSON 物件，欄位：thesis（一句話論點）、can_buy（目前能空的理由，60 字內）、"
    "cannot_buy（不能空或最大風險，60 字內）、news（新聞面判斷一句話：利多／利空／無關，並點出哪則；沒有新聞就寫「查無相關新聞」）、"
    "key_level（關鍵價位一句話）、plan（進場、停損、回補的執行計畫一句話）、"
    "stance（只能是 可空／小量／暫不空 其中之一）。不要輸出 JSON 以外的文字。"
)


def stances_for(side):
    return STANCES_SHORT if side == "short" else STANCES


def funnel_counts(cards):
    """初選 → 篩選後的漏斗統計。cards：tail_card.decide 的輸出 list。"""
    total = len(cards)
    kept = [c for c in cards if c.get("decision") in ("buy", "small", "short")]
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


def news_lines(news):
    """news：{"news":[{src,time,title,summary}], "mops":[{date,subject,category,excerpt}]} 或 None → 證據包文字行。"""
    news = news or {}
    items, mops = news.get("news") or [], news.get("mops") or []
    out = []
    if not items and not mops:
        return ["相關新聞與重大訊息：查無（不代表沒有，只是資料庫近 36 小時沒收錄到這檔）"]
    for n in items[:5]:
        sm = f"｜{str(n.get('summary'))[:80]}" if n.get("summary") else ""
        out.append(f"新聞［{n.get('src', '')} {n.get('time', '')}］{str(n.get('title'))[:80]}{sm}")
    for m in mops[:4]:
        ex = f"｜{str(m.get('excerpt'))[:80]}" if m.get("excerpt") else ""
        out.append(f"重大訊息［{m.get('date', '')} {m.get('category', '')}］{str(m.get('subject'))[:80]}{ex}")
    return out


def build_pack(card, market_line="", funnel="", news=None):
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
    lines.extend(news_lines(news))
    return "\n".join(lines)


def parse(text, side="long"):
    """嚴格解析；欄位不齊、型別錯、stance 不在清單 → None（整筆退回）。side：long／short（stance 字彙不同）。"""
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
    if obj.get("stance") not in stances_for(side):
        return None
    out["stance"] = obj["stance"]
    if isinstance(obj.get("news"), str) and obj["news"].strip():
        out["news"] = obj["news"].strip()
    return out


def run(cards, market_line, llm_call, funnel=None, side="long", news=None, workers=4):
    """對買／小量卡（side=short 時為做空卡 decision=short／small）做推演。llm_call(system, user) → (ok, text) 或 (ok, text, via)。
    news：{symbol: {"news": [...], "mops": [...]}}，併入證據包。各檔平行呼叫（最多 workers 個），讓 13:18 前來得及。
    回傳 {symbol: 結果或 {"error": ...}}；任何失敗都不中斷。"""
    from concurrent.futures import ThreadPoolExecutor
    fun = funnel if funnel is not None else funnel_text(cards)
    want = ("short", "small") if side == "short" else ("buy", "small")
    prompt = SYSTEM_PROMPT_SHORT if side == "short" else SYSTEM_PROMPT
    todo = [c for c in cards if c.get("decision") in want]

    def one(c):
        sym = str(c.get("symbol"))
        try:
            r = llm_call(prompt, build_pack(c, market_line, fun, (news or {}).get(sym)))
        except Exception as e:  # noqa: BLE001
            return sym, {"error": type(e).__name__}
        ok, text = r[0], r[1]
        via = r[2] if len(r) > 2 else ""
        if not ok:
            return sym, {"error": str(text)[:80]}
        res = parse(text, side)
        if res is None:
            return sym, {"error": "輸出格式不合（退回）"}
        if via:
            res["via"] = via
        return sym, res
    if not todo:
        return {}
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(todo)))) as ex:
        return dict(ex.map(one, todo))


def disagree(card, ai):
    """AI 立場與系統判定不同：系統買（空）但 AI 暫不買（空）；或系統小量但 AI 說可買（較不嚴重，不標）。"""
    return bool(ai and "stance" in ai and card.get("decision") in ("buy", "small", "short") and ai["stance"] in ("暫不買", "暫不空"))


def lines_for(card, ai):
    """顯示用文字行；ai 無效（含 error）就回空 list（不顯示壞資料）。"""
    if not ai or "stance" not in ai:
        return []
    act = "能空" if ai["stance"] in STANCES_SHORT and ai["stance"] != "小量" else "能買"
    if card.get("decision") == "short":
        act = "能空"
    mark = {"可買": "🟢", "可空": "🟢", "小量": "🟡", "暫不買": "🔴", "暫不空": "🔴"}.get(ai["stance"], "⚪")
    out = [f"   🤖 AI 戰略推演 {mark}{ai['stance']}：{ai['thesis']}",
           f"      {act}：{ai['can_buy']}",
           f"      不能{act[-1]}／風險：{ai['cannot_buy']}",
           *([f"      新聞面：{ai['news']}"] if ai.get("news") else []),
           f"      關鍵價位：{ai['key_level']}",
           f"      計畫：{ai['plan']}"]
    if disagree(card, ai):
        out.append("      ⚠️ AI 與系統判定不同（系統仍判進場，請自行權衡）")
    out.append(f"      （{UNVERIFIED}" + (f"；模型：{ai['via']}" if ai.get("via") else "") + "）")
    return out
