"""【2026-10-17 老闆：每天篩出太多股票、無法集中火力 → 交叉比對濃縮】盤前主戰清單 → 10:00 修正。

設計（依目前回測證據，標註樣本數與限制見專案文件《查指令新規則回測與最佳方案_20261017》）：
- 三條軌道，各自只留前幾名，總數 ≤ 10 檔：
  1. 波段主力（最多 5 檔）：查3∧查2（5 年回測 10 日超額 +1.08%、樣本內外一致）。排序＝沒有⚠️追高警示優先 →
     有效標籤數（取最新 cmd_deep_bt _focus 的 good_tags_IS；沒有就用預設）→ 價值分。
  2. 短線動能（最多 3 檔）：主力偵測「週轉率雙訊號（2 個型態以上）∧ 營收年增>20%」，且前晚評分沒有落在空方（>−6）。
     近 40 天實盤 5 日超額 +2.2%（t 4.1，31 天、單一多頭盤勢，需 5 年回測 m2/m3 確認）。排序＝創 20 日新高、法人連買、週轉率。
  3. 空方觀察（最多 2 檔）：查8（昨日強勢＋今天續漲＝隔日沖賣壓）且前晚評分 ≤ 0，依評分由低到高。
- 10:00 修正：多方看「多方進場提示」（long_entry_hint，型態由盤中研究 part_f 決定）；空方看已驗證的空方第三關
  （10:00 收盤＜開盤；候選上 t≈±2.4）。不合格剔除、資料不足保留但標註。
純函式、可離線測試。"""
from __future__ import annotations

import re

DEFAULT_GOOD_TAGS = ("vs70", "hi20", "fb3", "tb")
TAG_FROM_CMD = {"查1.": "c1", "查9.": "c9", "查6.": "c6v", "查4.": "c4v"}
N_SWING, N_MOM, N_SHORT = 5, 3, 2


def _pfx(c):
    m = re.match(r"\s*(查\s*\d+)\s*\.?", str(c))
    return (m.group(1).replace(" ", "") + ".") if m else None


def card_tags(matched, card):
    """把隔夜掃描的命中指令＋卡片摘要轉成 cmd_deep_bt.FOCUS_TAGS 同名的標籤集合。"""
    card = card or {}
    t = {TAG_FROM_CMD[p] for p in (_pfx(c) for c in (matched or [])) if p in TAG_FROM_CMD}
    vs = card.get("value_score")
    if vs is not None and float(vs) >= 70:
        t.add("vs70")
    if card.get("hi20_break"):
        t.add("hi20")
    if (card.get("f_buy") or 0) > 0:
        t.add("fb3")
    if (card.get("t_buy") or 0) > 0:
        t.add("tb")
    n = card.get("near_ma20_pct")
    if n is not None and 0 <= float(n) <= 2:
        t.add("near2")
    if "金叉" in str(card.get("kdj_str") or ""):
        t.add("kgold")
    if card.get("is_red") or (card.get("gain") or 0) > 0:
        t.add("red")
    return t


def rank_swing(rows, good_tags=None, n=N_SWING):
    """rows：overnight_scan_results 列（symbol, matched_commands, score, card_snapshot）。只取查3∧查2。"""
    good = set(good_tags or DEFAULT_GOOD_TAGS) - {"c1", "c9"}   # 查1/查9 是追高警示，不當加分
    out = []
    for r in rows or []:
        ps = {_pfx(c) for c in (r.get("matched_commands") or [])}
        if not ("查2." in ps and "查3." in ps):
            continue
        card = r.get("card_snapshot") or {}
        tags = card_tags(r.get("matched_commands"), card)
        warn = bool(ps & {"查1.", "查9.", "查8."})
        hit = sorted(tags & good)
        out.append({"symbol": r["symbol"], "track": "波段主力", "direction": "long",
                    "rank": (0 if warn else 1, len(hit), float(card.get("value_score") or 0)),
                    "why": "查3∧查2" + ("｜加分：" + "、".join(hit) if hit else "") + ("｜⚠️追高警示" if warn else "")})
    out.sort(key=lambda x: x["rank"], reverse=True)
    return out[:n]


def rank_momentum(smart_rows, score_map=None, exclude=(), n=N_MOM):
    """smart_money_candidates 列：patterns, rev_yoy, broke_20d_high, foreign_streak, trust_streak, turnover_pct。"""
    score_map = score_map or {}
    out = []
    for r in smart_rows or []:
        sym = r.get("symbol")
        if not sym or sym in exclude:
            continue
        np_ = len(r.get("patterns") or [])
        yoy = r.get("rev_yoy")
        sc = score_map.get(sym)
        if np_ < 2 or yoy is None or float(yoy) <= 20 or (sc is not None and float(sc) <= -6):
            continue
        inst = (r.get("foreign_streak") or 0) >= 3 or (r.get("trust_streak") or 0) >= 3
        b20 = bool(r.get("broke_20d_high"))
        out.append({"symbol": sym, "track": "短線動能", "direction": "long",
                    "rank": (int(b20) + int(inst), float(r.get("turnover_pct") or 0)),
                    "why": f"主力偵測{np_}訊號＋營收年增{float(yoy):.0f}%" + ("｜創20日新高" if b20 else "") + ("｜法人連買" if inst else "")})
    out.sort(key=lambda x: x["rank"], reverse=True)
    return out[:n]


def rank_short(rows, n=N_SHORT, exclude=()):
    out = []
    for r in rows or []:
        ps = {_pfx(c) for c in (r.get("matched_commands") or [])}
        sc = r.get("score")
        if "查8." not in ps or r.get("symbol") in exclude or sc is None or float(sc) > 0:
            continue
        out.append({"symbol": r["symbol"], "track": "空方觀察", "direction": "short", "rank": (-float(sc),),
                    "why": f"查8隔日沖賣壓＋評分{float(sc):g}"})
    out.sort(key=lambda x: x["rank"], reverse=True)
    return out[:n]


def build_preopen(scan_rows, smart_rows, score_map=None, good_tags=None):
    sw = rank_swing(scan_rows, good_tags)
    mo = rank_momentum(smart_rows, score_map, exclude={x["symbol"] for x in sw})
    sh = rank_short(scan_rows, exclude={x["symbol"] for x in sw + mo})
    lst = sw + mo + sh
    for x in lst:
        x.pop("rank", None)
    return lst


def long_entry_hint(bars5, prev_close=None):
    """多方 10:00 進場提示（暫定：10:00 前不破開盤 0.5% 且 10:00 收在開盤上；盤中研究 part_f 結果出爐後依實證調整）。
    bars5：當天 5 分K list[dict(bar_time 起始時間, open, high, low, close)]。回傳 verdict pass/fail/unknown 與說明。"""
    bs = sorted([b for b in (bars5 or []) if str(b.get("bar_time", "")) < "10:00"], key=lambda b: b["bar_time"])
    if len(bs) < 10 or bs[0].get("bar_time") != "09:00":
        return {"verdict": "unknown", "label": "10:00 前 5 分K 不足，無法判斷"}
    o = float(bs[0]["open"])
    lo = min(float(b["low"]) for b in bs)
    c = float(bs[-1]["close"])
    if lo >= o * 0.995 and c > o:
        return {"verdict": "pass", "label": f"10:00 前守住開盤（最低 {lo:g}≥開盤 {o:g}×0.995）且收在開盤上"}
    return {"verdict": "fail", "label": ("10:00 前跌破開盤" if lo < o * 0.995 else "10:00 收在開盤下") + "，多方進場提示不成立"}


def revise_at_10(preopen, gate_rows=None, long_hints=None):
    """preopen：build_preopen 結果；gate_rows：intraday_gate_results 當天列（symbol, direction, gate3_verdict, overall_verdict）；
    long_hints：{symbol: long_entry_hint 結果}。回傳 keep / drop 兩個 list（每筆附 reason）。"""
    g = {(r.get("symbol"), r.get("direction")): r for r in (gate_rows or [])}
    long_hints = long_hints or {}
    keep, drop = [], []
    for x in preopen or []:
        x = dict(x)
        if x["direction"] == "short":
            r = g.get((x["symbol"], "short")) or {}
            v = r.get("gate3_verdict")
            if v == "fail" or r.get("overall_verdict") == "fail":
                x["reason"] = "空方第三關不合格（10:00 已收復開盤，反彈有力）" if v == "fail" else (r.get("overall_label") or "空方三關不合格")
                drop.append(x)
            else:
                x["reason"] = "空方第三關合格（10:00 收盤＜開盤）" if v == "pass" else "空方第三關資料不足（保留、人工確認）"
                keep.append(x)
        else:
            h = long_hints.get(x["symbol"]) or {"verdict": "unknown", "label": "10:00 前資料不足（保留、人工確認）"}
            x["reason"] = h["label"]
            (drop if h["verdict"] == "fail" else keep).append(x)
    return {"keep": keep, "drop": drop}
