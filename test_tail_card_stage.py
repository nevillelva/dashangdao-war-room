"""【2026-10-23】尾盤戰卡排程（system_scheduler._tail_card_run）離線煙霧測試：假資料庫＋假永豐金＋假 yfinance／FinMind，
確認流程跑得完、主戰清單規則／熱門區域／持有中都有產出，且 final 會寫 focus_tail（含進場價與持有計畫）。"""
import json
import sys
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

import system_scheduler as S

ok = True


def chk(n, c, extra=None):
    global ok
    print(("✅ " if c else "❌ ") + n + ("" if c else f"  → {extra}"))
    ok = ok and c


RUN = datetime.now(S.TAIPEI_TZ).strftime("%Y-%m-%d")
PREV = [(pd.Timestamp(RUN) - pd.tseries.offsets.BDay(k)).strftime("%Y-%m-%d") for k in range(25, 0, -1)]


class Q:
    def __init__(self, db, name):
        self.db, self.name, self.f = db, name, []
        self._lim = None

    def select(self, *_a, **_k):
        return self

    def eq(self, k, v):
        self.f.append(lambda r: r.get(k) == v)
        return self

    def lt(self, k, v):
        self.f.append(lambda r: str(r.get(k)) < v)
        return self

    def gt(self, k, v):
        self.f.append(lambda r: (r.get(k) or 0) > v)
        return self

    def in_(self, k, vs):
        self.f.append(lambda r: r.get(k) in vs)
        return self

    def like(self, k, pat):
        p = pat.replace("%", "")
        self.f.append(lambda r: str(r.get(k, "")).startswith(p))
        return self

    def order(self, k, desc=False):
        self._ord = (k, desc)
        return self

    def limit(self, n):
        self._lim = n
        return self

    def range(self, a, b):
        self._rng = (a, b)
        return self

    def upsert(self, row, on_conflict=None):
        self.db.setdefault("_upserts", []).append((self.name, row))
        self._up = True
        return self

    def execute(self):
        if getattr(self, "_up", False):
            return type("R", (), {"data": []})()
        rows = [r for r in self.db.get(self.name, []) if all(f(r) for f in self.f)]
        if getattr(self, "_ord", None):
            rows.sort(key=lambda r: str(r.get(self._ord[0])), reverse=self._ord[1])
        if getattr(self, "_rng", None):
            rows = rows[self._rng[0]:self._rng[1] + 1]
        if self._lim:
            rows = rows[:self._lim]
        return type("R", (), {"data": rows})()


class DB:
    def __init__(self, data):
        self.data = data

    def table(self, name):
        return Q(self.data, name)


syms = [f"{1100 + k}" for k in range(120)]
snap_rows = [{"trade_date": d, "symbol": s, "trading_value": 1e9 + k * 1e7} for d in PREV for k, s in enumerate(syms + ["2330"])]
focus = {"date": RUN, "list": [{"symbol": "1101", "direction": "long", "track": "波段主力", "cmds": ["查3.價值"], "lv": {"ref": 100.0, "def": 96.0}}]}
prev_tail = (pd.Timestamp(RUN) - pd.tseries.offsets.BDay(3)).strftime("%Y-%m-%d")
db = DB({
    "system_config": [{"config_key": f"focus_list:{RUN}", "config_value": json.dumps(focus)},
                      {"config_key": f"focus_tail:{prev_tail}", "config_value": json.dumps({"results": [
                          {"symbol": "1102", "pass": True, "entry_px": 50.0, "plan": {"hold": 20, "tp": None, "stop_pct": 10}, "source": "主戰清單"}]})}],
    "user_state": [{"state_key": "commander_main", "state_value": {"portfolio": {"1103": {"qty": 1, "side": "long", "entry_price": 80}},
                                                                    "pinned_stocks": {"1104": {}}}}],
    "overnight_scan_results": [{"scan_date": PREV[-1], "symbol": "1104", "matched_commands": ["查6.營收雙增"]}],
    "twse_market_snapshot": snap_rows,
})
S.get_config = lambda sb, k, d: next((r["config_value"] for r in sb.data["system_config"] if r["config_key"] == k), d)
S.is_trading_day = lambda d=None: True
sent = []
S._notify_quiet = lambda m: sent.append(m) or True
S._tg_name_map = lambda: {"1101": "甲"}
S._log_stage_run = lambda *a, **k: None


def bars(open_, path):
    """09:00 起 1 分K；path(k) → 價。"""
    out, prev = [], open_
    for k in range(255):
        ts = pd.Timestamp(f"{RUN} 09:01") + pd.Timedelta(minutes=k)
        px = path(k)
        out.append({"ts": ts, "Open": prev, "High": max(prev, px), "Low": min(prev, px), "Close": px, "Volume": 10})
        prev = px
    return out


KB = {"1101": bars(100.0, lambda k: 100 + min(k, 30) * 0.05 + max(0, k - 30) * 0.004),   # 09:30 +1.5%、之後緩漲 → 查3 過
      "1102": bars(55.0, lambda k: 55.5), "1103": bars(70.0, lambda k: 69.0), "1104": bars(30.0, lambda k: 30.0),
      "1150": bars(98.0, lambda k: 98.5)}


class Snap:
    def __init__(self, code, open_, close, rate):
        self.code, self.open, self.close, self.change_rate, self.high, self.low = code, open_, close, rate, close * 1.01, open_ * 0.99


class API:
    class Contracts:
        Stocks = {s: s for s in syms}

    def snapshots(self, cs, timeout=0):
        out = []
        for c in cs:
            if c == "1150":
                out.append(Snap(c, 98.0, 98.5, -1.5))     # 昨收 100 → 開 98（開低 2%）
            else:
                out.append(Snap(c, 100.0, 100.5, 0.5))
        return out

    def logout(self):
        pass


S._wc.open_shioaji_readonly = lambda a, b: API()
S._wc.fetch_shioaji_kbars_today = lambda api, want, d, max_symbols=45: ({s: KB[s] for s in want if s in KB}, {})
S._wc.fetch_twse_attention_stocks = lambda: []
S._wc.fetch_twse_disposal_stocks = lambda: []
S._wc.fetch_tpex_disposal_stocks = lambda: []
dates = pd.bdate_range(end=pd.Timestamp(RUN) - pd.Timedelta(days=1), periods=100)


def _inst(sym, years, token, sb=None):
    df = pd.DataFrame({"f_buy": 0.0, "t_buy": 0.0, "d_buy": 0.0, "margin_diff": 0.0}, index=[d.strftime("%Y-%m-%d") for d in dates])
    if sym == "1150":
        df.iloc[-1, df.columns.get_loc("t_buy")] = 600.0     # 昨天投信買 600 張 ≥ 量 5,000 張的 5%
    return df


S._wc.fetch_institutional_history = _inst


class Tk:
    def __init__(self, t):
        self.t = t

    def history(self, period="6mo", auto_adjust=False, timeout=20):
        n = len(dates)
        c = np.linspace(90, 100, n)
        return pd.DataFrame({"Open": c, "High": c * 1.01, "Low": c * 0.99, "Close": c, "Volume": np.full(n, 5_000_000.0)}, index=dates)


import yfinance  # noqa: E402
yfinance.Ticker = Tk

note = S._tail_card_run(db, "prep")
chk("預備版跑完", isinstance(note, str) and note.startswith("prep"), note)
chk("有推播", len(sent) == 1, len(sent))
msg = sent[0] if sent else ""
chk("主戰清單 1101 查3 規則過 → 建議買", "✅ 買 甲(1101)" in msg, msg[:800])
chk("熱門區域：1150 投信大買＋季線上＋開低 → 建議買（Z1）", "1150" in msg and "Z1" in msg, msg[:1500])
chk("持有中：之前確認的 1102 與老闆持倉 1103 都有", "1102" in msg and "1103" in msg, msg)
chk("雷達 1104 有評估（查6 規則）", "1104" in msg, msg)
ups = {r["config_key"]: r for t, r in db.data.get("_upserts", []) if t == "system_config"}
chk("存 tail_card:日期:prep", f"tail_card:{RUN}:prep" in ups, list(ups))
db.data["system_config"].append({"config_key": f"tail_card:{RUN}:prep", "config_value": ups[f"tail_card:{RUN}:prep"]["config_value"]})
sent.clear()
note2 = S._tail_card_run(db, "final")
ups = {r["config_key"]: r for t, r in db.data.get("_upserts", []) if t == "system_config"}
ft = json.loads(ups.get(f"focus_tail:{RUN}", {}).get("config_value", "{}"))
chk("定版寫 focus_tail（主戰區顯示）", bool(ft.get("results")), ft)
r1101 = next((r for r in ft.get("results", []) if r["symbol"] == "1101"), {})
chk("focus_tail 含進場價與持有計畫（給之後追蹤續抱／賣）", r1101.get("entry_px") and (r1101.get("plan") or {}).get("hold") == 60, r1101)
chk("定版沿用預備版的區域比對（不重抓籌碼）", "zone_checked" in json.loads(ups[f"tail_card:{RUN}:final"]["config_value"]), None)
chk("定版也有推播", len(sent) == 1 and "13:20 尾盤定版" in sent[0], sent[:1])
sys.exit(0 if ok else 1)
