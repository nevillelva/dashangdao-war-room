"""【2026-10-20 老闆：10:00 多方規則、查7 13:15 買進版先做暫定版，並測近兩天實際勝率】純函式，可離線測試。
限制（寫進報告）：樣本只有近 2 個交易日、同日多檔並非獨立，n<30 僅供觀察，不能當定版證據；
10:00 規則用 5 分K（庫存只到 09:55 那根）；查7 13:15 價用永豐 1 分K，抓不到時該筆標 no_1m、不估算；漲停鎖死價視為買不到。"""
from __future__ import annotations

from statistics import mean

COST = 0.585          # 持有過夜／多日做多的來回成本（%）
LIMIT_UP_PCT = 9.4    # 13:15 價已 ≥ 昨收 +9.4% 視為已鎖漲停、買不到


def _daily_pair(df, d):
    """df：日K（index 日期、Open／Close）。回傳 (D 開, D 收, 昨收, D+1 開, D+1 收)；缺的為 None。"""
    if df is None or len(df) == 0:
        return (None,) * 5
    idx = [i.strftime("%Y-%m-%d") for i in df.index]
    if d not in idx:
        return (None,) * 5
    k = idx.index(d)
    g = lambda j, col: float(df[col].iloc[j]) if 0 <= j < len(df) else None  # noqa: E731
    return g(k, "Open"), g(k, "Close"), g(k - 1, "Close"), g(k + 1, "Open"), g(k + 1, "Close")


def _pct(a, b, cost=COST):
    return None if not (a and b and a > 0) else round((b / a - 1) * 100 - cost, 3)


def eval_long10(bars5, df, d, hint_fn):
    """暫定 10:00 多方規則的單檔結果。bars5：當天 5 分K（bar_time 起始、open/high/low/close）。"""
    h = hint_fn(bars5)
    o_d, c_d, _pc, o_n, c_n = _daily_pair(df, d)
    bs = sorted([b for b in (bars5 or []) if str(b.get("bar_time", "")) < "10:00"], key=lambda b: b["bar_time"])
    e10 = float(bs[-1]["close"]) if bs else None
    if h["verdict"] == "unknown" or not e10 or not c_d:
        return {"verdict": h["verdict"], "status": "no_data"}
    return {"verdict": h["verdict"], "status": "done", "open": o_d, "e10": e10,
            "r_open_close": _pct(o_d, c_d), "r_e10_close": _pct(e10, c_d),
            "r_e10_nextopen": _pct(e10, o_n) if o_n else None, "r_e10_nextclose": _pct(e10, c_n) if c_n else None}


def eval_q7(p1315, prev_close, high_after, df, d_sell, cost=COST):
    """查7 13:15 買進版單檔：E 日 13:15 價買、D（賣出日）開盤賣。prev_close＝E 日昨收，用來判斷 13:15 是否已鎖漲停。"""
    o_d, c_d, _pc, _, _ = _daily_pair(df, d_sell)
    if not p1315 or not prev_close or not o_d:
        return {"status": "no_data"}
    up = (p1315 / prev_close - 1) * 100
    locked = up >= LIMIT_UP_PCT
    r = _pct(p1315, o_d, cost)
    return {"status": "locked_skip" if locked else "done", "up_at_1315": round(up, 2), "locked": locked,
            "r_sell_open": r, "r_sell_close": _pct(p1315, c_d, cost)}


def summarize(rows, key):
    xs = [r[key] for r in rows if r.get("status") == "done" and r.get(key) is not None]
    if not xs:
        return {"n": 0}
    return {"n": len(xs), "avg": round(mean(xs), 3), "win": round(sum(1 for x in xs if x > 0) / len(xs) * 100, 1),
            "min": round(min(xs), 2), "max": round(max(xs), 2)}
