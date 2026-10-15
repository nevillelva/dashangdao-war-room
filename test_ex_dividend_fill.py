"""【2026-10-16】查11 填息回測：事件判定、彙總、驗證退回、快取與排程補算。"""
import sys, datetime as dt
import ex_dividend_fill as X

fails = []
def check(n, c, extra=""):
    print(("PASS: " if c else "FAIL: ") + n + ("" if c or not extra else f"  -> {extra}"))
    if not c: fails.append(n)

def days(start, n):
    d, out = dt.date.fromisoformat(start), []
    while len(out) < n:
        if d.weekday() < 5: out.append(d.isoformat())
        d += dt.timedelta(days=1)
    return out

# 兩次除息：A 在第 4 日填息；B 到下次除息前都沒填；C（最後一次）資料到頭、觀察中
dts = days("2025-01-02", 300)
px = [100.0] * 300
ex_a, ex_b, ex_c = dts[10], dts[100], dts[250]
for i in range(10, 100): px[i] = 96.0          # A 除息後 96
for i in range(13, 100): px[i] = 100.0         # A：第 4 個交易日(index 13)回到 100 → 填息 days=4
for i in range(100, 250): px[i] = 97.0         # B 後都沒回到 100（before=100）
for i in range(250, 300): px[i] = 96.0         # C 後 50 個交易日仍 <100 → 觀察中
closes = list(zip(dts, px))
def D(ex, before, after, cash=4.0, kind="息"):
    return {"ex_date": ex, "cash": cash, "before_price": before, "after_price": after, "kind": kind}
divs = [D(ex_a, 100, 96), D(ex_b, 100, 96), D(ex_c, 100, 96)]
ev, sk = X.compute_events(divs, closes)
check("A 第4個交易日填息", ev[0]["status"] == "filled" and ev[0]["days"] == 4)
check("B 到下次除息前未填息", ev[1]["status"] == "unfilled")
check("C 資料到頭且未滿120日＝觀察中", ev[2]["status"] == "pending" and ev[2]["days"] == 50)
s = X.summarize(ev, sk)
check("填息率只算完成的 2 次：1/2=50%", s["n_done"] == 2 and s["n_filled"] == 1 and s["rate"] == 50.0 and s["n_pending"] == 1)
check("中位/最久天數", s["med_days"] == 4 and s["max_days"] == 4 and s["within_5"] == 1)

# 老事件、資料早已走過 120 日仍沒填 → 未填息而不是永遠觀察中
ev2, _ = X.compute_events([D(ex_b, 100, 96)], closes)
check("距今超過120日仍未填 → unfilled", ev2[0]["status"] == "unfilled")

# 含股票股利不計入；價格交叉比對不符剔除
ev3, sk3 = X.compute_events([D(ex_a, 100, 96, kind="權息"), D(ex_b, 100, 60)], closes)
check("權息另計、價差不符剔除", ev3 == [] and sk3["stock_div"] == 1 and sk3["price_mismatch"] == 1)

# 顯示字串
check("單檔文字", "填息1/2次" in X.fmt_stock(s) and "中位4天" in X.fmt_stock(s) and "另有1次觀察中" in X.fmt_stock(s))
few = X.summarize(ev[:1], {})
check("樣本<3 誠實標註", "不足採信" in X.fmt_stock(few))
check("無資料文字", X.fmt_stock(None) == "待排程回測")
check("逐次明細", "✅4天" in X.fmt_events(s) and "❌未填息" in X.fmt_events(s) and "⏳觀察中" in X.fmt_events(s))
check("群組彙總", "填息率" in X.fmt_group([s, few]) and X.fmt_group([None]).startswith("尚無"))

# fetch_symbol：用假 FinMind
today = dt.date(2026, 10, 16)
def mk_fetch(div_rows, px_rows, fail=None):
    def fetch(url, params):
        if fail and params["dataset"] == fail: raise RuntimeError("boom")
        return {"data": div_rows if params["dataset"] == "TaiwanStockDividendResult" else px_rows}
    return fetch
d0 = days("2024-01-02", 400)
pr = [{"stock_id": "2330", "date": d, "close": 100.0 if i < 20 or i >= 24 else 96.0} for i, d in enumerate(d0)]
dv = [{"stock_id": "2330", "date": d0[20], "stock_or_cache_dividend": "息", "stock_and_cache_dividend": 4.0, "before_price": 100.0, "after_price": 96.0}]
r, msg = X.fetch_symbol(mk_fetch(dv, pr), "2330", today)
check("fetch_symbol 成功", r is not None and r["n_filled"] == 1 and r["events"][0][3] == 5, msg)
bad = [dict(dv[0], stock_id="9999")]
r, msg = X.fetch_symbol(mk_fetch(bad, pr), "2330", today)
check("代號不符整檔退回", r is None and "驗證不過" in msg)
r, msg = X.fetch_symbol(mk_fetch(dv, pr, fail="TaiwanStockPrice"), "2330", today)
check("股價查詢失敗回 None", r is None and "失敗" in msg)
r, msg = X.fetch_symbol(mk_fetch([], pr), "2330", today)
check("無配息", r is None and "沒有" in msg)

# 快取與補算
class _Q:
    def __init__(self, db, ):
        self.db = db; self._keys = None; self._up = None
    def select(self, *a): return self
    def in_(self, col, keys): self._keys = keys; return self
    def upsert(self, row, on_conflict=None): self._up = row; return self
    def execute(self):
        if self._up is not None:
            self.db[self._up["config_key"]] = self._up["config_value"]; return type("R", (), {"data": []})
        return type("R", (), {"data": [{"config_key": k, "config_value": self.db[k]} for k in (self._keys or []) if k in self.db]})
class _SB:
    def __init__(self): self.db = {}
    def table(self, n): return _Q(self.db)
sb = _SB()
msg, sm = X.sync(sb, mk_fetch(dv, pr), ["2330"], today=today, sleep=lambda s: None)
check("sync 寫入一檔", sm["ok"] == 1 and "ex_div_fill_v1:2330" in sb.db)
c = X.load_cache(sb, ["2330", "2317"])
check("load_cache 讀回", c["2330"]["n_filled"] == 1 and "2317" not in c)
msg, sm = X.sync(sb, mk_fetch(dv, pr), ["2330"], today=today, sleep=lambda s: None)
check("30 天內不重算", sm["due"] == 0)
check("超過30天重算", X.due_symbols(["2330"], c, today + dt.timedelta(days=31)) == ["2330"])
fs = _SB()
msg, sm = X.sync(fs, mk_fetch(dv, pr, fail="TaiwanStockDividendResult"), [str(1000 + i) for i in range(9)], today=today, sleep=lambda s: None)
check("連續5檔失敗提早停止", sm["stopped_early"] and sm["errors"] == 5)
if fails:
    print(f"\n{len(fails)} 項失敗"); sys.exit(1)
print("\n全部通過")
