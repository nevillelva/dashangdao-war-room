import ex_div_runup as R
dates = [f"2025-{m:02d}-{d:02d}" for m in (1, 2, 3, 4, 5, 6) for d in range(1, 29)]
px = [100.0] * len(dates)
ex = dates[100]
s = R._idx_on_or_after(dates, ex)
for i in range(s - 20, s):           # 除息前20日緩漲到 110
    px[i] = 100 + (i - (s - 20)) * 0.5
for i in range(s, len(px)):
    px[i] = 95.0
rets = R.event_returns(dates, px, [ex])
r20 = [r for r in rets if r["k"] == 20][0]
assert abs(r20["ret"] - (px[s - 1] / px[s - 20] - 1)) < 1e-9 and r20["ret"] > 0.09
res = R.analyze([{"sym": "X", "dates": dates, "px": px, "events": [(ex, 3.0)]}])
assert res["n_events_total"] == 1 and res["k20"]["all"]["n"] == 1 and res["k20"]["all"]["win%"] == 100.0
print("ok test_ex_div_runup")
