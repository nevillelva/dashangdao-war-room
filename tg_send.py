"""
Telegram 統一推播（2026-10-07）——取代過去 4 份各自為政的實作。

為什麼要統一：
  1. Telegram 單則訊息上限 4096 字，超過會被整則拒收（HTTP 400）——長報表（夜間掃描、勝率總覽）之前就是這樣「收不到」。
  2. 舊實作失敗時多半只 print，沒有重試也沒有留紀錄。

本模組只做三件事（純函式 + requests，不依賴 Streamlit/Supabase）：
  - split_message()：依「段落 → 行 → 硬切」把長文切成每則 ≤ limit 字，多則時加 (1/3) 標記。
  - send_telegram()：逐則發送，遇 429 依 retry_after 等待、其他失敗重試，回傳 (全部成功?, 明細)。
  - 不做任何格式轉換（純文字模式，避免 <>& 造成 400）。
"""
import os
import time

import requests

# Telegram 上限 4096；留邊界給 "(1/3)\n" 前綴與 UTF-16 計算差異
TG_SAFE_LIMIT = 3800


def split_message(text, limit=TG_SAFE_LIMIT):
    """把 text 切成多則，每則（含 (i/n) 前綴）長度 ≤ limit。

    切法優先序：空行（段落）→ 換行 → 句號/空白 → 硬切。單則時原文不加任何標記。
    """
    text = "" if text is None else str(text)
    if len(text) <= limit:
        return [text]
    body_limit = limit - 12  # 預留 "(10/10)\n" 前綴
    pieces = []
    rest = text
    while len(rest) > body_limit:
        window = rest[:body_limit]
        cut = window.rfind("\n\n")
        if cut < body_limit * 0.4:
            cut = window.rfind("\n")
        if cut < body_limit * 0.4:
            cut = max(window.rfind("。"), window.rfind("；"), window.rfind(" "))
            cut = cut + 1 if cut >= body_limit * 0.4 else -1
        if cut <= 0:
            cut = body_limit
        pieces.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip("\n")
    if rest.strip():
        pieces.append(rest.rstrip())
    n = len(pieces)
    if n == 1:
        return pieces
    return [f"({i + 1}/{n})\n{p}" for i, p in enumerate(pieces)]


def send_telegram(text, token=None, chat_id=None, retries=2, timeout=10,
                  session=None, sleep=time.sleep, limit=TG_SAFE_LIMIT):
    """送出（可能多則）訊息。回傳 (ok, details)；details 是每則的 {"part","ok","status","error"}。

    token/chat_id 省略時讀環境變數 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID；沒設定回 (False, [])。
    ok=True 代表「每一則都成功」。失敗不拋例外。
    """
    token = token or os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        return False, []
    http = session or requests
    parts = split_message(text, limit=limit)
    details = []
    all_ok = True
    for idx, part in enumerate(parts, 1):
        rec = {"part": idx, "ok": False, "status": None, "error": None}
        for attempt in range(retries + 1):
            try:
                resp = http.post(f"https://api.telegram.org/bot{token}/sendMessage",
                                 json={"chat_id": chat_id, "text": part}, timeout=timeout)
                rec["status"] = resp.status_code
                if resp.status_code == 200:
                    rec["ok"] = True
                    rec["error"] = None
                    break
                body = ""
                try:
                    body = resp.text[:200]
                except Exception:
                    pass
                rec["error"] = f"HTTP {resp.status_code} {body}"
                if resp.status_code == 429:
                    wait = 3
                    try:
                        wait = int(resp.json().get("parameters", {}).get("retry_after", 3))
                    except Exception:
                        pass
                    sleep(min(max(wait, 1), 30))
                elif resp.status_code in (400, 401, 403, 404):
                    break  # 內容/權限問題，重試無用
                else:
                    sleep(1.5 * (attempt + 1))
            except Exception as e:  # noqa: BLE001
                rec["error"] = f"{type(e).__name__}: {e}"[:200]
                sleep(1.5 * (attempt + 1))
        details.append(rec)
        all_ok = all_ok and rec["ok"]
        if idx < len(parts):
            sleep(0.4)  # 避免同一聊天室 1 則/秒的限制
    return all_ok, details
