#!/usr/bin/env python3
"""
54088 戰情室 — 系統自主選股排程腳本 (V160 A階段)
================================================================================
這支腳本由 GitHub Actions 排程觸發，「不用開網頁」就能自動跑，分三個階段：

  --stage signal   每交易日 22:00 執行：全市場掃描 → 選多空候選 → 寫入待執行清單
  --stage gate     隔日 8:55 執行：檢查隔夜總經，劇變則標記暫緩
  --stage execute  隔日 9:01 執行：用開盤價把待執行清單正式進場 + 檢查既有持倉出場

用法：
  python system_scheduler.py --stage signal
  python system_scheduler.py --stage gate
  python system_scheduler.py --stage execute

環境變數（在 GitHub Actions secrets 設定）：
  SUPABASE_URL, SUPABASE_KEY  — 同 Streamlit secrets
  FINMIND_TOKEN               — FinMind API token（逗號分隔多組）
  TELEGRAM_BOT_TOKEN          — Telegram Bot token（選填，設了才推播）
  TELEGRAM_CHAT_ID            — 你的 Telegram chat id（選填）

注意：這支腳本是獨立的，不 import Streamlit。它重用選股/出場的「純邏輯」，
      但資料存取直接走 Supabase（因為 GitHub Actions 環境沒有本機 SQLite）。
================================================================================
"""
import os
import sys
import argparse
from datetime import datetime

import requests

try:
    from supabase import create_client
except ImportError:
    print("需要安裝 supabase 套件：pip install supabase")
    sys.exit(1)


# ------------------------------------------------------------------------------
# 連線與工具
# ------------------------------------------------------------------------------
def get_supabase():
    url = os.environ.get("SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_KEY", "")
    if not url or not key:
        print("❌ 缺少 SUPABASE_URL / SUPABASE_KEY 環境變數")
        sys.exit(1)
    return create_client(url, key)


def notify_telegram(msg):
    """推播到 Telegram（若有設定 token）。無設定則只印出。
    【修復】原本用 requests.post() 沒有檢查回傳狀態碼——如果 Telegram API 說
    「chat_id 有問題」「token 無效」這類錯誤，是用 HTTP 狀態碼回傳的，不是連線例外，
    原本的 try/except 完全抓不到，導致整個排程顯示成功、但訊息其實沒送出去，
    而且看不到任何錯誤訊息。現在會檢查狀態碼，失敗時把 Telegram 實際回傳的錯誤原因印出來。
    """
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    print(msg)
    if not token or not chat_id:
        print("⚠️ Telegram 推播已跳過：TELEGRAM_BOT_TOKEN 或 TELEGRAM_CHAT_ID 未設定")
        return
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": msg, "parse_mode": "HTML"},
            timeout=10,
        )
        if resp.status_code == 200:
            print("✅ Telegram 推播成功")
        else:
            print(f"❌ Telegram 推播失敗（HTTP {resp.status_code}）：{resp.text}")
    except Exception as e:
        print(f"❌ Telegram 推播失敗（連線例外）: {e}")


def get_config(sb, key, default):
    try:
        r = sb.table("system_config").select("config_value").eq("config_key", key).limit(1).execute()
        if r.data:
            return r.data[0]["config_value"]
    except Exception:
        pass
    return default


# ------------------------------------------------------------------------------
# 資料抓取（yfinance + FinMind），與網頁版邏輯一致但獨立實作
# ------------------------------------------------------------------------------
def fetch_price_hist(symbol):
    """抓個股歷史股價（yfinance）。回傳 DataFrame 或 None。"""
    import yfinance as yf
    for suffix in (".TW", ".TWO"):
        try:
            tk = yf.Ticker(f"{symbol}{suffix}")
            hist = tk.history(period="3mo").dropna(subset=["Close"])
            if len(hist) >= 20:
                return hist
        except Exception:
            continue
    return None


def compute_signal_for(symbol):
    """
    精簡版訊號計算（排程專用，不依賴 Streamlit）：算評分、防守線、停利點。
    這裡只用技術面（均線/爆量/ATR），因為排程環境輕量。正式選股門檻靠評分。
    回傳 dict 或 None。
    """
    hist = fetch_price_hist(symbol)
    if hist is None:
        return None
    import numpy as np
    close = hist["Close"]
    cur = float(close.iloc[-1])
    ma5 = float(close.tail(5).mean())
    ma20 = float(close.tail(20).mean())
    prev = float(close.iloc[-2])
    gain = (cur - prev) / prev * 100 if prev else 0.0
    # ATR(14)
    high, low = hist["High"], hist["Low"]
    tr = (high - low).tail(14).mean()
    atr = float(tr) if tr and not np.isnan(tr) else cur * 0.02
    vol = hist["Volume"]
    vol_ratio = float(vol.iloc[-1] / vol.tail(20).mean()) if vol.tail(20).mean() > 0 else 1.0
    def_line = round(ma5 - 0.5 * atr, 2)
    take_profit = round(cur + atr, 2)

    # 評分（與網頁 determine_signal 精神一致的精簡版）
    score = 0
    if cur > ma5 > ma20:
        score += 2
    elif cur > ma5:
        score += 1
    elif cur < ma5:
        score -= 2
    if vol_ratio > 2.0:
        score += 1
    elif vol_ratio < 0.6:
        score -= 1
    # 爆量下殺強制偏空
    day_low = float(low.iloc[-1]); day_high = float(high.iloc[-1])
    rng = day_high - day_low
    close_near_low = rng > 0 and (cur - day_low) / rng <= 0.35
    if vol_ratio >= 2.0 and cur < float(hist["Open"].iloc[-1]) and gain < -1.0 and close_near_low:
        score = min(score, -3)

    return {"symbol": symbol, "price": cur, "score": score, "gain": round(gain, 2),
            "def_line": def_line, "take_profit": take_profit, "vol_ratio": round(vol_ratio, 2)}


def fetch_name_map(token):
    """
    【V160 修復】取得代號→名稱對照表。

    先前排程寫入持倉時是 "name": c["symbol"]，直接把代號當名稱塞進資料庫，
    所以畫面上「名稱」欄看到的全是數字（例如 2409 顯示成 2409 而不是友達）。
    這裡改用 FinMind TaiwanStockInfo（涵蓋上市/上櫃/興櫃全市場）建立真正的對照表。
    抓不到時回空 dict，呼叫端會退回顯示代號 —— 寧可顯示代號，也不編造名稱。
    """
    try:
        params = {"dataset": "TaiwanStockInfo"}
        if token:
            params["token"] = token
        r = requests.get("https://api.finmindtrade.com/api/v4/data",
                         params=params, timeout=20)
        rows = (r.json() or {}).get("data", []) or []
        return {str(x.get("stock_id", "")).strip(): str(x.get("stock_name", "")).strip()
                for x in rows
                if str(x.get("stock_id", "")).strip() and str(x.get("stock_name", "")).strip()}
    except Exception:
        return {}


def is_trading_day(d=None):
    """
    【V160 修復】非交易日防呆。

    先前 gate/execute 的 cron 設成週二~週六，Friday 22:00 選出來的單會在
    「週六」早上 09:01 被轉成持倉 —— 週六根本沒開盤，卻產生了 entry_date 是
    週六的持倉（總指揮官在附件3 發現 7/18、7/19 是六日卻有進場紀錄）。
    這裡做最後一道防線：週六日一律不建倉、不出場。

    注意：這只擋週末，不含國定假日（免費資料源沒有可靠的台股行事曆）。
    真正的保險是 execute 階段會用「最近一個交易日」的價格，
    且非交易日不會有新的收盤資料，所以不會產生錯誤的損益。
    """
    d = d or datetime.now()
    return d.weekday() < 5          # 0=週一 ... 4=週五


def get_scan_pool(sb):
    """
    取得掃描池：從 Supabase inst_holding 抓「最新一個交易日」的完整代號清單。
    【V160 修復】原本 limit(1000) 會漏掉，且可能混到跨日期的舊代號（含已停用者）。
    改成：先找最新日期，再對那一天分頁抓完整代號（突破1000筆上限），確保是真正的
    全市場掃描池，不是被截斷的子集。這點很重要——總指揮官指出：一旦排程改成背景
    全自動執行，掃全市場對使用者體驗沒有負擔（沒人在等畫面），所以應該用完整市場
    範圍才能得到精準的判斷與勝率，不該延用網頁版為了即時互動而設的容量上限。
    """
    try:
        r = sb.table("inst_holding").select("date").order("date", desc=True).limit(1).execute()
        if not r.data:
            return []
        latest_date = r.data[0]["date"]
        syms, start, page = set(), 0, 1000
        while True:
            r2 = (sb.table("inst_holding").select("symbol")
                  .eq("date", latest_date).range(start, start + page - 1).execute())
            batch = r2.data or []
            syms.update(row["symbol"] for row in batch)
            if len(batch) < page:
                break
            start += page
        return sorted(syms)
    except Exception:
        return []


# ------------------------------------------------------------------------------
# 各階段
# ------------------------------------------------------------------------------
def stage_health(sb):
    """
    【V160 新增】資料源健康度檢查 + 異常時 Telegram 告警。

    要解決的結構性風險：先前除權息欄位改名、營收參數矛盾這類問題，畫面上都只顯示
    「查無資料」，跟「本來就沒資料」長得一模一樣，每次都拖好幾輪才被發現。
    這個階段每天自動實測各資料源，壞掉當天就推播通知，不用等你察覺畫面怪怪的。

    刻意設計：只有「異常時」才推播。全部正常就安靜寫進 log 就好——
    每天推一則「一切正常」只會讓你對通知麻痺，真的出事時反而被忽略。
    """
    run_date = datetime.now().strftime("%Y-%m-%d")
    token = (os.environ.get("FINMIND_TOKEN") or "").split(",")[0].strip()
    checks = []

    def _probe(name, fn, ok_test, detail_fn):
        try:
            r = fn()
            ok = ok_test(r)
            checks.append((name, ok, detail_fn(r)))
        except Exception as e:
            checks.append((name, False, f"例外：{type(e).__name__}: {e}"))

    # 1) FinMind 全市場法人（批次籌碼的主來源）
    def _inst():
        url = "https://api.finmindtrade.com/api/v4/data"
        params = {"dataset": "TaiwanStockInstitutionalInvestorsBuySell",
                  "start_date": run_date, "end_date": run_date}
        if token:
            params["token"] = token
        return requests.get(url, params=params, timeout=20).json().get("data", [])
    _probe("FinMind 全市場法人", _inst, lambda r: len(r) > 100, lambda r: f"{len(r)} 列")

    # 2) 證交所除權息預告表（欄位名稱改過一次，最容易再壞）
    def _div():
        return requests.get("https://openapi.twse.com.tw/v1/exchangeReport/TWT48U_ALL",
                            timeout=15).json()
    _probe("證交所除權息表", _div, lambda r: isinstance(r, list) and len(r) > 0,
           lambda r: f"{len(r) if isinstance(r, list) else 0} 筆")

    # 3) 證交所個股日成交（掃描池排序依賴）
    def _turnover():
        return requests.get("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL",
                            timeout=15).json()
    _probe("證交所個股成交值", _turnover, lambda r: isinstance(r, list) and len(r) > 500,
           lambda r: f"{len(r) if isinstance(r, list) else 0} 檔")

    # 4) Supabase 連線（所有持倉/績效的家）
    def _sb_check():
        return sb.table("system_portfolio").select("id").limit(1).execute()
    _probe("Supabase 雲端", _sb_check, lambda r: r is not None, lambda r: "連線正常")

    bad = [c for c in checks if not c[1]]
    summary = "；".join(f"{n}={'OK' if ok else 'FAIL'}" for n, ok, _ in checks)
    try:
        sb.table("system_run_log").insert({
            "run_date": run_date, "stage": "health", "picked_count": 0,
            "executed_count": 0, "gate_status": "normal" if not bad else "error",
            "note": summary,
        }).execute()
    except Exception as e:
        print(f"[健康檢查] 寫入log失敗：{e}")

    if bad:
        # 只在異常時推播——每天推「一切正常」會讓你對通知麻痺
        lines = "\n".join(f"❌ {n}：{d}" for n, _, d in bad)
        notify_telegram(f"🩺 [{run_date}] 資料源異常警報\n{lines}\n\n"
                        f"（其餘 {len(checks) - len(bad)} 項正常）")
    print(f"[健康檢查] {summary}")


def stage_signal(sb):
    """22:00 選股：掃描 → 選多空候選 → 寫入 system_portfolio（status='pending'）。"""
    run_date = datetime.now().strftime("%Y-%m-%d")
    pool = get_scan_pool(sb)
    if not pool:
        notify_telegram(f"⚠️ [{run_date}] 選股階段：掃描池為空，無法選股")
        return

    # 【V160 修復】排除已經持有中的標的（同方向），跟網頁版 system_select_candidates 邏輯一致，
    # 避免排程重跑或漏執行補跑時對同一檔重複進場。
    # 【V160 修復2】排除範圍必須同時涵蓋 holding 與 pending：
    #   本階段寫入的是 status='pending'，要等隔日 stage_execute 才轉 holding。
    #   若同一天 signal 跑了兩次（手動測試 + 排程），第二次只查 holding 會看不到
    #   第一次留下的 pending，就對同一檔重複建倉 → 隔日兩筆一起轉 holding →
    #   之後各出場一次（症狀：出場通知同一檔重複、獲利%完全相同）。
    try:
        held = (sb.table("system_portfolio").select("symbol,side,status")
                .in_("status", ["holding", "pending"]).execute().data) or []
    except Exception:
        held = []
    held_long = {h["symbol"] for h in held if h.get("side") == "long"}
    held_short = {h["symbol"] for h in held if h.get("side") == "short"}

    longs, shorts = [], []
    for sym in pool:
        sig = compute_signal_for(sym)
        if not sig:
            continue
        if sig["score"] >= 3 and sym not in held_long:
            longs.append(sig)
        elif sig["score"] <= -3 and sym not in held_short:
            shorts.append(sig)
    longs.sort(key=lambda x: x["score"], reverse=True)
    shorts.sort(key=lambda x: x["score"])
    longs, shorts = longs[:5], shorts[:5]

    total_cap = int(float(get_config(sb, "system_pick_daily_capital", "300000")))

    def _mk_entries(cands, side):
        if not cands:
            return []
        per = total_cap / len(cands)
        out = []
        for c in cands:
            price = c["price"]
            shares = max(1, int(per / (price * 1000)))
            reason = (f"{'偏多攻擊' if side == 'long' else '偏空防守'}（評分{c['score']}）｜"
                      f"爆量比{c.get('vol_ratio', 0):.1f}｜漲跌{c.get('gain', 0):+.1f}%")
            out.append({
                "symbol": c["symbol"],
                # 【V160 修復】用真實股票名稱，抓不到才退回代號（不編造）
                "name": name_map.get(c["symbol"]) or c["symbol"],
                "side": side,
                "entry_date": run_date, "entry_price": price, "shares": shares,
                "capital": round(shares * price * 1000, 0),
                "def_line": c["def_line"], "take_profit": c["take_profit"],
                "status": "pending", "trigger_source": "scheduler",   # 待執行，等隔日開盤
                "select_reason": reason,
            })
        return out

    # 【V160 關鍵修復】總指揮官回報：早上沒收到閘門推播、開盤執行推播裡出場清單重複——
    # 深入排查時發現一個更根本、更早存在的問題：這裡呼叫 fetch_name_map(FINMIND_TOKEN)
    # 直接把環境變數的「名稱」當成 Python 變數在用，但 FINMIND_TOKEN 這個變數從頭到尾
    # 沒有在這個函式（或整個檔案任何地方）被真正賦值過——這是一直存在的 NameError，
    # 只是這次總指揮官手動觸發並仔細看了 log 才被抓到。
    # 修正：跟 stage_health() 用同一套讀法，從環境變數讀（支援逗號分隔多組token取第一組）。
    token = (os.environ.get("FINMIND_TOKEN") or "").split(",")[0].strip()
    name_map = fetch_name_map(token)
    entries = _mk_entries(longs, "long") + _mk_entries(shorts, "short")
    if entries:
        sb.table("system_portfolio").insert(entries).execute()
    sb.table("system_run_log").insert({
        "run_date": run_date, "stage": "signal", "picked_count": len(longs) + len(shorts),
        "executed_count": 0, "gate_status": "pending",
        "note": f"選股：做多{len(longs)}檔、做空{len(shorts)}檔待執行",
    }).execute()
    # 【V160 新增】總指揮官回報：推播只寫「做多5檔/做空5檔」，看不出是哪幾檔、投多少錢。
    # 這裡把每一檔的代號、名稱、進場價、張數、投入金額都列出來。
    # Telegram 單則訊息上限約4096字元，10檔明細大約600-800字元，不會超過；
    # 但仍保守設個上限，超過就只列前12檔並註明還有幾檔（寧可截斷也不要整則發不出去）。
    def _fmt_entries(items, label):
        if not items:
            return f"{label}：無"
        lines = [f"{label}：{len(items)} 檔"]
        for e in items[:12]:
            lines.append(f"  {e['symbol']} {e['name']}｜{e['entry_price']} 元"
                         f"×{e['shares']}張＝{int(e['capital']):,}元")
        if len(items) > 12:
            lines.append(f"  …另有 {len(items) - 12} 檔")
        return "\n".join(lines)

    _long_e = [e for e in entries if e["side"] == "long"]
    _short_e = [e for e in entries if e["side"] == "short"]
    _total_cap = int(sum(e["capital"] for e in entries))
    _msg = (f"📋 [{run_date}] 選股完成（明日開盤執行）\n\n"
            f"🔴 {_fmt_entries(_long_e, '做多')}\n\n"
            f"🔵 {_fmt_entries(_short_e, '做空')}\n\n"
            f"💰 合計投入：{_total_cap:,} 元")
    if not entries:
        _msg = f"📋 [{run_date}] 選股完成\n今日無符合標的，明日空手"
    notify_telegram(_msg)


def stage_gate(sb):
    """8:55 總經閘門：隔夜劇變則把今日 pending 標記暫緩。"""
    import yfinance as yf
    run_date = datetime.now().strftime("%Y-%m-%d")
    danger = []
    for name, sym in [("那斯達克", "^IXIC"), ("標普500", "^GSPC"), ("費半", "^SOX")]:
        try:
            hist = yf.Ticker(sym).history(period="5d").dropna(subset=["Close"])
            if len(hist) >= 2:
                pct = (float(hist["Close"].iloc[-1]) - float(hist["Close"].iloc[-2])) / float(hist["Close"].iloc[-2]) * 100
                if pct <= -2.0:
                    danger.append(f"{name} {pct:+.1f}%")
        except Exception:
            continue

    if danger:
        # 把今日 pending 全部標記 halted
        try:
            sb.table("system_portfolio").update({"status": "halted"}).eq("status", "pending").execute()
        except Exception:
            pass
        sb.table("system_run_log").insert({
            "run_date": run_date, "stage": "gate", "picked_count": 0, "executed_count": 0,
            "gate_status": "halted", "note": "隔夜劇變暫緩：" + "、".join(danger),
        }).execute()
        notify_telegram(f"🛑 [{run_date}] 總經閘門：隔夜劇變，今日暫緩進場\n" + "、".join(danger))
    else:
        sb.table("system_run_log").insert({
            "run_date": run_date, "stage": "gate", "picked_count": 0, "executed_count": 0,
            "gate_status": "normal", "note": "隔夜平穩，照計畫執行",
        }).execute()
        notify_telegram(f"✅ [{run_date}] 總經閘門：隔夜平穩，開盤照計畫執行")


def stage_execute(sb):
    """9:01 執行：pending → holding（進場）；同時檢查既有 holding 是否出場。"""
    run_date = datetime.now().strftime("%Y-%m-%d")

    # 【V160 修復】非交易日不建倉：週末不會有新的收盤資料，
    # 在週六把 pending 轉成持倉會產生 entry_date 是週六的假持倉。
    if not is_trading_day():
        print(f"⏭️ {run_date} 非交易日（週末），略過開盤執行階段")
        notify_telegram(f"⏭️ [{run_date}] 非交易日，今日不進場、不出場")
        return

    # 1) 進場：pending 轉 holding（正式版可改用當日開盤價；這裡沿用選股時的 entry_price）
    # 【V160 修復2 第二道防線】即使 pending 清單裡已經有重複（例如修復前殘留的舊資料），
    # 這裡也不會把同一檔同方向轉成兩筆持倉：同 symbol+side 只取第一筆轉 holding，
    # 其餘標記 'cancelled'（不進場、不計入勝率、保留紀錄可追查）。
    # 同時也擋掉「已經有 holding 中的同標的」再被 pending 疊上去的情況。
    duplicated = 0
    try:
        pend = sb.table("system_portfolio").select("*").eq("status", "pending").execute().data or []
        try:
            cur_hold = (sb.table("system_portfolio").select("symbol,side")
                        .eq("status", "holding").execute().data) or []
        except Exception:
            cur_hold = []
        seen = {(h.get("symbol"), h.get("side", "long")) for h in cur_hold}
        executed = 0
        for p in pend:
            key = (p.get("symbol"), p.get("side", "long"))
            if key in seen:
                sb.table("system_portfolio").update({
                    "status": "cancelled",
                    "exit_reason": "duplicate_skip",
                }).eq("id", p["id"]).execute()
                duplicated += 1
                continue
            seen.add(key)
            sb.table("system_portfolio").update({"status": "holding"}).eq("id", p["id"]).execute()
            executed += 1
    except Exception:
        executed = 0

    # 2) 出場：檢查 holding
    exits = []
    dup_holding_skip = 0
    try:
        holds = sb.table("system_portfolio").select("*").eq("status", "holding").execute().data or []
        # 【V160 新增第三道防線】總指揮官回報出場通知同一檔重複出現——即使前兩道防線
        # （建倉排除pending+holding、pending轉holding時去重）都生效，只要資料庫裡已經
        # 存在過重複的 holding 列（例如修復前的殘留、或這次cron分派bug造成的），
        # 出場檢查照樣會把每一列都獨立判斷、獨立出場，重複列就重複出場、重複顯示。
        # 這裡在處理出場前，同 symbol+side 只保留一列（id最小的），其餘標記
        # 'cancelled'/'duplicate_holding_cleanup'，不計入出場、不出現在通知裡。
        seen_hold_keys = set()
        deduped_holds = []
        for h in sorted(holds, key=lambda x: x.get("id", 0)):
            k = (h.get("symbol"), h.get("side", "long"))
            if k in seen_hold_keys:
                sb.table("system_portfolio").update({
                    "status": "cancelled", "exit_reason": "duplicate_holding_cleanup",
                }).eq("id", h["id"]).execute()
                dup_holding_skip += 1
                continue
            seen_hold_keys.add(k)
            deduped_holds.append(h)

        for h in deduped_holds:
            sig = compute_signal_for(h["symbol"])
            if not sig:
                continue
            cur = sig["price"]
            side = h.get("side", "long")
            entry = float(h.get("entry_price", 0) or 0)
            defl = float(h.get("def_line", 0) or 0)
            tp = float(h.get("take_profit", 0) or 0)
            reason = None
            if side == "long":
                if defl > 0 and cur <= defl:
                    reason = "stop_loss"
                elif tp > 0 and cur >= tp:
                    reason = "take_profit"
            else:
                if entry > 0 and cur >= entry * 1.03:
                    reason = "stop_loss"
                elif entry > 0 and cur <= entry * 0.95:
                    reason = "take_profit"
            if reason:
                shares = int(h.get("shares", 0) or 0)
                pnl = (cur - entry) * shares * 1000 if side == "long" else (entry - cur) * shares * 1000
                roi = (pnl / (entry * shares * 1000) * 100) if entry > 0 and shares > 0 else 0.0
                sb.table("system_portfolio").update({
                    "status": "closed", "exit_date": run_date, "exit_price": cur,
                    "exit_reason": reason, "realized_pnl": round(pnl, 0), "realized_roi": round(roi, 2),
                }).eq("id", h["id"]).execute()
                # 【V160 新增】出場原因改中文顯示，跟網頁版用同一份對照表邏輯
                # （這裡是獨立腳本，不import網頁版模組，維持一份小型對照表）
                _reason_zh = {'stop_loss': '停損', 'take_profit': '停利',
                             'trail_stop': '移動停利'}.get(reason, reason)
                exits.append(f"{h['symbol']}({'做多' if side=='long' else '做空'},{_reason_zh},{roi:+.1f}%)")
    except Exception as e:
        print(f"出場檢查錯誤: {e}")

    dup_note = f"；略過重複{duplicated}檔" if duplicated else ""
    dup_hold_note = f"；清除重複持倉{dup_holding_skip}檔" if dup_holding_skip else ""
    sb.table("system_run_log").insert({
        "run_date": run_date, "stage": "execute", "picked_count": 0, "executed_count": executed,
        "gate_status": "normal", "note": f"進場{executed}檔；出場{len(exits)}檔{dup_note}{dup_hold_note}",
    }).execute()
    msg = f"⚡ [{run_date}] 開盤執行\n進場：{executed} 檔"
    if duplicated:
        msg += f"（另略過重複 {duplicated} 檔）"
    if dup_holding_skip:
        msg += f"\n⚠️ 偵測並清除 {dup_holding_skip} 檔重複持倉（可能是排程曾誤觸發，建議檢查GitHub Actions執行紀錄）"
    if exits:
        msg += "\n出場：" + "、".join(exits)
    notify_telegram(msg)


# 【V160】排程版本標記——跟網頁版 BUILD_VERSION 是同一個機制，每次交付都要更新。
# 總指揮官發現先前排程可能一直在跑舊版（我們web app的修復都有同步更新版本號，
# 但排程檔案是獨立部署到GitHub Actions，容易忘記同步）。這行會印在GitHub Actions
# 的執行紀錄裡，之後點開任一次執行的log第一行就能確認跑的是不是最新版。
SCHEDULER_VERSION = "作戰室 排程 v1.0 (2026-07-21 Round24：選股推播加上個股明細與金額)"


# ------------------------------------------------------------------------------
def main():
    print(f"🏷️ {SCHEDULER_VERSION}")
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", required=True,
                        choices=["signal", "gate", "execute", "health"])
    args = parser.parse_args()
    sb = get_supabase()
    if args.stage == "signal":
        stage_signal(sb)
    elif args.stage == "gate":
        stage_gate(sb)
    elif args.stage == "execute":
        stage_execute(sb)
    elif args.stage == "health":
        stage_health(sb)


if __name__ == "__main__":
    main()
