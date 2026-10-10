# -*- coding: utf-8 -*-
"""
etf_core.py —— ETF 月配規劃的純計算核心（R99新增）

【架構規則】跟 warroom_core.py 一樣：不 import streamlit、不連 Supabase，網頁版(etf_tab.py)與排程版
(system_scheduler.py 的 etf_dividend_sync)共用；所有函式都是「資料進、結果出」的純函式，可離線單元測試。
【單位】股數一律用「股」存（1張=1000股），與專案「資料庫以股為單位」的慣例一致。

【領息資格規則（台股）】除息日(ex_date)前一個營業日收盤時仍持有，才有資格領這次配息。
這裡用「交易日期 < 除息日」的買賣紀錄算持股（T+2 交割不影響資格認定，以成交日為準）。
【發放日】優先用資料源提供的發放日；沒有就用該檔歷史「除息→發放」天數中位數估算，並標示為估算。
【稅費（可調）】證交稅：ETF賣出0.1%；手續費0.1425%×折扣(最低1元，零股/整股都以此簡化，使用者可手動改)；
配息扣：匯費10元（可關）；二代健保補充保費2.11%（單次給付≥20,000元才扣，可關）。
【不含】綜合所得稅（股利所得合併或分離課稅，依個人身分，這裡不替使用者算）。
"""
import math
import datetime as dt
from collections import defaultdict

FEE_RATE = 0.001425
ETF_SELL_TAX = 0.001
NHI_RATE = 0.0211
NHI_THRESHOLD = 20000
REMIT_FEE = 10
LOT = 1000


# ------------------------------------------------------------------ 小工具
def to_date(x):
    if x is None or x == "":
        return None
    if isinstance(x, dt.datetime):
        return x.date()
    if isinstance(x, dt.date):
        return x
    s = str(x).strip()[:10]
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
        try:
            return dt.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _f(x, default=0.0):
    try:
        v = float(x)
        return v if math.isfinite(v) else default
    except (TypeError, ValueError):
        return default


def norm_events(events):
    """把資料庫列(dict)正規化：{symbol, ex_date(date), cash(float), pay_date(date|None), estimated(bool)}，依除息日排序。"""
    out = []
    for e in events or []:
        ex = to_date(e.get("ex_date"))
        cash = _f(e.get("cash_per_unit", e.get("cash")))
        if not ex or cash <= 0:
            continue
        out.append({"symbol": str(e.get("symbol", "")).strip(), "ex_date": ex, "cash": cash,
                    "pay_date": to_date(e.get("pay_date")),
                    "estimated": bool(e.get("pay_date_estimated", e.get("estimated", False)))})
    out.sort(key=lambda x: (x["symbol"], x["ex_date"]))
    return out


def median_pay_lag(events, default=25):
    lags = sorted((e["pay_date"] - e["ex_date"]).days for e in events
                  if e.get("pay_date") and not e.get("estimated") and 0 <= (e["pay_date"] - e["ex_date"]).days <= 90)
    if not lags:
        return default
    return lags[len(lags) // 2]


def effective_pay_date(ev, lag):
    """有資料源發放日用它；否則用 除息日+lag 估算。回傳 (date, 是否估算)。"""
    if ev.get("pay_date"):
        return ev["pay_date"], bool(ev.get("estimated"))
    return ev["ex_date"] + dt.timedelta(days=lag), True


# ------------------------------------------------------------------ 配息統計 / 頻率分類
def trailing(events, today, days=365):
    lo = today - dt.timedelta(days=days)
    return [e for e in events if lo < e["ex_date"] <= today]


def annual_dividend_per_unit(events, today):
    return sum(e["cash"] for e in trailing(events, today))


def classify_frequency(events, today):
    """依近12個月已除息次數分類：monthly(≥10)/quarterly(3~5且各月不同)/semiannual(2)/annual(1)/irregular/none。"""
    ev = trailing(events, today)
    months = sorted({e["ex_date"].month for e in ev})
    n = len(ev)
    if n == 0:
        return "none"
    if n >= 10:
        return "monthly"
    if 3 <= n <= 5 and len(months) == n:
        return "quarterly"
    if n == 2:
        return "semiannual"
    if n == 1:
        return "annual"
    return "irregular"


FREQ_LABEL = {"monthly": "月配", "quarterly": "季配", "semiannual": "半年配", "annual": "年配",
              "irregular": "不定期", "none": "近一年無配息"}


def pay_month_amounts(events, today):
    """近12個月每次配息的『發放月份→每單位金額』，供規劃器逐月試算。回傳 {1..12: 金額}。"""
    lag = median_pay_lag(events)
    out = defaultdict(float)
    for e in trailing(events, today):
        pd_, _ = effective_pay_date(e, lag)
        out[pd_.month] += e["cash"]
    return dict(out)


# ------------------------------------------------------------------ 持倉與損益（移動平均成本法）
def norm_trades(trades):
    out = []
    for i, t in enumerate(trades or []):
        d = to_date(t.get("trade_date"))
        sh = _f(t.get("shares"))
        px = _f(t.get("price"))
        side = str(t.get("side", "")).lower()
        if not d or sh <= 0 or px <= 0 or side not in ("buy", "sell"):
            continue
        out.append({"id": t.get("id", i), "symbol": str(t.get("symbol", "")).strip(), "trade_date": d,
                    "side": side, "shares": sh, "price": px, "fee": _f(t.get("fee"))})
    out.sort(key=lambda x: (x["trade_date"], 0 if x["side"] == "buy" else 1, str(x["id"])))
    return out


def default_fee(amount, discount=1.0):
    return max(1.0, round(amount * FEE_RATE * discount))


def position_summary(trades, prices, sell_tax=ETF_SELL_TAX):
    """
    trades: 資料庫列；prices: {symbol: 現價}。
    回傳 {symbol: {shares, avg_cost, cost_basis, price, market_value, unrealized, unrealized_pct,
                   realized, oversold}}；已全數賣出的標的 shares=0 但保留 realized。
    買進手續費計入成本；賣出扣手續費與證交稅後計入已實現損益。
    """
    pos = {}
    for t in norm_trades(trades):
        p = pos.setdefault(t["symbol"], {"shares": 0.0, "cost": 0.0, "realized": 0.0, "oversold": False})
        amt = t["shares"] * t["price"]
        fee = t["fee"] if t["fee"] > 0 else default_fee(amt)
        if t["side"] == "buy":
            p["shares"] += t["shares"]
            p["cost"] += amt + fee
        else:
            sell_sh = min(t["shares"], p["shares"])
            if t["shares"] > p["shares"] + 1e-9:
                p["oversold"] = True
            if p["shares"] > 0 and sell_sh > 0:
                avg = p["cost"] / p["shares"]
                tax = amt * sell_tax * (sell_sh / t["shares"])
                p["realized"] += sell_sh * t["price"] - fee * (sell_sh / t["shares"]) - tax - avg * sell_sh
                p["cost"] -= avg * sell_sh
                p["shares"] -= sell_sh
    out = {}
    for sym, p in pos.items():
        px = _f((prices or {}).get(sym))
        sh = p["shares"]
        mv = sh * px if px > 0 else None
        unreal = (mv - p["cost"]) if mv is not None else None
        out[sym] = {"shares": sh, "avg_cost": (p["cost"] / sh) if sh > 0 else 0.0, "cost_basis": p["cost"],
                    "price": px if px > 0 else None, "market_value": mv, "unrealized": unreal,
                    "unrealized_pct": (unreal / p["cost"] * 100) if (unreal is not None and p["cost"] > 0) else None,
                    "realized": p["realized"], "oversold": p["oversold"]}
    return out


def shares_before(trades, symbol, before_date):
    """除息日前一個營業日收盤時的持股 = 成交日 < before_date 的買賣淨額。"""
    sh = 0.0
    for t in norm_trades(trades):
        if t["symbol"] == symbol and t["trade_date"] < before_date:
            sh += t["shares"] if t["side"] == "buy" else -t["shares"]
    return max(0.0, sh)


# ------------------------------------------------------------------ 配息入帳（已領 / 待發 / 預估）
def net_payment(gross, apply_nhi=True, apply_fee=True, ratio=1.0):
    """ratio＝這次配息中屬於『股利或盈餘所得(54C)』的比例。二代健保只對這一塊計費，且 2 萬門檻也是看這一塊的金額；
    財產交易所得(資本利得)與收益平準金不計（見今周刊/ETtoday 2026-09~10 對 00919/00878/0056 的說明）。預設 1.0＝保守地全部計費。"""
    gross = math.floor(gross)
    base = math.floor(gross * min(1.0, max(0.0, ratio if ratio is not None else 1.0)))
    nhi = round(base * NHI_RATE) if (apply_nhi and base >= NHI_THRESHOLD) else 0
    fee = REMIT_FEE if (apply_fee and gross > 0) else 0
    return gross, nhi, fee, max(0, gross - nhi - fee)


def dividend_cashflows(trades, events, today, apply_nhi=True, apply_fee=True, ratios=None, default_ratio=1.0):
    """
    已持有過的標的，逐筆配息事件：
      status = received(已入帳) / pending(已除息、待發放) / upcoming(已公告、尚未除息，以目前持股估算)
    回傳 list[dict]，依發放日排序。
    """
    ev_all = norm_events(events)
    by_sym = defaultdict(list)
    for e in ev_all:
        by_sym[e["symbol"]].append(e)
    syms = {t["symbol"] for t in norm_trades(trades)}
    rows = []
    for sym in syms:
        evs = by_sym.get(sym, [])
        lag = median_pay_lag(evs)
        for e in evs:
            if e["ex_date"] > today:
                sh = shares_before(trades, sym, today + dt.timedelta(days=1))   # 以目前持股估
            else:
                sh = shares_before(trades, sym, e["ex_date"])
            if sh <= 0:
                continue
            pay, est = effective_pay_date(e, lag)
            rt = (ratios or {}).get(sym)
            gross, nhi, fee, net = net_payment(sh * e["cash"], apply_nhi, apply_fee, default_ratio if rt is None else rt)
            status = "received" if pay <= today else ("pending" if e["ex_date"] <= today else "upcoming")
            rows.append({"symbol": sym, "ex_date": e["ex_date"], "pay_date": pay, "pay_estimated": est,
                         "shares": sh, "cash_per_unit": e["cash"], "gross": gross, "nhi": nhi,
                         "fee": fee, "net": net, "status": status})
    rows.sort(key=lambda r: (r["pay_date"], r["symbol"]))
    return rows


def project_income(trades, events, today, apply_nhi=True, apply_fee=True, months=12, ratios=None, default_ratio=1.0):
    """
    未來 N 個月入帳預估：把「已公告但未發放」的事件當實際值；其餘以各檔『去年同期』配息為樣板、用目前持股推算，
    樣板與已公告事件的除息日相差 ≤25 天視為同一筆（不重複計算）。
    回傳 list[{pay_date, symbol, gross, net, kind('announced'|'projected')}]。
    """
    ev_all = norm_events(events)
    by_sym = defaultdict(list)
    for e in ev_all:
        by_sym[e["symbol"]].append(e)
    horizon = today + dt.timedelta(days=int(months * 30.5))
    pos_sh = {}
    for t in norm_trades(trades):
        pos_sh[t["symbol"]] = pos_sh.get(t["symbol"], 0.0) + (t["shares"] if t["side"] == "buy" else -t["shares"])
    rows = []
    for sym, sh in pos_sh.items():
        if sh <= 0:
            continue
        evs = by_sym.get(sym, [])
        lag = median_pay_lag(evs)
        rt = (ratios or {}).get(sym)
        rt = default_ratio if rt is None else rt
        announced = []
        for e in evs:
            pay, _ = effective_pay_date(e, lag)
            if pay > today:      # 尚未入帳
                if e["ex_date"] <= today:
                    s_ = shares_before(trades, sym, e["ex_date"])
                else:
                    s_ = sh
                if s_ > 0:
                    g, _, _, n = net_payment(s_ * e["cash"], apply_nhi, apply_fee, rt)
                    rows.append({"pay_date": pay, "symbol": sym, "gross": g, "net": n, "kind": "announced"})
                    announced.append(e["ex_date"])
        for e in trailing(evs, today):
            nxt_ex = e["ex_date"] + dt.timedelta(days=365)
            if nxt_ex <= today:
                continue
            if any(abs((nxt_ex - a).days) <= 25 for a in announced):
                continue
            pay, _ = effective_pay_date(e, lag)
            nxt_pay = pay + dt.timedelta(days=365)
            if nxt_pay > horizon:
                continue
            g, _, _, n = net_payment(sh * e["cash"], apply_nhi, apply_fee, rt)
            rows.append({"pay_date": nxt_pay, "symbol": sym, "gross": g, "net": n, "kind": "projected"})
    rows.sort(key=lambda r: (r["pay_date"], r["symbol"]))
    return rows


def monthly_buckets(rows, key="net"):
    out = defaultdict(float)
    for r in rows:
        out[r["pay_date"].strftime("%Y-%m")] += r[key]
    return dict(sorted(out.items()))


def holdings_by_frequency(trades, events, prices, today):
    """目前持股依『月配/季配/…』分組：檔數、市值、近12月每單位配息推得的預估年領、平均每月、每次約領。"""
    pos = position_summary(trades, prices)
    ev_all = norm_events(events)
    by_sym = defaultdict(list)
    for e in ev_all:
        by_sym[e["symbol"]].append(e)
    groups = {}
    for sym, p in pos.items():
        if p["shares"] <= 0:
            continue
        evs = by_sym.get(sym, [])
        fq = classify_frequency(evs, today)
        n_ev = len(trailing(evs, today))
        annual_unit = annual_dividend_per_unit(evs, today)
        g = groups.setdefault(fq, {"count": 0, "market_value": 0.0, "cost": 0.0, "annual": 0.0, "events_per_year": 0})
        g["count"] += 1
        g["market_value"] += p["market_value"] or 0.0
        g["cost"] += p["cost_basis"]
        g["annual"] += p["shares"] * annual_unit
        g["events_per_year"] = max(g["events_per_year"], n_ev)
    for fq, g in groups.items():
        g["monthly_avg"] = g["annual"] / 12
        g["per_payment"] = (g["annual"] / g["events_per_year"]) if g["events_per_year"] else 0.0
        g["yield_pct_on_cost"] = (g["annual"] / g["cost"] * 100) if g["cost"] > 0 else None
    return groups


# ------------------------------------------------------------------ 候選 ETF 排行
def candidate_table(master_rows, events, today, min_events=1):
    """master_rows: etf_master 列；回傳每檔 {symbol,name,freq,price,annual,yield_pct,n_events,pay_months{月:金額},
    last_ex,next_ex,lag, is_bond}，只含有現價且近12月有配息者。"""
    ev_all = norm_events(events)
    by_sym = defaultdict(list)
    for e in ev_all:
        by_sym[e["symbol"]].append(e)
    out = []
    for m in master_rows or []:
        sym = str(m.get("symbol", "")).strip()
        price = _f(m.get("last_price"))
        evs = by_sym.get(sym, [])
        tr = trailing(evs, today)
        if price <= 0 or len(tr) < min_events or m.get("active", True) is False:
            continue
        annual = sum(e["cash"] for e in tr)
        fut = [e for e in evs if e["ex_date"] > today]
        p1y = _f(m.get("price_1y"))
        listed = to_date(m.get("listed_date"))
        first_ev = evs[0]["ex_date"] if evs else None
        # 上市(或首次配息)未滿一年：近12月配息次數不足一年份，年領/殖利率會被低估或無法代表常態
        # 上市未滿約一年：①一年前沒有收盤價(價格歷史不足，price_1y 空白但有現價) ②或首次配息距今不到 330 天且配息次數不足一整年
        young = bool((price > 0 and p1y <= 0 and m.get("price_1y") in (None, "")) or (listed and (today - listed).days < 365)
                     or (first_ev and (today - first_ev).days < 330 and len(tr) < 12))
        freq_ = classify_frequency(evs, today)
        mult = FREQ_PER_YEAR.get(freq_, len(tr))                  # 一年配幾次（不定期者用近12月實際次數）
        ex_months = sorted({e["ex_date"].month for e in tr})
        name_ = m.get("name") or ""
        out.append({"kind": etf_kind(sym, name_), "foreign": is_foreign(name_), "active_etf": is_active_etf(sym, name_),
                    "ex_months": ex_months, "ex_group": ex_group_of(ex_months, freq_),
                    # 三種「年領」口徑：近12月實際(保守)／最近一次×年配次數／單次最高×年配次數(宣傳常用的樂觀口徑)
                    "annual_latest": tr[-1]["cash"] * mult, "annual_peak": max(e["cash"] for e in tr) * mult,
                    "symbol": sym, "ratio": (None if m.get("div_income_ratio") in (None, "") else _f(m.get("div_income_ratio"))),
                    "young": young, "listed_date": listed,
                    "ret_1y_price": ((price / p1y - 1) * 100) if p1y > 0 else None,
                    "ret_1y_total": (((price - p1y + annual) / p1y) * 100) if p1y > 0 else None,
                    "vol": (None if m.get("vol_1y") in (None, "") else _f(m.get("vol_1y"))),
                    "mdd": (None if m.get("mdd_1y") in (None, "") else _f(m.get("mdd_1y"))),
                    "sharpe": (None if m.get("sharpe_1y") in (None, "") else _f(m.get("sharpe_1y"))),
                    "beta": (None if m.get("beta_1y") in (None, "") else _f(m.get("beta_1y"))),
                    "active": m.get("active", True) is not False, "name": m.get("name") or "", "freq": classify_frequency(evs, today),
                    "price": price, "annual": annual, "yield_pct": annual / price * 100,
                    "n_events": len(tr), "pay_months": pay_month_amounts(evs, today),
                    "last_ex": tr[-1]["ex_date"], "next_ex": (min(e["ex_date"] for e in fut) if fut else None),
                    "lag": median_pay_lag(evs),
                    "is_bond": sym.upper().endswith(("B", "D")) or ("債" in (m.get("name") or "")), "min_event": min(e["cash"] for e in tr),
                    "max_event": max(e["cash"] for e in tr)})
    out.sort(key=lambda r: -r["yield_pct"])
    return out


# ------------------------------------------------------------------ 規劃器：我想月領 X 元
def _round_up_shares(x, lot):
    if lot <= 1:
        return max(1, int(math.ceil(x)))
    return max(lot, int(math.ceil(x / lot)) * lot)


def _monthly_gross(c, shares):
    """該檔持有 shares 股時，12 個日曆月份（以發放月計）的毛配息金額。"""
    m = {k: 0.0 for k in range(1, 13)}
    for month, per_unit in c["pay_months"].items():
        m[int(month)] += math.floor(shares * per_unit)
    return m


def _monthly_net(per_month_gross, apply_nhi, apply_fee, ratio=1.0):
    out = {}
    for k, g in per_month_gross.items():
        out[k] = net_payment(g, apply_nhi, apply_fee, ratio)[3] if g > 0 else 0
    return out


def _pr(p, default_ratio):
    r = p.get("ratio")
    return default_ratio if r is None else r


def plan_income(target_monthly, picks, weights=None, lot=LOT, apply_nhi=True, apply_fee=True, default_ratio=1.0):
    """
    目標：12 個月平均『每月實領』≥ target_monthly（扣二代健保/匯費之後）。
    picks：candidate_table 的列（已由使用者選定/自動建議）；weights：各檔「年領金額占比」，預設等分。
    回傳 {rows:[每檔配置], monthly_net:{1..12}, avg_monthly, min_month, max_month, capital, blended_yield_pct,
          achieved(bool)}。
    做法：先用毛年領粗算股數，再用實際逐月扣費後金額反覆放大(最多30次)直到平均月領達標，最後依整張/零股無條件進位。
    """
    picks = [p for p in picks if p["annual"] > 0]
    if not picks or target_monthly <= 0:
        return None
    if not weights:
        weights = {p["symbol"]: 1.0 / len(picks) for p in picks}
    tw = sum(weights.get(p["symbol"], 0) for p in picks) or 1.0
    w = {p["symbol"]: weights.get(p["symbol"], 0) / tw for p in picks}

    def build(scale):
        alloc = {}
        for p in picks:
            raw = target_monthly * 12 * w[p["symbol"]] * scale / p["annual"]
            alloc[p["symbol"]] = _round_up_shares(raw, lot) if w[p["symbol"]] > 0 else 0
        mg = {k: 0.0 for k in range(1, 13)}
        for p in picks:
            for k, v in _monthly_gross(p, alloc[p["symbol"]]).items():
                mg[k] += v
        return alloc, mg

    scale = 1.0
    alloc, mg = build(scale)
    for _ in range(30):
        # 每月實領須逐「檔」逐「次」扣費，這裡用各檔分開計算再加總
        mn = {k: 0 for k in range(1, 13)}
        for p in picks:
            for k, v in _monthly_net(_monthly_gross(p, alloc[p["symbol"]]), apply_nhi, apply_fee, _pr(p, default_ratio)).items():
                mn[k] += v
        avg = sum(mn.values()) / 12
        if avg >= target_monthly:
            break
        scale *= max(1.002, target_monthly / max(avg, 1))
        alloc, mg = build(scale)
    mn = {k: 0 for k in range(1, 13)}
    rows = []
    capital = 0.0
    annual_gross = 0.0
    for p in picks:
        sh = alloc[p["symbol"]]
        pm_net = _monthly_net(_monthly_gross(p, sh), apply_nhi, apply_fee, _pr(p, default_ratio))
        for k, v in pm_net.items():
            mn[k] += v
        cap = sh * p["price"]
        capital += cap
        ag = sh * p["annual"]
        annual_gross += ag
        rows.append({"symbol": p["symbol"], "name": p["name"], "freq": p["freq"], "price": p["price"],
                     "shares": sh, "lots": sh / LOT, "capital": cap, "annual_gross": ag,
                     "yield_pct": p["yield_pct"], "n_events": p["n_events"],
                     "per_payment_avg": ag / p["n_events"] if p["n_events"] else 0.0})
    vals = list(mn.values())
    avg = sum(vals) / 12
    return {"rows": rows, "monthly_net": mn, "avg_monthly": avg, "min_month": min(vals), "max_month": max(vals),
            "capital": capital, "annual_gross": annual_gross,
            "blended_yield_pct": (annual_gross / capital * 100) if capital else 0.0,
            "achieved": avg >= target_monthly - 1e-6, "months_with_income": sum(1 for v in vals if v > 0)}


def suggest_combo(cands, mode="auto", include_bond=False, min_events=3, max_yield_pct=15.0):
    """
    自動建議（只用事實排序，不預測）：
      monthly   → 近12月殖利率最高、且「月配(≥10次)」的 1 檔。
      quarterly → 季配中，依發放月份的循環(月份 mod 3)分成 3 組，各取殖利率最高 1 檔 → 三檔錯開、每月都有入帳。
      auto      → 兩者各產生一組方案，回傳 list[{label, picks}]。
    排除：近12月配息次數 < min_events、殖利率 > max_yield_pct（過高常含本金或異常，需人工審視）、債券ETF(可選)。
    """
    pool = [c for c in cands if c["n_events"] >= min_events and c["yield_pct"] <= max_yield_pct
            and (include_bond or not c["is_bond"])]
    plans = []
    if mode in ("monthly", "auto"):
        m = [c for c in pool if c["freq"] == "monthly"]
        if m:
            plans.append({"label": "月配單押（殖利率最高的月配ETF）", "picks": [max(m, key=lambda c: c["yield_pct"])]})
            if len(m) >= 3:
                plans.append({"label": "月配前3名分散", "picks": sorted(m, key=lambda c: -c["yield_pct"])[:3]})
    if mode in ("quarterly", "auto"):
        q = [c for c in pool if c["freq"] == "quarterly"]
        groups = defaultdict(list)
        for c in q:
            ms = sorted(c["pay_months"].keys())
            groups[ms[0] % 3].append(c) if ms else None
        if len(groups) == 3:
            plans.append({"label": "季配三檔錯開（每月都有入帳）",
                          "picks": [max(g, key=lambda c: c["yield_pct"]) for _, g in sorted(groups.items())]})
    return plans


# ====================================================================== 2026-10-06 ETF 改版：分類／稅務／資金配置
# 目標：把「選股、資金、稅」放在同一個簡單流程——你有多少本金、挑哪幾檔（高股息／市值型都可）、每月實領多少、稅後剩多少。
# 全部是純函式（不連網、不依賴 streamlit），test_etf_core.py 離線驗證。
FREQ_PER_YEAR = {"monthly": 12, "quarterly": 4, "semiannual": 2, "annual": 1}

# ---------------------------------------------------------------- ETF 分類（只靠「名稱關鍵字＋代號格式」，僅供篩選；以投信公開說明書為準）
KIND_LABEL = {"dividend": "高股息", "cap": "市值型/寬基", "theme": "主題/產業", "bond": "債券", "lev": "槓桿/反向/商品", "other": "其他"}
_DIV_KW = ("高股息", "高息", "優息", "股利", "股息", "收益", "鑫收", "豐收", "入息", "收息", "高填息", "息成長")
_CAP_KW = ("台灣50", "臺灣50", "台50", "臺50", "中型100", "MSCI台灣", "加權", "富櫃50", "市值", "藍籌", "公司治理", "領袖50",
           "智慧50", "TOP50", "旗艦50", "優選50", "龍頭等權", "ESG永續", "台灣ESG", "台ESG", "S&P500", "標普500", "NASDAQ", "那斯達克",
           "道瓊", "全球品牌", "全球菁英", "世界股票", "美國50", "日本東證", "日經225", "臺灣中小", "台灣中小", "中小", "上櫃ESG", "淨零ESG")
_THEME_KW = ("科技", "半導體", "AI", "5G", "電動車", "生技", "基因", "電池", "綠能", "通訊", "金融", "資安", "航運", "太空", "元宇宙",
             "FANG", "機器人", "航太", "IC設計", "晶圓", "PCB", "電力", "算力", "稀土", "數位支付", "智能車", "未來車", "電子", "REITs",
             "不動產", "地產", "能源", "潔淨", "創新", "成長", "動能")
_THEME_STRONG = ("生技", "半導體", "電動車", "5G", "資安", "航運", "太空", "元宇宙", "機器人", "航太", "IC設計", "晶圓", "PCB", "電池", "綠能")
_FOREIGN_KW = ("中國", "美國", "日本", "日經", "越南", "印度", "歐洲", "全球", "S&P", "標普", "NASDAQ", "那斯達克", "道瓊", "恒生", "上証",
               "滬深", "深100", "深証", "韓", "KOSPI", "US", "北美", "新興", "亞太", "澳洲", "FANG", "東證", "費城", "台日韓", "台美", "MAG7",
               "世界", "ARK")


def _sym_suffix(sym):
    """台股 ETF 代號格式＝數字(4~6碼)＋可選的英文字尾（B=債券、L/R=槓反、U=商品期貨、A=主動式、K/C=其他幣別櫃台、T=平衡型）。純英文代號不判字尾。"""
    s = str(sym or "").strip().upper()
    return s[-1] if len(s) >= 5 and s[-1].isalpha() and s[:-1].isdigit() else ""


def etf_kind(symbol, name):
    """回傳 dividend / cap / theme / bond / lev / other。順序很重要：槓反→債券→(REITs/主題中的『入息』不算高股息)→高股息→市值型→主題。"""
    sym = str(symbol or "").strip().upper()
    nm = str(name or "")
    suf = _sym_suffix(sym)
    if suf in ("L", "R", "U") or "正2" in nm or "反1" in nm or nm.startswith("期"):
        return "lev"
    if suf in ("B", "D") or "債" in nm:
        return "bond"
    if any(k in nm for k in ("REITs", "不動產", "地產")):
        return "theme"
    if any(k in nm for k in _DIV_KW):
        return "dividend"
    if any(k in nm for k in _THEME_STRONG):
        return "theme"
    if any(k in nm for k in _CAP_KW):
        return "cap"
    if any(k in nm for k in _THEME_KW):
        return "theme"
    return "other"


def is_foreign(name):
    nm = str(name or "")
    return any(k in nm for k in _FOREIGN_KW)


def is_active_etf(symbol, name):
    """主動式 ETF（代號 A 結尾或名稱含『主動』）；上市時間短、無長期配息紀錄，預設不納入建議。"""
    return _sym_suffix(symbol) == "A" or "主動" in str(name or "")


def concentration_tag(tsmc_pct, top3_pct, top10_pct):
    """【2026-10-07 F10】集中度標記（用已揭露持股；揭露不全時是下限）。
    高含積量：台積電權重 ≥25%；含積量中：10～25%；其餘看前 3 大：≥45% 集中、否則分散；沒資料 → '—'。"""
    if tsmc_pct is None and top3_pct is None:
        return "—"
    t = float(tsmc_pct or 0.0)
    if t >= 25:
        return "🔴 高含積量"
    if t >= 10:
        return "🟡 含積量中"
    if top3_pct is not None and float(top3_pct) >= 45:
        return "🟠 前三大集中"
    return "🟢 分散"


# ---------------------------------------------------------------- 季配的「錯開組」：除息月 1/4/7/10＝A、2/5/8/11＝B、3/6/9/12＝C
EX_GROUPS = {"A": (1, 4, 7, 10), "B": (2, 5, 8, 11), "C": (3, 6, 9, 12)}


def ex_group_of(ex_months, freq):
    """季配且各次除息月同餘(mod 3)才歸組，否則 None；月配回傳 'M'。"""
    if freq == "monthly":
        return "M"
    if freq != "quarterly" or not ex_months:
        return None
    rs = {m % 3 for m in ex_months}
    if len(rs) != 1:
        return None
    return {1: "A", 2: "B", 0: "C"}[next(iter(rs))]


# ---------------------------------------------------------------- 綜合所得稅（只算『股利所得』這一塊的增減）
DIV_CREDIT_RATE = 0.085       # 合併計稅：股利可抵減稅額 8.5%
DIV_CREDIT_CAP = 80000        # 每一申報戶上限 8 萬
SEPARATE_RATE = 0.28          # 分開計稅：股利單獨 28%
BRACKETS = (0.0, 0.05, 0.12, 0.20, 0.30, 0.40)


def income_tax_on_dividends(taxable_div, bracket):
    """
    taxable_div：一年內『應課綜所稅的股利所得』合計（元；ETF 配息中屬股利/盈餘 54C 的部分，證券交易所得停徵不計）。
    bracket：你『加入股利前』的綜所稅邊際稅率（0/5/12/20/30/40%）。
    合併計稅＝股利×邊際稅率 − min(股利×8.5%, 8 萬)；結果可為負數（抵減額大於應納稅額時可退稅）。
    分開計稅＝股利×28%。取較低者。
    簡化（會寫在畫面上）：沒考慮加入股利後跳級距、也沒計免稅額/扣除額、利息所得(5A)與海外所得(基本所得額)。
    """
    d = max(0.0, float(taxable_div or 0.0))
    credit = min(d * DIV_CREDIT_RATE, DIV_CREDIT_CAP)
    combined = d * float(bracket) - credit
    separate = d * SEPARATE_RATE
    best = "combined" if combined <= separate else "separate"
    return {"taxable": d, "credit": credit, "combined": combined, "separate": separate,
            "best": best, "best_tax": min(combined, separate)}



# ---------------------------------------------------------------- 候選 ETF 排行／錯開建議
def rank_value(c, rank_by):
    if rank_by == "total":
        v = c.get("ret_1y_total")
    elif rank_by == "sharpe":
        v = c.get("sharpe")
    elif rank_by == "lowrisk":
        v = (-abs(c["mdd"])) if c.get("mdd") is not None else None      # 最大回撤絕對值小的在前
    else:
        v = c.get("yield_pct")
    return float("-inf") if v is None else float(v)


def filter_candidates(cands, kinds=("dividend", "cap"), include_bond=False, include_active=False, allow_young=False,
                      max_yield_pct=15.0, min_events=3):
    """規劃器可選池：預設排除債券/槓反/主動式/上市未滿一年/殖利率過高(常含本金)/近一年配息不足 3 次。"""
    out = []
    for c in cands:
        k = c.get("kind", "other")
        if k == "lev":
            continue
        if k == "bond" and not include_bond:
            continue
        if kinds and k not in kinds and not (k == "bond" and include_bond):
            continue
        if c.get("active_etf") and not include_active:
            continue
        if c.get("young") and not allow_young:
            continue
        # 近 12 月配息次數門檻只套在高股息類（市值型 ETF 常是半年配 2 次，不應因此被排除）
        if c["yield_pct"] > max_yield_pct or c["n_events"] < (min_events if k == "dividend" else 1):
            continue
        out.append(c)
    return out


def suggest_ladder(cands, rank_by="yield", top=3):
    """把候選依『配息節奏』分成 A/B/C(季配三組)、M(月配)，各組依 rank_by 排序取前 top 檔；
    A+B+C 各挑一檔＝每月都有入帳；M 單挑一檔也是每月入帳。回傳 {'A':[..],'B':[..],'C':[..],'M':[..],'other':[..]}。"""
    g = {"A": [], "B": [], "C": [], "M": [], "other": []}
    for c in cands:
        g.setdefault(c.get("ex_group") or "other", []).append(c)
    for k in g:
        g[k] = sorted(g[k], key=lambda c: (-rank_value(c, rank_by), -c["yield_pct"], c["symbol"]))[:top]
    return g


def kind_summary(cands):
    """各類型的事實統計（中位數）：檔數、殖利率、近一年含息總報酬、最大回撤、波動。用來比較『高股息 vs 市值型』，不是預測。"""
    import statistics as st_

    def med(xs):
        xs = [x for x in xs if x is not None]
        return round(st_.median(xs), 2) if xs else None

    out = {}
    for k in KIND_LABEL:
        g = [c for c in cands if c.get("kind") == k and not c.get("young") and c["n_events"] >= 1]
        if not g:
            continue
        out[k] = {"label": KIND_LABEL[k], "n": len(g), "yield_med": med([c["yield_pct"] for c in g]),
                  "total_med": med([c.get("ret_1y_total") for c in g]), "mdd_med": med([c.get("mdd") for c in g]),
                  "vol_med": med([c.get("vol") for c in g])}
    return out


# ---------------------------------------------------------------- 資金配置 → 每月實領 → 稅後
def allocate_shares(capital, picks, alloc=None, lot=LOT, buy_discount=1.0):
    """capital：總資金（含買進手續費）；alloc：{symbol: 占比}（任意正數，會正規化；預設等分）。
    每檔買『不超過其預算』的最大整張/零股，預算已先扣掉買進手續費(0.1425%×折扣)與每檔 1 元進位誤差。
    回傳 {symbol: 股數}。剩餘零頭不再重分配（實務上買不起整張的零頭就是現金）。"""
    picks = [p for p in picks if p.get("price", 0) > 0]
    if not picks or capital <= 0:
        return {}
    w = {p["symbol"]: max(0.0, float((alloc or {}).get(p["symbol"], 1.0))) for p in picks}
    tw = sum(w.values())
    if tw <= 0:
        w = {p["symbol"]: 1.0 for p in picks}
        tw = float(len(picks))
    spendable = capital / (1.0 + FEE_RATE * buy_discount)
    out = {}
    for p in picks:
        budget = spendable * w[p["symbol"]] / tw - 1.0
        units = max(0, math.floor(budget / p["price"]))
        sh = (units // lot) * lot if lot > 1 else units
        out[p["symbol"]] = int(sh)
    return out


def evaluate_holdings(picks, shares, default_ratio=1.0, apply_nhi=True, apply_fee=True, bracket=0.12, buy_discount=1.0, capital=None):
    """
    已知『買哪幾檔、各買幾股』，算出：逐月入帳（毛/補充保費/匯費/實領）、全年稅前稅後、三種年領口徑、買進手續費。
    年領用『近 12 個月實際配息』（保守）；latest/peak 兩種口徑只給年總額供對照（宣傳常用的是 peak）。
    補充保費逐『檔』逐『月』判斷：只對 54C 比例的那一塊、且該次 ≥ 2 萬才扣。綜所稅只算股利所得的增減，見 income_tax_on_dividends。
    """
    rows = []
    mg = {k: 0.0 for k in range(1, 13)}
    mnhi = {k: 0 for k in range(1, 13)}
    mfee = {k: 0 for k in range(1, 13)}
    mnet = {k: 0 for k in range(1, 13)}
    taxable = 0.0
    cost = 0.0
    buy_fee = 0.0
    nhi_hits, near_hits = [], []
    max_base = 0
    for p in picks:
        sh = int(shares.get(p["symbol"], 0) or 0)
        if sh <= 0:
            continue
        r = min(1.0, max(0.0, _pr(p, default_ratio)))
        gm = _monthly_gross(p, sh)
        a_g = a_nhi = a_fee = a_net = 0
        top_base = 0
        for k, g in gm.items():
            if g <= 0:
                continue
            gg, nhi, fee, net = net_payment(g, apply_nhi, apply_fee, r)
            mg[k] += gg
            mnhi[k] += nhi
            mfee[k] += fee
            mnet[k] += net
            a_g += gg
            a_nhi += nhi
            a_fee += fee
            a_net += net
            top_base = max(top_base, math.floor(gg * r))
        taxable += a_g * r
        c_ = sh * p["price"]
        cost += c_
        bf = default_fee(c_, buy_discount)
        buy_fee += bf
        max_base = max(max_base, top_base)
        if top_base >= NHI_THRESHOLD:
            nhi_hits.append(p["symbol"])
        elif top_base >= NHI_THRESHOLD * 0.75:
            near_hits.append(p["symbol"])
        n_ev = max(1, p.get("n_events") or 1)
        rows.append({"symbol": p["symbol"], "name": p.get("name", ""), "kind": p.get("kind", "other"), "freq": p.get("freq", ""),
                     "ex_group": p.get("ex_group"), "price": p["price"], "shares": sh, "lots": sh / LOT, "capital": c_,
                     "yield_pct": p.get("yield_pct", 0.0), "ratio_used": r,
                     "annual_gross": a_g, "annual_nhi": a_nhi, "annual_fee": a_fee, "annual_net": a_net,
                     "annual_latest": sh * p.get("annual_latest", p.get("annual", 0.0)),
                     "annual_peak": sh * p.get("annual_peak", p.get("annual", 0.0)),
                     "per_payment_avg": a_g / n_ev, "max_payment_base": top_base,
                     "young": bool(p.get("young")), "foreign": bool(p.get("foreign")), "n_events": p.get("n_events", 0)})
    ann_g = sum(mg.values())
    ann_nhi = sum(mnhi.values())
    ann_fee = sum(mfee.values())
    ann_net = sum(mnet.values())
    tax = income_tax_on_dividends(taxable, bracket)
    after = ann_net - tax["best_tax"]
    cap_in = capital if capital else cost
    vals = list(mnet.values())
    out = {"rows": rows, "cost": cost, "capital": capital if capital else cost,
           "cash_left": (capital - cost - buy_fee) if capital else 0.0, "buy_fee": buy_fee,
           "monthly_gross": mg, "monthly_nhi": mnhi, "monthly_fee": mfee, "monthly_net": mnet,
           "annual_gross": ann_g, "annual_nhi": ann_nhi, "annual_fee": ann_fee, "annual_net": ann_net,
           "taxable_dividend": taxable, "income_tax": tax, "annual_net_after_tax": after,
           "monthly_avg_net": ann_net / 12, "monthly_avg_after_tax": after / 12,
           "min_month": min(vals) if vals else 0, "max_month": max(vals) if vals else 0,
           "months_with_income": sum(1 for v in vals if v > 0),
           "yield_gross_pct": (ann_g / cost * 100) if cost else 0.0,
           "yield_after_tax_pct": (after / cap_in * 100) if cap_in else 0.0,
           "basis_annual": {"trailing": ann_g, "latest": sum(x["annual_latest"] for x in rows), "peak": sum(x["annual_peak"] for x in rows)},
           "nhi_hits": nhi_hits, "nhi_near": near_hits, "max_payment_base": max_base}
    out["basis_monthly"] = {k: v / 12 for k, v in out["basis_annual"].items()}
    return out


def plan_by_capital(capital, picks, alloc=None, lot=LOT, **kw):
    """『我有 X 元本金』：依占比買進後的每月實領/稅後。kw 傳給 evaluate_holdings。"""
    sh = allocate_shares(capital, picks, alloc, lot, kw.get("buy_discount", 1.0))
    return evaluate_holdings(picks, sh, capital=capital, **kw)


def plan_for_after_tax(target_after_tax, picks, weights=None, lot=LOT, bracket=0.12, apply_nhi=True, apply_fee=True,
                       default_ratio=1.0, buy_discount=1.0, max_iter=10):
    """『我要每月稅後實領 X 元』→ 需要買多少。先用 plan_income 反推稅前，再用 evaluate_holdings 扣綜所稅，差多少就把稅前目標加多少，重複到達標。
    回傳 evaluate_holdings 的結果（多一個 achieved 欄位）；picks 沒配息資料則 None。"""
    t = float(target_after_tax)
    best = None
    for _ in range(max_iter):
        r = plan_income(t, picks, weights, lot, apply_nhi, apply_fee, default_ratio)
        if not r:
            return None
        shares = {x["symbol"]: int(x["shares"]) for x in r["rows"]}
        best = evaluate_holdings(picks, shares, default_ratio, apply_nhi, apply_fee, bracket, buy_discount)
        gap = target_after_tax - best["monthly_avg_after_tax"]
        if gap <= 1e-9:
            break
        t += max(gap, 1.0)
    best["achieved"] = best["monthly_avg_after_tax"] >= target_after_tax - 1e-6
    best["target_after_tax"] = float(target_after_tax)
    return best


# ------------------------------------------------------------------ 2026-10-10 貼文研究建議落實（純函式，不連網、不連 DB）
COMPOSITION_KEEP = 12   # 每檔最多保留最近 12 次配息組成


def latest_ex_by_symbol(events):
    """每檔最近一次除息事件：{symbol: {"ex_date", "cash", "pay_date"}}。events 為 etf_dividend_events 列（dict）。"""
    out = {}
    for e in events or []:
        sym = str(e.get("symbol") or "").strip()
        exd = str(e.get("ex_date") or "")[:10]
        if not sym or not exd:
            continue
        if sym not in out or exd > out[sym]["ex_date"]:
            out[sym] = {"ex_date": exd, "cash": _f(e.get("cash_per_unit"), 0.0), "pay_date": str(e.get("pay_date") or "")[:10]}
    return out


def composition_sync(latest_ex, ledger):
    """【A2】逐次配息組成台帳同步（自動化的『偵測＋沿用＋提醒』部分）。
    規則：
      • 出現新的除息日（台帳沒有這一筆）→ 新增一筆 status='carried'，54C 占比沿用上一次（沒有就留空），來源註明『沿用』。
      • 已確認（status='confirmed'）的紀錄永遠不會被自動覆寫。
      • 台帳本身只靠人工確認或投信公告來源（目前沒有可合法排程抓取的官方結構化來源，見 etf_composition 說明）。
    回傳 (new_ledger, pending)：pending＝[(symbol, ex_date)] 仍待確認（status='carried'）的紀錄。"""
    new = {k: [dict(r) for r in v] for k, v in (ledger or {}).items()}
    for sym, ex in (latest_ex or {}).items():
        recs = new.setdefault(sym, [])
        if any(r.get("ex_date") == ex["ex_date"] for r in recs):
            continue
        prev = next((r for r in reversed(recs) if r.get("ratio_54c") is not None), None)
        recs.append({"ex_date": ex["ex_date"], "ratio_54c": prev.get("ratio_54c") if prev else None,
                     "ratio_5a": prev.get("ratio_5a") if prev else None, "status": "carried",
                     "source": (f"沿用 {prev['ex_date']} 的占比" if prev else "尚無占比，待確認")})
        recs.sort(key=lambda r: r.get("ex_date") or "")
        new[sym] = recs[-COMPOSITION_KEEP:]
    pending = [(sym, r["ex_date"]) for sym, recs in new.items() for r in recs[-1:] if r.get("status") == "carried"]
    return new, sorted(pending)


def current_ratio(ledger, symbol):
    """規劃器用的『目前 54C 占比』＝台帳最近一筆的占比（確認或沿用皆可）；沒有則 None。"""
    recs = (ledger or {}).get(symbol) or []
    for r in reversed(recs):
        if r.get("ratio_54c") is not None:
            return float(r["ratio_54c"])
    return None


def confirm_record(ledger, symbol, ex_date, ratio_54c, ratio_5a=None, source="人工確認"):
    """人工確認某次配息的占比（status→confirmed）。找不到該除息日就新增一筆。回傳新台帳。"""
    new = {k: [dict(r) for r in v] for k, v in (ledger or {}).items()}
    recs = new.setdefault(symbol, [])
    rec = next((r for r in recs if r.get("ex_date") == ex_date), None)
    if rec is None:
        rec = {"ex_date": ex_date}
        recs.append(rec)
    rec.update({"ratio_54c": round(float(ratio_54c), 4), "ratio_5a": None if ratio_5a is None else round(float(ratio_5a), 4),
                "status": "confirmed", "source": source})
    recs.sort(key=lambda r: r.get("ex_date") or "")
    new[symbol] = recs[-COMPOSITION_KEEP:]
    return new


def small_payment_flags(rows, threshold=2000.0):
    """【C3】每次平均入帳毛額低於門檻的標的（匯費 10 元占比過高）。rows＝規劃器結果的 rows（需 symbol、per_payment_avg）。"""
    return [x.get("symbol") for x in rows or [] if x.get("per_payment_avg") is not None and float(x["per_payment_avg"]) < threshold]


def fee_share(gross, fee=REMIT_FEE):
    """一次配息的匯費占毛額比例（0~1）；毛額 ≤0 回 None。"""
    return None if not gross or gross <= 0 else fee / float(gross)


def merge_announced(events, announced):
    """【A1】把『已公告、尚未除息』的配息併入事件清單（同一檔同一除息日已存在就不重複加）。
    announced＝[{symbol, ex_date, cash_per_unit, pay_date}]；併入的列標記 source='manual_announced'。"""
    out = list(events or [])
    seen = {(str(e.get("symbol")), str(e.get("ex_date"))[:10]) for e in out}
    for a in announced or []:
        key = (str(a.get("symbol")), str(a.get("ex_date"))[:10])
        if not key[0] or not key[1] or key in seen:
            continue
        out.append({"symbol": key[0], "ex_date": key[1], "cash_per_unit": a.get("cash_per_unit"),
                    "pay_date": a.get("pay_date") or None, "pay_date_estimated": False, "source": "manual_announced"})
        seen.add(key)
    return out


def is_leveraged(symbol, name):
    """【C4】槓桿／反向商品（每日重設，無配息、長期持有有耗損）。"""
    return etf_kind(symbol, name) == "lev"


def apply_sourced(ledger, sourced, latest_ex):
    """【2026-10-10 死規則一：任何資料來源都需做驗證後再處理】
    把『來源占比檔』併入台帳。每一筆都要通過：
      • symbol 存在於 latest_ex（有配息事件）
      • ex_date 是該檔的一次實際除息日（不接受猜的日期）
      • 0 ≤ ratio_54c ≤ 1，ratio_5a 若有也要在 0~1
      • source 必須是 https 網址（沒有來源不收）
    通過才寫成 status='confirmed'；不通過的整筆退回並附原因。已人工確認（confirmed 且來源非來源檔）的不覆寫。
    回傳 (new_ledger, applied_count, rejected[(row, reason)])。"""
    new = {k: [dict(r) for r in v] for k, v in (ledger or {}).items()}
    applied, rejected = 0, []
    for row in sourced or []:
        if not isinstance(row, dict):
            rejected.append((row, "格式不是物件"))
            continue
        sym = str(row.get("symbol") or "").strip()
        exd = str(row.get("ex_date") or "")[:10]
        src = str(row.get("source") or "").strip()
        reason = None
        try:
            r54 = float(row.get("ratio_54c"))
        except (TypeError, ValueError):
            r54 = None
        r5a_raw = row.get("ratio_5a")
        try:
            r5a = None if r5a_raw in (None, "") else float(r5a_raw)
        except (TypeError, ValueError):
            r5a = -1.0
        known_ex = {(latest_ex or {}).get(sym, {}).get("ex_date")} | {r.get("ex_date") for r in new.get(sym, [])}
        if sym not in (latest_ex or {}):
            reason = "找不到此檔配息事件"
        elif not exd or exd not in known_ex:
            reason = "除息日不是此檔已知的配息日"
        elif r54 is None or not (0.0 <= r54 <= 1.0):
            reason = "54C 占比不在 0~1"
        elif r5a is not None and not (0.0 <= r5a <= 1.0):
            reason = "5A 占比不在 0~1"
        elif not src.lower().startswith("https://"):
            reason = "來源缺少 https 網址"
        if reason:
            rejected.append((row, reason))
            continue
        recs = new.setdefault(sym, [])
        rec = next((r for r in recs if r.get("ex_date") == exd), None)
        if rec is None:
            rec = {"ex_date": exd}
            recs.append(rec)
        rec.update({"ratio_54c": round(r54, 4), "ratio_5a": None if r5a is None else round(r5a, 4),
                    "status": "confirmed", "source": f"來源檔：{src}"})
        recs.sort(key=lambda r: r.get("ex_date") or "")
        new[sym] = recs[-COMPOSITION_KEEP:]
        applied += 1
    return new, applied, rejected

