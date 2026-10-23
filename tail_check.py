"""【2026-10-22 老闆：三關可能不用，改 09:30／11:00／12:30 多時點判斷，找讓每個做多指令勝率≥五成的組合】
13:20 尾盤進場確認（純函式，可離線測試）。規則來自多時點組合 5 年回測（ui_selftest_reports id=686，intraday_combo_bt；
盤中 5 分K 193 檔、2021-10～2026-10；只用樣本內 ≤2024-03-31 挑，樣本外驗證，且贏同組合的隨機基準）。

共同核心：09:30 價 ≥ 開盤 +0.5%（A2）＋ 12:30 價 ≥ 當日均價（C2）→ 13:20 進場（12:30 與 13:20 進場結果幾乎一樣）。
各指令最佳組合（數字＝勝率／每筆平均，已扣成本 0.585%；括號＝樣本數）：
  查3 價值：A2＋C2、抱 60 日　　　　　　　 樣本內 60.2%／+8.48%（949）　樣本外 66.3%／+14.32%（876）　隨機基準同組合 +3.51／+8.14
  查6 營收雙增：A2＋C2、抱 20 日　　　　　 樣本內 50.3%／+1.69%（2,156）樣本外 56.7%／+5.71%（3,741）基準 +1.14／+2.20
  查4 投信：A2＋B3(11:00≥均價)＋C2、抱20日 樣本內 51.5%／+1.50%（2,738）樣本外 53.5%／+4.58%（3,411）基準 +1.21／+2.34
  查2 拉回：A2＋B3＋C3(12:30≥11:00價)、抱 20 日、停利 3%（勝率優先版）
                                            樣本內 76.9%／+0.47%（661）　樣本外 81.3%／+0.77%（662）　基準 +0.02／+0.15
  動能：A2＋B1(09:30~11:00不破開盤)＋C5(12:30距當日高≤1%)、抱 10 日、停利 5%
                                            樣本內 67.7%／+1.32%（223）　樣本外 70.7%／+1.18%（205）　基準 −0.06／−0.26（樣本小，t 樣本外 1.95）
  做多主力（查3∧查2）：沒有任何組合樣本內外都達五成（n 1,600；樣本內本身為負）→ 依查3 規則判斷，標「做多主力本身未達五成」。
限制：母體偏大型股（193 檔）；主戰清單「短線動能」是主力偵測＋營收年增，與回測的動能（量比≥2＋創20日新高）不完全相同，借用其規則；
樣本外 2024-04 之後是強多頭，勝率偏高；停利 3% 的高勝率版本每筆平均小、輸的時候可能抱很久。
"""
from __future__ import annotations

RULES = {
    "查3": {"a": 2, "b": 0, "c": 2, "hold": 60, "tp": None, "label": "09:30≥開盤+0.5%＋12:30≥均價→13:20 進、抱 60 日",
           "stat": "回測勝率 60%／66%（樣本內／外）、每筆 +8.5%／+14.3%"},
    "查6": {"a": 2, "b": 0, "c": 2, "hold": 20, "tp": None, "label": "09:30≥開盤+0.5%＋12:30≥均價→13:20 進、抱 20 日",
           "stat": "回測勝率 50%／57%、每筆 +1.7%／+5.7%"},
    "查4": {"a": 2, "b": 3, "c": 2, "hold": 20, "tp": None, "label": "09:30≥開盤+0.5%＋11:00≥均價＋12:30≥均價→13:20 進、抱 20 日",
           "stat": "回測勝率 52%／54%、每筆 +1.5%／+4.6%"},
    "查2": {"a": 2, "b": 3, "c": 3, "hold": 20, "tp": 3, "label": "09:30≥開盤+0.5%＋11:00≥均價＋12:30≥11:00價→13:20 進、停利 3%、最多抱 20 日",
           "stat": "回測勝率 77%／81%、每筆 +0.5%／+0.8%（勝率優先版）"},
    "動能": {"a": 2, "b": 1, "c": 5, "hold": 10, "tp": 5, "label": "09:30≥開盤+0.5%＋09:30~11:00 不破開盤＋12:30 距當日高≤1%→13:20 進、停利 5%、最多抱 10 日",
            "stat": "回測勝率 68%／71%、每筆 +1.3%／+1.2%（樣本 220 筆，偏少）"},
}


def _pfx(c):
    s = str(c)
    return s.split(".")[0] if "." in s else s


def rule_for(item):
    """主戰清單一檔做多 → (規則名, 備註)。"""
    if item.get("direction") != "long":
        return None, None
    if item.get("track") == "短線動能":
        return "動能", "主戰清單短線動能＝主力偵測＋營收年增，借用回測動能（量比≥2＋創20日新高）的規則"
    cmds = {_pfx(c) for c in (item.get("cmds") or [])}
    if item.get("track") == "波段主力":
        return "查3", "做多主力（查3∧查2）本身沒有組合達五成，依查3 規則"
    for k in ("查3", "查6", "查4", "查2"):
        if k in cmds:
            return k, None
    return None, None


def minute_rows_to_ihist(rows):
    """永豐金 1 分K（ts 為該分鐘結束、台北時間）→ intraday_timing_bt.day_profiles 吃的格式（t＝分鐘起點，自 1970-01-01 起的分鐘數）。"""
    import numpy as np
    import pandas as pd
    t, o, h, l, c, v = [], [], [], [], [], []
    for r in rows or []:
        try:
            ts = pd.to_datetime(r["ts"]) - pd.Timedelta(minutes=1)
        except Exception:  # noqa: BLE001
            continue
        t.append(int((ts - pd.Timestamp("1970-01-01")).total_seconds() // 60))
        o.append(float(r["Open"])); h.append(float(r["High"])); l.append(float(r["Low"])); c.append(float(r["Close"])); v.append(float(r.get("Volume") or 0))
    if not t:
        return None
    k = np.argsort(t)
    return {"t": np.array(t)[k], "o": np.array(o)[k], "h": np.array(h)[k], "l": np.array(l)[k], "c": np.array(c)[k], "v": np.array(v)[k]}


def pf_from_snaps(snaps):
    """【2026-10-23】永豐金盤中 1 分K 當天查不到（實測 10/23 全天回空）→ 改用 09:30／11:00／12:30 三個時點的快照組出同格式的 pf。
    snaps：{"09:30": [開, 高, 低, 現價, 均價], "11:00": [...], "12:30": [...]}（缺的時點可省略）。
    近似：B1「09:30~11:00 不破開盤」只能用「到 11:00 為止的當日最低 ≥ 開盤」判斷（比原定義嚴）；C4 不提供。"""
    snaps = {k: v for k, v in (snaps or {}).items() if v and v[0] and v[3]}
    if not snaps:
        return None
    op = float(next(iter(snaps.values()))[0])
    pf = {"open": op, "p": {}, "vw": {}, "hi": {}, "lo": {}, "slo": {}, "approx": True}
    for t, v in snaps.items():
        pf["p"][t] = float(v[3])
        pf["vw"][t] = float(v[4]) if v[4] else None
        pf["hi"][t], pf["lo"][t] = float(v[1]), float(v[2])
    if "11:00" in pf["lo"]:
        pf["slo"]["11:00"] = pf["lo"]["11:00"] if pf["lo"]["11:00"] < op else op
    return pf


def evaluate(item, rows, pf=None):
    """回傳 {symbol, rule, pass, detail, note}；資料不足 pass=None。rows＝當日 1 分K；沒有時可給 pf（pf_from_snaps）。"""
    import intraday_combo_bt as XC
    import intraday_timing_bt as IT
    rk, note = rule_for(item)
    out = {"symbol": item.get("symbol"), "track": item.get("track"), "rule": rk, "note": note, "pass": None, "detail": ""}
    if not rk:
        out["detail"] = "沒有對應的回測規則"
        return out
    data = minute_rows_to_ihist(rows) if rows else None
    prof = IT.day_profiles(data) if data is not None else {}
    if prof:
        pf = list(prof.values())[-1]
    elif pf is None:
        out["detail"] = "盤中資料不足（沒有 1 分K、也沒有 09:30／12:30 快照）"
        return out
    else:
        out["source_data"] = "時點快照"
    if not pf["p"].get("12:30") or not pf["p"].get("09:30"):
        out["detail"] = "12:30 或 09:30 價格缺"
        return out
    a, b, c = XC.flags(pf)
    R = RULES[rk]
    ok_a, ok_b, ok_c = bool((a >> R["a"]) & 1), bool((b >> R["b"]) & 1), bool((c >> R["c"]) & 1)
    op, p930, p1230, vw = pf["open"], pf["p"]["09:30"], pf["p"]["12:30"], pf["vw"].get("12:30")
    parts = [f"09:30 {(p930 / op - 1) * 100:+.1f}%（要≥+0.5%）{'✅' if ok_a else '❌'}"]
    if R["b"]:
        parts.append(f"{XC.B_NAMES[R['b']][3:]} {'✅' if ok_b else '❌'}")
    parts.append(f"{XC.C_NAMES[R['c']][3:]}" + (f"（價 {p1230:g}／均價 {vw:.2f}）" if vw else "") + f" {'✅' if ok_c else '❌'}")
    out.update({"pass": ok_a and ok_b and ok_c, "detail": "；".join(parts), "open": op, "p0930": p930, "p1230": p1230,
                "vwap1230": round(vw, 2) if vw else None, "hold": R["hold"], "tp": R["tp"]})
    return out


def message(run_date, results, name_of=None):
    name_of = name_of or {}
    lines = [f"🎯 [{run_date}] 13:20 尾盤進場確認（多時點規則，回測勝率≥五成）"]
    ok = [r for r in results if r.get("pass")]
    ng = [r for r in results if r.get("pass") is False]
    na = [r for r in results if r.get("pass") is None]
    for r in ok:
        R = RULES[r["rule"]]
        lines.append(f"✅ 進場 {name_of.get(r['symbol'], r['symbol'])}({r['symbol']})｜{r['rule']}｜{R['label']}｜{R['stat']}｜{r['detail']}")
    for r in ng:
        lines.append(f"❌ 不進 {name_of.get(r['symbol'], r['symbol'])}({r['symbol']})｜{r['rule']}｜{r['detail']}")
    for r in na:
        lines.append(f"⚪ 無法判斷 {name_of.get(r['symbol'], r['symbol'])}({r['symbol']})｜{r['detail']}")
    if not results:
        lines.append("今天主戰清單沒有做多標的。")
    lines.append("說明：條件全過才在 13:20 買；不過的今天不買（明天清單會重算）。只是提示，不下單。")
    return "\n".join(lines)
