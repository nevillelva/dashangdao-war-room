#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_boot_sync_incremental.py —— 開機同步增量化（2026-11-15）。假 Supabase + in-memory SQLite，不連外。

驗證：
  1) 首次（本機空）= 全量；第二次（已完整全量過）= 增量，只抓 gte(最新日-重疊) 之後，且最終本機內容與「再做一次全量」逐列一致
  2) 增量能吸收新日期、重疊區間內的修正（外資數字被改）
  3) 增量撈取中途失敗 → 該表回退全量，最終內容仍完整
  4) 全量曾失敗/不完整 → 不開啟增量；超過 24 小時 → 回全量
  5) 本機缺頭（最早日期晚於 cutoff+容許）→ 全量；incremental=False → 全量
  6) 穩定排序：增量查詢仍帶 inst_holding 的 (date, symbol) 排序
"""
import sys, types, sqlite3, threading, time
from datetime import datetime, timedelta
try:
    import openai  # noqa: F401
except ImportError:
    _m = types.ModuleType("openai"); _m.OpenAI = object; sys.modules["openai"] = _m
import dashangdao_helpers as h

ok_all = True
def chk(name, cond, extra=""):
    global ok_all
    print(("✅ " if cond else "❌ ") + name + ("" if cond or not extra else f"  [{extra}]"))
    ok_all = ok_all and bool(cond)

LOG = []   # (table, gte_val, orders)

class FakeConn:
    def __init__(self, tables, fail_when=None):
        self.tables, self.fail_when = tables, fail_when
    def table(self, name): return FakeQuery(self, name)

class FakeQuery:
    def __init__(self, conn, name):
        self.conn, self.name, self.filters, self.orders, self.lo, self.hi = conn, name, [], [], 0, None
    def select(self, *_a, **_k): return self
    def gte(self, col, val): self.filters.append((col, val)); return self
    def order(self, col, **_k): self.orders.append(col); return self
    def range(self, lo, hi): self.lo, self.hi = lo, hi; return self
    def execute(self):
        LOG.append((self.name, self.filters[0][1] if self.filters else None, tuple(self.orders)))
        if self.conn.fail_when and self.conn.fail_when(self.name, self.filters):
            raise RuntimeError("injected failure")
        rows = self.conn.tables[self.name]
        for col, val in self.filters:
            rows = [r for r in rows if str(r.get(col) or "") >= val]
        key = (lambda r: (r["date"], r["symbol"])) if self.name == "inst_holding" else (lambda r: r["id"])
        rows = sorted(rows, key=key)
        return type("R", (), {"data": rows[self.lo:self.hi + 1]})()

def day(today, d): return (today - timedelta(days=d)).strftime('%Y-%m-%d')

def make(today, newest_offset=0, span=45, nsym=30):
    inst, bh, i = [], [], 0
    for d in range(newest_offset, span):
        for s in range(nsym):
            inst.append({"date": day(today, d), "symbol": f"{1000+s}", "foreign_buy": s - d, "trust_buy": s,
                         "dealer_buy": -s, "margin": s * 3, "big_holder": 0, "big_holder_date": ""})
        if d % 7 == 0:
            for s in range(nsym):
                i += 1
                bh.append({"id": i, "symbol": f"{1000+s}", "week_date": day(today, d), "ratio_pct": 10 + s})
    return {"inst_holding": inst, "big_holder_weekly": bh}

def new_db():
    c = sqlite3.connect(":memory:", check_same_thread=False)
    c.execute("CREATE TABLE inst_holding (date TEXT, symbol TEXT, foreign_buy REAL, trust_buy REAL, "
              "dealer_buy REAL, margin REAL, big_holder REAL, big_holder_date TEXT, PRIMARY KEY(date, symbol))")
    c.execute("CREATE TABLE big_holder_history (code TEXT, date TEXT, percent REAL, PRIMARY KEY(code, date))")
    c.commit(); return c

def dump(c):
    return (c.execute("SELECT * FROM inst_holding ORDER BY date, symbol").fetchall(),
            c.execute("SELECT * FROM big_holder_history ORDER BY code, date").fetchall())

def setup(tables, fail_when=None, db=None):
    h.SQLITE_CONN = db or new_db(); h.DB_LOCK = threading.Lock()
    h.SUPABASE_CONN = FakeConn(tables, fail_when); h.SUPABASE_ENABLED = True
    h._BOOT_SYNC_META.update({"last_full_ts": 0.0, "last_mode": None})
    LOG.clear()
    return h.SQLITE_CONN

today = datetime.now(h.TAIPEI_TZ)
cutoff = day(today, 45)

# 1) 首次全量 → 第二次增量
T = make(today, newest_offset=1)               # 雲端最新 = 昨天
db = setup(T)
r1 = h.sync_from_supabase_on_boot(days_back=45)
chk("首次：模式為全量", h._BOOT_SYNC_META["last_mode"] == "full")
chk("首次：gte 用 cutoff", all(g == cutoff for _, g, _ in LOG), LOG[:2])
full_rows = r1[0]
# 雲端新增今天資料 + 修正重疊區間內某筆（昨天 1000 外資改成 99999）
for s in range(30):
    T["inst_holding"].append({"date": day(today, 0), "symbol": f"{1000+s}", "foreign_buy": 5, "trust_buy": 5,
                              "dealer_buy": 5, "margin": 1, "big_holder": 0, "big_holder_date": ""})
for r in T["inst_holding"]:
    if r["date"] == day(today, 1) and r["symbol"] == "1000": r["foreign_buy"] = 99999
LOG.clear()
r2 = h.sync_from_supabase_on_boot(days_back=45)
chk("第二次：模式為增量", h._BOOT_SYNC_META["last_mode"] == "incremental", h._BOOT_SYNC_META)
inst_g = [g for t, g, _ in LOG if t == "inst_holding"]
chk("第二次：inst 查詢 gte = 最新日-7 天（遠晚於 cutoff）", inst_g and all(g == day(today, 1 + 7) for g in inst_g), inst_g)
chk("第二次：寫入筆數遠少於全量", 0 < r2[0] < full_rows / 3, f"{r2} vs {full_rows}")
chk("第二次：新日期已寫入", db.execute("SELECT COUNT(*) FROM inst_holding WHERE date=?", (day(today, 0),)).fetchone()[0] == 30)
chk("第二次：重疊區間內的修正已吸收",
    db.execute("SELECT foreign_buy FROM inst_holding WHERE date=? AND symbol='1000'", (day(today, 1),)).fetchone()[0] == 99999)
chk("增量查詢仍帶穩定排序 (date, symbol)", all(o == ("date", "symbol") for t, _, o in LOG if t == "inst_holding"), LOG)
inc_dump = dump(db)
setup(T); h.sync_from_supabase_on_boot(days_back=45)   # 全部重抓一次作基準
chk("增量結果 == 全量結果（inst 逐列）", inc_dump[0] == dump(h.SQLITE_CONN)[0])
chk("增量結果 == 全量結果（大戶逐列）", inc_dump[1] == dump(h.SQLITE_CONN)[1])

# 2) 增量中途失敗 → 回退全量
T = make(today, newest_offset=1)
db = setup(T); h.sync_from_supabase_on_boot(days_back=45)
state = {"n": 0}
def fail_inc(name, filters):
    return name == "inst_holding" and filters and filters[0][1] != cutoff   # 只讓「增量」查詢失敗，全量放行
h.SUPABASE_CONN = FakeConn(T, fail_inc); LOG.clear()
r = h.sync_from_supabase_on_boot(days_back=45)
chk("增量失敗：籌碼表回退全量（模式 mixed：大戶表仍增量）", h._BOOT_SYNC_META["last_mode"] == "mixed", h._BOOT_SYNC_META)
chk("增量失敗：之後有以 cutoff 重撈 inst", any(t == "inst_holding" and g == cutoff for t, g, _ in LOG), LOG)
chk("增量失敗：本機內容完整", len(dump(db)[0]) == len(T["inst_holding"]))

# 3) 全量不完整 → 不開增量
T = make(today, newest_offset=1)
db = setup(T, fail_when=lambda n, f: n == "big_holder_weekly")
h.sync_from_supabase_on_boot(days_back=45)
chk("全量時大戶失敗：不記錄全量完成時間", h._BOOT_SYNC_META["last_full_ts"] == 0.0)
h.SUPABASE_CONN = FakeConn(T); LOG.clear(); h.sync_from_supabase_on_boot(days_back=45)
chk("全量不完整後下一次仍為全量", h._BOOT_SYNC_META["last_mode"] == "full")

# 4) 超過 24 小時 → 全量
T = make(today, newest_offset=1)
db = setup(T); h.sync_from_supabase_on_boot(days_back=45)
h._BOOT_SYNC_META["last_full_ts"] = time.time() - 25 * 3600; LOG.clear()
h.sync_from_supabase_on_boot(days_back=45)
chk("距上次全量 >24 小時：回全量", h._BOOT_SYNC_META["last_mode"] == "full")

# 5) 本機缺頭 → 全量；incremental=False → 全量
T = make(today, newest_offset=1)
db = setup(T); h.sync_from_supabase_on_boot(days_back=45)
db.execute("DELETE FROM inst_holding WHERE date < ?", (day(today, 20),)); db.commit(); LOG.clear()
h.sync_from_supabase_on_boot(days_back=45)
chk("本機缺頭（最早日期晚於視窗）：該表全量補齊", h._BOOT_SYNC_META["last_mode"] == "mixed" and len(dump(db)[0]) == len(T["inst_holding"]))
h.sync_from_supabase_on_boot(days_back=45)
chk("補齊後下一次回到增量", h._BOOT_SYNC_META["last_mode"] == "incremental")
h.sync_from_supabase_on_boot(days_back=45, incremental=False)
chk("incremental=False 強制全量", h._BOOT_SYNC_META["last_mode"] == "full")

# 6) 本機空 → 全量（即使旗標顯示近期全量過，例如容器重建 DB 被清）
T = make(today, newest_offset=1)
setup(T); h.sync_from_supabase_on_boot(days_back=45)
h.SQLITE_CONN = new_db(); h.sync_from_supabase_on_boot(days_back=45)
chk("本機被清空：全量重建", h._BOOT_SYNC_META["last_mode"] == "full" and len(dump(h.SQLITE_CONN)[0]) == len(T["inst_holding"]))

# 7) _sb_fetch_all stats
st = {}
setup(make(today)); rows = h._sb_fetch_all("inst_holding", gte_col="date", gte_val=cutoff, stats=st)
chk("_sb_fetch_all stats.complete=True（正常撈完）", st.get("complete") is True and len(rows) > 0)
st = {}; h._sb_fetch_all("inst_holding", gte_col="date", gte_val="2999-01-01", stats=st)
chk("_sb_fetch_all 查詢成功但無資料 → complete", st.get("complete") is True)
h.SUPABASE_CONN = FakeConn(make(today), lambda n, f: True); st = {}
h._sb_fetch_all("inst_holding", stats=st)
chk("_sb_fetch_all 查詢失敗 → complete=False", st.get("complete") is False)
st = {}; h._sb_fetch_all("inst_holding", max_seconds=-1, stats=st)
chk("_sb_fetch_all max_seconds 超時 → complete=False", st.get("complete") is False)

print("全部通過" if ok_all else "有失敗項目")
sys.exit(0 if ok_all else 1)
