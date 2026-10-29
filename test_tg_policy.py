import sys
import tg_policy as P
ok = True
def chk(n, c, e=None):
    global ok
    print(("✅ " if c else "❌ ") + n + ("" if c else f"  → {e}"))
    ok = ok and c
T = lambda m: P.classify(m)[0]
chk("尾盤定版戰卡推", T("🎯 13:18 尾盤定版戰卡 10/29\n建議買…") == "push")
chk("預備戰卡推", T("📋 預備戰卡（12:33）…") == "push")
chk("09:03 開盤確認推", T("🔻 09:03 做空開盤確認…") == "push")
chk("早盤情報推", T("🌅 早盤情報 10/29") == "push")
chk("時間停損出場推", T("⏱️ [2026-10-29] 時間停損出場 2330") == "push")
chk("強制出場抓不到價（異常且交易）推", T("⚠️ [2026-10-29] 華新(1605) 13:25強制出場抓不到即時價") == "push")
chk("看門狗補打通知不推", T("🛠️ [獨立監控-Cloudflare] 偵測到 2 個排程漏跑，已主動補打GitHub Actions觸發：") == "digest")
chk("休市日不進場不推", T("⏭️ [2026-10-29] 非交易日，今日不進場、不出場") == "digest")
chk("無可空標的不推", T("🔻 09:03 開盤確認：沒有符合條件的做空標的") == "digest")
chk("三關輪詢資訊不推", T("ℹ️ [d] 三關輪詢：候選池尚未就緒") == "digest")
chk("回補完成不推", T("✅ 券商分點歷史回補完成：…") == "digest")
chk("門檻校準完成不推", T("🎯 [d] 門檻敏感度掃描完成…") == "digest")
chk("券商分點停擺（已知）不推", T("⚠️ [d] 券商分點抓取停擺 …") == "digest")
chk("千張大戶警告不推", T("⚠️ [d] 千張大戶排程：TDCC連線失敗") == "digest")
chk("候選池寫入失敗＝異常警報", T("⚠️ [d] 候選池寫入Supabase失敗") == "alert")
chk("資料源異常警報＝異常警報", T("🩺 [d] 資料源異常警報 …") == "alert")
chk("未知訊息預設推", T("某個新功能的訊息") == "push")
chk("警報去重鍵忽略數字", P.alert_key("⚠️ [2026-10-29] 只評分到 3/100 檔") == P.alert_key("⚠️ [2026-10-30] 只評分到 5/120 檔"))
chk("警報去重鍵不同內容不同", P.alert_key("⚠️ A 失敗") != P.alert_key("⚠️ B 失敗"))
sys.exit(0 if ok else 1)
