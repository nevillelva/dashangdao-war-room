"""
持倉風險預算（F6，2026-10-07）——純函式，不依賴網路／Supabase。只套用在『回測規則』模擬倉（trade_type=swing_bt）的**新進場**，
不動既有持倉的出場，也不改變任何規則的進出場判斷。**這些措施不提高勝率，目的是降低同時虧損與回撤。**

1. 同族群上限：同一族群持倉（含已掛單）最多 sector_cap 檔（預設 3；k_slots=10 時等於最多 30% 集中在同族群）。
   族群不明（不在 sector_map）一律視為各自獨立、不受限（fail-open）。
2. 連續虧損熔斷：近 breaker_n 筆已平倉（至少 breaker_min_n 筆）勝率 < breaker_min_win，或這些單的淨報酬加總 ≤ breaker_min_sum_roi（%），
   → 暫停新進場 breaker_pause_days 個日曆天（只擋新單，既有持倉照常出場）。暫停到期後只看『觸發日之後』新平倉的單，避免同一批舊虧損反覆觸發。
3. 波動調整部位（預設關閉 vol_sizing=false）：20 日年化波動越高、單筆金額越小（比例 = clamp(ref_vol / vol, min_scale, 1)）。
   預設關閉的原因：回測口徑是每筆等額，開啟會讓實盤與回測的損益不再可直接比較。
"""
import math
from datetime import datetime, timedelta

DEFAULTS = {
    "sector_cap": 3,
    "breaker_enabled": True,
    "breaker_n": 20,
    "breaker_min_n": 12,
    "breaker_min_win": 0.35,
    "breaker_min_sum_roi": -25.0,     # 近 N 筆淨報酬(%)加總：每筆等額下 ≈ 資金 25% 單筆的虧損總量
    "breaker_pause_days": 5,
    "vol_sizing": False,
    "vol_ref": 0.35,                  # 年化 35% 當基準（台股個股常態約 25~45%）
    "vol_min_scale": 0.5,
}


def _d(s):
    return datetime.strptime(str(s)[:10], "%Y-%m-%d")


# ------------------------------------------------------------------ 1. 同族群上限

def sector_cap_filter(signals, sector_of, held_symbols, cap=3, small_name="小族群合併"):
    """依序走過訊號（保持原順序），已持有＋已接受的同族群檔數達 cap 就擋掉。回傳 (kept, dropped[(sig, 原因)])。
    族群不明（sector_of 查不到）→ 不受限。cap<=0 → 不限制。"""
    if not cap or cap <= 0:
        return list(signals), []
    count = {}
    for sym in held_symbols or []:
        sec = (sector_of or {}).get(str(sym))
        if sec:
            count[sec] = count.get(sec, 0) + 1
    kept, dropped = [], []
    for s in signals:
        sec = (sector_of or {}).get(str(s["symbol"]))
        if sec and count.get(sec, 0) >= cap:
            dropped.append((s, f"同族群「{sec}」已有 {count[sec]} 檔（上限 {cap}）"))
            continue
        if sec:
            count[sec] = count.get(sec, 0) + 1
        kept.append(s)
    return kept, dropped


# ------------------------------------------------------------------ 2. 熔斷

def evaluate_breaker(closed, state, today, cfg=None):
    """closed：[{exit_date, realized_roi}]（只含 swing_bt 已平倉）；state：上次熔斷狀態 dict 或 None；today：'YYYY-MM-DD'。
    回傳 (paused, reason, new_state)；new_state 只在觸發時改變（呼叫端據此寫回 system_config 並推播一次）。"""
    c = dict(DEFAULTS, **(cfg or {}))
    if not c.get("breaker_enabled", True):
        return False, "", state
    orig = state
    state = state if isinstance(state, dict) else {}
    until = str(state.get("until") or "")[:10]
    if until and today <= until:
        return True, f"熔斷中（{state.get('triggered_on')} 觸發，暫停到 {until}）：{state.get('reason', '')}", state
    since = str(state.get("triggered_on") or "")[:10]
    rows = [r for r in closed or [] if r.get("exit_date") and r.get("realized_roi") is not None and (not since or str(r["exit_date"])[:10] > since)]
    rows.sort(key=lambda r: str(r["exit_date"]))
    last = rows[-int(c["breaker_n"]):]
    if len(last) < int(c["breaker_min_n"]):
        return False, "", orig
    wins = sum(1 for r in last if float(r["realized_roi"]) > 0)
    wr = wins / len(last)
    tot = sum(float(r["realized_roi"]) for r in last)
    why = []
    if wr < float(c["breaker_min_win"]):
        why.append(f"近 {len(last)} 筆勝率 {wr:.0%} < {float(c['breaker_min_win']):.0%}")
    if tot <= float(c["breaker_min_sum_roi"]):
        why.append(f"近 {len(last)} 筆淨報酬合計 {tot:+.1f}% ≤ {float(c['breaker_min_sum_roi']):+.0f}%")
    if not why:
        return False, "", orig
    until_new = (_d(today) + timedelta(days=int(c["breaker_pause_days"]))).strftime("%Y-%m-%d")
    ns = {"triggered_on": today, "until": until_new, "reason": "；".join(why), "n": len(last), "win_rate": round(wr, 3), "sum_roi": round(tot, 2)}
    return True, f"觸發熔斷：{ns['reason']}，暫停新進場到 {until_new}", ns


# ------------------------------------------------------------------ 3. 波動調整部位

def annualized_vol(closes, n=20):
    """近 n 日收盤的日報酬標準差 × √252。資料不足回 None。"""
    cs = [float(x) for x in closes if x is not None and float(x) > 0][-(n + 1):]
    if len(cs) < n // 2 + 1:
        return None
    rets = [cs[i] / cs[i - 1] - 1 for i in range(1, len(cs))]
    m = sum(rets) / len(rets)
    var = sum((r - m) ** 2 for r in rets) / max(len(rets) - 1, 1)
    return math.sqrt(var) * math.sqrt(252)


def vol_scale(vol, cfg=None):
    """部位比例 ∈ [min_scale, 1]；vol 未知或功能關閉 → 1.0。"""
    c = dict(DEFAULTS, **(cfg or {}))
    if not c.get("vol_sizing") or not vol or vol <= 0:
        return 1.0
    return max(float(c["vol_min_scale"]), min(1.0, float(c["vol_ref"]) / float(vol)))
