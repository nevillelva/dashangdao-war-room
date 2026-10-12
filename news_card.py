"""【2026-10-13】新聞→個股快速卡（規則式，不靠 AI 編數字）。
新聞抽到的股票，附上最近一個交易日的戰卡因子（factor_snapshot），讓重大消息能對照戰卡。
重要邊界：
- 只引用資料庫已有的數字；缺欄位就寫「資料不足」。
- 不用總分判斷多空：P7 回測（30 交易日、31,122 筆）顯示總分 ≥6 組 5 日超額為 +0.05%，沒有預測力。
- 方向一律標「需人工判讀」，附失效提醒；只供研究參考，不是投資建議。"""

FACTOR_LABELS = [
    ("f_ma_position", "均線位置"),
    ("f_volume_ratio", "量比"),
    ("f_foreign_buy", "外資買超"),
    ("f_institutional_resonance", "法人共振"),
    ("f_institutional_persistence", "法人連續性"),
    ("f_revenue_momentum", "營收動能"),
    ("f_buffer_pct", "緩衝%"),
]


def _fmt(v):
    if v is None:
        return "資料不足"
    try:
        return f"{float(v):+.2f}"
    except (TypeError, ValueError):
        return str(v)


def build_card(symbol, news_title, factor_row, own=False):
    """回傳 dict：symbol、trade_date、score、lines（給訊息用）、missing（缺的欄位數）。
    factor_row 為 None 代表這檔沒有戰卡資料（例如非追蹤股），仍會給出新聞行。"""
    card = {"symbol": symbol, "own": own, "lines": [], "missing": 0, "trade_date": None, "score": None}
    card["lines"].append(f"{symbol}{'（你的持倉/雷達）' if own else ''}｜新聞：{news_title}")
    if not factor_row:
        card["lines"].append("戰卡：這檔沒有最近的戰卡因子資料（不在追蹤池），無法對照，請人工查看。")
        card["lines"].append("判讀：需人工判讀（資料不足）")
        card["missing"] = len(FACTOR_LABELS)
        return card
    card["trade_date"] = factor_row.get("trade_date")
    card["score"] = factor_row.get("total_score_default_weight")
    parts = []
    for key, label in FACTOR_LABELS:
        v = factor_row.get(key)
        if v is None:
            card["missing"] += 1
        parts.append(f"{label} {_fmt(v)}")
    score = card["score"]
    score_txt = "資料不足" if score is None else f"{float(score):.2f}"
    card["lines"].append(f"戰卡（{card['trade_date']}）：總分 {score_txt}｜" + "｜".join(parts))
    card["lines"].append("判讀：需人工判讀（總分高低不作為多空依據）")
    card["lines"].append("失效提醒：新聞若已反映在股價、或量價未跟上，這個消息的效果會減弱。")
    return card


def format_cards(cards):
    """把多張卡組成 Telegram 文字段落（沒有卡就回空字串）。"""
    if not cards:
        return ""
    out = ["\n🧭 新聞相關個股快速卡（規則式，只引用戰卡數字）"]
    for c in cards:
        for i, line in enumerate(c["lines"]):
            out.append(("• " if i == 0 else "   ") + line)
    out.append("僅供自己研究參考，不是投資建議。")
    return "\n".join(out)
