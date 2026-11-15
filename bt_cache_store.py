"""回測資料快取的永久備份：把 bt_cache 目錄打包後存到 Supabase Storage 私有 bucket。

為什麼不用 GitHub Release：本倉庫是公開 repo，Release 附件任何人都能下載，而快取內容是 FinMind／
yfinance 抓來的資料，不適合公開散布。Supabase Storage 的 bucket 設為私有（public=false），只有持有
金鑰的工作流程讀寫；免費方案容量足夠（單檔上限 50MB，因此切成 45MB 一片）。

為什麼不取代 actions/cache：actions/cache 讀取最快，保留為第一層加速；Storage 是第二層（合併進來，
同名檔案以修改時間較新者為準），並作為 7 天未讀取就被清除的風險之保險。

用法（工作流程內）：
    python bt_cache_store.py pull [目錄]    # 開跑前：把 Storage 上的快取合併回本地（不覆蓋較新的本地檔）
    python bt_cache_store.py push [目錄]    # 跑完後：把本地目錄打包上傳（先傳分片，最後才寫 manifest）

需要的設定（只列名稱，值放在 GitHub Secrets）：
    SUPABASE_URL                 （既有）
    SUPABASE_STORAGE_KEY         （選填）可寫入 Storage 的金鑰；未設定則退回 SUPABASE_KEY
    BT_CACHE_BUCKET              （選填）bucket 名稱，預設 bt-cache；push 時若不存在會自動建立（私有）

任何失敗都不讓回測中斷：pull 失敗就照常抓資料，push 失敗只印一行警告（actions/cache 仍會存）。
不印任何資料內容與金鑰，只印位元組數與分片數。
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tarfile
import tempfile
import time
from datetime import datetime, timezone

BUCKET_DEFAULT = "bt-cache"
PART_BYTES = 45 * 1024 * 1024          # 免費方案單檔上限 50MB，留緩衝
MANIFEST_NAME = "manifest.json"
MANIFEST_VERSION = 1
SKIP_SUFFIXES = (".tmp",)


class StoreError(RuntimeError):
    """可預期的錯誤（設定缺漏、校驗失敗、路徑不安全）。訊息不含金鑰。"""


# ---------------------------------------------------------------- 傳輸層（可注入假物件測試）
class SupabaseStorage:
    """極薄的 Supabase Storage REST 包裝。只用 requests（工作流程已安裝）。"""

    def __init__(self, url: str, key: str, bucket: str, session=None, timeout: int = 120):
        import requests  # 延遲匯入，測試環境不必連網
        self.base = url.rstrip("/") + "/storage/v1"
        self.bucket = bucket
        self.key = key
        self.timeout = timeout
        self.session = session or requests.Session()

    def _headers(self, extra=None):
        h = {"apikey": self.key, "Authorization": f"Bearer {self.key}"}
        if extra:
            h.update(extra)
        return h

    def ensure_bucket(self) -> None:
        r = self.session.post(f"{self.base}/bucket", headers=self._headers({"Content-Type": "application/json"}),
                              data=json.dumps({"id": self.bucket, "name": self.bucket, "public": False}),
                              timeout=self.timeout)
        # 200/201 = 新建；409 = 已存在，兩者都可以繼續
        if r.status_code not in (200, 201, 409):
            raise StoreError(f"建立 bucket 失敗 HTTP {r.status_code}")

    def put(self, path: str, data: bytes) -> None:
        r = self.session.post(f"{self.base}/object/{self.bucket}/{path}",
                              headers=self._headers({"x-upsert": "true", "Content-Type": "application/octet-stream"}),
                              data=data, timeout=self.timeout)
        if r.status_code not in (200, 201):
            raise StoreError(f"上傳 {path} 失敗 HTTP {r.status_code}")

    def get(self, path: str):
        """回傳 bytes；物件不存在回 None；其他錯誤丟 StoreError。"""
        r = self.session.get(f"{self.base}/object/authenticated/{self.bucket}/{path}",
                             headers=self._headers(), timeout=self.timeout)
        if r.status_code == 404:
            return None
        if r.status_code != 200:
            raise StoreError(f"下載 {path} 失敗 HTTP {r.status_code}")
        return r.content


def storage_from_env(session=None) -> SupabaseStorage:
    url = (os.environ.get("SUPABASE_URL") or "").strip()
    key = (os.environ.get("SUPABASE_STORAGE_KEY") or "").strip() or (os.environ.get("SUPABASE_KEY") or "").strip()
    if not url or not key:
        raise StoreError("缺少 SUPABASE_URL 或 SUPABASE_STORAGE_KEY/SUPABASE_KEY（只印名稱）")
    bucket = (os.environ.get("BT_CACHE_BUCKET") or "").strip() or BUCKET_DEFAULT
    return SupabaseStorage(url, key, bucket, session=session)


# ---------------------------------------------------------------- 打包／解包
def _iter_files(root: str):
    for dirpath, _dirs, files in os.walk(root):
        for name in sorted(files):
            if name.endswith(SKIP_SUFFIXES):
                continue
            full = os.path.join(dirpath, name)
            yield full, os.path.relpath(full, root).replace(os.sep, "/")


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def pack(root: str, out_path: str) -> int:
    """把 root 目錄內所有檔案打成 tar.gz（相對路徑）。回傳檔案數。"""
    n = 0
    with tarfile.open(out_path, "w:gz") as tf:
        for full, rel in _iter_files(root):
            tf.add(full, arcname=rel, recursive=False)
            n += 1
    return n


def _safe_member(name: str) -> bool:
    if not name or name.startswith("/") or "\\" in name:
        return False
    parts = name.split("/")
    return ".." not in parts and "" not in parts[:-1] and not name.endswith("/")


def unpack_merge(archive_path: str, root: str) -> dict:
    """把 tar.gz 合併進 root：本地檔較新（mtime 較大）就保留本地，否則以封存檔覆蓋。

    先整包驗證成員路徑與型態（只接受一般檔案），全部通過才開始寫入。
    """
    os.makedirs(root, exist_ok=True)
    stats = {"written": 0, "kept_local": 0}
    with tarfile.open(archive_path, "r:gz") as tf:
        members = tf.getmembers()
        for m in members:
            if not m.isfile() or not _safe_member(m.name):
                raise StoreError("快取封存內有不安全的成員，已整包拒絕")
        for m in members:
            dest = os.path.join(root, *m.name.split("/"))
            if os.path.exists(dest) and os.path.getmtime(dest) >= m.mtime:
                stats["kept_local"] += 1
                continue
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            src = tf.extractfile(m)
            tmp = dest + ".tmp"
            with open(tmp, "wb") as out:
                out.write(src.read())
            os.utime(tmp, (m.mtime, m.mtime))
            os.replace(tmp, dest)
            stats["written"] += 1
    return stats


# ---------------------------------------------------------------- 高階流程
def _prefix_for(root: str) -> str:
    return os.path.basename(os.path.normpath(root)) or "btcache"


def push(root: str, store) -> dict:
    if not os.path.isdir(root) or not any(True for _ in _iter_files(root)):
        return {"status": "nothing_to_backup"}
    prefix = _prefix_for(root)
    with tempfile.TemporaryDirectory() as td:
        arc = os.path.join(td, "cache.tar.gz")
        n_files = pack(root, arc)
        digest = _sha256_file(arc)
        size = os.path.getsize(arc)
        parts = 0
        store.ensure_bucket()
        with open(arc, "rb") as f:
            while True:
                chunk = f.read(PART_BYTES)
                if not chunk:
                    break
                store.put(f"{prefix}/part-{parts:03d}.tar.gz", chunk)
                parts += 1
        manifest = {
            "version": MANIFEST_VERSION,
            "parts": parts,
            "bytes": size,
            "sha256": digest,
            "files": n_files,
            "saved_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        store.put(f"{prefix}/{MANIFEST_NAME}", json.dumps(manifest).encode("utf-8"))
    return {"status": "ok", "files": n_files, "bytes": size, "parts": parts}


def pull(root: str, store) -> dict:
    prefix = _prefix_for(root)
    raw = store.get(f"{prefix}/{MANIFEST_NAME}")
    if raw is None:
        return {"status": "no_backup"}
    manifest = json.loads(raw.decode("utf-8"))
    if manifest.get("version") != MANIFEST_VERSION:
        raise StoreError("manifest 版本不符，略過")
    with tempfile.TemporaryDirectory() as td:
        arc = os.path.join(td, "cache.tar.gz")
        with open(arc, "wb") as f:
            for i in range(int(manifest["parts"])):
                data = store.get(f"{prefix}/part-{i:03d}.tar.gz")
                if data is None:
                    raise StoreError(f"缺少分片 {i}，整包放棄")
                f.write(data)
        if os.path.getsize(arc) != int(manifest["bytes"]) or _sha256_file(arc) != manifest["sha256"]:
            raise StoreError("快取校驗失敗（大小或 sha256 不符），未寫入本地")
        stats = unpack_merge(arc, root)
    stats["status"] = "ok"
    return stats


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) < 1 or argv[0] not in ("pull", "push"):
        print("用法：bt_cache_store.py pull|push [目錄]")
        return 2
    root = argv[1] if len(argv) > 1 else (os.environ.get("BT_CACHE_DIR") or "btcache")
    t0 = time.time()
    try:
        store = storage_from_env()
        res = pull(root, store) if argv[0] == "pull" else push(root, store)
        print(f"[回測快取備份] {argv[0]} {root}：{res}（{round(time.time() - t0)} 秒）")
    except Exception as e:  # noqa: BLE001 — 備份失敗不得中斷回測
        detail = str(e) if isinstance(e, StoreError) else type(e).__name__   # StoreError 訊息已確認不含金鑰
        print(f"[回測快取備份] {argv[0]} 未完成：{detail}（不影響回測，actions/cache 仍為後備）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
