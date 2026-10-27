"""【2026-10-23】尾盤快速戰卡（tail_card.py）離線測試。"""
import sys

import numpy as np

import tail_card as T

ok = True


def chk(n, c, extra=None):
    global ok
    print(("✅ " if c else "❌ ") + n + ("" if c else f"  → {extra}"))
    ok = ok and c


# 1. 區域比對
chk("Z1 需要投信大買＋季線上＋開低", [z["key"] for z in T.zone_match({"tbig", "tw60", "gap_dn", "t1"})] == ["Z1"])
chk("少一個條件就不算", T.zone_match({"tbig", "gap_dn"}) == [])

# 2. 主戰清單：規則過 → 買；停損依軌道
rr = {"pass": True, "rule": "查3", "hold": 60, "tp": None, "detail": "09:30 +1.0% ✅；12:30≥均價 ✅"}
c = T.decide({"symbol": "A", "source": "主戰清單", "track": "波段主力", "direction": "long"}, rr, px=100, ref=99, lv={"def": 95.5}, bull60=True)
chk("規則過：買", c["decision"] == "buy", c)
chk("波段主力停損 −10%＝90", c["stop_px"] == 90.0 and c["stop_pct"] == 10.0, c)
chk("隔天：不用賣、抱 60 日、跌破 90 才賣", "60" in c["next_day"] and "90" in c["next_day"], c["next_day"])
chk("進場說明含戰卡防守線（短線參考）", "95.5" in c["entry"], c["entry"])
c2 = T.decide({"symbol": "A", "source": "主戰清單", "track": "波段主力"}, rr, px=100, ref=99, bull60=False)
chk("大盤季線下：波段主力改小量", c2["decision"] == "small" and any("季線下" in w for w in c2["why"]), c2)
c3 = T.decide({"symbol": "A", "source": "主戰清單", "track": "波段主力"}, dict(rr, **{"pass": False, "detail": "09:30 +0.1% ❌"}), px=100, ref=99)
chk("規則沒過：不買", c3["decision"] == "skip", c3)
c4 = T.decide({"symbol": "A", "source": "主戰清單", "track": "波段主力"}, rr, px=108.5, ref=99)
chk("離漲停 ≤1%：不買", c4["decision"] == "skip" and any("漲停" in w for w in c4["why"]), c4)
c5 = T.decide({"symbol": "A", "source": "主戰清單", "track": "短線動能"}, dict(rr, rule="動能", hold=10, tp=5), px=101.5, ref=99)
chk("短線動能高於昨收 2% 不追", c5["decision"] == "skip" and any("不追" in w for w in c5["why"]), c5)
c6 = T.decide({"symbol": "A", "source": "主戰清單", "track": "短線動能"}, dict(rr, rule="動能", hold=10, tp=5), px=100, ref=99)
chk("短線動能：停損 −5%、停利 +5%", c6["stop_px"] == 95.0 and c6["tp_px"] == 105.0, c6)
c7 = T.decide({"symbol": "A", "source": "主戰清單", "track": "波段主力"}, rr, px=100, ref=99, vetoes=["處置股：不做"])
chk("處置股一票否決", c7["decision"] == "skip" and "處置股：不做" in c7["why"], c7)
c8 = T.decide({"symbol": "A", "source": "主戰清單", "track": "波段主力"}, dict(rr, **{"pass": None, "detail": "沒有 1 分K"}), px=100, ref=99)
chk("資料不足：不買並說明", c8["decision"] == "skip" and "資料不足" in c8["why"][0], c8)

# 3. 熱門區域
z = T.zone_match({"tbig", "tw60", "gap_dn"})
c9 = T.decide({"symbol": "B", "source": "熱門區域"}, None, zones=z, px=50, ref=51)
chk("熱門區域符合：買、抱 20 日、不設停損（回測停損變差）", c9["decision"] == "buy" and c9["hold"] == 20 and c9["stop_px"] is None and "第 20 個交易日" in c9["next_day"], c9)
c9b = T.decide({"symbol": "B", "source": "熱門區域"}, None, zones=z, px=50, ref=51, sox_crash=True)
chk("費半前一晚重挫：區域單降為小量", c9b["decision"] == "small" and any("重挫" in w for w in c9b["why"]), c9b)
c10 = T.decide({"symbol": "B", "source": "熱門區域"}, None, zones=[], px=50, ref=51)
chk("熱門區域不符合：不買", c10["decision"] == "skip", c10)
c11 = T.decide({"symbol": "A", "source": "主戰清單", "track": "波段主力"}, rr, zones=z, px=100, ref=99)
chk("規則＋區域同時成立：加星號", any("⭐" in w for w in c11["why"]), c11)

# 4. 持有中
h = {"symbol": "C", "entry_px": 100, "hold": 20, "stop_pct": 10, "tp": None, "source": "主戰清單"}
chk("持有中正常：續抱", T.holding_action(h, 103, hi_since=105, lo_since=97, days_held=3)["action"] == "續抱")
chk("跌破停損：賣", T.holding_action(h, 92, lo_since=89.5, days_held=4)["action"].startswith("賣出：已跌破停損"))
chk("滿 20 日：賣", T.holding_action(h, 104, lo_since=98, days_held=20)["action"].startswith("賣出：持有已滿"))
h2 = dict(h, tp=5, hold=10)
chk("碰到停利：賣", T.holding_action(h2, 103, hi_since=105.2, lo_since=99, days_held=2)["action"].startswith("賣出：已達停利"))
hp = {"symbol": "D", "entry_px": 50, "source": "持倉"}
chk("老闆持倉跌破防守線且在均價下：留意", T.holding_action(hp, 48, vwap1230=48.5, def_line=49)["action"].startswith("留意"))

# 5. 旗標：用 hot_zone_bt 同一套定義，今天開低 −2%
n = 80
C = np.linspace(100, 110, n)
O, H, L, V = C.copy(), C * 1.01, C * 0.99, np.full(n, 1_000_000.0)
O[-1] = C[-2] * 0.98
dates = [f"2026-{1 + k // 28:02d}-{1 + k % 28:02d}" for k in range(n)]
t = np.zeros(n); t[-2] = 60.0
fl = T.flags_from_arrays(dates, O, H, L, C, V, inst={"t": t, "f": np.zeros(n), "d": np.zeros(n), "m": np.zeros(n)},
                         tw={dates[-2]: {"bull60": True, "bull20": True}}, us={dates[-1]: {"sox": 0.2, "sox_up20": True, "tsm": 1.5, "ndx": 0.3}})
chk("昨天投信大買、今天開低、季線上 → 有 tbig、gap_dn、tw60", fl is not None and {"tbig", "gap_dn", "tw60"} <= fl, fl)
chk("→ 符合 Z1、Z2", {z["key"] for z in T.zone_match(fl)} >= {"Z1", "Z2"}, T.zone_match(fl))

# 6. 訊息
msg = T.message("2026-10-23", "prep", [c, c3, c9], [T.holding_action(h, 103, days_held=3) | {"symbol": "C"}], {"A": "甲", "B": "乙"})
chk("訊息含買／不買／持有中／隔天說明", "✅ 買 甲(A)" in msg and "❌ 不買" in msg and "📦 持有中" in msg and "隔天" in msg, msg)

# 7. 【2026-10-27】雷達股沒有指令規則：只列觀察，不是「資料不足」
c12 = T.decide({"symbol": "R", "source": "雷達"}, None, px=50, ref=49)
chk("雷達無規則：僅觀察、不買", c12["decision"] == "skip" and "僅觀察" in c12["why"][0] and "資料不足" not in c12["why"][0], c12)
c13 = T.decide({"symbol": "R", "source": "雷達"}, {"pass": None, "rule": None, "detail": T.NO_RULE_TEXT}, px=50, ref=49)
chk("雷達 tail_check 回「沒有規則」：也是僅觀察", c13["decision"] == "skip" and "僅觀察" in c13["why"][0], c13)
c14 = T.decide({"symbol": "R", "source": "雷達"}, None, zones=z, px=50, ref=51)
chk("雷達無規則但符合區域：不買（區域規則只在熱門前100回測過）", c14["decision"] == "skip", c14)
sys.exit(0 if ok else 1)
