"""【2026-10-19 老闆：每天主推的 10 檔也要做勝率分析，才能改進增加勝率】主戰清單實際結果追蹤（純函式，可離線測試）。
規則（與 5 年回測同口徑，才能拿實盤對照回測）：
- 做多（波段主力、短線動能）：清單日 D 開盤買進，持有 1／5／10／20 個交易日後收盤賣出；成本 0.585%；
  超額＝同期間加權指數（同樣 D 開盤→第 N 天收盤）報酬。
- 查8 空方：D 開盤比昨收（ref_close）高 >1% 才放空、當天收盤回補（成本 0.435%）；沒開高＝不做（記為 skipped，不算勝負）。
限制：①進場價用 D 開盤價，未套用 10:00 修正（保留／剔除）的過濾，是「清單全部」的成績；②樣本要累積，
<30 筆時只當觀察；③漲跌停無法成交的情形未處理。每筆結果存 system_config「focus_result:日期」，彙總存私有表 ui_selftest_reports。
"""
from __future__ import annotations

from statistics import mean

COST_LONG, COST_DT = 0.585, 0.435
HORIZONS = (1, 5, 10, 20)   # 2026-10-19 老闆：做多要看持有 20 日的勝率
# 【2026-10-22 口徑對齊回測】各軌道「照規則做」的結果（與 5 年回測 cmd_playbook_bt 同口徑）：
#   波段主力：D 開盤進、抱 20 日、進場價 −10% 觸價停損（跳空以開盤價出）；短線動能：抱 5 日、−5% 停損；
#   空方：開盤比昨收高 3～7%（分點空單 1～7%）才空、收盤前回補（收漲停補不了＝隔天開盤回補）；查7 持有者：開高≥0% 開盤賣（09:05～09:15 以開盤近似）、開低撐到收盤賣。
# 成本：做多 0.585%、當沖空 0.435%、查7 賣出端 0.435%。進場用開盤價（回測口徑；實際建議 12:30～13:20 下單，回測顯示晚買約多 0.15～0.3 個百分點）。
STRAT = {"波段主力": (20, 10.0), "短線動能": (5, 5.0)}
SHORT_NEED_C8, SHORT_NEED_DT, SHORT_MAX = 3.0, 1.0, 7.0
LOCK_PCT = 9.4


def _slice(df, d):
    """df：日K DataFrame（index 日期、Open／Close）。回傳 d 當天（含）之後的列。"""
    if df is None or len(df) == 0:
        return None
    s = df[df.index.strftime("%Y-%m-%d") >= d]
    return s if len(s) else None


def _strat_long(track, s, o):
    """做多軌道照規則做：D 開盤進、抱 hold 日收盤出、進場價 −stop% 觸價停損。資料不夠＝進行中（status=open，附目前未實現報酬）。"""
    hold, stop = STRAT[track]
    lv = o * (1 - stop / 100)
    has_low = "Low" in s.columns
    for k in range(min(hold, len(s))):
        op = float(s["Open"].iloc[k])
        lo = float(s["Low"].iloc[k]) if has_low else None
        if op > 0 and op <= lv:
            return {"status": "done", "r": round((op / o - 1) * 100 - COST_LONG, 3), "stopped": True, "days": k + 1}
        if lo is not None and lo > 0 and lo <= lv:
            return {"status": "done", "r": round(-stop - COST_LONG, 3), "stopped": True, "days": k + 1}
    if len(s) >= hold:
        return {"status": "done", "r": round((float(s["Close"].iloc[hold - 1]) / o - 1) * 100 - COST_LONG, 3), "stopped": False, "days": hold}
    return {"status": "open", "mtm": round((float(s["Close"].iloc[-1]) / o - 1) * 100, 3), "days": len(s)}


def _strat_short(item, s, o, c, ref):
    gap = (o / ref - 1) * 100
    need = SHORT_NEED_C8 if item.get("cmds") else SHORT_NEED_DT
    if gap < need or gap >= SHORT_MAX:
        return {"status": "skipped", "gap": round(gap, 2)}
    h = float(s["High"].iloc[0]) if "High" in s.columns else c
    locked = (c / ref - 1) * 100 >= LOCK_PCT and c >= h * 0.9995
    if locked:
        if len(s) >= 2:
            nxt = float(s["Open"].iloc[1])
            return {"status": "done", "r": round((o - nxt) / o * 100 - COST_DT, 3), "locked": True}
        return {"status": "open", "locked": True}
    return {"status": "done", "r": round((o - c) / o * 100 - COST_DT, 3), "locked": False}


def settle_item(item, d, df, twii=None):
    """單一清單項目結算。item：{symbol, track, direction, ref_close?}。回傳 dict（包含 status）。"""
    out = {"symbol": item.get("symbol"), "track": item.get("track"), "direction": item.get("direction")}
    s = _slice(df, d)
    if s is None or s.index[0].strftime("%Y-%m-%d") != d:
        # 清單日不是交易日（週末、國定假日）：不算失敗也不算樣本。有後續日K但沒有 D 那天＝非交易日；資料根本沒有＝才是 no_data
        import datetime as _dt
        wk = _dt.date.fromisoformat(d).weekday() >= 5
        out["status"] = "non_trading" if (wk or (s is not None and len(s) > 0)) else "no_data"
        return out
    o = float(s["Open"].iloc[0])
    if not o > 0:
        out["status"] = "no_data"
        return out
    if item.get("direction") == "short":
        ref = item.get("ref_close")
        c = float(s["Close"].iloc[0])
        if not ref or float(ref) <= 0:
            out["status"] = "no_ref"
            return out
        gap = (o / float(ref) - 1) * 100
        out["gap"] = round(gap, 2)
        if gap < 1.0:
            out["status"] = "skipped"      # 沒開高>1%：規則上不做
            return out
        out.update(status="done", r1=round((o - c) / o * 100 - COST_DT, 3))
        out["st"] = _strat_short(item, s, o, c, float(ref))
        return out
    if item.get("direction") == "exit":
        ref = item.get("ref_close")
        if not ref or float(ref) <= 0:
            out["status"] = "no_ref"
            return out
        gap = (o / float(ref) - 1) * 100
        xp = o if gap >= 0 else float(s["Close"].iloc[0])
        out.update(status="done", gap=round(gap, 2), r1=round((xp / float(ref) - 1) * 100 - COST_DT, 3))
        out["st"] = {"status": "done", "r": out["r1"], "rule": "開高≥0% 開盤賣、開低撐到收盤賣（成本只算賣出端，買進價＝昨收漲停）"}
        return out
    st = {}
    tw = _slice(twii, d) if twii is not None else None
    for h in HORIZONS:
        if len(s) < h:
            continue
        c = float(s["Close"].iloc[h - 1])
        r = (c / o - 1) * 100 - COST_LONG
        e = {"r": round(r, 3)}
        if tw is not None and len(tw) >= h and float(tw["Open"].iloc[0]) > 0:
            m = (float(tw["Close"].iloc[h - 1]) / float(tw["Open"].iloc[0]) - 1) * 100
            e["ex"] = round(r - m, 3)
        st[h] = e
    out["status"] = "done" if st else "pending"
    if item.get("track") in STRAT:
        out["st"] = _strat_long(item["track"], s, o)
    for h, e in st.items():
        out[f"r{h}"] = e["r"]
        if "ex" in e:
            out[f"ex{h}"] = e["ex"]
    return out


def aggregate(results):
    """results：settle_item 輸出的 list（含 date 欄）。依軌道與期間彙總：筆數、勝率、平均淨報酬、平均超額。"""
    res = {}
    results = [r for r in results if r.get("status") != "non_trading"]
    tracks = sorted({r.get("track") for r in results if r.get("track")})
    for t in tracks + ["全部"]:
        sub = [r for r in results if t == "全部" or r.get("track") == t]
        e = {"listed": len(sub), "skipped": sum(1 for r in sub if r.get("status") == "skipped"),
             "pending": sum(1 for r in sub if r.get("status") == "pending"),
             "no_data": sum(1 for r in sub if r.get("status") in ("no_data", "no_ref"))}
        for h in HORIZONS:
            xs = [r[f"r{h}"] for r in sub if f"r{h}" in r]
            if not xs:
                continue
            ex = [r[f"ex{h}"] for r in sub if f"ex{h}" in r]
            e[f"h{h}"] = {"n": len(xs), "win": round(100 * sum(1 for x in xs if x > 0) / len(xs), 1),
                          "net": round(mean(xs), 3), "ex": round(mean(ex), 3) if ex else None,
                          "small_sample": len(xs) < 30}
        sts = [r["st"] for r in sub if r.get("st")]
        dn = [x["r"] for x in sts if x.get("status") == "done" and x.get("r") is not None]
        op = [x["mtm"] for x in sts if x.get("status") == "open" and x.get("mtm") is not None]
        if sts:
            e["strategy"] = {"done": len(dn), "open": len(op), "skipped": sum(1 for x in sts if x.get("status") == "skipped"),
                             "win": round(100 * sum(1 for x in dn if x > 0) / len(dn), 1) if dn else None,
                             "avg": round(mean(dn), 3) if dn else None, "stopped": sum(1 for x in sts if x.get("stopped")),
                             "avg_open_mtm": round(mean(op), 3) if op else None, "small_sample": len(dn) < 30}
        res[t] = e
    return res
