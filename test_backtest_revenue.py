"""backtest_revenue 單元測試（純函式＋合成資料，不連網）。python3 test_backtest_revenue.py"""
import numpy as np
import pandas as pd
import backtest_revenue as bv

FAIL = []


def check(name, cond, extra=""):
    if not cond:
        FAIL.append(name)
        print("❌", name, extra)
    else:
        print("✅", name)


check("訊號日＝次月11日", bv.announce_signal_date(2026, 9) == "2026-10-11" and bv.announce_signal_date(2025, 12) == "2026-01-11")
rows = [(2024, m, 100.0) for m in range(1, 13)] + [(2025, m, 100.0 * (1.5 if m >= 10 else 1.0)) for m in range(1, 13)]
f = {(x["year"], x["month"]): x for x in bv.revenue_features(rows)}
check("沒有去年同月就略過", (2024, 5) not in f)
check("yoy 計算", abs(f[(2025, 10)]["yoy"] - 0.5) < 1e-9 and abs(f[(2025, 5)]["yoy"]) < 1e-9)
check("月增 mom", abs(f[(2025, 10)]["mom"] - 0.5) < 1e-9 and abs(f[(2025, 11)]["mom"]) < 1e-9)
check("3月累計 yoy", abs(f[(2025, 12)]["yoy3"] - 0.5) < 1e-9 and abs(f[(2025, 10)]["yoy3"] - (350 / 300 - 1)) < 1e-9, (f[(2025, 12)]["yoy3"], f[(2025, 10)]["yoy3"]))
check("加速：10月 yoy 0.5 vs 前3月平均 0 → +0.5", abs(f[(2025, 10)]["accel"] - 0.5) < 1e-9)
check("創新高：10月是、12月持平不是、營收不變的8月不是", f[(2025, 10)]["new_high"] and not f[(2025, 12)]["new_high"] and not f[(2025, 8)]["new_high"])
check("重複列取最後一筆、壞值略過", len(bv.revenue_features([(2024, 1, 100), (2024, 1, 0), (2025, 1, None), (2025, 1, "x")])) == 0)

# 出場模擬：進場後一路漲 → 停利；一路跌 → 停損；跳空越過停損以開盤價
n = 60
idx = pd.bdate_range("2025-01-01", periods=n)
up = pd.DataFrame({"Open": np.full(n, 100.0), "High": np.full(n, 100.0), "Low": np.full(n, 100.0), "Close": np.full(n, 100.0)}, index=idx)
up.loc[idx[6:], ["High", "Close", "Open", "Low"]] = 130.0           # 進場後第 5 天起大漲
r = bv.simulate_exits(up, [0], exits=[(0.12, 0.15, 20)], max_hold=20)[(0.12, 0.15, 20)][0]
check("跳空越過停利以開盤價成交（≈+30% 扣成本）", 0.29 < r < 0.30, r)
dn = up.copy()
dn.loc[idx[6:], ["High", "Close", "Open", "Low"]] = 70.0
r = bv.simulate_exits(dn, [0], exits=[(0.12, 0.15, 20)], max_hold=20)[(0.12, 0.15, 20)][0]
check("跳空越過停損以開盤價成交（≈−30% 扣成本）", -0.31 < r < -0.30, r)
flat = up.copy(); flat[["Open", "High", "Low", "Close"]] = 100.0
r = bv.simulate_exits(flat, [0], exits=[(0.12, 0.15, 20)], max_hold=20)[(0.12, 0.15, 20)][0]
check("沒觸發持有到期＝只扣成本", abs(r + 0.00585) < 1e-9, r)

# 單格統計與通過判定（每期 240 筆：訊號 200 筆勝率 75%；非訊號 40 筆）
R = np.tile(np.array([0.1] * 150 + [-0.05] * 50 + [-0.05] * 40, dtype=float), 2)
dates = np.array([np.datetime64("2023-06-01")] * 240 + [np.datetime64("2025-06-01")] * 240)
sig = np.tile(np.array([True] * 200 + [False] * 40), 2)
SP = np.datetime64("2024-06-01")
ev = bv.evaluate_family(sig, dates, R, SP, (0.12, 0.15, 20))
check("訊號勝率高、贏過非訊號、各年穩定 → 通過", ev["passed"], ev)
check("沒有非訊號對照（全是訊號）→ 不通過", not bv.evaluate_family(np.ones(480, bool), dates, R, SP, (0.12, 0.15, 20))["passed"])
R_same = np.tile(np.array([0.1] * 150 + [-0.05] * 50 + [0.1] * 40, dtype=float), 2)      # 非訊號全贏 → 訊號沒有贏過同日其他股票
check("沒贏過非訊號 → 不通過（扣掉大盤漲跌）", not bv.evaluate_family(sig, dates, R_same, SP, (0.12, 0.15, 20))["passed"])
check("樣本不足 → 不通過", not bv.evaluate_family(np.array([True] * 20 + [False] * 460), dates, R, SP, (0.12, 0.15, 20))["passed"])
check("cell 空集合", bv.cell([])["n"] == 0)

# 合成資料：價格與營收無關，不該有家族通過
prices, rev = bv.synthetic(n=25, seed=5)
rep = bv.run_backtest(prices, rev, null_reps=20)
check("合成資料能跑完並有事件", rep.get("n_events", 0) > 100, rep.get("error"))
check("無關聯資料不會有家族通過實盤出場（防假陽性）", not any(v.get("live_exit", {}).get("passed") for v in rep["families"].values()),
      {k: v.get("live_exit", {}).get("passed") for k, v in rep["families"].items()})
check("公開摘要不含代號", "2330" not in bv.public_summary(rep) and "細節存私有表" in bv.public_summary(rep))
# FinMind 取得：第一組額度用完 → 換第二組重試同一檔；失敗原因進 diag（不含 token）
import requests as _rq


class _R:
    def __init__(self, c, j): self.status_code, self._j = c, j
    def json(self): return self._j


_calls = []
def _fake(url, params=None, timeout=0):
    _calls.append(params.get("token"))
    if params.get("token") == "A":
        return _R(200, {"msg": "Your level is free, request limit"})
    return _R(200, {"msg": "success", "data": [{"revenue_year": 2024, "revenue_month": 1, "revenue": 5}]})
_orig = _rq.get
_rq.get = _fake
_d = {}
_out = bv.fetch_revenue(["2330", "2317"], "A,B", sleep=0, diag=_d)
_rq.get = _orig
check("額度用完自動換下一組 token 並重試同一檔", set(_out) == {"2330", "2317"} and _calls == ["A", "B", "B"], _calls)

print("\n結果：", "全部通過" if not FAIL else f"失敗 {len(FAIL)} 項：{FAIL}")
raise SystemExit(1 if FAIL else 0)
