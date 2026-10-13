"""【2026-10-13】stage_etf_gap_probe 離線測試（假 Supabase、假 FinMind）。python3 test_etf_gap_probe_stage.py
重點：①已確認的空檔不再查 FinMind（0 次呼叫）②有新空檔才查、查到 0 占位且 0 日有價才寫入確認
③查詢失敗的代號保留原有確認 ④有日有價的空檔不寫入（真缺資料）⑤已排進 bundle_evening。"""
import os
import sys
import json

os.environ.setdefault("SUPABASE_URL", "http://x")
os.environ.setdefault("SUPABASE_KEY", "x")
import system_scheduler as ss  # noqa: E402
import etf_qc as QC  # noqa: E402
import etf_price_sync as P  # noqa: E402

FAIL = []


def check(name, cond, extra=""):
    if cond:
        print("✅", name)
    else:
        FAIL.append(name)
        print("❌", name, extra)


class Res:
    def __init__(self, data): self.data = data


class Tbl:
    def __init__(self, db, name):
        self.db, self.name, self.eqs, self.rng, self.op, self.payload = db, name, {}, None, "select", None

    def select(self, *a, **k): self.op = "select"; return self
    def eq(self, c, v): self.eqs[c] = v; return self
    def order(self, *a, **k): return self
    def limit(self, n): return self
    def range(self, a, b): self.rng = (a, b); return self
    def delete(self): self.op = "delete"; return self
    def insert(self, p): self.op, self.payload = "insert", p; return self
    def upsert(self, p, on_conflict=None): self.op, self.payload = "upsert", p; return self

    def execute(self):
        t = self.db.setdefault(self.name, [])
        if self.op == "insert":
            t += self.payload if isinstance(self.payload, list) else [self.payload]
            return Res([])
        if self.op == "upsert":
            p = self.payload
            t[:] = [r for r in t if r.get("config_key") != p["config_key"]] + [p]
            return Res([])
        if self.op == "delete":
            t[:] = [r for r in t if not all(r.get(k) == v for k, v in self.eqs.items())]
            return Res([])
        rows = [r for r in t if all(r.get(k) == v for k, v in self.eqs.items())]
        if self.rng:
            rows = rows[self.rng[0]:self.rng[1] + 1]
        return Res(rows)


class FakeSB:
    def __init__(self, db): self.db = db
    def table(self, n): return Tbl(self.db, n)


calls = []
logs = []
fm_data = {}


def fake_finmind(url, params):
    calls.append(params["data_id"])
    v = fm_data[params["data_id"]]
    if isinstance(v, Exception):
        raise v
    return {"data": v}


ss._finmind_get = fake_finmind
ss._log_stage_run = lambda sb, stage, rd, **kw: logs.append((stage, kw))
P.PACING_SEC = 0
os.environ.pop("GAP_SYMBOLS", None)
os.environ.pop("GAP_FORCE", None)


def raw(sym, dates, close):
    return [{"stock_id": sym, "date": d, "close": close, "Trading_Volume": 0 if close == 0 else 10} for d in dates]


def daterange(a, b):
    import datetime as dt
    d0, d1 = dt.date.fromisoformat(a), dt.date.fromisoformat(b)
    return [(d0 + dt.timedelta(days=i)).isoformat() for i in range((d1 - d0).days + 1)]


# ---- 情境 1：全部已確認 → 略過、0 次 FinMind
seg = {"from": "2026-01-02", "to": "2026-03-10", "days": 67, "placeholder_days": 40}
db = {"etf_price_gaps": [{"symbol": "AAA", "prev_date": "2026-01-02", "next_date": "2026-03-10"}],
      "system_config": [{"config_key": QC.NO_TRADE_KEY,
                         "config_value": json.dumps({"AAA": {"segments": [seg], "checked": "2026-10-13", "source": "x"}})}]}
msg = ss.stage_etf_gap_probe(FakeSB(db))
check("全部已確認：略過", "略過" in msg, msg)
check("全部已確認：0 次 FinMind 呼叫", calls == [], calls)
check("全部已確認：有寫執行紀錄", logs and logs[-1][0] == "etf_gap_probe" and logs[-1][1].get("picked_count") == 0, logs)

# ---- 情境 2：新空檔（BBB 沒成交、CCC 有日有價、DDD 查詢失敗且原本有確認）
calls.clear(); logs.clear()
dd_seg = {"from": "2025-05-01", "to": "2025-08-01", "days": 92, "placeholder_days": 50}
db = {
    "etf_price_gaps": [
        {"symbol": "AAA", "prev_date": "2026-01-02", "next_date": "2026-03-10"},
        {"symbol": "BBB", "prev_date": "2026-02-01", "next_date": "2026-04-01"},
        {"symbol": "CCC", "prev_date": "2026-02-01", "next_date": "2026-04-01"},
        {"symbol": "DDD", "prev_date": "2025-05-01", "next_date": "2025-08-20"},  # 與既有確認不完全相符 → 未覆蓋
    ],
    "system_config": [{"config_key": QC.NO_TRADE_KEY, "config_value": json.dumps({
        "AAA": {"segments": [seg], "checked": "2026-10-13", "source": "x"},
        "DDD": {"segments": [dd_seg], "checked": "2026-10-01", "source": "x"}})}],
    "etf_price_daily": (
        [{"symbol": "BBB", "trade_date": d} for d in ("2026-01-30", "2026-02-01", "2026-04-01", "2026-04-02")]
        + [{"symbol": "CCC", "trade_date": d} for d in ("2026-01-30", "2026-02-01", "2026-04-01", "2026-04-02")]
        + [{"symbol": "DDD", "trade_date": d} for d in ("2025-05-01", "2025-08-20")]),
}
bbb = raw("BBB", ["2026-02-01", "2026-04-01"], 10) + raw("BBB", daterange("2026-02-02", "2026-03-31"), 0)
ccc = raw("CCC", ["2026-02-01", "2026-04-01"], 10) + raw("CCC", daterange("2026-02-02", "2026-03-31")[:20], 0) \
    + raw("CCC", ["2026-03-05"], 11)
fm_data.update({"BBB": bbb, "CCC": ccc, "DDD": RuntimeError("boom")})
sb = FakeSB(db)
msg = ss.stage_etf_gap_probe(sb)
check("只查未覆蓋的代號（BBB、CCC、DDD），AAA 不查", sorted(calls) == ["BBB", "CCC", "DDD"], calls)
cfg = json.loads([r for r in db["system_config"] if r["config_key"] == QC.NO_TRADE_KEY][0]["config_value"])
check("BBB 沒成交 → 寫入確認", "BBB" in cfg and cfg["BBB"]["segments"][0]["from"] == "2026-02-01", cfg.keys())
check("CCC 空檔內有日有價 → 不寫入", "CCC" not in cfg, cfg.keys())
check("AAA 原有確認保留", "AAA" in cfg)
check("DDD 查詢失敗 → 原有確認保留", cfg.get("DDD", {}).get("segments") == [dd_seg], cfg.get("DDD"))
rep = [r for r in db["ui_selftest_reports"] if r["summary"] == "etf_gap_probe"]
check("明細只寫私有表且僅一筆", len(rep) == 1 and set(rep[0]["report"]["symbols"]) == {"BBB", "CCC", "DDD"})
check("查詢失敗時 gate_status=partial", logs and logs[-1][1].get("gate_status") == "partial", logs)
check("摘要不含價格明細", "10.0" not in msg, msg)

# ---- 情境 3：GAP_FORCE=1 強制重查全部
calls.clear()
os.environ["GAP_FORCE"] = "1"
fm_data["AAA"] = raw("AAA", ["2026-01-02", "2026-03-10"], 5)
db["etf_price_daily"] += [{"symbol": "AAA", "trade_date": d} for d in ("2026-01-02", "2026-03-10")]
ss.stage_etf_gap_probe(sb)
check("GAP_FORCE 重查所有有空檔代號", sorted(calls) == ["AAA", "BBB", "CCC", "DDD"], calls)
os.environ.pop("GAP_FORCE")

# ---- 情境 4：排程登記
src = open("system_scheduler.py", encoding="utf-8").read()
blk = src[src.index('"bundle_evening"'):]
blk = blk[:blk.index("]")]
check("bundle_evening 含 etf_gap_probe 且在 etf_price_sync 之後",
      "etf_gap_probe" in blk and blk.index("etf_price_sync") < blk.index("etf_gap_probe"), blk[:300])
check("etf_gap_probe 已註冊為 stage", '"etf_gap_probe"' in src and "stage_etf_gap_probe" in src)

print("\n結果：", "全部通過" if not FAIL else f"{len(FAIL)} 項失敗 {FAIL}")
sys.exit(1 if FAIL else 0)
