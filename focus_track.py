"""【2026-10-19 老闆：每天主推的 10 檔也要做勝率分析，才能改進增加勝率】主戰清單實際結果追蹤（純函式，可離線測試）。
規則（與 5 年回測同口徑，才能拿實盤對照回測）：
- 做多（波段主力、短線動能）：清單日 D 開盤買進，持有 1／5／10 個交易日後收盤賣出；成本 0.585%；
  超額＝同期間加權指數（同樣 D 開盤→第 N 天收盤）報酬。
- 查8 空方：D 開盤比昨收（ref_close）高 >1% 才放空、當天收盤回補（成本 0.435%）；沒開高＝不做（記為 skipped，不算勝負）。
限制：①進場價用 D 開盤價，未套用 10:00 修正（保留／剔除）的過濾，是「清單全部」的成績；②樣本要累積，
<30 筆時只當觀察；③漲跌停無法成交的情形未處理。每筆結果存 system_config「focus_result:日期」，彙總存私有表 ui_selftest_reports。
"""
from __future__ import annotations

from statistics import mean

COST_LONG, COST_DT = 0.585, 0.435
HORIZONS = (1, 5, 10)


def _slice(df, d):
    """df：日K DataFrame（index 日期、Open／Close）。回傳 d 當天（含）之後的列。"""
    if df is None or len(df) == 0:
        return None
    s = df[df.index.strftime("%Y-%m-%d") >= d]
    return s if len(s) else None


def settle_item(item, d, df, twii=None):
    """單一清單項目結算。item：{symbol, track, direction, ref_close?}。回傳 dict（包含 status）。"""
    out = {"symbol": item.get("symbol"), "track": item.get("track"), "direction": item.get("direction")}
    s = _slice(df, d)
    if s is None or s.index[0].strftime("%Y-%m-%d") != d:
        out["status"] = "no_data"
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
    for h, e in st.items():
        out[f"r{h}"] = e["r"]
        if "ex" in e:
            out[f"ex{h}"] = e["ex"]
    return out


def aggregate(results):
    """results：settle_item 輸出的 list（含 date 欄）。依軌道與期間彙總：筆數、勝率、平均淨報酬、平均超額。"""
    res = {}
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
        res[t] = e
    return res
