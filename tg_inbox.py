"""【2026-10-13】Telegram 收件匣消費端（第 9-1 項）。

Worker 把使用者傳到機器人的文字存進 telegram_inbox（status='new'）。
本模組負責：讀取待處理 → 用 paste_feed.parse_pasted 驗證預覽 → 管理員確認後才寫入
manual_feed_batches → 把收件匣列標成 done／rejected。
規則：驗證不過整份不寫入；同內容 hash 不重複寫；狀態只從 'new' 轉出（避免重複處理）。
不連網、不抓任何網站，Supabase 用戶端由呼叫端傳入（方便離線測試）。"""
from datetime import date

from paste_feed import parse_pasted

NOTE_MAX = 200


def fetch_new(sb, limit=30):
    """讀取待處理訊息（舊的在前，依序處理）。"""
    rows = (sb.table("telegram_inbox")
            .select("id,created_at,update_id,chat_id,message_id,text,status,note")
            .eq("status", "new").order("id").limit(limit).execute().data) or []
    return rows


def recent_done(sb, limit=15):
    """最近處理過的訊息（done／rejected），供對帳。"""
    return (sb.table("telegram_inbox")
            .select("id,created_at,status,note")
            .in_("status", ["done", "rejected"])
            .order("id", desc=True).limit(limit).execute().data) or []


def preview(row, today=None):
    """對一則收件匣訊息做解析驗證，回傳 parse_pasted 的結果。"""
    return parse_pasted((row or {}).get("text") or "", today=today or date.today())


def _mark(sb, inbox_id, status, note):
    """只允許從 new 轉出；回傳是否真的更新到一列。"""
    res = (sb.table("telegram_inbox")
           .update({"status": status, "note": (note or "")[:NOTE_MAX]})
           .eq("id", inbox_id).eq("status", "new").execute().data) or []
    return len(res) > 0


def reject(sb, inbox_id, reason):
    """退回：不寫入批次，只標記 rejected 並記原因。"""
    return _mark(sb, inbox_id, "rejected", reason or "管理員退回")


def commit(sb, row, label=None, today=None):
    """重新驗證後寫入 manual_feed_batches。回傳 (狀態, 訊息)。
    狀態：'written'｜'duplicate'｜'invalid'｜'conflict'。"""
    res = preview(row, today)
    inbox_id = row["id"]
    if not res["ok"]:
        return "invalid", res["message"]
    dup = (sb.table("manual_feed_batches").select("id")
           .eq("content_hash", res["content_hash"]).limit(1).execute().data) or []
    if dup:
        if _mark(sb, inbox_id, "done", f"內容已存過（批次 #{dup[0]['id']}），未重複寫入"):
            return "duplicate", f"內容已存過（批次 #{dup[0]['id']}），未重複寫入"
        return "conflict", "這則訊息已被處理過"
    # 先佔位：把狀態從 new 轉出成功的人才有資格寫入，避免兩人同時按確認而重複寫
    if not _mark(sb, inbox_id, "parsed", "寫入中"):
        return "conflict", "這則訊息已被處理過"
    try:
        sb.table("manual_feed_batches").insert({
            "source_label": (label or f"Telegram #{inbox_id}")[:60],
            "content_hash": res["content_hash"],
            "row_count": len(res["rows"]),
            "header": res["header"],
            "rows": res["rows"],
            "raw_text": (row.get("text") or "")[:200000],
        }).execute()
    except Exception as e:  # noqa: BLE001
        # 寫入失敗：還原成 new，讓人可以重試，並留下原因
        (sb.table("telegram_inbox").update({"status": "new", "note": f"寫入失敗：{type(e).__name__}"})
         .eq("id", inbox_id).eq("status", "parsed").execute())
        return "invalid", f"寫入失敗（{type(e).__name__}），訊息已保留可重試"
    (sb.table("telegram_inbox").update({"status": "done", "note": f"已寫入 {len(res['rows'])} 列"})
     .eq("id", inbox_id).eq("status", "parsed").execute())
    return "written", f"已寫入 {len(res['rows'])} 列"
