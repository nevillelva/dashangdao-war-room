"""【P3】公告貼上解析＋事件表核對離線測試。python3 test_etf_paste.py（文字格式同 test_etf_twse_scan.py 的 00940 樣本）"""
import etf_core as E
import etf_paste as P

FAIL = []


def check(name, cond, extra=""):
    if cond:
        print("✅", name)
    else:
        FAIL.append(name)
        print("❌", name, extra)


TEXT_940 = """每受益權單位配發金額:新臺幣0.055元。 6.除息交易日:115/10/07
9.預估收益分配組成占比資訊: (1)股利所得占比20.00% (2)利息所得占比0.00% (3)收益平準金占比0.00% (4)已實現資本利得占比80.00% (5)其他所得占比0.00%"""
URL = "https://www.yuantafunds.com/example/00940-2026-10-07"
EVENTS = [
    {"symbol": "00940", "ex_date": "2026-10-07", "cash_per_unit": 0.055},
    {"symbol": "00940", "ex_date": "2026-07-07", "cash_per_unit": 0.05},
    {"symbol": "0056", "ex_date": "2026-10-22", "cash_per_unit": 1.72},
]

r = P.check_paste(TEXT_940, "00940", URL, EVENTS)
check("通過核對", r["ok"] is True, r.get("reason"))
check("寫入列：股利占比 20%", r.get("row", {}).get("sources", [{}])[0].get("ratio_54c") == 0.2, r.get("row"))
check("寫入列：利息占比 0%", r.get("row", {}).get("sources", [{}])[0].get("ratio_5a") == 0.0, r.get("row"))
check("寫入列：除息日與網址", r["row"]["ex_date"] == "2026-10-07" and r["row"]["sources"][0]["url"] == URL)

# 退回：除息日不在事件表
r = P.check_paste(TEXT_940.replace("115/10/07", "115/11/07"), "00940", URL, EVENTS)
check("除息日不在事件表 → 退回", r["ok"] is False and "事件表" in r["reason"], r.get("reason"))

# 退回：金額與事件表不符（0.06 vs 0.055，差 9%）
r = P.check_paste(TEXT_940.replace("0.055", "0.06"), "00940", URL, EVENTS)
check("金額差距超過 1% → 退回", r["ok"] is False and "差距" in r["reason"], r.get("reason"))

# 通過：金額差距在 1% 內（0.0553 vs 0.055，差 0.5%）
r = P.check_paste(TEXT_940.replace("0.055元", "0.0553元"), "00940", URL, EVENTS)
check("金額差距 0.5% → 通過", r["ok"] is True, r.get("reason"))

# 退回：網址不是 https
r = P.check_paste(TEXT_940, "00940", "http://example.com/a", EVENTS)
check("非 https 網址 → 退回", r["ok"] is False and "https" in r["reason"], r.get("reason"))
r = P.check_paste(TEXT_940, "00940", "", EVENTS)
check("未填網址 → 退回", r["ok"] is False, r.get("reason"))

# 退回：沒有組成占比 / 沒有金額 / 空白 / 沒有代號
r = P.check_paste("除息交易日:115/10/07 每受益權單位配發金額:新臺幣0.055元", "00940", URL, EVENTS)
check("無組成占比 → 退回", r["ok"] is False and "解析失敗" in r["reason"], r.get("reason"))
r = P.check_paste(TEXT_940.replace("每受益權單位配發金額:新臺幣0.055元。", ""), "00940", URL, EVENTS)
check("無配發金額 → 退回", r["ok"] is False and "配發金額" in r["reason"], r.get("reason"))
r = P.check_paste("   ", "00940", URL, EVENTS)
check("空白文字 → 退回", r["ok"] is False, r.get("reason"))
r = P.check_paste(TEXT_940, "", URL, EVENTS)
check("未填代號 → 退回", r["ok"] is False, r.get("reason"))

# 整合：通過的 row 經 apply_sourced 寫入台帳，狀態為 confirmed
ok = P.check_paste(TEXT_940, "00940", URL, EVENTS)
latest = E.latest_ex_by_symbol(EVENTS)
led, applied, rej = E.apply_sourced({}, [ok["row"]], latest, min_sources=1)
rec = (led.get("00940") or [{}])[-1]
check("apply_sourced 寫入 1 筆", applied == 1 and not rej, rej)
check("台帳狀態 confirmed 且占比正確", rec.get("status") == "confirmed" and rec.get("ratio_54c") == 0.2, rec)
check("ratio_provenance 標示為確認（多來源）", E.ratio_provenance(led, "00940")["label"] == "確認（多來源）")

print("\n全部通過" if not FAIL else f"\n失敗 {FAIL}")
raise SystemExit(1 if FAIL else 0)
