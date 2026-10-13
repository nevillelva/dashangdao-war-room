"""【2026-10-13】Telegram 收件匣消費端（第 9-1 項）。

Worker 把使用者傳到機器人的文字存進 telegram_inbox（status='new'）。
本模組負責：讀取待處理 → 合併連續多則（同一批表格分段傳來）→ 用 paste_feed.parse_pasted 驗證預覽
→ 管理員確認後才寫入 manual_feed_batches → 把收件匣列標成 done／rejected。

合併規則（先確認過不會悄悄切掉文字）：
  1. Worker 不截斷：超過 TG_MAX_TEXT 直接回覆「請分段」並不入庫；paste_feed 超過上限也是報錯不截斷。
  2. 表格列若被切在兩則之間，切出來的片段欄位數與表頭不符，paste_feed 會報錯、整批不寫入（安全失敗）。
  3. 第二則若重貼表頭，會去掉與第一則相同的表頭列（否則表頭會被當成資料列，且欄位數相同、不會報錯）。
  4. 只合併「彼此相隔不超過 MAX_GAP_S 秒」的連續訊息，避免把不相干的訊息湊在一起。

規則：驗證不過整批不寫入；同內容 hash 不重複寫；狀態只從 'new' 轉出（避免重複處理）。
不連網、不抓任何網站，Supabase 用戶端由呼叫端傳入（方便離線測試）。
"""
from datetime import date, datetime

from paste_feed import parse_pasted

NOTE_MAX = 200
MAX_GAP_S = 120  # 連續兩則訊息的最大間隔（秒）；超過就視為不同批


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


def _ts(v):
    """把 Supabase 的時間字串轉成 datetime；讀不到回傳 None。"""
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None


def group_rows(rows, max_gap_s=MAX_GAP_S):
    """把待處理訊息依 id 排序後，相鄰且時間間隔 ≤ max_gap_s 的分成同一組。
    時間讀不到的訊息一律單獨成組（不冒險合併）。"""
    groups = []
    for r in sorted(rows, key=lambda x: x["id"]):
        if groups:
            prev = groups[-1][-1]
            t_prev, t_cur = _ts(prev.get("created_at")), _ts(r.get("created_at"))
            if t_prev and t_cur and 0 <= (t_cur - t_prev).total_seconds() <= max_gap_s:
                groups[-1].append(r)
                continue
        groups.append([r])
    return groups


def merge_text(rows):
    """合併同一組訊息的文字：去除第二則起與第一則相同的表頭列。"""
    header = None
    lines = []
    for r in rows:
        ls = [ln for ln in (r.get("text") or "").splitlines() if ln.strip()]
        if not ls:
            continue
        if header is None:
            header = ls[0].strip()
            lines.extend(ls)
        else:
            if ls[0].strip() == header:
                ls = ls[1:]
            lines.extend(ls)
    return "\n".join(lines)


def preview_group(rows, today=None):
    """對一組訊息做合併後的解析驗證。"""
    return parse_pasted(merge_text(rows), today=today or date.today())


def preview(row, today=None):
    """對單一訊息做解析驗證（相容舊介面）。"""
    return preview_group([row], today=today)


def _mark_many(sb, ids, status, note):
    """把一組 id 從 new 轉成 status；只要有任一列不是 new 就整組還原，回傳是否全部成功。"""
    res = (sb.table("telegram_inbox")
           .update({"status": status, "note": (note or "")[:NOTE_MAX]})
           .in_("id", ids).eq("status", "new").execute().data) or []
    if len(res) == len(ids):
        return True
    if res and status != "new":
        # 只轉到一部分（有人同時處理）：把已轉的還原，避免半套狀態
        got = [r["id"] for r in res]
        (sb.table("telegram_inbox").update({"status": "new", "note": ""})
         .in_("id", got).eq("status", status).execute())
    return False


def reject_group(sb, rows, reason):
    """退回整組：不寫入批次，標記 rejected 並記原因。"""
    return _mark_many(sb, [r["id"] for r in rows], "rejected", reason or "管理員退回")


def reject(sb, inbox_id, reason):
    """退回單一訊息（相容舊介面）。"""
    return _mark_many(sb, [inbox_id], "rejected", reason or "管理員退回")


def commit_group(sb, rows, label=None, today=None):
    """重新驗證整組後寫入 manual_feed_batches。回傳 (狀態, 訊息)。
    狀態：'written'｜'duplicate'｜'invalid'｜'conflict'。"""
    ids = [r["id"] for r in rows]
    res = preview_group(rows, today)
    if not res["ok"]:
        return "invalid", res["message"]
    dup = (sb.table("manual_feed_batches").select("id")
           .eq("content_hash", res["content_hash"]).limit(1).execute().data) or []
    if dup:
        if _mark_many(sb, ids, "done", f"內容已存過（批次 #{dup[0]['id']}），未重複寫入"):
            return "duplicate", f"內容已存過（批次 #{dup[0]['id']}），未重複寫入"
        return "conflict", "這批訊息已被處理過"
    # 先佔位：整組從 new 轉成 parsed，才有資格寫入，避免兩人同時按確認而重複寫
    if not _mark_many(sb, ids, "parsed", "寫入中"):
        return "conflict", "這批訊息已被處理過"
    first = ids[0]
    try:
        sb.table("manual_feed_batches").insert({
            "source_label": (label or (f"Telegram #{first}" if len(ids) == 1
                                       else f"Telegram #{first}–#{ids[-1]}（{len(ids)} 則合併）"))[:60],
            "content_hash": res["content_hash"],
            "row_count": len(res["rows"]),
            "header": res["header"],
            "rows": res["rows"],
            "raw_text": merge_text(rows)[:200000],
        }).execute()
    except Exception as e:  # noqa: BLE001
        # 寫入失敗：整組還原為 new，讓人可以重試，並留下原因
        (sb.table("telegram_inbox").update({"status": "new", "note": f"寫入失敗：{type(e).__name__}"})
         .in_("id", ids).eq("status", "parsed").execute())
        return "invalid", f"寫入失敗（{type(e).__name__}），訊息已保留可重試"
    (sb.table("telegram_inbox").update({"status": "done", "note": f"已寫入 {len(res['rows'])} 列"})
     .in_("id", ids).eq("status", "parsed").execute())
    return "written", f"已寫入 {len(res['rows'])} 列"


def commit(sb, row, label=None, today=None):
    """單一訊息寫入（相容舊介面）。"""
    return commit_group(sb, [row], label=label, today=today)
