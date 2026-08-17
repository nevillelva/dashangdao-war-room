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
from datetime import datetime, timedelta, time as dt_time
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
        determine_signal, fetch_institutional_history, fetch_revenue_history_lagged,
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
        # 【R97新增】NVIDIA AI推演共用核心，跟網頁版(warroom_v160.py)共用
        build_ai_strategy_prompt, call_ai_models_parallel, NIM_FALLBACK_MODELS,
    )
except ImportError:
    print("找不到 warroom_core.py——請確認它跟 system_scheduler.py 在同一個目錄。")
    sys.exit(1)

# 【R60新增】版本相容性檢查——避免排程端踩到「warroom_core.py沒跟著換版」
# 這個已經真實發生過兩次的bug類型。
_REQUIRED_CORE_VERSION = 103
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


def compute_full_signal_for(symbol, fm_token=""):
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
    cur = float(close.iloc[-1])
    ma5 = float(close.tail(5).mean())
    ma10 = float(close.tail(10).mean()) if len(close) >= 10 else ma5
    ma20 = float(close.tail(20).mean())
    ma60 = float(close.tail(60).mean()) if len(close) >= 60 else ma20
    prev = float(close.iloc[-2])
    gain = (cur - prev) / prev * 100 if prev else 0.0
    atr = calculate_atr(hist)
    if atr <= 0:
        atr = cur * 0.02
    high, low = hist["High"], hist["Low"]
    vol = hist["Volume"]
    vol_ratio = float(vol.iloc[-1] / vol.tail(20).mean()) if vol.tail(20).mean() > 0 else 1.0
    def_line = round(ma5 - DEF_LINE_ATR_MULT * atr, 2)
    take_profit = round(cur + atr, 2)
    open_price = float(hist["Open"].iloc[-1])
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
    day_high = float(high.iloc[-1])
    day_low = float(low.iloc[-1])
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
    inst_feat = {"f_single": None, "t_single": None, "f_5d": None, "f_10d": None,
                 "foreign_buy_streak3": None}
    try:
        inst_df = fetch_institutional_history(symbol, years=0.2, token=fm_token)
        inst_feat = _derive_institutional_features(inst_df)
    except Exception as e:
        print(f"[compute_full_signal_for] {symbol} 籌碼資料抓取失敗，本次評分不含籌碼因子："
              f"{type(e).__name__}: {e}")

    # 基本面——同樣獨立try/except
    rev_feat = {"rev_yoy": None, "rev_mom": None}
    try:
        rev_df = fetch_revenue_history_lagged(symbol, years=1, token=fm_token)
        rev_feat = _derive_revenue_features(rev_df)
    except Exception as e:
        print(f"[compute_full_signal_for] {symbol} 營收資料抓取失敗，本次評分不含基本面因子："
              f"{type(e).__name__}: {e}")

    # 【R97補做，稽核抓到的漏接】地雷警訊——需要估值百分位(fetch_pe_history，
    # 額外1次FinMind呼叫) + rev_yoy(已有) + f_5d(已有)。獨立try/except，
    # 失敗保守回傳False，不中斷整體評分流程。
    landmine = False
    try:
        landmine = compute_landmine_flag(symbol, cur, rev_feat["rev_yoy"],
                                         inst_feat["f_5d"], token=fm_token)
    except Exception as e:
        print(f"[compute_full_signal_for] {symbol} 地雷警訊計算失敗，本次評分不含此因子："
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
    )

    return {"symbol": symbol, "price": cur, "score": score, "gain": round(gain, 2),
            "def_line": def_line, "take_profit": take_profit, "vol_ratio": round(vol_ratio, 2),
            "ma5": round(ma5, 2), "ma10": round(ma10, 2), "ma20": round(ma20, 2),
            "ma60": round(ma60, 2), "signal_text": signal_text, "reasons": reasons,
            "is_volume_dump": is_volume_dump, "trend_gate_triggered": trend_gate_triggered,
            # 【R97新增，供NVIDIA AI推演的prompt使用，見開發歷程.md】排程端
            # 原本這些欄位算完就丟掉，AI推演需要用到，這裡一併回傳。
            # big_holder/pe/value_score排程端目前沒有抓這些資料，維持None，
            # build_ai_strategy_prompt對None欄位有妥善的預設文字，不會報錯。
            "code": symbol, "name": symbol, "landmine": landmine,
            "rev_yoy": rev_feat["rev_yoy"], "f_5d": inst_feat["f_5d"] or 0.0,
            "big_holder": None, "pe": None, "value_score": None, "macd_str": None, "f_vwap": None}


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
        sig_a = compute_full_signal_for(sym)
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
        sig = compute_full_signal_for(sym)
        if not sig:
            continue
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

    def _pct_change(sym):
        try:
            hist = yf.Ticker(sym).history(period="5d", timeout=8).dropna(subset=["Close"])
            if len(hist) >= 2:
                prev, cur = float(hist["Close"].iloc[-2]), float(hist["Close"].iloc[-1])
                return (cur - prev) / prev * 100 if prev else None
        except Exception as e:
            print(f"[stage_gate-診斷] {sym} 漲跌幅查詢失敗：{type(e).__name__}: {e}")
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
            sig = compute_full_signal_for(h["symbol"])
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

            sig = compute_full_signal_for(p["symbol"])
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
            sig = compute_full_signal_for(h["symbol"])
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
        cutoff = (datetime.now(TAIPEI_TZ) - timedelta(days=keep_days)).strftime('%Y-%m-%d')
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
    run_date = datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d")
    symbols, _raw_count = get_scan_pool(sb)
    if not symbols:
        print("[券商分點] 掃描池是空的（inst_holding可能還沒有資料），跳過本次抓取。")
        return

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
                    print(f"[券商分點] 連續{_consecutive_fail}檔失敗，可能是暫時性IP限流，"
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
                    print(f"[券商分點] 暫停重試後仍然連續失敗，確認不是單純的暫時性阻擋，提早中止。")
                _aborted_early = True
                print(f"[券商分點] 連續 {_consecutive_fail} 檔失敗（已測試 {_idx + 1}/{len(symbols)} 檔），"
                      f"研判本次GitHub Actions這組IP/這個時段連不上HiStock，提早中止，"
                      f"不繼續浪費剩餘{len(symbols) - _idx - 1}檔的執行時間。")
                # 【R96調整，總指揮官決定】原本這裡連續失敗時會推播Telegram警示，
                # 但這個排程幾乎每次都因為GitHub Actions連不上HiStock而失敗，
                # 總指揮官決定改成自己手動觸發雷達股票即可（網頁版有對應的
                # 「補跑今日券商分點」按鈕），不需要排程每次失敗都推播提醒——
                # 拿掉notify_telegram，只保留print()寫進GitHub Actions的log，
                # 需要查證時還是查得到，只是不再主動推播騷擾。
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
    # 【R96調整，總指揮官決定】原本失敗率超過30%時會推播Telegram警示，
    # 同樣理由拿掉——總指揮官已決定這個排程改成自己手動觸發雷達股票即可，
    # 不需要排程失敗時主動推播提醒。失敗統計仍然完整寫進system_run_log，
    # 需要查證時網頁版「排程執行履歷」可以直接看到，只是不再推播Telegram。
    if not _aborted_early and _fail > len(symbols) * 0.3:
        print(f"[券商分點] {len(symbols)}檔裡有{_fail}檔失敗（超過30%門檻），"
              f"可能是HiStock網站異常或改版，需要人工檢查（已停止對此推播Telegram，"
              f"詳情請查GitHub Actions log或網頁版排程執行履歷）。")


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
    """
    if not NVIDIA_API_KEY:
        print("[AI推演] 未配置 NVIDIA_API_KEY，本次跳過所有AI推演（不影響選股/候選池本身）。")
        return {}
    name_map = name_map or {}
    results = {}
    for p in picks:
        sym = p.get("symbol")
        if not sym:
            continue
        _direction = p.get(direction_key, default_direction)
        _card = dict(p)
        _card.setdefault("code", sym)
        _card["name"] = name_map.get(sym, sym)
        try:
            system_prompt, user_prompt = build_ai_strategy_prompt(_card, direction=_direction)
            ok, result = call_ai_models_parallel(system_prompt, user_prompt, NVIDIA_API_KEY,
                                                 models=NIM_FALLBACK_MODELS, timeout=30)
            results[sym] = result if ok else f"AI推演失敗：{result}"
        except Exception as e:
            print(f"[AI推演] {sym} 呼叫失敗（不影響選股/候選池結果）：{type(e).__name__}: {e}")
            results[sym] = None
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
            turnover_info[code] = compute_interval_turnover(code, days=TURNOVER_DAYS)
        except Exception as e:
            print(f"[候選池] {code} 區間週轉率計算失敗：{type(e).__name__}: {e}")
        time.sleep(FINMIND_CALL_PACING_SEC)   # 【R97新增】拉開請求間隔，避免撞burst limit
    scored = [(code, info) for code, info in turnover_info.items()
             if info.get("turnover_pct") is not None]
    scored.sort(key=lambda x: x[1]["turnover_pct"], reverse=True)
    stage0b_codes = [code for code, _info in scored[:STAGE0B_TOP]]
    _overheated_count = sum(1 for c in stage0b_codes if turnover_info[c]["overheated"])
    print(f"[候選池] Stage0b：{len(stage0a_codes)}檔算出區間週轉率{len(scored)}檔，"
          f"取前{len(stage0b_codes)}檔進系統A評分（其中{_overheated_count}檔標記⚠️過熱)。")

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
            sig = compute_full_signal_for(code)
        except Exception as e:
            print(f"[候選池] {code} 系統A評分失敗：{type(e).__name__}: {e}")
            time.sleep(FINMIND_CALL_PACING_SEC)
            continue
        if not sig:
            continue
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
    # 覆蓋手動清單原本的方向判斷）。candidate pool抓不到/是空的都不影響
    # 既有手動清單這條路徑，屬於錦上添花不是必要依賴。
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
            print(f"[自建5分K] 候選池併入 {len(_pool_rows)} 檔（來自stage_build_intraday_pool）。")
    except Exception as e:
        print(f"[自建5分K] 讀取intraday_candidate_pool失敗（不影響手動清單這條主路徑）：{e}"
              f"（可能是尚未執行相關migration建表，或今天candidate pool階段還沒跑）")

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
    _actual_start = datetime.now(TAIPEI_TZ)
    if _actual_start.time() >= _end_time:
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
                                "build_intraday_pool", "intraday_execute", "intraday_force_exit"])
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


if __name__ == "__main__":
    main()
