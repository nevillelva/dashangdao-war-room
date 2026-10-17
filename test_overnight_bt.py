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
sys.exit(0 if ok else 1)
