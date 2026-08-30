#!/usr/bin/env python3
# ══════════════════════════════════════════════════════════════════════════
# 🛑🛑🛑 任何人／任何AI，動這個檔案的程式碼之前，先讀完這三條規則 🛑🛑🛑
# ══════════════════════════════════════════════════════════════════════════
#
# 【規則一】A方案不行就換B，B不行就換C——一定找得到能用的方法或解法。
#   遇到卡關（某個資料源被擋、某個欄位查無資料、某個API不支援、某條路走
#   不通），不要卡在原地重試同一招，換個角度／換個資料源／換個技術方案
#   繼續往前，不要因為一個方案失敗就放棄整個目標。
#   （R98續21實例：MOPS的ajax_t163sb04內部端點被referer-wall擋住，改用
#   TWSE官方OpenAPI(openapi.twse.com.tw)繞開，最終解決全市場財報掃描。）
#
# 【規則二】改任何程式碼都要記得會有連動——改A可能牽動B或C。
#   改完之後，務必主動檢查有沒有連動受影響的地方：呼叫端、平行組裝的
#   ctx/dict、資料庫欄位、UI顯示文字、其他呼叫這個函式/欄位的位置。
#   不是只確認「這裡改對了」就結束，要確認「連動的地方也都跟著對了」。
#
# 【規則三】只要程式碼有改動，一定要做以下完整檢查，不能只做其中一項：
#   1. ast.parse 語法檢查（三個核心檔案都要跑一次）
#   2. audit_scoring_wiring.py（determine_signal參數接線檢查）
#   3. python3 -c "import 模組名"——真正的匯入測試，不是只看語法。
#      語法正確不代表函式真的存在、沒有被意外刪除。
#      （R98續20血淋淋的教訓：一次str_replace編輯不小心把
#      fetch_financial_health()的def這一行刪掉了，函式的docstring/
#      本體都還在、語法完全合法，但函式名稱消失了——ast.parse跟
#      audit_scoring_wiring.py兩個檢查都沒抓到，這個bug被推上GitHub
#      main分支好幾個小時，直到真正執行import才發現。之後每次改完
#      都要用python3 -c "import dashangdao; import warroom_core;
#      import system_scheduler"這種方式實際測試三個模組都能完整載入。）
#   如果之後找到新的、更有效的檢查工具或檢查方式，也要一併加進這個
#   清單——這個清單會持續擴充，不是寫死不變的。
#
# ══════════════════════════════════════════════════════════════════════════
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
import concurrent.futures
from datetime import datetime, timedelta, time as dt_time, timezone
from zoneinfo import ZoneInfo

import requests
import pandas as pd  # 【R97新增】系統A評分需要的_derive_*_features()用pd.to_datetime/pd.notna

# 【R96修復，見開發歷程.md時區bug章節】GitHub Actions是UTC不是台灣時間，
# 需要具體時分時一律用datetime.now(TAIPEI_TZ)。
TAIPEI_TZ = ZoneInfo("Asia/Taipei")

try:
    from supabase import create_client
except ImportError:
    print("需要安裝 supabase 套件：pip install supabase")
    sys.exit(1)

# 【V160 Round39新增】共用核心模組——跟網頁版共同import，常數/ATR算法
# 只維護一份。warroom_core.py不import streamlit，GitHub Actions環境安全可用。
try:
    import warroom_core as _wc
    from warroom_core import (
        DEF_LINE_ATR_MULT, calculate_atr, build_trade_zones,
        set_finmind_tokens, get_fm_quota_status, _finmind_get, FinMindAPIError,
        fetch_tdcc_holding_csv_direct, parse_tdcc_holding_csv, compute_big_holder_ratios,
        compute_small_holder_ratios,
        fetch_branch_data_with_fallback,  # 【R96新增】FinMind優先、失敗才退回HiStock爬蟲
        fetch_twse_attention_stocks, fetch_twse_disposal_stocks, fetch_tpex_disposal_stocks,
        check_disposal_attention_status, fetch_twse_material_announcements,
        filter_self_compiled_announcements,
        scan_volume_ratio_sensitivity, scan_six_day_gain_sensitivity,
        # 【R95續新增】查1~14+情報雷達 每週自動回測校準
        run_filter_backtest, summarize_filter_backtest, run_intel_radar_backtest,
        probe_price_data_availability,
        # 【R95續28新增】自建5分K 第一階段資料收集
        fetch_twse_mis_batch, aggregate_intraday_snapshots_to_bars,
        # 【R95續29新增】自建5分K 回溯驗證
        validate_intraday_bars_vs_daily,
        # 【R96新增】自建5分K 第二階段：9:30三關（查15）判斷邏輯
        fetch_industry_map_raw, get_industry_leader_for_symbol,
        evaluate_930_three_gate,
        # 【R97新增，總指揮官確認：排程評分統一改用系統A】原本compute_signal_for
        # 是簡化版（只有技術面），跟網頁版determine_signal（技術+籌碼+基本面，
        # ±10分尺度）不是同一套標準——如果系統自動選股跟總指揮官手動判斷用不同
        # 評分基準，兩者勝率沒有辦法公平比較。這裡把網頁版的完整評分引擎、以及
        # 籌碼/基本面資料抓取函式一併接進排程，讓兩邊用同一把尺。
        determine_signal, run_additive_factors_detailed, fetch_institutional_history,
        fetch_revenue_history_lagged,
        # 【R97新增】候選池篩選(週轉率+系統A評分)+空方三關支援
        # （fetch_shares_outstanding/fetch_stock_price_and_value_history/safe_float/
        # evaluate_gate2_leader_deviation_short/evaluate_short_position_precheck
        # 只在core.py內部被compute_interval_turnover/evaluate_930_three_gate呼叫，
        # 排程端自己不用直接呼叫，不用重複import——R97稽核時順便清掉）
        fetch_market_turnover_ranking_with_value, compute_interval_turnover,
        # 【R97補做，評分邏輯稽核抓到的漏接】
        fetch_twii_regime_history, compute_landmine_flag,
        # 【R97新增】候選池最終候選標記當沖比過熱（只對Stage2篩出的最終
        # 候選加查，不是對Stage0b全部30檔，控制成本）
        fetch_day_trading_info, evaluate_day_trader_ratio,
        # 【R97新增】事件驅動系統：十大會影響股價事件的分類+否決/標記
        # （fetch_twse_material_announcements已經在上面import過，這裡
        # 只需要補classify_material_announcements）
        classify_material_announcements,
        # 【R97補做，這輪全面稽核抓到的真bug】趨勢資格硬閘門——文件裡明講
        # 是整套框架信心最高、最不可退讓的核心規則，但排程端compute_full_
        # signal_for完全沒有接上，見下面的修復。
        evaluate_trend_qualification_gate,
        # 【R97續2修復，總指揮官抓到根本原因】get_fm_real_quota_status之前
        # 失敗是因為函式本身沒帶正常瀏覽器身分(User-Agent)被FinMind端點
        # 擋掉，跟token/認證方式無關，已經修好、改回真實額度查詢，見下面
        # stage_build_intraday_pool的說明。
        get_fm_real_quota_status,
        check_api_key_usage_anomaly,
        # 【R97新增】NVIDIA AI推演共用核心，跟網頁版(warroom_v160.py)共用
        build_ai_strategy_prompt, call_ai_models_parallel, NIM_FALLBACK_MODELS,
        # 【R97續5新增，見對話紀錄「FinMind限流根因排查」】TWSE官方批次端點，
        # 取代選股迴圈逐檔打FinMind——一次選股從4000+次FinMind請求降到3次
        # TWSE官方請求，真實資料驗證過不會被限流。
        sync_twse_market_snapshot,
        # 【R97續10新增】四維度主力偵測，取材CMoney選股法
        detect_smart_money_patterns,
        # 【R97續10新增，總指揮官要求：分段計時+快取命中率診斷，不要用猜的】
        reset_snapshot_cache_counters, get_snapshot_cache_counters,
        # 【R97續14新增】股本快取批次backfill階段(stage_backfill_shares_
        # outstanding)要直接呼叫，跟原本「排程端不用直接呼叫」的註記不同——
        # 這支階段的存在目的就是主動把快取表補滿，其他stage仍維持不直接呼叫。
        fetch_shares_outstanding, SHARES_CACHE_TTL_DAYS, SHARES_ATTEMPT_BACKOFF_DAYS,
        # 【R98新增，總指揮官方案二拍板第5項】隔日沖動態統計，見
        # stage_overnight_flip_dealer_stats的完整說明。
        compute_overnight_flip_dealer_stats, classify_overnight_flip_dealer_tier,
        # 【R98新增】連續遞增突破因子需要的計算函式，見determine_signal
        # 新增的higher_high_low_streak參數。
        compute_higher_high_low_streak,
        # 【R98新增】過熱煞車+連續攻擊熄燈反轉，見determine_signal新增的
        # is_overheated/attack_reversal_triggered參數。
        detect_bollinger_overheat, detect_attack_streak_reversal,
        # 【R98新增】買賣家數差代理指標，接入評分(buyer_seller_concentration因子)。
        compute_buyer_seller_branch_diff_proxy,
        # 【R98續新增】Finnhub報價查詢，供stage_gate()的SOX/TSM漲跌幅
        # 判斷優先使用，不受Yahoo限流影響。
        fetch_finnhub_quote,
        # 【R98續2新增】TAIEX 20MA判斷，同樣供stage_gate()優先使用。
        fetch_taiex_ma20_bull_status,
        # 【R98新增，總指揮官方案二P1】財報體質排程化——原本按需查詢，
        # 見stage_financial_health_scan的完整說明。
        fetch_financial_health,
        compute_financial_risk_score,
        fetch_mops_financial_batch,
        fetch_mops_balance_sheet_batch,
        _mops_quarter_dates,
        fetch_shioaji_snapshot,
        fetch_live_quotes_resilient,
        is_twse_market_hours,
    )
except ImportError as _e:
    # 【R97續14修復，總指揮官實測抓到：這段訊息會誤導人】原本固定印
    # 「找不到warroom_core.py」，不管背後真正的ImportError是什麼都是
    # 同一句話——總指揮官這輪抓到file確實存在、但排程還是報這個錯，
    # 查了老半天才發現是這句話本身把真正原因吃掉了。改成把_e的內容
    # 直接印出來，下次再發生能直接看到真正卡在哪個名字/哪個套件，
    # 不用再靠人工在乾淨環境重現才找得到。
    print(f"匯入warroom_core.py內容失敗：{type(_e).__name__}: {_e}")
    print("（這代表warroom_core.py檔案存在，但裡面某個名字對不上、或它"
          "依賴的某個套件沒裝——不是檔案真的找不到。上面這行錯誤訊息"
          "會直接告訴你是哪個名字/套件。）")
    sys.exit(1)

# 【R60新增】版本相容性檢查——避免排程端踩到「warroom_core.py沒跟著換版」
# 這個已經真實發生過兩次的bug類型。
_REQUIRED_CORE_VERSION = 113
if getattr(_wc, "CORE_VERSION", 0) < _REQUIRED_CORE_VERSION:
    print(f"[版本不同步] 這份 system_scheduler.py 需要 warroom_core.py "
          f"CORE_VERSION >= {_REQUIRED_CORE_VERSION}，但目前是 "
          f"{getattr(_wc, 'CORE_VERSION', '未知（太舊）')}。請確認 repo 裡的 "
          f"warroom_core.py 也已經換成最新版，兩個檔案要一起更新。")
    sys.exit(1)

# 【R47】改用共用模組的FinMind多帳號輪替+illegal-token判斷，取代原本這支
# 排程腳本自己另一份獨立、無輪替的實作，順便修掉「只取token第一組」的bug。
set_finmind_tokens((os.environ.get("FINMIND_TOKEN") or "").split(","))
# 【R97新增，見開發歷程.md「NVIDIA AI推演接進排程」章節】排程端讀
# os.environ（GitHub Actions secrets），跟網頁版讀st.secrets來源不同，
# 但下游呼叫的是同一套warroom_core.py共用邏輯（build_ai_strategy_prompt/
# call_ai_models_parallel），只有「金鑰從哪裡讀」這件事各自處理。
NVIDIA_API_KEY = (os.environ.get("NVIDIA_API_KEY") or "").strip()


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
        # 【R95續6修復】原本parse_mode="HTML"，訊息文字裡剛好出現的<>&會被當
        # HTML語法解析導致推播失敗(HTTP 400)。改成純文字模式，徹底避免這類問題。
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


def get_backtest_symbol_pool(sb, limit=60):
    """
    【R97新增，總指揮官回報：filter_backtest手動測試log出現大量重複的
    "$5347.TW: No data found, symbol may be delisted"，追查後發現這是
    system_portfolio/user_state裡殘留的真正已下市/變更代號股票，不是R95續6
    修過的$字元前綴問題（那個是格式髒污，這個是資料本身過期）——兩者外觀
    很像，容易搞混，這裡用不同機制個別處理。

    這段邏輯原本在 stage_threshold_calibration 跟 stage_filter_backtest
    各自複製一份完全相同的代碼（讀system_portfolio + user_state.portfolio +
    pinned_stocks），這正是本檔案開頭module docstring警告過的「同一套邏輯
    分散維護」問題——這輪順便合併成一份共用函式，並加上下市代號過濾。

    做法：抓到候選代碼後，用FinMind TaiwanStockInfo（涵蓋上市/上櫃/興櫃
    全市場，不是只有上市)反查每個代碼是否還在「目前有效」的清單裡，過濾掉
    查無此代碼的（很可能已下市/被合併/代碼變更），並印出清單方便總指揮官
    去system_portfolio/pinned_stocks手動清掉——這裡刻意不自動刪除持倉/雷達
    清單資料，那是使用者自己的資料，排程沒有權限自己動手清，只負責回報。

    抓不到TaiwanStockInfo時（API異常）不過濾，避免誤殺全部候選（寧可讓
    backtest多跑幾檔查無資料的舊代碼，也不要因為驗證清單本身抓取失敗
    而不小心把整批正常股票也濾掉）。

    回傳 (valid_symbols, stale_symbols)，兩者都是排序過的list。
    """
    symbols = set()
    try:
        rows = (sb.table("system_portfolio").select("symbol")
                .in_("status", ["holding", "pending"]).execute().data or [])
        symbols.update(_clean_symbol(r.get("symbol")) for r in rows if r.get("symbol"))
    except Exception as e:
        print(f"[候選池] 讀取system_portfolio失敗：{e}")
    try:
        res = sb.table("user_state").select("state_value").eq("state_key", "commander_main").limit(1).execute()
        if res.data:
            state = res.data[0].get("state_value", {}) or {}
            symbols.update(_clean_symbol(k) for k in (state.get("portfolio") or {}).keys())
            symbols.update(_clean_symbol(k) for k in (state.get("pinned_stocks") or {}).keys())
    except Exception as e:
        print(f"[候選池] 讀取user_state失敗：{e}")

    symbols = sorted(s for s in symbols if s)

    stale = []
    try:
        _info_rows = fetch_taiwan_stock_info_raw()
        if _info_rows:
            _all_active_codes = {str(x.get("stock_id", "")).strip() for x in _info_rows
                                 if str(x.get("stock_id", "")).strip()}
            valid = [s for s in symbols if s in _all_active_codes]
            stale = [s for s in symbols if s not in _all_active_codes]
            symbols = valid
        else:
            print("[候選池] TaiwanStockInfo抓不到資料，本次跳過下市代號過濾（避免誤殺全部候選）。")
    except Exception as e:
        print(f"[候選池] 下市代號過濾失敗：{e}，本次跳過過濾。")

    if stale:
        print(f"[候選池] 偵測到 {len(stale)} 檔可能已下市/代碼變更，本次已排除不跑回測："
              f"{', '.join(stale)}（建議去Supabase system_portfolio或網頁版持倉/雷達清單"
              f"手動確認並清除，排程不會自動刪除你的持倉/雷達資料）。")

    return symbols[:limit], stale


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
    _last_err = None
    for suffix in (".TW", ".TWO"):
        try:
            tk = yf.Ticker(f"{symbol}{suffix}")
            hist = tk.history(period="3mo", timeout=8).dropna(subset=["Close"])
            if len(hist) >= 20:
                return hist
        except Exception as e:
            _last_err = e
            continue
    # 【R96新增，診斷用】.TW跟.TWO都試過還是失敗，才印一次總結——單一
    # 後綴失敗是正常的（例如上市股試.TWO本來就會失敗），不用每次都印，
    # 兩個都失敗才代表這檔股票真的抓不到，值得留下線索。
    print(f"[fetch_price_hist-診斷] {symbol} 試過.TW跟.TWO都抓不到（近期最後一次例外："
          f"{type(_last_err).__name__}: {_last_err}）")
    return None


def compute_signal_for(symbol):
    """
    【R97起停用，見開發歷程.md】原本是排程專用的簡化版評分（只有技術面，
    ±3分尺度），總指揮官確認排程評分要統一改用系統A（compute_full_signal_for，
    呼叫determine_signal，跟網頁版同一套引擎），stage_signal/stage_gate/
    stage_execute/stage_holding_check全部已經改call compute_full_signal_for，
    這個函式目前沒有任何production路徑在用。

    保留這個函式不刪除，是為了A/B對照用——見 stage_score_ab_compare()，
    拿同一批股票分別跑這個(系統B)跟compute_full_signal_for(系統A)，
    比較兩者判定是否有系統性差異，驗證完全面切換到系統A沒有意外之後，
    這個函式才考慮真的移除。

    精簡版訊號計算：算評分、防守線、停利點，只用技術面（均線/爆量/ATR）。

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
    # 【V160 Round39緊急修復】改用calculate_atr()時漏拿掉的high/low中間
    # 變數，下面「爆量下殺」判定還要用，原本會NameError崩潰，這裡補回來。
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


def _derive_institutional_features(inst_df):
    """
    【R97新增】從fetch_institutional_history()回傳的DataFrame（欄位：
    f_buy/t_buy/d_buy/margin_diff，依日期排序不保證）derive出
    determine_signal()需要的f_single/t_single/f_5d/f_10d/
    foreign_buy_streak3——語意對齊warroom_v160.py calculate_signals_worker
    裡對inst_df的處理（該處欄位命名foreign_buy/trust_buy，是網頁版另一條
    走本機SQLite快取的路徑算出來的，這裡改成直接對fetch_institutional_
    history的原始欄位做同一件事，數值意義相同，只是資料來源不同）。

    inst_df為None或空時，全部回傳None——determine_signal對None的處理是
    「這個因子沒有資料，不觸發」，不會報錯。
    """
    empty = {"f_single": None, "t_single": None, "f_5d": None, "f_10d": None,
             "foreign_buy_streak3": None}
    if inst_df is None or inst_df.empty:
        return empty
    df = inst_df.copy()
    df.index = pd.to_datetime(df.index, errors="coerce")
    df = df[df.index.notna()].sort_index(ascending=False)   # 新到舊
    if df.empty:
        return empty
    latest = df.iloc[0]
    f_single = float(latest.get("f_buy", 0.0) or 0.0)
    t_single = float(latest.get("t_buy", 0.0) or 0.0)
    df_5d, df_10d = df.head(5), df.head(10)
    f_5d = float(df_5d["f_buy"].sum()) if "f_buy" in df_5d else None
    f_10d = float(df_10d["f_buy"].sum()) if "f_buy" in df_10d else None
    df_3d = df.head(3)
    foreign_buy_streak3 = (bool((df_3d["f_buy"] > 0).all())
                            if "f_buy" in df_3d and len(df_3d) >= 3 else None)
    return {"f_single": f_single, "t_single": t_single, "f_5d": f_5d, "f_10d": f_10d,
            "foreign_buy_streak3": foreign_buy_streak3}


def _derive_revenue_features(rev_df):
    """
    【R97新增】從fetch_revenue_history_lagged()回傳的DataFrame
    （欄位：available_date, yoy, mom）取出「今天可用」的最新一期
    rev_yoy/rev_mom——原函式已經處理好揭露延遲，這裡只要取
    available_date <= 今天 的最後一筆即可，不用重算延遲邏輯。
    """
    if rev_df is None or rev_df.empty:
        return {"rev_yoy": None, "rev_mom": None}
    df = rev_df.copy()
    df["available_date"] = pd.to_datetime(df["available_date"], errors="coerce")
    today = pd.Timestamp(datetime.now(TAIPEI_TZ).date())
    usable = df[df["available_date"] <= today].sort_values("available_date")
    if usable.empty:
        return {"rev_yoy": None, "rev_mom": None}
    last = usable.iloc[-1]
    return {"rev_yoy": float(last["yoy"]) if pd.notna(last.get("yoy")) else None,
            "rev_mom": float(last["mom"]) if pd.notna(last.get("mom")) else None}


def compute_full_signal_for(symbol, fm_token="", sb=None):
    """
    【R97新增，總指揮官確認：排程評分統一改用系統A(determine_signal)】
    見開發歷程.md——原本的compute_signal_for是簡化版（只有技術面，±3分
    尺度），跟網頁版determine_signal（技術+籌碼+基本面，±10分尺度，
    classify_score()校準過2/6/-2/-6四個分級）不是同一套標準。如果排程
    自動選股跟總指揮官手動判斷用不同評分基準，之後比較「系統選的」vs
    「人工選的」勝率，基準就不公平。

    技術面部分（價格/均線/量比/OHCL/ATR/buffer_pct）沿用compute_signal_for
    已經驗證穩定的算法，只是改用determine_signal當評分引擎本體。

    籌碼/基本面：直接呼叫fetch_institutional_history/
    fetch_revenue_history_lagged即時抓（不是讀網頁版的本機SQLite快取
    get_inst_data_from_db——那份快取只存在於Streamlit容器本機，GitHub
    Actions排程是完全獨立的執行環境，讀不到，必須自己抓一份）。這兩個
    抓取各自獨立try/except，任一個失敗都用None優雅降級——determine_signal
    對None的處理是「這個因子沒有資料，不觸發」，不會報錯、不會硬猜，
    整體流程不會因為籌碼或基本面某一段抓取失敗就中斷。

    回傳的dict保留跟compute_signal_for相同的核心欄位(symbol/price/score/
    gain/def_line/take_profit/vol_ratio/ma5/ma10/ma20/ma60)，呼叫端不用
    改欄位存取方式，只有score的計算依據換了；另外多回傳signal_text/
    reasons供log/推播顯示判定文字跟理由。

    回傳 dict 或 None。
    """
    hist = fetch_price_hist(symbol)
    if hist is None:
        return None
    close = hist["Close"]
    ma5 = float(close.tail(5).mean())
    ma10 = float(close.tail(10).mean()) if len(close) >= 10 else ma5
    ma20 = float(close.tail(20).mean())
    ma60 = float(close.tail(60).mean()) if len(close) >= 60 else ma20
    prev = float(close.iloc[-2])
    high, low = hist["High"], hist["Low"]
    vol = hist["Volume"]

    # 【R98續32新增，總指揮官指示P0主線開始動工：compute_full_signal_for
    # 徹底升級成「TWSE MIS優先、歷史資料備援」】這正是交接文件記錄的P0
    # 根因：這支函式被stage_signal/stage_morning_exit/stage_tail_entry
    # 等5個排程呼叫，全部都在盤中執行，但cur/day_high/day_low/open_price
    # 原本全部來自hist(yfinance每日K棒)的最後一筆——yfinance的每日K棒盤中
    # 不會即時更新，代表這些排程盤中拿到的可能是舊資料，跟總指揮官反映的
    # 「出場價=進場價」異常模式(2026-08-19單日超過40檔同時觸發)是同一個
    # 根因家族。
    #
    # 【設計決策，刻意的取捨】只在is_twse_market_hours()判斷確實是盤中
    # 時，才嘗試用fetch_live_quotes_resilient()(TWSE MIS+重試+永豐金
    # 備援，跟網頁端R98續32抽出來的同一套共用邏輯)拿當下真正即時的
    # 現價/開高低；查詢失敗、或收盤後(此時歷史資料的最後一筆本來就已經
    # 是正確的收盤價，不需要多此一舉)，優雅退回原本的行為(歷史資料
    # 最後一筆)，不會比升級前更差，只會更好或不變。
    #
    # 【刻意不變動的部分】MA5/10/20/60、higher_high_low_streak(用完整
    # high/low歷史序列算的多日型態)全部維持用歷史資料計算，不混用即時
    # 價——這些定義上就是「過去N天」的計算，用盤中還在跳動的即時價去
    # 替換其中一天會讓計算失去一致性，不在這次升級範圍內。
    _price_source = 'historical_close'
    if is_twse_market_hours():
        try:
            _sj_key = os.environ.get("SHIOAJI_API_KEY", "").strip()
            _sj_secret = os.environ.get("SHIOAJI_SECRET_KEY", "").strip()
            _live_map, _live_diag = fetch_live_quotes_resilient(
                [(symbol, 'tse')], shioaji_api_key=_sj_key, shioaji_secret_key=_sj_secret)
            _live_quote = _live_map.get(symbol)
        except Exception as e:
            print(f"[compute_full_signal_for] {symbol} 即時報價查詢失敗，退回歷史資料"
                  f"最後一筆(不影響其餘計算流程)：{type(e).__name__}: {e}")
            _live_quote = None
    else:
        _live_quote = None

    if _live_quote is not None and _live_quote.get('price'):
        cur = float(_live_quote['price'])
        day_high = float(_live_quote['high']) if _live_quote.get('high') else float(high.iloc[-1])
        day_low = float(_live_quote['low']) if _live_quote.get('low') else float(low.iloc[-1])
        open_price = float(_live_quote['open']) if _live_quote.get('open') else float(hist["Open"].iloc[-1])
        _price_source = _live_quote.get('source') or 'twse_mis'
    else:
        cur = float(close.iloc[-1])
        day_high = float(high.iloc[-1])
        day_low = float(low.iloc[-1])
        open_price = float(hist["Open"].iloc[-1])

    gain = (cur - prev) / prev * 100 if prev else 0.0
    atr = calculate_atr(hist)
    if atr <= 0:
        atr = cur * 0.02
    vol_ratio = float(vol.iloc[-1] / vol.tail(20).mean()) if vol.tail(20).mean() > 0 else 1.0
    def_line = round(ma5 - DEF_LINE_ATR_MULT * atr, 2)
    take_profit = round(cur + atr, 2)
    is_open_high_close_low = (open_price > prev) and (cur < open_price)
    zones = build_trade_zones(cur, ma5, ma20, atr, hist)

    # 【R97補做，這輪全面稽核抓到的真bug，見開發歷程.md】爆量下殺強制
    # 偏空——舊版compute_signal_for(系統B)有這段判斷(vol_ratio>=2.0 且
    # 當日收黑且跌幅>1%且收在當日低點附近，強制score上限為-3)，
    # determine_signal()本身也有對應機制(apply_override_rules裡的
    # is_volume_dump參數)，但這個函式一開始沒有計算is_volume_dump傳進去
    # （預設False），導致這個機制被靜默停用——分數看起來正常，不會報錯，
    # 但爆量下殺這種典型主力出貨型態不會再被強制降到偏空，是這輪系統A/B
    # 切換時真正遺漏掉的部分，不是無害的技術債。跟網頁版
    # calculate_signals_worker用同一套判斷公式(day_low/day_high/
    # close_near_low)，只是vol_ratio門檻用系統B原本的2.0(網頁版是
    # get_threshold('vol_ratio_surge')可調參數，排程端沒有這個機制，
    # 先用同樣驗證過的2.0)。
    _day_range = day_high - day_low
    close_near_low = (_day_range > 0 and (cur - day_low) / _day_range <= 0.35)
    is_volume_dump = bool(vol_ratio >= 2.0 and cur < open_price and gain < -1.0 and close_near_low)

    # 【R97補做，這輪全面稽核抓到的真bug，見開發歷程.md】趨勢資格硬閘門
    # ——文件裡明講是整套框架信心最高、最不可退讓的核心規則（連續3天收在
    # 月線下方，無條件判定出場，不管其他因子分數多高），但這個函式一開始
    # 完全沒有計算/傳入trend_gate_triggered，跟is_volume_dump是同一類型
    # 的疏漏：機制在determine_signal裡就緒，只是排程端沒接上輸入。
    _trend_gate = evaluate_trend_qualification_gate(hist)
    trend_gate_triggered = bool(_trend_gate.get("triggered"))

    # 【R97補做，稽核抓到的漏接】大盤(加權指數TWII)多空位階——大盤破20MA
    # 時，偏多攻擊門檻從6提高到8（見apply_override_rules）。用yfinance
    # 抓TWII，不吃FinMind額度，而且fetch_twii_regime_history()自己有
    # process內快取，同一次執行對多檔股票重複呼叫不會重複打API。
    try:
        _twii_regime = fetch_twii_regime_history(years=1)
        if _twii_regime is not None and len(_twii_regime) > 0:
            market_bull = bool(_twii_regime.iloc[-1])
        else:
            market_bull = True   # 抓不到時維持修復前的行為，不誤判成空頭
    except Exception as e:
        print(f"[compute_full_signal_for] 抓大盤位階失敗，本次評分假設多頭市場："
              f"{type(e).__name__}: {e}")
        market_bull = True

    # 籌碼——各自獨立try/except，失敗就是None，不中斷整體流程
    # 【R97新增，總指揮官要求：不要用預測式額度查詢(已證實不可靠)，
    # 改用反應式——真的偵測到FinMindAPIError(reason='rate_limited')這個
    # 真實發生的事實，才是可靠的訊號，比事先猜測剩多少額度準確，也比
    # 靠「連續N檔空結果」這種間接跡象更快、更直接。】
    _finmind_rate_limited = False
    inst_feat = {"f_single": None, "t_single": None, "f_5d": None, "f_10d": None,
                 "foreign_buy_streak3": None}
    try:
        inst_df = fetch_institutional_history(symbol, years=0.2, token=fm_token, sb=sb)
        inst_feat = _derive_institutional_features(inst_df)
    except FinMindAPIError as e:
        if e.reason == "rate_limited":
            _finmind_rate_limited = True
        print(f"[compute_full_signal_for] {symbol} 籌碼資料抓取失敗，本次評分不含籌碼因子："
              f"{type(e).__name__}: {e}")
    except Exception as e:
        print(f"[compute_full_signal_for] {symbol} 籌碼資料抓取失敗，本次評分不含籌碼因子："
              f"{type(e).__name__}: {e}")

    # 基本面——同樣獨立try/except
    rev_feat = {"rev_yoy": None, "rev_mom": None}
    try:
        rev_df = fetch_revenue_history_lagged(symbol, years=1, token=fm_token, sb=sb)
        rev_feat = _derive_revenue_features(rev_df)
    except FinMindAPIError as e:
        if e.reason == "rate_limited":
            _finmind_rate_limited = True
        print(f"[compute_full_signal_for] {symbol} 營收資料抓取失敗，本次評分不含基本面因子："
              f"{type(e).__name__}: {e}")
    except Exception as e:
        print(f"[compute_full_signal_for] {symbol} 營收資料抓取失敗，本次評分不含基本面因子："
              f"{type(e).__name__}: {e}")

    # 【R97補做，稽核抓到的漏接】地雷警訊——需要估值百分位(fetch_pe_history，
    # 額外1次FinMind呼叫) + rev_yoy(已有) + f_5d(已有)。獨立try/except，
    # 失敗保守回傳False，不中斷整體評分流程。
    landmine = False
    try:
        landmine = compute_landmine_flag(symbol, cur, rev_feat["rev_yoy"],
                                         inst_feat["f_5d"], token=fm_token, sb=sb)
    except Exception as e:
        print(f"[compute_full_signal_for] {symbol} 地雷警訊計算失敗，本次評分不含此因子："
              f"{type(e).__name__}: {e}")

    # 【R98新增，總指揮官指示：買賣家數差代理指標接入評分】算出代理指標傳給
    # determine_signal。只在sb存在時查，查該股票broker_flows最新一天的分點
    # 資料算「買超家數−賣超家數」。查不到（大部分股票沒有分點資料、或查詢
    # 失敗）就傳None，buyer_seller_concentration因子會靜默跳過，不影響評分。
    # 效能考量：這裡多一次DB查詢，但compute_full_signal_for本來就是每檔會
    # 打多次Supabase的重量級函式（籌碼/營收/地雷都在查），多這一次影響有限；
    # 且只查最新一天、limit在DB端，不是全量掃描。
    _bs_diff_proxy = None
    if sb is not None:
        try:
            _bf_latest = (sb.table("broker_flows").select("log_date")
                          .eq("symbol", symbol).order("log_date", desc=True)
                          .limit(1).execute())
            if _bf_latest.data:
                _bf_date = _bf_latest.data[0]["log_date"]
                _bs_result = compute_buyer_seller_branch_diff_proxy(sb, symbol, _bf_date)
                _bs_diff_proxy = _bs_result.get("diff_proxy")
        except Exception as e:
            print(f"[compute_full_signal_for] {symbol} 買賣家數差代理計算失敗，本次評分不含此因子："
                  f"{type(e).__name__}: {e}")

    # 【R98續17新增，總指揮官方向C：價值面融合進短波段判斷】讀
    # financial_health_snapshot.risk_score傳給determine_signal。跟上面
    # _bs_diff_proxy同一套設計：純讀取排程(stage_financial_health_scan)
    # 已經算好存進DB的分數，不重新呼叫fetch_financial_health/compute_
    # financial_risk_score(那兩個都要打FinMind，這裡是選股排程，不該
    # 為了一個因子多打一次FinMind額度)。查不到(還沒被financial_health_
    # scan掃到這一季)就傳None，financial_risk因子靜默跳過，不影響評分。
    _financial_risk_score = None
    if sb is not None:
        try:
            _fh_res = (sb.table("financial_health_snapshot").select("risk_score")
                       .eq("symbol", symbol).limit(1).execute())
            if _fh_res.data and _fh_res.data[0].get("risk_score") is not None:
                _financial_risk_score = int(_fh_res.data[0]["risk_score"])
        except Exception as e:
            print(f"[compute_full_signal_for] {symbol} 財務風險分數查詢失敗，本次評分不含此因子："
                  f"{type(e).__name__}: {e}")

    signal_text, _color, score, reasons = determine_signal(
        # 【R97修復】foreign_buy是determine_signal的必要位置參數(不是R41新增
        # 的向下相容選填參數)，網頁版一律傳0.0(不是None)，這裡比照同樣
        # 慣例——即使已經修好core.py那邊的None防護，這裡仍保留這層防護，
        # 避免同一類問題以後在其他沒防護到的因子上重演。
        cur, ma5, ma20, inst_feat["f_single"] if inst_feat["f_single"] is not None else 0.0,
        vol_ratio, is_open_high_close_low,
        zones["buffer_pct"], gain=gain, ma60=ma60, is_volume_dump=is_volume_dump,
        trend_gate_triggered=trend_gate_triggered, market_bull=market_bull, landmine=landmine,
        # 【R97總指揮官決議，刻意寫死，不做成system_config可調設定】
        # 排程是全自動下單/賣出流程，跟網頁版讓人工決定要不要開啟末日熔斷
        # 的情境不同，這裡固定關閉，跟網頁版預設值一致。
        enable_doomsday=False,
        trust_buy=inst_feat["t_single"], foreign_buy_5d=inst_feat["f_5d"],
        foreign_buy_10d=inst_feat["f_10d"], rev_mom=rev_feat["rev_mom"],
        rev_yoy=rev_feat["rev_yoy"], foreign_buy_streak3=inst_feat["foreign_buy_streak3"],
        # 【R98新增】連續遞增突破——用hist['High']/hist['Low']算，跟本函式
        # 前面trend_gate用的是同一份hist，不多抓資料。
        higher_high_low_streak=compute_higher_high_low_streak(high, low),
        # 【R98新增】過熱煞車+連續攻擊熄燈反轉——同樣用hist，不多抓資料。
        is_overheated=bool(detect_bollinger_overheat(hist).get("is_overheated")),
        attack_reversal_triggered=bool(detect_attack_streak_reversal(hist).get("reversal_triggered")),
        # 【R98新增】買賣家數差代理指標，見上方_bs_diff_proxy計算。
        buyer_seller_diff_proxy=_bs_diff_proxy,
        # 【R98續17新增】財務風險分數，見上方_financial_risk_score計算。
        financial_risk_score=_financial_risk_score,
    )

    # 【R97續20新增，多因子權重可視化(深版)+回測工作台的共用地基】
    # 刻意不改determine_signal()的簽名/回傳值——那個函式有audit_scoring_
    # wiring.py強制規定的參數稽核機制，牽動所有呼叫端，風險/效益不划算。
    # 這裡另外組一份跟determine_signal()內部完全一致的ctx，直接呼叫明細版
    # 因子函式(run_additive_factors_detailed)算一次因子明細——這是同一組
    # 輸入的重複運算(純CPU運算，不是網路請求)，成本可忽略，換到的是zero
    # risk：不動determine_signal分毫，也不用重新走一次全呼叫端稽核。
    _factor_ctx = {"price": cur, "ma5": ma5, "ma20": ma20, "ma60": ma60,
                   "foreign_buy": inst_feat["f_single"] if inst_feat["f_single"] is not None else 0.0,
                   "trust_buy": inst_feat["t_single"],
                   "foreign_buy_5d": inst_feat["f_5d"], "foreign_buy_10d": inst_feat["f_10d"],
                   "foreign_buy_streak3": inst_feat["foreign_buy_streak3"],
                   "vol_ratio": vol_ratio, "is_ohcl": is_open_high_close_low,
                   "buffer_pct": zones["buffer_pct"], "landmine": landmine, "gain": gain,
                   "rev_mom": rev_feat["rev_mom"], "rev_yoy": rev_feat["rev_yoy"],
                   # 【R98新增】跟上面determine_signal()呼叫用同一份high/low算，
                   # 保持這份平行ctx跟真正評分用的ctx內容一致，避免深版權重
                   # 可視化畫面顯示的因子明細跟實際評分依據對不上。
                   "higher_high_low_streak": compute_higher_high_low_streak(high, low),
                   "buyer_seller_diff_proxy": _bs_diff_proxy,
                   # 【R98續17新增】同步financial_risk_score，理由同上一行——
                   # 保持這份平行ctx跟真正評分用的ctx內容一致。
                   "financial_risk_score": _financial_risk_score}
    _, _, factor_detail = run_additive_factors_detailed(_factor_ctx)

    return {"symbol": symbol, "price": cur, "score": score, "gain": round(gain, 2),
            "def_line": def_line, "take_profit": take_profit, "vol_ratio": round(vol_ratio, 2),
            "ma5": round(ma5, 2), "ma10": round(ma10, 2), "ma20": round(ma20, 2),
            "ma60": round(ma60, 2), "signal_text": signal_text, "reasons": reasons,
            "is_volume_dump": is_volume_dump, "trend_gate_triggered": trend_gate_triggered,
            "factor_detail": factor_detail,
            # 【R97新增，供NVIDIA AI推演的prompt使用，見開發歷程.md】排程端
            # 原本這些欄位算完就丟掉，AI推演需要用到，這裡一併回傳。
            # big_holder/pe/value_score排程端目前沒有抓這些資料，維持None，
            # build_ai_strategy_prompt對None欄位有妥善的預設文字，不會報錯。
            "code": symbol, "name": symbol, "landmine": landmine,
            "rev_yoy": rev_feat["rev_yoy"], "f_5d": inst_feat["f_5d"] or 0.0,
            "big_holder": None, "pe": None, "value_score": None, "macd_str": None, "f_vwap": None,
            # 【R97新增，反應式額度保護】真的偵測到FinMindAPIError(rate_limited)
            # 才是True，呼叫端(Stage2迴圈)看到這個就該立刻停止，不用再猜。
            "finmind_rate_limited": _finmind_rate_limited,
            # 【R98續32新增，P0升級】這次評分的cur/day_high/day_low/open_price
            # 是用即時報價還是歷史資料算的——'historical_close'/'twse_mis'/
            # 'shioaji'三種，供log/未來排查用，呼叫端不強制使用這個欄位。
            "price_source": _price_source}


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
    d = d or datetime.now(TAIPEI_TZ)
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
    run_date = datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d")
    checks = []

    def _probe(name, fn, ok_test, detail_fn):
        try:
            r = fn()
            ok = ok_test(r)
            checks.append((name, ok, detail_fn(r)))
        except Exception as e:
            checks.append((name, False, f"例外：{type(e).__name__}: {e}"))

    # 【V160 Round36/R47修復】原本探測全市場模式(付費限定)永遠回報0列誤報，
    # 改用2330單檔近10天，免費方案打得到的真實探測；改用共用_finmind_get()，
    # token失效時自動換組。
    def _inst():
        url = "https://api.finmindtrade.com/api/v4/data"
        _start = (datetime.now(TAIPEI_TZ) - timedelta(days=10)).strftime("%Y-%m-%d")
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


def stage_score_ab_compare(sb):
    """
    【R97新增，總指揮官要求：系統A/B對照驗證】不寫入system_portfolio、
    不影響任何實際交易/持倉——純診斷用途，全面依賴compute_full_signal_for
    (系統A)之前，先跑一次同一批股票在系統A/系統B下的判定差異，人工確認
    合理再放心用。

    做法：對現有scan pool（跟stage_signal同一套抓法，一致才有可比性）
    各自跑一次compute_signal_for(系統B)、compute_full_signal_for(系統A)，
    列出：①分數本身的差異分佈 ②判定方向（多/空/中性）不一致的個股
    （這種最需要人工看一下，因為代表兩套系統對同一檔股票的方向判斷不同，
    不只是分數高低差異）。結果印進log+存進system_run_log的note欄位，
    不推播Telegram（避免這種一次性診斷變成每天的推播雜訊）。

    建議手動觸發（workflow_dispatch指定stage=score_ab_compare）跑1-2次
    確認沒問題即可，不需要排進日常排程。
    """
    run_date = datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d")
    _info_rows = fetch_taiwan_stock_info_raw()
    listed_codes = fetch_listed_only_codes(_info_rows)
    pool, _raw_count = get_scan_pool(sb, listed_codes)
    if not pool:
        print("[A/B對照] 掃描池是空的，無法對照。")
        return
    pool = pool[:60]   # 跟其他診斷型階段同樣的規模上限，避免單次執行時間過長

    print(f"[A/B對照] 對 {len(pool)} 檔股票分別跑系統A/系統B評分...")
    rows = []
    direction_mismatch = []
    for sym in pool:
        sig_b = compute_signal_for(sym)
        sig_a = compute_full_signal_for(sym, sb=sb)
        if not sig_b or not sig_a:
            continue
        score_b, score_a = sig_b["score"], sig_a["score"]

        def _direction(s, pos_th, neg_th):
            if s >= pos_th:
                return "多"
            if s <= neg_th:
                return "空"
            return "中性"

        # 系統B自己的分級只有±3（分數本身就是原始加減分，沒有分級門檻），
        # 這裡用0當多空分界；系統A用±2（觀察偏多/轉弱謹慎）當多空分界，
        # 兩邊都用「較寬鬆」的門檻判方向，才是公平比較兩套系統「傾向」
        # 是否一致，不是比較「要不要進場」（進場門檻是另一件事，見
        # stage_signal裡的±6）。
        dir_b = _direction(score_b, 1, -1)
        dir_a = _direction(score_a, 2, -2)
        rows.append({"symbol": sym, "score_b": score_b, "score_a": score_a,
                     "dir_b": dir_b, "dir_a": dir_a})
        if dir_b != dir_a and dir_b != "中性" and dir_a != "中性":
            direction_mismatch.append(sym)

    if not rows:
        print("[A/B對照] 沒有任何一檔同時算出系統A/B分數，無法對照。")
        return

    avg_b = sum(r["score_b"] for r in rows) / len(rows)
    avg_a = sum(r["score_a"] for r in rows) / len(rows)
    detail_lines = "\n".join(
        f"  {r['symbol']}：系統B={r['score_b']}({r['dir_b']}) / 系統A={r['score_a']}({r['dir_a']})"
        for r in rows)
    summary = (f"共比對 {len(rows)} 檔，系統B平均分數={avg_b:.2f}，系統A平均分數={avg_a:.2f}，"
              f"方向判定不一致 {len(direction_mismatch)} 檔"
              + (f"（{', '.join(direction_mismatch)}）" if direction_mismatch else ""))
    print(f"[A/B對照] {summary}")
    print(detail_lines)

    try:
        sb.table("system_run_log").insert({
            "run_date": run_date, "stage": "score_ab_compare", "picked_count": len(rows),
            "executed_count": len(direction_mismatch), "gate_status": "normal",
            "note": summary,
        }).execute()
    except Exception as e:
        print(f"[A/B對照] 寫入system_run_log失敗：{e}")


def _log_stage_run(sb, stage, run_date, picked_count=0, executed_count=0,
                   gate_status="normal", note=""):
    """
    【R98續26新增，總指揮官反映smart_money_scan/route2_confirm_scan
    「查了好幾天都沒有任何執行紀錄」——查證後發現這兩支函式從一開始
    就沒有寫system_run_log這個習慣（不管成功、找到候選、還是0檔，
    通通只有print()跟notify_telegram()，完全沒有留下可查詢的紀錄），
    不是排程沒執行，是排程本來就沒有留下「有沒有執行過」這件事的
    證據，才會讓人誤以為壞掉了。這裡統一補一個輕量寫入函式，讓這兩支
    (以及未來其他新排程)不用重複寫一樣的try/except樣板，任何一次
    執行結束(不管有沒有找到東西)都留下一筆查得到的紀錄。
    """
    try:
        sb.table("system_run_log").insert({
            "run_date": run_date, "stage": stage,
            "picked_count": picked_count, "executed_count": executed_count,
            "gate_status": gate_status, "note": note[:500] if note else "",
        }).execute()
    except Exception as e:
        print(f"[{stage}] 寫入system_run_log失敗（不影響本次排程結果）：{e}")


def stage_route2_confirm_scan(sb):
    """
    【R97續11新增，路線2「最後一塊拼圖」的資料產生端，見對話紀錄「路線2
    雙重確認設計」】

    路線2設計（總指揮官確認的方向）：
      波段評分(昨晚已算好，market_signal_snapshot) ∩ 今日開盤確認
      (真的照劇本方向啟動) ∩ 週轉率≥2 → 寫進route2_watchlist，供追蹤
      面板/雷達使用。

    這裡刻意不重跑一次完整系統A評分當「當沖評分」——如果當沖評分用的是
    同一份昨晚才更新一次的官方資料(法人/融資/PE/營收)，跟波段評分算出來
    的數字會一模一樣，「兩邊都要≥6」這個條件會變成恆真句，沒有實質意義。
    真正該讓「當沖」有別於「波段」的，是多確認「今天早上開盤後，價格有
    沒有真的照昨晚訊號的方向啟動」——這裡用一次全市場批次即時報價查詢
    達成(mis.twse.com.tw，不是FinMind，1074檔分批約11次請求，跟既有
    補位掃描用同一支已驗證安全的端點，只是範圍從24檔擴大到1074檔)。

    建議排程時間：09:10（開盤後10分鐘，有基本報價可查，早於09:24三關
    輪詢，這裡跟三關輪詢是完全獨立的兩條路，互不影響）。
    """
    run_date = datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d")

    # 找昨晚(或最近一次)的波段評分快照，取通過±6門檻的
    try:
        _snap_res = (sb.table("market_signal_snapshot").select("trade_date")
                    .order("trade_date", desc=True).limit(1).execute())
        _night_dates = _snap_res.data or []
        if not _night_dates:
            print("[路線2] market_signal_snapshot沒有任何資料，本次跳過"
                  "（可能stage_signal還沒用新版跑過）。")
            _log_stage_run(sb, "route2_confirm_scan", run_date, gate_status="error",
                          note="market_signal_snapshot沒有任何資料，本次跳過")
            return
        night_date = _night_dates[0]["trade_date"]
        _rows = (sb.table("market_signal_snapshot").select("symbol,score")
                .eq("trade_date", night_date).execute().data) or []
    except Exception as e:
        print(f"[路線2] 讀取market_signal_snapshot失敗：{type(e).__name__}: {e}")
        _log_stage_run(sb, "route2_confirm_scan", run_date, gate_status="error",
                      note=f"讀取market_signal_snapshot失敗：{e}")
        return

    strong_longs = {r["symbol"]: r["score"] for r in _rows if r.get("score") is not None and r["score"] >= 6}
    strong_shorts = {r["symbol"]: r["score"] for r in _rows if r.get("score") is not None and r["score"] <= -6}
    all_strong = set(strong_longs) | set(strong_shorts)
    if not all_strong:
        print(f"[路線2] {night_date}波段評分裡沒有任何一檔達±6門檻，本次跳過。")
        _log_stage_run(sb, "route2_confirm_scan", run_date, gate_status="normal",
                      note=f"{night_date}波段評分裡沒有任何一檔達±6門檻")
        return
    print(f"[路線2] 波段評分({night_date})：{len(strong_longs)}檔多方強勢／"
          f"{len(strong_shorts)}檔空方強勢，開始今日開盤確認...")

    # 今日開盤確認：一次批次查全部即時報價（沿用既有fetch_twse_mis_batch，
    # 內部自動分chunk，跟補位掃描用同一支函式，只是範圍大很多）
    try:
        _pairs = [(sym, 'tse') for sym in all_strong]
        _quotes = fetch_twse_mis_batch(_pairs)
    except Exception as e:
        print(f"[路線2] 批次查詢今日報價失敗：{type(e).__name__}: {e}")
        _log_stage_run(sb, "route2_confirm_scan", run_date, executed_count=len(all_strong),
                      gate_status="error", note=f"批次查詢今日報價失敗：{e}")
        return

    candidates = []
    for sym in all_strong:
        q = _quotes.get(sym)
        if not q or q.get("change_pct") is None:
            continue
        today_gain = q["change_pct"]
        is_long_candidate = sym in strong_longs and today_gain > 0
        is_short_candidate = sym in strong_shorts and today_gain < 0
        if not (is_long_candidate or is_short_candidate):
            continue   # 波段強勢但今天開盤方向沒有照劇本走，不列入

        turnover_info = compute_interval_turnover(sym, days=10, sb=sb)
        turnover_pct = turnover_info.get("turnover_pct")
        if turnover_pct is None or turnover_pct < 2.0:
            continue   # 週轉率不足，資金活躍度不夠，不列入

        direction = "long" if is_long_candidate else "short"
        candidates.append({
            "trade_date": run_date, "symbol": sym, "direction": direction,
            "night_score": strong_longs.get(sym) or strong_shorts.get(sym),
            "night_score_date": night_date, "today_gain_pct": today_gain,
            "turnover_pct": turnover_pct,
            "note": f"波段{('多方' if direction=='long' else '空方')}評分" 
                   f"{strong_longs.get(sym) or strong_shorts.get(sym)}，今日開盤{today_gain:+.2f}%照劇本走，"
                   f"週轉率{turnover_pct}%",
        })

    # 【R97續19修復，深度複查抓到】原本0檔候選時直接return，跳過DELETE——
    # 如果同一天這個stage被觸發第二次以上（例如手動重跑），且這次候選數
    # 剛好變成0，第一次寫進去的舊資料會永久殘留，不會被清掉。改成不論
    # candidates是否為空，都先刪掉今天日期的舊資料，確保這次執行結果
    # 才是「今天」的唯一真相，不會有新舊資料混雜的情況。
    try:
        sb.table("route2_watchlist").delete().eq("trade_date", run_date).execute()
    except Exception as e:
        print(f"[路線2] 清除今日舊資料失敗：{type(e).__name__}: {e}")
        _log_stage_run(sb, "route2_confirm_scan", run_date, executed_count=len(all_strong),
                      gate_status="error", note=f"清除今日舊資料失敗：{e}")
        return

    if not candidates:
        print(f"[路線2] {len(all_strong)}檔波段強勢股，今天沒有任何一檔同時滿足"
              f"「開盤方向確認+週轉率≥2」，本次不寫入（今日舊資料已清除）。")
        _log_stage_run(sb, "route2_confirm_scan", run_date, executed_count=len(all_strong),
                      gate_status="normal",
                      note=f"{len(all_strong)}檔波段強勢股，今天沒有任何一檔同時滿足開盤方向確認+週轉率≥2")
        return

    try:
        sb.table("route2_watchlist").upsert(candidates, on_conflict="trade_date,symbol").execute()
    except Exception as e:
        print(f"[路線2] 寫入route2_watchlist失敗：{type(e).__name__}: {e}")
        _log_stage_run(sb, "route2_confirm_scan", run_date, executed_count=len(all_strong),
                      gate_status="error", note=f"寫入route2_watchlist失敗：{e}")
        return

    print(f"[路線2] {run_date}雙重確認完成，{len(all_strong)}檔波段強勢裡"
          f"有{len(candidates)}檔通過今日開盤確認+週轉率篩選。")
    _log_stage_run(sb, "route2_confirm_scan", run_date, picked_count=len(candidates),
                  executed_count=len(all_strong), gate_status="normal",
                  note=f"{len(all_strong)}檔波段強勢裡有{len(candidates)}檔通過雙重確認")
    lines = [f"🎯 [{run_date}] 路線2雙重確認清單（共{len(candidates)}檔）："]
    for c in candidates[:10]:
        arrow = "🔴多" if c["direction"] == "long" else "🔵空"
        # 【R98續2新增，總指揮官反映：Telegram通知只顯示代號沒有股名】
        # 股名不寫進route2_watchlist表(該表沒有這個欄位，不為了通知顯示
        # 就改資料庫schema)，改用_quotes(fetch_twse_mis_batch的原始結果，
        # 現在有name欄位)當場查，只用在這則訊息格式化。
        _name = _quotes.get(c["symbol"], {}).get("name", "")
        _label = f"{c['symbol']} {_name}" if _name else c["symbol"]
        lines.append(f"・{arrow} {_label}｜波段{c['night_score']}｜"
                     f"今日{c['today_gain_pct']:+.2f}%｜週轉{c['turnover_pct']}%")
    if len(candidates) > 10:
        lines.append(f"...其餘{len(candidates)-10}檔請至網頁版查看")
    notify_telegram("\n".join(lines))


def stage_smart_money_scan(sb):
    """
    【R97續10新增】四維度主力偵測，取材CMoney「週轉率高的熱門股/週轉率
    異常/週轉率高的反轉股」三篇選股法+總指揮官提出的週轉率逐步墊高。
    見warroom_core.py的detect_smart_money_patterns()完整說明。

    掃描範圍跟stage_signal同一個1074檔上市掃描池，全部從twse_market_
    snapshot累積歷史計算，不逐檔打FinMind——建議排在stage_signal(22:00)
    之後執行，這樣當天的官方批次快照已經同步完成，這裡才有資料可讀。

    符合任一維度的股票寫進smart_money_candidates表，供之後的追蹤面板
    （路線2功能）使用；同時推播Telegram摘要。
    """
    run_date = datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d")
    _info_rows = fetch_taiwan_stock_info_raw()
    listed_codes = fetch_listed_only_codes(_info_rows)
    pool, _raw_count = get_scan_pool(sb, listed_codes)
    if not pool:
        print("[主力偵測] 掃描池為空，本次不執行。")
        _log_stage_run(sb, "smart_money_scan", run_date, gate_status="error",
                      note="掃描池為空，本次不執行")
        return

    # 【R97續15新增，硬地板：處置/注意股一次抓，掃描時直接排除】
    # 這層不是策略選擇，是「這檔能不能實際交易」的門檻——處置股有分盤集合
    # 競價、預收款券等限制，不適合當沖/波段標的，不管籌碼多漂亮都先剔除。
    # 用已驗證的check_disposal_attention_status逐檔比對（清單只抓一次）。
    _att_list = _disp_twse = _disp_tpex = None
    try:
        _att_list = fetch_twse_attention_stocks()
        _disp_twse = fetch_twse_disposal_stocks()
        _disp_tpex = fetch_tpex_disposal_stocks()
        _has_disposal_data = any([_att_list, _disp_twse, _disp_tpex])
        print(f"[主力偵測] 處置/注意股清單已抓取（注意{len(_att_list or [])}／"
              f"上市處置{len(_disp_twse or [])}／上櫃處置{len(_disp_tpex or [])}）。")
    except Exception as e:
        _has_disposal_data = False
        print(f"[主力偵測] 抓處置/注意股清單失敗（本次不套用這層硬地板）：{type(e).__name__}: {e}")

    # 流動性硬地板門檻：今日成交金額 < 此值就剔除（避免抓到根本沒量、
    # 買不太到也賣不太掉的股票）。可用環境變數覆蓋，預設1億(新台幣元)。
    _min_value = float(os.environ.get("SMART_MONEY_MIN_TRADING_VALUE") or str(1_0000_0000))

    candidates = []
    _floor_liquidity = _floor_disposal = 0
    for sym in pool:
        try:
            # 處置/注意股硬地板（清單抓得到才套用，抓不到不因這層誤剔）
            if _has_disposal_data:
                _st = check_disposal_attention_status(sym, _att_list, _disp_twse, _disp_tpex)
                if _st.get("attention") or _st.get("disposal"):
                    _floor_disposal += 1
                    continue
            r = detect_smart_money_patterns(sb, sym, trade_date=run_date)
            if not r["patterns"]:
                continue
            # 流動性硬地板：成交金額算得出來且低於門檻→剔除（算不出來的
            # 不因為這層被剔，交給其他濾網，誠實不猜）
            _tv = r.get("trading_value")
            if _tv is not None and _tv < _min_value:
                _floor_liquidity += 1
                continue
            candidates.append(r)
        except Exception as e:
            print(f"[主力偵測] {sym} 判斷失敗：{type(e).__name__}: {e}")

    print(f"[主力偵測] 硬地板剔除：處置/注意{_floor_disposal}檔、流動性不足{_floor_liquidity}檔。")

    if not candidates:
        print(f"[主力偵測] {run_date} 掃描完成，{len(pool)}檔裡沒有任何一檔通過"
              f"四維度+硬地板。")
        _log_stage_run(sb, "smart_money_scan", run_date, executed_count=len(pool),
                      gate_status="normal",
                      note=f"{len(pool)}檔裡沒有任何一檔通過四維度+硬地板"
                           f"（處置/注意剔除{_floor_disposal}檔、流動性不足剔除{_floor_liquidity}檔）")
        return

    rows = [{
        "trade_date": run_date, "symbol": c["symbol"], "patterns": c["patterns"],
        "turnover_pct": c["turnover_pct"], "vol_ratio_5d": c["vol_ratio_5d"], "note": c["note"],
        # R97續15 enrich欄位
        "trading_value": c.get("trading_value"), "inst_net_5d": c.get("inst_net_5d"),
        "foreign_streak": c.get("foreign_streak"), "trust_streak": c.get("trust_streak"),
        "shares": c.get("shares"), "above_ma20": c.get("above_ma20"),
        "above_ma60": c.get("above_ma60"), "broke_20d_high": c.get("broke_20d_high"),
        "rev_yoy": c.get("rev_yoy"),
    } for c in candidates]
    try:
        sb.table("smart_money_candidates").delete().eq("trade_date", run_date).execute()
        sb.table("smart_money_candidates").upsert(rows, on_conflict="trade_date,symbol").execute()
    except Exception as e:
        print(f"[主力偵測] 寫入smart_money_candidates失敗：{type(e).__name__}: {e}")
        notify_telegram(f"⚠️ [{run_date}] 主力偵測掃描完成但寫入資料庫失敗：{e}")
        _log_stage_run(sb, "smart_money_scan", run_date, executed_count=len(pool),
                      gate_status="error", note=f"寫入smart_money_candidates失敗：{e}")
        return

    print(f"[主力偵測] {run_date} 掃描完成，{len(pool)}檔裡有{len(candidates)}檔符合。")
    _log_stage_run(sb, "smart_money_scan", run_date, picked_count=len(candidates),
                  executed_count=len(pool), gate_status="normal",
                  note=f"{len(pool)}檔裡有{len(candidates)}檔符合四維度+硬地板")

    # 依維度分類統計，推播摘要（只列前5檔避免訊息過長）
    by_pattern = {}
    for c in candidates:
        for p in c["patterns"]:
            by_pattern.setdefault(p, []).append(c["symbol"])
    lines = [f"🔍 [{run_date}] 主力偵測掃描完成，共{len(candidates)}檔符合："]
    # 【R98續2新增，總指揮官反映：Telegram通知只顯示代號沒有股名】
    # _info_rows開頭已經抓過，用既有的fetch_name_map()衍生對照表，
    # 不多打任何API。
    _name_map = fetch_name_map(_info_rows)
    for p, syms in by_pattern.items():
        _labeled = [f"{s} {_name_map.get(s, '')}".strip() for s in syms[:5]]
        preview = "、".join(_labeled) + ("..." if len(syms) > 5 else "")
        lines.append(f"・{p}（{len(syms)}檔）：{preview}")
    notify_telegram("\n".join(lines))


def stage_backfill_shares_outstanding(sb):
    """
    【R97續14新增，見對話紀錄「smart_money_scan全市場1078檔股本快取風暴」】
    stock_shares_outstanding快取表剛上線時，全市場1078檔幾乎都是空的，
    smart_money_scan/build_intraday_pool這種常態掃描一遇到還沒快取過的
    symbol就要重打FinMind，量一大就連續撞額度上限，拖慢執行時間，而且
    新加的「失敗退避」(SHARES_ATTEMPT_BACKOFF_DAYS)只是讓同一批symbol不
    會每天重打，並不會真的幫忙把快取補齊。

    這支獨立的批次補齊階段，用跟「補跑今日券商分點」同一套「斷點續傳＋
    每次限量」設計：只抓「還沒快取成功」的symbol，一次最多抓
    BACKFILL_BATCH_SIZE(可用環境變數BACKFILL_SHARES_BATCH_SIZE覆蓋，
    預設150檔)，抓完就停，不追求一次跑完全市場——手動多按幾次
    workflow_dispatch（或之後排一個離峰時段的cron）,幾天內就能把整個
    快取表補齊，之後smart_money_scan/build_intraday_pool命中率就會接近
    100%，不會再重演這次的rate_limited連環撞。

    呼叫fetch_shares_outstanding時帶ignore_backoff=True——這個階段的
    目的正是要「強制重試」那些被退避機制擋住的symbol，跟其他stage的
    「不要浪費額度重打已知失敗」邏輯剛好相反。
    """
    run_date = datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d")
    _info_rows = fetch_taiwan_stock_info_raw()
    listed_codes = fetch_listed_only_codes(_info_rows)
    pool, _raw_count = get_scan_pool(sb, listed_codes)
    if not pool:
        print("[股本backfill] 掃描池為空，本次不執行。")
        return

    # 找出已經有「有效快取」的symbol（shares非空且在TTL內），不重複打
    try:
        _cached_res = sb.table("stock_shares_outstanding").select("symbol,shares,updated_at").execute()
        _cached_rows = _cached_res.data or []
    except Exception as e:
        print(f"[股本backfill] 查詢既有快取失敗：{type(e).__name__}: {e}")
        _cached_rows = []

    _fresh_cached = set()
    for r in _cached_rows:
        if not r.get("shares"):
            continue
        try:
            _updated_dt = datetime.fromisoformat(r["updated_at"].replace("Z", "+00:00"))
            _age_days = (datetime.now(timezone.utc) - _updated_dt).days
        except (ValueError, TypeError, KeyError, AttributeError):
            _age_days = 9999
        if _age_days <= SHARES_CACHE_TTL_DAYS:
            _fresh_cached.add(r["symbol"])

    _need_backfill = [c for c in pool if c not in _fresh_cached]
    print(f"[股本backfill] 掃描池{len(pool)}檔，已有效快取{len(_fresh_cached & set(pool))}檔，"
          f"還缺{len(_need_backfill)}檔。")
    if not _need_backfill:
        print("[股本backfill] 全部都在有效快取內，本次不用補。")
        return

    _batch_size = int(os.environ.get("BACKFILL_SHARES_BATCH_SIZE") or "150")
    _targets = _need_backfill[:_batch_size]
    print(f"[股本backfill] 這次補{len(_targets)}檔（還剩{max(0, len(_need_backfill) - len(_targets))}檔"
          f"留給下次繼續補）。")

    ok_count, fail_count = 0, 0
    for i, sym in enumerate(_targets):
        shares = fetch_shares_outstanding(sym, sb=sb, ignore_backoff=True)
        if shares:
            ok_count += 1
        else:
            fail_count += 1
        if (i + 1) % 20 == 0:
            print(f"[股本backfill] 進度 {i + 1}/{len(_targets)}（成功{ok_count}／失敗{fail_count}）")

    _remaining_after = max(0, len(_need_backfill) - len(_targets))
    print(f"[股本backfill] {run_date} 本批完成：成功{ok_count}檔／失敗{fail_count}檔"
          f"（FinMind本身沒有資料或撞額度，已記錄嘗試時間，{SHARES_ATTEMPT_BACKOFF_DAYS}天內"
          f"其他stage不會重打）。全市場還缺{_remaining_after}檔，"
          + ("已全部補齊。" if _remaining_after == 0 else "請再次手動觸發此stage繼續補。"))
    notify_telegram(f"📦 [{run_date}] 股本快取backfill：本批{len(_targets)}檔（成功{ok_count}／"
                    f"失敗{fail_count}），全市場還缺{_remaining_after}檔"
                    + ("（已補齊）" if _remaining_after == 0 else "，之後可再手動觸發繼續補。"))


# 【R97續16新增，總指揮官要求：測試資料要能自動判斷清理，不要每次都手動填
# 日期】以trade_date為主鍵維度的表，幾乎都是用(trade_date,symbol)
# upsert寫入——只要「真正的排程」之後有跑過同一個trade_date，測試資料
# 會被自動覆蓋掉，不需要清。真正會變成永久殘留垃圾的，只有「這個
# trade_date永遠不會再有真正排程跑過」的情況，最常見、也是唯一能100%
# 安全自動判斷的案例，就是「trade_date落在週六/週日」——台股週末絕對
# 不開盤，任何一筆週末trade_date的資料，不管哪張表，都保證是測試/手動
# 誤觸留下的，不可能是真實排程寫入的，可以放心自動刪除，零誤刪風險。
#
# 平日（週一~五）的trade_date沒辦法這樣安全判斷——因為測試通常也是用
# 「今天」的日期跑，跟真正排程用的是同一把日期，兩者在資料庫裡長得
# 一模一樣，沒有額外標記的話，自動判斷「這筆是測試還是正式」等於用猜的，
# 猜錯砍到正式資料的風險不可接受。這裡對平日資料採用「只回報、不自動
# 刪」的保守做法——把每個平日trade_date的筆數列出來，跟該表近期的
# 正常筆數區間比對，明顯異常（例如遠低於正常值，像是測試中斷留下的
# 半批資料）的才特別標記，交給總指揮官人工確認要不要清，不會自作主張砍。
_CLEANUP_TARGET_TABLES = [
    "smart_money_candidates", "route2_watchlist", "twse_market_snapshot",
    "intraday_candidate_pool", "intraday_gate_results", "intraday_5min_bars",
]


def stage_cleanup_test_residue(sb):
    """
    自動清理測試殘留資料——見上方模組註解說明「為什麼週末可以自動刪、
    平日只能回報」的完整理由。可安全排進每天/每週固定跑一次，或隨時
    手動觸發。
    """
    run_date = datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d")
    _lookback_days = int(os.environ.get("CLEANUP_LOOKBACK_DAYS") or "10")
    _today = datetime.now(TAIPEI_TZ).date()
    _check_dates = [(_today - timedelta(days=d)) for d in range(_lookback_days)]
    _weekend_dates = [d.strftime("%Y-%m-%d") for d in _check_dates if d.weekday() >= 5]  # 5=六,6=日
    _weekday_dates = [d.strftime("%Y-%m-%d") for d in _check_dates if d.weekday() < 5]

    print(f"[自動清理] 檢查範圍：近{_lookback_days}天，其中週末{len(_weekend_dates)}天"
          f"（{_weekend_dates}）會自動刪除、平日{len(_weekday_dates)}天只回報。")

    _deleted_summary = []
    if _weekend_dates:
        for table in _CLEANUP_TARGET_TABLES:
            try:
                _sel_col = "id" if table not in ("twse_market_snapshot", "intraday_5min_bars") else "symbol"
                _res = sb.table(table).select(_sel_col).in_("trade_date", _weekend_dates).execute()
                _n = len(_res.data or [])
                if _n > 0:
                    sb.table(table).delete().in_("trade_date", _weekend_dates).execute()
                    _deleted_summary.append(f"{table}:{_n}筆")
                    print(f"[自動清理] {table} 刪除週末殘留 {_n} 筆（trade_date在{_weekend_dates}）。")
            except Exception as e:
                print(f"[自動清理] {table} 清理失敗（跳過，不影響其他表）：{type(e).__name__}: {e}")

    _flagged = []
    _flag_rows = []   # 【R97續17新增】寫進cleanup_flags表，供網頁版UI讀取+一鍵刪除
    if _weekday_dates:
        for table in _CLEANUP_TARGET_TABLES:
            try:
                _counts = {}
                for d in _weekday_dates:
                    _sel_col = "id" if table not in ("twse_market_snapshot", "intraday_5min_bars") else "symbol"
                    _r = sb.table(table).select(_sel_col).eq("trade_date", d).execute()
                    _counts[d] = len(_r.data or [])
                _nonzero = [v for v in _counts.values() if v > 0]
                if len(_nonzero) >= 3:
                    _median = sorted(_nonzero)[len(_nonzero) // 2]
                    for d, n in _counts.items():
                        # 明顯偏低（不到中位數的20%，且中位數本身不能太小否則雜訊太大）
                        if 0 < n < _median * 0.2 and _median >= 10:
                            _reason = f"筆數{n}遠低於近期中位數{_median}，可能是中斷的測試殘留"
                            _flagged.append(f"{table}/{d}：{n}筆（近期中位數{_median}筆，"
                                           f"明顯偏低，可能是中斷的測試殘留，建議人工確認）")
                            _flag_rows.append({"table_name": table, "trade_date": d,
                                              "row_count": n, "median_count": _median,
                                              "reason": _reason, "status": "pending"})
            except Exception as e:
                print(f"[自動清理] {table} 平日筆數檢查失敗：{type(e).__name__}: {e}")

    # 【R97續17新增】寫進cleanup_flags表——用upsert，同一組(table_name,
    # trade_date)重複被標記只更新一次，不會每天疊加出重複列。
    if _flag_rows:
        try:
            sb.table("cleanup_flags").upsert(_flag_rows, on_conflict="table_name,trade_date").execute()
        except Exception as e:
            print(f"[自動清理] 寫入cleanup_flags失敗（不影響Telegram通知，只是網頁版看不到清單）："
                  f"{type(e).__name__}: {e}")

    _msg_lines = [f"🧹 [{run_date}] 自動清理測試殘留資料"]
    if _deleted_summary:
        _msg_lines.append(f"✅ 週末殘留已自動刪除：{', '.join(_deleted_summary)}")
    else:
        _msg_lines.append("✅ 近期沒有週末殘留資料需要清理。")
    if _flagged:
        _msg_lines.append(f"⚠️ 平日資料量異常偏低，建議人工確認（不會自動刪）：")
        _msg_lines.extend(f"　• {f}" for f in _flagged)
    else:
        _msg_lines.append("平日資料量都在正常範圍，沒有標記可疑項目。")
    _final_msg = "\n".join(_msg_lines)
    print(f"[自動清理] {_final_msg}")
    notify_telegram(_final_msg)


# 【R97續21新增，總指揮官要求：脆弱性要有監控，不能等到很久之後才發現壞了】
# 這次法人籌碼濾網的bug就是活生生的教訓——twse_market_snapshot.f_buy這個
# 欄位長期全市場全部是0，卻因為判斷邏輯只看「這個表有沒有查到資料列」、
# 不檢查「值本身有沒有意義」，被誤判成正常，一路悄悄壞了好幾輪都沒被
# 發現。這裡建一個通用的規則清單+檢查框架，任何一張表、任何一個「應該
# 要有變化卻異常不變」的欄位都能加進來監控，不用每次都重新設計檢查邏輯。
#
# 規則格式：
#   table: 要查的表名
#   column: 要查的欄位名
#   date_column: 這張表的日期欄位名（不同表叫法不同：trade_date/date/
#                log_date...）
#   window_days: 檢查最近幾天
#   min_nonzero_ratio: 這個欄位「非零/非null」的筆數比例，低於這個門檻
#                       就判定異常（例如全部是0，比例=0，遠低於門檻）
#   description: 人類可讀的說明，出現在警示訊息裡
DATA_HEALTH_RULES = [
    {"table": "twse_market_snapshot", "column": "f_buy", "date_column": "trade_date",
     "window_days": 3, "min_nonzero_ratio": 0.1,
     "description": "外資買賣超(twse_market_snapshot.f_buy)——R97續20已知這欄位"
                    "資料源頭沒填值，法人資料改讀inst_holding表，這條規則是"
                    "持續監控這個欄位未來有沒有被誤用回來的安全網"},
    {"table": "inst_holding", "column": "foreign_buy", "date_column": "date",
     "window_days": 3, "min_nonzero_ratio": 0.3,
     "description": "外資買賣超(inst_holding.foreign_buy)——法人籌碼濾網"
                    "真正倚賴的資料源，這張表若異常會直接讓主力偵測的"
                    "籌碼濾網重演續20那次失效"},
    {"table": "smart_money_candidates", "column": "inst_net_5d", "date_column": "trade_date",
     "window_days": 3, "min_nonzero_ratio": 0.2,
     "description": "主力偵測enrich欄位(smart_money_candidates.inst_net_5d)——"
                    "續20修復後應該要有真實非零值，持續監控避免又變回全0"},
    {"table": "stock_shares_outstanding", "column": "shares", "date_column": None,
     "window_days": None, "min_nonzero_ratio": 0.8,
     "description": "股本快取覆蓋率(stock_shares_outstanding.shares)——"
                    "全表(不分日期，這張表本來就是symbol為主鍵的累積快取)"
                    "覆蓋率若掉到80%以下，代表backfill機制可能故障"},
]


def run_data_health_checks(sb):
    """
    【R97續21新增】依照DATA_HEALTH_RULES逐條檢查，異常的寫進
    data_health_alerts表(upsert，同一條規則重複觸發只更新last_seen_at，
    不會每天疊加出重複警示)，並推播Telegram。正常的規則如果先前有過
    未解決的警示，這裡會自動標記resolved(代表已經恢復正常，不用人工
    確認)。

    這個函式刻意設計成「規則清單+檢查框架」分離——以後新增要監控的
    表/欄位，只要往DATA_HEALTH_RULES加一條規則，不用改這支函式本身。
    """
    run_date = datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d")
    _alerts = []
    _recovered = []

    for rule in DATA_HEALTH_RULES:
        table, column = rule["table"], rule["column"]
        try:
            if rule.get("date_column") and rule.get("window_days"):
                _cutoff = (datetime.now(TAIPEI_TZ)
                          - timedelta(days=rule["window_days"])).strftime("%Y-%m-%d")
                res = (sb.table(table).select(column)
                      .gte(rule["date_column"], _cutoff).execute())
            else:
                res = sb.table(table).select(column).execute()
            rows = res.data or []
            total = len(rows)
            if total == 0:
                # 查不到任何列——這本身可能是另一種問題(表是空的)，但不是
                # 這條規則要抓的「有資料但值異常」，交給cleanup_test_residue
                # 那類「筆數異常偏低」的檢查去處理，這裡不重複判斷。
                continue
            nonzero = sum(1 for r in rows if r.get(column) not in (None, 0, "0"))
            ratio = nonzero / total
            is_healthy = ratio >= rule["min_nonzero_ratio"]

            if not is_healthy:
                _detail = (f"{rule['description']}：近期{total}筆裡只有{nonzero}筆"
                          f"({ratio:.0%})非零，低於門檻{rule['min_nonzero_ratio']:.0%}")
                _alerts.append({
                    "rule_name": f"nonzero_ratio_{column}", "table_name": table,
                    "column_name": column, "severity": "warning", "detail": _detail,
                    "status": "pending", "last_seen_at": datetime.now(timezone.utc).isoformat(),
                })
                print(f"[資料健檢] ⚠️ {_detail}")
            else:
                _recovered.append((f"nonzero_ratio_{column}", table, column))
        except Exception as e:
            print(f"[資料健檢] {table}.{column} 檢查失敗（跳過，不影響其他規則）："
                  f"{type(e).__name__}: {e}")

    if _alerts:
        try:
            sb.table("data_health_alerts").upsert(
                _alerts, on_conflict="rule_name,table_name,column_name").execute()
        except Exception as e:
            print(f"[資料健檢] 寫入data_health_alerts失敗：{type(e).__name__}: {e}")

    # 恢復正常的規則，如果先前有pending警示，標記成resolved
    for rule_name, table, column in _recovered:
        try:
            sb.table("data_health_alerts").update(
                {"status": "resolved", "resolved_at": datetime.now(timezone.utc).isoformat()}
            ).eq("rule_name", rule_name).eq("table_name", table).eq(
                "column_name", column).eq("status", "pending").execute()
        except Exception:
            pass   # 恢復標記失敗不影響主流程，下次健檢還會再試

    if _alerts:
        _msg = (f"🩺 [{run_date}] 資料健檢發現{len(_alerts)}項異常，"
               f"詳見網頁版「資料健康監控」面板：\n" +
               "\n".join(f"　• {a['detail']}" for a in _alerts))
        notify_telegram(_msg)
    else:
        print(f"[資料健檢] {run_date} 全部規則正常，沒有異常項目。")


def stage_signal(sb):
    """22:00 選股：掃描 → 選多空候選 → 寫入 system_portfolio（status='pending'）。"""
    run_date = datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d")
    # TaiwanStockInfo只抓一次，name_map跟上市清單都從同一份rows衍生。
    # 【R47】token輪替由_finmind_get()內部自動處理，呼叫端不用自己管理。
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

    # 【R97續5新增，見對話紀錄「FinMind限流根因排查」】每天選股開始前，
    # 先用TWSE官方三支批次端點(T86/MI_MARGN/BWIBBU_ALL)一次同步全市場
    # 快照進twse_market_snapshot表，下面for sym in pool逐檔評分時，
    # compute_full_signal_for會優先讀這張表，不再逐檔打FinMind。
    # 這裡失敗不中斷選股流程——sync_twse_market_snapshot內部任何一支
    # 端點失敗都只是該部分資料留空，不會讓整個同步報例外；就算三支全部
    # 失敗，下面的compute_full_signal_for一樣會照舊retry退回FinMind，
    # 只是那樣就沒有這次的效能優化了。
    try:
        sync_twse_market_snapshot(sb, trade_date=run_date)
    except Exception as e:
        print(f"[stage_signal] TWSE官方快照同步失敗，本次選股退回逐檔FinMind："
              f"{type(e).__name__}: {e}")

    # 【V160修復】排除已持有標的(同方向)，範圍要涵蓋holding跟pending兩種
    # 狀態，避免同一天跑兩次(手動測試+排程)對同一檔重複進場。
    try:
        held = (sb.table("system_portfolio").select("symbol,side,status")
                .in_("status", ["holding", "pending"]).execute().data) or []
    except Exception as e:
        print(f"[stage_signal-診斷] ⚠️ 查詢目前持倉失敗，將視為「目前沒有任何持倉」繼續選股："
              f"{type(e).__name__}: {e}——這可能導致對已持有的標的重複進場，若這次選股結果"
              f"出現本來就有的持股，請優先檢查這個原因。")
        held = []
    held_long = {h["symbol"] for h in held if h.get("side") == "long"}
    held_short = {h["symbol"] for h in held if h.get("side") == "short"}

    longs, shorts = [], []
    _all_scores_for_route2 = []   # 【R97續11新增】路線2用，全市場每一檔的分數都留一份
    _factor_snapshot_rows = []   # 【R97續20新增】多因子權重可視化(深版)+回測工作台地基
    for sym in pool:
        # 【R97】改用compute_full_signal_for（系統A，determine_signal），
        # 不再用compute_signal_for（系統B簡化版）——見開發歷程.md，理由：
        # 系統自動選股要跟總指揮官手動判斷用同一套評分基準，勝率比較才公平。
        #
        # 【重要，門檻同步調整】原本±3是配合系統B自己的分數範圍（實際只有
        # -3~+3）校準的，系統A分數範圍是±10、且已有自己校準過的分級
        # （classify_score()：≥6🔥偏多攻擊／≥2🟡觀察偏多／≤-6🔵偏空防守／
        # ≤-2⚠️轉弱謹慎）。這裡選擇比照「偏多攻擊/偏空防守」這個較嚴格的
        # 分級當自動進場門檻（±6，不是±2）——因為這裡是會實際寫入
        # system_portfolio、產生真實部位的選股邏輯，比對照網頁版看盤用的
        # 「觀察偏多」寬鬆門檻更保守，總指揮官如果覺得太嚴/太鬆，這兩個
        # 數字可以直接調，不用改其他任何地方。
        sig = compute_full_signal_for(sym, sb=sb)
        if not sig:
            continue
        # 【R97續20新增】每一檔的因子明細都留一份，供多因子權重可視化
        # (深版)+回測工作台使用——這是compute_full_signal_for既有運算的
        # 副產品，不多花任何額外網路成本，只是多存一筆到factor_snapshot。
        _fd = sig.get("factor_detail") or {}
        _factor_snapshot_rows.append({
            "trade_date": run_date, "symbol": sym,
            "f_ma_position": _fd.get("ma_position", 0),
            "f_foreign_buy": _fd.get("foreign_buy", 0),
            "f_volume_ratio": _fd.get("volume_ratio", 0),
            "f_open_high_close_low": _fd.get("open_high_close_low", 0),
            "f_buffer_pct": _fd.get("buffer_pct", 0),
            "f_landmine": _fd.get("landmine", 0),
            "f_ma_compression_breakout": _fd.get("ma_compression_breakout", 0),
            "f_institutional_resonance": _fd.get("institutional_resonance", 0),
            "f_institutional_persistence": _fd.get("institutional_persistence", 0),
            "f_revenue_momentum": _fd.get("revenue_momentum", 0),
            "total_score_default_weight": sig["score"],
        })
        # 【R97續11新增，路線2「波段」側資料來源】不管有沒有過±6門檻、
        # 不管有沒有已持有排除，全市場每一檔的分數都留一份——這是既有
        # 運算的副產品，不多花任何額外運算成本，只是多存一筆。
        _all_scores_for_route2.append({
            "trade_date": run_date, "symbol": sym, "score": sig["score"],
            "reasons": "、".join(sig.get("reasons", []))[:500],
        })
        if sig["score"] >= 6 and sym not in held_long:
            longs.append(sig)
        elif sig["score"] <= -6 and sym not in held_short:
            shorts.append(sig)
    longs.sort(key=lambda x: x["score"], reverse=True)
    shorts.sort(key=lambda x: x["score"])
    # 【V160 Round39】Top5→Top10：加速樣本累積(每天最多20筆而非10筆)，也讓
    # R42回測校準時有低分股票的樣本可驗證「分數高低跟勝率有沒有關係」——
    # 只選最高分5檔永遠驗證不了這件事。
    longs, shorts = longs[:10], shorts[:10]

    # 【R97新增，見開發歷程.md「事件驅動評分系統」章節】波段選股也接上
    # 同一套十大事件過濾——波段持有時間比當沖更久，曝險時間更長，這類
    # 事件的影響力只會更需要注意，不只當沖候選池要擋。命中否決類事件
    # (增資減資/募資計劃/經營權之爭併購/內部人買賣)直接從選股結果排除，
    # 標記類事件只加註在select_reason，不排除。
    try:
        _pick_codes = {c["symbol"] for c in longs + shorts}
        _announcements_signal = fetch_twse_material_announcements()
        _event_map_signal = classify_material_announcements(
            _announcements_signal, tracked_symbols=_pick_codes, reference_date=run_date
        ) if _announcements_signal else {}
    except Exception as e:
        print(f"[stage_signal-事件過濾] 查詢重大訊息失敗（不影響選股結果，本次跳過事件過濾）：{e}")
        _event_map_signal = {}

    if _event_map_signal:
        _vetoed_signal = {code for code, ev in _event_map_signal.items() if ev["veto"]}
        if _vetoed_signal:
            print(f"[stage_signal-事件過濾] {len(_vetoed_signal)}檔因重大事件被排除：{sorted(_vetoed_signal)}")
            longs = [c for c in longs if c["symbol"] not in _vetoed_signal]
            shorts = [c for c in shorts if c["symbol"] not in _vetoed_signal]

    # 【R97新增，見開發歷程.md「NVIDIA AI推演接進排程」章節】只對最終選股
    # 結果(longs+shorts，通常各≤10檔)呼叫AI推演，不是對整個掃描池呼叫。
    _ai_picks = ([dict(c, direction="long") for c in longs]
                + [dict(c, direction="short") for c in shorts])
    _ai_reports = run_ai_commentary_for_picks(_ai_picks, name_map=name_map)

    # 【V160 Round39修復】改用「各買1張+報酬率等權」取代金額平分制，修掉
    # 兩個真bug（做多做空各自拿完整預算變2倍；高價股1張爆預算）。
    def _mk_entries(cands, side):
        if not cands:
            return []
        out = []
        for c in cands:
            price = c["price"]
            shares = 1   # 各買1張，報酬率等權——不再有「預算」這個概念
            reason = (f"{'偏多攻擊' if side == 'long' else '偏空防守'}（評分{c['score']}）｜"
                      f"爆量比{c.get('vol_ratio', 0):.1f}｜漲跌{c.get('gain', 0):+.1f}%")
            _tag_events = _event_map_signal.get(c["symbol"], {}).get("tag") if _event_map_signal else None
            if _tag_events:
                reason += f"｜⚠️事件標記：{'；'.join(_tag_events)}"
            _ai_text = _ai_reports.get(c["symbol"])
            if _ai_text:
                reason += f"｜🤖AI推演：{_ai_text[:200]}..."   # select_reason欄位長度有限，只存摘要
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

    # 【R97續11新增，路線2「波段」側資料寫入】不管有沒有過門檻，全市場
    # 每一檔的分數都寫進market_signal_snapshot，供隔天早上的路線2雙重
    # 確認掃描讀取。這裡失敗不影響選股主流程（entries已經寫完了），只是
    # 路線2那份追蹤資料這次會缺，不影響今天真正的選股/下單結果。
    if _all_scores_for_route2:
        try:
            sb.table("market_signal_snapshot").delete().eq("trade_date", run_date).execute()
            _CHUNK = 500
            for i in range(0, len(_all_scores_for_route2), _CHUNK):
                sb.table("market_signal_snapshot").upsert(
                    _all_scores_for_route2[i:i + _CHUNK], on_conflict="trade_date,symbol").execute()
            print(f"[stage_signal] 路線2快照寫入完成，共{len(_all_scores_for_route2)}檔"
                  f"（全市場每一檔的分數，不只是過門檻的{len(longs)+len(shorts)}檔）。")
        except Exception as e:
            print(f"[stage_signal] 路線2快照(market_signal_snapshot)寫入失敗"
                  f"（不影響本次選股主流程）：{type(e).__name__}: {e}")

    # 【R97續20新增】factor_snapshot批次寫入——跟上面market_signal_snapshot
    # 同一套delete+批次upsert模式，失敗不影響選股主流程。這張表是多因子
    # 權重可視化(深版)+回測工作台的共用地基，只要今天有掃描結果就寫，
    # 不等網頁UI做完才開始累積——UI晚點做沒關係，資料要從今天就開始存，
    # 越早開始累積，回測工作台將來能用的樣本區間越長。
    if _factor_snapshot_rows:
        try:
            sb.table("factor_snapshot").delete().eq("trade_date", run_date).execute()
            _CHUNK = 500
            for i in range(0, len(_factor_snapshot_rows), _CHUNK):
                sb.table("factor_snapshot").upsert(
                    _factor_snapshot_rows[i:i + _CHUNK], on_conflict="trade_date,symbol").execute()
            print(f"[stage_signal] factor_snapshot寫入完成，共{len(_factor_snapshot_rows)}檔"
                  f"（多因子權重可視化+回測工作台地基）。")
        except Exception as e:
            print(f"[stage_signal] factor_snapshot寫入失敗（不影響本次選股主流程）："
                  f"{type(e).__name__}: {e}")

    sb.table("system_run_log").insert({
        "run_date": run_date, "stage": "signal", "picked_count": len(longs) + len(shorts),
        "executed_count": 0, "gate_status": "pending",
        "note": f"選股：做多{len(longs)}檔、做空{len(shorts)}檔待執行",
    }).execute()
    # 【V160新增】推播列出每一檔代號/名稱/進場價/張數/投入金額，不只是
    # 「做多5檔」這種籠統訊息。超過12檔只列前12檔並註明還有幾檔。
    def _fmt_entries(items, label):
        if not items:
            return f"{label}：無"
        lines = [f"{label}：{len(items)} 檔"]
        for e in items[:12]:
            # 【V160修復】推播價格出現浮點數精度亂碼(18.100000381469727)，
            # 統一用round(...,2)清乾淨，跟畫面戰卡精度一致。
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
    run_date = datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d")

    # 【R98續新增，總指揮官指示：最徹底解法，換成有API key的正式資料源，
    # 不受Streamlit Cloud/GitHub Actions共享IP被Yahoo限流影響】
    #
    # 【嚴重性說明，這是本次修復的核心動機】這支函式原本完全靠yfinance、
    # 沒有快取、沒有備援——一旦Yahoo限流剛好發生在台灣08:55判斷時刻，
    # sox_pct/tsm_pct會靜默變成None，而classify_gate_mode()的判斷條件是
    # 「is not None and <= 門檻」，None永遠無法觸發panic/hedge，系統會
    # 悄悄地當作「隔夜平穩」正常下單——即使費半/TSM ADR當晚真的重挫，
    # 完全沒有錯誤提示、沒有人會發現。這比網頁HUD顯示問題嚴重得多，因為
    # 這裡直接控制真實下單決策(today_gate_mode)。
    #
    # 修法：SOX用SOXX ETF代理、TSM用原生代號，都先查Finnhub(金鑰綁帳號，
    # 不受IP限流)，查不到才退回原本的yfinance(向下相容，FINNHUB_TOKEN
    # 沒設定時行為不變)。SOXX對SOX指數的追蹤誤差極小(遠低於這裡用的
    # -2.0%/-1.9%~-0.5%門檻級距)，用來做這種級距式風控判斷完全足夠。
    _finnhub_token = (os.environ.get("FINNHUB_TOKEN") or "").strip()

    def _pct_change(sym, finnhub_sym=None):
        if finnhub_sym and _finnhub_token:
            q = fetch_finnhub_quote(finnhub_sym, _finnhub_token)
            # 【R98續2新增，總指揮官指示：Finnhub限流追蹤監控】不管成功失敗
            # 都記一筆，之後查data_source_health_log表就能看到Finnhub這幾天
            # 是否真的不再被限流，不用人工盯著看。
            try:
                sb.table("data_source_health_log").insert({
                    "log_date": run_date, "source": "finnhub", "symbol": finnhub_sym,
                    "ok": bool(q.get("ok")), "fallback_used": not bool(q.get("ok")),
                    "note": f"stage_gate SOX/TSM查詢" + (f"｜失敗原因：{q.get('error', '')}"
                                                        if not q.get("ok") else ""),
                }).execute()
            except Exception as _e:
                print(f"[stage_gate-監控] 寫入data_source_health_log失敗（不影響判斷本身）：{_e}")
            if q.get("ok") and q.get("pc"):
                return round(q["dp"], 4)
            print(f"[stage_gate-診斷] Finnhub查{finnhub_sym}失敗或無資料，退回yfinance查{sym}。")
        try:
            hist = yf.Ticker(sym).history(period="5d", timeout=8).dropna(subset=["Close"])
            if len(hist) >= 2:
                prev, cur = float(hist["Close"].iloc[-2]), float(hist["Close"].iloc[-1])
                return (cur - prev) / prev * 100 if prev else None
        except Exception as e:
            print(f"[stage_gate-診斷] {sym} 漲跌幅查詢失敗：{type(e).__name__}: {e}")
        return None

    sox_pct = _pct_change("^SOX", finnhub_sym="SOXX")
    tsm_pct = _pct_change("TSM", finnhub_sym="TSM")

    # 大盤是否站上20MA——【R98續2改為FinMind優先】原本純yfinance ^TWII、
    # 無備援，是Finnhub整合那輪修完SOX/TSM後仍未處理的殘餘風險，見
    # fetch_taiex_ma20_bull_status()完整說明。FinMind失敗才退回yfinance，
    # 抓不到時保守假設站上20MA(不主動觸發對沖/熔斷)，避免資料源問題誤殺
    # 原本該執行的多單。
    twii_bull = fetch_taiex_ma20_bull_status()
    try:
        sb.table("data_source_health_log").insert({
            "log_date": run_date, "source": "finmind_taiex", "symbol": "TAIEX",
            "ok": twii_bull is not None, "fallback_used": twii_bull is None,
            "note": "stage_gate TAIEX 20MA查詢",
        }).execute()
    except Exception as _e:
        print(f"[stage_gate-監控] 寫入data_source_health_log失敗（不影響判斷本身）：{_e}")
    if twii_bull is None:
        print("[stage_gate-診斷] FinMind查TAIEX 20MA失敗，退回yfinance ^TWII。")
        twii_bull = True
        try:
            hist = yf.Ticker("^TWII").history(period="2mo", timeout=8).dropna(subset=["Close"])
            if len(hist) >= 20:
                close = float(hist["Close"].iloc[-1])
                ma20 = float(hist["Close"].tail(20).mean())
                twii_bull = close >= ma20
        except Exception as e:
            print(f"[stage_gate-診斷] TWII大盤位階查詢失敗，保守假設站上20MA："
                  f"{type(e).__name__}: {e}")

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
    run_date = datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d")
    if not is_trading_day():
        print(f"⏭️ {run_date} 非交易日，略過09:15早盤出場檢查")
        return

    exits = []
    try:
        holds = (sb.table("system_portfolio").select("*")
                 .eq("status", "holding").eq("side", "long")
                 .eq("trade_type", "swing").execute().data) or []
        for h in holds:
            sig = compute_full_signal_for(h["symbol"], sb=sb)
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
                exits.append(f"{h['symbol']} {h.get('name', '')}(早盤衝高,{roi:+.1f}%)")
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
    run_date = datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d")
    if not is_trading_day():
        print(f"⏭️ {run_date} 非交易日（週末），略過尾盤進場階段")
        notify_telegram(f"⏭️ [{run_date}] 非交易日，今日不進場、不出場")
        return

    # 【等到13:20】13:00觸發後先睡到目標時間，過了13:20才執行就不睡。
    # 【R96修復，重大bug】原本datetime.now()沒指定時區，UTC環境下實際睡眠
    # 從20分鐘暴增到8小時20分。改用datetime.now(TAIPEI_TZ)，見開發歷程.md。
    now = datetime.now(TAIPEI_TZ)
    target = now.replace(hour=13, minute=20, second=0, microsecond=0)
    wait_sec = (target - now).total_seconds()
    if wait_sec > 0:
        print(f"[尾盤進場] 目前台灣時間 {now.strftime('%H:%M:%S')}，"
              f"等待 {int(wait_sec)} 秒到13:20再動作")
        time.sleep(wait_sec)

    # 【R96新增】股票代號→名稱對照表，供出場推播訊息使用（見下面exits.append
    # 那段）。抓取失敗時_tail_entry_name_map是空dict，呼叫端.get(code, code)
    # 會自然退回顯示代號本身，不會讓整個尾盤出場流程因為這個附加功能失敗。
    try:
        _tail_entry_name_map = fetch_name_map(fetch_taiwan_stock_info_raw())
    except Exception as e:
        print(f"[尾盤進場] 股票名稱對照表抓取失敗（不影響出場邏輯本身）：{e}")
        _tail_entry_name_map = {}

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
        except Exception as e:
            print(f"[stage_tail_entry-診斷] ⚠️ 查詢目前持倉失敗，將視為「目前沒有任何持倉」繼續："
                  f"{type(e).__name__}: {e}——這可能導致對已持有的標的重複建倉，若這次尾盤進場"
                  f"結果出現本來就有的持股，請優先檢查這個原因。")
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

            sig = compute_full_signal_for(p["symbol"], sb=sb)
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
    total_pnl = 0.0   # 【R96新增】當日出場總盈虧加總，供推播訊息顯示總結，不用逐檔自己心算
    dup_holding_skip = 0
    try:
        holds = (sb.table("system_portfolio").select("*")
                .eq("status", "holding").eq("trade_type", "swing").execute().data) or []
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
            sig = compute_full_signal_for(h["symbol"], sb=sb)
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
                # 【R96新增】股票名稱＋盈虧結論——原本只有代號＋報酬率%，補上
                # fetch_name_map()名稱對照+實際損益金額(pnl)，金額比百分比更直觀。
                _pnl_word = "獲利" if pnl > 0 else ("虧損" if pnl < 0 else "打平")
                _name = _tail_entry_name_map.get(h['symbol'], h['symbol'])
                exits.append(f"{_name}({h['symbol']})({'做多' if side=='long' else '做空'},{_reason_zh},"
                            f"{roi:+.1f}%,{_pnl_word}{abs(round(pnl)):,.0f}元)")
                total_pnl += pnl   # 【R96新增】累加進當日總盈虧
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
        # 【R96新增，總指揮官反映「沒有當日賣出的總盈利or虧損金額」】逐檔的
        # 盈虧金額已經在exits裡各自標注，這裡再加一行「今天出場加總起來到底
        # 是賺是賠」的總結——不用自己一檔一檔心算加總，一眼看今天出場整體
        # 表現。只在有出場時才顯示這行，沒有出場時不留一行「總盈虧0元」的
        # 無意義訊息。
        _total_word = "獲利" if total_pnl > 0 else ("虧損" if total_pnl < 0 else "打平")
        msg += f"\n💰 今日出場總計：{_total_word} {abs(round(total_pnl)):,.0f} 元（共{len(exits)}檔）"
    notify_telegram(msg)



def _cleanup_old_broker_flows(sb, keep_days=365):
    """
    【R98新增，總指揮官方案二拍板：延長保留期至365天，供長期券商行為分析
    (哪些券商最常對特定股票隔日沖/當沖)使用】原本31天保留期只夠短期籌碼
    校正，無法累積出「這家券商過去一年在這檔股票上做了幾次隔日沖」這種
    統計。已估算365天全量儲存成本約173-213MB，佔Supabase免費額度500MB的
    35-43%，有餘裕（估算依據：R74估算的每日新增量×365天，實際因R97續25
    後範圍改為「持倉+雷達+波段候選+當沖候選+turnover_universe」而非全市場
    1076檔，實際佔用會低於這個估算上限）。
    【R74原註解，保留】全市場天天抓分點，估算每天新增約32,000筆(1076檔×約30筆)、
    5-8MB，一年下來會累積到1.3-2GB，可能超過Supabase免費方案的資料庫
    空間上限。連續買超判讀最多只看近幾天到一個月的變化，沒必要無限期
    保留全市場歷史，所以只保留最近31天，超過的自動清掉，讓儲存空間
    穩定在可控範圍（估算約150-240MB）。

    放在stage_broker_flows每次執行的最後面做，不用另外開一個排程時段——
    反正這個階段本來就會連進broker_flows寫資料，順手清一次舊資料成本
    很低。
    """
    try:
        cutoff = (datetime.now(TAIPEI_TZ) - timedelta(days=keep_days)).strftime('%Y-%m-%d')
        sb.table("broker_flows").delete().lt("log_date", cutoff).execute()
        print(f"[券商分點] 已清理 {cutoff} 之前的舊資料（只保留最近{keep_days}天）")
    except Exception as e:
        print(f"[券商分點] 清理舊資料失敗：{e}")


def get_broker_flows_target_symbols(sb):
    """
    【R97續25新增，總指揮官要求擴大券商分點補齊範圍；R98再擴大第五個來源】
    組合「持倉 + 雷達 + 今日波段候選(route2_watchlist) + 今日當沖候選
    (intraday_candidate_pool) + turnover_universe全部symbol」的聯集——
    原本只有持倉+雷達，R97續25加上當天篩選出來的當沖/波段候選，R98再加上
    turnover_universe（過去365天內曾通過週轉率粗篩的股票），這些股票是
    最值得長期追蹤分點行為的標的，不該漏掉。

    雷達清單(pinned_stocks)存在Supabase的user_state表(state_key=
    'commander_main')，是持久化資料，排程端(沒有瀏覽器session)讀得到，
    跟get_backtest_symbol_pool()用的同一套讀取方式。

    回傳排序過的symbol list（去重，維持穩定順序方便分批時可預期）。
    """
    run_date = datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d")
    symbols = set()
    try:
        rows = (sb.table("system_portfolio").select("symbol")
                .in_("status", ["holding", "pending"]).execute().data or [])
        symbols.update(_clean_symbol(r.get("symbol")) for r in rows if r.get("symbol"))
    except Exception as e:
        print(f"[券商分點-範圍] 讀取system_portfolio(持倉)失敗：{e}")
    try:
        res = sb.table("user_state").select("state_value").eq("state_key", "commander_main").limit(1).execute()
        if res.data:
            state = res.data[0].get("state_value", {}) or {}
            symbols.update(_clean_symbol(k) for k in (state.get("pinned_stocks") or {}).keys())
    except Exception as e:
        print(f"[券商分點-範圍] 讀取user_state(雷達)失敗：{e}")
    try:
        rows = (sb.table("route2_watchlist").select("symbol")
                .eq("trade_date", run_date).execute().data or [])
        symbols.update(_clean_symbol(r.get("symbol")) for r in rows if r.get("symbol"))
    except Exception as e:
        print(f"[券商分點-範圍] 讀取route2_watchlist(波段候選)失敗：{e}")
    try:
        rows = (sb.table("intraday_candidate_pool").select("symbol")
                .eq("trade_date", run_date).execute().data or [])
        symbols.update(_clean_symbol(r.get("symbol")) for r in rows if r.get("symbol"))
    except Exception as e:
        print(f"[券商分點-範圍] 讀取intraday_candidate_pool(當沖候選)失敗：{e}")
    # 【R98新增，總指揮官方案二拍板第3項】第五個來源：turnover_universe
    # 全部symbol——這張表累積「過去365天內曾通過週轉率粗篩」的股票，範圍
    # 比單日候選池大很多（估計300-400檔甚至更多），是達成「長期券商行為
    # 分析」目標的關鍵補齊——沒有這個來源，分點資料永遠只覆蓋當天熱門股，
    # 累積不出「這檔股票過去一年被哪些券商反覆隔日沖」的統計。
    try:
        rows = sb.table("turnover_universe").select("symbol").execute().data or []
        symbols.update(_clean_symbol(r.get("symbol")) for r in rows if r.get("symbol"))
    except Exception as e:
        print(f"[券商分點-範圍] 讀取turnover_universe(週轉率宇宙)失敗：{e}")
    return sorted(s for s in symbols if s)


def stage_broker_flows(sb):
    """
    【V160 R72 新增，R96移除自動排程，R97續25重新設計並恢復自動排程】

    背景（R96曾經拿掉自動排程的原因）：原本全市場~1078檔都抓、走HiStock
    爬蟲，GitHub Actions的IP長期被HiStock擋、幾乎每次執行都失敗，總指揮官
    當時決定拿掉自動cron，改成只能手動觸發、靠使用者自己家用網路IP執行。

    【R97續25重新評估，實測驗證】fetch_branch_data_with_fallback()現在是
    「FinMind Sponsor分點資料優先、失敗才退回HiStock」——這次總指揮官要求
    改回自動排程前，先手動觸發一次實測驗證：FinMind Sponsor在GitHub
    Actions這組IP上確認可以正常取得分點資料（不是走容易被擋的爬蟲，是
    正式API），但額度有限——實測單次執行大約在30幾檔後開始連續失敗
    （FinMind額度用盡+HiStock備援也連不上），觸發既有的早期斷路器提早
    中止。這證實了總指揮官原本的判斷：一次要求234/340檔規模會撞到限制，
    但根因是「FinMind分點資料額度」不是「HiStock擋IP」，兩者需要的解法
    不同——這裡改成「分批」而非「整批硬撐」。

    範圍擴大：原本只抓持倉+雷達，這次加上get_broker_flows_target_symbols()
    組合的「持倉+雷達+今日波段候選+今日當沖候選」聯集。

    分批設計：不要求一次抓完全部，每次執行只處理一批（預設30檔，可用
    環境變數BROKER_FLOWS_BATCH_SIZE覆蓋），用跟網頁版「補跑今日券商
    分點」同一套斷點續傳邏輯——今天broker_flows已經有記錄的symbol視為
    做過，只抓「今天還缺的」。這代表：cron排程當天觸發幾次，就自動累積
    抓幾批，抓完的以後的觸發會自動偵測「今天都缺的都抓完了」直接快速
    結束，不會浪費任何運算資源，也不需要精算「剛好幾批」。
    """
    run_date = datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d")
    all_symbols = get_broker_flows_target_symbols(sb)
    if not all_symbols:
        print("[券商分點] 目標範圍(持倉+雷達+波段候選+當沖候選+週轉率宇宙)是空的，跳過本次抓取。")
        return

    # 【R97續25新增，斷點續傳】今天broker_flows已經有記錄的symbol視為做過，
    # 跟網頁版get_todays_broker_flow_progress()同一套邏輯，不需要額外維護
    # 「上次跑到第幾檔」的游標狀態。
    try:
        _done_res = sb.table("broker_flows").select("symbol").eq("log_date", run_date).execute()
        _done = {r["symbol"] for r in (_done_res.data or [])}
    except Exception as e:
        print(f"[券商分點] 查詢今日已完成進度失敗，視為全部還沒抓：{e}")
        _done = set()

    remaining = [s for s in all_symbols if s not in _done]
    print(f"[券商分點] 目標範圍共{len(all_symbols)}檔(持倉+雷達+波段候選+當沖候選+週轉率宇宙)，"
          f"今天已完成{len(_done)}檔，還缺{len(remaining)}檔。")
    if not remaining:
        print("[券商分點] 今天目標範圍內的symbol全部都已經抓過，本次不用做事。")
        return

    _batch_size = int(os.environ.get("BROKER_FLOWS_BATCH_SIZE") or "30")
    symbols = remaining[:_batch_size]
    print(f"[券商分點] 本次處理{len(symbols)}檔（還剩{max(0, len(remaining) - len(symbols))}檔"
          f"留給今天之後的觸發繼續補）。")

    _ok, _fail = 0, 0
    # 【R95續10新增】早期斷路器——連續失敗達門檻(8檔)就提早中止並推播明確
    # 訊息（疑似這次GH Actions連不上HiStock，不是逐檔真的沒資料），不用
    # 等18分鐘整批跑完才知道。詳細判斷依據見開發歷程.md。
    _consecutive_fail = 0
    _EARLY_ABORT_THRESHOLD = 8
    _aborted_early = False
    _has_retried_after_pause = False
    for _idx, code in enumerate(symbols):
        df = fetch_branch_data_with_fallback(code, run_date)
        if df is None or df.empty:
            _fail += 1
            _consecutive_fail += 1
            if _consecutive_fail >= _EARLY_ABORT_THRESHOLD:
                # 【R96新增】容錯重試——暫時性IP限流停頓後往往會恢復，加一次
                # 「暫停90秒+重試」的機會，只重試一次，避免真的是永久性問題時
                # 無限重試浪費額度。
                if not _has_retried_after_pause:
                    _has_retried_after_pause = True
                    print(f"[券商分點] 連續{_consecutive_fail}檔失敗，可能是暫時性限流，"
                          f"暫停90秒後重試一次...")
                    time.sleep(90)
                    _retry_consecutive_fail = 0
                    _retry_recovered = False
                    for _rcode in symbols[max(0, _idx - _consecutive_fail + 1):_idx + 1]:
                        _rdf = fetch_branch_data_with_fallback(_rcode, run_date)
                        if _rdf is None or _rdf.empty:
                            _retry_consecutive_fail += 1
                        else:
                            _retry_recovered = True
                            break
                    if _retry_recovered:
                        print(f"[券商分點] 暫停重試後恢復正常，繼續原本的掃描（視為單次暫時性阻擋）。")
                        _consecutive_fail = 0
                        continue
                    print(f"[券商分點] 暫停重試後仍然連續失敗，確認不是單純的暫時性阻擋，提早中止，"
                          f"剩下的留給今天之後的觸發繼續補（斷點續傳，不會重複抓已完成的）。")
                _aborted_early = True
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
        time.sleep(1)  # 對FinMind/HiStock這兩個資源客氣一點，不要連續轟炸

    _tested_count = _idx + 1 if _aborted_early else len(symbols)
    _remaining_after = max(0, len(remaining) - _tested_count)
    print(f"[券商分點] 本批完成：{_ok} 檔成功、{_fail} 檔失敗（本批{len(symbols)}檔，"
          f"實際測試{_tested_count}檔{'，提早中止' if _aborted_early else ''}）。"
          f"今天目標範圍還缺{_remaining_after}檔，"
          + ("已全部補齊。" if _remaining_after == 0 else "留給今天之後的觸發繼續補。"))
    _cleanup_old_broker_flows(sb, keep_days=365)
    try:
        sb.table("system_run_log").insert({
            "run_date": run_date, "stage": "broker_flows", "picked_count": len(symbols),
            "executed_count": _ok, "gate_status": "normal" if _fail == 0 else "error",
            "note": f"FinMind優先+HiStock備援(持倉+雷達+波段+當沖+週轉率宇宙)本批：{_ok}成功/{_fail}失敗，"
                    f"今天還缺{_remaining_after}檔"
                    + ("（提早中止，疑似連線/額度問題）" if _aborted_early else ""),
        }).execute()
    except Exception as e:
        print(f"[券商分點] 寫入log失敗：{e}")


def stage_overnight_flip_dealer_stats(sb):
    """
    【R98新增，總指揮官方案二拍板第5項：隔日沖動態名單】

    每週(建議週日離峰時段)呼叫一次warroom_core.compute_overnight_flip_
    dealer_stats()，掃描broker_flows近180天資料統計出隔日沖慣犯券商，
    寫入overnight_flip_dealers表。不做每日更新——分點行為模式變化較慢，
    過於頻繁更新反而不穩定（比照原始CMoney方法論分析報告的建議）。

    每次更新前不清空舊資料，用upsert寫入本次統計結果——保留歷史版本
    （靠created_at/updated_at欄位分辨），供之後檢視「某券商是何時被系統
    認定為隔日沖慣犯」用，不做物理刪除。

    此stage完全不依賴「週一FinMind間隔測試」的結果——它只是讀取已經在
    broker_flows裡累積的歷史資料做統計，跟分點資料當下抓取的額度限制
    無關，可以獨立上線。真正需要等資料量的是「統計結果的可信度」：
    365天保留期剛延長上線時，broker_flows歷史深度還很淺，統計出來的
    repeat_count會偏低，需要幾個月的資料累積才會穩定，這點在UI呈現時
    需要用訊號筆數門檻誠實標示（比照策略統計驗證模組的樣本不足警示）。
    """
    run_date = datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d")
    try:
        stats = compute_overnight_flip_dealer_stats(sb, lookback_days=180,
                                                       min_repeat_count=3, min_flip_ratio=0.5)
    except Exception as e:
        print(f"[隔日沖動態統計] 統計失敗：{type(e).__name__}: {e}")
        try:
            sb.table("system_run_log").insert({
                "run_date": run_date, "stage": "overnight_flip_dealer_stats",
                "picked_count": 0, "executed_count": 0, "gate_status": "error",
                "note": f"統計失敗：{type(e).__name__}: {e}",
            }).execute()
        except Exception:
            pass
        return

    if not stats:
        print("[隔日沖動態統計] 本次無符合條件的慣犯券商（可能是資料量還不夠，"
              "不代表市場上真的沒有隔日沖行為，靜態DAY_TRADER_BROKERS名單持續正常運作）。")
        try:
            sb.table("system_run_log").insert({
                "run_date": run_date, "stage": "overnight_flip_dealer_stats",
                "picked_count": 0, "executed_count": 0, "gate_status": "normal",
                "note": "本次統計無符合門檻(repeat_count>=3)的券商，樣本可能不足。",
            }).execute()
        except Exception:
            pass
        return

    rows = [{
        "broker_name": broker,
        "repeat_count": info["repeat_count"],
        "avg_flip_ratio": info["avg_flip_ratio"],
        "symbols_involved": info["symbols_involved"],
        "tier": classify_overnight_flip_dealer_tier(info["repeat_count"]),
        "stats_date": run_date,
    } for broker, info in stats.items()]
    try:
        sb.table("overnight_flip_dealers").upsert(rows, on_conflict="broker_name").execute()
        print(f"[隔日沖動態統計] 本次統計出{len(rows)}家慣犯券商，已寫入overnight_flip_dealers。")
        sb.table("system_run_log").insert({
            "run_date": run_date, "stage": "overnight_flip_dealer_stats",
            "picked_count": len(rows), "executed_count": len(rows), "gate_status": "normal",
            "note": f"統計出{len(rows)}家慣犯券商，"
                    f"核心慣犯{sum(1 for r in rows if r['tier']=='核心慣犯')}家、"
                    f"疑似慣犯{sum(1 for r in rows if r['tier']=='疑似慣犯')}家。",
        }).execute()
    except Exception as e:
        print(f"[隔日沖動態統計] 寫入overnight_flip_dealers失敗：{type(e).__name__}: {e}")


def stage_data_source_health_report(sb):
    """
    【R98續2新增，總指揮官指示：Finnhub是否真的不再被限流，由排程自動追蹤，
    只要最後結果】每週一次，統計過去7天data_source_health_log裡各資料源
    (finnhub/finmind_taiex)的成功率，Telegram推播一份摘要，不用你自己
    查表——這就是「最後結果」的呈現方式。

    設計原則：只在有異常(成功率明顯偏低)或首次啟用時才主動推播完整報告，
    平時運作正常就推播簡短一行摘要，避免每週固定轟炸一則長篇通知反而
    降低你對真正異常時的注意力。
    """
    run_date = datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d")
    _since = (datetime.now(TAIPEI_TZ) - timedelta(days=7)).strftime("%Y-%m-%d")
    try:
        rows = (sb.table("data_source_health_log").select("source, ok, log_date")
                .gte("log_date", _since).execute().data or [])
    except Exception as e:
        print(f"[資料源健康週報] 查詢失敗：{type(e).__name__}: {e}")
        return

    if not rows:
        print("[資料源健康週報] 過去7天沒有任何記錄，可能是FINNHUB_TOKEN還沒設定"
              "或這段期間排程沒觸發到相關stage，本次不推播。")
        return

    from collections import defaultdict
    stats = defaultdict(lambda: {"total": 0, "ok": 0})
    for r in rows:
        s = stats[r["source"]]
        s["total"] += 1
        if r.get("ok"):
            s["ok"] += 1

    lines = [f"📊 資料源健康週報（過去7天，{_since}~{run_date}）"]
    any_bad = False
    for source, s in stats.items():
        rate = round(s["ok"] / s["total"] * 100, 1) if s["total"] else 0.0
        _label = {"finnhub": "Finnhub", "finmind_taiex": "FinMind TAIEX"}.get(source, source)
        _flag = ""
        if rate < 80:
            _flag = "⚠️"
            any_bad = True
        lines.append(f"{_flag}{_label}：{s['ok']}/{s['total']}次成功（{rate}%）")
    lines.append("" if any_bad else "整體運作正常，Finnhub暫無被限流跡象。" if "finnhub" in stats else "")

    msg = "\n".join(l for l in lines if l)
    try:
        notify_telegram(msg)
        print(f"[資料源健康週報] 已推播：{msg}")
    except Exception as e:
        print(f"[資料源健康週報] Telegram推播失敗：{type(e).__name__}: {e}")

    try:
        sb.table("system_run_log").insert({
            "run_date": run_date, "stage": "data_source_health_report",
            "picked_count": len(rows), "executed_count": len(rows),
            "gate_status": "error" if any_bad else "normal", "note": msg[:200],
        }).execute()
    except Exception as e:
        print(f"[資料源健康週報] 寫入system_run_log失敗：{e}")


def _current_disclosed_quarter():
    """
    【R98續20新增】算出「現在這個時間點，市場上已經公告的最新一季財報」
    是哪一季——不是「現在是哪一季」，是「上一個已經公告完的季度」。
    例如現在是2026/08/25（Q3進行中），Q2(4-6月)公告截止日是8/14，已經
    過了，所以「已公告最新季」是Q2；如果現在是2026/07/20（還沒到8/14），
    Q2還沒公告完，「已公告最新季」要往前推到Q1。
    """
    today = datetime.now(TAIPEI_TZ).date()
    year_roc = today.year - 1911
    # 從「今年」開始最多往回找8季(2年)，逐一檢查disclosure_est是否已過，
    # 不能只往回退一年就假設一定是Q4已公告——年初時去年Q4(年報)截止日
    # 是隔年3/31，這段期間去年Q4也還沒公告，要再往前一季檢查。
    y, s = year_roc, 4
    for _ in range(8):
        _, disclosure_est = _mops_quarter_dates(y, s)
        if today >= disclosure_est:
            return y, s
        s -= 1
        if s == 0:
            y -= 1
            s = 4
    # 理論上跑不到這裡(8季=2年前的資料一定早就公告過)，防禦性保底
    return year_roc - 2, 4


def stage_mops_financial_scan(sb, year_roc=None, season=None):
    """
    【R98續20新增，R98續21改版：全市場財報快照】用
    fetch_mops_financial_batch()拿全市場(上市sii)最新一期財報彙總資料，
    寫進mops_financial_snapshot——不像stage_financial_health_scan那樣
    受FinMind額度限制要分批，這個排程一次就能覆蓋全市場上市公司。

    【R98續21重要變更】底層資料源已從被referer-wall擋住的MOPS ajax
    端點，改用TWSE官方OpenAPI——這個來源只提供「當期最新」快照，
    不能像舊設計那樣指定year_roc/season查任意歷史季度。這裡保留這兩個
    參數只是相容舊呼叫介面，實際上每次呼叫都只會拿到目前最新一期資料，
    quarter_end_date/disclosure_date_est標記的是「這份快照對應到系統
    判斷的最新已公告季度」(_current_disclosed_quarter())，不代表能
    回溯查詢那一季。想累積歷史時間序列，只能靠這個排程長期定期運行，
    每次把「當下最新快照」存進DB，自然隨時間累積，沒辦法一次性回溯
    過去。

    上櫃(otc)目前不支援(TPEx的OpenAPI端點命名規則不同，還沒接)，
    fetch_mops_financial_batch()對market='otc'會直接回傳空dict，
    這裡因此只查market='sii'，不浪費一次無意義的呼叫。
    """
    if year_roc is None or season is None:
        year_roc, season = _current_disclosed_quarter()
    quarter_end, disclosure_est = _mops_quarter_dates(year_roc, season)
    print(f"[MOPS財報排程] 開始抓全市場上市公司最新財報快照"
          f"（對應季度：民國{year_roc}年Q{season}，季底{quarter_end}）")

    # 【R98續20新增，診斷用，保留】用contextlib.redirect_stdout擷取
    # fetch_mops_financial_batch()內部的print()診斷輸出，0檔時寫進
    # system_config——GitHub Actions原始log存在讀不到的blob storage，
    # 這是繞開這個限制的既有解法。
    import io
    import contextlib
    _diag_buf = io.StringIO()

    _ok, _fail = 0, 0
    try:
        with contextlib.redirect_stdout(_diag_buf):
            batch = fetch_mops_financial_batch(market='sii')
    except Exception as e:
        batch = {}
        print(f"[MOPS財報排程] 整批請求失敗：{type(e).__name__}: {e}")

    for sym, fields in batch.items():
        try:
            sb.table("mops_financial_snapshot").upsert({
                "symbol": sym, "year_roc": year_roc, "season": season,
                "quarter_end_date": quarter_end.isoformat(),
                "disclosure_date_est": disclosure_est.isoformat(),
                "revenue": fields.get("revenue"),
                "gross_profit": fields.get("gross_profit"),
                "operating_income": fields.get("operating_income"),
                "net_income": fields.get("net_income"),
                "eps": fields.get("eps"),
                "market": "sii",
            }, on_conflict="symbol,year_roc,season").execute()
            _ok += 1
        except Exception as e:
            print(f"[MOPS財報排程] {sym} 寫入失敗：{type(e).__name__}: {e}")
            _fail += 1

    # 【R98續39新增，總指揮官指示方案C：財報體質P2】跟損益表同一輪順便
    # 抓資產負債表，upsert同一張表補上6個資產負債表欄位——用同一組
    # (symbol,year_roc,season)當key，跟損益表對齊到同一季，不會產生
    # 不同季度的資料混在一起。debt_ratio(負債比)是P2的核心指標之一。
    _bs_ok, _bs_fail = 0, 0
    try:
        with contextlib.redirect_stdout(_diag_buf):
            bs_batch = fetch_mops_balance_sheet_batch(market='sii')
    except Exception as e:
        bs_batch = {}
        print(f"[MOPS資產負債表排程] 整批請求失敗：{type(e).__name__}: {e}")

    for sym, fields in bs_batch.items():
        try:
            sb.table("mops_financial_snapshot").upsert({
                "symbol": sym, "year_roc": year_roc, "season": season,
                "quarter_end_date": quarter_end.isoformat(),
                "disclosure_date_est": disclosure_est.isoformat(),
                "total_assets": fields.get("total_assets"),
                "total_liabilities": fields.get("total_liabilities"),
                "current_assets": fields.get("current_assets"),
                "current_liabilities": fields.get("current_liabilities"),
                "equity_total": fields.get("equity_total"),
                "debt_ratio": fields.get("debt_ratio"),
                "market": "sii",
            }, on_conflict="symbol,year_roc,season").execute()
            _bs_ok += 1
        except Exception as e:
            print(f"[MOPS資產負債表排程] {sym} 寫入失敗：{type(e).__name__}: {e}")
            _bs_fail += 1
    print(f"[MOPS資產負債表排程] 完成：成功寫入{_bs_ok}檔，失敗{_bs_fail}檔。")

    print(f"[MOPS財報排程] 完成：成功寫入{_ok}檔，失敗{_fail}檔。")
    _diag_text = _diag_buf.getvalue()
    print(_diag_text)  # 正常log也印一份，雖然讀不到，至少本機/未來能讀log的環境用得到
    if _ok == 0:
        # 只在真的0檔時才寫進system_config——避免正常運作時也一直洗掉
        # 上次的診斷內容，且减少不必要的Supabase寫入。
        try:
            set_config(sb, "diag_mops_financial_scan_result", _diag_text[:8000])
        except Exception:
            pass
    try:
        sb.table("system_run_log").insert({
            "run_date": datetime.now(TAIPEI_TZ).strftime('%Y-%m-%d'),
            "stage": "mops_financial_scan",
            "picked_count": _ok, "executed_count": _ok + _fail,
            "gate_status": "normal" if _ok > 0 else "error",
            "note": f"民國{year_roc}Q{season}(TWSE OpenAPI當期快照)，損益表成功{_ok}/失敗{_fail}"
                   f"｜資產負債表成功{_bs_ok}/失敗{_bs_fail}",
        }).execute()
    except Exception as e:
        print(f"[MOPS財報排程] 寫入system_run_log失敗：{e}")


def stage_diag_mis_live(sb):
    """
    【R98續25新增，臨時診斷用，之後會拿掉】總指揮官反映戰情速覽全部
    股票都停在昨天收盤，即使強制重整頁面也一樣——用GitHub Actions的
    真實網路環境直接查幾檔知名liquid股票(2303聯電/2330台積電/2317鴻海)
    現在這個當下fetch_twse_mis_batch()實際拿到什麼，不用猜。
    """
    test_pairs = [('2303', 'tse'), ('2330', 'tse'), ('2317', 'tse')]
    try:
        results, diag = fetch_twse_mis_batch(test_pairs, return_diagnostics=True)
        lines = [f"查詢時間(台北): {datetime.now(TAIPEI_TZ).strftime('%Y-%m-%d %H:%M:%S')}",
                f"results: {results}",
                f"diag: {diag}"]
        full_text = "\n".join(lines)
    except Exception as e:
        import traceback
        full_text = f"整批呼叫拋出例外：{type(e).__name__}: {e}\n{traceback.format_exc()}"
    print(full_text)
    set_config(sb, "diag_mis_live_result", full_text)


def stage_diag_balance_sheet_live(sb):
    """
    【R98續38新增，臨時診斷用，之後會拿掉】方案C財報體質P2——用GitHub
    Actions真實網路環境實測fetch_mops_balance_sheet_batch()，確認
    t187ap07_X_*這個端點家族真的能正常抓到資產負債表資料，不用猜。
    """
    import io
    import contextlib
    _diag_buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(_diag_buf):
            results = fetch_mops_balance_sheet_batch()
        lines = [f"查詢時間(台北): {datetime.now(TAIPEI_TZ).strftime('%Y-%m-%d %H:%M:%S')}",
                f"總筆數: {len(results)}"]
        for sym in ['2330', '2317', '2882', '2801', '1101']:
            if sym in results:
                lines.append(f"{sym}: {results[sym]}")
            else:
                lines.append(f"{sym}: 查無資料")
        _internal_log = _diag_buf.getvalue()
        if _internal_log:
            lines.append(f"函式內部診斷輸出：\n{_internal_log}")
        full_text = "\n".join(lines)
    except Exception as e:
        import traceback
        full_text = f"整批呼叫拋出例外：{type(e).__name__}: {e}\n{traceback.format_exc()}"
    print(full_text)
    set_config(sb, "diag_balance_sheet_live_result", full_text)

    # 【R98續39新增，臨時測試，之後會拿掉】總指揮官確認資產負債表原本
    # 網頁本身就有多個分頁組成一份完整報告，t187ap07_X_*資料量偏少可能
    # 是同一種結構性限制的另一種呈現——直接測試看有沒有_L_後綴的完整版
    # 端點(比照損益表t187ap06_L_*那種)存在。
    try:
        import requests as _req
        _test_url = "https://openapi.twse.com.tw/v1/opendata/t187ap07_L_ci"
        _resp = _req.get(_test_url, timeout=15)
        _test_result = f"t187ap07_L_ci 測試：HTTP {_resp.status_code}"
        if _resp.status_code == 200:
            try:
                _rows = _resp.json()
                _test_result += f"，筆數={len(_rows) if isinstance(_rows, list) else '非list格式'}"
            except Exception as _je:
                _test_result += f"，JSON解析失敗：{_je}"
    except Exception as _te:
        _test_result = f"t187ap07_L_ci 測試失敗：{type(_te).__name__}: {_te}"
    print(_test_result)
    set_config(sb, "diag_balance_sheet_l_suffix_test", _test_result)


def stage_diag_p0_signal_live(sb):
    """
    【R98續32新增，臨時診斷用，之後會拿掉】P0主線(compute_full_signal_
    for徹底升級)完成後，用GitHub Actions真實網路環境+真實股票，實際
    跑一次確認price_source是不是真的會用到twse_mis/shioaji(不是一直
    退回historical_close)，不用猜。
    """
    lines = [f"查詢時間(台北): {datetime.now(TAIPEI_TZ).strftime('%Y-%m-%d %H:%M:%S')}",
            f"is_twse_market_hours(): {is_twse_market_hours()}"]
    for sym in ['2330', '2303', '2317']:
        try:
            result = compute_full_signal_for(sym, sb=sb)
            if result is None:
                lines.append(f"{sym}: 回傳None(fetch_price_hist查不到歷史資料)")
            else:
                lines.append(f"{sym}: price_source={result.get('price_source')}, "
                            f"price={result.get('price')}, gain={result.get('gain')}%, "
                            f"score={result.get('score')}")
        except Exception as e:
            import traceback
            lines.append(f"{sym}: 拋出例外：{type(e).__name__}: {e}\n{traceback.format_exc()}")
    full_text = "\n".join(lines)
    print(full_text)
    set_config(sb, "diag_p0_signal_live_result", full_text)


def stage_diag_shioaji_live(sb):
    """
    【R98續29新增，臨時診斷用，之後會拿掉】總指揮官已完成永豐金API Key
    申請並存進secrets——用GitHub Actions的真實網路環境+真實Key，實際
    呼叫fetch_shioaji_snapshot()查幾檔知名liquid股票，確認整條路徑
    真的能拿到即時報價，不用猜。

    安全提醒：這支診斷stage一樣只呼叫fetch_shioaji_snapshot()做查詢，
    不會呼叫任何下單/CA憑證相關函式，check_shioaji_safety.py會確認
    這一點。
    """
    api_key = (os.environ.get("SHIOAJI_API_KEY") or "").strip()
    secret_key = (os.environ.get("SHIOAJI_SECRET_KEY") or "").strip()
    lines = [f"查詢時間(台北): {datetime.now(TAIPEI_TZ).strftime('%Y-%m-%d %H:%M:%S')}",
            f"SHIOAJI_API_KEY是否有設定: {'是(長度' + str(len(api_key)) + ')' if api_key else '否'}",
            f"SHIOAJI_SECRET_KEY是否有設定: {'是(長度' + str(len(secret_key)) + ')' if secret_key else '否'}"]
    if not api_key or not secret_key:
        lines.append("金鑰未設定，無法測試，直接結束。")
        full_text = "\n".join(lines)
        print(full_text)
        set_config(sb, "diag_shioaji_live_result", full_text)
        return

    test_symbols = ['2303', '2330', '2317']
    import io
    import contextlib
    _diag_buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(_diag_buf):
            results = fetch_shioaji_snapshot(test_symbols, api_key, secret_key)
        lines.append(f"查詢股票: {test_symbols}")
        lines.append(f"results: {results}")
    except Exception as e:
        import traceback
        lines.append(f"整批呼叫拋出例外：{type(e).__name__}: {e}\n{traceback.format_exc()}")

    # 【R98續30新增，臨時診斷，之後會拿掉】總指揮官反映即時時間欄位顯示
    # 「22:30:00」跟查詢當下的台北時間對不上——與其繼續猜snap.ts的真實
    # 單位(奈秒/微秒/其他)，直接繞過fetch_shioaji_snapshot()的轉換邏輯，
    # 印出snap.ts的原始數值(不做任何轉換)，用這個真實數字回推正確的
    # 換算方式，不用再猜。
    try:
        import shioaji as sj
        _api2 = sj.Shioaji(simulation=False)
        _api2.login(api_key=api_key, secret_key=secret_key, subscribe_trade=False)
        _c = _api2.Contracts.Stocks['2330']
        if _c is not None:
            _raw_snaps = _api2.snapshots([_c], timeout=15000)
            if _raw_snaps:
                _raw = _raw_snaps[0]
                lines.append(f"[原始ts診斷] snap.ts原始值(未轉換): {_raw.ts}")
                lines.append(f"[原始ts診斷] 用/1e9當秒數轉換(現有邏輯): "
                            f"{datetime.fromtimestamp(_raw.ts / 1e9, tz=TAIPEI_TZ)}")
                lines.append(f"[原始ts診斷] 用/1e6當秒數轉換(假設是微秒): "
                            f"{datetime.fromtimestamp(_raw.ts / 1e6, tz=TAIPEI_TZ)}")
                lines.append(f"[原始ts診斷] 完整snapshot物件內容: {_raw}")
        _api2.logout()
    except Exception as _ts_e:
        lines.append(f"[原始ts診斷] 失敗：{type(_ts_e).__name__}: {_ts_e}")

    # 【R98續29補，重要教訓】fetch_shioaji_snapshot()內部自己有try/except，
    # 大部分錯誤(登入失敗/查詢失敗)都在函式內部被接住、印出診斷訊息後
    # 回傳空dict，不會讓例外往外傳——外層這裡原本只看得到「results是空的」
    # 但看不到「為什麼是空的」，因為函式內部的print()訊息一樣飄進讀不到
    # 的GitHub Actions原始log。用跟MOPS財報排程同一招：擷取這段期間的
    # stdout，一起寫進system_config，才能看到函式內部真正發生了什麼。
    _internal_log = _diag_buf.getvalue()
    if _internal_log:
        lines.append(f"函式內部診斷輸出：\n{_internal_log}")
    full_text = "\n".join(lines)
    print(full_text)
    set_config(sb, "diag_shioaji_live_result", full_text)


def stage_key_usage_monitor(sb):
    """
    【R98續31新增，總指揮官方向：金鑰使用量異常監控】薄包裝層，實際
    邏輯都在warroom_core.py的check_api_key_usage_anomaly()（跟網頁端
    共用同一份邏輯，這裡只是排程端的呼叫入口）。獨立成自己的stage/
    cron排程，不跟stage_gate這種控制真實下單決策的關鍵排程混在一起，
    降低互相影響的風險。
    """
    check_api_key_usage_anomaly(sb)


def stage_financial_health_scan(sb):
    """
    【R98新增，總指揮官方案二P1：財報體質排程化】

    背景：fetch_financial_health()（毛利率/ROE/現金流品質）原本只在網頁版
    按需查詢（使用者手動點「查詢深度財報」按鈕才觸發），完全沒有排程，
    battery全市場一次抓要400檔×3張表=1200次API額度，對免費額度是災難性
    浪費（這是原本設計時就明講的取捨）。

    範圍縮小：跟stage_broker_flows同一個邏輯，只對get_broker_flows_
    target_symbols()（持倉+雷達+波段候選+當沖候選+週轉率宇宙）掃描，
    不是全市場——這些本來就是系統關注的股票，財報體質對這個範圍才有
    實際意義。

    分批+斷點續傳：財報是季更資料，不需要每天全部重查。這裡用
    financial_health_snapshot表的quarter_date欄位判斷「這一季是否已經
    查過」，已查過的symbol直接跳過，只處理「還沒查這一季」的。每次執行
    只處理一批（預設20檔，比broker_flows的30檔更保守——這裡每檔要打3個
    資料集，單檔成本是broker_flows的3倍），累積觸發自動補齊剩餘的。

    建議排程頻率：每週1-2次即可（財報不會日更），不需要跟broker_flows
    一樣密集排程。
    """
    run_date = datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d")
    _this_quarter = f"{datetime.now(TAIPEI_TZ).year}Q{(datetime.now(TAIPEI_TZ).month - 1) // 3 + 1}"

    all_symbols = get_broker_flows_target_symbols(sb)
    if not all_symbols:
        print("[財報體質排程] 目標範圍是空的，跳過本次掃描。")
        return

    try:
        _existing_res = sb.table("financial_health_snapshot").select("symbol, scan_quarter").execute()
        _done = {r["symbol"] for r in (_existing_res.data or []) if r.get("scan_quarter") == _this_quarter}
    except Exception as e:
        print(f"[財報體質排程] 查詢既有快照失敗，視為全部還沒查：{e}")
        _done = set()

    remaining = [s for s in all_symbols if s not in _done]
    print(f"[財報體質排程] 目標範圍共{len(all_symbols)}檔，本季({_this_quarter})已查過{len(_done)}檔，"
          f"還缺{len(remaining)}檔。")
    if not remaining:
        print("[財報體質排程] 本季目標範圍內全部查過了，本次不用做事。")
        return

    _batch_size = int(os.environ.get("FINANCIAL_HEALTH_BATCH_SIZE") or "20")
    symbols = remaining[:_batch_size]
    print(f"[財報體質排程] 本次處理{len(symbols)}檔（還剩{max(0, len(remaining) - len(symbols))}檔"
          f"留給之後的觸發繼續補）。")

    _ok, _fail = 0, 0
    for code in symbols:
        try:
            # 【R98】跟其他排程stage同樣的慣例——不用另外抓fm_token，
            # _finmind_get()內部的多帳號輪替池(set_finmind_tokens()已在
            # 模組載入時設定好)會自動處理，傳空字串即可，比照
            # compute_full_signal_for()等既有呼叫端的一致做法。
            fh = fetch_financial_health(code, "")
            if fh is None:
                _fail += 1
                continue
            # 【R98續17修復，總指揮官指示方向C融合系統】原本只寫gross_margin/
            # roe/cash_quality三個舊指標，R98續2早就在fetch_financial_health()
            # 裡算好的debt_ratio/interest_coverage/free_cash_flow三個新指標
            # 從來沒被寫進DB——compute_financial_risk_score()因此永遠只拿得到
            # 一半指標，是個「函式寫好了但資料沒接上」的斷點。這裡一次補齊：
            # 三個新指標直接寫欄位；risk_score/risk_level在排程當下就算好存
            # 起來(不是每次網頁端讀取時才重算)，網頁端/determine_signal因子
            # 直接讀risk_score即可，不用重新呼叫compute_financial_risk_score，
            # 也不用重新查6個指標。
            # 【R98續19修正】interest_coverage已確認FinMind資料源沒有這個
            # 科目、永遠是None，改寫current_ratio(流動比率)——見
            # fetch_financial_health()裡的完整說明。
            _risk = compute_financial_risk_score(fh)
            sb.table("financial_health_snapshot").upsert({
                "symbol": code, "scan_quarter": _this_quarter, "scan_date": run_date,
                "quarter_date": fh.get("quarter_date"), "gross_margin": fh.get("gross_margin"),
                "roe": fh.get("roe"), "cash_quality": fh.get("cash_quality"),
                "cash_quality_note": fh.get("cash_quality_note"),
                "debt_ratio": fh.get("debt_ratio"),
                "current_ratio": fh.get("current_ratio"),
                "free_cash_flow": fh.get("free_cash_flow"),
                "risk_score": _risk.get("score") if _risk else None,
                "risk_level": _risk.get("level") if _risk else None,
            }, on_conflict="symbol").execute()
            _ok += 1
        except Exception as e:
            print(f"[財報體質排程] {code} 處理失敗：{type(e).__name__}: {e}")
            _fail += 1
        time.sleep(1)  # 跟broker_flows同樣的節流考量，對FinMind客氣一點

    print(f"[財報體質排程] 本批完成：{_ok}檔成功、{_fail}檔失敗（本批{len(symbols)}檔）。")
    try:
        sb.table("system_run_log").insert({
            "run_date": run_date, "stage": "financial_health_scan",
            "picked_count": len(symbols), "executed_count": _ok,
            "gate_status": "normal" if _fail == 0 else "error",
            "note": f"本季({_this_quarter})財報體質掃描本批：{_ok}成功/{_fail}失敗，"
                    f"還缺{max(0, len(remaining) - len(symbols))}檔。",
        }).execute()
    except Exception as e:
        print(f"[財報體質排程] 寫入log失敗：{e}")


def _validate_previous_trading_day(sb):
    """
    【R95續29新增】自建5分K的回溯驗證輔助函式——在每次stage_intraday_kbar
    開始收集「今天」之前，先驗證「上一個有收集到資料的交易日」，用官方
    日K的開盤/當日最高/最低當基準，交叉比對出組裝邏輯有沒有系統性問題。

    找「上一個交易日」的方式：直接查intraday_5min_bars裡「今天以外，最新
    的一個trade_date」，不用自己猜是昨天還是上週五——這樣就算中間跳過
    某幾天沒收集(排程失敗、假日)，也能正確找到真正有資料可以驗證的那天，
    不會驗證到一個根本沒收集過的日期。
    """
    _today = datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d")
    try:
        res = (sb.table("intraday_5min_bars").select("trade_date")
              .neq("trade_date", _today).order("trade_date", desc=True).limit(1).execute())
        if not res.data:
            print("[自建5分K回溯驗證] 還沒有任何過去的資料可以驗證，略過（第一次執行時正常）。")
            return
        _prev_date = res.data[0]["trade_date"]
    except Exception as e:
        print(f"[自建5分K回溯驗證] 查詢上一個交易日失敗：{e}，略過本次驗證。")
        return

    try:
        res2 = (sb.table("intraday_5min_bars").select("*")
               .eq("trade_date", _prev_date).execute())
        rows = res2.data or []
    except Exception as e:
        print(f"[自建5分K回溯驗證] 讀取 {_prev_date} 的K棒失敗：{e}，略過本次驗證。")
        return

    by_symbol = {}
    for r in rows:
        by_symbol.setdefault(r["symbol"], []).append(r)

    _ok_count, _bad_count = 0, 0
    import yfinance as yf
    for sym, bars in by_symbol.items():
        try:
            # 【R95續29】用「抓近期一段區間、篩選出那一天」，不用yfinance的
            # start/end單日查詢——這個專案其他地方已經確認period+篩選是
            # 比較穩定可靠的抓法，這裡沿用同一套模式，不引入新的不確定性。
            hist = None
            for _suffix in ('.TW', '.TWO'):
                tk = yf.Ticker(f"{sym}{_suffix}")
                _h = tk.history(period="10d", timeout=10)
                if _h.empty:
                    continue
                _h.index = _h.index.strftime('%Y-%m-%d')
                if _prev_date in _h.index:
                    hist = _h.loc[[_prev_date]]
                    break
            if hist is None or hist.empty:
                print(f"[自建5分K回溯驗證] {sym} 抓不到 {_prev_date} 的官方日K，無法驗證，跳過。")
                continue
            _daily_open = float(hist['Open'].iloc[0])
            _daily_high = float(hist['High'].iloc[0])
            _daily_low = float(hist['Low'].iloc[0])
        except Exception as e:
            print(f"[自建5分K回溯驗證] {sym} 抓官方日K時發生例外：{e}，跳過。")
            continue

        result = validate_intraday_bars_vs_daily(bars, _daily_open, _daily_high, _daily_low)
        if result['ok']:
            _ok_count += 1
        else:
            _bad_count += 1
            print(f"[自建5分K回溯驗證] ⚠️ {sym}（{_prev_date}）驗證發現異常：{'；'.join(result['issues'])}")

    print(f"[自建5分K回溯驗證] {_prev_date} 共驗證 {_ok_count + _bad_count} 檔，"
         f"{_ok_count} 檔正常、{_bad_count} 檔異常。")


def _get_day_trader_tag(symbol):
    """
    【R97新增，總指揮官依實戰經驗提供：當沖比>50~60%代表短線客在對作，
    波動大機會多】只對Stage2篩出的最終候選（通常個位數~10幾檔）呼叫，
    不對Stage0b全部30檔呼叫，控制FinMind額外用量（fetch_day_trading_info
    本身1次呼叫，加上fetch_price_hist抓當日總成交量，這個是yfinance不吃
    FinMind額度）。

    做法：fetch_day_trading_info()拿當沖成交量，fetch_price_hist()拿當日
    總成交量（兩者單位都是「股」，跟evaluate_day_trader_ratio()要求的
    單位一致，不用轉換），呼叫evaluate_day_trader_ratio()得到判定。

    回傳一段可以直接接進note欄位的文字，任何一段抓不到資料都誠實回報
    「當沖比資料不足」，不是造假一個數字。
    """
    try:
        _dt_info = fetch_day_trading_info(symbol)
        if not _dt_info or _dt_info.get("day_trade_volume") is None:
            return "當沖比資料不足"
        _hist = fetch_price_hist(symbol)
        if _hist is None or _hist.empty:
            return "當沖比資料不足(缺當日總量)"
        _total_volume = float(_hist["Volume"].iloc[-1])
        _r = evaluate_day_trader_ratio(_dt_info["day_trade_volume"], _total_volume,
                                       cold_threshold=30.0, hot_threshold=50.0)
        # 【依總指揮官提供的實戰門檻】50~60%以上代表短線客在對作——這裡
        # hot_threshold改成50(不是核心因子evaluate_day_trader_ratio原本
        # 校準給「投機過熱主力易出貨」判斷用的40)，因為候選池標記的目的
        # 是「當沖機會大」，跟核心因子判斷「主力出貨風險」的門檻嚴格度
        # 不必然相同，這裡刻意調整成總指揮官這次提供的50這個更貼近
        # 「熱門當沖標的」語意的門檻。
        if _r["verdict"] == "unknown":
            return "當沖比資料不足"
        return f"當沖比{_r['ratio_pct']}%" + ("(⚠️短線客對作熱區)" if _r["ratio_pct"] and _r["ratio_pct"] > 50 else "")
    except Exception as e:
        print(f"[候選池-當沖比] {symbol} 查詢失敗：{type(e).__name__}: {e}")
        return "當沖比查詢失敗"


def run_ai_commentary_for_picks(picks, name_map=None, direction_key='direction', default_direction='long'):
    """
    【R97新增，見開發歷程.md「NVIDIA AI推演接進排程」章節】對最終候選/選股
    結果逐一產生NVIDIA AI戰略推演文字，只對「最終結果」呼叫（stage_signal
    的longs+shorts、candidate pool的最終pool_rows），不是對Stage0b/Stage2
    篩選過程中所有候選都呼叫——理由跟day_trader_ratio標記那次一樣，控制
    額外API成本，NVIDIA也是按用量計費，不該對還沒確定要用的候選浪費呼叫。

    picks：list of dict，每個dict至少要有symbol/score等
    compute_full_signal_for()回傳格式的欄位（因為這個函式的回傳已經在
    R97補上了AI推演需要的欄位）。

    name_map：symbol -> 中文名稱的對照表，沒有的話AI prompt裡的名稱會
    直接用代號，不會報錯，只是文字沒那麼友善。

    回傳 {symbol: ai_text} 的dict，任何一檔AI呼叫失敗都不影響其他檔，
    也不影響呼叫端原本的選股/候選池邏輯——AI推演失敗只是少一段文字，
    不該讓整個排程因此掛掉。

    【R97續14優化，總指揮官實測回報：build_intraday_pool單次執行658.4秒
    耗在「其餘含AI推演等」，根因是這裡原本逐檔序列呼叫(for p in picks)，
    每檔call_ai_models_parallel(timeout=30)——跨模型那層已經用
    ThreadPoolExecutor平行(見warroom_core.py)，但跨股票這層仍是序列，
    15檔候選池遇到部分模型變慢/fallback，累加起來就是10分鐘級。
    這裡改成跨股票也用ThreadPoolExecutor平行，AI_COMMENTARY_MAX_WORKERS
    (預設5)——不設太高是刻意的：NVIDIA NIM/免費額度對短時間內大量並發
    請求可能有自己的限流，5個並發已經能把15檔的總耗時從「15×平均秒數」
    壓到接近「3輪×平均秒數」，同時不會一次炸出15個並發請求去賭對方
    限流門檻在哪裡。
    """
    if not NVIDIA_API_KEY:
        print("[AI推演] 未配置 NVIDIA_API_KEY，本次跳過所有AI推演（不影響選股/候選池本身）。")
        return {}
    name_map = name_map or {}
    results = {}

    def _run_one(p):
        sym = p.get("symbol")
        if not sym:
            return None, None
        _direction = p.get(direction_key, default_direction)
        _card = dict(p)
        _card.setdefault("code", sym)
        _card["name"] = name_map.get(sym, sym)
        try:
            system_prompt, user_prompt = build_ai_strategy_prompt(_card, direction=_direction)
            ok, result = call_ai_models_parallel(system_prompt, user_prompt, NVIDIA_API_KEY,
                                                 models=NIM_FALLBACK_MODELS, timeout=30)
            return sym, (result if ok else f"AI推演失敗：{result}")
        except Exception as e:
            print(f"[AI推演] {sym} 呼叫失敗（不影響選股/候選池結果）：{type(e).__name__}: {e}")
            return sym, None

    _max_workers = int(os.environ.get("AI_COMMENTARY_MAX_WORKERS") or "5")
    with concurrent.futures.ThreadPoolExecutor(max_workers=_max_workers) as executor:
        futures = [executor.submit(_run_one, p) for p in picks]
        for future in concurrent.futures.as_completed(futures):
            sym, text = future.result()
            if sym:
                results[sym] = text
    return results


def stage_build_intraday_pool(sb):
    """
    【R97新增，見開發歷程.md「候選池篩選架構」章節】為09:24-10:00的5分K
    三關輪詢，自動產生候選池，不再只依賴手動持倉/雷達清單。建議排程時間
    09:15（開盤後15分鐘，供最後一步的盤中補位掃描用今天真實的開盤走勢）。

    三層篩選（依總指揮官確認的完整設計）：
      Stage0a 成交值粗篩：fetch_market_turnover_ranking_with_value()，
        只取上市（總指揮官確認：上櫃流通性不足、波動異常，先不看），
        取成交值前STAGE0A_TOP檔，零額外API成本（bulk端點）。
      Stage0b 區間週轉率細篩：對Stage0a結果逐檔算compute_interval_turnover
        （近10天成交金額/市值），取週轉率前STAGE0B_TOP檔，>50%標記過熱
        （標記不排除，見這輪討論結論——排除會把最熱動能股踢掉）。
      Stage2 系統A評分篩選：對Stage0b結果逐檔跑compute_full_signal_for，
        score>=6歸類多方候選、score<=-6歸類空方候選（門檻比照stage_signal
        的嚴格度——見這輪討論確認：候選池最終會餵給真的會自動下單的
        當沖執行流程，不是網頁版單純「追蹤觀察」的寬鬆情境，該用嚴格門檻）。
      補位掃描：Stage0b篩過、但Stage2沒選中的股票，用fetch_twse_mis_batch
        查「今天」開盤後的漲跌幅，abs(change_pct)>=SUPPLEMENT_GAIN_PCT_MIN
        的補進候選池——解決「昨天普通、今天才轉強」的黑馬會被Stage2嚴格
        門檻漏掉的問題（見這輪討論的解法）。

    最終候選池 = Stage2篩選結果 ∪ 補位掃描結果，寫入intraday_candidate_pool
    表（trade_date, symbol, direction, source, score, turnover_pct,
    overheated, note）。stage_intraday_kbar()會讀這張表併入輪詢清單，
    跟手動持倉/雷達清單取聯集（手動清單優先權更高，不受這裡的門檻限制）。

    這裡的門檻/規模全部用具名常數放在函式開頭，方便總指揮官之後調整不用
    重新設計程式碼結構。
    """
    # 【R97續3修復，見開發歷程.md「候選池rate_limited排查」章節最終結論】
    # get_fm_real_quota_status()之前失敗，總指揮官這輪抓到根本原因：函式
    # 本身沒帶正常瀏覽器身分(User-Agent)被FinMind端點擋掉，跟token/認證
    # 方式無關，已經修好、改用_SESSION發送。現在重新接回真實額度查詢，
    # 開跑前先知道真實剩餘多少，動態決定Stage0b/Stage2能處理幾檔，不用
    # 再只靠寫死的50/30這組經驗值。
    # STAGE0A_TOP/STAGE0B_TOP維持50/30當上限保底（就算真實額度顯示綽綽
    # 有餘，也不無限擴大，避免單次執行時間拖太長），真實額度查詢只在
    # 額度明顯不夠時才往下砍，不會讓額度查詢本身變成「越查越大」的理由。
    # 【R97補做，見開發歷程.md】原本寫死在程式碼裡，總指揮官之後想調整
    # 規模需要改程式碼重新部署——這次改成讀system_config，之後直接在
    # Supabase改一個數字就能調，不用重新部署。找不到設定值時用現在驗證
    # 過穩定的50/30當預設值。
    STAGE0A_TOP = int(get_config(sb, "intraday_pool_stage0a_top", 50))
    STAGE0B_TOP = int(get_config(sb, "intraday_pool_stage0b_top", 30))
    _t_func_start = time.time()   # 【R97續10新增】整段執行時間的起點
    reset_snapshot_cache_counters()   # 【R97續10新增】歸零快取命中統計，這次執行重新算
    TURNOVER_DAYS = 10         # 區間週轉率的天數視窗
    SUPPLEMENT_GAIN_PCT_MIN = 5.0   # 補位掃描：今日漲跌幅絕對值達此門檻才補進
    # 【R97修復，見開發歷程.md「候選池rate_limited排查」章節】總指揮官實測
    # 回報：Stage2 60檔每一檔的FinMind呼叫全數rate_limited，但照文件寫的
    # 每組帳號600次/小時額度計算，2組會員+1訪客(1500次/小時)理論上只用到
    # 這次候選池總用量(380次)的25%，遠遠沒有打滿。這代表真正卡住的不是
    # 「總額度不夠」，是短時間內連續發送請求撞到文件沒寫的瞬間流量限制
    # (burst limit)——加帳號在這種情況下效果有限，因為現在的輪替邏輯是
    # 「有幾組帳號就快速輪流打」，一樣會在短時間內把每一組都連續打過一輪。
    # 這裡在每次FinMind呼叫之間加一個小間隔，拉開請求密度，這是比「多申請
    # 帳號」更直接對症的解法（帳號數量沒變，但不會再短時間內連續轟炸）。
    FINMIND_CALL_PACING_SEC = 0.5

    run_date = datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d")

    # ---------- Stage0a：成交值粗篩（只看上市，零額外API成本） ----------
    try:
        _ranked = fetch_market_turnover_ranking_with_value()
    except Exception as e:
        print(f"[候選池] Stage0a成交值排行抓取失敗，本次跳過候選池產生：{e}")
        return
    _twse_ranked = [(code, val) for code, val, ex in _ranked if ex == 'twse']
    stage0a_codes = [code for code, _val in _twse_ranked[:STAGE0A_TOP]]
    if not stage0a_codes:
        print("[候選池] Stage0a沒有抓到任何上市成交值資料，本次跳過候選池產生"
              "（不影響stage_intraday_kbar的手動清單這條主路徑）。")
        return
    print(f"[候選池] Stage0a：全市場成交值排行(僅上市)取前{len(stage0a_codes)}檔。")
    # 【R97續10新增，總指揮官要求：不要用猜的，程式碼自己把每段花多久
    # 印出來】分段計時，下次執行完直接從log看時間花在哪一段，不用再
    # 靠人工比對log行數猜測。
    _t_stage0a_done = time.time()

    # 【R97續3新增，總指揮官確認：真實額度查詢已修好，重新接回】開跑前先
    # 查一次FinMind真實剩餘額度，動態決定Stage0b能處理幾檔。_real_remaining
    # 是None代表查詢機制本身失敗（不是真的沒額度），這種情況維持
    # STAGE0A_TOP/STAGE0B_TOP的既有規模正常跑，不會誤判成0而砍光候選池
    # ——這是R97續1那次教訓學到的防呆，繼續保留。
    _quota_check = get_fm_real_quota_status()
    _real_remaining = _quota_check["total_remaining"]
    for _t in _quota_check["tokens"]:
        _note = _t.get("note", "")
        print(f"[候選池] FinMind真實額度：已用{_t.get('used')}/{_t.get('limit')}，"
              f"剩餘{_t.get('remaining')}" + (f"（{_note}）" if _note else ""))
    if _real_remaining is None:
        print("[候選池] FinMind真實額度查詢機制本身失敗（詳情見上面log），"
              "無法判斷真實剩餘額度，維持STAGE0A_TOP/STAGE0B_TOP既有規模正常執行。")
    else:
        # Stage0b每檔2次呼叫(股本+價量歷史)，保留20次緩衝不要用到見底
        _affordable0b = max(0, (_real_remaining - 20) // 2)
        if _affordable0b < len(stage0a_codes):
            print(f"[候選池] FinMind真實剩餘額度{_real_remaining}，Stage0b只夠處理約"
                  f"{_affordable0b}檔（原本{len(stage0a_codes)}檔），依成交值高低只取前"
                  f"{_affordable0b}檔。")
            stage0a_codes = stage0a_codes[:_affordable0b]

    # ---------- Stage0b：區間週轉率細篩 ----------
    turnover_info = {}   # code -> compute_interval_turnover()結果
    for code in stage0a_codes:
        try:
            turnover_info[code] = compute_interval_turnover(code, days=TURNOVER_DAYS, sb=sb)
        except Exception as e:
            print(f"[候選池] {code} 區間週轉率計算失敗：{type(e).__name__}: {e}")
        # 【R97續8，總指揮官確認：這裡刻意維持無條件延遲，不要學股本快取
        # 那樣做條件式跳過】compute_interval_turnover內部還有
        # fetch_stock_price_and_value_history（價量歷史）這支完全沒有
        # 快取、每次都真的打FinMind，只有股本那半邊現在有快取。這個延遲
        # 保護的是價量歷史那支，不能拿掉，拿掉會有重新撞burst limit的風險。
        time.sleep(FINMIND_CALL_PACING_SEC)
    scored = [(code, info) for code, info in turnover_info.items()
             if info.get("turnover_pct") is not None]
    scored.sort(key=lambda x: x[1]["turnover_pct"], reverse=True)
    stage0b_codes = [code for code, _info in scored[:STAGE0B_TOP]]
    _overheated_count = sum(1 for c in stage0b_codes if turnover_info[c]["overheated"])
    print(f"[候選池] Stage0b：{len(stage0a_codes)}檔算出區間週轉率{len(scored)}檔，"
          f"取前{len(stage0b_codes)}檔進系統A評分（其中{_overheated_count}檔標記⚠️過熱)。")

    # 【R98新增，總指揮官方案二拍板】把Stage0b通過週轉率細篩的股票累積寫入
    # turnover_universe，作為「過去365天內曾符合週轉率條件」的持久化名單，
    # 供get_broker_flows_target_symbols()當第五個來源使用。用upsert而非
    # insert：已存在的symbol只更新last_qualified_date+qualified_count遞增，
    # 不重複造成第一次通過日期(first_qualified_date)被覆蓋掉。這裡刻意
    # 不做「淘汰」邏輯（365天沒再符合才淘汰）——淘汰是查詢時用
    # last_qualified_date篩選即可達成的效果，不需要額外的刪除排程，避免
    # 又新增一個要維護的刪除邏輯（比照broker_flows表本身有獨立的
    # _cleanup_old_broker_flows，這裡刻意不重複造一個類似的清理階段，
    # 讀取端用WHERE last_qualified_date >= 今天-365天 就能達到同樣效果）。
    if stage0b_codes:
        try:
            _existing_res = (sb.table("turnover_universe").select("symbol, qualified_count")
                              .in_("symbol", stage0b_codes).execute())
            _existing = {r["symbol"]: r.get("qualified_count", 1) for r in (_existing_res.data or [])}
            _tu_rows = [{
                "symbol": code,
                "first_qualified_date": run_date if code not in _existing else None,
                "last_qualified_date": run_date,
                "qualified_count": _existing.get(code, 0) + 1,
            } for code in stage0b_codes]
            # first_qualified_date=None的新symbol要補上run_date，已存在的
            # 則不動它原本的first_qualified_date（upsert只更新有帶值的欄位，
            # 這裡用兩批處理：新symbol帶完整3欄位，舊symbol只更新後兩欄）
            _new_rows = [r for r in _tu_rows if r["symbol"] not in _existing]
            _old_rows = [{"symbol": r["symbol"], "last_qualified_date": r["last_qualified_date"],
                          "qualified_count": r["qualified_count"]}
                         for r in _tu_rows if r["symbol"] in _existing]
            for r in _new_rows:
                r["first_qualified_date"] = run_date
            if _new_rows:
                sb.table("turnover_universe").upsert(_new_rows, on_conflict="symbol").execute()
            for r in _old_rows:
                sb.table("turnover_universe").update({
                    "last_qualified_date": r["last_qualified_date"],
                    "qualified_count": r["qualified_count"],
                }).eq("symbol", r["symbol"]).execute()
            print(f"[候選池-週轉率宇宙] 本次{len(stage0b_codes)}檔中，"
                  f"新增{len(_new_rows)}檔、更新{len(_old_rows)}檔進turnover_universe。")
        except Exception as e:
            print(f"[候選池-週轉率宇宙] 寫入turnover_universe失敗（不影響候選池本身結果）："
                  f"{type(e).__name__}: {e}")
    _t_stage0b_done = time.time()
    print(f"[候選池-計時] Stage0a耗時{_t_stage0a_done - _t_func_start:.1f}秒／"
          f"Stage0b耗時{_t_stage0b_done - _t_stage0a_done:.1f}秒"
          f"（這段是50檔逐檔算週轉率，如果還是很慢，代表snapshot快取"
          f"沒生效、還在逐檔打FinMind，要往這個方向查）")

    # ---------- Stage2：系統A評分，門檻比照stage_signal(±6) ----------
    # 【R97續3修復，見開發歷程.md最終結論】真實額度查詢已修好（根因是
    # 沒帶正常瀏覽器身分被FinMind端點擋掉，不是token問題），開跑前再查
    # 一次，這次算的是Stage2的成本（每檔3次：法人買賣超+融資+營收）。
    # 「連續N檔都是空結果」的偵測繼續保留當第二道安全網，處理額度查詢
    # 本身查完之後、跑到一半才被其他行程搶走額度的情況。
    FINMIND_COST_PER_STAGE2_STOCK = 3
    _quota_check2 = get_fm_real_quota_status()
    _real_remaining2 = _quota_check2["total_remaining"]
    if _real_remaining2 is None:
        print("[候選池] FinMind真實額度查詢機制本身失敗（詳情見上面log），"
              "無法判斷真實剩餘額度，Stage2維持既有規模正常執行。")
    else:
        _affordable2 = max(0, (_real_remaining2 - 20) // FINMIND_COST_PER_STAGE2_STOCK)
        if _affordable2 < len(stage0b_codes):
            print(f"[候選池] FinMind真實剩餘額度{_real_remaining2}，Stage2只夠評分約"
                  f"{_affordable2}檔（原本{len(stage0b_codes)}檔），依區間週轉率高低只取前"
                  f"{_affordable2}檔，其餘下次執行再處理。")
            stage0b_codes = stage0b_codes[:_affordable2]

    RATE_LIMIT_STREAK_STOP = 8
    pool_rows = []
    long_codes, short_codes, stage2_reject_codes = [], [], []
    _consecutive_no_data = 0
    _stage2_early_stop = False
    for code in stage0b_codes:
        try:
            sig = compute_full_signal_for(code, sb=sb)
        except Exception as e:
            print(f"[候選池] {code} 系統A評分失敗：{type(e).__name__}: {e}")
            time.sleep(FINMIND_CALL_PACING_SEC)
            continue
        if not sig:
            continue
        # 【R97新增，反應式額度保護，總指揮官要求：不用預測，用真實發生的
        # 事實反應】compute_full_signal_for真的偵測到FinMindAPIError
        # (rate_limited)才會回傳finmind_rate_limited=True——這比下面
        # 「連續N檔空結果」的間接推測更直接、更快，一偵測到就立刻停止，
        # 不用再等湊滿8檔才確認。
        if sig.get("finmind_rate_limited"):
            print(f"[候選池] {code} 真的偵測到FinMind rate_limited（不是猜測），"
                  f"立即停止Stage2剩餘評分，已處理{stage0b_codes.index(code) + 1}/"
                  f"{len(stage0b_codes)}檔，其餘下次執行再處理。")
            _stage2_early_stop = True
            break
        # 【判斷是不是額度耗盡】sig存在但score剛好等於0、且完全沒有reasons
        # (代表所有因子都因為缺資料沒觸發)，是額度被打滿的典型症狀——
        # 連續出現太多次就代表額度真的用盡了，不是個別股票剛好沒訊號。
        if sig.get("score") == 0 and not sig.get("reasons"):
            _consecutive_no_data += 1
        else:
            _consecutive_no_data = 0
        if _consecutive_no_data >= RATE_LIMIT_STREAK_STOP:
            print(f"[候選池] 連續{_consecutive_no_data}檔評分都是空結果，研判FinMind額度"
                  f"已經用盡，提早停止Stage2（剩餘{len(stage0b_codes) - stage0b_codes.index(code) - 1}"
                  f"檔這次不評分，下次執行再處理，避免浪費時間硬跑到底）。")
            _stage2_early_stop = True
            break
        _info = turnover_info.get(code, {})
        if sig["score"] >= 6:
            long_codes.append(code)
            _dt_note = _get_day_trader_tag(code)
            pool_rows.append({
                "trade_date": run_date, "symbol": code, "direction": "long", "source": "turnover_score",
                "score": sig["score"], "turnover_pct": _info.get("turnover_pct"),
                "overheated": bool(_info.get("overheated")),
                "note": f"系統A={sig['score']}(≥6多方候選)，{_info.get('note', '')}，{_dt_note}",
            })
        elif sig["score"] <= -6:
            short_codes.append(code)
            _dt_note = _get_day_trader_tag(code)
            pool_rows.append({
                "trade_date": run_date, "symbol": code, "direction": "short", "source": "turnover_score",
                "score": sig["score"], "turnover_pct": _info.get("turnover_pct"),
                "overheated": bool(_info.get("overheated")),
                "note": f"系統A={sig['score']}(≤-6空方候選)，{_info.get('note', '')}，{_dt_note}",
            })
        else:
            stage2_reject_codes.append(code)
        time.sleep(FINMIND_CALL_PACING_SEC)   # 【R97新增】拉開請求間隔，避免撞burst limit
    print(f"[候選池] Stage2：系統A評分完成，多方候選{len(long_codes)}檔／"
          f"空方候選{len(short_codes)}檔／未達門檻{len(stage2_reject_codes)}檔"
          + ("（因額度用盡提早停止）" if _stage2_early_stop else ""))
    _t_stage2_done = time.time()
    print(f"[候選池-計時] Stage2耗時{_t_stage2_done - _t_stage0b_done:.1f}秒"
          f"（這段是30檔逐檔跑完整評分含法人/融資/PE/營收/月營收，"
          f"如果還是很慢，同樣代表snapshot快取沒生效）")
    if _stage2_early_stop:
        notify_telegram(f"⚠️ [{run_date}] 候選池Stage2因FinMind額度用盡提早停止，"
                        f"只評分了{len(long_codes) + len(short_codes) + len(stage2_reject_codes)}/"
                        f"{len(stage0b_codes)}檔。已加入請求間隔緩解，若持續發生建議調小"
                        f"STAGE0A_TOP/STAGE0B_TOP降低單次執行的API用量。")

    # ---------- 補位掃描：Stage0b篩過但Stage2沒選中的，用今天開盤走勢補位 ----------
    supplement_codes = []
    if stage2_reject_codes:
        try:
            _pairs = [(c, 'tse') for c in stage2_reject_codes]
            _quotes = fetch_twse_mis_batch(_pairs)
            for code in stage2_reject_codes:
                q = _quotes.get(code)
                if not q or q.get("change_pct") is None:
                    continue
                _chg = q["change_pct"]
                if abs(_chg) >= SUPPLEMENT_GAIN_PCT_MIN:
                    _direction = "long" if _chg > 0 else "short"
                    supplement_codes.append(code)
                    _info = turnover_info.get(code, {})
                    pool_rows.append({
                        "trade_date": run_date, "symbol": code, "direction": _direction, "source": "momentum_supplement",
                        "score": None, "turnover_pct": _info.get("turnover_pct"),
                        "overheated": bool(_info.get("overheated")),
                        "note": f"昨日評分未達門檻，但今日開盤漲跌幅{_chg}%達補位條件"
                               f"(≥{SUPPLEMENT_GAIN_PCT_MIN}%)，補進候選池。",
                    })
        except Exception as e:
            print(f"[候選池] 補位掃描失敗（不影響前面Stage0/Stage2的結果）：{type(e).__name__}: {e}")
    print(f"[候選池] 補位掃描：{len(stage2_reject_codes)}檔重新檢查今日開盤走勢，"
          f"{len(supplement_codes)}檔補進候選池。")
    _t_supplement_done = time.time()
    print(f"[候選池-計時] 補位掃描耗時{_t_supplement_done - _t_stage2_done:.1f}秒"
          f"（單一批次即時報價查詢，正常應該在幾秒內完成，如果這段很慢，"
          f"代表fetch_twse_mis_batch本身卡住，不是FinMind問題，要往這個"
          f"方向查——跟z欄位='-'那些診斷log是不是同一批一起看）")

    # ---------- 事件驅動過濾：十大會影響股價事件，標記+否決並用 ----------
    # 【R97新增，見開發歷程.md「事件驅動評分系統」章節】對這批已經篩出來
    # 的最終候選（不是對Stage0b全部30檔），查TWSE重大訊息公告，命中
    # 否決類事件(增資減資/募資計劃/經營權之爭併購/內部人買賣)的直接排除，
    # 命中標記類事件(股東會/法說會/股利政策/除權息/月營收/季報)的只在
    # note加註提醒，不排除。零額外FinMind成本——這支是TWSE自己的
    # openapi端點，不計入FinMind額度。
    try:
        _final_codes = {r["symbol"] for r in pool_rows}
        _announcements = fetch_twse_material_announcements()
        _event_map = classify_material_announcements(_announcements, tracked_symbols=_final_codes,
                                                      reference_date=run_date) if _announcements else {}
    except Exception as e:
        print(f"[候選池-事件過濾] 查詢重大訊息失敗（不影響前面篩選結果，本次跳過事件過濾）：{e}")
        _event_map = {}

    if _event_map:
        _vetoed_codes = set()
        for row in pool_rows:
            _code = row["symbol"]
            _events = _event_map.get(_code)
            if not _events:
                continue
            if _events["veto"]:
                _vetoed_codes.add(_code)
                print(f"[候選池-事件過濾] {_code} 命中否決類事件，排除：{_events['veto']}")
            elif _events["tag"]:
                row["note"] = row["note"] + f"，⚠️事件標記：{'；'.join(_events['tag'])}"
        if _vetoed_codes:
            pool_rows = [r for r in pool_rows if r["symbol"] not in _vetoed_codes]
            long_codes = [c for c in long_codes if c not in _vetoed_codes]
            short_codes = [c for c in short_codes if c not in _vetoed_codes]
            print(f"[候選池-事件過濾] 共 {len(_vetoed_codes)} 檔因重大事件被排除：{sorted(_vetoed_codes)}")
            notify_telegram(f"🚨 [{run_date}] 候選池事件過濾：{len(_vetoed_codes)} 檔因重大事件"
                            f"(增資/併購/經營權/內部人買賣等)被排除，不進候選池：{sorted(_vetoed_codes)}")

    # 【R97新增，見開發歷程.md「NVIDIA AI推演接進排程」章節】只對最終候選池
    # （通常10幾檔內）呼叫AI推演，不是對Stage0b/Stage2篩選過程中的候選呼叫。
    # 這裡沒有另外抓中文名稱對照表(name_map)——候選池規模已經控制在小範圍，
    # 多一次批次抓名稱的API成本不划算，AI prompt沒有中文名稱時會直接用
    # 代號，不影響推演本身的判斷內容。
    if pool_rows:
        _ai_reports_pool = run_ai_commentary_for_picks(pool_rows)
        for row in pool_rows:
            _ai_text = _ai_reports_pool.get(row["symbol"])
            if _ai_text:
                row["note"] = row["note"] + f"｜🤖AI推演：{_ai_text[:200]}..."

    # ---------- 寫入 intraday_candidate_pool ----------
    if not pool_rows:
        print("[候選池] 本次沒有任何股票通過候選池篩選，intraday_candidate_pool"
              "今天會是空的（stage_intraday_kbar仍會用手動持倉/雷達清單繼續運作）。")
        return
    try:
        sb.table("intraday_candidate_pool").delete().eq("trade_date", run_date).execute()
        sb.table("intraday_candidate_pool").insert(pool_rows).execute()
        print(f"[候選池] 完成，共寫入 {len(pool_rows)} 檔候選"
              f"（多方{len(long_codes)}／空方{len(short_codes)}／補位{len(supplement_codes)}）。")
        print(f"[候選池-計時] 總耗時{time.time() - _t_func_start:.1f}秒"
              f"（Stage0a {_t_stage0a_done - _t_func_start:.1f}s／"
              f"Stage0b {_t_stage0b_done - _t_stage0a_done:.1f}s／"
              f"Stage2 {_t_stage2_done - _t_stage0b_done:.1f}s／"
              f"補位掃描 {_t_supplement_done - _t_stage2_done:.1f}s／"
              f"其餘含AI推演等 {time.time() - _t_supplement_done:.1f}s）")
        _cache_stats = get_snapshot_cache_counters()
        print(f"[候選池-快取命中率] 價量:{_cache_stats['price_value_hit']}命中/"
              f"{_cache_stats['price_value_miss']}退回FinMind／"
              f"股本:{_cache_stats['shares_hit']}命中/{_cache_stats['shares_miss']}退回FinMind/"
              f"{_cache_stats.get('shares_backoff', 0)}退避跳過／"
              f"法人:{_cache_stats['institutional_hit']}命中/{_cache_stats['institutional_miss']}退回FinMind／"
              f"PE:{_cache_stats['pe_hit']}命中/{_cache_stats['pe_miss']}退回FinMind／"
              f"營收:{_cache_stats['revenue_hit']}命中/{_cache_stats['revenue_miss']}退回FinMind"
              f"（退回FinMind次數多，就是Stage0b/Stage2慢的直接根因，不用再猜）")
        sb.table("system_run_log").insert({
            "run_date": run_date, "stage": "build_intraday_pool", "picked_count": len(pool_rows),
            "executed_count": len(long_codes) + len(short_codes), "gate_status": "normal",
            "note": f"候選池：多方{len(long_codes)}/空方{len(short_codes)}/補位{len(supplement_codes)}",
        }).execute()
    except Exception as e:
        print(f"[候選池] 寫入Supabase失敗：{e}"
              f"（可能是尚未執行相關migration建立intraday_candidate_pool表）")
        notify_telegram(f"⚠️ [{run_date}] 候選池寫入Supabase失敗，今天stage_intraday_kbar"
                        f"會退回只用手動持倉/雷達清單。錯誤內容：{e}")


def stage_intraday_kbar(sb):
    """
    【R95續28新增】自建5分K 第一階段：資料收集。9:30三關(查15)盤中策略需要
    5分鐘K棒，但FinMind官方分K資料集(TaiwanStockKBar)已確認免費帳號用不了
    (健康度檢查顯示HTTP 400 "Your level is free")。這裡改用專案已經在用、
    已經驗證過穩定的TWSE即時報價端點(fetch_twse_mis_batch)，在9:25-9:50這段
    關鍵時間窗自己反覆輪詢、組裝成5分K，存進intraday_5min_bars表。

    【R96新增第二階段】資料收集穩定運作後，這輪接上三關（查15）判斷邏輯——
    依總指揮官確認的三張參考圖設計：第一關9:30量價配合、第二關族群內個股
    強弱、第三關拉回量價（洗盤或出貨）。判斷本體在warroom_core.py的
    evaluate_930_three_gate()，這裡只負責：①額外把每檔的產業龍頭也併入
    輪詢清單（第二關要比較）②輪詢/組裝5分K結束後呼叫判斷函式③結果寫進
    新的intraday_gate_results表（見supabase_migration_r96_intraday_gate.sql）。

    【R96更新】第三關（拉回體檢）輪詢窗口已從09:51延伸到10:00（總指揮官
    依另一位操盤手的反轉機率經驗法則確認：10:00是明確檢查點，12:45太
    接近收盤(13:30)已經是尾盤階段，不採用）。10:00仍然不算長，第三關
    在窗口延伸初期可能還是常顯示「資料不足」，但已經比09:51多了近10
    分鐘的拉回觀察空間，之後可以持續觀察是否需要再延伸。

    【設計決策，見supabase_migration_r95_intraday_kbar.sql同樣的說明】
    - 只抓持倉+雷達清單，不是全市場——跟券商分點方向二同一個理由。
    - 每30秒輪詢一次，不是每5分鐘才查一次——一根5分鐘K棒內至少10次取樣
      機會，大幅降低「整根K棒開天窗」的機率（aggregate_intraday_
      snapshots_to_bars本身也已經測過這個情境）。
    - 這個排程本身要跑滿整段9:25-9:50時間窗，不是觸發一次就結束——排程
      觸發時間點抓在9:24(留1分鐘緩衝)，內部用time.sleep()跑滿整個窗口，
      不依賴GitHub Actions cron能精準到分鐘級（cron在高負載時段可能有
      幾分鐘延遲，不能靠「每分鐘觸發一次新job」這種設計）。
    - tse/otc判斷：排程端沒有網頁版那套fetch_listed_only_codes()可以用，
      這裡簡化成「每個代號同時查tse跟otc兩種組合」，哪個有回應就用哪個
      ——watchlist通常只有幾十檔，兩倍查詢量還是很小，用簡單換取穩定，
      不用另外維護一份上市/上櫃判斷邏輯。
    - 用try/finally包住整個輪詢迴圈——就算中途發生非預期例外，已經收集
      到的快照還是會被組裝、寫入，不會因為最後一刻出錯就整批作廢。

    【R95續29新增回溯驗證】總指揮官提出：與其被動等資料累積、日後才發現
    組裝邏輯有問題，不如主動拿已經可靠的日K資料交叉比對，及早抓出系統性
    錯誤。今天9:25-9:50才剛開始收集，今天的官方日K要收盤後才會定案，
    沒辦法驗證「今天」——所以改成每次執行時，先驗證「上一個交易日」已經
    收集好、而且官方日K現在已經確定的資料，驗證完再開始收集今天的。
    這樣不用另外排一個獨立的排程階段，每天執行的同時自然而然把前一天的
    資料驗證掉，異常會直接印進log（現階段先不推播Telegram，避免資料
    收集才剛上線就急著推播雜訊——先觀察log幾天，穩定後再考慮要不要推播）。
    """
    _validate_previous_trading_day(sb)

    run_date = datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d")

    # 【R97新增，總指揮官要求：09:24-10:00不能只靠單一cron觸發點，需要
    # 備援】GitHub Actions排程觸發延遲是平台層級風險，沒辦法保證09:24
    # 這個時間點絕對不會delay。解法不是「加更多主要觸發點」（那樣正常
    # 情況下反而會重複跑、產生衝突/浪費），而是「加一個備用觸發時間點
    # (09:29，見system_scheduler.yml)，觸發時先檢查今天09:24那次是否
    # 已經正常跑過」——如果今天已經有一筆輪詢次數看起來健康的紀錄，
    # 備用觸發就直接跳過，不重複執行；如果今天完全沒有紀錄、或紀錄顯示
    # 輪詢次數異常低（像前幾天的0次），備用觸發才真的接手執行，等於
    # 是「主要觸發失靈時的第二道防線」，不是無條件多跑一次。
    #
    # 【重要，避免跟測試模式互相干擾】手動測試(INTRADAY_KBAR_TEST_MINUTES
    # 有設定)時，不管今天正式排程有沒有跑過，都要強制執行——這道
    # 「今天已經跑過就跳過」的防護，只是為了避免09:24/09:29兩個正式
    # 排程觸發點互相重複，不該擋住總指揮官刻意要測試的手動觸發。
    if os.environ.get("INTRADAY_KBAR_TEST_MINUTES"):
        print("[自建5分K] 偵測到測試模式(INTRADAY_KBAR_TEST_MINUTES)，"
              "跳過「今天是否已經跑過」的備援防護檢查，強制執行本次測試。")
    else:
        try:
            # 【重要】intraday_kbar這個階段內部寫進system_run_log的stage
            # 欄位實際值是"intraday_gate"（不是"intraday_kbar"這個CLI階段
            # 名稱本身），這是既有程式碼的命名，查詢要用真正寫進去的值，
            # 不是CLI參數名稱。picked_count在這裡代表「三關判斷了幾檔」，
            # 不是輪詢次數本身，但兩者高度相關——輪詢完全失敗(像之前的
            # 0次)時，三關也會是0檔，用這個當健康度代理指標是合理的。
            _today_runs = (sb.table("system_run_log").select("picked_count, note")
                          .eq("run_date", run_date).eq("stage", "intraday_gate")
                          .execute().data) or []
            _healthy_prior_run = any(
                (r.get("picked_count") or 0) >= 3 for r in _today_runs
            )
            if _healthy_prior_run:
                print(f"[自建5分K] 今天({run_date})已經有一筆輪詢次數看起來健康的"
                      f"intraday_kbar紀錄，本次判斷是備援觸發點接手到已經正常執行過"
                      f"的情況，跳過重複執行，避免同一天重複輪詢造成資料衝突/浪費。")
                return
            if _today_runs:
                print(f"[自建5分K] 今天已有 {len(_today_runs)} 筆intraday_kbar紀錄，"
                      f"但輪詢次數都偏低（可能是09:24那次觸發delay或失敗），"
                      f"本次視為備援接手，正常繼續執行。")
        except Exception as e:
            print(f"[自建5分K] 檢查今天既有執行紀錄失敗：{e}，保守起見繼續正常執行"
                  f"（查詢本身失敗不該擋住輪詢執行）。")

    symbols = set()
    direction_of = {}   # symbol -> 'long'/'short'，供稍後三關判斷用；預設long
    try:
        res = sb.table("user_state").select("state_value").eq("state_key", "commander_main").limit(1).execute()
        if res.data:
            state = res.data[0].get("state_value", {}) or {}
            _manual_symbols = set()
            _manual_symbols.update(_clean_symbol(k) for k in (state.get("portfolio") or {}).keys())
            _manual_symbols.update(_clean_symbol(k) for k in (state.get("pinned_stocks") or {}).keys())
            symbols.update(_manual_symbols)
            for _s in _manual_symbols:
                direction_of[_s] = 'long'   # 手動持倉/雷達清單目前沒有方向欄位，預設當多方
    except Exception as e:
        print(f"[自建5分K] 讀取user_state失敗：{e}")

    # 【R97新增，見開發歷程.md候選池章節】讀取stage_build_intraday_pool
    # (08:xx跑，見該函式)產生的當日候選池——週轉率+系統A評分篩選出來的
    # 多空候選，跟手動清單取聯集，手動清單優先權更高（direction_of裡手動
    # 清單已經先設過'long'，這裡候選池的方向只在還沒設過時才補上，不會
    # 覆蓋手動清單原本的方向判斷）。
    #
    # 【R97續7修正，見對話紀錄「候選池延遲根因排查」】原本這裡的註解寫
    # 「候選池抓不到/是空的都不影響，屬於錦上添花不是必要依賴」——這句話
    # 本身沒錯（手動清單這條主路徑確實不受影響），但也正是這句話造成的
    # 沉默失敗：候選池因為排程延遲（Stage2評分卡在FinMind限流）常常
    # 晚於09:24輪詢開始時間才寫完，這裡查到空的候選池時完全沒有任何警告，
    # 導致「候選池空方候選這條路徑整段失效」這件事在正式環境裡默默發生了
    # 好幾週都沒被發現。這裡改成：查到空的候選池時，明確推播Telegram
    # 警告（不是靜默略過），讓總指揮官當天就能知道，不用等好幾週後才
    # 回頭查資料庫才發現。
    try:
        _pool_rows = (sb.table("intraday_candidate_pool").select("symbol,direction")
                      .eq("trade_date", run_date).execute().data) or []
        for _r in _pool_rows:
            _sym = _clean_symbol(_r.get("symbol"))
            if not _sym:
                continue
            symbols.add(_sym)
            if _sym not in direction_of:
                direction_of[_sym] = _r.get("direction") or "long"
        if _pool_rows:
            _pool_short_count = sum(1 for _r in _pool_rows if _r.get("direction") == "short")
            print(f"[自建5分K] 候選池併入 {len(_pool_rows)} 檔（來自stage_build_intraday_pool，"
                  f"其中空方 {_pool_short_count} 檔）。")
        else:
            # 【R98續28新增，總指揮官確認要加：自我修復機制】2026-08-27實測
            # 抓到根因：build_intraday_pool當天排定09:05執行，但GitHub
            # Actions的排程觸發本身被平台跳過了(00:19到02:57UTC之間整整
            # 2.5小時完全沒有任何run被觸發，GitHub官方文件本身就承認排程
            # 觸發在負載高時可能延遲甚至被跳過，這是平台已知限制，不是我們
            # 的程式碼問題)。與其只是發警告然後放著候選池空一整天，這裡
            # 當場自己補跑一次stage_build_intraday_pool()當自我修復——
            # 會多花約11分鐘(R97續14量測的單次執行時間)，但總比整個交易日
            # 空方候選完全掃描不到來得好。補跑完後重新查一次候選池表，
            # 補跑成功的話症狀在這次09:24輪詢當下就解決，不用等隔天。
            print(f"[自建5分K] ⚠️ 候選池是空的（trade_date={run_date} 查無資料）——"
                  f"這代表今天stage_build_intraday_pool可能還沒跑完、跑失敗，或跑得比"
                  f"這次09:24輪詢晚。啟動自我修復：當場補跑一次stage_build_"
                  f"intraday_pool()（預期約11分鐘）...")
            notify_telegram(f"⚠️ [{run_date}] 09:24三關輪詢：候選池是空的，啟動自我修復"
                            f"（當場補跑build_intraday_pool，預期約11分鐘），完成後會再"
                            f"通知結果。")
            try:
                stage_build_intraday_pool(sb)
                _retry_res = (sb.table("intraday_candidate_pool").select("symbol,direction")
                             .eq("trade_date", run_date).execute().data) or []
                for _r in _retry_res:
                    _sym = _clean_symbol(_r.get("symbol"))
                    if not _sym:
                        continue
                    symbols.add(_sym)
                    if _sym not in direction_of:
                        direction_of[_sym] = _r.get("direction") or "long"
                if _retry_res:
                    _retry_short_count = sum(1 for _r in _retry_res if _r.get("direction") == "short")
                    print(f"[自建5分K] 自我修復成功：補跑後取得{len(_retry_res)}檔"
                          f"（空方{_retry_short_count}檔），已併入這次輪詢。")
                    notify_telegram(f"✅ [{run_date}] 09:24三關輪詢自我修復成功：補跑候選池"
                                    f"取得{len(_retry_res)}檔（空方{_retry_short_count}檔），"
                                    f"已併入這次輪詢，不用人工介入。")
                else:
                    print(f"[自建5分K] 自我修復後候選池仍然是空的——這次不是排程被跳過，"
                          f"是今天真的沒有股票通過候選池的篩選門檔，屬於合理情況。")
                    notify_telegram(f"ℹ️ [{run_date}] 09:24三關輪詢：自我修復已補跑，但候選池"
                                    f"仍然是空的——這代表不是排程被跳過，是今天真的沒有股票"
                                    f"通過篩選門檻，不需要人工介入。")
            except Exception as _repair_e:
                print(f"[自建5分K] 自我修復失敗：{type(_repair_e).__name__}: {_repair_e}")
                notify_telegram(f"❌ [{run_date}] 09:24三關輪詢自我修復失敗："
                                f"{type(_repair_e).__name__}: {_repair_e}，這次輪詢仍然只會用"
                                f"手動持倉/雷達清單，麻煩人工確認build_intraday_pool的狀況。")

    except Exception as e:
        print(f"[自建5分K] 讀取intraday_candidate_pool失敗（不影響手動清單這條主路徑）：{e}"
              f"（可能是尚未執行相關migration建表，或今天candidate pool階段還沒跑）")
        notify_telegram(f"⚠️ [{run_date}] 09:24三關輪詢：讀取候選池失敗：{e}")


    # 【R97】上限從40提高到150——候選池機制上線後symbols來源不再只有
    # 手動清單，理論上限要放寬，但仍保留一個安全上限避免上游篩選出問題時
    # 拖垮整個輪詢視窗（實際數量預期會遠低於150，見候選池設計的兩層篩選）。
    symbols = sorted(symbols)[:150]

    if not symbols:
        print("[自建5分K] 持倉+雷達清單+候選池都是空的，跳過本次輪詢。")
        return

    # 【R96新增，5分K第二階段】三關第二關需要龍頭的盤中漲幅當比較基準，
    # 這裡把每檔的固定龍頭一起併入輪詢清單（同一批請求，不加開新批次）。
    _stock_to_ind, _ = fetch_industry_map_raw()
    leader_symbols = set()
    leader_of = {}   # symbol -> leader_code，供稍後三關判斷時查對照
    for s in symbols:
        _ld_code, _ld_name = get_industry_leader_for_symbol(s, _stock_to_ind)
        if _ld_code:
            leader_symbols.add(_ld_code)
            leader_of[s] = _ld_code
    all_poll_symbols = sorted(set(symbols) | leader_symbols)
    if leader_symbols:
        print(f"[自建5分K] 額外併入 {len(leader_symbols)} 檔產業龍頭一起輪詢"
              f"（供三關第二關比較用），輪詢總數 {len(all_poll_symbols)} 檔。")

    pairs = [(s, 'tse') for s in all_poll_symbols] + [(s, 'otc') for s in all_poll_symbols]

    print(f"[自建5分K] 對 {len(all_poll_symbols)} 檔股票開始輪詢，預計跑到約10:00（每30秒一次）...")
    snapshots = []
    _poll_count = 0
    # 【R96修復，見開發歷程.md時區bug章節】改用datetime.now(TAIPEI_TZ)，
    # 結束時間延伸到10:00（總指揮官確認的反轉機率經驗法則檢查點）。
    _end_time = dt_time(10, 0, 0)

    # 【R97新增，總指揮官要求：不想每次都要等明天09:24-10:00這個窄窗口
    # 才能測試完整的多次輪詢流程】讀環境變數INTRADAY_KBAR_TEST_MINUTES，
    # 有設定時（例如手動觸發時在GitHub Actions workflow_dispatch的
    # env裡臨時加這個變數，或直接在repo的Variables設定），改成「從現在
    # 開始跑N分鐘」，不管現在是幾點，都能立刻測試完整的多次輪詢→組K棒
    # →三關判斷這條完整鏈路，不用受限於必須是09:24-10:00這段真實窗口，
    # 也不用等到明天。正式排程(cron)沒有設定這個環境變數時，行為完全
    # 不變，還是照原本09:24觸發、跑到10:00為止的邏輯。
    _test_minutes = os.environ.get("INTRADAY_KBAR_TEST_MINUTES")
    _actual_start = datetime.now(TAIPEI_TZ)
    _is_test_mode = False
    if _test_minutes:
        try:
            _test_minutes_f = float(_test_minutes)
            _end_time = (_actual_start + timedelta(minutes=_test_minutes_f)).time()
            _is_test_mode = True
            print(f"[自建5分K] 🧪 測試模式啟動（INTRADAY_KBAR_TEST_MINUTES={_test_minutes}）："
                  f"不使用正式的10:00截止時間，改成從現在開始跑{_test_minutes}分鐘就結束，"
                  f"目的是讓總指揮官不用等明天09:24-10:00這個窄窗口，現在（只要是盤中，"
                  f"即時報價端點有資料）就能測試完整的多次輪詢→組5分K→三關判斷這條鏈路。"
                  f"正式排程(cron)不會設定這個環境變數，行為不受影響。")
        except ValueError:
            print(f"[自建5分K] INTRADAY_KBAR_TEST_MINUTES='{_test_minutes}'不是有效數字，忽略，"
                  f"維持正式的10:00截止時間。")

    # 【R97修復，總指揮官實測回報：今天輪詢「共0次」，總耗時只有25秒】
    # 根因是GitHub Actions排程觸發時間跟工作「真正開始執行」的時間可能
    # 有延遲——這是GitHub官方文件記載過的已知限制，系統負載高時排程
    # 觸發可能明顯延後。原本的迴圈邏輯是「進迴圈先檢查現在時間有沒有
    # 超過10:00，超過就直接跳出」，如果整個工作因為排隊delay到啟動時
    # 已經過了10:00，迴圈會一次都沒真的輪詢就直接結束，變成0筆資料——
    # 這正是今天發生的情況。這裡先把「真正開始執行的時間」印出來，以後
    # 不用再猜是不是delay造成的；同時把邏輯改成「先做一次輪詢，再檢查
    # 時間」（do-while），就算真的晚啟動，至少能拿到一次快照，不會變成
    # 完全零筆資料。
    if not _is_test_mode and _actual_start.time() >= _end_time:
        print(f"[自建5分K] ⚠️ 警告：實際開始執行時間是 {_actual_start.strftime('%H:%M:%S')}，"
              f"已經超過預定結束時間10:00——這代表GitHub Actions排程觸發延遲了"
              f"（cron設定09:24觸發，但工作真正開始跑的時間明顯晚於這個時間點）。"
              f"這不是程式邏輯的bug，是GitHub Actions排程佇列延遲的已知限制。"
              f"下面仍會強制跑至少一次輪詢，盡量拿到一筆快照，不會完全零資料，"
              f"但資料品質會比正常情況差很多。")

    try:
        while True:
            _poll_time_str = datetime.now(TAIPEI_TZ).strftime('%H:%M:%S')
            try:
                live = fetch_twse_mis_batch(pairs)
            except Exception as e:
                print(f"[自建5分K] {_poll_time_str} 輪詢失敗：{e}")
                live = {}
            _poll_count += 1
            for sym in all_poll_symbols:
                q = live.get(sym)
                snapshots.append({
                    'symbol': sym, 'poll_time': _poll_time_str,
                    'price': q.get('price') if q else None,
                    'volume_cum': q.get('volume_cum') if q else None,
                    # 【R96新增，內外盤成交比率】fetch_twse_mis_batch本來
                    # 就會回傳bids/asks，一併存進快照供tick rule分類，不多打API。
                    'bids': q.get('bids') if q else None,
                    'asks': q.get('asks') if q else None,
                })
            # 【R97修復】原本這個判斷在while迴圈開頭（進迴圈前就檢查），
            # 現在移到「做完至少一次輪詢之後」才檢查要不要結束——這是
            # 「先做一次輪詢、再檢查時間」的do-while寫法，保證至少跑一次。
            _now = datetime.now(TAIPEI_TZ).time()
            if _now >= _end_time:
                break
            time.sleep(30)
    except Exception as e:
        print(f"[自建5分K] 輪詢迴圈中途發生例外：{type(e).__name__}: {e}——"
              f"已收集到的{_poll_count}次快照仍會嘗試組裝寫入，不整批作廢。")
    finally:
        print(f"[自建5分K] 輪詢結束，共{_poll_count}次，開始組裝5分K並寫入Supabase...")
        bars_by_symbol = aggregate_intraday_snapshots_to_bars(snapshots, bar_minutes=5)
        _total_bars = 0
        for sym, bars in bars_by_symbol.items():
            if not bars:
                continue
            rows = [{
                'symbol': sym, 'trade_date': run_date, 'bar_time': b['bar_time'],
                'open': b['open'], 'high': b['high'], 'low': b['low'], 'close': b['close'],
                'volume': b['volume'], 'sample_count': b['sample_count'],
                # 【R96新增，內外盤成交比率】需先執行supabase_migration_
                # r96_outer_inner_volume.sql，欄位還沒建立前upsert會失敗
                # （下面except會接住，不中止整批寫入）。
                'outer_volume': b.get('outer_volume', 0.0), 'inner_volume': b.get('inner_volume', 0.0),
            } for b in bars]
            try:
                sb.table("intraday_5min_bars").upsert(
                    rows, on_conflict="symbol,trade_date,bar_time").execute()
                _total_bars += len(rows)
            except Exception as e:
                print(f"[自建5分K] {sym} 寫入失敗：{e}"
                     f"（可能是尚未執行supabase_migration_r95_intraday_kbar.sql"
                     f"或supabase_migration_r96_outer_inner_volume.sql建表/加欄位）")
        print(f"[自建5分K] 完成，共寫入 {_total_bars} 根K棒（{len(symbols)}檔股票，"
              f"理論上限每檔5根，實際根數視輪詢期間有沒有抓到有效樣本而定）。")

        # 【R96新增，5分K第二階段】三關判斷只對持倉+雷達的symbols跑，
        # 龍頭symbols只當比較基準。直接用記憶體裡的bars_by_symbol，
        # 不重查Supabase。
        print("[自建5分K] 開始跑9:30三關（查15）判斷...")
        _gate_results, _gate_pass, _gate_fail = [], 0, 0
        for sym in symbols:
            stock_bars = bars_by_symbol.get(sym, [])
            if not stock_bars:
                continue
            _leader_code = leader_of.get(sym)
            leader_bars = bars_by_symbol.get(_leader_code, []) if _leader_code else None
            _direction = direction_of.get(sym, 'long')
            # 【R97】空方需要日K做防接刀位置檢查(evaluate_short_position_
            # precheck)，多方不需要——只在空方時才多打這次查詢，控制成本。
            _daily_hist = None
            if _direction == 'short':
                try:
                    _daily_hist = fetch_price_hist(sym)
                except Exception as _e:
                    print(f"[自建5分K三關] {sym} 空方防接刀查日K失敗，本次跳過位置檢查："
                          f"{type(_e).__name__}: {_e}")
            try:
                verdict = evaluate_930_three_gate(stock_bars, leader_bars,
                                                  direction=_direction, daily_hist=_daily_hist)
            except Exception as e:
                print(f"[自建5分K三關] {sym} 判斷失敗：{type(e).__name__}: {e}")
                continue
            _gate_results.append({
                'symbol': sym, 'trade_date': run_date, 'direction': _direction,
                'overall_verdict': verdict['overall_verdict'],
                'overall_label': verdict['overall_label'],
                'gate1_verdict': verdict['gate1']['verdict'] if verdict.get('gate1') else None,
                'gate2_verdict': verdict['gate2']['verdict'] if verdict.get('gate2') else None,
                'gate3_verdict': verdict['gate3']['verdict'] if verdict.get('gate3') else None,
                'detail': verdict,
            })
            if verdict['overall_verdict'] == 'pass':
                _gate_pass += 1
            elif verdict['overall_verdict'] == 'fail':
                _gate_fail += 1
        if _gate_results:
            try:
                sb.table("intraday_gate_results").upsert(
                    _gate_results, on_conflict="symbol,trade_date,direction").execute()
                print(f"[自建5分K三關] 完成，{len(_gate_results)}檔已判斷"
                      f"（合格{_gate_pass}／不合格{_gate_fail}／其餘資料不足待觀察）。")
                try:
                    sb.table("system_run_log").insert({
                        "run_date": run_date, "stage": "intraday_gate", "picked_count": len(_gate_results),
                        "executed_count": _gate_pass, "gate_status": "normal",
                        "note": f"5分K三關：{len(_gate_results)}檔已判斷，合格{_gate_pass}／不合格{_gate_fail}",
                    }).execute()
                except Exception as _e:
                    print(f"[自建5分K三關] 寫入system_run_log失敗（不影響三關結果本身）：{_e}")
            except Exception as e:
                # 【R96修復——重大靜默失敗，見開發歷程.md】原本這裡失敗只print到
                # GitHub Actions的log，使用者在網頁上完全看不到任何提示——總指揮官
                # 反映「連續兩天9:30三關查詢都是空的」，這正是這類靜默失敗最典型的
                # 症狀：無法分辨「真的沒有股票通過」跟「寫入根本失敗、表可能還沒建」。
                # 現在推播Telegram+寫system_run_log，失敗不會再悄悄被吞掉。
                print(f"[自建5分K三關] 寫入失敗：{e}"
                     f"（可能是尚未執行supabase_migration_r96_intraday_gate.sql建表）")
                notify_telegram(
                    f"⚠️ [{run_date}] 5分K三關（查15）結果寫入Supabase失敗，"
                    f"9:30三關查詢今天會是空的（不代表真的沒有股票通過，是寫入本身"
                    f"就失敗了）。最可能原因：尚未執行supabase_migration_r96_"
                    f"intraday_gate.sql建立intraday_gate_results表。錯誤內容：{e}")
                try:
                    sb.table("system_run_log").insert({
                        "run_date": run_date, "stage": "intraday_gate", "picked_count": len(_gate_results),
                        "executed_count": 0, "gate_status": "error",
                        "note": f"寫入intraday_gate_results失敗：{e}",
                    }).execute()
                except Exception:
                    pass   # 連system_run_log都寫不進去，代表Supabase整個連不上，Telegram已經推播過了
        else:
            # 【R96新增】_gate_results本身是空的（代表symbols裡沒有任何一檔真的
            # 抓到5分K bars），這種情況原本完全沒有任何log或提示，跟上面「寫入
            # 失敗」是不同的失敗模式（這是「根本沒資料可判斷」，不是「有資料但
            # 寫不進去」），一樣要讓使用者看得到，不要悄悄跳過。
            print(f"[自建5分K三關] {len(symbols)}檔symbols裡沒有任何一檔抓到5分K bars，"
                  f"跳過三關判斷（可能是今天輪詢階段整個失敗，請檢查上面的輪詢log）。")


def stage_intraday_execute(sb):
    """
    【R97新增，見開發歷程.md「當沖自動下單」章節】stage_intraday_kbar跑完
    （09:24-10:00收集+三關判斷）之後執行，讀當天intraday_gate_results，
    對overall_verdict='pass'的候選直接自動執行進場，寫入system_portfolio
    (trade_type='intraday', status='holding')，市價進場（用
    fetch_twse_mis_batch現價，不是收盤價，因為現在是盤中）。跟stage_signal
    同樣「各買1張、報酬率等權」的部位邏輯，方便勝率統計互相比較。

    【R97修正，總指揮官確認】多空一視同仁全自動執行，不特別把空方留成
    pending待人工確認——system_portfolio這張表本身是系統自己的追蹤紀錄，
    不是真的呼叫券商下單API（這個專案完全沒有券商下單串接），寫入
    'holding'只是記錄一筆部位供之後統計績效比較用。先前考慮的「券源/
    違約交割」風險，只有在總指揮官自己拿這筆紀錄去下真實市場的空單時
    才會發生，跟這裡的自動記錄本身無關，所以沒有理由把空方特殊化成
    半自動——這正是總指揮官要拿多空、自動vs人工做勝率比較的前提。

    當天同一檔+同方向已經有intraday部位（今天已經進過場）不重複進場，
    避免gate結果每次都是pass時、重複執行到多筆。
    """
    run_date = datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d")
    if not is_trading_day():
        print(f"⏭️ {run_date} 非交易日，略過當沖自動執行")
        return

    try:
        gate_rows = (sb.table("intraday_gate_results").select("*")
                    .eq("trade_date", run_date).eq("overall_verdict", "pass").execute().data) or []
    except Exception as e:
        print(f"[當沖執行] 讀取intraday_gate_results失敗：{e}"
              f"（可能是尚未執行migration建表，或今天stage_intraday_kbar還沒跑完）")
        return

    if not gate_rows:
        print(f"[{run_date}] 當沖執行：今天沒有任何三關pass的候選，不動作。")
        return

    try:
        _existing = (sb.table("system_portfolio").select("symbol,side")
                    .eq("trade_type", "intraday").eq("entry_date", run_date)
                    .in_("status", ["holding", "pending"]).execute().data) or []
        _already_in = {(r["symbol"], r.get("side", "long")) for r in _existing}
    except Exception as e:
        print(f"[當沖執行] 查詢既有當沖部位失敗，保守起見本次全部跳過避免重複進場：{e}")
        return

    _candidates = [
        (r["symbol"], r.get("direction", "long"))
        for r in gate_rows
        if (r["symbol"], r.get("direction", "long")) not in _already_in
    ]

    _quotes = {}
    try:
        _pairs = [(sym, 'tse') for sym, _side in _candidates]
        _quotes = fetch_twse_mis_batch(_pairs)
    except Exception as e:
        print(f"[當沖執行] 抓即時報價失敗：{e}")

    executed_long, executed_short = [], []

    for sym, direction in _candidates:
        q = _quotes.get(sym)
        if not q or not q.get("price"):
            print(f"[當沖執行] {sym}（{direction}）抓不到即時價，本次跳過（下次執行再試）。")
            continue
        price = q["price"]
        side = "long" if direction == "long" else "short"
        try:
            sb.table("system_portfolio").insert({
                "symbol": sym, "side": side, "trade_type": "intraday",
                "entry_date": run_date, "entry_price": price, "shares": 1,
                "capital": round(price * 1000, 0), "status": "holding",
                "trigger_source": "scheduler_intraday",
                "select_reason": f"5分K三關(查15){'多方' if side == 'long' else '空方'}三關全過，自動當沖進場",
            }).execute()
            if side == "long":
                executed_long.append(f"{sym}@{price}")
            else:
                executed_short.append(f"{sym}@{price}")
        except Exception as e:
            print(f"[當沖執行] {sym} 寫入system_portfolio失敗：{e}"
                 f"（可能是尚未執行migration，system_portfolio缺trade_type欄位）")

    try:
        sb.table("system_run_log").insert({
            "run_date": run_date, "stage": "intraday_execute",
            "picked_count": len(_candidates),
            "executed_count": len(executed_long) + len(executed_short), "gate_status": "normal",
            "note": f"多方自動進場{len(executed_long)}檔／空方自動進場{len(executed_short)}檔",
        }).execute()
    except Exception as e:
        print(f"[當沖執行] 寫入system_run_log失敗：{e}")

    if executed_long or executed_short:
        msg = f"⚡ [{run_date}] 當沖三關自動執行\n"
        if executed_long:
            msg += f"🔴 多方自動進場（{len(executed_long)}檔）：\n" + "、".join(executed_long) + "\n"
        if executed_short:
            msg += f"🟢 空方自動進場（{len(executed_short)}檔）：\n" + "、".join(executed_short) + "\n"
        notify_telegram(msg)
    print(f"[{run_date}] 當沖執行完成：多方自動進場{len(executed_long)}檔，"
         f"空方自動進場{len(executed_short)}檔。")


def stage_intraday_force_exit(sb):
    """
    【R97新增，見SOP手冊「當沖鐵律」】13:25強制平倉——不管盈虧，所有
    trade_type='intraday'且status='holding'的部位，收盤集合競價前用市價
    （fetch_twse_mis_batch即時報價）全部出清。當沖不留倉是硬性軍規，這裡
    不做任何「再等等看」的判斷，時間到就出場。

    只處理status='holding'（真正有部位的），不處理'pending'（那些是還沒
    人工確認券源的空方候選，本來就沒有真實部位，不需要出場）。
    """
    run_date = datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d")
    if not is_trading_day():
        print(f"⏭️ {run_date} 非交易日，略過13:25當沖強制出場")
        return

    try:
        holds = (sb.table("system_portfolio").select("*")
                .eq("trade_type", "intraday").eq("status", "holding").execute().data) or []
    except Exception as e:
        print(f"[當沖強制出場] 讀取持倉失敗：{e}")
        return

    if not holds:
        print(f"[{run_date}] 13:25當沖強制出場：目前沒有任何當沖持倉。")
        return

    try:
        _pairs = [(h["symbol"], 'tse') for h in holds]
        _quotes = fetch_twse_mis_batch(_pairs)
    except Exception as e:
        print(f"[當沖強制出場] 抓即時報價失敗：{e}")
        _quotes = {}

    exits, total_pnl = [], 0.0
    for h in holds:
        sym = h["symbol"]
        q = _quotes.get(sym)
        if not q or not q.get("price"):
            print(f"[當沖強制出場] {sym} 抓不到即時價，無法出場，這次跳過"
                 f"（⚠️會留倉違反當沖鐵律，請人工立即處理）。")
            notify_telegram(f"⚠️ [{run_date}] {sym} 13:25強制出場抓不到即時價，"
                            f"目前仍是holding狀態，請立即人工確認並手動平倉，避免違反當沖不留倉規則。")
            continue
        cur = q["price"]
        entry = float(h.get("entry_price", 0) or 0)
        shares = int(h.get("shares", 0) or 0)
        side = h.get("side", "long")
        if entry <= 0 or shares <= 0:
            continue
        if side == "long":
            pnl = (cur - entry) * shares * 1000
        else:
            pnl = (entry - cur) * shares * 1000
        roi = (pnl / (entry * shares * 1000) * 100) if entry > 0 else 0.0
        try:
            sb.table("system_portfolio").update({
                "status": "closed", "exit_date": run_date, "exit_price": cur,
                "exit_reason": "intraday_force_exit_1325",
                "realized_pnl": round(pnl, 0), "realized_roi": round(roi, 2),
            }).eq("id", h["id"]).execute()
            exits.append(f"{sym}({side},{roi:+.1f}%)")
            total_pnl += pnl
        except Exception as e:
            print(f"[當沖強制出場] {sym} 寫入出場失敗：{e}")

    try:
        sb.table("system_run_log").insert({
            "run_date": run_date, "stage": "intraday_force_exit", "picked_count": len(holds),
            "executed_count": len(exits), "gate_status": "normal",
            "note": f"13:25強制出場{len(exits)}檔，合計損益{round(total_pnl, 0)}",
        }).execute()
    except Exception as e:
        print(f"[當沖強制出場] 寫入system_run_log失敗：{e}")

    if exits:
        notify_telegram(f"🔔 [{run_date}] 13:25當沖強制出場（不留倉鐵律）\n"
                        + "、".join(exits) + f"\n合計損益：{round(total_pnl, 0)}元")
    print(f"[{run_date}] 13:25當沖強制出場完成：{len(exits)}檔，合計損益{round(total_pnl, 0)}。")


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
    run_date = datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d")

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
        _cutoff = (datetime.now(TAIPEI_TZ) - timedelta(days=60)).strftime('%Y-%m-%d')
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
    run_date = datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d")
    # 【R97】改用共用的 get_backtest_symbol_pool()，順便過濾掉已下市/代碼
    # 變更的殘留代號，不要浪費API額度、也不要讓log被一堆「possibly delisted」
    # 訊息洗版。stale清單只印出來提醒，不自動動使用者的持倉/雷達資料。
    symbols, _stale = get_backtest_symbol_pool(sb, limit=60)
    if not symbols:
        print("[門檻校準] 目前沒有任何追蹤股票，跳過本次掃描。")
        return

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
    - 回測窗固定「近2年」——因為years參數是每次執行時才用datetime.now(TAIPEI_TZ)
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
    run_date = datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d")
    # 【R97】改用共用的 get_backtest_symbol_pool()——見該函式docstring，
    # 這裡原本跟stage_threshold_calibration各自複製一份一樣的抓法，現在
    # 合併成一份，並且順便過濾掉已下市/代碼變更的殘留代號（總指揮官手動
    # 測試時log裡那批"$5347.TW: possibly delisted"就是這批殘留代號造成的）。
    symbols, _stale = get_backtest_symbol_pool(sb, limit=60)

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

        # 【R95續4新增】60檔近2年查1~14統計上幾乎不可能0筆訊號，加一個輕量
        # 探測(試抓2330)區分「yfinance整個連不通」跟「這週真的沒訊號」兩種情況。
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
                    # 【R95續5】yfinance整體連得通不代表每檔都抓得到，
                    # 單獨對這批symbols跑一次，區分「股票池抓不到」跟
                    # 「資料都在、條件真的沒觸發」兩種情況。
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
        # 【R95續4】完全沒樣本，但診斷探測判斷出「疑似yfinance被擋」時，
        # 這資訊也值得推播，不用等到查log才知道連線層級的問題。
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


SCHEDULER_VERSION = "作戰室 排程 v1.0 (2026-08-07 R95續29：自建5分K加上回溯驗證，每次執行自動交叉比對前一交易日)"


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
    run_date = datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d")
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
                                "filter_backtest", "intraday_kbar", "score_ab_compare",
                                "build_intraday_pool", "intraday_execute", "intraday_force_exit",
                                "smart_money_scan", "route2_confirm_scan",
                                "backfill_shares_outstanding", "cleanup_test_residue",
                                "data_health_check",
                                # 【R98新增，總指揮官方案二拍板】
                                "overnight_flip_dealer_stats", "financial_health_scan",
                                "data_source_health_report",
                                # 【R98續20新增】
                                "mops_financial_scan",
                                # 【R98續25新增，臨時診斷用】
                                "diag_mis_live",
                                "diag_shioaji_live",
                                "key_usage_monitor",
                                "diag_p0_signal_live",
                                "diag_balance_sheet_live"])
    parser.add_argument("--mops_year_roc", type=int, default=None,
                        help="【選填，只給mops_financial_scan用】指定民國年，"
                             "留空預設抓現在已公告的最新一季")
    parser.add_argument("--mops_season", type=int, default=None,
                        help="【選填，只給mops_financial_scan用】指定季別1-4，"
                             "留空預設抓現在已公告的最新一季")
    args = parser.parse_args()
    sb = get_supabase()
    # 【R98續20臨時新增，診斷用】GitHub Actions的原始log存在讀不到的
    # blob storage，之前diag_fin_fields那次已經證實這個問題——這裡加一層
    # 最外層的例外捕捉，任何stage炸掉都把完整traceback寫進system_config，
    # 用Supabase查得到，不用再另外部署專門的診斷stage。這個mops_
    # financial_scan剛失敗過一次，先靠這個抓出真正原因。
    try:
        _dispatch_stage(sb, args)
    except Exception as _e:
        import traceback as _tb
        _err_text = f"{type(_e).__name__}: {_e}\n\n{_tb.format_exc()}"
        print(_err_text)
        try:
            set_config(sb, f"stage_crash_{args.stage}", _err_text[:8000])
        except Exception:
            pass
        raise


def _dispatch_stage(sb, args):
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
    elif args.stage == "intraday_kbar":
        stage_intraday_kbar(sb)
    elif args.stage == "score_ab_compare":
        stage_score_ab_compare(sb)
    elif args.stage == "build_intraday_pool":
        stage_build_intraday_pool(sb)
    elif args.stage == "intraday_execute":
        stage_intraday_execute(sb)
    elif args.stage == "intraday_force_exit":
        stage_intraday_force_exit(sb)
    elif args.stage == "smart_money_scan":
        stage_smart_money_scan(sb)
    elif args.stage == "route2_confirm_scan":
        stage_route2_confirm_scan(sb)
    elif args.stage == "backfill_shares_outstanding":
        stage_backfill_shares_outstanding(sb)
    elif args.stage == "cleanup_test_residue":
        stage_cleanup_test_residue(sb)
    elif args.stage == "data_health_check":
        run_data_health_checks(sb)
    elif args.stage == "overnight_flip_dealer_stats":
        stage_overnight_flip_dealer_stats(sb)
    elif args.stage == "financial_health_scan":
        stage_financial_health_scan(sb)
    elif args.stage == "mops_financial_scan":
        stage_mops_financial_scan(sb, year_roc=args.mops_year_roc, season=args.mops_season)
    elif args.stage == "diag_mis_live":
        stage_diag_mis_live(sb)
    elif args.stage == "diag_shioaji_live":
        stage_diag_shioaji_live(sb)
    elif args.stage == "key_usage_monitor":
        stage_key_usage_monitor(sb)
    elif args.stage == "diag_p0_signal_live":
        stage_diag_p0_signal_live(sb)
    elif args.stage == "diag_balance_sheet_live":
        stage_diag_balance_sheet_live(sb)
    elif args.stage == "data_source_health_report":
        stage_data_source_health_report(sb)


if __name__ == "__main__":
    main()
