#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""唯讀：抓證交所休市日曆與 FinMind 交易日曆，結果存進 Supabase 私有表供查看（Actions 日誌無法由沙盒讀取）。"""
import os, json, requests
from supabase import create_client

out = {"kind": "tw_calendar_probe"}
try:
    # 【2026-10-12】改用 OpenAPI（www.twse.com.tw 直連違反證交所使用條款第6條）
    import tw_calendar as C
    r = requests.get(C.OPENAPI_HOLIDAY_URL, headers={"Accept": "application/json"}, timeout=30)
    d = r.json()
    closed, n = C.parse_closed(d, 2026)
    out["twse_openapi"] = {"status": r.status_code, "rows": n, "closed_2026": sorted(closed), "data": d}
except Exception as e:
    out["twse"] = f"失敗 {type(e).__name__}: {e}"
try:
    r = requests.get("https://api.finmindtrade.com/api/v4/data", params={"dataset": "TaiwanStockTradingDate", "start_date": "2026-01-01", "token": os.environ.get("FM", "")}, timeout=30)
    ds = [x["date"] for x in r.json().get("data", [])]
    out["finmind"] = {"n": len(ds), "first": ds[:1], "last": ds[-1:], "since_0920": [x for x in ds if x >= "2026-09-20"]}
except Exception as e:
    out["finmind"] = f"失敗 {type(e).__name__}: {e}"
sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
sb.table("ui_selftest_reports").insert({"run_id": os.environ.get("GITHUB_RUN_ID", ""), "summary": "tw_calendar_probe", "report": out}).execute()
print("已存入")
