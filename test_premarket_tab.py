"""premarket_tab 測試：純函式 + 用 Streamlit AppTest 以假資料渲染整個分頁（不連網）。"""
import premarket_tab as T

FAIL = []


def check(name, cond, extra=""):
    if not cond:
        FAIL.append(name)
        print("❌", name, extra)
    else:
        print("✅", name)


rows = [{"direction": 1, "ret_1d": 2.0, "ret_5d": 3.0, "category": "自結營收"},
        {"direction": 1, "ret_1d": -1.0, "ret_5d": None, "category": "自結營收"},
        {"direction": -1, "ret_1d": -2.0, "ret_5d": -1.0, "category": "籌資稀釋"},
        {"direction": -1, "ret_1d": 1.0, "ret_5d": None, "category": "籌資稀釋"},
        {"direction": -1, "ret_1d": None, "ret_5d": None, "category": "籌資稀釋"}]
st_ = {x["方向"]: x for x in T.outcome_stats(rows)}
check("偏多 1日命中 50%", st_["偏多"]["樣本數"] == 2 and st_["偏多"]["1日命中率%"] == 50.0 and st_["偏多"]["1日平均報酬%"] == 0.5, st_)
check("偏多 5日只算有值的", st_["偏多"]["5日樣本"] == 1 and st_["偏多"]["5日命中率%"] == 100.0)
check("偏空：下跌算命中", st_["偏空"]["樣本數"] == 2 and st_["偏空"]["1日命中率%"] == 50.0)
check("空資料", T.outcome_stats([]) == [] and T.category_stats([]) == [])
cs = T.category_stats(rows)
check("類別統計依方向", any(c["事件類別"] == "籌資稀釋" and c["樣本數"] == 2 for c in cs), cs)
picks = [{"symbol": "1", "bias": "偏多"}, {"symbol": "2", "bias": "偏空", "own": True}, {"symbol": "3", "bias": "中性"}]
check("篩選", [p["symbol"] for p in T.filter_picks(picks, "偏多")] == ["1"] and [p["symbol"] for p in T.filter_picks(picks, "我的持倉／雷達")] == ["2"] and len(T.filter_picks(picks, "全部")) == 3)


def app():
    import streamlit as st
    import premarket_tab as T2
    from types import SimpleNamespace

    class Q:
        def __init__(self, t): self.t = t
        def select(self, *a, **k): return self
        def eq(self, *a): return self
        def gte(self, *a): return self
        def lte(self, *a): return self
        def order(self, *a, **k): return self
        def limit(self, *a): return self
        def range(self, a, b): self.off = a; return self
        def execute(self):
            D = {
                "premarket_brief": [{"brief_date": "2026-10-08", "status": "sent", "sent_at": "x", "supplement": {"generated_hm": "08:00", "picks": [], "own_flags": ["鴻海(2317) 處置中至10/14"], "news": []},
                                     "brief": {"generated_hm": "05:31", "us_line": "費半 +1.0%", "regime_line": "盤勢：低波動", "ai_overview": "測試概述", "ai_status": "AI ok(1)",
                                               "source_status": {"material_stale": True, "material_latest": "2026-10-05", "twse": "ok", "tpex": "ok", "news": {}},
                                               "picks": [{"symbol": "2330", "name": "台積電", "bias": "偏多", "own": False, "sector": "半導體", "why": "營收強", "risk": "高檔", "tech": "昨晚掃描命中 查1",
                                                          "events": [{"category": "自結營收", "subject": "公告9月營收", "ev_time": "18:00:00", "growth_pct": 30.0, "flags": ["測試旗標"]}]}],
                                               "calendar": {"meetings": [{"name": "聯發科", "symbol": "2454", "date": "2026-10-09"}], "exdiv": [], "own_flags": []},
                                               "podcast": [{"name": "股癌", "title": "EP1", "url": "https://p", "published_at": "2026-10-05T20:00:00+08:00"}]}}],
                "mops_events": [{"ev_date": "2026-10-07", "ev_time": "18:00:00", "market": "L", "symbol": "2330", "name": "台積電", "category": "自結營收", "direction": 2, "importance": 4, "subject": "公告9月營收", "event_date": None}],
                "news_items": [{"source": "cna_finance", "title": "新聞標題", "url": "https://n", "published_at": "2026-10-07T22:00:00+08:00", "symbols": ["2330"], "ai_ok": True},
                               {"source": "udn_money", "title": "連結標題", "url": "https://u", "published_at": "2026-10-07T21:00:00+08:00", "symbols": [], "ai_ok": False}],
                "premarket_outcome": [{"brief_date": "2026-10-07", "symbol": "2330", "category": "自結營收", "direction": 1, "ret_1d": 2.0, "ret_5d": None}],
            }
            data = D.get(self.t, [])
            return SimpleNamespace(data=data if getattr(self, "off", 0) == 0 else [])

    class SB:
        def table(self, t): return Q(t)

    T2.render_premarket_tab(SB())


from streamlit.testing.v1 import AppTest

at = AppTest.from_function(app, default_timeout=30).run()
check("渲染無例外", not at.exception, [e.value for e in at.exception])
check("無 st.error", not at.error, [e.value for e in at.error])
txt = " ".join([m.value for m in at.markdown] + [m.value for m in at.info] + [m.value for m in at.warning] + [c.value for c in at.caption])
check("標題與概述", at.title[0].value == "📰 早盤情報" and "測試概述" in txt)
check("過期警示", "尚未更新" in txt)
check("個股卡片", "台積電" in txt and "營收強" in txt and "測試旗標" in txt)
check("08:00 補充區", "08:00 補充" in txt and "處置中" in txt)
check("新聞連結（含僅連結標記）", "連結標題" in txt and "僅連結" in txt)
check("法說會", "聯發科2454" in txt)
check("6 個分頁", len(at.tabs) == 6, len(at.tabs))
print("\n結果：", "全部通過" if not FAIL else f"失敗 {len(FAIL)} 項：{FAIL}")
raise SystemExit(1 if FAIL else 0)
