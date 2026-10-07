"""
舊評分做空的三道防線（2026-10-07）——純函式，不依賴網路/Supabase，方便測試。

背景（實盤 305 筆已平倉做空：勝率 56%、平均 +0.07%、累計虧損；虧損集中在 ma_reclaim 出場 63 筆平均 −2.90%）：
  1) 盤勢：近 5 年回測（backtest_regime）顯示，做空在「波動高於近一年中位(wild)」「指數急殺後(shock5)」「修正中(dd10_deep)」
     勝率只有 34~47%（軋空），只有「波動低於近一年中位(calm)」樣本內外勝率都 >52%（5/5 年同號、穩健）。
     → 新做空只在 calm 且沒有 shock5/dd10_deep 時進場。
  2) 弱勢族群：族群『中位數 收盤/MA20』低於 0 且低於全市場中位數 → 才允許放空該族群的股票
     （族群別「靜態白名單」在 5 年樣本外驗證不過，所以改用「當下相對弱勢」的動態判斷）。
  3) 硬性停損：持有中的空單收盤價 ≥ 進場價 ×(1+6%) → 強制回補（原本沒有任何固定停損，ma_reclaim 平均要吃 −2.9%）。
     實盤 305 筆回算：停損上限 −6% 可使平均報酬由 +0.07% → +0.16%。
"""
import statistics

SHORT_REGIME_REQUIRE = "calm"
SHORT_REGIME_FORBID = ("shock5", "dd10_deep", "wild")
DEFAULT_HARD_STOP_PCT = 6.0
MIN_SECTOR_N = 5


def short_regime_ok(state, expected_asof=None):
    """state：system_config.regime_state_v1 解析後的 dict（含 asof、flags_true）。
    回傳 (ok, 說明)。沒有狀態／日期過期 → 不放行（做空期望值為負，缺資料時寧可不進場）。"""
    if not isinstance(state, dict) or not state:
        return False, "沒有盤勢狀態資料"
    flags = set(state.get("flags_true") or [])
    asof = str(state.get("asof") or "")[:10]
    if expected_asof and asof and asof < str(expected_asof)[:10]:
        return False, f"盤勢狀態過期（{asof} < {expected_asof}）"
    bad = [f for f in SHORT_REGIME_FORBID if f in flags]
    if bad:
        return False, "盤勢不利做空：" + "、".join(bad)
    if SHORT_REGIME_REQUIRE not in flags:
        return False, "盤勢未達『低波動(calm)』，歷史上做空勝率不到 5 成"
    return True, "盤勢 calm（歷史上做空勝率 52~55%）"


def weak_sector_set(cards, sector_of, min_n=MIN_SECTOR_N, small_name="小族群合併"):
    """cards：{代號: {price, ma20}} 或 [(代號, card)]；回傳「相對弱勢」的族群集合。
    弱勢＝該族群樣本≥min_n、中位數(price/ma20−1) < 0 且 < 全市場中位數。"""
    items = cards.items() if isinstance(cards, dict) else cards
    by_sec, allv = {}, []
    for sym, c in items:
        try:
            p, m = float(c.get("price")), float(c.get("ma20"))
        except (TypeError, ValueError, AttributeError):
            continue
        if not (p > 0 and m > 0):
            continue
        v = p / m - 1.0
        allv.append(v)
        by_sec.setdefault((sector_of or {}).get(str(sym)) or small_name, []).append(v)
    if not allv:
        return set()
    mkt = statistics.median(allv)
    weak = set()
    for sec, vs in by_sec.items():
        if len(vs) >= min_n:
            med = statistics.median(vs)
            if med < 0 and med < mkt:
                weak.add(sec)
    return weak


def filter_short_candidates(shorts, sector_of, weak_sectors, small_name="小族群合併"):
    """依弱勢族群過濾做空候選。回傳 (kept, dropped)；weak_sectors 為空集合時（樣本不足）全部擋下＝今天不做空。"""
    kept, dropped = [], []
    for c in shorts:
        sec = (sector_of or {}).get(str(c.get("symbol"))) or small_name
        (kept if sec in weak_sectors else dropped).append(dict(c, sector=sec))
    return kept, dropped


def short_hard_stop_hit(entry, cur, pct=DEFAULT_HARD_STOP_PCT):
    """空單硬性停損：現價 ≥ 進場價×(1+pct%)。"""
    try:
        e, c = float(entry), float(cur)
    except (TypeError, ValueError):
        return False
    return e > 0 and c > 0 and c >= e * (1 + pct / 100.0)
