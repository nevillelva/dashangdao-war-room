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


def _fresh():
    from fake_supabase_for_tests import FakeDB, FakeStorage
    old_tg = [{"created_at": "2026-08-01T00:00:00+00:00", "id": 1, "text": "a"},
              {"created_at": "2026-08-02T00:00:00+00:00", "id": 2, "text": "b"},
              {"created_at": "2026-11-10T00:00:00+00:00", "id": 3, "text": "c"}]
    bk = [{"id": i, "symbol": "x", "v": i} for i in range(5)]
    data = {"telegram_inbox": old_tg, "telegram_inbox_images": [], "manual_feed_batches": [],
            "backup_instant_exit_artifacts_20261005": bk,
            "inst_holding": [{"date": "2026-01-01", "symbol": "2330"}, {"date": "2026-11-01", "symbol": "2330"}]}

    def _drop(params):
        data.pop(params["p_table"])
    return FakeDB(data, {dr.DROP_RPC: _drop}), FakeStorage(), data


def exec_tests():
    T = date(2026, 11, 15)
    only = ["telegram_inbox", "backup_instant_exit_artifacts_20261005", "inst_holding"]

    db, st, data = _fresh()
    rc = _quiet(lambda: dr.run_execute(T, only, client=db, store=st))
    check("--execute 成功路徑：舊列刪除、新列保留", [r["id"] for r in data["telegram_inbox"]] == [3]
          and [r["date"] for r in data["inst_holding"]] == ["2026-11-01"] and rc == 0)
    check("--execute 成功路徑：備份表經 RPC DROP，且先有匯出（manifest 5 列）",
          "backup_instant_exit_artifacts_20261005" not in data and db.rpc_calls[0][0] == dr.DROP_RPC
          and any(m["rows"] == 5 and m["table"] == "backup_instant_exit_artifacts_20261005" for m in st.manifests().values()))

    for label, setup in [("上傳失敗", lambda st: setattr(st, "fail_put", True)),
                         ("回讀校驗失敗", lambda st: setattr(st, "corrupt_get", True)),
                         ("回讀不到 manifest", lambda st: setattr(st, "hide_manifest", True))]:
        db, st, data = _fresh()
        setup(st)
        rc = _quiet(lambda: dr.run_execute(T, only, client=db, store=st))
        check(f"匯出{label}：一列都不刪、不 DROP、回傳非 0",
              len(data["telegram_inbox"]) == 3 and len(data["inst_holding"]) == 2
              and "backup_instant_exit_artifacts_20261005" in data and db.deletes == [] and db.rpc_calls == [] and rc == 1)

    db, st, data = _fresh()
    db.rpc_handlers = {}
    rc = _quiet(lambda: dr.run_execute(T, ["backup_instant_exit_artifacts_20261005"], client=db, store=st))
    check("未套用 DROP RPC：已匯出但表仍在、回傳非 0", "backup_instant_exit_artifacts_20261005" in data and rc == 1)

    db, st, data = _fresh()
    real = dr.db_archive.archive_before
    dr.db_archive.archive_before = lambda *a, **k: {**real(*a, **k), "rows": 999}
    try:
        rc = _quiet(lambda: dr.run_execute(T, ["telegram_inbox"], client=db, store=st))
    finally:
        dr.db_archive.archive_before = real
    check("刪前對帳不符（匯出列數 != 現在待刪列數）：不刪", len(data["telegram_inbox"]) == 3 and db.deletes == [] and rc == 1)

    db, st, data = _fresh()
    rc = _quiet(lambda: dr.run_execute(date(2026, 10, 20), ["backup_instant_exit_artifacts_20261005"], client=db, store=st))
    check("備份表未滿 30 天：不匯出、不 DROP", st.objects == {} and db.rpc_calls == [] and rc == 0)

    # od 瘦身報告：只印報告，不連線
    dr._make_client = lambda: (_ for _ in ()).throw(AssertionError("瘦身報告不應連線"))
    rc, out = run_main(["--od-slim-report"])
    check("--od-slim-report 只出報告：含 source_url 去重與 VACUUM FULL 提醒，無 DELETE/DROP/UPDATE 語句",
          rc == 0 and "source_url" in out and "VACUUM FULL" in out
          and not any(w in out for w in ("DELETE", "DROP", "UPDATE", "ALTER")), out)
    lines = dr.od_slim_report()
    check("瘦身報告合計上限 > 0 且小於資料庫 5%", 0 < sum(int(dr.OD_SLIM_BASELINE[k]) for _, k, _ in dr.OD_SLIM_CANDIDATES) < 25 * 1024 * 1024)
    dry_out = run_main(["--today", "2026-11-15"])[1]
    check("預設乾跑也附瘦身報告", "official_open_data 欄位瘦身乾跑報告" in dry_out)


def _quiet(fn):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        return fn()


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
    backups = ["backup_holiday_derived_20261014", "backup_holiday_intraday_bars_20261005",
               "backup_holiday_twse_snapshot_20261005", "backup_instant_exit_artifacts_20261005"]
    check("備份表與 mops 無日期欄位（備份表走 export_drop）",
          all(by[n].date_col is None for n in backups + ["mops_financial_snapshot"])
          and all(by[n].action == "export_drop" for n in backups))
    check("manual_feed_batches／telegram 兩表有 created_at 與 30 天保留（唯讀 SQL 證據）",
          all(by[n].date_col == "created_at" and by[n].keep_days == 30
              for n in ["manual_feed_batches", "telegram_inbox", "telegram_inbox_images"]))
    t0 = date(2026, 11, 15)
    eligible = [s.name for s in dr.TABLES if dr.exec_eligible(s, t0)[0]]
    check("2026-11-15 執行清單 = 4 張熱表 + 4 備份表 + 3 移除功能表",
          sorted(eligible) == sorted(["inst_holding", "broker_flows", "twse_market_snapshot",
                                      "market_signal_snapshot"] + backups
                                     + ["telegram_inbox", "telegram_inbox_images", "manual_feed_batches"]),
          str(eligible))
    check("mops（無日期欄位）與 etf_price_daily／big_holder_weekly（待決定）不在執行清單",
          not any(n in eligible for n in ["mops_financial_snapshot", "etf_price_daily", "big_holder_weekly"]))
    young = dr.exec_eligible(by["backup_holiday_derived_20261014"], date(2026, 10, 20))
    check("備份表未滿 30 天不可處理（2026-10-20 時 20261014 僅 6 天）", young[0] is False and "未滿" in young[1], str(young))
    check("表名無日期的 backup_ 表不處理",
          dr.backup_age_days("backup_foo", t0) is None and dr.backup_age_days("backup_x_20261340", t0) is None)
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
        for k in ("SUPABASE_URL", "SUPABASE_KEY", "SUPABASE_STORAGE_KEY"):
            os.environ.pop(k, None)
        rc, out = run_main(["--execute", "--today", "2026-11-15"])
        check("確認值正確但 Storage 設定缺漏 → --execute 拒絕（回傳 2）、未建立 client", rc == 2 and "Storage" in out, out)
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

    exec_tests()
    passed = sum(RESULTS)
    print(f"\n結果：{passed}/{len(RESULTS)} 通過")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
