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

    def order(self, col, desc=False):
        self._ord = (col, desc)
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
        out = [dict(r) for r in match]
        if getattr(self, "_ord", None):
            out.sort(key=lambda r: str(r.get(self._ord[0])), reverse=self._ord[1])
        return type("R", (), {"data": out})()


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

    # ---------- 族群閘門（10/6）：被標為 fail 的族群，其訊號不得掛單；其餘照常
    sb_real = sb
    sb = FakeSB()
    syms = sorted(full)
    bad, good = syms[0], syms[1:]
    W = lambda n, w, e: {"n": n, "win": w, "exp_pct": e}
    CONFIG["sector_map_v1"] = json.dumps({"map": {bad: "壞族群", **{g: "好族群" for g in good}}})
    CONFIG["sector_winrate_ref_v1"] = json.dumps({"long": {"sectors": {
        "壞族群": {"live_rules": {"chuan_e_ma60_40": {"IS": W(40, .4, -1), "OOS": W(30, .4, -1), "gate_ok": False, "n_enough": True}}},
        "好族群": {"live_rules": {"chuan_e_ma60_40": {"IS": W(40, .6, 1), "OOS": W(30, .6, 1), "gate_ok": True, "n_enough": True}}}}}})
    CONFIG["bt_strategy_config"] = json.dumps({"sector_gate": "soft", "max_new_per_day": 10, "k_slots": 20, "sector_cap": 0})
    RUNLOG.clear()
    run_night(S)
    pend_g = {x["symbol"] for x in sb.db.get("system_portfolio", []) if x["status"] == "pending"}
    check("族群閘門：壞族群的訊號不掛單、好族群照常", bad not in pend_g and set(good) <= pend_g, f"pend={pend_g} bad={bad}")
    check("族群閘門：掛單理由記錄族群與閘門狀態", all("族群：好族群（回測閘門：pass）" in x.get("select_reason", "") for x in sb.db["system_portfolio"] if x["status"] == "pending"))
    check("族群閘門：夜間紀錄註明擋下檔數", any("族群閘門擋下1檔" in r_[4] for r_ in RUNLOG), str(RUNLOG[-1:]))
    sb = FakeSB()
    CONFIG["bt_strategy_config"] = json.dumps({"sector_gate": "off", "max_new_per_day": 10, "k_slots": 20, "sector_cap": 0})
    run_night(S)
    pend_off = {x["symbol"] for x in sb.db.get("system_portfolio", []) if x["status"] == "pending"}
    check("sector_gate=off：全部掛單", bad in pend_off, f"{pend_off}")
    for k_ in ("sector_map_v1", "sector_winrate_ref_v1", "bt_strategy_config"):
        CONFIG.pop(k_, None)
    sb = sb_real

    # ---------- 【F6】持倉風險預算：同族群上限、連續虧損熔斷
    sb_real = sb
    syms = sorted(full)
    CONFIG["sector_map_v1"] = json.dumps({"map": {g: "同一族群" for g in syms}})
    CONFIG["bt_strategy_config"] = json.dumps({"max_new_per_day": 10, "k_slots": 20, "sector_gate": "off", "regime_gate": "off"})
    sb = FakeSB()
    RUNLOG.clear()
    run_night(S)
    pend_cap = [x for x in sb.db.get("system_portfolio", []) if x["status"] == "pending"]
    check("F6 同族群上限：預設 3 檔，其餘被擋", len(pend_cap) == 3 and len(full) > 3, f"pend={len(pend_cap)} full={len(full)}")
    check("F6 夜間紀錄註明同族群上限擋下檔數", any("同族群上限擋下" in r_[4] for r_ in RUNLOG), str(RUNLOG[-1:]))
    sb = FakeSB()
    CONFIG["bt_strategy_config"] = json.dumps({"max_new_per_day": 10, "k_slots": 20, "sector_gate": "off", "regime_gate": "off", "sector_cap": 0})
    run_night(S)
    check("F6 sector_cap=0：不限制", len([x for x in sb.db.get("system_portfolio", []) if x["status"] == "pending"]) == len(full))
    # 熔斷：塞 14 筆已平倉（2 勝 12 敗）→ 今晚不新增掛單、寫入熔斷狀態、推播一次
    CONFIG["bt_strategy_config"] = json.dumps({"max_new_per_day": 10, "k_slots": 20, "sector_gate": "off", "regime_gate": "off", "sector_cap": 0})
    sb = FakeSB()
    sb.db["system_portfolio"] = [{"id": 900 + i, "symbol": f"9{i:03d}", "status": "closed", "trade_type": "swing_bt",
                                  "exit_date": f"2026-09-{10 + i:02d}", "realized_roi": (4.0 if i < 2 else -5.0)} for i in range(14)]
    TG.clear()
    run_night(S)
    check("F6 熔斷：不新增掛單", not [x for x in sb.db["system_portfolio"] if x["status"] == "pending"])
    st_rb = json.loads(CONFIG.get("risk_breaker_v1", "{}") or "{}")
    check("F6 熔斷：狀態寫入 risk_breaker_v1（暫停到 +5 日）", st_rb.get("triggered_on") == "2026-10-02" and st_rb.get("until") == "2026-10-07", str(st_rb))
    check("F6 熔斷：推播一次", len([m for m in TG if "風險預算熔斷" in m]) == 1, str([m[:40] for m in TG]))
    TG.clear()
    run_night(S)
    check("F6 熔斷中重跑：不重複推播、仍不掛單", not [m for m in TG if "風險預算熔斷" in m] and not [x for x in sb.db["system_portfolio"] if x["status"] == "pending"])
    last = json.loads(CONFIG["bt_last_scan"])
    check("F6 bt_last_scan 記錄熔斷原因", "勝率" in (last.get("breaker") or ""), str(last.get("breaker")))
    CONFIG["bt_strategy_config"] = json.dumps({"max_new_per_day": 10, "k_slots": 20, "sector_gate": "off", "regime_gate": "off", "sector_cap": 0, "breaker_enabled": False})
    sb = FakeSB()
    sb.db["system_portfolio"] = [{"id": 900 + i, "symbol": f"9{i:03d}", "status": "closed", "trade_type": "swing_bt",
                                  "exit_date": f"2026-09-{10 + i:02d}", "realized_roi": -5.0} for i in range(14)]
    CONFIG.pop("risk_breaker_v1", None)
    run_night(S)
    check("F6 breaker_enabled=false：照常掛單", len([x for x in sb.db["system_portfolio"] if x["status"] == "pending"]) >= 3)
    for k_ in ("sector_map_v1", "bt_strategy_config", "risk_breaker_v1"):
        CONFIG.pop(k_, None)
    sb = sb_real

    # ---------- 盤勢閘門（10/6 第二輪）：不利盤勢不掛單、有利盤勢照掛；off 全放行；旗標算失敗/無參考表 → 放行
    import regime as _regime_mod
    _orig_tf = _regime_mod.today_flags
    sb_real = sb
    cell = lambda ok: {"IS": W(300, .6 if ok else .45, 1 if ok else -1), "OOS": W(300, .6 if ok else .42, 1 if ok else -1), "gate_ok": ok, "n_enough": True}
    CONFIG["regime_policy_ref_v1"] = json.dumps({"regimes": {"calm": "波動低", "wild": "波動高"}, "long": {"sectors": {
        "全體市場(對照)": {"rules": {"chuan_e_ma60_40": {"all": cell(True), "calm": cell(False), "wild": cell(True)}}}}}})
    _regime_mod.today_flags = lambda prices, asof=None: ("2026-10-02", {"all": True, "calm": True, "wild": False},
                                                         {"breadth20_pct": 55.0, "ew_vs_ma60_pct": 3.2, "n_stocks": 300})
    CONFIG["bt_strategy_config"] = json.dumps({"regime_gate": "soft", "max_new_per_day": 10, "k_slots": 20})
    sb = FakeSB()
    RUNLOG.clear()
    run_night(S)
    check("盤勢閘門(soft)：不利盤勢（低波動）→ 全部擋下、不掛單", not [x for x in sb.db.get("system_portfolio", []) if x["status"] == "pending"], str(sb.db.get("system_portfolio")))
    last = json.loads(CONFIG["bt_last_scan"])
    check("盤勢閘門：bt_last_scan 記錄盤勢與擋下檔數", last.get("regime", {}).get("flags_true") == ["calm"] and last.get("regime_dropped") == len(full), str(last.get("regime")) + str(last.get("regime_dropped")))
    check("盤勢閘門：regime_state_v1 寫入今日盤勢", json.loads(CONFIG["regime_state_v1"])["flags_true"] == ["calm"])
    check("盤勢閘門：夜間紀錄註明擋下檔數", any("盤勢閘門擋下" in r_[4] for r_ in RUNLOG), str(RUNLOG[-1:]))
    CONFIG["bt_strategy_config"] = json.dumps({"regime_gate": "off", "max_new_per_day": 10, "k_slots": 20})
    sb = FakeSB()
    run_night(S)
    check("regime_gate=off：照常掛單", len([x for x in sb.db.get("system_portfolio", []) if x["status"] == "pending"]) >= 3)
    CONFIG["bt_strategy_config"] = json.dumps({"regime_gate": "soft", "max_new_per_day": 10, "k_slots": 20})
    _regime_mod.today_flags = lambda prices, asof=None: ("2026-10-02", {"all": True, "calm": False, "wild": True}, {"n_stocks": 300})
    sb = FakeSB()
    run_night(S)
    check("盤勢閘門(soft)：有利盤勢（高波動）→ 照常掛單", len([x for x in sb.db.get("system_portfolio", []) if x["status"] == "pending"]) >= 3)
    _regime_mod.today_flags = lambda prices, asof=None: (_ for _ in ()).throw(RuntimeError("boom"))
    sb = FakeSB()
    run_night(S)
    check("盤勢旗標計算失敗 → 不套用閘門、照常掛單（缺資料不讓系統停擺）", len([x for x in sb.db.get("system_portfolio", []) if x["status"] == "pending"]) >= 3)
    _regime_mod.today_flags = lambda prices, asof=None: ("2026-10-02", {"all": True, "calm": True}, {"n_stocks": 300})
    CONFIG.pop("regime_policy_ref_v1")
    sb = FakeSB()
    run_night(S)
    check("沒有盤勢參考表 → 放行", len([x for x in sb.db.get("system_portfolio", []) if x["status"] == "pending"]) >= 3)
    _regime_mod.today_flags = _orig_tf
    for k_ in ("bt_strategy_config", "regime_state_v1"):
        CONFIG.pop(k_, None)
    sb = sb_real

    # ---------- 【2026-10-07 治本】舊評分修復版 old_score_v2：候選由 stage_signal 傳入（可不在母體內），停損改 10%
    sb_real = sb
    syms = sorted(full)
    univ_backup = list(UNIVERSE)
    UNIVERSE[:] = syms[:1]                       # 母體只有 1 檔：其餘候選股必須靠『需要下載的清單含候選』才拿得到日K
    CONFIG["bt_strategy_config"] = json.dumps({"rules": ["old_score_v2"], "breadth_min": 0.0, "max_new_per_day": 10, "k_slots": 20,
                                               "sector_gate": "off", "regime_gate": "off", "sector_cap": 0})
    cands_old = [{"symbol": x, "score": 8, "score_nochase": 8 + i % 2, "price": 100.0, "reasons": ["測試因子"]} for i, x in enumerate(syms[:4])]
    cands_old += [{"symbol": "9999", "score": 9, "score_nochase": 9, "price": 50.0, "reasons": []}]      # 沒有日K → 略過
    cands_old += [{"symbol": syms[4], "score": 4, "score_nochase": 5, "price": 50.0, "reasons": []}] if len(syms) > 4 else []   # 分數不到 6 → 略過
    sb = FakeSB()

    def run_night_old(day, cands):
        CUR[0] = datetime(day.year, day.month, day.day, 22, 30, tzinfo=TZ)
        PRICES.clear()
        PRICES.update(view_until(day))
        return stage_bt_nightly(sb, name_map={x: f"測試{x}" for x in full}, old_cands=cands)

    run_night_old(S, cands_old)
    po = [x for x in sb.db.get("system_portfolio", []) if x["status"] == "pending"]
    check("old_score_v2：候選(含不在母體者)全數掛單，無日K/分數不足者略過", sorted(x["symbol"] for x in po) == sorted(syms[:4]), str([x["symbol"] for x in po]))
    check("old_score_v2：strategy_tag／理由／停損線 10%",
          all(x["strategy_tag"] == "old_score_v2" and "舊評分修復版" in x["select_reason"] and "停損10%" in x["select_reason"]
              and abs(x["def_line"] - round(x["entry_price"] * 0.90, 2)) < 0.011 for x in po), str(po[:1]))
    n_old = len(sb.db["system_portfolio"])
    run_night_old(S, cands_old)
    check("old_score_v2：同夜重跑不重複掛單（冪等）", len(sb.db["system_portfolio"]) == n_old)
    day2 = S
    syms_o = {x["symbol"] for x in po}
    for _ in range(30):
        day2 = day2 + pd.offsets.BDay(1)
        run_night_old(day2, None)
        if not [x for x in sb.db["system_portfolio"] if x["status"] in ("pending", "holding") and x["symbol"] in syms_o]:
            break
    rows_o = [x for x in sb.db["system_portfolio"] if x["symbol"] in syms_o and x["status"] in ("closed", "holding")]
    ok_o, n_o = True, 0
    for row in rows_o:
        d = full[row["symbol"]]
        ed = pd.Timestamp(row["entry_date"])
        E = float(d.loc[ed, "Open"])
        if abs(row["def_line"] - round(E * 0.90, 2)) > 0.011 and row["status"] == "holding":
            ok_o = False
            print("   ⚠️ 停損線不是 -10%：", row["symbol"], row["def_line"], E)
        exp = bts.evaluate_exit(d[(d.index >= ed) & (d.index <= day2)], E, 0.12, 0.10, 20)
        if exp is None:
            ok_o = ok_o and row["status"] == "holding"
        else:
            n_o += 1
            if (row["status"] != "closed" or row["exit_date"] != exp["exit_date"] or row["exit_reason"] != exp["reason"]
                    or abs(row["realized_roi"] - round(exp["net_ret"] * 100, 2)) > 1e-6):
                ok_o = False
                print("   ⚠️ old_score_v2 出場不符：", row["symbol"], row["exit_date"], row["exit_reason"], exp)
    check(f"old_score_v2：成交/出場逐筆與 evaluate_exit(停利12%/停損10%/20日) 一致（{n_o} 筆已出場）", ok_o and n_o >= 1, f"n_o={n_o}")
    # 關閉規則：不產生舊評分修復版掛單；候選為 None 不爆
    CONFIG["bt_strategy_config"] = json.dumps({"rules": ["chuan_e_ma60_40"], "breadth_min": 1.01})
    sb = FakeSB()
    run_night_old(S, cands_old)
    check("rules 不含 old_score_v2：不掛舊評分修復版", not [x for x in sb.db.get("system_portfolio", []) if x.get("strategy_tag") == "old_score_v2"])
    CONFIG["bt_strategy_config"] = json.dumps({"rules": ["old_score_v2"], "breadth_min": 0.0})
    sb = FakeSB()
    run_night_old(S, None)
    check("old_cands=None：不爆、不掛單", not sb.db.get("system_portfolio"))
    CONFIG.pop("bt_strategy_config", None)
    UNIVERSE[:] = univ_backup
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
