import sector_map as sm

ok = 0
def check(c, m):
    global ok
    assert c, m
    ok += 1
    print("✅", m)

check(sm.normalize_sector("金融業") == "金融保險" and sm.normalize_sector(" 數位雲端類 ") == "數位雲端", "別名統一")
check(sm.normalize_sector(None) is None and sm.normalize_sector("  ") is None, "空值回 None")
check(sm.pick_sector(["電子工業", "半導體業"]) == "半導體業", "細分類優先於粗分類(電子工業)")
check(sm.pick_sector(["半導體業", "電子工業"]) == "半導體業", "與列順序無關")
check(sm.pick_sector(["電子工業"]) == "電子其他(未細分)", "只有粗分類 → 未細分")
check(sm.pick_sector(["其他"]) == "其他", "其他")
check(sm.pick_sector(["ETF"]) is None and sm.pick_sector(["存託憑證"]) is None, "ETF/存託憑證排除")
check(sm.pick_sector(["數位雲端類", "資訊服務業"]) == "資訊服務業", "標準產業優先於櫃買主題")
check(sm.pick_sector(["綠能環保類"]) == "綠能環保", "主題別名")
check(sm.pick_sector([]) is None and sm.pick_sector(None) is None, "沒有分類")
check(sm.pick_sector(["新奇分類B", "新奇分類A"]) == "新奇分類A", "未知分類取字典序，確定性")
rows = [{"stock_id": "2330", "industry_category": "電子工業"}, {"stock_id": "2330", "industry_category": "半導體業"},
        {"stock_id": "2881", "industry_category": "金融業"}, {"stock_id": "0050", "industry_category": "ETF"}]
m = sm.build_sector_map(rows)
check(m == {"2330": "半導體業", "2881": "金融保險"}, f"build_sector_map：{m}")
check(sm.sector_map_from_flat({"1101": "水泥工業", "2330": "電子工業", "X": "ETF"}) == {"1101": "水泥工業", "2330": "電子其他(未細分)"}, "備援扁平對照")
syms = [f"{1000+i}" for i in range(30)]
base = {s: ("A" if i < 20 else "B" if i < 25 else "C") for i, s in enumerate(syms[:28])}   # 最後 2 檔沒分類
out, cnt = sm.merge_small(base, syms, min_n=12)
check(cnt == {"A": 20, sm.SMALL_NAME: 10}, f"小族群併入、無分類歸入小族群：{cnt}")
check(out[syms[22]] == sm.SMALL_NAME and out[syms[0]] == "A", "個別對照正確")
print(f"\n{ok} 項通過")

# ---- winrate_by_sector
rows = [{"symbol": "2330", "side": "long", "realized_roi": 3.0}, {"symbol": "2330", "side": "long", "realized_roi": -1.0},
        {"symbol": "2303", "side": "long", "realized_roi": 2.0}, {"symbol": "9999", "side": "short", "realized_roi": 0.5},
        {"symbol": "2881", "side": "short", "realized_roi": -2}, {"symbol": "2881", "side": "x", "realized_roi": 1},
        {"symbol": "2881", "side": "short", "realized_roi": None}]
mm = {"2330": "半導體業", "2303": "半導體業", "2881": "金融保險"}
w = sm.winrate_by_sector(rows, mm)
d = {(x["side"], x["sector"]): x for x in w}
check(d[("long", "半導體業")]["n"] == 3 and d[("long", "半導體業")]["wins"] == 2 and d[("long", "半導體業")]["win_pct"] == 66.7, "族群勝率：做多半導體 2/3")
check(d[("short", "金融保險")]["n"] == 2 and d[("short", "金融保險")]["win_pct"] == 0.0, "族群勝率：roi=None 視為 0（不算贏）")
check(("short", "未分類") in d and all(x["side"] in ("long", "short") for x in w), "查不到族群歸未分類、未知方向略過")
check(sm.winrate_by_sector([], mm) == [] and sm.winrate_by_sector(None, None) == [], "空輸入")
check(sm.winrate_by_sector(rows[:1], lambda s: "X")[0]["sector"] == "X", "sector_of 可為函式")
print(f"\n{ok} 項通過")

# ---- 參考表讀取端
W = lambda n, w, e: {"n": n, "win": w, "exp_pct": e}
ref = {"live_exit": "x", "long": {"sectors": {
    "金融保險": {"n_symbols": 26, "random": {"IS": W(286, .566, .37), "OOS": W(197, .65, 2.76)},
              "live_rules": {"pullback_burst": {"IS": W(40, .6, 1.0), "OOS": W(25, .64, 2.0), "gate_ok": True, "n_enough": True},
                             "chuan_e_ma60_40": {"IS": W(10, .5, 0), "OOS": W(5, .4, -1), "gate_ok": False, "n_enough": False}},
              "best_exits": [{"kind": "random_entry", "label": "停利8%/停損15%/20日", "IS": W(286, .6, .5), "OOS": W(197, .7, 3.0)},
                             {"kind": "live_rule", "rule": "pullback_burst", "label": "停利12%/停損15%/20日", "IS": W(40, .6, 1.0), "OOS": W(25, .64, 2.0)}]},
    "鋼鐵工業": {"n_symbols": 19, "random": {"IS": W(119, .36, -2.0), "OOS": W(81, .42, -1.0)},
              "live_rules": {"pullback_burst": {"IS": W(35, .4, -1.0), "OOS": W(22, .45, -0.5), "gate_ok": False, "n_enough": True}},
              "best_exits": []}}}}
check(sm.sector_gate_status(ref, "long", "金融保險", "pullback_burst")[0] == "pass", "閘門：達標 → pass")
check(sm.sector_gate_status(ref, "long", "鋼鐵工業", "pullback_burst")[0] == "fail", "閘門：樣本夠但不達標 → fail")
check(sm.sector_gate_status(ref, "long", "金融保險", "chuan_e_ma60_40")[0] == "nodata", "閘門：訊號太少 → nodata（不等於不好）")
check(sm.sector_gate_status(ref, "long", "不存在族群", "pullback_burst")[0] == "nodata", "閘門：族群不在參考表 → nodata")
check(sm.sector_gate_status({}, "long", "金融保險", "pullback_burst")[0] == "noref" and sm.sector_gate_status(None, "long", "x", "y")[0] == "noref", "閘門：沒有參考表 → noref")
check(sm.sector_gate_status(ref, "short", "金融保險", "pullback_burst")[0] == "nodata", "閘門：該方向無資料 → nodata")
t1, t2 = sm.ref_rows(ref, "long")
check(len(t1) == 2 and {r["族群"] for r in t1} == {"金融保險", "鋼鐵工業"}, "顯示表1：每族群一列")
fin = [r for r in t1 if r["族群"] == "金融保險"][0]
check(fin["內_勝率%"] == 56.6 and fin["外_勝率%"] == 65.0 and fin["爆量回檔_閘門"] == "✅" and fin["穿山惡龍_閘門"] == "—" and fin["爆量回檔_內/外勝率%"] == "60.0／64.0", "顯示表1：數字與閘門符號")
check(len(t2) == 2 and t2[0]["類型"].startswith("隨機進場") and t2[1]["類型"] == "實盤:爆量回檔", "顯示表2：可行出場")
check(sm.ref_rows({}, "long") == ([], []) and sm.ref_rows(None, "short") == ([], []), "顯示表：空參考表")
print(f"\n{ok} 項通過")
