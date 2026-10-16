import sys
import trust_hold_backtest as T
ok = True
def chk(n, c):
    global ok
    print(("PASS " if c else "FAIL ") + n)
    ok = ok and c
dates = [f"2025-{m:02d}-{d:02d}" for m in range(1, 13) for d in (1, 15)]
def mk(slope): return {"dates": dates, "px": [100 + slope * i for i in range(len(dates))], "shares": 1000}
per = {f"S{i}": mk(i * 0.1) for i in range(30)}
holds = {"2025-03-31": {f"S{i}": {"amt_active": i * 1000.0} for i in range(30)}, "2025-06-30": {f"S{i}": {"amt_active": i * 1000.0} for i in range(30)}}
rows = T.build_rows(per, holds, ["2025-03-31", "2025-06-30"])
chk("rows 產生", len(rows) > 0)
res = T.analyze(rows)
chk("W1 前20%超額>0（斜率與持股同向）", res["W1"]["持股占比前20%"]["mean_ex"] > 0)
chk("W1 後20%超額<0", res["W1"]["持股占比後20%(含0)"]["mean_ex"] < 0)
chk("可用日 = 季底+20天", T.AVAIL_DAYS == 20)
sys.exit(0 if ok else 1)
