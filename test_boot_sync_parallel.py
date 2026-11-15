#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_boot_sync_parallel.py —— 開機同步「籌碼 + 大戶」並行抓取：與改動前（序列）版本比對。

比對項目（相同假資料、相同 days_back）：
  1) 回傳的 (籌碼筆數, 大戶筆數) 一致
  2) 寫入本機 SQLite 的兩張表內容逐列一致
  3) 籌碼表第 2 頁失敗、大戶表第 1 頁失敗時，兩版降級結果一致（不能因並行而改變失敗語意）
  4) 進度回報 pct 單調不遞減
  5) 有網路延遲時，並行版耗時明顯低於序列版（籌碼與大戶兩段重疊）
用假 Supabase client 與 in-memory SQLite，不呼叫任何外部服務、不寫入 Supabase。
"""
import sys, types, time, sqlite3
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


# ---------- 改動前的序列版本（逐字保留原邏輯，作為基準；僅移除註解） ----------
def old_sync_from_supabase_on_boot(days_back=None, progress_cb=None):
    def _report(pct, label):
        if progress_cb:
            try:
                progress_cb(pct, label)
            except Exception:
                pass
    if not h.SUPABASE_ENABLED or h.SUPABASE_CONN is None:
        return 0, 0
    cutoff = (datetime.now(h.TAIPEI_TZ) - timedelta(days=days_back)).strftime('%Y-%m-%d')
    inst_rows = bh_rows = 0
    _report(0.05, "連線雲端中")
    _report(0.15, "下載籌碼資料中")
    inst_data = h._sb_fetch_all("inst_holding", gte_col="date", gte_val=cutoff, max_seconds=20)
    _report(0.45, f"寫入籌碼資料（{len(inst_data):,} 筆）")
    if inst_data:
        try:
            _rows_tuples = [
                (r.get("date"), r.get("symbol"), r.get("foreign_buy", 0), r.get("trust_buy", 0),
                 r.get("dealer_buy", 0), r.get("margin", None), r.get("big_holder", 0),
                 r.get("big_holder_date", ""))
                for r in inst_data
            ]
            with h.DB_LOCK:
                h.SQLITE_CONN.executemany('''
                    INSERT INTO inst_holding (date, symbol, foreign_buy, trust_buy, dealer_buy, margin, big_holder, big_holder_date)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(date, symbol) DO UPDATE SET
                        foreign_buy=excluded.foreign_buy, trust_buy=excluded.trust_buy,
                        dealer_buy=excluded.dealer_buy,
                        margin=COALESCE(excluded.margin, inst_holding.margin)
                ''', _rows_tuples)
                h.SQLITE_CONN.commit()
            inst_rows = len(inst_data)
        except Exception as e:
            print(f"[old] 回填 inst_holding 失敗: {e}")
    _report(0.70, "下載大戶資料中")
    bh_data_raw = h._sb_fetch_all("big_holder_weekly", gte_col="week_date", gte_val=cutoff, max_seconds=15)
    bh_data = [{"code": r.get("symbol"), "date": str(r.get("week_date") or ""),
                "percent": r.get("ratio_pct")} for r in bh_data_raw]
    _report(0.85, f"寫入大戶資料（{len(bh_data):,} 筆）")
    if bh_data:
        try:
            _bh_tuples = [(r.get("code"), r.get("date"), r.get("percent"))
                         for r in bh_data if r.get("percent") and r.get("percent") > 0]
            if _bh_tuples:
                with h.DB_LOCK:
                    h.SQLITE_CONN.executemany('''
                        INSERT INTO big_holder_history (code, date, percent) VALUES (?, ?, ?)
                        ON CONFLICT(code, date) DO UPDATE SET percent=excluded.percent
                    ''', _bh_tuples)
                    h.SQLITE_CONN.commit()
            bh_rows = len(bh_data)
        except Exception as e:
            print(f"[old] 回填 big_holder_history 失敗: {e}")
    return inst_rows, bh_rows


# ---------- 假 Supabase client（支援 select / gte / range / execute） ----------
class FakeConn:
    def __init__(self, tables, latency=0.0, fail_at=None):
        self.tables, self.latency, self.fail_at = tables, latency, (fail_at or {})
    def table(self, name):
        return FakeQuery(self, name)

class FakeQuery:
    def __init__(self, conn, name):
        self.conn, self.name, self.filters, self.lo, self.hi = conn, name, [], 0, None
    def select(self, *_a, **_k): return self
    def gte(self, col, val): self.filters.append((col, val)); return self
    def order(self, *_a, **_k): return self
    def range(self, lo, hi): self.lo, self.hi = lo, hi; return self
    def execute(self):
        if self.conn.latency:
            time.sleep(self.conn.latency)
        rows = self.conn.tables[self.name]
        for col, val in self.filters:
            rows = [r for r in rows if str(r.get(col) or "") >= val]
        if self.name in self.conn.fail_at and self.lo >= self.conn.fail_at[self.name]:
            raise RuntimeError(f"{self.name} page at {self.lo} failed (injected)")
        page = rows[self.lo:self.hi + 1]
        return type("R", (), {"data": page})()


def make_data(today):
    """籌碼：50 檔 × 45 天內 + 外圍舊資料（應被 cutoff 排除）；大戶：40 檔 × 45 天，含 0 與 None。"""
    inst, bh = [], []
    for d in range(0, 45):
        day = (today - timedelta(days=d)).strftime('%Y-%m-%d')
        for s in range(50):
            inst.append({"date": day, "symbol": f"{1000+s}", "foreign_buy": s*3-7, "trust_buy": s,
                         "dealer_buy": -s, "margin": (None if s % 7 == 0 else s*11),
                         "big_holder": s/10, "big_holder_date": day})
        for s in range(40):
            pct = None if (d + s) % 13 == 0 else (0 if (d + s) % 11 == 0 else round((s+1)*0.25 + d*0.01, 2))
            bh.append({"symbol": f"{1000+s}", "week_date": day, "ratio_pct": pct})
    for d in range(60, 90):   # 45 天以前的舊資料，應被排除
        day = (today - timedelta(days=d)).strftime('%Y-%m-%d')
        inst.append({"date": day, "symbol": "9999", "foreign_buy": 1, "trust_buy": 1, "dealer_buy": 1,
                     "margin": 1, "big_holder": 1, "big_holder_date": day})
        bh.append({"symbol": "9999", "week_date": day, "ratio_pct": 5})
    return {"inst_holding": inst, "big_holder_weekly": bh}


def new_db():
    c = sqlite3.connect(":memory:", check_same_thread=False)
    c.execute("CREATE TABLE inst_holding (date TEXT, symbol TEXT, foreign_buy REAL, trust_buy REAL, "
              "dealer_buy REAL, margin REAL, big_holder REAL, big_holder_date TEXT, PRIMARY KEY(date, symbol))")
    c.execute("CREATE TABLE big_holder_history (code TEXT, date TEXT, percent REAL, PRIMARY KEY(code, date))")
    c.commit()
    return c

def dump(c):
    return (c.execute("SELECT * FROM inst_holding ORDER BY date, symbol").fetchall(),
            c.execute("SELECT * FROM big_holder_history ORDER BY code, date").fetchall())

def run(fn, tables, latency=0.0, fail_at=None, days_back=45):
    """在獨立的 DB 與假 client 上執行 fn，回傳 (結果, DB 內容, 耗時, 進度列表)。"""
    db = new_db()
    h.SQLITE_CONN = db
    h.DB_LOCK = __import__("threading").Lock()
    h.SUPABASE_CONN = FakeConn(tables, latency=latency, fail_at=fail_at)
    h.SUPABASE_ENABLED = True
    prog = []
    t0 = time.time()
    res = fn(days_back=days_back, progress_cb=lambda p, l: prog.append((p, l)))
    return res, dump(db), time.time() - t0, prog


today = datetime.now(h.TAIPEI_TZ)
TABLES = make_data(today)

# 1) 回傳值與寫入內容一致（正常資料）
r_old, d_old, _, p_old = run(old_sync_from_supabase_on_boot, TABLES)
r_new, d_new, _, p_new = run(h.sync_from_supabase_on_boot, TABLES)
chk("正常資料：回傳 (籌碼筆數, 大戶筆數) 與序列版一致", r_old == r_new, f"old={r_old} new={r_new}")
chk("正常資料：籌碼筆數 = 45 天內 50 檔 × 45 天 = 2250", r_new[0] == 2250, str(r_new[0]))
chk("正常資料：寫入 inst_holding 內容逐列一致", d_old[0] == d_new[0], f"rows old={len(d_old[0])} new={len(d_new[0])}")
chk("正常資料：寫入 big_holder_history 內容逐列一致", d_old[1] == d_new[1], f"rows old={len(d_old[1])} new={len(d_new[1])}")
chk("正常資料：未寫入 45 天以前的舊資料（9999 不得出現）",
    all(row[1] != "9999" for row in d_new[0]) and all(row[0] != "9999" for row in d_new[1]))

# 2) 失敗情境降級結果一致
r_old, d_old, _, _ = run(old_sync_from_supabase_on_boot, TABLES, fail_at={"inst_holding": 1000})
r_new, d_new, _, _ = run(h.sync_from_supabase_on_boot, TABLES, fail_at={"inst_holding": 1000})
chk("籌碼第 2 頁失敗：降級結果（只剩第 1 頁）與序列版一致", r_old == r_new and d_old == d_new, f"old={r_old} new={r_new}")
chk("籌碼第 2 頁失敗：回傳籌碼筆數 < 完整筆數（誠實反映不完整）", 0 < r_new[0] < 2250, str(r_new[0]))

r_old, d_old, _, _ = run(old_sync_from_supabase_on_boot, TABLES, fail_at={"big_holder_weekly": 0})
r_new, d_new, _, _ = run(h.sync_from_supabase_on_boot, TABLES, fail_at={"big_holder_weekly": 0})
chk("大戶第 1 頁失敗：回傳與寫入內容與序列版一致", r_old == r_new and d_old == d_new, f"old={r_old} new={r_new}")
chk("大戶第 1 頁失敗：籌碼仍完整寫入（兩段互不影響）", r_new[0] == 2250 and len(d_new[0]) == 2250, str(r_new))

# 3) 進度回報單調不遞減，且最後到 0.85
mono = all(p_new[i][0] <= p_new[i+1][0] for i in range(len(p_new)-1))
chk("進度回報 pct 單調不遞減", mono, str(p_new))
chk("進度回報最後一步為 0.85（寫入大戶資料）", p_new and abs(p_new[-1][0] - 0.85) < 1e-9, str(p_new[-1:]))

# 4) 網路延遲下並行版耗時應明顯較短（籌碼 3 頁 + 大戶 2 頁，每頁 0.15 秒）
_, _, t_old, _ = run(old_sync_from_supabase_on_boot, TABLES, latency=0.15)
_, _, t_new, _ = run(h.sync_from_supabase_on_boot, TABLES, latency=0.15)
print(f"   耗時（每頁延遲 0.15 秒）：序列 {t_old:.2f} 秒 / 並行 {t_new:.2f} 秒")
chk("並行版耗時明顯低於序列版（<0.85 倍）", t_new < 0.85 * t_old, f"old={t_old:.2f} new={t_new:.2f}")

# 5) 未啟用 Supabase 時維持 (0, 0)
h.SUPABASE_ENABLED = False
chk("Supabase 未啟用時回傳 (0, 0)", h.sync_from_supabase_on_boot(days_back=45) == (0, 0))

print("全部通過" if ok_all else "有失敗項目")
sys.exit(0 if ok_all else 1)
