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
check("收盤為負 → 退回", S.validate_rows("0050", [{"stock_id": "0050", "date": "2026-10-01", "close": -1}])[1])
r0, p0 = S.validate_rows("0050", [{"stock_id": "0050", "date": "2026-10-01", "close": 0}, {"stock_id": "0050", "date": "2026-10-02", "close": 50}])
check("收盤 0＝無價日：略過不寫、不整檔退回", p0 == [] and r0 == [("2026-10-02", 50.0)], (r0, p0))
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

# 4b) empty_data（新掛牌無歷史）不是故障：不計錯誤、不中斷
class _Empty(Exception):
    reason = "empty_data"
many_new = [{"symbol": f"N{i:03d}", "last_price": 10, "active": True} for i in range(8)]
sb3 = FakeSB(many_new)
def new_etf(url, params): raise _Empty("empty_data: API 回傳成功但 data 為空")
msg3, s3 = S.run_price_sync(sb3, new_etf, today=today, sleep=lambda s: None)
check("empty_data 不計錯誤、不提早停止", s3["errors"] == 0 and not s3["stopped_early"] and s3.get("no_data") == 8, s3)

# 5) 連續失敗提早停止
many = [{"symbol": f"E{i:03d}", "last_price": 10, "active": True} for i in range(10)]
sb2 = FakeSB(many)
def boom(url, params): raise RuntimeError("額度用盡")
msg2, s2 = S.run_price_sync(sb2, boom, today=today, sleep=lambda s: None)
check("連續 5 次失敗即提早停止", s2["stopped_early"] and s2["errors"] == S.MAX_CONSECUTIVE_FAIL, s2)

# 6) 手動匯入寫入同一張表
n = S.upsert_manual(sb, "0050", [("2025-01-02", 90.0)], "https://example.com/x")
check("手動匯入寫入並記錄來源", n == 1 and any(r.get("source") == "https://example.com/x" for r in sb.db["etf_price_daily"]))

# ------------------------------------------------------------ 2026-10-12 新增
# 7) 交叉比對：現價日沒有列時，取現價日之前最後一筆（成交稀少的 ETF）
ill = [("2026-09-10", 8.30), ("2026-09-16", 8.33)]
check("ref_close：現價日當天", S.ref_close([("2026-10-07", 9.0)], "2026-10-07") == 9.0)
check("ref_close：之前最後一筆", S.ref_close(ill, "2026-10-07") == 8.33)
check("ref_close：之前都沒有 → None", S.ref_close(ill, "2026-09-01") is None)
check("ref_close：無現價日 → 最後一筆", S.ref_close(ill, None) == 8.33)
raw_ill = [{"stock_id": "00625K", "date": d, "close": c} for d, c in ill]
r7, p7 = S.validate_rows("00625K", raw_ill, master_last=8.33, master_date="2026-10-07")
check("成交稀少：用之前最後一筆比對通過", r7 and not p7, p7)
r7b, p7b = S.validate_rows("00625K", raw_ill, master_last=9.5, master_date="2026-10-07")
check("成交稀少：之前最後一筆偏差 >5% → 退回", r7b == [] and p7b, p7b)

# 8) 跳過規則
td = dt.date(2026, 10, 12)
corp = {"delisted": {"0058": "2015-01-01", "0050": "2000-01-01"}}
check("下市且無現價 → 跳過", S.should_skip("0058", {"last_price": None}, corp, {}, td) == "delisted")
check("下市名單但仍有現價 → 不跳過（防代號重用）", S.should_skip("0050", {"last_price": 100}, corp, {}, td) == "")
check("30 天內確認無資料 → 跳過", S.should_skip("X", {"last_price": None}, {}, {"X": "2026-10-01"}, td) == "no_data_recent")
check("超過 30 天 → 重抓", S.should_skip("X", {"last_price": None}, {}, {"X": "2026-08-01"}, td) == "")

# 9) 分割與下市解析
sp_raw = [
    {"date": "2025-06-18", "stock_id": "0050", "type": "分割", "before_price": 188.65, "after_price": 47.16},
    {"date": "2024-12-11", "stock_id": "00632R", "type": "反分割", "before_price": 3.28, "after_price": 22.96},
    {"date": "2025-01-01", "stock_id": "2330", "type": "分割", "before_price": 100, "after_price": 50},
    {"date": "2025-02-01", "stock_id": "0052", "type": "合併", "before_price": 100, "after_price": 50},
    {"date": "2025-03-01", "stock_id": "0056", "type": "分割", "before_price": 30, "after_price": 29.5},
]
sp, sp_p = S.parse_splits(sp_raw, ["0050", "00632R", "0052", "0056"])
check("分割：只留清單內、型別合法、比例不近 1", [x["symbol"] for x in sp] == ["0050", "00632R"], sp)
check("分割：1 拆 4 比例約 4", abs(sp[0]["factor"] - 188.65 / 47.16) < 1e-6)
check("分割：反分割比例 <1", sp[1]["factor"] < 1)
check("分割：不合法列計數（型別、比例）", len(sp_p) == 2, sp_p)
dl = S.parse_delisting([{"stock_id": "0058", "date": "2010-01-01"}, {"stock_id": "0058", "date": "2012-01-01"},
                        {"stock_id": "2330", "date": "2020-01-01"}], ["0058", "0050"])
check("下市：只留清單內、取最晚日期", dl == {"0058": "2012-01-01"}, dl)

# 10) 配息驗證
dv_ok = [{"stock_id": "0056", "date": "2026-01-22", "stock_or_cache_dividend": "除息", "stock_and_cache_dividend": 1.0,
          "before_price": 37.0, "after_price": 36.0}]
rv, pv = S.validate_dividends("0056", dv_ok)
check("配息：除息前後價差相符 → 通過", rv and rv[0]["cash"] == 1.0 and not pv, pv)
dv_real = [{"stock_id": "0050", "date": "2025-01-17", "stock_or_cache_dividend": "息", "stock_and_cache_dividend": 2.7,
            "before_price": 198.05, "after_price": 195.35}]
check("配息：FinMind 實際類型「息」通過（0050 2025-01-17 實值）", S.validate_dividends("0050", dv_real)[0][0]["cash"] == 2.7)
check("配息：類型「息」價差不符也要退回", S.validate_dividends("0050", [dict(dv_real[0], after_price=190.0)])[1])
bad = [dict(dv_ok[0], after_price=35.0)]
check("配息：價差不符 → 整檔退回", S.validate_dividends("0056", bad)[1])
wq = [dict(dv_ok[0], stock_or_cache_dividend="權息", after_price=30.0)]
check("配息：權息不做價差核對（含股票股利）", S.validate_dividends("0056", wq)[0])
check("配息：純除權略過", S.validate_dividends("0056", [dict(dv_ok[0], stock_or_cache_dividend="除權")]) == ([], []))
check("配息：金額 0 → 退回", S.validate_dividends("0056", [dict(dv_ok[0], stock_and_cache_dividend=0)])[1])
check("配息：代號不符 → 退回", S.validate_dividends("0056", [dict(dv_ok[0], stock_id="0050")])[1])
check("配息：同日重複 → 退回", S.validate_dividends("0056", dv_ok * 2)[1])

# 11) 配息排程：從未抓過優先、最久未抓其次、7 天內不重抓、上限
due = S.due_for_dividends(["A", "B", "C", "D"], {"B": "2026-10-10", "C": "2026-09-01", "D": "2026-10-01"}, td, 3)
check("配息排程順序與間隔", due == ["A", "C", "D"], due)
check("配息排程上限", S.due_for_dividends(["A", "B", "C"], {}, td, 2) == ["A", "B"])

# 12) 配息同步與整體流程
sb5 = FakeSB([{"symbol": "0056", "last_price": 36.0, "price_date": "2026-01-22", "active": True},
              {"symbol": "0058", "last_price": None, "active": True}])
calls5 = []
def fetch5(url, params):
    calls5.append(dict(params))
    ds = params["dataset"]
    if ds == S.SPLIT_DATASET:
        return {"data": sp_raw}
    if ds == S.DELIST_DATASET:
        return {"data": [{"stock_id": "0058", "date": "2012-01-01"}]}
    if ds == S.DATASET:
        return {"data": [{"stock_id": params["data_id"], "date": "2026-01-21", "close": 37.0},
                         {"stock_id": params["data_id"], "date": "2026-01-22", "close": 36.0}]}
    if ds == S.DIV_DATASET:
        return {"data": dv_ok}
    raise AssertionError(ds)
msg5, s5 = S.run_all(sb5, fetch5, today=td, sleep=lambda s: None)
check("整體流程：下市代號不抓股價", not any(c.get("data_id") == "0058" for c in calls5), calls5)
check("整體流程：股價與配息都寫入", s5["price"]["synced"] == 1 and s5["dividend"]["rows_written"] == 1, s5)
check("整體流程：配息表欄位", sb5.db.get(S.DIV_TABLE, [{}])[0].get("cash") == 1.0 and sb5.db[S.DIV_TABLE][0].get("symbol") == "0056")
corp_saved = [r for r in sb5.db["system_config"] if r["config_key"] == S.CORP_KEY]
check("整體流程：公司行為（分割、下市）已存檔", corp_saved and '"delisted"' in corp_saved[0]["config_value"], corp_saved)
calls5.clear()
S.run_all(sb5, fetch5, today=td + dt.timedelta(days=1), sleep=lambda s: None)
check("隔天：配息 7 天內不重抓", not any(c["dataset"] == S.DIV_DATASET for c in calls5), calls5)

# 13) 分割表抓取失敗時沿用上次
sb6 = FakeSB([])
sb6.db["system_config"] = [{"config_key": S.CORP_KEY, "config_value": '{"splits": [{"symbol": "0050"}], "delisted": {}}'}]
def fetch_fail(url, params): raise RuntimeError("timeout")
corp6, m6 = S.sync_corporate_actions(sb6, fetch_fail, [], td)
check("公司行為抓取失敗沿用上次", corp6.get("splits") == [{"symbol": "0050"}] and "沿用" in m6, m6)

print("\n全部通過" if not FAIL else f"\n失敗 {FAIL}")
raise SystemExit(1 if FAIL else 0)
