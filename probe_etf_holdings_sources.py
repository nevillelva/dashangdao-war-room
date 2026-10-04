#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
probe_etf_holdings_sources.py —— 探測 ETF 持股/規模/受益人數的可抓取來源（只讀、跑在 Actions；沙箱連不到這些網站）
輸出：probe_out/probe_report.md（每個來源的 HTTP 狀態、內容類型、大小、開頭片段、瀏覽器載入時攔到的 JSON/XHR 與下載連結）
"""
import os
import re
import json
import sys

import requests

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36",
      "Accept-Language": "zh-TW,zh;q=0.9"}
PLAIN = [
    ("TWSE OpenAPI 基金基本資料", "https://openapi.twse.com.tw/v1/opendata/t187ap47_L"),
    ("TWSE OpenAPI swagger", "https://openapi.twse.com.tw/v1/swagger.json"),
    ("MoneyDJ 0056 成分股", "https://www.moneydj.com/etf/x/basic/basic0007a.xdjhtm?etfid=0056.tw"),
    ("MoneyDJ 00878 成分股", "https://www.moneydj.com/etf/x/basic/basic0007a.xdjhtm?etfid=00878.tw"),
    ("CMoney 00878 成分股", "https://www.cmoney.tw/forum/stock/00878?s=constituent"),
    ("玩股網 受益人數排行", "https://www.wantgoo.com/stock/etf/ranking/shareholders"),
    ("TWSE ETF e添富 00878", "https://www.twse.com.tw/zh/ETFortune/etfInfo/00878"),
]
BROWSER = [
    ("元大 0056 持股", "https://www.yuantaetfs.com/product/detail/0056/ratio"),
    ("元大 00713 持股", "https://www.yuantaetfs.com/product/detail/00713/ratio"),
    ("元大 00940 持股", "https://www.yuantaetfs.com/product/detail/00940/ratio"),
    ("國泰 00878 持股", "https://www.cathaysite.com.tw/ETF/detail/ECN?tab=etf3"),
    ("群益 ETF 清單", "https://www.capitalfund.com.tw/etf/product/list"),
    ("復華 00929", "https://www.fhtrust.com.tw/ETF/etf_detail/ETF23"),
    ("富邦 006208", "https://websys.fsit.com.tw/FubonETF/Trade/Assets.aspx"),
    ("統一 00939", "https://www.ezmoney.com.tw/ETF/Fund/Info?fundCode=49YTW"),
]


def snip(t, n=400):
    return re.sub(r"\s+", " ", t)[:n]


def plain(L):
    for name, url in PLAIN:
        L.append(f"### {name}\n- {url}")
        try:
            r = requests.get(url, headers=UA, timeout=40)
            L.append(f"- HTTP {r.status_code}｜{r.headers.get('content-type')}｜{len(r.content)} bytes")
            L.append(f"- 開頭：`{snip(r.text)}`")
            if "openapi" in url and url.endswith("t187ap47_L") and r.ok:
                try:
                    j = r.json()
                    syms = [x for x in j if str(x.get('公司代號', x.get('證券代號', ''))).startswith("00")]
                    L.append(f"- JSON 筆數 {len(j)}；欄位 {list(j[0].keys()) if j else []}；00 開頭 {len(syms)} 筆")
                except Exception as e:
                    L.append(f"- JSON 解析失敗 {e}")
        except Exception as e:
            L.append(f"- 失敗：{type(e).__name__}: {e}")
        L.append("")


def browser(L):
    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:
        L.append(f"playwright 不可用：{e}")
        return
    with sync_playwright() as p:
        b = p.chromium.launch()
        for name, url in BROWSER:
            L.append(f"### {name}\n- {url}")
            ctx = b.new_context(user_agent=UA["User-Agent"], locale="zh-TW")
            page = ctx.new_page()
            hits = []

            def on_resp(resp):
                try:
                    ct = resp.headers.get("content-type", "")
                    if resp.request.resource_type in ("xhr", "fetch") or "json" in ct or "excel" in ct or "csv" in ct or "spreadsheet" in ct:
                        body = ""
                        if "json" in ct or "text" in ct:
                            try:
                                body = snip(resp.text(), 300)
                            except Exception:
                                body = ""
                        hits.append((resp.status, ct, resp.url, body))
                except Exception:
                    pass
            page.on("response", on_resp)
            try:
                page.goto(url, wait_until="networkidle", timeout=60000)
                page.wait_for_timeout(4000)
                L.append(f"- 標題：{page.title()}｜最終網址 {page.url}")
                links = page.eval_on_selector_all("a[href]", "els=>els.map(e=>[e.innerText.trim().slice(0,30),e.href])")
                dl = [x for x in links if re.search(r"xls|csv|download|匯出|下載|pcf|portfolio|ratio", (x[0] + x[1]), re.I)]
                L.append(f"- 下載/持股相關連結 {len(dl)} 個：" + "；".join(f"{t}→{h}" for t, h in dl[:12]))
                txt = snip(page.inner_text("body"), 500)
                L.append(f"- 頁面文字：`{txt}`")
            except Exception as e:
                L.append(f"- 載入失敗：{type(e).__name__}: {str(e)[:200]}")
            for st, ct, u, body in hits[:25]:
                L.append(f"  - XHR {st} {ct[:30]} {u[:200]}" + (f"\n    `{body}`" if body else ""))
            L.append("")
            ctx.close()
        b.close()


def main():
    os.makedirs("probe_out", exist_ok=True)
    L = ["# ETF 持股/規模/受益人數 來源探測", ""]
    plain(L)
    browser(L)
    open("probe_out/probe_report.md", "w", encoding="utf-8").write("\n".join(L))
    print("\n".join(L)[:3000])


if __name__ == "__main__":
    main()
