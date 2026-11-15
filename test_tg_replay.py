"""【2026-11-15】Telegram 分流回放（離線、唯讀）：三類則數、疑似誤殺清單、規則漂移。"""
import json
import sys
from datetime import date

import tg_replay as R

ok = True


def chk(n, c, extra=None):
    global ok
    print(("✅ " if c else "❌ ") + n + ("" if c else f"  → {extra}"))
    ok = ok and c


items = [
    {"m": "🎯 13:18 尾盤定版戰卡 買 2330", "src": "tg_shadow", "route": "sent"},
    {"m": "⚠️ [d] 候選池寫入Supabase失敗", "src": "tg_shadow", "route": "sent"},
    {"m": "🩺 [d] 資料源異常警報", "src": "tg_digest", "route": "digest"},
    {"m": "✅ 券商分點歷史回補完成：…", "src": "tg_digest", "route": "digest"},
    {"m": "⏭️ [d] 非交易日，今日不進場", "src": "tg_digest", "route": "digest"},        # 明確降噪（設計）→ 不算誤殺
    {"m": "ℹ️ 偵測到 2330 停損價 xxx", "src": "tg_digest", "route": "digest"},            # ℹ️ 資訊含交易字 → 疑似
    {"m": "✅ " + "資料更新完成，" * 40 + "附註：2330 觸發強制出場", "src": "tg_digest", "route": "digest"},  # 交易字在 200 字之後 → 疑似
    {"m": "某新功能訊息", "src": "tg_shadow", "route": "digest"},                            # 當時降噪、現在 push → 漂移
    {"m": "🎯 門檻敏感度掃描完成", "src": "tg_shadow", "route": "sent"},                     # 當時送出、現在 digest → 漂移
    {"m": "📋 預備戰卡", "src": "tg_shadow", "route": "direct"},
]
res = R.replay(items)
c = res["counts"]
chk("三類則數合計＝總則數", res["n"] == 10 and sum(c.values()) == 10, c)
chk("push／alert／digest 則數", (c["push"], c["alert"], c["digest"]) == (3, 2, 5), c)
chk("by_src 分來源統計", sum(res["by_src"]["tg_digest"].values()) == 5 and sum(res["by_src"]["tg_shadow"].values()) == 5, res["by_src"])
chk("明確降噪（非交易日）不列疑似誤殺", all("非交易日" not in s["m"] for s in res["suspects"]), res["suspects"])
chk("ℹ️ 含交易字列為疑似", any(s["m"].startswith("ℹ️") and s["key"] == "停損" for s in res["suspects"]), res["suspects"])
chk("交易字在 200 字外的 digest 列為疑似", any("強制出場" in s["m"] or s["key"] == "強制出場" for s in res["suspects"]), res["suspects"])
chk("疑似誤殺 2 則", len(res["suspects"]) == 2, res["suspects"])
kinds = sorted(d["kind"] for d in res["drift"])
chk("規則漂移：當時送出現歸 digest／當時降噪現歸 push", kinds == ["當時送出、現行歸digest", "當時降噪、現行歸push"], kinds)
chk("空輸入不爆", R.replay([])["n"] == 0 and R.replay(None)["counts"] == {"push": 0, "alert": 0, "digest": 0})
line = R.public_line(res, {"digest_days": 3, "shadow_days": 1})
chk("公開行只有數量不含訊息內容", "回放 10 則" in line and "疑似誤殺 2 則" in line and "2330" not in line, line)


class Q:
    def __init__(self, db, name):
        self.db, self.name, self.f, self.like_, self.gte_, self.rg = db, name, {}, None, None, None

    def select(self, *a, **k):
        return self

    def eq(self, k, v):
        self.f[k] = v
        return self

    def like(self, k, v):
        self.like_ = v.rstrip("%")
        return self

    def gte(self, k, v):
        self.gte_ = v
        return self

    def order(self, *a, **k):
        return self

    def limit(self, *a):
        return self

    def range(self, a, b):
        self.rg = (a, b)
        return self

    def execute(self):
        rows = self.db[self.name]
        if self.name == "system_config":
            rows = [r for r in rows if r["config_key"] == self.f.get("config_key")]
        else:
            rows = [r for r in rows if r["summary"].startswith(self.like_) and (self.gte_ is None or r["summary"] >= self.gte_)]
        if self.rg:
            rows = rows[self.rg[0]:self.rg[1] + 1]
        return type("R", (), {"data": rows})()


class DB(dict):
    def table(self, n):
        return Q(self, n)


db = DB(system_config=[{"config_key": "tg_digest:2026-11-12", "config_value": json.dumps([{"t": "09:00", "why": "摘要", "m": "✅ 券商分點歷史回補完成"}, {"t": "10:00", "why": "摘要", "m": "ℹ️ 偵測到 2330 停損"}])},
                       {"config_key": "tg_digest:2026-09-01", "config_value": json.dumps([{"m": "太舊不讀"}])}],
        ui_selftest_reports=[{"summary": "tg_shadow:2026-11-13", "report": {"m": "🎯 尾盤定版戰卡", "route": "sent", "tier": "push"}},
                             {"summary": "tg_shadow:2026-09-01", "report": {"m": "太舊", "route": "sent"}},
                             {"summary": "factor_card_shadow:2026-11-13", "report": {"x": 1}}])
its, cov = R.load_from_db(db, 30, today=date(2026, 11, 13))
chk("讀近 30 天 tg_digest＋影子記錄，舊的與無關的不讀", len(its) == 3 and cov["digest_days"] == 1 and cov["shadow_days"] == 1, (its, cov))
chk("涵蓋說明誠實註明影子記錄前無留底", "沒有留底" in cov["note"])
rr = R.replay(its)
chk("端到端回放", rr["n"] == 3 and rr["counts"]["push"] == 1 and len(rr["suspects"]) == 1, rr)
# 唯讀：回放過程不呼叫任何寫入方法
class RO(DB):
    def table(self, n):
        q = Q(self, n)
        for w in ("insert", "upsert", "delete", "update"):
            setattr(q, w, lambda *a, **k: (_ for _ in ()).throw(AssertionError("回放不得寫入")))
        return q


R.load_from_db(RO(system_config=[], ui_selftest_reports=[]), 30, today=date(2026, 11, 13))
chk("load_from_db 唯讀", True)
sys.exit(0 if ok else 1)
