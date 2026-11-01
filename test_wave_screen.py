"""波段型態篩選離線測試（合成價量，不連網）。"""
import sys
import numpy as np
import wave_screen as W

ok = True


def chk(n, c, e=None):
    global ok
    print(("✅ " if c else "❌ ") + n + ("" if c else f"  → {e}"))
    ok = ok and c


def synth():
    """150 日潛伏（價在 100±8%）→ 第 150 日爆量突破 → 之後一路漲到 3 倍 → 回落 30%。"""
    rng = np.random.default_rng(3)
    base = 100 + rng.uniform(-4, 4, 150)
    c = list(base)
    v = list(np.full(150, 1000.0))
    s_px = max(c[-60:]) * 1.05                    # 確實收盤突破前 60 日最高
    c.append(s_px); v.append(6000.0)              # 爆量突破（3～4 倍以上）
    up = np.linspace(s_px, s_px * 3.0, 200)       # 主升段到 3 倍
    c += list(up); v += [1500.0] * 200
    dn = np.linspace(up[-1], up[-1] * 0.65, 60)   # 回落 35%
    c += list(dn); v += [1200.0] * 60
    c = np.array(c)
    o = np.r_[c[0], c[:-1]]
    o[150] = c[149]                               # 爆量日收紅
    h = c * 1.01
    l = c * 0.99
    v = np.array(v)
    dates = [f"D{k:04d}" for k in range(len(c))]
    return dates, o, h, l, c, v


dates, o, h, l, c, v = synth()
evs, surge = W.detect_events(dates, o, h, l, c, v)
chk("合成爆量突破被偵測到", surge >= 1 and len(evs) >= 1, (surge, len(evs)))
e = evs[0] if evs else {}
chk("起漲日是第 150 日", e.get("start") == "D0150", e.get("start"))
chk("主升段漲幅 ≥100%", (e.get("gain_pct") or 0) >= 100, e.get("gain_pct"))
chk("趨勢結束日在回落之後（非仍在進行）", e.get("ongoing") is False, e)
chk("回落 ≥25% 結束", (e.get("dd_end_pct") or 0) <= -25, e.get("dd_end_pct"))
chk("爆量倍數計算正確（約 6 倍）", 5 <= (e.get("vol_x") or 0) <= 7, e.get("vol_x"))

# 潛伏期太寬（振幅 >35%）→ 不算
c2 = c.copy(); c2[:150] = np.linspace(60, 140, 150)
h2, l2 = c2 * 1.01, c2 * 0.99                 # 高低價必須跟著收盤改
ev2, _ = W.detect_events(dates, o, h2, l2, c2, v)
chk("潛伏期振幅過大（非盤整）→ 不算事件", len(ev2) == 0, len(ev2))

# 沒漲到 100% → 不算
c3 = c.copy(); c3[150:] = np.linspace(c[150], c[150] * 1.5, len(c) - 150)
ev3, _ = W.detect_events(dates, o, c3 * 1.01, c3 * 0.99, c3, v)
chk("主升段未達 100%（只到 50%）→ 不算", len(ev3) == 0, len(ev3))

# 爆量不夠（只 1.5 倍）→ 不算突破
v4 = v.copy(); v4[150] = 1500.0
ev4, s4 = W.detect_events(dates, o, h, l, c, v4)
chk("量未達 3 倍 → 不算爆量突破", s4 == 0 and len(ev4) == 0, (s4, len(ev4)))

# 完整特徵：要有 KD 低檔金叉、MACD 零軸上方二次金叉、柱狀連續翻紅 ≥5
chk("KD 指標輸出長度一致", len(W.kd(h, l, c)[0]) == len(c))
d_, e_, o_ = W.macd(c)
chk("MACD 柱狀＝DIF－DEA", np.allclose(o_, d_ - e_))
chk("EMA 第一個值等於輸入", abs(W.ema([5, 6, 7], 3)[0] - 5) < 1e-9)
chk("無事件時 summarize 不出錯", W.summarize([], 0, 10)["n_events"] == 0)
s = W.summarize(evs and [dict(evs[0], symbol="X")], 1, 10)
chk("summarize 統計欄位齊全", s["n_events"] == 1 and "median_gain_pct" in s and "full_n" in s, s)
p = W._pearson([1, 2, 3] * 10, [2, 4, 6] * 10)
chk("相關係數：完全同向 ≈ 1", p is not None and p > 0.99, p)
chk("相關係數：樣本太少回 None", W._pearson([1, 2], [2, 3]) is None)
sys.exit(0 if ok else 1)
