"""【2026-10-16】外匯改走 yfinance：Finnhub 外匯端點已移除，且 fetch_yf_forex_quote 行為正確。"""
import sys
import pandas as pd
import warroom_core as w

fails = []
def check(n, c):
    print(("PASS: " if c else "FAIL: ") + n)
    if not c: fails.append(n)

check("Finnhub 外匯函式已移除", not hasattr(w, "fetch_finnhub_forex_quote"))

calls = []
class _TK:
    def __init__(self, sym, session=None): calls.append(sym)
    def history(self, **k): return pd.DataFrame({"Close": [31.0, 31.5, float("nan"), 31.8]})
orig = w.yf.Ticker
w.yf.Ticker = _TK
try:
    r = w.fetch_yf_forex_quote("USD", "TWD")
    check("USD/TWD 用 TWD=X", calls == ["TWD=X"])
    check("取最近兩個有效收盤算漲跌", r["ok"] and r["c"] == 31.8 and r["pc"] == 31.5 and r["dp"] == round((31.8-31.5)/31.5*100, 2))
    w.fetch_yf_forex_quote("EUR", "TWD")
    check("非美元基準用 BASEQUOTE=X", calls[-1] == "EURTWD=X")
    class _TK2(_TK):
        def history(self, **k): return pd.DataFrame({"Close": [31.0]})
    w.yf.Ticker = _TK2
    check("資料不足回 ok=False", w.fetch_yf_forex_quote("USD", "TWD")["ok"] is False)
    class _TK3(_TK):
        def history(self, **k): raise RuntimeError("boom token=SECRET1")
    w.yf.Ticker = _TK3
    r = w.fetch_yf_forex_quote("USD", "TWD")
    check("例外回 ok=False 且不含金鑰", r["ok"] is False and "SECRET1" not in str(r))
finally:
    w.yf.Ticker = orig

import dashangdao_helpers as h, inspect
src = inspect.getsource(h)
check("HUD 對照表已無 forex", "('forex'" not in src)
if fails:
    print(f"\n{len(fails)} 項失敗"); sys.exit(1)
print("\n全部通過")
