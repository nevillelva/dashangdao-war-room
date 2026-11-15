"""戰卡第 3 項驗收腳本離線測試（合成 5 分 K 與三關判讀、假 Supabase；不連網）。"""
import io
import sys
from contextlib import redirect_stdout

import card3_accept as CA
import signal_cards as SC

fails = []


def check(name, cond):
    print(("PASS: " if cond else "FAIL: ") + name)
    if not cond:
        fails.append(name)


def bars(last_close=100.0, last_time="13:25"):
    return [{"symbol": "2330", "bar_time": "09:00", "open": 99.0, "high": 101, "low": 98, "close": 99.5, "volume": 100},
            {"symbol": "2330", "bar_time": last_time, "open": 99.5, "high": 101, "low": 99, "close": last_close, "volume": 200}]


GATE = {"symbol": "2330", "direction": "long", "gate1_verdict": "strong_bull", "gate2_verdict": "pass",
        "gate3_verdict": "pass", "overall_verdict": "pass", "overall_label": "三關通過", "detail": "x"}


def item(last=100.0, ref=100.0, gate=GATE, t="13:25", sym="2330", card_gate=None):
    g = dict(gate, symbol=sym) if gate else None
    card = SC.build_card(sym, "", bars(last, t), gate=(card_gate if card_gate is not None else g))
    return {"symbol": sym, "direction": "long", "card": card, "gate": g, "ref_close": ref}


# 純函式
check("誤差 0.5% 邊界不算超過", abs(CA.price_error_pct(100.5, 100) - 0.5) < 1e-9)
check("空值／零價 → None", CA.price_error_pct(None, 100) is None and CA.price_error_pct(100, 0) is None and CA.price_error_pct("x", 1) is None)
check("13:25 算已收盤、09:30 不算", CA.is_closed_card({"time": "13:25"}) and CA.is_closed_card({"time": "2026-11-13 13:30:00"}) and not CA.is_closed_card({"time": "09:30"}))
check("卡片結論與三關獨立換算一致", CA.conclusion_from_card(item()["card"]) == CA.conclusion_from_gate(GATE))

# 單日
d_ok = CA.evaluate_day("2026-11-13", [item(100.4, 100.0)])
check("誤差 0.4% 且結論一致 → 合格", d_ok["passed"] and d_ok["price_fail"] == 0 and d_ok["conclusion_rate"] == 1.0)
d_bad = CA.evaluate_day("2026-11-13", [item(101.0, 100.0)])
check("誤差 1% → 不合格", not d_bad["passed"] and d_bad["price_fail"] == 1 and d_bad["fails"][0]["kind"] == "price")
# 卡片結論被改（模擬卡片與資料庫不一致）
wrong = dict(GATE, overall_label="別的說明")
d_cons = CA.evaluate_day("2026-11-13", [item(100.0, 100.0, card_gate=wrong)])
check("結論不一致 → 一致率 0 → 不合格", not d_cons["passed"] and d_cons["conclusion_fail"] == 1 and d_cons["conclusion_rate"] == 0.0)
d_mix = CA.evaluate_day("x", [item(sym="2330"), item(sym="2317"), item(sym="1101", card_gate=wrong)])
check("2/3 一致（66.7%）不算過", not d_mix["passed"] and abs(d_mix["conclusion_rate"] - 2 / 3) < 1e-9)
d_open = CA.evaluate_day("x", [item(t="10:00", ref=50.0)])
check("未收盤不比價（略過，不算不合格）但無比價樣本也不算有資料", d_open["price_skipped"] == 1 and d_open["price_fail"] == 0 and not d_open["has_data"] and not d_open["passed"])
d_nogate = CA.evaluate_day("x", [item(gate=None)])
check("沒有三關判讀不納入分母", d_nogate["no_gate"] == 1 and d_nogate["conclusion_checked"] == 0 and not d_nogate["passed"])
d_noref = CA.evaluate_day("x", [item(ref=None)])
check("沒有參考價略過比價", d_noref["price_skipped"] == 1 and not d_noref["has_data"])

# 5 天
good = [dict(d_ok, date=f"2026-11-{20 - i:02d}") for i in range(5)]
check("5 天全過 → pass", CA.evaluate_run(good)["status"] == "pass")
check("4 天 → insufficient（不當通過）", CA.evaluate_run(good[:4])["status"] == "insufficient")
check("5 天中有 1 天不合格 → fail", CA.evaluate_run(good[:2] + [dict(d_bad, has_data=True)] + good[3:])["status"] == "fail")
check("無資料的日子不佔 5 天名額", CA.evaluate_run([dict(d_open)] + good)["status"] == "pass")
check("只看最近 5 個有資料日（更舊的失敗不影響）", CA.evaluate_run(good + [dict(d_bad)])["status"] == "pass")


# 薄 I/O：假 Supabase
class R:
    def __init__(self, d): self.data = d


class Q:
    def __init__(self, db, name):
        self.db, self.name, self.f, self.ins, self.lim = db, name, [], None, None
    def select(self, *_): return self
    def eq(self, k, v): self.f.append(lambda r: str(r.get(k))[:10] == v); return self
    def in_(self, k, v): self.f.append(lambda r: r.get(k) in v); return self
    def order(self, *_a, **_k): return self
    def limit(self, n): self.lim = n; return self
    def insert(self, row): self.ins = row; return self
    def execute(self):
        if self.ins is not None:
            self.db.setdefault(self.name, []).append(self.ins)
            return R([])
        rows = [r for r in self.db.get(self.name, []) if all(f(r) for f in self.f)]
        return R(rows[:self.lim] if self.lim else rows)


class SB:
    def __init__(self, db): self.db = db
    def table(self, n): return Q(self.db, n)


days = [f"2026-11-{d:02d}" for d in (20, 19, 18, 17, 16, 13)]
db = {"intraday_gate_results": [dict(GATE, trade_date=d, created_at=d) for d in days],
      "intraday_5min_bars": [dict(b, trade_date=d) for d in days for b in bars(100.2)],
      "twse_market_snapshot": [{"symbol": "2330", "trade_date": d, "close_price": 100.0} for d in days]}
sb = SB(db)
buf = io.StringIO()
with redirect_stdout(buf):
    res = CA.run(sb, run_id="t")
check("端到端 6 天資料 → pass，評估 5 天", res["status"] == "pass" and res["days_evaluated"] == 5 and res["days_passed"] == 5)
rep = db["ui_selftest_reports"][0]
check("寫入 ui_selftest_reports(summary=card3_accept)", rep["summary"] == "card3_accept" and rep["report"]["counts"]["status"] == "pass")
check("日誌只印數量", "2330" not in buf.getvalue() and "status pass" in buf.getvalue())

db["twse_market_snapshot"][0]["close_price"] = 90.0       # 最新一天參考價差 11% → 該卡不合格
with redirect_stdout(io.StringIO()):
    res2 = CA.run(SB(db), write=False)
check("參考價不符 → 整體 fail", res2["status"] == "fail" and res2["days"][0]["price_fail"] == 1)

with redirect_stdout(io.StringIO()):
    res3 = CA.run(SB({}), write=False)
check("沒有任何資料 → insufficient", res3["status"] == "insufficient")

sys.exit(1 if fails else 0)
