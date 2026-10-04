#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""probe_etf_holdings_sources.py —— 第三輪探測：MoneyDJ 持股表/基本資料頁結構(正確編碼)與復華 00929 Excel。只讀。"""
import os
import re
import io

import requests

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36",
      "Accept-Language": "zh-TW,zh;q=0.9"}


def get(url):
    r = requests.get(url, headers=UA, timeout=40)
    r.encoding = "utf-8"
    return r


def main():
    from bs4 import BeautifulSoup
    os.makedirs("probe_out", exist_ok=True)
    L = ["# ETF 來源探測（第三輪）", ""]
    for etf in ("0056", "00878", "006208"):
        for page in ("basic0007a", "basic0001", "basic0003"):
            url = f"https://www.moneydj.com/etf/x/basic/{page}.xdjhtm?etfid={etf}.tw"
            L.append(f"## MoneyDJ {page} {etf}")
            try:
                r = get(url)
                soup = BeautifulSoup(r.text, "html.parser")
                txt = soup.get_text(" ", strip=True)
                L.append(f"- HTTP {r.status_code}，文字長度 {len(txt)}")
                for kw in ("資料日期", "資料月份", "規模", "成立日", "經理費", "追蹤", "發行", "受益", "週轉", "淨值"):
                    for m in list(re.finditer(kw, txt))[:2]:
                        L.append(f"  - {kw}: …{txt[max(0, m.start()-15):m.start()+45]}…")
                for ti, t in enumerate(soup.find_all("table")):
                    rows = [[c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])] for tr in t.find_all("tr")]
                    rows = [x for x in rows if any(x)]
                    if rows:
                        L.append(f"  - table#{ti} {len(rows)}列：{rows[:3]}")
            except Exception as e:
                L.append(f"- 失敗 {type(e).__name__}: {e}")
            L.append("")
    # 復華 00929 (ETF21)
    for ymd in ("20261002", "20261001"):
        u = f"https://www.fhtrust.com.tw/api/assetsExcel/ETF21/{ymd}"
        try:
            r = requests.get(u, headers=UA, timeout=40)
            L.append(f"## 復華 ETF21 {ymd}: HTTP {r.status_code} {len(r.content)} bytes {r.headers.get('content-type')}")
            if r.ok:
                import pandas as pd
                df = pd.read_excel(io.BytesIO(r.content), header=None)
                L.append(f"```\n{df.head(16).to_string()}\n```")
                break
        except Exception as e:
            L.append(f"失敗 {e}")
    open("probe_out/probe_report.md", "w", encoding="utf-8").write("\n".join(L))
    print("\n".join(L)[:2000])


if __name__ == "__main__":
    main()
