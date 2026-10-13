#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_winrate_horizon.py —— win_rate_3d／avg_return_3d 欄位的語意分開看待（只有 horizon_days=5 的列給畫面用）。
靜態檢查：寫入端一定標 5、讀取端一定只讀 5；遷移檔會把舊列留成 NULL（語意未驗證）。"""
import re
import sys

fails = []


def check(name, cond):
    print(("PASS: " if cond else "FAIL: ") + name)
    if not cond:
        fails.append(name)


sch = open("system_scheduler.py", encoding="utf-8").read()
ui = open("dashangdao.py", encoding="utf-8").read()
mig = open("supabase_migration_filter_backtest_horizon.sql", encoding="utf-8").read()

i = sch.index('sb.table("filter_backtest_weekly_results").insert(rows_to_save)')
block = sch[max(0, i - 900):i]
check("寫入端：每列都帶 horizon_days = 5", '"horizon_days": 5' in block)
j = ui.index('SUPABASE_CONN.table("filter_backtest_weekly_results")')
rd = ui[j:j + 600]
check("讀取端：只讀 horizon_days = 5 的列", '.eq("horizon_days", 5)' in rd)
check("讀取端：select 含 horizon_days", "horizon_days" in rd.split(".eq(")[0])
check("遷移：新增 horizon_days 欄位", "add column if not exists horizon_days" in mig)
check("遷移：只把確定是 5 日語意的列（2026-10-02 12:46+08 之後）標 5，其餘維持 NULL",
      re.search(r"set horizon_days = 5\s+where horizon_days is null and created_at >= '2026-10-02 04:46:09\+00'", mig) is not None)
if fails:
    print(f"\n{len(fails)} 項失敗")
    sys.exit(1)
print("\n全部通過")
