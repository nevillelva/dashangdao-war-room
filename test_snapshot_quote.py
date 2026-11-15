#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_snapshot_quote.py —— 驗證持倉市值快照的報價備援鏈（不需網路/套件）。

用 ast 從 system_scheduler.py 抽出 _snapshot_price_fallback 與 stage_portfolio_value_snapshot，
塞入假的 Supabase（支援 eq/in_/gte/lte/not_.is_/order/range/upsert）與假的報價函式執行。
驗證：
  1. 即時報價全有 → 照舊寫入，calc_method=v2，來源全部 live
  2. 即時全缺、收盤備援全有 → 覆蓋率 100%，寫入，calc_method=v2+fallback，不把缺價當 0
  3. 即時全缺、收盤備援只有一半 → 覆蓋率 < 80%，維持原本「不寫入」行為
  4. 部分缺價且無備援 → 覆蓋率正確，缺價的持倉不進未實現、不當 0
  5. 來源標記：同日收盤優先於同日快取；上一交易日收盤標示價格日期
  6. 備援查詢失敗 → 不拋例外、缺價持倉不估值，覆蓋率門檻照常生效
  7. 空股價(0 或 None)不被當成有價格
"""
import ast
import types
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Taipei")
SNAP_DATE = "2026-11-13"
SRC = open("system_scheduler.py", encoding="utf-8").read()
TREE = ast.parse(SRC)
FNS = [n for n in TREE.body if isinstance(n, ast.FunctionDef)
       and n.name in ("_snapshot_price_fallback", "stage_portfolio_value_snapshot")]
assert len(FNS) == 2, "找不到目標函式"


class Fail(Exception):
    pass


class Res:
    def __init__(self, data):
        self.data = data


class Q:
    """極簡的 PostgREST 查詢替身：只支援本測試用到的條件。"""

    def __init__(self, db, table):
        self.db, self.table = db, table
        self.filters, self._range, self.payload = [], None, None
        self.not_ = _Not(self)

    def select(self, _cols):
        return self

    def eq(self, c, v):
        self.filters.append(lambda r: r.get(c) == v)
        return self

    def in_(self, c, vs):
        vs = set(vs)
        self.filters.append(lambda r: r.get(c) in vs)
        return self

    def gte(self, c, v):
        self.filters.append(lambda r: r.get(c) is not None and str(r.get(c)) >= str(v))
        return self

    def lte(self, c, v):
        self.filters.append(lambda r: r.get(c) is not None and str(r.get(c)) <= str(v))
        return self

    def order(self, _c, desc=False):
        return self

    def range(self, a, b):
        self._range = (a, b)
        return self

    def upsert(self, payload, on_conflict=None):
        self.payload = payload
        self.db.writes.append((self.table, payload))
        return self

    def execute(self):
        if self.db.fail_tables and self.table in self.db.fail_tables:
            raise RuntimeError(f"模擬 {self.table} 查詢失敗")
        if self.payload is not None:
            return Res([self.payload])
        rows = [r for r in self.db.tables.get(self.table, []) if all(f(r) for f in self.filters)]
        if self._range:
            rows = rows[self._range[0]:self._range[1] + 1]
        return Res(rows)


class _Not:
    def __init__(self, q):
        self.q = q

    def is_(self, c, v):
        if v == "null":
            self.q.filters.append(lambda r: r.get(c) is not None)
        return self.q


class FakeSB:
    def __init__(self, tables, fail_tables=()):
        self.tables = tables
        self.fail_tables = set(fail_tables)
        self.writes = []

    def table(self, name):
        return Q(self, name)


def make_ns(sb, live_map):
    logs = []

    def _log(_sb, stage, run_date, picked=0, executed=0, gate="normal", note=""):
        logs.append({"stage": stage, "picked": picked, "executed": executed, "gate": gate, "note": note})

    fake_wc = types.SimpleNamespace(sb_fetch_all=lambda mk, **_kw: mk().execute().data)
    ns = {
        "datetime": datetime, "timedelta": timedelta, "TAIPEI_TZ": TZ,
        "os": types.SimpleNamespace(environ={}),
        "_wc": fake_wc,
        "_snapshot_trade_date": lambda now=None: (SNAP_DATE, "收盤後"),
        "fetch_taiwan_stock_info_raw": lambda: [{"stock_id": "2330", "type": "twse"}],
        "fetch_live_quotes_resilient": lambda pairs, **_kw: (dict(live_map), {}),
        "_log_stage_run": _log,
    }
    exec(compile(ast.Module(FNS, []), "snap_quote", "exec"), ns)
    return ns, logs


# ----- 測試資料：6 檔持倉（1 檔做空） -----
HOLD = [
    {"symbol": "2330", "entry_price": 1000.0, "side": "long", "shares": 2},
    {"symbol": "2317", "entry_price": 100.0, "side": "long", "shares": 1},
    {"symbol": "2454", "entry_price": 1200.0, "side": "long", "shares": 1},
    {"symbol": "2603", "entry_price": 200.0, "side": "long", "shares": 3},
    {"symbol": "2609", "entry_price": 50.0, "side": "short", "shares": 2},
    {"symbol": "3008", "entry_price": 3000.0, "side": "long", "shares": 1},
]
CLOSE_TODAY = {"2330": 1100.0, "2317": 105.0, "2454": 1100.0, "2603": 210.0, "2609": 40.0, "3008": 3100.0}


def base_tables(hold=HOLD, mkt=(), lc=()):
    return {
        "system_portfolio": [dict(h, status="holding") for h in hold]
        + [{"status": "closed", "exit_date": "2026-10-01", "realized_roi": 1.5, "realized_pnl": 10000}],
        "twse_market_snapshot": [dict(r) for r in mkt],
        "live_quote_cache": [dict(r) for r in lc],
        "portfolio_value_snapshot": [],
    }


def mkt_rows(prices, date=SNAP_DATE):
    return [{"symbol": s, "trade_date": date, "close_price": p} for s, p in prices.items()]


def run(tables, live_map, fail_tables=()):
    sb = FakeSB(tables, fail_tables)
    ns, logs = make_ns(sb, live_map)
    ns["stage_portfolio_value_snapshot"](sb)
    return sb, logs


results = []


def check(cond, msg):
    results.append(bool(cond))
    print(("✅" if cond else "❌"), msg)


# 1. 即時報價全有
live_all = {s: {"price": p} for s, p in CLOSE_TODAY.items()}
sb, logs = run(base_tables(mkt=mkt_rows(CLOSE_TODAY)), live_all)
row = sb.writes[0][1] if sb.writes else {}
check(len(sb.writes) == 1, "1a 即時全有 → 寫入一筆快照")
check(row.get("calc_method") == "v2" and row.get("quote_coverage") == 1.0,
      f"1b 即時全有 → calc_method=v2、覆蓋率 1.0（實際 {row.get('calc_method')}／{row.get('quote_coverage')}）")
exp_unreal = sum((-1 if h["side"] == "short" else 1) * (CLOSE_TODAY[h["symbol"]] - h["entry_price"])
                 * h["shares"] * 1000 for h in HOLD)
check(abs(row.get("unrealized_pnl", 0) - round(exp_unreal, 0)) < 1,
      f"1c 未實現損益正確（期望 {round(exp_unreal)}，實際 {row.get('unrealized_pnl')}）")

# 2. 即時全缺、收盤備援全有（同日收盤）
sb, logs = run(base_tables(mkt=mkt_rows(CLOSE_TODAY)), {})
row = sb.writes[0][1] if sb.writes else {}
check(len(sb.writes) == 1 and row.get("quote_coverage") == 1.0,
      f"2a 即時全缺但同日收盤全有 → 寫入，覆蓋率 1.0（寫入數 {len(sb.writes)}）")
check(row.get("calc_method") == "v2+fallback", f"2b 有備援來源 → calc_method=v2+fallback（實際 {row.get('calc_method')}）")
check("當日收盤 6" in logs[-1]["note"] and logs[-1]["gate"] == "normal",
      "2c 執行紀錄標示價格來源：當日收盤 6 檔")
check(row.get("unrealized_pnl", 0) != 0, "2d 價格不是 0（未實現非零）")

# 3. 即時全缺、收盤只有一半 → 覆蓋率 50% < 80%，不寫入
half = {s: p for i, (s, p) in enumerate(CLOSE_TODAY.items()) if i < 3}
sb, logs = run(base_tables(mkt=mkt_rows(half)), {})
check(len(sb.writes) == 0 and logs[-1]["gate"] == "skipped_incomplete",
      f"3a 覆蓋率 50% → 不寫入、紀錄 skipped_incomplete（實際寫入 {len(sb.writes)}、gate={logs[-1]['gate']}）")
check("0/" not in logs[-1]["note"] and "3/6" in logs[-1]["note"],
      f"3b 紀錄內容為實際覆蓋數 3/6（note={logs[-1]['note'][:40]}…）")

# 4. 部分缺價且無備援：5 檔有即時價、1 檔完全沒有
live_5 = {s: {"price": p} for s, p in CLOSE_TODAY.items() if s != "3008"}
sb, logs = run(base_tables(mkt=[]), live_5)
row = sb.writes[0][1] if sb.writes else {}
exp_unreal5 = sum((-1 if h["side"] == "short" else 1) * (CLOSE_TODAY[h["symbol"]] - h["entry_price"])
                  * h["shares"] * 1000 for h in HOLD if h["symbol"] != "3008")
check(len(sb.writes) == 1 and row.get("open_position_count") == 5,
      f"4a 5/6 有價（≥80%）→ 寫入，持倉有價數 5（實際 {row.get('open_position_count')}）")
check(abs(row.get("quote_coverage", 0) - 0.833) < 0.001,
      f"4b 覆蓋率 0.833（實際 {row.get('quote_coverage')}）")
check(abs(row.get("unrealized_pnl", 0) - round(exp_unreal5, 0)) < 1,
      "4c 缺價的 3008 不進未實現損益（沒有被當成 0 價格計算）")
check(row.get("open_capital") == round(sum(h["entry_price"] * h["shares"] * 1000
                                           for h in HOLD if h["symbol"] != "3008"), 0),
      "4d 缺價的 3008 不進 open_capital")

# 5. 來源標記：同日收盤優先於同日快取；上一交易日收盤標示價格日期
tables = base_tables(
    mkt=mkt_rows({"2330": 1100.0, "2317": 105.0}) + mkt_rows({"2454": 1090.0, "2603": 205.0, "2609": 41.0,
                                                               "3008": 3050.0}, date="2026-11-12"),
    lc=[{"symbol": "2454", "price": 1111.0, "quote_date": "20261113", "quote_time": "10:30:00"},
        {"symbol": "2330", "price": 999.0, "quote_date": "20261113", "quote_time": "10:30:00"}])
sb, logs = run(tables, {})
row = sb.writes[0][1] if sb.writes else {}
note = logs[-1]["note"]
check(len(sb.writes) == 1 and row.get("open_position_count") == 6,
      f"5a 混合來源 6/6 → 寫入（實際 {row.get('open_position_count')}）")
check("當日收盤 2" in note and "即時快取 1" in note and "上一交易日收盤 3" in note,
      "5b 來源計數：當日收盤 2、即時快取 1、上一交易日收盤 3")
check("2026-11-12" in note, "5c 上一交易日收盤標示價格日期 2026-11-12")
exp_mixed = (
    (1100.0 - 1000.0) * 2 * 1000            # 2330：同日收盤 1100 優先於快取 999
    + (105.0 - 100.0) * 1 * 1000            # 2317：同日收盤
    + (1111.0 - 1200.0) * 1 * 1000          # 2454：同日快取 1111（同日收盤不存在）
    + (205.0 - 200.0) * 3 * 1000            # 2603：上一交易日收盤 205
    - (41.0 - 50.0) * 2 * 1000              # 2609：做空，上一交易日收盤 41
    + (3050.0 - 3000.0) * 1 * 1000          # 3008：上一交易日收盤 3050
)
check(abs(row.get("unrealized_pnl", 0) - round(exp_mixed, 0)) < 1,
      f"5d 混合來源未實現損益正確（期望 {round(exp_mixed)}，實際 {row.get('unrealized_pnl')}）")
check(row.get("calc_method") == "v2+fallback", "5e 混合來源 → calc_method=v2+fallback")

# 6. 備援查詢失敗：不拋例外，缺價持倉不估值，門檻照常
sb, logs = run(base_tables(mkt=mkt_rows(CLOSE_TODAY)), live_5, fail_tables=("twse_market_snapshot",))
row = sb.writes[0][1] if sb.writes else {}
check(len(sb.writes) == 1 and row.get("open_position_count") == 5,
      "6a 備援查詢失敗 → 不拋例外，只用既有 5 檔即時價寫入")
sb, logs = run(base_tables(mkt=mkt_rows(CLOSE_TODAY)), {"2330": {"price": 1100.0}},
               fail_tables=("twse_market_snapshot",))
check(len(sb.writes) == 0 and logs[-1]["gate"] == "skipped_incomplete",
      "6b 備援失敗且覆蓋率不足 → 維持不寫入")

# 7. 空價格不被當作有價格
zero_mkt = [{"symbol": "2330", "trade_date": SNAP_DATE, "close_price": 0},
            {"symbol": "2317", "trade_date": SNAP_DATE, "close_price": None}]
fb = None
ns, _ = make_ns(FakeSB(base_tables(mkt=zero_mkt)), {})
fb = ns["_snapshot_price_fallback"](FakeSB(base_tables(mkt=zero_mkt)), ["2330", "2317"], SNAP_DATE, {})
check(fb == {}, "7a 收盤價 0 或 NULL 不會被當成備援價格")
sb, logs = run(base_tables(mkt=zero_mkt), {})
check(len(sb.writes) == 0, "7b 全部價格為 0/NULL → 不寫入（不把缺價當 0）")

passed = sum(results)
print(f"\n合計 {passed}/{len(results)} 通過")
raise SystemExit(0 if passed == len(results) else 1)
