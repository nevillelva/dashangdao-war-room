#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_etf_core.py —— ETF 改版後的純函式：分類、綜所稅、補充保費、資金配置、錯開建議、三種年領口徑。離線。"""
import datetime as dt
import json
import os
import sys

import etf_core as E

bad = 0
TODAY = dt.date(2026, 10, 6)


def check(ok, msg):
    global bad
    print("✅" if ok else "❌", msg)
    bad += (not ok)


def near(a, b, tol=1e-6):
    return abs(a - b) <= tol


# ---------- 1) 分類
cases = {("0056", "元大高股息"): "dividend", ("00878", "國泰永續高股息"): "dividend", ("00919", "群益台灣精選高息"): "dividend",
         ("00929", "復華台灣科技優息"): "dividend", ("00940", "元大台灣價值高息"): "dividend", ("00927", "群益半導體收益"): "dividend",
         ("0050", "元大台灣50"): "cap", ("006208", "富邦台50"): "cap", ("00850", "元大臺灣ESG永續"): "cap", ("00888", "永豐台灣ESG"): "cap",
         ("00679B", "元大美債20年"): "bond", ("00967B", "元大優息美債"): "bond", ("00631L", "元大台灣50正2"): "lev",
         ("00632R", "元大台灣50反1"): "lev", ("00635U", "期元大S&P黃金"): "lev", ("00892", "富邦台灣半導體"): "theme",
         ("00908", "富邦入息REITs+"): "theme", ("00678", "群益那斯達克生技"): "theme"}
wrong = [(k, E.etf_kind(*k), v) for k, v in cases.items() if E.etf_kind(*k) != v]
check(not wrong, f"ETF 分類（{len(cases)} 檔代表性標的）：{wrong or '全部正確'}")
check(E.is_active_etf("00400A", "主動國泰動能高息") and not E.is_active_etf("0056", "元大高股息"), "主動式判斷")
check(E.is_foreign("中信全球高股息") and not E.is_foreign("元大高股息"), "海外標的判斷")
sc = os.path.join(os.path.dirname(os.path.abspath(__file__)))
check(E.ex_group_of([1, 4, 7, 10], "quarterly") == "A" and E.ex_group_of([2, 5, 8, 11], "quarterly") == "B"
      and E.ex_group_of([3, 6, 9, 12], "quarterly") == "C" and E.ex_group_of([1, 5], "quarterly") is None
      and E.ex_group_of(list(range(1, 13)), "monthly") == "M", "季配錯開組 A/B/C 與月配 M")

# ---------- 2) 綜所稅（股利所得）
t = E.income_tax_on_dividends(100000, 0.05)
check(near(t["credit"], 8500) and near(t["combined"], -3500) and near(t["separate"], 28000) and t["best"] == "combined", "5% 級距：合併 −3,500（可退稅）、分開 28,000 → 選合併")
t = E.income_tax_on_dividends(100000, 0.12)
check(near(t["combined"], 3500) and t["best"] == "combined", "12% 級距：合併 3,500")
t = E.income_tax_on_dividends(100000, 0.30)
check(near(t["combined"], 21500) and t["best"] == "combined", "30% 級距：合併 21,500 < 分開 28,000")
t = E.income_tax_on_dividends(100000, 0.40)
check(near(t["combined"], 31500) and near(t["separate"], 28000) and t["best"] == "separate" and near(t["best_tax"], 28000), "40% 級距：分開 28% 較省")
t = E.income_tax_on_dividends(2_000_000, 0.20)
check(near(t["credit"], 80000) and near(t["combined"], 320000) and near(t["separate"], 560000), "抵減額上限 8 萬（股利 200 萬、20%：合併 32 萬）")
t = E.income_tax_on_dividends(0, 0.2)
check(t["best_tax"] == 0 and t["credit"] == 0, "沒有股利：稅為 0")

# ---------- 3) 補充保費（只對 54C 占比、單次 ≥ 2 萬）
g, nhi, fee, net = E.net_payment(100000, True, True, 0.1)
check(nhi == 0 and net == 100000 - 10, "單次 10 萬、54C 10% → 基數 1 萬 < 2 萬 → 不扣補充保費")
g, nhi, fee, net = E.net_payment(100000, True, True, 0.25)
check(nhi == round(25000 * 0.0211) and net == 100000 - nhi - 10, "54C 25% → 基數 2.5 萬 ≥ 2 萬 → 扣整筆基數的 2.11%（不是只扣超出部分）")
g, nhi, fee, net = E.net_payment(19999, True, True, 1.0)
check(nhi == 0, "19,999 元 → 未達門檻不扣")

# ---------- 4) 候選表（合成配息）與新欄位
def ev(sym, y, m, d, cash):
    ex = dt.date(y, m, d)
    return {"symbol": sym, "ex_date": ex.isoformat(), "cash_per_unit": cash, "pay_date": (ex + dt.timedelta(days=25)).isoformat()}


MASTER = [
    {"symbol": "MA", "name": "月配高息", "last_price": 20.0, "price_1y": 18.0, "listed_date": "2020-01-01", "vol_1y": 12, "mdd_1y": -10, "sharpe_1y": 0.8},
    {"symbol": "QA", "name": "季配高股息A", "last_price": 30.0, "price_1y": 28.0, "listed_date": "2018-01-01", "mdd_1y": -8, "sharpe_1y": 1.2},
    {"symbol": "QB", "name": "季配高股息B", "last_price": 40.0, "price_1y": 36.0, "listed_date": "2018-01-01", "mdd_1y": -12, "sharpe_1y": 0.5},
    {"symbol": "QC", "name": "季配高股息C", "last_price": 25.0, "price_1y": 24.0, "listed_date": "2018-01-01", "mdd_1y": -6, "sharpe_1y": 0.9},
    {"symbol": "CAP", "name": "台灣50", "last_price": 100.0, "price_1y": 80.0, "listed_date": "2003-01-01", "mdd_1y": -15, "sharpe_1y": 1.5},
    {"symbol": "BD", "name": "某某債券B", "last_price": 15.0, "price_1y": 14.5, "listed_date": "2015-01-01"},
]
EVENTS = []
for k in range(12):           # MA 月配：2025-10 ~ 2026-09 每月 0.1（今天 2026-10-06，近 12 個月 12 次）
    y, m = (2025, 10 + k) if 10 + k <= 12 else (2026, 10 + k - 12)
    EVENTS.append(ev("MA", y, m, 10, 0.1))
for sym_ in ("MA", "QA", "QB", "QC", "CAP", "BD"):       # 更早的歷史（避免被判成『上市未滿一年』）
    EVENTS.append(ev(sym_, 2024, 6, 10, 0.3))
# QA：除息月 1/4/7/10（近一年 2025-10-15 已超過 365 天? 2025-10-15 < 2025-10-06+... 取 2026-01/04/07 + 2025-10-20 避免邊界）
for (y, m, c) in [(2025, 10, 0.5), (2026, 1, 0.5), (2026, 4, 0.6), (2026, 7, 0.9)]:
    EVENTS.append(ev("QA", y, m, 20, c))
for (y, m, c) in [(2025, 11, 0.6), (2026, 2, 0.6), (2026, 5, 0.6), (2026, 8, 0.6)]:
    EVENTS.append(ev("QB", y, m, 20, c))
for (y, m, c) in [(2025, 12, 0.4), (2026, 3, 0.4), (2026, 6, 0.4), (2026, 9, 0.4)]:
    EVENTS.append(ev("QC", y, m, 20, c))
for (y, m, c) in [(2025, 11, 1.0), (2026, 5, 1.2)]:
    EVENTS.append(ev("CAP", y, m, 20, c))
for k in range(4):
    EVENTS.append(ev("BD", 2025 + (10 + 3 * k) // 12, (10 + 3 * k) % 12 + 1, 5, 0.2))

cands = E.candidate_table(MASTER, EVENTS, TODAY)
cm = {c["symbol"]: c for c in cands}
check(set(cm) >= {"MA", "QA", "QB", "QC", "CAP"}, f"候選表含各檔（{sorted(cm)}）")
check(cm["MA"]["kind"] == "dividend" and cm["MA"]["freq"] == "monthly" and cm["MA"]["ex_group"] == "M", "月配高息：高股息／月配／組 M")
check(cm["QA"]["ex_group"] == "A" and cm["QB"]["ex_group"] == "B" and cm["QC"]["ex_group"] == "C", "季配三檔分屬 A/B/C 組")
check(cm["CAP"]["kind"] == "cap", "台灣50 → 市值型")
qa = cm["QA"]
check(near(qa["annual"], 2.5) and near(qa["annual_latest"], 0.9 * 4) and near(qa["annual_peak"], 0.9 * 4), "QA：近12月 2.5；最近一次×4＝3.6；單次最高×4＝3.6")
check(near(qa["annual_latest"] / qa["annual"], 3.6 / 2.5), "宣傳口徑(最近一次×4)比近12月實際高 44%")
check(cm["BD"]["kind"] == "bond", "債券B → bond")

# ---------- 5) 篩選 / 錯開建議 / 類型統計
f = E.filter_candidates(cands, kinds=("dividend", "cap"))
check({c["symbol"] for c in f} == {"MA", "QA", "QB", "QC", "CAP"}, f"預設池：高股息＋市值型（半年配的市值型不被次數門檻排除）{sorted(c['symbol'] for c in f)}")
check("BD" not in {c["symbol"] for c in f} and "BD" in {c["symbol"] for c in E.filter_candidates(cands, kinds=("dividend",), include_bond=True)}, "債券需明確勾選才納入")
f2 = E.filter_candidates(cands, kinds=("dividend",), max_yield_pct=8.0)
check({c["symbol"] for c in f2} == {"MA", "QB", "QC"}, f"殖利率上限 8%：QA(8.33%) 被排除（{sorted(c['symbol'] for c in f2)}）")
lad = E.suggest_ladder(f, "yield", top=3)
check([c["symbol"] for c in lad["A"]] == ["QA"] and [c["symbol"] for c in lad["B"]] == ["QB"] and [c["symbol"] for c in lad["C"]] == ["QC"]
      and [c["symbol"] for c in lad["M"]] == ["MA"], "錯開建議：A/B/C/M 各自歸位")
check(E.rank_value(cm["QA"], "sharpe") > E.rank_value(cm["QB"], "sharpe") and E.rank_value(cm["QC"], "lowrisk") > E.rank_value(cm["QB"], "lowrisk"), "排序依據：Sharpe、低回撤")
ks = E.kind_summary(cands)
check("dividend" in ks and "cap" in ks and ks["dividend"]["n"] >= 4 and ks["cap"]["yield_med"] is not None, "高股息 vs 市值型 的事實統計")

# ---------- 6) 資金配置（整張/零股）
P = [{"symbol": "X", "price": 30.0}, {"symbol": "Y", "price": 20.0}, {"symbol": "Z", "price": 10.0}]
sh = E.allocate_shares(700000, P)
check(all(v % 1000 == 0 for v in sh.values()) and sh["X"] == 7000 and sh["Y"] == 11000 and sh["Z"] == 23000, f"70 萬等分三檔、整張：{sh}")
sh1 = E.allocate_shares(700000, P, lot=1)
cost = sum(sh1[p["symbol"]] * p["price"] for p in P)
check(cost + E.default_fee(cost) <= 700000 + 3 and cost > 690000, f"零股：成本 {cost:,.0f} ＋手續費不超過本金")
sh2 = E.allocate_shares(700000, P, {"X": 50, "Y": 30, "Z": 20})
cap2 = {p["symbol"]: sh2[p["symbol"]] * p["price"] for p in P}
check(cap2["X"] > cap2["Y"] > cap2["Z"] and abs(cap2["X"] / sum(cap2.values()) - 0.5) < 0.06, f"自訂占比 50/30/20 → 資金約 {cap2['X'] / sum(cap2.values()):.0%}/{cap2['Y'] / sum(cap2.values()):.0%}/{cap2['Z'] / sum(cap2.values()):.0%}")
check(E.allocate_shares(0, P) == {} and E.allocate_shares(1000, [{"symbol": "X", "price": 30.0}], lot=1000) == {"X": 0}, "資金不足一張 → 0 股")

# ---------- 7) 逐月實領＋稅後（手算對照）
ma = dict(cm["MA"])
r = E.plan_by_capital(1_010_000, [ma], lot=1000, bracket=0.12, default_ratio=1.0)
row = r["rows"][0]
check(row["shares"] == 50000, f"101 萬買月配(20 元)：50,000 股（{row['shares']}）")
check(r["months_with_income"] == 12 and near(r["monthly_gross"][1] + r["monthly_gross"][2] + sum(r["monthly_gross"][k] for k in range(3, 13)), 60000), "年毛配息 6 萬、12 個月都有入帳")
check(r["annual_net"] == 60000 - 12 * 10 and r["annual_nhi"] == 0, "匯費 10×12 後實領 59,880、無補充保費")
check(near(r["income_tax"]["combined"], 60000 * 0.12 - 60000 * 0.085) and r["income_tax"]["best"] == "combined", "12% 級距合併計稅 2,100")
check(near(r["annual_net_after_tax"], 59880 - 2100) and near(r["monthly_avg_after_tax"], (59880 - 2100) / 12), "稅後全年 57,780、月均 4,815")
check(near(r["buy_fee"], E.default_fee(1_000_000)) and r["cash_left"] > 0, f"買進手續費 {r['buy_fee']:,.0f}；剩餘現金 {r['cash_left']:,.0f}")
check(near(r["yield_after_tax_pct"], 57780 / 1_010_000 * 100), "稅後殖利率＝稅後年領 ÷ 本金")

# 補充保費觸發（季配、單次 ≥ 2 萬）
q = dict(cm["QB"])           # 每季 0.6、30 元… 價 40
rq = E.evaluate_holdings([q], {"QB": 40000}, default_ratio=1.0, bracket=0.12)    # 每季 24,000
check(rq["nhi_hits"] == ["QB"] and rq["annual_nhi"] == 4 * round(24000 * 0.0211), f"每季 2.4 萬、54C 100% → 每次扣補充保費（年 {rq['annual_nhi']:,}）")
rq2 = E.evaluate_holdings([q], {"QB": 40000}, default_ratio=0.0, bracket=0.12)
check(rq2["annual_nhi"] == 0 and rq2["taxable_dividend"] == 0 and rq2["income_tax"]["best_tax"] == 0, "54C 占比 0%（如 00919 近期）→ 補充保費與綜所稅都為 0")
rq3 = E.evaluate_holdings([q], {"QB": 30000}, default_ratio=0.9, bracket=0.12)     # 每季 18,000×0.9=16,200 → 接近門檻
check(rq3["nhi_hits"] == [] and rq3["nhi_near"] == ["QB"], "單次 54C 基數 1.62 萬（≥75% 門檻）→ 列為『接近門檻』提醒")
# 分檔避開門檻：同樣 8 萬股拆兩檔（各 4 萬股）→ 每檔每季各 2.4 萬 → 仍觸發；拆成三檔各 2.67 萬股 → 1.6 萬 → 不觸發
q2 = dict(cm["QB"]); q2["symbol"] = "QB2"
q3 = dict(cm["QB"]); q3["symbol"] = "QB3"
rs = E.evaluate_holdings([q, q2, q3], {"QB": 26000, "QB2": 26000, "QB3": 26000}, default_ratio=1.0)
check(rs["annual_nhi"] == 0, "同樣總股數拆成三檔、每檔每次 < 2 萬 → 不用繳補充保費（分散的稅務好處）")

# 三種口徑
qa_r = E.evaluate_holdings([dict(cm["QA"])], {"QA": 1000})
ba = qa_r["basis_annual"]
check(near(ba["trailing"], 2500) and near(ba["latest"], 3600) and near(ba["peak"], 3600) and near(qa_r["basis_monthly"]["peak"], 300), "三種年領口徑：實際 2,500／最近×4 3,600／最高×4 3,600")

# ---------- 8) 反推本金與正向計算一致
pl = E.plan_income(20000, [dict(cm["QA"]), dict(cm["QB"]), dict(cm["QC"])], lot=1000)
sh_r = {x["symbol"]: int(x["shares"]) for x in pl["rows"]}
ev_r = E.evaluate_holdings([dict(cm["QA"]), dict(cm["QB"]), dict(cm["QC"])], sh_r)
check(near(pl["avg_monthly"], ev_r["monthly_avg_net"]) and ev_r["months_with_income"] == pl["months_with_income"],
      f"規劃器(反推)與 evaluate_holdings 同一組股數算出的月均實領一致（{ev_r['monthly_avg_net']:,.0f}）")
check(ev_r["months_with_income"] == 12, "A/B/C 三檔錯開 → 12 個月都有入帳")

# ---------- 9) 稅後目標反推
trio = [dict(cm["QA"]), dict(cm["QB"]), dict(cm["QC"])]
pa = E.plan_for_after_tax(20000, trio, lot=1000, bracket=0.20)
check(pa["achieved"] and pa["monthly_avg_after_tax"] >= 20000 and pa["monthly_avg_after_tax"] < 20000 + 0.08 * 20000, f"每月稅後 2 萬（20% 級距）→ 需本金 {pa['cost']:,.0f}，稅後月均 {pa['monthly_avg_after_tax']:,.0f}")
pb = E.plan_for_after_tax(20000, trio, lot=1000, bracket=0.0)
check(pb["achieved"] and pb["cost"] < pa["cost"], "邊際稅率 0%（合併計稅可退 8.5%）所需本金比 20% 級距少")
check(E.plan_for_after_tax(20000, [], lot=1000) is None, "沒有可用標的 → None")

check(E.concentration_tag(30, 50, 80) == "🔴 高含積量" and E.concentration_tag(12, 30, 60) == "🟡 含積量中", "集中度標記：台積電權重分級")
check(E.concentration_tag(3, 50, 70) == "🟠 前三大集中" and E.concentration_tag(0, 20, 50) == "🟢 分散" and E.concentration_tag(None, None, None) == "—", "集中度標記：前三大／分散／無資料")

print(f"\n{'全部通過' if not bad else str(bad) + ' 項失敗'}")
raise SystemExit(1 if bad else 0)
