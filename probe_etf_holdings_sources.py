#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""probe_etf_holdings_sources.py —— 第二輪探測：MoneyDJ 成分股/規模表格結構、TWSE swagger 的 ETF 相關端點、復華 Excel 下載、元大下載頁。只讀。"""
import os
import re
import sys
import io

import requests

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36",
      "Accept-Language": "zh-TW,zh;q=0.9"}


def snip(t, n=400):
    return re.sub(r"\s+", " ", t)[:n]


def get(url, **kw):
    return requests.get(url, headers=UA, timeout=40, **kw)


def main():
    os.makedirs("probe_out", exist_ok=True)
    L = ["# ETF 來源探測（第二輪）", ""]
    # 1 TWSE swagger：ETF/基金相關端點
    try:
        sw = get("https://openapi.twse.com.tw/v1/swagger.json").json()
        L.append("## TWSE OpenAPI 與 ETF/基金/受益 相關端點")
        for path, v in sw.get("paths", {}).items():
            s = (v.get("get", {}).get("summary", "") + " " + v.get("get", {}).get("description", ""))
            if re.search(r"ETF|基金|受益|規模|申購|買回|成分", s + path):
                L.append(f"- {path}｜{snip(s, 120)}")
    except Exception as e:
        L.append(f"swagger 失敗 {e}")
    # 2 MoneyDJ 成分股頁與基本資料頁
    try:
        from bs4 import BeautifulSoup
    except Exception:
        BeautifulSoup = None
    for etf in ("0056", "00878", "00919", "00929", "00940", "00713", "006208"):
        for page in ("basic0007a", "basic0001"):
            url = f"https://www.moneydj.com/etf/x/basic/{page}.xdjhtm?etfid={etf}.tw"
            L.append(f"## MoneyDJ {page} {etf}\n- {url}")
            try:
                r = get(url)
                L.append(f"- HTTP {r.status_code} {len(r.content)} bytes")
                if BeautifulSoup and r.ok:
                    soup = BeautifulSoup(r.text, "html.parser")
                    txt = soup.get_text(" ", strip=True)
                    m = re.search(r"資料(?:日期|月份)[:：]?\s*([0-9/\-]+)", txt)
                    L.append(f"- 資料日期：{m.group(1) if m else '?'}")
                    if page == "basic0001":
                        for kw in ("基金規模", "成立日期", "經理費", "追蹤指數", "發行單位", "受益人"):
                            mm = re.search(kw + r"[^\d\-]{0,12}([^\s]{1,20}\s?[^\s]{0,10})", txt)
                            L.append(f"  - {kw}: {mm.group(0)[:50] if mm else '無'}")
                    else:
                        tabs = soup.find_all("table")
                        L.append(f"- table 數 {len(tabs)}")
                        for t in tabs:
                            rows = [[c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])] for tr in t.find_all("tr")]
                            if len(rows) > 10:
                                L.append(f"  - 表格 {len(rows)} 列；前6列：{rows[:6]}")
                                break
            except Exception as e:
                L.append(f"- 失敗 {type(e).__name__}: {e}")
            L.append("")
    # 3 復華 Excel
    L.append("## 復華 Excel 下載")
    for u in ("https://www.fhtrust.com.tw/api/assetsExcel/ETF23/20261002",):
        try:
            r = get(u)
            L.append(f"- {u} → HTTP {r.status_code}｜{r.headers.get('content-type')}｜{len(r.content)} bytes｜{r.headers.get('content-disposition')}")
            if r.ok:
                try:
                    import pandas as pd
                    df = pd.read_excel(io.BytesIO(r.content), header=None)
                    L.append(f"  - 形狀 {df.shape}\n```\n{df.head(15).to_string()}\n```")
                except Exception as e:
                    L.append(f"  - 讀 Excel 失敗 {e}；前200字：{snip(r.text, 200)}")
        except Exception as e:
            L.append(f"- 失敗 {e}")
    try:
        r = get("https://www.fhtrust.com.tw/ETF/etf_detail/ETF23")
        ids = sorted(set(re.findall(r"ETF\d{2,3}", r.text)))
        L.append(f"- 復華頁面內的 ETF id：{ids[:40]}")
        idx = r.text.find("00929")
        L.append(f"- 00929 附近：{snip(r.text[max(0, idx-200):idx+200], 400)}")
    except Exception as e:
        L.append(f"- 失敗 {e}")
    # 4 元大下載頁 / PCF 頁（SPA，改用瀏覽器）
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            b = p.chromium.launch()
            for name, url in (("元大 0056 下載頁", "https://www.yuantaetfs.com/product/detail/0056/download"),
                              ("元大 0056 PCF", "https://www.yuantaetfs.com/tradeInfo/pcf/0056"),
                              ("元大 0056 持股(點開)", "https://www.yuantaetfs.com/product/detail/0056/ratio"),
                              ("富邦 PCF", "https://websys.fsit.com.tw/FubonETF/Trade/Pcf.aspx"),
                              ("統一 PCF", "https://www.ezmoney.com.tw/ETF/Transaction/PCF")):
                L.append(f"## {name}\n- {url}")
                ctx = b.new_context(user_agent=UA["User-Agent"], locale="zh-TW")
                page = ctx.new_page()
                hits = []
                page.on("response", lambda resp, h=hits: h.append((resp.status, resp.headers.get("content-type", ""), resp.url))
                        if ("json" in resp.headers.get("content-type", "") or "excel" in resp.headers.get("content-type", "") or "spreadsheet" in resp.headers.get("content-type", "") or "csv" in resp.headers.get("content-type", "")) else None)
                try:
                    page.goto(url, wait_until="networkidle", timeout=60000)
                    page.wait_for_timeout(3000)
                    if "ratio" in url:
                        for sel in ("text=展開", "text=全部", "text=持股明細", "text=更多"):
                            try:
                                page.click(sel, timeout=2000)
                                page.wait_for_timeout(2500)
                            except Exception:
                                pass
                    tbl = page.eval_on_selector_all("table tr", "els=>els.slice(0,8).map(e=>e.innerText.replace(/\\s+/g,' ').slice(0,120))")
                    L.append(f"- 表格前列：{tbl}")
                    links = page.eval_on_selector_all("a[href]", "els=>els.map(e=>[e.innerText.trim().slice(0,30),e.href])")
                    dl = [x for x in links if re.search(r"xls|csv|download|匯出|下載|Excel|api", x[0] + x[1], re.I)]
                    L.append(f"- 下載連結：{dl[:12]}")
                    txt = snip(page.inner_text("body"), 700)
                    L.append(f"- 文字：`{txt}`")
                except Exception as e:
                    L.append(f"- 失敗 {type(e).__name__}: {str(e)[:150]}")
                for st, ct, u in hits[:25]:
                    if "etfapi" in u or "Json" in u or "api" in u.lower() or "xls" in u.lower():
                        L.append(f"  - {st} {ct[:25]} {u[:230]}")
                ctx.close()
            b.close()
    except Exception as e:
        L.append(f"playwright 失敗 {e}")
    open("probe_out/probe_report.md", "w", encoding="utf-8").write("\n".join(L))
    print("\n".join(L)[:2500])


if __name__ == "__main__":
    main()
