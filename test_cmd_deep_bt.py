import sys
import numpy as np
import pandas as pd
import cmd_deep_bt as B
import warroom_core as W
ok = True
def chk(n, c):
    global ok
    print(("PASS " if c else "FAIL ") + n)
    ok = ok and c
rng = np.random.default_rng(0)
idx = pd.bdate_range("2023-01-02", periods=400)
def mk(drift):
    c = 100 * np.cumprod(1 + drift + rng.normal(0, 0.015, len(idx)))
    o = c * (1 + rng.normal(0, 0.003, len(idx)))
    return pd.DataFrame({"Open": o, "High": np.maximum(o, c) * 1.01, "Low": np.minimum(o, c) * 0.99, "Close": c,
                         "Volume": rng.integers(1000, 5000, len(idx)).astype(float)}, index=idx)
inst = pd.DataFrame({"f_buy": rng.normal(0, 100, len(idx)), "t_buy": rng.normal(0, 50, len(idx)),
                     "margin_diff": rng.normal(0, 30, len(idx))}, index=idx.strftime("%Y-%m-%d"))
kw = dict(inst=inst, eval_fn=W.evaluate_single_condition, feat_fn=W.compute_rule_features)
rows = B.build_rows("A", mk(0.001), **kw) + B.build_rows("B", mk(-0.001), **kw)
chk("有列", len(rows) > 500)
chk("每列都有基準", all("base" in r["hits"] for r in rows))
chk("查4 原規則命中≈投信買超>0 的天數", 0 < sum(1 for r in rows if "c4" in r["hits"]) < len(rows))
chk("查4 連買3日是原規則子集", all("c4" in r["hits"] for r in rows if "c4v" in r["hits"]))
chk("查5 新規則是舊規則子集", all("c5_old" in r["hits"] for r in rows if "c5" in r["hits"]))
chk("查9 新規則是舊規則子集", all("c9_old" in r["hits"] for r in rows if "c9" in r["hits"]))
res = B.analyze(rows)
chk("基準超額≈0", abs(res["base"]["all"]["r10"]["ex"]) < 1e-6)
chk("MAE 為負或 0", res["base"]["all"]["mae10_mean"] <= 0)
chk("樣本內外切分存在", "IS" in res["c4"] and "OOS" in res["c4"])
chk("每列有標籤", all("tags" in r and "hi20" in r["tags"] for r in rows))
for r in rows[::7]:
    r["hits"].add("c32_and")
f = B.analyze_focus(rows, key="c32_and")
chk("濃縮：前1檔樣本數≤全部", f["topk"]["1"]["n"] <= f["base"]["n"])
chk("濃縮：每個標籤有有/無", all("yes" in v and "no" in v for v in f["tags"].values()))
chk("濃縮：無樣本回 n=0", B.analyze_focus(rows, key="zzz") == {"n": 0})
sys.exit(0 if ok else 1)
