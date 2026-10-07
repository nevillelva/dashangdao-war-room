"""risk_budget 單元測試（純函式）。python3 test_risk_budget.py"""
import risk_budget as rb

FAIL = []


def check(name, cond, extra=""):
    if not cond:
        FAIL.append(name)
        print("❌", name, extra)
    else:
        print("✅", name)


S = lambda sym: {"symbol": sym, "rule": "x"}
sec = {"1": "A", "2": "A", "3": "A", "4": "A", "5": "B"}
# 已持有 A 兩檔、cap=3：訊號 3(A)可收、4(A)擋、5(B)收、9(未知族群)收
kept, dropped = rb.sector_cap_filter([S("3"), S("4"), S("5"), S("9")], sec, ["1", "2"], cap=3)
check("同族群達上限被擋", [k["symbol"] for k in kept] == ["3", "5", "9"] and [d[0]["symbol"] for d in dropped] == ["4"], (kept, dropped))
check("擋掉原因含族群與上限", "A" in dropped[0][1] and "3" in dropped[0][1])
kept, _ = rb.sector_cap_filter([S("3"), S("4")], sec, [], cap=1)
check("同批訊號也受限（cap=1 只收一檔）", [k["symbol"] for k in kept] == ["3"])
kept, dropped = rb.sector_cap_filter([S("3"), S("4")], sec, ["1", "2", "3"], cap=0)
check("cap=0 不限制", len(kept) == 2 and not dropped)
kept, _ = rb.sector_cap_filter([S("8"), S("9")], {}, ["1"] * 9, cap=1)
check("族群不明 fail-open", len(kept) == 2)
check("不改動訊號內容", rb.sector_cap_filter([S("5")], sec, [], cap=3)[0][0] == S("5"))

# 熔斷
def trades(rois, start="2026-09-01"):
    out = []
    for i, r in enumerate(rois):
        d = f"2026-09-{(i % 28) + 1:02d}" if i < 28 else f"2026-10-{(i % 28) + 1:02d}"
        out.append({"exit_date": d, "realized_roi": r})
    return out

good = trades([3, -2, 4, 5, -1, 2, 3, 6, -2, 4, 1, 3])
p, why, st = rb.evaluate_breaker(good, None, "2026-10-07")
check("績效正常不熔斷", not p and st is None, (p, why))
bad = trades([-3, -2, -4, 5, -1, -2, -3, -6, 2, -4, -1, -3])      # 勝率 2/12=17%
p, why, st = rb.evaluate_breaker(bad, None, "2026-10-07")
check("勝率過低觸發熔斷", p and st and st["until"] == "2026-10-12" and "勝率" in why, (p, why, st))
p2, why2, st2 = rb.evaluate_breaker(bad, st, "2026-10-09")
check("暫停期間持續暫停且狀態不變", p2 and st2 == st and "熔斷中" in why2)
p3, _, st3 = rb.evaluate_breaker(bad, st, "2026-10-13")
check("到期後舊虧損不會反覆觸發（只看觸發日之後新平倉）", (not p3) and st3 == st, (p3, st3))
more = bad + [{"exit_date": f"2026-10-{14 + i}", "realized_roi": -5} for i in range(12)]
p4, why4, st4 = rb.evaluate_breaker(more, st, "2026-10-27")
check("到期後新虧損再累積到門檻會再觸發", p4 and st4["triggered_on"] == "2026-10-27", (p4, why4))
few = trades([-9, -9, -9])
check("樣本不足(<12 筆)不觸發", not rb.evaluate_breaker(few, None, "2026-10-07")[0])
big_loss = trades([2, 3, 1, 2, 3, 1, 2, 3, 1, 2, 3, -40])      # 勝率高但一筆大虧 → 合計 −40+23<0? 23-40=-17 >-25
check("勝率高且合計未達門檻不觸發", not rb.evaluate_breaker(big_loss, None, "2026-10-07")[0])
big_loss2 = trades([1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, -45])
p, why, _ = rb.evaluate_breaker(big_loss2, None, "2026-10-07")
check("勝率高但累計虧損過大也熔斷", p and "淨報酬合計" in why, (p, why))
check("熔斷可關閉", not rb.evaluate_breaker(bad, None, "2026-10-07", {"breaker_enabled": False})[0])
check("缺值列被忽略", not rb.evaluate_breaker([{"exit_date": None, "realized_roi": -5}] * 20, None, "2026-10-07")[0])

# 波動
import math
flat = [100 + 0.1 * i for i in range(30)]
check("低波動 vol 小", rb.annualized_vol(flat) < 0.1)
wild = [100 * (1.05 if i % 2 else 0.95) ** (1 if i % 2 else 1) for i in range(30)]
check("高波動 vol 大", rb.annualized_vol([100, 105, 99, 106, 98, 107, 97, 108, 96, 109, 95, 110, 94, 111, 93, 112, 92, 113, 91, 114, 90]) > 0.8)
check("資料不足回 None", rb.annualized_vol([100, 101]) is None)
check("預設關閉＝1.0", rb.vol_scale(1.2) == 1.0)
check("開啟後高波動縮小且不低於下限", rb.vol_scale(0.7, {"vol_sizing": True}) == 0.5 and abs(rb.vol_scale(0.5, {"vol_sizing": True}) - 0.7) < 1e-9)
check("開啟後低波動不放大", rb.vol_scale(0.2, {"vol_sizing": True}) == 1.0)
check("未知波動 = 1.0", rb.vol_scale(None, {"vol_sizing": True}) == 1.0)

print("\n結果：", "全部通過" if not FAIL else f"失敗 {len(FAIL)} 項：{FAIL}")
raise SystemExit(1 if FAIL else 0)
