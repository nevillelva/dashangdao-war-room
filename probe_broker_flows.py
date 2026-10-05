#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""probe_broker_flows.py —— 查券商分點 9/3 後停寫的根因（只讀）。
結果只存 Supabase 私有表 ui_selftest_reports（kind=probe_broker），不印 token、不放公開日誌。"""
import os, json, re, time
import requests
from supabase import create_client

R = {"kind": "probe_broker", "ts": time.strftime("%Y-%m-%d %H:%M:%S"), "items": []}
TOK = os.environ.get("FM", "")


def add(name, **kw):
    R["items"].append({"name": name, **kw})


def mask(s):
    s = str(s)
    return s.replace(TOK, "***") if TOK else s


def get(url, params=None, headers=None, timeout=25):
    t0 = time.time()
    try:
        r = requests.get(url, params=params, headers=headers, timeout=timeout)
        return r, round(time.time() - t0, 2), None
    except Exception as e:
        return None, round(time.time() - t0, 2), f"{type(e).__name__}: {mask(e)[:200]}"


# 1) 帳號等級與額度
for label, url, kw in [
    ("user_info(header)", "https://api.web.finmindtrade.com/v2/user_info", dict(headers={"Authorization": f"Bearer {TOK}"})),
    ("user_info(param)", "https://api.web.finmindtrade.com/v2/user_info", dict(params={"token": TOK})),
]:
    r, dt, err = get(url, **kw)
    if r is None:
        add(label, error=err, sec=dt)
    else:
        body = mask(r.text)[:400]
        add(label, status=r.status_code, sec=dt, body=body)

# 2) token 能不能抓一般資料
r, dt, err = get("https://api.finmindtrade.com/api/v4/data", params={"dataset": "TaiwanStockPrice", "data_id": "2330", "start_date": "2026-09-28", "token": TOK})
if r is None:
    add("TaiwanStockPrice 2330", error=err, sec=dt)
else:
    try:
        j = r.json()
        add("TaiwanStockPrice 2330", status=r.status_code, sec=dt, msg=j.get("msg"), n=len(j.get("data") or []), last=(j.get("data") or [{}])[-1].get("date"))
    except Exception as e:
        add("TaiwanStockPrice 2330", status=r.status_code, body=mask(r.text)[:200])

# 3) 分點資料：多個日期、多個標的、header 與 param 兩種授權
for code in ("2330", "2317", "6488"):
    for d in ("2026-10-02", "2026-09-30", "2026-09-24", "2026-09-04", "2026-09-03"):
        for mode in ("param", "header"):
            if mode == "param":
                r, dt, err = get("https://api.finmindtrade.com/api/v4/taiwan_stock_trading_daily_report", params={"data_id": code, "date": d, "token": TOK})
            else:
                r, dt, err = get("https://api.finmindtrade.com/api/v4/taiwan_stock_trading_daily_report", params={"data_id": code, "date": d}, headers={"Authorization": f"Bearer {TOK}"})
            if r is None:
                add(f"分點 {code} {d} {mode}", error=err, sec=dt)
                continue
            try:
                j = r.json()
                add(f"分點 {code} {d} {mode}", status=r.status_code, sec=dt, msg=mask(j.get("msg")), n=len(j.get("data") or []))
            except Exception:
                add(f"分點 {code} {d} {mode}", status=r.status_code, sec=dt, body=mask(r.text)[:200])
        if code != "2330":
            break  # 其他標的只測第一個日期
    time.sleep(0.5)

# 4) HiStock
for code in ("2330", "2317", "6488"):
    r, dt, err = get(f"https://histock.tw/stock/branch.aspx?no={code}", headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36", "Accept-Language": "zh-TW,zh;q=0.9"})
    if r is None:
        add(f"HiStock {code}", error=err, sec=dt)
        continue
    t = r.text
    title = re.search(r"<title>(.*?)</title>", t, re.S)
    add(f"HiStock {code}", status=r.status_code, sec=dt, length=len(t), title=(title.group(1).strip()[:80] if title else None),
        has_table=("tbBuy" in t or "買超" in t), snippet=re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", t))[:300])
    # 用專案自己的解析函式
    try:
        import warroom_core as wc
        df = wc.parse_histock_branch_html(t)
        add(f"HiStock parse {code}", rows=(0 if df is None else len(df)), head=(None if df is None else df.head(3).to_dict("records")))
    except Exception as e:
        add(f"HiStock parse {code}", error=f"{type(e).__name__}: {str(e)[:200]}")

# 5) 專案函式實際路徑
try:
    import warroom_core as wc
    for code in ("2330", "2317"):
        t0 = time.time()
        df = wc.fetch_finmind_branch_data(code, "2026-10-02")
        add(f"專案 fetch_finmind_branch_data {code}", rows=(0 if df is None else len(df)), sec=round(time.time() - t0, 2))
        t0 = time.time()
        df = wc.fetch_histock_branch_data(code)
        add(f"專案 fetch_histock_branch_data {code}", rows=(0 if df is None else len(df)), sec=round(time.time() - t0, 2))
except Exception as e:
    add("專案函式", error=f"{type(e).__name__}: {str(e)[:300]}")

sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
sb.table("ui_selftest_reports").insert({"run_id": os.environ.get("GITHUB_RUN_ID", ""), "summary": "probe_broker 分點探測", "report": R}).execute()
print("已上傳", len(R["items"]), "項")
