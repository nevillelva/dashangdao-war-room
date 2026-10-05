#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ui_admin_test.py —— 主帳號(admin)全面真瀏覽器測試（Playwright，跑在 GitHub Actions，市場開盤時段）。
寫入防護：guarded_app.py 會攔截所有 Supabase 寫入 / GitHub / Telegram / Shioaji / Cloudflare 的寫入類請求；
AI(NVIDIA)/Shioaji/GitHub 金鑰在測試 secrets 中皆為空白，所以按鈕實際不會下單、不會改資料庫、不會發推播。
流程：登入計時 → 輸入多檔股票(上市/上櫃/中文名)加入雷達並檢查卡片資料完整性 → 各頁展開 expander/分頁籤 →
各頁逐一點擊按鈕(含計時)。報告存 Supabase 私有表，日誌只印統計。"""
import os, re, sys, json, time, subprocess, urllib.request

os.environ["TEST_VIEWER_PIN"] = os.environ["TEST_ADMIN_PIN"]   # 重用 ui_browser_test 的工具函式
import ui_browser_test as B   # noqa: E402  (會建立 ui_browser_out 目錄，不影響)

PORT = B.PORT
URL = B.URL
OUT = "ui_admin_out"
os.makedirs(OUT, exist_ok=True)
DEADLINE = time.time() + int(os.environ.get("MAX_TOTAL_SEC", "5400"))
BTN_CAP = int(os.environ.get("MAX_BUTTONS_PER_NAV", "60"))
BTN_WAIT = int(os.environ.get("BTN_WAIT_SEC", "150")) * 1000
STOCKS = os.environ.get("TEST_STOCKS", "2330,6488,2409,3008,聯電")
SKIP_BTN = re.compile(r"登出|清除|刪除|重置|重設|清空|全部取消|匯入|還原|上傳|下載|匯出")
MISSING = re.compile(r"(N/A|查無|抓不到|暫缺|無資料|資料不足|未取得|取得失敗|無法取得|尚無|缺少)")
NAVS = B.NAVS


def now():
    return time.time()


def wait_idle_timed(page, cap_ms=None):
    t = now()
    try:
        page.wait_for_timeout(600)
        page.wait_for_function("() => !document.querySelector('[data-testid=\"stStatusWidget\"]')", timeout=cap_ms or B.IDLE_TIMEOUT)
        ok = True
    except Exception:
        ok = False
    page.wait_for_timeout(400)
    return ok, round(now() - t, 1)


def body_lines(page):
    return [l.strip() for l in page.evaluate("() => document.body.innerText").splitlines() if l.strip()]


def main():
    from playwright.sync_api import sync_playwright
    rep = {"kind": "ui_admin_out", "role": "admin", "timings": {}, "pages": [], "stocks": {}, "buttons": [], "findings": [], "login": None}
    t_boot = now()
    srv = subprocess.Popen([sys.executable, "-m", "streamlit", "run", "guarded_app.py", "--server.headless", "true",
                            "--server.port", str(PORT), "--browser.gatherUsageStats", "false"],
                           stdout=open("streamlit_stdout.txt", "w"), stderr=subprocess.STDOUT, env=dict(os.environ))
    for _ in range(120):
        try:
            urllib.request.urlopen(URL + "/_stcore/health", timeout=2)
            break
        except Exception:
            time.sleep(1)
    rep["timings"]["server_health_sec"] = round(now() - t_boot, 1)

    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page(viewport={"width": 1400, "height": 900}, locale="zh-TW")
        cerr = []
        page.on("console", lambda m: cerr.append(m.text[:200]) if m.type == "error" else None)
        page.on("pageerror", lambda e: cerr.append("pageerror: " + str(e)[:200]))
        t0 = now()
        page.goto(URL, wait_until="domcontentloaded", timeout=120000)
        try:
            page.wait_for_selector('input[type="password"]', timeout=240000)
            rep["timings"]["login_page_visible_sec"] = round(now() - t0, 1)
            page.fill('input[type="password"]', os.environ["TEST_ADMIN_PIN"])
            t1 = now()
            page.get_by_role("button", name=re.compile("登入")).first.click()
            page.wait_for_selector('[data-testid="stSidebar"]', timeout=B.IDLE_TIMEOUT)
            rep["timings"]["login_to_sidebar_sec"] = round(now() - t1, 1)
            ok, sec = wait_idle_timed(page)
            rep["timings"]["login_to_idle_sec"] = round(now() - t1, 1)
            rep["login"] = "ok"
        except Exception as e:
            rep["login"] = f"登入失敗 {type(e).__name__}: {str(e)[:200]}"
            rep["findings"].append(rep["login"])

        def save():
            json.dump(rep, open(f"{OUT}/report.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)

        if rep["login"] == "ok":
            home = B.scan(page, "登入後首頁", cerr)
            rep["pages"].append(home)
            # 確認角色：admin 才看得到的按鈕/區塊（側邊欄是否出現「唯讀」字樣）
            side = page.locator('[data-testid="stSidebar"]').inner_text()
            rep["role_ui_check"] = {"sidebar_has_readonly_text": ("唯讀" in side), "sidebar_first_200": side[:200]}

            # ---- A. 切換各導覽頁計時（含已載入的真實雷達/持倉卡片）----
            nav_times = {}
            for nav in NAVS:
                t = now()
                try:
                    page.locator('[data-testid="stSidebar"]').get_by_text(nav, exact=False).first.click(timeout=15000)
                    idle, sec = wait_idle_timed(page)
                    sc = B.scan(page, f"切到 {nav}", cerr)
                    sc["idle"] = idle
                    sc["render_sec"] = sec
                    rep["pages"].append(sc)
                    nav_times[nav] = sec
                except Exception as e:
                    rep["findings"].append(f"切到 {nav} 失敗：{type(e).__name__}")
            rep["timings"]["nav_render_sec"] = nav_times
            save()

            # ---- B. 輸入多檔股票（盤中作戰頁）----
            try:
                page.locator('[data-testid="stSidebar"]').get_by_text(NAVS[0], exact=False).first.click(timeout=15000)
                wait_idle_timed(page)
                base = set(body_lines(page))
                box = page.locator('input[aria-label^="🔍 手動股票代號"]').first
                box.scroll_into_view_if_needed(timeout=10000)
                box.fill(STOCKS)
                box.press("Enter")
                wait_idle_timed(page)
                t_add = now()
                btn = page.get_by_role("button", name=re.compile("直接加入常態雷達")).first
                btn.scroll_into_view_if_needed(timeout=10000)
                btn.click(timeout=15000)
                idle, sec = wait_idle_timed(page, cap_ms=420000)
                rep["timings"]["add_stocks_sec"] = round(now() - t_add, 1)
                rep["timings"]["add_stocks_idle"] = idle
                sc = B.scan(page, f"輸入股票 {STOCKS} 加入雷達", cerr)
                rep["pages"].append(sc)
                lines = body_lines(page)
                new = [l for l in lines if l not in base]
                rep["stocks_new_line_count"] = len(new)
                rep["stocks_new_lines_sample"] = new[:160]
                dirty = [l for l in new if B.DIRTY.search(l)]
                miss = [l for l in new if MISSING.search(l)]
                rep["stocks_dirty_lines"] = dirty[:40]
                rep["stocks_missing_lines"] = miss[:60]
                txt = "\n".join(lines)
                for token in [x.strip() for x in STOCKS.split(",") if x.strip()]:
                    rep["stocks"][token] = {"occurrences_on_page": txt.count(token)}
                # 展開卡片內所有 expander，並逐一計時
                exps = page.locator('[data-testid="stMain"] [data-testid="stExpander"] summary')
                n = min(exps.count(), int(os.environ.get("MAX_EXPANDERS", "60")))
                exp_rows = []
                for i in range(n):
                    if now() > DEADLINE:
                        break
                    try:
                        el = exps.nth(i)
                        name = (el.inner_text(timeout=3000) or "")[:40].replace("\n", " ")
                        el.scroll_into_view_if_needed(timeout=8000)
                        t = now()
                        el.click(timeout=15000, force=True)
                        idle, sec = wait_idle_timed(page)
                        s2 = B.scan(page, f"加入後 › 展開「{name}」", cerr)
                        s2["render_sec"] = sec
                        s2["idle"] = idle
                        rep["pages"].append(s2)
                        exp_rows.append((name, sec))
                    except Exception as e:
                        rep["pages"].append({"label": f"加入後 expander#{i}", "exceptions": [f"（測試操作失敗，非程式例外）{type(e).__name__}"], "alerts_error": [], "dirty_values": {}, "h_overflow": 0, "console_errors": []})
                rep["timings"]["expander_avg_sec"] = round(sum(s for _, s in exp_rows) / max(1, len(exp_rows)), 1)
                rep["timings"]["expander_max"] = sorted(exp_rows, key=lambda x: -x[1])[:5]
            except Exception as e:
                rep["findings"].append(f"輸入股票流程失敗：{type(e).__name__}: {str(e)[:200]}")
            save()

            # ---- C. 各頁：分頁籤 ----
            for nav in NAVS:
                if now() > DEADLINE:
                    break
                try:
                    page.locator('[data-testid="stSidebar"]').get_by_text(nav, exact=False).first.click(timeout=15000)
                    wait_idle_timed(page)
                    tabs = page.locator('[data-testid="stMain"] button[role="tab"]')
                    nt = min(tabs.count(), B.MAX_TABS)
                    for i in range(nt):
                        if now() > DEADLINE:
                            break
                        try:
                            el = tabs.nth(i)
                            name = (el.inner_text(timeout=3000) or "")[:30].replace("\n", " ")
                            el.scroll_into_view_if_needed(timeout=8000)
                            el.click(timeout=15000, force=True)
                            idle, sec = wait_idle_timed(page)
                            s2 = B.scan(page, f"{nav} › 分頁「{name}」", cerr)
                            s2["render_sec"] = sec
                            rep["pages"].append(s2)
                        except Exception as e:
                            rep["pages"].append({"label": f"{nav} › tab#{i}", "exceptions": [f"（測試操作失敗，非程式例外）{type(e).__name__}"], "alerts_error": [], "dirty_values": {}, "h_overflow": 0, "console_errors": []})
                except Exception as e:
                    rep["findings"].append(f"{nav} 分頁籤：{type(e).__name__}")
            save()

            # ---- D. 各頁：逐一點擊按鈕 ----
            for nav in NAVS:
                if now() > DEADLINE:
                    break
                try:
                    page.locator('[data-testid="stSidebar"]').get_by_text(nav, exact=False).first.click(timeout=15000)
                    wait_idle_timed(page)
                    # 先把頁面上空白的文字輸入框填 2330（方便按鈕有輸入可用）
                    boxes = page.locator('[data-testid="stMain"] [data-testid="stTextInput"] input[type="text"]')
                    for i in range(min(boxes.count(), 8)):
                        try:
                            bx = boxes.nth(i)
                            if not (bx.input_value() or "").strip():
                                bx.fill("2330")
                                bx.press("Enter")
                                wait_idle_timed(page, 60000)
                        except Exception:
                            pass
                    labels = page.eval_on_selector_all(
                        '[data-testid="stMain"] button:not([disabled])',
                        "els=>els.filter(e=>e.offsetParent!==null).map(e=>e.innerText.trim().replace(/\\s+/g,' ')).filter(t=>t.length>0)")
                    seen, todo = set(), []
                    for l in labels:
                        if l in seen or SKIP_BTN.search(l) or l.startswith("keyboard") or len(l) > 80:
                            continue
                        seen.add(l)
                        todo.append(l)
                    rep.setdefault("button_counts", {})[nav] = {"found": len(labels), "unique_clickable": len(todo)}
                    for l in todo[:BTN_CAP]:
                        if now() > DEADLINE:
                            break
                        try:
                            btn = page.locator('[data-testid="stMain"] button:not([disabled])').filter(has_text=l).first
                            btn.scroll_into_view_if_needed(timeout=8000)
                            t = now()
                            btn.click(timeout=15000, force=True)
                            idle, sec = wait_idle_timed(page, BTN_WAIT)
                            s2 = B.scan(page, f"{nav} › 按鈕「{l[:40]}」", cerr)
                            s2["render_sec"] = sec
                            s2["idle"] = idle
                            rep["pages"].append(s2)
                            rep["buttons"].append({"nav": nav, "label": l[:60], "sec": sec, "idle": idle,
                                                   "exceptions": len(s2["exceptions"]), "alerts": len(s2["alerts_error"])})
                        except Exception as e:
                            rep["buttons"].append({"nav": nav, "label": l[:60], "error": f"{type(e).__name__}"})
                    save()
                except Exception as e:
                    rep["findings"].append(f"{nav} 按鈕掃描：{type(e).__name__}: {str(e)[:150]}")
        b.close()
    srv.terminate()
    bw = []
    try:
        for ln in open("blocked_writes.jsonl", encoding="utf-8"):
            bw.append(json.loads(ln))
    except Exception:
        pass
    rep["blocked_writes"] = bw
    rep["elapsed_sec"] = round(now() - t_boot, 1)
    # ---- markdown 摘要 ----
    L = [f"# 戰情室主帳號(admin)全面測試｜登入：{rep['login']}｜總耗時 {rep['elapsed_sec']}s", "",
         f"## 速度（Actions 共享 2 核主機，僅供相對比較）", f"- {json.dumps(rep['timings'], ensure_ascii=False)}"]
    if rep["findings"]:
        L += ["", "## 重大發現", *[f"- {x}" for x in rep["findings"]]]
    L += ["", f"## 輸入股票：{STOCKS}", f"- 新增畫面行數 {rep.get('stocks_new_line_count')}，髒值行 {len(rep.get('stocks_dirty_lines', []))}，資料缺漏字樣行 {len(rep.get('stocks_missing_lines', []))}"]
    L += [f"  - 缺漏：{x[:140]}" for x in rep.get("stocks_missing_lines", [])[:25]]
    L += [f"  - 髒值：{x[:140]}" for x in rep.get("stocks_dirty_lines", [])[:15]]
    L += ["", "## 各檢查點"]
    for s in rep["pages"]:
        bad = []
        if s.get("exceptions"): bad += [f"❌例外：{x[:160]}" for x in s["exceptions"]]
        if s.get("alerts_error"): bad += [f"⚠️錯誤訊息：{x[:120]}" for x in s["alerts_error"]]
        if s.get("dirty_values"): bad += [f"🧹髒值 {k}：{v[0]}" for k, v in s["dirty_values"].items()]
        if (s.get("h_overflow") or 0) > 5: bad += [f"↔橫向溢出 {s['h_overflow']}px"]
        if s.get("idle") is False: bad += ["⏱逾時未跑完"]
        rs = f"（{s['render_sec']}s）" if s.get("render_sec") is not None else ""
        L.append(f"- {'✅' if not bad else '🔶'} {s['label']}{rs}" + ("".join(f"\n    - {x}" for x in bad)))
    L += ["", f"## 按鈕點擊 {len(rep['buttons'])} 顆（>30s 或錯誤者）"]
    L += [f"- {b['nav']} › {b['label']}：{b.get('sec')}s idle={b.get('idle')} {b.get('error','')}" for b in rep["buttons"] if (b.get('sec') or 0) > 30 or b.get('error') or not b.get('idle', True)]
    seen = {}
    for x in bw:
        k = (x.get("kind"), str(x.get("table"))[-40:])
        seen[k] = seen.get(k, 0) + 1
    L += ["", f"## admin 操作期間觸發的寫入嘗試（已攔截，沒有真的寫入）共 {len(bw)} 筆"]
    L += [f"- {k[0]} → {k[1]}｜{n} 次" for k, n in seen.items()]
    open(f"{OUT}/report.md", "w", encoding="utf-8").write("\n".join(L))
    json.dump(rep, open(f"{OUT}/report.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
    print(f"完成：{len(rep['pages'])} 個檢查點、{len(rep['buttons'])} 顆按鈕，耗時 {rep['elapsed_sec']}s")


if __name__ == "__main__":
    main()
