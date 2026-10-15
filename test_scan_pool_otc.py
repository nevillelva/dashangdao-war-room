"""【2026-10-16】掃描池納入上櫃：分群挑日期、上櫃先到不縮池、關閉開關退回只掃上市、上櫃篩選。"""
import sys
import system_scheduler as S

fails = []
def check(n, c):
    print(("PASS: " if c else "FAIL: ") + n)
    if not c: fails.append(n)

TSE = [str(1000 + i) for i in range(600)]
OTC = [str(5000 + i) for i in range(80)]
DB = {  # date -> symbols
    "2026-10-15": set(OTC),                    # 上櫃先到、上市還沒到
    "2026-10-14": set(TSE) | set(OTC),
    "2026-10-13": set(TSE),
}
class Q:
    def __init__(s): s.f = {}; s.cols = None; s.rng = None
    def select(s, c): s.cols = c; return s
    def eq(s, k, v): s.f[k] = v; return s
    def order(s, *a, **k): return s
    def range(s, a, b): s.rng = (a, b); return s
    def execute(s):
        class R: data = []
        r = R()
        if s.cols == "date":
            rows = [{"date": d} for d in sorted(DB, reverse=True) for _ in DB[d]]
            r.data = rows[s.rng[0]:s.rng[1] + 1]
        else:
            syms = sorted(DB.get(s.f["date"], []))
            r.data = [{"symbol": x} for x in syms[s.rng[0]:s.rng[1] + 1]]
        return r
class SB:
    def table(s, n): return Q()

listed = set(TSE) | set(OTC)
S.OTC_SCAN_CODES = set(OTC)
pool, raw = S.get_scan_pool(SB(), listed)
check("上櫃先到時，上市取 10/14（齊全日）、上櫃取 10/15 → 合併", set(pool) == set(TSE) | set(OTC))
check("池內上市 600 檔未縮水", len([x for x in pool if x in set(TSE)]) == 600)

S.OTC_SCAN_CODES = set()
pool2, _ = S.get_scan_pool(SB(), set(TSE))
check("不納入上櫃：只回上市", set(pool2) == set(TSE))

# 開關與篩選
class R:
    def __init__(s, d): s.d = d
    def raise_for_status(s): pass
    def json(s): return s.d
rows = [{"stock_id": "5000", "type": "tpex"}, {"stock_id": "5001", "type": "tpex"}, {"stock_id": "5002", "type": "tpex"},
        {"stock_id": "1000", "type": "twse"}, {"stock_id": "0050", "type": "tpex"}]
quotes = [{"SecuritiesCompanyCode": "5000", "TradingAmount": "90,000,000"}, {"SecuritiesCompanyCode": "5001", "TradingAmount": "10,000,000"},
          {"SecuritiesCompanyCode": "1000", "TradingAmount": "99999999999"}, {"SecuritiesCompanyCode": "5002", "TradingAmount": "60000000"}]
S._OTC_CODES_CACHE.clear()
got = S.fetch_otc_scan_codes(rows, get=lambda *a, **k: R(quotes))
check("上櫃：成交值<5,000萬、非 tpex、非 4 碼普通股都排除", got == {"5000", "5002"})
S._OTC_CODES_CACHE.clear()
def boom(*a, **k): raise RuntimeError("down")
check("取行情失敗 → 空集合（退回只掃上市）", S.fetch_otc_scan_codes(rows, get=boom) == set())
S._OTC_CODES_CACHE.clear()
S.SCAN_INCLUDE_OTC = False
check("關閉開關 → 只回上市代號", S.fetch_listed_only_codes(rows) == {"1000"} and S.OTC_SCAN_CODES == set())
print(f"\n{len(fails)} failed"); sys.exit(1 if fails else 0)
