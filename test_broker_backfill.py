#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_broker_backfill.py —— 分點歷史回補階段(stage_broker_backfill)的單元測試：假 Supabase＋假 DJ 抓取。
驗證：①休市日略過 ②已帶 broker_code 的(標的,日)不重抓 ③舊列(無 broker_code)會被重抓並整筆取代
④暫時性失敗會重試 ⑤無資料(停牌/ETF)記入 broker_backfill_empty、第二次執行不再重查
⑥資料日與查詢日不符不寫入 ⑦第二次執行＝全部補齊後直接結束。"""
import os
import sys
import json
import time

import pandas as pd

os.environ.setdefault("SUPABASE_URL", "http://x")
os.environ.setdefault("SUPABASE_KEY", "x")
import system_scheduler as ss  # noqa: E402

time.sleep = lambda *_a, **_k: None   # 重試間隔不真的等


class Res:
    def __init__(self, data):
        self.data = data


class Q:
    def __init__(self, db, name):
        self.db, self.name = db, name
        self.filters, self.nn_cols, self._rng, self._op, self._payload = [], [], None, "select", None
        self._conflict = None

    # --- builder ---
    def select(self, *_a, **_k):
        self._op = "select"
        return self

    def eq(self, k, v):
        self.filters.append((k, v))
        return self

    def gte(self, k, v):
        self.gte_f = getattr(self, "gte_f", []) + [(k, v)]
        return self

    @property
    def not_(self):
        return self

    def is_(self, col, val):
        if val == "null":
            self.nn_cols.append(col)
        return self

    def order(self, *_a, **_k):
        return self

    def range(self, a, b):
        self._rng = (a, b)
        return self

    def limit(self, n):
        self._rng = (0, n - 1)
        return self

    def delete(self):
        self._op = "delete"
        return self

    def upsert(self, payload, on_conflict=None):
        self._op, self._payload, self._conflict = "upsert", payload, on_conflict
        return self

    def insert(self, payload):
        self._op, self._payload = "insert", payload
        return self

    def _match(self, r):
        return (all(r.get(k) == v for k, v in self.filters) and all(r.get(c) is not None for c in self.nn_cols)
                and all(str(r.get(k)) >= v for k, v in getattr(self, "gte_f", [])))

    def execute(self):
        t = self.db.setdefault(self.name, [])
        if self._op == "select":
            rows = [dict(r) for r in t if self._match(r)]
            if self._rng:
                rows = rows[self._rng[0]:self._rng[1] + 1]
            return Res(rows)
        if self._op == "delete":
            self.db[self.name] = [r for r in t if not self._match(r)]
            return Res([])
        rows = self._payload if isinstance(self._payload, list) else [self._payload]
        for r in rows:
            if self._op == "upsert" and self._conflict:
                keys = [k.strip() for k in self._conflict.split(",")]
                for ex in t:
                    if all(ex.get(k) == r.get(k) for k in keys):
                        ex.update(r)
                        break
                else:
                    t.append(dict(r))
            else:
                t.append(dict(r))
        return Res([])


class FakeSB:
    def __init__(self):
        self.db = {}

    def table(self, name):
        return Q(self.db, name)


def mkdf(date, code, broker_prefix="券商"):
    rows = [{"broker_name": f"{broker_prefix}{i}", "buy_shares": 100 + i, "sell_shares": 10, "net_shares": 90 + i,
             "broker_code": f"B{i}", "pct_of_volume": 1.5} for i in range(3)]
    df = pd.DataFrame(rows)
    df.attrs.update({"data_date": date, "total_buy": 1000, "total_sell": 900, "avg_buy_cost": 10.5, "avg_sell_cost": 10.2})
    return df


calls = []
flaky = {"2317|2026-09-24": 1}   # 第一次失敗


def fake_fetch(code, start=None, end=None, hosts=None, **_k):
    host = hosts[0][0]
    calls.append((code, start, host))
    if code == "9999":
        ss._wc._dj_fail_streak[host] = 0
        return None                                  # 無資料(停牌)
    key = f"{code}|{start}"
    if flaky.get(key, 0) > 0:
        flaky[key] -= 1
        ss._wc._dj_fail_streak[host] = ss._wc._dj_fail_streak.get(host, 0) + 1
        return None                                  # 暫時性失敗
    ss._wc._dj_fail_streak[host] = 0
    if code == "2330" and start == "2026-09-29":
        return mkdf("2026-09-30", code)              # 資料日與查詢日不符
    return mkdf(start, code)


ss._wc.fetch_dj_branch_data = fake_fetch
ss.get_broker_flows_target_symbols = lambda sb: ["2330", "2317", "9999"]
ss.notify_telegram = lambda *_a, **_k: None
os.environ["BACKFILL_START"] = "2026-09-24"
os.environ["BACKFILL_END"] = "2026-09-30"

sb = FakeSB()
# 9/30：2330 有舊列(無 broker_code)；2317 已帶 broker_code(視為完成)
sb.db["broker_flows"] = [
    {"symbol": "2330", "log_date": "2026-09-30", "broker_name": "舊券商", "buy_shares": 1, "sell_shares": 0, "net_shares": 1, "broker_code": None},
    {"symbol": "2317", "log_date": "2026-09-30", "broker_name": "已完成", "buy_shares": 5, "sell_shares": 0, "net_shares": 5, "broker_code": "ZZ"},
]
ss.stage_broker_backfill(sb)

bf = sb.db["broker_flows"]
ok = True


def check(cond, msg):
    global ok
    if not cond:
        ok = False
        print("❌", msg)


days_called = {c[1] for c in calls}
check("2026-09-25" not in days_called and "2026-09-28" not in days_called, f"休市日不該被查：{sorted(days_called)}")
check(not any(c[0] == "2317" and c[1] == "2026-09-30" for c in calls), "已帶 broker_code 的 2317/9-30 不該重抓")
old = [r for r in bf if r["symbol"] == "2330" and r["log_date"] == "2026-09-30"]
check(old and all(r["broker_name"] != "舊券商" for r in old) and all(r["broker_code"] for r in old), "2330/9-30 舊列應被整筆取代並帶 broker_code")
check(any(r["symbol"] == "2317" and r["log_date"] == "2026-09-24" for r in bf), "2317/9-24 暫時失敗後應重試成功")
check(not any(r["symbol"] == "2330" and r["log_date"] == "2026-09-29" for r in bf), "資料日不符(2330/9-29)不該寫入")
check(not any(r["symbol"] == "9999" for r in bf), "無資料標的不該寫入分點列")
emp = json.loads(next(r["config_value"] for r in sb.db["system_config"] if r["config_key"] == "broker_backfill_empty"))
check(set(emp) == {"2026-09-24", "2026-09-29", "2026-09-30"} and all(v == ["9999"] for v in emp.values()), f"無資料清單不對：{emp}")
check(any(r["symbol"] == "2317" and r["log_date"] == "2026-09-29" and r["pct_of_volume"] == 1.5 for r in bf), "應寫入 pct_of_volume")
check(len(sb.db.get("broker_flow_summary", [])) >= 4, "應寫入彙總")
rl = sb.db["system_run_log"][-1]
check(rl["stage"] == "broker_backfill", f"應寫 run_log：{rl}")
check(len(sb.db.get("broker_style_daily", [])) > 0, "回補寫入新資料後應順手補算分點型態彙總")

# 第二次執行：不應再查 9999；2330/9-29 因日期不符仍待補（每次都會重試，這是預期）
calls.clear()
ss.stage_broker_backfill(sb)
check(not any(c[0] == "9999" for c in calls), "第二次不該再查已確認無資料的 9999")
check(all(c[0] == "2330" and c[1] == "2026-09-29" for c in calls), f"第二次只該重試 2330/9-29：{calls}")

print("✅ test_broker_backfill 全部通過" if ok else "❌ test_broker_backfill 失敗")
sys.exit(0 if ok else 1)
