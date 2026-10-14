"""【2026-10-14】回測資料集的打包／解包（純函式＋薄薄的資料庫存取），讓網頁不用重算 5 年資料就能秒開自助回測。

建置在 GitHub Actions（backtest_oldscore.py，設 BT_EXPORT=1）：把 5 年面板（date, sym, open, high, low, close, score）
壓縮成幾段文字存進私有表 bt_dataset；網頁讀回來還原。資料只含價格與重建分數，不含任何持倉或策略結果。
"""
import base64
import io
import zlib

import numpy as np
import pandas as pd

CHUNK = 400_000          # 每段 base64 字元數（PostgREST 單列太大會慢）
COLS = ("open", "high", "low", "close", "score")


def pack(P, score_col="score_old"):
    """P：長表（date, sym, open, high, low, close, <score_col>）。回傳 (meta dict, payload str)。"""
    d = P.rename(columns={score_col: "score"})[["date", "sym", *COLS]].copy()
    dates = np.array(sorted(d["date"].unique()), dtype="datetime64[D]")
    syms = np.array(sorted(d["sym"].unique()))
    di = {np.datetime64(x, "D"): i for i, x in enumerate(dates)}
    si = {s: i for i, s in enumerate(syms)}
    ii = d["sym"].map(si).to_numpy()
    jj = np.array([di[np.datetime64(x, "D")] for x in d["date"].to_numpy()])
    arrs = {}
    for k in COLS:
        m = np.full((len(syms), len(dates)), np.nan, dtype=np.float32)
        m[ii, jj] = d[k].to_numpy(dtype=np.float32)
        arrs[k] = m
    buf = io.BytesIO()
    np.savez_compressed(buf, dates=dates.astype("int64"), syms=syms.astype("U12"), **arrs)
    payload = base64.b64encode(zlib.compress(buf.getvalue(), 6)).decode("ascii")
    meta = {"n_sym": int(len(syms)), "n_days": int(len(dates)), "first": str(dates[0]), "last": str(dates[-1]),
            "cols": list(COLS), "bytes": len(payload)}
    return meta, payload


def unpack(payload):
    """還原成長表 DataFrame（date, sym, open, high, low, close, score）。"""
    raw = zlib.decompress(base64.b64decode(payload))
    z = np.load(io.BytesIO(raw), allow_pickle=False)
    dates = z["dates"].astype("datetime64[D]").astype("datetime64[ns]")
    syms = z["syms"]
    frames = []
    for i, s in enumerate(syms):
        f = pd.DataFrame({"date": dates, "sym": str(s), **{k: z[k][i].astype(float) for k in COLS}})
        frames.append(f[f["close"].notna()])
    return pd.concat(frames, ignore_index=True)


def split_chunks(payload, size=None):
    size = size or CHUNK
    return [payload[i:i + size] for i in range(0, len(payload), size)] or [""]


def save(sb, P, set_name="main", score_col="score_old", extra_meta=None):
    meta, payload = pack(P, score_col)
    meta.update(extra_meta or {})
    parts = split_chunks(payload)
    sb.table("bt_dataset").delete().eq("set_name", set_name).execute()
    for i, part in enumerate(parts):
        sb.table("bt_dataset").insert({"set_name": set_name, "part": i, "meta": meta if i == 0 else None, "payload": part}).execute()
    return dict(meta, parts=len(parts))


def load(sb, set_name="main"):
    """回傳 (meta, 長表 DataFrame)；沒有資料回 (None, None)。"""
    rows = (sb.table("bt_dataset").select("part,meta,payload").eq("set_name", set_name).order("part").execute().data) or []
    if not rows:
        return None, None
    payload = "".join(r["payload"] for r in rows)
    return rows[0].get("meta"), unpack(payload)
