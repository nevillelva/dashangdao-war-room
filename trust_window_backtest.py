"""【2026-10-16】查4（投信買超）「季底作帳窗口」回測（老闆：投信作帳有固定週期，查4 應只在作帳前一個月內篩選）。
訊號：查4＝投信單日買超>0；投信連買3日＝連 3 個交易日買超>0；基準＝同批股票每個交易日（隨機進場）。
時間分組（以季底 3/31、6/30、9/30、12/31 計，日曆天）：
  季底前1個月（距季底 ≤31 天）、季底前31~60天、季初2週（季底後 ≤14 天，結帳後）、其他。
報酬：5／10／20 個交易日後；另算「季底前1個月」買進持有到季底最後一個交易日。
超額＝個股報酬 − 同一天全部股票平均報酬（扣掉大盤當天的漲跌，看選股本身）。
【限制】母體＝現有回測股票池（偏大型、存活者偏誤）；同日訊號高度相關，有效樣本 < n；未扣成本；yfinance 還原價。"""
import datetime as dt
import statistics

HOLDS = (5, 10, 20)
OOS_YEAR = "2025"


def quarter_bounds(d):
    """回傳 (上一個季底, 下一個季底)，d 為 date；d 剛好是季底時，下一個季底＝d。"""
    q_ends = []
    for y in (d.year - 1, d.year):
        for m, dd in ((3, 31), (6, 30), (9, 30), (12, 31)):
            q_ends.append(dt.date(y, m, dd))
    nxt = next(q for q in q_ends + [dt.date(d.year + 1, 3, 31)] if q >= d)
    prev = max(q for q in q_ends if q < d)
    return prev, nxt


def bucket_of(d):
    prev, nxt = quarter_bounds(d)
    to, since = (nxt - d).days, (d - prev).days
    if to <= 31:
        return "季底前1個月", nxt
    if since <= 14:
        return "季初2週(結帳後)", nxt
    if to <= 60:
        return "季底前31~60天", nxt
    return "其他", nxt


def build_rows(per_stock):
    """per_stock：[{sym, dates[str], px[float], tbuy{date:float}}] → 逐筆列（含各訊號旗標、報酬）。"""
    rows = []
    for st in per_stock:
        dates, px, tb = st["dates"], st["px"], st["tbuy"]
        dd = [dt.date.fromisoformat(x) for x in dates]
        for i in range(3, len(px) - max(HOLDS)):
            if px[i] <= 0:
                continue
            t0 = tb.get(dates[i])
            if t0 is None:
                continue
            b, qe = bucket_of(dd[i])
            r = {"sym": st["sym"], "date": dates[i], "year": dates[i][:4], "bucket": b, "qm": qe.month,
                 "q4": t0 > 0,
                 "streak3": all((tb.get(dates[k]) or 0) > 0 for k in (i - 2, i - 1, i))}
            for h in HOLDS:
                r[f"r{h}"] = px[i + h] / px[i] - 1
            if b == "季底前1個月":
                j = max(k for k in range(i, len(dd)) if dd[k] <= qe) if dd[-1] >= qe else None
                r["r_qe"] = (px[j] / px[i] - 1) if j is not None and j > i else None
            rows.append(r)
    # 同日橫斷面平均（扣大盤）
    day = {}
    for r in rows:
        for h in HOLDS:
            day.setdefault((r["date"], h), []).append(r[f"r{h}"])
    dmean = {k: sum(v) / len(v) for k, v in day.items()}
    for r in rows:
        for h in HOLDS:
            r[f"x{h}"] = r[f"r{h}"] - dmean[(r["date"], h)]
    return rows


def _stat(sub, h):
    vals = [x[f"r{h}"] for x in sub]
    exc = [x[f"x{h}"] for x in sub]
    if len(vals) < 1:
        return None
    return {"n": len(vals), "days": len({x["date"] for x in sub}),
            "win%": round(sum(v > 0 for v in vals) / len(vals) * 100, 1),
            "mean%": round(statistics.mean(vals) * 100, 2),
            "excess%": round(statistics.mean(exc) * 100, 2),
            "beat_mkt%": round(sum(v > 0 for v in exc) / len(exc) * 100, 1)}


def analyze(rows):
    sigs = {"查4(投信買超>0)": lambda r: r["q4"], "投信連買3日": lambda r: r["streak3"], "基準(每天)": lambda r: True}
    buckets = ["季底前1個月", "季底前31~60天", "季初2週(結帳後)", "其他"]
    out = {}
    for sn, f in sigs.items():
        s_rows = [r for r in rows if f(r)]
        out[sn] = {}
        for b in buckets + ["全部"]:
            sub = s_rows if b == "全部" else [r for r in s_rows if r["bucket"] == b]
            out[sn][b] = {f"{h}日": _stat(sub, h) for h in HOLDS}
            out[sn][b]["IS<=2024_10日"] = _stat([r for r in sub if r["year"] < OOS_YEAR], 10)
            out[sn][b]["OOS>=2025_10日"] = _stat([r for r in sub if r["year"] >= OOS_YEAR], 10)
        q1 = [r for r in s_rows if r["bucket"] == "季底前1個月"]
        out[sn]["季底前1個月_6月12月"] = _stat([r for r in q1 if r["qm"] in (6, 12)], 10)
        out[sn]["季底前1個月_3月9月"] = _stat([r for r in q1 if r["qm"] in (3, 9)], 10)
        hq = [r["r_qe"] for r in q1 if r.get("r_qe") is not None]
        out[sn]["季底前1個月_持有到季底"] = ({"n": len(hq), "win%": round(sum(v > 0 for v in hq) / len(hq) * 100, 1),
                                         "mean%": round(statistics.mean(hq) * 100, 2)} if hq else None)
    out["n_rows"] = len(rows)
    out["n_stocks"] = len({r["sym"] for r in rows})
    return out
