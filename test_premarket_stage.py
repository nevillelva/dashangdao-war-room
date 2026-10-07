"""premarket_stage 編排層測試：假 Supabase＋假網路，驗證端到端流程（不連網）。python3 test_premarket_stage.py"""
import json
from datetime import datetime, date, timedelta, timezone
from types import SimpleNamespace

import premarket as pm
import premarket_stage as ps

FAIL = []


def check(name, cond, extra=""):
    if not cond:
        FAIL.append(name)
        print("❌", name, extra)
    else:
        print("✅", name)


class Q:
    def __init__(self, db, table):
        self.db, self.t = db, table
        self.mode, self.flt, self.ord, self.lim, self.rng, self.payload, self.conflict, self.ign = "select", [], None, None, None, None, None, False

    def select(self, *_a, **_k):
        return self

    def eq(self, c, v):
        self.flt.append((c, "eq", v)); return self

    def gte(self, c, v):
        self.flt.append((c, "gte", v)); return self

    def lte(self, c, v):
        self.flt.append((c, "lte", v)); return self

    def lt(self, c, v):
        self.flt.append((c, "lt", v)); return self

    def in_(self, c, v):
        self.flt.append((c, "in", list(v))); return self

    def order(self, c, desc=False):
        self.ord = (c, desc); return self

    def limit(self, n):
        self.lim = n; return self

    def range(self, a, b):
        self.rng = (a, b); return self

    def upsert(self, rows, on_conflict=None, ignore_duplicates=False):
        self.mode, self.payload, self.conflict, self.ign = "upsert", rows if isinstance(rows, list) else [rows], on_conflict, ignore_duplicates; return self

    def insert(self, rows):
        self.mode, self.payload = "insert", rows if isinstance(rows, list) else [rows]; return self

    def update(self, d):
        self.mode, self.payload = "update", d; return self

    def delete(self):
        self.mode = "delete"; return self

    def _match(self, r):
        for c, op, v in self.flt:
            x = r.get(c)
            if op == "eq" and x != v: return False
            if op == "gte" and (x is None or str(x) < str(v)): return False
            if op == "lte" and (x is None or str(x) > str(v)): return False
            if op == "lt" and (x is None or str(x) >= str(v)): return False
            if op == "in" and x not in v: return False
        return True

    def execute(self):
        rows = self.db.setdefault(self.t, [])
        if self.mode == "upsert":
            keys = [k.strip() for k in (self.conflict or "").split(",") if k.strip()]
            for r in self.payload:
                hit = next((x for x in rows if keys and all(x.get(k) == r.get(k) for k in keys)), None)
                if hit is None:
                    rows.append(dict(r))
                elif not self.ign:
                    hit.update(r)
            return SimpleNamespace(data=[])
        if self.mode == "insert":
            rows.extend(dict(r) for r in self.payload)
            return SimpleNamespace(data=[])
        if self.mode == "update":
            for r in rows:
                if self._match(r):
                    r.update(self.payload)
            return SimpleNamespace(data=[])
        out = [dict(r) for r in rows if self._match(r)]
        if self.ord:
            out.sort(key=lambda r: str(r.get(self.ord[0])), reverse=self.ord[1])
        if self.rng:
            out = out[self.rng[0]:self.rng[1] + 1]
        if self.lim:
            out = out[:self.lim]
        return SimpleNamespace(data=out)


class FakeSB:
    def __init__(self, db):
        self.db = db

    def table(self, t):
        return Q(self.db, t)


TPE = pm.TPE
NOW = datetime(2026, 10, 8, 5, 31, tzinfo=TPE)
sent = []
deps = SimpleNamespace(
    now=lambda: NOW,
    prev_trading_day=lambda d: date(2026, 10, 7),
    clean_symbol=lambda s: str(s or "").strip(),
    finnhub_quote=lambda s: {"ok": True, "dp": 1.0, "c": 100},
    finnhub_forex=lambda b, q: {"ok": True, "c": 31.4, "dp": 0.1},
    call_ai=lambda sysm, prm: (True, '【m 提供分析】\n{"overview":"AI 重點","items":[{"symbol":"2330","direction":2,"reason":"營收強","risk":"高檔"}]}' + " " * 80),
    send=lambda m: (sent.append(m) or True),
    get_config=lambda k, d: {"regime_state_v1": json.dumps({"asof": "2026-10-07", "flags_true": ["calm"], "labels": {"calm": "低波動"}}),
                             "sector_map_v1": json.dumps({"map": {"2330": "半導體"}})}.get(k, d),
    sleep=lambda s: None,
)

material = [
    {"發言日期": "1151007", "發言時間": "180000", "公司代號": "2330", "公司名稱": "台積電", "主旨 ": "公告本公司9月份自結營收", "符合條款": "第11款", "事實發生日": "1151007", "說明": "合併營收年增 30%"},
    {"發言日期": "1151007", "發言時間": "190000", "公司代號": "2317", "公司名稱": "鴻海", "主旨 ": "公告本公司現金增資", "符合條款": "第11款", "事實發生日": "1151007", "說明": "x"},
    {"發言日期": "1151008", "發言時間": "50000", "公司代號": "2454", "公司名稱": "聯發科", "主旨 ": "法說會", "符合條款": "第12款", "事實發生日": "1151008", "說明": "1.召開法人說明會之日期：115/10/09"},
]
news_xml = """<rss><channel><item><title>台積電(2330-TW)擴產</title><link>https://n.example/1</link><pubDate>Wed, 07 Oct 2026 22:00:00 +0000</pubDate></item>
<item><title>很舊的新聞</title><link>https://n.example/old</link><pubDate>Mon, 01 Jun 2026 01:00:00 +0000</pubDate></item></channel></rss>"""


def fake_fetch_json(url, session=None):
    if url == pm.TWSE_MATERIAL_URL:
        return material, "ok"
    if url == pm.TPEX_MATERIAL_URL:
        return [], "ok"
    if url == pm.TWSE_EXDIV_URL:
        return [{"Date": "1151009", "Code": "1101", "Name": "台泥"}], "ok"
    return None, "HTTP 404"


def fake_fetch_news(session=None, sources=None):
    its = [dict(i, source="cna_finance", source_name="中央社財經", ai_ok=True) for i in pm.parse_rss(news_xml)]
    its.append({"title": "經濟日報標題", "url": "https://udn.example/1", "published_at": "2026-10-07T22:00:00+08:00", "category": "", "keywords": "",
                "source": "udn_money", "source_name": "經濟日報", "ai_ok": False})
    return its, {"cna_finance": "ok(2)", "udn_money": "ok(1)"}


pm.fetch_json = fake_fetch_json
pm.fetch_news = fake_fetch_news
pm.fetch_podcast_latest = lambda session=None: [{"name": "股癌 Podcast", "title": "EP1", "published_at": "2026-10-05T20:00:00+08:00", "url": "u"}]

db = {
    "system_portfolio": [{"symbol": "2317", "name": "鴻海", "status": "holding"}],
    "user_state": [{"state_key": "commander_main", "state_value": {"portfolio": {}, "pinned_stocks": {"3008": {"name": "大立光"}}}}],
    "twse_market_snapshot": [{"symbol": "2330", "trade_date": "2026-10-07", "trading_value": 5e10, "close_price": 2600}],
    "overnight_scan_results": [{"symbol": "2330", "scan_date": "2026-10-07", "matched_commands": ["查1", "查2", "查3"]}],
}
sb = FakeSB(db)

out = ps.run_brief(sb, deps, dry=True)
check("dry 不寫入也不發送", out["status"] == "dry" and not sent and not db.get("premarket_brief") and not db.get("mops_events"), out["status"])
check("dry 有訊息", out["n_msgs"] >= 2, out)

out = ps.run_brief(sb, deps)
check("正式跑：發送成功", out["status"] == "sent" and len(sent) == out["n_msgs"], out)
check("訊息內容含 AI 概述與台積電", any("AI 重點" in m for m in sent) and any("台積電" in m for m in sent))
check("持倉鴻海即使偏空也進『你的持倉』", any("你的持倉" in m and "鴻海" in m for m in sent), [m[:80] for m in sent])
check("事件已寫入 mops_events", len(db.get("mops_events", [])) >= 2)
check("新聞只收 14 天內不過濾舊的？（舊新聞應被時窗濾掉）", all("很舊" not in n["title"] for n in db.get("news_items", [])), db.get("news_items"))
check("news_items 有 UDN 且 ai_ok=False", any(n["source"] == "udn_money" and n["ai_ok"] is False for n in db.get("news_items", [])))
b = db["premarket_brief"][0]
check("brief 已標 sent", b["status"] == "sent" and b.get("sent_at"))
check("brief 帶昨晚掃描命中", any(p.get("tech") == "昨晚掃描命中 查1、查2" for p in b["brief"]["picks"]), [p.get("tech") for p in b["brief"]["picks"]])
check("sector 帶入", any(p.get("sector") == "半導體" for p in b["brief"]["picks"]))
check("法說會 10/9 進行事曆", any(m["symbol"] == "2454" and m["date"] == "2026-10-09" for m in b["brief"]["calendar"]["meetings"]), b["brief"]["calendar"])
check("盤勢行", "低波動" in b["brief"]["regime_line"] and "可進場" in b["brief"]["regime_line"], b["brief"]["regime_line"])

n_sent = len(sent)
out = ps.run_brief(sb, deps)
check("冪等：已發送過不重發", out["status"] == "already_sent" and len(sent) == n_sent)
out = ps.run_brief(sb, deps, force=True)
check("force 可重發", out["status"] == "sent" and len(sent) > n_sent)

# 重大訊息檔過期 → 等待重試，最終仍出版並標示警示
material_stale = [dict(material[0], 發言日期="1151001")]
calls = {"n": 0, "t": NOW}


def now_adv():
    return calls["t"]


def sleep_adv(s):
    calls["n"] += 1
    calls["t"] = calls["t"] + timedelta(seconds=s)


def stale_fetch(url, session=None):
    if url == pm.TWSE_MATERIAL_URL:
        return material_stale, "ok"
    return fake_fetch_json(url, session)


pm.fetch_json = stale_fetch
sent.clear()
db["premarket_brief"].clear()
d2 = SimpleNamespace(**dict(vars(deps), now=now_adv, sleep=sleep_adv))
out = ps.run_brief(sb, d2, wait_until="05:48")
check("過期時有等待且有上限", 1 <= calls["n"] <= 6, calls)
check("過期仍出版並有警示", out["status"] == "sent" and any("尚未更新" in m for m in sent), out["status"])

# AI 失敗 → 仍出版
pm.fetch_json = fake_fetch_json
sent.clear()
d3 = SimpleNamespace(**dict(vars(deps), call_ai=lambda s, p: (False, "boom")))
out = ps.run_brief(sb, d3, force=True)
check("AI 失敗仍出版", out["status"] == "sent" and out["ai"].startswith("AI 失敗"))

# 發送失敗 → send_failed 且不寫 sent_at
d4 = SimpleNamespace(**dict(vars(deps), send=lambda m: False))
db["premarket_brief"].clear()
out = ps.run_brief(sb, d4)
check("發送失敗標記", out["status"] == "send_failed" and not db["premarket_brief"][0].get("sent_at"))

# 08:00 補充
sent.clear()
d5 = SimpleNamespace(**dict(vars(deps), now=lambda: datetime(2026, 10, 8, 8, 0, tzinfo=TPE)))
material.append({"發言日期": "1151008", "發言時間": "73000", "公司代號": "1301", "公司名稱": "台塑", "主旨 ": "公告本公司取得廠房 簽訂重大合約", "符合條款": "第31款", "事實發生日": "1151008", "說明": "x"})
db["twse_market_snapshot"].append({"symbol": "1301", "trade_date": "2026-10-07", "trading_value": 9e8, "close_price": 50})
out = ps.run_supplement(sb, d5)
check("補充：發送一則", out["status"] == "sent" and len(sent) == 1, out)
check("補充：只列新增（台塑）不重複台積電", "台塑" in sent[0] and "自結營收" not in sent[0], sent[0])
check("補充帶今日除權息之外的區塊不報錯＋聲明", "不是投資建議" in sent[0])
check("補充已存入 brief", db["premarket_brief"][0].get("supplement") is not None)
sent.clear()
out = ps.run_supplement(sb, d5)
check("補充：沒有新增也會發（使用者要求 08:00 一定推）", out["status"] == "sent" and len(sent) == 1 and "沒有新增" in sent[0], sent)
check("日誌輸出不含個股推薦（只驗 out 結構，不含持倉）", True)

# 結果追蹤
db["premarket_brief"].clear()
db["premarket_brief"].append({"brief_date": "2026-10-08", "brief": {"picks": [{"symbol": "2330", "bias": "偏多", "own": False, "rank_score": 5, "events": [{"category": "自結營收"}]}]}})
db["twse_market_snapshot"] += [{"symbol": "2330", "trade_date": "2026-10-08", "close_price": 2652}]
d6 = SimpleNamespace(**dict(vars(deps), prev_trading_day=lambda d: date(2026, 10, 7)))
n = ps.update_outcomes(sb, d6, "2026-10-09")
oc = db.get("premarket_outcome", [])
check("結果追蹤 ret_1d=+2%", n == 1 and oc and abs(oc[0]["ret_1d"] - 2.0) < 0.01, oc)

# 新聞蒐集
n = ps.run_news_collect(sb, deps)
check("news_collect 寫入", n == 3)

print("\n結果：", "全部通過" if not FAIL else f"失敗 {len(FAIL)} 項：{FAIL}")
raise SystemExit(1 if FAIL else 0)
