"""【2026-10-29 老闆：Telegram 太多看門狗／系統訊息，要以主戰區買進與早上新聞為主，其餘依標準分流】
Telegram 推播分流標準（純函式，不連網）。三種結果：
  push   ：馬上推（交易動作與要處理的事）。
  alert  ：異常警報——推，但同一則內容同一天只推一次（避免洗版）。
  digest ：不推，只記到私有表 tg_digest:日期（要查再看；公開日誌只印數量）。
判斷順序（先到先得）：①明確降噪 → digest ②ℹ️ 資訊前綴＋交易字 → digest（只是偵測通知，無實際成交或下單）
③交易關鍵字 → push ④泛用降噪（偵測到，無交易字才降噪）→ digest
⑤資訊／成功／回測／校準類 → digest ⑥異常開頭（⚠️❌🔴🚨）→ alert ⑦其餘一律 push（寧可多一則，也不漏掉交易訊息）。
✅ 買進訊號等交易動作仍走 ③ 推播；只有 ℹ️ 前綴的資訊通知才會因交易字而降到 digest。
「偵測到」太泛用（看門狗、資料源、交易訊號都會用），所以只在沒有交易關鍵字時才降噪；
「非交易日」「沒有符合條件的做空標的」等明確降噪詞仍優先於交易關鍵字。
"""
import hashlib
import re

# ① 明確降噪：不管開頭是什麼都不推（即使內容含交易字眼，例如「非交易日，今日不進場」）
DIGEST_FIRST = (
    "非交易日", "沒有符合條件的做空標的", "候選池尚未就緒", "已主動補打", "自我修復成功", "三關快照已連續",
    "券商分點抓取停擺", "券商分點歷史回補", "千張大戶", "ETF 54C", "ETF清單異動", "股本快取", "金鑰探測",
    "門檻敏感度", "濾網回測", "自結財報公告",
)
# ①' 泛用降噪：只有在沒有交易關鍵字時才降噪（「偵測到 2330 跌破停損」必須推）
DIGEST_WEAK = ("偵測到",)
# ② 交易關鍵字：一定推
PUSH_KEYS = (
    "尾盤定版戰卡", "預備戰卡", "尾盤戰卡", "開盤確認", "早盤情報", "早盤補充", "時間停損出場", "衝高出場", "強制出場",
    "處置股", "隔日沖", "總經閘門", "進場", "出場", "停損", "買進", "做空", "回補",
    "回測規則夜間作業", "新掛單", "成交", "穿山惡龍",   # 模擬倉夜間報告（掛單、成交、持倉）是交易訊息，不是回測完成通知
)
# ③ 資訊／成功／例行類：不推
INFO_PREFIX = "ℹ️"
DIGEST_PREFIX = (INFO_PREFIX, "✅", "📋", "🎯", "🔑", "📦", "📢")
DIGEST_KEYS = ("回測", "校準", "資料庫容量報告", "探測")
# ④ 異常開頭：推但去重
ALERT_PREFIX = ("⚠️", "❌", "🔴", "🚨", "🩺")

# 去重用切詞：日期整段遮罩；4–6 位獨立數字（股票代號）保留；其餘數字與符號遮罩
_ALERT_TOKEN = re.compile(
    r"(\d{4}\s*[\-／/.年]\s*\d{1,2}\s*[\-／/.月]\s*\d{1,2}\s*日?)"   # 日期：2026-10-29／2026/10/29／2026年10月29日
    r"|((?<!\d)\d{4,6}(?!\d))"                                      # 股票代號（4–6 位）
    r"|[\d\-:／/.]+"                                                 # 其餘數字與符號
)


def classify(msg):
    """回傳 (tier, reason)。tier ∈ push／alert／digest。"""
    t = str(msg or "").strip()
    head = t[:200]
    for k in DIGEST_FIRST:
        if k in head:
            return "digest", f"降噪：{k}"
    # ℹ️ 資訊通知（如「ℹ️ 偵測到 2330 停損價」）只是偵測提示，不是成交或下單指令 → digest
    if t.startswith(INFO_PREFIX):
        for k in PUSH_KEYS:
            if k in head:
                return "digest", f"資訊通知（ℹ️ 偵測提示，非成交或下單）：{k}"
    for k in PUSH_KEYS:
        if k in head:
            return "push", f"交易關鍵字：{k}"
    for k in DIGEST_WEAK:
        if k in head:
            return "digest", f"泛用降噪（無交易關鍵字）：{k}"
    if t.startswith(DIGEST_PREFIX):
        return "digest", "資訊／成功／例行"
    for k in DIGEST_KEYS:
        if k in head:
            return "digest", f"回測／校準／例行：{k}"
    if t.startswith(ALERT_PREFIX):
        return "alert", "異常警報（同內容當天只推一次）"
    return "push", "其餘預設推播"


def alert_key(msg):
    """去重用雜湊：拿掉日期與數字，但保留 4–6 位股票代號。
    同種警報、同一檔才視為重複：「⚠️ 2330 報價中斷」與「⚠️ 2454 報價中斷」鍵不同；同代號不同日期則同鍵。"""
    def _keep(m):
        if m.group(2):          # 股票代號 → 保留
            return m.group(2)
        return "#"              # 日期、其餘數字與符號 → 遮罩
    base = _ALERT_TOKEN.sub(_keep, str(msg or ""))[:160]
    return hashlib.sha1(base.encode("utf-8")).hexdigest()[:16]
