#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ui_browser_test.py —— 真瀏覽器(Playwright)逐頁點擊：以 viewer 登入，切換導覽、展開所有 expander、點所有分頁籤，
掃描畫面是否有例外、nan/None 這類髒值、錯誤訊息、橫向溢出。只點「導覽/展開/分頁籤」，不點任何按鈕。
寫入防護由 guarded_app.py 提供（Supabase 寫入、GitHub/Telegram 寫入類請求一律攔截）。"""
import os, re, sys, json, time, subprocess, urllib.request

PIN = os.environ["TEST_VIEWER_PIN"]
PORT = 8501
URL = f"http://localhost:{PORT}"
OUT = "ui_browser_out"
os.makedirs(OUT, exist_ok=True)
IDLE_TIMEOUT = int(os.environ.get("IDLE_TIMEOUT", "300")) * 1000
DEADLINE = time.time() + int(os.environ.get("MAX_TOTAL_SEC", "4800"))
MAX_EXP = int(os.environ.get("MAX_EXPANDERS", "400"))
MAX_TABS = int(os.environ.get("MAX_TABS", "60"))
DIRTY = re.compile(r"(?<![A-Za-z0-9_])(nan|NaN|None|NaT|undefined|inf|-inf|null)(?![A-Za-z0-9_])")
NAVS = ["盤中作戰", "策略回測", "情報覆盤", "ETF月配", "早盤情報"]


def wait_idle(page):
    """等 Streamlit 跑完（右上角 Running 狀態消失）。"""
    try:
        page.wait_for_timeout(800)
        page.wait_for_function("() => !document.querySelector('[data-testid=\"stStatusWidget\"]')", timeout=IDLE_TIMEOUT)
    except Exception:
        return False
    page.wait_for_timeout(500)
    return True


def scan(page, label, console_errors):
    d = {"label": label}
    d["exceptions"] = page.eval_on_selector_all('[data-testid="stException"]', "els=>els.map(e=>e.innerText.slice(0,400))")
    d["alerts_error"] = page.eval_on_selector_all('[data-testid="stAlert"]', "els=>els.map(e=>e.innerText.trim().slice(0,200)).filter(t=>/❌|失敗|錯誤|error|Error|無法|異常/.test(t))")
    txt = page.evaluate("() => document.body.innerText")
    hits = {}
    for m in DIRTY.finditer(txt):
        ctx = txt[max(0, m.start() - 25):m.end() + 15].replace("\n", " ")
        hits.setdefault(m.group(1), []).append(ctx)
    d["dirty_values"] = {k: v[:4] for k, v in hits.items()}
    d["h_overflow"] = page.evaluate("() => document.documentElement.scrollWidth - window.innerWidth")
    d["console_errors"] = list(dict.fromkeys(console_errors))[-8:]
    console_errors.clear()
    return d


def expand_in_active_tab(page, label, cerr, report, budget):
    """【2026-10-13】分頁籤內的 expander：主迴圈的展開步驟在點分頁之前就跑完，藏在其他分頁籤裡的區塊（例如 ETF 分頁的「🧊 成交稀少」）
    當時不可見、點不到。這裡在每個分頁籤被點開後，只對「目前可見且仍收合」的 summary 再展開一遍並掃描。
    回傳本次展開數。budget＝剩餘可用展開次數。"""
    sel = '[data-testid="stMain"] [data-testid="stExpander"] details:not([open]) > summary'
    n, skip = 0, 0          # skip＝清單前面已確定點不了（不可見或失敗）的項數，避免重複點同一個
    while n < budget and time.time() <= DEADLINE:
        if skip >= page.locator(sel).count():
            break
        sm = page.locator(sel).nth(skip)
        name = f"位置{skip}"
        try:
            if not sm.is_visible():
                skip += 1
                continue
            name = (sm.inner_text(timeout=3000) or "")[:40].replace("\n", " ") or name
            sm.scroll_into_view_if_needed(timeout=8000)
            sm.click(timeout=15000, force=True)
            n += 1
            wait_idle(page)
            report["pages"].append(scan(page, f"{label} › 展開「{name}」", cerr))
        except Exception as e:
            skip += 1
            report["pages"].append({"label": f"{label} › 展開失敗「{name}」", "exceptions": [f"（測試操作失敗，非程式例外）{type(e).__name__}: {str(e)[:160]}"], "alerts_error": [], "dirty_values": {}, "h_overflow": 0, "console_errors": []})
    return n


def main():
    from playwright.sync_api import sync_playwright
    env = dict(os.environ)
    srv = subprocess.Popen([sys.executable, "-m", "streamlit", "run", "guarded_app.py", "--server.headless", "true",
                            "--server.port", str(PORT), "--browser.gatherUsageStats", "false"],
                           stdout=open("streamlit_stdout.txt", "w"), stderr=subprocess.STDOUT, env=env)
    for _ in range(90):
        try:
            urllib.request.urlopen(URL + "/_stcore/health", timeout=2)
            break
        except Exception:
            time.sleep(2)
    report = {"pages": [], "login": None, "findings": [],
              # 【2026-10-13】頁面內容隨台北時間變動（隔夜自動掃描於隔天 13:30 後消失），每次執行記下時間以利比對
              "tw_time": time.strftime("%H:%M", time.gmtime(time.time() + 8 * 3600))}
    t0 = time.time()
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page(viewport={"width": 1400, "height": 900}, locale="zh-TW")
        cerr = []
        page.on("console", lambda m: cerr.append(m.text[:200]) if m.type == "error" else None)
        page.on("pageerror", lambda e: cerr.append("pageerror: " + str(e)[:200]))
        page.goto(URL, wait_until="domcontentloaded", timeout=120000)
        try:
            page.wait_for_selector('input[type="password"]', timeout=180000)
            page.fill('input[type="password"]', PIN)
            page.get_by_role("button", name=re.compile("登入")).first.click()
            page.wait_for_selector('[data-testid="stSidebar"]', timeout=IDLE_TIMEOUT)
            wait_idle(page)
            report["login"] = "ok"
        except Exception as e:
            report["login"] = f"登入失敗 {type(e).__name__}: {str(e)[:200]}"
            report["findings"].append(report["login"])
            page.screenshot(path=f"{OUT}/login_fail.png")
        if report["login"] == "ok":
            report["pages"].append(scan(page, "登入後首頁", cerr))
            for nav in NAVS:
                if time.time() > DEADLINE:
                    break
                t1 = time.time()
                try:
                    page.locator('[data-testid="stSidebar"]').get_by_text(nav, exact=False).first.click(timeout=15000)
                    idle = wait_idle(page)
                    sc = scan(page, f"切到 {nav}", cerr)
                    sc["idle"] = idle
                    report["pages"].append(sc)
                    # 展開所有 expander：只點「尚未展開」的 summary（含巢狀），點開後清單自動前移，避免索引跑位。
                    # 點失敗就跳過該項（skip+1），確保不會無限重試同一個元素。
                    # 依頁面固定順序逐一檢查每個 summary：若所屬 details 仍是收合，就點開。
                    # 名稱可能重複（例如多個「三關細節」），所以不用名稱去重，改用位置。
                    # 最多跑 6 輪（巢狀與被收回的區塊需要多輪），讓它們有機會被開啟；同一輪內點過又收回的記入 reclosed_after_open。
                    sum_sel = '[data-testid="stMain"] [data-testid="stExpander"] details > summary'
                    attempts = 0
                    clicked_names, reclosed, deferred_hidden = set(), set(), set()
                    for _pass in range(6):
                        total = page.locator(sum_sel).count()
                        if total == 0:
                            break
                        for i in range(total):
                            if attempts >= MAX_EXP or time.time() > DEADLINE:
                                break
                            sm = page.locator(sum_sel).nth(i)
                            try:
                                is_open = sm.evaluate("e => e.parentElement.open")
                            except Exception:
                                continue
                            if is_open:
                                continue
                            try:
                                name = (sm.inner_text(timeout=3000) or "")[:40].replace("\n", " ")
                            except Exception:
                                name = f"第{i}個"
                            if not name:
                                name = f"（無名稱，位置{i}）"
                            # 不可見＝外層仍收合（巢狀區塊）：先依序打開外層，再點本區塊
                            if not sm.is_visible():
                                page.evaluate("""(i) => {
                                    const s = document.querySelectorAll('[data-testid="stMain"] [data-testid="stExpander"] details > summary')[i];
                                    if (!s) return 0;
                                    let d = s.parentElement.parentElement, n = 0;
                                    while (d) {
                                        if (d.tagName === 'DETAILS' && !d.open) { d.querySelector(':scope > summary').click(); n++; }
                                        d = d.parentElement;
                                    }
                                    return n; }""", i)
                                wait_idle(page)
                            if _pass >= 1 and name in clicked_names:
                                reclosed.add(name)
                            attempts += 1
                            try:
                                sm.scroll_into_view_if_needed(timeout=8000)
                                sm.click(timeout=15000, force=True)
                                clicked_names.add(name)
                                wait_idle(page)
                                report["pages"].append(scan(page, f"{nav} › 展開「{name}」", cerr))
                            except Exception as e:
                                try:
                                    hidden_in_tab = not sm.is_visible()
                                except Exception:
                                    hidden_in_tab = False
                                if hidden_in_tab:
                                    # 藏在其他分頁籤裡、此刻不可見：不算失敗，等點到該分頁籤後由 expand_in_active_tab 展開並掃描
                                    deferred_hidden.add(name)
                                    continue
                                report["pages"].append({"label": f"{nav} › 展開失敗「{name}」", "exceptions": [f"（測試操作失敗，非程式例外）{type(e).__name__}: {str(e)[:160]}"], "alerts_error": [], "dirty_values": {}, "h_overflow": 0, "console_errors": []})
                        if attempts >= MAX_EXP or time.time() > DEADLINE:
                            break
                    closed_sel = '[data-testid="stMain"] [data-testid="stExpander"] details:not([open]) > summary'
                    # 覆蓋統計：這一頁共有幾個 expander、點了幾個未展開的、仍有幾個未展開（預設展開的不需點，頁面文字已整頁掃描）
                    report["pages"].append({"label": f"{nav} › 覆蓋統計", "exceptions": [], "alerts_error": [], "dirty_values": {}, "h_overflow": 0, "console_errors": [],
                                            "coverage": {"expanders_total": page.locator('[data-testid="stMain"] [data-testid="stExpander"]').count(),
                                                         "clicked_closed": attempts, "still_closed": page.locator(closed_sel).count(),
                                                         "default_open": page.locator('[data-testid="stMain"] [data-testid="stExpander"] details[open]').count(),
                                                         "reclosed_after_open": sorted(reclosed)[:30],
                                                         "deferred_to_tab_pass": sorted(deferred_hidden)[:30],
                                                         "still_closed_names": [page.locator(closed_sel).nth(k).inner_text(timeout=2000)[:40].replace("\n", " ") for k in range(min(page.locator(closed_sel).count(), 30))],
                                                         # 隔夜自動掃描區塊是否出現（只在台北時間 13:30 前、且有當日掃描資料時顯示）；未出現則下方查N／重疊區 expander 不會存在
                                                         "tw_time": report["tw_time"],
                                                         "overnight_banner": page.get_by_text("隔夜自動掃描").count()}})
                    # 【2026-10-05】重面板改成「▸ 標題」開關（打開才執行）：逐一打開並掃描，確保面板內容仍被測到
                    tgs = page.locator('[data-testid="stMain"] [data-testid="stToggle"]')
                    ntg = min(tgs.count(), MAX_EXP)
                    for i in range(ntg):
                        if time.time() > DEADLINE:
                            break
                        try:
                            el = tgs.nth(i)
                            name = (el.inner_text(timeout=3000) or "")[:40].replace("\n", " ")
                            if not name.strip().startswith("▸"):
                                continue
                            el.scroll_into_view_if_needed(timeout=8000)
                            el.locator("label").first.click(timeout=15000, force=True)
                            wait_idle(page)
                            report["pages"].append(scan(page, f"{nav} › 開啟「{name}」", cerr))
                        except Exception as e:
                            report["pages"].append({"label": f"{nav} › toggle#{i}", "exceptions": [f"（測試操作失敗，非程式例外）{type(e).__name__}: {str(e)[:160]}"], "alerts_error": [], "dirty_values": {}, "h_overflow": 0, "console_errors": []})
                    # 點所有分頁籤
                    tabs = page.locator('[data-testid="stMain"] button[role="tab"]')
                    nt = min(tabs.count(), MAX_TABS)
                    for i in range(nt):
                        if time.time() > DEADLINE:
                            break
                        try:
                            el = tabs.nth(i)
                            name = (el.inner_text(timeout=3000) or "")[:30].replace("\n", " ")
                            el.scroll_into_view_if_needed(timeout=8000)
                            el.click(timeout=15000, force=True)
                            wait_idle(page)
                            report["pages"].append(scan(page, f"{nav} › 分頁「{name}」", cerr))
                            tab_exp = expand_in_active_tab(page, f"{nav} › 分頁「{name}」", cerr, report, max(0, MAX_EXP - attempts))
                            attempts += tab_exp
                        except Exception as e:
                            report["pages"].append({"label": f"{nav} › tab#{i}", "exceptions": [f"（測試操作失敗，非程式例外）{type(e).__name__}: {str(e)[:160]}"], "alerts_error": [], "dirty_values": {}, "h_overflow": 0, "console_errors": []})
                    report["pages"][-1]["nav_elapsed"] = round(time.time() - t1, 1)
                except Exception as e:
                    report["findings"].append(f"{nav}：{type(e).__name__}: {str(e)[:200]}")
                json.dump(report, open(f"{OUT}/report.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        b.close()
    srv.terminate()
    bw = []
    try:
        for ln in open("blocked_writes.jsonl", encoding="utf-8"):
            bw.append(json.loads(ln))
    except Exception:
        pass
    report["blocked_writes"] = bw
    report["elapsed_sec"] = round(time.time() - t0, 1)
    # 彙整 markdown
    L = [f"# 戰情室真瀏覽器點擊測試｜登入：{report['login']}｜耗時 {report['elapsed_sec']}s｜台北時間 {report['tw_time']}", ""]
    if report["findings"]:
        L += ["## 重大發現", *[f"- {x}" for x in report["findings"]], ""]
    for s in report["pages"]:
        bad = []
        if s.get("exceptions"): bad += [f"❌例外：{x[:160]}" for x in s["exceptions"]]
        if s.get("alerts_error"): bad += [f"⚠️錯誤訊息：{x[:120]}" for x in s["alerts_error"]]
        if s.get("dirty_values"): bad += [f"🧹髒值 {k}：{v[0]}" for k, v in s["dirty_values"].items()]
        if (s.get("h_overflow") or 0) > 5: bad += [f"↔橫向溢出 {s['h_overflow']}px"]
        if s.get("console_errors"): bad += [f"🖥主控台：{x[:100]}" for x in s["console_errors"][:3]]
        if s.get("idle") is False: bad += ["⏱逾時未跑完"]
        L.append(f"- {'✅' if not bad else '🔶'} {s['label']}" + ("".join(f"\n    - {x}" for x in bad)))
    seen = {}
    for x in bw:
        k = (x.get("kind"), str(x.get("table"))[-40:], " > ".join(x.get("where", [])))
        seen[k] = seen.get(k, 0) + 1
    L += ["", f"## 真瀏覽器操作期間，viewer 觸發的寫入嘗試（已攔截）共 {len(bw)} 筆"]
    L += [f"- {k[0]} → {k[1]}｜{k[2]}｜{n} 次" for k, n in seen.items()]
    open(f"{OUT}/report.md", "w", encoding="utf-8").write("\n".join(L))
    json.dump(report, open(f"{OUT}/report.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
    print(f"完成：{len(report['pages'])} 個檢查點，耗時 {report['elapsed_sec']}s")


if __name__ == "__main__":
    main()
