"""【2026-10-13】ETF 組合歷史回測（純函式，不連網）。
只做「過去 N 年」的歷史計算：年化報酬、年化波動、最大回撤、可選年度再平衡。
不做「預估未來資產」（那是假設報酬的複利，不是資料）。
輸入價格為收盤價（未含息），結果須標示「未含息」。"""
from math import sqrt

TRADING_DAYS = 245  # 台股年交易日近似值（用於年化）


def validate_weights(weights):
    """回傳 (ok, 訊息)。權重為百分比，加總需為 100（容許 ±0.01）、每檔 >0、最多 5 檔。"""
    if not weights:
        return False, "至少要有 1 檔"
    if len(weights) > 5:
        return False, "最多 5 檔"
    if any(w is None or w <= 0 for w in weights.values()):
        return False, "每檔權重必須大於 0"
    total = sum(weights.values())
    if abs(total - 100.0) > 0.01:
        return False, f"權重加總需為 100%，目前 {total:.2f}%"
    return True, ""


def align(price_series):
    """price_series: {code: [(date_str, close), ...]}。只保留所有檔都有價格的日期（內連接），回傳 (dates, {code: [close...]})。"""
    maps = {c: dict(rows) for c, rows in price_series.items()}
    common = None
    for c, m in maps.items():
        common = set(m) if common is None else common & set(m)
    dates = sorted(common or [])
    closes = {c: [float(maps[c][d]) for d in dates] for c in maps}
    return dates, closes


LIMIT_JUMP = 0.105      # 台股漲跌幅限制 ±10%，超過（含捨入容差）即視為分割／反分割等除權息以外的股本變動
RESID_TOL = 0.02        # 推算殘差需 ≤2%，否則不確定，停止計算
_SPLIT_CANDS = sorted({float(n) for n in range(2, 31)} | {1.0 / n for n in range(2, 31)}
                      | {n / d for n in range(2, 11) for d in (2, 3, 4) if n % d} | {d / n for n in range(2, 11) for d in (2, 3, 4) if n % d})


def adjust_corporate_actions(rows):
    """rows: [(date, close)] 未還原的日收盤。偵測單日跳動超過漲跌幅限制的日子，
    推算分割比例 f（舊價 ≈ 新價 × f），殘差 ≤2% 才採用並把之前的價格除以 f；不確定就回報問題。
    回傳 (adjusted_rows, events, problems)。events：[{date, factor, resid_pct}]。"""
    rows = list(rows)
    events, problems = [], []
    factors = {}  # index → factor（發生在該 index 當日）
    for i in range(1, len(rows)):
        d, cur = rows[i]
        prev = rows[i - 1][1]
        if prev <= 0 or cur <= 0:
            problems.append(f"{d} 價格為 0 或負值")
            continue
        if abs(cur / prev - 1) <= LIMIT_JUMP:
            continue
        best = min(_SPLIT_CANDS, key=lambda f: abs((prev / f) / cur - 1))
        resid = (prev / best) / cur - 1
        if abs(resid) > RESID_TOL:
            problems.append(f"{d} 單日跳動 {(cur / prev - 1) * 100:+.1f}%，無法判定為分割比例（最接近 {best:.3g}，殘差 {resid * 100:+.1f}%）")
            continue
        factors[i] = best
        events.append({"date": d, "factor": round(best, 4), "resid_pct": round(resid * 100, 2)})
    if problems:
        return rows, events, problems
    # after[k] = 第 k 列之後（索引 > k）所有分割事件的累積比例；第 k 列價格要除以它
    after = [1.0] * len(rows)
    for k in range(len(rows) - 2, -1, -1):
        after[k] = after[k + 1] * factors.get(k + 1, 1.0)
    adjusted = [(d, c / after[k]) for k, (d, c) in enumerate(rows)]
    return adjusted, events, problems


def _max_drawdown(values):
    peak, mdd = values[0], 0.0
    for v in values:
        peak = max(peak, v)
        if peak > 0:
            mdd = max(mdd, (peak - v) / peak)
    return mdd * 100.0


def backtest(price_series, weights, rebalance="annual"):
    """回傳 dict：ok、message、years、cagr_pct、vol_pct、mdd_pct、n_days、rebalance、from、to。
    rebalance='annual'：每年第一個交易日回到目標權重；'none'：買入後不調整。"""
    ok, msg = validate_weights(weights)
    if not ok:
        return {"ok": False, "message": msg}
    if set(price_series) != set(weights):
        return {"ok": False, "message": "價格資料與權重的代號不一致"}
    # 價格未還原分割：先推算調整；推算不確定就整檔退回（死規則 1），不默默吃進去
    adj_series, all_events = {}, {}
    for c, rows in price_series.items():
        adj, events, problems = adjust_corporate_actions(rows)
        if problems:
            return {"ok": False, "message": f"{c}：" + "；".join(problems) + "。為避免假性暴跌，停止計算，請人工確認該檔的分割資料。"}
        adj_series[c] = adj
        if events:
            all_events[c] = events
    price_series = adj_series
    dates, closes = align(price_series)
    if len(dates) < 60:
        return {"ok": False, "message": f"共同交易日只有 {len(dates)} 天（少於 60 天），資料不足，不計算"}
    if any(c <= 0 for series in closes.values() for c in series):
        return {"ok": False, "message": "價格含 0 或負值，資料有誤"}

    codes = list(weights)
    w = {c: weights[c] / 100.0 for c in codes}
    # 初始：每檔配置 w[c]（以 1 元為基準）
    holdings = {c: w[c] / closes[c][0] for c in codes}
    values = []
    prev_year = dates[0][:4]
    for i, d in enumerate(dates):
        value = sum(holdings[c] * closes[c][i] for c in codes)
        if rebalance == "annual" and d[:4] != prev_year:
            holdings = {c: w[c] * value / closes[c][i] for c in codes}
            prev_year = d[:4]
        value = sum(holdings[c] * closes[c][i] for c in codes)
        values.append(value)

    rets = [values[i] / values[i - 1] - 1 for i in range(1, len(values))]
    n = len(rets)
    years = n / TRADING_DAYS
    total = values[-1] / values[0]
    cagr = (total ** (1 / years) - 1) * 100.0 if years > 0 else None
    mean = sum(rets) / n
    var = sum((r - mean) ** 2 for r in rets) / (n - 1) if n > 1 else 0.0
    vol = sqrt(var) * sqrt(TRADING_DAYS) * 100.0
    return {
        "ok": True, "message": "",
        "years": round(years, 2), "n_days": len(dates),
        "cagr_pct": round(cagr, 2) if cagr is not None else None,
        "vol_pct": round(vol, 2), "mdd_pct": round(_max_drawdown(values), 2),
        "rebalance": rebalance, "from": dates[0], "to": dates[-1],
        "curve": [(d, round(v, 6)) for d, v in zip(dates, values)],
        "adjustments": all_events,
        "note": "歷史回測，未含息，不代表未來績效。",
    }
