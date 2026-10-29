import sys
import simple_view as S
ok = True
def chk(n, c, e=None):
    global ok
    print(("✅ " if c else "❌ ") + n + ("" if c else f"  → {e}"))
    ok = ok and c
w = S.short_why("查8隔日沖倒貨（量比7.2）｜開盤比昨收高 3～7% 才當沖空（≥7% 離漲停太近、回補不了，不做），收盤前回補｜也中查7：已持有→開盤賣")
chk("盤前理由去掉價位規則長說明、保留入選原因", "才當沖空" not in w and "量比7.2" in w and "也中查7" in w, w)
chk("多方理由原樣保留", S.short_why("主力偵測2訊號＋營收年增44%｜法人連買") == "主力偵測2訊號＋營收年增44%　法人連買")
chk("空理由全是說明時至少留一段", S.short_why("開盤比昨收高 3～7% 才當沖空") != "")
chk("還沒開盤價", S.short_reason("還沒有開盤價（09:00 後自動顯示；保留）") == "等開盤價（09:00 後出來）")
chk("開高≥7%", "太接近漲停" in S.short_reason("開盤 108 已比昨收 100 高 8.0%（≥7%、離漲停只剩 2%）"))
chk("可空", S.short_reason("開盤 105 比昨收 100 高 5.0%：開盤當沖空、收盤前回補") == "開盤高 5.0%：可空，收盤前回補")
chk("持有者", S.short_reason("已持有（開盤 105，比昨收 +5.0%）→ 開盤直接賣；沒持有不追買").startswith("持有者：開盤直接賣"))
chk("多方 13:18", S.short_reason("13:18 依多時點規則確認才買：x｜參考：10:00 守住開盤").startswith("13:18 確認才買"))
chk("長字串截短", len(S.short_reason("某種沒見過的很長很長" * 10)) <= 40)
chk("空字串", S.short_reason("") == "" and S.short_why("") == "")
chk("訊號：通過", S.signal_verdict({"side": "long", "overall": "pass"})[:2] == ("✅", "多方通過"))
chk("訊號：不合格", S.signal_verdict({"side": "short", "overall": "fail"})[0] == "❌")
chk("訊號：等資料", S.signal_verdict({"overall": "pending"})[0] == "⏳")
chk("訊號：無判讀", S.signal_verdict({})[1] == "尚無判讀")
chk("訊號：與方向相反提醒", "與空方相反" in S.signal_verdict({"side": "short", "overall": "pass", "change_pct": 1.0})[2])
sys.exit(0 if ok else 1)
