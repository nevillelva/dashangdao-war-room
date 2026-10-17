"""多方第一關改為僅參考：弱勢空方不再讓整體 fail；空方規則不變。"""
import sys
import warroom_core as W
ok = True
def chk(n, c):
    global ok
    print(("PASS " if c else "FAIL ") + n)
    ok = ok and c
bars = [
    {"bar_time": "09:20", "open": 100, "high": 101, "low": 99.8, "close": 100.8, "volume": 1000},
    {"bar_time": "09:25", "open": 100.8, "high": 100.9, "low": 100.0, "close": 100.2, "volume": 900},
]
g1 = W.evaluate_930_gate1(W.bars_to_hist_df(bars), end_label=True)
chk("樣本第一關＝空方", g1["verdict"] in ("weak_bear", "strong_bear"))
r = W.evaluate_930_three_gate(bars, None, direction="long", end_label=True)
chk("多方：第一關偏空不再 fail", r["overall_verdict"] != "fail" or "第一關" not in r["overall_label"].split("；")[-1])
chk("多方：標籤註明不否決並附回測數據", "不否決" in (r.get("overall_label") or "") and "37,329" in (r.get("overall_label") or ""))
rs = W.evaluate_930_three_gate([dict(b, close=b["close"]) for b in bars], None, direction="short", end_label=True)
chk("空方：第一關偏空仍可往下走（不因第一關 fail）", not rs["overall_label"].startswith("第一關多") )
chk("開關預設 False", W.LONG_GATE1_VETO is False)
sys.exit(0 if ok else 1)
