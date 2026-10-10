"""【2026-10-11 P7】factor_snapshot 分數 vs 戰卡（warcard_cache）分數 影子紀錄（純函式，可離線測試）。
目的：累積 ≥20 個交易日後，評估能否用夜間算好的 factor_snapshot 分數代替網頁端即時戰卡計算。
注意：戰卡只快取『有人在網頁上看過』的股票，每日約 15 檔，樣本小；評估結論要標註樣本數。
python3 test_factor_card_shadow.py"""
import json
import math


def norm_date(d):
    s = str(d or "").strip().replace("-", "")
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}" if len(s) == 8 and s.isdigit() else ""


def pair_scores(cards, factors):
    """cards: [{symbol, payload(json str|dict)}]；factors: [{symbol, total_score_default_weight}]。
    回傳 [{symbol, card_score, factor_score}]，只含兩邊都有可讀數字者。"""
    fmap = {}
    for r in factors or []:
        v = r.get("total_score_default_weight")
        if isinstance(v, (int, float)) and math.isfinite(v):
            fmap[str(r.get("symbol"))] = float(v)
    out = []
    for r in cards or []:
        p = r.get("payload")
        if isinstance(p, str):
            try:
                p = json.loads(p)
            except (TypeError, ValueError):
                continue
        if not isinstance(p, dict):
            continue
        cs = p.get("score")
        sym = str(r.get("symbol"))
        if isinstance(cs, (int, float)) and math.isfinite(cs) and sym in fmap:
            out.append({"symbol": sym, "card_score": float(cs), "factor_score": fmap[sym]})
    return out


def _corr(xs, ys):
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if sx == 0 or sy == 0:
        return None
    return round(sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy), 3)


def summarize(pairs_with_ret, thr=6.0):
    """pairs_with_ret: [{card_score, factor_score, ret}]（ret=次日或N日報酬，小數；None 略過）。
    回傳：n、兩分數相關、同方向（以 ±thr 分類）一致率、各自 ≥thr 組的平均報酬。"""
    rows = [p for p in pairs_with_ret if isinstance(p.get("ret"), (int, float)) and math.isfinite(p["ret"])]
    n = len(rows)
    out = {"n": n, "corr_card_factor": _corr([r["card_score"] for r in rows], [r["factor_score"] for r in rows])}

    def side(s):
        return 1 if s >= thr else (-1 if s <= -thr else 0)
    out["same_side_pct"] = round(100 * sum(1 for r in rows if side(r["card_score"]) == side(r["factor_score"])) / n, 1) if n else None
    for name in ("card", "factor"):
        hit = [r["ret"] for r in rows if r[f"{name}_score"] >= thr]
        out[f"{name}_ge_thr"] = {"n": len(hit), "avg_ret_pct": round(100 * sum(hit) / len(hit), 3) if hit else None}
    return out
