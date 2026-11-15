"""【2026-11-15】影子記錄接線（system_scheduler）：notify_telegram／_notify_quiet 都會記；stage_tg_replay／stage_mops_gap_measure 只印數量（離線）。"""
import io
import json
import os
import sys
from contextlib import redirect_stdout
from datetime import datetime

import system_scheduler as S

ok = True


def chk(n, c, extra=None):
    global ok
    print(("✅ " if c else "❌ ") + n + ("" if c else f"  → {extra}"))
    ok = ok and c


class Q:
    def __init__(self, db, name):
        self.db, self.name, self.f, self.op, self.pl, self.like_ = db, name, {}, "sel", None, None

    def select(self, *a, **k):
        self.op = "sel"
        return self

    def eq(self, k, v):
        self.f[k] = v
        return self

    def like(self, k, v):
        self.like_ = v.rstrip("%")
        return self

    def gte(self, *a):
        return self

    def order(self, *a, **k):
        return self

    def limit(self, *a):
        return self

    def range(self, a, b):
        return self

    def delete(self):
        self.op = "del"
        return self

    def insert(self, row):
        self.op, self.pl = "ins", row
        return self

    def upsert(self, row, **k):
        self.op, self.pl = "ups", row
        return self

    def execute(self):
        t = self.db.setdefault(self.name, [])
        if self.op in ("ins", "ups"):
            t.append(self.pl)
            return type("R", (), {"data": []})()
        if self.op == "del":
            self.db[self.name] = [r for r in t if not all(r.get(k) == v for k, v in self.f.items())]
            return type("R", (), {"data": []})()
        rows = [r for r in t if all(r.get(k) == v for k, v in self.f.items())]
        if self.like_ is not None:
            rows = [r for r in rows if str(r.get("summary", "")).startswith(self.like_)]
        return type("R", (), {"data": rows})()


class DB(dict):
    def table(self, n):
        return Q(self, n)


db = DB()
S._TG_SB[0] = db
S.get_config = lambda sb, k, d: d
S.set_config = lambda *a, **k: None
sent = []
S._tg_send_telegram = lambda m: (sent.append(m) or (True, [{"ok": True}]))
os.environ["TELEGRAM_BOT_TOKEN"], os.environ["TELEGRAM_CHAT_ID"] = "dummy", "dummy"

with redirect_stdout(io.StringIO()):
    S.notify_telegram("🎯 13:18 尾盤定版戰卡 買 2330")
    S.notify_telegram("✅ 券商分點歷史回補完成：token=SHOULDNOTAPPEAR")
    S._notify_quiet("🧭 12:33 尾盤預備戰卡")
rows = [r["report"] for r in db["ui_selftest_reports"] if r["summary"].startswith("tg_shadow:")]
chk("三則都有影子記錄", len(rows) == 3, rows)
chk("推播：sent／分類 push", rows[0]["route"] == "sent" and rows[0]["tier"] == "push", rows[0])
chk("降噪：digest 走向且沒有真的送出", rows[1]["route"] == "digest" and rows[1]["tier"] == "digest" and len(sent) == 2, (rows[1], sent))
chk("影子記錄不含機敏", "SHOULDNOTAPPEAR" not in json.dumps(rows, ensure_ascii=False))
chk("直送路徑記 direct 並附分類", rows[2]["route"] == "direct" and rows[2]["tier"] == "push", rows[2])
chk("每則都有前 80 字、原因", all(0 < len(r["m"]) <= 80 and r["why"] for r in rows), rows)

# stage_tg_replay：只印數量
logs = []
S._log_stage_run = lambda *a, **k: logs.append((a, k))
buf = io.StringIO()
with redirect_stdout(buf):
    S.stage_tg_replay(db)
out = buf.getvalue()
saved = [r for r in db["ui_selftest_reports"] if r["summary"] == "tg_replay"]
chk("stage_tg_replay 寫私有表 tg_replay", len(saved) == 1 and saved[0]["report"]["n"] >= 3, saved)
chk("公開日誌只印數量（不含訊息內容）", "回放" in out and "尾盤定版戰卡" not in out and "2330" not in out, out)
chk("system_run_log 有一筆", any(x[0][1] == "tg_replay" and x[1].get("gate_status") == "normal" for x in logs), logs)

# stage_mops_gap_measure：只印數量
S._prev_trading_day = lambda d: datetime(2026, 11, 12).date()
db["mops_events"] = [{"ev_date": "2026-11-12", "ev_time": "10:00:00", "symbol": "2330", "category": "c", "importance": 3}]
db["system_config"] = [{"config_key": "tail_card:2026-11-12:final", "config_value": json.dumps({"cards": [{"symbol": "2330"}], "holds": []})}]
logs.clear()
buf = io.StringIO()
with redirect_stdout(buf):
    S.stage_mops_gap_measure(db)
out = buf.getvalue()
saved = [r for r in db["ui_selftest_reports"] if r["summary"] == "mops_gap_daily:2026-11-12"]
chk("stage_mops_gap_measure 寫私有表", len(saved) == 1 and saved[0]["report"]["intraday_cand"] == 1, saved)
chk("公開日誌只印數量（不含代號）", "盤中重訊 1 則" in out and "2330" not in out, out)
chk("system_run_log 有一筆 normal", any(x[0][1] == "mops_gap_measure" and x[1].get("gate_status") == "normal" for x in logs), logs)
chk("兩個新階段已註冊", "mops_gap_measure" in S.BUNDLE_STAGES["bundle_evening"], S.BUNDLE_STAGES["bundle_evening"])
sys.exit(0 if ok else 1)
