"""分點按需求回補離線測試（合成日K、假抓取函式、暫存快取目錄；不連網、不碰資料庫）。"""
import io
import os
import sys
import tempfile
from contextlib import redirect_stdout

import numpy as np
import pandas as pd

import bt_cache as BC
import broker_event_backfill as BE

fails = []


def check(name, cond):
    print(("PASS: " if cond else "FAIL: ") + name)
    if not cond:
        fails.append(name)


def synth(n=130, limit_at=100, surge_at=110):
    rng = np.random.default_rng(1)
    c = 100 + rng.uniform(-1, 1, n)
    o = c.copy()
    h = c * 1.005
    v = np.full(n, 1000.0)
    c[limit_at] = c[limit_at - 1] * 1.10
    h[limit_at] = c[limit_at]
    o[limit_at] = c[limit_at - 1]
    c[surge_at] = max(c[surge_at - 60:surge_at].max(), c[:surge_at].max()) * 1.05
    o[surge_at] = c[surge_at - 1]
    v[surge_at] = 5000.0
    idx = pd.bdate_range("2026-01-01", periods=n)
    return pd.DataFrame({"Open": o, "High": np.maximum(h, np.maximum(o, c)), "Low": np.minimum(o, c) * 0.995, "Close": c, "Volume": v}, index=idx)


df = synth()
ds = list(df.index.strftime("%Y-%m-%d"))
ev = BE.find_events(df)
check("偵測到漲停事件", "limit_up" in ev.get(ds[100], []))
check("偵測到爆量突破事件", "surge_breakout" in ev.get(ds[110], []))
check("可只挑一種", set(k for v in BE.find_events(df, ("limit_up",)).values() for k in v) == {"limit_up"})
check("資料太少回空", BE.find_events(df.iloc[:10]) == {})

# wave_start：與 wave_screen 測試同型的合成波段（150 日潛伏→爆量突破→漲到 3 倍→回落 35%）
def synth_wave():
    rng = np.random.default_rng(3)
    c = list(100 + rng.uniform(-4, 4, 150))
    v = [1000.0] * 150
    s_px = max(c[-60:]) * 1.05
    c.append(s_px); v.append(6000.0)
    up = np.linspace(s_px, s_px * 3.0, 200)
    c += list(up); v += [1500.0] * 200
    c += list(np.linspace(up[-1], up[-1] * 0.65, 60)); v += [1200.0] * 60
    c = np.array(c)
    o = np.r_[c[0], c[:-1]]
    o[150] = c[149]
    idx = pd.bdate_range("2024-01-01", periods=len(c))
    return pd.DataFrame({"Open": o, "High": c * 1.01, "Low": c * 0.99, "Close": c, "Volume": np.array(v)}, index=idx)


dw = synth_wave()
dsw = list(dw.index.strftime("%Y-%m-%d"))
evw = BE.find_events(dw)
check("wave_start 對應 wave_screen 的起漲日", "wave_start" in evw.get(dsw[150], []))

# window_dates / plan_fetch
check("window_dates 取前後各 1 日", BE.window_dates(ds, ds[100]) == [ds[99], ds[100], ds[101]])
check("window_dates 事件日不在序列 → 空", BE.window_dates(ds, "1999-01-01") == [])
check("window_dates 邊界不越界", BE.window_dates(ds, ds[0], 1, 1) == [ds[0], ds[1]])
plan = BE.plan_fetch({"2330": {ds[100]: ["limit_up"], ds[101]: ["surge_breakout"]}}, {"2330": ds})
check("plan 同日合併種類且新到舊", plan[0][1] > plan[-1][1] and dict(((s, d), k) for s, d, k in plan)[("2330", ds[101])] == ["limit_up", "surge_breakout"])
check("plan since 過濾", BE.plan_fetch({"2330": {ds[100]: ["limit_up"]}}, {"2330": ds}, since=ds[105]) == [])


class FakeFetch:
    def __init__(self, data): self.data, self.calls = data, []
    def __call__(self, sym, day):
        self.calls.append((sym, day))
        return self.data.get((sym, day))


ROW = [{"b": "某分點", "buy": 10, "sell": 0, "net": 10, "pct": 1.0}]
with tempfile.TemporaryDirectory() as td:
    BC.DIR = td
    plan = [("2330", "2026-05-12", ["limit_up"]), ("2330", "2026-05-11", ["limit_up"]), ("2330", "2026-05-08", ["limit_up"])]
    ff = FakeFetch({("2330", "2026-05-12"): ROW, ("2330", "2026-05-11"): ROW})
    r = BE.backfill(plan, ff, BC, budget_s=999)
    check("抓到 2、無資料 1", (r["fetched"], r["empty"], r["skipped"], r["left"]) == (2, 1, 0, 0))
    check("寫進回測快取 dj_day／dj_empty（與 dt_broker_backfill 同鍵）",
          os.path.exists(BC._p("dj_day", "2330_2026-05-12")) and os.path.exists(BC._p("dj_empty", "2330_2026-05-08")))
    check("索引記錄為何抓", BC._read(BC._p("dj_event_idx", "2330_2026-05-12")) == {"kinds": ["limit_up"]})
    ff2 = FakeFetch({})
    r2 = BE.backfill(plan, ff2, BC, budget_s=999)
    check("續跑不重抓", ff2.calls == [] and r2["skipped"] == 3)
    # 已有快取但事件種類新增 → 只更新索引
    BE.backfill([("2330", "2026-05-12", ["surge_breakout"])], ff2, BC, budget_s=999)
    check("索引合併新種類", BC._read(BC._p("dj_event_idx", "2330_2026-05-12")) == {"kinds": ["limit_up", "surge_breakout"]})

with tempfile.TemporaryDirectory() as td:
    BC.DIR = td
    plan = [("2330", f"2026-05-{d:02d}", ["limit_up"]) for d in range(20, 10, -1)]
    t = [0.0]
    def clock():
        t[0] += 10
        return t[0]
    ff = FakeFetch({(s, d): ROW for s, d, _ in plan})
    r = BE.backfill(plan, ff, BC, budget_s=45, clock=clock)
    check("預算用完就停並回報剩餘", r["stopped"] == "budget" and r["left"] > 0 and r["fetched"] + r["left"] == len(plan))

with tempfile.TemporaryDirectory() as td:
    BC.DIR = td
    st = [0]
    def fetch_fail(sym, day):
        st[0] += 1
        return None
    r = BE.backfill([("2330", f"2026-05-{d:02d}", ["limit_up"]) for d in range(20, 10, -1)], fetch_fail, BC, budget_s=999, fail_streak=lambda: st[0])
    check("DJ 連續失敗 3 次：停止，第 3 筆不寫成 dj_empty（前 2 筆沿用原階段行為）",
          r["stopped"] == "dj_fail_streak" and r["empty"] == 2 and r["left"] == 8
          and not os.path.exists(BC._p("dj_empty", "2330_2026-05-18")))

# run_on_demand 端到端（只回數量；不寫資料庫——模組根本沒有 DB 參數）
with tempfile.TemporaryDirectory() as td:
    BC.DIR = td
    ff = FakeFetch({})
    buf = io.StringIO()
    with redirect_stdout(buf):
        counts, plan = BE.run_on_demand(["2330", "2317"], lambda s: df if s == "2330" else None, ff, BC, kinds=("limit_up",))
    # 合成資料第 100 日漲停；第 110 日爆量突破同時也漲停（+15%）→ 2 個事件、各前後 1 日 = 6 天
    check("端到端：1 檔有資料、2 個漲停事件、計畫 6 天", counts["symbols"] == 1 and counts["events"] == 2 and counts["plan_days"] == 6)
    check("日誌只印數量", "2330" not in buf.getvalue() and "plan_days 6" in buf.getvalue())
    try:
        BE.run_on_demand(["2330"], lambda s: df, ff, BC, kinds=("bogus",))
        check("未知 kinds 要丟錯", False)
    except ValueError:
        check("未知 kinds 要丟錯", True)

# rows_from_df：日期不符視為無資料
d1 = pd.DataFrame([{"broker_name": "甲", "buy_shares": 5, "sell_shares": 1, "net_shares": 4, "pct_of_volume": 2.5}])
d1.attrs["data_date"] = "2026-05-12"
check("rows_from_df 正常轉換", BE.rows_from_df(d1, "2026-05-12") == [{"b": "甲", "buy": 5, "sell": 1, "net": 4, "pct": 2.5}])
check("rows_from_df 日期不符 → None", BE.rows_from_df(d1, "2026-05-13") is None)
check("rows_from_df 空 → None", BE.rows_from_df(pd.DataFrame(), "x") is None and BE.rows_from_df(None, "x") is None)

sys.exit(1 if fails else 0)
