#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_cmd_playbook_bt.py —— 各指令專屬進出場、0050 超額、美股前一晚對齊（無前視）、查4 季底窗口、查7 美股條件出場、查8 補不了、統計與篩選門檻。"""
import numpy as np
import pandas as pd
import cmd_playbook_bt as P

bad = 0
def chk(n, c, e=""):
    global bad; print("✅" if c else "❌", n, "" if c else e); bad += (not c)


def frame(close, start="2025-01-02", o=None, h=None, l=None):
    idx = pd.bdate_range(start, periods=len(close))
    c = np.array(close, dtype=float)
    o = c if o is None else np.array(o, dtype=float)
    h = np.maximum(o, c) if h is None else np.array(h, dtype=float)
    l = np.minimum(o, c) if l is None else np.array(l, dtype=float)
    return pd.DataFrame({"Open": o, "High": h, "Low": l, "Close": c, "Volume": 1.0}, index=idx)


# 0050 平盤（超額＝個股報酬）
n = 120
etf = frame([100.0] * n)
tw, px = P.build_tw(None, etf)
df = frame([100 + k * 0.1 for k in range(n)])
p = P.Px(df)

# 查1 A 持 5 日：隔日開盤進、第 i+5 天收盤出
i = 30
v = P.playbook("c1", p, i, px, {})
exp = (p.C[i + 5] / p.O[i + 1] - 1) * 100 - 0.585
chk("查1 A 持5日報酬＝C[i+5]/O[i+1]−成本", abs(v["A 持5日"]["r"] - exp) < 1e-9 and v["A 持5日"]["hold"] == 5, v)
chk("0050 平盤 → 超額＝報酬", abs(v["A 持5日"]["ex"] - v["A 持5日"]["r"]) < 1e-9)

# 查2 B 停損：第 i+3 天收盤跌破 MA20×0.97 → 隔天開盤出
c2 = [100.0] * 40 + [100, 100, 100, 90, 95, 96, 97, 98, 99, 100, 101, 102]
d2 = frame(c2)
p2 = P.Px(d2)
v2 = P.playbook("c2", p2, 40, px, {})
chk("查2 B 跌破 MA20×0.97 → 隔天開盤出（持有 4 天）", v2["B 跌破MA20×0.97就出(≤10日)"]["hold"] == 4, v2)

# 查7：漲停收盤買→隔日開盤賣；費半前晚 +1.5% → 抱到收盤
c7 = [100.0] * 30 + [110.0, 112.0]
o7 = [100.0] * 30 + [105.0, 115.0]
d7 = frame(c7, o=o7)
p7 = P.Px(d7)
v7 = P.playbook("c7", p7, 30, px, {"sox": 1.5})
chk("查7 A 開盤賣＝115/110−成本", abs(v7["A 漲停收盤買→隔日開盤賣"]["r"] - ((115 / 110 - 1) * 100 - 0.585)) < 1e-9)
chk("查7 C 費半+1.5% → 抱到收盤（112）", abs(v7["C 費半前晚≥+1%抱到收盤，否則開盤賣"]["r"] - ((112 / 110 - 1) * 100 - 0.585)) < 1e-9)
v7b = P.playbook("c7", p7, 30, px, {"sox": -0.5})
chk("查7 C 費半−0.5% → 開盤賣", abs(v7b["C 費半前晚≥+1%抱到收盤，否則開盤賣"]["r"] - v7b["A 漲停收盤買→隔日開盤賣"]["r"]) < 1e-9)
chk("查7 收盤進場：MAE 不算訊號日盤中低點", v7["A 漲停收盤買→隔日開盤賣"]["mae"] >= 0)

# 查8：開高 5% 空；收在漲停（補不了）→ 隔天開盤補
c8 = [100.0] * 30 + [110.0, 121.0, 125.0]
o8 = [100.0] * 30 + [105.0, 115.5, 126.0]
h8 = [100.0] * 30 + [110.0, 121.0, 127.0]
d8 = frame(c8, o=o8, h=h8)
p8 = P.Px(d8)
v8 = P.playbook("c8", p8, 30, px, {"sox": 0.2})
a8 = v8["A 開高3~7%開盤空、收盤補(補不了隔天開盤補)"]
chk("查8 鎖漲停 → 隔天開盤 126 回補：(115.5−126)/115.5 −0.435", abs(a8["r"] - ((115.5 - 126) / 115.5 * 100 - 0.435)) < 1e-9, a8)
v8b = P.playbook("c8", p8, 30, px, {"sox": 1.2})
chk("查8 B 費半前晚 +1.2% → 不空", "B A＋費半前晚≥+1%不空" not in v8b and "A 開高3~7%開盤空、收盤補(補不了隔天開盤補)" in v8b)
c8x = [100.0] * 30 + [110.0, 118.0, 119.0]
o8x = [100.0] * 30 + [105.0, 118.8, 119.0]
chk("查8 開高 8%（≥7%）不做", P.playbook("c8", P.Px(frame(c8x, o=o8x)), 30, px, {}) == {})

# 查4：季底窗口。訊號日 2025-02-14（季底前一月）→ B 抱到 3 月最後交易日
d4 = frame([100 + k * 0.05 for k in range(150)], start="2025-01-02")
p4 = P.Px(d4)
i4 = p4.d.index("2025-02-14")
v4 = P.playbook("c4", p4, i4, px, {})
q = P._qe_last_idx(p4, i4)
chk("季底最後交易日＝2025-03-31", p4.d[q] == "2025-03-31", p4.d[q])
chk("查4 季底前一月有 B／C／D 變體", all(k in v4 for k in ("A 持10日", "B 抱到季底最後交易日", "C 季底前第3個交易日出", "D【觀察】季底後10日(結帳賣壓)")), list(v4))
chk("季內位置／季別", P.month_pos("2025-02-14") == "季底前一月" and P.month_pos("2025-03-03") == "季底當月" and P.month_pos("2025-04-01") == "季初月"
    and P.quarter_kind("2025-11-10") == "年底(Q4)" and P.quarter_kind("2025-05-10") == "半年(Q2)")
i4b = p4.d.index("2025-01-15")
chk("查4 季初月只有 A", list(P.playbook("c4", p4, i4b, px, {})) == ["A 持10日"])

# 美股對齊：台股 2025-01-07（週二）要用美股 2025-01-06（週一），不能用 01-07
us_idx = pd.bdate_range("2024-12-01", periods=40)
sox = pd.DataFrame({"Close": np.arange(40, dtype=float) + 100}, index=us_idx)
us = P.build_us({"sox": sox}, ["2025-01-07"])
d_prev = us_idx[us_idx < pd.Timestamp("2025-01-07")][-1]
k = list(us_idx).index(d_prev)
chk("美股取台股日期之前最後一個交易日（無前視）", abs(us["2025-01-07"]["sox"] - ((sox.Close.iloc[k] / sox.Close.iloc[k - 1] - 1) * 100)) < 1e-9, us)

# 0050 開盤跳空
etf2 = frame([100.0, 100.0, 101.0], o=[100.0, 100.0, 101.5])
tw2, _ = P.build_tw(None, etf2)
chk("0050 開盤跳空＝開/前收", abs(tw2[etf2.index[2].strftime("%Y-%m-%d")]["gap0050"] - 1.5) < 1e-9)

# 統計與篩選：大盤線下的事件很差（IS、OOS 都差）→ 排除「台股大盤60日線=線下」有效
rng = np.random.default_rng(3)
evs = []
for k in range(600):
    bear = k % 3 == 0
    d = (pd.Timestamp("2022-01-03") + pd.Timedelta(days=int(k) * 2)).strftime("%Y-%m-%d")
    r = float(rng.normal(-2.0 if bear else 1.0, 0.5))
    evs.append({"k": "c5", "sym": f"S{k % 7}", "date": d, "i": k * 30, "v": {"A 持10日": {"r": r, "ex": r, "hold": 10, "mae": -1.0}},
                "tv20": 1.0 + k, "bull60": not bear, "bull20": True, "tw5": 0.0, "gap0050": 0.0, "sox": 0.0, "ndx": 0.0, "spx": 0.0,
                "tsm": 0.0, "sox_up20": True, "mpos": "季初月", "qkind": "Q1/Q3"})
res = P.analyze(evs)
f = res["c5"]["variants"]["A 持10日"]["filters"]
chk("篩選：排除大盤 60 日線下有效", any(x["排除"] == "台股大盤60日線=線下" for x in f), f)
chk("pick_by_IS 有樣本外數字", res["c5"]["pick_by_IS"]["avg_oos"] is not None)
st = res["c5"]["variants"]["A 持10日"]["all"]
chk("統計有 n／勝率／IS／OOS／t", st["n"] == 600 and "win" in st and st["n_is"] + st["n_oos"] == 600 and st["t_ex"] is not None, st)
# 不重疊：同一檔持有 10 天內不重複進場
ov = [dict(evs[0], i=0, sym="Z", date="2022-01-03"), dict(evs[1], i=3, sym="Z", date="2022-01-06"), dict(evs[2], i=20, sym="Z", date="2022-02-01")]
chk("不重疊：持有中的第二筆被略過", len(P.nonoverlap(ov, "A 持10日")) == 2)
# 規模三等分（用母體 cut）
cut = P.tercile_cuts({"2022-01-03": [1, 2, 3, 4, 5, 6, 7, 8, 9]})
e1 = [{"date": "2022-01-03", "tv20": 2}, {"date": "2022-01-03", "tv20": 5}, {"date": "2022-01-03", "tv20": 9}]
P.size_terciles(e1, cut)
chk("規模依母體切三等分", [x["size"] for x in e1] == ["小型", "中型", "大型"], e1)
# 等權母體指數與「比同母體」超額
acc = {}
dA = frame([100.0, 110.0, 110.0], o=[100.0, 105.0, 110.0])     # 第2天 隔夜 +5%、盤中 +4.76%
dB = frame([100.0, 90.0, 90.0], o=[100.0, 95.0, 90.0])         # 第2天 隔夜 −5%、盤中 −5.26%
P.ew_accumulate(acc, dA)
P.ew_accumulate(acc, dB)
ew = P.build_ew(acc)
d1 = dA.index[1].strftime("%Y-%m-%d")
chk("等權指數：隔夜平均 0% → 開盤點位不變", abs(ew[d1][0] - 1.0) < 1e-9, ew)
tr = {"r": 3.0, "ed": d1, "ek": "o", "xd": d1, "xk": "c", "sd": 1}
evx = [{"v": {"A": tr}}]
P.attach_ew(evx, ew)
m = (ew[d1][1] / ew[d1][0] - 1) * 100
chk("比同母體超額＝報酬−等權指數同時段報酬", abs(evx[0]["v"]["A"]["ew"] - (3.0 - m)) < 1e-9)
chk("交易帶有進出場日期與方向", all(k in v["A 持5日"] for k in ("ed", "ek", "xd", "xk", "sd")))
raise SystemExit(1 if bad else 0)
