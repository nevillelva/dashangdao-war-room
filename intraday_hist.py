"""【2026-10-17 老闆：回測基本都要以 5 年為主】盤中（5 分K）5 年歷史快取。

為什麼：永豐 1 分K 每天 500MB 額度，一次抓不完 5 年（實測約 1.9MB／檔／年）；Supabase 免費空間已用 460MB 放不下。
做法：每天夜間抓一批（剩餘額度低於門檻就停），1 分K 當場合成 5 分K，存成每檔一個壓縮 npz，放在 GitHub Actions 快取
（只限本倉庫工作流程讀取，不公開下載）；抓滿後所有盤中回測（第二三關、多方進場型態、查8 買點、隔日沖）都改讀這份 5 年資料。
純函式（存取／轉換），可離線測試。"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta

import numpy as np

MANIFEST = "manifest.json"
_EPOCH = datetime(1970, 1, 1)


def _to_min(dt):
    return int((dt - _EPOCH).total_seconds() // 60)


def _from_min(m):
    return _EPOCH + timedelta(minutes=int(m))


def bars5_from_1m(bars1):
    """[(start_dt,o,h,l,c,v)]（1 分K）→ 5 分K arrays dict（t＝起始時間 epoch 分鐘）。"""
    bk = {}
    for t, o, h, l, c, v in bars1:
        m = t.hour * 60 + t.minute
        key = (t.date(), (m - 540) // 5)
        b = bk.get(key)
        if b is None:
            bk[key] = [o, h, l, c, v]
        else:
            b[1] = max(b[1], h)
            b[2] = min(b[2], l)
            b[3] = c
            b[4] += v
    if not bk:
        return None
    keys = sorted(bk)
    ts = np.array([_to_min(datetime(d.year, d.month, d.day, 9, 0) + timedelta(minutes=5 * i)) for d, i in keys], dtype=np.int64)
    arr = np.array([bk[k] for k in keys], dtype=np.float64)
    return {"t": ts, "o": arr[:, 0].astype(np.float32), "h": arr[:, 1].astype(np.float32), "l": arr[:, 2].astype(np.float32),
            "c": arr[:, 3].astype(np.float32), "v": arr[:, 4].astype(np.float32)}


def merge(old, new):
    """兩份 5 分K arrays 合併（同時間以新的為準），依時間排序。"""
    if old is None:
        return new
    if new is None:
        return old
    t = np.concatenate([old["t"], new["t"]])
    order = np.argsort(t, kind="stable")
    t = t[order]
    keep = np.ones(len(t), dtype=bool)
    keep[:-1] = t[:-1] != t[1:]          # 重複時間保留最後一筆（新的）
    out = {"t": t[keep]}
    for k in ("o", "h", "l", "c", "v"):
        out[k] = np.concatenate([old[k], new[k]])[order][keep]
    return out


def save(dirpath, sym, data):
    os.makedirs(dirpath, exist_ok=True)
    np.savez_compressed(os.path.join(dirpath, f"{sym}.npz"), **data)


def load(dirpath, sym):
    p = os.path.join(dirpath, f"{sym}.npz")
    if not os.path.exists(p):
        return None
    z = np.load(p)
    return {k: z[k] for k in ("t", "o", "h", "l", "c", "v")}


def load_manifest(dirpath):
    p = os.path.join(dirpath, MANIFEST)
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def save_manifest(dirpath, m):
    os.makedirs(dirpath, exist_ok=True)
    with open(os.path.join(dirpath, MANIFEST), "w", encoding="utf-8") as f:
        json.dump(m, f, ensure_ascii=False)


def year_chunks(start_iso, end_iso):
    """把 [start, end] 切成一年一段（永豐 kbars 單次區間不宜過長）。回傳 [(s, e)] iso 字串，由舊到新。"""
    s = datetime.strptime(start_iso, "%Y-%m-%d")
    e = datetime.strptime(end_iso, "%Y-%m-%d")
    out = []
    while s <= e:
        nxt = min(e, s + timedelta(days=364))
        out.append((s.strftime("%Y-%m-%d"), nxt.strftime("%Y-%m-%d")))
        s = nxt + timedelta(days=1)
    return out


def missing_chunks(cov, start_iso, end_iso):
    """cov：已完成的區間 list[[s,e]]。回傳 year_chunks 中尚未完成者。"""
    done = {tuple(x) for x in (cov or [])}
    return [c for c in year_chunks(start_iso, end_iso) if c not in done]


def to_ir_days(data):
    """→ intraday_research 用的 {date: [(start_dt,o,h,l,c,v)]}（5 分K 當成 K 棒，價位函式通用）。"""
    out = {}
    if data is None:
        return out
    for i in range(len(data["t"])):
        dt = _from_min(data["t"][i])
        out.setdefault(dt.date(), []).append((dt, float(data["o"][i]), float(data["h"][i]), float(data["l"][i]),
                                              float(data["c"][i]), float(data["v"][i])))
    return out


def to_gate_bars(ir_days, upto_hm=600):
    """→ gate23_backtest 用的 list[dict(date,end,o,h,l,c,v,full)]；只留 10:00 前的 K 棒＋當天最後一根（算收盤）以省記憶體。"""
    out = []
    for d in sorted(ir_days):
        day = ir_days[d]
        for j, (t, o, h, l, c, v) in enumerate(day):
            end = t + timedelta(minutes=5)
            if end.hour * 60 + end.minute <= upto_hm or j == len(day) - 1:
                out.append({"date": d, "end": end, "o": o, "h": h, "l": l, "c": c, "v": v, "full": True})
    return out
