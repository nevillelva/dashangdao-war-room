#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_db_maintenance.py —— 保留期限：逐日刪、不刪保留期內、7 天下限、dry-run 不刪、容量報告分級。假 Supabase，不連網。"""
from datetime import datetime
import db_maintenance as dbm

class DB:
    def __init__(self, data): self.data = data   # {table: [dict]}
    def table(self, t): return Q(self, t)
class Q:
    def __init__(self, db, t): self.db, self.t, self.mode, self.col, self.lt_v = db, t, None, None, None
    def select(self, col): self.mode, self.col = "select", col; return self
    def order(self, col): return self
    def limit(self, n): return self
    def delete(self): self.mode = "delete"; return self
    def lt(self, col, v): self.col, self.lt_v = col, v; return self
    def execute(self):
        rows = self.db.data[self.t]
        if self.mode == "select":
            rows_sorted = sorted(rows, key=lambda r: str(r[self.col]))
            return type("R", (), {"data": rows_sorted[:1]})()
        self.db.data[self.t] = [r for r in rows if not (str(r[self.col])[:10] < self.lt_v)]
        return type("R", (), {"data": []})()

bad = 0
def chk(n, c, e=""):
    global bad; print("✅" if c else "❌", n, "" if c else e); bad += (not c)

today = datetime(2026, 10, 7)
rows = [{"scan_date": d} for d in ["2026-09-01", "2026-09-02", "2026-09-20", "2026-10-01", "2026-10-06"]]
db = DB({"t": list(rows)})
r = dbm.prune_table(db, "t", "scan_date", 14, today=today)
left = sorted(x["scan_date"] for x in db.data["t"])
chk("保留 14 天：刪掉 9/1、9/2、9/20 之前（cutoff 9/23）", left == ["2026-10-01", "2026-10-06"], left)
chk("回報清了 3 天", r["days_cleaned"] == 3 and r["error"] is None, r)

db = DB({"t": list(rows)})
r = dbm.prune_table(db, "t", "scan_date", 14, today=today, dry_run=True)
chk("dry-run 不刪", len(db.data["t"]) == 5 and r["days_cleaned"] == 1)

db = DB({"t": [{"scan_date": "2026-10-03"}, {"scan_date": "2026-10-06"}]})
dbm.prune_table(db, "t", "scan_date", 1, today=today)    # 要求保留 1 天 → 被 7 天下限擋住
chk("保留天數下限 7 天（防呆）", len(db.data["t"]) == 2, db.data["t"])

db = DB({"t": [{"created_at": "2026-08-01T10:00:00+00:00"}, {"created_at": "2026-10-05T10:00:00+00:00"}]})
dbm.prune_table(db, "t", "created_at", 30, today=today)
chk("timestamptz 欄位也可清", len(db.data["t"]) == 1)

class Boom:
    def table(self, t): raise RuntimeError("db down")
r = dbm.prune_table(Boom(), "t", "c", 30, today=today)
chk("失敗不拋例外、回報 error", r["error"] and "db down" in r["error"])

# 報告分級
mb = 1024 * 1024
t, lv = dbm.build_report({"db_bytes": 237 * mb, "tables": [{"t": "broker_flows", "bytes": 42 * mb}]}, [])
chk("237MB → ok", lv == "ok" and "237MB" in t, (t, lv))
chk("410MB → warn", dbm.build_report({"db_bytes": 410 * mb, "tables": []}, [])[1] == "warn")
chk("470MB → critical", dbm.build_report({"db_bytes": 470 * mb, "tables": []}, [])[1] == "critical")
chk("沒有容量資料也能出報告", dbm.build_report(None, [])[1] == "ok")
# 保留清單健全性
chk("所有保留天數 ≥ 7", all(d >= 7 for _, _, d in dbm.RETENTION))
chk("broker_flows 保留 ≤ 90 天（365 天會超過免費額度）", [d for t_, _, d in dbm.RETENTION if t_ == "broker_flows"][0] <= 90)
raise SystemExit(1 if bad else 0)
