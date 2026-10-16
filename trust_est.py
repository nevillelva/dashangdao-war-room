"""【2026-10-17】自建「投信估計持股」：SITCA 季持股（官方、免費可下載）當錨點 ＋ 每日投信買賣超（證交所 T86／FinMind，免費）累加。
與 MoneyDJ「估計持股」同一種做法（每日買賣超累加、季報校正），但來源全部合規。
錨點張數＝SITCA 該股所有基金持有金額（含被動 ETF）÷ 季底收盤 ÷ 1000。
限制：SITCA 只揭露「占基金淨值≥1%」的部位 → 錨點低估；季與季之間部位跨過 1% 門檻會造成跳動；投信買賣超含自營性質以外的所有投信帳戶。
validate()：用「上一季錨點＋期間買賣超」預測「下一季 SITCA」，量化誤差（純函式）。"""
from __future__ import annotations
from statistics import mean, median


def validate(pairs):
    """pairs：[(sym, q0, q1, lots0, lots1, flow)]。回傳誤差統計與「不加流量」對照。"""
    def rel(a, b):
        return abs(a - b) / b if b else None
    est = [rel(l0 + f, l1) for _, _, _, l0, l1, f in pairs if l1 > 0]
    naive = [rel(l0, l1) for _, _, _, l0, l1, f in pairs if l1 > 0]
    est = [x for x in est if x is not None]
    naive = [x for x in naive if x is not None]
    dchg = [(l1 - l0, f) for _, _, _, l0, l1, f in pairs]
    sign_ok = [((a > 0) == (b > 0)) for a, b in dchg if a != 0 and b != 0]
    n = len(dchg)
    if n >= 3:
        ma, mb = mean(a for a, _ in dchg), mean(b for _, b in dchg)
        cov = sum((a - ma) * (b - mb) for a, b in dchg)
        va = sum((a - ma) ** 2 for a, _ in dchg) ** 0.5
        vb = sum((b - mb) ** 2 for _, b in dchg) ** 0.5
        corr = cov / (va * vb) if va and vb else None
    else:
        corr = None
    return {"n": len(est), "median_rel_err": round(median(est), 3) if est else None,
            "mean_rel_err": round(mean(est), 3) if est else None,
            "median_rel_err_no_flow": round(median(naive), 3) if naive else None,
            "corr_change_vs_flow": round(corr, 3) if corr is not None else None,
            "sign_agree_pct": round(100 * sum(sign_ok) / len(sign_ok), 1) if sign_ok else None}
