"""【2026-10-16】第一關「只看單根 K」回測。
第一關（warroom_core.evaluate_930_gate1，快照模式 end_label=True）只用「09:20 起、09:25 起」兩根 5 分K（＝資料到 09:30）：
最後一根的實體占比、量比、是否跌破前兩根低點，分成強勢多方／弱勢多方／多空不明／弱勢空方／強勢空方。
本模組檢查：①各判定之後（09:30 價 → 當日收盤、→ 次日收盤）是否真有方向性；②和簡單的「開盤→09:30 累計漲跌」比較；
③兩者互相矛盾的情形（例：單根判空、但開盤至今仍明顯上漲）後來誰對。
超額＝個股報酬 − 同日全部樣本平均；另以「日」為單位取平均再算 t（避免同日多檔不獨立被高估）。
限制：基礎資料為永豐 1 分K 合成 5 分K（與盤中即時快照的組法略有差異）；期間有限、同一段盤勢；不含成本（來回約 0.585%）；母體偏大型高成交值股。
純函式、不碰網路，可離線測試。gate_fn(b25, b30) → verdict 字串，由呼叫端注入（實戰＝warroom_core.evaluate_930_gate1）。"""
from __future__ import annotations
from statistics import mean, pstdev

BULL = ("strong_bull", "weak_bull")
BEAR = ("strong_bear", "weak_bear")
CUM_THR = 0.3   # 開盤→09:30 累計漲跌 ±0.3% 以上才算「有方向」
CONFLICT_THR = 0.5


def day_rows(per):
    """per: {sym: resample(5) 輸出 list}。回傳每檔每日 dict：date,sym,b25,b30,open,entry,close"""
    out = []
    for sym, bars in per.items():
        by_day = {}
        for b in bars:
            by_day.setdefault(b["date"], []).append(b)
        days = sorted(by_day)
        closes = {d: by_day[d][-1]["c"] for d in days}
        for k, d in enumerate(days):
            bs = {(x["end"].hour * 60 + x["end"].minute): x for x in by_day[d]}
            b25, b30 = bs.get(9 * 60 + 25), bs.get(9 * 60 + 30)   # 結束時間 09:25、09:30 ＝ 起始 09:20、09:25
            first = by_day[d][0]
            if not b25 or not b30 or first["end"].hour * 60 + first["end"].minute != 9 * 60 + 5:
                continue
            entry = b30["c"]
            out.append({"date": d, "sym": sym, "b25": b25, "b30": b30, "open": first["o"], "entry": entry,
                        "eod": (closes[d] / entry - 1) * 100,
                        "nd": ((closes[days[k + 1]] / entry - 1) * 100) if k + 1 < len(days) else None,
                        "cum": (entry / first["o"] - 1) * 100})
    return out


def _t_by_day(items, key):
    """items: [(date, excess)]；日內先平均，再對「日」做 t。"""
    byd = {}
    for d, x in items:
        byd.setdefault(d, []).append(x)
    dm = [mean(v) for v in byd.values()]
    if len(dm) < 3:
        return None
    sd = pstdev(dm) or 1e-9
    return round(mean(dm) / sd * (len(dm) ** 0.5), 2)


def _stats(rows, ex_key):
    if not rows:
        return {"n": 0}
    ex = [r[ex_key] for r in rows if r.get(ex_key) is not None]
    if not ex:
        return {"n": 0}
    return {"n": len(ex), "excess": round(mean(ex), 4), "win": round(100.0 * sum(1 for x in ex if x > 0) / len(ex), 1),
            "t_day": _t_by_day([(r["date"], r[ex_key]) for r in rows if r.get(ex_key) is not None], ex_key),
            "days": len({r["date"] for r in rows})}


def analyze(per, gate_fn, split_date=None):
    rows = day_rows(per)
    for r in rows:
        r["verdict"] = gate_fn(r["b25"], r["b30"])
    # 同日基準
    base_e, base_n = {}, {}
    for r in rows:
        base_e.setdefault(r["date"], []).append(r["eod"])
        if r["nd"] is not None:
            base_n.setdefault(r["date"], []).append(r["nd"])
    be = {d: mean(v) for d, v in base_e.items()}
    bn = {d: mean(v) for d, v in base_n.items()}
    for r in rows:
        r["ex_eod"] = r["eod"] - be[r["date"]]
        r["ex_nd"] = (r["nd"] - bn[r["date"]]) if (r["nd"] is not None and r["date"] in bn) else None
        r["cum_dir"] = "up" if r["cum"] >= CUM_THR else ("down" if r["cum"] <= -CUM_THR else "flat")
        r["g_dir"] = "bull" if r["verdict"] in BULL else ("bear" if r["verdict"] in BEAR else "none")

    def pack(sub):
        return {"eod": _stats(sub, "ex_eod"), "nd": _stats(sub, "ex_nd")}

    out = {"n_rows": len(rows), "n_days": len(be), "n_syms": len({r["sym"] for r in rows}),
           "baseline_eod_mean": round(mean([r["eod"] for r in rows]), 4) if rows else None}
    out["by_verdict"] = {v: pack([r for r in rows if r["verdict"] == v])
                         for v in ("strong_bull", "weak_bull", "unclear", "weak_bear", "strong_bear", "unknown")}
    out["by_gate_dir"] = {d: pack([r for r in rows if r["g_dir"] == d]) for d in ("bull", "bear", "none")}
    out["by_cum_dir"] = {d: pack([r for r in rows if r["cum_dir"] == d]) for d in ("up", "flat", "down")}
    # 矛盾：單根判空但開盤至今 ≥ +CONFLICT_THR；單根判多但開盤至今 ≤ −CONFLICT_THR；以及一致的對照
    out["conflict"] = {
        "判空_但開盤至今漲": pack([r for r in rows if r["g_dir"] == "bear" and r["cum"] >= CONFLICT_THR]),
        "判多_但開盤至今跌": pack([r for r in rows if r["g_dir"] == "bull" and r["cum"] <= -CONFLICT_THR]),
        "判空_且開盤至今跌": pack([r for r in rows if r["g_dir"] == "bear" and r["cum"] <= -CONFLICT_THR]),
        "判多_且開盤至今漲": pack([r for r in rows if r["g_dir"] == "bull" and r["cum"] >= CONFLICT_THR]),
    }
    if split_date:
        for tag, sub in (("IS", [r for r in rows if r["date"].isoformat() < split_date]),
                         ("OOS", [r for r in rows if r["date"].isoformat() >= split_date])):
            out[f"split_{tag}"] = {"by_gate_dir": {d: pack([r for r in sub if r["g_dir"] == d]) for d in ("bull", "bear")},
                                   "by_cum_dir": {d: pack([r for r in sub if r["cum_dir"] == d]) for d in ("up", "down")}}
    return out


def analyze_candidates(per, gate_fn, cand):
    """候選條件版：cand={(sym, 訊號日 date): 'long'/'short'}（前一晚評分 ≥6／≤−6）。
    隔一個交易日套第一關，看判定 × 方向之後的超額（同日全樣本平均為基準）。
    為了直接回答「第一關否決有沒有用」：多方候選看 bull/none/bear 三組，空方候選同樣分三組；空方的「有利」＝超額為負。"""
    rows = day_rows(per)
    base_e, base_n = {}, {}
    for r in rows:
        base_e.setdefault(r["date"], []).append(r["eod"])
        if r["nd"] is not None:
            base_n.setdefault(r["date"], []).append(r["nd"])
    be = {d: mean(v) for d, v in base_e.items()}
    bn = {d: mean(v) for d, v in base_n.items()}
    by_sym_days = {}
    for r in rows:
        by_sym_days.setdefault(r["sym"], []).append(r["date"])
    nextday = {}
    for sym, ds in by_sym_days.items():
        ds = sorted(ds)
        for i in range(len(ds) - 1):
            nextday[(sym, ds[i])] = ds[i + 1]
    want = {}
    for (sym, d0), side in cand.items():
        d1 = nextday.get((sym, d0))
        if d1:
            want[(sym, d1)] = side
    out = {}
    sel = []
    for r in rows:
        side = want.get((r["sym"], r["date"]))
        if not side:
            continue
        v = gate_fn(r["b25"], r["b30"])
        r2 = dict(r)
        r2["side"] = side
        r2["g_dir"] = "bull" if v in BULL else ("bear" if v in BEAR else "none")
        r2["ex_eod"] = r["eod"] - be[r["date"]]
        r2["ex_nd"] = (r["nd"] - bn[r["date"]]) if (r["nd"] is not None and r["date"] in bn) else None
        sel.append(r2)
    for side in ("long", "short"):
        sub = [r for r in sel if r["side"] == side]
        out[side] = {g: {"eod": _stats([r for r in sub if r["g_dir"] == g], "ex_eod"),
                         "nd": _stats([r for r in sub if r["g_dir"] == g], "ex_nd")} for g in ("bull", "none", "bear")}
        out[side]["all"] = {"eod": _stats(sub, "ex_eod"), "nd": _stats(sub, "ex_nd")}
    return out
