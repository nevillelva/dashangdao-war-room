"""【P5 資料】etf_price_sync 離線測試（假的 Supabase 與假的 FinMind，不打網路）。python3 test_etf_price_sync.py"""
import datetime as dt
import etf_price_sync as S

FAIL = []


def check(name, cond, extra=""):
    if cond:
        print("✅", name)
    else:
        FAIL.append(name)
        print("❌", name, extra)


# 1) 驗證規則
good = [{"stock_id": "0050", "date": "2026-10-01", "close": 100}, {"stock_id": "0050", "date": "2026-10-02", "close": 101.5}]
rows, probs = S.validate_rows("0050", good, master_last=101)
check("正常列通過並排序", rows == [("2026-10-01", 100.0), ("2026-10-02", 101.5)] and not probs, (rows, probs))
check("代號不符 → 整檔退回", S.validate_rows("0050", [{"stock_id": "0056", "date": "2026-10-01", "close": 1}])[1])
check("日期不合法 → 退回", S.validate_rows("0050", [{"stock_id": "0050", "date": "2026-13-01", "close": 1}])[1])
check("收盤 ≤0 → 退回", S.validate_rows("0050", [{"stock_id": "0050", "date": "2026-10-01", "close": 0}])[1])
check("收盤非數字 → 退回", S.validate_rows("0050", [{"stock_id": "0050", "date": "2026-10-01", "close": "x"}])[1])
check("同日重複 → 退回", S.validate_rows("0050", [{"stock_id": "0050", "date": "2026-10-01", "close": 1}] * 2)[1])
r3, p3 = S.validate_rows("0050", good, master_last=110)
check("與現價偏差 >5% → 整檔退回（不寫入）", r3 == [] and p3 and "超過" in p3[0], p3)
check("查無資料不是錯誤", S.validate_rows("0050", []) == ([], []))
# 1b) 依現價日期比對（不拿最後一列比）
two = [{"stock_id": "0050", "date": "2026-10-07", "close": 100}, {"stock_id": "0050", "date": "2026-10-08", "close": 130}]
r_ok, p_ok = S.validate_rows("0050", two, master_last=100, master_date="2026-10-07")
check("現價日那天相符 → 通過（即使最後一列偏差大）", r_ok and not p_ok, p_ok)
r_bad, p_bad = S.validate_rows("0050", two, master_last=100, master_date="2026-10-08")
check("現價日那天偏差 >5% → 退回", r_bad == [] and p_bad, p_bad)
r_na, p_na = S.validate_rows("0050", two, master_last=100, master_date="2026-10-06")
check("現價日無對應列 → 不比對、不退回（計為未驗證）", r_na and not p_na, p_na)

# 2) 假的 Supabase
class Res:
    def __init__(self, data): self.data = data

class Tbl:
    def __init__(self, db, name):
        self.db, self.name, self.eqs, self.rng = db, name, {}, None
    def select(self, *a, **k): return self
    def eq(self, col, val): self.eqs[col] = val; return self
    def limit(self, n): return self
    def range(self, a, b): self.rng = (a, b); return self
    def upsert(self, payload, on_conflict=None):
        if isinstance(payload, dict): payload = [payload]
        self.db.setdefault(self.name, [])
        if self.name == "system_config":
            self.db[self.name] = [r for r in self.db[self.name] if r["config_key"] != payload[0]["config_key"]] + payload
        else:
            self.db[self.name] += payload
        return self
    def execute(self):
        if self.name == "etf_master":
            return Res(self.db.get("etf_master", []))
        if self.name == "system_config":
            return Res([r for r in self.db.get("system_config", []) if all(r.get(k) == v for k, v in self.eqs.items())])
        return Res(self.db.get(self.name, []))

class FakeSB:
    def __init__(self, master):
        self.db = {"etf_master": master}
    def table(self, name): return Tbl(self.db, name)

MASTER = [
    {"symbol": "0050", "last_price": 101, "active": True},
    {"symbol": "00679B", "last_price": 20, "active": True},
    {"symbol": "BAD", "last_price": 50, "active": True},
    {"symbol": "OLD", "last_price": 10, "active": False},
]

# 3) 同步：0050 正常、00679B 查無資料、BAD 價格偏差 → 退回；OLD 停用不抓
sb = FakeSB(MASTER)
today = dt.date(2026, 10, 12)
calls = []

def fetch(url, params):
    calls.append(params)
    if params["data_id"] == "0050":
        return {"data": [{"stock_id": "0050", "date": "2026-10-01", "close": 100}, {"stock_id": "0050", "date": "2026-10-02", "close": 101}]}
    if params["data_id"] == "00679B":
        return {"data": []}
    if params["data_id"] == "BAD":
        return {"data": [{"stock_id": "BAD", "date": "2026-10-02", "close": 80}]}
    raise AssertionError("停用的 ETF 不應被抓取")

msg, summ = S.run_price_sync(sb, fetch, today=today, sleep=lambda s: None)
check("只抓啟用中的 ETF", [c["data_id"] for c in calls] == ["0050", "00679B", "BAD"], calls)
check("首次回補 5 年起點", calls[0]["start_date"] == (today - dt.timedelta(days=365 * 5)).isoformat(), calls[0])
check("0050 寫入 2 筆", sum(1 for r in sb.db.get("etf_price_daily", []) if r["symbol"] == "0050") == 2)
check("BAD 偏差退回、不寫入", not any(r["symbol"] == "BAD" for r in sb.db.get("etf_price_daily", [])) and any("BAD" in x for x in summ["rejected"]), summ)
check("摘要：寫入 1 檔", summ["synced"] == 1 and summ["rows_written"] == 2, summ)
state = [r for r in sb.db["system_config"] if r["config_key"] == S.STATE_KEY]
check("狀態記錄最後同步日", state and "2026-10-02" in state[0]["config_value"], state)

# 4) 第二次：增量起點＝最後日前 7 天
calls.clear()
S.run_price_sync(sb, fetch, today=dt.date(2026, 10, 13), sleep=lambda s: None)
inc = [c for c in calls if c["data_id"] == "0050"][0]
check("增量起點＝最後日−7 天", inc["start_date"] == "2026-09-25", inc)

# 5) 連續失敗提早停止
many = [{"symbol": f"E{i:03d}", "last_price": 10, "active": True} for i in range(10)]
sb2 = FakeSB(many)
def boom(url, params): raise RuntimeError("額度用盡")
msg2, s2 = S.run_price_sync(sb2, boom, today=today, sleep=lambda s: None)
check("連續 5 次失敗即提早停止", s2["stopped_early"] and s2["errors"] == S.MAX_CONSECUTIVE_FAIL, s2)

# 6) 手動匯入寫入同一張表
n = S.upsert_manual(sb, "0050", [("2025-01-02", 90.0)], "https://example.com/x")
check("手動匯入寫入並記錄來源", n == 1 and any(r.get("source") == "https://example.com/x" for r in sb.db["etf_price_daily"]))

print("\n全部通過" if not FAIL else f"\n失敗 {FAIL}")
raise SystemExit(1 if FAIL else 0)
