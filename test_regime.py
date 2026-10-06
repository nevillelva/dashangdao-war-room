#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_regime.py —— 盤勢旗標（regime.py）：定義正確、沒有未來函數、位元遮罩可逆。離線、不需網路。"""
import os
os.environ.setdefault("BT_LIQ", "0")
import numpy as np
import pandas as pd

import backtest_rules as br
import regime as rg

bad = 0


def check(ok, msg):
    global bad
    print("✅" if ok else "❌", msg)
    bad += (not ok)


px = br.synthetic_prices(n=40, days=700, seed=3)
F = rg.regime_frame(px)
fl = rg.regime_flags(F)
check(list(fl.columns) == rg.REGIME_NAMES, "旗標欄位順序＝REGIME_NAMES")
check(fl["all"].all(), "all 旗標恆為 True")
check(fl.dtypes.eq(bool).all(), "旗標皆為 bool")
valid = F["ma60"].notna() & F["b20"].notna()
check(bool((~(fl["up60"] & fl["dn60"])).all()), "指數>MA60 與 指數<MA60 不會同時成立")
check(bool((~(fl["b50"] & fl["b40_lo"])).all()), "寬度≥50% 與 寬度≤40% 不會同時成立")
check(bool((~(fl["calm"] & fl["wild"])).all()), "低波動與高波動不會同時成立")
check(bool(fl.iloc[:59][[c for c in fl.columns if c in ("up60", "dn60", "up60_rise", "b50_up60")]].eq(False).all().all()),
      "MA60 還算不出來的前 59 天，相關旗標一律 False（不亂猜）")

# 沒有未來函數：把『某日之後』的資料全部改掉，那天（含）以前的旗標不可變
cut = F.index[450]
px2 = {}
for s, df in px.items():
    d2 = df.copy()
    m = d2.index > cut
    d2.loc[m, ["Open", "High", "Low", "Close"]] = d2.loc[m, ["Open", "High", "Low", "Close"]] * 3.0
    px2[s] = d2
fl2 = rg.regime_flags(rg.regime_frame(px2))
same = (fl.loc[:cut] == fl2.loc[:cut]).all().all()
check(bool(same), "改掉未來資料後，過去的旗標完全不變（無未來函數）")
check(not bool((fl.loc[cut:] == fl2.loc[cut:]).all().all()), "（對照）未來旗標確實會跟著改變，不是測試寫壞")

# 截短資料（只給前 450 天）算出的旗標＝完整資料算出的前 450 天
px3 = {s: df.iloc[:451] for s, df in px.items()}
fl3 = rg.regime_flags(rg.regime_frame(px3))
check(bool((fl.loc[fl3.index] == fl3).all().all()), "只給前 451 天算出的旗標＝完整資料的前 451 天")

# 位元遮罩可逆
bits = rg.flags_to_bits(fl)
back = pd.DataFrame({k: rg.bit_mask(bits.values, k) for k in rg.REGIME_NAMES}, index=fl.index)
check(bool((back == fl).all().all()), "旗標→位元遮罩→旗標 可逆")
check(len(rg.REGIME_NAMES) <= 32, "旗標數 ≤ 32（uint32 夠放）")

# today_flags
d, flags, info = rg.today_flags(px)
check(d == str(F.index[-1].date()) and set(flags) == set(rg.REGIME_NAMES), "today_flags 回傳最後交易日與全部旗標")
d_, flags_, _ = rg.today_flags(px, asof=str(cut.date()))
check(d_ == str(cut.date()) and flags_ == {k: bool(fl.loc[cut, k]) for k in rg.REGIME_NAMES}, "today_flags(asof) 與逐日表一致")

# 母體檔數太少時，除 all 以外一律 False
few = {s: df for s, df in list(px.items())[:5]}
flf = rg.regime_flags(rg.regime_frame(few))
check(bool(flf["all"].all()) and not bool(flf.drop(columns="all").any().any()), "母體 <20 檔：只剩 all=True，其餘旗標全 False")

print(f"\n{'全部通過' if not bad else str(bad) + ' 項失敗'}")
raise SystemExit(1 if bad else 0)
