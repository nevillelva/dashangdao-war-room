import datetime as dt
import trust_window_backtest as T
assert T.bucket_of(dt.date(2025, 6, 10))[0] == "季底前1個月"
assert T.bucket_of(dt.date(2025, 7, 8))[0] == "季初2週(結帳後)"
assert T.bucket_of(dt.date(2025, 5, 10))[0] == "季底前31~60天"
assert T.bucket_of(dt.date(2025, 7, 30))[0] == "其他"
assert T.quarter_bounds(dt.date(2025, 12, 31))[1] == dt.date(2025, 12, 31)
d0 = dt.date(2025, 5, 1)
dates = [(d0 + dt.timedelta(days=i)).isoformat() for i in range(120)]
px = [100 + i * 0.1 for i in range(120)]
tb = {d: (1.0 if i % 2 else -1.0) for i, d in enumerate(dates)}
rows = T.build_rows([{"sym": "A", "dates": dates, "px": px, "tbuy": tb, "vol": [1e6] * 120},
                     {"sym": "B", "dates": dates, "px": [100.0] * 120, "tbuy": {d: 1.0 for d in dates}, "vol": [2e6] * 120}])
res = T.analyze(rows)
assert res["n_stocks"] == 2 and res["基準(每天)"]["全部"]["10日"]["n"] == len(rows)
assert res["投信連買3日"]["全部"]["10日"]["n"] > 0
assert res["查4(投信買超>0)"]["季底前1個月_持有到季底"]["n"] > 0
assert "投信20日累計買超占量比前20%" in res
print("ok test_trust_window_backtest")
