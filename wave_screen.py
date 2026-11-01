"""【2026-11-01 老闆方案1】5 年上市股「長期潛伏 → 爆量突破 → 主升段 → 趨勢結束」型態篩選（純函式，可離線測試）。

規則（全部寫死在這裡，結果才可重現）：
  1. 潛伏期：起漲日前 120 個交易日的「最高－最低」振幅 ≤ 35%（盤整，不是大漲後的高檔）。
  2. 爆量突破（起漲日 s）：量 ≥ 前 20 日均量 3 倍；收盤突破前 60 日最高；收紅（收 > 開）。
  3. 主升段：自 s 起，以收盤計，最高點相對 s 收盤漲幅 ≥ 100%。
  4. 趨勢結束：自 s 之後，收盤從running最高點回落 ≥ 25%（且已達 100% 漲幅）。找不到則標記「仍在進行」，最多追到 s+500 日。
  5. KD(9,3,3)：啟動點＝s 前 20 日到後 5 日內，K<30 的黃金交叉；高檔鈍化＝波段內 K>80 的最長連續天數。
  6. MACD(12,26,9)：波段內（s-5 日～結束）DIF 在零軸上方的黃金交叉次數（需 ≥2＝零軸上方二次交叉）；
     柱狀體 OSC＝DIF－DEA，波段內最長連續為正的天數（需 ≥5 日＝連續翻紅放大）。
  7. 美股連動：波段期間（s～結束）個股日報酬與費城半導體（SOX）、那斯達克（IXIC）日報酬的相關係數。
  「完整特徵」＝規則 1～4 全部成立，且 KD 低檔黃金交叉、MACD 零軸上方二次交叉、柱狀體連續翻紅 ≥5 日三項皆成立。

限制（誠實標示）：
  • 這是歷史型態統計，每個事件都是事後才知道結束點，「最具代表性」的挑選有事後諸葛偏誤。
  • 只用價量與技術指標，不含基本面（營收）與消息面，那兩項要逐檔另外查證。
  • 同一波段只算一次；波段結束後才會再找下一個起漲點。
"""
import math

import numpy as np

BASE_N = 120
BASE_MAX_RANGE = 0.35
VOL_N = 20
VOL_MULT = 3.0
BREAK_N = 60
GAIN_MIN = 1.0       # 100%
DD_END = 0.25        # 自高點回落 25% 視為趨勢結束
MAX_WAVE = 500       # 最長追蹤 500 個交易日
RULES_TEXT = ("潛伏期振幅≤35%(120日)；爆量≥3×20日均量且收盤破60日高且收紅；"
              "主升段累計≥100%；回落≥25%結束；KD(9,3,3)低檔(K<30)金叉＋高檔鈍化；"
              "MACD零軸上方二次金叉＋柱狀連續翻紅≥5日；美股SOX/IXIC同期相關")


def ema(x, n):
    x = np.asarray(x, dtype=float)
    out = np.empty(len(x))
    a = 2.0 / (n + 1)
    for i, v in enumerate(x):
        out[i] = v if i == 0 else a * v + (1 - a) * out[i - 1]
    return out


def kd(h, l, c, n=9):
    h, l, c = (np.asarray(a, dtype=float) for a in (h, l, c))
    N = len(c)
    K = np.empty(N)
    D = np.empty(N)
    pk, pd_ = 50.0, 50.0
    for i in range(N):
        lo = max(0, i - n + 1)
        hh, ll = h[lo:i + 1].max(), l[lo:i + 1].min()
        rsv = 50.0 if hh == ll else (c[i] - ll) / (hh - ll) * 100.0
        K[i] = 2.0 / 3.0 * pk + 1.0 / 3.0 * rsv
        D[i] = 2.0 / 3.0 * pd_ + 1.0 / 3.0 * K[i]
        pk, pd_ = K[i], D[i]
    return K, D


def macd(c):
    c = np.asarray(c, dtype=float)
    dif = ema(c, 12) - ema(c, 26)
    dea = ema(dif, 9)
    return dif, dea, dif - dea


def _longest_run(mask):
    best = cur = 0
    for m in mask:
        cur = cur + 1 if m else 0
        best = max(best, cur)
    return best


def _pearson(a, b):
    if len(a) < 20:
        return None
    a, b = np.asarray(a, float), np.asarray(b, float)
    if a.std() == 0 or b.std() == 0:
        return None
    r = float(np.corrcoef(a, b)[0, 1])
    return None if math.isnan(r) else round(r, 3)


def detect_events(dates, o, h, l, c, v, us=None):
    """回傳 (events, surge_n)。dates：'YYYY-MM-DD' 字串序列；o/h/l/c/v：numpy 陣列；us：{'sox': {date: 報酬}, 'ndx': {...}} 或 None。
    surge_n＝符合爆量突破條件的日數（不管後來漲不漲，用來估計基準）。"""
    o, h, l, c, v = (np.asarray(a, dtype=float) for a in (o, h, l, c, v))
    N = len(c)
    if N < BASE_N + VOL_N + 60:
        return [], 0
    K, D = kd(h, l, c)
    DIF, DEA, OSC = macd(c)
    ret = np.full(N, np.nan)
    ret[1:] = c[1:] / c[:-1] - 1
    events, surge_n = [], 0
    i = BASE_N
    while i < N - 1:
        s = i
        b0 = s - BASE_N
        base_hi, base_lo = h[b0:s].max(), l[b0:s].min()
        if base_lo <= 0 or (base_hi - base_lo) / base_lo > BASE_MAX_RANGE:
            i += 1
            continue
        vol_avg = v[s - VOL_N:s].mean()
        brk = h[s - BREAK_N:s].max()
        surge = vol_avg > 0 and v[s] >= VOL_MULT * vol_avg and c[s] > brk and c[s] > o[s]
        if not surge:
            i += 1
            continue
        surge_n += 1
        # 趨勢：自 s 起追蹤 running 最高，回落 ≥25% 且已漲 ≥100% 即結束
        rm, rm_i, e = c[s], s, None
        for t in range(s + 1, min(N, s + MAX_WAVE + 1)):
            if c[t] > rm:
                rm, rm_i = c[t], t
            if rm / c[s] - 1 >= GAIN_MIN and c[t] <= (1 - DD_END) * rm:
                e = t
                break
        ongoing = e is None
        if ongoing:
            e = min(N - 1, s + MAX_WAVE)
        gain = rm / c[s] - 1
        if gain < GAIN_MIN:
            i += 1
            continue
        # KD：低檔金叉（s-20～s+5，K<30）；高檔鈍化（波段內 K>80 最長連續）
        kd_low = []
        for t in range(max(1, s - 20), min(N, s + 6)):
            if K[t - 1] <= D[t - 1] and K[t] > D[t] and K[t] < 30:
                kd_low.append(t)
        kd_hi_streak = _longest_run(K[s:e + 1] > 80)
        # MACD：波段內（s-5～e）零軸上方的黃金交叉次數；柱狀體最長連續為正
        zero2 = [t for t in range(max(1, s - 5), e + 1) if DIF[t - 1] <= DEA[t - 1] and DIF[t] > DEA[t] and DIF[t] > 0]
        osc_red = _longest_run(OSC[s - 5:e + 1] > 0)
        full = bool(kd_low) and len(zero2) >= 2 and osc_red >= 5
        ev = {
            "start": dates[s], "peak": dates[rm_i], "end": dates[e], "ongoing": ongoing,
            "days": int(e - s), "gain_pct": round(gain * 100, 1), "dd_end_pct": None if ongoing else round((c[e] / rm - 1) * 100, 1),
            "vol_x": round(float(v[s] / vol_avg), 1), "base_range_pct": round((base_hi - base_lo) / base_lo * 100, 1),
            "kd_low_cross": dates[kd_low[0]] if kd_low else None,
            "kd_k_at_cross": round(float(K[kd_low[0]]), 1) if kd_low else None,
            "kd_hi_streak": int(kd_hi_streak),
            "macd_zero_cross_n": len(zero2),
            "macd_zero_cross_dates": [dates[t] for t in zero2[:3]],
            "osc_red_streak": int(osc_red),
            "full": full,
        }
        if us:
            for key in ("sox", "ndx"):
                d = us.get(key) or {}
                xs, ys = [], []
                for t in range(s, e + 1):
                    if not np.isnan(ret[t]) and dates[t] in d:
                        xs.append(ret[t])
                        ys.append(d[dates[t]])
                ev[f"corr_{key}"] = _pearson(xs, ys)
        events.append(ev)
        i = e + 1
    return events, surge_n


def summarize(events, surge_n, n_stocks):
    if not events:
        return {"n_events": 0, "surge_n": surge_n, "n_stocks": n_stocks, "note": "沒有符合條件的波段"}
    gains = sorted(e["gain_pct"] for e in events)
    days = sorted(e["days"] for e in events)
    full = [e for e in events if e["full"]]
    mid = lambda xs: xs[len(xs) // 2]  # noqa: E731
    out = {
        "n_events": len(events), "surge_n": surge_n, "n_stocks": n_stocks,
        "n_stocks_with_event": len({e.get("symbol") for e in events}),
        "full_n": len(full), "full_stocks": len({e.get("symbol") for e in full}),
        "median_gain_pct": mid(gains), "median_days": mid(days),
        "ongoing_n": sum(1 for e in events if e["ongoing"]),
        "kd_low_cross_n": sum(1 for e in events if e["kd_low_cross"]),
        "macd_zero2_n": sum(1 for e in events if e["macd_zero_cross_n"] >= 2),
        "osc_red5_n": sum(1 for e in events if e["osc_red_streak"] >= 5),
    }
    for key in ("corr_sox", "corr_ndx"):
        xs = [e[key] for e in events if e.get(key) is not None]
        out[f"median_{key}"] = round(sorted(xs)[len(xs) // 2], 3) if xs else None
    return out
