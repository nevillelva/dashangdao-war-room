"""【2026-10-15】例外訊息不得帶出網址裡的 token（Streamlit 日誌曾印出 Finnhub token）。"""
import sys, io, contextlib
import warroom_core as w

fails = []
def check(n, c):
    print(("PASS: " if c else "FAIL: ") + n)
    if not c: fails.append(n)

msg = "403 Client Error: Forbidden for url: https://finnhub.io/api/v1/forex/candle?symbol=OANDA%3AUSD_TWD&resolution=D&from=1&to=2&token=SECRETVALUE123"
out = w._scrub_secrets(msg)
check("token 值被遮掉", "SECRETVALUE123" not in out and "token=***" in out)
check("其餘訊息保留", "403 Client Error" in out and "symbol=OANDA" in out)
check("apikey 也遮", "KEY9" not in w._scrub_secrets("x?apikey=KEY9&a=1") and "a=1" in w._scrub_secrets("x?apikey=KEY9&a=1"))
check("無金鑰文字不變", w._scrub_secrets("普通錯誤") == "普通錯誤")

class _R:
    def raise_for_status(self): raise RuntimeError(msg)
    status_code = 403
class _S:
    def get(self, *a, **k): return _R()
orig = w._SESSION
w._SESSION = _S()
try:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        r1 = w.fetch_finnhub_forex_quote("USD", "TWD", "SECRETVALUE123")
    check("外匯：日誌與回傳都不含 token", "SECRETVALUE123" not in buf.getvalue() and "SECRETVALUE123" not in str(r1))
    class _R2:
        status_code = 500
        def raise_for_status(self): raise RuntimeError(msg)
    class _S2:
        def get(self, *a, **k): return _R2()
    w._SESSION = _S2()
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        r2 = w.fetch_finnhub_quote("AAPL", "SECRETVALUE123")
    check("股票：日誌與回傳都不含 token", "SECRETVALUE123" not in buf.getvalue() and "SECRETVALUE123" not in str(r2))
finally:
    w._SESSION = orig
if fails:
    print(f"\n{len(fails)} 項失敗"); sys.exit(1)
print("\n全部通過")
