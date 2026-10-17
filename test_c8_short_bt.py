import sys
import numpy as np, pandas as pd
import c8_short_bt as S
idx = pd.bdate_range("2023-01-02", periods=200)
c = np.full(200, 100.0); c[100] = 106; c[101] = 108; c[102] = 109; c[103:] = 100
df = pd.DataFrame({"Open": c, "High": c * 1.01, "Low": c * 0.99, "Close": c, "Volume": 1000.0}, index=idx)
r = S.analyze(S.build_rows("X", df))
ok = r["new"]["n"] == 1 and r["new"]["d1"]["raw"] > 0 and r["lu"]["n"] == 0 and isinstance(r["new"]["d1"]["raw"], float)
print("PASS" if ok else "FAIL", r["new"]["n"], r["new"]["d1"])
sys.exit(0 if ok else 1)
