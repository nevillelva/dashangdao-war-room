#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 ui_selftest_out/report.json + report.md 存進 Supabase（私有表 ui_selftest_reports）。
公開倉庫的 Actions 日誌/artifact 任何人看得到，所以報告內容不放日誌，只存資料庫。"""
import os, json
from supabase import create_client

sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
rep = json.load(open("ui_selftest_out/report.json", encoding="utf-8"))
md = open("ui_selftest_out/report.md", encoding="utf-8").read()
rep["markdown"] = md
sb.table("ui_selftest_reports").insert({"run_id": os.environ.get("GITHUB_RUN_ID", ""), "summary": md[:300], "report": rep}).execute()
n_exc = sum(1 for p in rep.get("pages", []) for s in p["steps"] for _ in s["exceptions"])
print(f"已上傳報告：頁數 {len(rep.get('pages', []))}、例外 {n_exc}、攔截寫入 {len(rep.get('blocked_writes', []))}")
