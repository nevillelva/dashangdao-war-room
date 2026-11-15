"""價格查證階段離線測試（假 Supabase、暫存快取目錄；不連網）。"""
import io
import os
import sys
import tempfile
from contextlib import redirect_stdout

import pandas as pd

import bt_cache as BC
import price_verify_stage as PV

fails = []


def check(name, cond):
    print(("PASS: " if cond else "FAIL: ") + name)
    if not cond:
        fails.append(name)


class R:
    def __init__(self, d): self.data = d


class Q:
    def __init__(self, db, name):
        self.db, self.name, self.f, self.rng, self.ins = db, name, [], None, None

    def select(self, *_): return self
    def in_(self, k, v): self.f.append(lambda r: r[k] in v); return self
    def gte(self, k, v): self.f.append(lambda r: str(r[k]) >= v); return self
    def lte(self, k, v): self.f.append(lambda r: str(r[k]) <= v); return self
    def order(self, *_a, **_k): return self
    def range(self, a, b): self.rng = (a, b); return self
    def insert(self, row): self.ins = row; return self

    def execute(self):
        if self.ins is not None:
            self.db.setdefault(self.name, []).append(self.ins)
            return R([])
        rows = [r for r in self.db.get(self.name, []) if all(f(r) for f in self.f)]
        if self.rng:
            rows = rows[self.rng[0]:self.rng[1] + 1]
        return R(rows)


class SB:
    def __init__(self, db): self.db = db
    def table(self, n): return Q(self.db, n)


# expand_dates / clean_symbols
check("expand_dates 排除週末", PV.expand_dates("2026-11-12", "2026-11-17") == ["2026-11-12", "2026-11-13", "2026-11-16", "2026-11-17"])
check("expand_dates 可排除休市日", PV.expand_dates("2026-11-12", "2026-11-13", lambda d: d != "2026-11-13") == ["2026-11-12"])
for bad in (("2026-11-13", "2026-11-12"), ("2026/11/12", "2026-11-13")):
    try:
        PV.expand_dates(*bad)
        check(f"壞日期 {bad} 要丟錯", False)
    except ValueError:
        check(f"壞日期 {bad} 要丟錯", True)
check("clean_symbols 去重去亂碼", PV.clean_symbols([" 2330", "2330", "", "a;b", "00878", "123456789"]) == ["2330", "00878"])

# 快取：daily_raw
with tempfile.TemporaryDirectory() as td:
    idx = pd.to_datetime(["2026-11-12", "2026-11-13"])
    BC._write(os.path.join(td, "daily_raw", "2317_5.pkl"), pd.DataFrame({"Close": [100.0, 101.0]}, index=idx))
    open(os.path.join(td, "daily_raw", "9999_5.pkl"), "wb").write(b"broken")
    c = PV.read_cache(["2317", "9999", "1101"], ["2026-11-12", "2026-11-13"], cache_dir=td)
    check("read_cache 讀得到並略過壞檔與不存在", c == {("2317", "2026-11-12"): 100.0, ("2317", "2026-11-13"): 101.0})
    check("read_cache 不改動全域快取目錄", BC.DIR != td)

    # 完整階段
    db = {"twse_market_snapshot": [
        {"symbol": "2330", "trade_date": "2026-11-12", "close_price": 600},
        {"symbol": "2330", "trade_date": "2026-11-13", "close_price": None},
        {"symbol": "2317", "trade_date": "2026-11-12", "close_price": 110},      # 與快取 100 差 10% → 衝突
    ]}
    sb = SB(db)
    buf = io.StringIO()
    with redirect_stdout(buf):
        counts, plan = PV.run_stage(sb, ["2330", "2317"], "2026-11-12", "2026-11-13", cache_dir=td, run_id="t1")
    check("數量：總 4、快照 2、快取 1、待補 1、衝突 1",
          (counts["total"], counts["from_snapshot"], counts["from_cache"], counts["pending"], counts["conflicts"]) == (4, 2, 1, 1, 1))
    check("待補是 2330 11/13（快照為空值）", plan["pending_list"] == [{"symbol": "2330", "date": "2026-11-13"}])
    rep = db["ui_selftest_reports"][0]
    check("寫入 ui_selftest_reports(summary=price_verify)", rep["summary"] == "price_verify" and rep["report"]["counts"] == counts)
    out = buf.getvalue()
    check("公開日誌只印數量（不含價格與代號）", "600" not in out and "2330" not in out and "待補" not in out and "pending 1" in out)

# 沒資料全部待補；無有效代號丟錯；不寫入模式
sb2 = SB({})
with redirect_stdout(io.StringIO()):
    c2, p2 = PV.run_stage(sb2, ["2330"], "2026-11-12", "2026-11-12", cache_dir=tempfile.gettempdir() + "/_no_such_dir_pv", write=False)
check("全缺漏 → 全待補", c2["pending"] == 1 and c2["from_snapshot"] == 0)
check("write=False 不寫表", "ui_selftest_reports" not in sb2.db)
try:
    PV.run_stage(sb2, ["", "!"], "2026-11-12", "2026-11-12", write=False)
    check("無有效代號要丟錯", False)
except ValueError:
    check("無有效代號要丟錯", True)

# 待補清單上限
big = PV.build_plan(["2330"], [f"d{i}" for i in range(PV.PENDING_CAP + 5)], {}, {})
check("待補清單截斷但總數正確", len(big["pending_list"]) == PV.PENDING_CAP and big["pending"] == PV.PENDING_CAP + 5 and big["pending_truncated"])

# 分頁：超過 1000 列
from datetime import date, timedelta
many = [{"symbol": "2330", "trade_date": (date(2020, 1, 1) + timedelta(days=i)).isoformat(), "close_price": 1.0 + i} for i in range(1500)]
got = PV.read_snapshot(SB({"twse_market_snapshot": many}), ["2330"], "2020-01-01", "2030-12-31")
check("read_snapshot 分頁讀完 1500 列", len(got) == 1500)

sys.exit(1 if fails else 0)
