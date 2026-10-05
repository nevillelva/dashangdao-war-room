#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""probe_broker_flows4.py —— 第四輪：真瀏覽器能否載入 HiStock 分點頁；TPEx 券商買賣日報表的查詢方式。只讀。"""
import os, re, json, time
import requests
from supabase import create_client

R = {"kind": "probe_broker4", "ts": time.strftime("%Y-%m-%d %H:%M:%S"), "items": []}


def add(name, **kw):
    R["items"].append({"name": name, **kw})


# A) HiStock 真瀏覽器
try:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(locale="zh-TW", user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36")
        pg = ctx.new_page()
        t0 = time.time()
        try:
            pg.goto("https://histock.tw/stock/branch.aspx?no=2330", wait_until="domcontentloaded", timeout=45000)
            pg.wait_for_timeout(12000)
        except Exception as e:
            add("HiStock 瀏覽器載入", error=f"{type(e).__name__}: {str(e)[:150]}")
        txt = re.sub(r"\s+", " ", pg.inner_text("body"))[:400]
        add("HiStock 瀏覽器", title=pg.title(), url=pg.url, sec=round(time.time() - t0, 1), body=txt, has_table=bool(pg.query_selector("table")))
        b.close()
except Exception as e:
    add("HiStock 瀏覽器", error=f"{type(e).__name__}: {str(e)[:200]}")

# B) TPEx 券商買賣日報表
H = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36"}
try:
    r = requests.get("https://www.tpex.org.tw/web/stock/aftertrading/broker_trading/brokerBS.php?l=zh-tw", headers=H, timeout=25)
    r.encoding = "utf-8"
    t = r.text
    forms = re.findall(r"<form[^>]*>", t, re.I)
    inputs = re.findall(r"<(?:input|select)[^>]*>", t, re.I)
    scripts = re.findall(r"(?:src|href)=[\"']([^\"']+\.(?:js|php)[^\"']*)", t)
    add("TPEx 頁面結構", forms=forms[:3], inputs=[i[:140] for i in inputs[:15]], scripts=scripts[:15], has_captcha=("captcha" in t.lower()), text=re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", t))[:500])
except Exception as e:
    add("TPEx 頁面結構", error=str(e)[:200])

for u in [
    "https://www.tpex.org.tw/web/stock/aftertrading/broker_trading/brokerBS_result.php?l=zh-tw&stk_code=6488&charset=UTF-8",
    "https://www.tpex.org.tw/www/zh-tw/afterTrading/brokerBS?code=6488&date=2026%2F10%2F02&response=json",
    "https://www.tpex.org.tw/openapi/v1/tpex_broker_trading",
]:
    try:
        r = requests.get(u, headers=H, timeout=25)
        add("TPEx 端點 " + u[-60:], status=r.status_code, ctype=r.headers.get("content-type"), length=len(r.text), head=r.text[:250].replace("\n", " "))
    except Exception as e:
        add("TPEx 端點 " + u[-60:], error=str(e)[:150])

sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
sb.table("ui_selftest_reports").insert({"run_id": os.environ.get("GITHUB_RUN_ID", ""), "summary": "probe_broker4", "report": R}).execute()
print("已上傳", len(R["items"]))
