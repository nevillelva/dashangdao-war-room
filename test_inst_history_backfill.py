"""【2026-10-16】法人歷史回補：解析、驗證、交叉比對、續跑。"""
import sys, datetime as dt
import inst_history_backfill as B

fails = []
def check(n, c):
    print(("PASS: " if c else "FAIL: ") + n)
    if not c: fails.append(n)

T = dt.date(2026, 10, 16)
def row(d, name, buy, sell, sid="2330"): return {"date": d, "stock_id": sid, "name": name, "buy": buy, "sell": sell}
raw = [row("2025-01-02", "Foreign_Investor", 5000000, 2000000), row("2025-01-02", "Foreign_Dealer_Self", 9e9, 0),
       row("2025-01-02", "Investment_Trust", 100000, 300000), row("2025-01-02", "Dealer_self", 50000, 0),
       row("2025-01-02", "Dealer_Hedging", 20000, 10000), row("2025-01-03", "Foreign_Investor", 0, 1000000)]
rows, pr = B.parse_rows("2330", raw, today=T)
check("無問題", pr == [])
r0 = rows[0]
check("外資3000張、投信-200張、自營(50+10)=60張", r0["foreign_buy"] == 3000 and r0["trust_buy"] == -200 and r0["dealer_buy"] == 60)
check("缺名稱補0", rows[1]["foreign_buy"] == -1000 and rows[1]["trust_buy"] == 0)
r2, p2 = B.parse_rows("2330", [row("bad", "Foreign_Investor", 1, 1), row("2020-01-01", "Foreign_Investor", 1, 1),
                               row("2027-01-01", "Foreign_Investor", 1, 1), row("2025-01-02", "Foreign_Investor", "x", 1)], today=T)
check("日期壞/過早/未來/非數字全退回", r2 == [] and p2)
r3, p3 = B.parse_rows("2330", [row("2025-01-02", "Foreign_Investor", 1e12, 0)], today=T)
check("極端數值退回", r3 == [] and p3)
r4, p4 = B.parse_rows("2330", [row("2025-01-02", "Foreign_Investor", 1000, 0, sid="2317")], today=T)
check("代號不符退回", r4 == [])
ov, mm = B.cross_check(rows, {"2025-01-02": (3000, -200, 60), "2025-01-03": (-900, 0, 0)})
check("交叉比對：重疊2、不一致1", (ov, mm) == (2, 1))

class Q:
    def __init__(s, db, t): s.db, s.t, s.f, s.ign = db, t, {}, False; s.rows = None; s.op = "sel"
    def select(s, *a): s.op = "sel"; return s
    def eq(s, k, v): s.f[k] = v; return s
    def gte(s, *a): return s
    def order(s, *a, **k): return s
    def limit(s, *a): return s
    def range(s, *a): return s
    def upsert(s, rows, on_conflict=None, ignore_duplicates=False): s.op = "up"; s.rows = rows; s.ign = ignore_duplicates; return s
    def execute(s):
        class R: data = []
        r = R()
        if s.op == "up":
            for x in (s.rows if isinstance(s.rows, list) else [s.rows]):
                key = x.get("config_key") or (x["date"], x["symbol"])
                if s.ign and key in s.db[s.t]: continue
                s.db[s.t][key] = x
        elif s.t == "system_config":
            v = s.db["system_config"].get(s.f.get("config_key"))
            r.data = [v] if v else []
        elif s.t == "inst_holding":
            r.data = [dict(v) for k, v in s.db["inst_holding"].items() if k[1] == s.f.get("symbol")] if "symbol" in s.f else []
        return r
class SB:
    def __init__(s): s.db = {"system_config": {}, "inst_holding": {}}
    def table(s, n): return Q(s.db, n)

sb = SB()
sb.db["inst_holding"][("2025-01-02", "2330")] = {"date": "2025-01-02", "symbol": "2330", "foreign_buy": 1, "trust_buy": 2, "dealer_buy": 3}
calls = []
def fetch(url, params):
    calls.append(params["data_id"])
    if params["data_id"] == "9999": raise RuntimeError("boom")
    return {"data": raw if params["data_id"] == "2330" else []}
note, s = B.sync(sb, fetch, ["2330", "2317"], today=T, sleep=lambda x: None)
check("2330 寫入新日期、既有日期不覆蓋", sb.db["inst_holding"][("2025-01-02", "2330")]["foreign_buy"] == 1 and ("2025-01-03", "2330") in sb.db["inst_holding"])
check("2317 無資料也標完成", s["no_data"] == 1 and s["ok"] == 1)
check("不一致被計數（既有1/2/3 vs 3000/-200/60）", s["mismatch"] == 1 and s["overlap"] == 1)
calls.clear()
note2, s2 = B.sync(sb, fetch, ["2330", "2317"], today=T, sleep=lambda x: None)
check("續跑不重打已完成", calls == [] and s2["todo"] == 0)
sb2 = SB(); calls.clear()
note3, s3 = B.sync(sb2, fetch, ["9999"] * 1 + ["9998"] * 0, today=T, sleep=lambda x: None)
check("錯誤不標完成", s3["errors"] == 1 and "9999" not in B.load_state(sb2)["done"])
print(f"\n{len(fails)} failed"); sys.exit(1 if fails else 0)
