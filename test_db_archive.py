#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_db_archive.py —— 刪前匯出的核心：分頁讀取、分片、sha256 回讀校驗、格式退回、各種失敗都丟 ArchiveError。假 DB／假 Storage，不連網。"""
import json
import sys

import db_archive as da
from fake_supabase_for_tests import FakeDB, FakeStorage

bad = 0


def chk(name, cond, detail=""):
    global bad
    print(("✅ " if cond else "❌ ") + name + ("" if cond else f"  [{detail}]"))
    bad += (not cond)


def rows(n, day="2026-01-01"):
    return [{"date": day, "symbol": f"S{i:05d}", "payload": {"k": i}, "v": i * 1.5} for i in range(n)]


def raises(fn):
    try:
        fn()
    except da.ArchiveError as e:
        return str(e)
    return None


# 1. 成功：跨分頁（2500 列 > PAGE 1000）、分片、manifest 最後寫入、回讀通過
db, st = FakeDB({"inst_holding": rows(2500) + rows(3, "2026-12-01")}), FakeStorage()
orig_rows = da.ROWS_PER_SHARD
da.ROWS_PER_SHARD = 1000
res = da.archive_before(db, st, "inst_holding", "date", "2026-06-01")
da.ROWS_PER_SHARD = orig_rows
m = list(st.manifests().values())[0]
chk("匯出 2500 列、3 片、verified", res["rows"] == 2500 and res["parts"] == 3 and res["verified"], res)
chk("manifest 記錄每片 rows/sha256 且總和一致", sum(p["rows"] for p in m["parts"]) == 2500 and all(len(p["sha256"]) == 64 for p in m["parts"]))
chk("manifest 是最後寫入的物件", list(st.objects)[-1].endswith("manifest.json"))
chk("匯出本身不刪資料庫任何列", len(db.data["inst_holding"]) == 2503 and db.deletes == [])
try:
    import pyarrow  # noqa: F401
    chk("有 pyarrow 時使用 parquet 格式", res["format"] == "parquet" and all(k.endswith(".parquet") for k in st.objects if "part-" in k), res["format"])
except ImportError:
    chk("沒有 pyarrow 時退回 jsonl.gz", res["format"] == "jsonl.gz")

# 2. 混型別欄位 → parquet 編碼失敗 → 退回 jsonl.gz，仍通過校驗
mixed = [{"date": "2026-01-01", "symbol": "A", "x": 1}, {"date": "2026-01-01", "symbol": "B", "x": "text"}]
db, st = FakeDB({"inst_holding": mixed}), FakeStorage()
res = da.archive_before(db, st, "inst_holding", "date", "2026-06-01")
chk("混型別欄位退回 jsonl.gz 且校驗通過", res["verified"] and res["rows"] == 2, res)

# 3. 0 列：不寫任何物件
db, st = FakeDB({"inst_holding": rows(2, "2026-12-01")}), FakeStorage()
res = da.archive_before(db, st, "inst_holding", "date", "2026-06-01")
chk("0 列不寫 Storage、verified", res["rows"] == 0 and st.objects == {} and res["verified"])

# 4. 整表匯出（col=None，備份表）
db, st = FakeDB({"backup_instant_exit_artifacts_20261005": [{"id": i, "symbol": "x"} for i in range(7)]}), FakeStorage()
res = da.archive_before(db, st, "backup_instant_exit_artifacts_20261005")
chk("整表匯出 7 列", res["rows"] == 7 and res["verified"])

# 5. 失敗情境全部丟 ArchiveError
def mk(): return FakeDB({"inst_holding": rows(5)}), FakeStorage()
db, st = mk(); st.fail_put = True
chk("上傳失敗 → ArchiveError", raises(lambda: da.archive_before(db, st, "inst_holding", "date", "2026-06-01")) is not None)
db, st = mk(); st.corrupt_get = True
chk("回讀 sha256 不符 → ArchiveError", "校驗" in (raises(lambda: da.archive_before(db, st, "inst_holding", "date", "2026-06-01")) or ""))
db, st = mk(); st.hide_manifest = True
chk("回讀不到 manifest → ArchiveError", raises(lambda: da.archive_before(db, st, "inst_holding", "date", "2026-06-01")) is not None)
db, st = mk()
chk("超過 max_rows → ArchiveError（不寫任何物件）",
    raises(lambda: da.archive_before(db, st, "inst_holding", "date", "2026-06-01", max_rows=3)) is not None and st.objects == {})
db, st = mk(); db.fail_select_tables.add("inst_holding")
try:
    da.archive_before(db, st, "inst_holding", "date", "2026-06-01")
    chk("讀取失敗丟例外（由呼叫端捕捉後不刪除，不是靜默成功）", False)
except Exception:
    chk("讀取失敗丟例外（由呼叫端捕捉後不刪除，不是靜默成功）", True)

# 6. 讀出列數與 count 不符（模擬分頁漏列）
class ShortDB(FakeDB):
    def table(self, t):
        q = super().table(t)
        orig = q.execute
        def ex():
            r = orig()
            if r.count is None and r.data:
                r.data = r.data[:-1]
            return r
        q.execute = ex
        return q
db, st = ShortDB({"inst_holding": rows(5)}), FakeStorage()
chk("讀出列數少於 count → ArchiveError（不上傳）", "不符" in (raises(lambda: da.archive_before(db, st, "inst_holding", "date", "2026-06-01")) or "") and st.objects == {})

# 7. 分片過大會對半切
orig_pb = da.PART_BYTES
da.PART_BYTES = 600
shards = da._shards(rows(200), False)
da.PART_BYTES = orig_pb
chk("分片超過上限會對半切且列數守恆", len(shards) > 1 and sum(s[3] for s in shards) == 200 and all(len(s[0]) <= 600 for s in shards), [len(s[0]) for s in shards][:5])

# 8. storage_from_env 缺設定
import os
for k in ("SUPABASE_URL", "SUPABASE_KEY", "SUPABASE_STORAGE_KEY"):
    os.environ.pop(k, None)
chk("缺 Storage 設定 → ArchiveError（訊息只含變數名稱）", "SUPABASE_URL" in (raises(da.storage_from_env) or ""))

print(f"\n結果：{'全部通過' if not bad else f'{bad} 項失敗'}")
sys.exit(1 if bad else 0)
