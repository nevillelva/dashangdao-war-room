#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_db_retention.py —— 保留日期計算、乾跑不呼叫寫入/刪除、未設定確認變數時 --execute 被拒絕。
假 client、不連網、不碰真實資料庫。逐項 print ✅/❌。"""
import contextlib
import io
import os
import sys
import tempfile
from datetime import date
from pathlib import Path

import db_retention as dr

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append(bool(cond))
    print(("✅ " if cond else "❌ ") + name + (f"  [{detail}]" if detail and not cond else ""))


class FakeClient:
    """記錄所有呼叫。任何 delete / insert / upsert / update / rpc 都會被記下來。"""
    def __init__(self):
        self.calls = []

    def table(self, name):
        self.calls.append(("table", name))
        return FakeQuery(self)

    def rpc(self, *a, **k):
        self.calls.append(("rpc", a))
        raise AssertionError("不應呼叫 rpc")


class FakeQuery:
    def __init__(self, client):
        self.client = client

    def __getattr__(self, attr):
        if attr in ("delete", "insert", "upsert", "update", "rpc"):
            def _forbidden(*a, **k):
                self.client.calls.append((attr, a))
                raise AssertionError(f"乾跑不應呼叫 {attr}")
            return _forbidden
        def _chain(*a, **k):
            self.client.calls.append((attr, a))
            return self
        return _chain

    def execute(self):
        self.client.calls.append(("execute", None))
        class R:
            data = []
            count = 0
        return R()


def run_main(argv):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = dr.main(argv)
    return rc, buf.getvalue()


def main():
    # 1. 保留日期計算
    check("cutoff：保留 90 天、今天 2026-11-15 → 2026-08-17",
          dr.cutoff_for(90, date(2026, 11, 15)) == "2026-08-17", dr.cutoff_for(90, date(2026, 11, 15)))
    check("cutoff：保留 180 天 → 2026-05-19",
          dr.cutoff_for(180, date(2026, 11, 15)) == "2026-05-19", dr.cutoff_for(180, date(2026, 11, 15)))
    check("cutoff：保留天數低於下限 3 天時套用 7 天下限 → 2026-11-08",
          dr.cutoff_for(3, date(2026, 11, 15)) == "2026-11-08", dr.cutoff_for(3, date(2026, 11, 15)))
    try:
        dr.cutoff_for(None, date(2026, 11, 15))
        check("keep_days=None 拋出 ValueError", False)
    except ValueError:
        check("keep_days=None 拋出 ValueError", True)
    check("釋放估算：80MB、100 列、其中 50 列在截止日前 → 40MB",
          dr.estimate_freed_bytes(80 * 1024 * 1024, 100, 50) == 40 * 1024 * 1024)
    check("釋放估算：列數為 0 時回傳 0", dr.estimate_freed_bytes(100, 0, 0) == 0)

    # 2. 日期欄位與執行清單
    by = {s.name: s for s in dr.TABLES}
    unknown_ok = all(by[n].date_col is None for n in
                     ["backup_holiday_derived_20261014", "backup_holiday_intraday_bars_20261005",
                      "backup_holiday_twse_snapshot_20261005", "backup_instant_exit_artifacts_20261005",
                      "manual_feed_batches", "mops_financial_snapshot"])
    check("UNKNOWN 表（備份表、manual_feed_batches、mops）無日期欄位", unknown_ok)
    eligible = [s.name for s in dr.TABLES if dr.exec_eligible(s)[0]]
    check("執行清單只含 inst_holding、broker_flows、twse_market_snapshot、market_signal_snapshot",
          sorted(eligible) == sorted(["inst_holding", "broker_flows", "twse_market_snapshot",
                                      "market_signal_snapshot"]), str(eligible))
    check("backup_* 與 UNKNOWN 表不在執行清單", not any(n.startswith("backup_") or n == "manual_feed_batches"
                                                    for n in eligible))
    check("official_open_data 由 od_retention 負責，被排除",
          not dr.exec_eligible(by["official_open_data"])[0])
    check("每張表的日期欄位都附證據來源", all(s.source for s in dr.TABLES))

    # 3. 與既有 db_maintenance.py 保留設定一致
    import db_maintenance as dbm
    existing = {t: (c, d) for t, c, d in dbm.RETENTION}
    mismatch = [s.name for s in dr.TABLES if s.keep_days is not None and s.name in existing
                and existing[s.name] != (s.date_col, s.keep_days)]
    check("與 db_maintenance.RETENTION 的表名、欄位、天數一致", not mismatch, str(mismatch))

    # 4. 乾跑不呼叫任何寫入或刪除；_make_client 不會被呼叫
    orig_make = dr._make_client
    fake = FakeClient()
    dr._make_client = lambda: (_ for _ in ()).throw(AssertionError("乾跑不應建立 client"))
    try:
        rc, out = run_main(["--today", "2026-11-15"])
        check("乾跑回傳 0 且未建立 client", rc == 0)
        check("乾跑輸出含執行清單與 SQL 估算（字串，未執行）",
              "執行清單" in out and "SELECT count(*)" in out)
        check("乾跑輸出不含 DELETE 或 DROP 語句", "DELETE" not in out and "DROP" not in out)
    finally:
        dr._make_client = orig_make
    check("乾跑未對假 client 呼叫任何方法（零呼叫）", fake.calls == [])

    # 5. --execute 未設定確認環境變數 → 拒絕，且不建立 client
    os.environ.pop(dr.CONFIRM_ENV, None)
    dr._make_client = lambda: (_ for _ in ()).throw(AssertionError("拒絕路徑不應建立 client"))
    try:
        rc, out = run_main(["--execute", "--today", "2026-11-15"])
        check("未設定 RETENTION_CONFIRM 時 --execute 被拒絕（回傳 2）", rc == 2 and "拒絕" in out)
        os.environ[dr.CONFIRM_ENV] = "yes"
        rc, out = run_main(["--execute", "--today", "2026-11-15"])
        check("確認值錯誤時 --execute 被拒絕", rc == 2)
        os.environ[dr.CONFIRM_ENV] = dr.CONFIRM_VALUE
        with tempfile.TemporaryDirectory() as td:
            rc, out = run_main(["--execute", "--today", "2026-11-15", "--export-dir", td])
            check("確認值正確但缺 manifest 時 --execute 被拒絕且不刪除", rc == 2 and "manifest" in out)
    finally:
        os.environ.pop(dr.CONFIRM_ENV, None)
        dr._make_client = orig_make

    # 6. 統計檔估算輸出
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "stats.json"
        p.write_text('{"broker_flows": {"rows": 100, "rows_before_cutoff": 25, "bytes": 1048576}}', encoding="utf-8")
        items = dr.plan(date(2026, 11, 15), dr.load_stats(str(p)), ["broker_flows"])
        check("統計檔估算：broker_flows 1MB、25/100 列在截止前 → 約 0.25MB",
              items[0]["freed_bytes"] == 262144, str(items[0]["freed_bytes"]))

    passed = sum(RESULTS)
    print(f"\n結果：{passed}/{len(RESULTS)} 通過")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
