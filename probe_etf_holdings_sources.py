#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""probe_etf_holdings_sources.py —— 第五輪：元大持股頁「展開」按鈕的實際 DOM、點擊後的網路請求與文字；另看「申購買回清單／檔案下載」分頁。只讀。"""
import os
import re


def main():
    from playwright.sync_api import sync_playwright
    os.makedirs("probe_out", exist_ok=True)
    L = ["# 元大持股 第五輪探測", ""]
    with sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(locale="zh-TW", user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36")
        page = ctx.new_page()
        reqs = []
        page.on("request", lambda r: reqs.append((r.method, r.url[:200])) if r.resource_type in ("xhr", "fetch", "document") else None)
        page.goto("https://www.yuantaetfs.com/product/detail/0056/ratio", wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(4000)

        def stock_part():
            t = page.inner_text("body")
            i = t.find("基金權重-股票")
            j = t.find("基金權重-期貨")
            return re.sub(r"\s+", " ", t[i:j if j > i else i + 3000])

        before = stock_part()
        L.append(f"## 點擊前 股票區長度 {len(before)}\n{before[:500]}")
        info = page.eval_on_selector_all(
            "*", "els=>els.filter(e=>e.children.length===0 && e.innerText && e.innerText.trim()==='展開').map(e=>({tag:e.tagName,cls:e.className,html:e.outerHTML.slice(0,200),parent:e.parentElement?e.parentElement.outerHTML.slice(0,300):''}))")
        L.append("## 「展開」葉節點\n" + "\n".join(str(x) for x in info))
        n0 = len(reqs)
        try:
            page.get_by_text("展開", exact=True).first.click(timeout=5000)
            page.wait_for_timeout(3000)
            L.append("## 點擊成功")
        except Exception as e:
            L.append(f"## 點擊失敗 {type(e).__name__}: {str(e)[:200]}")
        after = stock_part()
        L.append(f"## 點擊後 股票區長度 {len(after)}\n{after[:1500]}")
        L.append("## 點擊後新請求\n" + "\n".join(f"{m} {u}" for m, u in reqs[n0:]))
        L.append("## 頁面全部 xhr/fetch 請求\n" + "\n".join(f"{m} {u}" for m, u in reqs[:60]))
        for tab in ("申購買回清單", "檔案下載"):
            try:
                n1 = len(reqs)
                page.get_by_text(tab, exact=True).first.click(timeout=5000)
                page.wait_for_timeout(4000)
                t = re.sub(r"\s+", " ", page.inner_text("body"))
                links = page.eval_on_selector_all("a", "els=>els.map(e=>[e.innerText.trim().slice(0,40),e.href]).filter(x=>/xls|csv|pdf|download|file/i.test(x[1]))")
                L.append(f"## 分頁 {tab}\n文字(前800): {t[:800]}\n連結: {links[:20]}\n新請求: " + "; ".join(f"{m} {u}" for m, u in reqs[n1:][:20]))
            except Exception as e:
                L.append(f"## 分頁 {tab} 失敗 {type(e).__name__}: {str(e)[:150]}")
        b.close()
    open("probe_out/probe_report.md", "w", encoding="utf-8").write("\n".join(L))
    print("\n".join(L)[:3000])


if __name__ == "__main__":
    main()
