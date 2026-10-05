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
