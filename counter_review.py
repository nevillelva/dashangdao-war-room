"""【2026-10-28】反方審查（影子版）：只對尾盤戰卡已判定「買／小量」的候選，請 LLM 依證據包列出最可能失敗的理由。

原則（見《TradingAgents評估_能否加入戰情室_20261028》）：
  • 只存紀錄（system_config），不改任何買賣判定、不推播、不下單。
  • LLM 輸出必須是合法 JSON 且欄位齊全，否則整筆退回（不默默吃進去）。
  • 證據包只用「決策當下已知」的資料（不含結果欄位），避免事後資訊混入。
  • 金鑰由呼叫端注入，本模組不讀取、不印出金鑰。
"""
import json

VERDICTS = ("避開", "維持", "觀察")
SYSTEM_PROMPT = (
    "你是台股交易的反方審查員。你的工作是找出這筆買進最可能失敗的理由，而不是替它辯護。"
    "只根據使用者提供的證據包判斷，不可引用證據包以外的新聞或事實。"
    "只回傳一個 JSON 物件，欄位：bull（多方理由，一段，50 字內）、bear（空方理由，一段，50 字內）、"
    "main_risk（最大風險，一句）、confidence（0 到 1 的數字，代表你認為此單會失敗的把握）、"
    "verdict（只能是 避開／維持／觀察 其中之一）。不要輸出 JSON 以外的文字。"
)


def build_pack(card, market_line=""):
    """把一張尾盤戰卡轉成證據包文字（只放決策當下已知的欄位）。"""
    lines = [
        f"股票：{card.get('symbol')}（來源：{card.get('source')}，軌道：{card.get('track') or '無'}）",
        f"現價：{card.get('px')}；昨收：{card.get('ref')}",
        f"命中熱門區域：{', '.join(card.get('zones') or []) or '無'}",
        f"規則判定：{card.get('basis') or '無'}",
        f"系統判定：{card.get('decision')}；理由：{'；'.join(card.get('why') or []) or '無'}",
        f"大盤：{market_line or '無'}",
    ]
    return "\n".join(lines)


def parse_verdict(text):
    """解析 LLM 輸出；欄位不齊、型別錯、verdict 不在允許清單、confidence 超出 0～1 → 回傳 None（整筆退回）。"""
    try:
        obj = json.loads(text.strip().strip("`").replace("json\n", "", 1))
    except (ValueError, AttributeError):
        return None
    if not isinstance(obj, dict):
        return None
    for k in ("bull", "bear", "main_risk"):
        if not isinstance(obj.get(k), str) or not obj[k].strip():
            return None
    conf = obj.get("confidence")
    if not isinstance(conf, (int, float)) or isinstance(conf, bool) or not (0.0 <= float(conf) <= 1.0):
        return None
    if obj.get("verdict") not in VERDICTS:
        return None
    return {"bull": obj["bull"].strip(), "bear": obj["bear"].strip(), "main_risk": obj["main_risk"].strip(),
            "confidence": round(float(conf), 3), "verdict": obj["verdict"]}


def review_cards(cards, market_line, llm_call):
    """對買／小量的卡跑反方審查。llm_call(system, user) → (ok, text)。回傳 {symbol: 結果或 {"error": ...}}。"""
    out = {}
    for c in cards:
        if c.get("decision") not in ("buy", "small"):
            continue
        sym = str(c.get("symbol"))
        ok, text = llm_call(SYSTEM_PROMPT, build_pack(c, market_line))
        if not ok:
            out[sym] = {"error": str(text)[:120]}
            continue
        res = parse_verdict(text)
        out[sym] = res if res is not None else {"error": "輸出格式不合（退回）"}
    return out


# ───────── 回放驗證（第 2 階段）：用歷史事件的「旗標＋日期」餵反方審查，對照實際結果 ─────────
REPLAY_START = "2025-02-01"   # Gemini 2.5 Flash 知識截止約 2025 年初；之後的事件才不算「模型已經看過」


def replay_pack(labels, date_str, zone_label):
    """只放條件與日期，不放代號、價格或任何結果欄位。labels：該事件成立的旗標中文名。"""
    return "\n".join([
        f"事件日期：{date_str}",
        f"命中區域：{zone_label}",
        f"成立的條件（共 {len(labels)} 個）：{'、'.join(labels) or '無'}",
        "判斷對象：一檔熱門股（前 100 檔）在當日開低後買進，預計持有 20 個交易日。",
    ])


def summarize_replay(records, placebo_rounds=300, seed=7):
    """records：[{"verdict": 避開／維持／觀察, "r": 報酬（小數，已扣成本）}]。
    回傳各桶 n、勝率、平均；並以「打亂 verdict」的安慰劑估計：避開桶的平均比其餘低的機率（越接近 0 越像真的）。"""
    import random
    import statistics
    rs = [x for x in records if isinstance(x.get("r"), (int, float)) and x.get("verdict") in VERDICTS]
    out = {"n": len(rs), "buckets": {}}
    for v in VERDICTS:
        xs = [x["r"] for x in rs if x["verdict"] == v]
        out["buckets"][v] = {"n": len(xs),
                             "win_pct": round(100 * sum(1 for r in xs if r > 0) / len(xs), 1) if xs else None,
                             "avg_pct": round(100 * statistics.fmean(xs), 2) if xs else None}
    avoid = [x["r"] for x in rs if x["verdict"] == "避開"]
    rest = [x["r"] for x in rs if x["verdict"] != "避開"]
    if len(avoid) >= 20 and len(rest) >= 20:
        obs = statistics.fmean(rest) - statistics.fmean(avoid)   # 正值＝避開桶確實比較差
        rng = random.Random(seed)
        allr = avoid + rest
        k = len(avoid)
        hits = 0
        for _ in range(placebo_rounds):
            rng.shuffle(allr)
            d = statistics.fmean(allr[k:]) - statistics.fmean(allr[:k])
            if d >= obs:
                hits += 1
        out["placebo_p"] = round((hits + 1) / (placebo_rounds + 1), 3)
        out["avoid_minus_rest_pct"] = round(100 * (statistics.fmean(avoid) - statistics.fmean(rest)), 2)
    else:
        out["placebo_p"] = None
        out["note"] = "避開或其餘樣本不足 20 筆，不下結論"
    return out
