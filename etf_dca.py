"""【P4｜2026-10-12，對應 C2】定期定額達標試算（純函式，可離線測試）。

只回答『如果依固定殖利率、每月定額投入，多久會達到目標』，不預測價格、不預測殖利率。
假設（一律揭露在畫面上）：
  • 價格不變，只計配息（配息是唯一的報酬來源，保守）；
  • 殖利率為固定常數，三種口徑由呼叫端給：近12月實際（保守）／最近一次×年配次數（中性）／單次最高×年配次數（樂觀，宣傳常用）；
  • 每月配息視為一次入帳，扣二代健保與匯費（與規劃器一致），未扣綜所稅；
  • 再投入：淨額（扣完健保與匯費）買回，不計再投入的買進費；每月定額買進扣 0.1425% 手續費。
達標判定：
  • 月領目標：某月『稅前實領（扣健保與匯費後）』≥ 目標；
  • 本金目標：某月市值（本金＋再投入）≥ 目標。
"""
import etf_core as E

BUY_FEE_RATE = 0.001425


def simulate(start_capital, monthly_invest, annual_yield_pct, reinvest=True, months=360,
             apply_nhi=True, apply_fee=True):
    """逐月模擬，回傳 list[dict(month, value, net)]：value＝月底市值，net＝當月稅前實領（扣健保與匯費）。"""
    v = float(start_capital or 0.0)
    rows = []
    y = float(annual_yield_pct or 0.0) / 100.0
    for m in range(1, months + 1):
        v += float(monthly_invest or 0.0) * (1 - BUY_FEE_RATE)
        gross = v * y / 12.0
        _, _nhi, _fee, net = E.net_payment(gross, apply_nhi, apply_fee)
        if reinvest:
            v += net
        rows.append({"month": m, "value": round(v, 2), "net": net})
    return rows


def months_to_target(target_kind, target_value, start_capital, monthly_invest, annual_yield_pct,
                     reinvest=True, horizon_years=30, apply_nhi=True, apply_fee=True):
    """回傳 {months, years, value_at, net_at}；達不到回傳 months=None。
    target_kind：'income'（月領目標，單位元）或 'capital'（本金目標，單位元）。"""
    if target_kind not in ("income", "capital"):
        raise ValueError("target_kind 必須是 income 或 capital")
    if not target_value or target_value <= 0:
        return {"months": None, "years": None, "value_at": None, "net_at": None, "reason": "目標金額需大於 0"}
    rows = simulate(start_capital, monthly_invest, annual_yield_pct, reinvest=reinvest,
                    months=int(horizon_years * 12), apply_nhi=apply_nhi, apply_fee=apply_fee)
    for r in rows:
        ok = r["net"] >= target_value if target_kind == "income" else r["value"] >= target_value
        if ok:
            return {"months": r["month"], "years": round(r["month"] / 12.0, 2), "value_at": r["value"],
                    "net_at": r["net"], "reason": ""}
    return {"months": None, "years": None, "value_at": rows[-1]["value"] if rows else None,
            "net_at": rows[-1]["net"] if rows else None, "reason": f"{horizon_years} 年內未達到"}


def yield_bases(cand):
    """從 candidate_table 的一列算三種年化殖利率口徑（%）。缺資料的口徑回傳 None。"""
    price = float(cand.get("price") or 0)
    out = {"近12月實際（保守）": cand.get("yield_pct")}
    for label, key in (("最近一次×年配次數（中性）", "annual_latest"), ("單次最高×年配次數（樂觀）", "annual_peak")):
        v = cand.get(key)
        out[label] = round(float(v) / price * 100, 3) if (v is not None and price > 0) else None
    return out


def range_table(cand, target_kind, target_value, start_capital, monthly_invest, reinvest=True, horizon_years=30,
                apply_nhi=True, apply_fee=True):
    """三種殖利率口徑各跑一次，回傳列表，供畫面顯示區間。"""
    rows = []
    for label, y in yield_bases(cand).items():
        if y is None:
            rows.append({"口徑": label, "年化殖利率%": None, "達標年數": None, "達標時市值": None, "達標時月領(稅前)": None, "說明": "缺資料"})
            continue
        r = months_to_target(target_kind, target_value, start_capital, monthly_invest, y, reinvest=reinvest,
                             horizon_years=horizon_years, apply_nhi=apply_nhi, apply_fee=apply_fee)
        rows.append({"口徑": label, "年化殖利率%": y, "達標年數": r["years"], "達標時市值": r["value_at"],
                     "達標時月領(稅前)": r["net_at"], "說明": r["reason"]})
    return rows


def fmt_years(y):
    return "—" if y is None else f"{y:.1f} 年"
