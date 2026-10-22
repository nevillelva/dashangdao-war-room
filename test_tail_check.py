"""【2026-10-22】13:20 尾盤進場確認（tail_check.py）。"""
import sys
import pandas as pd
import tail_check as TC

ok = True


def chk(n, c, extra=None):
    global ok
    print(("✅ " if c else "❌ ") + n + ("" if c else f"  → {extra}"))
    ok = ok and c


def rows(path):
    """path：函式 f(分鐘數 0..259) → 價；回傳永豐金 1 分K（ts＝該分鐘結束）。09:00～13:20。"""
    out, prev = [], path(0)
    for k in range(260):
        ts = pd.Timestamp("2026-10-23 09:01") + pd.Timedelta(minutes=k)
        px = path(k)
        out.append({"ts": ts, "Open": prev, "High": max(prev, px), "Low": min(prev, px), "Close": px, "Volume": 10})
        prev = px
    return out


strong = rows(lambda k: 100.0 + min(k, 30) * 0.05 + max(0, k - 30) * 0.004)     # 09:30 約 +1.5%、之後緩漲
weak = rows(lambda k: 100.0 + min(k, 30) * 0.05 - max(0, k - 30) * 0.01)        # 09:30 +1.5%、之後一路跌到均價下
flat = rows(lambda k: 100.0 + 0.001 * k)                                         # 09:30 只有 +0.03%

chk("波段主力 → 查3 規則", TC.rule_for({"direction": "long", "track": "波段主力", "cmds": ["查3.x", "查2.y"]})[0] == "查3")
chk("短線動能 → 動能規則（附借用說明）", TC.rule_for({"direction": "long", "track": "短線動能"})[0] == "動能")
chk("做空不適用", TC.rule_for({"direction": "short"})[0] is None)
r1 = TC.evaluate({"symbol": "A", "direction": "long", "track": "波段主力", "cmds": ["查3.x"]}, strong)
chk("強勢日：查3 規則通過", r1["pass"] is True, r1)
chk("說明有 09:30 漲幅與均價", "09:30 +" in r1["detail"] and "均價" in r1["detail"], r1["detail"])
r2 = TC.evaluate({"symbol": "B", "direction": "long", "track": "波段主力", "cmds": ["查3.x"]}, weak)
chk("早盤強、午盤跌破均價：不通過", r2["pass"] is False and "❌" in r2["detail"], r2)
r3 = TC.evaluate({"symbol": "C", "direction": "long", "track": "波段主力", "cmds": ["查3.x"]}, flat)
chk("09:30 沒有 +0.5%：不通過", r3["pass"] is False, r3)
r4 = TC.evaluate({"symbol": "D", "direction": "long", "track": "波段主力"}, None)
chk("沒有 1 分K：無法判斷", r4["pass"] is None, r4)
msg = TC.message("2026-10-23", [r1, r2, r3, r4], {"A": "甲"})
chk("通知含進場／不進／無法判斷", "✅ 進場 甲(A)" in msg and "❌ 不進" in msg and "⚪ 無法判斷" in msg, msg)
sys.exit(0 if ok else 1)
