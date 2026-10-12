#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""驗證休市日曆判斷（不連網：模擬證交所回應與連線失敗兩種情況）。"""
import sys, datetime as dt
sys.path.insert(0, ".")
import system_scheduler as S

TZ = S.TAIPEI_TZ
# 【2026-10-12】改為 OpenAPI 格式（list of dict；Date 為民國 yyyMMdd）
def _row(roc, name, note):
    return {"Name": name, "Date": roc, "Weekday": "", "Description": note}
fake = [
    _row("1150102", "國曆新年開始交易日", "國曆新年開始交易。"),
    _row("1150211", "農曆春節前最後交易日", "農曆春節前最後交易。"),
    _row("1150212", "市場無交易，僅辦理結算交割作業", ""),
    _row("1150925", "中秋節", "依規定放假1日。"),
    _row("1150928", "孔子誕辰紀念日/ 教師節", "依規定放假1日。"),
    # 證交所對「補假」日只寫『…補假。』沒有『放假』二字（2026-10-07 發現的 bug）
    _row("1151009", "國慶日", "國慶日為10月10日適逢星期六，於10月9日（星期五）補假。"),
    _row("1151010", "國慶日", "依規定放假1日。"),
    _row("1151026", "臺灣光復暨金門古寧頭大捷紀念日", "臺灣光復暨金門古寧頭大捷紀念日為10月25日適逢星期日，於10月26日（星期一）補假。"),
]
class R:
    def json(self): return fake
import requests
orig = requests.get
requests.get = lambda *a, **k: R()
S._TW_CLOSED_CACHE.clear()
D = lambda y, m, d: dt.datetime(y, m, d, 9, 0, tzinfo=TZ)
assert S.is_trading_day(D(2026, 9, 24)) is True
assert S.is_trading_day(D(2026, 9, 25)) is False, "中秋節"
assert S.is_trading_day(D(2026, 9, 28)) is False, "教師節"
assert S.is_trading_day(D(2026, 9, 29)) is True
assert S.is_trading_day(D(2026, 9, 26)) is False, "週六"
assert S.is_trading_day(D(2026, 1, 2)) is True, "開始交易日仍是交易日"
assert S.is_trading_day(D(2026, 2, 11)) is True, "春節前最後交易日仍是交易日"
assert S.is_trading_day(D(2026, 2, 12)) is False, "無交易結算日"
assert S.is_trading_day(D(2026, 10, 9)) is False, "國慶補假(週五 10/9) 必須休市（API 說明只寫『補假』）"
assert S.is_trading_day(D(2026, 10, 26)) is False, "光復節補假"
assert S.is_trading_day(D(2026, 10, 8)) is True and S.is_trading_day(D(2026, 10, 12)) is True
assert S._TW_CLOSED_SOURCE.get(2026) == "api", "必須真的走 OpenAPI 解析路徑"
print("OK 正常回應（OpenAPI 路徑）")
# API 只回一部分（解析漏掉）時，2026 仍併入內建清單，不會比內建少
fake_partial = [_row("1150925", "中秋節", "依規定放假1日。")]
class R2:
    def json(self): return fake_partial
requests.get = lambda *a, **k: R2()
S._TW_CLOSED_CACHE.clear()
assert S.is_trading_day(D(2026, 10, 9)) is False, "API 漏列時仍併入內建清單"
S._TW_CLOSED_CACHE.clear()
requests.get = lambda *a, **k: R()
assert S.is_trading_day(D(2027, 3, 1)) is True and S._TW_CLOSED_SOURCE.get(2027) == "builtin", "OpenAPI 只有當年度 → 其他年度退回內建"
requests.get = lambda *a, **k: R()
S._TW_CLOSED_CACHE.clear()

def boom(*a, **k): raise RuntimeError("network down")
requests.get = boom
S._TW_CLOSED_CACHE.clear()
assert S.is_trading_day(D(2026, 9, 25)) is False and S.is_trading_day(D(2026, 10, 9)) is False, "連不上時用內建 2026 清單"
assert S.is_trading_day(D(2026, 9, 24)) is True
S._TW_CLOSED_CACHE.clear()
assert S.is_trading_day(D(2027, 3, 1)) is True, "非 2026 且連不上 → 只擋週末"
assert S.is_trading_day(D(2027, 3, 6)) is False
print("OK 連線失敗退回內建/週末")
requests.get = orig
