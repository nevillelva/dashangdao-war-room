#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_c8_intraday_mkt.py —— 查8 盤中版：大盤代理、事件盤中欄位、鎖死判斷與 carry、10:00 平倉規則（鎖死平不了）、樣本門檻。"""
import pandas as pd
import c8_intraday_mkt as X

bad = 0
def chk(n, c, e=""):
    global bad; print("✅" if c else "❌", n, "" if c else e); bad += (not c)

idx = pd.bdate_range("2025-01-02", periods=6)
rows = [(100, 100, 100, 100, 1)] * 2 + [(100, 106, 100, 106, 1), (106, 108, 106, 108, 1), (116, 118, 112, 113, 1), (113, 114, 112, 113, 1)]
df = pd.DataFrame(rows, columns=["Open", "High", "Low", "Close", "Volume"], index=idx)
d3 = idx[4].strftime("%Y-%m-%d")

# 大盤代理：30 檔都在 10:00 比開盤高 1% → 1.0；只有 10 檔 → 不算
per = {f"S{k}": {d3: {"open": 100.0, "p1000": 101.0, "p0930": 100.5}} for k in range(30)}
mp = X.market_proxy(per, "p1000")
chk("大盤代理＝+1%，樣本≥30", abs(mp[d3] - 1.0) < 1e-9, mp)
chk("不到 30 檔不算", X.market_proxy({f"S{k}": {d3: {"open": 100.0, "p1000": 101.0}} for k in range(10)}) == {})
m0930 = X.market_proxy(per, "p0930")

# 事件：開 116（昨收 108、漲停 118.8）；10:00 在 117.5（距漲停 1.2%）；13:20 在 113
st = {d3: {"open": 116.0, "p1000": 117.5, "p1320": 113.0, "p0930": 117.0, "hi1315": 118.0}}
ev = X.build_events("X", df, st, mp, m0930)
chk("找到 1 筆事件", len(ev) == 1, ev)
e = ev[0]
chk("13:20 回補報酬＝(116-113)/116 −0.435", abs(e["r"] - ((116 - 113) / 116 * 100 - 0.435)) < 1e-6)
chk("距漲停 (118.8−117.5)/108=1.2%", abs(e["dist10"] - 1.2037) < 1e-3, e["dist10"])
chk("13:20 沒鎖死 → carry＝帳面", e["lock"] is False and e["r_carry"] == e["r"])
chk("10:00 平倉報酬＝(116-117.5)/116 −0.435（虧損）", abs(e["r10"] - ((116 - 117.5) / 116 * 100 - 0.435)) < 1e-6)

# 鎖死：13:20 在 118.8 → carry 以隔天開盤回補
st2 = {d3: {"open": 116.0, "p1000": 118.8, "p1320": 118.8, "p0930": 118.0, "hi1315": 118.8}}
e2 = X.build_events("Y", df, st2, mp, m0930)[0]
chk("13:20 鎖死、10:00 也鎖死", e2["lock"] and e2["lock1000"], e2)
chk("carry＝(116-113)/116 −0.435（隔天開盤 113）", abs(e2["r_carry"] - ((116 - 113) / 116 * 100 - 0.435)) < 1e-6, e2["r_carry"])
# 10:00 已鎖死 → 平倉規則平不了，退回 carry
chk("10:00 鎖死時「距漲停≤1%就平倉」退回 carry", abs(X.RULES["10:00 個股距漲停≤1% 就平倉"](e2) - e2["r_carry"]) < 1e-9)
# 未鎖死時規則生效
chk("距漲停≤2% 規則在 e 上平倉（用 r10）", abs(X.RULES["10:00 個股距漲停≤2% 就平倉"](e) - e["r10"]) < 1e-9)
chk("大盤代理 +1% ≥0.7% 規則平倉", abs(X.RULES["10:00 大盤代理≥+0.7% 就平倉"](e) - e["r10"]) < 1e-9)
chk("現行規則回 carry", X.RULES["現行：持有到13:20"](e) == e["r_carry"])
# 缺價位、缺大盤 → 略過
chk("缺 p1320 略過", X.build_events("X", df, {d3: {"open": 116.0, "p1000": 117.5}}, mp, m0930) == [])
chk("沒有大盤代理略過", X.build_events("X", df, st, {}, {}) == [])
res = X.analyze(ev * 50)
chk("analyze 有規則表與分組", "現行：持有到13:20" in res["gap3"]["rules"] and "m1000" in res["gap3"]["groups"] and res["gap3"]["n"] == 50)
# 盤中價與日K 對不上 → 退回
bad_st = {d3: {"open": 60.0, "p1000": 61.0, "p1320": 60.0, "p0930": 60.5, "hi1315": 61.0}}
chk("盤中開盤與日K 開盤差太多 → 退回", X.build_events("X", df, bad_st, mp, m0930) == [])
raise SystemExit(1 if bad else 0)
