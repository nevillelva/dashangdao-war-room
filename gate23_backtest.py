"""【2026-10-17】三關第二關（跟龍頭比）、第三關（多方拉回體檢／空方反彈無力）回測。
重建方式與盤中 10:00 收尾相同：當天 5 分K（起始時間標記）餵給 warroom_core.evaluate_930_three_gate(end_label=True)，
龍頭用同一套產業龍頭對照；空方第三關另以 evaluate_short_gate3_rebound（09:00~10:00）判斷。
報酬：以 10:00 價（起始 09:55 那根收盤）進場 → 當日收盤、次日收盤；超額＝扣同日全部樣本平均；t 值以「日」計。
每個股票日同時以「多方」「空方」各判一次（無條件版），另可給前一晚候選（評分≥6／≤−6）做條件版。
限制：1 分K 合成；空方防接刀（日K位置）未納入；不含成本；母體＝成交值前 N 檔。純函式，可離線測試。"""
from __future__ import annotations
from statistics import mean, pstdev


def to_dicts(bars5):
    """MB.resample(…,5) 一天的輸出 → evaluate_930_three_gate 要的 list[dict]（bar_time＝起始時間）。"""
    out = []
    for b in bars5:
        s = b["end"].hour * 60 + b["end"].minute - 5
        out.append({"bar_time": f"{s // 60:02d}:{s % 60:02d}", "open": b["o"], "high": b["h"], "low": b["l"],
                    "close": b["c"], "volume": b["v"]})
    return out


def split_days(bars5):
    d = {}
    for b in bars5:
        d.setdefault(b["date"], []).append(b)
    return d


def _t(pairs):
    byd = {}
    for d, x in pairs:
        byd.setdefault(d, []).append(x)
    dm = [mean(v) for v in byd.values()]
    if len(dm) < 3:
        return None
    sd = pstdev(dm) or 1e-9
    return round(mean(dm) / sd * len(dm) ** 0.5, 2)


def _st(sub, key):
    xs = [(r["date"], r[key]) for r in sub if r.get(key) is not None]
    if not xs:
        return {"n": 0}
    v = [x for _, x in xs]
    return {"n": len(v), "ex": round(mean(v), 4), "win": round(100 * sum(1 for x in v if x > 0) / len(v), 1), "t": _t(xs)}


def build(per, leader_of, self_leader, three_gate_fn, short_g3_fn, cand=None):
    """per：{sym: resample5 list}。回傳列：date, sym, side, g1, g2, g3, overall, sg3, ex_eod, ex_nd。"""
    days_of = {s: split_days(v) for s, v in per.items()}
    rows = []
    for sym, dd in days_of.items():
        ds = sorted(dd)
        for k, d in enumerate(ds):
            bars = [b for b in dd[d] if (b["end"].hour * 60 + b["end"].minute) <= 10 * 60]
            if len(bars) < 12 or bars[0]["end"].hour * 60 + bars[0]["end"].minute != 9 * 60 + 5:
                continue
            entry = bars[-1]["c"]
            close = dd[d][-1]["c"]
            nd = dd[ds[k + 1]][-1]["c"] if k + 1 < len(ds) else None
            sb = to_dicts(bars)
            ld = leader_of.get(sym)
            lb = None
            if ld and ld in days_of and d in days_of[ld]:
                lb = to_dicts([b for b in days_of[ld][d] if (b["end"].hour * 60 + b["end"].minute) <= 10 * 60])
            sides = ("long", "short") if cand is None else ((cand.get((sym, d)),) if cand.get((sym, d)) else ())
            for side in sides:
                try:
                    v = three_gate_fn(sb, lb, direction=side, stock_day_open=bars[0]["o"],
                                      leader_day_open=(lb[0]["open"] if lb else None),
                                      self_is_leader=(sym in self_leader and not ld), end_label=True)
                except Exception:  # noqa: BLE001
                    continue
                g = lambda x: (v.get(x) or {}).get("verdict") if v.get(x) else None  # noqa: E731
                sg3 = short_g3_fn(sb).get("verdict") if side == "short" else None
                rows.append({"date": d, "sym": sym, "side": side, "g1": g("gate1"), "g2": g("gate2"), "g3": g("gate3"),
                             "overall": v.get("overall_verdict"), "sg3": sg3,
                             "eod": (close / entry - 1) * 100, "nd": ((nd / entry - 1) * 100) if nd else None})
    base_e, base_n = {}, {}
    for r in rows:
        if r["side"] == "long" or cand is not None:
            base_e.setdefault(r["date"], []).append(r["eod"])
            if r["nd"] is not None:
                base_n.setdefault(r["date"], []).append(r["nd"])
    be = {d: mean(v) for d, v in base_e.items()}
    bn = {d: mean(v) for d, v in base_n.items()}
    for r in rows:
        r["ex_eod"] = r["eod"] - be.get(r["date"], 0.0)
        r["ex_nd"] = (r["nd"] - bn[r["date"]]) if (r["nd"] is not None and r["date"] in bn) else None
    return rows


def analyze(rows):
    out = {}
    for side in ("long", "short"):
        s = [r for r in rows if r["side"] == side]
        o = {"n": len(s)}
        for key in ("g2", "g3", "overall") + (("sg3",) if side == "short" else ()):
            vals = sorted({str(r[key]) for r in s})
            o[key] = {v: {"eod": _st([r for r in s if str(r[key]) == v], "ex_eod"),
                          "nd": _st([r for r in s if str(r[key]) == v], "ex_nd")} for v in vals}
        # 第一關已過（多方：非偏空；空方：偏空）之後，第二關合格 vs 不合格
        if side == "long":
            s1 = [r for r in s if r["g1"] not in ("strong_bear", "weak_bear", "unknown", "stale", None)]
        else:
            s1 = [r for r in s if r["g1"] in ("strong_bear", "weak_bear")]
        o["after_g1"] = {v: {"eod": _st([r for r in s1 if str(r["g2"]) == v], "ex_eod"),
                             "nd": _st([r for r in s1 if str(r["g2"]) == v], "ex_nd")} for v in sorted({str(r["g2"]) for r in s1})}
        out[side] = o
    return out
