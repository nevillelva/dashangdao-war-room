"""【2026-10-16】除息「前」行情回測：買在除息前 k 個交易日、除息前一日收盤賣，到底有沒有贏？（老闆質疑：查11 應列除息前的股票）
資料：近 5 年除息事件（ex_div_fill_v1 快取，只計純現金股利）＋ FinMind 未還原日收盤。
基準（控制市場漲跌）：同一檔、同一曆年、持有天數相同的所有滾動視窗，排除除息日前 40／後 10 個交易日內開始的視窗。
輸出：各 k 的樣本數、勝率、平均／中位報酬、超額（對基準）、殖利率分組、樣本內(<=2024)／樣本外(>=2025)。
【限制】事件集中在 7～8 月（同日事件高度相關，有效樣本小於 n）；母體＝目前掃到查11的股票，有存活者偏誤；未含交易成本與稅。"""
import statistics

KS = (20, 10, 5, 3)
OOS_YEAR = "2025"


def _idx_on_or_after(dates, d):
    return next((i for i, x in enumerate(dates) if x >= d), None)


def event_returns(dates, px, ex_dates, ks=KS):
    """回傳 list[{ex,k,ret,year,pos}]：進場 close[s-k]、出場 close[s-1]（s＝除息日起第一個交易日 index）。"""
    out = []
    for ex in ex_dates:
        s = _idx_on_or_after(dates, ex)
        if s is None or s < max(ks) + 1:
            continue
        for k in ks:
            e, x = px[s - k], px[s - 1]
            if e > 0:
                out.append({"ex": ex, "k": k, "ret": x / e - 1, "year": ex[:4], "s": s})
    return out


def baseline_means(dates, px, ex_dates, ks=KS):
    """{(k, year): 平均報酬}：同檔同年滾動視窗，排除與除息前 40／後 10 日重疊者。"""
    block = set()
    for ex in ex_dates:
        s = _idx_on_or_after(dates, ex)
        if s is not None:
            block.update(range(max(0, s - 40), s + 11))
    acc = {}
    for k in ks:
        for i in range(0, len(px) - k):
            if i in block or i + k in block:
                continue
            if px[i] > 0:
                acc.setdefault((k, dates[i][:4]), []).append(px[i + k] / px[i] - 1)
    return {key: sum(v) / len(v) for key, v in acc.items() if v}


def analyze(per_stock):
    """per_stock：[{sym, dates, px, events:[(ex,cash)]}]。回傳彙總 dict。"""
    rows = []
    for st in per_stock:
        ex_dates = [e[0] for e in st["events"]]
        cash = {e[0]: e[1] for e in st["events"]}
        base = baseline_means(st["dates"], st["px"], ex_dates)
        for r in event_returns(st["dates"], st["px"], ex_dates):
            b = base.get((r["k"], r["year"]))
            if b is None:
                continue
            s = r["s"]
            before = st["px"][s - 1]
            y = cash[r["ex"]] / before * 100 if before > 0 else 0
            rows.append({**r, "base": b, "excess": r["ret"] - b, "yield": y, "sym": st["sym"]})

    def stat(sub):
        if not sub:
            return None
        rets = [x["ret"] for x in sub]
        ex = [x["excess"] for x in sub]
        return {"n": len(sub), "n_stocks": len({x["sym"] for x in sub}),
                "win%": round(sum(1 for v in rets if v > 0) / len(rets) * 100, 1),
                "mean%": round(statistics.mean(rets) * 100, 2), "median%": round(statistics.median(rets) * 100, 2),
                "excess_mean%": round(statistics.mean(ex) * 100, 2),
                "beat_base%": round(sum(1 for v in ex if v > 0) / len(ex) * 100, 1)}
    res = {}
    for k in KS:
        ks = [x for x in rows if x["k"] == k]
        res[f"k{k}"] = {"all": stat(ks),
                        "IS<=2024": stat([x for x in ks if x["year"] < OOS_YEAR]),
                        "OOS>=2025": stat([x for x in ks if x["year"] >= OOS_YEAR]),
                        "yield>=4.5%": stat([x for x in ks if x["yield"] >= 4.5]),
                        "yield<4.5%": stat([x for x in ks if x["yield"] < 4.5])}
    res["n_events_total"] = len({(x["sym"], x["ex"]) for x in rows})
    return res
