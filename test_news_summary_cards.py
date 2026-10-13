"""【2026-10-14】新聞內文摘要＋戰卡對照測試（離線）。"""
import sys
from unittest import mock
import premarket as pm
import premarket_stage as ps
import premarket_tab as pt

fails = []


def check(name, cond):
    print(("PASS: " if cond else "FAIL: ") + name)
    if not cond:
        fails.append(name)


rss = """<rss><channel>
<item><title>台積電法說前瞻</title><link>https://a.com/1</link><pubDate>Wed, 14 Oct 2026 01:00:00 +0000</pubDate>
<description><![CDATA[<p>短摘要</p>]]></description>
<content:encoded><![CDATA[<p>長內文：聯發科與鴻海同步受惠。</p>]]></content:encoded></item>
<item><title>無內文</title><link>https://a.com/2</link></item></channel></rss>"""
items = pm.parse_rss(rss)
check("content:encoded 優先於 description", items[0]["summary"] == "長內文：聯發科與鴻海同步受惠。")
check("沒有內文的新聞摘要為空字串", items[1]["summary"] == "")
d = "<item><title>t</title><link>https://a.com/3</link><description>只有描述</description></item>"
check("只有 description 時退而用它", pm.parse_rss(d)[0]["summary"] == "只有描述")
check("摘要長度上限 600", len(pm.parse_rss("<item><title>t</title><link>https://a.com/4</link><description>" + "字" * 900 + "</description></item>")[0]["summary"]) == 600)

n2c = {"聯發科": "2454", "鴻海": "2317", "台積電": "2330"}
check("標題抓不到、內文有公司名 → 抽得到（≥3字名稱）", pm.extract_symbols("法說前瞻", n2c, "聯發科與台積電受惠") == ["2454", "2330"])
check("標題與內文代號標記都抽", pm.extract_symbols("某公司(2330-TW)", None, "另見（2317）") == ["2330", "2317"])
check("不給內文時行為不變", pm.extract_symbols("台積電漲", n2c) == ["2330"])

with mock.patch.object(pm, "http_get", return_value=(rss, "ok")):
    got, _ = pm.fetch_news(None, [{"id": "x", "name": "X", "url": "u", "ai_ok": True}, {"id": "y", "name": "Y", "url": "u", "ai_ok": False}])
check("可送 AI 的來源保留摘要", any(i["source"] == "x" and i["summary"] for i in got))
check("僅連結來源不留摘要", all(i["summary"] == "" for i in got if i["source"] == "y"))

rows = ps.persist_news.__code__  # 確認函式存在
class SB:
    def __init__(self): self.rows = []
    def table(self, n): return self
    def upsert(self, r, **k): self.rows += r; return self
    def execute(self): return self
sb = SB()
ps.persist_news(sb, [{"url_hash": "h1", "source": "x", "title": "t", "url": "u", "ai_ok": True, "summary": "內文"},
                     {"url_hash": "h2", "source": "y", "title": "t", "url": "u", "ai_ok": False, "summary": "不該存"}], None)
check("ai_ok 來源寫入摘要", sb.rows[0]["summary"] == "內文")
check("僅連結來源不寫摘要", sb.rows[1]["summary"] is None)

p = {"symbol": "2330", "name": "台積電", "bias": "偏多", "own": False, "events": [], "card_signal": "觀察偏多", "card_score": 3.0, "card_align": "一致"}
sp = ps.slim_pick(p)
check("slim_pick 保留戰卡欄位", sp["card_signal"] == "觀察偏多" and sp["card_score"] == 3.0 and sp["card_align"] == "一致")
check("card_line 顯示一致", "與早盤方向一致" in pt.card_line(sp) and "3.0" in pt.card_line(sp))
check("card_line 相反要警示", "相反" in pt.card_line(dict(sp, card_align="相反")))
check("無戰卡回空字串", pt.card_line({"symbol": "1"}) == "")

tags = ps.news_card_tags([{"symbols": ["2330", "9999"]}, {"symbols": ["2330"]}], {"2330": {"signal": "轉弱", "score": -3, "date": "d"}})
check("新聞戰卡標籤只含有卡的股票、去重", list(tags) == ["2330"] and tags["2330"]["score"] == -3)

msgs = pm.format_messages({"date": "2026-10-14", "picks": [], "calendar": {}, "news_ai": [{"title": "台積電漲", "source": "中央社", "url": "u", "symbols": ["2330"]}],
                           "news_cards": {"2330": {"signal": "轉弱", "score": -3}}})
check("Telegram 新聞行附戰卡", any("2330 戰卡轉弱(-3)" in m for m in msgs))

if fails:
    print(f"\n{len(fails)} 項失敗")
    sys.exit(1)
print("\n全部通過")
