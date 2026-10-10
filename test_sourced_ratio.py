"""2026-10-10 多來源互證：來源占比檔必須≥2個獨立來源且數字一致才併入台帳。純函式，不連網。
python3 test_sourced_ratio.py"""
import etf_core as E

FAIL = []


def check(name, cond, extra=""):
    if not cond:
        FAIL.append(name)
        print("❌", name, extra)
    else:
        print("✅", name)


latest = {"0056": {"ex_date": "2026-07-21", "cash": 1.35, "pay_date": ""},
          "00919": {"ex_date": "2026-09-16", "cash": 1.1, "pay_date": ""}}
ledger = {"0056": [{"ex_date": "2026-04-21", "ratio_54c": 0.2, "status": "confirmed", "source": "old"}]}
A = "https://a.example.com/x"
B = "https://b.example.com/y"

rows = [
    {"symbol": "0056", "ex_date": "2026-07-21", "sources": [{"url": A, "ratio_54c": 0.3496}, {"url": B, "ratio_54c": 0.35}]},  # 合格
    {"symbol": "0056", "ex_date": "2026-04-21", "sources": [{"url": A, "ratio_54c": 0.3}]},                                       # 只有一個來源
    {"symbol": "0056", "ex_date": "2026-04-21", "sources": [{"url": A, "ratio_54c": 0.3}, {"url": A, "ratio_54c": 0.3}]},        # 同一網址算一個
    {"symbol": "00919", "ex_date": "2026-09-16", "sources": [{"url": A, "ratio_54c": 0.1}, {"url": B, "ratio_54c": 0.3}]},       # 數字對不上
    {"symbol": "00919", "ex_date": "2026-09-17", "sources": [{"url": A, "ratio_54c": 0.1}, {"url": B, "ratio_54c": 0.1}]},       # 日期不是已知除息日
    {"symbol": "9999", "ex_date": "2026-09-16", "sources": [{"url": A, "ratio_54c": 0.1}, {"url": B, "ratio_54c": 0.1}]},        # 無此檔
    {"symbol": "00919", "ex_date": "2026-09-16", "sources": [{"url": "http://x", "ratio_54c": 0.1}, {"url": B, "ratio_54c": 0.1}]},  # 非 https
    {"symbol": "00919", "ex_date": "2026-09-16", "sources": [{"url": A, "ratio_54c": 1.4}, {"url": B, "ratio_54c": 1.4}]},       # 範圍錯
    {"symbol": "00919", "ex_date": "2026-09-16", "sources": "bad"},
    "not a dict",
]
new, applied, rejected = E.apply_sourced(ledger, rows, latest)
check("只有兩個獨立來源且一致的一筆併入", applied == 1, applied)
rec = next(r for r in new["0056"] if r["ex_date"] == "2026-07-21")
check("併入為 confirmed，記錄兩個來源網址", rec["status"] == "confirmed" and abs(rec["ratio_54c"] - 0.3498) < 1e-6 and A in rec["source"] and B in rec["source"], rec)
check("退回 9 筆", len(rejected) == 9, len(rejected))
check("00919 未被寫入", "00919" not in new)
check("原台帳不被改寫", ledger["0056"][0]["ratio_54c"] == 0.2)
again, applied2, _ = E.apply_sourced(new, rows[:1], latest)
check("重跑冪等", applied2 == 1 and len(again["0056"]) == len(new["0056"]))

print("\n全部通過" if not FAIL else f"\n失敗 {len(FAIL)} 項：{FAIL}")
raise SystemExit(1 if FAIL else 0)
