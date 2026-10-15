import sys
import cleanup_batch as cb

fails = []


def check(n, c):
    print(("PASS: " if c else "FAIL: ") + n)
    if not c:
        fails.append(n)


class Q:
    def __init__(self, log, table, fail_on=None):
        self.log, self.table, self.fail_on, self.op = log, table, fail_on, None
        self.eqs = []

    def delete(self): self.op = "delete"; return self
    def update(self, d): self.op = ("update", d.get("status")); return self
    def eq(self, k, v): self.eqs.append((k, v)); return self

    def execute(self):
        if self.fail_on and self.table == self.fail_on:
            raise RuntimeError("boom")
        self.log.append((self.table, self.op, tuple(self.eqs)))


class Conn:
    def __init__(self, fail_on=None): self.log, self.fail_on = [], fail_on
    def table(self, t): return Q(self.log, t, self.fail_on)


flags = [{"id": 1, "table_name": "route2_watchlist", "trade_date": "2026-08-24"},
         {"id": 2, "table_name": "intraday_5min_bars", "trade_date": "2026-09-02"}]
c = Conn()
ok, bad = cb.delete_flagged(c, flags)
check("兩筆都成功", len(ok) == 2 and not bad)
check("刪的是該表該日期", ("route2_watchlist", "delete", (("trade_date", "2026-08-24"),)) in c.log)
check("刪完才標 deleted", ("cleanup_flags", ("update", "deleted"), (("id", 1),)) in c.log)

c = Conn()
ok, bad = cb.delete_flagged(c, [{"id": 9, "table_name": "system_portfolio", "trade_date": "2026-09-01"}])
check("非白名單表名拒絕且不送出任何刪除", not ok and len(bad) == 1 and c.log == [])

c = Conn()
ok, bad = cb.delete_flagged(c, [{"id": 9, "table_name": "route2_watchlist", "trade_date": None}])
check("缺日期拒絕（避免刪整張表）", not ok and bad and c.log == [])

c = Conn(fail_on="route2_watchlist")
ok, bad = cb.delete_flagged(c, flags)
check("一筆失敗不影響另一筆", len(ok) == 1 and ok[0]["id"] == 2 and len(bad) == 1)
check("失敗那筆沒被標成 deleted", not any(x[0] == "cleanup_flags" and x[2] == (("id", 1),) for x in c.log))

c = Conn()
ok, bad = cb.dismiss_flagged(c, flags)
check("忽略只更新旗標、不刪資料", len(ok) == 2 and all(x[0] == "cleanup_flags" for x in c.log))
if fails:
    print(f"\n{len(fails)} 項失敗"); sys.exit(1)
print("\n全部通過")
