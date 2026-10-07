"""premarket.py 單元測試（全部用合成樣本，不連網）。python3 test_premarket.py"""
import premarket as pm

FAIL = []


def check(name, cond, extra=""):
    if not cond:
        FAIL.append(name)
        print("❌", name, extra)
    else:
        print("✅", name)


# --- 日期／時間
check("roc 7碼", pm.roc_to_iso("1151005") == "2026-10-05")
check("roc 斜線", pm.roc_to_iso("115/10/08") == "2026-10-08")
check("roc 中文", pm.roc_to_iso("115年10月05日") == "2026-10-05")
check("iso 原樣", pm.roc_to_iso("2026-10-05") == "2026-10-05")
check("壞日期", pm.roc_to_iso("1151345") is None and pm.roc_to_iso("") is None and pm.roc_to_iso(None) is None)
check("發言時間未補零", pm.fmt_time("70003") == "07:00:03" and pm.fmt_time("183000") == "18:30:00" and pm.fmt_time("") == "")

# --- 欄位尾端空白（證交所『主旨 』）
row = {"出表日期": "1151007", "發言日期": "1151007", "發言時間": "70003", "公司代號": "2330", "公司名稱": "台積電",
       "主旨 ": "公告本公司受邀參加法人說明會", "符合條款": "第12款", "事實發生日": "1151007",
       "說明": "1.召開法人說明會之日期：115/10/08 2.召開法人說明會之時間：14:00"}
ev = pm.normalize_material(row, "L")
check("主旨尾端空白可取到", ev["subject"].startswith("公告本公司受邀"), ev)
check("時間格式", ev["ev_time"] == "07:00:03")
c = pm.classify_event(ev)
check("法說會分類", c["category"] == "法說會" and c["event_date"] == "2026-10-08", c)

# --- 分類
def mk(subject, body="", clause=""):
    return {"subject": subject, "body": body, "clause": clause, "fact_date": None}

c = pm.classify_event(mk("公告本公司112年度第3季自結損益", "合併營收年增 35.2%，稅前盈餘成長"))
check("自結損益解析年增率", c["category"] == "自結損益" and c["growth_pct"] == 35.2 and c["direction"] == 2, c)
c = pm.classify_event(mk("公告本公司9月份自結營收", "較去年同期年減 25%"))
check("營收年減", c["growth_pct"] == -25.0 and c["direction"] == -2, c)
c = pm.classify_event(mk("公告本公司9月份自結營收", "年增 30% 但月減 年減 5%"))
check("矛盾值不亂猜", c["growth_pct"] is None and "未能從公告解析年增率，請看原文" in c["flags"], c)
c = pm.classify_event(mk("本公司廠房發生火災", ""))
check("災損", c["category"] == "災損停工" and c["direction"] == -2 and c["importance"] == 5, c)
c = pm.classify_event(mk("公告本公司董事會決議辦理現金增資", ""))
check("籌資稀釋", c["category"] == "籌資稀釋" and c["direction"] == -1, c)
c = pm.classify_event(mk("公告本公司董事會決議買回本公司股份", ""))
check("庫藏股", c["category"] == "庫藏股" and c["direction"] == 1, c)
c = pm.classify_event(mk("公告本公司更名", ""))
check("例行公告重要度0", c["category"] == "例行公告" and c["importance"] == 0, c)
c = pm.classify_event(mk("公告本公司股務代理人更名，發生火災", ""))
check("雜訊字眼但有災字不降級", c["category"] != "例行公告", c)
c = pm.classify_event(mk("公告本公司處分資產", "處分利益約 2 億元"))
check("處分利益屬一次性", c["direction"] == 1 and any("一次性" in f for f in c["flags"]), c)
c = pm.classify_event(mk("說明媒體報導", "與本公司無關"))
check("澄清", c["category"] == "澄清報導", c)

# --- 2026-10-07 實測抓到的誤判樣本（真實公告主旨）
for subj in ("係因本公司有價證券於集中交易市場達公布注意交易資訊 標準，故公布相關財務業務等重大訊息，以利投資人區別瞭解",
             "本公司115年現金增資催繳公告", "公告本公司註銷收回之限制員工權利新股 辦理減資變更登記完成",
             "代台灣人壽公告核准投資 Brookfield Infrastructure Fund VI-A, L.P."):
    c = pm.classify_event(mk(subj))
    check("例行公告不產生方向：" + subj[:14], c["importance"] == 0 and c["direction"] == 0, c)
c = pm.classify_event(mk("公告本公司買回庫藏股期間屆滿執行情形"))
check("庫藏股屆滿報告不算利多", c["direction"] == 0 and c["importance"] == 1, c)
c = pm.classify_event(mk("公告本公司董事會通過對外投資越南設廠"))
check("真正的對外投資仍算利多", c["direction"] == 1, c)
c = pm.classify_event(mk("公告本公司取得廠房及設備"))
check("取得廠房仍算利多", c["direction"] == 1, c)
cal0 = pm.build_calendar([{"Date": "1151009", "Code": "00919", "Name": "ETF"}, {"Date": "1151009", "Code": "2330", "Name": "台積電"}], [], [], [], [], [], "2026-10-07", "2026-10-21")
check("行事曆略過 ETF(00開頭)", [x["symbol"] for x in cal0["exdiv"]] == ["2330"], cal0)

# --- build_events / freshness
rows = [row, dict(row, 公司代號="9999-X", 發言日期="1151007"), dict(row, 發言日期="1151001", 公司代號="1101")]
evs = pm.build_events(rows, "L", "2026-10-06")
check("build_events 過濾舊日期與壞代號", [e["symbol"] for e in evs] == ["2330"] and evs[0]["ev_key"], evs)
ok, latest = pm.material_freshness(rows, "2026-10-06")
check("freshness ok", ok and latest == "2026-10-07")
ok, latest = pm.material_freshness([dict(row, 發言日期="1151001")], "2026-10-06")
check("freshness stale", (not ok) and latest == "2026-10-01")
ok, latest = pm.material_freshness([], "2026-10-06")
check("freshness empty", (not ok) and latest is None)
check("event_key 穩定", pm.event_key(ev) == pm.event_key(dict(ev)))

# --- RSS（含鉅亨網非標準：link 沒包標籤、title CDATA）
cnyes = """<?xml version="1.0"?><rss><channel>
<item><title><![CDATA[台積電(2330-TW)法說會登場 &amp; 展望]]></title>
https://news.cnyes.com/news/id/123
<pubDate>Wed, 07 Oct 2026 21:10:00 +0000</pubDate><category>台股</category></item>
<item><title>標準來源</title><link>https://example.com/a?x=1</link><pubDate>Wed, 07 Oct 2026 01:00:00 GMT</pubDate></item>
<item><title>沒連結</title></item>
</channel></rss>"""
its = pm.parse_rss(cnyes)
check("RSS 解析筆數", len(its) == 2, its)
check("cnyes 無 link 標籤抓得到網址", its[0]["url"] == "https://news.cnyes.com/news/id/123", its[0])
check("CDATA 與 &amp;", its[0]["title"] == "台積電(2330-TW)法說會登場 & 展望", its[0]["title"])
check("時間轉台北", its[1]["published_at"].startswith("2026-10-07T09:00:00+08:00"), its[1]["published_at"])
check("parse_rss 空字串", pm.parse_rss("") == [] and pm.parse_rss(None) == [])
check("extract 代號", pm.extract_symbols("台積電(2330-TW)與聯發科（2454）") == ["2330", "2454"])
check("extract 名稱比對（≥3字）", pm.extract_symbols("緯穎營運看增", {"緯穎": "6669", "世界先進": "5347"}) == [] and
      pm.extract_symbols("世界先進擴產", {"世界先進": "5347"}) == ["5347"])
check("news_hash 不同來源不同", pm.news_hash("a", "u") != pm.news_hash("b", "u"))

# --- fetch_news：假 session
class _R:
    def __init__(self, code, content=b""):
        self.status_code, self.content = code, content


class _S:
    def __init__(self, m):
        self.m = m
        self.headers = {}

    def get(self, url, timeout=0):
        v = self.m.get(url)
        if isinstance(v, Exception):
            raise v
        return v or _R(404)


srcs = [{"id": "a", "name": "甲", "url": "u1", "ai_ok": True}, {"id": "b", "name": "乙", "url": "u2", "ai_ok": False},
        {"id": "c", "name": "丙", "url": "u3", "ai_ok": True}]
sess = _S({"u1": _R(200, cnyes.encode("utf-8")), "u2": _R(403), "u3": TimeoutError("x")})
items, st = pm.fetch_news(sess, srcs)
check("fetch_news 單一來源失敗不影響其他", len(items) == 2 and st["a"].startswith("ok") and "403" in st["b"] and "TimeoutError" in st["c"], (items, st))
check("ai_ok 旗標帶入", all(i["ai_ok"] is True for i in items))

# --- 行事曆
cal = pm.build_calendar(
    [{"Date": "1151009", "Code": "2330", "Name": "台積電", "Exdividend": "息", "CashDividend": "5.0"},
     {"Date": "1151201", "Code": "1101", "Name": "台泥"}],
    [{"Code": "3008", "Name": "大立光", "DispositionPeriod": "115/10/01～115/10/14"},
     {"Code": "1234", "Name": "過期", "DispositionPeriod": "115/09/01～115/09/10"}],
    [{"Code": "2317", "Name": "鴻海"}], [], [], [], "2026-10-07", "2026-10-21")
check("行事曆除權息只留區間", [x["symbol"] for x in cal["exdiv"]] == ["2330"], cal)
check("處置中", [x["symbol"] for x in cal["disposal"]] == ["3008"], cal)
check("注意股", [x["symbol"] for x in cal["notice"]] == ["2317"])

# --- 排名
def E(sym, cat, d, imp, subject="x"):
    return {"symbol": sym, "name": sym + "名", "category": cat, "direction": d, "importance": imp, "subject": subject, "ev_time": "07:00:00", "body": ""}

events = [E("1001", "自結營收", 2, 4), E("1002", "籌資稀釋", -1, 3), E("1003", "法說會", 0, 1),
          E("1004", "庫藏股", 1, 3), E("1005", "自結營收", 1, 3), E("1006", "例行公告", 0, 0), E("2002", "處分資產", 0, 2)]
rk = pm.rank_events(events, own_symbols=["1006"], liquid={"1001": 5e8, "1002": 5e8, "1004": 1e6, "2002": 5e8})
syms = [r["symbol"] for r in rk]
check("持倉一律納入且排第一", syms[0] == "1006", syms)
check("成交值太低被濾", "1004" not in syms, syms)
check("查不到成交值但重要度3放行", "1005" in syms, syms)
check("重要度低被濾", "1003" not in syms, syms)
check("偏多偏空判定", {r["symbol"]: r["bias"] for r in rk}["1001"] == "偏多" and {r["symbol"]: r["bias"] for r in rk}["1002"] == "偏空")
check("liquid=None 不過濾流動性", "1004" in [r["symbol"] for r in pm.rank_events(events)])

# --- AI
prompt = pm.build_ai_prompt(rk, ["標題A"], "費半 +1.0%")
check("prompt 含候選與標題", "1001" in prompt and "標題A" in prompt and "費半" in prompt)
valid = {"1001", "1002"}
good = '前言 {"overview":"今日重點","items":[{"symbol":"1001","direction":5,"reason":"營收年增","risk":"高檔"},{"symbol":"9999","direction":1,"reason":"幻覺"}]} 結尾'
r = pm.parse_ai_json(good, valid)
check("AI 解析：丟掉幻覺代號、夾 direction", r and [i["symbol"] for i in r["items"]] == ["1001"] and r["items"][0]["direction"] == 2, r)
check("AI 壞 JSON → None", pm.parse_ai_json("不是json", valid) is None and pm.parse_ai_json("{壞", valid) is None and pm.parse_ai_json("", valid) is None)
r = pm.parse_ai_json('{"overview":"a","items":[{"symbol":"1001","direction":"x"}]}', valid)
check("direction 非數字→0", r["items"][0]["direction"] == 0)

# --- us line / format
us = {"SOXX": {"ok": True, "dp": 1.23}, "QQQ": {"ok": True, "dp": -0.5}, "NVDA": {"ok": False}, "USDTWD": {"ok": True, "c": 31.5}}
line = pm.us_market_line(us)
check("美股摘要", "費半 +1.2%" in line and "那指 -0.5%" in line and "NVDA" not in line and "31.50" in line, line)
brief = {"date": "2026-10-07", "generated_hm": "05:31", "us_line": line, "regime_line": "盤勢：低波動", "ai_overview": "測試重點",
         "source_status": {"material_stale": True, "material_latest": "2026-10-05"},
         "picks": [dict(rk[0], sector="半導體", why="營收強", risk="追高", tech="站上月線") if False else dict(r_, why="營收強") for r_ in rk],
         "calendar": {"meetings": [{"name": "台積電", "symbol": "2330", "date": "2026-10-08"}], "exdiv": [{"name": "台泥", "symbol": "1101", "date": "2026-10-09"}], "own_flags": ["1006 處置中"]},
         "news_ai": [{"title": "新聞", "source": "中央社"}], "news_links": [{"title": "連結", "source": "Yahoo", "url": "https://y"}],
         "podcast": [{"name": "股癌", "title": "EP600", "published_at": "2026-10-05T20:00:00+08:00"}]}
msgs = pm.format_messages(brief)
check("訊息 2~3 則", 2 <= len(msgs) <= 3, len(msgs))
check("過期警示出現", "尚未更新" in msgs[0])
check("非投資建議聲明", "不是投資建議" in msgs[-1])
check("連結媒體不進 AI 區塊但有標示", "僅連結，不經 AI" in msgs[-1])
check("每則 <4096", all(len(m) < 4096 for m in msgs), [len(m) for m in msgs])
empty = pm.format_messages({"date": "2026-10-07", "picks": []})
check("空資料也能出", empty and "沒有達到門檻" in empty[0])

# AI 回覆解析強化（2026-10-07：nemotron-parse 亂碼事件）
_v = {"2330"}
check("AI 解析：去掉思考段與程式碼框", (pm.parse_ai_json('<think>x {a}</think>```json\n{"overview":"o","items":[{"symbol":"2330","direction":1}]}```', _v) or {}).get("overview") == "o")
check("AI 解析：亂碼回 None", pm.parse_ai_json("**Table**,”**,{garbage}", _v) is None)
check("AI 解析：JSON 但沒有 overview/items 回 None", pm.parse_ai_json('{"foo":1}', _v) is None)
import warroom_core as _wc
check("NIM 排除清單含 parse/retriever", all(k in _wc.NIM_EXCLUDE_CORE for k in ("parse", "retriev")))

# F7 戰卡事件時間軸
_ev = [
    {"symbol": "2330", "ev_date": "2026-10-06", "ev_time": "18:12", "category": "營收", "direction": 1, "importance": 3, "subject": "9月自結合併營收"},
    {"symbol": "2330", "ev_date": "2026-10-01", "ev_time": "09:00", "category": "法說", "direction": 0, "importance": 2, "subject": "法說會"},
    {"symbol": "2330", "ev_date": "2026-09-10", "ev_time": "09:00", "category": "其他", "direction": 0, "importance": 2, "subject": "太舊"},
    {"symbol": "2317", "ev_date": "2026-10-06", "ev_time": "10:00", "category": "營收", "direction": 1, "importance": 3, "subject": "別檔"},
    {"symbol": "2330", "ev_date": "2026-10-06", "ev_time": "20:00", "category": "例行", "direction": 0, "importance": 0, "subject": "例行不列"},
]
_cal = {"disposal": [{"symbol": "2330", "until": "2026-10-15"}], "notice": [{"symbol": "2330"}],
        "exdiv": [{"symbol": "2330", "date": "2026-10-08"}, {"symbol": "2330", "date": "2026-09-01"}],
        "meetings": [{"symbol": "2330", "date": "2026-10-20"}]}
v = pm.card_event_view("2330", _ev, _cal, "2026-10-07")
check("事件時間軸：新→舊、只含該檔、近14天、重要度≥1", [e["subject"] for e in v["events"]] == ["9月自結合併營收", "法說會"], v)
check("事件時間軸：處置/注意/未來除權息/法說會旗標（過期除權息不列）", v["flags"] == ["⛔ 處置中至 10-15", "⚠️ 列注意股", "💰 10-08 除權息", "🎤 10-20 法說會"], v["flags"])
check("事件時間軸：沒事件回空", pm.card_event_view("9999", _ev, _cal, "2026-10-07") == {"flags": [], "events": []})
check("事件時間軸：壞日期不丟例外", isinstance(pm.card_event_view("2330", _ev, None, "bad"), dict))

# --- AI 回覆解析容錯（2026-10-08：17 檔候選時回覆被截斷／帶說明文字，整個 AI 摘要作廢）
_v = {"2330", "2317", "2454"}
_full = '{"overview":"x","items":[{"symbol":"2330","direction":1,"reason":"a","risk":"b"},{"symbol":"2317","direction":0,"reason":"c","risk":"d"}]}'
check("AI解析：標準 JSON", len(pm.parse_ai_json(_full, _v)["items"]) == 2)
check("AI解析：程式碼框＋後面有含大括號的說明文字", len(pm.parse_ai_json("```json\n" + _full + "\n```\n註 {說明}", _v)["items"]) == 2)
check("AI解析：思考段落 <think> 被剝掉", len(pm.parse_ai_json("<think>想 {x}</think>" + _full, _v)["items"]) == 2)
_tr = '{"overview":"x","items":[{"symbol":"2330","direction":1,"reason":"a","risk":"b"},{"symbol":"2317","direction":0,"reas'
_p = pm.parse_ai_json(_tr, _v)
check("AI解析：被截斷時救回已完整的 item", _p is not None and [i["symbol"] for i in _p["items"]] == ["2330"], _p)
check("AI解析：完全沒有完整 item 的截斷／非 JSON 回 None", pm.parse_ai_json('{"overview":"x","items":[{"symbol":"23', _v) is None and pm.parse_ai_json("沒有json", _v) is None)
check("AI解析：不合法代號被丟掉", pm.parse_ai_json('{"overview":"x","items":[{"symbol":"9999","direction":1}]}', _v)["items"] == [])

print("\n結果：", "全部通過" if not FAIL else f"失敗 {len(FAIL)} 項：{FAIL}")
raise SystemExit(1 if FAIL else 0)
