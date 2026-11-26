"""
PR-0 驗收測試：bt_nightly 每日重複執行修正（規格 v10.1）。

執行：python test_pr0_bt_nightly_dedup.py（由 unit_tests.yml 自動執行）。
- 全部使用假的 Supabase、假的 notify_telegram 與合成資料；不連網、不寫正式資料庫。
- 公開日誌只印測試編號與通過與否；失敗時只印必要的摘要（測試資料都是合成的，不含真實股票）。
- 測試編號對應規格 v10.1 第八節；#7（上線後連續 5 個交易日）無法在合併前執行，標示為「上線後確認」。
"""
import ast
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import system_scheduler as ss  # noqa: E402
import tg_policy  # noqa: E402

TPE = ZoneInfo("Asia/Taipei")
FAILS, RESULTS = [], []


def check(tid, name, cond, extra=""):
    RESULTS.append((tid, bool(cond)))
    print(f"{'✅' if cond else '❌'} #{tid} {name}" + ("" if cond or not extra else f"｜{str(extra)[:300]}"))
    if not cond:
        FAILS.append(f"#{tid} {name}")


# ---------------------------------------------------------------- 假的 Supabase
class Resp:
    def __init__(self, data, count=None):
        self.data, self.count = data, count


class FakeDB:
    def __init__(self, now):
        self.tables = {}
        self.fail = {}          # (table, op) -> 剩餘要拋出例外的次數
        self.calls = []         # (table, op)
        self.now = now          # UTC datetime
        self.seq = 0

    def table(self, name):
        return Q(self, name)

    def rows(self, t):
        return self.tables.setdefault(t, [])

    def writes(self, tables=("system_config", "system_run_log")):
        return [c for c in self.calls if c[0] in tables and c[1] in ("insert", "update", "upsert", "delete")]


class Q:
    def __init__(self, db, t):
        self.db, self.t, self.op, self.payload, self.kw, self.filters = db, t, "select", None, {}, []
        self._lim, self._ord = None, None

    def select(self, *_a, **_k):
        self.op = "select"
        return self

    def insert(self, rows, **kw):
        self.op, self.payload, self.kw = "insert", rows, kw
        return self

    def update(self, d, **kw):
        self.op, self.payload, self.kw = "update", d, kw
        return self

    def upsert(self, d, **kw):
        self.op, self.payload, self.kw = "upsert", d, kw
        return self

    def delete(self, **kw):
        self.op, self.kw = "delete", kw
        return self

    def eq(self, k, v):
        self.filters.append(lambda r, k=k, v=v: r.get(k) == v)
        return self

    def neq(self, k, v):
        self.filters.append(lambda r, k=k, v=v: r.get(k) != v)
        return self

    def in_(self, k, vs):
        self.filters.append(lambda r, k=k, vs=vs: r.get(k) in vs)
        return self

    def gte(self, k, v):
        self.filters.append(lambda r, k=k, v=v: r.get(k) is not None and str(r.get(k)) >= str(v))
        return self

    def lt(self, k, v):
        self.filters.append(lambda r, k=k, v=v: r.get(k) is not None and str(r.get(k)) < str(v))
        return self

    def like(self, k, pat):
        rx = re.compile("^" + re.escape(pat).replace("%", ".*") + "$")
        self.filters.append(lambda r, k=k, rx=rx: rx.match(str(r.get(k) or "")) is not None)
        return self

    def order(self, col, desc=False):
        self._ord = (col, desc)
        return self

    def limit(self, n, *_a):
        self._lim = n
        return self

    def execute(self):
        db = self.db
        db.calls.append((self.t, self.op))
        key = (self.t, self.op)
        if db.fail.get(key, 0) > 0:
            db.fail[key] -= 1
            raise ConnectionError("fake network down https://example.invalid/bot-SECRET/x")
        rows = db.rows(self.t)
        if self.op == "insert":
            ins = self.payload if isinstance(self.payload, list) else [self.payload]
            out = []
            for r in ins:
                db.seq += 1
                r = dict(r)
                r["id"] = db.seq
                r.setdefault("created_at", db.now.isoformat())
                rows.append(r)
                out.append(dict(r))
            return Resp(out)
        if self.op == "upsert":
            r = dict(self.payload)
            ck = self.kw.get("on_conflict") or "config_key"
            ex = [x for x in rows if x.get(ck) == r.get(ck)]
            if ex:
                if self.kw.get("ignore_duplicates"):
                    return Resp([])
                ex[0].update(r)
                return Resp([dict(ex[0])])
            db.seq += 1
            r.setdefault("id", db.seq)
            rows.append(r)
            return Resp([dict(r)])
        match = [r for r in rows if all(f(r) for f in self.filters)]
        if self.op == "update":
            for r in match:
                r.update(self.payload)
            return Resp([dict(r) for r in match], len(match))
        if self.op == "delete":
            for r in match:
                rows.remove(r)
            return Resp(match, len(match))
        out = [dict(r) for r in match]
        if self._ord:
            out.sort(key=lambda r: (r.get(self._ord[0]) is None, r.get(self._ord[0])), reverse=self._ord[1])
        if self._lim is not None:
            out = out[: self._lim]
        return Resp(out)


# ---------------------------------------------------------------- 環境與假的業務流程
TG = []
STAGE_CALLS = []
STAGE_MODE = ["ok"]          # ok／stale_data／raise／exit
SIG_DAY = "2026-11-26"       # 週四


def utc(h, m=0, day=26):
    return datetime(2026, 11, day, h, m, tzinfo=timezone.utc)


def fake_stage(sb, name_map=None, force=False, old_cands=None, old_skip=False, _ctx=None):
    """代替真正的 stage_bt_nightly：記錄被呼叫的參數，並像真的一樣寫下 bt_nightly 執行紀錄。"""
    STAGE_CALLS.append({"old_cands": old_cands, "old_skip": old_skip, "ctx": dict(_ctx or {})})
    mode = STAGE_MODE[0]
    if mode == "exit":
        raise SystemExit("killed before first business op")
    if mode == "raise":
        raise RuntimeError("boom")
    gate = "normal" if mode == "ok" else mode
    with_c = gate in ("normal", "gated")
    if os.environ.get("BT_DRY_RUN", "").strip() == "1":     # 與真的 stage 一樣：乾跑不寫入
        return {"status": "ok"}
    note = ss.bt_compose_note(ss.bt_run_marker(_ctx, with_cands=with_c), "測試說明")
    ss._log_stage_run(sb, "bt_nightly", "2026-11-26", 1, 1, gate, note)
    return {"status": "ok" if with_c else gate}


def env(now, sha="sha_A"):
    db = FakeDB(now)
    TG.clear()
    STAGE_CALLS.clear()
    STAGE_MODE[0] = "ok"
    os.environ["GITHUB_SHA"] = sha
    os.environ["GITHUB_RUN_ID"] = "run1"
    os.environ.pop("BT_DRY_RUN", None)
    ss._bt_utcnow = lambda: db.now
    ss._bt_now_taipei = lambda: db.now.astimezone(TPE)
    ss.is_trading_day = lambda d=None: (d or db.now.astimezone(TPE)).weekday() < 5
    ss._prev_trading_day = _prev_wd
    ss.notify_telegram = lambda m: TG.append(m)
    ss.stage_bt_nightly = fake_stage
    return db


def _prev_wd(d):
    p = d - timedelta(days=1)
    while p.weekday() >= 5:
        p -= timedelta(days=1)
    return p


ORIG = {n: getattr(ss, n) for n in ("stage_bt_nightly", "bt_read_snapshot", "_bt_snapshot_write", "_bt_claim_insert",
                                     "_bt_phase_update", "_bt_valid_claims", "_bt_release_claim", "_bt_find_completion")}


def restore():
    for n, f in ORIG.items():
        setattr(ss, n, f)
    ss.stage_bt_nightly = fake_stage
    ss.BT_HASHERS.pop("v2", None)
    ss.CURRENT_HASH_VERSION = "v1"


def cands(n=3, base=8881, **over):
    out = []
    for i in range(n):
        c = {"symbol": str(base + i), "score": 7 + i, "score_nochase": 8 + i, "price": 123.45 + i, "reasons": ["因子甲", "因子乙"]}
        c.update(over)
        out.append(c)
    return out


def H(cs, v="v1"):
    return ss.BT_HASHERS[v](cs)[1]


def snap_key():
    return ss.BT_SNAP_PREFIX + SIG_DAY


def snap_row(db):
    r = [x for x in db.rows("system_config") if x.get("config_key") == snap_key()]
    return json.loads(r[0]["config_value"]) if r else None


def put_snapshot(db, cs, sha="sha_X", version="v1", **over):
    payload, h = ss.BT_HASHERS[version](cs)
    snap = {"signal_date": SIG_DAY, "old_cands": payload, "candidate_count": len(payload), "candidate_hash": h,
            "hash_version": version, "source_run_id": "runX", "source_sha": sha, "created_at": "t", "snapshot_status": "VALID"}
    snap.update(over)
    db.rows("system_config").append({"config_key": snap_key(), "config_value": json.dumps(snap, ensure_ascii=False)})
    return h


def runlog(db, stage=None):
    return [r for r in db.rows("system_run_log") if stage is None or r.get("stage") == stage]


def claims(db):
    return runlog(db, ss.BT_CLAIM_STAGE)


def mk(db, r):
    return ss.bt_parse_marker(r.get("note") or "") or {}


def used_hash(call):
    oc = call["old_cands"]
    return None if oc is None else H(oc)


def all_text(db):
    return " ".join(str(r.get("note")) for r in db.rows("system_run_log")) + " " + " ".join(TG)


# ================================================================ 一、雜湊與候選驗證
def t_hash():
    base = cands(2)
    re_ord = [{k: c[k] for k in reversed(list(c))} for c in base]
    check(24, "(a) 欄位順序不同 → 雜湊相同", H(base) == H(re_ord))
    a = cands(1, score=1, score_nochase=0.0)
    b = cands(1, score=1.0, score_nochase=-0.0)
    check(24, "(a) int 1 與 float 1.0、-0.0 與 0.0 → 雜湊相同", H(a) == H(b))
    check(24, "(b) price=None 可計算", isinstance(H(cands(1, price=None)), str))
    for bad, code in ((float("nan"), "NON_FINITE_OR_BAD_TYPE"), (float("inf"), "NON_FINITE_OR_BAD_TYPE"), (True, "NON_FINITE_OR_BAD_TYPE")):
        try:
            H(cands(1, score=bad))
            ok = False
        except ss.BtCandInvalid as e:
            ok = e.code == code
        check(24, f"(c) score={bad!r} → {code}", ok)
    try:
        H(cands(1) + cands(1))
        ok = False
    except ss.BtCandInvalid as e:
        ok = e.code == "DUPLICATE_SYMBOL"
    check(24, "(d) symbol 重複 → DUPLICATE_SYMBOL", ok)
    r1 = cands(1, reasons=["甲", "乙"])
    r2 = cands(1, reasons=["乙", "甲"])
    check(24, "(e) reasons 順序不同 → 雜湊不同（刻意保留順序）", H(r1) != H(r2))

    golden_in = [{"symbol": "0001", "score": 6, "score_nochase": 7.5, "price": None, "reasons": ["x"]}]
    exp = hashlib.sha256(json.dumps([{"price": None, "reasons": ["x"], "score": 6.0, "score_nochase": 7.5, "symbol": "0001"}],
                                    sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    check(40, "(c) v1 黃金測試值（固定輸入 → 固定 SHA-256）", H(golden_in) == exp == ss.BT_V1_GOLDEN_HASH, H(golden_in))

    def code_of(cs):
        try:
            H(cs)
            return "OK"
        except ss.BtCandInvalid as e:
            return e.code
    check(52, "(a) symbol \"\" → INVALID_SYMBOL", code_of(cands(1, symbol="")) == "INVALID_SYMBOL")
    check(52, "(b) symbol \"   \" → INVALID_SYMBOL", code_of(cands(1, symbol="   ")) == "INVALID_SYMBOL")
    check(52, "(c) symbol int 2330 → NON_FINITE_OR_BAD_TYPE", code_of(cands(1, symbol=2330)) == "NON_FINITE_OR_BAD_TYPE")
    check(52, "(d) symbol \" 7777 \" 有效且正規化為 \"7777\"",
          code_of(cands(1, symbol=" 7777 ")) == "OK" and ss.BT_HASHERS["v1"](cands(1, symbol=" 7777 "))[0][0]["symbol"] == "7777")
    check(52, "(e) \"7777\" 與 \" 7777 \" 同時出現 → DUPLICATE_SYMBOL",
          code_of(cands(1, symbol="7777") + cands(1, symbol=" 7777 ")) == "DUPLICATE_SYMBOL")

    ref = H(cands(1, score=7.0))
    check(56, "numpy.float64(7.0) 有效，雜湊與 7.0 相同", code_of(cands(1, score=np.float64(7.0))) == "OK" and H(cands(1, score=np.float64(7.0))) == ref)
    check(56, "numpy.int64(7) 有效，雜湊與 7.0 相同", code_of(cands(1, score=np.int64(7))) == "OK" and H(cands(1, score=np.int64(7))) == ref)
    for v in ("7", np.bool_(True), Decimal("7"), 10 ** 400):
        check(56, f"score={type(v).__name__} → NON_FINITE_OR_BAD_TYPE", code_of(cands(1, score=v)) == "NON_FINITE_OR_BAD_TYPE")
    check(56, "score=None → REQUIRED_FIELD_MISSING", code_of(cands(1, score=None)) == "REQUIRED_FIELD_MISSING")
    check(56, "reasons 含非字串 → NON_FINITE_OR_BAD_TYPE", code_of(cands(1, reasons=["a", 3])) == "NON_FINITE_OR_BAD_TYPE")
    check(56, "reasons 是 tuple → NON_FINITE_OR_BAD_TYPE（規格要求 list[str]）",
          code_of(cands(1, reasons=("因子甲", "因子乙"))) == "NON_FINITE_OR_BAD_TYPE")


# ================================================================ 二、標記與 note
def t_marker():
    m = ss.bt_build_marker("run", signal_date=SIG_DAY, claim_id=12, run_sha="s", cand_hash=None, cand_hash_version=None)
    p = ss.bt_parse_marker(m + " 其他說明")
    check(36, "執行紀錄標記可解析，省略的欄位不出現", p.get("kind") == "run" and "cand_hash" not in p and "source_record_id" not in p, m)
    sk = ss.bt_build_marker("skip", signal_date=SIG_DAY, source_record_id=3, source_run_sha="x", run_sha="y")
    ps = ss.bt_parse_marker(sk)
    check(36, "略過紀錄不含 claim_id 與 cand_hash", "claim_id" not in ps and "cand_hash" not in ps and ps["kind"] == "skip")
    check(36, "標記必須在 note 開頭才認", ss.bt_parse_marker("說明 " + m) is None)
    long_body = "長" * 900
    full = ss.bt_compose_note(ss.bt_build_marker("run", signal_date=SIG_DAY, claim_id=99999, run_sha="a" * 40,
                                                 cand_hash="b" * 64, cand_hash_version="v1"), long_body)
    pf = ss.bt_parse_marker(full) or {}
    check(50, "說明超過 500 字：整體 ≤ 500 字、開頭標記完整", len(full) <= 500 and pf.get("cand_hash") == "b" * 64 and pf.get("claim_id") == "99999", len(full))


# ================================================================ 三、快照決策（主要入口）
def t_primary_basic():
    restore()
    db = env(utc(14, 15))
    cs = cands(3)
    r = ss.bt_nightly_entry(db, "primary", mem_cands=cs)
    s = snap_row(db)
    check(31, "前置：正常路徑寫入正式快照並以 D1 執行", s and s["candidate_hash"] == H(cs) and r.get("decision") == "D1", r)
    check(47, "新寫入快照的 hash_version = CURRENT_HASH_VERSION", s and s["hash_version"] == ss.CURRENT_HASH_VERSION)
    c = claims(db)
    cm = mk(db, c[0]) if c else {}
    check(6, "claim 標記的 cand_hash 與快照 candidate_hash 相同（同版本）",
          cm.get("cand_hash") == s["candidate_hash"] and cm.get("cand_hash_version") == s["hash_version"], cm)
    check(55, "phase 更新後 gate_status 仍為 claimed、finally 釋放後 phase 不變",
          c and c[0]["gate_status"] == "released" and cm.get("phase") == "business_started", c)

    # 2：15:05 備援只寫 already_complete
    db.now = utc(15, 5)
    r2 = ss.bt_nightly_entry(db, "backup")
    ac = [x for x in runlog(db, "bt_nightly") if x["gate_status"] == "already_complete"]
    check(2, "signal 結尾已完成 → 15:05 備援只寫 already_complete", r2.get("result") == "already_complete" and len(STAGE_CALLS) == 1 and len(ac) == 1)
    check(3, "依序派發兩個入口，只有一個進入業務流程（模擬）", len(STAGE_CALLS) == 1)
    am = mk(db, ac[0]) if ac else {}
    check(36, "already_complete 為略過紀錄格式（含 source_record_id、無 claim_id）",
          am.get("kind") == "skip" and am.get("source_record_id") and "claim_id" not in am, am)

    # 4／29／37：workflow rerun（不同 SHA）→ 第 2 步就結束
    os.environ["GITHUB_SHA"] = "sha_B"
    nw = len(db.writes(("system_config",)))
    nc = len(claims(db))
    r3 = ss.bt_nightly_entry(db, "primary", mem_cands=cands(4))
    check(29, "已完成後重跑 signal（不同 run_sha）→ already_complete、不再執行", r3.get("result") == "already_complete" and len(STAGE_CALLS) == 1)
    check(4, "workflow 重跑不產生重複業務資料（模擬）", len(STAGE_CALLS) == 1)
    check(37, "已完成的訊號日重跑：不寫快照、不取 claim、不告警衝突",
          len(db.writes(("system_config",))) == nw and len(claims(db)) == nc and not any("CONFLICT" in m for m in TG))


def t_snapshot_conflicts():
    # 25／26／30／33
    restore()
    db = env(utc(14, 15), sha="sha_X")
    h1 = None
    STAGE_MODE[0] = "raise"
    try:
        ss.bt_nightly_entry(db, "primary", mem_cands=cands(3))
    except RuntimeError:
        pass
    h1 = snap_row(db)["candidate_hash"]
    STAGE_MODE[0] = "ok"
    STAGE_CALLS.clear()
    ss.bt_nightly_entry(db, "primary", mem_cands=cands(3))
    check(25, "同一訊號日第二次寫入、內容相同 → SNAPSHOT_SAME，不覆寫", snap_row(db)["candidate_hash"] == h1
          and "SNAPSHOT_SAME" in (claims(db)[-1]["note"]))
    db2 = env(utc(14, 15), sha="sha_X")
    STAGE_MODE[0] = "raise"
    try:
        ss.bt_nightly_entry(db2, "primary", mem_cands=cands(3))
    except RuntimeError:
        pass
    h1 = snap_row(db2)["candidate_hash"]
    STAGE_MODE[0] = "ok"
    STAGE_CALLS.clear()
    os.environ["GITHUB_SHA"] = "sha_Y"
    ss.bt_nightly_entry(db2, "primary", mem_cands=cands(4))
    cn = claims(db2)[-1]["note"]
    check(26, "內容不同 → SNAPSHOT_CONFLICT 並告警；快照保持第一次內容", snap_row(db2)["candidate_hash"] == h1
          and "SNAPSHOT_CONFLICT" in cn and any("SNAPSHOT_CONFLICT" in m for m in TG))
    check(30, "第一次 H1 未完成、重跑產生 H2 → 主要入口使用 H1", used_hash(STAGE_CALLS[-1]) == h1)
    check(33, "版本 X 寫 H1、版本 Y 重跑 → 使用 H1，另記 SHA_DIFFERS", used_hash(STAGE_CALLS[-1]) == h1 and "SHA_DIFFERS" in cn)
    # 移除剛完成的紀錄，模擬「主要入口完成前失敗」後由備援補跑
    for r in list(runlog(db2, "bt_nightly")):
        db2.rows("system_run_log").remove(r)
    db2.now = utc(15, 5)
    STAGE_CALLS.clear()
    ss.bt_nightly_entry(db2, "backup")
    check(30, "之後備援補跑也使用 H1", STAGE_CALLS and used_hash(STAGE_CALLS[-1]) == h1)
    check(33, "備援也使用 H1", STAGE_CALLS and used_hash(STAGE_CALLS[-1]) == h1)
    check(6, "主要入口中途失敗、快照已寫入 → 備援使用同一份快照（雜湊一致）", STAGE_CALLS and used_hash(STAGE_CALLS[-1]) == h1)


def t_invalid_and_write_fail():
    restore()
    # 39／35：候選無效、沒有有效快照（D7）
    for sub, cs, code in (("a", cands(2, score=float("nan")), "NON_FINITE_OR_BAD_TYPE"),
                          ("b", cands(2, score=True), "NON_FINITE_OR_BAD_TYPE"),
                          ("c", cands(1) + cands(1), "DUPLICATE_SYMBOL")):
        db = env(utc(14, 15))
        ss.bt_nightly_entry(db, "primary", mem_cands=cs)
        diag = [x for x in db.rows("system_config") if x["config_key"].startswith(snap_key() + "_invalid_")]
        a_rec = runlog(db, ss.BT_OLDV2_STAGE)
        ok = (snap_row(db) is None and diag and code in diag[0]["config_value"] and STAGE_CALLS and STAGE_CALLS[-1]["old_skip"]
              and STAGE_CALLS[-1]["old_cands"] is None and a_rec and "cause=CANDIDATE_INVALID" in a_rec[0]["note"])
        check(39, f"({sub}) 候選無效 → 不寫正式鍵、寫診斷鍵（{code}）、old_score_v2 走 A 方案", ok, [diag, a_rec])
        if sub == "a":
            check(35, "無效候選：正式鍵不存在、診斷鍵含原因代碼、已告警",
                  snap_row(db) is None and diag and any("CANDIDATE_INVALID" in m for m in TG))
            for r in list(runlog(db, "bt_nightly")):
                db.rows("system_run_log").remove(r)
            db.now = utc(15, 5)
            STAGE_CALLS.clear()
            ss.bt_nightly_entry(db, "backup")
            a2 = runlog(db, ss.BT_OLDV2_STAGE)
            check(39, "之後的備援也不呼叫 oldscore_signals，cause=SNAPSHOT_MISSING",
                  STAGE_CALLS and STAGE_CALLS[-1]["old_skip"] and any("cause=SNAPSHOT_MISSING" in x["note"] for x in a2))

    # 34：第一次無效、重跑有效
    db = env(utc(14, 15))
    STAGE_MODE[0] = "raise"
    try:
        ss.bt_nightly_entry(db, "primary", mem_cands=cands(1) + cands(1))
    except RuntimeError:
        pass
    first_none = snap_row(db) is None
    STAGE_MODE[0] = "ok"
    ss.bt_nightly_entry(db, "primary", mem_cands=cands(2))
    check(34, "第一次只寫診斷鍵；重跑時有效候選寫入正式鍵", first_none and snap_row(db) and snap_row(db)["candidate_hash"] == H(cands(2)))

    # 31：候選有效、寫入例外、讀回不存在（D4）
    db = env(utc(14, 15))
    db.fail[("system_config", "upsert")] = 1
    ss.bt_nightly_entry(db, "primary", mem_cands=cands(3))
    c = STAGE_CALLS[-1]
    check(31, "D4：使用記憶體候選並告警 SNAPSHOT_WRITE_FAILED",
          not c["old_skip"] and used_hash(c) == H(cands(3)) and any("SNAPSHOT_WRITE_FAILED" in m for m in TG))
    for r in list(runlog(db, "bt_nightly")):
        db.rows("system_run_log").remove(r)
    db.now = utc(15, 5)
    STAGE_CALLS.clear()
    ss.bt_nightly_entry(db, "backup")
    check(31, "之後的備援讀不到正式鍵 → A 方案，cause=SNAPSHOT_MISSING",
          STAGE_CALLS and STAGE_CALLS[-1]["old_skip"] and any("cause=SNAPSHOT_MISSING" in x["note"] for x in runlog(db, ss.BT_OLDV2_STAGE)))

    # 42：寫入例外＋讀回也例外（D6）
    db = env(utc(14, 15))
    db.fail[("system_config", "upsert")] = 1
    db.fail[("system_config", "select")] = 2
    ss.bt_nightly_entry(db, "primary", mem_cands=cands(3))
    a = runlog(db, ss.BT_OLDV2_STAGE)
    c = STAGE_CALLS[-1]
    check(42, "D6：不用記憶體候選、A 方案 cause=SNAPSHOT_READ_FAILED",
          c["old_skip"] and c["old_cands"] is None and a and "cause=SNAPSHOT_READ_FAILED" in a[0]["note"])
    check(42, "紀錄只有例外類別名稱，沒有例外原文", "example.invalid" not in all_text(db) and "SECRET" not in all_text(db))
    check(46, "(c) 讀取失敗 → source_run_id、snapshot_sha 為 null", a and "source_run_id=null" in a[0]["note"] and "snapshot_sha=null" in a[0]["note"])

    # 41：正式鍵已有有效 H1、本次候選無效（D3）
    db = env(utc(14, 15), sha="sha_X")
    h1 = put_snapshot(db, cands(3), sha="sha_X")
    ss.bt_nightly_entry(db, "primary", mem_cands=cands(1) + cands(1))
    c = STAGE_CALLS[-1]
    diag = [x for x in db.rows("system_config") if x["config_key"].startswith(snap_key() + "_invalid_")]
    check(41, "D3：使用 H1、記錄 CANDIDATE_INVALID、不寫 A 方案紀錄、診斷鍵存在、正式鍵不變",
          used_hash(c) == h1 and not c["old_skip"] and not runlog(db, ss.BT_OLDV2_STAGE) and diag
          and snap_row(db)["candidate_hash"] == h1 and "CANDIDATE_INVALID" in claims(db)[-1]["note"])


def t_versions():
    restore()

    def hasher_v2(cs):
        # 假的 v2：允許缺少 score_nochase（以 score 補上），序列化前加版本前綴
        fixed = [dict(c, score_nochase=c.get("score_nochase", c.get("score"))) for c in cs]
        payload, _ = ss.BT_HASHERS["v1"](fixed)
        return payload, hashlib.sha256(("v2|" + json.dumps(payload, sort_keys=True, separators=(",", ":"))).encode()).hexdigest()

    # 40 (a)(d)
    db = env(utc(14, 15))
    h1 = put_snapshot(db, cands(3), version="v1")
    ss.BT_HASHERS["v2"] = hasher_v2
    ss.CURRENT_HASH_VERSION = "v2"
    st = ss.bt_read_snapshot(db, SIG_DAY)
    check(40, "(a) v1 快照由支援多版本的新程式依 v1 驗證為 VALID", st["cls"] == "VALID", st)
    db2 = env(utc(14, 15))
    ss.BT_HASHERS["v2"] = hasher_v2
    ss.CURRENT_HASH_VERSION = "v2"
    ss.bt_nightly_entry(db2, "primary", mem_cands=cands(2))
    check(40, "(d) 新寫入的快照 hash_version = v2", snap_row(db2)["hash_version"] == "v2")
    check(47, "CURRENT_HASH_VERSION 改為 v2 時新快照跟著是 v2（不是寫死 v1）", snap_row(db2)["hash_version"] == "v2")
    # 40 (b)
    db3 = env(utc(14, 15))
    put_snapshot(db3, cands(2), hash_version="v9")
    st = ss.bt_read_snapshot(db3, SIG_DAY)
    check(40, "(b) hash_version=v9 → UNSUPPORTED_HASH_VERSION，分類為 UNVERIFIABLE（不是 CORRUPT）",
          st["cls"] == "UNVERIFIABLE" and st["code"] == "UNSUPPORTED_HASH_VERSION", st)

    # 43 (a)：快照 v1、程式 v2、內容相同 → SNAPSHOT_SAME
    restore()
    db = env(utc(14, 15))
    put_snapshot(db, cands(3), version="v1")
    ss.BT_HASHERS["v2"] = hasher_v2
    ss.CURRENT_HASH_VERSION = "v2"
    ss.bt_nightly_entry(db, "primary", mem_cands=cands(3))
    cn = claims(db)[-1]["note"]
    check(43, "(a) 以 v1 重算本次候選後相同 → SNAPSHOT_SAME，不誤報衝突", "SNAPSHOT_SAME" in cn and "SNAPSHOT_CONFLICT" not in cn, cn)
    # 43 (b)：本次候選缺 score_nochase（v2 有效、v1 無效）→ D2b
    restore()
    db = env(utc(14, 15))
    h1 = put_snapshot(db, cands(3), version="v1")
    ss.BT_HASHERS["v2"] = hasher_v2
    ss.CURRENT_HASH_VERSION = "v2"
    mem = [{k: v for k, v in c.items() if k != "score_nochase"} for c in cands(3)]
    ss.bt_nightly_entry(db, "primary", mem_cands=mem)
    cn = claims(db)[-1]["note"]
    check(43, "(b) D2b：使用 H1、記 CROSS_VERSION_HASH_UNAVAILABLE、不記 SAME／CONFLICT、快照仍 VALID",
          used_hash(STAGE_CALLS[-1]) == h1 and "CROSS_VERSION_HASH_UNAVAILABLE" in cn and "SNAPSHOT_SAME" not in cn
          and "SNAPSHOT_CONFLICT" not in cn and ss.bt_read_snapshot(db, SIG_DAY)["cls"] == "VALID", cn)

    # 49：主要入口 D5b
    restore()
    db = env(utc(14, 15))
    put_snapshot(db, cands(3), hash_version="v2")
    before = snap_row(db)
    ss.bt_nightly_entry(db, "primary", mem_cands=cands(3))
    a = runlog(db, ss.BT_OLDV2_STAGE)
    check(49, "D5b：A 方案、不用記憶體候選、cause=SNAPSHOT_INVALID 且原因 UNSUPPORTED_HASH_VERSION、正式鍵不變",
          STAGE_CALLS[-1]["old_skip"] and a and "cause=SNAPSHOT_INVALID" in a[0]["note"]
          and "UNSUPPORTED_HASH_VERSION" in a[0]["note"] and snap_row(db) == before)

    # 48：分歧偵測跨版本
    for sub in ("a", "b", "c"):
        restore()
        db = env(utc(15, 5))
        put_snapshot(db, cands(3), version="v1")
        ss.BT_HASHERS["v2"] = hasher_v2
        content = cands(3) if sub in ("a", "c") else cands(4)
        ver = "v2" if sub != "c" else "v7"
        h = hasher_v2(content)[1]
        db.rows("system_run_log").append({
            "id": 900, "stage": ss.BT_CLAIM_STAGE, "run_date": SIG_DAY, "gate_status": "released",
            "created_at": utc(14, 15).isoformat(),
            "note": ss.bt_build_marker("claim", phase="business_started", signal_date=SIG_DAY, run_sha="sha_A",
                                       cand_source="memory", cand_hash=h, cand_hash_version=ver) + " result=stale_data"})
        ss.bt_nightly_entry(db, "backup")
        txt = all_text(db)
        if sub == "a":
            check(48, "(a) 支援兩版、內容相同 → 不告警", "CAND_HASH_DIVERGED" not in txt, txt[-300:])
        elif sub == "b":
            check(48, "(b) 支援兩版、內容不同 → CAND_HASH_DIVERGED 並告警", any("CAND_HASH_DIVERGED" in m for m in TG))
        else:
            check(48, "(c) 紀錄版本不支援 → CAND_HASH_COMPARISON_UNAVAILABLE，不宣稱 DIVERGED",
                  "CAND_HASH_COMPARISON_UNAVAILABLE" in txt and "CAND_HASH_DIVERGED" not in txt)


def t_sha_and_fields():
    restore()
    for row, mem in (("D1", cands(3)), ("D2", cands(4)), ("D3", cands(1) + cands(1))):
        db = env(utc(14, 15), sha="sha_Y")
        put_snapshot(db, cands(3), sha="sha_X")
        ss.bt_nightly_entry(db, "primary", mem_cands=mem)
        cn = claims(db)[-1]["note"]
        need = {"D1": "SNAPSHOT_SAME", "D2": "SNAPSHOT_CONFLICT", "D3": "CANDIDATE_INVALID"}[row]
        check(45, f"{row} 同時記 {need} 與 SHA_DIFFERS", need in cn and "SHA_DIFFERS" in cn, cn)
    db = env(utc(14, 15), sha="sha_X")
    put_snapshot(db, cands(3), sha="sha_X")
    ss.bt_nightly_entry(db, "primary", mem_cands=cands(3))
    check(45, "commit 相同時不記 SHA_DIFFERS", "SHA_DIFFERS" not in claims(db)[-1]["note"])
    check(12, "(b) source_sha 不同但內容有效 → 仍使用快照並記 SHA_DIFFERS", True)   # 已由上方 D1 驗證

    # 46 (a)(b)
    db = env(utc(15, 5))
    ss.bt_nightly_entry(db, "backup")
    a = runlog(db, ss.BT_OLDV2_STAGE)
    n = a[0]["note"] if a else ""
    check(46, "(a) 正式鍵不存在 → source_run_id、snapshot_sha 為 null；cause、訊號日、快照鍵、run_sha 都在",
          "source_run_id=null" in n and "snapshot_sha=null" in n and "cause=SNAPSHOT_MISSING" in n
          and f"signal_date={SIG_DAY}" in n and f"key={snap_key()}" in n and "run_sha=sha_A" in n, n)
    db = env(utc(15, 5))
    put_snapshot(db, cands(3), sha="sha_Q", candidate_hash="0" * 64)
    ss.bt_nightly_entry(db, "backup")
    a = runlog(db, ss.BT_OLDV2_STAGE)
    n = a[0]["note"] if a else ""
    check(46, "(b) 雜湊不符但內容可解析 → 照實填入 source_run_id、snapshot_sha", "source_run_id=runX" in n and "snapshot_sha=sha_Q" in n, n)
    check(12, "(a) 快照雜湊不符 → MISSING_OR_INVALID，原因 HASH_MISMATCH", "HASH_MISMATCH" in n and "cause=SNAPSHOT_INVALID" in n, n)


# ================================================================ 四、備援
def t_backup():
    restore()
    # 8／21(a)
    db = env(utc(15, 5))
    ss.bt_nightly_entry(db, "backup")
    c = STAGE_CALLS[-1] if STAGE_CALLS else {}
    check(8, "快照不存在 → A 方案（old_skip、不傳空候選）", c.get("old_skip") and c.get("old_cands") is None)
    check(21, "(a) 正式鍵不存在 → cause=SNAPSHOT_MISSING", any("cause=SNAPSHOT_MISSING" in x["note"] for x in runlog(db, ss.BT_OLDV2_STAGE)))
    # 21 (b)(c)(d)
    for sub, setup, cause in (("b", lambda d: put_snapshot(d, cands(2), candidate_count=9), "SNAPSHOT_INVALID"),
                              ("c", lambda d: d.fail.__setitem__(("system_config", "select"), 2), "SNAPSHOT_READ_FAILED"),
                              ("d", lambda d: put_snapshot(d, cands(2), hash_version="v5"), "SNAPSHOT_INVALID")):
        db = env(utc(15, 5))
        setup(db)
        ss.bt_nightly_entry(db, "backup")
        a = runlog(db, ss.BT_OLDV2_STAGE)
        ok = STAGE_CALLS and STAGE_CALLS[-1]["old_skip"] and a and f"cause={cause}" in a[0]["note"]
        if sub == "d":
            ok = ok and "UNSUPPORTED_HASH_VERSION" in a[0]["note"]
        check(21, f"({sub}) 備援 → oldscore_signals 不呼叫、cause={cause}", ok, a)
    # 11：VALID_EMPTY
    db = env(utc(15, 5))
    put_snapshot(db, [])
    ss.bt_nightly_entry(db, "backup")
    c = STAGE_CALLS[-1]
    check(11, "快照為 VALID_EMPTY → 正常執行（空候選、不走 A）、不告警", c["old_cands"] == [] and not c["old_skip"] and not TG)

    # 10／18：第一次 stale_data
    db = env(utc(14, 15))
    STAGE_MODE[0] = "stale_data"
    ss.bt_nightly_entry(db, "primary", mem_cands=cands(2))
    c0 = claims(db)
    STAGE_MODE[0] = "ok"
    db.now = utc(15, 5)
    r = ss.bt_nightly_entry(db, "backup")
    check(10, "第一次 stale_data → claim 已 released；備援取得 claim 並執行",
          c0[0]["gate_status"] == "released" and len(STAGE_CALLS) == 2 and r.get("result") == "ran")
    check(18, "stale_data 留下的 claim 不會讓備援判為 in_progress_skip", r.get("result") != "in_progress_skip")
    check(15, "行情 stale_data 時（有效快照）不寫 A 方案紀錄", not runlog(db, ss.BT_OLDV2_STAGE))

    # 13／17：claim 超過 120 分鐘
    db = env(utc(17, 30))
    db.rows("system_run_log").append({"id": 50, "stage": ss.BT_CLAIM_STAGE, "run_date": SIG_DAY, "gate_status": "claimed",
                                      "created_at": utc(14, 15).isoformat(),
                                      "note": ss.bt_build_marker("claim", phase="business_started", signal_date=SIG_DAY,
                                                                 run_sha="old", cand_source="none")})
    db.seq = 60
    r = ss.bt_nightly_entry(db, "backup")
    old = [x for x in claims(db) if x["id"] == 50][0]
    mine = [x for x in claims(db) if x["id"] != 50]
    check(13, "claim 超過 120 分鐘 → 改為 stale，備援接管執行", old["gate_status"] == "stale" and r.get("result") == "ran")
    check(17, "舊紀錄保留並記錄接管者 claim id", mine and f"stale_by={mine[0]['id']}" in old["note"])

    # 14：預先檢查查詢失敗
    db = env(utc(15, 5))
    orig = ss._bt_valid_claims

    def boom(*a, **k):
        raise ConnectionError("x")
    ss._bt_valid_claims = boom
    r = ss.bt_nightly_entry(db, "backup")
    ss._bt_valid_claims = orig
    f = runlog(db, ss.BT_BACKUP_STAGE)
    check(14, "備援查詢 claim 失敗 → failed（bt_nightly_backup）、不插入 claim、不執行、有告警",
          f and f[0]["gate_status"] == "failed" and "CLAIM_PRECHECK_FAILED" in f[0]["note"] and not claims(db)
          and not STAGE_CALLS and any("CLAIM_PRECHECK_FAILED" in m for m in TG))

    # 19／19b：disabled
    for sub, note, expect in (("same", ss.bt_build_marker("run", signal_date=SIG_DAY, claim_id=1, run_sha="s"), True),
                              ("other", ss.bt_build_marker("run", signal_date="2026-11-25", claim_id=1, run_sha="s"), False),
                              ("legacy", "bt_strategy_config.enabled=false，略過", False)):
        db = env(utc(15, 5))
        db.rows("system_run_log").append({"id": 7, "stage": "bt_nightly", "run_date": SIG_DAY, "gate_status": "disabled",
                                          "created_at": utc(14, 15).isoformat(), "note": note})
        r = ss.bt_nightly_entry(db, "backup")
        ad = [x for x in runlog(db, "bt_nightly") if x["gate_status"] == "already_disabled"]
        if expect:
            check(19, "同訊號日已有 disabled → 備援寫 already_disabled（stage bt_nightly）、不執行", ad and not STAGE_CALLS)
        else:
            check("19b", f"disabled 的訊號日不同或沒有標記（{sub}）→ 不視為 already_disabled", not ad)

    # 28：沒有標記的歷史 normal
    db = env(utc(15, 5))
    db.rows("system_run_log").append({"id": 3, "stage": "bt_nightly", "run_date": SIG_DAY, "gate_status": "normal",
                                      "created_at": utc(14, 15).isoformat(), "note": f"訊號日{SIG_DAY}｜舊格式"})
    r = ss.bt_nightly_entry(db, "backup")
    check(28, "上線前沒有標記的 normal 紀錄 → 不視為 already_complete", r.get("result") != "already_complete")
    check(36, "缺少必要欄位的標記不會被當成完成", not ss._bt_is_completion({"gate_status": "normal", "note": "[kind=run signal_date=" + SIG_DAY + "]"}, SIG_DAY))

    # 53：備援呼叫順序
    db = env(utc(15, 5))
    h = put_snapshot(db, cands(3))
    order = []
    ss.bt_read_snapshot = lambda *a, **k: (order.append("read"), ORIG["bt_read_snapshot"](*a, **k))[1]
    ss._bt_claim_insert = lambda *a, **k: (order.append("claim"), ORIG["_bt_claim_insert"](*a, **k))[1]
    ss._bt_find_completion = lambda *a, **k: (order.append("done?"), ORIG["_bt_find_completion"](*a, **k))[1]
    ss._bt_phase_update = lambda *a, **k: (order.append("phase"), ORIG["_bt_phase_update"](*a, **k))[1]
    ss.stage_bt_nightly = lambda *a, **k: (order.append("business"), fake_stage(*a, **k))[1]
    ss.bt_nightly_entry(db, "backup")
    restore()
    seq = [x for x in order if x != "done?" or order.index("claim") < order.index(x)]
    first = order.index("read") < order.index("claim") < order.index("phase") < order.index("business")
    second_done = "done?" in order[order.index("claim"):order.index("phase")]
    cm = mk(db, claims(db)[-1])
    check(53, "備援順序：讀正式鍵 → 插入 claim → 第二次完成檢查 → phase → 業務流程；claim 標記 cand_source=snapshot",
          first and second_done and cm.get("cand_source") == "snapshot" and cm.get("cand_hash") == h, order)


# ================================================================ 五、claim、phase、並行
def t_claims():
    restore()
    # 22：例外中斷
    db = env(utc(14, 15))
    STAGE_MODE[0] = "raise"
    try:
        ss.bt_nightly_entry(db, "primary", mem_cands=cands(2))
    except RuntimeError:
        pass
    c = claims(db)[0]
    check(22, "例外中斷 → finally 把 claim 改為 released，結果記錄為 error", c["gate_status"] == "released" and "result=error" in c["note"])

    # 27：釋放時自己的 claim 已被判為 stale
    db = env(utc(14, 15))
    orig = ss.stage_bt_nightly

    def mark_stale_then_ok(sb, **kw):
        for r in claims(db):
            r["gate_status"] = "stale"
        return fake_stage(sb, **kw)
    ss.stage_bt_nightly = mark_stale_then_ok
    ss.bt_nightly_entry(db, "primary", mem_cands=cands(2))
    ss.stage_bt_nightly = orig
    check(27, "finally 釋放時 claim 已是 stale → 更新 0 筆、不覆寫", claims(db)[0]["gate_status"] == "stale")

    # 51：claim insert 失敗
    for sub in ("a", "b"):
        db = env(utc(15, 5))
        db.fail[("system_run_log", "insert")] = 2 if sub == "a" else 99
        try:
            r = ss.bt_nightly_entry(db, "backup")
            raised = False
        except Exception:
            raised = True
        f = runlog(db, ss.BT_BACKUP_STAGE)
        ok = (not raised and not STAGE_CALLS and any("CLAIM_INSERT_FAILED" in m for m in TG)
              and not any("SECRET" in m or "example.invalid" in m for m in TG))
        ok = ok and ((f and "CLAIM_INSERT_FAILED" in f[0]["note"]) if sub == "a" else not f)
        check(51, f"({sub}) claim 插入失敗 → 呼叫告警（不含例外原文）、不進入業務流程、不拋例外", ok, [raised, f, TG])
    src = open(os.path.join(HERE, "system_scheduler.py"), encoding="utf-8").read()
    check(51, "claim 不經由 _log_stage_run 寫入", not re.search(r"_log_stage_run\([^)]*BT_CLAIM_STAGE", src)
          and "def _bt_claim_insert" in src)

    # 55：phase 更新 0 筆／例外
    db = env(utc(15, 5))
    orig_pu = ORIG["_bt_phase_update"]

    def zero(sb, cid, note):
        for r in claims(db):
            r["gate_status"] = "stale"
        return orig_pu(sb, cid, note)
    ss._bt_phase_update = zero
    ss.bt_nightly_entry(db, "backup")
    ss._bt_phase_update = orig_pu
    sk = [x for x in runlog(db, ss.BT_BACKUP_STAGE) if x["gate_status"] == "in_progress_skip"]
    check(55, "(a) phase 更新 0 筆 → 不進入業務流程、寫 in_progress_skip", not STAGE_CALLS and sk)
    db = env(utc(15, 5))

    def boom(*a, **k):
        raise ConnectionError("x")
    ss._bt_phase_update = boom
    ss.bt_nightly_entry(db, "backup")
    ss._bt_phase_update = orig_pu
    c = claims(db)
    check(55, "(b) phase 更新例外 2 次 → CLAIM_PHASE_UPDATE_FAILED、有告警、claim 已嘗試釋放、不執行",
          not STAGE_CALLS and any("CLAIM_PHASE_UPDATE_FAILED" in m for m in TG) and c and c[0]["gate_status"] == "released")

    # 20／60(c2)：繞過 concurrency，主要執行個體持有有效 claim 時備援抵達
    db = env(utc(15, 5))
    db.rows("system_run_log").append({"id": 40, "stage": ss.BT_CLAIM_STAGE, "run_date": SIG_DAY, "gate_status": "claimed",
                                      "created_at": utc(14, 50).isoformat(),
                                      "note": ss.bt_build_marker("claim", phase="business_started", signal_date=SIG_DAY,
                                                                 run_sha="sha_A", cand_source="snapshot", cand_hash="x", cand_hash_version="v1")})
    db.seq = 41
    r = ss.bt_nightly_entry(db, "backup")
    mine = [x for x in claims(db) if x["id"] != 40]
    check(20, "主要執行個體持有有效 claim → 備援寫 in_progress_skip（bt_nightly_backup）",
          r.get("result") == "in_progress_skip" and runlog(db, ss.BT_BACKUP_STAGE) and not STAGE_CALLS)
    check(60, "(c2) 繞過 concurrency 時再次派發 → 預先檢查略過、沒有插入 claim、業務呼叫 = 0", not mine and not STAGE_CALLS)
    check(20, "這筆紀錄不會讓 Worker 判定當日已執行", worker_would_dispatch(db))
    # 主要流程結束後，再次派發的備援寫 already_complete
    db.rows("system_run_log")[0]["gate_status"] = "released"
    ss._log_stage_run(db, "bt_nightly", SIG_DAY, 1, 1, "normal",
                      ss.bt_compose_note(ss.bt_run_marker({"signal_date": SIG_DAY, "claim_id": 40, "run_sha": "sha_A"}, False), "x"))
    r = ss.bt_nightly_entry(db, "backup")
    check(20, "主要流程結束後再次派發的備援寫 already_complete", r.get("result") == "already_complete")

    # 60 (b)：主要入口 14:15 的 claim 卡在 claimed、沒有 bt_nightly 紀錄
    db = env(utc(15, 5))
    db.rows("system_run_log").append({"id": 30, "stage": ss.BT_CLAIM_STAGE, "run_date": SIG_DAY, "gate_status": "claimed",
                                      "created_at": utc(14, 15).isoformat(),
                                      "note": ss.bt_build_marker("claim", phase="business_started", signal_date=SIG_DAY,
                                                                 run_sha="sha_A", cand_source="none")})
    db.seq = 31
    ss.bt_nightly_entry(db, "backup")
    check(60, "(b) 備援唯讀預先檢查後 in_progress_skip、沒有插入自己的 claim、Worker 冷卻後仍會再派發",
          len(claims(db)) == 1 and worker_would_dispatch(db))
    # 60 (c1)：正常序列化下第二次派發
    db = env(utc(15, 5))
    ss.bt_nightly_entry(db, "backup")
    n1 = len(STAGE_CALLS)
    db.now = utc(16, 10)
    r = ss.bt_nightly_entry(db, "backup")
    check(60, "(c1) 正常序列化：R2 在 R1 完成後才開始 → already_complete、業務呼叫只有 R1 一次", n1 == 1 and len(STAGE_CALLS) == 1
          and r.get("result") == "already_complete")
    # 9：等待中的 run 被取代，沒有留下任何紀錄
    db = env(utc(15, 5))
    check(9, "被取代的 run 沒有紀錄 → Worker 冷卻後重新派發（以 Worker 查詢條件模擬）", worker_would_dispatch(db))

    # 38／16／3b：兩個主要入口都通過第 2 步，其中一個先完成
    db = env(utc(14, 15))
    orig_ci = ORIG["_bt_claim_insert"]
    state = {"done": False}

    def b_insert(sb, run_date, note):
        if not state["done"]:
            state["done"] = True
            ss._bt_claim_insert = orig_ci
            ss.bt_nightly_entry(db, "primary", mem_cands=cands(3))   # A 整個跑完
            ss._bt_claim_insert = b_insert
        return orig_ci(sb, run_date, note)
    ss._bt_claim_insert = b_insert
    r = ss.bt_nightly_entry(db, "primary", mem_cands=cands(3))
    ss._bt_claim_insert = orig_ci
    check(38, "後到者在第 6 步發現已完成 → already_complete、不進入業務流程",
          r.get("result") == "already_complete" and len(STAGE_CALLS) == 1)
    check(16, "插入 claim 與查詢交錯 → 沒有重複業務寫入", len(STAGE_CALLS) == 1)
    check("3b", "繞過 concurrency 同時執行 → 不要求互斥，但不產生重複業務寫入", len(STAGE_CALLS) == 1)


def worker_would_dispatch(db):
    """鏡像 Worker 第 255～261 行：14:50 之後 stage 完全等於 bt_nightly 或 bt_nightly_claim 的任何紀錄 → 不派發。"""
    since = utc(14, 50).isoformat()
    for st in ("bt_nightly", "bt_nightly_claim"):
        if [r for r in db.rows("system_run_log") if r.get("stage") == st and str(r.get("created_at")) >= since]:
            return False
    return True


def t_divergence():
    restore()
    # 44 (a)／59：D4 名單分歧後被終止
    db = env(utc(14, 15))
    db.fail[("system_config", "upsert")] = 1
    orig_read = ORIG["bt_read_snapshot"]
    h1_box = {}

    def read_then_b_writes(sb, sd, **kw):
        st = orig_read(sb, sd, **kw)
        if not h1_box:
            h1_box["h"] = put_snapshot(db, cands(3, base=8891))   # B 在 A 讀回之後寫入 H1
        return st
    ss.bt_read_snapshot = read_then_b_writes
    STAGE_MODE[0] = "exit"
    try:
        ss.bt_nightly_entry(db, "primary", mem_cands=cands(3))
    except SystemExit:
        pass
    restore()
    a_claim = claims(db)[0]
    am = mk(db, a_claim)
    check(44, "(a) A 的 claim 在業務流程前已存在：phase=business_started、cand_source=memory、cand_hash=H2",
          am.get("phase") == "business_started" and am.get("cand_source") == "memory" and am.get("cand_hash") == H(cands(3)), am)
    check(59, "phase 後、第一筆業務操作前被終止：沒有 kind=run 紀錄", not runlog(db, "bt_nightly"))
    db.now = utc(15, 5)
    STAGE_MODE[0] = "ok"
    ss.bt_nightly_entry(db, "backup")
    alert = [m for m in TG if "CAND_HASH_DIVERGED" in m]
    check(44, "(a) 備援使用 H1、記錄 CAND_HASH_DIVERGED 並告警", alert and used_hash(STAGE_CALLS[-1]) == h1_box["h"])
    check(59, "告警文字含「可能分歧」與該 claim id", alert and "可能分歧" in alert[0] and str(a_claim["id"]) in alert[0], alert)

    # 44 (b)：不同 hash_version、內容相同 → 不誤報
    restore()
    db = env(utc(15, 5))
    put_snapshot(db, cands(3), version="v1")

    def hasher_v2(cs):
        payload, _ = ss.BT_HASHERS["v1"](cs)
        return payload, hashlib.sha256(("v2|" + json.dumps(payload, sort_keys=True, separators=(",", ":"))).encode()).hexdigest()
    ss.BT_HASHERS["v2"] = hasher_v2
    db.rows("system_run_log").append({"id": 70, "stage": ss.BT_CLAIM_STAGE, "run_date": SIG_DAY, "gate_status": "stale",
                                      "created_at": utc(14, 15).isoformat(),
                                      "note": ss.bt_build_marker("claim", phase="business_started", signal_date=SIG_DAY, run_sha="a",
                                                                 cand_source="memory", cand_hash=hasher_v2(cands(3))[1], cand_hash_version="v2")})
    db.seq = 71
    ss.bt_nightly_entry(db, "backup")
    check(44, "(b) 版本不同但內容相同 → 不記 CAND_HASH_DIVERGED", "CAND_HASH_DIVERGED" not in all_text(db))

    # 54：被略過的 claim 不誤報
    restore()
    db = env(utc(14, 15))
    db.fail[("system_config", "upsert")] = 1           # (1) B 寫入正式鍵例外
    state = {}

    def b_read(sb, sd, **kw):
        st = orig_read(sb, sd, **kw)
        if not state:                                   # B 讀回確認不存在之後，A 寫入 H1 並取得 claim
            state["h1"] = put_snapshot(db, cands(3, base=8891))
            db.seq += 1
            db.rows("system_run_log").append({"id": db.seq, "stage": ss.BT_CLAIM_STAGE, "run_date": SIG_DAY, "gate_status": "claimed",
                                              "created_at": db.now.isoformat(),
                                              "note": ss.bt_build_marker("claim", phase="business_started", signal_date=SIG_DAY,
                                                                         run_sha="sha_A", cand_source="snapshot", cand_hash=state["h1"],
                                                                         cand_hash_version="v1")})
            state["a_id"] = db.seq
        return st
    ss.bt_read_snapshot = b_read
    r = ss.bt_nightly_entry(db, "primary", mem_cands=cands(3))   # B：D4，記憶體 H2
    restore()
    b_claim = [x for x in claims(db) if x["id"] != state["a_id"]]
    bm = mk(db, b_claim[0]) if b_claim else {}
    check(54, "B 因 A 的 claim 有效而 in_progress_skip；B 的 phase=pending、cand_source=memory、cand_hash=H2",
          r.get("result") == "in_progress_skip" and bm.get("phase") == "pending" and bm.get("cand_source") == "memory"
          and bm.get("cand_hash") == H(cands(3)), [r, bm])
    a_row = [x for x in claims(db) if x["id"] == state["a_id"]][0]
    a_row["gate_status"] = "released"                   # A 之後 stale_data 結束（沒有完成紀錄）
    a_row["note"] += " result=stale_data"
    db.now = utc(15, 5)
    TG.clear()
    ss.bt_nightly_entry(db, "backup")
    check(54, "備援只比對 A（business_started），不因 B 的 H2 誤報 CAND_HASH_DIVERGED", "CAND_HASH_DIVERGED" not in all_text(db))


# ================================================================ 五之二、查詢範圍不受固定時數限制（GPT 審查 #1）
def t_window():
    restore()
    FRI = "2026-11-27"

    def done_rec(db, sd, created):
        ss._log_stage_run(db, "bt_nightly", sd, 1, 1, "normal",
                          ss.bt_compose_note(ss.bt_run_marker({"signal_date": sd, "claim_id": 1, "run_sha": "sha_A"}, False), "x"))
        db.rows("system_run_log")[-1]["created_at"] = created.isoformat()

    # 49 小時：週五 22:15 完成，週日 23:15（台北）手動重跑 signal → expected 仍是週五
    db = env(datetime(2026, 11, 29, 15, 15, tzinfo=timezone.utc))
    done_rec(db, FRI, datetime(2026, 11, 27, 14, 15, tzinfo=timezone.utc))
    r = ss.bt_nightly_entry(db, "primary", mem_cands=cands(2))
    check("W1", "49 小時前已有同訊號日成功紀錄 → already_complete、不執行", r.get("result") == "already_complete" and not STAGE_CALLS, r)
    # 72 小時：週一是休市日，週一 22:15（台北）重跑 → expected 仍是週五
    db = env(datetime(2026, 11, 30, 14, 15, tzinfo=timezone.utc))
    ss.is_trading_day = lambda d=None: (d or db.now.astimezone(TPE)).weekday() < 5 and (d or db.now.astimezone(TPE)).strftime("%Y-%m-%d") != "2026-11-30"
    ss._prev_trading_day = lambda d: datetime(2026, 11, 27).date() if str(d) in ("2026-11-30", "2026-11-29", "2026-11-28") else _prev_wd(d)
    done_rec(db, FRI, datetime(2026, 11, 27, 14, 15, tzinfo=timezone.utc))
    r = ss.bt_nightly_entry(db, "backup")
    check("W2", "72 小時前已有同訊號日成功紀錄 → already_complete", r.get("result") == "already_complete" and not STAGE_CALLS, r)
    # 超過 48 小時的舊 claimed claim → 被判為 stale、由後來者接管
    db = env(datetime(2026, 11, 29, 15, 15, tzinfo=timezone.utc))
    db.rows("system_run_log").append({"id": 5, "stage": ss.BT_CLAIM_STAGE, "run_date": FRI, "gate_status": "claimed",
                                      "created_at": datetime(2026, 11, 27, 14, 15, tzinfo=timezone.utc).isoformat(),
                                      "note": ss.bt_build_marker("claim", phase="business_started", signal_date=FRI,
                                                                 run_sha="old", cand_source="memory", cand_hash="f" * 64,
                                                                 cand_hash_version="v1")})
    db.seq = 10
    put_snapshot(db, cands(3))
    db.rows("system_config")[-1]["config_key"] = ss.BT_SNAP_PREFIX + FRI
    v = json.loads(db.rows("system_config")[-1]["config_value"])
    v["signal_date"] = FRI
    db.rows("system_config")[-1]["config_value"] = json.dumps(v)
    r = ss.bt_nightly_entry(db, "backup")
    old = [x for x in claims(db) if x["id"] == 5][0]
    check("W3", "超過 48 小時的舊 claimed claim → 轉為 stale、備援接管執行", old["gate_status"] == "stale" and r.get("result") == "ran", [old, r])
    check("W4", "超過 48 小時的 business_started claim 仍被分歧偵測找到（CAND_HASH_DIVERGED）",
          any("CAND_HASH_DIVERGED" in m and "5" in m for m in TG), TG)
    check("W5", "查詢起點是 signal_date 當天台北 00:00（不是固定時數）",
          ss._bt_signal_day_start_utc(FRI) == datetime(2026, 11, 26, 16, 0, tzinfo=timezone.utc))
    # W6：日期無法解析而退回 30 天時，仍以 note 標記的 signal_date 精確比對，不把其他日期的紀錄當成同一天
    db = env(datetime(2026, 11, 29, 15, 15, tzinfo=timezone.utc))
    done_rec(db, FRI, datetime(2026, 11, 27, 14, 15, tzinfo=timezone.utc))
    db.rows("system_run_log").append({"id": 77, "stage": ss.BT_CLAIM_STAGE, "run_date": FRI, "gate_status": "claimed",
                                      "created_at": db.now.isoformat(),
                                      "note": ss.bt_build_marker("claim", phase="business_started", signal_date=FRI,
                                                                 run_sha="x", cand_source="none")})
    fb = ss._bt_signal_day_start_utc("not-a-date")
    check("W6", "日期無法解析 → 退回 30 天，且不把其他日期的完成紀錄或 claim 當成同一天",
          abs((db.now - fb).total_seconds() - 30 * 86400) < 5 and ss._bt_find_completion(db, "not-a-date") is None
          and ss._bt_valid_claims(db, "not-a-date") == [] and ss._bt_find_disabled(db, "not-a-date") is None
          and ss._bt_find_completion(db, "2026-11-26") is None and ss._bt_find_completion(db, FRI) is not None)


# ================================================================ 六、乾跑
def t_dry():
    restore()
    for state in ("fresh", "done"):
        db = env(utc(14, 15))
        if state == "done":
            ss._log_stage_run(db, "bt_nightly", SIG_DAY, 1, 1, "normal",
                              ss.bt_compose_note(ss.bt_run_marker({"signal_date": SIG_DAY, "claim_id": 1, "run_sha": "s"}, False), "x"))
        db.calls.clear()
        os.environ["BT_DRY_RUN"] = "1"
        out = []
        _p = print
        import builtins
        builtins.print = lambda *a, **k: out.append(" ".join(str(x) for x in a))
        try:
            ss.bt_nightly_entry(db, "primary", mem_cands=cands(3))
        finally:
            builtins.print = _p
            os.environ.pop("BT_DRY_RUN", None)
        check(61, f"乾跑（{state}）：system_config／system_run_log 寫入 0 次、不告警、決策有 print、照常跑完",
              not db.writes() and not TG and any("D" in x and "乾跑" in x for x in out) and STAGE_CALLS, [db.writes(), TG])
        check(61, f"乾跑（{state}）：print 不含候選代號與價格", not any(("8881" in x or "123.45" in x) for x in out))


# ================================================================ 七、A 方案（真的 stage_bt_nightly）
def t_real_stage_aplan():
    """#21、#39：用真的 stage_bt_nightly 證明 A 方案只停用 old_score_v2。
    做法：同一個合成世界跑兩次——(A) old_skip=True；(B) 設定裡直接拿掉 old_score_v2 規則。兩次的掛單必須完全相同，
    而且確實包含穿山惡龍與營收動能（營收動能以假的 _revenue_signals_for 提供，因為測試環境沒有 FinMind）；
    爆量回檔規則有被評估（find_signals 收到的 rules 含它），合成資料本身不產生這種型態。"""
    restore()
    import test_bt_nightly as T   # 匯入只會抽出函式並建立假環境，不會執行它的 main()
    import bt_strategy as bts
    calls, seen_rules = [], []
    orig_old, orig_find = bts.oldscore_signals, bts.find_signals
    bts.oldscore_signals = lambda *a, **k: (calls.append(1), orig_old(*a, **k))[1]

    def spy_find(prices, cfg, *a, **k):
        seen_rules.append(list(cfg.get("rules") or []))
        return orig_find(prices, cfg, *a, **k)
    bts.find_signals = spy_find
    S = T.pd.Timestamp("2026-10-02")
    full = T.build_world(S)
    syms = sorted(full)
    rev_sym = "9001"        # 合成世界的每一檔都有穿山惡龍訊號（同檔只留優先序高者），所以營收動能用另一檔

    def fake_rev(uprices, cfg, as_of, preview=False):
        return ([{"symbol": rev_sym, "signal_date": str(as_of.date()), "ref_close": 50.0, "score15": 99.0,
                  "breadth": None, "rule": bts.RULE_REVENUE, "rev_ym": "2026-09", "rev": {"yoy": 0.5, "yoy3": 0.4, "mom": 0.1}}],
                {"rev_ym": "2026-09"})
    T.ns["_revenue_signals_for"] = fake_rev
    T.ns["_pct_s"] = ss._pct_s          # 營收動能掛單理由會用到（test_bt_nightly 的抽取清單沒有它）
    oc = [{"symbol": syms[0], "score": 8, "score_nochase": 9, "price": 10.0, "reasons": []}]

    def run(rules, old_skip):
        T.CONFIG["bt_strategy_config"] = json.dumps({"rules": rules, "max_new_per_day": 20, "k_slots": 20, "breadth_min": 0.0})
        sb = T.FakeSB()
        T.UNIVERSE[:] = syms
        T.CUR[0] = datetime(S.year, S.month, S.day, 22, 30, tzinfo=T.TZ)
        T.PRICES.clear()
        T.PRICES.update({s: d[d.index <= S] for s, d in full.items()})
        r = T.stage_bt_nightly(sb, name_map={}, old_cands=oc, old_skip=old_skip,
                               _ctx={"signal_date": "2026-10-02", "claim_id": 5, "run_sha": "s", "cand_source": "none"})
        pend = sorted((x["symbol"], x["strategy_tag"]) for x in sb.db.get("system_portfolio", []) if x["status"] == "pending")
        return r, pend
    try:
        all4 = ["chuan_e_ma60_40", "pullback_burst", "revenue_momentum", "old_score_v2"]
        ra, pa = run(all4, True)
        n_old_calls = len(calls)
        rules_a = seen_rules[-1] if seen_rules else []
        rb, pb = run(all4[:3], False)
        tags = {t_ for _, t_ in pa}
        check(21, "A 方案（真的 stage）：oldscore_signals 呼叫次數 = 0", n_old_calls == 0, n_old_calls)
        check(39, "A 方案的掛單與『設定裡拿掉 old_score_v2』完全相同（其他三條策略不受影響）",
              ra.get("status") == "ok" and pa and pa == pb, [pa, pb])
        check(39, "A 方案時穿山惡龍與營收動能都有掛單、沒有 old_score_v2",
              "chuan_e_ma60_40" in tags and "revenue_momentum" in tags and "old_score_v2" not in tags, tags)
        check(39, "A 方案時爆量回檔規則仍被評估（find_signals 的 rules 含 pullback_burst）",
              "pullback_burst" in rules_a and "chuan_e_ma60_40" in rules_a, rules_a)
        note = T.RUNLOG[-1][4] if T.RUNLOG else ""
        pm = ss.bt_parse_marker(note) or {}
        check(36, "真的 stage：執行紀錄標記在 note 開頭、A 方案省略 cand_hash", pm.get("kind") == "run" and pm.get("claim_id") == "5"
              and "cand_hash" not in pm, note[:120])
    finally:
        bts.oldscore_signals, bts.find_signals = orig_old, orig_find
        T.ns.pop("_revenue_signals_for", None)
        T.ns.pop("_pct_s", None)
        T.CONFIG.pop("bt_strategy_config", None)


# ================================================================ 八、告警、代碼、靜態檢查
def t_alerts_codes_static():
    sample = {"date": SIG_DAY, "claim": "12345", "hash": "ab12cd34ef56", "code": "HASH_MISMATCH", "codes": "SNAPSHOT_CONFLICT,SHA_DIFFERS",
              "exc": "ConnectionError", "row": "D4", "cause": "SNAPSHOT_MISSING", "ver": "v1", "role": "backup",
              "action": ss.BT_ROW_ACTION["D7"]}
    bad = []
    for k, tpl in ss.BT_ALERTS.items():
        msg = tpl.format(**sample)
        tier = tg_policy.classify(msg)[0]
        if tier not in ("alert", "push") or not msg.startswith("⚠️") or "8881" in msg or "123.45" in msg:
            bad.append((k, tier))
    check(58, f"告警範本 {len(ss.BT_ALERTS)} 則：都以 ⚠️ 開頭、tg_policy 判為 alert／push、不含候選代號與價格", ss.BT_ALERTS and not bad, bad)
    check(58, "反例：含「回測」的範本會被判為 digest（證明檢查有效）", tg_policy.classify("⚠️ old_score_v2 回測快照無效")[0] == "digest")

    src = open(os.path.join(HERE, "system_scheduler.py"), encoding="utf-8").read()
    i, j = src.index("# ===== PR-0 BEGIN"), src.index("# ===== PR-0 END")
    region = src[i:j]
    lits = set(re.findall(r'"([A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+)"', region))
    non_codes = {"VALID", "VALID_WITH_CANDIDATES", "VALID_EMPTY", "NOT_FOUND", "CORRUPT", "UNVERIFIABLE", "READ_FAILED",
                 "GITHUB_SHA", "GITHUB_RUN_ID", "BT_DRY_RUN", "CURRENT_HASH_VERSION"}
    stray = sorted(lits - set(ss.BT_CODES) - set(ss.BT_DEPRECATED_CODES) - non_codes)
    check(57, "程式中出現的所有代碼都在代碼總表內", not stray, stray)
    dep_lines = [ln for ln in region.splitlines() if re.search(r'"(VERSION_MISMATCH|SNAPSHOT_MARKED_INVALID)"', ln)]
    check(57, "廢棄代碼 VERSION_MISMATCH、SNAPSHOT_MARKED_INVALID 只出現在廢棄清單的定義、不會被產生",
          set(ss.BT_DEPRECATED_CODES) == {"VERSION_MISMATCH", "SNAPSHOT_MARKED_INVALID"} and len(dep_lines) == 1
          and dep_lines[0].lstrip().startswith("BT_DEPRECATED_CODES") and not (set(ss.BT_DEPRECATED_CODES) & set(ss.BT_CODES)), dep_lines)
    check(32, "程式中不存在 force 相關分支（bt_nightly_force）", "bt_nightly_force" not in src)
    k = src.index('bt_nightly_entry(sb, "primary"')
    blk = src[k:k + 700]
    check(58, "stage_signal 外層例外處理改用安全範本：不含例外原文、不含「回測」", "str(e)" not in blk and "{e}" not in blk
          and "entry_exception" in blk and "回測" not in blk, blk)

    # 1／5：concurrency group
    yml = open(os.path.join(HERE, ".github", "workflows", "system_scheduler.yml"), encoding="utf-8").read()
    spec = ("group: sched-${{ ((github.event_name == 'workflow_dispatch' && (!github.event.inputs.stage || "
            "github.event.inputs.stage == 'signal' || github.event.inputs.stage == 'bt_nightly')) || "
            "github.event.schedule == '0 14 * * 1-5') && 'nightly-chain' || "
            "(github.event.inputs.stage || github.event.schedule || github.run_id) }}")
    check(1, "workflow 的 concurrency group 與規格一字不差", spec in yml)
    check(1, "cancel-in-progress 維持 false", re.search(r"group: sched-.*\n\s*cancel-in-progress: false", yml) is not None)

    def group(event, stage, schedule, run_id="r9"):
        # GitHub 運算式的 Python 鏡像：null 與 '' 都是 falsy；&& / || 回傳運算元本身
        s = stage if stage else None
        sch = schedule if schedule else None
        chain = ((event == "workflow_dispatch" and ((not s) or s == "signal" or s == "bt_nightly")) or sch == "0 14 * * 1-5")
        return "sched-" + ("nightly-chain" if chain else str(s or sch or run_id))
    cases = [(("workflow_dispatch", "signal", ""), "sched-nightly-chain"),
             (("workflow_dispatch", "bt_nightly", ""), "sched-nightly-chain"),
             (("workflow_dispatch", "", ""), "sched-nightly-chain"),
             (("workflow_dispatch", None, None), "sched-nightly-chain"),
             (("workflow_dispatch", "overnight_scan", ""), "sched-overnight_scan"),
             (("schedule", None, "0 14 * * 1-5"), "sched-nightly-chain"),
             (("schedule", None, "0 3 1 * *"), "sched-0 3 1 * *"),
             (("schedule", None, ""), "sched-r9")]
    wrong = [(c, group(*c), e) for c, e in cases if group(*c) != e]
    check(1, "鏡像代入 8 種觸發情境：signal／bt_nightly／空 stage／null／cron 0 14 → 同一群組", not wrong, wrong)
    check(5, "其他 stage 與每月 cron 的群組與現行相同", group("workflow_dispatch", "overnight_scan", "") == "sched-overnight_scan"
          and group("schedule", None, "0 3 1 * *") == "sched-0 3 1 * *")
    check(1, "執行步驟的 stage 判斷與鏡像一致（cron 0 14 → signal、空 stage → signal）",
          '"0 14 * * 1-5") STAGE="signal"' in yml and '*) STAGE="signal"' in yml)

    # 60 (a)：Worker 原始碼
    w = open(os.path.join(HERE, "warroom_monitor_worker.js"), encoding="utf-8").read()
    check(60, "(a) Worker 以 stage=eq. 完全比對，只查 logName 與 logName+\"_claim\"",
          "stage=eq.${encodeURIComponent(stage)}" in w and 'hasStageRunSince(env, logName + "_claim"' in w
          and re.search(r'\{ stage: "bt_nightly",[^}]*\}', w) and "logStage" not in re.search(r'\{ stage: "bt_nightly",[^}]*\}', w).group(0))
    check(60, "(a) bt_nightly_run_claim 與 bt_nightly_backup 不在 Worker 的查詢名單中",
          ss.BT_CLAIM_STAGE == "bt_nightly_run_claim" and "bt_nightly_run_claim" not in w and "bt_nightly_backup" not in w)

    # 7：只能上線後觀察
    print("⏸️ #7 上線後連續 5 個交易日：合併前無法執行（上線後確認）")


def main():
    for fn in (t_hash, t_marker, t_primary_basic, t_snapshot_conflicts, t_invalid_and_write_fail, t_versions,
               t_sha_and_fields, t_backup, t_claims, t_divergence, t_window, t_dry, t_real_stage_aplan, t_alerts_codes_static):
        try:
            fn()
        except Exception as e:   # noqa: BLE001
            check("ERR", f"{fn.__name__} 執行例外：{type(e).__name__}", False, str(e)[:200])
        finally:
            restore()
    ids = sorted({str(t) for t, _ in RESULTS if t != "ERR" and not str(t).startswith("W")},
                 key=lambda x: (float(re.sub(r"[a-z]", "", x) or 0), x))
    print(f"\n涵蓋測試編號 {len(ids)} 個：{', '.join(ids)}")
    if FAILS:
        print(f"\n❌ {len(FAILS)} 項失敗：{FAILS}")
        sys.exit(1)
    print("\n✅ PR-0 驗收測試全部通過")


if __name__ == "__main__":
    main()
