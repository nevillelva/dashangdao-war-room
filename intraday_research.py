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
    # 【2026-10-17 v2】多方進場型態／隔日沖選股需要的欄位
    s["vol"] = sum(b[5] for b in day)
    first5 = [b for b in day if _hm(b[0]) < 9 * 60 + 5]
    if first5:
        s["or5_high"] = max(b[2] for b in first5)
        s["or5_up"] = first5[-1][4] > first5[0][1]
        s["vol5"] = sum(b[5] for b in first5)
    for lim, tag in ((10 * 60, "1000"), (11 * 60, "1100"), (13 * 60 + 15, "1315")):
        seg = [b for b in day if _hm(b[0]) < lim]
        if seg:
            s["lo" + tag] = min(b[3] for b in seg)
            s["hi" + tag] = max(b[2] for b in seg)
            vv = sum(b[5] for b in seg)
            s["vwap" + tag] = (sum(b[4] * b[5] for b in seg) / vv) if vv else None
            s["vol" + tag] = vv
            post = [b for b in seg if _hm(b[0]) >= 9 * 60 + 5]
            brk = next((b for b in post if first5 and b[2] > s["or5_high"]), None)
            s["orb5_" + tag] = brk is not None
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


def part_a(per_days, dates=None):
    """per_days：{sym: {date: day_stats}}（只用一般母體）。回傳各條件的超額。"""
    rows = []
    for sym, dd in per_days.items():
        ds = sorted(dd)
        for k, d in enumerate(ds[:-3]):
            if dates is not None and d not in dates:
                continue
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



# ───────────────────────── v2（2026-10-17）─────────────────────────
def _ctx(dd):
    """每檔每日的日K脈絡（只用當天以前的資料）：前收、MA20 是否上揚且前收在 MA20 上、前 5 日均量、前 14 日開盤 5 分鐘均量。"""
    ds = sorted(dd)
    closes = [dd[d]["close"] for d in ds]
    vols = [dd[d].get("vol") or 0 for d in ds]
    v5s = [dd[d].get("vol5") or 0 for d in ds]
    out = {}
    for k, d in enumerate(ds):
        c = {"prev_close": closes[k - 1] if k >= 1 else None}
        if k >= 25:
            ma = sum(closes[k - 20:k]) / 20
            ma_prev = sum(closes[k - 25:k - 5]) / 20
            c["uptrend"] = closes[k - 1] > ma and ma > ma_prev
        if k >= 5:
            c["avgvol5"] = sum(vols[k - 5:k]) / 5
        if k >= 14:
            c["avg_open5"] = sum(v5s[k - 14:k]) / 14
        out[d] = c
    return out


def _sell_px(s, t):
    return s["close"] if t == "13:25" else s.get("p" + t.replace(":", ""))


def _pack(dct, cost_note=None):
    out = {}
    for k, v in dct.items():
        xs = [x for _, x in v]
        dates = sorted(d for d, _ in v)
        mid = dates[len(dates) // 2] if dates else None
        out[k] = {**_st(xs), "t_day": _tday(v), "first_half": _st([x for d, x in v if d < mid]),
                  "second_half": _st([x for d, x in v if d >= mid])}
    return out


def part_d(per_days, dates=None):
    """查8 買點網格 v2（老闆：10:00、11:00、13:15 是早盤殺出後籌碼沉澱的時間）。
    D1＝收盤漲>5% 的強勢日（D1 當天買時，以「買進時點已漲>5%」判斷，避免偷看收盤）。
    買：D1/D2/D3 × 10:00/11:00/13:15；賣：買進之後的 D2~D4 各時點（09:05/09:30/13:25 收盤）。扣做多成本 0.585%。
    另依「買進當下是否已從開盤殺下來（價<開盤）」分組，驗證『殺完再買』是否比較好。"""
    res = {}
    sells = [("D2", "09:05"), ("D2", "09:30"), ("D2", "13:25"), ("D3", "09:05"), ("D3", "09:30"), ("D3", "13:25"),
             ("D4", "09:30"), ("D4", "13:25")]
    order = {"D1": 1, "D2": 2, "D3": 3, "D4": 4}
    for sym, dd in per_days.items():
        ds = sorted(dd)
        for k in range(1, len(ds) - 3):
            if dates is not None and ds[k] not in dates:
                continue
            pc = dd[ds[k - 1]]["close"]
            S = {"D1": dd[ds[k]], "D2": dd[ds[k + 1]], "D3": dd[ds[k + 2]], "D4": dd[ds[k + 3]]}
            if not pc:
                continue
            d1_close_strong = S["D1"]["close"] / pc - 1 > 0.05
            for bd in ("D1", "D2", "D3"):
                for bt in ("10:00", "11:00", "13:15"):
                    e = S[bd].get("p" + bt.replace(":", ""))
                    if not e:
                        continue
                    if bd == "D1":
                        if not e / pc - 1 > 0.05 or e / pc - 1 >= 0.094:   # 已近漲停買不到，排除
                            continue
                    elif not d1_close_strong:
                        continue
                    dumped = e < S[bd]["open"]
                    for sd, st in sells:
                        if order[sd] <= order[bd]:
                            continue
                        p = _sell_px(S[sd], st)
                        if not p:
                            continue
                        r = (p / e - 1) * 100 - COST_LONG
                        key = f"買{bd}_{bt}→賣{sd}_{st}"
                        res.setdefault(key, []).append((ds[k], r))
                        res.setdefault(key + ("｜買時已殺破開盤" if dumped else "｜買時在開盤之上"), []).append((ds[k], r))
    packed = _pack(res)
    best = sorted(((k, v) for k, v in packed.items() if v["n"] >= 30 and "｜" not in k),
                  key=lambda kv: -(kv[1]["mean"]))[:8]
    return {"grid": packed, "top8_by_mean": [k for k, _ in best]}


def part_e(per_days, dates=None):
    """隔日沖選股（2天1夜：13:15 前後買、隔天早盤賣）。母體：13:15 漲 3%~9.4%（未鎖漲停、買得到）。
    特徵：漲幅區間、13:15 是否貼近當日高點（≤1%）、13:15 前量比（今日累計量÷前5日均量）、日線上升趨勢、13:15 是否在均價線上。
    出場：D2 09:05／09:30／13:25。扣成本 0.585%。另列「全部特徵都好」的組合，前後半段穩定度。"""
    rows = []
    for sym, dd in per_days.items():
        ctx = _ctx(dd)
        ds = sorted(dd)
        for k in range(1, len(ds) - 1):
            if dates is not None and ds[k] not in dates:
                continue
            s1, s2, c = dd[ds[k]], dd[ds[k + 1]], ctx[ds[k]]
            pc, e = c.get("prev_close"), s1.get("p1315")
            if not (pc and e and c.get("avgvol5")):
                continue
            g = (e / pc - 1) * 100
            if not 3 <= g < 9.4:
                continue
            hi = s1.get("hi1315") or e
            vr = (s1.get("vol1315") or 0) / c["avgvol5"]
            r = {"date": ds[k], "sym": sym, "gb": "3~5%" if g < 5 else "5~7%" if g < 7 else "7~9.4%",
                 "near_hi": e >= hi * 0.99, "vr": "量比<1" if vr < 1 else "量比1~2" if vr < 2 else "量比≥2",
                 "up": bool(c.get("uptrend")), "vwap": bool(s1.get("vwap1315") and e > s1["vwap1315"])}
            for t in ("09:05", "09:30", "13:25"):
                p = _sell_px(s2, t)
                r["D2_" + t] = ((p / e - 1) * 100 - COST_LONG) if p else None
            rows.append(r)
    out = {"n": len(rows)}

    def grp(name, pred):
        sub = [r for r in rows if pred(r)]
        o = {}
        for t in ("09:05", "09:30", "13:25"):
            v = [(r["date"], r["D2_" + t]) for r in sub if r["D2_" + t] is not None]
            o["D2_" + t] = _pack({"x": v})["x"] if v else {"n": 0}
        out[name] = o
    grp("全部", lambda r: True)
    for b in ("3~5%", "5~7%", "7~9.4%"):
        grp("漲幅" + b, lambda r, b=b: r["gb"] == b)
    for b in ("量比<1", "量比1~2", "量比≥2"):
        grp(b, lambda r, b=b: r["vr"] == b)
    grp("貼近當日高點", lambda r: r["near_hi"])
    grp("離高點>1%（已拉回）", lambda r: not r["near_hi"])
    grp("日線上升趨勢", lambda r: r["up"])
    grp("非上升趨勢", lambda r: not r["up"])
    grp("均價線上", lambda r: r["vwap"])
    grp("組合：貼高＋均價線上＋上升趨勢", lambda r: r["near_hi"] and r["vwap"] and r["up"])
    grp("組合：貼高＋均價線上＋上升趨勢＋量比≥2", lambda r: r["near_hi"] and r["vwap"] and r["up"] and r["vr"] == "量比≥2")
    grp("組合：5~9.4%＋貼高＋上升趨勢", lambda r: r["gb"] != "3~5%" and r["near_hi"] and r["up"])
    return out


def part_f(per_days, dates=None):
    """多方進場提示型態（目標：像空方第三關一樣能分辨好壞）。條件母體＝日線上升趨勢（前收在上揚的 MA20 上，代理「做多候選」）。
    型態（判斷時點→進場價）：
      orb5：Zarattini 等「開盤區間突破」—開盤 5 分鐘收紅、開盤 5 分鐘量＞前 14 日同時段均量，10:00 前突破開盤 5 分鐘高點且 10:00 仍在其上 → 10:00 進場
      hold_open：09:00~10:00 最低價不破開盤 0.5%，且 10:00 價＞開盤 → 10:00
      vwap_hold：10:00 價在均價線上，且 09:30~10:00 沒跌破均價線（以 10:00 前最低≥均價×0.995 近似）→ 10:00
      gap_reclaim：開低（開盤<前收）但 10:00 收復前收 → 10:00
      hold_1100：11:00 價＞開盤且 09:00~11:00 最低不破開盤 1% → 11:00
      late_strong：13:15 價在均價線上、離當日高點≤1%、漲幅 1%~7% → 13:15（2天1夜型）
    報酬：進場 → 當日收盤、次日 09:30、次日收盤、3 日後收盤；超額＝扣同一進場時點、同日全部候選的平均。"""
    rows = []
    for sym, dd in per_days.items():
        ctx = _ctx(dd)
        ds = sorted(dd)
        for k in range(1, len(ds) - 3):
            if dates is not None and ds[k] not in dates:
                continue
            s, c = dd[ds[k]], ctx[ds[k]]
            if not c.get("uptrend") or s.get("first_hm") != 9 * 60 or not c.get("prev_close"):
                continue
            n1, n3 = dd[ds[k + 1]], dd[ds[k + 3]]
            pc, o = c["prev_close"], s["open"]
            for et in ("10:00", "11:00", "13:15"):
                e = s.get("p" + et.replace(":", ""))
                if not e:
                    continue
                r = {"date": ds[k], "sym": sym, "et": et, "eod": (s["close"] / e - 1) * 100,
                     "nd0930": ((n1["p0930"] / e - 1) * 100) if n1.get("p0930") else None,
                     "nd": (n1["close"] / e - 1) * 100, "d3": (n3["close"] / e - 1) * 100, "pat": set()}
                if et == "10:00":
                    if (s.get("or5_up") and c.get("avg_open5") and (s.get("vol5") or 0) > c["avg_open5"]
                            and s.get("orb5_1000") and e > s.get("or5_high", 1e18)):
                        r["pat"].add("orb5")
                    if s.get("lo1000") and s["lo1000"] >= o * 0.995 and e > o:
                        r["pat"].add("hold_open")
                    if s.get("vwap1000") and e > s["vwap1000"] and s.get("lo1000") and s["lo1000"] >= s["vwap1000"] * 0.995:
                        r["pat"].add("vwap_hold")
                    if o < pc and e > pc:
                        r["pat"].add("gap_reclaim")
                elif et == "11:00":
                    if s.get("lo1100") and s["lo1100"] >= o * 0.99 and e > o:
                        r["pat"].add("hold_1100")
                else:
                    g = (e / pc - 1) * 100
                    if s.get("vwap1315") and e > s["vwap1315"] and e >= (s.get("hi1315") or e) * 0.99 and 1 <= g < 7:
                        r["pat"].add("late_strong")
                rows.append(r)
    for h in ("eod", "nd0930", "nd", "d3"):
        acc = {}
        for r in rows:
            if r[h] is not None:
                acc.setdefault((r["date"], r["et"]), []).append(r[h])
        m = {k: mean(v) for k, v in acc.items()}
        for r in rows:
            r["x_" + h] = (r[h] - m[(r["date"], r["et"])]) if r[h] is not None else None
    labels = {"orb5": "開盤5分K收紅＋爆量＋突破並站穩開盤區間高點（10:00）", "hold_open": "10:00 前不破開盤＋10:00 在開盤上",
              "vwap_hold": "10:00 前守住均價線", "gap_reclaim": "開低後 10:00 收復前收", "hold_1100": "11:00 前不破開盤 1%＋在開盤上",
              "late_strong": "13:15 均價線上＋貼近高點＋漲 1~7%"}
    et_of = {"orb5": "10:00", "hold_open": "10:00", "vwap_hold": "10:00", "gap_reclaim": "10:00", "hold_1100": "11:00", "late_strong": "13:15"}
    out = {"n_rows": len(rows)}
    for p, lab in labels.items():
        base = [r for r in rows if r["et"] == et_of[p]]
        o = {"label": lab, "entry": et_of[p]}
        for tag, sub in (("yes", [r for r in base if p in r["pat"]]), ("no", [r for r in base if p not in r["pat"]])):
            e = {"n": len(sub)}
            for h in ("eod", "nd0930", "nd", "d3"):
                v = [(r["date"], r["x_" + h]) for r in sub if r["x_" + h] is not None]
                raw = [r[h] for r in sub if r[h] is not None]
                e[h] = {"ex": round(mean(x for _, x in v), 3) if v else None, "t": _tday(v),
                        "raw": round(mean(raw), 3) if raw else None,
                        "win_net": round(100 * sum(1 for x in raw if x > COST_LONG) / len(raw), 1) if raw else None}
            _is = [r["x_nd"] for r in sub if r["x_nd"] is not None and str(r["date"]) <= "2024-03-31"]
            _oos = [r["x_nd"] for r in sub if r["x_nd"] is not None and str(r["date"]) > "2024-03-31"]
            e["IS_ex_nd"] = round(mean(_is), 3) if _is else None
            e["OOS_ex_nd"] = round(mean(_oos), 3) if _oos else None
            dates = sorted({r["date"] for r in sub})
            if dates:
                mid = dates[len(dates) // 2]
                e["half_ex_nd"] = [round(mean([r["x_nd"] for r in sub if r["date"] < mid] or [0]), 3),
                                   round(mean([r["x_nd"] for r in sub if r["date"] >= mid] or [0]), 3)]
            o[tag] = e
        out[p] = o
    return out


def part_g(per_days, dates=None):
    """【2026-10-17 老闆：隔日沖的 T+1 應對】T 日漲停收盤（漲≥9.5% 且收在最高）→ T+1 盤中各種做法（5 分K）：
      跟單做多：開盤高於 T 收才買，09:05 買進；09:30 價沒高過 09:05（20~30 分鐘沒續強）就 09:30 賣，否則抱到收盤（另列抱到 10:00）。
      持有者：T 收盤買進的部位在 T+1 開盤／09:05／09:30／收盤賣出，哪個最好。
      放空：開盤放空（另列「開盤比 T 收高>1% 才空」），09:30／10:00／11:00／收盤回補；收盤版加「漲到開盤 +3% 停損」（以 13:15 前最高價近似）。
    成本：當沖 0.435%、隔夜持有 0.585%。前後半段、樣本內外（2024/3）。"""
    res = {}
    for sym, dd in per_days.items():
        ds = sorted(dd)
        for k in range(1, len(ds) - 1):
            if dates is not None and ds[k] not in dates:
                continue
            s0, s1, s2 = dd[ds[k - 1]], dd[ds[k]], dd[ds[k + 1]]
            pc, c1 = s0.get("close"), s1.get("close")
            if not (pc and c1 and c1 / pc - 1 >= 0.095 and c1 >= (s1.get("hi1315") or c1) * 0.999):
                continue
            o, p905, p930, p1000, p1100, c = (s2.get("open"), s2.get("p0905"), s2.get("p0930"), s2.get("p1000"),
                                             s2.get("p1100"), s2.get("close"))
            if not (o and p905 and p930 and p1000 and c) or s2.get("first_hm") != 9 * 60:
                continue
            d = ds[k]
            gap_up = o > c1
            add = lambda key, v: res.setdefault(key, []).append((d, v))  # noqa: E731
            if gap_up:
                exit_ = p930 if p930 <= p905 else c
                add("跟單做多：開高才09:05買，09:30沒續強就賣、否則抱到收盤", (exit_ / p905 - 1) * 100 - COST_DT)
                add("跟單做多：開高才09:05買，抱到10:00", (p1000 / p905 - 1) * 100 - COST_DT)
                add("跟單做多：開高才09:05買，抱到收盤", (c / p905 - 1) * 100 - COST_DT)
            for lab, px in (("開盤", o), ("09:05", p905), ("09:30", p930), ("收盤", c)):
                add(f"持有者：T收盤買、T+1 {lab}賣", (px / c1 - 1) * 100 - COST_LONG)
            hi = s2.get("hi1315") or c
            for cond, ok in (("", True), ("（開盤比T收高>1%）", o > c1 * 1.01)):
                if not ok:
                    continue
                for lab, px in (("09:30", p930), ("10:00", p1000), ("11:00", p1100), ("收盤", c)):
                    if px:
                        add(f"放空{cond}：開盤空、{lab}回補", (o - px) / o * 100 - COST_DT)
                add(f"放空{cond}：開盤空、收盤回補＋開盤+3%停損", (-3.0 - COST_DT) if hi >= o * 1.03 else ((o - c) / o * 100 - COST_DT))
    # 【2026-10-18】查7 可執行版：T 日 13:15 就要決定（收盤鎖死常買不到）。只用 13:15 以前的資訊：
    # 13:15 價 ≥ 前收 +9.4%（已到或貼近漲停）且日線上升趨勢 → 13:15 買進，T+1 開盤／09:05／09:30 賣。另列 13:15 漲 7%~9.4%（還沒到漲停）。
    for sym, dd in per_days.items():
        ctx = _ctx(dd)
        ds = sorted(dd)
        for k in range(1, len(ds) - 1):
            if dates is not None and ds[k] not in dates:
                continue
            s1, s2, c = dd[ds[k]], dd[ds[k + 1]], ctx[ds[k]]
            pc, e = c.get("prev_close"), s1.get("p1315")
            if not (pc and e and c.get("uptrend") and s2.get("open")):
                continue
            g = e / pc - 1
            if g >= 0.094:
                tag = "查7可執行版：13:15 已到漲停附近（≥+9.4%）＋上升趨勢，13:15 買"
            elif g >= 0.07:
                tag = "對照：13:15 漲 7%~9.4%＋上升趨勢，13:15 買"
            else:
                continue
            for lab, px in (("T+1 開盤賣", s2.get("open")), ("T+1 09:05 賣", s2.get("p0905")), ("T+1 09:30 賣", s2.get("p0930"))):
                if px:
                    res.setdefault(f"{tag}→{lab}", []).append((ds[k], (px / e - 1) * 100 - COST_LONG))
            res.setdefault(f"{tag}｜當天收盤是否鎖漲停（1＝是）", []).append((ds[k], 1.0 if s1["close"] / pc - 1 >= 0.095 else 0.0))
    out = {}
    for key, v in res.items():
        xs = [x for _, x in v]
        dts = sorted(d for d, _ in v)
        mid = dts[len(dts) // 2]
        out[key] = {**_st(xs), "t_day": _tday(v), "first_half": _st([x for d, x in v if d < mid]),
                    "second_half": _st([x for d, x in v if d >= mid]),
                    "IS": _st([x for d, x in v if str(d) <= "2024-03-31"]), "OOS": _st([x for d, x in v if str(d) > "2024-03-31"])}
    return out
