#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""盤中異常偵測：首輪不誤報、休市不警示、跨日基準重置。不需網路。python3 test_anomaly_detector.py"""
import sys
from datetime import datetime
sys.path.insert(0, ".")
import streamlit as st
import dashangdao_helpers as H

FAIL = []


def check(name, cond, extra=""):
    if not cond:
        FAIL.append(name)
        print("❌", name, extra)
    else:
        print("✅", name)


TZ = H.TAIPEI_TZ
H.fetch_trading_calendar = lambda: set()          # 不連網：無日曆 → 退回週一到五+時段判斷
MON_10 = datetime(2026, 10, 12, 10, 0, tzinfo=TZ)   # 週一盤中
MON_10B = datetime(2026, 10, 12, 10, 5, tzinfo=TZ)
MON_20 = datetime(2026, 10, 12, 20, 0, tzinfo=TZ)   # 週一晚上
SAT_10 = datetime(2026, 10, 10, 10, 0, tzinfo=TZ)   # 週六
TUE_10 = datetime(2026, 10, 13, 10, 0, tzinfo=TZ)

card = lambda gain, vr=1.0: [{'code': '2330', 'name': '台積電', 'gain': gain, 'vol_ratio': vr}]


def reset():
    for k in ('anomaly_snapshot', 'anomaly_snapshot_date', 'anomaly_log'):
        st.session_state.pop(k, None)


check("盤中 週一10:00", H.is_tw_intraday_session(MON_10))
check("晚上不是盤中", not H.is_tw_intraday_session(MON_20))
check("週六不是盤中", not H.is_tw_intraday_session(SAT_10))

reset()
check("首輪就是-6%：不誤報剛突破", H.detect_intraday_anomalies(card(-6.0), now=MON_10) == [])
a = H.detect_intraday_anomalies(card(-6.5), now=MON_10B)
check("已在-5%以下的第二輪也不重報", a == [])

reset()
H.detect_intraday_anomalies(card(-1.0), now=MON_10)
a = H.detect_intraday_anomalies(card(-5.2), now=MON_10B)
check("第二輪真的跨過-5%才警示", len(a) == 1 and '-5%' in a[0], a)
check("警示寫入紀錄", len(st.session_state['anomaly_log']) == 1)

reset()
H.detect_intraday_anomalies(card(0.0, 1.0), now=MON_10)
a = H.detect_intraday_anomalies(card(5.5, 2.5), now=MON_10B)
check("漲幅+爆量同時跨線 = 2則", len(a) == 2, a)

reset()
H.detect_intraday_anomalies(card(-1.0), now=MON_20)
a = H.detect_intraday_anomalies(card(-6.0), now=MON_20)
check("休市時段即使跨線也不警示", a == [])
check("休市仍更新基準線", st.session_state['anomaly_snapshot']['2330']['gain'] == -6.0)

reset()
H.detect_intraday_anomalies(card(-1.0), now=MON_10)
a = H.detect_intraday_anomalies(card(-6.0), now=TUE_10)
check("跨日舊快照不當基準", a == [])

print("\n失敗：", FAIL if FAIL else "無")
sys.exit(1 if FAIL else 0)
