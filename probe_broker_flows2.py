#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""probe_broker_flows2.py —— 第二輪：FINMIND_TOKEN 為何被判「Token is illegal」（只印不含機密的結構資訊）。"""
import os, json, re, time, base64
import requests
from supabase import create_client

R = {"kind": "probe_broker2", "ts": time.strftime("%Y-%m-%d %H:%M:%S"), "items": []}
RAW = os.environ.get("FM", "")


def add(name, **kw):
    R["items"].append({"name": name, **kw})


def b64j(s):
    try:
        s += "=" * (-len(s) % 4)
        return json.loads(base64.urlsafe_b64decode(s.encode()).decode())
    except Exception as e:
        return {"_err": f"{type(e).__name__}"}


# 1) token 結構（長度/空白/是否 JWT/到期）——不輸出 token 本身
t = RAW
parts = t.strip().split(".")
info = {"len_raw": len(RAW), "len_strip": len(RAW.strip()), "has_ws_edge": RAW != RAW.strip(), "has_inner_ws": bool(re.search(r"\s", RAW.strip())),
        "n_parts": len(parts), "looks_jwt": len(parts) == 3}
if len(parts) == 3:
    pl = b64j(parts[1])
    info["payload_keys"] = sorted(pl.keys())
    for k in ("exp", "iat", "date", "nbf"):
        if k in pl:
            v = pl[k]
            info[k] = v
            if isinstance(v, (int, float)) and v > 1e9:
                info[k + "_utc"] = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(v))
    info["now_utc"] = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
add("token結構", **info)

# 2) 去空白後重試、以及訪客(不帶token)
def call(label, params, headers=None, url="https://api.finmindtrade.com/api/v4/data"):
    try:
        r = requests.get(url, params=params, headers=headers, timeout=25)
        j = r.json()
        add(label, status=r.status_code, msg=str(j.get("msg"))[:120], n=len(j.get("data") or []))
    except Exception as e:
        add(label, error=f"{type(e).__name__}: {str(e)[:150]}")

call("strip後 TaiwanStockPrice", {"dataset": "TaiwanStockPrice", "data_id": "2330", "start_date": "2026-09-28", "token": RAW.strip()})
call("訪客 TaiwanStockPrice", {"dataset": "TaiwanStockPrice", "data_id": "2330", "start_date": "2026-09-28"})
call("訪客 分點", {"data_id": "2330", "date": "2026-10-02"}, url="https://api.finmindtrade.com/api/v4/taiwan_stock_trading_daily_report")
call("strip後 分點", {"data_id": "2330", "date": "2026-10-02", "token": RAW.strip()}, url="https://api.finmindtrade.com/api/v4/taiwan_stock_trading_daily_report")
call("Bearer strip後 TaiwanStockPrice", {"dataset": "TaiwanStockPrice", "data_id": "2330", "start_date": "2026-09-28"}, headers={"Authorization": f"Bearer {RAW.strip()}"})
# 官方 login 端點：不會用（需要帳密）；改查 user_info 的訪客回應
try:
    r = requests.get("https://api.web.finmindtrade.com/v2/user_info", timeout=20)
    add("user_info 訪客", status=r.status_code, body=r.text[:200])
except Exception as e:
    add("user_info 訪客", error=str(e)[:150])

# 3) 其他免費分點來源可達性（只看 HTTP 狀態與是否為挑戰頁）
for nm, url in [
    ("TWSE 券商買賣日報(bsr)", "https://bsr.twse.com.tw/bshtm/bsMenu.aspx"),
    ("TPEx 券商買賣", "https://www.tpex.org.tw/web/stock/aftertrading/broker_trading/brokerBS.php?l=zh-tw"),
    ("Goodinfo 分點", "https://goodinfo.tw/tw/ShowBuySaleChart.asp?STOCK_ID=2330"),
    ("富果 Fugle 文件", "https://developer.fugle.tw/"),
]:
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36"}, timeout=20)
        tt = re.search(r"<title>(.*?)</title>", r.text, re.S)
        add(nm, status=r.status_code, length=len(r.text), title=(tt.group(1).strip()[:60] if tt else None),
            captcha=("captcha" in r.text.lower() or "驗證碼" in r.text), cf=("Just a moment" in r.text))
    except Exception as e:
        add(nm, error=f"{type(e).__name__}: {str(e)[:120]}")

sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
sb.table("ui_selftest_reports").insert({"run_id": os.environ.get("GITHUB_RUN_ID", ""), "summary": "probe_broker2", "report": R}).execute()
print("已上傳", len(R["items"]))
