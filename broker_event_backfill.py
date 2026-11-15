"""【2026-11-15】券商分點「按需求回補」（獨立模組；純函式＋可注入的抓取函式，可離線測試）。

只在「事件日」前後抓買賣超前 15 大分點，不做全市場全日期：
  事件種類：limit_up（漲停收盤）、surge_breakout（爆量突破：量≥前20日均量3倍、收盤破前60日高、收紅）、
           wave_start（wave_screen.detect_events 的波段起點）。
抓取方式沿用 stage_dt_broker_backfill：fetch_dj_branch_data 單日查詢（三主機輪流、每主機間隔≥2.5 秒；DJ 連續失敗 3 次就停）；
儲存沿用回測快取 bt_cache（kind=dj_day／dj_empty，key=代號_日期；與 dt_broker_backfill 共用，已抓過的不重抓），
由 bt_cache_store.py 推到 Supabase Storage 私有 bucket。**不寫入主資料庫任何大表**；另存一份小索引 dj_event_idx 記錄「為何抓」。
可續跑：預算（秒）用完就停，下次接續。公開日誌只印數量。
"""
from __future__ import annotations

import os
import time

import numpy as np

KINDS = ("limit_up", "surge_breakout", "wave_start")


# ------------------------------------------------------------------ 事件偵測（純函式）
def detect_limit_up(dates, close, high, thr=0.095):
    """與 stage_dt_broker_backfill 同一規則：漲幅≥9.5% 且收盤≥當日高×0.999。回傳 [日期]。"""
    c, h = np.asarray(close, dtype=float), np.asarray(high, dtype=float)
    out = []
    for i in range(1, len(c)):
        if c[i - 1] > 0 and c[i] / c[i - 1] - 1 >= thr and c[i] >= h[i] * 0.999:
            out.append(dates[i])
    return out


def detect_surge_breakout(dates, open_, close, volume, vol_n=20, vol_mult=3.0, break_n=60):
    """爆量突破（沿用 wave_screen 的門檻常數）：量≥前 vol_n 日均量×vol_mult、收盤>前 break_n 日最高收盤、收紅。"""
    o, c, v = (np.asarray(a, dtype=float) for a in (open_, close, volume))
    out = []
    for i in range(max(vol_n, break_n), len(c)):
        avg = v[i - vol_n:i].mean()
        if avg > 0 and v[i] >= avg * vol_mult and c[i] > c[i - break_n:i].max() and c[i] > o[i]:
            out.append(dates[i])
    return out


def detect_wave_start(dates, o, h, l, c, v):
    """wave_screen.detect_events 的波段起點（起漲日）。資料不足回 []。"""
    import wave_screen as W
    evs, _ = W.detect_events(list(dates), o, h, l, c, v)
    return [e["start"] for e in evs]


def find_events(df, kinds=KINDS):
    """df：yfinance 日K（index 為日期；欄位 Open/High/Low/Close/Volume）。回傳 {事件日: [種類,...]}。"""
    if df is None or len(df) < 30:
        return {}
    d = df.dropna(subset=["Close"])
    dates = list(d.index.strftime("%Y-%m-%d"))
    o, h, l, c, v = (d[k].to_numpy(dtype=float) for k in ("Open", "High", "Low", "Close", "Volume"))
    found = {}
    if "limit_up" in kinds:
        for x in detect_limit_up(dates, c, h):
            found.setdefault(x, []).append("limit_up")
    if "surge_breakout" in kinds:
        for x in detect_surge_breakout(dates, o, c, v):
            found.setdefault(x, []).append("surge_breakout")
    if "wave_start" in kinds:
        for x in detect_wave_start(dates, o, h, l, c, v):
            found.setdefault(x, []).append("wave_start")
    return found


def window_dates(trading_dates, event_date, before=1, after=1):
    """以該股自己的交易日序列取事件日前 before 日～後 after 日（含事件日）。事件日不在序列內回 []。"""
    ds = list(trading_dates)
    if event_date not in ds:
        return []
    i = ds.index(event_date)
    return ds[max(0, i - before): i + after + 1]


def plan_fetch(events_by_symbol, trading_dates_by_symbol, before=1, after=1, since=None):
    """events_by_symbol={代號:{事件日:[種類]}} → 依事件日新到舊排序的 [(代號, 日期, [種類])]（同一 (代號, 日) 合併種類）。
    since：只要事件日 >= since（YYYY-MM-DD）。"""
    want = {}
    for sym, evs in events_by_symbol.items():
        tds = trading_dates_by_symbol.get(sym) or []
        for ed, kinds in evs.items():
            if since and ed < since:
                continue
            for d in window_dates(tds, ed, before, after):
                ks = want.setdefault((sym, d), [])
                for k in kinds:
                    if k not in ks:
                        ks.append(k)
    return sorted(((s, d, ks) for (s, d), ks in want.items()), key=lambda x: (x[1], x[0]), reverse=True)


# ------------------------------------------------------------------ 抓取與儲存
def rows_from_df(df, day):
    """DJ 回傳 DataFrame → 快取列（與 stage_dt_broker_backfill 同格式）。日期不符或空回 None。"""
    import pandas as pd
    if df is None or df.empty:
        return None
    if df.attrs.get("data_date") and df.attrs.get("data_date") != day:
        return None
    return [{"b": str(r["broker_name"]), "buy": int(r["buy_shares"]), "sell": int(r["sell_shares"]), "net": int(r["net_shares"]),
             "pct": (float(r["pct_of_volume"]) if "pct_of_volume" in df.columns and pd.notna(r.get("pct_of_volume")) else None)}
            for _, r in df.iterrows()]


def backfill(plan, fetch_day, cache, budget_s, fail_streak=lambda: 0, clock=time.time, max_fail_streak=3):
    """plan：plan_fetch 結果；fetch_day(代號, 日期)->列表或 None；cache：bt_cache 模組（_p/_read/_write）。
    已有 dj_day／dj_empty 的跳過。回傳數量 dict：fetched／empty／skipped／left（因預算或連續失敗未處理）／stopped。"""
    t0 = clock()
    res = {"fetched": 0, "empty": 0, "skipped": 0, "left": 0, "stopped": ""}
    for n, (sym, day, kinds) in enumerate(plan):
        key = f"{sym}_{day}"
        idx_path = cache._p("dj_event_idx", key)
        if os.path.exists(cache._p("dj_day", key)) or os.path.exists(cache._p("dj_empty", key)):
            res["skipped"] += 1
            _note_index(cache, idx_path, kinds)
            continue
        if clock() - t0 > budget_s:
            res["left"] = len(plan) - n
            res["stopped"] = "budget"
            break
        if fail_streak() >= max_fail_streak:
            res["left"] = len(plan) - n
            res["stopped"] = "dj_fail_streak"
            break
        rows = fetch_day(sym, day)
        if rows:
            cache._write(cache._p("dj_day", key), rows)
            res["fetched"] += 1
        else:
            if fail_streak() >= max_fail_streak:      # 疑似站台掛了，不把「抓不到」當成「當天沒資料」寫進快取
                res["left"] = len(plan) - n
                res["stopped"] = "dj_fail_streak"
                break
            cache._write(cache._p("dj_empty", key), 1)
            res["empty"] += 1
        _note_index(cache, idx_path, kinds)
    return res


def _note_index(cache, path, kinds):
    old = cache._read(path) if os.path.exists(path) else None
    merged = sorted(set((old or {}).get("kinds", [])) | set(kinds))
    if merged != (old or {}).get("kinds"):
        cache._write(path, {"kinds": merged})


def run_on_demand(symbols, load_daily, fetch_day, cache, kinds=KINDS, before=1, after=1, since=None,
                  budget_s=3600, fail_streak=lambda: 0, clock=time.time):
    """按需求回補。load_daily(代號)->日K DataFrame 或 None；回傳 (counts, plan)。counts 只含數量。"""
    kinds = tuple(k for k in kinds if k in KINDS)
    if not kinds:
        raise ValueError("kinds 必須是 " + "／".join(KINDS) + " 的子集合")
    evs, tds = {}, {}
    for sym in symbols:
        df = load_daily(sym)
        if df is None or len(df) < 30:
            continue
        df = df.dropna(subset=["Close"])
        tds[sym] = list(df.index.strftime("%Y-%m-%d"))
        evs[sym] = find_events(df, kinds)
    plan = plan_fetch(evs, tds, before, after, since)
    res = backfill(plan, fetch_day, cache, budget_s, fail_streak, clock)
    counts = {"symbols": len(evs), "events": sum(len(v) for v in evs.values()), "plan_days": len(plan), **res}
    print("[分點按需回補] " + "、".join(f"{k} {v}" for k, v in counts.items()))   # 公開日誌只印數量
    return counts, plan
