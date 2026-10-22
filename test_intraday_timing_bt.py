#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_intraday_timing_bt.py —— 盤中進場時點網格：5 分K→時點價、多方時點報酬組合（比例、不受還原價影響）、查7 型盤中買、已持有者隔天賣點、
尾盤時段排序、查8 空單時點、連續交易日檢查、成交量分布。"""
from datetime import datetime, timedelta
import numpy as np
import pandas as pd
import intraday_timing_bt as X

bad = 0
def chk(n, c, e=""):
    global bad; print("✅" if c else "❌", n, "" if c else e); bad += (not c)

EPOCH = datetime(1970, 1, 1)


def mk_day(date, closes, opens=None, vols=None):
    """一天 55 根 5 分K（09:00～13:30 起始），closes 長度 55。"""
    t, o, h, l, c, v = [], [], [], [], [], []
    base = datetime.strptime(date, "%Y-%m-%d").replace(hour=9)
    prev = opens[0] if opens else closes[0]
    for k, cc in enumerate(closes):
        oo = (opens[k] if opens else prev)
        t.append(int(((base + timedelta(minutes=5 * k)) - EPOCH).total_seconds() // 60))
        o.append(oo); c.append(cc); h.append(max(oo, cc)); l.append(min(oo, cc)); v.append((vols[k] if vols else 100.0))
        prev = cc
    return t, o, h, l, c, v


def mk_data(days):
    cols = [[], [], [], [], [], []]
    for d in days:
        for k in range(6):
            cols[k] += d[k]
    return {"t": np.array(cols[0]), "o": np.array(cols[1], dtype=float), "h": np.array(cols[2], dtype=float),
            "l": np.array(cols[3], dtype=float), "c": np.array(cols[4], dtype=float), "v": np.array(cols[5], dtype=float)}


# 一天：開 100，線性漲到 110（55 根）
cl = [100 + 10 * (k + 1) / 55 for k in range(55)]
d1 = mk_day("2025-03-03", cl, opens=[100.0] + cl[:-1])
pf = X.day_profiles(mk_data([d1]))["2025-03-03"]
chk("開盤＝第一根開盤", pf["open"] == 100.0)
k1245 = (12 * 60 + 40 - 540) // 5           # 12:40 那根
chk("12:45 價＝12:40 那根收盤", abs(pf["p"]["12:45"] - cl[k1245]) < 1e-9, (pf["p"]["12:45"], cl[k1245]))
chk("10:00 前最低＝開盤", abs(pf["lo"]["10:00"] - 100.0) < 1e-9)
chk("收盤＝最後一根收盤", abs(pf["close"] - 110.0) < 1e-9)

# A：多方時點報酬＝日K 開盤→出場 ×（盤中開盤/T 價）
idx = pd.bdate_range("2025-01-01", periods=60)
idx = idx[idx <= pd.Timestamp("2025-03-31")]
O = np.full(len(idx), 50.0); C = np.full(len(idx), 50.0)      # 日K 是還原價（尺度不同）
di = list(idx.strftime("%Y-%m-%d")).index("2025-03-03")
O[di] = 50.0; C[di] = 55.0                                     # 與盤中同比例（100→110 對 50→55）
C[di - 1 + 10] = 60.0
df = pd.DataFrame({"Open": O, "High": O, "Low": O, "Close": C, "Volume": 1.0}, index=idx)
rows = [{"date": idx[di - 1].strftime("%Y-%m-%d"), "hits": {"c4"}}]
ev = X.long_events("S", df, rows, {"2025-03-03": pf}, keys=("c4",))
chk("找到 1 筆多方事件", len(ev) == 1, ev)
e = ev[0]
rT = (e["g"] * e["ratio"]["12:45"] - 1) * 100 - X.COST
exp = (60.0 / (50.0 * cl[k1245] / 100.0) - 1) * 100 - X.COST
chk("12:45 進場報酬＝出場/（日K開盤×T價/盤中開盤）−成本", abs(rT - exp) < 1e-9, (rT, exp))
chk("確認條件：守住開盤（一路漲）", "守住開盤(T前最低≥開盤×0.995且T價>開盤)" in e["cond"]["10:00"] and "拉回(T價≤開盤×0.99)" not in e["cond"]["10:00"])
pf_bad = dict(pf, close=pf["close"] * 1.5)
chk("盤中與日K 比例不一致 → 略過", X.long_events("S", df, rows, {"2025-03-03": pf_bad}, keys=("c4",)) == [])
res = X.analyze_long(ev * 3)
chk("analyze_long 有時點×條件與「同批開盤買」比較", "vs_open_same" in res["c4"]["grid"]["12:45"]["無條件"] and res["c4"]["grid"]["12:45"]["無條件"]["n"] == 3)

# B／C／E 需要連續交易日
def flat_day(date, px, close=None, opens=None):
    cc = [px] * 54 + [close if close is not None else px]
    return mk_day(date, cc, opens=opens)

dates = ["2025-03-03", "2025-03-04", "2025-03-05", "2025-03-06", "2025-03-07"]
cal_next = {a: b for a, b in zip(dates, dates[1:])}
# 3/3 收 100；3/4 盤中 13:00 漲到 108（未鎖）、收 110（鎖漲停）；3/5 開 115
cl4 = [100 + 8 * min(1, (k + 1) / 48) for k in range(54)] + [110.0]
dd = [flat_day("2025-03-03", 100.0), mk_day("2025-03-04", cl4, opens=[100.0] + cl4[:-1]), flat_day("2025-03-05", 115.0, opens=[115.0] * 55)]
prof = X.day_profiles(mk_data(dd))
pdays = sorted(prof.items())
ok = X.consecutive(pdays, cal_next)
chk("連續交易日檢查", ok == {1}, ok)
ov = X.overnight_events("S", pdays, ok=ok)
e13 = [x for x in ov if x["T"] == "13:00"]
chk("13:00 漲 8%（7~8.5% 帶）未鎖 → 買進", len(e13) == 1 and e13[0]["band"] == "7~8.5%", e13)
chk("賣隔天開盤＝115/108−成本", abs(e13[0]["r_open"] - ((115 / 108 - 1) * 100 - X.COST)) < 1e-6)
chk("事後標記當天收漲停", e13[0]["lock_close"] is True)
chk("連續性不成立就不算", X.overnight_events("S", pdays, ok=set()) == [])
he = X.hold_exit_events(pdays, ok=ok)
chk("已持有者：前一天收漲停 → 隔天開盤賣＝115/110−成本", len(he) == 1 and abs(he[0]["開盤"] - ((115 / 110 - 1) * 100 - X.COST)) < 1e-6, he)

# D：尾盤時段排序（30 檔以上才算）
wr = []
for s in range(40):
    up = s % 2 == 0
    wr.append({"sym": f"S{s}", "date": "2025-03-03", "w": {"12:45-13:15": ((1.0 if up else -1.0) + s * 0.001, 0.1 + 0.01 * s)},
               "on": 1.0 if up else -1.0, "nx": 2.0 if up else -2.0})
for d in ("2025-03-04", "2025-03-05", "2025-03-06", "2025-03-07", "2025-03-10"):
    wr += [dict(r, date=d) for r in wr[:40]]
aw = X.analyze_windows(wr)
chk("時段強減弱：隔天收盤差＝4", abs(aw["12:45-13:15"]["強減弱_隔天收盤(收→收)"]["avg"] - 4.0) < 1e-9, aw["12:45-13:15"])
chk("少於 30 檔的日子不算", X.analyze_windows(wr[:20])["12:45-13:15"]["強減弱_隔天收盤(收→收)"]["n"] == 0)

# E：查8 空單時點
d_e = ["2025-03-03", "2025-03-04", "2025-03-05", "2025-03-06", "2025-03-07"]
cn = {a: b for a, b in zip(d_e, d_e[1:])}
days_e = [flat_day(d_e[0], 100.0), flat_day(d_e[1], 106.0, opens=[106.0] * 55), flat_day(d_e[2], 108.0, opens=[108.0] * 55),
          mk_day(d_e[3], [113.4] * 30 + [111.0] * 25, opens=[113.4] * 55), flat_day(d_e[4], 110.0, opens=[110.0] * 55)]
pe = sorted(X.day_profiles(mk_data(days_e)).items())
se = X.short_events("S", pe, cn)
chk("查8：開高 5%（3~7%）事件", len(se) == 1, se)
chk("開盤空 113.4、13:20 補 111 → (113.4−111)/113.4 −0.435", abs(se[0]["open"] - ((113.4 - 111) / 113.4 * 100 - 0.435)) < 1e-6, se[0])
chk("analyze_short 有各時點", "09:30" in X.analyze_short(se))

vp = X.volume_profile([pf])
chk("成交量分布合計≈100%", abs(sum(vp.values()) - 100) < 0.5, sum(vp.values()))

# F：當沖多方（10:00 進、13:25 出）
cA = [98.0] * 12 + [99.0] * 43          # 開 100、10:00 價 98（低於開盤）、13:20 價 99
cB = [101.0] * 12 + [102.0] * 43        # 開 100、10:00 價 101（不低於開盤）、13:20 價 102
dtp = X.day_profiles(mk_data([mk_day("2025-03-03", cA, opens=[100.0] + cA[:-1]), mk_day("2025-03-04", cB, opens=[100.0] + cB[:-1])]))
dtb = X.daytrade_baseline(dtp, {"2025-03-03": True, "2025-03-04": False})
chk("當沖基準：2 天各 1 筆", len(dtb) == 2, dtb)
ra = [e for e in dtb if e[1] == "2025-03-03"][0]
chk("低於開盤那天：g＝99/98、below_open、大盤線上", abs(ra[2] - 99 / 98) < 1e-9 and ra[3] is True and ra[4] is True, ra)
adt = X.analyze_daytrade(dtb)["ALL"]["groups"]
chk("做多＝(99/98−1)−0.435（只看低於開盤）", abs(adt["10:00 低於開盤"]["long"]["avg"] - ((99 / 98 - 1) * 100 - 0.435)) < 1e-3, adt["10:00 低於開盤"])
chk("做空＝−(102/101−1)−0.435（只看不低於開盤）", abs(adt["10:00 不低於開盤"]["short"]["avg"] - (-(102 / 101 - 1) * 100 - 0.435)) < 1e-3, adt["10:00 不低於開盤"])
chk("大盤線下組只有 3/4 那筆", adt["大盤60日線下"]["long"]["n"] == 1, adt["大盤60日線下"])
chk("缺 13:25 價的日子不算", X.daytrade_baseline({"2025-03-05": {"open": 100.0, "p": {"10:00": 99.0}}}) == [])
raise SystemExit(1 if bad else 0)
