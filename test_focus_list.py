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
chk("大盤季線下波段主力只留 1 檔並標小部位（2026-10-21 全上市回測）", sum(1 for x in bear if x["track"] == "波段主力") == 1
    and all("季線下" in x["why"] for x in bear if x["track"] == "波段主力"))
chk("大盤季線上波段主力理由附持有 20 日建議", all("20 個交易日" in x["why"] for x in lst if x["track"] == "波段主力"))
mo = [x["symbol"] for x in lst if x["track"] == "短線動能"]
chk("短線動能：需2訊號＋年增>20、排除前晚評分≤−6", mo == ["M1", "M5"])
chk("空方觀察：查8（量比高優先）", set(x["symbol"] for x in lst if x["track"] == "空方觀察") == {"K8", "K8b"})
_b = F.build_preopen(scan, smart, bull=False)
chk("大盤空頭：短線動能不列，波段主力只留 1 檔；查8 空頭照列（2026-10-21 大樣本：線下較好）", not any(x["track"] == "短線動能" for x in _b)
    and sum(1 for x in _b if x["track"] == "波段主力") == 1 and any(x["symbol"] in ("K8", "K8b") for x in _b))
_b20 = F.build_preopen(scan, smart, bull=True, bull20=True)
chk("加權在 20 日線上：查8 不列", not any(x["symbol"] in ("K8", "K8b") for x in _b20))
_b20n = F.build_preopen(scan, smart, bull=True, bull20=False)
chk("加權在 20 日線下：查8 照列", any(x["symbol"] in ("K8", "K8b") for x in _b20n))
gp = [{"symbol": "G1", "direction": "short", "track": "空方觀察", "why": "x", "ref_close": 100, "cmds": ["查8.x"]},
      {"symbol": "G2", "direction": "short", "track": "空方觀察", "why": "x", "ref_close": 100, "cmds": ["查8.x"]},
      {"symbol": "G3", "direction": "short", "track": "空方觀察", "why": "x", "ref_close": 100, "cmds": []}]
rg = F.revise_at_10(gp, open_px={"G1": 103.5, "G2": 101.5, "G3": 101.5})
gp.append({"symbol": "G4", "direction": "short", "track": "空方觀察", "why": "x", "ref_close": 100, "cmds": ["查8.x"]})
rg4 = F.revise_at_10(gp, open_px={"G1": 103.5, "G2": 101.5, "G3": 101.5, "G4": 107.5})
chk("開高≥7% 離漲停太近 → 剔除並說明", [x["symbol"] for x in rg4["drop"]] == ["G2", "G4"] and "不做" in [x for x in rg4["drop"] if x["symbol"] == "G4"][0]["reason"])
rg5 = F.revise_at_10(gp[:1], open_px={"G1": 103.5}, last_px={"G1": 109.5})
chk("盤中離漲停≤1% 出現警示（保留但提醒）", "離漲停只剩 0.5%" in rg5["keep"][0]["reason"] and rg5["keep"][0]["reason"].startswith("⚠️"))
rg6 = F.revise_at_10(gp[:1], open_px={"G1": 103.5}, last_px={"G1": 105.0})
chk("離漲停 5% 不警示", "⚠️" not in rg6["keep"][0]["reason"])
chk("limit_up_dist_pct：昨收 15.6、現價 17.10 → 約 0.4%", abs(F.limit_up_dist_pct(15.6, 17.10) - 0.3846) < 1e-3)
chk("查8：開高≥3% 保留、1~3% 剔除；分點空單維持 1%", [x["symbol"] for x in rg["keep"]] == ["G1", "G3"] and [x["symbol"] for x in rg["drop"]] == ["G2"]
    and "風險" in rg["keep"][0]["reason"])
dtf = [{"symbol": "L1", "pct": 35, "ref_close": 50}, {"symbol": "L2", "pct": 12, "ref_close": 40}, {"symbol": "L3", "pct": 22, "ref_close": 30}]
_d = F.build_preopen(scan, smart, score_map={"M4": -7}, dt_flags=dtf, bull=True)
_sd = [x["symbol"] for x in _d if x["track"] == "空方觀察"]
chk("分點≥20% 的漲停股優先進空方觀察（L1、L3 排前；L2 未達 20% 不列），總數 ≤ 14", _sd[:2] == ["L1", "L3"] and "L2" not in _sd and len(_d) <= 14)
_od = F.rank_overnight([{"symbol": "Q7", "matched_commands": ["查7.隔日沖漲停"], "card_snapshot": {"price": 50, "vol_ratio": 2}},
                        {"symbol": "Q78", "matched_commands": ["查7.隔日沖漲停", "查8.x"], "card_snapshot": {"price": 40, "vol_ratio": 1}}])
chk("查7 進主戰清單：同時中查8 排前、方向 exit", [x["symbol"] for x in _od] == ["Q78", "Q7"] and all(x["direction"] == "exit" for x in _od))
_ro = F.revise_at_10(_od, open_px={"Q78": 41.5, "Q7": 49})
chk("查7 開盤：一律保留；同時查8 且開高≥3% 註明可當沖空", len(_ro["keep"]) == 2 and "當沖空" in _ro["keep"][0]["reason"] and "當沖空" not in _ro["keep"][1]["reason"])
chk("查7 賣點：開高 3.75% → 09:05～09:15；開低 → 撐到收盤", "09:05～09:15" in _ro["keep"][0]["reason"] and "撐到收盤" in _ro["keep"][1]["reason"])
chk("查7 賣點：開高≥5% → 開盤直接賣", "開盤直接賣" in F.exit_plan(6.0))
chk("大盤空頭不列分點空單", not any(x["symbol"] in ("L1", "L3") for x in F.build_preopen(scan, smart, dt_flags=dtf, bull=False)))
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
chk("盤中修正：多方一律保留並給下單時段（A、B、E），C 剔除、D 保留",
    [x["symbol"] for x in rv["keep"]] == ["A", "B", "D", "E"] and [x["symbol"] for x in rv["drop"]] == ["C"]
    and all("12:30～13:20" in x["reason"] for x in rv["keep"] if x["direction"] == "long"))
chk("剔除都有原因", all(x.get("reason") for x in rv["drop"]))
# ── 價位（2026-10-22）
import numpy as np
import pandas as pd
_idx = pd.bdate_range("2026-09-01", periods=30)
_c = np.linspace(100, 129, 30)
_df = pd.DataFrame({"Open": _c, "High": _c + 1, "Low": _c - 1, "Close": _c}, index=_idx)
_lv = F.levels_from_daily(_df)
chk("價位：昨收＝最後收盤、ATR≈2、MA5＝近5日均", _lv and _lv["ref"] == 129.0 and abs(_lv["atr"] - 2.0) < 0.01 and abs(_lv["ma5"] - 127.0) < 1e-9)
chk("價位：防守線＝MA5−0.5ATR、上緣＝收盤＋ATR", abs(_lv["def"] - (127.0 - 0.5 * _lv["atr"])) < 0.01 and abs(_lv["atk"] - (129 + _lv["atr"])) < 0.01)
chk("價位：當天那根（盤中未完成）不算", F.levels_from_daily(_df, "2026-10-12")["ref"] < 129.0)
chk("價位：資料不足回 None", F.levels_from_daily(_df.head(10)) is None)
_pl = F.price_lines({"direction": "long", "track": "波段主力", "lv": _lv})
chk("做多主力價位行：市價下單、停損 −10%、不設進場上限", _pl and _pl[0][0] == "進場價" and "不設上限" in _pl[0][1] and any(l == "停損" and "−10%" in t_ and f"{129 * 0.9:.2f}" in t_ for l, t_ in _pl))
_pm = F.price_lines({"direction": "long", "track": "短線動能", "lv": _lv})
chk("動能價位行：高於昨收 2%（131.58）不追、停損 −5%", "131.58" in _pm[0][1] and any(l == "停損" and "−5%" in t_ for l, t_ in _pm))
chk("防守線只當參考、標明不能當波段停損", any(l == "戰卡防守線" and "不能拿來停損" in t_ for l, t_ in _pl))
_ps = F.price_lines({"direction": "short", "cmds": ["查8.x"], "ref_close": 100.0})
chk("做空價位行：進場帶 103～107、回補 109、漲停 110", "103.00～107.00" in _ps[0][1] and "109.00" in _ps[1][1] and "110.00" in _ps[1][1])
_pe = F.price_lines({"direction": "exit", "ref_close": 100.0})
chk("查7 持有者價位行：開高≥5% 為 105", "105.00" in _pe[0][1])
chk("沒有昨收就不給價位", F.price_lines({"direction": "long", "track": "波段主力"}) == [])
sys.exit(0 if ok else 1)
