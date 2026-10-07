"""strategy_monitor 單元測試。python3 test_strategy_monitor.py"""
import strategy_monitor as sm

FAIL = []


def check(name, cond, extra=""):
    if not cond:
        FAIL.append(name)
        print("❌", name, extra)
    else:
        print("✅", name)


lo, hi = sm.wilson(50, 100)
check("Wilson 50/100 ≈ 0.40~0.60", 0.39 < lo < 0.41 and 0.59 < hi < 0.61, (lo, hi))
check("Wilson n=0", sm.wilson(0, 0) == (0.0, 1.0))
lo, hi = sm.wilson(0, 20)
check("Wilson 0/20 下限=0 上限<0.2", lo == 0.0 and hi < 0.2, (lo, hi))

C = lambda isn, isw, oosn, ooswin, e=1.0: {"all": {"IS": {"n": isn, "win": isw, "exp_pct": e}, "OOS": {"n": oosn, "win": ooswin, "exp_pct": e}}}
ref = {"long": {"sectors": {
    "全體市場(對照)": {"rules": {"pullback_burst": C(200, .58, 150, .60), "chuan_e_ma60_40": C(100, .5, 80, .55)}},
    "半導體業": {"rules": {"pullback_burst": C(30, .6, 20, .7)}},
    "航運業": {"rules": {"pullback_burst": C(5, .6, 4, .5)}},       # 樣本不足 → 退回全體
}}}
b = sm.baseline(ref, "半導體業", "pullback_burst")
check("族群樣本足夠用族群基準（合併勝率 0.64）", b["src"] == "半導體業" and abs(b["p0"] - (30 * .6 + 20 * .7) / 50) < 1e-9, b)
b = sm.baseline(ref, "航運業", "pullback_burst")
check("族群樣本不足退回全體", b["src"] == sm.REF_ALL_SECTOR)
b = sm.baseline(ref, None, "chuan_e_ma60_40")
check("無族群用全體", b["src"] == sm.REF_ALL_SECTOR and abs(b["p0"] - (100 * .5 + 80 * .55) / 180) < 1e-9)
check("沒有參考表 → None", sm.baseline({}, "x", "pullback_burst") is None and sm.baseline(None, None, "r") is None)

mk = lambda rule, sym, roi: {"strategy_tag": rule, "symbol": sym, "realized_roi": roi}
sec = {"1": "半導體業", "2": "半導體業"}
# 爆量回檔整體：30 筆 6 勝（20%）→ 遠低於回測 59% → 偏離
bad = [mk("pullback_burst", "1", 5.0)] * 6 + [mk("pullback_burst", "2", -6.0)] * 24
res = sm.evaluate(bad, ref, sec)
top = res[0]
check("勝率遠低於回測 → 偏離且排第一", top["status"] == "偏離" and top["rule"] == "pullback_burst", top)
check("族群細項樣本≥20 也列出", any(r["sector"] == "半導體業" for r in res), [r["sector"] for r in res])
# 正常
ok = [mk("chuan_e_ma60_40", "1", 4.0)] * 15 + [mk("chuan_e_ma60_40", "1", -4.0)] * 10
r = sm.evaluate(ok, ref, sec)[0]
check("勝率 60% 高於回測 → 正常", r["status"] == "正常", r)
# 偏低：區間涵蓋回測值
low = [mk("chuan_e_ma60_40", "1", 4.0)] * 9 + [mk("chuan_e_ma60_40", "1", -4.0)] * 11     # 45% vs 52%
r = sm.evaluate(low, ref, sec)[0]
check("略低於回測但區間涵蓋 → 偏低(觀察)", r["status"] == "偏低", r)
# 樣本不足
few = [mk("pullback_burst", "1", -5.0)] * 5
r = sm.evaluate(few, ref, sec)[0]
check("<20 筆不下結論", r["status"] == "樣本不足", r)
check("缺值列忽略", sm.evaluate([{"strategy_tag": "x", "symbol": "1", "realized_roi": None}], ref, sec) == [])
nob = sm.evaluate([mk("unknown_rule", "1", 1.0)] * 25, ref, sec)[0]
check("沒有回測基準 → 無基準", nob["status"] == "無基準", nob)
txt = sm.build_text(res, "2026-10-07")
check("報告文字含偏離與建議", "偏離" in txt and "暫停" in txt and "爆量回檔" in txt, txt)
check("無資料 → 空字串", sm.build_text([], "x") == "")
new = sm.status_changes(None, res)
check("首次偏離視為新出現", len(new) >= 1)
check("已知偏離不重複", sm.status_changes(res, res) == [])
# F9 精簡版：參考表過期提醒
check("參考表年齡", sm.ref_age_days({"asof": "2026-10-06"}, "2026-10-16") == 10 and sm.ref_age_days({}, "2026-10-16") is None and sm.ref_age_days({"asof": "x"}, "2026-10-16") is None)
check("未滿 90 天不提醒", sm.ref_age_reminder({"asof": "2026-10-06"}, "2027-01-03") is None)
msg = sm.ref_age_reminder({"asof": "2026-10-06"}, "2027-01-05")
check("滿 90 天提醒", msg is not None and "91" in msg and "persist_ref" in msg, msg)
check("14 天內不重複提醒", sm.ref_age_reminder({"asof": "2026-10-06"}, "2027-01-10", last_reminded="2027-01-05") is None)
check("超過 14 天再提醒", sm.ref_age_reminder({"asof": "2026-10-06"}, "2027-01-25", last_reminded="2027-01-05") is not None)
check("沒有 asof（缺參考表）不提醒", sm.ref_age_reminder({}, "2027-01-25") is None)
print("\n結果：", "全部通過" if not FAIL else f"失敗 {len(FAIL)} 項：{FAIL}")
raise SystemExit(1 if FAIL else 0)
