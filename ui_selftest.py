#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ui_selftest.py —— 戰情室自家介面測試（Streamlit AppTest，不需要真瀏覽器、不需要人點）。

安全設計（重要）：
  1. 以「viewer（第二組密碼）」身分登入，不是總指揮官。
  2. 寫入防護：攔截 Supabase 的 insert/update/upsert/delete/rpc，一律不送出、只記錄
     （記錄 = 發現「viewer 也能觸發寫入」的地方）。
  3. 攔截對 api.github.com / api.telegram.org / 券商 的 POST/PUT/PATCH/DELETE，不送出、只記錄。
  4. 不點任何 st.button（只切換 radio/selectbox/checkbox/toggle/slider 這類讀取型控制項）。
  5. 測試用 secrets 不含 Shioaji / NVIDIA / GitHub token（券商與 AI 皆不啟用）。
輸出：ui_selftest_out/report.md、report.json
"""
import os
import sys
import json
import time
import traceback

OUT = "ui_selftest_out"
os.makedirs(OUT, exist_ok=True)
APP = os.environ.get("APP_FILE", "dashangdao.py")
VIEWER_PIN = os.environ.get("TEST_VIEWER_PIN", "")
BOOT_TIMEOUT = int(os.environ.get("BOOT_TIMEOUT", "600"))
STEP_TIMEOUT = int(os.environ.get("STEP_TIMEOUT", "240"))
MAX_OPTS = int(os.environ.get("MAX_OPTS", "4"))          # 每個控制項最多試幾個選項
MAX_WIDGETS_PER_PAGE = int(os.environ.get("MAX_WIDGETS", "25"))
DEADLINE = time.time() + int(os.environ.get("MAX_TOTAL_SEC", "5400"))   # 總時間上限，超過就收尾出報告

BLOCKED = []     # 被攔下的寫入嘗試
FINDINGS = []    # 發現的問題


def install_write_guards():
    import traceback as tb
    try:
        from postgrest._sync.request_builder import SyncRequestBuilder
    except Exception:
        from postgrest import SyncRequestBuilder

    # 讓「被攔截後回傳 self 的 SyncRequestBuilder」也有 eq/execute：回傳一個假物件
    class _Dummy:
        data = []
        count = None
        def __getattr__(self, n):
            return lambda *a, **k: self
        def execute(self, *a, **k):
            return self
    def mk2(name):
        def _blocked(self, *a, **k):
            stack = [f"{os.path.basename(f.filename)}:{f.lineno}" for f in tb.extract_stack()[-8:-1]
                     if "site-packages" not in f.filename]
            BLOCKED.append({"kind": f"supabase.{name}", "table": str(getattr(self, "path", "?")), "where": stack[-3:]})
            return _Dummy()
        return _blocked
    for n in ("insert", "update", "upsert", "delete"):
        setattr(SyncRequestBuilder, n, mk2(n))
    # rpc
    try:
        from postgrest import SyncPostgrestClient
        _orig_rpc = SyncPostgrestClient.rpc

        def _rpc(self, fn, *a, **k):
            if str(fn).lower().startswith(("get_", "list_", "select_", "read_", "v_")):
                return _orig_rpc(self, fn, *a, **k)
            BLOCKED.append({"kind": f"supabase.rpc:{fn}", "table": "-", "where": []})
            return _Dummy()
        SyncPostgrestClient.rpc = _rpc
    except Exception:
        pass
    # requests 寫入類方法
    import requests
    BAD_HOSTS = ("api.github.com", "api.telegram.org", "shioaji", "sinopac", "cloudflare.com")
    for meth in ("post", "put", "patch", "delete"):
        orig = getattr(requests, meth)

        def make(orig, meth):
            def guarded(url, *a, **k):
                if any(h in str(url) for h in BAD_HOSTS):
                    BLOCKED.append({"kind": f"requests.{meth}", "table": str(url)[:80], "where": []})

                    class R:
                        status_code = 599
                        text = "blocked-by-ui-selftest"
                        def json(self): return {}
                        def raise_for_status(self): raise RuntimeError("blocked-by-ui-selftest")
                    return R()
                return orig(url, *a, **k)
            return guarded
        setattr(requests, meth, make(orig, meth))


def snap(at, label):
    """收集目前畫面的異常/錯誤/警告。"""
    exc = [str(getattr(e, "value", e))[:400] for e in at.exception]
    err = [str(getattr(e, "value", e))[:300] for e in at.error]
    warn = [str(getattr(w, "value", w))[:200] for w in at.warning]
    return {"label": label, "exceptions": exc, "errors": err, "warnings": warn}


def record(page_log, at, label):
    s = snap(at, label)
    page_log.append(s)
    return s


def widgets_on_page(at):
    """可安全操作的讀取型控制項（排除導覽用 radio 與所有 button）。"""
    out = []
    for kind in ("radio", "selectbox", "checkbox", "toggle", "select_slider"):
        try:
            for w in getattr(at, kind):
                if getattr(w, "key", None) in ("main_nav_section", "login_pin_input"):
                    continue
                out.append((kind, w))
        except Exception:
            pass
    return out[:MAX_WIDGETS_PER_PAGE]


def brain_diag(at):
    """診斷「本機大腦(SQLite)有沒有籌碼資料」：開機回填結果、本機表筆數、最新日期。"""
    d = {}
    try:
        d["sb_sync_result"] = str(at.session_state["sb_sync_result"]) if "sb_sync_result" in at.session_state else None
        d["cloud_hydrated"] = str(at.session_state["cloud_hydrated"]) if "cloud_hydrated" in at.session_state else None
    except Exception as e:
        d["state_err"] = str(e)[:100]
    try:
        import sqlite3
        c = sqlite3.connect("54088_inst_history.db")
        for t in ("inst_holding", "big_holder_history"):
            try:
                n, mx, days = c.execute(f"select count(*), max(date), count(distinct date) from {t}").fetchone()
                d[t] = {"rows": n, "max_date": mx, "days": days}
            except Exception as e:
                d[t] = f"查詢失敗 {str(e)[:80]}"
        c.close()
    except Exception as e:
        d["sqlite_err"] = str(e)[:100]
    return d


def main():
    from streamlit.testing.v1 import AppTest
    install_write_guards()
    t0 = time.time()
    report = {"app": APP, "pages": [], "blocked_writes": BLOCKED, "login": None}

    at = AppTest.from_file(APP, default_timeout=BOOT_TIMEOUT)
    try:
        at.run()
    except Exception as e:
        report["login"] = f"啟動失敗 {type(e).__name__}: {e}"
        finish(report, t0)
        return 1
    s = snap(at, "啟動（未登入）")
    report["boot"] = s
    # ---- 登入（viewer）
    try:
        pin = at.text_input(key="login_pin_input")
        pin.input(VIEWER_PIN)
        btn = [b for b in at.button if "登入" in str(b.label)][0]
        btn.click()
        at.run(timeout=BOOT_TIMEOUT)
        role = at.session_state["user_role"] if "user_role" in at.session_state else None
        report["login"] = f"role={role}"
        if role != "viewer":
            FINDINGS.append(f"登入後角色不是 viewer（{role}）— 測試中止，避免以管理者身分操作。")
            finish(report, t0)
            return 2
    except Exception as e:
        report["login"] = f"登入失敗 {type(e).__name__}: {e}"
        FINDINGS.append(report["login"])
        finish(report, t0)
        return 1

    report["brain"] = brain_diag(at)
    # ---- 逐一切換主畫面分類
    try:
        navs = list(at.radio(key="main_nav_section").options)
    except Exception as e:
        navs = []
        FINDINGS.append(f"找不到主導覽 radio：{e}")
    for nav in navs:
        page = {"nav": nav, "steps": [], "widgets": 0, "elapsed": None}
        t1 = time.time()
        try:
            at.radio(key="main_nav_section").set_value(nav)
            at.run(timeout=STEP_TIMEOUT)
            record(page["steps"], at, f"切到 {nav}")
            ws = widgets_on_page(at)
            page["widgets"] = len(ws)
            for kind, w in ws:
                if time.time() > DEADLINE:
                    page['steps'].append({'label': '(已達總時間上限，其餘控制項略過)', 'exceptions': [], 'errors': [], 'warnings': []})
                    break
                label = f"{kind}:{getattr(w, 'label', '')}"[:60]
                try:
                    if kind in ("checkbox", "toggle"):
                        w.set_value(not w.value)
                        at.run(timeout=STEP_TIMEOUT)
                        record(page["steps"], at, f"{nav} › {label} 切換")
                        # 還原
                        for kk, ww in widgets_on_page(at):
                            if kk == kind and getattr(ww, "label", "") == getattr(w, "label", ""):
                                ww.set_value(not ww.value)
                                at.run(timeout=STEP_TIMEOUT)
                                break
                    else:
                        opts = list(w.options)[:MAX_OPTS]
                        for o in opts:
                            w.set_value(o)
                            at.run(timeout=STEP_TIMEOUT)
                            record(page["steps"], at, f"{nav} › {label} = {str(o)[:30]}")
                except Exception as e:
                    page["steps"].append({"label": f"{nav} › {label}", "exceptions": [f"測試操作失敗 {type(e).__name__}: {str(e)[:200]}"], "errors": [], "warnings": []})
                # 控制項操作後重新抓一次（元件物件可能失效）
        except Exception as e:
            page["steps"].append({"label": f"切到 {nav}", "exceptions": [f"{type(e).__name__}: {str(e)[:300]}"], "errors": [], "warnings": []})
            traceback.print_exc()
        page["elapsed"] = round(time.time() - t1, 1)
        report["pages"].append(page)
        finish(report, t0, quiet=True)
        print(f"[{nav}] 控制項 {page['widgets']} 個，{page['elapsed']}s", flush=True)
    finish(report, t0)
    bad = sum(1 for p in report["pages"] for s in p["steps"] if s["exceptions"])
    return 1 if bad else 0


def finish(report, t0, quiet=False):
    report["elapsed_sec"] = round(time.time() - t0, 1)
    report["findings"] = FINDINGS
    json.dump(report, open(f"{OUT}/report.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
    L = [f"# 戰情室自家介面測試報告", f"- 程式：{report['app']}｜登入：{report.get('login')}｜耗時 {report['elapsed_sec']}s", ""]
    b = report.get("boot")
    if b and (b["exceptions"] or b["errors"]):
        L += ["## 啟動畫面異常", *[f"- ❌ {x}" for x in b["exceptions"] + b["errors"]], ""]
    if report.get("brain"):
        L += ["## 本機大腦(SQLite)診斷", f"- {report['brain']}", ""]
    if FINDINGS:
        L += ["## 重大發現", *[f"- {x}" for x in FINDINGS], ""]
    L.append("## 各頁結果")
    for p in report["pages"]:
        exc = [(s["label"], x) for s in p["steps"] for x in s["exceptions"]]
        err = [(s["label"], x) for s in p["steps"] for x in s["errors"]]
        L.append(f"### {p['nav']}（控制項 {p['widgets']}、步驟 {len(p['steps'])}、{p['elapsed']}s）")
        if not exc and not err:
            L.append("- ✅ 無例外、無錯誤訊息")
        for lab, x in dict.fromkeys(exc):
            L.append(f"- ❌ 例外｜{lab}｜{x}")
        for lab, x in dict.fromkeys(err):
            L.append(f"- ⚠️ 錯誤訊息｜{lab}｜{x}")
        warns = {w for s in p["steps"] for w in s["warnings"]}
        if warns:
            L.append(f"- 警告訊息 {len(warns)} 種：" + "；".join(list(warns)[:8]))
    L += ["", f"## viewer 身分觸發的寫入嘗試（已攔截、未送出）共 {len(BLOCKED)} 筆"]
    seen = {}
    for x in BLOCKED:
        key = (x["kind"], x["table"], tuple(x["where"]))
        seen[key] = seen.get(key, 0) + 1
    for (k, t, w), n in seen.items():
        L.append(f"- {k} → {t}｜{' > '.join(w)}｜{n} 次")
    open(f"{OUT}/report.md", "w", encoding="utf-8").write("\n".join(L))
    if not quiet:
        print("\n".join(L)[:6000])


if __name__ == "__main__":
    sys.exit(main())
