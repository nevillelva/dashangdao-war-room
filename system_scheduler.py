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
import time
from datetime import datetime, timedelta

import requests

try:
    from supabase import create_client
except ImportError:
    print("需要安裝 supabase 套件：pip install supabase")
    sys.exit(1)

# 【V160 Round39 新增】共用核心模組——跟網頁版(warroom_v160.py)共同 import，
# 常數/ATR算法從此只維護一份。warroom_core.py本身完全不import streamlit，
# 在GitHub Actions這種沒有Streamlit runtime的環境安全可用。
# 需要跟 warroom_core.py 放在同一個 GitHub repo 根目錄，這支腳本才 import 得到。
try:
    import warroom_core as _wc
    from warroom_core import (
        DEF_LINE_ATR_MULT, calculate_atr, build_trade_zones,
        set_finmind_tokens, get_fm_quota_status, _finmind_get, FinMindAPIError,
        fetch_tdcc_holding_csv_direct, parse_tdcc_holding_csv, compute_big_holder_ratios,
        compute_small_holder_ratios,
        fetch_histock_branch_data,
        fetch_twse_attention_stocks, fetch_twse_disposal_stocks, fetch_tpex_disposal_stocks,
        check_disposal_attention_status, fetch_twse_material_announcements,
        filter_self_compiled_announcements,
        scan_volume_ratio_sensitivity, scan_six_day_gain_sensitivity,
        # 【R95續新增】查1~14+情報雷達 每週自動回測校準
        run_filter_backtest, summarize_filter_backtest, run_intel_radar_backtest,
        probe_price_data_availability,
    )
except ImportError:
    print("找不到 warroom_core.py——請確認它跟 system_scheduler.py 在同一個目錄。")
    sys.exit(1)

# 【R60新增】版本相容性檢查——網頁版(warroom_v160.py)已經加了同樣的檢查，
# 這裡一併補上，避免排程端也踩到「warroom_core.py沒跟著換版」這個已經
# 真實發生過兩次的bug類型，差別只是排程這邊發生時是完全沒有畫面、只能
# 從Telegram警報或GitHub Actions log事後才看得到。
_REQUIRED_CORE_VERSION = 101
if getattr(_wc, "CORE_VERSION", 0) < _REQUIRED_CORE_VERSION:
    print(f"[版本不同步] 這份 system_scheduler.py 需要 warroom_core.py "
          f"CORE_VERSION >= {_REQUIRED_CORE_VERSION}，但目前是 "
          f"{getattr(_wc, 'CORE_VERSION', '未知（太舊）')}。請確認 repo 裡的 "
          f"warroom_core.py 也已經換成最新版，兩個檔案要一起更新。")
    sys.exit(1)

# 【R47】FinMind 多帳號輪替 + illegal-token 判斷，原本只有網頁版(warroom_v160.py)
# 有，這支排程腳本一直是自己另一份獨立、更原始的實作（只取token第一組、無輪替、
# 無illegal判斷）。現在改用共用模組：這裡把環境變數裡的token清單（逗號分隔多組）
# 餵給 set_finmind_tokens()，之後所有FinMind請求都透過 _finmind_get() 走同一套
# 輪替/錯誤分類邏輯。這也順便修掉「只取split(',')[0]」那個小bug——多組token
# 現在才真的會被輪流用到。
set_finmind_tokens((os.environ.get("FINMIND_TOKEN") or "").split(","))


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
        # 【R95續6修復】原本parse_mode="HTML"但整個專案沒有任何一則Telegram
        # 訊息真的用到HTML標籤(<b>/<i>/<code>那些)，這個設定唯一的效果是把
        # 訊息文字裡剛好出現的<、>、&都當成HTML語法解析——這次濾網回測校準
        # 訊息裡的診斷文字帶了「(<40筆交易日)」，"<40筆交易日)"被Telegram
        # 誤判成一個開始標籤、格式不合法，整則推播直接被拒絕(HTTP 400)，
        # 總指揮官完全沒收到本來最關鍵的診斷訊息。改成純文字模式(拿掉
        # parse_mode)，徹底避免這整類「訊息內容剛好長得像HTML」就送不出去
        # 的問題，不用每次新增訊息文字都要小心會不會踩到<>&。
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": msg},
            timeout=10,
        )
        if resp.status_code == 200:
            print("✅ Telegram 推播成功")
        else:
            print(f"❌ Telegram 推播失敗（HTTP {resp.status_code}）：{resp.text}")
    except Exception as e:
        print(f"❌ Telegram 推播失敗（連線例外）: {e}")


def _clean_symbol(raw):
    """
    【R95續6新增】股票代號清洗——總指揮官回報排程log顯示「$5347.TW: possibly
    delisted」這種每一檔都失敗的狀況，追查後發現Supabase存的symbol本身帶了
    一個「$」字元前綴（很可能是使用者或情報雷達從社群貼文的cashtag寫法
    如"$2330"擷取進來的，那類平台常見用$開頭標記股票代號），變成
    yfinance查詢"$5347.TW"這種不存在的代號，每一檔都100%查無資料——不是
    yfinance被擋、也不是這批股票真的沒訊號，是代號本身格式錯了。

    這個函式在「使用symbol去打yfinance之前」統一清洗，去除常見的髒污：
    前後空白、開頭的$字元。之後如果發現其他髒污格式(例如小數點/全形字元)，
    在這裡加規則即可，不用每個呼叫端各自處理。
    """
    s = str(raw).strip()
    if s.startswith('$'):
        s = s[1:].strip()
    return s


def get_config(sb, key, default):
    try:
        r = sb.table("system_config").select("config_value").eq("config_key", key).limit(1).execute()
        if r.data:
            return r.data[0]["config_value"]
    except Exception:
        pass
    return default


def set_config(sb, key, value):
    """
    【V160 R43 新增】寫入 system_config 設定值——目前主要給08:55總經閘門
    存放今天的三態判斷結果(多頭順風/對沖模式/恐慌熔斷)，讓13:00-13:20的
    尾盤進場階段能讀回來決定要執行哪些候選標的。用upsert，同一個key
    每天覆蓋，不會累積歷史紀錄(如果需要歷史，system_run_log已經有記錄)。
    """
    try:
        sb.table("system_config").upsert(
            {"config_key": key, "config_value": str(value)}, on_conflict="config_key"
        ).execute()
        return True
    except Exception:
        return False


# ------------------------------------------------------------------------------
# 資料抓取（yfinance + FinMind），與網頁版邏輯一致但獨立實作
# ------------------------------------------------------------------------------
def fetch_price_hist(symbol):
    """抓個股歷史股價（yfinance）。回傳 DataFrame 或 None。"""
    import yfinance as yf
    for suffix in (".TW", ".TWO"):
        try:
            tk = yf.Ticker(f"{symbol}{suffix}")
            hist = tk.history(period="3mo", timeout=8).dropna(subset=["Close"])
            if len(hist) >= 20:
                return hist
        except Exception:
            continue
    return None


def compute_signal_for(symbol):
    """
    精簡版訊號計算（排程專用）：算評分、防守線、停利點。
    這裡只用技術面（均線/爆量/ATR），因為排程環境目前還沒有籌碼/基本面資料
    的抓取管線——完整多因子評分規劃在R41（那時本來就要幫排程加上新因子所需
    的資料抓取，屆時會一併把這裡換成跟網頁版一致的完整版）。

    【V160 Round39 修復】ATR跟防守線倍數改用 warroom_core 共用版本：
    - ATR原本是 (high-low).tail(14).mean()，只看當日高低差，漏掉跳空缺口的
      真實波動，會系統性低估有跳空的股票的ATR，導致防守線設太窄。
      calculate_atr() 用真實的True Range（同時考慮跳空），跟網頁版同一套算法。
    - 防守線倍數原本寫死0.5，改讀 DEF_LINE_ATR_MULT，跟網頁版共用同一個數字，
      以後這個常數永遠不會再兩邊不同步（這正是round36修過的健康檢查誤報、
      round24的token崩潰同一類「兩份程式碼各自維護」的問題）。
    回傳 dict 或 None。
    """
    hist = fetch_price_hist(symbol)
    if hist is None:
        return None
    close = hist["Close"]
    cur = float(close.iloc[-1])
    ma5 = float(close.tail(5).mean())
    ma10 = float(close.tail(10).mean()) if len(close) >= 10 else ma5
    ma20 = float(close.tail(20).mean())
    ma60 = float(close.tail(60).mean()) if len(close) >= 60 else ma20
    prev = float(close.iloc[-2])
    gain = (cur - prev) / prev * 100 if prev else 0.0
    atr = calculate_atr(hist)
    if atr <= 0:
        atr = cur * 0.02   # 資料不足時的保守預設，跟calculate_atr本身的防呆邏輯一致
    # 【V160 Round39 緊急修復】改用 calculate_atr() 時只拿掉了ATR自己算式裡用到的
    # high/low中間變數，沒注意到 high/low 在下面「爆量下殺」判定裡還要用——
    # 這行拿掉導致排程在 GitHub Actions 上直接 NameError 崩潰(第一次真正上線
    # 就被抓到)。這裡補回來，這次連同下面的用法一起確認過，不會再漏。
    high, low = hist["High"], hist["Low"]
    vol = hist["Volume"]
    vol_ratio = float(vol.iloc[-1] / vol.tail(20).mean()) if vol.tail(20).mean() > 0 else 1.0
    def_line = round(ma5 - DEF_LINE_ATR_MULT * atr, 2)
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
            "def_line": def_line, "take_profit": take_profit, "vol_ratio": round(vol_ratio, 2),
            "ma5": round(ma5, 2), "ma10": round(ma10, 2), "ma20": round(ma20, 2), "ma60": round(ma60, 2)}


def fetch_taiwan_stock_info_raw():
    """
    取得 FinMind TaiwanStockInfo 的原始資料列，供 fetch_name_map /
    fetch_listed_only_codes 共用同一次抓取結果衍生。
    含重試+失敗log，兩個衍生函式共用同一套錯誤處理。

    【R47 修復】改用共用的 _finmind_get()——原本這裡是自己一份獨立、原始的
    requests.get，只帶「第一組」token，遇到那組token失效（"Token is
    illegal."）或額度用盡，不會像網頁版一樣自動換下一組、退回訪客額度，
    會直接卡死回傳空資料。現在跟網頁版共用同一套多帳號輪替+illegal判斷邏輯
    （見 warroom_core.py），不再需要自己帶token參數。
    """
    for _attempt in range(2):
        try:
            payload = _finmind_get(
                "https://api.finmindtrade.com/api/v4/data",
                {"dataset": "TaiwanStockInfo"}, max_retries=2, timeout=20)
            rows = payload.get("data", []) or []
            if rows:
                return rows
            print(f"[TaiwanStockInfo] 第{_attempt+1}次嘗試回傳空資料（原始回應：{str(payload)[:300]}）")
        except FinMindAPIError as e:
            print(f"[TaiwanStockInfo] 第{_attempt+1}次嘗試失敗：{e.reason} - {e.detail[:200]}")
        except Exception as e:
            print(f"[TaiwanStockInfo] 第{_attempt+1}次嘗試失敗：{e}")
        if _attempt == 0:
            time.sleep(2)
    return []


def fetch_name_map(rows):
    """
    【V160 修復】取得代號→名稱對照表。

    先前排程寫入持倉時是 "name": c["symbol"]，直接把代號當名稱塞進資料庫，
    所以畫面上「名稱」欄看到的全是數字（例如 2409 顯示成 2409 而不是友達）。
    這裡改用 FinMind TaiwanStockInfo（涵蓋上市/上櫃/興櫃全市場）建立真正的對照表。
    抓不到時回空 dict，呼叫端會退回顯示代號 —— 寧可顯示代號，也不編造名稱。

    【V160 Round39-hotfix】改成接收已經抓好的 rows（見 fetch_taiwan_stock_info_raw），
    不再自己打一次API——這樣跟 fetch_listed_only_codes 共用同一次抓取結果，
    同一份資料在同一次執行裡不會被打兩次。
    """
    name_map = {str(x.get("stock_id", "")).strip(): str(x.get("stock_name", "")).strip()
               for x in rows
               if str(x.get("stock_id", "")).strip() and str(x.get("stock_name", "")).strip()}
    return name_map


def fetch_listed_only_codes(rows):
    """
    【V160 Round39 新增】取得「上市」(twse) 股票代號集合，供選股掃描池過濾用。

    總指揮官決定：自動排程只掃上市，上櫃股需要評估時由你自己手動加進網頁版
    的雷達/觀察區即可（那條路徑完全不受這裡的過濾影響）。理由：(1) 上櫃籌碼
    資料覆蓋率一直不如上市完整；(2) 縮小掃描範圍讓選股更快。

    【V160 Round39-hotfix】改成接收已經抓好的 rows（見 fetch_taiwan_stock_info_raw），
    跟 fetch_name_map 共用同一次抓取結果，不再各自獨立打一次API。
    """
    return {str(x.get("stock_id", "")).strip() for x in rows if x.get("type") == "twse"}


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


def get_scan_pool(sb, listed_codes=None):
    """
    取得掃描池：從 Supabase inst_holding 抓「最新一個交易日」的完整代號清單。
    【V160 修復】原本 limit(1000) 會漏掉，且可能混到跨日期的舊代號（含已停用者）。
    改成：先找最新日期，再對那一天分頁抓完整代號（突破1000筆上限），確保是真正的
    全市場掃描池，不是被截斷的子集。這點很重要——總指揮官指出：一旦排程改成背景
    全自動執行，掃全市場對使用者體驗沒有負擔（沒人在等畫面），所以應該用完整市場
    範圍才能得到精準的判斷與勝率，不該延用網頁版為了即時互動而設的容量上限。

    【V160 Round39 新增】只保留上市(twse)標的——理由見 fetch_listed_only_codes
    的說明。【Round39-hotfix】改成直接接收呼叫端算好的 listed_codes 集合，
    不再自己另外打一次API——這個上市清單現在跟名稱對照表共用同一次
    fetch_taiwan_stock_info_raw 抓取結果，同一份資料同次執行內只打一次。
    listed_codes 為 None 或空集合時不過濾（避免誤刪整個掃描池）。
    回傳 (掃描池清單, 上市過濾前的原始檔數) 供呼叫端記錄/推播。
    """
    try:
        r = sb.table("inst_holding").select("date").order("date", desc=True).limit(1).execute()
        if not r.data:
            return [], 0
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
        raw_count = len(syms)
        if listed_codes:
            syms = {s for s in syms if s in listed_codes}
        return sorted(syms), raw_count
    except Exception:
        return [], 0


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
    checks = []

    def _probe(name, fn, ok_test, detail_fn):
        try:
            r = fn()
            ok = ok_test(r)
            checks.append((name, ok, detail_fn(r)))
        except Exception as e:
            checks.append((name, False, f"例外：{type(e).__name__}: {e}"))

    # 1) FinMind 法人（單檔模式測，因為「全市場模式」是付費方案專屬）
    # 【V160 Round36 修復】總指揮官每天收到「FinMind 全市場法人：0列」的異常警報——
    # 這不是真的資料源壞了，是這個探測本身在測付費方案才能用的全市場模式，
    # 免費帳號本來就永遠是0列。round24 已經把網頁版(warroom_v160.py)的
    # check_data_source_health 改成測免費的單檔模式，但排程這裡是完全獨立的
    # 一份探測邏輯，那次沒有一起改到，導致這個誤報一直持續。改用2330單檔、
    # 近10天，這是免費方案打得到的真實探測。
    # 【R47】改用共用的 _finmind_get()，token失效/額度用盡時會自動換下一組，
    # 不會像先前那樣卡在單一組token上直接判定FAIL。
    def _inst():
        url = "https://api.finmindtrade.com/api/v4/data"
        _start = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")
        params = {"dataset": "TaiwanStockInstitutionalInvestorsBuySell",
                  "data_id": "2330", "start_date": _start}
        return _finmind_get(url, params, max_retries=2, timeout=20).get("data", [])
    _probe("FinMind 法人(單檔)", _inst, lambda r: len(r) > 0, lambda r: f"2330近10天 {len(r)} 列")

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
    # 【R47新增】每次健康檢查順便把FinMind額度用量印進log（不推播，只留紀錄），
    # 這樣排程端額度是不是快撞牆，也能像網頁版一樣事後查得到，不用只靠猜。
    for _row in get_fm_quota_status():
        print(f"[FinMind額度] {_row}")
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
    # TaiwanStockInfo只抓一次——name_map跟上市清單都從同一份rows衍生，不再讓
    # 同一個資料集在同次執行裡被打兩次。
    # 【R47】不再自己讀token/split(',')[0]——token清單已經在檔案開頭
    # set_finmind_tokens() 設定過，_finmind_get() 內部會自動輪替，
    # 不需要呼叫端自己管理用哪一組。
    _info_rows = fetch_taiwan_stock_info_raw()
    name_map = fetch_name_map(_info_rows)
    listed_codes = fetch_listed_only_codes(_info_rows)
    if not name_map:
        print("[名稱對照表] 本次抓取後 name_map 仍是空的——後面entries的名稱欄位會全部退回顯示代號。")

    pool, raw_count = get_scan_pool(sb, listed_codes)
    if not pool:
        notify_telegram(f"⚠️ [{run_date}] 選股階段：掃描池為空，無法選股")
        return
    # 【V160 Round39 新增】總指揮官希望知道「這次實際掃了幾檔」，不用再猜——
    # 之前排程的掃描池實際涵蓋範圍一直不透明(來自inst_holding，覆蓋率取決於
    # 同步狀況)，這裡明確記錄+推播讓每次選股完成的訊息都附帶這個數字。
    _excluded_otc = raw_count - len(pool)
    print(f"[掃描池] 上市過濾前 {raw_count} 檔 → 過濾後 {len(pool)} 檔"
          f"（排除上櫃/其他 {_excluded_otc} 檔）")

    # 【V160 修復】排除已經持有中的標的（同方向），跟網頁版 system_select_candidates 邏輯一致，
    # 避免排程重跑或漏執行補跑時對同一檔重複進場。
    # 【V160 修復2】排除範圍必須同時涵蓋 holding 與 pending：
    #   本階段寫入的是 status='pending'，要等隔日 stage_tail_entry(R43尾盤進場) 才轉 holding。
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
    # 【V160 Round39】Top5→Top10：加速樣本累積(每天最多20筆而非10筆)，也讓
    # R42回測校準時有低分股票的樣本可驗證「分數高低跟勝率有沒有關係」——
    # 只選最高分5檔永遠驗證不了這件事。
    longs, shorts = longs[:10], shorts[:10]

    # 【V160 Round39 修復】徹底修掉資金分配的兩個真bug，改用「各買1張＋
    # 報酬率等權」取代原本的金額平分制：
    #
    # 舊制的兩個bug（用7/21真實Telegram截圖驗證過）：
    #   Bug1：做多、做空分開呼叫_mk_entries，各自都拿「完整」total_cap去平分，
    #        實際總投入變成設定值的2倍（設30萬、實花59萬）。
    #   Bug2：shares=max(1,...)保底至少買1張，但高價股1張的金額可能遠超過
    #        該檔分配到的預算（141.5元的股票、每檔預算6萬，1張就爆2.36倍）。
    #
    # 改成「各買1張」後，這兩個bug的成因（分預算、算張數）整個消失——不是
    # 繞過問題，是問題賴以存在的機制本身不見了。
    #
    # 為什麼用等權而不是金額制：模擬倉的目的是驗證「這套選股邏輯準不準」，
    # 不是「這個投資組合賺多少錢」。金額制下，一檔大賠的高價股可以在美元
    # 損益上蓋過十檔小賺的低價股，但那只是部位大小的雜訊，不是訊號品質的
    # 真實反映。報酬率等權讓每一檔選股都有同等發言權，風報比/MDD算出來的
    # 才是訊號本身的品質，不會被股價高低這種無關的因素扭曲。
    #
    # capital 欄位保留，但降級成「純顯示用」——Telegram還是會顯示「若各買
    # 1張需投入X元」讓你有規模感，但不再是任何預算控管或部位大小的依據，
    # 也不影響 system_portfolio 統計時的等權計算（勝率/報酬%本來就是逐筆看
    # entry_price與exit_price的百分比變化，不吃capital這個欄位，所以這裡
    # 改變capital的意義不需要動任何下游統計邏輯）。
    def _mk_entries(cands, side):
        if not cands:
            return []
        out = []
        for c in cands:
            price = c["price"]
            shares = 1   # 各買1張，報酬率等權——不再有「預算」這個概念
            reason = (f"{'偏多攻擊' if side == 'long' else '偏空防守'}（評分{c['score']}）｜"
                      f"爆量比{c.get('vol_ratio', 0):.1f}｜漲跌{c.get('gain', 0):+.1f}%")
            out.append({
                "symbol": c["symbol"],
                # 【V160 修復】用真實股票名稱，抓不到才退回代號（不編造）
                "name": name_map.get(c["symbol"]) or c["symbol"],
                "side": side,
                "entry_date": run_date, "entry_price": price, "shares": shares,
                "capital": round(shares * price * 1000, 0),   # 純顯示用，不做預算控管
                "def_line": c["def_line"], "take_profit": c["take_profit"],
                "status": "pending", "trigger_source": "scheduler",   # 待執行，等隔日開盤
                "select_reason": reason,
            })
        return out

    # 【V160 Round39-hotfix】name_map 已經在函式最上面跟上市清單一起算好了
    # （共用同一次 fetch_taiwan_stock_info_raw），這裡不再重複抓取/呼叫。
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
            # 【V160 修復】總指揮官回報推播裡的價格出現一堆亂碼小數位
            # （例如 18.100000381469727），這是抓價來源本身的浮點數精度問題，
            # 沒有先四捨五入就直接塞進訊息。台股報價本來就是2位小數，這裡統一
            # 用 round(...,2) 清乾淨，跟畫面上戰卡顯示的價格精度一致。
            _price = round(float(e['entry_price']), 2)
            lines.append(f"  {e['symbol']} {e['name']}｜{_price} 元"
                         f"×{e['shares']}張＝{int(e['capital']):,}元")
        if len(items) > 12:
            lines.append(f"  …另有 {len(items) - 12} 檔")
        return "\n".join(lines)

    _long_e = [e for e in entries if e["side"] == "long"]
    _short_e = [e for e in entries if e["side"] == "short"]
    _total_cap = int(sum(e["capital"] for e in entries))
    _msg = (f"📋 [{run_date}] 選股完成（明日開盤執行）\n"
            f"🔎 本次掃描池：{len(pool)} 檔（上市，過濾前{raw_count}檔）\n\n"
            f"🔴 {_fmt_entries(_long_e, '做多')}\n\n"
            f"🔵 {_fmt_entries(_short_e, '做空')}\n\n"
            f"💰 合計投入：{_total_cap:,} 元")
    if not entries:
        _msg = f"📋 [{run_date}] 選股完成\n今日無符合標的，明日空手"
    notify_telegram(_msg)


def classify_gate_mode(sox_pct, tsm_pct, twii_bull):
    """
    【V160 R43 新增】三態總經閘門的純判斷邏輯，抽成獨立函式方便測試
    （不牽涉網路/Supabase/Telegram，單純的分類規則）。

    回傳 (mode, mode_zh, note)。mode 是給程式判斷用的英文代碼
    ('panic'/'hedge'/'bull')，mode_zh/note 是給人看的中文說明。
    """
    _sox_disp = f"{sox_pct:+.1f}%" if sox_pct is not None else "無資料"
    _tsm_disp = f"{tsm_pct:+.1f}%" if tsm_pct is not None else "無資料"

    if (sox_pct is not None and sox_pct <= -2.0) or (tsm_pct is not None and tsm_pct <= -2.5):
        return "panic", "🚨 恐慌熔斷", f"費半{_sox_disp}／TSM ADR{_tsm_disp}——今日0多單，只執行做空候選"
    elif sox_pct is not None and -1.9 <= sox_pct <= -0.5 and not twii_bull:
        return "hedge", "🟡 對沖模式", f"費半{_sox_disp}且大盤破20MA——做多/做空各50%資金建倉"
    else:
        return ("bull", "🟢 多頭順風",
                f"費半{_sox_disp}，大盤{'站上' if twii_bull else '破'}20MA——100%執行做多候選")


def stage_gate(sb):
    """
    8:55 總經閘門（R43 三態版，取代原本的binary暫緩/照常）。

    三態判斷（跟總指揮官確認過的規格，實際分類邏輯見 classify_gate_mode）：
      🚨 恐慌熔斷：費半跌幅<=-2.0% 或 TSM ADR跌幅<=-2.5%
                  → 今天0多單，只執行做空Top N候選
      🟡 對沖模式：費半跌幅落在 -0.5%~-1.9% 且 大盤跌破20MA
                  → 做多/做空各佔50%資金建倉
      🟢 多頭順風：以上皆非（美股平穩或上漲，或大盤仍站上20MA）
                  → 100%執行做多Top N候選，凍結做空清單

    判斷結果寫進 system_config（today_gate_mode/today_gate_date），
    13:00-13:20的尾盤進場階段會讀回來決定要執行哪些候選。存日期是防呆：
    如果哪天這個階段沒跑成功、尾盤階段讀到的是舊日期的值，能夠察覺不對勁
    而不是誤用昨天的判斷。

    這一版不再把pending標記halted——「要不要進場、進場比例多少」交給尾盤
    階段依三態模式執行，這裡的職責單純是「判斷今天是哪一態」。
    """
    import yfinance as yf
    run_date = datetime.now().strftime("%Y-%m-%d")

    def _pct_change(sym):
        try:
            hist = yf.Ticker(sym).history(period="5d", timeout=8).dropna(subset=["Close"])
            if len(hist) >= 2:
                prev, cur = float(hist["Close"].iloc[-2]), float(hist["Close"].iloc[-1])
                return (cur - prev) / prev * 100 if prev else None
        except Exception:
            pass
        return None

    sox_pct = _pct_change("^SOX")
    tsm_pct = _pct_change("TSM")

    # 大盤是否站上20MA——這裡用yfinance ^TWII，跟網頁版位階濾網同一套邏輯，
    # 但這是排程獨立的一次抓取(排程不import網頁版模組)。抓不到時保守假設
    # 站上20MA(不主動觸發對沖/熔斷)，避免資料源問題誤殺原本該執行的多單。
    twii_bull = True
    try:
        hist = yf.Ticker("^TWII").history(period="2mo", timeout=8).dropna(subset=["Close"])
        if len(hist) >= 20:
            close = float(hist["Close"].iloc[-1])
            ma20 = float(hist["Close"].tail(20).mean())
            twii_bull = close >= ma20
    except Exception:
        pass

    mode, mode_zh, note = classify_gate_mode(sox_pct, tsm_pct, twii_bull)

    set_config(sb, "today_gate_mode", mode)
    set_config(sb, "today_gate_date", run_date)

    sb.table("system_run_log").insert({
        "run_date": run_date, "stage": "gate", "picked_count": 0, "executed_count": 0,
        "gate_status": mode, "note": note,
    }).execute()
    notify_telegram(f"{mode_zh} [{run_date}] 總經閘門\n{note}")


def stage_morning_exit(sb):
    """
    【V160 R43 新增】9:15 早盤衝高出場檢查——只針對「做多」的既有持倉。

    背景：R43 把進場時機改成尾盤13:15-13:25，原本09:01的開盤價跳空過濾
    因此失去意義（尾盤進場當下不會有開盤跳空風險）。但既有的「做多」持倉
    還是需要在早盤監控——如果隔天早盤09:00-09:15內衝高，這是獲利了結的
    好時機，不用等到收盤才決定。

    規則（跟總指揮官確認過）：09:15定點檢查（不是盤中觸價就出場）——只看
    09:15這一刻的價格相對進場價漲幅是否 >= 5%，不是「盤中一度衝高就算」。
    這是刻意的設計：用定點檢查而非觸價，模擬結果才會誠實反映「真的做得到
    的績效」，觸價法會系統性高估（實務上很難精準賣在那一秒的高點）。

    只處理做多——做空回補用的是另一套「長線支撐或站上短期均線」規則，
    不適用這個+5%早盤衝高邏輯（做空的「衝高」對做空部位是虧損不是獲利）。
    """
    run_date = datetime.now().strftime("%Y-%m-%d")
    if not is_trading_day():
        print(f"⏭️ {run_date} 非交易日，略過09:15早盤出場檢查")
        return

    exits = []
    try:
        holds = (sb.table("system_portfolio").select("*")
                 .eq("status", "holding").eq("side", "long").execute().data) or []
        for h in holds:
            sig = compute_signal_for(h["symbol"])
            if not sig:
                continue
            cur = sig["price"]
            entry = float(h.get("entry_price", 0) or 0)
            if entry <= 0:
                continue
            gain_pct = (cur - entry) / entry * 100
            if gain_pct >= 5.0:
                shares = int(h.get("shares", 0) or 0)
                pnl = (cur - entry) * shares * 1000
                roi = (pnl / (entry * shares * 1000) * 100) if shares > 0 else 0.0
                sb.table("system_portfolio").update({
                    "status": "closed", "exit_date": run_date, "exit_price": cur,
                    "exit_reason": "morning_spike_exit",
                    "realized_pnl": round(pnl, 0), "realized_roi": round(roi, 2),
                }).eq("id", h["id"]).execute()
                exits.append(f"{h['symbol']}(早盤衝高,{roi:+.1f}%)")
    except Exception as e:
        print(f"09:15早盤出場檢查錯誤: {e}")

    if exits:
        sb.table("system_run_log").insert({
            "run_date": run_date, "stage": "morning_exit", "picked_count": 0,
            "executed_count": len(exits), "gate_status": "normal",
            "note": f"早盤衝高出場{len(exits)}檔",
        }).execute()
        notify_telegram(f"📈 [{run_date}] 09:15早盤衝高出場\n" + "、".join(exits))
    else:
        print(f"[{run_date}] 09:15早盤檢查：無持倉觸發+5%衝高出場")


def decide_exit_reason(side, cur, ma5, ma10, ma60, vol_ratio):
    """
    【V160 R43 新增】新的出場判斷規則，取代舊的固定%停損停利（entry*1.03/0.95、
    def_line/take_profit）——R43把進場時機改到尾盤，出場邏輯也跟著總指揮官
    確認過的新規格重新設計：

    做多賣出：跌破5MA或10MA任一 → 結構轉弱出場（ma_break）。
    做空回補：來到長期支撐（用60日均線MA60當代理，這是這裡的簡化選擇——
      「長期支撐」原本規格是質化描述，MA60是最接近的量化代理，記錄在這裡
      供之後檢視/調整）→ support_reached；或帶量站上短期均線（現價>MA5
      且量比>1.2，量比門檻沿用專案裡「帶量」的一般認定）→ ma_reclaim。

    抽成獨立純函式方便測試（不牽涉任何I/O），stage_tail_entry會呼叫這個
    做既有持倉的出場判斷。回傳 exit_reason 字串，不觸發時回 None。
    """
    if side == "long":
        if cur < ma5 or cur < ma10:
            return "ma_break"
    else:
        if cur <= ma60:
            return "support_reached"
        if cur > ma5 and vol_ratio > 1.2:
            return "ma_reclaim"
    return None


def stage_tail_entry(sb):
    """
    【V160 R43 新增】13:00觸發、等到13:20才真正動作的尾盤進場階段，取代原本
    9:01的開盤價進場。

    為什麼改成尾盤：總指揮官的交易邏輯——收盤前K線型態已大致底定（確認帶量
    突破壓力線、收出長下影線等），此時進場能確認實質買盤，避免早盤假突破
    騙線。09:01開盤價過濾（跳空風險攔截）因此被這個階段取代——尾盤進場當下
    不存在開盤跳空的問題，那個過濾機制本身也就沒有存在的必要。

    13:00觸發、等到13:20動作：緩解GitHub Actions排程cron的執行時間不精準
    問題（實測/官方文件都提過可能延遲數分鐘到十幾分鐘）。早一點觸發、
    程式自己睡到目標時間，比直接把cron設在13:20、又擔心它延遲跑到收盤後
    更可靠。

    籌碼面資料的已知限制：13:15-13:25進場當下，當天的三大法人/籌碼資料
    還沒公布（收盤後才更新），所以R41那些籌碼類因子在尾盤決策時只能用
    「昨天」的資料——這是市場資料的物理限制，不是系統缺陷，總指揮官已經
    知悉並接受這個取捨。

    三態閘門的執行邏輯：
      🟢 多頭順風：100%執行做多候選，做空候選全部跳過(不進場)
      🚨 恐慌熔斷：100%執行做空候選，做多候選全部跳過
      🟡 對沖模式：做多/做空候選都執行——在目前「各買1張、報酬率等權」的
                  資金模型下，沒有「50%資金」這個概念，等權模型下「兩邊
                  都執行」自然就是最貼近「多空各半」精神的做法（規格書
                  原本設想的50/50資金分配，是舊的金額制思維，此處記錄
                  這個對應關係，之後如果改回金額制需要重新設計這裡）。

    既有持倉的出場判斷改用 decide_exit_reason（MA5/10破線出場、MA60支撐/
    站上均線回補），取代舊的固定%停損停利。
    """
    run_date = datetime.now().strftime("%Y-%m-%d")
    if not is_trading_day():
        print(f"⏭️ {run_date} 非交易日（週末），略過尾盤進場階段")
        notify_telegram(f"⏭️ [{run_date}] 非交易日，今日不進場、不出場")
        return

    # 【等到13:20】13:00觸發後，先睡到目標時間再動作。已經過了13:20才執行
    # （例如手動補跑）就不睡，立刻處理。
    now = datetime.now()
    target = now.replace(hour=13, minute=20, second=0, microsecond=0)
    wait_sec = (target - now).total_seconds()
    if wait_sec > 0:
        print(f"[尾盤進場] 目前 {now.strftime('%H:%M:%S')}，等待 {int(wait_sec)} 秒到13:20再動作")
        time.sleep(wait_sec)

    # 讀今天的三態閘門判斷。日期對不上(閘門階段沒跑成功/資料是舊的)時，
    # 保守假設多頭順風，並在log註記這個異常情況，不悄悄用可能過期的模式。
    gate_mode = get_config(sb, "today_gate_mode", "bull")
    gate_date = get_config(sb, "today_gate_date", "")
    gate_stale = (gate_date != run_date)
    if gate_stale:
        gate_mode = "bull"
        print(f"⚠️ 今天的閘門判斷日期({gate_date})跟今天({run_date})對不上，"
              f"保守假設多頭順風，並繼續記錄這個異常")

    # 1) 進場：pending → holding，依三態模式決定要執行哪一側，
    #    entry_price 改用「這一刻」的真實現價（原本沿用22:00選股時的estimate，
    #    R43尾盤進場後這個estimate已經是超過12小時前的舊資料，不能再用）。
    duplicated = 0
    executed = 0
    skipped_by_mode = 0
    try:
        pend = sb.table("system_portfolio").select("*").eq("status", "pending").execute().data or []
        try:
            cur_hold = (sb.table("system_portfolio").select("symbol,side")
                        .eq("status", "holding").execute().data) or []
        except Exception:
            cur_hold = []
        seen = {(h.get("symbol"), h.get("side", "long")) for h in cur_hold}
        for p in pend:
            side = p.get("side", "long")
            # 三態模式決定這一側今天要不要執行
            if gate_mode == "bull" and side == "short":
                skipped_by_mode += 1
                continue
            if gate_mode == "panic" and side == "long":
                skipped_by_mode += 1
                continue
            # hedge模式兩側都執行，不跳過

            key = (p.get("symbol"), side)
            if key in seen:
                sb.table("system_portfolio").update({
                    "status": "cancelled", "exit_reason": "duplicate_skip",
                }).eq("id", p["id"]).execute()
                duplicated += 1
                continue
            seen.add(key)

            sig = compute_signal_for(p["symbol"])
            if not sig:
                # 抓不到即時價就不進場，保留pending狀態，下次執行時再試
                continue
            real_entry_price = sig["price"]
            sb.table("system_portfolio").update({
                "status": "holding", "entry_price": real_entry_price,
            }).eq("id", p["id"]).execute()
            executed += 1
    except Exception as e:
        print(f"尾盤進場錯誤: {e}")

    # 2) 出場：檢查既有 holding，改用新的MA破線/支撐回補規則
    exits = []
    dup_holding_skip = 0
    try:
        holds = sb.table("system_portfolio").select("*").eq("status", "holding").execute().data or []
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
            reason = decide_exit_reason(side, cur, sig["ma5"], sig["ma10"], sig["ma60"], sig["vol_ratio"])
            if reason:
                shares = int(h.get("shares", 0) or 0)
                pnl = (cur - entry) * shares * 1000 if side == "long" else (entry - cur) * shares * 1000
                roi = (pnl / (entry * shares * 1000) * 100) if entry > 0 and shares > 0 else 0.0
                sb.table("system_portfolio").update({
                    "status": "closed", "exit_date": run_date, "exit_price": cur,
                    "exit_reason": reason, "realized_pnl": round(pnl, 0), "realized_roi": round(roi, 2),
                }).eq("id", h["id"]).execute()
                _reason_zh = {"ma_break": "跌破均線", "support_reached": "來到支撐回補",
                             "ma_reclaim": "站上均線回補"}.get(reason, reason)
                exits.append(f"{h['symbol']}({'做多' if side=='long' else '做空'},{_reason_zh},{roi:+.1f}%)")
    except Exception as e:
        print(f"尾盤出場檢查錯誤: {e}")

    dup_note = f"；略過重複{duplicated}檔" if duplicated else ""
    dup_hold_note = f"；清除重複持倉{dup_holding_skip}檔" if dup_holding_skip else ""
    mode_note = f"；閘門模式={gate_mode}" + ("(⚠️日期過期改保守)" if gate_stale else "")
    sb.table("system_run_log").insert({
        "run_date": run_date, "stage": "tail_entry", "picked_count": 0, "executed_count": executed,
        "gate_status": gate_mode, "note": f"進場{executed}檔；出場{len(exits)}檔{dup_note}{dup_hold_note}{mode_note}",
    }).execute()
    msg = f"⚡ [{run_date}] 尾盤進場執行（13:20）\n閘門模式：{gate_mode}\n進場：{executed} 檔"
    if skipped_by_mode:
        msg += f"（依閘門模式跳過 {skipped_by_mode} 檔）"
    if duplicated:
        msg += f"（另略過重複 {duplicated} 檔）"
    if dup_holding_skip:
        msg += f"\n⚠️ 偵測並清除 {dup_holding_skip} 檔重複持倉（可能是排程曾誤觸發，建議檢查GitHub Actions執行紀錄）"
    if exits:
        msg += "\n出場：" + "、".join(exits)
    notify_telegram(msg)



def _cleanup_old_broker_flows(sb, keep_days=31):
    """
    【R74新增】全市場天天抓分點，估算每天新增約32,000筆(1076檔×約30筆)、
    5-8MB，一年下來會累積到1.3-2GB，可能超過Supabase免費方案的資料庫
    空間上限。連續買超判讀最多只看近幾天到一個月的變化，沒必要無限期
    保留全市場歷史，所以只保留最近31天，超過的自動清掉，讓儲存空間
    穩定在可控範圍（估算約150-240MB）。

    放在stage_broker_flows每次執行的最後面做，不用另外開一個排程時段——
    反正這個階段本來就會連進broker_flows寫資料，順手清一次舊資料成本
    很低。
    """
    try:
        cutoff = (datetime.now() - timedelta(days=keep_days)).strftime('%Y-%m-%d')
        sb.table("broker_flows").delete().lt("log_date", cutoff).execute()
        print(f"[券商分點] 已清理 {cutoff} 之前的舊資料（只保留最近{keep_days}天）")
    except Exception as e:
        print(f"[券商分點] 清理舊資料失敗：{e}")


def stage_broker_flows(sb):
    """
    【R72新增，R74改為全市場】券商分點自動化——多輪查證後，在HiStock
    (histock.tw)找到一個真正乾淨的免費路徑：
    https://histock.tw/stock/branch.aspx?no={代號}，傳統ASP.NET伺服器端
    渲染，不用登入、不用JS、沒有反爬蟲防護，用plain requests+
    pandas.read_html就能正常讀取（已實測驗證過表格結構）。這不是繞過任何
    安全機制——單純是這個公開頁面本身沒有設反自動化的防護。

    【R73曾經的折衷方案，R74廢棄】R73原本只抓「持倉/雷達+最近60天加入過
    雷達」的股票，理由是不想對這個免費資源太貪心。後來評估過全市場的
    實際成本：1076檔全抓約25-35分鐘（GitHub Actions額度完全夠用）、
    資料量搭配31天保留期估算約150-240MB（Supabase免費額度內），總指揮官
    決定直接全市場天天抓——這樣任何股票不管什麼時候開始關注，資料本來
    就已經在，徹底解決「新增股票沒有歷史」的空窗期問題，比追蹤池折衷
    方案更乾淨。

    每個交易日收盤後執行一次，對全市場(get_scan_pool回傳的完整上市清單，
    跟stage_signal選股用的是同一份資料源，不用另外多打API)逐一抓取當日
    分點資料，寫進跟網頁版CSV上傳共用的broker_flows表。執行完順便呼叫
    _cleanup_old_broker_flows清掉超過31天的舊資料。

    網頁版原本的CSV人工上傳保留當備援：HiStock哪天改版失效時還有退路。
    """
    run_date = datetime.now().strftime("%Y-%m-%d")
    symbols, _raw_count = get_scan_pool(sb)
    if not symbols:
        print("[券商分點] 掃描池是空的（inst_holding可能還沒有資料），跳過本次抓取。")
        return

    _ok, _fail = 0, 0
    # 【R95續10新增】早期斷路器——總指揮官抓到一個矛盾現象：網頁版(Streamlit
    # Cloud)health check在不同時間點兩次都測HiStock正常，但同一天GitHub
    # Actions排程卻回報1078檔「全部」失敗。兩邊用的是同一支函式
    # (fetch_histock_branch_data)、同一個15秒逾時，程式邏輯本身沒有分岔，
    # 比較支持「這次GH Actions這組雲端IP/這個時段連不上HiStock」，而不是
    # HiStock本身全面掛掉（如果是後者，網頁版當時應該也測不到才對，但
    # 網頁版兩次分別在不同時間點都成功）。
    #
    # 這無法從程式碼層面解決連線問題本身（IP會不會被擋不是我們能控制的），
    # 但原本的寫法即使一開始就注定連不上，還是會傻傻地把1078檔全部跑完
    # （逐檔time.sleep(1)，等於白白燒掉近18分鐘GitHub Actions額度、寫入
    # 1078筆毫無意義的失敗log），且要等整批跑完才推播，總指揮官要隔天才
    # 看得到。改成：連續失敗達到門檻(8檔)就提早中止，直接notify_telegram
    # 明確標示「疑似這次GH Actions連不上HiStock（不是逐檔真的沒資料），
    # 網頁版health check若同時測試正常，可佐證是連線層級而非資料源本身
    # 問題」，總指揮官能更快知道、也不用等18分鐘。連續失敗門檻只在
    # 「一開始」就連續失敗時觸發（用_consecutive_fail計數，中途偶爾夾雜
    # 成功會重置），避免誤判「單純這批股票裡剛好有幾檔沒交易」成連線問題。
    _consecutive_fail = 0
    _EARLY_ABORT_THRESHOLD = 8
    _aborted_early = False
    for _idx, code in enumerate(symbols):
        df = fetch_histock_branch_data(code)
        if df is None or df.empty:
            _fail += 1
            _consecutive_fail += 1
            if _consecutive_fail >= _EARLY_ABORT_THRESHOLD:
                _aborted_early = True
                print(f"[券商分點] 連續 {_consecutive_fail} 檔失敗（已測試 {_idx + 1}/{len(symbols)} 檔），"
                      f"研判本次GitHub Actions這組IP/這個時段連不上HiStock，提早中止，"
                      f"不繼續浪費剩餘{len(symbols) - _idx - 1}檔的執行時間。")
                notify_telegram(
                    f"⚠️ [{run_date}] 券商分點排程：連續{_consecutive_fail}檔失敗後提早中止"
                    f"（已測{_idx + 1}/{len(symbols)}檔）。研判是這次GitHub Actions連不上"
                    f"HiStock（不是逐檔真的沒資料）——若此時網頁版「立即檢查所有資料源」測試"
                    f"HiStock券商分點正常，可佐證是GitHub Actions這組IP/這個時段的連線問題，"
                    f"不是HiStock網站本身掛掉。建議觀察是否持續發生。")
                break
            continue
        _consecutive_fail = 0
        try:
            # 只存前15買超+前15賣超（HiStock頁面本身就是抓前15大，全存即可）
            rows = [{
                'symbol': code, 'log_date': run_date,
                'broker_name': str(r['broker_name']),
                'buy_shares': int(r['buy_shares']), 'sell_shares': int(r['sell_shares']),
                'net_shares': int(r['net_shares']),
            } for _, r in df.iterrows()]
            sb.table("broker_flows").upsert(
                rows, on_conflict="symbol,log_date,broker_name").execute()
            _ok += 1
        except Exception as e:
            print(f"[券商分點] {code} 寫入失敗：{e}")
            _fail += 1
        time.sleep(1)  # 對這個免費資源客氣一點，不要連續轟炸

    _tested_count = _idx + 1 if _aborted_early else len(symbols)
    print(f"[券商分點] 完成：{_ok} 檔成功、{_fail} 檔失敗（共{len(symbols)}檔全市場，"
          f"實際測試{_tested_count}檔{'，提早中止' if _aborted_early else ''}）")
    _cleanup_old_broker_flows(sb, keep_days=31)
    try:
        sb.table("system_run_log").insert({
            "run_date": run_date, "stage": "broker_flows", "picked_count": len(symbols),
            "executed_count": _ok, "gate_status": "normal" if _fail == 0 else "error",
            "note": f"HiStock自動抓取(全市場)：{_ok}成功/{_fail}失敗"
                    + ("（提早中止，疑似連線問題）" if _aborted_early else ""),
        }).execute()
    except Exception as e:
        print(f"[券商分點] 寫入log失敗：{e}")
    # 【R95續10】提早中止時，上面的斷路器已經推播過明確的診斷訊息，這裡不用
    # 再重複推播一次「全市場失敗率過高」——那則訊息是為了「跑完全部才發現
    # 失敗率高」設計的，跟提早中止的情境重複，兩則一起推播只會讓總指揮官
    # 收到兩則語意重疊的警示，反而分不清楚哪一則才是最新狀況。
    if not _aborted_early and _fail > len(symbols) * 0.3:
        # 全市場規模下，失敗門檻改成30%——單一兩檔查無資料是常態(新股/
        # 當天沒交易)，但全市場失敗率一高，通常代表HiStock本身有問題
        notify_telegram(f"⚠️ [{run_date}] 券商分點排程：{len(symbols)}檔裡有{_fail}檔失敗，"
                        f"可能是HiStock網站異常或改版，需要人工檢查。")


def stage_disposal_watch(sb):
    """
    【R79新增】處置股/注意股預警 + 自結財報/重大訊息掃描——兩個都已驗證過
    的官方端點，每個交易日執行一次，比對「值得盯」的股票清單(重用R73那個
    _get_tracked_symbols_for_broker的邏輯範圍：系統模擬倉+常態持倉/雷達+
    最近60天加入過雷達的)，有命中就推播Telegram。

    處置股風險意義重大——流動性驟降，對已持倉部位是實質風險，這是舊有
    calc_disposal_risk_proxy()簡化版代理指標一直沒有的「真正對照官方公告」
    這一塊，現在補上。
    """
    run_date = datetime.now().strftime("%Y-%m-%d")

    # 重用R73已經寫好的追蹤清單邏輯（系統模擬倉+常態持倉/雷達+最近60天）
    symbols = set()
    try:
        rows = (sb.table("system_portfolio").select("symbol")
                .in_("status", ["holding", "pending"]).execute().data or [])
        symbols.update(_clean_symbol(r.get("symbol")) for r in rows if r.get("symbol"))
    except Exception as e:
        print(f"[處置/注意股] 讀取system_portfolio失敗：{e}")
    try:
        res = sb.table("user_state").select("state_value").eq("state_key", "commander_main").limit(1).execute()
        if res.data:
            state = res.data[0].get("state_value", {}) or {}
            symbols.update(_clean_symbol(k) for k in (state.get("portfolio") or {}).keys())
            symbols.update(_clean_symbol(k) for k in (state.get("pinned_stocks") or {}).keys())
    except Exception as e:
        print(f"[處置/注意股] 讀取user_state失敗：{e}")
    try:
        _cutoff = (datetime.now() - timedelta(days=60)).strftime('%Y-%m-%d')
        rows2 = (sb.table("watchlist_entry_log").select("symbol,entry_date")
                .gte("entry_date", _cutoff).execute().data or [])
        symbols.update(_clean_symbol(r.get("symbol")) for r in rows2 if r.get("symbol"))
    except Exception as e:
        print(f"[處置/注意股] 讀取watchlist_entry_log失敗：{e}")

    if not symbols:
        print("[處置/注意股] 目前沒有任何追蹤股票，跳過本次掃描。")
        return

    # 抓三份官方清單（一次抓，逐檔比對，不用每檔各打一次API）
    attention_list = fetch_twse_attention_stocks()
    disposal_twse = fetch_twse_disposal_stocks()
    disposal_tpex = fetch_tpex_disposal_stocks()

    _alerts = []
    for code in symbols:
        status = check_disposal_attention_status(code, attention_list, disposal_twse, disposal_tpex)
        if status['attention'] or status['disposal']:
            _alerts.append(f"{code}：{status['detail']}")

    if _alerts:
        notify_telegram(f"🚨 [{run_date}] 處置股/注意股警示（{len(_alerts)}檔）：\n"
                        + "\n".join(_alerts))
        print(f"[處置/注意股] 發現 {len(_alerts)} 檔警示，已推播")
    else:
        print(f"[處置/注意股] 掃描 {len(symbols)} 檔，無警示")

    # 【R79新增】自結財報/重大訊息掃描——同一個排程順便做，不用另外開一個
    # 排程時段。只挑「自結」相關的重大訊息，避免每天推播一堆改名/法說會
    # 之類的噪音。
    announcements = fetch_twse_material_announcements()
    if announcements:
        _self_compiled = filter_self_compiled_announcements(announcements, tracked_symbols=symbols)
        if _self_compiled:
            _msgs = [f"{a.get('公司代號','')} {a.get('公司名稱','')}：{a.get('主旨','')}"
                    for a in _self_compiled]
            notify_telegram(f"📋 [{run_date}] 自結財報公告（{len(_msgs)}則）：\n" + "\n".join(_msgs))
            print(f"[重大訊息] 發現 {len(_msgs)} 則自結財報公告，已推播")
        else:
            print("[重大訊息] 今日無你追蹤股票的自結財報公告")
    else:
        print("[重大訊息] TWSE重大訊息端點連線失敗，本次跳過")

    try:
        sb.table("system_run_log").insert({
            "run_date": run_date, "stage": "disposal_watch", "picked_count": len(symbols),
            "executed_count": len(_alerts), "gate_status": "normal",
            "note": f"處置/注意警示{len(_alerts)}檔",
        }).execute()
    except Exception as e:
        print(f"[處置/注意股] 寫入log失敗：{e}")


def stage_threshold_calibration(sb):
    """
    【R87新增】命中率自動化驗證——門檻敏感度掃描。

    範圍聲明(誠實標註)：這不是把「查1~查12完整濾網回測」整套自動化，那套
    邏輯深度依賴warroom_v160.py其他函式，要整套搬進共用模組是一次大重構，
    這裡先聚焦在總指揮官具體點名的「爆量比門檻」跟「六日累計漲跌門檻」
    這兩個獨立驗證，完整12濾網自動化列為之後的延伸項目。

    每月第一個週日執行一次(不用太頻繁，市場結構不會一個月內劇烈改變)，
    對系統模擬倉+常態持倉/雷達的股票池，跑兩組門檻敏感度掃描，結果存進
    Supabase，網頁版有對應面板可以看敏感度曲線，決定要不要調整程式碼裡
    寫死的門檻——排程只負責產生數據，不自動修改任何程式碼裡的門檻常數，
    這個決定必須由人親自看過數據後做，不能讓系統自己改自己的判斷邏輯。
    """
    run_date = datetime.now().strftime("%Y-%m-%d")
    symbols = set()
    try:
        rows = (sb.table("system_portfolio").select("symbol")
                .in_("status", ["holding", "pending"]).execute().data or [])
        symbols.update(_clean_symbol(r.get("symbol")) for r in rows if r.get("symbol"))
    except Exception as e:
        print(f"[門檻校準] 讀取system_portfolio失敗：{e}")
    try:
        res = sb.table("user_state").select("state_value").eq("state_key", "commander_main").limit(1).execute()
        if res.data:
            state = res.data[0].get("state_value", {}) or {}
            symbols.update(_clean_symbol(k) for k in (state.get("portfolio") or {}).keys())
            symbols.update(_clean_symbol(k) for k in (state.get("pinned_stocks") or {}).keys())
    except Exception as e:
        print(f"[門檻校準] 讀取user_state失敗：{e}")
    if not symbols:
        print("[門檻校準] 目前沒有任何追蹤股票，跳過本次掃描。")
        return
    symbols = sorted(symbols)[:60]  # 限制規模，避免單次執行時間過長

    print(f"[門檻校準] 對 {len(symbols)} 檔股票跑爆量比敏感度掃描...")
    vol_result = scan_volume_ratio_sensitivity(symbols)
    print(f"[門檻校準] 對 {len(symbols)} 檔股票跑六日累計漲跌敏感度掃描...")
    gain_result = scan_six_day_gain_sensitivity(symbols)

    rows_to_save = []
    for threshold, stats in vol_result.items():
        rows_to_save.append({
            "run_date": run_date, "threshold_type": "vol_ratio", "threshold_value": threshold,
            "sample_count": stats['sample'], "win_rate": stats['win_rate'], "avg_return": stats['avg_ret'],
        })
    for threshold, stats in gain_result.items():
        rows_to_save.append({
            "run_date": run_date, "threshold_type": "six_day_gain", "threshold_value": threshold,
            "sample_count": stats['sample'], "win_rate": stats['win_rate'], "avg_return": stats['avg_ret'],
        })
    try:
        sb.table("threshold_calibration_results").insert(rows_to_save).execute()
        print(f"[門檻校準] 已存入 {len(rows_to_save)} 筆敏感度數據")
        notify_telegram(f"🎯 [{run_date}] 門檻敏感度掃描完成，結果已存入系統，"
                        f"去網頁版「🎯門檻校準結果」面板查看敏感度曲線、決定要不要調整程式碼裡的門檻。")
    except Exception as e:
        print(f"[門檻校準] 寫入失敗：{e}")
        notify_telegram(f"⚠️ [{run_date}] 門檻敏感度掃描結果寫入失敗：{e}"
                        f"（可能是尚未執行supabase_migration_r87_threshold_calibration.sql建表）")


def stage_filter_backtest(sb):
    """
    【R95續新增】查1~14 + 情報雷達 每週自動回測校準——這是R87
    stage_threshold_calibration docstring裡明講的「之後的延伸項目」，
    當時卡在_filter_backtest_one_stock/run_filter_backtest深度依賴
    warroom_v160.py的其他函式，這輪查1~14重構把整套邏輯搬進warroom_core.py
    之後，技術障礙已經排除，才真正做得到。

    【總指揮官確認過的設計】
    - 每週一次（不需要跟門檻校準一樣拉長到每月，但也不用像盤中排程那樣
      每天跑——濾網的統計特性不會一週內劇烈改變，跑太頻繁只是浪費API額度）。
    - 股票池：系統模擬倉 + 常態持倉/雷達清單，跟stage_threshold_calibration
      同一套抓法，不另外發明一套。
    - 回測窗固定「近2年」——因為years參數是每次執行時才用datetime.now()
      往回算，同一個years=2每週跑一次，效果就是每週自動往前滾動2年，
      不需要額外的「上次跑到哪」狀態，天生就是滾動視窗。
    - 情報雷達類條件納入，來源自動從intel_performance現有紀錄裡抓，
      不用像網頁版UI一樣讓使用者手動選——排程沒有使用者互動，全部來源
      跟黃金交叉都測。
    - 只寫資料庫，不自動修改任何門檻/濾網常數——跟門檻校準同一個原則，
      要不要調整查1~14的判斷邏輯，必須由人看過數據後自己決定。
    - Telegram推播：樣本數<10筆的濾網標成「樣本不足暫不判讀」，不列出
      看起來有意義、但統計上不可信的勝率數字（總指揮官確認的門檻）。
    """
    run_date = datetime.now().strftime("%Y-%m-%d")
    symbols = set()
    try:
        rows = (sb.table("system_portfolio").select("symbol")
                .in_("status", ["holding", "pending"]).execute().data or [])
        symbols.update(_clean_symbol(r.get("symbol")) for r in rows if r.get("symbol"))
    except Exception as e:
        print(f"[濾網回測校準] 讀取system_portfolio失敗：{e}")
    try:
        res = sb.table("user_state").select("state_value").eq("state_key", "commander_main").limit(1).execute()
        if res.data:
            state = res.data[0].get("state_value", {}) or {}
            symbols.update(_clean_symbol(k) for k in (state.get("portfolio") or {}).keys())
            symbols.update(_clean_symbol(k) for k in (state.get("pinned_stocks") or {}).keys())
    except Exception as e:
        print(f"[濾網回測校準] 讀取user_state失敗：{e}")
    symbols = sorted(symbols)[:60]   # 限制規模，避免單次執行時間過長，跟門檻校準同一個上限

    # 技術面查1~14（不含13以上的情報類，那組另外處理）——engine實際支援
    # 查1/2/3/4/5/6/8/9/10/12（查7未定義、查13+是情報類、查11是簡化版）。
    TECH_CMDS = [
        "查1.主升段突擊", "查2.魚頭慢伏支撐", "查3.價值分數優化股",
        "查4.投信作帳集團股", "查5.籌碼外資霸王色", "查6.營收雙增爆發突破",
        "查8.昨日強勢動能延續", "查9.均線糾結爆量突破", "查10.籌碼沉澱量縮潛伏",
        "查11.除權息尋寶雷達", "查12.K線型態尋寶型",
    ]
    K_PATTERNS = ["長紅", "紅三兵", "長黑", "黑三兵"]

    all_rows = []
    tech_sample_count = 0
    tech_probe_note = None
    if symbols:
        print(f"[濾網回測校準] 對 {len(symbols)} 檔股票跑查1~14技術面回測（近2年）...")
        try:
            fb_rows, _ = run_filter_backtest(
                symbols, 2, TECH_CMDS, K_PATTERNS, True,
                token="",           # 【R95】_finmind_get內部自行輪替憑證，這裡傳什麼都不影響行為
                dividend_db=None,   # 排程沒有網頁版的DIVIDEND_DB，查11樣本會變少但不會出錯
            )
            all_rows.extend(fb_rows)
            tech_sample_count = len(fb_rows)
        except Exception as e:
            print(f"[濾網回測校準] 技術面回測執行失敗：{e}")

        # 【R95續4新增】60檔股票跑近2年查1~14，統計上幾乎不可能一筆訊號都沒有
        # ——真的出現這種情況，比較可能是yfinance在這個執行環境(GitHub Actions
        # 的雲端IP)被Yahoo判定成機器人流量擋掉，不是「這週剛好沒訊號」。用
        # yfinance業界公認常見的這個限制，加一個輕量探測：直接試抓一檔一定
        # 有資料的股票(2330)近5天股價，藉此區分「yfinance整個連不通」跟
        # 「連得通、但這週真的沒訊號觸發」兩種情況，不用只靠沉默的0筆自己猜。
        if tech_sample_count == 0:
            try:
                import yfinance as yf
                _probe = yf.Ticker("2330.TW").history(period="5d", timeout=10)
                if _probe is None or _probe.empty:
                    tech_probe_note = ("技術面回測0筆樣本，且探測抓2330近5天股價也是空的——"
                                       "很可能是yfinance在這個執行環境被Yahoo擋掉(GitHub Actions"
                                       "雲端IP常見問題)，不是這週剛好沒訊號，建議去GitHub Actions "
                                       "log確認實際錯誤訊息。")
                else:
                    tech_probe_note = ("技術面回測0筆樣本，但探測抓2330近5天股價正常——"
                                       "yfinance本身連得通，這次真的是查1~14在這批股票近2年內"
                                       "都沒有觸發，不是連線問題。")
                    # 【R95續5新增】yfinance整體連得通，不代表這60檔「每一檔」都真的
                    # 抓得到堪用的2年價格資料——用同一套抓價邏輯單獨對這批symbols
                    # 跑一次，分解出「有幾檔真的有資料可用」，區分「這批股票池本身
                    # 大部分都抓不到資料」跟「資料都在、條件真的沒觸發」兩種情況，
                    # 不用停在單一檔的探測就下結論。
                    try:
                        _avail = probe_price_data_availability(symbols, years=2)
                        tech_probe_note += (f" 進一步分解：{len(symbols)}檔股票池中，"
                                            f"{_avail['usable']}檔有堪用的近2年價格資料、"
                                            f"{_avail['empty_or_short']}檔抓不到或資料不足"
                                            f"(未達40筆交易日)。")
                    except Exception as _ae:
                        print(f"[濾網回測校準] 股票池資料可用性分解探測失敗：{_ae}")
            except Exception as _pe:
                tech_probe_note = f"技術面回測0筆樣本，且探測抓2330股價也失敗（{_pe}）——很可能是yfinance連線問題。"
            print(f"[濾網回測校準] {tech_probe_note}")
    else:
        print("[濾網回測校準] 目前沒有任何追蹤股票，跳過技術面回測部分。")

    # 情報雷達——來源自動從現有紀錄抓，排程無使用者互動可選
    try:
        intel_rows = sb.table("intel_performance").select("*").execute().data or []
        intel_sources = sorted({r.get('source', '未知') for r in intel_rows if r.get('source')})
        if intel_rows:
            intel_cmds = [f"情報雷達：{s}" for s in intel_sources] + ["🏆 情報黃金交叉（多個情報來源同時指向）"]
            print(f"[濾網回測校準] 對 {len(intel_sources)} 個情報來源跑情報雷達/黃金交叉回測...")
            all_rows.extend(run_intel_radar_backtest(intel_rows, intel_cmds))
        else:
            print("[濾網回測校準] intel_performance目前沒有紀錄，跳過情報雷達部分。")
    except Exception as e:
        print(f"[濾網回測校準] 情報雷達回測執行失敗：{e}")

    if not all_rows:
        # 【R95續4】就算完全沒有樣本可以寫進資料庫，如果技術面的診斷探測
        # 判斷出「疑似yfinance被擋」，這個資訊本身就值得推播——總指揮官
        # 不用去GitHub Actions log才知道發生了連線層級的問題，不是安靜地
        # 什麼都沒發生。
        print("[濾網回測校準] 本次沒有產出任何有效樣本，不寫入資料庫。")
        if tech_probe_note:
            notify_telegram(f"⚠️ [{run_date}] 濾網回測校準本次完全沒有產出樣本。🔎 {tech_probe_note}")
        return

    summary = summarize_filter_backtest(all_rows)
    if summary.empty:
        print("[濾網回測校準] 彙總結果為空，不寫入資料庫、不推播。")
        return

    rows_to_save = [{
        "run_date": run_date, "filter_name": r["濾網條件"], "sample_count": int(r["樣本數"]),
        "win_rate_3d": r["3日勝率%"], "avg_return_3d": r["3日平均報酬%"], "avg_return_10d": r["10日平均報酬%"],
    } for _, r in summary.iterrows()]

    try:
        sb.table("filter_backtest_weekly_results").insert(rows_to_save).execute()
        print(f"[濾網回測校準] 已存入 {len(rows_to_save)} 筆濾網回測結果")
    except Exception as e:
        print(f"[濾網回測校準] 寫入失敗：{e}")
        notify_telegram(f"⚠️ [{run_date}] 濾網回測校準結果寫入失敗：{e}"
                        f"（可能是尚未執行supabase_migration_r95_filter_backtest.sql建表）")
        return

    # Telegram摘要：樣本數<10筆的一律標「樣本不足暫不判讀」，不列出看起來
    # 有意義、但統計上不可信的勝率數字（總指揮官確認的門檻，跟R44風報比
    # 面板的<10-sample gating同一個標準，全案一致不各自發明門檻）。
    MIN_SAMPLE = 10
    confident = [r for _, r in summary.iterrows() if r["樣本數"] >= MIN_SAMPLE]
    thin = [r for _, r in summary.iterrows() if r["樣本數"] < MIN_SAMPLE]
    confident_sorted = sorted(confident, key=lambda r: r["3日勝率%"] if r["3日勝率%"] is not None else -1, reverse=True)

    msg_lines = [f"📊 [{run_date}] 濾網回測校準完成（近2年滾動窗，{len(symbols)}檔股票+{len(all_rows)}筆訊號樣本）"]
    if tech_probe_note:
        msg_lines.append(f"🔎 {tech_probe_note}")
    if confident_sorted:
        top3 = confident_sorted[:3]
        bot3 = confident_sorted[-3:] if len(confident_sorted) > 3 else []
        msg_lines.append("🏆 本週表現最好：" + "、".join(
            f"{r['濾網條件']}({r['3日勝率%']:.0f}%/{r['樣本數']}筆)" for r in top3))
        if bot3:
            msg_lines.append("🔻 本週表現最差：" + "、".join(
                f"{r['濾網條件']}({r['3日勝率%']:.0f}%/{r['樣本數']}筆)" for r in bot3))
    if thin:
        msg_lines.append(f"⚠️ 樣本不足暫不判讀（<{MIN_SAMPLE}筆）：" + "、".join(
            f"{r['濾網條件']}({r['樣本數']}筆)" for r in thin))
    msg_lines.append("完整結果去網頁版查看，或直接查Supabase filter_backtest_weekly_results表。")
    notify_telegram("\n".join(msg_lines))


SCHEDULER_VERSION = "作戰室 排程 v1.0 (2026-08-06 R95續10：券商分點連續失敗提早中止斷路器)"


def stage_big_holder(sb):
    """
    【R70新增】千張大戶自動化——這是這輪最重要的更正。

    R69當時查證TDCC的opendata端點時，測試的是smart.tdcc.com.tw這個子網域，
    被robots.txt擋下來，因此判定只能走CSV人工上傳。後來重新查證才發現：
    官方文件跟社群實際長期使用的網址其實是opendata.tdcc.com.tw（不是
    smart.tdcc.com.tw，是不同子網域），這個網域根本沒有robots.txt檔案，
    而且有真實的VBA/Excel自動化案例長期穩定使用同一個URL。R69的CSV上傳
    結論是建立在測錯網域的前提上——這裡更正：千張大戶現在由排程自動抓取，
    每週六早上TDCC更新資料後執行一次，不用再靠總指揮官手動下載上傳CSV。

    网頁版的CSV上傳UI(sb_log_big_holder_weekly那個入口)繼續保留當備援——
    如果哪天TDCC官方網址又改版把這個路徑也擋掉了，還有手動路徑可以撐著，
    不會整個功能斷炊。
    """
    run_date = datetime.now().strftime("%Y-%m-%d")
    raw = fetch_tdcc_holding_csv_direct()
    if raw is None:
        notify_telegram(f"⚠️ [{run_date}] 千張大戶排程：TDCC連線失敗，這週跳過，"
                        f"下週六會再試一次。（也可以去網頁版側欄手動上傳CSV補這一週）")
        try:
            sb.table("system_run_log").insert({
                "run_date": run_date, "stage": "big_holder", "picked_count": 0,
                "executed_count": 0, "gate_status": "error", "note": "TDCC連線失敗",
            }).execute()
        except Exception as e:
            print(f"[千張大戶] 寫入log失敗：{e}")
        return

    df = parse_tdcc_holding_csv(raw)
    if df is None or df.empty:
        notify_telegram(f"⚠️ [{run_date}] 千張大戶排程：抓到回應但解析失敗"
                        f"（可能是TDCC改版了CSV格式），需要人工檢查。")
        return

    ratios = compute_big_holder_ratios(df)
    if not ratios:
        notify_telegram(f"⚠️ [{run_date}] 千張大戶排程：解析成功但算不出任何股票的比例，需要人工檢查。")
        return
    # 【R90新增】散戶（十張以下）比例——同一份df本來就含全級距明細，不用
    # 多打任何API，順手算出第二個指標一起存。
    small_ratios = compute_small_holder_ratios(df)

    try:
        rows = [{'symbol': s, 'week_date': run_date, 'ratio_pct': r,
                'small_holder_pct': small_ratios.get(s)} for s, r in ratios.items()]
        # 全市場一次可能上千檔，分批寫入避免單次payload過大
        _batch = 500
        _written = 0
        for i in range(0, len(rows), _batch):
            sb.table("big_holder_weekly").upsert(
                rows[i:i + _batch], on_conflict="symbol,week_date").execute()
            _written += len(rows[i:i + _batch])
        print(f"[千張大戶] 成功寫入 {_written} 檔股票的當週比例")
        sb.table("system_run_log").insert({
            "run_date": run_date, "stage": "big_holder", "picked_count": _written,
            "executed_count": _written, "gate_status": "normal",
            "note": f"TDCC自動抓取成功，{_written}檔",
        }).execute()
        # 只在異常時推播，正常完成不用每週打擾——這是排程一貫的設計原則
    except Exception as e:
        notify_telegram(f"⚠️ [{run_date}] 千張大戶排程：資料算好了但寫入Supabase失敗：{e}")


# ------------------------------------------------------------------------------
def main():
    print(f"🏷️ {SCHEDULER_VERSION}")
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", required=True,
                        choices=["signal", "gate", "morning_exit", "tail_entry", "health",
                                "big_holder", "broker_flows", "disposal_watch", "threshold_calibration",
                                "filter_backtest"])
    args = parser.parse_args()
    sb = get_supabase()
    if args.stage == "signal":
        stage_signal(sb)
    elif args.stage == "gate":
        stage_gate(sb)
    elif args.stage == "morning_exit":
        stage_morning_exit(sb)
    elif args.stage == "tail_entry":
        stage_tail_entry(sb)
    elif args.stage == "health":
        stage_health(sb)
    elif args.stage == "big_holder":
        stage_big_holder(sb)
    elif args.stage == "broker_flows":
        stage_broker_flows(sb)
    elif args.stage == "disposal_watch":
        stage_disposal_watch(sb)
    elif args.stage == "threshold_calibration":
        stage_threshold_calibration(sb)
    elif args.stage == "filter_backtest":
        stage_filter_backtest(sb)


if __name__ == "__main__":
    main()
