"""刪除前的冷資料匯出：把「即將被刪除」的列匯出成 jsonl.gz 到 Supabase Storage 私有 bucket。

格式：預設且排程環境一律 jsonl.gz（標準函式庫即可，不需 pyarrow，requirements 與工作流都不安裝 pyarrow）。
Parquet 路徑僅保留：呼叫端明確傳 prefer_parquet=True 且 pyarrow 已安裝才會用；沒有 pyarrow 時靜默走 jsonl.gz，
不報錯、校驗（sha256／回讀列數／伺服器 count）完全相同。

沿用 bt_cache_store.py 的做法：
  - 傳輸層直接用 bt_cache_store.SupabaseStorage（私有 bucket、單檔 45MB 一片、x-upsert）。
  - 先傳所有分片，最後才寫 manifest（manifest 存在 = 這批匯出完整）。
  - manifest 記每片 bytes／sha256／列數；匯出後「全部下載回來」重驗 sha256 與列數，總列數必須等於伺服器 count。

fail-safe：archive_before() 只要有任何一步不一致就丟 ArchiveError。呼叫端（db_maintenance／db_retention）
捕捉到例外就「不刪除」。本模組自己不刪任何資料庫列。

bucket：環境變數 DB_ARCHIVE_BUCKET（選填），預設 db-archive；不存在會自動建立（public=false）。
金鑰沿用 SUPABASE_URL／SUPABASE_STORAGE_KEY（未設定則退回 SUPABASE_KEY）；只印名稱，不印值。
"""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
from datetime import datetime, timezone

import bt_cache_store as _bcs

BUCKET_DEFAULT = "db-archive"
PAGE = 1000                        # PostgREST 單次回傳上限
ROWS_PER_SHARD = 50000
PART_BYTES = _bcs.PART_BYTES       # 45MB
MANIFEST_NAME = "manifest.json"
MANIFEST_VERSION = 1


class ArchiveError(RuntimeError):
    """匯出或校驗失敗。訊息不含金鑰。呼叫端必須因此放棄刪除。"""


# 讀取排序鍵：日期欄位之後的「決勝欄位」，確保分頁讀取順序穩定（PK 於 2026-11-15 以唯讀 SQL 查得）。
# 備份表沒有 PK，改用全部欄位排序（完全相同的列可互換，不影響正確性）。
ORDER_COLS = {
    "inst_holding": ("date", "symbol"),
    "broker_flows": ("log_date", "id"),
    "twse_market_snapshot": ("trade_date", "symbol"),
    "market_signal_snapshot": ("trade_date", "symbol"),
    "factor_snapshot": ("trade_date", "id"),
    "intraday_5min_bars": ("trade_date", "id"),
    "intraday_gate_results": ("trade_date", "id"),
    "route2_watchlist": ("trade_date", "id"),
    "broker_style_daily": ("log_date", "symbol"),
    "news_items": ("published_at", "id"),
    "mops_events": ("ev_date", "id"),
    "premarket_brief": ("brief_date",),
    "premarket_outcome": ("brief_date", "symbol"),
    "telegram_inbox": ("created_at", "id"),
    "telegram_inbox_images": ("created_at", "inbox_id"),
    "manual_feed_batches": ("created_at", "id"),
    "backup_holiday_derived_20261014": ("src_table", "trade_date", "backed_up_at"),
    "backup_holiday_intraday_bars_20261005": ("id",),
    "backup_holiday_twse_snapshot_20261005": ("trade_date", "symbol", "created_at"),
    "backup_instant_exit_artifacts_20261005": ("id",),
}


def storage_from_env(session=None):
    """回傳指向 db-archive bucket 的 SupabaseStorage；設定缺漏丟 ArchiveError。"""
    url = (os.environ.get("SUPABASE_URL") or "").strip()
    key = (os.environ.get("SUPABASE_STORAGE_KEY") or "").strip() or (os.environ.get("SUPABASE_KEY") or "").strip()
    if not url or not key:
        raise ArchiveError("缺少 SUPABASE_URL 或 SUPABASE_STORAGE_KEY/SUPABASE_KEY（只印名稱）")
    bucket = (os.environ.get("DB_ARCHIVE_BUCKET") or "").strip() or BUCKET_DEFAULT
    return _bcs.SupabaseStorage(url, key, bucket, session=session)


# ---------------------------------------------------------------- 編碼／解碼
def _has_pyarrow() -> bool:
    try:
        import pyarrow  # noqa: F401
        import pyarrow.parquet  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def _jsonable(v):
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False, separators=(",", ":"), default=str)
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    return str(v)


def _encode_jsonl_gz(rows) -> bytes:
    raw = "".join(json.dumps(r, ensure_ascii=False, default=str) + "\n" for r in rows).encode("utf-8")
    return gzip.compress(raw, 6, mtime=0)


def _encode_parquet(rows) -> bytes:
    import pyarrow as pa
    import pyarrow.parquet as pq
    cols = {}
    keys = []
    for r in rows:
        for k in r:
            if k not in cols:
                cols[k] = []
                keys.append(k)
    for r in rows:
        for k in keys:
            cols[k].append(_jsonable(r.get(k)))
    table = pa.table(cols)      # 同欄位混型別（如 int 與 str）會丟例外 → 呼叫端退回 jsonl.gz
    buf = io.BytesIO()
    pq.write_table(table, buf, compression="zstd")
    return buf.getvalue()


def encode_shard(rows, prefer_parquet=False):
    """回傳 (bytes, 副檔名, 格式名)。預設 jsonl.gz；僅在 prefer_parquet=True 且 pyarrow 已安裝時才用 Parquet，失敗退回 jsonl.gz。"""
    if prefer_parquet and _has_pyarrow():
        try:
            return _encode_parquet(rows), "parquet", "parquet"
        except Exception:  # noqa: BLE001
            pass
    return _encode_jsonl_gz(rows), "jsonl.gz", "jsonl.gz"


def count_rows(data: bytes, fmt: str) -> int:
    """實際解碼回來數列數（不是信任 manifest）。"""
    try:
        if fmt == "parquet":
            import pyarrow.parquet as pq
            # 真的解碼全部資料（不只讀 metadata）；關閉多執行緒——pyarrow 的執行緒池在部分環境會讓程序結束時卡住
            return pq.ParquetFile(io.BytesIO(data)).read(use_threads=False).num_rows
        return sum(1 for line in gzip.decompress(data).decode("utf-8").splitlines() if line.strip())
    except Exception as e:  # noqa: BLE001
        raise ArchiveError(f"分片解碼失敗：{type(e).__name__}") from e


def _shards(rows, fmt_pref=False):
    """把 rows 切成 ≤ROWS_PER_SHARD 列、且編碼後 ≤PART_BYTES 的分片（過大就對半切）。"""
    out = []
    chunks = [rows[i:i + ROWS_PER_SHARD] for i in range(0, len(rows), ROWS_PER_SHARD)]
    queue = list(reversed(chunks))
    while queue:
        chunk = queue.pop()
        data, ext, fmt = encode_shard(chunk, fmt_pref)
        if len(data) > PART_BYTES and len(chunk) > 1:
            mid = len(chunk) // 2
            queue.append(chunk[mid:])
            queue.append(chunk[:mid])
            continue
        if len(data) > PART_BYTES:
            raise ArchiveError("單列即超過分片上限，無法匯出")
        out.append((data, ext, fmt, len(chunk)))
    return out


# ---------------------------------------------------------------- 讀取
def server_count(sb, table, col=None, bound=None) -> int:
    q = sb.table(table).select("*", count="exact")
    if col is not None and bound is not None:
        q = q.lt(col, bound)
    n = q.limit(1).execute().count
    if n is None:
        raise ArchiveError(f"{table} 取不到伺服器列數")
    return int(n)


def read_rows(sb, table, col=None, bound=None, order_cols=None):
    rows, start = [], 0
    order_cols = tuple(order_cols or ORDER_COLS.get(table) or ())
    while True:
        q = sb.table(table).select("*")
        if col is not None and bound is not None:
            q = q.lt(col, bound)
        for oc in order_cols:
            q = q.order(oc)
        part = q.range(start, start + PAGE - 1).execute().data or []
        rows.extend(part)
        if len(part) < PAGE:
            return rows
        start += PAGE


# ---------------------------------------------------------------- 主流程
def archive_before(sb, store, table, col=None, bound=None, order_cols=None, now=None, prefer_parquet=False, max_rows=None) -> dict:
    """匯出 table 內 col < bound 的全部列（col 為 None 則整張表），驗證通過回傳摘要；否則丟 ArchiveError。

    回傳 {"table","rows","parts","bytes","prefix","format","verified":True}。rows==0 時不寫任何物件、verified=True。
    """
    expected = server_count(sb, table, col, bound)
    if max_rows is not None and expected > max_rows:
        raise ArchiveError(f"{table} 待匯出 {expected} 列超過單次上限 {max_rows}，中止")
    if expected == 0:
        return {"table": table, "rows": 0, "parts": 0, "bytes": 0, "prefix": None, "format": None, "verified": True}
    rows = read_rows(sb, table, col, bound, order_cols)
    if len(rows) != expected:
        raise ArchiveError(f"{table} 讀出 {len(rows)} 列與伺服器計數 {expected} 不符")
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%S")
    first = str(rows[0].get(col))[:10] if col else "all"
    prefix = f"{table}/{first}_{bound or 'all'}_{stamp}"
    shards = _shards(rows, prefer_parquet)
    if sum(s[3] for s in shards) != expected:
        raise ArchiveError(f"{table} 分片列數合計與計數不符")
    try:
        store.ensure_bucket()
        parts = []
        for i, (data, ext, fmt, n) in enumerate(shards):
            name = f"{prefix}/part-{i:03d}.{ext}"
            store.put(name, data)
            parts.append({"name": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                          "rows": n, "format": fmt})
        manifest = {"version": MANIFEST_VERSION, "table": table, "column": col, "bound": bound,
                    "rows": expected, "parts": parts,
                    "saved_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        store.put(f"{prefix}/{MANIFEST_NAME}", json.dumps(manifest, ensure_ascii=False).encode("utf-8"))
    except ArchiveError:
        raise
    except Exception as e:  # noqa: BLE001
        raise ArchiveError(f"{table} 上傳失敗：{type(e).__name__}") from e
    verify_archive(store, prefix, expected)
    return {"table": table, "rows": expected, "parts": len(parts), "bytes": sum(p["bytes"] for p in parts),
            "prefix": prefix, "format": parts[0]["format"], "verified": True}


def verify_archive(store, prefix, expected_rows) -> None:
    """回讀 manifest 與全部分片：bytes、sha256、解碼後列數都要對，總列數必須等於 expected_rows。"""
    try:
        raw = store.get(f"{prefix}/{MANIFEST_NAME}")
    except Exception as e:  # noqa: BLE001
        raise ArchiveError(f"回讀 manifest 失敗：{type(e).__name__}") from e
    if raw is None:
        raise ArchiveError("回讀不到 manifest")
    m = json.loads(raw.decode("utf-8"))
    total = 0
    for p in m.get("parts", []):
        try:
            data = store.get(p["name"])
        except Exception as e:  # noqa: BLE001
            raise ArchiveError(f"回讀分片失敗：{type(e).__name__}") from e
        if data is None:
            raise ArchiveError(f"回讀缺少分片 {p['name']}")
        if len(data) != p["bytes"] or hashlib.sha256(data).hexdigest() != p["sha256"]:
            raise ArchiveError(f"分片 {p['name']} 校驗失敗（大小或 sha256 不符）")
        n = count_rows(data, p["format"])
        if n != p["rows"]:
            raise ArchiveError(f"分片 {p['name']} 解碼列數 {n} 與 manifest {p['rows']} 不符")
        total += n
    if total != int(m.get("rows", -1)) or total != int(expected_rows):
        raise ArchiveError(f"回讀總列數 {total} 與預期 {expected_rows} 不符")
