"""factor_card_shadow 離線測試。python3 test_factor_card_shadow.py"""
import json
import factor_card_shadow as F

FAIL = []


def check(name, cond, extra=""):
    if cond:
        print("✅", name)
    else:
        FAIL.append(name)
        print("❌", name, extra)


check("日期正規化", F.norm_date("20261005") == "2026-10-05" and F.norm_date("2026-10-05") == "2026-10-05" and F.norm_date("x") == "")
cards = [{"symbol": "2330", "payload": json.dumps({"score": 7})}, {"symbol": "2317", "payload": {"score": -3}},
         {"symbol": "1101", "payload": "not json"}, {"symbol": "9999", "payload": json.dumps({"score": 5})},
         {"symbol": "2454", "payload": json.dumps({"score": None})}]
facs = [{"symbol": "2330", "total_score_default_weight": 8}, {"symbol": "2317", "total_score_default_weight": -2},
        {"symbol": "2454", "total_score_default_weight": 1}, {"symbol": "1101", "total_score_default_weight": 3}]
p = F.pair_scores(cards, facs)
check("配對只含兩邊可讀者", [x["symbol"] for x in p] == ["2330", "2317"], p)
rows = [{"card_score": 7, "factor_score": 8, "ret": 0.02}, {"card_score": 6, "factor_score": 2, "ret": -0.01},
        {"card_score": -7, "factor_score": -6, "ret": 0.0}, {"card_score": 1, "factor_score": 0, "ret": None}]
s = F.summarize(rows)
check("忽略無報酬列", s["n"] == 3, s)
check("同側一致率 2/3", s["same_side_pct"] == 66.7, s)
check("戰卡≥6 平均報酬 0.5%", s["card_ge_thr"] == {"n": 2, "avg_ret_pct": 0.5}, s)
check("樣本<3 無相關", F.summarize(rows[:2])["corr_card_factor"] is None)
print("\n全部通過" if not FAIL else f"\n失敗 {FAIL}")
raise SystemExit(1 if FAIL else 0)
