"""stage_intraday_execute 整合測試（假資料庫）：空方第三關不合格不進場並把閘門結果改成 fail；多方不受影響。"""
import os, sys
import system_scheduler as ss

fails = []
def check(n, c):
    print(("PASS: " if c else "FAIL: ") + n)
    if not c: fails.append(n)

TODAY = "2026-10-15"
TIMES = [f"09:{m:02d}" for m in range(0, 60, 5)]
def bars(sym, o, closes):
    out = []
    prev = o
    for t, c in zip(TIMES, closes):
        out.append({"symbol": sym, "bar_time": t, "open": prev, "high": max(prev, c), "low": min(prev, c), "close": c, "volume": 10})
        prev = c
    return out

BARS = (bars("S_PASS", 100, [99] * 12)              # 收 99 < 開 100 → 通過
        + bars("S_FAIL", 100, [99] * 11 + [101])    # 收 101 ≥ 開 100 → 不合格
        + bars("S_NODATA", 100, [99] * 3)[:3]       # 只有 3 根 → 資料不足不擋
        + bars("L_OK", 100, [101] * 12))            # 多方不檢查

GATES = [{"symbol": s, "trade_date": TODAY, "overall_verdict": "pass", "direction": d}
         for s, d in [("S_PASS", "short"), ("S_FAIL", "short"), ("S_NODATA", "short"), ("L_OK", "long")]]
GATES.append({"symbol": "M_MANUAL", "trade_date": TODAY, "overall_verdict": "pass", "direction": "long"})   # 只在手動雷達清單

class Q:
    def __init__(self, db, table): self.db, self.t = db, table; self.f = []; self.op = None; self.payload = None
    def select(self, *a, **k): self.op = "select"; return self
    def insert(self, p): self.op = "insert"; self.payload = p; return self
    def update(self, p): self.op = "update"; self.payload = p; return self
    def eq(self, k, v): self.f.append(("eq", k, v)); return self
    def in_(self, k, v): self.f.append(("in", k, v)); return self
    def execute(self):
        class R: pass
        r = R(); r.data = []
        rows = self.db.tables.setdefault(self.t, [])
        def ok(row):
            for kind, k, v in self.f:
                if kind == "eq" and str(row.get(k)) != str(v): return False
                if kind == "in" and row.get(k) not in v: return False
            return True
        if self.op == "select":
            r.data = [x for x in rows if ok(x)]
        elif self.op == "insert":
            rows.append(dict(self.payload)); self.db.inserts.append((self.t, dict(self.payload)))
        elif self.op == "update":
            for x in rows:
                if ok(x): x.update(self.payload)
        return r

class DB:
    def __init__(self):
        self.tables = {"intraday_gate_results": [dict(g) for g in GATES],
                       "intraday_5min_bars": [dict(b, trade_date=TODAY) for b in BARS],
                       "system_portfolio": [],
                       # 2026-10-22：只執行系統候選池裡的股票（手動清單只追蹤）→ 假資料庫補上候選池
                       "intraday_candidate_pool": [{"trade_date": TODAY, "symbol": s, "direction": d, "source": "turnover_score", "note": "系統A"}
                                                   for s, d in [("S_PASS", "short"), ("S_FAIL", "short"), ("S_NODATA", "short"), ("L_OK", "long")]]}
        self.inserts = []
    def table(self, n): return Q(self, n)

class FakeDT:
    pass

ss.is_trading_day = lambda d=None: True
os.environ["INTRADAY_EXECUTE_ALLOW_ANYTIME"] = "1"
ss.fetch_live_quotes_resilient = lambda pairs, **k: ({s: {"price": 100.0} for s, _ in pairs}, {})
ss.notify_telegram = lambda m: None
ss._tg_name_map = lambda: {}
ss.get_industry_map_with_fallback = lambda sb: ({}, {})   # 不連網
class _D(ss.datetime):
    @classmethod
    def now(cls, tz=None):
        return ss.datetime(2026, 10, 15, 10, 2, tzinfo=tz)
ss.datetime = _D

db = DB()
ss.stage_intraday_execute(db)
entered = {(r["symbol"], r["side"]) for t, r in db.inserts if t == "system_portfolio"}
check("通過的空方進場", ("S_PASS", "short") in entered)
check("資料不足的空方不擋單、照常進場", ("S_NODATA", "short") in entered)
check("不合格的空方不進場", ("S_FAIL", "short") not in entered)
check("多方照常進場", ("L_OK", "long") in entered)
g = {x["symbol"]: x for x in db.tables["intraday_gate_results"]}
check("不合格那筆閘門結果改成 fail 並寫明原因", g["S_FAIL"]["overall_verdict"] == "fail" and "反彈已收復起點" in g["S_FAIL"]["overall_label"])
check("gate3_verdict 寫入（pass/fail/unknown）",
      g["S_PASS"].get("gate3_verdict") == "pass" and g["S_FAIL"].get("gate3_verdict") == "fail"
      and g["S_NODATA"].get("gate3_verdict") == "unknown")
check("多方閘門結果不被改動", "gate3_verdict" not in g["L_OK"] and g["L_OK"]["overall_verdict"] == "pass")
check("手動雷達清單的股票不自動進場", not any(r["symbol"] == "M_MANUAL" for t, r in db.inserts if t == "system_portfolio"))
_lok = [r for t, r in db.inserts if t == "system_portfolio" and r["symbol"] == "L_OK"]
check("等資金：100 元 × 1.0 張 ＝ 10 萬元", bool(_lok) and _lok[0]["shares"] == 1.0 and _lok[0]["capital"] == 100000)
if fails:
    print(f"\n{len(fails)} 項失敗"); sys.exit(1)
print("\n全部通過")
