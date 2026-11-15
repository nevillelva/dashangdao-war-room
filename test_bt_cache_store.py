"""bt_cache_store 離線測試：用記憶體假 Storage 模擬 push／pull，不連網、不需金鑰。"""
import io
import os
import pickle
import sys
import tarfile
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bt_cache_store as bcs  # noqa: E402


class FakeStorage:
    def __init__(self):
        self.objects = {}
        self.bucket_created = False
        self.fail_put_paths = set()

    def ensure_bucket(self):
        self.bucket_created = True

    def put(self, path, data):
        if path in self.fail_put_paths:
            raise bcs.StoreError(f"上傳 {path} 失敗 HTTP 500")
        self.objects[path] = bytes(data)

    def get(self, path):
        return self.objects.get(path)


def _write(path, payload, mtime=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(payload)
    if mtime is not None:
        os.utime(path, (mtime, mtime))


class BtCacheStoreTest(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.base = self._td.name
        self.src = os.path.join(self.base, "btcache")
        self.store = FakeStorage()

    def tearDown(self):
        self._td.cleanup()

    def _sample_tree(self):
        _write(os.path.join(self.src, "daily_adj", "2330_5.pkl"), pickle.dumps({"a": 1}))
        _write(os.path.join(self.src, "fm_inst", "2330_5.pkl"), pickle.dumps([1, 2, 3]))
        _write(os.path.join(self.src, "fm_inst", "junk.pkl.tmp"), b"partial")  # 應被略過

    def test_round_trip_restores_files_and_skips_tmp(self):
        self._sample_tree()
        res = bcs.push(self.src, self.store)
        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["files"], 2)
        self.assertTrue(self.store.bucket_created)
        self.assertIn("btcache/manifest.json", self.store.objects)

        dst = os.path.join(self.base, "restored", "btcache")  # 同名目錄才會對到同一個 prefix
        out = bcs.pull(dst, self.store)
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["written"], 2)
        with open(os.path.join(dst, "daily_adj", "2330_5.pkl"), "rb") as f:
            self.assertEqual(pickle.load(f), {"a": 1})
        self.assertFalse(os.path.exists(os.path.join(dst, "fm_inst", "junk.pkl.tmp")))

    def test_pull_without_manifest_is_noop(self):
        dst = os.path.join(self.base, "empty", "btcache")
        self.assertEqual(bcs.pull(dst, self.store), {"status": "no_backup"})
        self.assertFalse(os.path.exists(dst))

    def test_merge_keeps_newer_local_file(self):
        old_t = time.time() - 3600
        _write(os.path.join(self.src, "x.pkl"), b"OLD", mtime=old_t)
        bcs.push(self.src, self.store)
        dst = os.path.join(self.base, "dst", "btcache")
        _write(os.path.join(dst, "x.pkl"), b"NEWER-LOCAL", mtime=time.time())
        _write(os.path.join(dst, "y.pkl"), b"LOCAL-ONLY", mtime=time.time())
        stats = bcs.pull(dst, self.store)
        self.assertEqual(stats["kept_local"], 1)
        with open(os.path.join(dst, "x.pkl"), "rb") as f:
            self.assertEqual(f.read(), b"NEWER-LOCAL")
        with open(os.path.join(dst, "y.pkl"), "rb") as f:
            self.assertEqual(f.read(), b"LOCAL-ONLY")

    def test_checksum_mismatch_writes_nothing(self):
        _write(os.path.join(self.src, "a.pkl"), b"data")
        bcs.push(self.src, self.store)
        self.store.objects["btcache/part-000.tar.gz"] = b"corrupted"
        dst = os.path.join(self.base, "dst", "btcache")
        with self.assertRaises(bcs.StoreError):
            bcs.pull(dst, self.store)
        self.assertFalse(os.path.exists(os.path.join(dst, "a.pkl")))

    def test_multi_part_split_and_reassemble(self):
        _write(os.path.join(self.src, "big.bin"), os.urandom(3 * 1024 * 1024))
        old = bcs.PART_BYTES
        try:
            bcs.PART_BYTES = 1024 * 1024          # 強制切成多片
            res = bcs.push(self.src, self.store)
        finally:
            bcs.PART_BYTES = old
        self.assertGreaterEqual(res["parts"], 3)
        dst = os.path.join(self.base, "dst", "btcache")
        self.assertEqual(bcs.pull(dst, self.store)["written"], 1)
        with open(os.path.join(self.src, "big.bin"), "rb") as a, open(os.path.join(dst, "big.bin"), "rb") as b:
            self.assertEqual(a.read(), b.read())

    def test_unsafe_archive_member_is_rejected(self):
        arc = os.path.join(self.base, "evil.tar.gz")
        with tarfile.open(arc, "w:gz") as tf:
            info = tarfile.TarInfo("../escape.pkl")
            info.size = 3
            tf.addfile(info, io.BytesIO(b"bad"))
        dst = os.path.join(self.base, "dst", "btcache")
        with self.assertRaises(bcs.StoreError):
            bcs.unpack_merge(arc, dst)
        self.assertFalse(os.path.exists(os.path.join(self.base, "escape.pkl")))

    def test_push_failure_raises_and_manifest_not_written(self):
        _write(os.path.join(self.src, "a.pkl"), b"data")
        self.store.fail_put_paths.add("btcache/part-000.tar.gz")
        with self.assertRaises(bcs.StoreError):
            bcs.push(self.src, self.store)
        self.assertNotIn("btcache/manifest.json", self.store.objects)

    def test_empty_dir_is_not_pushed(self):
        os.makedirs(self.src)
        self.assertEqual(bcs.push(self.src, self.store)["status"], "nothing_to_backup")
        self.assertEqual(self.store.objects, {})

    def test_missing_config_names_only(self):
        keys = ["SUPABASE_URL", "SUPABASE_KEY", "SUPABASE_STORAGE_KEY"]
        saved = {k: os.environ.pop(k, None) for k in keys}
        try:
            with self.assertRaises(bcs.StoreError) as cm:
                bcs.storage_from_env()
            msg = str(cm.exception)
            self.assertIn("SUPABASE_URL", msg)
            self.assertNotIn("=", msg)
        finally:
            for k, v in saved.items():
                if v is not None:
                    os.environ[k] = v

    def test_main_never_raises_on_missing_config(self):
        keys = ["SUPABASE_URL", "SUPABASE_KEY", "SUPABASE_STORAGE_KEY"]
        saved = {k: os.environ.pop(k, None) for k in keys}
        try:
            self.assertEqual(bcs.main(["push", self.src]), 0)
            self.assertEqual(bcs.main(["pull", self.src]), 0)
        finally:
            for k, v in saved.items():
                if v is not None:
                    os.environ[k] = v

    def test_ensure_bucket_treats_duplicate_400_as_exists(self):
        """Supabase 對已存在的 bucket 回 HTTP 400 + statusCode 409；其他 400 / 5xx 仍要丟錯。"""
        class R:
            def __init__(self, code, body):
                self.status_code, self._b = code, body
            def json(self):
                if self._b is None:
                    raise ValueError("no json")
                return self._b

        class S:
            def __init__(self, resp):
                self.resp = resp
            def post(self, *a, **k):
                return self.resp

        def mk(resp):
            return bcs.SupabaseStorage("https://x.supabase.co", "k", "b", session=S(resp))

        mk(R(200, {})).ensure_bucket()
        mk(R(409, {})).ensure_bucket()
        mk(R(400, {"statusCode": "409", "error": "Duplicate", "message": "The resource already exists"})).ensure_bucket()
        for bad in (R(400, {"statusCode": "400", "error": "Bad Request"}), R(400, None), R(500, {}), R(401, {})):
            with self.assertRaises(bcs.StoreError):
                mk(bad).ensure_bucket()


if __name__ == "__main__":
    unittest.main()
