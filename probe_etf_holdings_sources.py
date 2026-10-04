#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""probe_etf_holdings_sources.py —— 第四輪：找出元大持股頁背後的 API（攔截含成分股數量的回應），並列出頁面可點按鈕。只讀。"""
import os
import re
import json


def main():
    from playwright.sync_api import sync_playwright
    os.makedirs("probe_out", exist_ok=True)
    L = ["# 元大持股 API 探測", ""]
    with sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(locale="zh-TW", user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36")
        page = ctx.new_page()
        hits = []

        def on_resp(resp):
            try:
                if "etfapi" not in resp.url and "api" not in resp.url.lower():
                    return
                body = resp.text()
                if re.search(r"1303|2408|台積|南亞", body):
                    req = resp.request
                    hits.append({"url": resp.url, "method": req.method, "post": (req.post_data or "")[:500], "len": len(body), "body": body[:700]})
            except Exception:
                pass
        page.on("response", on_resp)
        page.goto("https://www.yuantaetfs.com/product/detail/0056/ratio", wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(5000)
        L.append(f"## 命中 {len(hits)} 個含成分股的 API 回應")
        for h in hits:
            L.append(f"- {h['method']} {h['url']}\n  - post: {h['post']}\n  - 長度 {h['len']}\n  - body: `{h['body']}`")
        txt = page.inner_text("body")
        i = txt.find("基金權重-股票")
        L.append("## 頁面「基金權重-股票」之後的文字(前1500字)\n" + re.sub(r"\s+", " ", txt[i:i + 1500]))
        btn = page.eval_on_selector_all("button, [role=button], a, .more, .btn", "els=>els.map(e=>e.innerText.trim().slice(0,20)).filter(x=>x)")
        L.append("## 可點元素文字\n" + str(sorted(set(btn))[:80]))
        b.close()
    open("probe_out/probe_report.md", "w", encoding="utf-8").write("\n".join(L))
    print("\n".join(L)[:3000])


if __name__ == "__main__":
    main()
