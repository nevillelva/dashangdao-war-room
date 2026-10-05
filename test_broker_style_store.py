#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_broker_style_store.py —— compute_and_store_broker_style（排程端：讀 broker_flows → 算型態 → 寫 broker_style_daily）。
假 Supabase。驗證：①只讀帶 broker_code 的列 ②各日彙總寫入且欄位齊全 ③第二次只重算最近兩個資料日
④表不存在時回 0 不拋例外 ⑤同日再抓到新列後重算會更新。"""
import os
import sys
from datetime import datetime, timedelta

os.environ.setdefault("SUPABASE_URL", "http://x")
os.environ.setdefault("SUPABASE_KEY", "x")
import system_scheduler as ss  # noqa: E402

ok = True


def check(cond, msg):
    global ok
    if not cond:
        ok = False
        print("❌", msg)


class Res:
    def __init__(self, data):
        self.data = data


class Q:
    def __init__(self, db, name, missing=()):
        self.db, self.name, self.missing = db, name, missing
        self.filters, self.gte_f, self.nn, self._rng, self._op, self._payload, self._conflict = [], [], [], None, "select", None, None

    def select(self, *_a, **_k):
        return self

    def eq(self, k, v):
        self.filters.append((k, v))
        return self

    def gte(self, k, v):
        self.gte_f.append((k, v))
        return self

    @property
    def not_(self):
        return self

    def is_(self, col, val):
        if val == "null":
            self.nn.append(col)
        return self

    def order(self, *_a, **_k):
        return self

    def range(self, a, b):
        self._rng = (a, b)
        return self

    def limit(self, n):
        self._rng = (0, n - 1)
        return self

    def upsert(self, payload, on_conflict=None):
        self._op, self._payload, self._conflict = "upsert", payload, on_conflict
        return self

    def insert(self, payload):
        self._op, self._payload = "insert", payload
        return self

    def execute(self):
        if self.name in self.missing:
            raise Exception(f'relation "public.{self.name}" does not exist')
        t = self.db.setdefault(self.name, [])
        if self._op == "select":
            rows = [dict(r) for r in t
                    if all(r.get(k) == v for k, v in self.filters)
                    and all(str(r.get(k)) >= v for k, v in self.gte_f)
                    and all(r.get(c) is not None for c in self.nn)]
            if self._rng:
                rows = rows[self._rng[0]:self._rng[1] + 1]
            return Res(rows)
        rows = self._payload if isinstance(self._payload, list) else [self._payload]
        for r in rows:
            if self._op == "upsert" and self._conflict:
                keys = [k.strip() for k in self._conflict.split(",")]
                for ex in t:
                    if all(str(ex.get(k)) == str(r.get(k)) for k in keys):
                        ex.update(r)
                        break
                else:
                    t.append(dict(r))
            else:
                t.append(dict(r))
        return Res([])


class FakeSB:
    def __init__(self, missing=()):
        self.db, self.missing = {}, missing

    def table(self, name):
        return Q(self.db, name, self.missing)


# 日期：用「最近的 6 個工作日」，確保落在 lookback 內
days, d = [], datetime.now(ss.TAIPEI_TZ).date()
while len(days) < 6:
    d -= timedelta(days=1)
    if d.weekday() < 5:
        days.append(d.isoformat())
days.reverse()
D, D_1 = days[-1], days[-2]


def brow(sym, date, name, net, code="C"):
    return {"symbol": sym, "log_date": date, "broker_name": name, "broker_code": f"{code}-{name}",
            "buy_shares": max(net, 0), "sell_shares": max(-net, 0), "net_shares": net}


sb = FakeSB()
rows = []
for s in ("S1", "S2", "S3"):
    for dd in days:
        rows.append(brow(s, dd, "背景券商", 5))
for dd in days[2:]:
    rows.append(brow("S1", dd, "建倉券商", 100))           # S1：連買 → 建倉
rows.append({**brow("S1", D, "舊列", 9999), "broker_code": None})   # 無 broker_code：忽略
sb.db["broker_flows"] = rows

n = ss.compute_and_store_broker_style(sb)
st = sb.db.get("broker_style_daily", [])
check(n == len(st) and n > 0, f"應寫入彙總：n={n} len={len(st)}")
check({r["log_date"] for r in st} == set(days), f"各日都應有彙總：{sorted({r['log_date'] for r in st})}")
s1 = next(r for r in st if r["symbol"] == "S1" and r["log_date"] == D)
for k in ("verdict", "top15_buy", "flip_buy", "build_buy", "foreign_buy", "other_buy", "flip_pct", "build_pct",
          "foreign_pct", "flip_sell", "build_sell", "build_net_win", "hist_days", "detail"):
    check(k in s1, f"彙總欄位缺 {k}")
check(s1["build_buy"] == 105 and s1["hist_days"] == 6, f"S1 當日建倉買超應 105(建倉券商100+背景5)、歷史 6 日：{s1}")
old_top15 = s1["top15_buy"]
check(s1["verdict"] == "build", f"S1 應判建倉主導：{s1['verdict']}")
check(all(r["top15_buy"] != 9999 for r in st), "舊列(無 broker_code)不該被計入")

# 第二次：全部已存在 → 只重算最近兩個資料日，且 upsert 不產生重複列
before = len(st)
n2 = ss.compute_and_store_broker_style(sb)
st2 = sb.db["broker_style_daily"]
check(len(st2) == before, f"重算不該產生重複列：{before} → {len(st2)}")
check(0 < n2 < n, f"第二次只該重算最近兩日：n2={n2} n={n}")

# 同日再抓到新列 → 重算會更新
sb.db["broker_flows"].append(brow("S1", D, "新進券商", 300))
ss.compute_and_store_broker_style(sb)
s1b = next(r for r in sb.db["broker_style_daily"] if r["symbol"] == "S1" and r["log_date"] == D)
check(s1b["top15_buy"] == old_top15 + 300, f"新列應反映在重算結果：{s1b['top15_buy']} vs {old_top15}")

# 指定日期
n3 = ss.compute_and_store_broker_style(sb, dates=[D_1])
check(n3 == 3, f"指定單日應只寫該日 3 檔：{n3}")

# recompute_all：每個交易日都重算
n4 = ss.compute_and_store_broker_style(sb, recompute_all=True)
check(n4 == n, f"recompute_all 應重算全部 {n} 列：{n4}")

# 表不存在 → 回 0、不拋例外
sb_missing = FakeSB(missing=("broker_style_daily",))
sb_missing.db["broker_flows"] = rows
check(ss.compute_and_store_broker_style(sb_missing) == 0, "表不存在應回 0")

# 沒有任何帶 broker_code 的列 → 0
sb_empty = FakeSB()
sb_empty.db["broker_flows"] = [{**rows[0], "broker_code": None}]
check(ss.compute_and_store_broker_style(sb_empty) == 0, "無帶 broker_code 的列應回 0")

print("✅ test_broker_style_store 全部通過" if ok else "❌ test_broker_style_store 失敗")
sys.exit(0 if ok else 1)
