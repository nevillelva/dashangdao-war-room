#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_mops_backfill.py —— 歷史財報回補：『查無早期資料』與『金融業沒有營收欄位』不再每次重打、不再被記成 error（2026-10-07 修）。假 Supabase／假 FinMind，不連網。"""
import os
import json

os.environ.setdefault("SUPABASE_URL", "http://x")
os.environ.setdefault("SUPABASE_KEY", "x")
import system_scheduler as ss  # noqa: E402

bad = 0


def chk(n, c, e=""):
    global bad
    print("✅" if c else "❌", n, "" if c else e)
    bad += (not c)


# ---- 純函式
t, n = ss._backfill_pick_targets(["1", "2", "3", "4"], {"1"}, {"2"}, 5)
chk("已回補與已嘗試都排除", t == ["3", "4"] and n == 2, (t, n))
t, n = ss._backfill_pick_targets(["1", "2", "3", "4"], set(), set(), 2)
chk("批次上限", t == ["1", "2"] and n == 4)
chk("成功→normal", ss._backfill_gate_status(3, 0, 0) == "normal")
chk("只有查無歷史→normal（原本被記成 error）", ss._backfill_gate_status(0, 2, 0) == "normal")
chk("全部失敗→error", ss._backfill_gate_status(0, 0, 2) == "error")
chk("部分失敗但有成功→normal", ss._backfill_gate_status(1, 0, 1) == "normal")


# ---- 整個階段（資產負債表）：2 檔查無歷史 → 第一次嘗試、第二次不再嘗試
class R:
    def __init__(self, d): self.data = d


class DB:
    def __init__(self):
        self.cfg, self.log, self.snap = {}, [], []
        self.fetch_calls = []

    def table(self, t): return Q(self, t)


class Q:
    def __init__(self, db, t): self.db, self.t, self.m, self.a = db, t, "select", {}

    def select(self, *a, **k): self.m = "select"; self.a["cols"] = a; return self
    def lt(self, *a, **k): self.a["lt"] = a; return self
    def eq(self, c, v): self.a.setdefault("eq", {})[c] = v; return self
    def not_(self): return self
    def is_(self, *a): return self
    def range(self, a, b): self.a["range"] = (a, b); return self
    def limit(self, n): return self
    def insert(self, row): self.m, self.a["row"] = "insert", row; return self
    def upsert(self, row, **k): self.m, self.a["row"] = "upsert", row; return self

    def execute(self):
        if self.t == "system_config":
            if self.m == "upsert":
                self.db.cfg[self.a["row"]["config_key"]] = self.a["row"]["config_value"]
                return R([])
            k = self.a.get("eq", {}).get("config_key")
            return R([{"config_value": self.db.cfg[k]}] if k in self.db.cfg else [])
        if self.t == "system_run_log":
            self.db.log.append(self.a["row"])
            return R([])
        if self.t == "mops_financial_snapshot":
            if self.m == "upsert":
                self.db.snap.append(self.a["row"])
                return R([])
            if "lt" in self.a:                  # 已回補查詢（year_roc < 113）：沒有任何列
                return R([])
            return R([{"symbol": s} for s in ("1101", "2330", "2881")])    # 全部股票清單（115 年 Q2）
        return R([])


db = DB()


def fake_fetch(sym, a, b):
    db.fetch_calls.append(sym)
    if sym == "2330":
        return [{"year_roc": 112, "season": 4, "total_assets": 1, "total_liabilities": 1, "current_assets": 1,
                 "current_liabilities": 1, "equity_total": 1, "debt_ratio": 1}]
    return []                                    # 1101、2881：查無早期資料


ss.fetch_finmind_balance_sheet_history = fake_fetch
ss.stage_mops_balance_sheet_backfill(db)
chk("第一次打 3 檔", db.fetch_calls == ["1101", "2330", "2881"], db.fetch_calls)
chk("寫入已嘗試清單（含查無歷史的）", set(json.loads(db.cfg["mops_backfill_attempted_bs"])) == {"1101", "2330", "2881"})
chk("第一次記 normal（有 1 檔成功）", db.log[-1]["gate_status"] == "normal", db.log[-1])
db.fetch_calls.clear()
db.log.clear()
ss.stage_mops_balance_sheet_backfill(db)
chk("第二次不再打任何一檔", db.fetch_calls == [], db.fetch_calls)

# 只有查無歷史的情況（原本 error）
db2 = DB()
db2.fetch_calls = []
def fake_fetch2(sym, a, b): db2.fetch_calls.append(sym); return []
ss.fetch_finmind_balance_sheet_history = fake_fetch2
ss.stage_mops_balance_sheet_backfill(db2)
chk("全部查無歷史：記 normal 而非 error", db2.log and db2.log[-1]["gate_status"] == "normal", db2.log)

# 損益表：金融業寫入成功但 revenue 為 None → 永遠不會變成『已回補』，靠已嘗試清單避免每次重打
db3 = DB()
db3.fetch_calls = []
def fake_is(sym, a, b):
    db3.fetch_calls.append(sym)
    return [{"year_roc": 112, "season": 4, "revenue": None, "gross_profit": None, "operating_income": None, "net_income": 1, "eps": 1}]
ss.fetch_finmind_income_statement_history = fake_is
ss.stage_mops_income_statement_backfill(db3)
chk("損益表第一次打 3 檔並記 normal", db3.fetch_calls == ["1101", "2330", "2881"] and db3.log[-1]["gate_status"] == "normal", (db3.fetch_calls, db3.log))
db3.fetch_calls.clear()
ss.stage_mops_income_statement_backfill(db3)
chk("損益表第二次不再重打（金融業無營收欄位）", db3.fetch_calls == [], db3.fetch_calls)

print("\n結果：", "全部通過" if not bad else f"失敗 {bad} 項")
raise SystemExit(1 if bad else 0)
