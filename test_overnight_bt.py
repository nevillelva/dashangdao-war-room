"""【2026-10-17】隔日沖 5 年日K回測（overnight_bt.py）。"""
import sys
import numpy as np
import pandas as pd
import overnight_bt as B
ok = True
def chk(n, c):
    global ok
    print(("PASS: " if c else "FAIL: ") + n)
    ok = ok and c
rng = np.random.default_rng(1)
idx = pd.bdate_range("2023-01-02", periods=300)
c = 100 * np.cumprod(1 + rng.normal(0.001, 0.03, len(idx)))
o = c * (1 + rng.normal(0, 0.005, len(idx)))
df = pd.DataFrame({"Open": o, "High": np.maximum(o, c) * 1.005, "Low": np.minimum(o, c) * 0.995, "Close": c,
                   "Volume": rng.integers(1000, 5000, len(idx)).astype(float)}, index=idx)
rows = B.build_rows("A", df, mkt_bull={d: True for d in idx.strftime("%Y-%m-%d")})
chk("有列且都是漲 3~9.4%", len(rows) > 10 and all(r["gb"] in ("3~5%", "5~7%", "7~9.4%") for r in rows))
res = B.analyze(rows)
chk("全部組有淨報酬與 t 值欄位", "net" in res["全部"]["o1"] and "t_net" in res["全部"]["o1"])
chk("超額≈0（全部對自己）", abs(res["全部"]["o1"]["ex"]) < 1e-9)
chk("大盤空頭組為 0 筆", res["大盤空頭"]["n"] == 0)
# T+1 隔日沖應對
c2 = c.copy(); h2 = np.maximum(o, c) * 1.005
for j in range(30, 290, 13):
    c2[j] = c2[j - 1] * 1.099; h2[j] = c2[j]
df2 = pd.DataFrame({"Open": o, "High": np.maximum(h2, c2), "Low": np.minimum(o, c2) * 0.995, "Close": c2,
                    "Volume": rng.integers(1000, 5000, len(idx)).astype(float)}, index=idx)
rt = B.build_rows_t1("B", df2, mkt_bull={d: True for d in idx.strftime("%Y-%m-%d")},
                     dt_pct={d: 25.0 for d in idx.strftime("%Y-%m-%d")})
chk("T+1：有漲停組", any("lu" in r["grp"] for r in rt))
at = B.analyze_t1(rt)
chk("T+1：每組有各動作淨報酬", "lu" in at and "short_oc" in at["lu"] and "hold_sell_open" in at["lu"])
chk("T+1：分點≥20% 組", "lu_dt20" in at)

# 【2026-10-19】整組：T 收盤買→T+1 開盤賣，開高>1% 再加開盤放空→收盤回補
import numpy as _np
import pandas as _pd
import overnight_bt as _OB
_n = 80
_idx = _pd.bdate_range("2025-01-01", periods=_n)
_C = _np.full(_n, 100.0)
_C[60] = 110.0                       # T 日漲停收盤
_O = _C.copy()
_O[61] = 112.0                       # T+1 開高 1.8%
_Cl = _C.copy()
_Cl[61] = 109.0                      # T+1 收盤
_df = _pd.DataFrame({"Open": _O, "High": _np.maximum(_O, _Cl), "Low": _np.minimum(_O, _Cl), "Close": _Cl, "Volume": _np.full(_n, 1000.0)}, index=_idx)
_rows = _OB.build_rows_t1("X", _df, dt_pct={_idx[60].strftime("%Y-%m-%d"): 25.0})
_r = [r for r in _rows if r["date"] == _idx[60].strftime("%Y-%m-%d")][0]
_a = (112 / 110 - 1) * 100 - _OB.COST
_b = (112 - 109) / 112 * 100 - _OB.COST_DT
assert abs(_r["combo_gap"] - (_a + _b)) < 1e-6 and abs(_r["combo_dt"] - (_a + _b)) < 1e-6, "整組報酬"
_rows2 = _OB.build_rows_t1("X", _df, dt_pct={_idx[60].strftime("%Y-%m-%d"): 5.0})
_r2 = [r for r in _rows2 if r["date"] == _idx[60].strftime("%Y-%m-%d")][0]
assert abs(_r2["combo_dt"] - _a) < 1e-6, "分點<20% 不做放空段"
print("PASS: 整組（買進＋開盤賣＋開高放空）報酬與分點條件")

sys.exit(0 if ok else 1)
