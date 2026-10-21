#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_od_retention.py —— official_open_data 分級保留期：先封存驗證才刪、留最新一天、sitca 永久、名單外不動、分頁、驗證失敗不刪、dry-run 不動。"""
from datetime import datetime
import od_retention as od


class Res:
    def __init__(self, data, count=None): self.data, self.count = data, count


class Q:
    def __init__(self, db): self.db = db; self.f = []; self.mode = "select"; self.o = None; self.desc = False; self.lim = None; self.rng = None; self.want_count = False; self.cols = None
    def select(self, cols, count=None): self.cols = cols; self.want_count = bool(count); return self
    def eq(self, c, v): self.f.append(("eq", c, v)); return self
    def gt(self, c, v): self.f.append(("gt", c, v)); return self
    def order(self, c, desc=False): self.o, self.desc = c, desc; return self
    def limit(self, n): self.lim = n; return self
    def range(self, a, b): self.rng = (a, b); return self
    def delete(self): self.mode = "delete"; return self
    def _match(self, r):
        for k, c, v in self.f:
            x = str(r[c])[:10]
            if k == "eq" and str(r[c]) != str(v) and x != str(v): return False
            if k == "gt" and not x > v: return False
        return True
    def execute(self):
        rows = [r for r in self.db.rows if self._match(r)]
        if self.mode == "delete":
            self.db.rows = [r for r in self.db.rows if r not in rows]
            return Res([])
        if self.o: rows = sorted(rows, key=lambda r: str(r[self.o]), reverse=self.desc)
        n = len(rows)
        if self.rng: rows = rows[self.rng[0]:self.rng[1] + 1]
        if self.lim: rows = rows[:self.lim]
        return Res([dict(r) for r in rows], n if self.want_count else None)


class Bucket:
    def __init__(self, store, corrupt=False): self.store, self.corrupt = store, corrupt
    def upload(self, name, data, opts=None): self.store[name] = data
    def download(self, name):
        d = self.store[name]
        return d[:-3] + b"xyz" if self.corrupt else d


class Storage:
    def __init__(self, corrupt=False): self.files = {}; self.corrupt = corrupt
    def create_bucket(self, *a, **k): pass
    def from_(self, b): return Bucket(self.files, self.corrupt)


class DB:
    def __init__(self, rows, corrupt=False): self.rows = rows; self.storage = Storage(corrupt)
    def table(self, t): return Q(self)


bad = 0
def chk(n, c, e=""):
    global bad; print("✅" if c else "❌", n, "" if c else e); bad += (not c)


def mk(ds, day, n, base=0):
    return [{"id": base + i, "dataset": ds, "data_date": day, "symbol": f"{1000 + i}", "payload": {"x": i}, "source_url": "u"} for i in range(n)]


today = datetime(2026, 11, 20)   # pledge 30 天 → cutoff 2026-10-21
rows = mk("pledge", "2026-10-10", 1500) + mk("pledge", "2026-10-15", 3, 5000) + mk("pledge", "2026-11-19", 3, 9000) + mk("sitca_trust_hold", "2021-09-30", 5, 20000) + mk("unknown_ds", "2020-01-01", 2, 30000)
db = DB(list(rows))
r = od.prune_dataset(db, "pledge", 30, today=today)
left = {(x["dataset"], x["data_date"]) for x in db.rows}
chk("刪掉 10/10、10/15（早於 10/21），留 11/19", ("pledge", "2026-10-10") not in left and ("pledge", "2026-10-15") not in left and ("pledge", "2026-11-19") in left, left)
chk("封存驗證通過、刪 2 天 1503 列（分頁>1000）", r["verified"] and r["deleted_days"] == 2 and r["rows"] == 1503 and r["error"] is None, r)
chk("sitca／名單外沒被動", ("sitca_trust_hold", "2021-09-30") in left and ("unknown_ds", "2020-01-01") in left)
chk("封存檔存在於 archive/official_open_data/pledge/", any(k.startswith("official_open_data/pledge/2026-10-10_2026-10-15") for k in db.storage.files), list(db.storage.files))

# 永遠保留最新一天：同步中斷、所有資料都過期
db = DB(mk("pledge", "2026-08-01", 3) + mk("pledge", "2026-08-02", 3, 100))
r = od.prune_dataset(db, "pledge", 30, today=today)
chk("全部過期仍保留最新一個資料日", {x["data_date"] for x in db.rows} == {"2026-08-02"} and r["deleted_days"] == 1, (db.rows[:1], r))

# 驗證不一致 → 不刪
db = DB(mk("pledge", "2026-10-10", 5) + mk("pledge", "2026-11-19", 2, 100), corrupt=True)
r = od.prune_dataset(db, "pledge", 30, today=today)
chk("下載檔損毀／不一致 → 不刪並回報", len(db.rows) == 7 and r["deleted_days"] == 0 and r["error"], r)

# dry-run
db = DB(mk("pledge", "2026-10-10", 5) + mk("pledge", "2026-11-19", 2, 100))
r = od.prune_dataset(db, "pledge", 30, today=today, dry_run=True)
chk("dry-run 不刪不封存、回報待清", len(db.rows) == 7 and not db.storage.files and r["days"] == 1 and r["rows"] == 5, r)

# 永久保留與 7 天下限
r = od.prune_dataset(DB(mk("sitca_trust_hold", "2021-09-30", 2)), "sitca_trust_hold", 30, today=today)
chk("sitca 永久保留，拒絕處理", r["error"] and r["deleted_days"] == 0)
db = DB(mk("pledge", "2026-11-17", 2) + mk("pledge", "2026-11-19", 2, 100))
od.prune_dataset(db, "pledge", 1, today=today)
chk("保留天數下限 7 天", len(db.rows) == 4)

# 多天積壓、一次處理多日；exdiv 未來日期不動
db = DB(mk("exdiv_upcoming", "2026-09-01", 2) + mk("exdiv_upcoming", "2026-09-02", 2, 100) + mk("exdiv_upcoming", "2026-12-05", 2, 200))
r = od.prune_dataset(db, "exdiv_upcoming", 30, today=today)
chk("exdiv 過期兩天清掉、未來日期保留", {x["data_date"] for x in db.rows} == {"2026-12-05"} and r["deleted_days"] == 2, r)

# 失敗不拋例外
class Boom:
    def table(self, t): raise RuntimeError("db down")
r = od.prune_dataset(Boom(), "pledge", 30, today=today)
chk("DB 失敗不拋例外", r["error"] and "db down" in r["error"])
chk("summarize 有輸出", "pledge" in od.summarize([{"dataset": "pledge", "deleted_days": 2, "rows": 9, "error": None}]))
raise SystemExit(1 if bad else 0)
