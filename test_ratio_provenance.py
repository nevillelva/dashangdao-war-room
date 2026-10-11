"""【P1】占比來源標示離線測試。python3 test_ratio_provenance.py"""
import etf_core as E

FAIL = []


def check(name, cond, extra=""):
    if cond:
        print("✅", name)
    else:
        FAIL.append(name)
        print("❌", name, extra)


L = {
    "00919": [{"ex_date": "2026-09-01", "ratio_54c": 0.0, "status": "confirmed",
               "source": "https://www.twse.com.tw/announcement/x"}],
    "0056": [{"ex_date": "2026-08-01", "ratio_54c": 0.3496, "status": "confirmed",
              "source": "多來源互證：https://api.yuantafunds.com/a、https://danny50610.github.io/b"}],
    "00878": [{"ex_date": "2026-08-01", "ratio_54c": 0.099, "status": "confirmed", "source": "人工確認(ETF分頁)"}],
    "00940": [{"ex_date": "2026-09-01", "ratio_54c": 0.2, "status": "carried", "source": "沿用 2026-06-01 的占比"}],
    "00679B": [{"ex_date": "2026-09-01", "ratio_54c": None, "status": "confirmed", "source": "清除(留空)"},
               {"ex_date": "2026-06-01", "ratio_54c": 0.1, "status": "confirmed", "source": "人工確認(ETF分頁)"}],
}

check("證交所公告 → 預估（投信公告）", E.ratio_provenance(L, "00919")["label"] == "預估（投信公告）")
check("多來源互證 → 確認（多來源）", E.ratio_provenance(L, "0056")["label"] == "確認（多來源）")
check("人工確認 → 確認（人工）", E.ratio_provenance(L, "00878")["label"] == "確認（人工）")
check("carried → 沿用（待確認）", E.ratio_provenance(L, "00940")["label"] == "沿用（待確認）")
check("清除留空的記錄略過，取上一筆有數字者", E.ratio_provenance(L, "00679B")["ex_date"] == "2026-06-01")
check("台帳優先於人工覆寫", E.ratio_provenance(L, "00919", master_val=0.5)["label"] == "預估（投信公告）")
check("無台帳＋人工覆寫 → 人工覆寫", E.ratio_provenance({}, "X", master_val=0.3)["label"] == "人工覆寫")
check("無台帳＋債券規則 → 推定", E.ratio_provenance({}, "X", bond_rule=True)["label"] == "推定（債券口徑）")
check("都沒有 → 預設占比", E.ratio_provenance({}, "X")["label"] == "預設占比")
check("空白覆寫視為沒有", E.ratio_provenance({}, "X", master_val="")["label"] == "預設占比")
check("說明文字涵蓋所有標示", all(k in E.RATIO_SRC_HINT for k in ("確認", "預估（投信公告）", "沿用", "人工覆寫", "推定", "預設占比")))

print("\n全部通過" if not FAIL else f"\n失敗 {FAIL}")
raise SystemExit(1 if FAIL else 0)
