"""【2026-11-15 盤中重訊缺口】每日量測（獨立模組，只讀 mops_events／tail_card，結果寫私有表）。

比對「前一交易日」：
  ・盤中（09:00～13:30 發言）重訊共幾則（mops_events 只存重要度≥1 者，數量是下限）；
  ・其中涉及前一交易日候選／持倉（尾盤預備＋定版戰卡的卡片與持有中）的幾則；
  ・戰卡當時的重訊資料時效（mops_asof）之後才發言、因此戰卡看不到的候選相關重訊幾則（時效不明則全部視為看不到）。
明細（代號、時間、類別）只寫私有表 ui_selftest_reports（summary=mops_gap_daily:日期）；公開日誌只印數量。純函式＋注入 sb，可離線測試。
"""
import json

import mops_gap as MG

INTRADAY_START, INTRADAY_END = "09:00:00", "13:30:59"


def _cfg(sb, key):
    try:
        rows = sb.table("system_config").select("config_value").eq("config_key", key).limit(1).execute().data or []
        if not rows:
            return None
        v = rows[0].get("config_value")
        return json.loads(v) if isinstance(v, str) else v
    except Exception:  # noqa: BLE001
        return None


def candidate_symbols(sb, day):
    """前一交易日尾盤戰卡（預備＋定版）的卡片與持有中代號。"""
    out = set()
    for mode in ("prep", "final"):
        p = _cfg(sb, f"tail_card:{day}:{mode}") or {}
        for c in p.get("cards") or []:
            if c.get("symbol"):
                out.add(str(c["symbol"]))
        for h in p.get("holds") or []:
            if h.get("symbol"):
                out.add(str(h["symbol"]))
    return out


def is_intraday(ev_time):
    t = str(ev_time or "")[:8]
    return bool(t) and INTRADAY_START <= t <= INTRADAY_END


def compute(events, cands, asof_hms):
    """events：mops_events 列；cands：候選代號集合；asof_hms：戰卡當時重訊資料時效 'HH:MM:SS' 或 None。純函式。"""
    intra = [e for e in events or [] if is_intraday(e.get("ev_time"))]
    hit = [e for e in intra if str(e.get("symbol")) in cands]
    if asof_hms:
        unseen = [e for e in hit if str(e.get("ev_time"))[:8] > asof_hms]
    else:
        unseen = list(hit)   # 時效不明：保守視為都看不到
    return {"intraday_total": len(intra), "intraday_cand": len(hit), "cand_symbols": len(cands),
            "unseen_by_card": len(unseen), "asof_unknown": asof_hms is None,
            "hits": [{"symbol": e.get("symbol"), "time": str(e.get("ev_time"))[:8], "category": e.get("category"),
                      "importance": e.get("importance"), "unseen": e in unseen} for e in sorted(hit, key=lambda x: str(x.get("ev_time")))][:60]}


def measure(sb, day):
    """量測 day（前一交易日 YYYY-MM-DD）。回傳報告 dict；資料庫讀取失敗拋例外由呼叫端處理。"""
    events, start = [], 0
    while True:
        rows = (sb.table("mops_events").select("symbol,ev_time,category,importance").eq("ev_date", day)
                .order("ev_key").range(start, start + 999).execute().data) or []
        events += rows
        if len(rows) < 1000:
            break
        start += 1000
    asof = MG.load_asof(sb, day)
    asof_hms = None
    if asof and asof.get("at") and asof["at"][:10] == day:   # 時效必須是該日的才有比對意義
        asof_hms = asof["at"][11:16] + ":59"
    rep = compute(events, candidate_symbols(sb, day), asof_hms)
    rep.update({"kind": "mops_gap_daily", "date": day, "asof": (asof or {}).get("at"), "asof_source": (asof or {}).get("source"),
                "note": "mops_events 只存重要度≥1，數量為下限；時效不明時 unseen 為保守估計"})
    return rep


def save(sb, rep, run_id=""):
    key = f"mops_gap_daily:{rep['date']}"
    sb.table("ui_selftest_reports").delete().eq("summary", key).execute()
    sb.table("ui_selftest_reports").insert({"run_id": run_id, "summary": key, "report": rep}).execute()


def public_line(rep):
    return (f"盤中重訊 {rep['intraday_total']} 則、涉及候選 {rep['intraday_cand']} 則（候選 {rep['cand_symbols']} 檔）、"
            f"戰卡看不到 {rep['unseen_by_card']} 則" + ("（時效不明，保守估計）" if rep.get("asof_unknown") else ""))
