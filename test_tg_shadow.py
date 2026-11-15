"""【2026-11-15】Telegram 影子記錄：分類、原因、前 80 字；不含機敏；失敗不影響推播（離線）。"""
import sys
from datetime import datetime

import tg_shadow as S
import tg_policy as P

ok = True


def chk(n, c, extra=None):
    global ok
    print(("✅ " if c else "❌ ") + n + ("" if c else f"  → {extra}"))
    ok = ok and c


chk("截成 80 字", len(S.scrub("字" * 300)) == 80)
chk("換行壓成空白", "\n" not in S.scrub("a\n\nb\tc"))
t = S.scrub("呼叫失敗 https://api.x.com/v1/q?symbol=1&token=SECRETVALUE123 請檢查")
chk("帶查詢字串網址被省略", "SECRETVALUE123" not in t and "網址已省略" in t, t)
chk("token=值被遮", "ABCDEF" not in S.scrub("token=ABCDEF&x=1") and "***" in S.scrub("token=ABCDEF"))
chk("apikey: 值被遮", "KEY99" not in S.scrub("apikey: KEY99 失敗"))
chk("Telegram bot token 被遮", "AAH" not in S.scrub("bot 123456789:AAHxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx 失敗"))
chk("Bearer 被遮", "abc.def" not in S.scrub("Authorization: Bearer abc.def 403"))
chk("長英數金鑰樣貌被遮", "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6" not in S.scrub("key是 A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6 喔"))
chk("JWT 被遮", "eyJhbGciOiJIUzI1NiJ9.payload.sig" not in S.scrub("jwt eyJhbGciOiJIUzI1NiJ9.payload.sig"))
chk("先遮再截：截斷處不留半截金鑰", "SECRET" not in S.scrub("x" * 70 + " token=SECRETSECRETSECRET"))
chk("一般訊息與股票代號保留", S.scrub("🎯 13:18 尾盤定版戰卡｜買 2330") == "🎯 13:18 尾盤定版戰卡｜買 2330")
row = S.make_row("🎯 13:18 尾盤定版戰卡\n買 2330", "push", "交易關鍵字：尾盤定版戰卡", "sent", datetime(2026, 11, 13, 13, 18, tzinfo=S.TAIPEI_TZ))
chk("列含分類、原因、前 80 字、走向、時間", row == {"t": "13:18", "tier": "push", "why": "交易關鍵字：尾盤定版戰卡", "route": "sent", "m": "🎯 13:18 尾盤定版戰卡 買 2330"}, row)
tier, why = P.classify("⚠️ [d] 候選池寫入Supabase失敗")
row2 = S.make_row("⚠️ [d] 候選池寫入Supabase失敗", tier, why, "dedup")
chk("警報去重走向", row2["tier"] == "alert" and row2["route"] == "dedup")


class DB:
    def __init__(self, boom=False):
        self.rows, self.boom = [], boom

    def table(self, n):
        assert n == "ui_selftest_reports", n   # 只寫私有表
        return self

    def insert(self, row):
        if self.boom:
            raise RuntimeError("db down")
        self.rows.append(row)
        return self

    def execute(self):
        return None


db = DB()
chk("record 寫私有表 summary=tg_shadow:日期", S.record(db, "訊息 token=ZZZ", "digest", "降噪：x", "digest", "run9", datetime(2026, 11, 13, 9, 0, tzinfo=S.TAIPEI_TZ))
    and db.rows[0]["summary"] == "tg_shadow:2026-11-13" and db.rows[0]["run_id"] == "run9" and "ZZZ" not in str(db.rows[0]))
chk("DB 失敗回 False 不拋例外", S.record(DB(boom=True), "x", "push", "y", "sent") is False)
chk("無資料庫連線回 False", S.record(None, "x", "push", "y", "sent") is False)
sys.exit(0 if ok else 1)
