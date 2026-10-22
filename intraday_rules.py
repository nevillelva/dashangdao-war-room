"""【2026-10-22 老闆：當沖報告的建議全部做】當沖自動進退場的規則（純函式，可離線測試）。

起因（見專案文件「當沖自動進退場分析_動能做多當沖回測_20261022」）：
  1. 10/22 自動做多 8 檔全虧：其中 5 檔是老闆自己的雷達清單／持倉（三關把手動清單預設成做多、且三關一律放行），
     3 檔是主戰清單「短線動能」——5 年回測「10:00 進、13:25 出」的動能做多每筆 −0.61%、勝率 29%（t −14）。
  2. 每檔固定 1 張 → 元損益被股價高低決定（世芯-KY 一張 369.5 萬，其餘 8 檔加起來 120.6 萬）。
  3. 8 檔同向、同題材，等於一個押注；13:25 前沒有停損。
  4. 方向是 exit（查7 已持有者賣出）的候選如果三關通過，舊程式會當成「做空」進場。

規則：
  - 只執行「系統候選池」裡的股票；手動雷達清單／持倉只追蹤、不自動進場。
  - 方向只接受 long／short；exit 不進場。
  - 主戰清單「短線動能」的做多改成影子單（trade_type=intraday_shadow）：照樣記錄、13:25 照樣平倉，但不計入績效與通知合計。
  - 其他做多：每天最多 DT_MAX_LONG 檔、同產業最多 DT_MAX_PER_IND 檔；波段主力優先，其次候選分數高者。
  - 每檔等資金 DT_CAP 元（shares 欄位是浮點數，以「張」計、可到 0.001 張＝1 股零股）。
  - 停損：13:25 平倉時用永豐金當日 1 分K 回看「進場後」的最低（做空看最高），觸及停損價就以停損價記錄出場（等同事先掛好停損單）；
    若那根 K 棒開盤就已越過停損價，以該根開盤價出（跳空滑價）。DT_STOP_PCT 依「當沖停損」回測結果設定。
  - 通知與績效以「等資金、扣成本」呈現，並附等權平均報酬。
"""
from __future__ import annotations

DT_CAP = 100_000          # 每檔等資金（元）
DT_COST = 0.435           # 當沖來回成本（手續費 0.1425%×2＋當沖證交稅 0.15%）
DT_MAX_LONG = 3           # 每天做多最多檔數（不含影子單）
DT_MAX_PER_IND = 1        # 同產業做多最多檔數
# 當沖停損 3%：5 年回測（id=686，21 萬筆 10:00 進 13:25 出）停損 1.5～5% 的平均都在 −0.49～−0.52%（差異在誤差內），
# 3% 觸發約 5%、平均 −0.514%（不停損 −0.515%）→ 幾乎不影響報酬、把單筆最大虧損限在約 −3.4%。
DT_STOP_PCT = 3.0
SHADOW_TYPE = "intraday_shadow"
SHADOW_TRACKS = ("短線動能",)


def _num(x, default=0.0):
    try:
        v = float(x)
        return v if v == v else default
    except (TypeError, ValueError):
        return default


def plan_entries(gate_pass, pool_rows, industry_of=None, max_long=DT_MAX_LONG, max_per_ind=DT_MAX_PER_IND):
    """gate_pass：[(symbol, direction)]（三關 pass、且今天尚未進場）；pool_rows：intraday_candidate_pool 當日列（symbol/direction/source/note/score）。
    回傳 {"enter": [(sym, side, trade_type, reason)], "skipped": [(sym, direction, 原因)]}。"""
    industry_of = industry_of or {}
    pool = {}
    for r in pool_rows or []:
        pool.setdefault((r.get("symbol"), r.get("direction") or "long"), r)
    enter, skipped, longs = [], [], []
    for sym, d in gate_pass:
        if d not in ("long", "short"):
            skipped.append((sym, d, "方向不是多／空（例：查7 持有者賣出），不進場"))
            continue
        row = pool.get((sym, d))
        if row is None:
            skipped.append((sym, d, "不在系統候選池（手動雷達清單／持倉只追蹤、不自動進場）"))
            continue
        note = str(row.get("note") or "")
        if d == "short":
            enter.append((sym, "short", "intraday", "空方三關全過"))
            continue
        if any(t in note for t in SHADOW_TRACKS):
            enter.append((sym, "long", SHADOW_TYPE, "短線動能做多→影子單（5 年回測當沖每筆 −0.61%，不計績效）"))
            continue
        pri = 0 if "波段主力" in note else 1
        longs.append((pri, -_num(row.get("score")), sym))
    used_ind, n_long = {}, 0
    for pri, _neg, sym in sorted(longs):
        ind = industry_of.get(sym) or f"_無產業_{sym}"
        if n_long >= max_long:
            skipped.append((sym, "long", f"當天做多已達上限 {max_long} 檔"))
            continue
        if used_ind.get(ind, 0) >= max_per_ind:
            skipped.append((sym, "long", f"同產業（{ind}）已有 {max_per_ind} 檔"))
            continue
        used_ind[ind] = used_ind.get(ind, 0) + 1
        n_long += 1
        enter.append((sym, "long", "intraday", "多方三關全過（波段主力優先）" if pri == 0 else "多方三關全過"))
    return {"enter": enter, "skipped": skipped}


def size_shares(price, cap=DT_CAP):
    """等資金：回傳張數（可為零股，最小 0.001 張＝1 股）。"""
    p = _num(price)
    if p <= 0:
        return 0.0
    return max(0.001, round(cap / (p * 1000), 3))


def minute_bars(rows, label="end"):
    """永豐金 1 分K rows（ts/Open/High/Low/Close）→ [(起始 'HH:MM', o, h, l, c)]，依時間排序。ts 視為台北當地時間。"""
    import pandas as pd
    out = []
    for r in rows or []:
        try:
            t = pd.to_datetime(r["ts"])
        except Exception:  # noqa: BLE001
            continue
        if label == "end":
            t = t - pd.Timedelta(minutes=1)
        out.append((t.strftime("%H:%M"), _num(r.get("Open")), _num(r.get("High")), _num(r.get("Low")), _num(r.get("Close"))))
    return sorted(out)


def stop_check(bars, entry_hhmm, entry_price, side, stop_pct):
    """bars：minute_bars 輸出。只看起始時間 > entry_hhmm 的 K 棒（進場那一分鐘之後）。
    回傳 None（沒觸及／未啟用）或 {"price": 出場價, "time": 'HH:MM', "gap": bool}。"""
    if not stop_pct or not entry_price or entry_price <= 0:
        return None
    lv = entry_price * (1 - stop_pct / 100) if side == "long" else entry_price * (1 + stop_pct / 100)
    for t, o, h, l, _c in bars:
        if t <= entry_hhmm:
            continue
        if side == "long":
            if o > 0 and o <= lv:
                return {"price": o, "time": t, "gap": True}
            if l > 0 and l <= lv:
                return {"price": round(lv, 2), "time": t, "gap": False}
        else:
            if o > 0 and o >= lv:
                return {"price": o, "time": t, "gap": True}
            if h > 0 and h >= lv:
                return {"price": round(lv, 2), "time": t, "gap": False}
    return None


def trade_result(side, entry, exit_px, shares):
    """回傳 (帳面損益元, 毛報酬%, 扣成本報酬%, 等資金損益元)。"""
    entry, exit_px, shares = _num(entry), _num(exit_px), _num(shares)
    if entry <= 0:
        return 0.0, 0.0, 0.0, 0.0
    sgn = 1 if side == "long" else -1
    pnl = sgn * (exit_px - entry) * shares * 1000
    roi = sgn * (exit_px / entry - 1) * 100
    net = roi - DT_COST
    return pnl, roi, net, net / 100 * DT_CAP


def exit_report(run_date, exits, shadow_exits, stop_note=""):
    """exits／shadow_exits：[{label, side, net, eq_pnl, stopped}]。回傳 Telegram 文字。"""
    def _line(x):
        tag = "⛔停損 " if x.get("stopped") else ""
        return f"{tag}{x['label']}({'做多' if x['side'] == 'long' else '做空'},{x['net']:+.2f}%,{x['eq_pnl']:+,.0f}元)"
    lines = [f"🔔 [{run_date}] 13:25當沖強制出場（不留倉鐵律）",
             f"口徑：每檔等資金 {DT_CAP:,} 元、已扣當沖成本 {DT_COST}%{stop_note}"]
    if exits:
        lines.append("、".join(_line(x) for x in exits))
        tot = sum(x["eq_pnl"] for x in exits)
        avg = sum(x["net"] for x in exits) / len(exits)
        win = sum(1 for x in exits if x["net"] > 0)
        nl = [x["net"] for x in exits if x["side"] == "long"]
        ns = [x["net"] for x in exits if x["side"] == "short"]
        part = []
        if nl:
            part.append(f"做多 {len(nl)} 檔平均 {sum(nl) / len(nl):+.2f}%")
        if ns:
            part.append(f"做空 {len(ns)} 檔平均 {sum(ns) / len(ns):+.2f}%")
        lines.append(f"💰 合計：等資金 {tot:+,.0f} 元｜{len(exits)} 檔等權平均 {avg:+.2f}%｜勝 {win}／敗 {len(exits) - win}"
                     + (f"｜{'；'.join(part)}" if part else ""))
    else:
        lines.append("（沒有計入績效的部位）")
    if shadow_exits:
        lines.append("👻 影子單（不計績效）：" + "、".join(_line(x) for x in shadow_exits))
    return "\n".join(lines)
