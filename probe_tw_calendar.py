#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""唯讀：抓證交所休市日曆與 FinMind 交易日曆，結果存進 Supabase 私有表供查看（Actions 日誌無法由沙盒讀取）。"""
import os, json, requests
from supabase import create_client

out = {"kind": "tw_calendar_probe"}
try:
    r = requests.get("https://www.twse.com.tw/rwd/zh/holidaySchedule/holidaySchedule", params={"response": "json", "queryYear": 2026},
                     headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
    d = r.json()
    out["twse"] = {"status": r.status_code, "stat": d.get("stat"), "fields": d.get("fields"), "data": d.get("data")}
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
