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

# ---- 未還原價漲幅（raw_gain_pct）與股利貢獻（div_contrib_pct）----
# 合成：還原價 A＝synth() 路徑（連續、無除息）。兩次現金除息發生在起漲後、高點前：第 170、230 日，
# 除息前一日收盤的殖利率分別 6%、5%。yfinance 式還原因子 F_t＝∏_{k>t} φ_k（φ_k＝1－殖利率），未還原價 raw＝A／F。
# 高點在兩次除息之後 → F_peak＝1，F_start＝φ1·φ2，調整因子 K＝F_peak／F_start＝1／(φ1·φ2)。
Y1, Y2, K1, K2 = 0.06, 0.05, 170, 230
dates2, o2, h2, l2, A, v2 = synth()
F = np.ones(len(A))
F[:K1] *= (1 - Y1)
F[:K2] *= (1 - Y2)
raw = A / F
K_exp = 1 / ((1 - Y1) * (1 - Y2))
PAT = ("start", "peak", "end", "ongoing", "days", "gain_pct", "full", "kd_low_cross", "macd_zero_cross_n", "osc_red_streak", "vol_x")

ev_raw, _ = W.detect_events(dates2, o2, h2, l2, A, v2, raw_c=raw)
ev_adj, _ = W.detect_events(dates2, o2, h2, l2, A, v2)
chk("有除息：仍偵測到 1 個波段，且型態欄位與只用還原價時完全相同",
    len(ev_raw) == 1 and len(ev_adj) == 1 and all(ev_raw[0][k] == ev_adj[0][k] for k in PAT),
    (len(ev_raw), len(ev_adj)))
e2 = ev_raw[0]
si, pi = dates2.index(e2["start"]), dates2.index(e2["peak"])
raw_exp = (raw[pi] / raw[si] - 1) * 100
gain_exp = (A[pi] / A[si] - 1) * 100
print(f"  數值：起漲 {e2['start']} 高點 {e2['peak']} 終點 {e2['end']}；F_peak={F[pi]:.4f} F_start={F[si]:.6f} K_exp={K_exp:.6f}")
print(f"  數值：raw_gain_pct={e2['raw_gain_pct']} 獨立計算={raw_exp:.4f}；gain_pct={e2['gain_pct']} 獨立計算={gain_exp:.4f}；div_contrib_pct={e2['div_contrib_pct']}")
chk("F_peak＝1 且 F_start＝φ1·φ2（合成因子正確）", F[pi] == 1.0 and abs(F[si] - (1 - Y1) * (1 - Y2)) < 1e-12)
chk("raw_gain_pct 與獨立計算一致（容差 0.06 pp）", abs(e2["raw_gain_pct"] - raw_exp) < 0.06, (e2["raw_gain_pct"], raw_exp))
chk("gain_pct（還原價）與獨立計算一致（容差 0.06 pp）", abs(e2["gain_pct"] - gain_exp) < 0.06, (e2["gain_pct"], gain_exp))
rel = (1 + e2["raw_gain_pct"] / 100) * K_exp
chk("(1+raw)×調整因子 ≈ (1+gain)（容差 0.2 pp，即四捨五入誤差）", abs(rel - (1 + e2["gain_pct"] / 100)) < 0.002, (rel, 1 + e2["gain_pct"] / 100))
chk("div_contrib_pct＝gain_pct－raw_gain_pct", abs(e2["div_contrib_pct"] - (e2["gain_pct"] - e2["raw_gain_pct"])) < 1e-9, e2["div_contrib_pct"])
chk("兩次除息使還原價漲幅高於未還原（股利貢獻 > 0）", e2["div_contrib_pct"] > 0, e2["div_contrib_pct"])
chk("起漲日未還原價 > 還原價（除息前未還原價較高）", raw[si] > A[si], (raw[si], A[si]))

ev0, _ = W.detect_events(dates2, o2, h2, l2, A, v2, raw_c=A)
chk("無除息（raw_c＝還原價）：raw_gain＝gain、股利貢獻＝0", ev0 and ev0[0]["raw_gain_pct"] == ev0[0]["gain_pct"] and ev0[0]["div_contrib_pct"] == 0.0, ev0[:1])
evn, _ = W.detect_events(dates2, o2, h2, l2, A, v2)
chk("未傳 raw_c：新欄位為 None（向下相容）", evn and evn[0]["raw_gain_pct"] is None and evn[0]["div_contrib_pct"] is None)
rawn = raw.copy(); rawn[si] = np.nan
evnan, _ = W.detect_events(dates2, o2, h2, l2, A, v2, raw_c=rawn)
chk("起漲日未還原價為 NaN → raw_gain_pct 為 None、不出錯", evnan and evnan[0]["raw_gain_pct"] is None and evnan[0]["gain_pct"] == e2["gain_pct"])
try:
    W.detect_events(dates2, o2, h2, l2, A, v2, raw_c=raw[:-1]); bad_len = False
except ValueError:
    bad_len = True
chk("raw_c 長度與日期不符 → 拋出 ValueError（不默默錯位）", bad_len)
sm = W.summarize([dict(e2, symbol="A"), dict(ev0[0], symbol="B")], 2, 2)
chk("summarize 含 median_raw_gain_pct、median_div_contrib_pct、n_raw_gain", sm.get("median_raw_gain_pct") is not None and sm.get("median_div_contrib_pct") is not None and sm.get("n_raw_gain") == 2, sm)
sm0 = W.summarize([dict(evn[0], symbol="A")], 1, 1)
chk("summarize：全部無未還原價 → median 為 None", sm0["median_raw_gain_pct"] is None and sm0["n_raw_gain"] == 0, sm0)
chk("full_match／top_gain 來源事件含新欄位", "raw_gain_pct" in e2 and "div_contrib_pct" in e2)
sys.exit(0 if ok else 1)
