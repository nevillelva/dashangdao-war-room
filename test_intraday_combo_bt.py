"""【2026-10-22】多時點組合回測（intraday_combo_bt.py）離線測試。"""
import sys
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

import intraday_combo_bt as X
import intraday_timing_bt as IT

ok = True


def chk(n, c, extra=None):
    global ok
    print(("✅ " if c else "❌ ") + n + ("" if c else f"  → {extra}"))
    ok = ok and c


EPOCH = datetime(1970, 1, 1)


def mk_day(date, closes, opens=None):
    t, o, h, l, c, v = [], [], [], [], [], []
    base = datetime.strptime(date, "%Y-%m-%d").replace(hour=9)
    prev = opens if opens is not None else closes[0]
    for k, cc in enumerate(closes):
        oo = prev
        t.append(int(((base + timedelta(minutes=5 * k)) - EPOCH).total_seconds() // 60))
        o.append(oo); c.append(cc); h.append(max(oo, cc)); l.append(min(oo, cc)); v.append(100.0)
        prev = cc
    return t, o, h, l, c, v


def mk_data(days):
    cols = [[], [], [], [], [], []]
    for d in days:
        for k in range(6):
            cols[k] += d[k]
    return {"t": np.array(cols[0]), "o": np.array(cols[1], dtype=float), "h": np.array(cols[2], dtype=float),
            "l": np.array(cols[3], dtype=float), "c": np.array(cols[4], dtype=float), "v": np.array(cols[5], dtype=float)}


# 1. 條件旗標：開 100，一路緩漲到 105（55 根）→ A1/A2/A3、B1~B4、C1~C5 都成立
up = [100 + 5 * (k + 1) / 55 for k in range(55)]
pf = IT.day_profiles(mk_data([mk_day("2025-03-03", up, opens=100.0)]))["2025-03-03"]
a, b, c = X.flags(pf)
chk("緩漲日：A0~A3 全成立", a == 0b1111, bin(a))
chk("緩漲日：B0~B4 全成立", b == 0b11111, bin(b))
chk("緩漲日：C0~C5 全成立", c == 0b111111, bin(c))
# 開高走低：開 100、09:30 前 101、之後跌到 98
dn = [101.0] * 6 + [98.0] * 49
pf2 = IT.day_profiles(mk_data([mk_day("2025-03-04", dn, opens=100.0)]))["2025-03-04"]
a2, b2, c2 = X.flags(pf2)
chk("開高走低：A1 成立（09:30 高於開盤）", a2 & 0b10, bin(a2))
chk("開高走低：B1 不成立（09:30~11:00 跌破開盤）、B4 不成立", not (b2 & 0b10) and not (b2 & 0b10000), bin(b2))
chk("開高走低：C1 不成立（12:30 低於開盤）", not (c2 & 0b10), bin(c2))

# 2. 出場模擬
e = np.array([1.0, 1.0, 1.0, 1.0], dtype=np.float32)
D = np.full((4, 3, 4), np.nan, dtype=np.float32)
D[0] = [[1.00, 1.06, 0.99, 1.04], [1.04, 1.05, 1.02, 1.03], [1.03, 1.04, 1.01, 1.02]]   # 第 1 天碰到 +5% 停利
D[1] = [[0.98, 0.99, 0.93, 0.95], [0.95, 0.96, 0.94, 0.95], [0.95, 0.96, 0.94, 0.96]]   # 第 1 天碰到 −5% 停損
D[2] = [[0.90, 0.92, 0.89, 0.91], [0.91, 0.92, 0.90, 0.91], [0.91, 0.92, 0.90, 0.92]]   # 跳空開 0.90 → 以開盤出
D[3] = [[1.00, 1.01, 0.99, 1.00], [1.00, 1.01, 0.99, 1.00], [1.00, 1.01, 0.99, 1.02]]   # 都沒碰到 → 第 3 天收盤
wf = np.zeros((4, 3), dtype=bool)
wx = np.full((4, 3), np.nan, dtype=np.float32)
r = X.simulate(e, D, wf, wx, 3, 5, 5, False)
chk("停利 5%：以 +5% 出", abs(r[0] - (5 - X.COST)) < 1e-4, r)
chk("停損 5%：以 −5% 出", abs(r[1] - (-5 - X.COST)) < 1e-4, r)
chk("跳空跌破停損：以開盤 0.90 出", abs(r[2] - (-10 - X.COST)) < 1e-4, r)
chk("都沒碰到：第 3 天收盤 +2%", abs(r[3] - (2 - X.COST)) < 1e-4, r)
D2 = D.copy(); D2[0, 0] = [1.00, 1.06, 0.93, 1.00]
r2 = X.simulate(e[:1], D2[:1], wf[:1], wx[:1], 3, 5, 5, False)
chk("同一天停利停損都碰到 → 保守算停損", abs(r2[0] - (-5 - X.COST)) < 1e-4, r2)
wf2 = wf.copy(); wf2[3, 1] = True
wx2 = wx.copy(); wx2[3, 1] = 0.995
r3 = X.simulate(e, D, wf2, wx2, 3, None, None, True)
chk("12:30 轉弱：第 2 天 13:20 以 0.995 賣", abs(r3[3] - (-0.5 - X.COST)) < 1e-4, r3)
r4 = X.simulate(e, D, wf2, wx2, 3, None, None, False)
chk("轉弱規則關閉：照抱到第 3 天收盤", abs(r4[3] - (2 - X.COST)) < 1e-4, r4)
e5 = np.array([1.02], dtype=np.float32)
r5 = X.simulate(e5, D[3:4], wf[3:4], wx[3:4], 3, None, None, False)
chk("12:30 以 1.02 進場 → 報酬以進場價計算", abs(r5[0] - ((1.02 / 1.02 - 1) * 100 - X.COST)) < 1e-4, r5)

# 3. 端到端：30 個交易日，第 0 天有 c6 與 base 訊號；第 1 天是緩漲日（條件全過）
dates = pd.bdate_range("2023-01-02", periods=30)
ds = [d.strftime("%Y-%m-%d") for d in dates]
closes = [100.0, 105.0] + [105.0 + 0.5 * k for k in range(1, 29)]
opens = [100.0, 100.0] + closes[1:-1]
df = pd.DataFrame({"Open": opens, "High": [max(o, c) + 1 for o, c in zip(opens, closes)],
                   "Low": [min(o, c) - 1 for o, c in zip(opens, closes)], "Close": closes}, index=dates)
days = [mk_day(ds[1], up, opens=100.0)] + [mk_day(ds[k], [closes[k]] * 55, opens=opens[k]) for k in range(2, 30)]
prof = IT.day_profiles(mk_data(days))
rows = [{"date": ds[0], "hits": ["c6", "base"]}]
ev = X.combo_events("S", df, rows, prof, bull={ds[1]: True})
chk("訊號隔天產生 c6 與 base 事件", set(ev) == {"c6", "base"} and len(ev["c6"]) == 1, list(ev))
x = ev["c6"][0]
chk("進場日旗標＝緩漲日（全成立）", x["f"] == (0b1111, 0b11111, 0b111111), x["f"])
chk("開盤進場相對價 1.0、13:20 進場≈1.0473", x["e"][0] == 1.0 and abs(x["e"][4] - up[51] / 100) < 1e-4, x["e"])
chk("c6 路徑 20 日、base 路徑 60 日", x["D"].shape == (20, 4) and ev["base"][0]["D"].shape == (60, 4))
chk("第 1 天收盤相對進場日開盤＝105.5/100", abs(x["D"][0, 3] - 1.055) < 1e-5, x["D"][0])

# 4. analyze 能跑完（門檻放低）並輸出結構
evs = {k: v * 1 for k, v in ev.items()}
G = X.grid_stats(evs["c6"], "c6")
chk("grid 形狀：遮罩 120 × 出場 150", G["E"]["13:20"]["is"]["n"].shape == (120, 150), G["E"]["13:20"]["is"]["n"].shape)
out = X.analyze_key(G, "c6", None, min_is=1, min_oos=0)
chk("analyze_key 有組合數與現行做法", out["combos"] > 0 and "現行(開盤進、抱預設天數)" in out, list(out))
sys.exit(0 if ok else 1)
