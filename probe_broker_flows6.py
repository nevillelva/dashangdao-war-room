#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""probe_broker_flows6.py —— 第六輪：把 DJ zco 公開頁的『原始 HTML』存進私有表，供寫解析器與單元測試的固定樣本用。只讀、低頻。"""
import os, time
import requests
from supabase import create_client

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36 warroom-research/1.0"
R = {"kind": "probe_broker6", "ts": time.strftime("%Y-%m-%d %H:%M:%S"), "pages": {}}
for key, url in [
    ("fubon_2330", "https://fubon-ebrokerdj.fbs.com.tw/z/zc/zco/zco.djhtm?a=2330"),
    ("fubon_6488", "https://fubon-ebrokerdj.fbs.com.tw/z/zc/zco/zco.djhtm?a=6488"),
    ("yuanta_2330", "https://jdata.yuanta.com.tw/z/zc/zco/zco.djhtm?a=2330"),
    ("kgi_2330_5d", "https://kgieworld.moneydj.com/z/zc/zco/zco.djhtm?a=2330&e=2026-9-29&f=2026-10-2"),
    ("fubon_2330_br", "https://fubon-ebrokerdj.fbs.com.tw/z/zc/zco/zco0/zco0.djhtm?a=2330&b=9600"),
]:
    try:
        r = requests.get(url, headers={"User-Agent": UA, "Accept-Language": "zh-TW"}, timeout=25)
        raw = r.content
        try:
            html = raw.decode("big5hkscs")
        except Exception:
            html = raw.decode("cp950", errors="replace")
        R["pages"][key] = {"url": url, "status": r.status_code, "encoding_guess": r.encoding, "html": html}
    except Exception as e:
        R["pages"][key] = {"url": url, "error": str(e)[:200]}
    time.sleep(3)
sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
sb.table("ui_selftest_reports").insert({"run_id": os.environ.get("GITHUB_RUN_ID", ""), "summary": "probe_broker6", "report": R}).execute()
print("已上傳", len(R["pages"]))
