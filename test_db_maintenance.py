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

# ---------------------------------------------------------------- 【2026-11-15】刪前匯出 + fail-safe（假 DB、假 Storage）
import db_archive
from fake_supabase_for_tests import FakeDB, FakeStorage

def _inst():
    return [{"date": "2026-03-01", "symbol": "2330", "v": 1}, {"date": "2026-03-01", "symbol": "2317", "v": 2},
            {"date": "2026-03-02", "symbol": "2330", "v": 3}, {"date": "2026-10-01", "symbol": "2330", "v": 4}]

def _arch(store, fake_db):
    return lambda table, col, bound: db_archive.archive_before(fake_db, store, table, col, bound)

for pp in (True, False):   # Parquet（若有 pyarrow）與 jsonl.gz 兩種格式都要驗
    fdb, st = FakeDB({"inst_holding": _inst()}), FakeStorage()
    orig = db_archive.encode_shard
    db_archive.encode_shard = lambda rows, prefer_parquet=True, _pp=pp, _o=orig: _o(rows, _pp)
    r = dbm.prune_table(fdb, "inst_holding", "date", 180, today=today, archiver=_arch(st, fdb))
    db_archive.encode_shard = orig
    chk(f"匯出成功才刪（parquet優先={pp}）：舊列刪光、新列保留、匯出 3 列",
        [x["date"] for x in fdb.data["inst_holding"]] == ["2026-10-01"] and r["archived_rows"] == 3 and r["error"] is None, r)
    chk("Storage 有 manifest 且 bucket 已建立", st.bucket_created and len(st.manifests()) == 2)

def _no_delete(name, fdb, r):
    chk(name + "：未刪任何列", len(fdb.data["inst_holding"]) == 4 and fdb.deletes == [], fdb.deletes)
    chk(name + "：回報 error", bool(r["error"]) and r["days_cleaned"] == 0, r)

fdb, st = FakeDB({"inst_holding": _inst()}), FakeStorage(); st.fail_put = True
_no_delete("匯出上傳失敗", fdb, dbm.prune_table(fdb, "inst_holding", "date", 180, today=today, archiver=_arch(st, fdb)))

fdb, st = FakeDB({"inst_holding": _inst()}), FakeStorage(); st.corrupt_get = True
_no_delete("回讀 sha256 校驗失敗", fdb, dbm.prune_table(fdb, "inst_holding", "date", 180, today=today, archiver=_arch(st, fdb)))

fdb, st = FakeDB({"inst_holding": _inst()}), FakeStorage(); st.hide_manifest = True
_no_delete("回讀不到 manifest", fdb, dbm.prune_table(fdb, "inst_holding", "date", 180, today=today, archiver=_arch(st, fdb)))

fdb = FakeDB({"inst_holding": _inst()})
_no_delete("匯出器不可用（None）", fdb, dbm.prune_table(fdb, "inst_holding", "date", 180, today=today, archiver=None))

fdb = FakeDB({"inst_holding": _inst()})
_no_delete("匯出器回報 verified=False", fdb, dbm.prune_table(fdb, "inst_holding", "date", 180, today=today,
           archiver=lambda t, c, b: {"verified": False, "rows": 3}))

fdb = FakeDB({"inst_holding": _inst()})
_no_delete("刪除前列數與已匯出不符", fdb, dbm.prune_table(fdb, "inst_holding", "date", 180, today=today,
           archiver=lambda t, c, b: {"verified": True, "rows": 99}))

fdb = FakeDB({"inst_holding": _inst()})
def _boom(t, c, b): raise db_archive.ArchiveError("匯出炸了")
_no_delete("匯出器丟例外", fdb, dbm.prune_table(fdb, "inst_holding", "date", 180, today=today, archiver=_boom))

fdb, st = FakeDB({"inst_holding": _inst()}), FakeStorage()
r = dbm.prune_table(fdb, "inst_holding", "date", 180, today=today, dry_run=True, archiver=_arch(st, fdb))
chk("dry-run 不匯出也不刪", st.objects == {} and len(fdb.data["inst_holding"]) == 4 and fdb.deletes == [])

# run_retention：匯出器不可用時，必匯出的表不刪，其餘表（perf_log，不在 ARCHIVE_TABLES）照常清
fdb = FakeDB({"inst_holding": _inst(), "perf_log": [{"created_at": "2026-08-01T00:00:00+00:00"}, {"created_at": "2026-10-05T00:00:00+00:00"}]})
res = {r_["table"]: r_ for r_ in dbm.run_retention(fdb, today=today, archiver=None)}
chk("run_retention 無匯出器：inst_holding 保留 4 列、perf_log 照常清掉舊的",
    len(fdb.data["inst_holding"]) == 4 and len(fdb.data["perf_log"]) == 1 and res["inst_holding"]["error"], res["inst_holding"])
chk("指定 4 張表都在 ARCHIVE_TABLES",
    all(t_ in dbm.ARCHIVE_TABLES for t_ in ("inst_holding", "broker_flows", "twse_market_snapshot", "market_signal_snapshot")))
chk("ARCHIVE_TABLES 都在 RETENTION 內", all(t_ in {x[0] for x in dbm.RETENTION} for t_ in dbm.ARCHIVE_TABLES))
chk("ARCHIVE_TABLES 都有讀取排序鍵", all(t_ in db_archive.ORDER_COLS for t_ in dbm.ARCHIVE_TABLES))
_t, _lv = dbm.build_report(None, [{"table": "inst_holding", "error": "x", "days_cleaned": 0}])
chk("匯出失敗讓報告升級為 warn", _lv == "warn", _lv)
raise SystemExit(1 if bad else 0)
