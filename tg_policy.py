"""【2026-10-29 老闆：Telegram 太多看門狗／系統訊息，要以主戰區買進與早上新聞為主，其餘依標準分流】
Telegram 推播分流標準（純函式，不連網）。三種結果：
  push   ：馬上推（交易動作與要處理的事）。
  alert  ：異常警報——推，但同一則內容同一天只推一次（避免洗版）。
  digest ：不推，只記到私有表 tg_digest:日期（要查再看；公開日誌只印數量）。
判斷順序（先到先得）：①明確降噪 → digest ②交易關鍵字 → push ③資訊／成功／回測／校準類 → digest
④異常開頭（⚠️❌🔴🚨）→ alert ⑤其餘一律 push（寧可多一則，也不漏掉交易訊息）。
"""
import hashlib

# ① 明確降噪：不管開頭是什麼都不推
DIGEST_FIRST = (
    "非交易日", "沒有符合條件的做空標的", "候選池尚未就緒", "已主動補打", "偵測到", "自我修復成功", "三關快照已連續",
    "券商分點抓取停擺", "券商分點歷史回補", "千張大戶", "ETF 54C", "ETF清單異動", "股本快取", "金鑰探測",
    "門檻敏感度", "濾網回測", "回測規則夜間作業", "自結財報公告",
)
# ② 交易關鍵字：一定推
PUSH_KEYS = (
    "尾盤定版戰卡", "預備戰卡", "尾盤戰卡", "開盤確認", "早盤情報", "早盤補充", "時間停損出場", "衝高出場", "強制出場",
    "處置股", "隔日沖", "總經閘門", "進場", "出場", "停損", "買進", "做空", "回補",
)
# ③ 資訊／成功／例行類：不推
DIGEST_PREFIX = ("ℹ️", "✅", "📋", "🎯", "🔑", "📦", "📢")
DIGEST_KEYS = ("回測", "校準", "資料庫容量報告", "探測")
# ④ 異常開頭：推但去重
ALERT_PREFIX = ("⚠️", "❌", "🔴", "🚨", "🩺")


def classify(msg):
    """回傳 (tier, reason)。tier ∈ push／alert／digest。"""
    t = str(msg or "").strip()
    head = t[:200]
    for k in DIGEST_FIRST:
        if k in head:
            return "digest", f"降噪：{k}"
    for k in PUSH_KEYS:
        if k in head:
            return "push", f"交易關鍵字：{k}"
    if t.startswith(DIGEST_PREFIX):
        return "digest", "資訊／成功／例行"
    for k in DIGEST_KEYS:
        if k in head:
            return "digest", f"回測／校準／例行：{k}"
    if t.startswith(ALERT_PREFIX):
        return "alert", "異常警報（同內容當天只推一次）"
    return "push", "其餘預設推播"


def alert_key(msg):
    """去重用雜湊：拿掉數字與日期，讓同一種警報不因數字變動而重複推。"""
    import re
    base = re.sub(r"[\d\-:／/.]+", "#", str(msg or ""))[:160]
    return hashlib.sha1(base.encode("utf-8")).hexdigest()[:16]
