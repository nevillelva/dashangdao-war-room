"""B4 盤中 MACD 回測純函式的離線測試。python3 test_intraday_macd_bt.py"""
import random
from datetime import datetime, timedelta

import intraday_macd_bt as M

FAIL = []


def check(name, cond, extra=""):
    if cond:
        print("✅", name)
    else:
        FAIL.append(name)
        print("❌", name, extra)


def synth_rows(days, seed=1, drift=0.0):
    """產生每日 09:00～13:30 的 1 分K（ts=結束時間），隨機漫步。"""
    rnd = random.Random(seed)
    rows, px = [], 100.0
    d = datetime(2026, 9, 1)
    made = 0
    while made < days:
        if d.weekday() < 5:
            for m in range(270):
                st = d.replace(hour=9, minute=0) + timedelta(minutes=m)
                o = px
                px = max(1.0, px * (1 + rnd.gauss(drift, 0.0008)))
                rows.append({"ts": st + timedelta(minutes=1), "Open": o, "High": max(o, px) * 1.0002,
                             "Low": min(o, px) * 0.9998, "Close": px, "Volume": 10})
            made += 1
        d += timedelta(days=1)
    return rows


b1 = M.minute_bars(synth_rows(3))
check("1 分K：每日 270 根", len(b1) == 270 * 3, len(b1))
check("ts 結束時間 → 起始時間（首根 09:00）", b1[0][0].hour == 9 and b1[0][0].minute == 0)
check("盤前盤後被排除", M.minute_bars([{"ts": datetime(2026, 9, 1, 8, 59), "Open": 1, "High": 1, "Low": 1, "Close": 1, "Volume": 1},
                                        {"ts": datetime(2026, 9, 1, 13, 31), "Open": 1, "High": 1, "Low": 1, "Close": 1, "Volume": 1}]) == [])
check("量為 0 略過", M.minute_bars([{"ts": datetime(2026, 9, 1, 9, 5), "Open": 1, "High": 1, "Low": 1, "Close": 1, "Volume": 0}]) == [])

b15 = M.resample(b1, 15)
check("15 分K：每日 18 根", len(b15) == 18 * 3, len(b15))
check("15 分K 首根結束 09:15", b15[0]["end"].hour == 9 and b15[0]["end"].minute == 15)
b60 = M.resample(b1, 60)
check("60 分K：每日 5 根（最後一根 30 分鐘不完整）", len(b60) == 15 and sum(1 for b in b60 if not b["full"]) == 3, [b["full"] for b in b60])

# EMA／MACD：常數序列 → 全 0
dif, dea, hist = M.macd([10.0] * 50)
check("常數序列 MACD=0", all(abs(x) < 1e-12 for x in dif + dea + hist))
# 先跌後漲 → 出現金叉
ser = [100 - i * 0.5 for i in range(60)] + [70 + i * 0.8 for i in range(60)]
d2, e2, h2 = M.macd(ser)
cross = [i for i in range(1, len(h2)) if h2[i - 1] <= 0 < h2[i]]
check("先跌後漲會出現金叉", len(cross) >= 1 and cross[0] >= 60, cross[:3])

# 訊號：只在視窗、暖機後、完整K棒
bars = M.resample(M.minute_bars(synth_rows(12, seed=3)), 15)
sigs = M.find_signals(bars)
check("訊號只落在 09:45～12:00", all((s["end"].hour * 60 + s["end"].minute) >= 585 and (s["end"].hour * 60 + s["end"].minute) <= 720 for s in sigs))
check("訊號種類合法", {s["kind"] for s in sigs} <= {"gc", "gc0", "dc", "dc0"})
check("暖機前不出訊號", all(s["i"] >= 35 for s in sigs))

# 前瞻報酬
cbd = M.day_closes(bars)
days = sorted(cbd)
eod, nd = M.forward_returns(bars, 40, cbd, days)
check("到當日收盤報酬算法", abs(eod - (cbd[bars[40]["date"]] / bars[40]["c"] - 1) * 100) < 1e-9)
last_i = max(i for i, b in enumerate(bars) if b["date"] == days[-1])
check("最後一日沒有次日報酬", M.forward_returns(bars, last_i, cbd, days)[1] is None)

# 評估：多檔隨機漫步 → 超額報酬約 0（|t| 不應大到離譜）
per = {f"S{k}": M.resample(M.minute_bars(synth_rows(30, seed=100 + k)), 15) for k in range(12)}
res = M.evaluate(per, "15m")
n_gc = res["gc"]["eod"].get("n", 0)
check("評估有產出訊號", n_gc > 20, n_gc)
check("隨機漫步下金叉超額報酬不顯著", abs(res["gc"]["eod"].get("t", 0)) < 3.5, res["gc"])
check("結果含四種訊號", all(k in res for k in ("gc", "gc0", "dc", "dc0")))

# 稀疏成交（冷門股很多分鐘沒有成交）：K 棒仍應視為完整
_sp = [{"ts": datetime(2026, 9, 1, 9, 0) + timedelta(minutes=m + 1), "Open": 10, "High": 10, "Low": 10, "Close": 10, "Volume": 5}
       for m in range(270) if m % 5 == 0]
_spb = M.resample(M.minute_bars(_sp), 15)
check("稀疏成交的 15 分K 仍為完整", len(_spb) == 18 and all(x["full"] for x in _spb), len(_spb))

print("\n全部通過" if not FAIL else f"\n失敗 {len(FAIL)} 項：{FAIL}")
raise SystemExit(1 if FAIL else 0)
