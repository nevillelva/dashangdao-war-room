"""【2026-10-22】當沖自動進退場規則（intraday_rules.py）。以 10/22 實際案例重現。"""
import sys
import intraday_rules as IR

ok = True


def chk(n, c, extra=None):
    global ok
    print(("✅ " if c else "❌ ") + n + ("" if c else f"  → {extra}"))
    ok = ok and c


# 10/22：三關 pass 的做多 8 檔（5 檔是手動雷達清單、3 檔是主戰清單短線動能）＋做空 1 檔；另加 1 檔查7 exit 方向
gate = [("1605", "long"), ("2303", "long"), ("3231", "long"), ("3706", "long"), ("8182", "long"),
        ("4566", "long"), ("6756", "long"), ("8021", "long"), ("3661", "short"), ("4526", "exit")]
pool = [{"symbol": "4566", "direction": "long", "note": "🎯主戰清單｜短線動能｜…"},
        {"symbol": "6756", "direction": "long", "note": "🎯主戰清單｜短線動能｜…"},
        {"symbol": "8021", "direction": "long", "note": "🎯主戰清單｜短線動能｜…"},
        {"symbol": "3661", "direction": "short", "note": "系統A=-7", "score": "-7"},
        {"symbol": "4526", "direction": "exit", "note": "查7"}]
p = IR.plan_entries(gate, pool)
ent = {(s, d): t for s, d, t, _ in p["enter"]}
sk = {s: why for s, _d, why in p["skipped"]}
chk("手動雷達清單 5 檔不自動進場", all(s in sk and "候選池" in sk[s] for s in ("1605", "2303", "3231", "3706", "8182")), sk)
chk("短線動能 3 檔改影子單", all(ent.get((s, "long")) == IR.SHADOW_TYPE for s in ("4566", "6756", "8021")), ent)
chk("空方照常進場", ent.get(("3661", "short")) == "intraday", ent)
chk("查7 exit 方向不會被當成做空", "4526" in sk and ("4526", "short") not in ent, sk)

# 做多上限與同產業
pool2 = [{"symbol": f"L{k}", "direction": "long", "note": ("🎯主戰清單｜波段主力" if k == 4 else "系統A"), "score": str(k)} for k in range(5)]
ind = {"L0": "半導體", "L1": "半導體", "L2": "電子零組件", "L3": "航運", "L4": "半導體"}
p2 = IR.plan_entries([(f"L{k}", "long") for k in range(5)], pool2, industry_of=ind)
got = [s for s, d, t, _ in p2["enter"]]
chk("做多最多 3 檔、波段主力優先、同產業 1 檔", got == ["L4", "L3", "L2"], got)
chk("被擋的有原因", {s for s, _d, _w in p2["skipped"]} == {"L0", "L1"}, p2["skipped"])

# 等資金零股
chk("世芯-KY 3695 元：10 萬元＝0.027 張（27 股）", IR.size_shares(3695) == 0.027, IR.size_shares(3695))
chk("華新 37.75 元：10 萬元＝2.649 張", IR.size_shares(37.75) == 2.649, IR.size_shares(37.75))
chk("價格無效回 0", IR.size_shares(0) == 0.0)

# 停損回看
bars = IR.minute_bars([{"ts": "2026-10-22 10:06:00", "Open": 511, "High": 512, "Low": 510, "Close": 511},
                       {"ts": "2026-10-22 10:07:00", "Open": 511, "High": 511, "Low": 500, "Close": 501},
                       {"ts": "2026-10-22 11:30:00", "Open": 498, "High": 499, "Low": 494, "Close": 495}])
chk("1 分K：ts 是結束時間 → 起始 10:05", bars[0][0] == "10:05", bars)
st = IR.stop_check(bars, "10:06", 511, "long", 3.0)
chk("做多停損 3%：11:29 那根最低 494 ≤ 495.67 → 以停損價出", st and abs(st["price"] - 495.67) < 0.01 and st["time"] == "11:29" and not st["gap"], st)
chk("進場那一分鐘以前的 K 棒不算", IR.stop_check(bars, "11:29", 511, "long", 3.0) is None)
chk("停損未啟用回 None", IR.stop_check(bars, "10:06", 511, "long", None) is None)
bs = IR.minute_bars([{"ts": "2026-10-22 10:10:00", "Open": 3820, "High": 3830, "Low": 3800, "Close": 3810}])
st2 = IR.stop_check(bs, "10:06", 3695, "short", 3.0)
chk("做空停損：開盤 3820 已超過 3805.85 → 以開盤價出（跳空）", st2 and st2["price"] == 3820 and st2["gap"], st2)

# 損益與通知
pnl, roi, net, eq = IR.trade_result("short", 3695, 3485, 0.027)
chk("做空 3695→3485：毛 +5.68%、扣成本 +5.25%、等資金 +5,248 元", abs(roi - 5.683) < 0.01 and abs(net - 5.248) < 0.01 and abs(eq - 5248) < 2, (roi, net, eq))
chk("帳面損益按實際張數（0.027 張）", abs(pnl - 5670) < 1, pnl)
msg = IR.exit_report("2026-10-22", [{"label": "世芯-KY(3661)", "side": "short", "net": 5.25, "eq_pnl": 5248, "stopped": False},
                                    {"label": "某股(1111)", "side": "long", "net": -3.44, "eq_pnl": -3435, "stopped": True}],
                     [{"label": "尖點(8021)", "side": "long", "net": -6.6, "eq_pnl": -6600, "stopped": False}])
chk("通知有等資金合計、等權平均、停損標記、影子單分開", "等資金 +1,813 元" in msg and "等權平均 +0.91%" in msg and "⛔停損" in msg and "👻 影子單" in msg, msg)
chk("影子單不算進合計", "-6,600" not in msg.split("👻")[0], msg)
sys.exit(0 if ok else 1)
