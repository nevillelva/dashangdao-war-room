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

# 9 合併：時間窗（相隔 30 秒合併、相隔 300 秒不合併、無時間視為獨立）
def _m(i, text, t): return {"id": i, "text": text, "status": "new", "created_at": t}
rows_t = [_m(1, "a", "2026-10-13T10:00:00+00:00"), _m(2, "b", "2026-10-13T10:00:30+00:00"),
          _m(3, "c", "2026-10-13T10:05:30+00:00"), _m(4, "d", None)]
gs = ti.group_rows(rows_t)
check("時間窗內連續訊息合併、窗外不合併", [[r["id"] for r in g] for g in gs] == [[1, 2], [3], [4]])

# 10 合併：第二則重貼表頭要去掉（否則表頭會被當資料列）
h = "代號\t日期\t收盤"
part1 = _m(10, h + "\n2330\t2026-10-12\t1020\n", "2026-10-13T10:00:00+00:00")
part2 = _m(11, h + "\n2317\t2026-10-12\t210.5\n", "2026-10-13T10:00:10+00:00")
mt = ti.merge_text([part1, part2])
check("合併時去除重複表頭", mt.count("代號") == 1 and "2317" in mt)
r_merged = ti.preview_group([part1, part2], today=TODAY)
check("合併後預覽 2 列且通過", r_merged["ok"] and len(r_merged["rows"]) == 2)

# 11 合併：表格列被切在兩則中間 → 驗證失敗、整批不寫入（安全失敗，不會悄悄錯位）
cut_a = _m(20, h + "\n2330\t2026-10-12\t10", "2026-10-13T10:00:00+00:00")
cut_b = _m(21, "20\n2317\t2026-10-12\t210.5", "2026-10-13T10:00:05+00:00")
db = mk(); db["telegram_inbox"] += [dict(cut_a), dict(cut_b)]
st, msg = ti.commit_group(db, [db["telegram_inbox"][0], db["telegram_inbox"][1]], today=TODAY)
check("切開的列被驗證擋下、不寫入", st == "invalid" and not db["manual_feed_batches"])
check("切開的整組仍為 new 可處理", all(r["status"] == "new" for r in db["telegram_inbox"]))

# 12 合併確認：整組一次寫入、每則都標 done、批次只寫一筆
db = mk(); db["telegram_inbox"] += [dict(part1), dict(part2)]
grp = ti.fetch_new(db)
st, msg = ti.commit_group(db, grp, today=TODAY)
check("整組合併寫入一筆批次", st == "written" and len(db["manual_feed_batches"]) == 1 and db["manual_feed_batches"][0]["row_count"] == 2)
check("整組每則都標 done", all(r["status"] == "done" for r in db["telegram_inbox"]))
check("批次來源註明合併則數", "2 則合併" in db["manual_feed_batches"][0]["source_label"])

# 13 退回整組：每則都標 rejected；已處理者不可再退
db = mk(); db["telegram_inbox"] += [dict(part1), dict(part2)]
check("退回整組成功", ti.reject_group(db, ti.fetch_new(db), "不要") and all(r["status"] == "rejected" for r in db["telegram_inbox"]))
check("已退回整組不可重退", ti.reject_group(db, [db["telegram_inbox"][0]], "again") is False)

if fails:
    print(f"\n失敗 {len(fails)} 項"); raise SystemExit(1)
print("\n全部通過")
