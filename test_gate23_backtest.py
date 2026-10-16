import sys
from datetime import date, datetime, timedelta
import gate23_backtest as G
import warroom_core as W
ok = True
def chk(n, c):
    global ok
    print(("PASS " if c else "FAIL ") + n)
    ok = ok and c
def mkday(d, o, closes, vol=100.0):
    out, p = [], o
    for i, c in enumerate(closes):
        s = datetime(d.year, d.month, d.day, 9, 0) + timedelta(minutes=5 * i)
        out.append({"date": d, "end": s + timedelta(minutes=5), "o": p, "h": max(p, c) * 1.001, "l": min(p, c) * 0.999, "c": c, "v": vol})
        p = c
    return out
days = [date(2026, 1, 5) + timedelta(days=i) for i in range(4)]
up = sum((mkday(d, 100, [100 + 0.3 * j for j in range(1, 55)]) for d in days), [])
dn = sum((mkday(d, 100, [100 - 0.3 * j for j in range(1, 55)]) for d in days), [])
per = {"UP": up, "DN": dn, "LD": up}
rows = G.build(per, {"UP": "LD", "DN": "LD"}, set(), W.evaluate_930_three_gate, W.evaluate_short_gate3_rebound)
chk("有列（多空各一）", len(rows) >= 12)
chk("空方第三關：下跌股通過", any(r["sym"] == "DN" and r["side"] == "short" and r["sg3"] == "pass" for r in rows))
chk("空方第三關：上漲股不通過", any(r["sym"] == "UP" and r["side"] == "short" and r["sg3"] == "fail" for r in rows))
res = G.analyze(rows)
chk("分析有多空兩邊", "long" in res and "short" in res)
sys.exit(0 if ok else 1)
