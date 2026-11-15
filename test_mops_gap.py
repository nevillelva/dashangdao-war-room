"""【2026-11-15】盤中重訊缺口：重訊資料時效、新聞轉述提醒、戰卡／推演顯示（離線）。"""
import sys
import json
from datetime import datetime, timezone, timedelta

import mops_gap as MG
import strategy_view as SV
import tail_card as TK
import premarket as pm

ok = True


def chk(n, c, extra=None):
    global ok
    print(("✅ " if c else "❌ ") + n + ("" if c else f"  → {extra}"))
    ok = ok and c


TZ = MG.TAIPEI_TZ
NOW = datetime(2026, 11, 13, 12, 33, tzinfo=TZ)
# ① HTTP Last-Modified → 台北時間
dt = MG.parse_http_date("Fri, 13 Nov 2026 03:58:12 GMT")
chk("Last-Modified 轉台北時間", dt is not None and dt.strftime("%Y-%m-%d %H:%M") == "2026-11-13 11:58", dt)
chk("壞日期回 None", MG.parse_http_date("not a date") is None and MG.parse_http_date("") is None)
# 檔內最新發言
rows = [{"發言日期": "1151113", "發言時間": "93000", "公司代號": "2330"}, {"發言日期": "1151113", "發言時間": "110512", "公司代號": "1101"},
        {"發言日期": "1151112", "發言時間": "235959", "公司代號": "2317"}, {"發言日期": "bad", "發言時間": "1"}]
ls = MG.latest_speech(rows, pm.roc_to_iso, pm.fmt_time, pm._g)
chk("檔內最新發言＝11:05", ls is not None and ls.strftime("%Y-%m-%d %H:%M") == "2026-11-13 11:05", ls)
chk("空檔回 None", MG.latest_speech([], pm.roc_to_iso, pm.fmt_time, pm._g) is None)
# 合併：優先官方檔產生時間
a = MG.build_asof(dt, ls, NOW)
chk("優先官方檔產生時間", a["source"] == MG.SRC_HEADER and a["at"] == "2026-11-13 11:58", a)
a2 = MG.build_asof(None, ls, NOW)
chk("退而用檔內最新發言並標來源", a2["source"] == MG.SRC_ROWS and a2["at"].endswith("11:05"), a2)
chk("兩者皆無回 None", MG.build_asof(None, None, NOW) is None)
# 顯示文字
t1 = MG.asof_text(a, today="2026-11-13")
chk("顯示『重訊資料截至 11:58』", "重訊資料截至 11:58" in t1 and "不等於沒有重訊" in t1, t1)
chk("非今日資料帶日期", "重訊資料截至 11/12 " in MG.asof_text({"at": "2026-11-12 18:30", "source": MG.SRC_HEADER}, today="2026-11-13"))
chk("來源非官方檔時標註", "檔內最新發言時間" in MG.asof_text(a2, today="2026-11-13"))
t0 = MG.asof_text(None)
chk("無時效不默默當成無重訊", "時間不明" in t0 and "請勿視為沒有重訊" in t0, t0)


# 時效存取（假資料庫）
class Q:
    def __init__(self, db, name):
        self.db, self.name, self.f, self.op, self.pl = db, name, {}, None, None

    def select(self, *_a, **_k):
        self.op = "sel"
        return self

    def eq(self, k, v):
        self.f[k] = v
        return self

    def limit(self, *_a):
        return self

    def upsert(self, row, **_k):
        self.op, self.pl = "ups", row
        return self

    def execute(self):
        t = self.db.setdefault(self.name, {})
        if self.op == "ups":
            t[self.pl["config_key"]] = self.pl["config_value"]
            return type("R", (), {"data": []})()
        k = self.f.get("config_key")
        return type("R", (), {"data": [{"config_value": t[k]}] if k in t else []})()


class DB(dict):
    def table(self, n):
        return Q(self, n)


db = DB()
chk("存時效成功", MG.save_asof(db, "2026-11-13", a))
chk("讀回時效", MG.load_asof(db, "2026-11-13") == a)
chk("沒有紀錄讀回 None", MG.load_asof(db, "2026-11-12") is None)


class Sess:
    def __init__(self, lm):
        self.lm = lm

    def head(self, url, **k):
        return type("R", (), {"status_code": 200, "headers": {"Last-Modified": self.lm} if self.lm else {}})()


asof, txt = MG.asof_for_display(DB(), "2026-11-13", Sess("Fri, 13 Nov 2026 03:00:00 GMT"), NOW)
chk("無紀錄時補 HEAD 並顯示", asof and "重訊資料截至 11:00" in txt, txt)
asof, txt = MG.asof_for_display(DB(), "2026-11-13", Sess(None), NOW)
chk("HEAD 也沒給 → 時間不明", asof is None and "時間不明" in txt, txt)
asof, txt = MG.asof_for_display(DB(), "2026-11-13", None, NOW)
chk("無 session 無紀錄 → 時間不明", "時間不明" in txt)

# ② 新聞轉述
news = [
    {"title": "台積電公告取得廠房，股價走揚", "source": "鉅亨", "published_at": "2026-11-13T02:10:00+00:00", "symbols": ["2330"]},
    {"title": "華新重訊：子公司訂單", "source": "經濟日報", "published_at": "2026-11-13T03:00:00Z", "symbols": ["1605", "2330"]},
    {"title": "盤勢分析：電子股強", "source": "x", "published_at": "2026-11-13T03:00:00Z", "symbols": ["2330"]},   # 不含公告／重訊
    {"title": "聯發科公告法說會", "source": "x", "published_at": "2026-11-13T03:00:00Z", "symbols": ["2454"]},     # 不在候選
    {"title": "台積電公告取得廠房，股價走揚", "source": "他報", "published_at": "2026-11-13T02:11:00+00:00", "symbols": ["2330"]},  # 重複標題
]
h = MG.relay_hits(news, ["2330", "1605"])
chk("只收含公告／重訊且在候選者", set(h) == {"2330", "1605"} and len(h["2330"]) == 2 and len(h["1605"]) == 1, h)
chk("同標題去重", len([x for x in h["2330"] if x["title"].startswith("台積電公告")]) == 1)
ln = MG.relay_lines(h, lambda s: {"2330": "台積電(2330)"}.get(s, s))
chk("轉述行含標示與代號名稱", any(MG.RELAY_TAG in x for x in ln[1:]) and any("台積電(2330)" in x for x in ln) and "不參與買賣判定" in ln[0], ln)
chk("無命中回空 list", MG.relay_lines({}) == [] and MG.relay_hits([], ["2330"]) == {})


class DBN:
    def __init__(self, rows, boom=False):
        self.rows, self.boom = rows, boom

    def table(self, n):
        return self

    def select(self, *a):
        return self

    def overlaps(self, *a, **k):
        if self.boom:
            raise RuntimeError("x")
        return self

    gte = order = limit = overlaps

    def execute(self):
        return type("R", (), {"data": self.rows})()


chk("load_relay 讀庫命中", "2330" in MG.load_relay(DBN(news), ["2330"], NOW))
chk("load_relay 失敗回空（不影響戰卡）", MG.load_relay(DBN(news, boom=True), ["2330"], NOW) == {})
chk("load_relay 無代號回空", MG.load_relay(DBN(news), [], NOW) == {})

# ③ 戰卡與推演顯示
cards = [{"symbol": "2330", "decision": "buy", "source": "主戰清單", "basis": "x", "entry": "e", "next_day": "n", "why": []}]
msg = TK.message("2026-11-13", "prep", cards, [], {"2330": "台積電"}, mops_asof=t1, relay=ln)
chk("預備戰卡顯示重訊資料截至", "🕒 重訊資料截至 11:58" in msg, msg)
chk("預備戰卡顯示新聞轉述提醒", "新聞轉述，未經官方驗證" in msg)
msg0 = TK.message("2026-11-13", "prep", cards, [], {})
chk("舊呼叫方式（無新參數）不變", "重訊資料截至" not in msg0 and "新聞轉述" not in msg0)
smsg = TK.short_message("2026-11-13", [], {}, mops_asof=t1, relay=ln)
chk("做空開盤確認也顯示時效與轉述", "重訊資料截至 11:58" in smsg and "新聞轉述，未經官方驗證" in smsg)
ai = {"stance": "可買", "thesis": "t", "can_buy": "c", "cannot_buy": "n", "key_level": "k", "plan": "p", "mops_asof": t1}
lines = SV.lines_for({"decision": "buy"}, ai)
chk("推演結果顯示重訊資料截至", any("重訊資料截至 11:58" in x for x in lines), lines)
chk("推演無時效欄位時不多出該行", not any("🕒" in x for x in SV.lines_for({"decision": "buy"}, {k: v for k, v in ai.items() if k != "mops_asof"})))
pk = SV.build_pack({"symbol": "2330"}, news={"news": [], "mops": [], "mops_asof": t1})
chk("證據包查無時仍帶時效", "重訊資料截至 11:58" in pk and "不代表沒有" in pk, pk)
pk2 = SV.build_pack({"symbol": "2330"}, news={"news": [], "mops": [{"date": "2026-11-13", "category": "c", "subject": "s"}], "mops_asof": t1})
chk("證據包有重訊也帶時效", "重訊資料截至 11:58" in pk2)
chk("轉述不進 AI 證據包", "轉述" not in pk and "轉述" not in pk2)
sys.exit(0 if ok else 1)
