"""【2026-10-29 老闆：全部照建議落實】「全部查6 推薦」追蹤（純函式，可離線測試）。
為什麼：只盯「漲上來的那幾檔」會有挑選偏誤（上週 10/14～10/22 首次出現在隔夜掃描的 1,050 檔，到 10/27 平均 −2.2%、勝率 26%；
其中有查6 的 450 檔平均 −4.5%、勝率 19%；漲上來的只是少數贏家）。所以每個隔夜掃描日出現的查6，不論之後漲跌，全部追蹤。
口徑（與 5 年回測 cmd_playbook_bt 同口徑）：掃描日 S 收盤後出現 → 下一個交易日 D 開盤買 → 抱 20 日收盤賣；成本 0.585%；
三種停損各算一次：不設、−10%、−15%（觸價，跳空以開盤價出；同日只看最低價是否觸及）。資料不夠 20 日＝進行中（附目前未實現報酬）。
基準：同期「沒有查6 的掃描股」與「全市場掃描股」，同一套口徑，才看得出查6 是否真的比較好。
"""
from __future__ import annotations

from statistics import mean, median

COST = 0.585
HOLD = 20
STOPS = (None, 10.0, 15.0)   # None＝不設停損


def _slice_after(df, d):
    """掃描日 d 的下一個交易日（含）起的日K。df：index 日期、含 Open／Low／Close。"""
    if df is None or len(df) == 0:
        return None
    s = df[df.index.strftime("%Y-%m-%d") > d]
    return s if len(s) else None


def settle(s, stop=None, hold=HOLD):
    """s：進場日起的日K（第一列＝進場日）。回傳 {status: done/open, r 或 mtm, stopped, days}。"""
    if s is None or len(s) == 0:
        return {"status": "no_data"}
    o = float(s["Open"].iloc[0])
    if not o > 0:
        return {"status": "no_data"}
    lv = o * (1 - stop / 100) if stop else None
    has_low = "Low" in s.columns
    for k in range(min(hold, len(s))):
        if lv is not None:
            op = float(s["Open"].iloc[k])
            lo = float(s["Low"].iloc[k]) if has_low else None
            if op > 0 and op <= lv:
                return {"status": "done", "r": round((op / o - 1) * 100 - COST, 3), "stopped": True, "days": k + 1}
            if lo is not None and lo > 0 and lo <= lv:
                return {"status": "done", "r": round(-stop - COST, 3), "stopped": True, "days": k + 1}
    if len(s) >= hold:
        return {"status": "done", "r": round((float(s["Close"].iloc[hold - 1]) / o - 1) * 100 - COST, 3), "stopped": False, "days": hold}
    return {"status": "open", "mtm": round((float(s["Close"].iloc[-1]) / o - 1) * 100, 3), "days": len(s)}


def settle_all(df, d):
    """對一檔、掃描日 d，三種停損各結算一次。回傳 {stop_key: result}；沒有進場日資料回 None。"""
    s = _slice_after(df, d)
    if s is None:
        return None
    return {("none" if st is None else f"sl{int(st)}"): settle(s, st) for st in STOPS}


def aggregate(rows):
    """rows：[{cohort, sym, d, res: {stop_key: result}}]。依 cohort × 停損彙總（完成與進行中分開，避免混在一起誤導）。"""
    out = {}
    cohorts = sorted({r["cohort"] for r in rows})
    for c in cohorts:
        sub = [r for r in rows if r["cohort"] == c and r.get("res")]
        e = {"n": len(sub)}
        for key in ("none", "sl10", "sl15"):
            rs = [r["res"][key] for r in sub if key in r["res"]]
            dn = [x["r"] for x in rs if x.get("status") == "done"]
            op = [x["mtm"] for x in rs if x.get("status") == "open"]
            e[key] = {"done": len(dn),
                      "win": round(100 * sum(1 for x in dn if x > 0) / len(dn), 1) if dn else None,
                      "avg": round(mean(dn), 3) if dn else None,
                      "stopped": sum(1 for x in rs if x.get("stopped")),
                      "open": len(op),
                      "open_avg_mtm": round(mean(op), 3) if op else None,
                      "open_win": round(100 * sum(1 for x in op if x > 0) / len(op), 1) if op else None,
                      "open_median": round(median(op), 3) if op else None,
                      "small_sample": len(dn) < 30}
        out[c] = e
    return out
