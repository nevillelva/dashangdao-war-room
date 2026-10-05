#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_bt_cloud_store.py —— 訊號命中率回測實驗室「改存雲端」的單元測試（用假 Supabase client，不連網）。
驗證：存檔寫到雲端(runs+signals 分批)；列表/摘要從雲端讀(分頁超過 1000 筆)；NaN 轉 None；雲端寫入失敗→回收半套→退回本機 SQLite；
雲端讀取失敗→退回本機。"""
import sqlite3

import pandas as pd

import dashangdao_helpers as H


class _Res:
    def __init__(self, data):
        self.data = data


class _Q:
    def __init__(self, store, name):
        self.s, self.name = store, name
        self.f, self.order_col, self.desc, self.lim, self.rng, self.op, self.payload, self.cols = [], None, False, None, None, None, None, None

    def insert(self, payload):
        self.op, self.payload = "insert", payload
        return self

    def delete(self):
        self.op = "delete"
        return self

    def select(self, cols):
        self.op, self.cols = "select", cols
        return self

    def eq(self, c, v):
        self.f.append((c, v))
        return self

    def order(self, c, desc=False):
        self.order_col, self.desc = c, desc
        return self

    def limit(self, n):
        self.lim = n
        return self

    def range(self, a, b):
        self.rng = (a, b)
        return self

    def execute(self):
        s, t = self.s, self.s.tables[self.name]
        if s.fail_insert_on == self.name and self.op == "insert" and (s.fail_after is None or s.inserts[self.name] >= s.fail_after):
            raise RuntimeError("boom")
        if self.op == "insert":
            s.inserts[self.name] += 1
            rows = self.payload if isinstance(self.payload, list) else [self.payload]
            out = []
            for r in rows:
                r = dict(r)
                s.seq[self.name] += 1
                r["run_id" if self.name == "backtest_runs" else "id"] = s.seq[self.name] if self.name == "backtest_runs" else s.seq[self.name]
                t.append(r)
                out.append(r)
            return _Res(out)
        if self.op == "delete":
            keep = [r for r in t if not all(r.get(c) == v for c, v in self.f)]
            t[:] = keep
            return _Res([])
        if s.fail_select:
            raise RuntimeError("read boom")
        rows = [r for r in t if all(r.get(c) == v for c, v in self.f)]
        if self.order_col:
            rows = sorted(rows, key=lambda r: r.get(self.order_col) or 0, reverse=self.desc)
        if self.rng:
            rows = rows[self.rng[0]:self.rng[1] + 1]
        if self.lim:
            rows = rows[:self.lim]
        cols = [c.strip() for c in (self.cols or "").split(",") if c.strip()]
        return _Res([{c: r.get(c) for c in cols} if cols else dict(r) for r in rows])


class FakeSB:
    def __init__(self):
        self.tables = {"backtest_runs": [], "backtest_signals": []}
        self.seq = {"backtest_runs": 0, "backtest_signals": 0}
        self.inserts = {"backtest_runs": 0, "backtest_signals": 0}
        self.fail_insert_on, self.fail_after, self.fail_select = None, None, False

    def table(self, name):
        return _Q(self, name)


def _rows(n, nan_at=None):
    out = []
    for i in range(n):
        v = float("nan") if i == nan_at else (i % 7) - 3.0
        out.append({"stock": "2330", "date": f"2026-01-{i % 28 + 1:02d}", "signal": "🔥 偏多攻擊" if i % 2 else "🔵 偏空防守",
                    "future_3d_ret": v, "future_10d_ret": 1.0, "is_breached": i % 3 == 0})
    return out


def main():
    H.SQLITE_CONN = sqlite3.connect(":memory:", check_same_thread=False)
    H._ensure_schema(H.SQLITE_CONN)
    H.SUPABASE_ENABLED = True
    sb = FakeSB()
    H.SUPABASE_CONN = sb
    # 1) 雲端存檔：2500 筆 → 3 批；NaN 轉 None
    rid = H.save_backtest_run(["2330"], 2, 1.5, True, False, _rows(2500, nan_at=5))
    assert rid == 1 and H.backtest_storage_label() == "雲端 Supabase"
    assert sb.inserts["backtest_signals"] == 3 and len(sb.tables["backtest_signals"]) == 2500
    assert sb.tables["backtest_signals"][5]["future_3d_ret"] is None
    assert all(r["run_id"] == 1 for r in sb.tables["backtest_signals"])
    # 2) 列表/摘要從雲端讀（分頁：2500 筆 → 3 頁）
    df = H.list_backtest_runs(mode="technical")
    assert len(df) == 1 and int(df.iloc[0]["run_id"]) == 1 and df.iloc[0]["sample_count"] == 2500
    summ = H.load_backtest_summary(1)
    assert set(summ["訊號"]) == {"🔥 偏多攻擊", "🔵 偏空防守"} and int(summ["樣本數"].sum()) == 2500
    # 3) 濾網回測：欄位對映 future_5d_ret → future_3d_ret
    frows = [{"stock": "2317", "date": "2026-02-01", "future_5d_ret": 2.0, "future_10d_ret": 3.0, "filter": "查9.均線糾結爆量突破"}] * 40
    rid2 = H.save_filter_backtest_run(["2317"], 2, frows)
    assert rid2 == 2
    assert len(H.list_backtest_runs(mode="filter")) == 1 and len(H.list_backtest_runs(mode="technical")) == 1
    fs = H.load_filter_backtest_summary(2)
    assert not fs.empty
    # 4) 雲端寫入中途失敗 → 半套回收 → 退回本機 SQLite
    sb2 = FakeSB()
    sb2.fail_insert_on, sb2.fail_after = "backtest_signals", 0
    H.SUPABASE_CONN = sb2
    rid3 = H.save_backtest_run(["2330"], 2, 1.5, True, False, _rows(10))
    assert H.backtest_storage_label().startswith("本機 SQLite")
    assert sb2.tables["backtest_runs"] == [] and sb2.tables["backtest_signals"] == []   # 沒有留下半套
    # 5) 雲端讀取失敗 → 退回本機清單（裡面有上一步的本機紀錄）
    sb3 = FakeSB()
    sb3.fail_select = True
    H.SUPABASE_CONN = sb3
    local = H.list_backtest_runs(mode="technical")
    assert H.backtest_storage_label().startswith("本機 SQLite") and (local.empty or rid3 in local["run_id"].tolist())
    # 6) 雲端未啟用 → 直接走本機
    H.SUPABASE_ENABLED, H.SUPABASE_CONN = False, None
    rid4 = H.save_backtest_run(["2330"], 2, 1.5, True, False, _rows(4))
    assert rid4 > rid3
    print("✅ test_bt_cloud_store 全部通過")


if __name__ == "__main__":
    main()
