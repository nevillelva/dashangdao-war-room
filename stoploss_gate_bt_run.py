#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""stoploss_gate_bt_run.py —— 停損寬度 × 族群 × 大盤多空／寬度門檻 × 策略 回測的執行階段（邏輯在 stoploss_gate_bt.py）。

唯讀：只讀行情與族群表，不下單、不改任何正式規則、不寫正式資料表。
資料來源優先序：bt_cache 快取（BT_CACHE_DIR，預設 btcache；日K 還原價與未還原價各一份）→ yfinance（快取沒有才抓）。
  --from-cache：母體改用快取裡已有的股票，完全不連網（族群、大盤也只用快取；沒有就標 未分類／大盤多空 未知）。
  --synthetic ：合成資料自我測試（不連網、不連資料庫；數字沒有任何市場意義，只用來確認流程）。
結果：只寫私有表 ui_selftest_reports（summary='stoploss_gate_bt[:tag]'）；公開日誌只印數量。--json-out 可另存本機檔。
GitHub 執行器：.github/workflows/backtest_stoploss_gate.yml（手動觸發）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

import numpy as np
import pandas as pd

import bt_cache
import stoploss_gate_bt as G


def _tidy(df):
    if df is None or len(df) == 0:
        return None
    df = df.dropna(subset=["Close"])
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    return df


def fetch_pair(sym, years):
    """回傳 (還原價日K, 未還原價日K)；任一抓不到回 (None, None)。先上市 .TW、沒有再試上櫃 .TWO；皆走 bt_cache。"""
    import yfinance as yf
    out = []
    for adjusted in (True, False):
        df = None
        for suffix, key in ((".TW", sym), (".TWO", sym + "_O")):
            df = bt_cache.get_daily(key, years, adjusted,
                                    lambda per, _s=sym, _x=suffix, _a=adjusted: yf.Ticker(f"{_s}{_x}").history(period=per, auto_adjust=_a, timeout=20),
                                    min_rows=20)
            if df is not None and len(df):
                break
        out.append(_tidy(df))
    return (out[0], out[1]) if out[0] is not None and out[1] is not None else (None, None)


def symbols_from_cache(years):
    """bt_cache 內已有還原價日K 的代號（檔名 {sym}_{years}.pkl；上櫃檔名帶 _O 後綴一併還原成代號）。"""
    d = os.path.join(bt_cache.DIR, "daily_adj")
    if not os.path.isdir(d):
        return []
    syms = set()
    for f in os.listdir(d):
        if f.endswith(f"_{years}.pkl"):
            s = f[: -len(f"_{years}.pkl")]
            s = s[:-2] if s.endswith("_O") else s
            if s.isdigit() and len(s) == 4:
                syms.add(s)
    return sorted(syms)


def synthetic_pair(n=40, days=1500):
    """合成：還原價用幾何隨機漫步；未還原價假設每 250 根有一次 3% 股息（除息日之前的還原價較低）。"""
    import backtest_rules as br
    adj = br.synthetic_prices(n=n, days=days)
    raw = {}
    for s, df in adj.items():
        k = np.arange(len(df))
        n_div_after = (len(df) - 1 - k) // 250          # 該日之後還有幾次除息
        f = 0.97 ** n_div_after                          # 還原因子：越早越小
        r = df.copy()
        for c in ("Open", "High", "Low", "Close"):
            r[c] = df[c].values / f
        raw[s] = r
    return adj, raw


def synthetic_finders():
    """合成資料沒有真實訊號：用固定間隔當進場點，只為了走完整條流程（數字沒有任何市場意義）。"""
    return {"chuan_e": lambda df: [(i, "syn") for i in range(120, len(df) - 1, 37)],
            "pullback_burst": lambda df: [(i, "syn") for i in range(130, len(df) - 1, 53)]}


def build_all(adj_prices, raw_prices, sector_of, mkt_close, min_sector=12, t0=None, finders=None):
    import bt_strategy as bts
    breadth = bts.compute_breadth(adj_prices)
    regime = G.market_regime(mkt_close) if mkt_close is not None and len(mkt_close) else None
    events, fail = [], 0
    for i, (sym, df) in enumerate(adj_prices.items()):
        try:
            events += G.build_events(sym, df, raw_prices.get(sym), sector_of.get(sym), regime, breadth, finders=finders)
        except Exception as e:  # noqa: BLE001  單檔失敗不影響其他
            fail += 1
            print(f"[停損閘門回測] {sym} 略過：{type(e).__name__}")
        if t0 and (i + 1) % 100 == 0:
            print(f"  進度 {i + 1}/{len(adj_prices)}  {time.time() - t0:.0f}s", flush=True)
    return G.add_all_strategy(events), fail


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=int(os.environ.get("BT_N") or 900))
    ap.add_argument("--years", type=int, default=int(os.environ.get("BT_YEARS") or 6), help="下載年數（含約 1 年暖機；判定約 5 年）")
    ap.add_argument("--min-sector", type=int, default=int(os.environ.get("BT_MIN_SECTOR") or 12))
    ap.add_argument("--fee-discount", type=float, default=float(os.environ.get("BT_FEE_DISCOUNT") or 1.0))
    ap.add_argument("--slippage-pct", type=float, default=float(os.environ.get("BT_SLIPPAGE_PCT") or 0.0))
    ap.add_argument("--tag", default=os.environ.get("BT_TAG") or "")
    ap.add_argument("--synthetic", action="store_true")
    ap.add_argument("--from-cache", action="store_true")
    ap.add_argument("--no-upload", action="store_true")
    ap.add_argument("--json-out", default="")
    a = ap.parse_args(argv)
    t0 = time.time()
    cost = G.round_trip_cost_pct(a.fee_discount, a.slippage_pct)
    sb = None
    if not (a.synthetic or a.from_cache or a.no_upload) and os.environ.get("SUPABASE_URL"):
        from supabase import create_client
        sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])

    import backtest_sector as bsec
    import sector_map as sm
    mkt = None
    if a.synthetic:
        adj, raw = synthetic_pair()
        closes = pd.DataFrame({s: d["Close"] for s, d in adj.items()})
        mkt = (1 + closes.pct_change(fill_method=None).mean(axis=1).fillna(0)).cumprod()
        src_note = "合成資料"
    else:
        import backtest_rules as br
        symbols = symbols_from_cache(a.years) if a.from_cache else br.load_universe(a.n)
        adj, raw, bad = {}, {}, 0
        for s in symbols:
            x, y = fetch_pair(s, a.years)
            if x is None or len(x) < 300:
                bad += 1
                continue
            adj[s], raw[s] = x, y
        print(f"[停損閘門回測] 母體 {len(symbols)} 檔、取得 {len(adj)} 檔（缺 {bad}）；快取 {bt_cache.summary()}")
        if len(adj) < 5:
            print("❌ 有效股票太少（沒有快取且連不上資料來源？）")
            return 2
        import yfinance as yf
        tw = _tidy(bt_cache.get_daily("TWII", a.years, False, lambda per: yf.Ticker("^TWII").history(period=per, auto_adjust=False, timeout=20), min_rows=200))
        mkt = None if tw is None else tw["Close"]
        src_note = "bt_cache／yfinance" + ("（僅快取）" if a.from_cache else "")
    if a.synthetic:
        sector_of, sec_src = bsec.load_sector_map(list(adj), None, True)
    elif a.from_cache:
        sector_of, sec_src = {}, "僅快取模式：無族群表"
    else:
        sector_of, sec_src = bsec.load_sector_map(list(adj), sb, False)
    if sector_of:
        final, _counts = sm.merge_small(sector_of, list(adj), 3 if a.synthetic else a.min_sector)
        sector_of = final
    events, fail = build_all(adj, raw, sector_of, mkt, a.min_sector, t0, synthetic_finders() if a.synthetic else None)
    base = [e for e in events if e["strategy"] != G.ALL]
    if not base:
        print("❌ 沒有任何事件（資料不足）")
        return 2
    meta = {"source": src_note, "sector_source": sec_src, "symbols": len(adj), "fail": fail, "synthetic": bool(a.synthetic),
            "first_entry": min(e["date"] for e in base), "last_entry": max(e["date"] for e in base),
            "regime_available": mkt is not None and len(mkt) > 60}
    report = G.build_report(events, None, cost, meta)
    print(f"[停損閘門回測] 事件 {report['n_events']}；結論 {len(report['conclusions'])} 條；耗時 {time.time() - t0:.0f}s（內容只存私有表／本機檔）")
    summ = os.environ.get("GITHUB_STEP_SUMMARY")
    if summ:
        with open(summ, "a", encoding="utf-8") as f:
            f.write(f"停損閘門回測完成：事件 {report['n_events']}（細節存私有表）\n")
    if a.json_out:
        with open(a.json_out, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, default=str)
    if sb is not None:
        sb.table("ui_selftest_reports").insert({"run_id": os.environ.get("GITHUB_RUN_ID", ""),
                                                "summary": "stoploss_gate_bt" + (f":{a.tag}" if a.tag else ""),
                                                "report": report}).execute()
        print("✅ 已寫入 Supabase ui_selftest_reports")
    if a.synthetic and not a.json_out:
        print(G.render_text(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
