#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""【2026-10-17】一次性：補 twse_market_snapshot 缺的成交量／成交金額（10/12、10/13 收盤價已補、但 trading_value 全空，
造成主力偵測「週轉率逐步墊高」10/15 起幾乎歸零、5 日量比失真）。

為什麼不用證交所：OpenAPI STOCK_DAY_ALL 只有最新交易日；www MI_INDEX 直連違反使用條款第6條（死規則 2 → 備案）。
備案：yfinance 成交股數（Volume）；成交金額以「收盤價 × 成交股數」估算（與實際成交金額差在當日均價與收盤價的差距，通常數 %）。
驗證（死規則 1）：拿「資料庫已有證交所成交量與成交金額」的最近一天當對照：
  估算金額與證交所成交金額相差 ≤10% 的比例 ≥ 90% 才寫入（成交股數一致率只記錄，口徑不同不寫）。
只寫原本為空的 trading_value，不動其他欄位、不覆蓋已有值。公開日誌只印數量。
"""
import datetime as dt
import os
import sys

import pandas as pd
import yfinance as yf
from supabase import create_client

MIN_VOL_MATCH = 0.90
MIN_VAL_MATCH = 0.90


def pg_all(q, page=1000):
    out, off = [], 0
    while True:
        r = q().range(off, off + page - 1).execute().data or []
        out += r
        if len(r) < page:
            return out
        off += page


def match_ratio(ref, est, tol):
    common = [s for s in ref if s in est and ref[s] and est[s] == est[s]]
    if not common:
        return 0.0, 0
    ok = sum(1 for s in common if abs(est[s] / ref[s] - 1) <= tol)
    return ok / len(common), len(common)


def main():
    sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
    start_scan = (dt.date.today() - dt.timedelta(days=21)).isoformat()
    rows = pg_all(lambda: sb.table("twse_market_snapshot").select("trade_date,symbol,close_price,trading_value,trading_volume")
                  .gte("trade_date", start_scan).order("trade_date").order("symbol"))
    df = pd.DataFrame(rows)
    df["has_v"] = df["trading_value"].notna()
    df["has_c"] = df["close_price"].notna()
    g = df.groupby("trade_date")[["has_v", "has_c"]].sum()
    ref_date = g[g.has_v > 800].index.max()
    targets = [d for d in g.index if g.loc[d, "has_c"] > 800 and g.loc[d, "has_v"] < 100]
    print(f"[量額回補] 參考日 {ref_date}；待補日期數 {len(targets)}")
    report = {"ref_date": ref_date, "targets": targets, "filled": {}}
    if not targets:
        return 0
    syms = sorted(df[df.trade_date == ref_date].symbol.unique())
    alld = sorted(set(targets) | {ref_date})
    data = yf.download([f"{s}.TW" for s in syms], start=alld[0], end=(dt.date.fromisoformat(alld[-1]) + dt.timedelta(days=1)).isoformat(),
                       auto_adjust=False, progress=False, group_by="ticker", threads=True)
    vols = {}
    for s in syms:
        try:
            ser = data[f"{s}.TW"]["Volume"].dropna()
        except Exception:  # noqa: BLE001
            continue
        for ts, v in ser.items():
            vols.setdefault(ts.strftime("%Y-%m-%d"), {})[s] = float(v)
    ref = df[df.trade_date == ref_date].set_index("symbol")
    ref_vol = {s: float(v) for s, v in ref["trading_volume"].dropna().items() if float(v) > 0}
    ref_val = {s: float(v) for s, v in ref["trading_value"].dropna().items() if float(v) > 0}
    ref_close = {s: float(v) for s, v in ref["close_price"].dropna().items()}
    yv = vols.get(ref_date, {})
    r1, n1 = match_ratio(ref_vol, yv, 0.02)
    est_val = {s: yv[s] * ref_close[s] for s in yv if s in ref_close}
    r2, n2 = match_ratio(ref_val, est_val, 0.10)
    print(f"[量額回補] 對照 {ref_date}：成交股數一致 {r1:.3f}（{n1} 檔）、估算金額一致 {r2:.3f}（{n2} 檔）")
    report.update({"vol_match": round(r1, 4), "val_match": round(r2, 4), "n": n1})
    # 2026-10-17 實測：成交股數 2% 內一致只有 63%（Yahoo 與證交所口徑不同），估算金額 10% 內一致 95.8%。
    # 用途是週轉率／量比（金額），所以只以金額一致率把關、只寫金額；成交股數比例僅記錄。
    if r2 < MIN_VAL_MATCH:
        report["status"] = "rejected_basis"
        print("[量額回補] 驗證不過，整批不寫入")
    else:
        for d in targets:
            sub = df[(df.trade_date == d)].set_index("symbol")
            cur = vols.get(d, {})
            up = []
            for s, r in sub.iterrows():
                if pd.notna(r["trading_value"]) or pd.isna(r["close_price"]) or s not in cur or cur[s] <= 0:
                    continue
                # 每列都帶同一組欄位（PostgREST 批次 upsert 以欄位聯集處理，缺欄位會被寫成空值）；已有成交股數就沿用原值
                # 只寫成交金額（估算）；成交股數 Yahoo 與證交所口徑不同（2% 內一致僅約 63%），不寫
                up.append({"trade_date": d, "symbol": s, "trading_value": round(cur[s] * float(r["close_price"]))})
            if len(up) < 800:
                report["filled"][d] = {"status": "rejected_few", "n": len(up)}
                print(f"[量額回補] {d} 只有 {len(up)} 檔，略過")
                continue
            for i in range(0, len(up), 500):
                sb.table("twse_market_snapshot").upsert(up[i:i + 500], on_conflict="trade_date,symbol").execute()
            report["filled"][d] = {"status": "filled", "n": len(up)}
            print(f"[量額回補] {d} 寫入 {len(up)} 檔")
        report["status"] = "done"
    sb.table("ui_selftest_reports").insert({"run_id": os.environ.get("GITHUB_RUN_ID", ""), "summary": "value_backfill_yf",
                                            "report": report}).execute()
    return 0 if report["status"] == "done" else 1


if __name__ == "__main__":
    sys.exit(main())
