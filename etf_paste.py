"""【P3｜2026-10-12】公告貼上解析：老闆在證交所或投信頁面『手動』複製公告文字，貼進來，系統解析並與事件表核對。
不做任何自動擷取（死規則 5：證交所使用條款第 6 條禁止自動化下載）。純函式，可離線測試。

流程：check_paste → 通過者產生 apply_sourced 可用的 row（單一獨立來源即可，老闆 2026-10-10 指示；來源網址必填且須 https）。
核對項目（全部成立才通過）：
  1. 代號、來源網址（https）、文字都有
  2. 解析得到可信的『預估收益分配組成占比』（股利所得必有、加總 98~102%），且有除息交易日
  3. 除息日必須是事件表中該檔已知的配息日（不接受事件表以外的日期）
  4. 公告的配發金額與事件表同一除息日的每單位現金股利差距 ≤ 1%（核對金額）
"""
import etf_core as E
import etf_twse_scan as S

AMOUNT_TOL = 0.01   # 相對誤差上限 1%（公告與事件表都可能四捨五入）


def _event_index(events):
    """{(symbol, ex_date 'YYYY-MM-DD'): cash_per_unit}，只取現金股利 > 0 者。"""
    idx = {}
    for e in events or []:
        sym = str(e.get("symbol") or "").strip().upper()
        exd = str(e.get("ex_date") or "")[:10]
        cash = E._f(e.get("cash_per_unit", e.get("cash")), 0.0)
        if sym and exd and cash > 0:
            idx[(sym, exd)] = cash
    return idx


def check_paste(text, fund, url, events):
    """回傳 {ok, reason, row, parsed, cash}。ok=False 時 reason 說明退回原因（不默默吃進去）。"""
    fund = str(fund or "").strip().upper()
    url = str(url or "").strip()
    if not fund:
        return {"ok": False, "reason": "未填 ETF 代號"}
    if not url.lower().startswith("https://"):
        return {"ok": False, "reason": "來源網址必須是 https://（每筆都要附來源）"}
    if not str(text or "").strip():
        return {"ok": False, "reason": "沒有貼上公告文字"}
    parsed, why = S.parse_announcement(text, fund, url)
    if parsed is None:
        return {"ok": False, "reason": f"解析失敗：{why}"}
    ex = parsed["ex_date"]
    idx = _event_index(events)
    if (fund, ex) not in idx:
        return {"ok": False, "reason": f"除息日 {ex} 不在 {fund} 的配息事件表內（請先確認事件表，或該檔尚未公告此次配息）", "parsed": parsed}
    cash = idx[(fund, ex)]
    amt = parsed.get("amount")
    if amt is None:
        return {"ok": False, "reason": "公告沒有『配發金額』，無法與事件表核對", "parsed": parsed}
    if abs(amt - cash) > AMOUNT_TOL * cash:
        return {"ok": False, "reason": f"配發金額 {amt} 與事件表 {cash} 差距超過 {AMOUNT_TOL:.0%}，退回", "parsed": parsed, "cash": cash}
    comp = parsed["composition"]
    src = {"url": url, "ratio_54c": comp["div"]}
    if comp.get("int") is not None:
        src["ratio_5a"] = comp["int"]
    row = {"symbol": fund, "ex_date": ex, "sources": [src]}
    return {"ok": True, "reason": "", "row": row, "parsed": parsed, "cash": cash}
