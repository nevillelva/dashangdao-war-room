"""AI 戰略推演離線測試：假 LLM，不打任何外部服務。"""
import json
import sys

import strategy_view as SV
import tail_card as TK

ok = True


def chk(n, c, extra=None):
    global ok
    print(("✅ " if c else "❌ ") + n + ("" if c else f"  → {extra}"))
    ok = ok and c


good = {"thesis": "投信連買且在季線上", "can_buy": "開低後量縮", "cannot_buy": "大盤跌破季線", "key_level": "停損 90",
        "plan": "13:20 進、抱 20 日", "stance": "小量"}
chk("合法 JSON 通過", SV.parse(json.dumps(good, ensure_ascii=False)) == good)
chk("包 ```json 也能解", SV.parse("```json\n" + json.dumps(good, ensure_ascii=False) + "\n```") == good)
chk("非 JSON 退回", SV.parse("我覺得可以買") is None)
chk("缺欄位退回", SV.parse(json.dumps({k: v for k, v in good.items() if k != "plan"})) is None)
chk("stance 不在清單退回", SV.parse(json.dumps(dict(good, stance="買進"))) is None)
chk("空字串欄位退回", SV.parse(json.dumps(dict(good, thesis="  "))) is None)

buy = {"symbol": "2330", "source": "主戰清單", "track": "波段主力", "px": 100, "ref": 99, "zones": ["Z1"], "basis": "查3 ✅",
       "decision": "buy", "why": [], "entry": "13:20 市價", "stop_px": 90, "stop_pct": 10, "hold": 20, "next_day": "明天不用賣"}
small = dict(buy, symbol="2317", decision="small")
skip = {"symbol": "1101", "source": "雷達", "decision": "skip", "why": ["規則沒過"]}
obs = {"symbol": "3231", "decision": "skip", "why": ["無對應回測規則：僅觀察，不評估買賣"]}
cards = [buy, small, skip, obs, dict(skip, symbol="9999")]

f = SV.funnel_counts(cards)
chk("漏斗：5 檔初選、2 檔留下、1 檔僅觀察、2 檔被擋", f == {"total": 5, "kept": 2, "observe": 1, "dropped": 2}, f)
ft = SV.funnel_text(cards)
chk("漏斗文字含初選與篩後", "初選 5 檔" in ft and "2 檔" in ft and "擋下 2" in ft, ft)

seen = []


def fake(system, user):
    seen.append(user)
    return True, json.dumps(good, ensure_ascii=False)


res = SV.run(cards, "大盤：加權跌破季線", fake)
chk("只推演買／小量", sorted(res) == ["2317", "2330"], res)
chk("證據包含漏斗與停損、不含報酬或勝率欄位", "初選 5 檔" in seen[0] and "停損：90" in seen[0] and "報酬" not in seen[0], seen[:1])
chk("LLM 失敗：記錄錯誤、不中斷", "error" in SV.run([buy], "", lambda s, u: (False, "HTTP 429"))["2330"])
chk("LLM 拋例外：記錄、不中斷", "error" in SV.run([buy], "", lambda s, u: (_ for _ in ()).throw(RuntimeError("x")))["2330"])
chk("格式不合：退回", SV.run([buy], "", lambda s, u: (True, "好"))["2330"] == {"error": "輸出格式不合（退回）"})
chk("壞資料不顯示", SV.lines_for(buy, {"error": "x"}) == [] and SV.lines_for(buy, None) == [])

dis = dict(good, stance="暫不買")
ls = SV.lines_for(buy, dis)
chk("AI 暫不買 vs 系統買：標出不同意", any("與系統判定不同" in x for x in ls), ls)
chk("一致時不標不同意、但一定標未驗證", not any("與系統判定不同" in x for x in SV.lines_for(buy, good)) and any("未經回測驗證" in x for x in SV.lines_for(buy, good)))

# 戰卡訊息整合
b = dict(buy, ai=good, entry="13:20 市價", tp_px=None)
msg = TK.message("2026-10-29", "prep", [b, skip], [], {"2330": "台積電"}, market="大盤：x", funnel=SV.funnel_text([b, skip]))
chk("戰卡訊息含漏斗與 AI 推演", "🔎 初選 2 檔" in msg and "🤖 AI 戰略推演" in msg and "能買：" in msg and "計畫：" in msg, msg)
msg2 = TK.message("2026-10-29", "prep", [buy, skip], [], {})
chk("沒有 ai 時訊息不變（無推演行）", "AI 戰略推演" not in msg2)

# 追高提醒已撤回：5 年回測（chuan_stop_bt）進場日開盤高於前收 ≥5% 並沒有比較差，沒有依據就不提醒
d0 = TK.decide({"symbol": "2330", "source": "主戰清單", "track": "波段主力"}, rule_res={"pass": True, "rule": "查3", "detail": "ok", "hold": 20},
               px=107.5, ref=100.0, bull60=True)
chk("現價高於昨收 7.5%：照規則判買、沒有未經驗證的追高提醒", d0["decision"] == "buy" and not any("追高" in w for w in d0["why"]), d0)

# ───── 做空開盤確認 ─────
item_c8 = {"symbol": "8213", "cmds": ["查8.昨日強勢動能延續"], "why": "查8隔日沖倒貨", "ref_close": 100.0}
item_dt = {"symbol": "2413", "cmds": [], "why": "分點空單", "ref_close": 100.0}
d = TK.short_decide(item_c8, 105.0)
chk("查8 開高 5%：可空，停損 +6%", d["decision"] == "short" and d["stop_px"] == 111.3 and d["gap"] == 5.0, d)
chk("查8 開高 2%：不空（要 ≥3%）", TK.short_decide(item_c8, 102.0)["decision"] == "skip")
chk("分點空單開高 1.5%：可空（門檻 1%）", TK.short_decide(item_dt, 101.5)["decision"] == "short")
chk("開高 ≥7%：不做（離漲停太近）", TK.short_decide(item_c8, 107.5)["decision"] == "skip")
chk("缺開盤價：資料不足", "資料不足" in TK.short_decide(item_c8, None)["why"][0])
good_s = dict(good, stance="可空")
chk("做空 stance 字彙：可空通過、可買退回", SV.parse(json.dumps(good_s), "short") == good_s and SV.parse(json.dumps(dict(good, stance="可買")), "short") is None)
chk("做多不接受『可空』", SV.parse(json.dumps(good_s), "long") is None)
seen_s = []
def fake_s(system, user):
    seen_s.append(system)
    return True, json.dumps(good_s, ensure_ascii=False)
rs = SV.run([d, TK.short_decide(item_c8, 102.0)], "大盤：x", fake_s, side="short")
chk("做空只推演可空卡、使用做空 prompt", list(rs) == ["8213"] and "做空" in seen_s[0], (rs, seen_s[:1]))
d["ai"] = rs["8213"]
sm = TK.short_message("2026-10-29", [d, TK.short_decide(item_c8, 102.0)], {"8213": "測試"}, market="大盤：x", funnel="空方初選 2 檔 → 開盤過關 1 檔")
chk("做空訊息含漏斗、可空、能空、AI 推演、不空原因", all(k in sm for k in ("🔎 空方初選 2 檔", "可空 測試(8213)", "能空：", "AI 戰略推演", "❌ 不空")), sm)
chk("沒有可空時訊息明說", "沒有符合條件的做空標的" in TK.short_message("2026-10-29", [TK.short_decide(item_c8, 102.0)]))
sys.exit(0 if ok else 1)
