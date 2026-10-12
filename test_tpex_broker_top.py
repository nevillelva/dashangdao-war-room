"""櫃買券商排行補充來源的離線測試。python3 test_tpex_broker_top.py"""
from datetime import date

import tpex_broker_top as T

FAIL = []


def check(name, cond, extra=""):
    if cond:
        print("✅", name)
    else:
        FAIL.append(name)
        print("❌", name, extra)


def row(d="20261012", name="富喬(1815)", sr="1", firm="元大", fr="1", buy="11596", sell="9824"):
    return {"Date": d, "StockRanking": sr, "SecuritiesCompanyCodeAndCompanyName": name, "SecuritiesFirmsRanking": fr,
            "SecuritiesFirmsCode": firm, "TotalPurchaseShares": buy, "TotalSellShares": sell}


TODAY = date(2026, 10, 12)
g, b = T.parse_rows([row()], today=TODAY)
check("正常列", len(g) == 1 and g[0]["symbol"] == "1815" and g[0]["stock_name"] == "富喬" and g[0]["log_date"] == "2026-10-12", g)
check("數量轉整數", g[0]["buy_shares"] == 11596 and g[0]["sell_shares"] == 9824)
g, b = T.parse_rows([row(d="2026-10-12")], today=TODAY)
check("日期格式不符退回", not g and b.get("日期格式") == 1, b)
g, b = T.parse_rows([row(d="20261013")], today=TODAY)
check("未來日期退回", not g and "日期超出範圍" in b, b)
g, b = T.parse_rows([row(d="20260901")], today=TODAY)
check("過舊日期退回", not g and "日期超出範圍" in b, b)
g, b = T.parse_rows([row(name="沒有代號")], today=TODAY)
check("代號格式退回", not g and "代號格式" in b)
g, b = T.parse_rows([row(buy="-5")], today=TODAY)
check("負數退回", not g and "券商或數量無效" in b)
g, b = T.parse_rows([row(firm="")], today=TODAY)
check("缺券商退回", not g)
g, b = T.parse_rows([row(buy="1,234")], today=TODAY)
check("千分位可解析", g and g[0]["buy_shares"] == 1234)

# 批次驗證
rows = []
for i in range(6):
    for f in ("元大", "富邦"):
        rows += T.parse_rows([row(name=f"股{i}({2000 + i})", firm=f)], today=TODAY)[0]
ok, why, st = T.validate_batch(list(rows))
check("批次通過", ok and st["stocks"] == 6, (ok, why))
ok, why, _ = T.validate_batch(rows[:4])
check("檔數過少退回", not ok)
dup = rows + rows[:1]
ok, why, _ = T.validate_batch(list(dup))
check("券商重複退回", not ok and "重複" in why, why)
ok, why, _ = T.validate_batch(list(rows), known_symbols={"9999"})
check("交叉比對失敗退回", not ok and "交叉比對" in why, why)
rr = list(rows)
ok, why, st = T.validate_batch(rr, known_symbols={str(2000 + i) for i in range(6)} | {"x"})
check("交叉比對通過", ok and st["rows_after_xcheck"] == 12)
mixed = rows + T.parse_rows([row(d="20261009", name="另一(3000)")], today=TODAY)[0]
ok, why, _ = T.validate_batch(mixed)
check("多個資料日退回", not ok)

n = T.net_by_firm([{"firm": "A", "buy_shares": 10, "sell_shares": 30}, {"firm": "B", "buy_shares": 50, "sell_shares": 20}])
check("買賣超排序", n[0]["firm"] == "B" and n[0]["net"] == 30 and n[1]["net"] == -20, n)


# sync 用假資料庫
class _Q:
    def __init__(self, db, name): self.db, self.name, self.f = db, name, {}
    def select(self, *a): return self
    def eq(self, k, v): self.f[k] = v; return self
    def limit(self, n): return self
    def lt(self, k, v): self.f[("lt", k)] = v; return self
    def upsert(self, rows, on_conflict=None): self.db.written += rows; return self
    def delete(self): self.db.deleted = True; return self
    def execute(self):
        class R: pass
        r = R()
        if self.name == "tpex_broker_top": r.data = [{"symbol": "x"}] if self.db.have else []
        else: r.data = [{"symbol": str(2000 + i)} for i in range(6)]
        return r


class _DB:
    def __init__(self, have=False): self.have, self.written, self.deleted = have, [], False
    def table(self, name): return _Q(self, name)


db = _DB()
msg = T.sync(db, fetch=lambda: [r for r in [row(name=f"股{i}({2000 + i})", firm=f) for i in range(6) for f in ("元大", "富邦")]], today=TODAY)
check("sync 寫入", len(db.written) == 12 and "寫入 12 列" in msg, msg)
db2 = _DB(have=True)
check("已有當日資料不重寫", not db2.written and "略過" in T.sync(db2, fetch=lambda: [row()], today=TODAY))
check("抓取失敗不拋例外", "抓取失敗" in T.sync(_DB(), fetch=lambda: (_ for _ in ()).throw(ConnectionError("x")), today=TODAY))

print("\n全部通過" if not FAIL else f"\n失敗 {len(FAIL)} 項：{FAIL}")
raise SystemExit(1 if FAIL else 0)
