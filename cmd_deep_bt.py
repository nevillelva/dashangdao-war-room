"""【2026-10-17】查指令深度回測：原規則（與線上 evaluate_single_condition 完全相同）＋「名稱對應的忠實版」變體。

為什麼要變體：盤點發現多個查X的名稱和實際條件不符（例：查9 叫「均線糾結爆量突破」但只檢查爆量；查6 叫「營收雙增」只看年增；
查4 叫「投信作帳集團股」只看單日投信買超）。變體照名稱字面補齊條件，看「名稱想抓的東西」到底有沒有效。

回測口徑（比線上 filter_backtest 嚴格）：
- 進場＝訊號日「隔天開盤」（線上回測用訊號日收盤，但掃描在 22:00，實際買不到當天收盤 → 有前視）。
- 報酬：5／10／20 個交易日後收盤；MAE10＝進場後 10 日內最低價相對進場價（最大不利波動，回撤）；MFE10＝最高價。
- 超額＝個股報酬 − 同日全池平均（扣掉大盤）；勝率＝超額>0 的比例；t 值以「日」為單位（同日多檔不獨立）。
- 樣本內 ≤ 2024-03-31、樣本外 ≥ 2024-04-01；另列逐年超額。
- 不含成本（來回約 0.585%）；母體＝目前成交值前 N 檔（存活者偏誤）；股價用還原權值。
查11（殖利率）歷史用「目前股利÷當時股價」會前視，已另案分析，這裡不測；查12（K 線型態）已在 backtest_cmd_rules 測過，這裡不重複。
純函式、不碰網路，可離線測試。"""
from __future__ import annotations
from statistics import mean, pstdev

import numpy as np
import pandas as pd

IS_END = "2024-03-31"

LABELS = {
    "base": "隨機基準(全部交易日)",
    "c1": "查1 新：上升趨勢＋KD當天金叉＋首根攻擊紅K＋爆量", "c1_old": "查1 舊",
    "c2": "查2 新：季線上揚、貼MA20(0~5%)、量縮", "c2_old": "查2 舊：收>MA60且量比≥1.2",
    "c3": "查3 新：價值≥60＋非地雷＋營收循環回升", "c3_old": "查3 舊：價值≥60＋非地雷",
    "c4": "查4 投信買超（未改）", "c4v": "查4 投信連買3日", "c4w": "查4 連買3日且距季底≤31天",
    "c5": "查5 新：外資買＋融資確定減少", "c5_old": "查5 舊：缺融資資料也放行",
    "c6": "查6 新：年增>20%＋月增>0＋創20日新高＋量比≥1.5", "c6_old": "查6 舊：年增>20%",
    "c7": "查7 隔日沖漲停（漲停收盤＋前日上升趨勢＋大盤多頭）",
    "c8": "查8 新：昨日漲>5%且今天續漲", "c8_old": "查8 舊：昨日漲>5%",
    "c9": "查9 新：前日均線糾結＋爆量＋站上三線收紅", "c9_old": "查9 舊：量比≥2",
    "c10": "查10 新：量縮＋融資確定減少", "c10_old": "查10 舊：缺融資資料也放行",
    "c8_short": "查8 舊（當空方看：超額越負越好）",
    "c19_and": "查1新∧查9新（同時成立）", "c19_or": "查1新或查9新",
    "c1_bull": "查1新＋大盤多頭(加權>MA60)", "c9_bull": "查9新＋大盤多頭", "c19_or_bull": "查1或查9＋大盤多頭",
    "c1_old_bull": "查1舊＋大盤多頭", "c9_old_bull": "查9舊＋大盤多頭",
    "c6_v": "查6 改回：年增>20%＋月增>0", "c32_and": "查3新∧查2新", "c32_or": "查3新或查2新",
    "c3_bull": "查3新＋大盤多頭", "c2_bull": "查2新＋大盤多頭",
    # 【2026-10-17】短線動能軌（代理「主力偵測四維度」：近 40 天實盤 週轉率異常∧營收年增>20 的 5 日超額 +2.2%、t 4.1，需 5 年驗證）
    "m1": "動能：量比≥2＋創20日新高", "m2": "動能：量比≥2＋創20日新高＋營收年增>20%",
    "m3": "動能：量比≥1.5＋站上MA20＋營收年增>20%", "m2_bull": "動能 m2＋大盤多頭",
    "m3_c32": "動能 m3 ∧ 做多主力（查3∧查2）",
    # 【2026-10-20】查12 K 線型態 5 年回測（線上同一支 detect_k_line_patterns_v152；排程預設型態＝長紅吞噬／紅三兵／低檔長紅）
    "c12": "查12 看漲K線（長紅吞噬／紅三兵／低檔長紅 任一）", "c12_engulf": "查12 長紅吞噬", "c12_3sold": "查12 紅三兵",
    "c12_red": "查12 低檔長紅",
    # 空方技術型態（當空方看：超額越負越好）
    "c12d": "查12 空方型態（長黑吞噬／黑三兵／高檔長黑 任一；空方看）", "c12d_engulf": "查12 長黑吞噬（空方看）",
    "c12d_3crow": "查12 黑三兵（空方看）", "c12d_black": "查12 高檔長黑（空方看）",
    "c12d_bear": "查12 空方型態＋大盤空頭（空方看）", "c12d_hi": "查12 空方型態＋前20日大漲後（高檔，空方看）", "c12_bull": "查12 看漲K線＋大盤多頭", "c12_c32": "查12 ∧ 做多主力（查3∧查2）",
}
ORDER = list(LABELS)


def _quarter_end_days(d: pd.Timestamp) -> int:
    q_end_month = ((d.month - 1) // 3 + 1) * 3
    end = pd.Timestamp(d.year, q_end_month, 1) + pd.offsets.MonthEnd(0)
    return int((end - d).days)


def build_rows(sym, df, inst=None, rev=None, pe=None, eval_fn=None, lookup_rev=None, pe_landmine=30.0,
               vol_surge=2.0, vol_low=0.6, feat_fn=None, cycle_fn=None, yoys_fn=None, mkt_bull=None, kline_fn=None):
    """df：日K（index=日期，Open/High/Low/Close/Volume，已還原）。inst：index=日期字串，f_buy/t_buy/margin_diff。
    rev：fetch_revenue_history_lagged 的結果；lookup_rev(rev, ts)→(yoy, mom)。pe：PER Series（index=日期字串）。
    eval_fn(cmd, card)：線上的 evaluate_single_condition。回傳每日 dict（含 hits 集合與前瞻報酬）。"""
    if df is None or len(df) < 80:
        return []
    df = df.copy()
    c = df["Close"].astype(float)
    df["MA5"], df["MA10"], df["MA20"], df["MA60"] = (c.rolling(n).mean() for n in (5, 10, 20, 60))
    df["V5"] = df["Volume"].rolling(5).mean()
    hl = pd.concat([df["High"] - df["Low"], (df["High"] - c.shift()).abs(), (df["Low"] - c.shift()).abs()], axis=1).max(axis=1)
    df["ATR"] = hl.rolling(14).mean()
    lo9, hi9 = df["Low"].rolling(9).min(), df["High"].rolling(9).max()
    k = ((c - lo9) / (hi9 - lo9 + 1e-9) * 100).bfill().ffill().ewm(com=2, adjust=False).mean()
    d_ = k.ewm(com=2, adjust=False).mean()
    hi20 = c.rolling(20).max()
    dates = df.index.strftime("%Y-%m-%d")
    O, H, L, C, V = (df[x].to_numpy(dtype=float) for x in ("Open", "High", "Low", "Close", "Volume"))
    tb = fb = mg = None
    if inst is not None and len(inst):
        ii = inst.reindex(dates)
        tb = ii["t_buy"].to_numpy(dtype=float) if "t_buy" in ii else None
        fb = ii["f_buy"].to_numpy(dtype=float) if "f_buy" in ii else None
        mg = ii["margin_diff"].to_numpy(dtype=float) if "margin_diff" in ii else None
    out = []
    n = len(df)
    for i in range(60, n - 21):
        try:
            if np.isnan(df["MA60"].iat[i]) or not df["V5"].iat[i] > 0:
                continue
            price, opn = C[i], O[i]
            vr = V[i] / df["V5"].iat[i]
            atr = df["ATR"].iat[i] if not np.isnan(df["ATR"].iat[i]) else price * 0.02
            is_first_red = (price > opn) and (C[i - 1] < O[i - 1]) and (abs(price - opn) > atr * 0.5)
            prev_gain = (C[i - 1] / C[i - 2] - 1) * 100 if C[i - 2] > 0 else 0.0
            kv, dv = float(k.iat[i]), float(d_.iat[i])
            t_buy = float(tb[i]) if tb is not None and not np.isnan(tb[i]) else 0.0
            f_buy = float(fb[i]) if fb is not None and not np.isnan(fb[i]) else 0.0
            has_m = mg is not None and not np.isnan(mg[i])
            m_diff = float(mg[i]) if has_m else 0.0
            yoy = mom = None
            if rev is not None and lookup_rev is not None:
                yoy, mom = lookup_rev(rev, df.index[i])
            # 查3 的歷史價值分數（與 warroom_core._filter_backtest_one_stock 同一套；殖利率項因前視不計）
            vscore, landmine = 0, False
            if pe is not None:
                d0 = dates[i]
                pe_raw = float(pe.loc[d0]) if d0 in pe.index else None
                w = pe[pe.index < d0]
                pct = float((w < pe_raw).mean() * 100) if (pe_raw is not None and len(w) >= 60) else None
                s = 40
                if pct is not None:
                    s += 30 if pct <= 20 else 18 if pct <= 40 else 5 if pct <= 60 else -10 if pct <= 80 else -20
                elif pe_raw is not None:
                    s += 20 if pe_raw <= 12 else 10 if pe_raw <= 18 else (-12 if pe_raw > pe_landmine else 0)
                else:
                    s -= 15
                if yoy is not None:
                    s += 22 if yoy > 20 else 12 if yoy > 0 else -18 if yoy < -10 else (-10 if yoy < 0 else 0)
                vscore = int(max(0, min(100, s)))
                expensive = (pct is not None and pct >= 80) or (pct is None and pe_raw is not None and pe_raw > pe_landmine)
                f5 = float(np.nansum(fb[max(0, i - 4): i + 1])) if fb is not None else 0.0
                landmine = bool(expensive and (yoy is not None and yoy < 0) and f5 < 0)
            card = {"price": price, "ma60": float(df["MA60"].iat[i]), "vol_ratio": vr, "t_buy": t_buy, "f_buy": f_buy,
                    "margin_diff": m_diff, "has_margin": has_m, "rev_yoy": yoy,
                    "kdj_str": f"金叉 (K:{kv:.1f})" if kv > dv else f"死叉 (K:{kv:.1f})", "k_val": round(kv, 1),
                    "value_score": vscore, "landmine": landmine, "is_first_red": is_first_red,
                    "is_yesterday_strong": prev_gain > 5.0, "div_yield": 0.0, "detected_patterns": []}
            if feat_fn is not None:
                card.update(feat_fn(df.iloc[max(0, i - 89): i + 1]))
            card["rev_mom"] = mom
            card["rev_cycle_up"] = cycle_fn(yoys_fn(rev, df.index[i])) if (rev is not None and cycle_fn and yoys_fn) else None
            hits = {"base"}
            if kline_fn is not None:   # 與線上同一支函式；只傳最近 25 根（函式只用到最後 3 根與 20 日區間）
                _pt = [x.get("text") for x in kline_fn(df.iloc[max(0, i - 24): i + 1], atr)]
                card["detected_patterns"] = [{"text": t} for t in _pt]
                if "長紅吞噬" in _pt:
                    hits.add("c12_engulf")
                if "紅三兵" in _pt:
                    hits.add("c12_3sold")
                if "低檔長紅" in _pt:
                    hits.add("c12_red")
                if any(t in _pt for t in ("長紅吞噬", "紅三兵", "低檔長紅")):
                    hits.add("c12")
                if "長黑吞噬" in _pt:
                    hits.add("c12d_engulf")
                if "黑三兵" in _pt:
                    hits.add("c12d_3crow")
                if "高檔長黑" in _pt:
                    hits.add("c12d_black")
                if any(t in _pt for t in ("長黑吞噬", "黑三兵", "高檔長黑")):
                    hits.add("c12d")
                    if i >= 20 and C[i - 20] > 0 and C[i] / C[i - 20] - 1 >= 0.15:
                        hits.add("c12d_hi")
            if eval_fn is not None:
                card["mkt_bull"] = (bool(mkt_bull.get(dates[i])) if mkt_bull and dates[i] in mkt_bull else None)
                for key, cmd in (("c7", "查7."), ("c1", "查1."), ("c2", "查2."), ("c4", "查4."), ("c5", "查5."), ("c6", "查6."),
                                 ("c8", "查8."), ("c9", "查9."), ("c10", "查10.")):
                    if eval_fn(cmd, card):
                        hits.add(key)
                if pe is not None and eval_fn("查3.", card):
                    hits.add("c3")
            # 舊規則（2026-10-16 以前）供比較
            surge, low = vol_surge, vol_low
            if is_first_red and vr >= surge and kv > dv and kv > 50:
                hits.add("c1_old")
            if price > float(df["MA60"].iat[i]) and vr >= 1.2:
                hits.add("c2_old")
            if pe is not None and vscore >= 60 and not landmine:
                hits.add("c3_old")
            m_shrink_old = (m_diff < 0) if has_m else True
            if f_buy > 0 and m_shrink_old:
                hits.add("c5_old")
            if yoy is not None and yoy > 20:
                hits.add("c6_old")
            if prev_gain > 5.0:
                hits.add("c8_old")
                hits.add("c8_short")
            if vr >= surge:
                hits.add("c9_old")
            if 0 < vr <= low and m_shrink_old:
                hits.add("c10_old")
            tb3 = tb is not None and all((not np.isnan(tb[j])) and tb[j] > 0 for j in (i, i - 1, i - 2))
            if tb3:
                hits.add("c4v")
                if _quarter_end_days(df.index[i]) <= 31:
                    hits.add("c4w")
            bull = bool(mkt_bull.get(dates[i])) if mkt_bull else False
            if "c1" in hits and "c9" in hits:
                hits.add("c19_and")
            if "c1" in hits or "c9" in hits:
                hits.add("c19_or")
                if bull:
                    hits.add("c19_or_bull")
            if bull:
                for _k in ("c1", "c9", "c1_old", "c9_old", "c3", "c2"):
                    if _k in hits:
                        hits.add(f"{_k}_bull")
            if yoy is not None and yoy > 20 and mom is not None and mom > 0:
                hits.add("c6_v")
            if "c3" in hits and "c2" in hits:
                hits.add("c32_and")
            if "c3" in hits or "c2" in hits:
                hits.add("c32_or")
            if "c12d" in hits and mkt_bull and not bull and dates[i] in mkt_bull:
                hits.add("c12d_bear")
            if "c12" in hits:
                if bull:
                    hits.add("c12_bull")
                if "c32_and" in hits:
                    hits.add("c12_c32")
            _hi20 = bool(card.get("hi20_break"))
            if vr >= 2 and _hi20:
                hits.add("m1")
                if yoy is not None and yoy > 20:
                    hits.add("m2")
                    if bull:
                        hits.add("m2_bull")
            if vr >= 1.5 and price > float(df["MA20"].iat[i]) and yoy is not None and yoy > 20:
                hits.add("m3")
                if "c32_and" in hits:
                    hits.add("m3_c32")
            entry = O[i + 1]
            if not entry > 0:
                continue
            seg_l, seg_h = L[i + 1: i + 11], H[i + 1: i + 11]
            f3 = float(np.nansum(fb[max(0, i - 2): i + 1])) if fb is not None else 0.0
            tags = {"c1": "c1" in hits, "c9": "c9" in hits, "c6v": "c6_v" in hits, "c4v": "c4v" in hits,
                    "hi20": bool(card.get("hi20_break")), "fb3": f3 > 0, "tb": t_buy > 0, "bull": bull,
                    "vs70": vscore >= 70, "near2": (card.get("near_ma20_pct") is not None and 0 <= card["near_ma20_pct"] <= 2),
                    "kgold": kv > dv, "red": price > opn}
            out.append({"sym": sym, "date": dates[i], "hits": hits, "tags": tags, "vs": vscore,
                        "r5": (C[i + 5] / entry - 1) * 100, "r10": (C[i + 10] / entry - 1) * 100, "r20": (C[i + 20] / entry - 1) * 100,
                        "mae10": (float(seg_l.min()) / entry - 1) * 100, "mfe10": (float(seg_h.max()) / entry - 1) * 100,
                        # 【2026-10-17】大盤多空、規模（近 20 日平均成交金額，analyze 再依當天母體切三等分）
                        "bull": (bool(mkt_bull.get(dates[i])) if mkt_bull else None),
                        "tv20": float(np.mean(C[max(0, i - 19): i + 1] * V[max(0, i - 19): i + 1]))})
        except Exception:  # noqa: BLE001
            continue
    return out


def _tday(pairs):
    byd = {}
    for d, x in pairs:
        byd.setdefault(d, []).append(x)
    dm = [mean(v) for v in byd.values()]
    if len(dm) < 5:
        return None
    sd = pstdev(dm) or 1e-9
    return round(mean(dm) / sd * len(dm) ** 0.5, 2)


def analyze(rows):
    base = {}
    for h in ("r5", "r10", "r20", "mae10"):
        acc = {}
        for r in rows:
            acc.setdefault(r["date"], []).append(r[h])
        base[h] = {d: mean(v) for d, v in acc.items()}
    for r in rows:
        for h in ("r5", "r10", "r20", "mae10"):
            r["x" + h] = r[h] - base[h][r["date"]]

    # 規模分組：每天依近 20 日平均成交金額把母體切三等分（小／中／大）
    byd = {}
    for r in rows:
        byd.setdefault(r["date"], []).append(r)
    for d, rs in byd.items():
        vs = sorted(x.get("tv20", 0) for x in rs)
        q1, q2 = vs[len(vs) // 3], vs[2 * len(vs) // 3]
        for x in rs:
            t = x.get("tv20", 0)
            x["size"] = "small" if t < q1 else ("mid" if t < q2 else "large")

    def st(sub):
        if not sub:
            return {"n": 0}
        o = {"n": len(sub), "days": len({r["date"] for r in sub})}
        for h in ("r5", "r10", "r20"):
            xs = [r["x" + h] for r in sub]
            o[h] = {"raw": round(mean(r[h] for r in sub), 3), "ex": round(mean(xs), 3),
                    "win_raw": round(100 * sum(1 for r in sub if r[h] > 0) / len(sub), 1),
                    "win_net": round(100 * sum(1 for r in sub if r[h] > 0.585) / len(sub), 1),
                    "win": round(100 * sum(1 for x in xs if x > 0) / len(xs), 1), "t": _tday([(r["date"], r["x" + h]) for r in sub])}
        maes = sorted(r["mae10"] for r in sub)
        o["mae10_mean"] = round(mean(maes), 2)
        o["mae10_p10"] = round(maes[int(len(maes) * 0.1)], 2)
        o["mae10_ex"] = round(mean(r["xmae10"] for r in sub), 2)
        o["mfe10_mean"] = round(mean(r["mfe10"] for r in sub), 2)
        return o

    res = {}
    years = sorted({r["date"][:4] for r in rows})
    for key in ORDER:
        sub = [r for r in rows if key in r["hits"]]
        e = {"label": LABELS[key], "all": st(sub),
             "IS": st([r for r in sub if r["date"] <= IS_END]), "OOS": st([r for r in sub if r["date"] > IS_END])}
        e["by_year_ex10"] = {}
        for y in years:
            ys = [r["xr10"] for r in sub if r["date"][:4] == y]
            e["by_year_ex10"][y] = {"n": len(ys), "ex": round(mean(ys), 3) if ys else None}
        # 【2026-10-17 老闆：回測要搭配大盤趨勢】大盤多頭（加權>MA60）／空頭、規模三等分，各自 10 日超額與樣本內外
        def _mini(xs):
            if not xs:
                return {"n": 0}
            return {"n": len(xs), "ex10": round(mean(r["xr10"] for r in xs), 3), "t10": _tday([(r["date"], r["xr10"]) for r in xs]),
                    "win10_raw": round(100 * sum(1 for r in xs if r["r10"] > 0) / len(xs), 1),
                    "IS_ex10": round(mean([r["xr10"] for r in xs if r["date"] <= IS_END]), 3) if any(r["date"] <= IS_END for r in xs) else None,
                    "OOS_ex10": round(mean([r["xr10"] for r in xs if r["date"] > IS_END]), 3) if any(r["date"] > IS_END for r in xs) else None}
        e["regime"] = {"bull": _mini([r for r in sub if r.get("bull") is True]), "bear": _mini([r for r in sub if r.get("bull") is False])}
        e["size"] = {z: _mini([r for r in sub if r.get("size") == z]) for z in ("small", "mid", "large")}
        res[key] = e
    return res


FOCUS_TAGS = {"c1": "＋查1", "c9": "＋查9", "c6v": "＋查6雙增", "c4v": "＋投信連買3日", "hi20": "＋創20日新高",
              "fb3": "＋外資3日累計買超", "tb": "＋投信當日買超", "bull": "＋大盤多頭", "vs70": "＋價值分≥70",
              "near2": "＋貼MA20 0~2%", "kgold": "＋KD金叉", "red": "＋收紅K"}


def analyze_focus(rows, key="c32_and", topk=(1, 3, 5)):
    """【2026-10-17】濃縮研究：做多主力（查3∧查2）當天若同時有其他標籤，是否更好？每天只挑前 K 檔是否更好？
    需先跑 analyze(rows)（會寫入 xr5/xr10）。排序分數＝有效標籤數（只用 IS 期間超額>0 的標籤，避免偷看樣本外）＋價值分/1000。"""
    sub = [r for r in rows if key in r["hits"] and "xr10" in r]
    if not sub:
        return {"n": 0}

    def st(xs):
        if not xs:
            return {"n": 0}
        return {"n": len(xs), "days": len({r["date"] for r in xs}), "ex5": round(mean(r["xr5"] for r in xs), 3),
                "ex10": round(mean(r["xr10"] for r in xs), 3), "win10": round(100 * sum(1 for r in xs if r["r10"] > 0) / len(xs), 1),
                "t10": _tday([(r["date"], r["xr10"]) for r in xs]),
                "OOS_ex10": (round(mean(r["xr10"] for r in xs if r["date"] > IS_END), 3)
                             if any(r["date"] > IS_END for r in xs) else None)}
    out = {"base": st(sub), "tags": {}}
    good, bad = [], []

    def _m(xs):
        return mean(xs) if len(xs) >= 30 else None
    for t, lab in FOCUS_TAGS.items():
        yes = [r for r in sub if r["tags"].get(t)]
        no = [r for r in sub if not r["tags"].get(t)]
        out["tags"][t] = {"label": lab, "yes": st(yes), "no": st(no)}
        # 【2026-10-17 修正】只看樣本內會選到「樣本外反而變差」的標籤（例：KD金叉）。改成樣本內、樣本外都同方向才算數。
        yi, ni = _m([r["xr10"] for r in yes if r["date"] <= IS_END]), _m([r["xr10"] for r in no if r["date"] <= IS_END])
        yo, no_ = _m([r["xr10"] for r in yes if r["date"] > IS_END]), _m([r["xr10"] for r in no if r["date"] > IS_END])
        if None in (yi, ni, yo, no_):
            continue
        if yi > ni and yo > no_:
            good.append(t)
        elif yi < ni and yo < no_:
            bad.append(t)
    out["good_tags_IS"] = good          # 名稱沿用（排程讀這個鍵）；意義改為「樣本內外都加分」
    out["bad_tags"] = bad               # 樣本內外都減分
    byd = {}
    for r in sub:
        r["_fs"] = (sum(1 for t in good if r["tags"].get(t)) + sum(1 for t in bad if not r["tags"].get(t))
                    + r.get("vs", 0) / 1000.0 * (-1 if "vs70" in bad else 1))
        byd.setdefault(r["date"], []).append(r)
    out["topk"] = {}
    for k in topk:
        pick = []
        for d, xs in byd.items():
            pick += sorted(xs, key=lambda z: -z["_fs"])[:k]
        out["topk"][str(k)] = st(pick)
    out["per_day_mean"] = round(len(sub) / len(byd), 2)
    # 誠實檢驗（walk-forward）：只用樣本內前後兩段（≤2022-08-31／之後到 IS_END）都同方向的標籤打分，再看「樣本外」分數高低是否有差
    mid = "2022-08-31"
    g2, b2 = [], []
    for t in FOCUS_TAGS:
        ys = [[r["xr10"] for r in sub if r["tags"].get(t) and lo < r["date"] <= hi] for lo, hi in (("", mid), (mid, IS_END))]
        ns = [[r["xr10"] for r in sub if not r["tags"].get(t) and lo < r["date"] <= hi] for lo, hi in (("", mid), (mid, IS_END))]
        if min(len(x) for x in ys + ns) < 20:
            continue
        d1, d2 = mean(ys[0]) - mean(ns[0]), mean(ys[1]) - mean(ns[1])
        if d1 > 0 and d2 > 0:
            g2.append(t)
        elif d1 < 0 and d2 < 0:
            b2.append(t)
    oos = [r for r in sub if r["date"] > IS_END]
    wf = {}
    for r in oos:
        k = sum(1 for t in g2 if r["tags"].get(t)) + sum(1 for t in b2 if not r["tags"].get(t))
        wf.setdefault(str(min(k, 6)), []).append(r)
    byd_o = {}
    for r in oos:
        r["_wf"] = sum(1 for t in g2 if r["tags"].get(t)) + sum(1 for t in b2 if not r["tags"].get(t))
        byd_o.setdefault(r["date"], []).append(r)
    top_o = {}
    for k in topk:
        pk = []
        for d, xs in byd_o.items():
            pk += sorted(xs, key=lambda z: -z["_wf"])[:k]
        top_o[str(k)] = st(pk)
    out["walk_forward"] = {"good_IS": g2, "bad_IS": b2, "oos_by_score": {k: st(v) for k, v in sorted(wf.items())},
                           "oos_all": st(oos), "oos_topk": top_o}
    sc = {}
    for r in sub:
        sc.setdefault(max(0, min(int(round(r["_fs"])), 6)), []).append(r)
    out["by_score"] = {str(k): st(v) for k, v in sorted(sc.items())}
    return out


def replay_live(rows, key="c32_and", ks=(1, 3, 5), horizons=("r5", "r10", "r20")):
    """【2026-10-19 老闆：隔夜掃描只留 11 天，用 5 年資料回補主戰清單勝率；做多要看持有 20 日】
    用 5 年日K重建「波段主力」每日清單：查3∧查2、排序＝無追高警示（查1／查9／查8）→ 有查6 → 價值分<70 → 價值分低者優先
    （與 focus_list.rank_swing 同口徑），每天取前 k 檔，隔天開盤買進，持有 5／10／20 日收盤賣出（成本 0.585%）。
    回傳各 k 的整體／樣本內／樣本外／大盤多頭／空頭：n、勝率（扣成本後>0）、平均淨報酬、比同日母體超額、t 值。需先跑 analyze(rows)。"""
    sub = [r for r in rows if key in r["hits"] and "xr10" in r and "xr20" in r]
    byd = {}
    for r in sub:
        warn = bool(r["hits"] & {"c1", "c9", "c8_short"})
        r["_lr"] = (0 if warn else 1, 1 if r["tags"].get("c6v") else 0, 0 if r["tags"].get("vs70") else 1, -r.get("vs", 0))
        byd.setdefault(r["date"], []).append(r)

    def st(xs):
        if not xs:
            return {"n": 0}
        o = {"n": len(xs), "days": len({r["date"] for r in xs})}
        for h in horizons:
            net = [r[h] - 0.585 for r in xs]
            o[h] = {"win": round(100 * sum(1 for v in net if v > 0) / len(net), 1), "net": round(mean(net), 3),
                    "ex": round(mean(r["x" + h] for r in xs), 3), "t": _tday([(r["date"], r["x" + h]) for r in xs])}
        return o
    out = {"universe": st(sub)}
    for k in ks:
        pick = []
        for d, xs in byd.items():
            pick += sorted(xs, key=lambda z: z["_lr"], reverse=True)[:k]
        out[f"top{k}"] = {"all": st(pick), "IS": st([r for r in pick if r["date"] <= IS_END]),
                          "OOS": st([r for r in pick if r["date"] > IS_END]),
                          "bull": st([r for r in pick if r.get("bull") is True]), "bear": st([r for r in pick if r.get("bull") is False])}
    return out


# 【2026-10-20 老闆：最佳持有勝率每檔都一樣（全是查3 的全體 20 日）→ 要針對這一檔個股、哪個持有天數勝率最高】
# 同一檔 5 年內每個條件「不重疊」的事件（進場後 h 天內再命中不重算），算 5/10/20 日的扣成本勝率、平均淨報酬、比同日母體超額。
SYM_KEYS = {"查1.": "c1", "查2.": "c2", "查3.": "c3", "查4.": "c4", "查5.": "c5", "查6.": "c6", "查9.": "c9", "查10.": "c10", "查12.": "c12"}
SYM_COST = 0.585


def per_symbol_stats(rows, keys=SYM_KEYS, cost=SYM_COST, horizons=(("r5", 5), ("r10", 10), ("r20", 20))):
    """rows：build_rows 的輸出（先跑 analyze 才有 xr5/xr10/xr20）。
    回傳 {sym: {"查3.": [[n, 勝率%, 平均淨報酬%, 平均超額%], ...5/10/20 日]}}；沒有事件的條件不列。"""
    bysym = {}
    for r in rows:
        bysym.setdefault(r["sym"], []).append(r)
    out = {}
    for sym, rs in bysym.items():
        rs.sort(key=lambda r: r["date"])
        cell = {}
        for pfx, key in keys.items():
            idx = [i for i, r in enumerate(rs) if key in r["hits"]]
            if not idx:
                continue
            per_h = []
            for h, hd in horizons:
                nxt, vals, exs = -1, [], []
                for i in idx:
                    if i < nxt:
                        continue
                    nxt = i + hd
                    v = rs[i].get(h)
                    if v is None:
                        continue
                    vals.append(float(v) - cost)
                    if rs[i].get("x" + h) is not None:
                        exs.append(float(rs[i]["x" + h]))
                n = len(vals)
                per_h.append([n, round(100 * sum(1 for v in vals if v > 0) / n, 1) if n else None,
                              round(sum(vals) / n, 2) if n else None, round(sum(exs) / len(exs), 2) if exs else None])
            cell[pfx] = per_h
        if cell:
            out[sym] = cell
    return out
