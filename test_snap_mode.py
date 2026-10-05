#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_snap_mode.py —— 三關「快照式」取資料(Shioaji 1分K → 5分K)的測試：
①kbars_to_5min_bars 的時間標記/分桶/半成品棒/零量/盤前排除 ②新鮮度與比對純函式 ③_run_snap_pass 三種模式：
shadow 連 2 天通過自動晉升 fast、fast 正常走三關寫入、fast 失敗自動退回 poll（pass1 回 fallback_poll）。"""
import os
import sys
import json
import datetime as dt

os.environ.setdefault("SUPABASE_URL", "http://x")
os.environ.setdefault("SUPABASE_KEY", "x")
import warroom_core as wc  # noqa: E402
import system_scheduler as ss  # noqa: E402

ok = True


def check(cond, msg):
    global ok
    if not cond:
        ok = False
        print("❌", msg)


# ---------- 1) kbars_to_5min_bars ----------
def mk_rows(start_end_minute=(9, 1), n=36, base=100.0, vol=10, date="2026-10-06"):
    rows = []
    h, m = start_end_minute
    for i in range(n):
        tot = h * 60 + m + i
        ts = dt.datetime.fromisoformat(f"{date} {tot // 60:02d}:{tot % 60:02d}:00")
        px = base + i * 0.1
        rows.append({"ts": ts, "Open": px, "High": px + 0.05, "Low": px - 0.05, "Close": px + 0.02, "Volume": vol + i})
    return rows


rows = mk_rows()                       # ts 09:01 .. 09:36（結束標記）→ 分鐘起點 09:00 .. 09:35
bars = wc.kbars_to_5min_bars(rows, complete_before="09:35")
check([b["bar_time"] for b in bars] == ["09:00", "09:05", "09:10", "09:15", "09:20", "09:25", "09:30"],
      f"09:36 快照應含 09:00~09:30 七根完整棒：{[b['bar_time'] for b in bars]}")
b0 = bars[0]
check(abs(b0["open"] - 100.0) < 1e-9 and abs(b0["close"] - (100.0 + 4 * 0.1 + 0.02)) < 1e-9, f"第一根 open/close：{b0}")
check(b0["sample_count"] == 5 and b0["volume"] == sum(10 + i for i in range(5)), f"第一根量/樣本數：{b0}")
check(b0["outer_volume"] == 0.0 and b0["inner_volume"] == 0.0, "內外盤應為 0")
# label='start'：不減 1 分鐘 → 第一根 09:01 起點 → 09:00 桶只有 4 根
bs = wc.kbars_to_5min_bars(rows, label="start", complete_before="09:35")
check(bs[0]["sample_count"] == 4, f"label=start 時 09:00 桶應只有 4 根：{bs[0]['sample_count']}")
# 半成品棒(complete_before 之後)不輸出
check(all(b["bar_time"] <= "09:30" for b in bars), "不應輸出 09:35 半成品棒")
full = wc.kbars_to_5min_bars(rows, complete_before=None)
check(full[-1]["bar_time"] == "09:35", "complete_before=None 應含半成品棒")
# 零量/價格缺漏跳過、盤前排除
rows2 = mk_rows() + [{"ts": dt.datetime(2026, 10, 6, 8, 45), "Open": 99, "High": 99, "Low": 99, "Close": 99, "Volume": 50},
                     {"ts": dt.datetime(2026, 10, 6, 9, 20), "Open": 0, "High": 0, "Low": 0, "Close": 0, "Volume": 0}]
bars2 = wc.kbars_to_5min_bars(rows2, complete_before="09:35")
check(len(bars2) == 7 and bars2[0]["bar_time"] == "09:00", "盤前(08:45)與零量列應被排除")
check(wc.kbars_day_open(rows) == 100.0, f"day_open 應為第一根開盤價：{wc.kbars_day_open(rows)}")
# 奈秒整數 ts
ns = int(dt.datetime(2026, 10, 6, 9, 1).replace(tzinfo=dt.timezone.utc).timestamp() * 1e9)
bn = wc.kbars_to_5min_bars([{"ts": ns, "Open": 10, "High": 10.5, "Low": 9.9, "Close": 10.2, "Volume": 7}], complete_before=None)
check(bn and bn[0]["bar_time"] == "09:00", f"奈秒 ts 應視為台北當地時間：{bn}")

# 三關第一關能吃這個格式
g1 = wc.evaluate_930_gate1(bars)
check(g1["verdict"] != "stale", f"快照5分K 餵第一關不應 stale：{g1['verdict']}")

# ---------- 2) 新鮮度 / 比對 ----------
bb = {"A": bars, "B": bars[:3], "C": []}
f, n, r, miss = ss._snap_freshness(bb, ["A", "B", "C"], "09:25")
check((f, n, miss) == (1, 3, ["B", "C"]), f"新鮮度：{(f, n, r, miss)}")
poll = [{"symbol": "A", "bar_time": b["bar_time"], "close": b["close"] * 1.001, "volume": b["volume"] * 1.05, "sample_count": 10} for b in bars]
cmp_ = ss._compare_snap_vs_poll({"A": bars}, poll)
check(cmp_["n_pairs"] == 7 and abs(cmp_["close_med_abs_pct"] - 0.1) < 0.02 and abs(cmp_["vol_med_abs_pct"] - 4.76) < 0.2, f"比對：{cmp_}")
poll_low = [dict(p, sample_count=3) for p in poll]
check(ss._compare_snap_vs_poll({"A": bars}, poll_low)["n_pairs"] == 0, "樣本數不足的輪詢棒不比對")


# ---------- 3) _run_snap_pass ----------
class Res:
    def __init__(self, data):
        self.data = data


class Q:
    def __init__(self, db, name):
        self.db, self.name, self.f, self.inn = db, name, [], None
        self.op, self.payload, self.conf = "select", None, None

    def select(self, *a, **k):
        return self

    def eq(self, k, v):
        self.f.append((k, v))
        return self

    def in_(self, k, vs):
        self.inn = (k, set(vs))
        return self

    def limit(self, *a):
        return self

    def upsert(self, p, on_conflict=None):
        self.op, self.payload, self.conf = "upsert", p, on_conflict
        return self

    def insert(self, p):
        self.op, self.payload = "insert", p
        return self

    def execute(self):
        t = self.db.setdefault(self.name, [])
        if self.op == "select":
            rows = [dict(r) for r in t if all(r.get(k) == v for k, v in self.f)
                    and (self.inn is None or r.get(self.inn[0]) in self.inn[1])]
            return Res(rows)
        rows = self.payload if isinstance(self.payload, list) else [self.payload]
        for r in rows:
            if self.op == "upsert" and self.conf:
                keys = [k.strip() for k in self.conf.split(",")]
                for ex in t:
                    if all(ex.get(k) == r.get(k) for k in keys):
                        ex.update(r)
                        break
                else:
                    t.append(dict(r))
            else:
                t.append(dict(r))
        return Res([])


class FakeSB:
    def __init__(self):
        self.db = {}

    def table(self, n):
        return Q(self.db, n)


class FakeAPI:
    def logout(self):
        pass


alerts = []
ss.notify_telegram = lambda m, *a, **k: alerts.append(m)
ss._wc.open_shioaji_readonly = lambda k, s: FakeAPI()
ss._validate_previous_trading_day = lambda sb: None
ss.time.sleep = lambda *_a, **_k: None
ss.dt_time = dt.time
# 讓「等到目標時刻」立刻通過：把目標時刻前的等待迴圈略過（假裝現在已是 10:05）
_real_datetime = ss.datetime


class _FakeDT(_real_datetime):
    @classmethod
    def now(cls, tz=None):
        return _real_datetime(2026, 10, 6, 10, 5, 0, tzinfo=tz)


ss.datetime = _FakeDT


def kb_for(syms, n_min=60, bad=()):
    """09:01~10:00 的 1 分K（結束標記）。bad 內的標的只給到 09:20 以前（模擬資料落後）。"""
    out = {}
    for s in syms:
        rr = mk_rows(n=n_min, base=50 + hash(s) % 10)
        if s in bad:
            rr = rr[:20]
        out[s] = rr
    return out


SYMS = ["2330", "2317", "2454", "3008", "6488"]
flushed = []


def flush_stub(final=True, bars_override=None, day_open_override=None):
    flushed.append((final, sorted(bars_override), day_open_override))


# 輪詢組成的 K 棒（與快照幾乎相同 → 比對通過）
def poll_rows_from(kb, date="2026-10-06"):
    rows = []
    for s, rr in kb.items():
        for b in wc.kbars_to_5min_bars(rr, complete_before="10:00"):
            rows.append({"symbol": s, "trade_date": date, "bar_time": b["bar_time"], "close": b["close"] * 1.0005,
                         "volume": b["volume"] * 1.03, "sample_count": 10})
    return rows


# --- shadow：連 2 天通過 → 自動晉升 fast
kb = kb_for(SYMS)
ss._wc.fetch_shioaji_kbars_today = lambda api, syms, d, **k: (kb, {"asked": len(syms), "got": len(kb), "no_contract": [], "errors": [], "truncated": 0})
sb = FakeSB()
sb.db["intraday_5min_bars"] = poll_rows_from(kb)
r1 = ss._run_snap_pass(sb, 2, "2026-10-06", SYMS, {}, SYMS, "shadow", flush_stub)
check(r1 == "done" and not flushed, "shadow 不應寫正式表/跑 flush")
days = json.loads(ss.get_config(sb, "intraday_snap_validated_days", "[]"))
check(days == ["2026-10-06"], f"第一天通過後 validated_days：{days}")
check(ss._get_intraday_mode(sb) == "shadow", "第一天不應晉升")
sb.db["intraday_5min_bars"] = [dict(r, trade_date="2026-10-07") for r in poll_rows_from(kb)]
r2 = ss._run_snap_pass(sb, 2, "2026-10-07", SYMS, {}, SYMS, "shadow", flush_stub)
check(ss._get_intraday_mode(sb) == "fast", "連 2 天通過應自動切 fast")
check(any("快照模式" in a for a in alerts), "晉升時應推播")
rep = [r for r in sb.db["ui_selftest_reports"] if r["summary"] == "intraday_snap_shadow"][-1]["report"]
check(rep.get("promoted") is True and rep["compare"]["n_pairs"] > 20, f"報告：{rep.get('promoted')}, {rep.get('compare')}")

# --- shadow：比對不過 → 清空連續天數
sb2 = FakeSB()
bad_poll = [dict(r, close=r["close"] * 1.05) for r in poll_rows_from(kb)]
sb2.db["intraday_5min_bars"] = bad_poll
ss.set_config(sb2, "intraday_snap_validated_days", json.dumps(["2026-10-05"]))
ss._run_snap_pass(sb2, 2, "2026-10-06", SYMS, {}, SYMS, "shadow", flush_stub)
check(json.loads(ss.get_config(sb2, "intraday_snap_validated_days", "[]")) == [], "比對不過應清空連續天數")
check(ss._get_intraday_mode(sb2) == "shadow", "比對不過不應晉升")

# --- fast：正常 → flush(final 依 pass)
flushed.clear()
sb3 = FakeSB()
ss.set_config(sb3, "intraday_mode", "fast")
res = ss._run_snap_pass(sb3, 1, "2026-10-06", SYMS, {}, SYMS, "fast", flush_stub)
check(res == "done" and len(flushed) == 1 and flushed[0][0] is False, f"fast pass1 應 flush(final=False)：{flushed}")
check(set(flushed[0][2]) == set(SYMS) and all(v for v in flushed[0][2].values()), "應帶每檔開盤價")
res = ss._run_snap_pass(sb3, 2, "2026-10-06", SYMS, {}, SYMS, "fast", flush_stub)
check(res == "done" and flushed[-1][0] is True, "fast pass2 應 flush(final=True)")

# --- fast：資料落後(>30% 標的最後一棒太舊) → 退回 poll
flushed.clear()
alerts.clear()
kb_bad = kb_for(SYMS, bad=("2330", "2317", "2454"))
ss._wc.fetch_shioaji_kbars_today = lambda api, syms, d, **k: (kb_bad, {"asked": len(syms), "got": len(kb_bad), "no_contract": [], "errors": [], "truncated": 0})
sb4 = FakeSB()
ss.set_config(sb4, "intraday_mode", "fast")
res = ss._run_snap_pass(sb4, 1, "2026-10-06", SYMS, {}, SYMS, "fast", flush_stub)
check(res == "fallback_poll" and not flushed, f"資料落後時 pass1 應回 fallback_poll：{res}")
check(ss._get_intraday_mode(sb4) == "poll", "應自動切回 poll")
check(any("切回" in a for a in alerts), "應推播切回")

# --- 登入失敗 + fast pass2 → 記 error、不 flush
ss._wc.open_shioaji_readonly = lambda k, s: None
sb5 = FakeSB()
ss.set_config(sb5, "intraday_mode", "fast")
res = ss._run_snap_pass(sb5, 2, "2026-10-06", SYMS, {}, SYMS, "fast", flush_stub)
check(res == "done" and ss._get_intraday_mode(sb5) == "poll", "fast pass2 登入失敗應切回 poll")
check(any(r.get("gate_status") == "error" for r in sb5.db.get("system_run_log", [])), "應寫 error 紀錄")

# ---------- 4) 端到端：stage_intraday_kbar(snap_pass) 在 fast 模式實際寫 5分K、跑三關、寫結果與 run_log ----------
ss._wc.open_shioaji_readonly = lambda k, s_: FakeAPI()
kb_ok = kb_for(SYMS)
# 讓 2330 的 09:25/09:30 兩根是「實體長紅、量放大」→ 第一關應給 strong_bull
for r_ in kb_ok["2330"]:
    pass
ss._wc.fetch_shioaji_kbars_today = lambda api, syms, d, **k: (kb_ok, {"asked": len(syms), "got": len(kb_ok), "no_contract": [], "errors": [], "truncated": 0})
ss.get_industry_map_with_fallback = lambda sb_: ({}, None)
ss.get_industry_leader_for_symbol = lambda sym, m: (None, None)
ss.is_trading_day = lambda d=None: True


class _FakeDT2(_real_datetime):
    @classmethod
    def now(cls, tz=None):
        return _real_datetime(2026, 10, 6, 9, 40, 0, tzinfo=tz)


ss.datetime = _FakeDT2
sb6 = FakeSB()
ss.set_config(sb6, "intraday_mode", "fast")
sb6.db["user_state"] = [{"state_key": "commander_main", "state_value": {"portfolio": {}, "pinned_stocks": {s_: {} for s_ in SYMS}}}]
sb6.db["intraday_candidate_pool"] = [{"symbol": "2330", "trade_date": "2026-10-06", "direction": "long"}]
ss.stage_intraday_snap_pass_env = None
os.environ["INTRADAY_SNAP_PASS"] = "1"
ss.stage_intraday_snap(sb6)
bars_rows = sb6.db.get("intraday_5min_bars", [])
check(len(bars_rows) >= 5 * 7 - 2, f"fast pass1 應寫入各檔 5分K（約 5檔×7根）：{len(bars_rows)}")
check(min(r_["bar_time"] for r_ in bars_rows) == "09:00" and max(r_["bar_time"] for r_ in bars_rows) == "09:30", "pass1 的棒應為 09:00~09:30")
gate_rows = sb6.db.get("intraday_gate_results", [])
check(len(gate_rows) == len(SYMS), f"fast pass1 應寫 {len(SYMS)} 檔三關結果：{len(gate_rows)}")
check(not [r_ for r_ in sb6.db.get("system_run_log", []) if r_.get("stage") == "intraday_gate"], "pass1(final=False) 不應寫 intraday_gate 紀錄")
os.environ["INTRADAY_SNAP_PASS"] = "2"
ss.datetime = _real_datetime
class _FakeDT3(_real_datetime):
    @classmethod
    def now(cls, tz=None):
        return _real_datetime(2026, 10, 6, 10, 2, 0, tzinfo=tz)
ss.datetime = _FakeDT3
ss.stage_intraday_snap(sb6)
logs = [r_ for r_ in sb6.db.get("system_run_log", []) if r_.get("stage") == "intraday_gate"]
check(len(logs) == 1 and logs[0]["picked_count"] == len(SYMS), f"pass2(final) 應寫一筆 intraday_gate 紀錄：{logs}")
check(max(r_["bar_time"] for r_ in sb6.db["intraday_5min_bars"]) == "09:55", "pass2 的最後一根應為 09:55")
# 輪詢階段在 fast 模式要跳過並留紀錄（讓看門狗知道已處理）
n_before = len(sb6.db["intraday_5min_bars"])
os.environ.pop("INTRADAY_SNAP_PASS", None)
ss.stage_intraday_kbar(sb6)
check(len(sb6.db["intraday_5min_bars"]) == n_before, "fast 模式下輪詢階段不應寫 K 棒")
check(any(r_.get("gate_status") == "skipped_fast_mode" for r_ in sb6.db["system_run_log"]), "輪詢階段應留 skipped_fast_mode 紀錄")
# poll 模式下快照階段略過
ss.set_config(sb6, "intraday_mode", "poll")
n_before = len(sb6.db["intraday_5min_bars"])
os.environ["INTRADAY_SNAP_PASS"] = "2"
ss.stage_intraday_snap(sb6)
check(len(sb6.db["intraday_5min_bars"]) == n_before, "poll 模式下快照階段不應寫 K 棒")

print("✅ test_snap_mode 全部通過" if ok else "❌ test_snap_mode 失敗")
sys.exit(0 if ok else 1)
