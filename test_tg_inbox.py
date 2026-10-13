"""【2026-10-13】Telegram 收件匣消費端測試（離線，記憶體假資料庫）。"""
from datetime import date

import tg_inbox as ti

fails = []
TODAY = date(2026, 10, 13)


def check(name, cond):
    print(("PASS: " if cond else "FAIL: ") + name)
    if not cond:
        fails.append(name)


class Q:
    def __init__(self, db, name):
        self.db, self.name = db, name
        self.f, self.mode, self.payload, self.lim, self.desc = [], "select", None, None, False

    def select(self, *_a): self.mode = "select"; return self
    def eq(self, k, v): self.f.append(lambda r: r.get(k) == v); return self
    def in_(self, k, vs): self.f.append(lambda r: r.get(k) in vs); return self
    def order(self, k, desc=False): self.desc = desc; return self
    def limit(self, n): self.lim = n; return self
    def update(self, p): self.mode, self.payload = "update", p; return self
    def insert(self, p): self.mode, self.payload = "insert", p; return self

    def execute(self):
        t = self.db[self.name]
        if self.mode == "insert":
            if self.db.get("fail_insert"):
                raise RuntimeError("boom")
            row = dict(self.payload); row["id"] = len(t) + 1; t.append(row)
            return type("R", (), {"data": [row]})
        hit = [r for r in t if all(f(r) for f in self.f)]
        if self.mode == "update":
            for r in hit:
                r.update(self.payload)
            return type("R", (), {"data": hit})
        hit.sort(key=lambda r: r["id"], reverse=self.desc)
        return type("R", (), {"data": hit[: self.lim] if self.lim else hit})


class DB(dict):
    def table(self, n): return Q(self, n)


def mk():
    return DB(telegram_inbox=[], manual_feed_batches=[])


GOOD = "代號\t日期\t收盤\n2330\t2026-10-12\t1,020\n2317\t2026-10-12\t210.5\n"

# 1 寫入成功
db = mk(); db["telegram_inbox"].append({"id": 1, "text": GOOD, "status": "new"})
st, msg = ti.commit(db, db["telegram_inbox"][0], today=TODAY)
check("正常表格寫入批次", st == "written" and len(db["manual_feed_batches"]) == 1)
check("收件匣標記 done", db["telegram_inbox"][0]["status"] == "done")
check("批次來源標記含 Telegram 編號", db["manual_feed_batches"][0]["source_label"] == "Telegram #1")

# 2 重複確認（狀態已不是 new）
st, _ = ti.commit(db, db["telegram_inbox"][0], today=TODAY)
check("已處理訊息不會再寫入", st in ("duplicate", "conflict") and len(db["manual_feed_batches"]) == 1)

# 3 同內容另一則訊息：不重複寫
db["telegram_inbox"].append({"id": 2, "text": GOOD, "status": "new"})
st, _ = ti.commit(db, db["telegram_inbox"][1], today=TODAY)
check("同內容 hash 不重複寫", st == "duplicate" and len(db["manual_feed_batches"]) == 1)
check("重複者標 done 並註記", db["telegram_inbox"][1]["status"] == "done" and "已存過" in db["telegram_inbox"][1]["note"])

# 4 驗證不過：不寫入、狀態維持 new
db = mk(); db["telegram_inbox"].append({"id": 1, "text": "你好這是一般文字", "status": "new"})
st, msg = ti.commit(db, db["telegram_inbox"][0], today=TODAY)
check("非表格文字被退回", st == "invalid" and not db["manual_feed_batches"])
check("驗證失敗時訊息仍為 new（可人工退回）", db["telegram_inbox"][0]["status"] == "new")

# 5 管理員退回
check("退回成功", ti.reject(db, 1, "不是表格") and db["telegram_inbox"][0]["status"] == "rejected")
check("退回原因被記錄", db["telegram_inbox"][0]["note"] == "不是表格")
check("已退回者不可再退回", ti.reject(db, 1, "again") is False)

# 6 寫入批次失敗：還原成 new
db = mk(); db["telegram_inbox"].append({"id": 1, "text": GOOD, "status": "new"}); db["fail_insert"] = True
st, msg = ti.commit(db, db["telegram_inbox"][0], today=TODAY)
check("寫入失敗回報且不吞掉", st == "invalid" and "寫入失敗" in msg)
check("寫入失敗後還原為 new 可重試", db["telegram_inbox"][0]["status"] == "new")

# 7 fetch_new 只取 new 並由舊到新
db = mk()
for i, s in [(1, "done"), (2, "new"), (3, "new"), (4, "rejected")]:
    db["telegram_inbox"].append({"id": i, "text": "x", "status": s})
check("fetch_new 只取待處理且舊的在前", [r["id"] for r in ti.fetch_new(db)] == [2, 3])
check("recent_done 取已處理且新的在前", [r["id"] for r in ti.recent_done(db)] == [4, 1])

# 8 超長備註被截斷
db = mk(); db["telegram_inbox"].append({"id": 1, "text": "x", "status": "new"})
ti.reject(db, 1, "字" * 999)
check("備註截斷", len(db["telegram_inbox"][0]["note"]) == ti.NOTE_MAX)

if fails:
    print(f"\n失敗 {len(fails)} 項"); raise SystemExit(1)
print("\n全部通過")
