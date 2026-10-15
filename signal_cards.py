"""【2026-10-13 第 3 項】即時訊號卡（純函式，不連網、不寫庫）。

資料來源（已查證）：
- intraday_5min_bars：5 分 K（symbol, trade_date, bar_time, open, high, low, close, volume）。
  outer_volume／inner_volume（內外盤）只有少數 K 棒有值，近三個交易日全為 0 → 不可用於多空力道。
- intraday_gate_results：三關判讀（gate1_verdict、direction=long/short、overall_label、detail）。
  注意：direction 是盤前候選池的「波段強弱方向」（候選來源），gate1_verdict 才是盤中實際走勢判讀；
  兩者可以不一致（查證：近 7 日 long 候選中有 15 筆第一關為空方判讀），因此卡片分開標示。

多空力道的替代算法（死規則 2：A 不行找 B）：K 線法推算。
每根 5 分 K：收 > 開 記 +量、收 < 開 記 −量、平盤記 0；加總即「多空力道」。
這是推算，不是內外盤；卡片上會標示「K線推算」。
"""

G1_LABELS = {
    "strong_bull": "強勢多方",
    "weak_bull": "弱勢多方",
    "unclear": "方向不明",
    "weak_bear": "弱勢空方",
    "strong_bear": "強勢空方",
    "stale": "資料過期",
    "unknown": "判讀未知",
}
G1_SIDE = {"strong_bull": "long", "weak_bull": "long", "strong_bear": "short", "weak_bear": "short"}
DIRECTION_LABELS = {"long": "多方", "short": "空方"}
G2_LABELS = {"pass": "通過", "fail": "不合格", "unknown": "資料不足", "not_applicable": "不適用（本身即龍頭）"}


def power_series(bars):
    """回傳 [(bar_time, power)]；power = ±volume（收>開為正、收<開為負、平盤為 0）。"""
    out = []
    for b in sorted(bars, key=lambda x: str(x.get("bar_time", ""))):
        o, c, v = b.get("open"), b.get("close"), float(b.get("volume") or 0)
        if o is None or c is None:
            continue
        sign = 1 if c > o else (-1 if c < o else 0)
        out.append((str(b.get("bar_time", "")), sign * v))
    return out


def build_card(symbol, name, bars, gate=None, tags=()):
    """回傳卡片 dict。bars 為當日 5 分 K（可為空）；gate 為當日最新一筆三關判讀（可為 None）；tags 如 ('持倉','雷達')。"""
    tags = list(tags or [])
    card = {"symbol": symbol, "name": name or "", "tags": tags, "empty": True,
            "time": None, "last": None, "change_pct": None, "chips": [],
            "closes": [], "power": [], "power_ratio": None, "side": None,
            "g1": None, "g2": None, "g1_label": None, "gate_label": None, "detail": None}
    bs = [b for b in (bars or []) if b.get("close") is not None and b.get("open") is not None]
    bs.sort(key=lambda x: str(x.get("bar_time", "")))
    if bs:
        first, last = bs[0], bs[-1]
        card["empty"] = False
        card["time"] = str(last.get("bar_time", ""))
        card["last"] = float(last["close"])
        if float(first["open"]) > 0:
            card["change_pct"] = round((float(last["close"]) / float(first["open"]) - 1) * 100, 2)
        card["closes"] = [(str(b.get("bar_time", "")), float(b["close"])) for b in bs]
        pw = power_series(bs)
        card["power"] = pw
        total_vol = sum(float(b.get("volume") or 0) for b in bs)
        net = sum(p for _, p in pw)
        card["power_ratio"] = round(net / total_vol, 3) if total_vol > 0 else None

    if gate:
        g1 = gate.get("gate1_verdict")
        card["g1"] = g1
        card["g2"] = gate.get("gate2_verdict")
        card["g1_label"] = G1_LABELS.get(g1, g1 or "未知")
        card["side"] = gate.get("direction") if gate.get("direction") in DIRECTION_LABELS else G1_SIDE.get(g1)
        card["gate_label"] = gate.get("overall_label") or ""
        card["detail"] = gate.get("detail")
    chips = []
    if card["side"] in DIRECTION_LABELS:
        chips.append(("候選方向", DIRECTION_LABELS[card["side"]]))
    if card["g1_label"]:
        chips.append(("第一關", card["g1_label"]))
    if card["g2"] in G2_LABELS:
        chips.append(("第二關", G2_LABELS[card["g2"]]))
    chips += [("標記", t) for t in tags]
    card["chips"] = chips
    return card


def filter_cards(cards, side=None, g1_group=None, tag=None):
    """side：'long'／'short'／None＝全部；g1_group：第一關判讀代碼（如 'strong_bull'）或 None；tag：'持倉'／'雷達'／'觀察'／None。
    結果依時間新到舊排序（沒有時間的排最後）。"""
    out = []
    for c in cards:
        if side and c.get("side") != side:
            continue
        if g1_group and c.get("g1") != g1_group:
            continue
        if tag and tag not in c.get("tags", []):
            continue
        out.append(c)
    out.sort(key=lambda c: c.get("time") or "", reverse=True)
    return out
