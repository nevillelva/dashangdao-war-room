"""【2026-10-16】分點資料新鮮度：過期不評分。"""
import sys
from datetime import date
import warroom_core as w
fails = []
def check(n, c):
    print(("PASS: " if c else "FAIL: ") + n)
    if not c: fails.append(n)
t = date(2026, 10, 16)
check("昨天算新鮮", w.is_broker_date_fresh("2026-10-15", today=t))
check("當天算新鮮", w.is_broker_date_fresh("2026-10-16", today=t))
check("4 天前（週末＋休市）仍新鮮", w.is_broker_date_fresh("2026-10-12", today=t))
check("10/08（8 天前）過期", not w.is_broker_date_fresh("2026-10-08", today=t))
check("未來日期視為不可信", not w.is_broker_date_fresh("2026-10-20", today=t))
check("亂碼視為不新鮮", not w.is_broker_date_fresh("abc", today=t) and not w.is_broker_date_fresh(None, today=t))
check("可自訂天數", w.is_broker_date_fresh("2026-10-08", today=t, max_age_days=10))
print(f"\n{len(fails)} failed"); sys.exit(1 if fails else 0)
