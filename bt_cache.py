"""【2026-10-17 老闆：測試／回測用到的資料都要先留存，不用每次重抓重下載】回測資料快取。

存在目錄 BT_CACHE_DIR（預設 btcache），由 GitHub Actions 快取在回測階段之間保存（只限本倉庫工作流程讀取，不公開、不佔資料庫）。
- 日K（yfinance）：未還原價只補抓最近 3 個月合併；還原價（除權息會改寫整段歷史）超過 7 天才整段重抓。
- 其他（FinMind 法人／營收／本益比等）：成功取得才存，預設 7 天內直接用；抓失敗不存，下次再補 → 額度不足時覆蓋率會逐次累積。
純 I/O 小工具；不印任何資料內容，只統計命中數。"""
from __future__ import annotations

import os
import pickle
import time

DIR = os.environ.get("BT_CACHE_DIR") or "btcache"
STATS = {"hit": 0, "miss": 0, "update": 0, "fail": 0}


def _p(kind, key):
    safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in str(key))
    return os.path.join(DIR, kind, f"{safe}.pkl")


def _read(path):
    try:
        with open(path, "rb") as f:
            return pickle.load(f)
    except Exception:  # noqa: BLE001
        return None


def _write(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        pickle.dump(obj, f, protocol=pickle.HIGHEST_PROTOCOL)
    os.replace(tmp, path)


def _age_days(path):
    try:
        return (time.time() - os.path.getmtime(path)) / 86400
    except OSError:
        return 1e9


def get_obj(kind, key, fetch_fn, max_age_days=7.0):
    """有快取且未過期就直接用；否則呼叫 fetch_fn()，取得非空結果才存。"""
    path = _p(kind, key)
    if os.path.exists(path) and _age_days(path) <= max_age_days:
        obj = _read(path)
        if obj is not None:
            STATS["hit"] += 1
            return obj
    try:
        obj = fetch_fn()
    except Exception:  # noqa: BLE001
        obj = None
    empty = obj is None or (hasattr(obj, "__len__") and len(obj) == 0)
    if empty:
        STATS["fail"] += 1
        old = _read(path) if os.path.exists(path) else None   # 抓不到就退回舊快取（有總比沒有好），並標註
        if old is not None:
            STATS["hit"] += 1
            return old
        return obj
    STATS["miss"] += 1
    _write(path, obj)
    return obj


def get_daily(sym, years, adjusted, ticker_fn, min_rows=200):
    """yfinance 日K 快取。ticker_fn(period) → DataFrame（呼叫端負責 .TW 與參數）。"""
    import pandas as pd
    kind = "daily_adj" if adjusted else "daily_raw"
    path = _p(kind, f"{sym}_{years}")
    old = _read(path) if os.path.exists(path) else None
    fresh_enough = old is not None and _age_days(path) <= (7 if adjusted else 0.6)
    if fresh_enough:
        STATS["hit"] += 1
        return old
    if old is not None and not adjusted:
        try:
            new = ticker_fn("3mo")
            if new is not None and len(new):
                df = pd.concat([old, new])
                df = df[~df.index.duplicated(keep="last")].sort_index()
                STATS["update"] += 1
                _write(path, df)
                return df
        except Exception:  # noqa: BLE001
            pass
        STATS["hit"] += 1
        return old
    try:
        df = ticker_fn(f"{years}y")
    except Exception:  # noqa: BLE001
        df = None
    if df is None or len(df) < min_rows:
        STATS["fail"] += 1
        return old
    STATS["miss"] += 1
    _write(path, df)
    return df


def summary():
    return dict(STATS)
