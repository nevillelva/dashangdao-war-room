"""【P5｜2026-10-12】股債配置組合試算（純函式，可離線測試）。

資料來源（死規則 5 與 4）：
  • 日收盤價：由老闆『手動』從 FinMind 網站下載 CSV 後貼入（不做自動抓取；FinMind 使用條款本文未能讀取，且 README 寫明為教育／非商業用途，
    在確認條款前不做自動化）。TWSE／TPEx OpenAPI 只有當日行情，沒有 ETF 歷史，不能用於回測。
  • 配息：沿用 etf_dividend_events（既有資料）計算含息報酬。
  • 本模組只做計算，不抓資料。

驗證（死規則 1）：格式、代號需在 ETF 清單內、日期合法、收盤 >0 且有限、不重複、來源網址 https、最新收盤與 etf_master 現價偏差 ≤5%。
不過就退回，不默默吃進去。

回測假設（一律揭露）：每月底再平衡到目標股債比例；含息（除息日當日配息計入）；未扣手續費與稅；
樣本是歷史一段期間，OOS 偏多頭，通過不代表未來；月頻計算，最大回撤是月底權益曲線的回撤（低估日內回撤）。
"""
import csv
import io
import math
import statistics
from collections import defaultdict
from datetime import date

PRICE_CHECK_TOL = 0.05
MIN_MONTHS = 24          # 少於 24 個月的結果標示為樣本不足


def _norm_date(s):
    s = str(s or "").strip().replace("/", "-")
    if len(s) == 8 and s.isdigit():
        s = f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    try:
        return date.fromisoformat(s).isoformat()
    except ValueError:
        return ""


def parse_price_csv(text, symbols, source_url):
    """解析貼上的 CSV（欄位：symbol,date,close；有無標題列皆可）。
    回傳 (ok, result)：ok=True 時 result={symbol: [(date, close), ...]}；False 時 result 是錯誤訊息清單。"""
    if not str(source_url or "").lower().startswith("https://"):
        return False, ["來源網址必須是 https://（每筆資料都要附來源）"]
    if not str(text or "").strip():
        return False, ["沒有貼上內容"]
    allowed = {str(s).strip().upper() for s in symbols}
    errors, seen, out = [], set(), defaultdict(list)
    rows_all = [[c.strip() for c in r] for r in csv.reader(io.StringIO(text.strip()))]
    # 欄位依標題名稱對應：支援本系統格式 symbol,date,close，以及 FinMind 原生欄位 stock_id,date,...,close
    cols = {"sym": 0, "date": 1, "close": 2}
    if rows_all and rows_all[0]:
        head = [h.lower() for h in rows_all[0]]
        if "close" in head and "date" in head and ("symbol" in head or "stock_id" in head):
            cols = {"sym": head.index("symbol") if "symbol" in head else head.index("stock_id"),
                    "date": head.index("date"), "close": head.index("close")}
            rows_all = rows_all[1:]
        elif head[0] in ("symbol", "代號", "證券代號"):
            rows_all = rows_all[1:]
    need = max(cols.values()) + 1
    for i, row in enumerate(rows_all, 1):
        if not row or not any(row):
            continue
        if len(row) < need:
            errors.append(f"第 {i} 行欄位不足（需要 symbol/stock_id、date、close）")
            continue
        sym, d_raw, c_raw = row[cols["sym"]].upper(), row[cols["date"]], row[cols["close"]]
        if sym not in allowed:
            errors.append(f"第 {i} 行代號 {sym} 不在 ETF 清單內")
            continue
        d = _norm_date(d_raw)
        if not d:
            errors.append(f"第 {i} 行日期格式錯誤：{d_raw}")
            continue
        try:
            c = float(c_raw.replace(",", ""))
        except ValueError:
            errors.append(f"第 {i} 行收盤價不是數字：{c_raw}")
            continue
        if not math.isfinite(c) or c <= 0:
            errors.append(f"第 {i} 行收盤價必須大於 0：{c_raw}")
            continue
        key = (sym, d)
        if key in seen:
            errors.append(f"第 {i} 行重複：{sym} {d}")
            continue
        seen.add(key)
        out[sym].append((d, c))
    if errors:
        return False, errors[:50]
    if not out:
        return False, ["沒有有效資料列"]
    for sym in out:
        out[sym].sort()
    return True, dict(out)


def cross_check(prices, master_last_price):
    """最新收盤與 etf_master 現價比對，偏差 >5% 退回。回傳 (ok, 問題清單)。"""
    problems = []
    for sym, rows in prices.items():
        ref = master_last_price.get(sym)
        if not ref:
            problems.append(f"{sym} 在 etf_master 沒有現價，無法交叉比對")
            continue
        last_c = rows[-1][1]
        dev = abs(last_c - float(ref)) / float(ref)
        if dev > PRICE_CHECK_TOL:
            problems.append(f"{sym} 最新收盤 {last_c} 與 etf_master 現價 {ref} 相差 {dev:.1%}，超過 {PRICE_CHECK_TOL:.0%}")
    return (not problems), problems


def total_return_daily(rows, cash_events):
    """rows: [(date, close)] 已排序；cash_events: [(ex_date, cash_per_unit)]。
    回傳 [(date, 含息日報酬)]，第一天沒有報酬故不列。除息日當天：(收盤＋現金股利)/前日收盤−1。"""
    ex_cash = {}
    for exd, cash in cash_events:
        # 除息日若非交易日，歸到之後第一個交易日
        for d, _ in rows:
            if d >= exd:
                ex_cash[d] = ex_cash.get(d, 0.0) + float(cash)
                break
    out = []
    for (d0, c0), (d1, c1) in zip(rows, rows[1:]):
        cash = ex_cash.get(d1, 0.0)
        out.append((d1, (c1 + cash) / c0 - 1.0))
    return out


def monthly_returns(daily):
    """日報酬 → 月報酬（同月內複利）。回傳 {YYYY-MM: r}。"""
    acc = defaultdict(lambda: 1.0)
    for d, r in daily:
        acc[d[:7]] *= (1.0 + r)
    return {m: v - 1.0 for m, v in acc.items()}


def _metrics(monthly_list):
    n = len(monthly_list)
    if n == 0:
        return {"months": 0}
    eq, peak, mdd = 1.0, 1.0, 0.0
    for r in monthly_list:
        eq *= (1 + r)
        peak = max(peak, eq)
        mdd = min(mdd, eq / peak - 1)
    cagr = eq ** (12.0 / n) - 1 if eq > 0 else -1.0
    vol = statistics.stdev(monthly_list) * math.sqrt(12) if n >= 2 else None
    return {
        "months": n,
        "cagr_pct": round(cagr * 100, 2),
        "vol_pct": round(vol * 100, 2) if vol is not None else None,
        "mdd_pct": round(mdd * 100, 2),
        "worst_month_pct": round(min(monthly_list) * 100, 2),
        "best_month_pct": round(max(monthly_list) * 100, 2),
        "positive_months_pct": round(100 * sum(1 for r in monthly_list if r > 0) / n, 1),
        "sample_ok": n >= MIN_MONTHS,
    }


def mix_backtest(stock_monthly, bond_monthly, stock_weight):
    """每月再平衡：組合月報酬＝w×股＋(1−w)×債。只取兩邊都有資料的月份。
    回傳 {mix, stock_only, bond_only, period:{start,end}, series:[(month, equity)]}。"""
    if not 0.0 <= stock_weight <= 1.0:
        raise ValueError("stock_weight 必須在 0~1 之間")
    months = sorted(set(stock_monthly) & set(bond_monthly))
    if not months:
        return {"mix": _metrics([]), "stock_only": _metrics([]), "bond_only": _metrics([]),
                "period": None, "series": []}
    s = [stock_monthly[m] for m in months]
    b = [bond_monthly[m] for m in months]
    mix = [stock_weight * x + (1 - stock_weight) * y for x, y in zip(s, b)]
    eq, series = 1.0, []
    for m, r in zip(months, mix):
        eq *= (1 + r)
        series.append((m, round(eq, 4)))
    return {"mix": _metrics(mix), "stock_only": _metrics(s), "bond_only": _metrics(b),
            "period": {"start": months[0], "end": months[-1]}, "series": series}
