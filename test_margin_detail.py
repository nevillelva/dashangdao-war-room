"""【2026-10-16】融資／融券明細：解析、驗證、比率計算、sync 寫入欄位。"""
import sys
import warroom_core as w

fails = []
def check(n, c):
    print(("PASS: " if c else "FAIL: ") + n)
    if not c: fails.append(n)

ROWS = [
    {"股票代號": "2330", "融資前日餘額": "1,000", "融資今日餘額": "1,200", "融資限額": "5,000", "融券前日餘額": "50", "融券今日餘額": "80"},
    {"股票代號": "2317", "融資前日餘額": "0", "融資今日餘額": "0", "融資限額": "0", "融券前日餘額": "5", "融券今日餘額": "9"},
    {"股票代號": "9999", "融資前日餘額": "abc", "融資今日餘額": "10", "融資限額": "100", "融券今日餘額": "1", "融券前日餘額": "1"},
    {"股票代號": "0050", "融資前日餘額": "1", "融資今日餘額": "2", "融資限額": "10", "融券前日餘額": "0", "融券今日餘額": "0"},
]
class _R:
    def raise_for_status(self): pass
    def json(self): return ROWS
orig = w.requests.get
w.requests.get = lambda *a, **k: _R()
try:
    d = w.fetch_twse_margin_detail_snapshot()
    old = w.fetch_twse_margin_snapshot()
finally:
    w.requests.get = orig

check("2330 融資增減=200、餘額1200、限額5000", d["2330"]["margin_diff"] == 200 and d["2330"]["margin_balance"] == 1200 and d["2330"]["margin_limit"] == 5000)
check("2330 融券餘額80、增減30", d["2330"]["short_balance"] == 80 and d["2330"]["short_diff"] == 30)
check("限額0 → 融資限額 None（不當分母）", d["2317"]["margin_limit"] is None)
check("無法解析的列被丟棄", "9999" not in d)
check("ETF 代號被排除", "0050" not in d)
check("舊函式行為不變", old == {"2330": 200.0, "2317": 0.0})

r = w.calc_margin_ratios(1200, 5000, 80)
check("融資使用率 24%、券資比 6.67%", r["margin_use_pct"] == 24.0 and r["short_margin_ratio"] == 6.67)
r0 = w.calc_margin_ratios(0, None, 5)
check("分母無效回 None", r0["margin_use_pct"] is None and r0["short_margin_ratio"] is None)

class _T:
    def __init__(self): self.rows = []
    def upsert(self, rows, on_conflict=None): self.rows += rows; return self
    def execute(self): return self
class _SB:
    def __init__(self): self.t = _T()
    def table(self, n): return self.t
sv = (w.fetch_twse_t86_snapshot, w.fetch_twse_margin_detail_snapshot, w.fetch_twse_pe_snapshot,
      w.fetch_twse_monthly_revenue_snapshot, w.fetch_twse_daily_price_value_snapshot)
w.fetch_twse_t86_snapshot = lambda d: {}
w.fetch_twse_margin_detail_snapshot = lambda: d
w.fetch_twse_pe_snapshot = lambda: {}
w.fetch_twse_monthly_revenue_snapshot = lambda: {}
w.fetch_twse_daily_price_value_snapshot = lambda d: {}
try:
    sb = _SB()
    n = w.sync_twse_market_snapshot(sb, trade_date="2026-10-15")
finally:
    (w.fetch_twse_t86_snapshot, w.fetch_twse_margin_detail_snapshot, w.fetch_twse_pe_snapshot,
     w.fetch_twse_monthly_revenue_snapshot, w.fetch_twse_daily_price_value_snapshot) = sv
row = next(x for x in sb.t.rows if x["symbol"] == "2330")
check("sync 寫入 margin_balance/limit、short_balance/diff", row["margin_balance"] == 1200 and row["margin_limit"] == 5000 and row["short_balance"] == 80 and row["short_diff"] == 30 and row["margin_diff"] == 200)

print(f"\n{len(fails)} failed")
sys.exit(1 if fails else 0)
