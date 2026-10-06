#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
regime.py —— 「盤勢」狀態的純函式層（2026-10-06 新增；回測 backtest_regime.py 與實盤 system_scheduler 共用同一份定義）

【為什麼不直接用加權指數】回測與實盤要用「完全相同」的定義才有意義；加權指數需要另外下載、且回測母體(近千檔)與
實盤母體(近 300 檔)大小不同。這裡改用「母體自己」算出的『等權重指數＋市場寬度』，兩邊都只需要母體日K：
  ew     ＝ 母體所有個股「當日報酬的平均」累乘出來的等權重指數
  b20/b60＝ 收盤站上 MA20 / MA60 的個股比例
盤勢旗標全部只用「當日(含)以前」的資料，沒有未來函數（test_regime.py 以「改動未來資料、過去旗標不變」驗證）。
一天可以同時符合多個旗標（例如「指數>MA60」與「寬度≥50%」），所以用位元遮罩存。
"""
import numpy as np
import pandas as pd

# 旗標順序即位元位置（bit 0 起）。名稱要穩定：存進 system_config 後實盤用名稱比對。
REGIME_DEFS = [
    ("all", "不分盤勢"),
    ("up20", "指數>MA20"),
    ("up60", "指數>MA60"),
    ("up60_rise", "指數>MA60且MA60上彎"),
    ("dn20", "指數<MA20"),
    ("dn60", "指數<MA60"),
    ("b50", "寬度(站上MA20)≥50%"),
    ("b50_up60", "寬度≥50%且指數>MA60"),
    ("b40_lo", "寬度(站上MA20)≤40%"),
    ("dd8", "指數距120日高≤8%(非修正)"),
    ("dd10_deep", "指數距120日高≥10%(修正中)"),
    ("calm", "波動低於近一年中位"),
    ("wild", "波動高於近一年中位"),
    ("shock5", "指數5日跌≥3%(急殺後)"),
]
REGIME_NAMES = [k for k, _ in REGIME_DEFS]
REGIME_LABEL = dict(REGIME_DEFS)
REGIME_BIT = {k: i for i, k in enumerate(REGIME_NAMES)}
MIN_BREADTH_STOCKS = 20          # 母體有效檔數太少時，寬度沒意義


def regime_frame(prices):
    """prices: {symbol: DataFrame(Open/High/Low/Close/Volume, DatetimeIndex)}。
    回傳 DataFrame(index=日期)：ew, ma20, ma60, ma60_prev10, b20, b60, dd120, vol20, vol_med250, mom5, n。純函式。"""
    closes = pd.DataFrame({s: df["Close"] for s, df in prices.items()}).sort_index()
    ret = closes.pct_change(fill_method=None)
    n_ret = ret.notna().sum(axis=1)
    ew_ret = ret.mean(axis=1).where(n_ret >= MIN_BREADTH_STOCKS).fillna(0.0)
    ew = (1.0 + ew_ret).cumprod()
    ma20, ma60 = ew.rolling(20).mean(), ew.rolling(60).mean()
    m20s, m60s = closes.rolling(20).mean(), closes.rolling(60).mean()
    ok20, ok60 = m20s.notna(), m60s.notna()
    b20 = ((closes > m20s) & ok20).sum(axis=1) / ok20.sum(axis=1).replace(0, np.nan)
    b60 = ((closes > m60s) & ok60).sum(axis=1) / ok60.sum(axis=1).replace(0, np.nan)
    vol20 = ew_ret.rolling(20).std() * np.sqrt(252)
    out = pd.DataFrame({
        "ew": ew, "ma20": ma20, "ma60": ma60, "ma60_prev10": ma60.shift(10),
        "b20": b20, "b60": b60,
        "dd120": ew / ew.rolling(120, min_periods=60).max() - 1.0,
        "vol20": vol20, "vol_med250": vol20.rolling(250, min_periods=120).median(),
        "mom5": ew / ew.shift(5) - 1.0, "n": n_ret})
    return out


def regime_flags(F):
    """F: regime_frame 的輸出 → DataFrame(bool, columns=REGIME_NAMES)。資料不足(NaN)的一律 False（包含 all 之外的所有旗標）。"""
    f = pd.DataFrame(index=F.index)
    f["all"] = True
    f["up20"] = F["ew"] > F["ma20"]
    f["up60"] = F["ew"] > F["ma60"]
    f["up60_rise"] = (F["ew"] > F["ma60"]) & (F["ma60"] > F["ma60_prev10"])
    f["dn20"] = F["ew"] < F["ma20"]
    f["dn60"] = F["ew"] < F["ma60"]
    f["b50"] = F["b20"] >= 0.5
    f["b50_up60"] = (F["b20"] >= 0.5) & (F["ew"] > F["ma60"])
    f["b40_lo"] = F["b20"] <= 0.4
    f["dd8"] = F["dd120"] >= -0.08
    f["dd10_deep"] = F["dd120"] <= -0.10
    f["calm"] = F["vol20"] < F["vol_med250"]
    f["wild"] = F["vol20"] >= F["vol_med250"]
    f["shock5"] = F["mom5"] <= -0.03
    f = f.fillna(False).astype(bool)
    f.loc[F["n"] < MIN_BREADTH_STOCKS, [c for c in f.columns if c != "all"]] = False
    return f[REGIME_NAMES]


def flags_to_bits(flags):
    """DataFrame(bool) → Series(uint32 位元遮罩，index 同)。"""
    bits = np.zeros(len(flags), dtype=np.uint32)
    for k, i in REGIME_BIT.items():
        bits |= (flags[k].values.astype(np.uint32) << np.uint32(i))
    return pd.Series(bits, index=flags.index)


def bit_mask(bits, name):
    """bits(ndarray uint32) → 是否有 name 旗標。"""
    return (np.asarray(bits, dtype=np.uint32) >> np.uint32(REGIME_BIT[name])) & np.uint32(1) == 1


def today_flags(prices, asof=None):
    """實盤用：回傳 (asof 日期字串, {旗標名: bool}, 說明用數值)。asof 為 None 取最後一個交易日。"""
    F = regime_frame(prices)
    fl = regime_flags(F)
    if asof is not None:
        fl = fl[fl.index <= pd.Timestamp(asof)]
        F = F[F.index <= pd.Timestamp(asof)]
    if len(fl) == 0:
        return None, {}, {}
    d = fl.index[-1]
    row = F.loc[d]
    info = {"ew_vs_ma20_pct": round(float(row["ew"] / row["ma20"] - 1) * 100, 2) if pd.notna(row["ma20"]) else None,
            "ew_vs_ma60_pct": round(float(row["ew"] / row["ma60"] - 1) * 100, 2) if pd.notna(row["ma60"]) else None,
            "breadth20_pct": round(float(row["b20"]) * 100, 1) if pd.notna(row["b20"]) else None,
            "dd120_pct": round(float(row["dd120"]) * 100, 2) if pd.notna(row["dd120"]) else None,
            "n_stocks": int(row["n"])}
    return str(pd.Timestamp(d).date()), {k: bool(fl.loc[d, k]) for k in REGIME_NAMES}, info
