"""
實盤 vs 回測偏離監控（F5，2026-10-07）——純函式。

目的：規則失效時第一時間知道，不要等虧完才發現。
做法：把模擬倉（trade_type=swing_bt）已平倉的單，依「規則」與「規則×族群」統計實際勝率與 95% 信賴區間（Wilson），
與 5 年回測參考表（system_config.regime_policy_ref_v1，樣本內＋樣本外合併）比較：
  • 信賴區間『上限』仍低於回測勝率  → 🔴 偏離（統計上顯著低於回測，建議暫停該組合）
  • 勝率低於回測、但區間還涵蓋回測值 → 🟡 偏低（觀察）
  • 其餘 → 🟢 正常；樣本 < min_n → ⚪ 樣本不足（不下結論）
注意：這是『是否偏離回測』的統計檢查，不是預測；樣本小時信賴區間很寬，所以 min_n 預設 20 筆才判定。
"""
import math

REF_ALL_SECTOR = "全體市場(對照)"
RULE_ZH = {"chuan_e_ma60_40": "穿山惡龍", "pullback_burst": "爆量回檔", "revenue_momentum": "營收動能"}


def wilson(k, n, z=1.96):
    """成功 k 次／n 次 的 Wilson 信賴區間 (lo, hi)；n=0 回 (0,1)。"""
    if n <= 0:
        return 0.0, 1.0
    p = k / n
    den = 1 + z * z / n
    c = p + z * z / (2 * n)
    r = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return max(0.0, (c - r) / den), min(1.0, (c + r) / den)


def baseline(ref, sector, rule, min_n=30):
    """回測基準（樣本內＋樣本外合併）。族群樣本足夠（IS+OOS ≥ min_n）→ 用該族群；否則退到全體市場。
    回傳 {p0, n0, exp0, src} 或 None。"""
    sectors = (((ref or {}).get("long") or {}).get("sectors")) or {}

    def _cell(sec):
        c = (((sectors.get(sec) or {}).get("rules") or {}).get(rule) or {}).get("all")
        if not c:
            return None
        i, o = c.get("IS") or {}, c.get("OOS") or {}
        n = int(i.get("n") or 0) + int(o.get("n") or 0)
        if n <= 0:
            return None
        w = (float(i.get("win") or 0) * int(i.get("n") or 0) + float(o.get("win") or 0) * int(o.get("n") or 0)) / n
        e = (float(i.get("exp_pct") or 0) * int(i.get("n") or 0) + float(o.get("exp_pct") or 0) * int(o.get("n") or 0)) / n
        return {"p0": w, "n0": n, "exp0": e}

    c = _cell(sector) if sector else None
    if c and c["n0"] >= min_n:
        return dict(c, src=sector)
    c = _cell(REF_ALL_SECTOR)
    return dict(c, src=REF_ALL_SECTOR) if c else None


def judge(wins, n, p0, min_n=20):
    lo, hi = wilson(wins, n)
    if n < min_n:
        return "樣本不足", lo, hi
    wr = wins / n
    if p0 is None:
        return "無基準", lo, hi
    if hi < p0:
        return "偏離", lo, hi
    if wr < p0:
        return "偏低", lo, hi
    return "正常", lo, hi


def evaluate(closed, ref, sector_of, min_n=20):
    """closed：[{strategy_tag, symbol, realized_roi}]（swing_bt 已平倉）。
    回傳 list[dict]，依嚴重程度排序：偏離 > 偏低 > 正常 > 樣本不足。每筆含 rule, sector(None=整體), n, wr, lo, hi, avg_roi, p0, exp0, base_src, status。"""
    groups = {}
    for t in closed or []:
        if t.get("realized_roi") is None:
            continue
        rule = t.get("strategy_tag") or "?"
        sec = (sector_of or {}).get(str(t.get("symbol")))
        groups.setdefault((rule, None), []).append(float(t["realized_roi"]))
        if sec:
            groups.setdefault((rule, sec), []).append(float(t["realized_roi"]))
    out = []
    for (rule, sec), rois in groups.items():
        n = len(rois)
        if sec is not None and n < min_n:
            continue            # 族群細項樣本不足就不列（避免一堆「樣本不足」雜訊）
        wins = sum(1 for r in rois if r > 0)
        b = baseline(ref, sec, rule)
        st, lo, hi = judge(wins, n, b["p0"] if b else None, min_n)
        out.append({"rule": rule, "sector": sec, "n": n, "wins": wins, "wr": wins / n, "lo": lo, "hi": hi,
                    "avg_roi": sum(rois) / n, "p0": b["p0"] if b else None, "exp0": b["exp0"] if b else None,
                    "base_src": b["src"] if b else None, "status": st})
    order = {"偏離": 0, "偏低": 1, "正常": 2, "無基準": 3, "樣本不足": 4}
    out.sort(key=lambda x: (order.get(x["status"], 9), x["rule"], x["sector"] or ""))
    return out


ICON = {"偏離": "🔴", "偏低": "🟡", "正常": "🟢", "樣本不足": "⚪", "無基準": "⚪"}


def line(r):
    zh = RULE_ZH.get(r["rule"], r["rule"])
    who = zh + (f"×{r['sector']}" if r["sector"] else "（整體）")
    base = f"回測 {r['p0']:.0%}" if r["p0"] is not None else "無回測基準"
    return (f"{ICON.get(r['status'], '')} {who}：實盤 {r['wins']}/{r['n']}＝{r['wr']:.0%}（95%區間 {r['lo']:.0%}～{r['hi']:.0%}）｜{base}"
            f"｜平均報酬 {r['avg_roi']:+.2f}%｜{r['status']}")


def build_text(results, today):
    if not results:
        return ""
    bad = [r for r in results if r["status"] == "偏離"]
    low = [r for r in results if r["status"] == "偏低"]
    head = f"🩺 [{today}] 實盤 vs 回測偏離監控"
    if bad:
        head += f"：{len(bad)} 項顯著偏離"
    L = [head]
    L += [line(r) for r in results if r["status"] in ("偏離", "偏低", "正常")][:12]
    ns = [r for r in results if r["status"] == "樣本不足"]
    if ns:
        L.append(f"⚪ 樣本不足（<20 筆）不下結論：" + "、".join(RULE_ZH.get(r["rule"], r["rule"]) + (f"×{r['sector']}" if r["sector"] else "") + f"({r['n']}筆)" for r in ns[:6]))
    if bad:
        L.append("👉 建議：暫停該規則（bt_strategy_config 的 rules 拿掉它）或檢查盤勢是否已改變；回測是歷史，實盤連續低於區間下限代表環境可能變了。")
    elif low:
        L.append("👉 偏低但尚未顯著，繼續觀察；每週五與出現『偏離』時推播。")
    return "\n".join(L)


def status_changes(prev, cur):
    """比較上次與這次的『偏離』清單，回傳新出現的偏離（避免每天重複推播）。prev/cur：list[dict] 或 None。"""
    key = lambda r: (r["rule"], r["sector"])
    old = {key(r) for r in (prev or []) if r.get("status") == "偏離"}
    return [r for r in cur if r["status"] == "偏離" and key(r) not in old]
