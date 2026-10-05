#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""probe_broker_flows5.py —— 第五輪：各家券商「DJ 系統」公開個股頁(zco 券商進出)能否從 Actions 用 requests 取得、
是否有驗證碼/擋爬、資料長相(前15大買/賣超分點)。只讀、低頻(請求間隔≥2.5秒)、帶可辨識 UA、不碰任何驗證碼。"""
import os, re, json, time
import requests
from supabase import create_client

R = {"kind": "probe_broker5", "ts": time.strftime("%Y-%m-%d %H:%M:%S"), "items": []}
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36 warroom-research/1.0"
H = {"User-Agent": UA, "Accept-Language": "zh-TW,zh;q=0.9"}


def add(name, **kw):
    R["items"].append({"name": name, **kw})


def txt(html):
    t = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
    t = re.sub(r"<[^>]+>", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def probe(label, url):
    try:
        r = requests.get(url, headers=H, timeout=25, allow_redirects=True)
        raw = r.content
        for enc in ("big5hkscs", "cp950", "utf-8"):
            try:
                html = raw.decode(enc)
                break
            except Exception:
                html = raw.decode("utf-8", errors="replace")
        low = html.lower()
        info = dict(status=r.status_code, final_url=r.url[:160], ctype=r.headers.get("content-type"), length=len(raw),
                    server=r.headers.get("server"),
                    cf_challenge=("just a moment" in low or "cf-chl" in low or "challenge-platform" in low),
                    captcha=("captcha" in low or "驗證碼" in html),
                    has_buy_sell=("買超券商" in html or "賣超券商" in html),
                    n_GenLink2stk=len(re.findall(r"GenLink2stk", html)),
                    n_rows_t3n1=len(re.findall(r't3n1', html)),
                    n_rows_t4t1=len(re.findall(r't4t1', html)),
                    title=(re.search(r"<title>(.*?)</title>", html, re.I | re.S) or [None, ""])[1].strip()[:80])
        t = txt(html)
        i = t.find("買超券商")
        info["text_head"] = t[:300]
        info["text_around_buy"] = t[max(0, i - 80): i + 900] if i >= 0 else ""
        # 日期欄位/下拉選單線索
        info["inputs"] = [m[:140] for m in re.findall(r"<(?:input|select)[^>]*>", html, re.I)[:12]]
        info["date_hints"] = re.findall(r"(20\d\d[-/]\d{1,2}[-/]\d{1,2})", html)[:6]
        add(label, url=url, **info)
        return html if r.status_code == 200 else None
    except Exception as e:
        add(label, url=url, error=f"{type(e).__name__}: {str(e)[:160]}")
        return None


SITES = [
    ("富邦", "https://fubon-ebrokerdj.fbs.com.tw"),
    ("元大", "https://jdata.yuanta.com.tw"),
    ("兆豐/Masterlink", "https://asp.masterlink.com.tw"),
    ("玉山", "https://sjmain.esunsec.com.tw"),
    ("永豐/MoneyDJ", "https://5850web.moneydj.com"),
    ("凱基", "https://kgieworld.moneydj.com"),
    ("康和", "https://concords.moneydj.com"),
    ("日盛", "https://jsjustweb.jihsun.com.tw"),
]
first_ok = None
for name, base in SITES:
    html = probe(f"{name} zco 2330", f"{base}/z/zc/zco/zco.djhtm?a=2330")
    time.sleep(2.5)
    if html and "買超券商" in html and not first_ok:
        first_ok = (name, base, html)

# 對第一個可用站台：換股票/櫃買股/日期區間，確認欄位與是否可查歷史日
if first_ok:
    name, base, html = first_ok
    for code in ("6488", "3293"):
        probe(f"{name} zco {code}", f"{base}/z/zc/zco/zco.djhtm?a={code}")
        time.sleep(2.5)
    for qs in ("a=2330&e=2026-10-2&f=2026-10-2", "a=2330&e=2026-9-29&f=2026-10-2", "a=2330&b=1&c=B&d=1"):
        probe(f"{name} zco 2330 [{qs}]", f"{base}/z/zc/zco/zco.djhtm?{qs}")
        time.sleep(2.5)
    # 單一分點歷史(zco0 系列)與分點進出明細頁是否可用
    for path in ("/z/zg/zgb/zgb0.djhtm?a=9600&b=9600", "/z/zc/zco/zco0/zco0.djhtm?a=2330&b=9600&BHID=9600"):
        probe(f"{name} {path[:40]}", base + path)
        time.sleep(2.5)

sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
sb.table("ui_selftest_reports").insert({"run_id": os.environ.get("GITHUB_RUN_ID", ""), "summary": "probe_broker5", "report": R}).execute()
print("已上傳", len(R["items"]))
