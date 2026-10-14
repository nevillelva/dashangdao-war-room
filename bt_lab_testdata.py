"""回測引擎測試用的合成資料（隨機漫步＋可植入已知效果）。"""
import numpy as np
import pandas as pd


def mk_panel(n_sym=60, n_days=500, effect=0.0, seed=1, p_sig=0.03, hold_eff=15):
    """隨機漫步；分數高的第一天之後 hold_eff 日每日額外漂移 effect。"""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2021-01-04", periods=n_days)
    rows = []
    for s in range(n_sym):
        score = np.zeros(n_days)
        drift = np.zeros(n_days)
        t = 5
        while t < n_days - hold_eff - 2:
            if rng.random() < p_sig:
                score[t:t + 3] = 7
                drift[t + 1:t + 1 + hold_eff] = effect
                t += hold_eff + 5
            else:
                t += 1
        ret = rng.normal(0, 0.015, n_days) + drift
        c = 100 * np.cumprod(1 + ret)
        o = np.concatenate([[100], c[:-1]]) * (1 + rng.normal(0, 0.002, n_days))
        h = np.maximum(o, c) * 1.005
        l = np.minimum(o, c) * 0.995
        for i in range(n_days):
            rows.append((dates[i], f"S{s:03d}", o[i], h[i], l[i], c[i], score[i]))
    return pd.DataFrame(rows, columns=["date", "sym", "open", "high", "low", "close", "score"])
