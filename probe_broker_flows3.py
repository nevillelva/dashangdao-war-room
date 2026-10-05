#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""probe_broker_flows3.py —— 第三輪：FINMIND_TOKEN 是「逗號分隔多組」，逐組測試帳號等級/額度/分點權限（不輸出 token）。"""
import os, json, re, time, base64
import requests
from supabase import create_client

R = {"kind": "probe_broker3", "ts": time.strftime("%Y-%m-%d %H:%M:%S"), "items": []}
RAW = os.environ.get("FM", "")
TOKS = [x.strip() for x in RAW.split(",") if x.strip()]


def add(name, **kw):
    R["items"].append({"name": name, **kw})


def b64j(s):
    try:
        s += "=" * (-len(s) % 4)
        return json.loads(base64.urlsafe_b64decode(s.encode()).decode())
    except Exception as e:
        return {"_err": type(e).__name__}


add("token組數", n=len(TOKS), lens=[len(t) for t in TOKS])
for i, t in enumerate(TOKS, 1):
    parts = t.split(".")
    info = {"i": i, "n_parts": len(parts)}
    if len(parts) == 3:
        pl = b64j(parts[1])
        info["payload_keys"] = sorted(pl.keys())
        for k in ("exp", "iat", "date"):
            if k in pl:
                info[k] = pl[k]
                if isinstance(pl[k], (int, float)) and pl[k] > 1e9:
                    info[k + "_utc"] = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(pl[k]))
    info["now_utc"] = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
    add(f"token{i} 結構", **info)

    try:
        r = requests.get("https://api.web.finmindtrade.com/v2/user_info", headers={"Authorization": f"Bearer {t}"}, timeout=20)
        j = r.json()
        add(f"token{i} user_info", status=r.status_code, body={k: j.get(k) for k in j if k not in ("token",)})
    except Exception as e:
        add(f"token{i} user_info", error=str(e).replace(t, "***")[:150])

    for lab, url, params in [
        ("TaiwanStockPrice", "https://api.finmindtrade.com/api/v4/data", {"dataset": "TaiwanStockPrice", "data_id": "2330", "start_date": "2026-09-28"}),
        ("分點 2330 10/02", "https://api.finmindtrade.com/api/v4/taiwan_stock_trading_daily_report", {"data_id": "2330", "date": "2026-10-02"}),
        ("分點 2330 09/03", "https://api.finmindtrade.com/api/v4/taiwan_stock_trading_daily_report", {"data_id": "2330", "date": "2026-09-03"}),
        ("分點 6488 10/02", "https://api.finmindtrade.com/api/v4/taiwan_stock_trading_daily_report", {"data_id": "6488", "date": "2026-10-02"}),
    ]:
        try:
            r = requests.get(url, params={**params, "token": t}, timeout=30)
            j = r.json()
            add(f"token{i} {lab}", status=r.status_code, msg=str(j.get("msg"))[:140], n=len(j.get("data") or []))
        except Exception as e:
            add(f"token{i} {lab}", error=str(e).replace(t, "***")[:150])

sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
sb.table("ui_selftest_reports").insert({"run_id": os.environ.get("GITHUB_RUN_ID", ""), "summary": "probe_broker3", "report": R}).execute()
print("已上傳", len(R["items"]))
