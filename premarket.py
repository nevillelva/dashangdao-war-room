"""
早盤情報（2026-10-07）——純邏輯 + 抓取，不依賴 Streamlit。

目標：交易日 05:30 蒐集「前一個交易日～今晨」的重大事件（官方重大訊息、新聞、美股、行事曆），
規則分類 → 篩出需要關注的個股 → （可選）AI 整理推薦原因 → Telegram 推播 + 網站「📰 早盤情報」區。

資料來源與合規（2026-10-07 實測／查證，細節見 claude/擴充優化_族群勝率_早盤情報研究_20261007.md 與子報告）：
  ✅ 證交所 OpenAPI t187ap04_L（上市重大訊息）、櫃買 OpenAPI mopsfin_t187ap04_O（上櫃）：政府資料開放授權，官方同意的介接管道。
     （公開資訊觀測站網頁：證交所使用條款第 6 條禁止自動化存取 → 不爬，改用上述 OpenAPI 取得同一份資料。）
  ✅ 中央社 RSS：robots 聲明 ai-input=yes（可送 AI 摘要，不可訓練）。
  ✅ 鉅亨網 RSS：條款無 AI 明文，但禁止轉載全文/營利 → 只存「標題＋代號＋時間＋連結」，標題可送 AI。
  🔗 Yahoo 股市、經濟日報、MoneyDJ RSS：UDN／MoneyDJ 的 robots 明文禁止 AI/ML 用途，Yahoo 封鎖 AI 爬蟲
     → 只當 RSS 閱讀器顯示「標題＋連結」，**不進任何 AI 流程**（ai_ok=False）。
  ❌ 股癌 Vocus 沙龍（付費訂閱第三方逐字稿）、CMoney（無公開 API、forum robots 封鎖 AI、著作權保留）、
     富邦／群益／永豐／國泰研報（登入牆、驗證碼或 WAF）、Google News RSS（robots 全禁、僅限個人閱讀器）→ 不使用，也不嘗試繞過。
  ℹ️ 股癌（Gooaye）公開 Podcast RSS（SoundOn）只列「集數＋發布時間＋連結」，不存簡介、不送 AI。
"""
import hashlib
import html
import json
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import requests

TPE = timezone(timedelta(hours=8))
UA = "Mozilla/5.0 (compatible; warroom-premarket/1.0; personal research use)"
TIMEOUT = 20

TWSE_MATERIAL_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap04_L"
TPEX_MATERIAL_URL = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap04_O"
TWSE_EXDIV_URL = "https://openapi.twse.com.tw/v1/exchangeReport/TWT48U_ALL"
TWSE_PUNISH_URL = "https://openapi.twse.com.tw/v1/announcement/punish"
TWSE_NOTETRANS_URL = "https://openapi.twse.com.tw/v1/announcement/notetrans"
TPEX_EXDIV_URL = "https://www.tpex.org.tw/openapi/v1/tpex_exright_prepost"
TPEX_DISPOSAL_URL = "https://www.tpex.org.tw/openapi/v1/tpex_disposal_information"
TPEX_WARNING_URL = "https://www.tpex.org.tw/openapi/v1/tpex_trading_warning_information"
OPEN_DATA_ATTRIBUTION = "資料來源：臺灣證券交易所／證券櫃檯買賣中心 OpenAPI（政府資料開放授權條款 1.0 https://data.gov.tw/license）"

# 新聞 RSS：ai_ok=True 才允許把標題送進 AI；False 只顯示標題＋連結
NEWS_SOURCES = [
    {"id": "cna_finance", "name": "中央社財經", "url": "https://feeds.feedburner.com/rsscna/finance", "ai_ok": True},
    {"id": "cnyes_tw", "name": "鉅亨網台股", "url": "https://news.cnyes.com/rss/v1/news/category/tw_stock", "ai_ok": True},
    {"id": "cnyes_all", "name": "鉅亨網頭條", "url": "https://news.cnyes.com/rss/v1/news/category/all", "ai_ok": True},
    {"id": "yahoo_tw", "name": "Yahoo股市", "url": "https://tw.stock.yahoo.com/rss?category=tw-market", "ai_ok": False},
    {"id": "udn_money", "name": "經濟日報", "url": "https://money.udn.com/rssfeed/news/1001/5590?ch=money", "ai_ok": False},
    {"id": "moneydj", "name": "MoneyDJ", "url": "https://www.moneydj.com/KMDJ/RssCenter.aspx?svc=NR&fno=1&arg=MB010000", "ai_ok": False},
]
PODCAST_FEEDS = [
    {"id": "gooaye", "name": "股癌 Podcast", "url": "https://feeds.soundon.fm/podcasts/954689a5-3096-43a4-a80b-7810b219cef3.xml"},
]

# ------------------------------------------------------------------ 基本工具

def roc_to_iso(s):
    """民國日期 → ISO。支援 '1151005'、'115/10/05'、'115年10月05日'、'2026-10-05'。無法解析回 None。"""
    if s is None:
        return None
    t = str(s).strip()
    if not t:
        return None
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", t[:10])
    if m:
        return t[:10]
    m = re.fullmatch(r"(\d{3})(\d{2})(\d{2})", t)
    if not m:
        m = re.search(r"(\d{2,3})\s*[/年\-.]\s*(\d{1,2})\s*[/月\-.]\s*(\d{1,2})", t)
    if not m:
        return None
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if y < 1911:
        y += 1911
    try:
        return datetime(y, mo, d).strftime("%Y-%m-%d")
    except ValueError:
        return None


def fmt_time(s):
    """發言時間未補零：'70003' → '07:00:03'、'183000' → '18:30:00'。"""
    t = re.sub(r"\D", "", str(s or ""))
    if not t:
        return ""
    t = t.zfill(6)[-6:]
    return f"{t[0:2]}:{t[2:4]}:{t[4:6]}"


def _g(row, key, default=""):
    """取欄位：容許欄名尾端有空白（證交所『主旨 』）。"""
    if key in row:
        return row[key]
    for k, v in row.items():
        if str(k).strip() == key:
            return v
    return default


def clean_text(s, limit=None):
    t = html.unescape(str(s or ""))
    t = re.sub(r"<!\[CDATA\[|\]\]>", "", t)
    t = re.sub(r"<[^>]+>", "", t)
    t = re.sub(r"[\r\n\t]+", " ", t)
    t = re.sub(r"\s{2,}", " ", t).strip()
    return t[:limit] if limit else t


def make_session():
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept": "application/json, application/rss+xml, */*"})
    return s


def http_get(url, session=None, retries=2, timeout=TIMEOUT):
    """回傳 (text 或 None, 狀態字串)。"""
    ss = session or make_session()
    last = ""
    for i in range(retries + 1):
        try:
            r = ss.get(url, timeout=timeout)
            if r.status_code == 200:
                try:
                    return r.content.decode("utf-8"), "ok"
                except UnicodeDecodeError:
                    return r.content.decode("big5", errors="replace"), "ok"
            last = f"HTTP {r.status_code}"
        except Exception as e:  # noqa: BLE001
            last = f"{type(e).__name__}: {str(e)[:80]}"
    return None, last


def fetch_json(url, session=None):
    text, st = http_get(url, session)
    if text is None:
        return None, st
    try:
        d = json.loads(text)
        return (d if isinstance(d, list) else None), ("ok" if isinstance(d, list) else "不是 list")
    except Exception as e:  # noqa: BLE001
        return None, f"JSON 解析失敗：{type(e).__name__}"


# ------------------------------------------------------------------ 重大訊息

def normalize_material(row, market):
    """證交所(L)／櫃買(O) 的一筆重大訊息 → 統一格式。"""
    d = roc_to_iso(_g(row, "發言日期"))
    sym = str(_g(row, "公司代號") or _g(row, "SecuritiesCompanyCode")).strip()
    return {
        "market": market, "ev_date": d, "ev_time": fmt_time(_g(row, "發言時間")),
        "symbol": sym, "name": str(_g(row, "公司名稱") or _g(row, "CompanyName")).strip(),
        "subject": clean_text(_g(row, "主旨")), "clause": str(_g(row, "符合條款")).strip(),
        "fact_date": roc_to_iso(_g(row, "事實發生日")), "body": clean_text(_g(row, "說明")),
    }


NOISE_WORDS = ("更名", "面額", "公告更正", "更正", "股務代理", "代理發言人", "章程", "重編", "公布注意交易資訊", "催繳",
               "變更登記完成", "限制員工權利新股", "代為公告", "核准投資", "受託公告")

# (類別, 關鍵字, 方向, 重要度)；依序比對，第一個命中者為主類別
CATEGORY_RULES = [
    ("災損停工", ("火災", "爆炸", "停工", "停產", "災害", "災損", "罷工", "地震", "污染", "意外", "重大損失", "淹水"), -2, 5),
    ("訴訟裁罰", ("訴訟", "裁罰", "罰鍰", "起訴", "羈押", "違反", "調查局", "檢調", "搜索"), -1, 4),
    ("法說會", ("法人說明會", "法說會", "投資人說明會"), 0, 1),
    ("自結營收", ("自結營收", "月營收", "合併營收", "營業收入"), 0, 3),
    ("自結損益", ("自結損益", "自結財報", "自結稅前", "自結盈餘", "財務報告", "第一季", "第二季", "第三季", "年度財報"), 0, 3),
    ("籌資稀釋", ("現金增資", "私募", "海外存託憑證", "可轉換公司債", "可轉債", "發行公司債", "辦理減資", "減資", "訂價"), -1, 3),
    ("併購經營權", ("公開收購", "併購", "收購", "經營權", "股權轉讓", "委託書", "合併"), 0, 4),
    ("庫藏股", ("買回本公司股份", "庫藏股"), 1, 3),
    ("取得擴產訂單", ("取得不動產", "取得設備", "取得廠房", "擴建", "擴產", "新建廠", "資本支出", "重大訂單", "訂單", "簽訂", "合約", "增資子公司", "對外投資", "轉投資"), 1, 3),
    ("處分資產", ("處分",), 0, 2),
    ("股利配息", ("股利", "配息", "配股", "盈餘分派", "盈餘分配"), 1, 2),
    ("澄清報導", ("澄清", "媒體報導"), 0, 2),
    ("董監經理人異動", ("董事", "監察人", "經理人", "會計師", "財務主管", "總經理", "董事長", "內部人"), 0, 2),
    ("除權息股東會", ("除權", "除息", "停止過戶", "股東常會", "股東臨時會", "股東會"), 0, 1),
]

_PCT = re.compile(r"(年增|年減|增加|減少|成長|衰退|下降|上升)[^0-9\-+]{0,10}([+-]?\d+(?:\.\d+)?)\s*%")


def parse_growth_pct(text):
    """從公告文字找『年增/增加 xx%』，回傳帶正負號的百分比或 None。找不到、或有多個互相矛盾的值回 None。"""
    vals = []
    for word, num in _PCT.findall(text or ""):
        v = float(num)
        if word in ("年減", "減少", "衰退", "下降"):
            v = -abs(v)
        vals.append(v)
    if not vals:
        return None
    if all(v >= 0 for v in vals) or all(v <= 0 for v in vals):
        return vals[0]
    return None


def parse_meeting_date(body):
    """法說會說明欄：『1.召開法人說明會之日期：115/10/08』→ ISO；找不到回 None。"""
    m = re.search(r"說明會之日期[:：]\s*([0-9/年月日\-.]+)", body or "")
    return roc_to_iso(m.group(1)) if m else None


def classify_event(ev):
    """回傳 dict：category, direction(-2..+2), importance(0..5), event_date(法說會的開會日), growth_pct, flags。純規則，不是投資建議。"""
    subj, body, clause = ev.get("subject", ""), ev.get("body", ""), ev.get("clause", "")
    text = subj + " " + body[:300]
    out = {"category": "其他", "direction": 0, "importance": 1, "event_date": None, "growth_pct": None, "flags": []}
    if any(w in subj for w in NOISE_WORDS) and not any(w in subj for w in ("災", "訴訟", "火")):
        out.update(category="例行公告", importance=0)
        return out
    cat = None
    for name, kws, direction, imp in CATEGORY_RULES:
        if any(k in subj for k in kws):
            cat = (name, direction, imp)
            break
    if cat is None and "第12款" in clause:
        cat = ("法說會", 0, 1)
    if cat is None:
        # 主旨沒命中時才看說明欄的強訊號（避免說明欄的一般性用語造成誤判）
        for name, kws, direction, imp in CATEGORY_RULES[:2]:
            if any(k in body[:200] for k in kws):
                cat = (name, direction, max(imp - 1, 1))
                break
    if cat is None:
        return out
    name, direction, imp = cat
    if name == "庫藏股" and any(k in subj for k in ("屆滿", "執行情形", "執行完畢")):
        direction, imp = 0, 1          # 買回期間屆滿／執行情形＝事後報告，不是新的利多
    out.update(category=name, direction=direction, importance=imp)
    if name == "法說會":
        out["event_date"] = parse_meeting_date(body) or ev.get("fact_date")
    elif name in ("自結營收", "自結損益"):
        g = parse_growth_pct(text)
        out["growth_pct"] = g
        if g is not None:
            out["direction"] = 2 if g >= 20 else (1 if g >= 5 else (-2 if g <= -20 else (-1 if g <= -10 else 0)))
            out["importance"] = imp + (1 if abs(g) >= 20 else 0)
        else:
            out["flags"].append("未能從公告解析年增率，請看原文")
    elif name == "處分資產":
        if "利益" in text or "利得" in text:
            out["direction"] = 1
            out["flags"].append("處分利益屬一次性（非經常性）")
        elif "損失" in text:
            out["direction"] = -1
    elif name == "籌資稀釋":
        out["flags"].append("可能稀釋股權")
    elif name == "併購經營權":
        out["flags"].append("不確定性高，方向看條件")
    return out


def event_key(ev):
    return hashlib.sha1(f"{ev.get('ev_date')}|{ev.get('ev_time')}|{ev.get('symbol')}|{ev.get('subject')}".encode("utf-8")).hexdigest()


def build_events(rows, market, since_date):
    """rows → 統一格式 + 分類；只留發言日期 >= since_date。回傳 list[dict]。"""
    out = []
    for r in rows or []:
        ev = normalize_material(r, market)
        if not ev["symbol"] or not ev["ev_date"] or ev["ev_date"] < since_date:
            continue
        if not re.fullmatch(r"\d{4,6}[A-Z]?", ev["symbol"]):
            continue
        ev.update(classify_event(ev))
        ev["ev_key"] = event_key(ev)
        out.append(ev)
    return out


def material_freshness(rows, expected_date):
    """重大訊息檔是不是已經含 expected_date（前一個交易日）之後的資料。回傳 (ok, 最新發言日期)。"""
    dates = [roc_to_iso(_g(r, "發言日期")) for r in rows or []]
    dates = [d for d in dates if d]
    latest = max(dates) if dates else None
    return (latest is not None and latest >= expected_date), latest


# ------------------------------------------------------------------ 新聞 RSS

def parse_rss(text):
    """寬鬆解析 RSS（含鉅亨網那種 link 沒包 <link> 標籤、title 包 CDATA 被跳脫的非標準格式）。
    回傳 [{title, url, published_at(iso, 台北時區), category, keywords}]。"""
    items = []
    for blk in re.findall(r"<item>(.*?)</item>", text or "", flags=re.S | re.I):
        m = re.search(r"<title>(.*?)</title>", blk, flags=re.S | re.I)
        title = clean_text(m.group(1)) if m else ""
        url = ""
        m = re.search(r"<link>(.*?)</link>", blk, flags=re.S | re.I)
        if m and m.group(1).strip().startswith("http"):
            url = m.group(1).strip()
        if not url:
            m = re.search(r"^\s*(https?://\S+)\s*$", blk, flags=re.M)
            if m:
                url = m.group(1)
        if not url:
            m = re.search(r"<guid[^>]*>(https?://[^<\s]+)</guid>", blk, flags=re.I)
            url = m.group(1) if m else ""
        pub = None
        m = re.search(r"<pubdate>(.*?)</pubdate>", blk, flags=re.S | re.I)
        if m:
            try:
                dt = parsedate_to_datetime(m.group(1).strip())
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                pub = dt.astimezone(TPE).isoformat()
            except Exception:  # noqa: BLE001
                pub = None
        m = re.search(r"<category>(.*?)</category>", blk, flags=re.S | re.I)
        cat = clean_text(m.group(1)) if m else ""
        m = re.search(r"<media:keywords>(.*?)</media:keywords>", blk, flags=re.S | re.I)
        kw = clean_text(m.group(1)) if m else ""
        if title and url:
            items.append({"title": title, "url": url, "published_at": pub, "category": cat, "keywords": kw})
    return items


_CODE_TAG = re.compile(r"\((\d{4})-TW\)|（(\d{4})）|\((\d{4})\)")


def extract_symbols(title, name_to_code=None):
    """標題 → 股票代號清單：先抓 '(2330-TW)'、'(2330)' 這類代號，再用公司名稱（≥3 字，避免短名誤判）比對。"""
    syms = []
    for m in _CODE_TAG.finditer(title or ""):
        c = next(g for g in m.groups() if g)
        if c not in syms:
            syms.append(c)
    if name_to_code:
        for nm, c in name_to_code.items():
            if len(nm) >= 3 and nm in (title or "") and c not in syms:
                syms.append(c)
    return syms[:6]


def news_hash(source, url):
    return hashlib.sha1(f"{source}|{url}".encode("utf-8")).hexdigest()


def fetch_news(session=None, sources=None):
    """逐一抓 RSS。回傳 (items, status{source_id: 狀態})。每筆 item 含 source、source_name、ai_ok。"""
    items, status = [], {}
    for src in (sources or NEWS_SOURCES):
        text, st = http_get(src["url"], session)
        if text is None:
            status[src["id"]] = st
            continue
        got = parse_rss(text)
        status[src["id"]] = f"ok({len(got)})" if got else "ok(0)"
        for it in got:
            items.append(dict(it, source=src["id"], source_name=src["name"], ai_ok=src["ai_ok"]))
    return items, status


def fetch_podcast_latest(session=None):
    """股癌等 Podcast：只回『最新一集的標題、發布時間、連結』（不存簡介、不送 AI）。失敗回 None。"""
    out = []
    for p in PODCAST_FEEDS:
        text, st = http_get(p["url"], session, retries=1)
        if not text:
            continue
        m = re.search(r"<item>(.*?)</item>", text, flags=re.S | re.I)
        if not m:
            continue
        blk = m.group(1)
        t = re.search(r"<title>(.*?)</title>", blk, flags=re.S | re.I)
        d = re.search(r"<pubDate>(.*?)</pubDate>", blk, flags=re.S | re.I)
        link = re.search(r"<link>(.*?)</link>", blk, flags=re.S | re.I)
        try:
            pub = parsedate_to_datetime(d.group(1).strip()).astimezone(TPE).isoformat() if d else None
        except Exception:  # noqa: BLE001
            pub = None
        out.append({"name": p["name"], "title": clean_text(t.group(1)) if t else "", "published_at": pub,
                    "url": (link.group(1).strip() if link else "")})
    return out


# ------------------------------------------------------------------ 行事曆

def build_calendar(exdiv_rows, punish_rows, notice_rows, tpex_exdiv, tpex_disposal, tpex_warning, start, end):
    """除權息／處置／注意 → 依代號整理，只留 start~end（含）內的日期。回傳 dict 三個 list。
    官方欄位名稱跨端點不一致，這裡採寬鬆解析；解析不到的列略過（寧缺勿錯）。"""
    cal = {"exdiv": [], "disposal": [], "notice": []}

    def _in(d):
        return d and start <= d <= end

    for r in list(exdiv_rows or []):
        d = roc_to_iso(r.get("Date") or r.get("除權除息日期"))
        code = str(r.get("Code") or r.get("股票代號") or "").strip()
        if code and not code.startswith("00") and _in(d):
            cal["exdiv"].append({"symbol": code, "name": str(r.get("Name") or r.get("名稱") or "").strip(), "date": d,
                                 "kind": str(r.get("Exdividend") or "").strip(), "cash": r.get("CashDividend"), "stock": r.get("StockDividendRatio")})
    for r in list(tpex_exdiv or []):
        d = roc_to_iso(r.get("ExRrightsExDividendDate") or r.get("ExRightsExDividendDate") or r.get("Date") or r.get("除權息日期"))
        code = str(r.get("SecuritiesCompanyCode") or r.get("Code") or r.get("代號") or "").strip()
        if code and not code.startswith("00") and _in(d):
            cal["exdiv"].append({"symbol": code, "name": str(r.get("CompanyName") or r.get("Name") or "").strip(), "date": d, "kind": "", "cash": r.get("CashDividend"), "stock": None})
    for r in list(punish_rows or []) + list(tpex_disposal or []):
        code = str(r.get("Code") or r.get("SecuritiesCompanyCode") or r.get("證券代號") or "").strip()
        per = str(r.get("DispositionPeriod") or r.get("處置起迄時間") or "")
        ds = re.findall(r"\d{2,3}/\d{1,2}/\d{1,2}|\d{7}", per)
        iso = [roc_to_iso(x) for x in ds]
        iso = [x for x in iso if x]
        if code and iso and iso[-1] >= start:
            cal["disposal"].append({"symbol": code, "name": str(r.get("Name") or r.get("CompanyName") or "").strip(),
                                    "until": iso[-1], "reason": str(r.get("ReasonsOfDisposition") or r.get("處置原因") or "")[:40]})
    for r in list(notice_rows or []) + list(tpex_warning or []):
        code = str(r.get("Code") or r.get("SecuritiesCompanyCode") or r.get("證券代號") or "").strip()
        if code and re.fullmatch(r"\d{4}", code):
            cal["notice"].append({"symbol": code, "name": str(r.get("Name") or r.get("CompanyName") or "").strip()})
    return cal


# ------------------------------------------------------------------ 選股與排名

def rank_events(events, own_symbols=None, liquid=None, min_importance=2, min_value=1e8, unknown_min_importance=3):
    """把事件彙整成『每檔一筆』的候選。
    own_symbols：持倉＋雷達（一律納入，不論分數）。
    liquid：{代號: 前一交易日成交值}（None＝不過濾）。查得到成交值 → 須 ≥ min_value（避免冷門股）；
            查不到（快照只涵蓋部分股票，上櫃多半不在）→ 事件重要度須 ≥ unknown_min_importance。
    回傳 list[dict]：symbol, name, events[], bull/bear 分數, rank_score, own。依 rank_score 由大到小。"""
    own = set(own_symbols or [])
    by = {}
    for ev in events:
        if ev.get("importance", 0) < 1 and ev["symbol"] not in own:
            continue
        s = by.setdefault(ev["symbol"], {"symbol": ev["symbol"], "name": ev.get("name", ""), "events": [], "bull": 0, "bear": 0})
        s["events"].append(ev)
        d, imp = ev.get("direction", 0), ev.get("importance", 0)
        if d > 0:
            s["bull"] += imp * d
        elif d < 0:
            s["bear"] += imp * -d
    out = []
    for s in by.values():
        top = max(e.get("importance", 0) for e in s["events"])
        s["own"] = s["symbol"] in own
        s["rank_score"] = round(max(s["bull"], s["bear"]) + top * 0.5, 2)
        s["bias"] = "偏多" if s["bull"] > s["bear"] else ("偏空" if s["bear"] > s["bull"] else "中性")
        if s["own"]:
            out.append(s)
            continue
        if top < min_importance:
            continue
        if liquid is not None:
            tv = liquid.get(s["symbol"])
            if tv is None:
                if top < unknown_min_importance:
                    continue
            elif float(tv) < min_value:
                continue
        out.append(s)
    out.sort(key=lambda x: (-x["own"], -x["rank_score"], x["symbol"]))
    return out


def event_line(ev, max_len=60):
    t = f"{ev.get('ev_time', '')[:5]} {ev.get('subject', '')}".strip()
    return t if len(t) <= max_len else t[: max_len - 1] + "…"


# ------------------------------------------------------------------ AI 摘要（只送允許的內容）

AI_SYSTEM = ("你是台股盤前資訊整理助手。只能根據使用者提供的公告原文與新聞標題整理，不可編造任何數字、事實或公司；"
             "不確定就寫『原文未提及』。不要給買賣建議。輸出必須是 JSON，不要任何其他文字。")


def build_ai_prompt(cands, news_titles, us_line, max_cands=14):
    lines = ["【美股與匯率】" + (us_line or "（無資料）"), "", "【候選個股與官方公告原文】"]
    for c in cands[:max_cands]:
        lines.append(f"- {c['symbol']} {c.get('name', '')}")
        for e in c["events"][:3]:
            lines.append(f"  · [{e.get('category')}] {e.get('subject', '')[:80]}｜{(e.get('body') or '')[:220]}")
    if news_titles:
        lines += ["", "【新聞標題（僅供背景）】"] + [f"- {t}" for t in news_titles[:30]]
    lines += ["", "請輸出 JSON：", '{"overview":"不超過120字的今日盤前重點","items":[{"symbol":"代號","direction":-2到2的整數,'
              '"reason":"不超過60字，只引用上面提供的事實","risk":"不超過40字"}]}',
              "items 只能包含上面列出的代號。direction：+2 明確利多、+1 偏多、0 中性、-1 偏空、-2 明確利空。"]
    return "\n".join(lines)


def parse_ai_json(text, valid_symbols):
    """從模型回覆取出 JSON；欄位清洗：只留合法代號、direction 夾在 -2..2、字數截斷。失敗回 None。"""
    if not text:
        return None
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S | re.I)      # 思考型模型的推理段落
    m = re.search(r"\{.*\}", text, flags=re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(d, dict) or not ("items" in d or "overview" in d):
        return None
    items = []
    for it in d.get("items") or []:
        if not isinstance(it, dict):
            continue
        sym = str(it.get("symbol", "")).strip()
        if sym not in valid_symbols:
            continue
        try:
            dr = max(-2, min(2, int(it.get("direction", 0))))
        except (TypeError, ValueError):
            dr = 0
        items.append({"symbol": sym, "direction": dr, "reason": str(it.get("reason", ""))[:80], "risk": str(it.get("risk", ""))[:60]})
    return {"overview": str(d.get("overview", ""))[:200], "items": items}


# ------------------------------------------------------------------ 推播文字

WEEKDAY = "一二三四五六日"


def us_market_line(us):
    """us：{'SOXX': {'dp':..}, ...}。"""
    if not us:
        return ""
    names = [("SOXX", "費半"), ("QQQ", "那指"), ("SPY", "標普"), ("TSM", "台積電ADR"), ("NVDA", "NVDA")]
    parts = []
    for k, nm in names:
        q = us.get(k)
        if q and q.get("ok") and q.get("dp") is not None:
            parts.append(f"{nm} {q['dp']:+.1f}%")
    fx = us.get("USDTWD")
    if fx and fx.get("ok") and fx.get("c"):
        parts.append(f"美元/台幣 {fx['c']:.2f}")
    return "｜".join(parts)


def format_messages(brief):
    """回傳要推播的多則訊息（每則另外還會被 tg_send 自動切到 <3800 字）。純文字、不含 HTML。"""
    d = brief["date"]
    try:
        wd = WEEKDAY[datetime.strptime(d, "%Y-%m-%d").weekday()]
    except Exception:  # noqa: BLE001
        wd = ""
    gen = brief.get("generated_hm", "")
    head = [f"📰 早盤情報 {d[5:7]}/{d[8:10]}（{wd}）{gen}"]
    if brief.get("us_line"):
        head.append("🌎 " + brief["us_line"])
    if brief.get("regime_line"):
        head.append("🌡️ " + brief["regime_line"])
    st = brief.get("source_status", {})
    if st.get("material_stale"):
        head.append(f"⚠️ 證交所重大訊息尚未更新（最新只到 {st.get('material_latest') or '?'}），先以新聞與行事曆出版，08:00 會補發。")
    if brief.get("ai_overview"):
        head.append("🧭 " + brief["ai_overview"])

    m1 = list(head)
    picks = brief.get("picks", [])
    bulls = [p for p in picks if p.get("bias") == "偏多" and not p.get("own")]
    bears = [p for p in picks if p.get("bias") == "偏空" and not p.get("own")]
    neut = [p for p in picks if p.get("bias") == "中性" and not p.get("own")]

    def _pick_block(p, i):
        ls = [f"{i}. {p['name']}({p['symbol']}) {p.get('bias', '')}" + (f"｜{p['sector']}" if p.get("sector") else "")]
        for e in p["events"][:2]:
            ls.append(f"   • {event_line(e)}")
        if p.get("why"):
            ls.append(f"   原因：{p['why']}")
        if p.get("tech"):
            ls.append(f"   位置：{p['tech']}")
        if p.get("risk"):
            ls.append(f"   風險：{p['risk']}")
        return "\n".join(ls)

    if bulls:
        m1.append("\n⭐ 偏多觀察（事件面，非買進指令）")
        m1.extend(_pick_block(p, i) for i, p in enumerate(bulls[:6], 1))
    if bears:
        m1.append("\n⚠️ 偏空／風險")
        m1.extend(_pick_block(p, i) for i, p in enumerate(bears[:5], 1))
    if not bulls and not bears:
        m1.append("\n（今天沒有達到門檻的重大事件個股）")
    msgs = ["\n".join(m1)]

    m2 = []
    own = [p for p in picks if p.get("own")]
    if own:
        m2.append("📌 你的持倉／雷達相關")
        m2.extend(_pick_block(p, i) for i, p in enumerate(own[:10], 1))
    if neut:
        m2.append("\n📋 其他值得留意")
        m2.extend(_pick_block(p, i) for i, p in enumerate(neut[:5], 1))
    cal = brief.get("calendar", {})
    lines = []
    if cal.get("meetings"):
        lines.append("🎤 法說會：" + "、".join(f"{x['name']}{x['symbol']}({x['date'][5:]})" for x in cal["meetings"][:12]))
    if cal.get("exdiv"):
        lines.append("💰 近日除權息：" + "、".join(f"{x['name'] or x['symbol']}({x['date'][5:]})" for x in cal["exdiv"][:12]))
    if cal.get("own_flags"):
        lines.append("🔔 持倉提醒：" + "、".join(cal["own_flags"][:10]))
    if lines:
        m2.append("\n📅 行事曆")
        m2.extend(lines)
    if m2:
        msgs.append("\n".join(m2))

    m3 = []
    ai_news = brief.get("news_ai", [])
    link_news = brief.get("news_links", [])
    if ai_news:
        m3.append("📰 新聞重點（中央社／鉅亨）")
        m3.extend(f"• {n['title']}（{n['source']}）" for n in ai_news[:10])
    if link_news:
        m3.append("\n🔗 其他媒體標題（僅連結，不經 AI）")
        m3.extend(f"• {n['title']}（{n['source']}）{n['url']}" for n in link_news[:6])
    pod = brief.get("podcast")
    if pod:
        m3.append("\n🎙️ " + "｜".join(f"{p['name']}：{p['title']}" + (f"（{p['published_at'][5:10]}）" if p.get("published_at") else "") for p in pod[:1]))
    m3.append("\nℹ️ 事件與新聞整理僅供自己研究參考，不是投資建議；戰情室沒有下單功能。完整內容見網站「📰 早盤情報」。")
    msgs.append("\n".join(m3))
    return msgs
