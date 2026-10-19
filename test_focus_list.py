"""【2026-10-17】盤前主戰清單 → 10:00 修正（focus_list.py）。"""
import sys
import focus_list as F
ok = True


def chk(n, c):
    global ok
    print(("PASS: " if c else "FAIL: ") + n)
    ok = ok and c


M = ["查2.魚頭慢伏支撐", "查3.價值投資與循環"]
scan = [{"symbol": f"S{i}", "score": 3, "matched_commands": M, "card_snapshot": {"value_score": 60 + i * 2, "f_buy": 1 if i % 2 else -1}}
        for i in range(8)]
scan[5]["matched_commands"] = M + ["查6.營收雙增"]   # S5 有查6 → 波段排序最優先
scan.append({"symbol": "W", "score": 9, "matched_commands": M + ["查1.主升段突擊"], "card_snapshot": {"value_score": 99, "f_buy": 5, "hi20_break": True}})
scan.append({"symbol": "K8", "score": -3, "matched_commands": ["查8.昨日強勢動能延續"]})
scan.append({"symbol": "K8b", "score": 4, "matched_commands": ["查8.昨日強勢動能延續"]})
smart = [{"symbol": "M1", "patterns": ["a", "b"], "rev_yoy": 35, "broke_20d_high": True, "turnover_pct": 5},
         {"symbol": "M2", "patterns": ["a"], "rev_yoy": 50},
         {"symbol": "M3", "patterns": ["a", "b"], "rev_yoy": 10},
         {"symbol": "M4", "patterns": ["a", "b"], "rev_yoy": 40},
         {"symbol": "M5", "patterns": ["a", "b"], "rev_yoy": 40, "turnover_pct": 9}]
lst = F.build_preopen(scan, smart, score_map={"M4": -7})
tr = [x["track"] for x in lst]
chk("總數 ≤ 10", len(lst) <= 10)
chk("波段主力最多 3 檔", tr.count("波段主力") == 3)
chk("有⚠️追高警示的 W 不進前 5（無警示優先）", "W" not in [x["symbol"] for x in lst if x["track"] == "波段主力"])
chk("波段主力：查6 優先，其次價值分低的", [x["symbol"] for x in lst[:3]] == ["S5", "S0", "S1"] and "S7" not in [x["symbol"] for x in lst])
chk("減分標籤寫進理由（價值分≥70）", any("減分：價值分≥70" in x["why"] for x in F.rank_swing(scan, n=8)))
bear = F.build_preopen(scan, smart, score_map={"M4": -7}, bull=False)
chk("大盤多空只記錄、不改檔數（回測方向不一致）", sum(1 for x in bear if x["track"] == "波段主力") == 3)
mo = [x["symbol"] for x in lst if x["track"] == "短線動能"]
chk("短線動能：需2訊號＋年增>20、排除前晚評分≤−6", mo == ["M1", "M5"])
chk("空方觀察：查8（量比高優先）", set(x["symbol"] for x in lst if x["track"] == "空方觀察") == {"K8", "K8b"})
_b = F.build_preopen(scan, smart, bull=False)
chk("大盤空頭：查8 當沖空、短線動能不列（回測為負），波段主力照列", not any(x["track"] in ("空方觀察", "短線動能") for x in _b)
    and sum(1 for x in _b if x["track"] == "波段主力") == 3)
gp = [{"symbol": "G1", "direction": "short", "track": "空方觀察", "why": "x", "ref_close": 100},
      {"symbol": "G2", "direction": "short", "track": "空方觀察", "why": "x", "ref_close": 100}]
rg = F.revise_at_10(gp, open_px={"G1": 101.5, "G2": 100.3})
chk("查8：開高>1% 保留、沒開高剔除", [x["symbol"] for x in rg["keep"]] == ["G1"] and [x["symbol"] for x in rg["drop"]] == ["G2"])
chk("每筆有理由", all(x.get("why") for x in lst))
# 10:00 修正
bars_ok = [{"bar_time": f"09:{m:02d}", "open": 100 + m / 50, "high": 100.5 + m / 50, "low": 99.8 + m / 50, "close": 100.2 + m / 50}
           for m in range(0, 60, 5)]
bars_bad = [{"bar_time": f"09:{m:02d}", "open": 100 - m / 20, "high": 100.2 - m / 20, "low": 99 - m / 20, "close": 99.5 - m / 20}
            for m in range(0, 60, 5)]
chk("多方提示：守住開盤 → pass", F.long_entry_hint(bars_ok)["verdict"] == "pass")
chk("多方提示：跌破開盤 → fail", F.long_entry_hint(bars_bad)["verdict"] == "fail")
chk("多方提示：資料不足 → unknown", F.long_entry_hint(bars_ok[:3])["verdict"] == "unknown")
pre = [{"symbol": "A", "direction": "long", "track": "波段主力", "why": "x"},
       {"symbol": "B", "direction": "long", "track": "短線動能", "why": "x"},
       {"symbol": "C", "direction": "short", "track": "空方觀察", "why": "x"},
       {"symbol": "D", "direction": "short", "track": "空方觀察", "why": "x"},
       {"symbol": "E", "direction": "long", "track": "波段主力", "why": "x"}]
rv = F.revise_at_10(pre, gate_rows=[{"symbol": "C", "direction": "short", "gate3_verdict": "fail"},
                                    {"symbol": "D", "direction": "short", "gate3_verdict": "pass"}],
                    long_hints={"A": F.long_entry_hint(bars_ok), "B": F.long_entry_hint(bars_bad)})
chk("10:00 修正：A 保留、B 剔除、C 剔除、D 保留、E 資料不足保留",
    [x["symbol"] for x in rv["keep"]] == ["A", "D", "E"] and [x["symbol"] for x in rv["drop"]] == ["B", "C"])
chk("剔除都有原因", all(x.get("reason") for x in rv["drop"]))
sys.exit(0 if ok else 1)
