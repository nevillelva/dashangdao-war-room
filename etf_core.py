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
def net_payment(gross, apply_nhi=True, apply_fee=True):
    gross = math.floor(gross)
    nhi = round(gross * NHI_RATE) if (apply_nhi and gross >= NHI_THRESHOLD) else 0
    fee = REMIT_FEE if (apply_fee and gross > 0) else 0
    return gross, nhi, fee, max(0, gross - nhi - fee)


def dividend_cashflows(trades, events, today, apply_nhi=True, apply_fee=True):
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
            gross, nhi, fee, net = net_payment(sh * e["cash"], apply_nhi, apply_fee)
            status = "received" if pay <= today else ("pending" if e["ex_date"] <= today else "upcoming")
            rows.append({"symbol": sym, "ex_date": e["ex_date"], "pay_date": pay, "pay_estimated": est,
                         "shares": sh, "cash_per_unit": e["cash"], "gross": gross, "nhi": nhi,
                         "fee": fee, "net": net, "status": status})
    rows.sort(key=lambda r: (r["pay_date"], r["symbol"]))
    return rows


def project_income(trades, events, today, apply_nhi=True, apply_fee=True, months=12):
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
        announced = []
        for e in evs:
            pay, _ = effective_pay_date(e, lag)
            if pay > today:      # 尚未入帳
                if e["ex_date"] <= today:
                    s_ = shares_before(trades, sym, e["ex_date"])
                else:
                    s_ = sh
                if s_ > 0:
                    g, _, _, n = net_payment(s_ * e["cash"], apply_nhi, apply_fee)
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
            g, _, _, n = net_payment(sh * e["cash"], apply_nhi, apply_fee)
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
        if price <= 0 or len(tr) < min_events:
            continue
        annual = sum(e["cash"] for e in tr)
        fut = [e for e in evs if e["ex_date"] > today]
        out.append({"symbol": sym, "name": m.get("name") or "", "freq": classify_frequency(evs, today),
                    "price": price, "annual": annual, "yield_pct": annual / price * 100,
                    "n_events": len(tr), "pay_months": pay_month_amounts(evs, today),
                    "last_ex": tr[-1]["ex_date"], "next_ex": (min(e["ex_date"] for e in fut) if fut else None),
                    "lag": median_pay_lag(evs),
                    "is_bond": sym.upper().endswith("B"), "min_event": min(e["cash"] for e in tr),
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


def _monthly_net(per_month_gross, apply_nhi, apply_fee):
    out = {}
    for k, g in per_month_gross.items():
        out[k] = net_payment(g, apply_nhi, apply_fee)[3] if g > 0 else 0
    return out


def plan_income(target_monthly, picks, weights=None, lot=LOT, apply_nhi=True, apply_fee=True):
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
            for k, v in _monthly_net(_monthly_gross(p, alloc[p["symbol"]]), apply_nhi, apply_fee).items():
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
        pm_net = _monthly_net(_monthly_gross(p, sh), apply_nhi, apply_fee)
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
