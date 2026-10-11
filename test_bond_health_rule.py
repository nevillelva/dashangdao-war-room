"""【P2】債券ETF 健保口徑規則離線測試。python3 test_bond_health_rule.py"""
import etf_core as E

FAIL = []


def check(name, cond, extra=""):
    if cond:
        print("✅", name)
    else:
        FAIL.append(name)
        print("❌", name, extra)


# 1) 只對債券生效；股票類不受影響
check("債券＋offshore → 0", E.bond_ratio_override("bond", "offshore") == 0.0)
check("債券＋default → None（沿用預設）", E.bond_ratio_override("bond", "default") is None)
check("高股息＋offshore → None（不影響）", E.bond_ratio_override("dividend", "offshore") is None)
check("市值型＋offshore → None", E.bond_ratio_override("cap", "offshore") is None)

# 2) etf_kind 能正確辨識債券（代號字尾 B/D 或名稱含「債」）
check("00679B 辨識為債券", E.etf_kind("00679B", "元大美債20年") == "bond")
check("名稱含債 辨識為債券", E.etf_kind("00000", "某某公司債ETF") == "bond")
check("0056 不是債券", E.etf_kind("0056", "元大高股息") != "bond")

# 3) 台帳實際占比優先：nhi_base_ratio 有值時 override 不應該介入（呼叫端邏輯以台帳為先）
ledger = {"00679B": [{"ex_date": "2026-09-01", "ratio_54c": 0.0, "ratio_5a": 0.5}]}
check("台帳有值時 nhi_base_ratio 不為 None", E.nhi_base_ratio(ledger, "00679B", "combined") == 0.5)
check("台帳無值時 nhi_base_ratio 為 None", E.nhi_base_ratio({}, "00679B", "combined") is None)

# 4) 健保口徑對金額的影響（offshore＝ratio 0 → 不扣補充保費）
gross = 100_000
_, nhi_default, _, _ = E.net_payment(gross, True, True, 1.0)
_, nhi_offshore, _, _ = E.net_payment(gross, True, True, 0.0)
check("預設（100%）應扣 2.11%", nhi_default == round(gross * 0.0211), nhi_default)
check("offshore（0%）不扣補充保費", nhi_offshore == 0, nhi_offshore)

print("\n全部通過" if not FAIL else f"\n失敗 {FAIL}")
raise SystemExit(1 if FAIL else 0)
