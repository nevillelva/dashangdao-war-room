"""【2026-11-15】Telegram 影子記錄：每則訊息記「分類、原因、前 80 字」到私有表 ui_selftest_reports（summary=tg_shadow:日期）。
目的：現行只有被降噪的訊息（tg_digest）有留底，「已送出」的沒有，無法事後回放驗證分流規則有沒有誤殺。
不含機敏：存入前遮掉 token／金鑰／帶查詢字串的網址／長英數字串，並截成 80 字；只記，不影響推播（任何失敗都吞掉）。
"""
import re
from datetime import datetime, timedelta, timezone

TAIPEI_TZ = timezone(timedelta(hours=8))
MAX_CHARS = 80

_PATTERNS = (
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-~+/=]+"), "Bearer ***"),   # 先遮 Bearer，避免下一條只吃掉 "Bearer" 字樣、漏掉後面的值
    (re.compile(r"(?i)((?:token|apikey|api_key|secret|key|password|passwd|authorization)\s*[=:：]\s*)[^\s&,;'\"]+"), r"\1***"),
    (re.compile(r"\b\d{6,}:[A-Za-z0-9_\-]{20,}\b"), "***"),               # Telegram bot token
    (re.compile(r"https?://[^\s]*\?[^\s]*"), "[網址已省略]"),                # 帶查詢字串的網址
    (re.compile(r"(?<![A-Za-z0-9])[A-Za-z0-9_\-]{28,}(?![A-Za-z0-9])"), "***"),   # 長英數字串（金鑰樣貌）
    (re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-.]+"), "***"),     # JWT
)


def scrub(text, limit=MAX_CHARS):
    """遮機敏樣貌後取前 limit 字（換行壓成空白）。先遮再截，避免截斷後留下半截金鑰。"""
    t = str(text or "")
    for pat, rep in _PATTERNS:
        t = pat.sub(rep, t)
    t = re.sub(r"\s+", " ", t).strip()
    return t[:limit]


def make_row(msg, tier, reason, route, now=None):
    """route：sent（分流後推播）／digest（降噪不推）／dedup（同內容今天已推過）／direct（不經分流直送）／policy_error。"""
    now = now or datetime.now(TAIPEI_TZ)
    return {"t": now.astimezone(TAIPEI_TZ).strftime("%H:%M"), "tier": str(tier), "why": scrub(reason, 60),
            "route": str(route), "m": scrub(msg)}


def record(sb, msg, tier, reason, route, run_id="", now=None):
    """寫一筆影子記錄；失敗回 False，不拋例外、不影響推播。"""
    if sb is None:
        return False
    try:
        now = now or datetime.now(TAIPEI_TZ)
        row = make_row(msg, tier, reason, route, now)
        sb.table("ui_selftest_reports").insert({"run_id": run_id, "summary": f"tg_shadow:{now.astimezone(TAIPEI_TZ).strftime('%Y-%m-%d')}",
                                                "report": row}).execute()
        return True
    except Exception:  # noqa: BLE001
        return False
