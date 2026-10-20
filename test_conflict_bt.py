import numpy as np, pandas as pd
import conflict_bt as C

def _df(n=140, seed=1):
    r = np.random.default_rng(seed)
    c = 50 * np.cumprod(1 + r.normal(0.001, 0.02, n))
    o = c * (1 + r.normal(0, 0.005, n)); h = np.maximum(o, c) * 1.01; l = np.minimum(o, c) * 0.99
    return pd.DataFrame({"Open": o, "High": h, "Low": l, "Close": c, "Volume": r.integers(1000, 5000, n)},
                        index=pd.bdate_range("2025-01-01", periods=n))

def test_build_and_analyze():
    rows = []
    for s in range(30):
        d = _df(seed=s)
        d.iloc[60:63, d.columns.get_loc("Close")] *= [1.06, 1.12, 1.2]   # 製造訊號
        rows += C.build_rows(f"s{s}", d)
    res = C.analyze(rows, min_n=1)
    assert set(res) == {"A", "A_s3", "B", "C"} and "all" in res["A"]

def test_kd_range():
    d = _df()
    k = C._kd(d["High"].to_numpy(), d["Low"].to_numpy(), d["Close"].to_numpy())
    assert np.nanmax(k) <= 100 and np.nanmin(k) >= 0
