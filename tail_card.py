"""【2026-10-23 老闆：12:30 加一次監控；尾盤前把戰卡的必要因素自動做快速分析——買不買、價格多少、停損多少、隔天賣還是續抱】
尾盤快速戰卡（純函式，可離線測試）。12:33 預備版、13:17 定版各跑一次（system_scheduler.stage_tail_prep／stage_focus_tail_check）。

候選來源：
  1. 主戰清單做多（tail_check 多時點規則：09:30／11:00／12:30）。
  2. 熱門區域（hot_zone_bt 5 年回測 id=697：熱門前 100 檔 × 籌碼 × 大盤／美股 × 今天開低，13:20 進、抱 20 日）——不需要查指令，獨立的一條。
  3. 雷達清單（有隔夜掃描指令的才套對應規則；只提示）。
每檔判斷順序（每一關都有回測或硬限制依據）：
  ① 規則：主戰／雷達要過多時點規則；熱門區域要全部條件成立。
  ② 硬限制：處置／注意股不做；離漲停 ≤1% 不追（排不到、追價）；短線動能高於昨收 2% 不追（回測 2～3% −1.16%）。
  ③ 大盤在季線下：主戰波段只做 1 檔小部位（5 年：季線下抱 20 日 −1.60%）。
  ④ 價位：進場＝13:20 市價；停損＝依來源的回測停損；戰卡防守線只當短線參考。
  ⑤ 持有計畫與隔天動作：依規則抱 N 日（回測「13:20 買、隔天開盤賣」熱門股勝率只有 39～42%、平均 −0.2～−0.3%，所以隔天不賣）。
持有中（之前 13:20 確認過的、老闆自己的持倉）：每天 12:33 檢查停損／停利／到期 → 給「明天續抱或賣」。只是提示，不下單。"""
from __future__ import annotations

# 熱門區域（hot_zone_bt；數字由 stage 依最新報告覆寫 stat，這裡是預設文字）。need＝hot_zone_bt.FLAGS 的代碼，全部成立才算。
ZONES = [   # 2026-10-23 hot_zone_bt id=697（2021-11～2026-10、1,090 檔上市普通股；勝率／每筆平均＝樣本內／樣本外，已扣成本 0.585%）
    {"key": "Z1", "label": "投信大買＋加權在季線上＋今天開低", "need": ["tbig", "tw60", "gap_dn"], "hold": 20,
     "stat": "勝率 55.7%／59.0%、抱 20 日每筆 +2.9%／+4.7%（n 343／817，t 3.0／5.5）"},
    {"key": "Z2", "label": "投信大買＋費半在月線上＋今天開低", "need": ["tbig", "sox20", "gap_dn"], "hold": 20,
     "stat": "勝率 55.9%／59.0%、每筆 +2.7%／+4.6%（n 229／402，t 2.7／4.7）"},
    {"key": "Z3", "label": "外資投信同買＋那斯達克前一晚漲＋今天開低", "need": ["ft", "ndx_up", "gap_dn"], "hold": 20,
     "stat": "勝率 55.2%／58.1%、每筆 +3.7%／+4.2%（n 212／272，t 3.4／3.0）"},
]
ZONE_STOP_NOTE = ("區域單回測不設停損最好（Z1 設 −10% 停損：樣本外每筆 +4.7%→+2.2%、勝率 59%→47%）；"
                  "最差 5% 約 −16%，要控制最壞情況可自行設 −10%")
ZONE_NOTE = "熱門區域＝前一天近 20 日均成交值前 100 名；投信大買＝投信買超≥當日成交量 5%；開低＝今天開盤比昨收低≥1%"

SWING_STOP = {"波段主力": 10.0, "短線動能": 5.0}
RULE_STOP = {"查3": 10.0, "查6": 10.0, "查4": 10.0, "查2": 10.0, "動能": 5.0}
CHASE_PCT = {"短線動能": 2.0}
NEAR_LIMIT_PCT = 1.0
NEXT_OPEN_NOTE = "回測：13:20 買、隔天開盤就賣，熱門股勝率 39～42%、平均 −0.2～−0.3%（扣成本）→ 隔天不賣，照計畫抱"


def _f(x):
    try:
        v = float(x)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


def zone_match(flags):
    """flags：hot_zone_bt 旗標代碼集合 → 符合的區域 list。"""
    s = set(flags or ())
    return [z for z in ZONES if all(k in s for k in z["need"])]


def flags_from_arrays(dates, O, H, L, C, V, inst=None, tw=None, us=None):
    """用 hot_zone_bt.stock_arrays 的同一套定義算「昨天＝訊號日、今天＝進場日」的旗標（今天那根用盤中到目前的開高低與現價）。
    dates 最後一個＝今天。回傳旗標代碼 set；資料不足回 None。"""
    import hot_zone_bt as HZ
    n = len(dates)
    if n < 62:
        return None
    a = HZ.stock_arrays(dates, O, H, L, C, V, inst=inst, tw=tw, us=us)
    idx = list(a["sig_idx"])
    if (n - 2) not in idx:
        return None
    fl = int(a["flags"][idx.index(n - 2)])
    return {k for b, (k, _l, _c) in enumerate(HZ.FLAGS) if (fl >> b) & 1}


def limit_up(ref):
    return round(float(ref) * 1.1, 2)


CRASH_NOTE = "費半前一晚重挫（≤−3%）：Z1 在這種日子樣本內勝率只有 44.9%（n=89；樣本外 56.3%，n=293）→ 降為小量"


def decide(item, rule_res=None, zones=None, px=None, ref=None, lv=None, vetoes=None, bull60=None, sox_crash=False):
    """item：{symbol, source(主戰/熱門區域/雷達), track, direction}；rule_res：tail_check.evaluate 結果（主戰／雷達）；zones：zone_match 結果。
    回傳 {decision: buy/small/skip, why:[...], entry, stop_px, stop_pct, hold, tp, next_day, basis}。"""
    why, zones = [], list(zones or [])
    src = item.get("source") or "主戰清單"
    px, ref = _f(px), _f(ref)
    px = round(px, 2) if px else px
    out = {"symbol": item.get("symbol"), "source": src, "track": item.get("track"), "px": px, "ref": ref, "zones": [z["key"] for z in zones]}
    rule_ok = bool(rule_res and rule_res.get("pass"))
    if src == "熱門區域":
        ok = bool(zones)
        basis = "；".join(f"{z['key']} {z['label']}（{z['stat']}）" for z in zones)
    else:
        ok = rule_ok
        basis = (rule_res or {}).get("detail") or "沒有對應的回測規則"
        if rule_res and rule_res.get("pass") is None:
            out.update(decision="skip", why=["資料不足：" + (rule_res.get("detail") or "")], basis=basis)
            return out
    if not ok:
        out.update(decision="skip", why=["規則沒過：" + basis], basis=basis)
        return out
    for v in vetoes or []:
        why.append(v)
    if ref and px and px >= limit_up(ref) * (1 - NEAR_LIMIT_PCT / 100):
        why.append(f"離漲停 ≤{NEAR_LIMIT_PCT:g}%（漲停 {limit_up(ref):g}）：排不到、也是追價，不買")
    cap = CHASE_PCT.get(item.get("track"))
    if cap and ref and px and px > ref * (1 + cap / 100):
        why.append(f"現價高於昨收 +{cap:g}%（{ref * (1 + cap / 100):.2f}）不追（回測 2～3% 平均 −1.16%）")
    if why:
        out.update(decision="skip", why=why, basis=basis)
        return out
    # 停損與持有
    rk = (rule_res or {}).get("rule")
    hold = (rule_res or {}).get("hold")
    tp = (rule_res or {}).get("tp")
    stop = SWING_STOP.get(item.get("track")) or RULE_STOP.get(rk)
    if src == "熱門區域" or (not rule_ok and zones):
        z = zones[0]
        hold, tp, stop = z["hold"], None, None
        why.append(ZONE_STOP_NOTE)
    if zones and rule_ok:
        why.append("⭐ 多時點規則＋熱門高勝率區域同時成立（兩個獨立優勢）")
    dec = "buy"
    if sox_crash and zones and not rule_ok:
        dec = "small"
        why.append(CRASH_NOTE)
    if bull60 is False and item.get("track") == "波段主力":
        dec = "small"
        why.append("大盤在季線下：只做 1 檔、小部位（5 年：季線下抱 20 日 −1.60%、勝率 39%）")
    base_px = px or ref
    stop_px = round(base_px * (1 - stop / 100), 2) if (stop and base_px) else None
    tp_px = round(base_px * (1 + tp / 100), 2) if (tp and base_px) else None
    entry = f"13:20 市價（現價 {px:g}）" if px else "13:20 市價"
    if lv and lv.get("def"):
        entry += f"｜戰卡防守線 {lv['def']:g}（短線參考，不當波段停損）"
    nd = [f"明天不用賣：照計畫最多抱 {hold} 個交易日" if hold else "明天不用賣"]
    if stop_px:
        nd.append(f"收盤或盤中跌破 {stop_px:g}（−{stop:g}%）才賣")
    elif not stop:
        nd.append("中途不停損，第 20 個交易日 13:20 賣")
    if tp_px:
        nd.append(f"碰到 {tp_px:g}（+{tp:g}%）停利")
    out.update(decision=dec, why=why, basis=basis, entry=entry, stop_pct=stop, stop_px=stop_px, tp=tp, tp_px=tp_px, hold=hold,
               next_day="；".join(nd))
    return out


def holding_action(h, px_now, hi_since=None, lo_since=None, days_held=None, vwap1230=None, def_line=None):
    """持有中的部位 → 明天（或今天 13:20）動作。h：{symbol, entry_px, hold, stop_pct, tp, source}。純函式。"""
    e, px = _f(h.get("entry_px")), _f(px_now)
    if not e or not px:
        return {"symbol": h.get("symbol"), "action": "資料不足", "detail": "缺進場價或現價"}
    ret = (px / e - 1) * 100
    stop, tp, hold = _f(h.get("stop_pct")), _f(h.get("tp")), h.get("hold")
    lo, hi = _f(lo_since), _f(hi_since)
    detail = [f"進場 {e:g}、現價 {px:g}（{ret:+.1f}%）"]
    if days_held is not None and hold:
        detail.append(f"第 {days_held}/{hold} 個交易日")
    act = "續抱"
    if stop and ((lo is not None and lo <= e * (1 - stop / 100)) or px <= e * (1 - stop / 100)):
        act = f"賣出：已跌破停損 {e * (1 - stop / 100):.2f}（−{stop:g}%）"
    elif tp and ((hi is not None and hi >= e * (1 + tp / 100)) or px >= e * (1 + tp / 100)):
        act = f"賣出：已達停利 {e * (1 + tp / 100):.2f}（+{tp:g}%）"
    elif hold and days_held is not None and days_held >= hold:
        act = f"賣出：持有已滿 {hold} 日（今天 13:20 賣）"
    else:
        if stop:
            detail.append(f"停損 {e * (1 - stop / 100):.2f}")
        if tp:
            detail.append(f"停利 {e * (1 + tp / 100):.2f}")
        if h.get("source") == "持倉" and vwap1230 and def_line and px < def_line and px < vwap1230:
            act = "留意：跌破戰卡防守線且 12:30 在均價下（參考，未回測）"
    return {"symbol": h.get("symbol"), "action": act, "detail": "，".join(detail), "ret": round(ret, 2)}


def message(run_date, mode, cards, holds, name_of=None, market=None):
    """mode：prep（12:33 預備）／final（13:17 定版）。"""
    nm = name_of or {}
    lab = lambda s: f"{nm.get(s, '')}({s})" if nm.get(s) else str(s)  # noqa: E731
    title = "🧭 12:33 尾盤預備戰卡（13:17 會再確認一次）" if mode == "prep" else "🎯 13:20 尾盤定版戰卡"
    lines = [f"{title}｜{run_date}"]
    if market:
        lines.append(market)
    buys = [c for c in cards if c.get("decision") in ("buy", "small")]
    skips = [c for c in cards if c.get("decision") == "skip"]
    for c in buys:
        tag = "✅ 買" if c["decision"] == "buy" else "🟡 小量"
        lines.append(f"{tag} {lab(c['symbol'])}｜{c['source']}{('·' + c['track']) if c.get('track') else ''}")
        lines.append(f"   依據：{c.get('basis', '')}")
        lines.append(f"   進場：{c.get('entry', '')}")
        if c.get("stop_px"):
            lines.append(f"   停損：{c['stop_px']:g}（−{c['stop_pct']:g}%）" + (f"｜停利 {c['tp_px']:g}（+{c['tp']:g}%）" if c.get("tp_px") else "｜不設停利"))
        else:
            lines.append("   停損：不設（見下方說明）｜不設停利")
        lines.append(f"   隔天：{c.get('next_day', '')}")
        for w in c.get("why") or []:
            lines.append(f"   {w}")
    if not buys:
        lines.append("今天沒有符合條件的做多標的。")
    for c in skips[:12]:
        lines.append(f"❌ 不買 {lab(c['symbol'])}｜{c['source']}｜{'；'.join(c.get('why') or [])[:120]}")
    if holds:
        lines.append("📦 持有中（明天動作）：")
        for h in holds:
            lines.append(f"・{lab(h['symbol'])}｜{h['action']}｜{h.get('detail', '')}")
    lines.append(NEXT_OPEN_NOTE)
    lines.append("只是提示，不下單。")
    return "\n".join(lines)
