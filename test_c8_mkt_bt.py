#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_c8_mkt_bt.py —— 查8×大盤回測：事件定義、報酬計算、鎖漲停回補、事前/事後分開、否決候選門檻、NaN 防呆。"""
import numpy as np
import pandas as pd
import c8_mkt_bt as M

bad = 0
def chk(n, c, e=""):
    global bad; print("✅" if c else "❌", n, "" if c else e); bad += (not c)


def frame(rows, start="2025-01-02"):
    idx = pd.bdate_range(start, periods=len(rows))
    return pd.DataFrame(rows, columns=["Open", "High", "Low", "Close", "Volume"], index=idx)


# 個股：D0=100、D1 漲 6%、D2 續漲、D3 開 +7.4%（空）、收在 -2%、D4 隨便
rows = [(100, 100, 100, 100, 1)] * 2
rows += [(100, 106, 100, 106, 1)]                        # D2 i-1：g1=6%
rows += [(106, 108, 106, 108, 1)]                        # D3 i：g2>0
rows += [(116, 116, 112, 113, 1)]                        # D4：開 116(+7.4%)，收 113
rows += [(113, 114, 112, 113, 1)]
df = frame(rows)
mk = {df.index[4].strftime("%Y-%m-%d"): {"gap": 0.8, "prev": 0.3, "oc": 1.2, "cc": 2.0, "bull": True}}
ev = M.build_events("X", df, mk)
chk("找到 1 筆查8 事件", len(ev) == 1, ev)
e = ev[0]
chk("報酬＝(116-113)/116 −0.435", abs(e["r"] - ((116 - 113) / 116 * 100 - 0.435)) < 1e-6, e)
chk("跳空 7.4%、個股相對大盤 6.6%", abs(e["gap"] - 7.4074) < 1e-2 and abs(e["rel"] - (e["gap"] - 0.8)) < 1e-9)
chk("沒鎖漲停 → carry 等於帳面", e["lock"] is False and e["r_carry"] == e["r"])

# 鎖漲停：收在最高且≥9.4%
rows2 = list(rows[:4]) + [(116, 118.8, 116, 118.8, 1), (125, 126, 124, 125, 1)]
df2 = frame(rows2)
mk2 = {df2.index[4].strftime("%Y-%m-%d"): {"gap": 0.1, "prev": 0, "oc": 0, "cc": 0, "bull": False}}
e2 = M.build_events("Y", df2, mk2)[0]
chk("鎖漲停被標記（118.8/108=+10%）", e2["lock"] and e2["touch"], e2)
chk("回補不了留到隔天開盤：(116-125)/116 −0.435", abs(e2["r_carry"] - ((116 - 125) / 116 * 100 - 0.435)) < 1e-6)
chk("carry 與帳面不同", abs(e2["r_carry"] - e2["r"]) > 1e-6)

# NaN／缺值不炸
df3 = df.copy(); df3.iloc[3, 3] = np.nan
chk("NaN 不炸", isinstance(M.build_events("Z", df3, mk), list))
chk("沒有對應大盤日就略過", M.build_events("X", df, {}) == [])

# 大盤表
n = 120
idx = pd.bdate_range("2025-01-02", periods=n)
c = 100 + np.arange(n) * 0.5
dfm = pd.DataFrame({"Open": c - 0.2, "High": c + 1, "Low": c - 1, "Close": c, "Volume": 1}, index=idx)
m = M.build_mkt(dfm)
d = idx[100].strftime("%Y-%m-%d")
chk("大盤 gap/prev/oc/cc 計算", abs(m[d]["gap"] - ((c[100] - 0.2) / c[99] - 1) * 100) < 1e-9 and abs(m[d]["oc"] - (c[100] / (c[100] - 0.2) - 1) * 100) < 1e-9)
chk("bull 用前一日（無前視）：上升趨勢為 True", m[d]["bull"] is True)

# 分析：構造「大盤開高 → 空單明顯變差」，IS／OOS 都成立 → 候選；否則不成候選
rng = np.random.default_rng(1)
evs = []
for k in range(400):
    hi = k % 2 == 0
    day = pd.Timestamp("2022-01-03") + pd.Timedelta(days=int(k) * 3)
    evs.append({"sym": "S", "date": day.strftime("%Y-%m-%d"), "gap": 4.0, "r": float(rng.normal(-1.0 if hi else 0.8, 0.3)), "r_carry": 0.0, "lock": False, "touch": False,
                "mgap": 1.2 if hi else 0.0, "mprev": 0.0, "moc": 1.0 if hi else 0.0, "mcc": 1.0 if hi else 0.0, "bull": True, "rel": 2.8 if hi else 4.0})
res = M.analyze(evs)
v = res["gap3"]["vetos"]["大盤開高≥0.5%"]
chk("大盤開高≥0.5% 被標為否決候選", v["candidate"] and v["veto"]["avg"] < v["keep"]["avg"] - 1.5, v)
chk("事後指標獨立列在 oracle_vetos", "【事後】大盤當日漲≥1%" in res["gap3"]["oracle_vetos"] and "【事後】大盤當日漲≥1%" not in res["gap3"]["vetos"])
for e in evs:
    e["r"] = float(rng.normal(0.3, 0.3))
chk("沒差異就不是候選", not M.analyze(evs)["gap3"]["vetos"]["大盤開高≥0.5%"]["candidate"])
chk("樣本太少不成候選", not M.analyze(evs[:30])["gap3"]["vetos"]["大盤開高≥0.5%"]["candidate"])
raise SystemExit(1 if bad else 0)
