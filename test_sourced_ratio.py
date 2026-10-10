"""2026-10-10 死規則一：來源占比檔必須驗證後才併入台帳；AI 金鑰探測的判定邏輯。純函式，不連網。
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
SRC = "https://www.yuantaetfs.com/example"

rows = [
    {"symbol": "0056", "ex_date": "2026-07-21", "ratio_54c": 0.25, "source": SRC},        # 合格
    {"symbol": "0056", "ex_date": "2026-04-21", "ratio_54c": 0.3, "source": SRC},         # 合格（既有歷史日）
    {"symbol": "00919", "ex_date": "2026-09-16", "ratio_54c": 1.4, "source": SRC},        # 範圍錯
    {"symbol": "00919", "ex_date": "2026-09-17", "ratio_54c": 0.1, "source": SRC},        # 日期不是已知除息日
    {"symbol": "9999", "ex_date": "2026-09-16", "ratio_54c": 0.1, "source": SRC},         # 無此檔
    {"symbol": "00919", "ex_date": "2026-09-16", "ratio_54c": 0.1, "source": ""},         # 無來源
    {"symbol": "00919", "ex_date": "2026-09-16", "ratio_54c": 0.1, "source": "http://x"}, # 非 https
    {"symbol": "00919", "ex_date": "2026-09-16", "ratio_54c": None, "source": SRC},       # 無數字
    "not a dict",
]
new, applied, rejected = E.apply_sourced(ledger, rows, latest)
check("合格兩筆併入", applied == 2, applied)
rec = next(r for r in new["0056"] if r["ex_date"] == "2026-07-21")
check("併入後為 confirmed 且記錄來源", rec["status"] == "confirmed" and rec["ratio_54c"] == 0.25 and rec["source"].startswith("來源檔："), rec)
check("退回 7 筆", len(rejected) == 7, len(rejected))
check("00919 未被污染", "00919" not in new or all(r.get("status") != "confirmed" for r in new["00919"]))
check("原台帳不被改寫", ledger["0056"][0]["ratio_54c"] == 0.2)

# 已人工確認、數字相同 → 再次併入不應失敗
again, applied2, _ = E.apply_sourced(new, rows[:1], latest)
check("重跑冪等", applied2 == 1 and len(again["0056"]) == len(new["0056"]), again)

print("\n全部通過" if not FAIL else f"\n失敗 {len(FAIL)} 項：{FAIL}")
raise SystemExit(1 if FAIL else 0)
