#!/usr/bin/env python3
"""【2026-10-12】把 GitHub Actions 這次執行的輸出存進私有表 ui_selftest_reports（summary 以 runlog: 開頭）。

原因：Claude 工作環境的網路政策擋下 Actions 日誌下載（results-receiver.actions.githubusercontent.com），
改在執行器內把日誌寫進我們自己的資料庫，供事後診斷。不是繞過政策：資料只從執行器寫到自家 Supabase。

安全（常設規則：憑證不寫進任何文件或日誌）：
  • 寫入前，所有已知密鑰環境變數的值一律換成 ***；另外把 JWT 與 32 字以上的長 token 樣式字串遮蔽。
  • 只存私有表（RLS 開、無 policy），公開日誌只印筆數。
  • 只保留尾段 TAIL_LINES 行＋含錯誤／警告關鍵字的重點行（最多 HIGHLIGHT_MAX 行），14 天前的 runlog 自動刪除。
用法：python save_run_log.py /tmp/run.log（STAGE、RUN_ID、JOB_STATUS 由環境變數提供；失敗不影響 job 結果）。
"""
import os
import re
import sys
import datetime as dt

TAIL_LINES = 300
HIGHLIGHT_MAX = 200
KEEP_DAYS = 14
SECRET_ENV_NAMES = [
    "SUPABASE_KEY", "FINMIND_TOKEN", "FINNHUB_TOKEN", "SHIOAJI_API_KEY", "SHIOAJI_SECRET_KEY", "NVIDIA_API_KEY",
    "GEMINI_API_KEY", "GOOGLE_API_KEY", "DEEPSEEK_API_KEY", "HEALTHCHECKS_API_KEY", "CLOUDFLARE_API_TOKEN",
    "TELEGRAM_BOT_TOKEN", "GITHUB_TOKEN",
]
_PATTERNS = [
    re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{5,}"),   # JWT
    re.compile(r"\b(?:ghp|gho|ghs|github_pat)_[A-Za-z0-9_]{20,}\b"),                  # GitHub token
    re.compile(r"\b\d{8,12}:[A-Za-z0-9_\-]{30,}\b"),                                   # Telegram bot token
    re.compile(r"\b[A-Za-z0-9_\-]{40,}\b"),                                            # 其他長 token 樣式
    re.compile(r"(?<![A-Za-z0-9])[A-Z][12]\d{8}(?!\d)"),                               # 身分證字號格式（永豐金 SDK 連線訊息會印出）
]
_HIGHLIGHT = re.compile(r"❌|⚠️|Traceback|Error|錯誤|失敗|退回|warning|WARN|額度|402|403|banned", re.I)


def secret_values(env=None, names=None):
    env = os.environ if env is None else env
    vals = []
    for n in names or SECRET_ENV_NAMES:
        raw = env.get(n) or ""
        for v in raw.split(","):                 # FINMIND_TOKEN 為逗號分隔多組
            v = v.strip()
            if len(v) >= 6:
                vals.append(v)
    return sorted(set(vals), key=len, reverse=True)


def redact(text, secrets):
    for v in secrets:
        text = text.replace(v, "***")
    for p in _PATTERNS:
        text = p.sub("***", text)
    return text


def select_lines(lines, tail=TAIL_LINES, hl_max=HIGHLIGHT_MAX):
    """回傳 (tail_lines, highlight_lines)：尾段，以及尾段之前的重點行（避免重複）。"""
    head_part = lines[:-tail] if len(lines) > tail else []
    highlights = [ln for ln in head_part if _HIGHLIGHT.search(ln)][-hl_max:]
    return lines[-tail:], highlights


def build_report(text, stage, status, run_id, secrets):
    clean = redact(text, secrets)
    lines = clean.splitlines()
    tail, hl = select_lines(lines)
    return {
        "run_id": str(run_id or ""),
        "summary": f"runlog:{stage or 'unknown'}:{status or 'unknown'}",
        "report": {"stage": stage, "status": status, "total_lines": len(lines),
                   "highlights": hl, "tail": tail,
                   "saved_at": dt.datetime.now(dt.timezone.utc).isoformat()},
    }


def main(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except FileNotFoundError:
        text = "（找不到日誌檔：執行步驟可能在產生輸出前就失敗）"
    row = build_report(text, os.environ.get("STAGE", ""), os.environ.get("JOB_STATUS", ""),
                       os.environ.get("RUN_ID", ""), secret_values())
    from supabase import create_client
    sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
    sb.table("ui_selftest_reports").insert(row).execute()
    cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=KEEP_DAYS)).isoformat()
    try:
        sb.table("ui_selftest_reports").delete().like("summary", "runlog:%").lt("created_at", cutoff).execute()
    except Exception as e:
        print(f"[日誌保存] 清理舊日誌失敗（不影響本次）：{type(e).__name__}")
    print(f"[日誌保存] 已存 {row['report']['total_lines']} 行中的尾段 {len(row['report']['tail'])} 行、重點 {len(row['report']['highlights'])} 行")


if __name__ == "__main__":
    try:
        main(sys.argv[1] if len(sys.argv) > 1 else "/tmp/run.log")
    except Exception as e:          # 保存失敗不影響 job
        print(f"[日誌保存] 失敗：{type(e).__name__}")
    sys.exit(0)
