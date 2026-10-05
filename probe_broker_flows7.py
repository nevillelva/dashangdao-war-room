#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""probe_broker_flows7.py —— 第七輪（2026-10-05）：DJ 主力賣買超頁「歷史單日查詢」能回溯多久、單日是否真的是單日資料。
只讀、低頻（每主機>=2.5 秒）。結果存 ui_selftest_reports(summary='probe_broker7')（私有表，不公開）。

要回答的問題：
  1. 以 &e=YYYY-M-D&f=YYYY-M-D（同一天）查歷史單日，三個主機都支援嗎？回溯最遠到哪一天？
  2. 單日查詢的結果是否「每天不同」（而不是永遠回傳最新資料）？
  3. 5 日區間的買賣超，是否約等於這 5 個單日加總（驗證單日資料正確）？
  4. 單檔單頁耗時，用來估算回補 23 個交易日 × 226 檔所需時間。
"""
import os
import sys
import time
import json
import datetime as dt

import warroom_core as wc

CODES = ["2330", "2317", "3189"]
HOSTS = [h for h in wc.DJ_HOSTS]


def summarize(df):
    if df is None or df.empty:
        return None
    top_b = df.sort_values("net_shares", ascending=False).head(3)
    top_s = df.sort_values("net_shares").head(3)
    return {
        "rows": int(len(df)), "data_date": df.attrs.get("data_date"),
        "total_buy": df.attrs.get("total_buy"), "total_sell": df.attrs.get("total_sell"),
        "top_buy": [(r.broker_name, int(r.net_shares)) for r in top_b.itertuples()],
        "top_sell": [(r.broker_name, int(r.net_shares)) for r in top_s.itertuples()],
        "host": df.attrs.get("host"),
    }


def trading_days_back(n):
    """粗略往回找 n 個週一~週五（假日由 DJ 回傳空表自己顯示）。"""
    d = dt.date.today()
    out = []
    while len(out) < n:
        d -= dt.timedelta(days=1)
        if d.weekday() < 5:
            out.append(d.isoformat())
    return out


R = {"kind": "probe_broker7", "ts": time.strftime("%Y-%m-%d %H:%M:%S"), "single_day": {}, "far_back": {}, "range_vs_sum": {}, "timing": {}}

# 1+2：近 25 個週間日，2330 單日
t0 = time.time()
for d in trading_days_back(25):
    df = wc.fetch_dj_branch_data("2330", d, d)
    R["single_day"][d] = summarize(df)
R["timing"]["25_single_days_sec"] = round(time.time() - t0, 1)

# 1：更久以前
for d in ["2026-08-03", "2026-07-01", "2026-04-01", "2026-01-05", "2025-10-01", "2025-01-02"]:
    df = wc.fetch_dj_branch_data("2330", d, d)
    R["far_back"][d] = summarize(df)

# 3：5 日區間 vs 單日加總（以 2317、3189 驗證）
days5 = ["2026-09-29", "2026-09-30", "2026-10-01", "2026-10-02"]
for code in ("2317", "3189"):
    rng = wc.fetch_dj_branch_data(code, days5[0], days5[-1])
    per = {d: wc.fetch_dj_branch_data(code, d, d) for d in days5}
    sums = {}
    for d, df in per.items():
        if df is None:
            continue
        for r in df.itertuples():
            sums[r.broker_name] = sums.get(r.broker_name, 0) + int(r.net_shares)
    R["range_vs_sum"][code] = {
        "range_top_buy": summarize(rng)["top_buy"] if rng is not None else None,
        "sum_of_days_top_buy": sorted(sums.items(), key=lambda kv: -kv[1])[:3],
        "per_day_rows": {d: (None if df is None else int(len(df))) for d, df in per.items()},
    }

print(json.dumps(R, ensure_ascii=False, indent=1)[:6000])
try:
    from supabase import create_client
    sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
    sb.table("ui_selftest_reports").insert(
        {"run_id": os.environ.get("GITHUB_RUN_ID", ""), "summary": "probe_broker7", "report": R}).execute()
    print("已寫入 ui_selftest_reports")
except Exception as e:
    print("寫入失敗", type(e).__name__, e)
    sys.exit(1)
