"""【2026-10-14】回測資料集打包／解包測試。"""
import sys
import numpy as np
import pandas as pd
import bt_dataset_io as io_
from bt_lab_testdata import mk_panel

fails = []


def check(name, cond):
    print(("PASS: " if cond else "FAIL: ") + name)
    if not cond:
        fails.append(name)


P = mk_panel(n_sym=8, n_days=120, effect=0.0, seed=2).rename(columns={"score": "score_old"})
P = P[~((P["sym"] == "S001") & (P["date"] < "2021-02-15"))]   # 有些股票晚上市
meta, payload = io_.pack(P)
Q = io_.unpack(payload)
check("筆數一致", len(Q) == len(P))
a = P.sort_values(["sym", "date"]).reset_index(drop=True)
b = Q.sort_values(["sym", "date"]).reset_index(drop=True)
check("代號與日期一致", (a["sym"].to_numpy() == b["sym"].to_numpy()).all() and (a["date"].to_numpy() == b["date"].to_numpy()).all())
check("價格與分數誤差在 float32 內", np.allclose(a["close"], b["close"], rtol=1e-6) and np.allclose(a["score_old"], b["score"], rtol=1e-6))
check("meta 記錄範圍", meta["n_sym"] == 8 and meta["first"] <= meta["last"])
parts = io_.split_chunks(payload, 1000)
check("分段後可無損接回", "".join(parts) == payload)


class R:
    def __init__(self, d): self.data = d


class SB:
    def __init__(self): self.t = []; self._f = None
    def table(self, n): self._n = n; return self
    def delete(self): self._op = "del"; return self
    def eq(self, k, v):
        if getattr(self, "_op", "") == "del": self.t = [r for r in self.t if r["set_name"] != v]
        self._f = v; return self
    def insert(self, r): self.t.append(r); return self
    def select(self, *_): self._op = "sel"; return self
    def order(self, *_): return self
    def execute(self):
        if getattr(self, "_op", "") == "sel":
            return R(sorted([r for r in self.t if r["set_name"] == self._f], key=lambda r: r["part"]))
        return R([])


sb = SB()
io_.CHUNK = 1000
info = io_.save(sb, P, extra_meta={"source": "test"})
m2, Q2 = io_.load(sb)
check("存檔→讀回一致", len(Q2) == len(P) and m2["source"] == "test" and info["parts"] > 1)
sb2 = SB()
check("沒有資料集 → (None, None)", io_.load(sb2) == (None, None))
if fails:
    print(f"\n{len(fails)} 項失敗"); sys.exit(1)
print("\n全部通過")
