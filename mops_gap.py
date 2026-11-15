"""【2026-11-15 盤中重訊缺口】戰卡／AI 戰略推演的重大訊息資料時效與新聞轉述提醒（純函式為主，資料庫由呼叫端傳入）。

問題：證交所重大訊息 OpenAPI 檔是滾動快照、盤中不即時；戰卡過去在「查無重訊」時沒有說明資料停在哪個時間，
使用者會把「檔案還沒更新」誤讀成「沒有重訊」。本模組提供三件事：
  ① 資料時效：以官方檔產生時間（HTTP Last-Modified）為準；官方沒給時，退而用檔內最新發言時間，並明確標示來源；
     兩者都沒有就誠實顯示「時間不明」，絕不默默當成無重訊。
  ② 新聞轉述提醒：news_items 標題含「公告」「重訊」且提到候選／持倉者，只加提醒標示「新聞轉述，未經官方驗證」，
     不參與任何買賣判定、不餵進 AI 證據包。
  ③ 時效紀錄存取（system_config mops_asof:日期），供戰卡、推演與每日量測（mops_gap_measure）共用。
全部失敗皆回傳空值、不拋例外，不影響戰卡主流程。
"""
import json
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

TAIPEI_TZ = timezone(timedelta(hours=8))
MATERIAL_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap04_L"
RELAY_TAG = "新聞轉述，未經官方驗證"
RELAY_WORDS = ("公告", "重訊")
SRC_HEADER = "官方檔產生時間"
SRC_ROWS = "檔內最新發言時間"
ASOF_KEY = "mops_asof:{day}"


# ───────── ① 資料時效 ─────────
def parse_http_date(s):
    """HTTP Last-Modified／Date（RFC 7231）→ 台北時間 datetime；解析不了回 None。"""
    if not s:
        return None
    try:
        dt = parsedate_to_datetime(str(s))
    except Exception:  # noqa: BLE001
        return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(TAIPEI_TZ)


def head_last_modified(session, url=MATERIAL_URL, timeout=10):
    """對官方檔送一次 HEAD（不下載內容），回傳 Last-Modified 的台北時間；沒有或失敗回 None。"""
    try:
        r = session.head(url, timeout=timeout, allow_redirects=True)
        if getattr(r, "status_code", 0) != 200:
            return None
        h = getattr(r, "headers", {}) or {}
        return parse_http_date(h.get("Last-Modified") or h.get("last-modified"))
    except Exception:  # noqa: BLE001
        return None


def latest_speech(rows, roc_to_iso, fmt_time, get=None):
    """檔內最新「發言日期＋發言時間」→ 台北時間 datetime 或 None。roc_to_iso／fmt_time／get 由 premarket 傳入（避免循環依賴）。"""
    best = None
    g = get or (lambda r, k: r.get(k, ""))
    for r in rows or []:
        try:
            d = roc_to_iso(g(r, "發言日期"))
            t = fmt_time(g(r, "發言時間")) or "00:00:00"
            if not d:
                continue
            dt = datetime.strptime(f"{d} {t}", "%Y-%m-%d %H:%M:%S").replace(tzinfo=TAIPEI_TZ)
        except Exception:  # noqa: BLE001
            continue
        if best is None or dt > best:
            best = dt
    return best


def build_asof(header_dt=None, rows_dt=None, now=None):
    """合併兩個時間來源，回傳 dict 或 None。優先官方檔產生時間；沒有才退檔內最新發言時間（來源欄位會註明）。
    {"at": "YYYY-MM-DD HH:MM", "source": SRC_*, "checked_at": "YYYY-MM-DD HH:MM"}"""
    now = now or datetime.now(TAIPEI_TZ)
    pick, src = (header_dt, SRC_HEADER) if header_dt else ((rows_dt, SRC_ROWS) if rows_dt else (None, None))
    if pick is None:
        return None
    return {"at": pick.astimezone(TAIPEI_TZ).strftime("%Y-%m-%d %H:%M"), "source": src,
            "checked_at": now.astimezone(TAIPEI_TZ).strftime("%Y-%m-%d %H:%M")}


def asof_text(asof, today=None):
    """顯示文字。asof 為 None（官方沒給時間、也沒存過）→ 明確警示，不得當成無重訊。
    資料日不是今天時加上 MM/DD，避免把昨天的檔誤當今天。"""
    if not asof or not asof.get("at"):
        return "重訊資料截至時間不明（官方檔未提供產生時間；請勿視為沒有重訊）"
    day, hhmm = asof["at"][:10], asof["at"][11:16]
    pre = f"{day[5:7]}/{day[8:10]} " if (today and day != today) else ""
    note = "" if asof.get("source") == SRC_HEADER else f"（{asof.get('source')}）"
    return f"重訊資料截至 {pre}{hhmm}{note}；之後的官方重訊未納入，不等於沒有重訊"


def save_asof(sb, day, asof):
    """存 system_config mops_asof:日期；失敗回 False。"""
    if sb is None or not asof:
        return False
    try:
        sb.table("system_config").upsert({"config_key": ASOF_KEY.format(day=day),
                                          "config_value": json.dumps(asof, ensure_ascii=False)},
                                         on_conflict="config_key").execute()
        return True
    except Exception:  # noqa: BLE001
        return False


def load_asof(sb, day):
    if sb is None:
        return None
    try:
        rows = (sb.table("system_config").select("config_value").eq("config_key", ASOF_KEY.format(day=day))
                .limit(1).execute().data) or []
        if not rows:
            return None
        v = rows[0].get("config_value")
        v = json.loads(v) if isinstance(v, str) else v
        return v if isinstance(v, dict) and v.get("at") else None
    except Exception:  # noqa: BLE001
        return None


def probe_and_save(sb, day, session, now=None):
    """沒有今天的時效紀錄時的輕量補救：只送 HEAD（不下載、不寫 mops_events）。取不到回 None（呼叫端據此顯示時間不明）。"""
    hd = head_last_modified(session)
    asof = build_asof(hd, None, now)
    if asof:
        save_asof(sb, day, asof)
    return asof


def asof_for_display(sb, day, session=None, now=None):
    """戰卡／推演用：回傳 (asof dict 或 None, 顯示文字)。先讀今天的紀錄，沒有再補一次 HEAD。"""
    asof = load_asof(sb, day)
    if asof is None and session is not None:
        asof = probe_and_save(sb, day, session, now)
    return asof, asof_text(asof, today=day)


# ───────── ② 新聞轉述提醒（只提醒，不參與判定）─────────
def relay_hits(news_rows, symbols, limit_per_symbol=3):
    """news_rows：news_items 列（title/source/published_at/symbols/url）。回傳 {代號: [{title, src, time}]}。
    只收標題含「公告」或「重訊」、且 symbols 欄位含該代號者。同標題去重。"""
    want = {str(s) for s in symbols or []}
    out = {}
    for r in news_rows or []:
        title = str(r.get("title") or "").strip()
        if not title or not any(w in title for w in RELAY_WORDS):
            continue
        syms = {str(x) for x in (r.get("symbols") or [])}
        for s in sorted(syms & want):
            lst = out.setdefault(s, [])
            if any(x["title"] == title for x in lst) or len(lst) >= limit_per_symbol:
                continue
            t = str(r.get("published_at") or "")
            try:
                t = datetime.fromisoformat(t.replace("Z", "+00:00")).astimezone(TAIPEI_TZ).strftime("%m-%d %H:%M")
            except Exception:  # noqa: BLE001
                t = t[:16]
            lst.append({"title": title[:80], "src": str(r.get("source") or ""), "time": t})
    return out


def load_relay(sb, symbols, now=None, hours=36):
    """讀 news_items 近 hours 小時、提到任一 symbols 者，套 relay_hits。任何失敗回 {}（只是提醒，不影響戰卡）。"""
    syms = [str(s) for s in symbols or [] if s]
    if sb is None or not syms:
        return {}
    now = now or datetime.now(timezone.utc)
    since = (now.astimezone(timezone.utc) - timedelta(hours=hours)).isoformat()
    try:
        rows = (sb.table("news_items").select("source,title,published_at,symbols")
                .overlaps("symbols", syms).gte("published_at", since)
                .order("published_at", desc=True).limit(300).execute().data) or []
    except Exception:  # noqa: BLE001
        return {}
    return relay_hits(rows, syms)


def relay_lines(hits, label=None):
    """Telegram／戰卡顯示行；沒有命中回 []。label(sym)→『名稱(代號)』。"""
    if not hits:
        return []
    lab = label or (lambda s: str(s))
    out = [f"📰 新聞轉述提醒（{RELAY_TAG}；只提醒，不參與買賣判定）："]
    for sym in sorted(hits):
        for h in hits[sym]:
            out.append(f"・{lab(sym)} {h.get('time', '')}［{h.get('src', '')}］{h.get('title', '')}〔{RELAY_TAG}〕")
    return out
