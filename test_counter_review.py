"""反方審查（影子版）離線測試：假 LLM，不打任何外部服務。"""
import json
import sys

import counter_review as CR

ok = True


def chk(n, c, extra=None):
    global ok
    print(("✅ " if c else "❌ ") + n + ("" if c else f"  → {extra}"))
    ok = ok and c


good = {"bull": "投信連買", "bear": "費半弱", "main_risk": "大盤破季線", "confidence": 0.6, "verdict": "觀察"}
chk("合法 JSON 通過", CR.parse_verdict(json.dumps(good, ensure_ascii=False)) == dict(good, confidence=0.6), None)
chk("包 ```json 也能解", CR.parse_verdict("```json\n" + json.dumps(good, ensure_ascii=False) + "\n```") is not None)
chk("非 JSON 退回", CR.parse_verdict("我覺得不錯") is None)
chk("缺欄位退回", CR.parse_verdict(json.dumps({"bull": "a", "bear": "b", "confidence": 0.5, "verdict": "維持"})) is None)
chk("verdict 不在清單退回", CR.parse_verdict(json.dumps(dict(good, verdict="買進"))) is None)
chk("confidence 超出範圍退回", CR.parse_verdict(json.dumps(dict(good, confidence=1.5))) is None)
chk("confidence 為字串退回", CR.parse_verdict(json.dumps(dict(good, confidence="0.5"))) is None)
chk("confidence 為布林退回", CR.parse_verdict(json.dumps(dict(good, confidence=True))) is None)

card = {"symbol": "2330", "source": "主戰清單", "track": "波段主力", "px": 100, "ref": 99,
        "zones": ["Z1"], "basis": "查3 ✅", "decision": "buy", "why": []}
skip = {"symbol": "1101", "decision": "skip", "why": ["規則沒過"]}
seen = []


def fake_llm(system, user):
    seen.append(user)
    return True, json.dumps(good, ensure_ascii=False)


res = CR.review_cards([card, skip], "大盤：加權跌破季線", fake_llm)
chk("只審買／小量，跳過不買", list(res) == ["2330"], res)
chk("結果含 verdict 與 confidence", res["2330"]["verdict"] == "觀察" and res["2330"]["confidence"] == 0.6, res)
chk("證據包不含結果欄位（只有決策當下資料）", "報酬" not in seen[0] and "勝率" not in seen[0] and "2330" in seen[0], seen[:1])

res2 = CR.review_cards([card], "", lambda s, u: (False, "HTTP 429"))
chk("LLM 失敗：記錄錯誤、不中斷", "error" in res2["2330"], res2)
res3 = CR.review_cards([card], "", lambda s, u: (True, "我覺得不錯"))
chk("LLM 輸出格式不合：退回", res3["2330"] == {"error": "輸出格式不合（退回）"}, res3)

# 8. 回放：證據包不含結果；統計與安慰劑
pk = CR.replay_pack(["投信買超", "季線上"], "2025-03-03", "Z1")
chk("回放證據包不含報酬或代號", "報酬" not in pk and "%" not in pk and "2025-03-03" in pk, pk)
recs = [{"verdict": "避開", "r": -0.05}] * 30 + [{"verdict": "維持", "r": 0.04}] * 60 + [{"verdict": "觀察", "r": 0.01}] * 20
sm = CR.summarize_replay(recs, placebo_rounds=200)
chk("統計：避開桶平均較差", sm["buckets"]["避開"]["avg_pct"] < sm["buckets"]["維持"]["avg_pct"], sm)
chk("安慰劑：明顯差異 → p 很小", sm["placebo_p"] is not None and sm["placebo_p"] < 0.05, sm)
rnd = [{"verdict": v, "r": (0.01 if i % 2 else -0.01)} for i, v in enumerate(["避開", "維持", "觀察"] * 40)]
sm2 = CR.summarize_replay(rnd, placebo_rounds=200)
chk("安慰劑：無差異 → p 不小", sm2["placebo_p"] is not None and sm2["placebo_p"] > 0.2, sm2)
chk("樣本太少：不下結論", CR.summarize_replay(recs[:5])["placebo_p"] is None)
sys.exit(0 if ok else 1)
