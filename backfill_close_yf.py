#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""【2026-10-14】一次性：補 twse_market_snapshot 缺的收盤價（10/9、10/12、10/13）。
為什麼不能用證交所 OpenAPI：STOCK_DAY_ALL 只有「最新交易日」，已過去的日子拿不到（死規則 2 → 備案）。
備案：yfinance（本倉庫 5 年回測一直在 Actions 內使用；只讀、只在 Actions 內跑、結果只寫私有庫）。
驗證（死規則 1）：先拿「資料庫已有證交所收盤價」的最近一天做對照，Yahoo 未還原 Close 與證交所收盤價相差 ≤0.01 的檔數
≥98% 才視為同一價格基準，否則整批不寫；再檢查補寫日相對前一日的漲跌幅 ≤12% 的比例 ≥97%。
公開日誌只印數量。
"""
import os
import sys
import json
import datetime as dt

import pandas as pd
import yfinance as yf
from supabase import create_client

MIN_MATCH = 0.98
MIN_MOVE_OK = 0.97


def pg_all(q, page=1000):
    out, off = [], 0
    while True:
        r = q().range(off, off + page - 1).execute().data or []
        out += r
        if len(r) < page:
            return out
        off += page


def judge(ref_stored, ref_yf, tol=0.01):
    """同一天、同一批代號：Yahoo 與證交所收盤價一致的比例。回傳 (ratio, n)。"""
    common = [s for s in ref_stored if s in ref_yf and ref_yf[s] == ref_yf[s]]
    if not common:
        return 0.0, 0
    ok = sum(1 for s in common if abs(ref_stored[s] - ref_yf[s]) <= tol)
    return ok / len(common), len(common)


def main():
    sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
    rows = pg_all(lambda: sb.table("twse_market_snapshot").select("trade_date,symbol,close_price")
                  .gte("trade_date", "2026-10-01").order("trade_date").order("symbol"))
    df = pd.DataFrame(rows)
    df["has"] = df["close_price"].notna()
    by = df.groupby("trade_date")["has"].sum()
    ref_date = by[by > 800].index.max()
    target_dates = [d for d in by.index if d > ref_date and by[d] < 100]
    print(f"[回補] 參考日 {ref_date}；待補日期數 {len(target_dates)}")
    if not target_dates:
        return 0
    syms = sorted(df[df.trade_date == ref_date].symbol.unique())
    start = (dt.date.fromisoformat(ref_date) - dt.timedelta(days=5)).isoformat()
    end = (dt.date.fromisoformat(max(target_dates)) + dt.timedelta(days=1)).isoformat()
    data = yf.download([f"{s}.TW" for s in syms], start=start, end=end, auto_adjust=False, progress=False,
                       group_by="ticker", threads=True)
    closes = {}
    for s in syms:
        try:
            ser = data[f"{s}.TW"]["Close"].dropna()
        except Exception:  # noqa: BLE001
            continue
        for ts, v in ser.items():
            closes.setdefault(ts.strftime("%Y-%m-%d"), {})[s] = round(float(v), 2)
    stored = {r["symbol"]: float(r["close_price"]) for r in rows if r["trade_date"] == ref_date and r["close_price"]}
    ratio, n = judge(stored, closes.get(ref_date, {}))
    print(f"[回補] 對照 {ref_date}：共同 {n} 檔，一致比例 {ratio:.3f}")
    report = {"ref_date": ref_date, "match_ratio": round(ratio, 4), "n_common": n, "filled": {}}
    if ratio < MIN_MATCH:
        print("[回補] 價格基準對不上，整批不寫入")
        report["status"] = "rejected_basis"
        sb.table("ui_selftest_reports").insert({"run_id": os.environ.get("GITHUB_RUN_ID", ""), "summary": "close_backfill_yf",
                                                "report": report}).execute()
        return 1
    prev = dict(stored)
    for d in sorted(target_dates):
        cur = closes.get(d, {})
        both = [s for s in cur if s in prev and prev[s]]
        move_ok = sum(1 for s in both if abs(cur[s] / prev[s] - 1) <= 0.12) / max(len(both), 1)
        if len(cur) < 800 or move_ok < MIN_MOVE_OK:
            print(f"[回補] {d} 驗證不過（檔數 {len(cur)}、漲跌合理比例 {move_ok:.3f}），略過")
            report["filled"][d] = {"status": "rejected", "n": len(cur), "move_ok": round(move_ok, 4)}
            continue
        have = {r["symbol"] for r in rows if r["trade_date"] == d}
        up = [{"trade_date": d, "symbol": s, "close_price": c} for s, c in cur.items() if s in have]
        for i in range(0, len(up), 500):
            sb.table("twse_market_snapshot").upsert(up[i:i + 500], on_conflict="trade_date,symbol").execute()
        print(f"[回補] {d} 寫入 {len(up)} 檔（漲跌合理比例 {move_ok:.3f}）")
        report["filled"][d] = {"status": "filled", "n": len(up), "move_ok": round(move_ok, 4)}
        prev = cur
    report["status"] = "done"
    sb.table("ui_selftest_reports").insert({"run_id": os.environ.get("GITHUB_RUN_ID", ""), "summary": "close_backfill_yf",
                                            "report": report}).execute()
    return 0


if __name__ == "__main__":
    sys.exit(main())
