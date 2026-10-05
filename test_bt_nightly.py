#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_bt_nightly.py —— 端到端驗證 system_scheduler.stage_bt_nightly（記憶體假 Supabase + 合成價格，不連網）。
用 ast 只抽出 _bt_cfg / stage_bt_nightly 兩個函式（system_scheduler 頂層會 import 一堆重套件）。

情境：同一個交易日 S 有多檔合成「穿山惡龍」訊號 →
  夜1(S)    ：產生 pending（≤ 每日上限 3 檔）、不成交
  夜2(S+1)  ：pending 以 S+1 開盤價成交、等額張數 = notional/(開盤×1000)
  之後每夜  ：依日K判定出場 → 與直接呼叫 bt_strategy.evaluate_exit 的結果逐筆一致（出場日/原因/淨報酬）
並驗證：重跑同一夜不重複掛單（冪等）、寬度閘門擋下時不掛單、tail_entry 不處理 swing_bt（以字串檢查）。
"""
import ast
import json
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

import backtest_rules as br
import bt_strategy as bts
from test_bt_strategy import _synthetic_chuan_e

TZ = ZoneInfo("Asia/Taipei")
FAILS = []


def check(name, cond, extra=""):
    print(("  ✅ " if cond else "  ❌ ") + name + ("" if cond else f" {extra}"))
    if not cond:
        FAILS.append(name)


# ------------------------------------------------------------------ 抽出被測函式
src = open("system_scheduler.py", encoding="utf-8").read()
tree = ast.parse(src)
want = {"_bt_cfg", "stage_bt_nightly"}
nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in want]
assert {n.name for n in nodes} == want, "找不到 _bt_cfg / stage_bt_nightly"

CUR = [None]


class FakeDT(datetime):
    @classmethod
    def now(cls, tz=None):
        return CUR[0] if tz is None else CUR[0].astimezone(tz)


CONFIG = {}
TG, RUNLOG = [], []


class FakeQuery:
    def __init__(self, db, table):
        self.db, self.t = db, table
        self.filters, self.op, self.payload = [], "select", None

    def select(self, *_a, **_k):
        self.op = "select"
        return self

    def insert(self, rows):
        self.op, self.payload = "insert", rows
        return self

    def update(self, d):
        self.op, self.payload = "update", d
        return self

    def eq(self, k, v):
        self.filters.append(lambda r, k=k, v=v: r.get(k) == v)
        return self

    def in_(self, k, vs):
        self.filters.append(lambda r, k=k, vs=vs: r.get(k) in vs)
        return self

    def gte(self, k, v):
        self.filters.append(lambda r, k=k, v=v: r.get(k) is not None and str(r.get(k)) >= str(v))
        return self

    def limit(self, *_a):
        return self

    def execute(self):
        rows = self.db.setdefault(self.t, [])
        if self.op == "insert":
            ins = self.payload if isinstance(self.payload, list) else [self.payload]
            for r in ins:
                r = dict(r)
                r["id"] = len(rows) + 1
                rows.append(r)
            return type("R", (), {"data": ins})()
        match = [r for r in rows if all(f(r) for f in self.filters)]
        if self.op == "update":
            for r in match:
                r.update(self.payload)
            return type("R", (), {"data": match})()
        return type("R", (), {"data": [dict(r) for r in match]})()


class FakeSB:
    def __init__(self):
        self.db = {}

    def table(self, name):
        return FakeQuery(self.db, name)


def fake_is_trading_day(d=None):
    return d.weekday() < 5


def fake_prev_trading_day(d):
    p = d - timedelta(days=1)
    while p.weekday() >= 5:
        p -= timedelta(days=1)
    return p


UNIVERSE = []
PRICES = {}

import os
ns = {
    "os": os,
    "json": json, "pd": pd, "datetime": FakeDT, "timezone": timezone, "timedelta": timedelta, "TAIPEI_TZ": TZ,
    "get_config": lambda sb, k, d: CONFIG.get(k, d),
    "set_config": lambda sb, k, v: CONFIG.__setitem__(k, v) or True,
    "is_trading_day": fake_is_trading_day, "_prev_trading_day": fake_prev_trading_day,
    "notify_telegram": lambda m: TG.append(m),
    "_log_stage_run": lambda sb, stage, rd, p=0, e=0, gs="normal", note="": RUNLOG.append((stage, p, e, gs, note)),
    "fetch_name_map": lambda rows=None: {}, "fetch_taiwan_stock_info_raw": lambda: [],
}
exec(compile(ast.Module(nodes, []), "sched", "exec"), ns)
stage_bt_nightly = ns["stage_bt_nightly"]

# 讓 stage 內 `import backtest_rules as br` 拿到的是被我們換掉下載/母體的同一個模組物件
br.load_universe = lambda n: list(UNIVERSE)
br.download_prices = lambda symbols, years: {s: PRICES[s].copy() for s in symbols if s in PRICES}


def build_world(expected_day):
    """多檔合成股：每檔的『訊號日』對齊到同一天 S；S 之後保留原本的後續走勢（供逐夜推進）。"""
    cfg = bts.merge_cfg({"breadth_min": 0.0})
    out, full = {}, {}
    seeds = [(1, 2), (2, 2), (3, 3), (6, 1), (7, 2), (8, 3), (9, 2), (10, 3)]
    for k, (seed, gap) in enumerate(seeds):
        df = _synthetic_chuan_e(seed=seed, gap=gap, drift=0.004)
        evs = br.find_chuan_e_events(df, cfg["ma_n"], cfg["rally_min"], cfg["body_min"], cfg["fast_days"],
                                     cfg["slow_wait"], entries_only=True)
        if not evs:
            continue
        sig_i = evs[0][0] - 1
        # 後面至少留 40 根供逐夜推進
        pre = df.iloc[: sig_i + 1]
        post = df.iloc[sig_i + 1: sig_i + 1 + 40]
        d = pd.concat([pre, post])
        idx = pd.bdate_range(end=expected_day, periods=len(pre)).append(
            pd.bdate_range(start=expected_day + pd.Timedelta(days=1), periods=len(post)))
        d.index = idx
        sym = f"{1101 + k}"
        full[sym] = d
    return full


def main():
    sb = FakeSB()
    S = pd.Timestamp("2026-10-02")           # 假想訊號日（週五）
    full = build_world(S)
    check("合成世界產生 ≥3 檔同日訊號股", len(full) >= 3, f"n={len(full)}")
    UNIVERSE[:] = sorted(full)

    def view_until(day):
        return {s: d[d.index <= day] for s, d in full.items()}

    def run_night(day, note=""):
        CUR[0] = datetime(day.year, day.month, day.day, 22, 30, tzinfo=TZ)
        PRICES.clear()
        PRICES.update(view_until(day))
        return stage_bt_nightly(sb, name_map={s: f"測試{s}" for s in full})

    # ---------- 夜 1：訊號日
    r = run_night(S)
    pend = [x for x in sb.db.get("system_portfolio", []) if x["status"] == "pending"]
    check("夜1 狀態 ok", r.get("status") == "ok", str(r))
    check("夜1 掛單 = min(候選, 每日上限 3)", len(pend) == min(len(full), 3), f"pend={len(pend)}")
    check("夜1 掛單欄位正確（swing_bt／tag／long／隔日開盤）",
          all(p["trade_type"] == "swing_bt" and p["strategy_tag"] == bts.STRATEGY_TAG and p["side"] == "long"
              and p["trigger_source"] == "bt_rule" and p["entry_date"] == "2026-10-02" for p in pend))
    check("夜1 尚無任何成交", not [x for x in sb.db["system_portfolio"] if x["status"] == "holding"])
    # 冪等：同夜重跑
    n_before = len(sb.db["system_portfolio"])
    run_night(S)
    check("同夜重跑不重複掛單（冪等）", len(sb.db["system_portfolio"]) == n_before,
          f"{n_before}->{len(sb.db['system_portfolio'])}")

    # ---------- 逐夜推進
    day = S
    picked_syms = {p["symbol"] for p in pend}
    for _ in range(30):
        day = day + pd.offsets.BDay(1)
        run_night(day)
        if not [x for x in sb.db["system_portfolio"] if x["status"] in ("pending", "holding")
                and x["symbol"] in picked_syms]:
            break
    rows = [x for x in sb.db["system_portfolio"] if x["symbol"] in picked_syms and x["trade_type"] == "swing_bt"
            and x["status"] in ("closed", "holding")]
    # 之後的夜晚可能又產生新訊號（合成後續走勢），只比對『夜1 那批』
    first_batch = {}
    for x in rows:
        if x["entry_date"] >= "2026-10-05":
            first_batch.setdefault(x["symbol"], x)
    check("夜1 掛單的股票都已成交（第一個交易日開盤）", len(first_batch) == len(picked_syms), f"{len(first_batch)} vs {len(picked_syms)}")
    all_ok, n_cmp = True, 0
    for sym, row in first_batch.items():
        d = full[sym]
        ed = pd.Timestamp(row["entry_date"])
        E = float(d.loc[ed, "Open"])
        check_entry = abs(row["entry_price"] - round(E, 2)) < 1e-6 and ed == S + pd.offsets.BDay(1)
        exp = bts.evaluate_exit(d[(d.index >= ed) & (d.index <= day)], E, 0.12, 0.15, 20)
        shares_ok = abs(row["shares"] - round(100000 / (E * 1000), 4)) < 1e-9
        if not (check_entry and shares_ok):
            all_ok = False
            print(f"   ⚠️ {sym} 進場不符 entry={row['entry_price']} E={E:.2f} shares={row['shares']}")
        if exp is None:
            if row["status"] != "holding":
                all_ok = False
        else:
            n_cmp += 1
            if (row["status"] != "closed" or row["exit_date"] != exp["exit_date"] or row["exit_reason"] != exp["reason"]
                    or abs(row["realized_roi"] - round(exp["net_ret"] * 100, 2)) > 1e-6):
                all_ok = False
                print(f"   ⚠️ {sym} 出場不符 row={row['exit_date']},{row['exit_reason']},{row['realized_roi']} exp={exp}")
    check(f"進場價/張數/出場日/原因/淨報酬與 bt_strategy 逐筆一致（{n_cmp} 筆已出場）", all_ok)
    check("至少有一筆走完出場流程", n_cmp >= 1, f"n_cmp={n_cmp}")
    swing_bt_shares = [x["shares"] for x in sb.db["system_portfolio"] if x["status"] in ("holding", "closed")]
    check("等額：每筆名目本金 ≈ 10 萬（容許 tick 取整）",
          all(abs(x["shares"] * x["entry_price"] * 1000 - 100000) < 100 for x in sb.db["system_portfolio"]
              if x["status"] in ("holding", "closed")), str(swing_bt_shares[:5]))

    # ---------- 寬度閘門
    sb2_len = len(sb.db["system_portfolio"])
    CONFIG["bt_strategy_config"] = json.dumps({"breadth_min": 1.01})
    run_night(S)           # 回到訊號日資料；閘門 1.01 → 必擋（冪等也不會因此新增）
    CONFIG.pop("bt_strategy_config")
    last = json.loads(CONFIG["bt_last_scan"])
    check("寬度閘門(門檻>100%)擋下 → gated 且無新增掛單", last["gated"] is True and len(sb.db["system_portfolio"]) == sb2_len, str(last))
    # ---------- 停用
    CONFIG["bt_strategy_config"] = json.dumps({"enabled": False})
    r = run_night(S)
    CONFIG.pop("bt_strategy_config")
    check("enabled=false 時略過", r.get("status") == "disabled")

    # ---------- 乾跑模式：不得寫入任何東西
    import os as _os
    _os.environ["BT_DRY_RUN"] = "1"
    sb_real = sb
    sb = FakeSB()
    TG.clear()
    n_cfg = len(CONFIG)
    CONFIG.pop("bt_last_scan", None)
    r = run_night(S)
    _os.environ.pop("BT_DRY_RUN")
    check("BT_DRY_RUN=1：回報 ok 但資料表零寫入、不寫 config、不推播",
          r.get("status") == "ok" and r.get("picked", 0) >= 1 and not sb.db.get("system_portfolio")
          and "bt_last_scan" not in CONFIG and not TG, f"{r} db={sb.db} tg={len(TG)}")
    sb = sb_real

    # ---------- 靜態檢查：tail_entry 不得處理 swing_bt pending；舊做多預設停用
    i = src.index("def stage_tail_entry")
    j = src.index('p.get("trade_type") == "swing_bt"', i)
    check("stage_tail_entry 對 swing_bt pending 直接略過", 0 < j - i < 6000)
    check("stage_signal 預設停用舊規則做多", "_old_long_on" in src and "old_long_enabled" in src)

    if FAILS:
        print(f"\n❌ {len(FAILS)} 項失敗：{FAILS}")
        sys.exit(1)
    print("\n✅ bt_nightly 端到端全部通過")


if __name__ == "__main__":
    main()
