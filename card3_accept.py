"""【2026-11-15】戰卡第 3 項（即時訊號卡 signal_cards）驗收腳本。純函式＋薄 I/O，可離線測試。

驗收標準（寫死；全部成立才算「當日合格」）：
  ① 現價誤差：卡片現價（當日最後一根 5 分 K 收盤）與參考價（twse_market_snapshot 收盤）相差 > 0.5% → 該卡不合格。
     只有最後一根 K 棒 >= 13:25（已收盤）才比對；未收盤的卡不比價，列入「略過」並計數（不當成合格也不當成不合格）。
  ② 一句話結論：卡片結論（候選方向｜第一關｜整體｜整體說明）必須與 intraday_gate_results 該列「獨立換算」的結論逐字一致，
     一致率必須 100%。沒有三關判讀列的卡不納入分母，另計數。
  ③ 連續 5 個有資料的交易日全部合格才算「驗收通過」；不足 5 天回 insufficient，不當成通過。
結果：寫私有表 ui_selftest_reports（summary=card3_accept），公開日誌只印數量；通過回傳碼 0，否則 1。
"""
from __future__ import annotations

import os
import sys
from datetime import datetime

import signal_cards as SC

PRICE_TOL_PCT = 0.5
CONSIST_REQUIRED = 1.0
DAYS_REQUIRED = 5
CLOSE_BAR = "13:25"
SUMMARY = "card3_accept"


def price_error_pct(card_price, ref_price):
    """|現價/參考價 − 1| × 100；任一為空或 <=0 回 None。"""
    try:
        a, b = float(card_price), float(ref_price)
    except (TypeError, ValueError):
        return None
    if a <= 0 or b <= 0:
        return None
    return abs(a / b - 1) * 100


def _bar_hhmm(t):
    s = str(t or "")
    i = s.find(":")
    return s[i - 2:i + 3] if i >= 2 else ""


def is_closed_card(card):
    return _bar_hhmm(card.get("time")) >= CLOSE_BAR


def conclusion_from_card(card):
    """從「卡片」讀出一句話結論（來自卡片 chips 與欄位）。"""
    chips = dict(card.get("chips") or [])
    return "｜".join([chips.get("候選方向", ""), chips.get("第一關", ""), chips.get("整體", ""), card.get("gate_label") or ""])


def conclusion_from_gate(row):
    """直接從 intraday_gate_results 列獨立換算同一句結論（不經過 build_card）。"""
    g1 = row.get("gate1_verdict")
    side = row.get("direction") if row.get("direction") in SC.DIRECTION_LABELS else SC.G1_SIDE.get(g1)
    return "｜".join([SC.DIRECTION_LABELS.get(side, ""), SC.G1_LABELS.get(g1, g1 or "未知"),
                    SC.OVERALL_LABELS.get(row.get("overall_verdict"), ""), row.get("overall_label") or ""])


def evaluate_day(date, items, tol_pct=PRICE_TOL_PCT):
    """items：[{symbol, direction, card, gate, ref_close}]（card 為 build_card 結果；gate 為 intraday_gate_results 列或 None）。
    回傳當日結果 dict（只含數量與不合格明細，不含價格以外的資料）。"""
    price_checked = price_fail = price_skipped = 0
    cons_checked = cons_fail = no_gate = 0
    fails = []
    for it in items:
        card, gate, sym = it["card"], it.get("gate"), it["symbol"]
        if card.get("empty"):
            price_skipped += 1
        elif not is_closed_card(card) or it.get("ref_close") is None:
            price_skipped += 1
        else:
            err = price_error_pct(card.get("last"), it["ref_close"])
            if err is None:
                price_skipped += 1
            else:
                price_checked += 1
                if err > tol_pct:
                    price_fail += 1
                    fails.append({"symbol": sym, "kind": "price", "err_pct": round(err, 3)})
        if gate is None:
            no_gate += 1
        else:
            cons_checked += 1
            if conclusion_from_card(card) != conclusion_from_gate(gate):
                cons_fail += 1
                fails.append({"symbol": sym, "kind": "conclusion", "direction": it.get("direction")})
    rate = None if cons_checked == 0 else (cons_checked - cons_fail) / cons_checked
    has_data = price_checked > 0 and cons_checked > 0
    passed = has_data and price_fail == 0 and rate is not None and rate >= CONSIST_REQUIRED
    return {"date": date, "cards": len(items), "price_checked": price_checked, "price_fail": price_fail, "price_skipped": price_skipped,
            "conclusion_checked": cons_checked, "conclusion_fail": cons_fail, "conclusion_rate": rate, "no_gate": no_gate,
            "has_data": has_data, "passed": passed, "fails": fails[:50]}


def evaluate_run(days, required=DAYS_REQUIRED):
    """days：evaluate_day 結果清單（新到舊）。取最近 required 個「有資料」的日子；全部 passed 才 pass。
    status：pass／fail／insufficient（有資料日數不足）。"""
    have = [d for d in days if d["has_data"]][:required]
    if len(have) < required:
        status = "insufficient"
    else:
        status = "pass" if all(d["passed"] for d in have) else "fail"
    return {"status": status, "days_evaluated": len(have), "days_required": required,
            "days_passed": sum(1 for d in have if d["passed"]), "days": have}


# ------------------------------------------------------------------ 薄 I/O
def load_day(sb, date):
    """讀 date 當日的 5 分 K、三關判讀與快照收盤，組成 evaluate_day 的 items。"""
    gates = sb.table("intraday_gate_results").select("*").eq("trade_date", date).order("created_at", desc=True).execute().data or []
    syms = sorted({g["symbol"] for g in gates})
    bars_by, refs = {}, {}
    for i in range(0, len(syms), 100):
        ch = syms[i:i + 100]
        for r in sb.table("intraday_5min_bars").select("symbol,bar_time,open,high,low,close,volume").eq("trade_date", date).in_("symbol", ch).execute().data or []:
            bars_by.setdefault(r["symbol"], []).append(r)
        for r in sb.table("twse_market_snapshot").select("symbol,close_price").eq("trade_date", date).in_("symbol", ch).execute().data or []:
            try:
                v = float(r.get("close_price"))
                refs[r["symbol"]] = v if v > 0 else None
            except (TypeError, ValueError):
                pass
    items = []
    for sym, by_dir in SC.latest_gate_by_direction(gates).items():
        for direction, g in by_dir.items():
            items.append({"symbol": sym, "direction": direction, "gate": g, "ref_close": refs.get(sym),
                          "card": SC.build_card(sym, "", bars_by.get(sym, []), gate=g)})
    return items


def recent_dates(sb, n=15):
    rows = sb.table("intraday_gate_results").select("trade_date").order("trade_date", desc=True).limit(2000).execute().data or []
    out = []
    for r in rows:
        d = str(r["trade_date"])[:10]
        if d not in out:
            out.append(d)
        if len(out) >= n:
            break
    return out


def run(sb, dates=None, write=True, run_id=None):
    dates = dates or recent_dates(sb)
    days = [evaluate_day(d, load_day(sb, d)) for d in dates]
    res = evaluate_run(days)
    counts = {"status": res["status"], "days_evaluated": res["days_evaluated"], "days_passed": res["days_passed"],
              "price_fail": sum(d["price_fail"] for d in res["days"]), "conclusion_fail": sum(d["conclusion_fail"] for d in res["days"])}
    if write:
        sb.table("ui_selftest_reports").insert({"run_id": run_id if run_id is not None else os.environ.get("GITHUB_RUN_ID", ""),
                                                "summary": SUMMARY, "report": {"_meta": {"kind": SUMMARY, "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                                "price_tol_pct": PRICE_TOL_PCT, "days_required": DAYS_REQUIRED}, "counts": counts, "result": res}}).execute()
    print("[戰卡第3項驗收] " + "、".join(f"{k} {v}" for k, v in counts.items()))   # 公開日誌只印數量
    return res


def main():
    from supabase import create_client
    sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
    return 0 if run(sb)["status"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
