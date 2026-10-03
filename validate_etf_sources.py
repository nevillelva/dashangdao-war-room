#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
validate_etf_sources.py —— ETF 月配規劃的「資料來源驗證」（R99新增，跑在 GitHub Actions，唯讀、不寫任何資料表）

【目的】總指揮官要求「ETF 分頁需要驗證後再動工」。這支腳本回答三個問題：
  1. 台股 ETF 清單抓得到嗎？（FinMind TaiwanStockInfo）
  2. 每檔 ETF 的「除息日、每單位配息、發放日」資料齊不齊、準不準？
       主源  = FinMind TaiwanStockDividend（有發放日欄位 CashDividendPaymentDate）
       對照源 = yfinance 的配息紀錄（除息日 + 金額）
       官方源 = 證交所 OpenAPI / 櫃買 OpenAPI（自動從 swagger 探索含除權息的端點，記錄可用性與欄位）
  3. 能不能據此做出「月配/季配/年配」分類與「除息→發放」天數分佈？（規劃器需要）

【輸出】etf_validation_out/report.md、etf_report.json、etf_table.csv，並寫進 GITHUB_STEP_SUMMARY。
【通過門檻（事先寫死，避免看完結果再放寬）】
  A 清單：抓到的 ETF 檔數 >= 100
  B 覆蓋：yfinance 近12個月有配息的 ETF 中，FinMind 也有配息紀錄的比例 >= 90%
  C 發放日：FinMind 配息紀錄中，發放日欄位有值的比例 >= 80%
  D 準確：兩來源可配對的除息事件中，每單位配息金額相差 <= 2% 的比例 >= 95%
  四項全過 → 建議以 FinMind 為主源建 ETF 分頁；C 不過但官方源有發放日 → 以官方源補發放日；否則該功能的「發放日」只能標示預估。
"""
import os
import re
import sys
import csv
import json
import time
import argparse
import datetime as dt

import requests

FINMIND_URL = "https://api.finmindtrade.com/api/v4/data"
UA = {"User-Agent": "Mozilla/5.0 (warroom-etf-validate)"}
THRESH = {"A_min_etfs": 100, "B_coverage": 0.90, "C_paydate": 0.80, "D_amount_agree": 0.95}
ETF_ID_RE = re.compile(r"^00\d{2,4}[A-Z]?$")


# ------------------------------------------------------------------ 純函式（可離線單元測試）
def classify_frequency(ex_dates):
    """ex_dates: 近12個月的除息日(date)清單 → ('monthly'|'quarterly'|'semiannual'|'annual'|'irregular'|'none', 月份集合)"""
    uniq = sorted(set(ex_dates))
    months = sorted({d.month for d in uniq})
    n = len(uniq)
    if n == 0:
        return "none", []
    if n >= 10:
        return "monthly", months
    if 3 <= n <= 5 and len(months) == n:
        return "quarterly", months
    if n == 2:
        return "semiannual", months
    if n == 1:
        return "annual", months
    return "irregular", months


def match_events(fm_events, yf_events, day_tol=3, amt_tol=0.02):
    """
    fm_events / yf_events: [(date, amount)]。以除息日相差<=day_tol天且一對一貪婪配對。
    回傳 (matched, agree)：可配對事件數、其中金額相差<=amt_tol的事件數。
    """
    used = set()
    matched = agree = 0
    for d1, a1 in sorted(fm_events):
        best = None
        for j, (d2, a2) in enumerate(sorted(yf_events)):
            if j in used:
                continue
            gap = abs((d1 - d2).days)
            if gap <= day_tol and (best is None or gap < best[0]):
                best = (gap, j, a2)
        if best:
            used.add(best[1])
            matched += 1
            a2 = best[2]
            if a1 and a2 and abs(a1 - a2) / max(a1, a2) <= amt_tol:
                agree += 1
    return matched, agree


def parse_date(x):
    if not x:
        return None
    x = str(x).strip()[:10]
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
        try:
            return dt.datetime.strptime(x, fmt).date()
        except ValueError:
            continue
    return None


def fm_cash_per_unit(row):
    """FinMind 一列的每單位現金配息 = 盈餘分配 + 法定盈餘公積（ETF 通常只有前者或另有資本利得相關欄位時以總和為準）。"""
    tot = 0.0
    for k in ("CashEarningsDistribution", "CashStatutorySurplus"):
        try:
            tot += float(row.get(k) or 0)
        except (TypeError, ValueError):
            pass
    return tot


# ------------------------------------------------------------------ 資料取得
_FM_EXHAUSTED = False   # 額度用盡後，不再對 FinMind 逐檔硬等（避免每檔卡30秒+拖到逾時）
_TOKEN_BAD = False   # FINMIND_TOKEN 被判定無效後，改用訪客額度（與正式程式 _finmind_get 的備援行為一致）


def fm_get(dataset, token, **params):
    global _TOKEN_BAD, _FM_EXHAUSTED
    if _FM_EXHAUSTED:
        return [], "rate_limited(已略過)"
    p = {"dataset": dataset, **params}
    if token and not _TOKEN_BAD:
        p["token"] = token
    for attempt in range(3):
        try:
            r = requests.get(FINMIND_URL, params=p, timeout=30)
            j = r.json()
            if j.get("msg") == "success":
                return j.get("data") or [], None
            err = f"{r.status_code} {j.get('msg')}"
            if "illegal" in str(j.get("msg", "")).lower() and "token" in p:
                _TOKEN_BAD = True
                print("⚠️ FINMIND_TOKEN 被 FinMind 判定為無效(Token is illegal)，改用訪客額度重試。")
                p.pop("token", None)
                continue
            if "limit" in str(j.get("msg", "")).lower() or "402" in str(err):
                if attempt >= 1:
                    _FM_EXHAUSTED = True
                    print(f"⚠️ FinMind 額度用盡（{j.get('msg')}），之後改為只用 yfinance/官方來源。")
                    return [], f"rate_limited: {j.get('msg')}"
                time.sleep(4)
                continue
            return [], err
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            time.sleep(1.5 * (attempt + 1))
    return [], err


def get_etf_list(token):
    rows, err = fm_get("TaiwanStockInfo", token)
    if not rows:
        return [], {"error": err}
    cats = {}
    etfs = {}
    for r in rows:
        sid = str(r.get("stock_id", "")).strip()
        cat = str(r.get("industry_category", "")).strip()
        is_etf = ("ETF" in cat.upper()) or ("受益" in cat) or bool(ETF_ID_RE.match(sid))
        if is_etf and sid:
            etfs[sid] = {"stock_id": sid, "name": r.get("stock_name", ""), "category": cat,
                         "market": r.get("type", "")}
            cats[cat] = cats.get(cat, 0) + 1
    return sorted(etfs.values(), key=lambda x: x["stock_id"]), {"categories": cats, "total_rows": len(rows)}


def yf_dividends(symbol, market_hint):
    import yfinance as yf
    order = [".TW", ".TWO"] if market_hint != "tpex" else [".TWO", ".TW"]
    for suf in order:
        try:
            s = yf.Ticker(symbol + suf).dividends
            if s is not None and len(s) > 0:
                return [(d.date(), float(a)) for d, a in s.items()]
        except Exception:
            continue
    return []


def probe_official_sources():
    """自動從 swagger 探索含「除權息/ETF/配息」的端點，抓一筆看欄位。只記錄結果，不假設格式。"""
    out = []
    for name, swagger in (("TWSE", "https://openapi.twse.com.tw/v1/swagger.json"),
                          ("TPEx", "https://www.tpex.org.tw/openapi/swagger.json")):
        try:
            r = requests.get(swagger, headers=UA, timeout=30)
            if r.status_code != 200:
                out.append({"source": name, "swagger_status": r.status_code})
                continue
            sw = r.json()
            host = ("https://openapi.twse.com.tw/v1" if name == "TWSE" else "https://www.tpex.org.tw/openapi/v1")
            keys = ("exright", "TWT48", "TWT49", "dividend", "Dividend", "ETF", "etf", "除權", "除息", "股利")
            for path, spec in (sw.get("paths") or {}).items():
                desc = json.dumps(spec, ensure_ascii=False)[:600]
                if any(k in path or k in desc for k in keys):
                    entry = {"source": name, "path": path, "summary": ""}
                    try:
                        entry["summary"] = (spec.get("get") or {}).get("summary", "")
                        rr = requests.get(host + path if path.startswith("/") else host + "/" + path,
                                          headers=UA, timeout=30)
                        entry["status"] = rr.status_code
                        if rr.status_code == 200:
                            data = rr.json()
                            if isinstance(data, list):
                                entry["rows"] = len(data)
                                entry["fields"] = list(data[0].keys()) if data else []
                                entry["has_etf_rows"] = any(
                                    ETF_ID_RE.match(str(v).strip()) for d in data[:3000]
                                    for v in list(d.values())[:4]) if data else False
                                entry["sample"] = data[0] if data else None
                    except Exception as e:
                        entry["error"] = f"{type(e).__name__}: {e}"
                    out.append(entry)
        except Exception as e:
            out.append({"source": name, "error": f"{type(e).__name__}: {e}"})
    return out


# ------------------------------------------------------------------ 主流程
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=int(os.environ.get("ETF_MAX") or "0"), help="只驗證前N檔（0=全部）")
    ap.add_argument("--out", default="etf_validation_out")
    a = ap.parse_args()
    token = os.environ.get("FINMIND_TOKEN", "").strip()
    today = dt.date.today()
    one_year_ago = today - dt.timedelta(days=365)
    os.makedirs(a.out, exist_ok=True)
    rep = {"generated": dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"), "criteria": THRESH}
    rep["token_configured"] = bool(token)

    etfs, meta = get_etf_list(token)
    rep["etf_list"] = {"count": len(etfs), **meta}
    print(f"[ETF清單] {len(etfs)} 檔；分類 {meta.get('categories')}")
    if a.max:
        etfs = etfs[:a.max]

    table = []
    pay_lags = []
    tot_fm_rows = tot_pay = tot_matched = tot_agree = 0
    have_yf = have_both = 0
    fm_skipped_n = 0
    for i, e in enumerate(etfs):
        sid = e["stock_id"]
        start = (today - dt.timedelta(days=800)).strftime("%Y-%m-%d")
        fm_rows, err = fm_get("TaiwanStockDividend", token, data_id=sid, start_date=start)
        fm_skipped = bool(err and "rate_limited" in str(err))
        fm_events, n_pay = [], 0
        for r in fm_rows:
            exd = parse_date(r.get("CashExDividendTradingDate"))
            amt = fm_cash_per_unit(r)
            payd = parse_date(r.get("CashDividendPaymentDate"))
            if exd and amt > 0:
                fm_events.append((exd, amt))
                tot_fm_rows += 1
                if payd:
                    n_pay += 1
                    tot_pay += 1
                    pay_lags.append((payd - exd).days)
        yf_events_all = yf_dividends(sid, str(e.get("market", "")).lower())
        yf_12m = [(d, a_) for d, a_ in yf_events_all if d >= one_year_ago]
        fm_12m = [(d, a_) for d, a_ in fm_events if d >= one_year_ago]
        if yf_12m and not fm_skipped:
            have_yf += 1
            if fm_12m:
                have_both += 1
        if fm_skipped:
            fm_skipped_n += 1
        m, ag = match_events([x for x in fm_events if x[0] >= one_year_ago], yf_12m)
        tot_matched += m
        tot_agree += ag
        freq, months = classify_frequency([d for d, _ in (fm_12m or yf_12m)])
        table.append({"stock_id": sid, "name": e["name"], "category": e["category"],
                      "fm_events_12m": len(fm_12m), "yf_events_12m": len(yf_12m),
                      "fm_with_paydate": n_pay, "matched": m, "amount_agree": ag,
                      "frequency": freq, "ex_months": "/".join(str(x) for x in months),
                      "fm_error": err or ""})
        if (i + 1) % 25 == 0:
            print(f"  進度 {i + 1}/{len(etfs)}")
        time.sleep(0.25 if (token and not _TOKEN_BAD) else 1.3)

    cov = (have_both / have_yf) if have_yf else None
    paydate_rate = (tot_pay / tot_fm_rows) if tot_fm_rows else None
    agree_rate = (tot_agree / tot_matched) if tot_matched else None
    rep["metrics"] = {
        "etfs_checked": len(etfs), "etfs_skipped_finmind_rate_limited": fm_skipped_n, "etfs_with_yf_dividends_12m": have_yf,
        "etfs_also_in_finmind": have_both, "coverage_B": cov,
        "finmind_events": tot_fm_rows, "with_paydate": tot_pay, "paydate_rate_C": paydate_rate,
        "matched_events": tot_matched, "amount_agree": tot_agree, "agree_rate_D": agree_rate,
        "pay_lag_days": ({"min": min(pay_lags), "median": sorted(pay_lags)[len(pay_lags) // 2],
                          "max": max(pay_lags), "n": len(pay_lags)} if pay_lags else None),
    }
    freq_count = {}
    for t in table:
        freq_count[t["frequency"]] = freq_count.get(t["frequency"], 0) + 1
    rep["frequency_distribution"] = freq_count

    rep["finmind_token_invalid"] = _TOKEN_BAD
    rep["official_sources"] = probe_official_sources()

    def ok(v, th):
        return None if v is None else v >= th
    verdict = {
        "A_etf_list": rep["etf_list"]["count"] >= THRESH["A_min_etfs"],
        "B_coverage": ok(cov, THRESH["B_coverage"]),
        "C_paydate": ok(paydate_rate, THRESH["C_paydate"]),
        "D_amount_agree": ok(agree_rate, THRESH["D_amount_agree"]),
    }
    rep["verdict"] = verdict

    with open(os.path.join(a.out, "etf_report.json"), "w", encoding="utf-8") as f:
        json.dump(rep, f, ensure_ascii=False, indent=1, default=str)
    if table:
        with open(os.path.join(a.out, "etf_table.csv"), "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(table[0].keys()))
            w.writeheader()
            w.writerows(table)

    def pct(x):
        return "n/a" if x is None else f"{x * 100:.1f}%"

    def mark(x):
        return "—（無資料）" if x is None else ("✅ 通過" if x else "❌ 未過")

    L = [f"# ETF 配息資料來源驗證（{rep['generated']}）", "",
         f"- ETF 清單：{rep['etf_list']['count']} 檔，分類 {rep['etf_list'].get('categories')}",
         f"- 實際驗證：{len(etfs)} 檔（其中 {fm_skipped_n} 檔因 FinMind 額度用盡而只做了 yfinance，不計入 B/C/D）；FINMIND_TOKEN 無效而改用訪客額度：{rep['finmind_token_invalid']}", "",
         "| 門檻 | 結果 | 數值 | 標準 |", "|---|---|---|---|",
         f"| A 清單 | {mark(verdict['A_etf_list'])} | {rep['etf_list']['count']} 檔 | ≥ {THRESH['A_min_etfs']} |",
         f"| B 覆蓋 | {mark(verdict['B_coverage'])} | {pct(cov)}（{have_both}/{have_yf}） | ≥ {pct(THRESH['B_coverage'])} |",
         f"| C 發放日 | {mark(verdict['C_paydate'])} | {pct(paydate_rate)}（{tot_pay}/{tot_fm_rows}） | ≥ {pct(THRESH['C_paydate'])} |",
         f"| D 金額準確 | {mark(verdict['D_amount_agree'])} | {pct(agree_rate)}（{tot_agree}/{tot_matched}） | ≥ {pct(THRESH['D_amount_agree'])} |",
         "", f"- 除息→發放天數：{rep['metrics']['pay_lag_days']}",
         f"- 配息頻率分佈：{freq_count}", "", "## 官方來源探索（證交所/櫃買 OpenAPI）", ""]
    for o in rep["official_sources"]:
        L.append(f"- {o.get('source')} `{o.get('path', '')}` status={o.get('status', o.get('swagger_status', '?'))} "
                 f"rows={o.get('rows', '?')} ETF列={o.get('has_etf_rows', '?')} 欄位={o.get('fields', o.get('error', ''))}")
    bad = [t for t in table if t["yf_events_12m"] and not t["fm_events_12m"]]
    L += ["", f"## FinMind 缺漏的 ETF（yfinance 有、FinMind 沒有）：{len(bad)} 檔", ""]
    L += [f"- {t['stock_id']} {t['name']}（{t['category']}）{t['fm_error']}" for t in bad[:40]]
    L += ["", "完整逐檔表見 etf_table.csv。結論請對照上方四項門檻判讀；本腳本不改任何正式資料。"]
    rpt = "\n".join(L)
    with open(os.path.join(a.out, "report.md"), "w", encoding="utf-8") as f:
        f.write(rpt)
    print(rpt)
    summ = os.environ.get("GITHUB_STEP_SUMMARY")
    if summ:
        with open(summ, "a", encoding="utf-8") as f:
            f.write(rpt)


if __name__ == "__main__":
    main()
