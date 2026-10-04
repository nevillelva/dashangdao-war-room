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
MAX_EXP = int(os.environ.get("MAX_EXPANDERS", "25"))
MAX_TABS = int(os.environ.get("MAX_TABS", "25"))
DIRTY = re.compile(r"(?<![A-Za-z0-9_])(nan|NaN|None|NaT|undefined|inf|-inf|null)(?![A-Za-z0-9_])")
NAVS = ["盤中作戰", "策略回測", "情報覆盤", "ETF月配"]


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
    report = {"pages": [], "login": None, "findings": []}
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
                    # 展開所有 expander
                    exps = page.locator('[data-testid="stExpander"] summary')
                    n = min(exps.count(), MAX_EXP)
                    for i in range(n):
                        if time.time() > DEADLINE:
                            break
                        try:
                            el = exps.nth(i)
                            name = (el.inner_text(timeout=3000) or "")[:40].replace("\n", " ")
                            el.click(timeout=5000)
                            wait_idle(page)
                            report["pages"].append(scan(page, f"{nav} › 展開「{name}」", cerr))
                        except Exception as e:
                            report["pages"].append({"label": f"{nav} › expander#{i}", "exceptions": [f"操作失敗 {type(e).__name__}"], "alerts_error": [], "dirty_values": {}, "h_overflow": 0, "console_errors": []})
                    # 點所有分頁籤
                    tabs = page.locator('button[role="tab"]')
                    nt = min(tabs.count(), MAX_TABS)
                    for i in range(nt):
                        if time.time() > DEADLINE:
                            break
                        try:
                            el = tabs.nth(i)
                            name = (el.inner_text(timeout=3000) or "")[:30].replace("\n", " ")
                            el.click(timeout=5000)
                            wait_idle(page)
                            report["pages"].append(scan(page, f"{nav} › 分頁「{name}」", cerr))
                        except Exception as e:
                            report["pages"].append({"label": f"{nav} › tab#{i}", "exceptions": [f"操作失敗 {type(e).__name__}"], "alerts_error": [], "dirty_values": {}, "h_overflow": 0, "console_errors": []})
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
    L = [f"# 戰情室真瀏覽器點擊測試｜登入：{report['login']}｜耗時 {report['elapsed_sec']}s", ""]
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
