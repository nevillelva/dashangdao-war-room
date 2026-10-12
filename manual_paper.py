"""【2026-10-13】手動模擬倉（自己下單的紙上交易）— 純函式，不連網、不下單。
與系統模擬倉（system_portfolio）分開帳本，用來比較「人工判斷」與「系統訊號」。
成本假設與專案統一值一致：來回 0.585%。張數以 1 張 = 1000 股計。"""

ROUND_TRIP_COST_PCT = 0.585
SIDES = ("買", "賣")


def validate_trade(side, price, lots, stop=None, target=None):
    """回傳 (ok, 錯誤訊息)。只做格式與邏輯檢查，不碰資料庫。"""
    if side not in SIDES:
        return False, "方向只能是買或賣"
    try:
        price = float(price)
        lots = int(lots)
    except (TypeError, ValueError):
        return False, "價格與張數必須是數字"
    if price <= 0:
        return False, "價格必須大於 0"
    if lots <= 0:
        return False, "張數必須是正整數"
    if stop is not None and stop != "":
        stop = float(stop)
        if stop <= 0:
            return False, "停損價必須大於 0"
        if side == "買" and stop >= price:
            return False, "買進的停損價應低於買進價"
        if side == "賣" and stop <= price:
            return False, "放空的停損價應高於放空價"
    if target is not None and target != "":
        target = float(target)
        if target <= 0:
            return False, "目標價必須大於 0"
        if side == "買" and target <= price:
            return False, "買進的目標價應高於買進價"
        if side == "賣" and target >= price:
            return False, "放空的目標價應低於放空價"
    return True, ""


def pnl(side, entry, last, lots, cost_pct=ROUND_TRIP_COST_PCT):
    """依方向計算損益（已扣來回成本）。last 可以是最新價或出場價。
    回傳 dict：pct（報酬率 %，已扣成本）、amount（元，已扣成本）。
    成本以進場金額的百分比計，放空與買進同樣扣來回成本。"""
    entry, last, lots = float(entry), float(last), int(lots)
    shares = lots * 1000
    if side == "買":
        gross_pct = (last / entry - 1) * 100
    else:
        gross_pct = (entry / last - 1) * 100
    net_pct = gross_pct - cost_pct
    notional = entry * shares
    return {"pct": round(net_pct, 3), "amount": round(notional * net_pct / 100, 0)}


def stop_hit(side, last, stop):
    """最新價是否已觸及停損（只是提醒，不自動平倉）。"""
    if stop in (None, ""):
        return False
    stop = float(stop)
    return last <= stop if side == "買" else last >= stop


def target_hit(side, last, target):
    if target in (None, ""):
        return False
    target = float(target)
    return last >= target if side == "買" else last <= target
