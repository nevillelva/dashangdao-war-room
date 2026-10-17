"""【2026-10-17】盤中研究（永豐 1 分K）：
A. 多方盤中條件能不能像空方第三關一樣有效：10:00 判斷的幾種替代條件 → 以系統實際的尾盤 13:20 進場，看隔日收盤／3 日後收盤超額。
B. 查8（隔日沖型）買賣時點網格：第1天 13:15 漲>5% 時買進，第2／3天各時點賣出；第3天各時點放空、當沖回補（含 5% 停損，用 1 分K 實際判斷）。
C. 時段驗證：「9:30 前沖高殺低、10~11 點亂流結束、尾盤 13:15 進場」——各時段波動大小、開盤 30 分鐘的走勢之後是延續還是反轉。
成本：做多來回 0.585%（手續費 0.285%＋證交稅 0.3%）、當沖放空 0.435%（證交稅減半）。超額＝扣同日全部樣本平均。
限制：約 1 年資料、同一段盤勢；以 1 分K 收盤價當成交價（實際有滑價）；查8 母體偏向波動大的股票；純函式，可離線測試。"""
from __future__ import annotations
from statistics import mean, median, pstdev

COST_LONG, COST_DT = 0.585, 0.435
TIMES = ("09:05", "09:30", "10:00", "11:00", "13:25")


def _hm(t):
    return t.hour * 60 + t.minute


def day_map(bars1):
    """1 分K list[(start_dt,o,h,l,c,v)] → {date: list}。"""
    d = {}
    for b in bars1:
        d.setdefault(b[0].date(), []).append(b)
    return d


def price_at(day, hhmm):
    """該時點之前最後一根 1 分K 的收盤（例如 '10:00' → 09:59 那根）。"""
    lim = int(hhmm[:2]) * 60 + int(hhmm[3:])
    last = None
    for b in day:
        if _hm(b[0]) < lim:
            last = b[4]
        else:
            break
    return last


def day_stats(day):
    o = day[0][1]
    c = day[-1][4]
    first30 = [b for b in day if _hm(b[0]) < 9 * 60 + 30]
    to10 = [b for b in day if _hm(b[0]) < 10 * 60]
    s = {"open": o, "close": c, "first_hm": _hm(day[0][0])}
    if first30:
        s["or_high"] = max(b[2] for b in first30)
        s["vol_0930"] = sum(b[5] for b in first30)
    if to10:
        s["hi10"] = max(b[2] for b in to10)
        pv = sum(b[4] * b[5] for b in to10)
        vv = sum(b[5] for b in to10)
        s["vwap10"] = pv / vv if vv else None
        s["vol_1000"] = sum(b[5] for b in to10 if _hm(b[0]) >= 9 * 60 + 30)
    for t in ("09:05", "09:30", "10:00", "11:00", "13:15", "13:20", "13:25"):
        s["p" + t.replace(":", "")] = price_at(day, t)
    return s


def _st(xs):
    if not xs:
        return {"n": 0}
    return {"n": len(xs), "mean": round(mean(xs), 3), "win": round(100 * sum(1 for x in xs if x > 0) / len(xs), 1),
            "median": round(median(xs), 3)}


def _tday(pairs):
    byd = {}
    for d, x in pairs:
        byd.setdefault(d, []).append(x)
    dm = [mean(v) for v in byd.values()]
    if len(dm) < 5:
        return None
    sd = pstdev(dm) or 1e-9
    return round(mean(dm) / sd * len(dm) ** 0.5, 2)


def part_a(per_days):
    """per_days：{sym: {date: day_stats}}（只用一般母體）。回傳各條件的超額。"""
    rows = []
    for sym, dd in per_days.items():
        ds = sorted(dd)
        for k, d in enumerate(ds[:-3]):
            s = dd[d]
            if s.get("first_hm") != 9 * 60 or not s.get("p1000") or not s.get("p1320") or not s.get("or_high"):
                continue
            n1, n3 = dd[ds[k + 1]]["close"], dd[ds[k + 3]]["close"]
            r = {"date": d, "sym": sym, "ret10": (s["p1000"] / s["open"] - 1) * 100,
                 "nd": (n1 / s["p1320"] - 1) * 100, "d3": (n3 / s["p1320"] - 1) * 100,
                 "eod10": (s["close"] / s["p1000"] - 1) * 100,
                 "orb": s["p1000"] > s["or_high"], "vwap": bool(s.get("vwap10") and s["p1000"] > s["vwap10"]),
                 "up": s["p1000"] > s["open"]}
            rng = s["hi10"] - s["open"]
            r["shallow"] = bool(r["up"] and rng > 0 and (s["hi10"] - s["p1000"]) / rng < 1 / 3)
            r["volfade"] = bool(r["up"] and s.get("vol_0930") and s.get("vol_1000") is not None and s["vol_1000"] < s["vol_0930"] * 0.6)
            rows.append(r)
    byd = {}
    for r in rows:
        byd.setdefault(r["date"], []).append(r)
    for d, rs in byd.items():
        rets = sorted(x["ret10"] for x in rs)
        med = rets[len(rets) // 2]
        top = rets[int(len(rets) * 0.7)] if len(rets) >= 5 else None
        mnd, md3, meod = mean(x["nd"] for x in rs), mean(x["d3"] for x in rs), mean(x["eod10"] for x in rs)
        for x in rs:
            x["rs_pos"] = x["ret10"] > med
            x["rs_top"] = top is not None and x["ret10"] >= top
            x["x_nd"], x["x_d3"], x["x_eod"] = x["nd"] - mnd, x["d3"] - md3, x["eod10"] - meod
    out = {"n_rows": len(rows)}
    conds = {"orb": "10:00 價突破 9:00~9:30 高點", "vwap": "10:00 價在均價線之上", "rs_pos": "開盤到 10:00 漲幅強於當日中位數",
             "rs_top": "開盤到 10:00 漲幅在當日前 30%", "shallow": "上漲中拉回不到漲幅 1/3", "volfade": "上漲且 9:30~10:00 量縮到前 30 分 6 成以下",
             "orb+rs_top": "突破開盤高點＋前 30% 強勢", "vwap+shallow": "均價線上＋淺拉回"}
    for key, label in conds.items():
        parts = key.split("+")
        yes = [r for r in rows if all(r.get(p) for p in parts)]
        no = [r for r in rows if not all(r.get(p) for p in parts)]
        out[key] = {"label": label}
        for tag, sub in (("yes", yes), ("no", no)):
            out[key][tag] = {"n": len(sub),
                             "x_eod_from10": round(mean(r["x_eod"] for r in sub), 3) if sub else None,
                             "x_nd_from1320": round(mean(r["x_nd"] for r in sub), 3) if sub else None,
                             "x_d3_from1320": round(mean(r["x_d3"] for r in sub), 3) if sub else None,
                             "t_nd": _tday([(r["date"], r["x_nd"]) for r in sub]),
                             "t_d3": _tday([(r["date"], r["x_d3"]) for r in sub])}
    return out


def part_c(per_days_raw):
    """時段驗證：per_days_raw {sym: {date: [1分K]}}。各 30 分鐘時段平均絕對報酬；開盤 30 分鐘漲跌之後到收盤的走勢。"""
    buckets = {}
    after = {"up_big": [], "down_big": [], "flat": []}
    hour10_11 = {"up": [], "down": []}
    for sym, dd in per_days_raw.items():
        for d, day in dd.items():
            if not day or _hm(day[0][0]) != 9 * 60:
                continue
            for b in day:
                key = f"{_hm(b[0]) // 30 * 30 // 60:02d}:{_hm(b[0]) // 30 * 30 % 60:02d}"
                if b[1] > 0:
                    buckets.setdefault(key, []).append(abs(b[4] / b[1] - 1) * 100)
            o, p930, p1000, p1100, c = day[0][1], price_at(day, "09:30"), price_at(day, "10:00"), price_at(day, "11:00"), day[-1][4]
            if not (o and p930 and p1100):
                continue
            m = (p930 / o - 1) * 100
            rest = (c / p930 - 1) * 100
            after["up_big" if m >= 1 else "down_big" if m <= -1 else "flat"].append(rest)
            if p1000:
                h = (p1100 / p1000 - 1) * 100
                if abs(h) >= 0.5:
                    hour10_11["up" if h > 0 else "down"].append((c / p1100 - 1) * 100)
    return {"abs_ret_by_30min": {k: round(mean(v), 4) for k, v in sorted(buckets.items())},
            "after_first30_to_close": {k: _st(v) for k, v in after.items()},
            "after_10_11_to_close": {k: _st(v) for k, v in hour10_11.items()}}


def part_b(per_days, per_days_raw, stop_pct=5.0):
    """查8 時點網格。per_days：{sym:{date:day_stats}}；per_days_raw：{sym:{date:[1分K]}}（算停損用）。"""
    longs = {}
    shorts = {}
    combo = {}
    for sym, dd in per_days.items():
        ds = sorted(dd)
        for k in range(1, len(ds) - 2):
            d0, d1, d2, d3 = ds[k - 1], ds[k], ds[k + 1], ds[k + 2]
            s0, s1, s2, s3 = dd[d0], dd[d1], dd[d2], dd[d3]
            pc = s0["close"]
            if not (pc and s1.get("p1315") and s1["p1315"] / pc - 1 > 0.05):
                continue
            e = s1["p1315"]
            cont = s2["close"] > s1["close"]
            # 13:15 已接近漲停（≥+9.4%）時實際很難買到（排隊鎖單），分開統計「買得到」的 +5%~+9.4%
            near_lu = s1["p1315"] / pc - 1 >= 0.094
            tag_lu = "（13:15未達漲停）" if not near_lu else "（13:15已近漲停，可能買不到）"
            for t in ("09:05", "09:30", "13:25"):
                for day_tag, s in (("D2", s2), ("D3", s3)):
                    p = s.get("p" + t.replace(":", "")) if t != "13:25" else s["close"]
                    if p:
                        longs.setdefault(f"買D1_13:15{tag_lu}→賣{day_tag}_{t}", []).append((d1, (p / e - 1) * 100 - COST_LONG))
            for day_tag, s in (("D2", s2), ("D3", s3)):
                for t in TIMES:
                    p = s.get("p" + t.replace(":", "")) if t != "13:25" else s["close"]
                    if p:
                        longs.setdefault(f"買D1_13:15→賣{day_tag}_{t}", []).append((d1, (p / e - 1) * 100 - COST_LONG))
                        if day_tag == "D3" and cont:
                            longs.setdefault(f"買D1_13:15→（D2續漲才續抱）賣D3_{t}", []).append((d1, (p / e - 1) * 100 - COST_LONG))
            # 策略：D2 收盤沒續漲就 D2 收盤賣；有續漲抱到 D3 09:30
            p_exit = (s3.get("p0930") if cont else s2["close"])
            if p_exit:
                combo.setdefault("買D1_13:15；D2沒續漲收盤賣、有續漲抱到D3_09:30", []).append((d1, (p_exit / e - 1) * 100 - COST_LONG))
            # D2 當沖空（典型隔日沖倒貨）
            raw2 = per_days_raw.get(sym, {}).get(d2, [])
            for t in ("09:05", "09:30", "10:00", "11:00"):
                ent = s2.get("p" + t.replace(":", ""))
                if ent:
                    shorts.setdefault(f"空D2_{t}", []).append((d2, _short_ret(raw2, t, ent, s2["close"], stop_pct)))
            if cont:
                raw3 = per_days_raw.get(sym, {}).get(d3, [])
                gap = s3["open"] > s2["close"] * 1.01
                for t in ("09:05", "09:30", "10:00", "11:00"):
                    ent = s3.get("p" + t.replace(":", ""))
                    if ent:
                        r = _short_ret(raw3, t, ent, s3["close"], stop_pct)
                        shorts.setdefault(f"空D3_{t}", []).append((d3, r))
                        if gap:
                            shorts.setdefault(f"空D3_{t}（D3開高>1%）", []).append((d3, r))
    def pack(dct):
        out = {}
        for k, v in dct.items():
            xs = [x for _, x in v]
            dates = sorted(d for d, _ in v)
            mid = dates[len(dates) // 2] if dates else None
            out[k] = {**_st(xs), "first_half": _st([x for d, x in v if d < mid]), "second_half": _st([x for d, x in v if d >= mid])}
        return out
    return {"long": pack(longs), "combo": pack(combo), "short": pack(shorts)}


def _short_ret(day, t, entry, close, stop_pct):
    """當沖放空：t 之後若 1 分K 最高價碰到 entry×(1+stop) 就以停損出場，否則收盤回補。扣當沖成本。"""
    lim = int(t[:2]) * 60 + int(t[3:])
    stop = entry * (1 + stop_pct / 100)
    for b in day:
        if _hm(b[0]) >= lim and b[2] >= stop:
            return -stop_pct - COST_DT
    return (entry - close) / entry * 100 - COST_DT
