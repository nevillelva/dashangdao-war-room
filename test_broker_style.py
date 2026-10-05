#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_broker_style.py —— 分點型態分類（broker_style.py）單元測試：合成資料，檢驗各規則與邊界。"""
import sys

import broker_style as bs

ok = True


def check(cond, msg):
    global ok
    if not cond:
        ok = False
        print("❌", msg)


DATES = [f"2026-09-{d:02d}" for d in (3, 4, 7, 8, 9, 10, 11, 14)]   # 8 個交易日
D = DATES[-1]


def row(sym, date, name, net, sell=None, code=None, buy=None):
    """net>0：買超；sell 預設 0（買超）／-net（賣超）。"""
    if net >= 0:
        b, s = (buy if buy is not None else net), (sell if sell is not None else 0)
    else:
        b, s = (buy if buy is not None else 0), (sell if sell is not None else -net)
    return {"symbol": sym, "log_date": date, "broker_name": name, "broker_code": code or f"C-{name}",
            "buy_shares": b, "sell_shares": s, "net_shares": net}


rows = []
# ── 標的 AAA：各型態各一家 ──
# 1) 凱基-台北：d3 買 300→d4 賣 250(倒貨)；d7 買 200→d8 賣 180(倒貨)；今天 d14 再買 400 → 隔日沖(實測)
for d, n in ((DATES[0], 300), (DATES[1], -250), (DATES[2], 200), (DATES[3], -180), (DATES[7], 400)):
    rows.append(row("AAA", d, "凱基-台北", n))
# 2) 元富-建倉：d9~d14 連買（今天 +100）→ 建倉(實測)
for d in DATES[4:]:
    rows.append(row("AAA", d, "元富-建倉", 100))
# 3) 美林(外資)：今天 +200 且無歷史 → 外資
rows.append(row("AAA", D, "美林", 200, code="1440"))
# 4) 新進券商：今天 +100 → 未判
rows.append(row("AAA", D, "新券商", 100))
# 5) 國泰-敦南：名單、今天 +50、無歷史 → 隔日沖(名單)
rows.append(row("AAA", D, "國泰-敦南", 50))
# 6) 賣超側：建倉者今天小幅出貨 -30（<前一日淨買 80 的一半 → 仍算續抱 → 建倉型）
for d in DATES[3:7]:
    rows.append(row("AAA", d, "遠智-建倉B", 80))
rows.append(row("AAA", D, "遠智-建倉B", -30))
# 其他標的讓交易日曆成立（每個日期都有資料）
for sym in ("BBB", "CCC"):
    for d in DATES:
        rows.append(row(sym, d, "某券商", 10))
# 舊列（無 broker_code）不應被納入
rows.append({"symbol": "AAA", "log_date": D, "broker_name": "舊列券商", "broker_code": None,
             "buy_shares": 9999, "sell_shares": 0, "net_shares": 9999})

tdates = bs.trading_dates_from_rows([r for r in rows if r["broker_code"]])
check(tdates == DATES, f"交易日曆應為 8 日：{tdates}")

by = bs.group_by_symbol(rows)
check(all(r["broker_code"] for r in by["AAA"]), "group_by_symbol 應排除無 broker_code 的舊列")

rec = bs.compute_symbol_style(by["AAA"], D, tdates, listed_names=("國泰-敦南", "凱基-台北"))
check(rec is not None, "AAA 應有結果")
m = {c["name"]: c for c in rec["brokers"]}
check(m["凱基-台北"]["type"] == "flip" and m["凱基-台北"]["basis"] == "實測" and m["凱基-台北"]["flips"] == 2, f"凱基-台北應為隔日沖(實測)：{m['凱基-台北']}")
check(m["元富-建倉"]["type"] == "build" and m["元富-建倉"]["streak"] == 4 and m["元富-建倉"]["holds"] == 3, f"元富-建倉應為建倉、連買4日：{m['元富-建倉']}")
check(m["美林"]["type"] == "foreign", f"美林應為外資：{m['美林']}")
check(m["新券商"]["type"] == "unknown", f"新券商應為未判：{m['新券商']}")
check(m["國泰-敦南"]["type"] == "flip" and m["國泰-敦南"]["basis"] == "名單", f"國泰-敦南應為隔日沖(名單)：{m['國泰-敦南']}")
check("舊列券商" not in m, "舊列券商不應出現")
check(rec["flip_buy"] == 450 and rec["build_buy"] == 100 and rec["foreign_buy"] == 200 and rec["other_buy"] == 100,
      f"買超彙總不對：{rec['flip_buy']}/{rec['build_buy']}/{rec['foreign_buy']}/{rec['other_buy']}")
check(rec["top15_buy"] == 850, f"總買超應 850：{rec['top15_buy']}")
check(abs(rec["flip_pct"] - 52.9) < 0.1 and abs(rec["build_pct"] - 11.8) < 0.1, f"百分比不對：{rec['flip_pct']}/{rec['build_pct']}")
check(rec["verdict"] == "flip", f"應判隔日沖主導：{rec['verdict']}")
check(rec["build_sell"] == 30, f"建倉者出貨應 30：{rec['build_sell']}")
check(rec["build_net_win"] == 400 + (80 * 4 - 30), f"建倉型近期累計淨買應 690：{rec['build_net_win']}")
check(rec["detail"]["flip"][0]["b"] == "凱基-台北", "detail 隔日沖第一名應是凱基-台北")

# 行為證據優先於名單：名單券商若實際是「買了不賣」→ 建倉
rows2 = [row("DDD", d, "國泰-敦南", 100) for d in DATES[3:]]
for sym in ("BBB", "CCC"):
    for d in DATES:
        rows2.append(row(sym, d, "某券商", 10))
rec2 = bs.compute_symbol_style(bs.group_by_symbol(rows2)["DDD"], D, tdates, listed_names=("國泰-敦南",))
check(rec2["brokers"][0]["type"] == "build", f"名單券商買了不賣應歸建倉：{rec2['brokers'][0]}")
check(rec2["verdict"] == "build", f"應判建倉主導：{rec2['verdict']}")

# 隔天沒進賣超榜的可見性：賣超榜滿 15 家且門檻 100；買 400(賣一半=200≥100 → 續抱)；買 150(75<100 → 不計)
rows3 = [row("EEE", DATES[3], "甲券商", 400), row("EEE", DATES[3], "乙券商", 150)]
rows3 += [row("EEE", DATES[4], f"賣{i}", -(100 + i)) for i in range(15)]      # d9 賣超榜 15 家，最小 100
rows3 += [row("EEE", DATES[5], "甲券商", 10), row("EEE", DATES[6], "甲券商", 10), row("EEE", D, "甲券商", 10)]
for sym in ("BBB", "CCC"):
    for d in DATES:
        rows3.append(row(sym, d, "某券商", 10))
rec3 = bs.compute_symbol_style(bs.group_by_symbol(rows3)["EEE"], D, tdates)
m3 = {c["name"]: c for c in rec3["brokers"]}
check(m3["甲券商"]["holds"] >= 1 and m3["甲券商"]["flips"] == 0, f"甲券商 d8→d9 應算續抱：{m3['甲券商']}")
check(m3["乙券商"]["holds"] == 0 and m3["乙券商"]["flips"] == 0, f"乙券商 d8→d9 應不計（榜門檻 100、賣一半僅 75）：{m3['乙券商']}")

# 資料日不存在 → None；歷史天數不足 → nodata
check(bs.compute_symbol_style(by["AAA"], "2026-09-15", tdates) is None, "不在交易日曆的日期應回 None")
rows4 = [row("FFF", D, "某某", 100)]
for sym in ("BBB", "CCC"):
    for d in DATES:
        rows4.append(row(sym, d, "某券商", 10))
rec4 = bs.compute_symbol_style(bs.group_by_symbol(rows4)["FFF"], D, tdates)
check(rec4["verdict"] == "nodata" and rec4["hist_days"] == 1, f"只有1天資料應 nodata：{rec4['verdict']}")

# 批次輸出
out = bs.compute_style_rows(rows, [D, DATES[-2]], listed_names=("國泰-敦南",))
syms = {(o["symbol"], o["log_date"]) for o in out}
check(("AAA", D) in syms and ("BBB", D) in syms, f"批次應包含 AAA/BBB：{sorted(syms)[:6]}")
check(all("brokers" not in o and "detail" in o for o in out), "批次輸出不該含 brokers 明細，需含 detail")

# 外資判斷
check(bs.is_foreign_broker("港商野村") and bs.is_foreign_broker("台灣摩根士丹利") and bs.is_foreign_broker("新加坡商瑞銀"), "外資名稱判斷")
check(not bs.is_foreign_broker("群益金鼎-高盛") and not bs.is_foreign_broker("大和國泰") and not bs.is_foreign_broker("凱基-台北"), "國內券商不該被當外資")
check(bs.is_foreign_broker("某某", "8440"), "代號 8440 應為外資")

# 顯示字串
check("建倉主導" in bs.verdict_label("build") and bs.short_label(None) == "—", "顯示字串")
check(bs.short_label(rec).startswith("🎲隔日沖主導 建12/沖53/外24"), f"short_label：{bs.short_label(rec)}")

# ── 預測力驗證 validate_predictive：隔日沖型隔天倒貨率高、建倉型低 ──
VD = [f"2026-08-{d:02d}" for d in range(3, 15)]            # 12 個資料日（含週末無所謂，只看順序）
vrows = []
for i, d in enumerate(VD):
    if i % 2 == 0:
        vrows.append(row("VVV", d, "凱基-台北", 300))        # 偶數日買、隔天賣
    else:
        vrows.append(row("VVV", d, "凱基-台北", -280))
    vrows.append(row("VVV", d, "元富-建倉", 100))             # 每天買、不賣
for sym in ("BBB", "CCC"):
    for d in VD:
        vrows.append(row(sym, d, "某券商", 10))
v = bs.validate_predictive(vrows, listed_names=())
check("隔日沖型(實測)" in v and v["隔日沖型(實測)"]["flip_rate"] == 100.0, f"隔日沖型隔天倒貨率應 100%：{v}")
check("建倉型(實測)" in v and v["建倉型(實測)"]["flip_rate"] == 0.0, f"建倉型隔天倒貨率應 0%：{v}")
check(all(x["n"] >= 1 for x in v.values()), "每個型態都該有樣本")

print("✅ test_broker_style 全部通過" if ok else "❌ test_broker_style 失敗")
sys.exit(0 if ok else 1)
