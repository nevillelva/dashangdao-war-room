#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
backtest_otc.py —— 上櫃股 vs 上市股「同一套進場訊號」的勝率比較（2026-10-16，老闆指示：先回測上櫃勝率再決定要不要納入自動掃描）

做法：
  1. 母體：上市＝既有 load_universe 的成交值前 N；上櫃＝櫃買 OpenAPI 日收盤行情（含成交金額）前 N（4 位數代號）。
  2. 價格：yfinance 還原價（上市 .TW、上櫃 .TWO，不混用），5 年。
  3. 訊號：沿用 backtest_winrate_tuning.signal_families（官網式分數、回檔、大盤寬度、型態）；出場只看『自然出場』5/10/20 日，
     不設停利停損，測進場訊號本身。大盤寬度用上市母體計算（兩組共用，避免上櫃自己的寬度偏誤）。
  4. 判準：上櫃樣本外(OOS)必須 ① 樣本數夠 ② 期望值>0 ③ 勝率贏同組隨機基準 ④ 超額勝率不低於上市超額勝率 3 個百分點以上。
【限制】母體用「現在」的成交值挑，有存活者偏誤；OOS 為多頭；同日訊號集中，有效樣本比 n 小；yfinance 還原價與實際不完全一致。
結果只寫私有表 ui_selftest_reports（kind=backtest_otc）；公開日誌只印數量。
"""
import os
import sys
import json
import datetime as dt

import numpy as np
import pandas as pd
import requests

import backtest_rules as br
import backtest_winrate_tuning as bt

NO = 9.99
bt.TP_GRID = (NO,)
bt.SL_GRID = (NO,)
bt.HOLD_GRID = (5, 10, 20)
bt.MAX_HOLD = 20
MIN_N_OOS = int(os.environ.get("BT_MIN_N_OOS") or 100)
KEY_FAMS = ("官網式分數≥10", "官網式分數≥12", "官網式分數≥14", "分數≥12且3日回檔≥3%", "分數≥12且大盤寬度≥50%")


def otc_universe(n):
    r = requests.get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes",
                     headers={"Accept": "application/json", "User-Agent": "warroom-research/1.0"}, timeout=40)
    r.raise_for_status()
    rows = []
    for it in r.json():
        code = str(it.get("SecuritiesCompanyCode", it.get("Code", ""))).strip()
        if len(code) != 4 or not code.isdigit() or code[0] == "0":
            continue
        try:
            amt = float(str(it.get("TradingAmount", it.get("TradeValue", 0))).replace(",", ""))
        except ValueError:
            continue
        if amt > 0:
            rows.append((code, amt))
    rows.sort(key=lambda x: -x[1])
    return [c for c, _ in rows[:n]]


def download(symbols, suffix, years):
    import yfinance as yf
    out = {}
    for i in range(0, len(symbols), 40):
        chunk = symbols[i:i + 40]
        try:
            df = yf.download([s + suffix for s in chunk], period=f"{years}y", auto_adjust=True,
                             group_by="ticker", threads=True, progress=False)
        except Exception as e:
            print(f"[download] {suffix} chunk 失敗：{type(e).__name__}")
            continue
        if not isinstance(df.columns, pd.MultiIndex):
            continue
        for s in chunk:
            try:
                d = df[s + suffix].dropna(subset=["Close"])
            except KeyError:
                continue
            if len(d) >= 300:
                out[s] = d[["Open", "High", "Low", "Close", "Volume"]].astype(float)
    return out


def run_group(prices, breadth, split):
    acc = {}
    for s, df in prices.items():
        for fam, t_idx in bt.signal_families(df, breadth).items():
            if len(t_idx) == 0:
                continue
            res, dates = bt.simulate_family(df, t_idx)
            for key, arr in res.items():
                acc.setdefault((fam, key[2]), []).append((dates, arr))
    out = {}
    for (fam, hold), parts in acc.items():
        dates = np.concatenate([p[0].values for p in parts])
        rets = np.concatenate([p[1] for p in parts])
        is_m = dates < np.datetime64(split)
        yrs = pd.DatetimeIndex(dates).year
        out[(fam, hold)] = {"IS": bt.stats(rets[is_m]), "OOS": bt.stats(rets[~is_m]),
                            "by_year": {int(y): bt.stats(rets[yrs == y]) for y in sorted(set(yrs))}}
    return out


def main():
    n = int(os.environ.get("BT_N") or 200)
    years = int(os.environ.get("BT_YEARS") or 5)
    tse_syms = br.load_universe(n)
    otc_syms = otc_universe(n)
    tse = download(tse_syms, ".TW", years)
    otc = download(otc_syms, ".TWO", years)
    print(f"[母體] 上市 {len(tse)}/{len(tse_syms)} 檔、上櫃 {len(otc)}/{len(otc_syms)} 檔")
    if len(tse) < 30 or len(otc) < 30:
        print("❌ 有效股票太少")
        sys.exit(1)
    split = br.pick_split_date({**tse, **otc})
    breadth = bt.market_breadth(tse)
    T = run_group(tse, breadth, split)
    O = run_group(otc, breadth, split)

    def g(tab, fam, hold, part):
        return (tab.get((fam, hold)) or {}).get(part) or {}

    rows, verdicts = [], {}
    for fam in sorted({k[0] for k in T} | {k[0] for k in O}):
        for hold in (5, 10, 20):
            if fam == "隨機進場(基準)":
                continue
            to, oo = g(T, fam, hold, "OOS"), g(O, fam, hold, "OOS")
            tb, ob = g(T, "隨機進場(基準)", hold, "OOS"), g(O, "隨機進場(基準)", hold, "OOS")
            if not oo.get("n") or not ob.get("n"):
                continue
            t_ex = (to.get("win", 0) - tb.get("win", 0)) * 100 if to.get("n") else None
            o_ex = (oo["win"] - ob["win"]) * 100
            row = {"family": fam, "hold": hold,
                   "tse_oos": to, "otc_oos": oo, "tse_base": tb, "otc_base": ob,
                   "tse_is": g(T, fam, hold, "IS"), "otc_is": g(O, fam, hold, "IS"),
                   "tse_excess_pt": None if t_ex is None else round(t_ex, 1), "otc_excess_pt": round(o_ex, 1),
                   "otc_by_year": O[(fam, hold)]["by_year"]}
            ok = (oo["n"] >= MIN_N_OOS and oo["exp_pct"] > 0 and o_ex > 0
                  and (t_ex is None or o_ex >= t_ex - 3))
            row["otc_pass"] = bool(ok)
            rows.append(row)
            if fam in KEY_FAMS:
                verdicts.setdefault(fam, {})[hold] = bool(ok)
    key_pass = {f: all(v.get(h) for h in (5, 10, 20) if h in v) for f, v in verdicts.items()}
    include = bool(key_pass) and sum(key_pass.values()) >= 2
    rep = {"kind": "backtest_otc", "at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
           "n_tse": len(tse), "n_otc": len(otc), "split": str(split.date()), "years": years,
           "min_n_oos": MIN_N_OOS, "key_families_pass": key_pass, "recommend_include_otc": include,
           "rows": rows}
    print(f"[上櫃回測] 評估 {len(rows)} 列；關鍵訊號通過 {sum(key_pass.values())}/{len(key_pass)}；建議納入={include}")
    try:
        from supabase import create_client
        sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
        sb.table("ui_selftest_reports").insert({"run_id": os.environ.get("GITHUB_RUN_ID", ""),
                                                "summary": f"backtest_otc:include={include}", "report": json.loads(json.dumps(rep, default=str))}).execute()
    except Exception as e:
        print(f"[上櫃回測] 寫私有表失敗：{type(e).__name__}")
        sys.exit(1)


if __name__ == "__main__":
    main()
