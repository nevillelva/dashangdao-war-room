#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ETF 待確認 54C 台帳清單：近期／較舊分流、債券ETF標記。不需網路。"""
import datetime as dt
import sys
sys.path.insert(0, ".")
import etf_tab as T

FAIL = []


def check(name, cond, extra=""):
    if not cond:
        FAIL.append(name)
        print("❌", name, extra)
    else:
        print("✅", name)


today = dt.date(2026, 10, 13)
led = {
    "0056": [{"ex_date": "2026-10-01", "ratio_54c": 0.3496, "status": "carried"}],
    "00919": [{"ex_date": "2026-03-01", "ratio_54c": None, "status": "carried"}],
    "00720B": [{"ex_date": "2026-09-20", "ratio_54c": 0.0, "status": "carried"}],
    "00878": [{"ex_date": "2026-10-05", "ratio_54c": 0.099, "status": "confirmed"}],
    "0050": [],
    "BAD": [{"ex_date": "壞日期", "ratio_54c": 0.1, "status": "carried"}],
}
recent, older = T.pending_ledger_rows(led, {"0056": "元大高股息"}, today)
check("近期只含 90 天內且未確認", [r["代號"] for r in recent] == ["0056", "00720B"], recent)
check("較舊含過時與壞日期", sorted(r["代號"] for r in older) == ["00919", "BAD"], older)
check("名稱與占比換算", recent[0]["名稱"] == "元大高股息" and recent[0]["沿用54C占比%"] == 34.96, recent[0])
check("債券ETF 標記", recent[1]["類型"] == "債券ETF" and recent[0]["類型"] == "一般")
check("已確認與空台帳不列入", all(r["代號"] not in ("00878", "0050") for r in recent + older))
d = T._dash_df(older)
check("空值顯示破折號", d.loc[d["代號"] == "00919", "沿用54C占比%"].iloc[0] == "—")
check("空輸入不當掉", T.pending_ledger_rows({}, {}, today) == ([], []))

print("\n失敗：", FAIL if FAIL else "無")
sys.exit(1 if FAIL else 0)
