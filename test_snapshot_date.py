#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_snapshot_date.py —— 驗證 _snapshot_trade_date 的日期歸屬規則（不需網路/套件：用 ast 抽出函式、塞替身的 is_trading_day）。"""
import ast
from datetime import datetime, timedelta, time as dt_time
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Taipei")
src = open("system_scheduler.py", encoding="utf-8").read()
fn = next(n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name == "_snapshot_trade_date")
CLOSED = {"2026-09-25", "2026-09-28", "2026-10-09", "2026-10-10"}
ns = {"datetime": datetime, "timedelta": timedelta, "dt_time": dt_time, "TAIPEI_TZ": TZ,
      "is_trading_day": lambda d: d.weekday() < 5 and d.strftime("%Y-%m-%d") not in CLOSED}
exec(compile(ast.Module([fn], []), "snap", "exec"), ns)
f = ns["_snapshot_trade_date"]


def at(s):
    return datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=TZ)


cases = [
    ("2026-10-02 17:35", "2026-10-02"),   # 週五收盤後 → 當天
    ("2026-10-02 23:57", "2026-10-02"),
    ("2026-10-02 10:00", None),            # 盤中 → 不記
    ("2026-10-02 08:00", "2026-10-01"),    # 盤前 → 上一交易日
    ("2026-10-01 00:03", "2026-09-30"),    # 午夜後 → 前一天(原本誤記成 10/1)
    ("2026-10-03 12:00", "2026-10-02"),    # 週六 → 週五
    ("2026-10-05 00:40", "2026-10-02"),    # 週一凌晨 → 週五
    ("2026-09-29 00:06", "2026-09-24"),    # 9/25 中秋、9/28 教師節休市 → 回溯到 9/24
    ("2026-10-12 08:00", "2026-10-08"),    # 10/9 補假、10/10 → 回溯到 10/8
]
bad = 0
for t, exp in cases:
    got, why = f(at(t))
    ok = got == exp
    bad += (not ok)
    print(("✅" if ok else "❌"), t, "→", got, f"({why})", "" if ok else f"期望 {exp}")
raise SystemExit(1 if bad else 0)
