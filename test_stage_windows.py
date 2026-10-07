#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_stage_windows.py —— 驗證 _stage_window_check（時窗守門）與 _prev_trading_day。用 ast 抽出，不需網路/套件。"""
import ast
from datetime import datetime, timedelta, time as dt_time
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Taipei")
src = open("system_scheduler.py", encoding="utf-8").read()
tree = ast.parse(src)
want_funcs = {"_stage_window_check", "_prev_trading_day"}
want_assign = {"HOLIDAY_SKIP_STAGES", "STAGE_TIME_WINDOWS", "SILENT_LOG_STAGES"}
nodes = []
for n in tree.body:
    if isinstance(n, ast.FunctionDef) and n.name in want_funcs:
        nodes.append(n)
    if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in want_assign for t in n.targets):
        nodes.append(n)
CLOSED = {"2026-09-25", "2026-09-28", "2026-10-09", "2026-10-10"}
import os
ns = {"os": os, "datetime": datetime, "timedelta": timedelta, "dt_time": dt_time, "TAIPEI_TZ": TZ,
      "is_market_holiday": lambda d: d.strftime("%Y-%m-%d") in CLOSED,
      "is_trading_day": lambda d: d.weekday() < 5 and d.strftime("%Y-%m-%d") not in CLOSED}
exec(compile(ast.Module(nodes, []), "w", "exec"), ns)
chk, prev = ns["_stage_window_check"], ns["_prev_trading_day"]


def at(s):
    return datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=TZ)


cases = [
    ("gate", "2026-10-05 09:10", True),
    ("gate", "2026-10-05 14:37", False),      # 稽核抓到的收盤後重算閘門
    ("morning_exit", "2026-10-05 09:20", True),
    ("morning_exit", "2026-10-05 13:56", False),  # 稽核抓到的 13:56 才早盤出場
    ("time_stop_check", "2026-10-05 10:25", True),
    ("time_stop_check", "2026-10-05 16:37", False),
    ("tail_entry", "2026-10-05 13:05", True),
    ("tail_entry", "2026-10-05 18:47", False),
    ("tail_entry", "2026-10-03 03:00", False),    # 週六凌晨補跑
    ("intraday_kbar", "2026-10-05 09:14", True),
    ("intraday_kbar", "2026-10-05 10:23", False), # 看門狗誤重發
    ("intraday_snap", "2026-10-05 09:26", True),   # Worker 09:26 派發 pass1（10/6 因下限 09:30 被略過的回歸）
    ("intraday_snap", "2026-10-05 09:35", True),   # 快照 pass1（runner 排隊晚起跑）
    ("intraday_snap", "2026-10-05 09:56", True),   # Worker 09:56 派發 pass2
    ("intraday_snap", "2026-10-05 10:02", True),   # 快照 pass2
    ("intraday_snap", "2026-10-05 08:50", False),  # 太早不該跑
    ("intraday_snap", "2026-10-05 13:20", False),  # 收盤前補跑會拿到無意義的資料
    ("intraday_snap", "2026-10-09 09:35", False),  # 休市日
    ("gate", "2026-10-09 09:10", False),          # 國慶補假
    ("premarket_brief", "2026-10-08 05:31", True),     # 【10/7】Worker 05:30 派發
    ("premarket_brief", "2026-10-08 05:58", True),
    ("premarket_brief", "2026-10-08 06:30", False),    # 6 點後才到就不發（使用者要 06:00 前）
    ("premarket_brief", "2026-10-08 03:00", False),
    ("premarket_brief", "2026-10-09 05:31", False),    # 休市日
    ("premarket_brief", "2026-10-10 05:31", False),    # 週六
    ("premarket_supplement", "2026-10-08 08:01", True),
    ("premarket_supplement", "2026-10-08 09:30", False),
    ("premarket_supplement", "2026-10-09 08:01", False),
    ("signal", "2026-10-03 03:00", True),         # 夜間類不受此守門限制（另有去重）
    ("health", "2026-10-03 03:00", True),
    ("intraday_force_exit", "2026-10-05 13:46", True),  # 強制平倉晚到仍要執行(不留倉)
]
bad = 0
for st, t, exp in cases:
    ok, why = chk(st, at(t))
    good = ok == exp
    bad += (not good)
    print("✅" if good else "❌", st, t, "→", "執行" if ok else f"略過({why})")
# 交叉檢查：Worker 排程裡「有時窗守門的階段」，派發時刻（台北）必須落在該階段的時窗內，
# 否則 Worker 派出去、階段一開跑就被當成「晚到補跑」略過，還會留 skipped_late 讓看門狗以為已處理（10/6 的 09:30 快照就是這樣漏的）。
import re
js = open("warroom_monitor_worker.js", encoding="utf-8").read()
n_chk = 0
for m in re.finditer(r'\{\s*stage:\s*"([a-z_0-9]+)",\s*h:\s*(\d+),\s*m:\s*(\d+),\s*days:\s*\[([^\]]*)\]', js):
    st, h, mi, days = m.group(1), int(m.group(2)), int(m.group(3)), m.group(4)
    if st not in ns["STAGE_TIME_WINDOWS"]:
        continue
    tp_h = (h + 8) % 24
    ok, why = chk(st, at("2026-10-07 %02d:%02d" % (tp_h, mi)))      # 2026-10-07 為週三交易日
    good = bool(ok)
    bad += (not good)
    n_chk += 1
    print("✅" if good else "❌", f"Worker 排程 {st} 派發 {tp_h:02d}:{mi:02d}", "在時窗內" if ok else f"→ 被時窗擋掉：{why}")
bad += (n_chk == 0)
print("✅" if n_chk else "❌", f"Worker 排程交叉檢查共 {n_chk} 筆")
d = prev(at("2026-10-12 08:00").date())
print("✅" if d.strftime("%Y-%m-%d") == "2026-10-08" else "❌", "10/12 前一交易日 =", d)
bad += d.strftime("%Y-%m-%d") != "2026-10-08"
d = prev(at("2026-10-05 08:00").date())
print("✅" if d.strftime("%Y-%m-%d") == "2026-10-02" else "❌", "10/5 前一交易日 =", d)
bad += d.strftime("%Y-%m-%d") != "2026-10-02"
raise SystemExit(1 if bad else 0)
