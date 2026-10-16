import sys
from datetime import date, datetime, timedelta
import gate1_backtest as G
ok = True
def chk(n, c):
    global ok
    print(("PASS " if c else "FAIL ") + n)
    ok = ok and c

def mkday(d, o, closes):
    """closes: 5 分K 收盤序列（第1根起始 09:00）。"""
    bars, prev = [], o
    for i, c in enumerate(closes):
        s = datetime(d.year, d.month, d.day, 9, 0) + timedelta(minutes=5 * i)
        bars.append({"date": d, "end": s + timedelta(minutes=5), "o": prev, "h": max(prev, c), "l": min(prev, c), "c": c, "v": 100.0})
        prev = c
    return bars

d1, d2 = date(2026, 1, 5), date(2026, 1, 6)
# 往上的股票與往下的股票各 6 根（09:00~09:30），之後一根收盤
up = mkday(d1, 100, [101, 102, 103, 104, 105, 106, 110]) + mkday(d2, 110, [111, 112, 113, 114, 115, 116, 120])
dn = mkday(d1, 100, [99, 98, 97, 96, 95, 94, 90]) + mkday(d2, 90, [89, 88, 87, 86, 85, 84, 80])
per = {"UP": up, "DN": dn}
# 假 gate：最後一根是紅就多、黑就空
def gate(b25, b30):
    return "weak_bull" if b30["c"] > b30["o"] else ("weak_bear" if b30["c"] < b30["o"] else "unclear")
r = G.analyze(per, gate)
chk("列數：2 檔 × 2 日", r["n_rows"] == 4)
chk("判多組當日超額為正", r["by_gate_dir"]["bull"]["eod"]["excess"] > 0)
chk("判空組當日超額為負", r["by_gate_dir"]["bear"]["eod"]["excess"] < 0)
chk("累計方向與判定一致時 conflict 為空", r["conflict"]["判空_但開盤至今漲"]["eod"]["n"] == 0)
chk("次日只有第一天有", r["by_gate_dir"]["bull"]["nd"]["n"] == 1)
sys.exit(0 if ok else 1)
