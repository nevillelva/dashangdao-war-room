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

# 14 AI 斷行修復：AI 只回行號、程式逐字接回；任何不合法的回覆整個作廢
CUT = [_m(1, "代號\t日期\t收盤\n2330\t2026-10-12\t1,0", "2026-10-13T10:00:00+00:00"),
       _m(2, "20\n2317\t2026-10-12\t210.5\n2454\t2026-10-12\t9", "2026-10-13T10:00:30+00:00"),
       _m(3, "80\n", "2026-10-13T10:00:50+00:00")]
lines = ti.repair_lines(CUT)
check("斷行前：整組驗證不過", ti.preview_group(CUT, today=TODAY)["ok"] is False)
check("提示詞含欄數與行號、不含 AI 以外的指令", "表頭有 3 欄" in ti.build_repair_prompt(lines) and "3｜1｜20" in ti.build_repair_prompt(lines))
check("行號清單解析：合法", ti.parse_join_plan('{"join_with_previous":[3,5]}', 5) == [3, 5])
check("行號清單解析：夾雜說明文字也能取出 JSON", ti.parse_join_plan('好的\n{"join_with_previous":[2]}', 5) == [2])
for bad, why in [('{"join_with_previous":[1]}', "第 1 行不能接"), ('{"join_with_previous":[201]}', "超出行數"),
                 ('{"join_with_previous":[3,3]}', "重複"), ('{"join_with_previous":["3"]}', "非整數"),
                 ('{"join_with_previous":[true]}', "布林"), ('不是json', "格式"), ('{"x":1}', "缺欄位"),
                 ('{"join_with_previous":' + str(list(range(2, 2 + 101))) + '}', "超過接回上限")]:
    check(f"行號清單解析：{why} → 作廢", ti.parse_join_plan(bad, 200) is None)
check("apply_join_plan 逐字串接、不加字元", ti.apply_join_plan(["a\tb", "c", "d"], [2]) == "a\tbc\nd")

def mk_ai(reply):
    seen = {}
    def f(sys_, usr, val):
        seen["usr"] = usr
        return (True, reply) if val(reply) else (False, "內容未通過驗證")
    f.seen = seen
    return f

# 這組的行：1 表頭；2 "2330..1,0"；3 "20"；4 "2317…"；5 "2454…9"；6 "80"
ai = mk_ai('{"join_with_previous":[3,6]}')
r = ti.ai_repair(CUT, ai, today=TODAY)
check("AI 判斷正確 → 接回後通過驗證，數字逐字不變", r["ok"] and r["joins"] == [3, 6] and "1,020" in r["text"] and "980" in r["text"]
      and len(r["preview"]["rows"]) == 3)
check("接回後去掉換行的內容與原文相同（沒有改字）", r["text"].replace("\n", "") == "".join(lines))
check("AI 只看到提示詞、回覆受 token 限制常數約束", ti.REPAIR_MAX_TOKENS <= 400 and "收盤" in ai.seen["usr"])
r2 = ti.ai_repair(CUT, mk_ai('{"join_with_previous":[3]}'), today=TODAY)
check("AI 少接一處 → 驗證仍不過，不得採用", r2["ok"] is False and "驗證" in r2["error"])
r3 = ti.ai_repair(CUT, mk_ai('{"join_with_previous":[]}'), today=TODAY)
check("AI 說不用接 → 不採用", r3["ok"] is False)
r4 = ti.ai_repair(CUT, lambda s_, u_, v_: (False, "全部模型失敗"), today=TODAY)
check("AI 失敗 → 回報原因、不採用", r4["ok"] is False and "全部模型失敗" in r4["error"])
big = [_m(i, f"2330\t2026-10-12\t{i}\n", "2026-10-13T10:00:00+00:00") for i in range(2, 2 + ti.REPAIR_MAX_LINES + 5)]
big.insert(0, _m(1, "代號\t日期\t收盤\n", "2026-10-13T10:00:00+00:00"))
r5 = ti.ai_repair(big, mk_ai('{"join_with_previous":[2]}'), today=TODAY)
check("行數超過上限 → 拒絕處理（不截斷）", r5["ok"] is False and "上限" in r5["error"])

# 採用：文字集中到第一列，其餘清空，仍同一組可 commit
db = mk(); db["telegram_inbox"] += [dict(x) for x in CUT]
grp = ti.fetch_new(db)
rr = ti.ai_repair(grp, mk_ai('{"join_with_previous":[3,6]}'), today=TODAY)
check("採用接回結果成功", ti.apply_repair(db, grp, rr["text"]) is True)
grp2 = ti.fetch_new(db)
check("採用後整組驗證通過", ti.preview_group(grp2, today=TODAY)["ok"])
st, msg = ti.commit_group(db, grp2, today=TODAY)
check("採用後確認寫入成功，列數 3", st == "written" and db["manual_feed_batches"][0]["row_count"] == 3)
db2 = mk(); db2["telegram_inbox"] += [dict(x, status="done") for x in CUT]
check("已處理過的列不可採用", ti.apply_repair(db2, CUT, "x") is False)

if fails:
    print(f"\n失敗 {len(fails)} 項"); raise SystemExit(1)
print("\n全部通過")
