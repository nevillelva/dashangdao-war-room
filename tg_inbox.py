"""【2026-10-13】Telegram 收件匣消費端（第 9-1 項）。

Worker 把使用者傳到機器人的文字存進 telegram_inbox（status='new'）。
本模組負責：讀取待處理 → 合併連續多則（同一批表格分段傳來）→ 用 paste_feed.parse_pasted 驗證預覽
→ 管理員確認後才寫入 manual_feed_batches → 把收件匣列標成 done／rejected。

合併規則（先確認過不會悄悄切掉文字）：
  1. Worker 不截斷：上限已提高到 Telegram 單則上限 4096 字（平台本身送不出更長的），不再有「可送達卻被拒」；paste_feed 超過上限也是報錯不截斷。
  2. 表格列若被切在兩則之間，切出來的片段欄位數與表頭不符，paste_feed 會報錯、整批不寫入（安全失敗）。
  3. 第二則若重貼表頭，會去掉與第一則相同的表頭列（否則表頭會被當成資料列，且欄位數相同、不會報錯）。
  4. 只合併「彼此相隔不超過 MAX_GAP_S 秒」的連續訊息，避免把不相干的訊息湊在一起。

【2026-10-13 圖片收件】Worker 也會收圖片（kind='photo'，位元組存 telegram_inbox_images）。
圖片列一開始 text 是空的：管理員在頁面自選辨識引擎（視覺模型或 OCR，見 image_intake），辨識結果寫回 text 後，
就和文字訊息一樣走「合併 → 驗證預覽 → 確認寫入」；辨識結果可先人工修改。寫入或退回後立刻刪除圖片位元組。

【2026-10-13 AI 斷行修復】表格列被 Telegram 切在兩則之間時，AI 只負責「判斷哪幾行要接回上一行」，輸出一小段 JSON
（行號清單，字數受 max_tokens 與行號上限約束）；接回由程式照原文逐字串接，AI 碰不到任何資料字元，所以不可能改數字。
接回後仍須通過 paste_feed 驗證、並且管理員在預覽按確認才寫入（見 ai_repair／apply_join_plan）。

規則：驗證不過整批不寫入；同內容 hash 不重複寫；狀態只從 'new' 轉出（避免重複處理）。
不連網、不抓任何網站，Supabase 用戶端由呼叫端傳入（方便離線測試）。
"""
import json
import re
from datetime import date, datetime

import base64

from paste_feed import parse_pasted

NOTE_MAX = 200
MAX_GAP_S = 120  # 連續兩則訊息的最大間隔（秒）；超過就視為不同批


def fetch_new(sb, limit=30):
    """讀取待處理訊息（舊的在前，依序處理）。"""
    rows = (sb.table("telegram_inbox")
            .select("id,created_at,update_id,chat_id,message_id,text,status,note,kind,file_id,caption,engine")
            .eq("status", "new").order("id").limit(limit).execute().data) or []
    return rows


def recent_done(sb, limit=15):
    """最近處理過的訊息（done／rejected），供對帳。"""
    return (sb.table("telegram_inbox")
            .select("id,created_at,status,note")
            .in_("status", ["done", "rejected"])
            .order("id", desc=True).limit(limit).execute().data) or []


def is_pending_photo(row):
    """圖片列且還沒辨識（text 為空）：需要先選引擎辨識，不能直接合併或寫入。"""
    return (row.get("kind") == "photo") and not (row.get("text") or "").strip()


def split_pending(rows):
    """把待處理列分成 (尚未辨識的圖片, 其餘)。其餘（文字訊息＋已辨識的圖片）才進入合併與預覽。"""
    photos = [r for r in rows if is_pending_photo(r)]
    others = [r for r in rows if not is_pending_photo(r)]
    return photos, others


def fetch_image(sb, inbox_id):
    """讀取圖片位元組。回傳 (bytes, mime)；沒有（已刪除或下載失敗）回傳 None。"""
    rows = (sb.table("telegram_inbox_images").select("mime,data_b64").eq("inbox_id", inbox_id)
            .limit(1).execute().data) or []
    if not rows:
        return None
    try:
        return base64.b64decode(rows[0]["data_b64"]), rows[0]["mime"]
    except (ValueError, KeyError, TypeError):
        return None


def purge_images(sb, ids):
    """刪除圖片位元組（寫入或退回後不長期保留）。失敗不影響主流程，回傳是否成功。"""
    try:
        sb.table("telegram_inbox_images").delete().in_("inbox_id", list(ids)).execute()
        return True
    except Exception:  # noqa: BLE001
        return False


def extract_photo(sb, row, engine, extractor=None):
    """對一張尚未寫入的圖片辨識，結果寫回 telegram_inbox.text（只改 status='new' 的列）。
    extractor 預設為 image_intake.extract（測試可注入假實作）。回傳 {ok, text, warnings, error}。"""
    if extractor is None:
        from image_intake import extract as extractor  # noqa: PLC0415
    img = fetch_image(sb, row["id"])
    if img is None:
        return {"ok": False, "text": "", "warnings": [], "error": "找不到圖片內容（可能已處理或下載失敗），請重傳"}
    data, mime = img
    res = extractor(engine, data, mime)
    if not res.get("ok"):
        return res
    note = f"辨識:{engine}"
    if res.get("warnings"):
        note += "｜" + "；".join(res["warnings"])
    upd = (sb.table("telegram_inbox").update({"text": res["text"], "engine": engine, "note": note[:NOTE_MAX]})
           .eq("id", row["id"]).eq("status", "new").execute().data) or []
    if not upd:
        return {"ok": False, "text": "", "warnings": [], "error": "這則已被處理過，辨識結果未寫入"}
    return res


def save_text(sb, inbox_id, text):
    """人工修改辨識結果（只改 status='new' 的列）。回傳是否成功。"""
    t = (text or "").strip()
    if not t:
        return False
    upd = (sb.table("telegram_inbox").update({"text": t}).eq("id", inbox_id).eq("status", "new").execute().data) or []
    return bool(upd)


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
    ids = [r["id"] for r in rows]
    ok = _mark_many(sb, ids, "rejected", reason or "管理員退回")
    if ok:
        purge_images(sb, ids)
    return ok


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
            purge_images(sb, ids)
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
    purge_images(sb, ids)
    return "written", f"已寫入 {len(res['rows'])} 列"


def commit(sb, row, label=None, today=None):
    """單一訊息寫入（相容舊介面）。"""
    return commit_group(sb, [row], label=label, today=today)


# ------------------------------------------------------------------ AI 斷行修復（2026-10-13）
REPAIR_MAX_LINES = 400      # 一次最多讓 AI 判斷的行數（超過就不處理，不截斷）
REPAIR_LINE_SHOW = 160      # 給 AI 看的每行字數上限（只影響提示詞，不影響資料本身）
REPAIR_MAX_JOINS = 100      # 一次最多接回幾處（AI 回覆受此約束，避免亂接）
REPAIR_MAX_TOKENS = 400     # AI 回覆字數上限：只回一小段 JSON

REPAIR_SYSTEM = (
    "你是表格斷行判斷員。使用者把一張以 Tab 或逗號分隔的表格貼到聊天軟體，因為單則字數上限，"
    "有些資料列被從中間切斷成兩行。你只判斷：哪些行其實是『上一行的後半段』。"
    "你不能改寫、補字、刪字，只能回報行號。只輸出一行 JSON，格式 {\"join_with_previous\": [行號, ...]}；"
    "沒有需要接回的就輸出 {\"join_with_previous\": []}。不要任何說明文字。"
)


def _delim_of(line):
    return "\t" if "\t" in line else ("," if "," in line else None)


def repair_lines(rows):
    """合併後的非空行（已去除第二則起重複的表頭）。"""
    return [ln for ln in merge_text(rows).splitlines() if ln.strip()]


def build_repair_prompt(lines):
    """行號從 1 起算；附上每行的欄位數，讓 AI 看得出哪些行欄位不足（表頭欄位數＝完整一列的欄位數）。"""
    d = _delim_of(lines[0]) if lines else None
    ncols = (lines[0].count(d) + 1) if d else 0
    out = [f"表頭有 {ncols} 欄（第 1 行）。逐行如下（行號｜欄數｜內容）："]
    for i, ln in enumerate(lines, 1):
        c = (ln.count(d) + 1) if d else 1
        show = ln if len(ln) <= REPAIR_LINE_SHOW else ln[:REPAIR_LINE_SHOW] + "…"
        out.append(f"{i}｜{c}｜{show.replace(chr(9), '⇥')}")
    out.append("請回報哪些行（第 2 行起）是上一行被切斷的後半段，只回 JSON。")
    return "\n".join(out)


def parse_join_plan(txt, n_lines):
    """從 AI 回覆取出行號清單。格式不對、行號不是 2..n_lines 的整數、重複、超過上限 → 回 None（整個計畫作廢）。"""
    m = re.search(r"\{.*\}", txt or "", re.S)
    if not m:
        return None
    try:
        v = json.loads(m.group(0)).get("join_with_previous")
    except (ValueError, AttributeError):
        return None
    if not isinstance(v, list) or len(v) > REPAIR_MAX_JOINS:
        return None
    if any((not isinstance(x, int)) or isinstance(x, bool) or x < 2 or x > n_lines for x in v):
        return None
    if len(set(v)) != len(v):
        return None
    return sorted(v)


def apply_join_plan(lines, joins):
    """把 joins 裡的行逐字接到上一行後面（不加任何字元）。回傳新文字。"""
    js = set(joins)
    out = []
    for i, ln in enumerate(lines, 1):
        if i in js and out:
            out[-1] += ln
        else:
            out.append(ln)
    return "\n".join(out)


def ai_repair(rows, ai_call, today=None):
    """對一組驗證不過的訊息，請 AI 判斷斷行並接回。ai_call(system, user, validate) -> (ok, text)。
    回傳 {ok, text, joins, preview, error}；ok 只代表『接回後通過 paste_feed 驗證』，仍要管理員確認才寫入。
    不改任何字元：接回後的文字去掉換行必與原文去掉換行逐字相同（內部再檢查一次，防呆）。"""
    lines = repair_lines(rows)
    if len(lines) < 2:
        return {"ok": False, "text": "", "joins": [], "preview": None, "error": "行數太少，沒有可接回的行"}
    if len(lines) > REPAIR_MAX_LINES:
        return {"ok": False, "text": "", "joins": [], "preview": None,
                "error": f"共 {len(lines)} 行，超過 AI 一次判斷上限 {REPAIR_MAX_LINES} 行；請分批傳送（不會截斷）"}
    ok, txt = ai_call(REPAIR_SYSTEM, build_repair_prompt(lines), lambda t: parse_join_plan(t, len(lines)) is not None)
    if not ok:
        return {"ok": False, "text": "", "joins": [], "preview": None, "error": f"AI 沒有給出可用的判斷：{txt}"}
    joins = parse_join_plan(txt, len(lines))
    if joins is None:
        return {"ok": False, "text": "", "joins": [], "preview": None, "error": "AI 回覆格式不符"}
    if not joins:
        return {"ok": False, "text": "", "joins": [], "preview": None, "error": "AI 判斷沒有需要接回的行；請人工檢查內容"}
    new = apply_join_plan(lines, joins)
    if new.replace("\n", "") != "".join(lines):   # 防呆：只准接行，不准有任何字元差異
        return {"ok": False, "text": "", "joins": [], "preview": None, "error": "接回結果與原文不一致，已捨棄"}
    pv = parse_pasted(new, today=today or date.today())
    if not pv["ok"]:
        return {"ok": False, "text": new, "joins": joins, "preview": pv,
                "error": f"接回後仍無法通過驗證：{pv['message']}"}
    return {"ok": True, "text": new, "joins": joins, "preview": pv, "error": ""}


def apply_repair(sb, rows, text):
    """採用修復結果：把整組的文字換成修復後的單一文字（放在第一列；其餘列文字清空並保持 status=new，
    之後仍合併為同一組、經 commit_group 重新驗證）。只改 status='new' 的列；回傳是否全部成功。"""
    t = (text or "").strip()
    if not t:
        return False
    ids = [r["id"] for r in rows]
    first = sb.table("telegram_inbox").update({"text": t, "note": "AI斷行修復後（已人工確認預覽）"}).eq("id", ids[0]).eq("status", "new").execute().data or []
    if not first:
        return False
    for i in ids[1:]:
        sb.table("telegram_inbox").update({"text": ""}).eq("id", i).eq("status", "new").execute()
    return True
