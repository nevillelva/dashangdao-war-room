import pandas as pd
import provisional_eval as P

def _df():
    return pd.DataFrame({"Open": [100, 102, 105], "Close": [101, 104, 103]}, index=pd.to_datetime(["2026-10-16", "2026-10-19", "2026-10-20"]))

def test_q7_locked_and_done():
    r = P.eval_q7(109.5, 100, 110, _df(), "2026-10-19")
    assert r["status"] == "locked_skip"
    r = P.eval_q7(106, 100, 110, _df(), "2026-10-20")
    assert r["status"] == "done" and r["r_sell_open"] == round((105 / 106 - 1) * 100 - P.COST, 3)

def test_long10():
    bars = [{"bar_time": f"09:{m:02d}", "open": 100, "high": 101, "low": 99.8, "close": 100.5} for m in range(0, 60, 5)]
    r = P.eval_long10(bars, _df(), "2026-10-19", lambda b: {"verdict": "pass"})
    assert r["status"] == "done" and r["e10"] == 100.5 and r["r_e10_close"] == round((104 / 100.5 - 1) * 100 - P.COST, 3)
    assert P.summarize([r], "r_e10_close")["n"] == 1
