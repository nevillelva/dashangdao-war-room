"""離線測試用的假 Supabase 資料庫與假 Storage（不連網、不需金鑰）。非測試檔，供 test_db_*.py 匯入。"""
import json


class FakeDB:
    """支援 select(cols, count=)、lt、order、range、limit、delete、rpc 的最小 PostgREST 替身。"""

    def __init__(self, data, rpc_handlers=None):
        self.data = data                     # {table: [dict]}
        self.deletes = []                    # [(table, col, bound)]
        self.rpc_calls = []
        self.rpc_handlers = rpc_handlers or {}
        self.fail_select_tables = set()

    def table(self, t):
        return _Q(self, t)

    def rpc(self, name, params):
        self.rpc_calls.append((name, dict(params)))
        h = self.rpc_handlers.get(name)
        if h is None:
            raise RuntimeError("rpc 不存在")
        return _R(h(params))


class _R:
    def __init__(self, data=None, count=None):
        self.data, self.count = data, count

    def execute(self):
        return self


class _Q:
    def __init__(self, db, t):
        self.db, self.t = db, t
        self.mode, self.cols, self.want_count = "select", None, False
        self.lt_f, self.order_cols, self.lim, self.rng = None, [], None, None

    def select(self, cols="*", count=None):
        self.cols, self.want_count = cols, bool(count)
        return self

    def lt(self, col, v):
        self.lt_f = (col, v)
        return self

    def order(self, col, desc=False):
        self.order_cols.append(col)
        return self

    def limit(self, n):
        self.lim = n
        return self

    def range(self, a, b):
        self.rng = (a, b)
        return self

    def delete(self):
        self.mode = "delete"
        return self

    def _filtered(self):
        rows = self.db.data[self.t]
        if self.lt_f:
            c, v = self.lt_f
            rows = [r for r in rows if str(r[c])[:10] < v]
        return rows

    def execute(self):
        if self.t in self.db.fail_select_tables:
            raise RuntimeError("db down")
        rows = self._filtered()
        if self.mode == "delete":
            c, v = self.lt_f
            self.db.deletes.append((self.t, c, v))
            self.db.data[self.t] = [r for r in self.db.data[self.t] if r not in rows]
            return _R([])
        if self.order_cols:
            rows = sorted(rows, key=lambda r: tuple(str(r.get(c)) for c in self.order_cols))
        total = len(rows)
        if self.rng:
            rows = rows[self.rng[0]:self.rng[1] + 1]
        elif self.lim is not None:
            rows = rows[:self.lim]
        if self.cols not in (None, "*"):
            keep = [c.strip() for c in self.cols.split(",")]
            rows = [{k: r[k] for k in keep if k in r} for r in rows]
        return _R(rows, total if self.want_count else None)


class FakeStorage:
    """記憶體 Storage；可模擬上傳失敗、回讀損毀、回讀缺檔。"""

    def __init__(self):
        self.objects = {}
        self.bucket_created = False
        self.fail_put = False
        self.corrupt_get = False
        self.hide_manifest = False

    def ensure_bucket(self):
        self.bucket_created = True

    def put(self, path, data):
        if self.fail_put:
            raise RuntimeError("上傳失敗 HTTP 500")
        self.objects[path] = bytes(data)

    def get(self, path):
        if self.hide_manifest and path.endswith("manifest.json"):
            return None
        d = self.objects.get(path)
        if d is not None and self.corrupt_get and not path.endswith("manifest.json"):
            return d[:-1] + bytes([(d[-1] + 1) % 256])
        return d

    def manifests(self):
        return {k: json.loads(v) for k, v in self.objects.items() if k.endswith("manifest.json")}
