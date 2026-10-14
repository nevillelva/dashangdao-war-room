"""【2026-10-14】收盤價回補測試（離線，假 Supabase）。"""
import sys
import close_backfill as cb

fails = []


def check(name, cond):
    print(("PASS: " if cond else "FAIL: ") + name)
    if not cond:
        fails.append(name)


class R:
    def __init__(self, d): self.data = d


class Q:
    def __init__(self, db, name):
        self.db, self.name, self.f, self.neg, self.lt_, self.nn, self.ord, self.lim, self.rng, self.up = db, name, {}, None, None, False, None, None, None, None

    def select(self, *_): return self
    def eq(self, k, v): self.f[k] = v; return self
    def lt(self, k, v): self.lt_ = (k, v); return self
    @property
    def not_(self): self.nn = True; return self
    def is_(self, k, v): return self
    def order(self, k, desc=False): self.ord = (k, desc); return self
    def limit(self, n): self.lim = n; return self
    def range(self, a, b): self.rng = (a, b); return self
    def upsert(self, rows, on_conflict=None): self.up = rows; return self

    def execute(self):
        t = self.db[self.name]
        if self.up is not None:
            for r in self.up:
                for x in t:
                    if x["trade_date"] == r["trade_date"] and x["symbol"] == r["symbol"]:
                        x.update(r)
            return R([])
        rows = [dict(x) for x in t if all(x.get(k) == v for k, v in self.f.items())]
        if self.lt_:
            rows = [x for x in rows if x["trade_date"] < self.lt_[1]]
        if self.nn:
            rows = [x for x in rows if x.get("close_price")]
        if self.ord:
            rows.sort(key=lambda x: x[self.ord[0]], reverse=self.ord[1])
        if self.lim:
            rows = rows[: self.lim]
        if self.rng:
            rows = rows[self.rng[0]: self.rng[1] + 1]
        return R(rows)


class SB:
    def __init__(self, db): self.db = db
    def table(self, n): return Q(self.db, n)


def mk(n=900, d="2026-10-13", base=100.0, move=1.01):
    syms = [f"{1000 + i}" for i in range(n)]
    snap = [{"trade_date": "2026-10-12", "symbol": s, "close_price": base} for s in syms]
    snap += [{"trade_date": d, "symbol": s, "close_price": None} for s in syms]
    api = [{"Date": "1151013", "Code": s, "ClosingPrice": str(base * move), "TradeValue": "1000", "TradeVolume": "10"} for s in syms]
    api += [{"Date": "1151013", "Code": "0050", "ClosingPrice": "150", "TradeValue": "1", "TradeVolume": "1"}]
    return SB({"twse_market_snapshot": snap}), api


td = lambda iso: True
sb, api = mk()
res = cb.backfill_latest(sb, lambda: api, "2026-10-14", td)
check("正常情況補寫成功", res["status"] == "filled" and res["written"] == 900)
check("只補缺的欄位、其他日期不動", all(x["close_price"] for x in sb.db["twse_market_snapshot"]))
check("第二次執行＝已完整", cb.backfill_latest(sb, lambda: api, "2026-10-14", td)["status"] == "already_ok")

sb, api = mk(move=1.5)
check("漲跌幅整批不合理 → 退回", cb.backfill_latest(sb, lambda: api, "2026-10-14", td)["status"] == "rejected")
check("退回時不寫入", not any(x["close_price"] for x in sb.db["twse_market_snapshot"] if x["trade_date"] == "2026-10-13"))
sb, api = mk(n=300)
check("筆數太少 → 退回", cb.backfill_latest(sb, lambda: api, "2026-10-14", td)["status"] == "rejected")
sb, api = mk()
check("資料日晚於今天 → 退回", cb.backfill_latest(sb, lambda: api, "2026-10-12", td)["reason"] == "bad_date")
check("資料日非交易日 → 退回", cb.backfill_latest(sb, lambda: api, "2026-10-14", lambda i: False)["reason"] == "bad_date")
sb, api = mk()
sb.db["twse_market_snapshot"] = [x for x in sb.db["twse_market_snapshot"] if x["trade_date"] != "2026-10-13"]
check("資料庫沒有該日快照列 → 不憑空新增", cb.backfill_latest(sb, lambda: api, "2026-10-14", td)["reason"] == "no_snapshot_rows")
check("空回應 → no_data", cb.backfill_latest(sb, lambda: [], "2026-10-14", td)["status"] == "no_data")
sb, api = mk()
check("dry 不寫入", cb.backfill_latest(sb, lambda: api, "2026-10-14", td, dry=True)["status"] == "dry"
      and not any(x["close_price"] for x in sb.db["twse_market_snapshot"] if x["trade_date"] == "2026-10-13"))
check("ETF（00 開頭）被排除", cb.is_plain_code("2330") and not cb.is_plain_code("0050") and not cb.is_plain_code("00878"))

if fails:
    print(f"\n{len(fails)} 項失敗"); sys.exit(1)
print("\n全部通過")
