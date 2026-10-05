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
ns = {"datetime": datetime, "timedelta": timedelta, "dt_time": dt_time, "TAIPEI_TZ": TZ,
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
    ("intraday_snap", "2026-10-05 09:35", True),   # 快照 pass1
    ("intraday_snap", "2026-10-05 10:02", True),   # 快照 pass2
    ("intraday_snap", "2026-10-05 13:20", False),  # 收盤前補跑會拿到無意義的資料
    ("intraday_snap", "2026-10-09 09:35", False),  # 休市日
    ("gate", "2026-10-09 09:10", False),          # 國慶補假
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
d = prev(at("2026-10-12 08:00").date())
print("✅" if d.strftime("%Y-%m-%d") == "2026-10-08" else "❌", "10/12 前一交易日 =", d)
bad += d.strftime("%Y-%m-%d") != "2026-10-08"
d = prev(at("2026-10-05 08:00").date())
print("✅" if d.strftime("%Y-%m-%d") == "2026-10-02" else "❌", "10/5 前一交易日 =", d)
bad += d.strftime("%Y-%m-%d") != "2026-10-02"
raise SystemExit(1 if bad else 0)
