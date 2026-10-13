#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""隔夜總經HUD：美股交易日標示。不需網路。python3 test_us_quote_session.py"""
import sys
from datetime import datetime
from zoneinfo import ZoneInfo
sys.path.insert(0, ".")
import dashangdao_helpers as H

FAIL = []


def check(name, cond, extra=""):
    if not cond:
        FAIL.append(name)
        print("❌", name, extra)
    else:
        print("✅", name)


NY, TPE = ZoneInfo("America/New_York"), ZoneInfo("Asia/Taipei")
# 週五 2026-10-09 16:00 美東收盤的最後成交
fri_close = int(datetime(2026, 10, 9, 16, 0, tzinfo=NY).timestamp())
# 台北週一早上 08:30（=美東週日晚）查詢 → 應標 10/09 收盤，不是 10/12
now = datetime(2026, 10, 12, 8, 30, tzinfo=TPE)
d, closed = H.us_quote_session(fri_close, now=now)
check("週一早上看到的是上週五10/09，不是查詢日10/12", d == "10/09", d)
check("週末已收盤", closed is True)

# 美股盤中：美東週二 11:00，報價剛成交
tue_mid = int(datetime(2026, 10, 13, 11, 0, tzinfo=NY).timestamp())
now = datetime(2026, 10, 13, 11, 1, tzinfo=NY)
d, closed = H.us_quote_session(tue_mid, now=now)
check("盤中標示 10/13 且未收盤", d == "10/13" and closed is False, (d, closed))

# 同日 16:05 以後視為收盤
now = datetime(2026, 10, 13, 16, 5, tzinfo=NY)
check("同日16:05後=收盤", H.us_quote_session(tue_mid, now=now)[1] is True)

# 沒有時間戳：不標日期
check("ts=0 不標日期", H.us_quote_session(0) == ("", True))
check("ts=None 不標日期", H.us_quote_session(None) == ("", True))
check("ts=壞值 不標日期", H.us_quote_session("abc") == ("", True))

print("\n失敗：", FAIL if FAIL else "無")
sys.exit(1 if FAIL else 0)
