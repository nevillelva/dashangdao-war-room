"""【2026-10-17】隔夜掃描改「做多主力＝查3∧查2」主清單，其他查X 改標註（取代 10/16 的逐條件展開排序測試）。"""
import sys
import warroom_core as W
ok = True


def chk(n, c):
    global ok
    print(("PASS: " if c else "FAIL: ") + n)
    ok = ok and c


rows = [{"symbol": "A", "score": 3, "matched_commands": ["查2.魚頭慢伏支撐", "查3.價值投資與循環", "查1.主升段突擊"]},
        {"symbol": "B", "score": 1, "matched_commands": ["查3.價值投資與循環", "查2.魚頭慢伏支撐", "查6.營收雙增"]},
        {"symbol": "C", "matched_commands": ["查3.價值投資與循環"]},
        {"symbol": "D", "matched_commands": ["查8.昨日強勢動能延續"]},
        {"symbol": "E", "matched_commands": ["查11.高殖利率(≥4.5%)", "查13.情報雷達：X"]},
        {"symbol": "F", "matched_commands": ["查12.K線型態尋寶型", "查10.籌碼沉澱量縮潛伏"]}]
g = W.split_long_main(rows)
chk("主清單＝同時中查3與查2", [r["symbol"] for r in g["main"]] == ["B", "A"])
chk("有⚠️追高警示的排後面", g["main"][-1]["symbol"] == "A" and "⚠️" in g["main"][-1]["tags"])
chk("標註不含查2/查3本身", all("查2" not in r["tags"] and "查3" not in r["tags"] for r in g["main"]))
chk("只中其一 → 次要", [r["symbol"] for r in g["single"]] == ["C"])
chk("查8 → 空方候選", [r["symbol"] for r in g["short"]] == ["D"])
chk("其他 → 參考", {r["symbol"] for r in g["other"]} == {"E", "F"})
chk("情報標成📡", "📡情報" in g["other"][0]["tags"] or "📡情報" in g["other"][1]["tags"])
chk("查10 不會被當成查1", "查1追高" not in [r for r in g["other"] if r["symbol"] == "F"][0]["tags"])
chk("顯示名稱：短名＋角色（查1／查9 警示）", W.cmd_display_name("查9.均線糾結爆量突破") == "查9 糾結爆量（警示）"
    and W.cmd_display_name("查10.籌碼沉澱量縮潛伏") == "查10 量縮籌碼穩"
    and W.cmd_display_name("查7.隔日沖漲停") == "查7 漲停隔日賣"
    and W.cmd_display_name("查13. 情報雷達：x") == "查13. 情報雷達：x")
src = open("dashangdao.py", encoding="utf-8").read()
chk("頁面改用 split_long_main，不再逐條件展開", "_wc.split_long_main(_os_visible)" in src and "_by_command" not in src)
chk("查5、查10 移出選單", "CMD_RETIRED" in src and "CMD_RETIRED" in open("system_scheduler.py", encoding="utf-8").read())
sys.exit(0 if ok else 1)
