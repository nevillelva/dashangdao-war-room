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
# ==============================================================================
# 54088 戰情室 V156 — 量化擴張 · 神盾修復版
# 相對 V155 的變更請見檔尾 CHANGELOG
# ==============================================================================
import streamlit as st
import streamlit.components.v1 as components
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, date, timedelta, time as dt_time, timezone
# 【R96修復，見開發歷程.md時區bug章節】Streamlit Cloud系統時鐘是UTC，
# 需要精確時分比對的地方一律用datetime.now(TAIPEI_TZ)。
TAIPEI_TZ = timezone(timedelta(hours=8))
import re
import time
import random
import json
import os
import io
import csv
import requests
import warnings
import urllib3
import concurrent.futures
from openai import OpenAI
import tempfile
import sqlite3
import threading
import queue
import base64
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# 【V160 Round39 新增】共用核心模組——網頁版與排程版(system_scheduler.py)共同
# import，訊號計算/常數/MIS即時報價從此只維護一份，不再各自漂移。
# 這個模組本身完全不 import streamlit，可以放心被排程端也一起用。
from warroom_core import (
    GOV_HEADERS, get_safe_session, _SESSION,
    DEF_LINE_ATR_MULT, DEF_LINE_ATR_MULT_TIGHTENED, COMMON_BROKER_BRANCHES,
    DAY_TRADER_BROKERS, check_day_trader_alert, get_dynamic_day_trader_brokers,
    # 【R98續110新增】P2-2最大拉回計算精確化用
    compute_true_mdd_from_snapshots,
    compute_day_trader_ratio_from_broker_flows, compute_buyer_seller_branch_diff_proxy,
    fetch_finnhub_quote, fetch_finnhub_forex_quote,
    compute_financial_risk_score, compute_valuation_models, compute_valuation_river,
    fetch_latest_real_eps,
    is_finmind_likely_exhausted,
    fetch_mops_history_df, _lookup_point_in_time_ttm_eps,
    fetch_shioaji_snapshot,
    fetch_live_quotes_resilient,
    evaluate_weekly_trend_gate, compute_buyer_seller_branch_diff_proxy,
    calculate_atr, build_trade_zones,
    evaluate_closing_strength,  # 【R96新增】收盤強弱代查（策略框架圖整合 Step 1）
    find_attack_bar, evaluate_volume_followthrough,  # 【R96新增】Step 2 量能達標代查
    evaluate_pullback_health,  # 【R96新增】Step 3 拉回體檢母關
    determine_active_intraday_gate,  # 【R96新增】Step 4 時段自動選關
    evaluate_order_book_pressure,  # 【R96新增】Step 5 五檔買盤結構
    classify_trend_regime, evaluate_rsi_dual_mode,  # 【R96新增】三態分類+RSI雙版本
    evaluate_trend_qualification_gate,  # 【R96新增】趨勢資格硬閘門
    evaluate_rebound_health,  # 【R96新增】反彈健康度
    check_institutional_season_end_warning,  # 【R96新增】投信季底作帳警示
    evaluate_today_liquidity_by_avg,  # 【R96新增】累積清單第9項：今日流動性過濾器
    evaluate_market_gainer_concentration,  # 【R96新增】累積清單第4項：漲幅榜族群性
    evaluate_gate2_leader_deviation,  # 【R96新增】族群強弱獨立面板複用5分K三關第二關邏輯
    evaluate_daytrade_recommendation,  # 【R96新增】當沖操作建議整合層
    evaluate_day_trader_ratio, evaluate_margin_balance_regime,  # 【R96新增】累積清單第5項
    fetch_day_trading_info,  # 【R97搬進共用模組，原本在這個檔案本身】
    # 【R97新增】NVIDIA AI推演共用核心，跟排程端(system_scheduler.py)共用
    NIM_FALLBACK_MODELS, build_ai_strategy_prompt, call_ai_models_parallel,
    calc_intraday_vwap_from_bars, evaluate_vwap_position,  # 【R96新增】累積清單第7項
    fetch_industry_map_raw, FIXED_INDUSTRY_LEADERS,  # 【R96新增】5分K三關共用
    determine_signal, score_zone1_fundamental, score_zone2_technical,
    score_zone3_chips, _fmt_zone_summary,
    fetch_twse_mis_batch, _safe_mis_float,
    FinMindAPIError, set_finmind_tokens, get_fm_quota_status, get_fm_real_quota_status,
    _finmind_get, _finmind_get_once,
    _parse_holding_level_lower, parse_tdcc_holding_csv, compute_big_holder_ratios,
    compute_small_holder_ratios,
    fetch_tdcc_holding_csv_direct, fetch_histock_branch_data,
    fetch_branch_data_with_fallback,  # 【R96新增】FinMind優先、失敗才退回HiStock爬蟲
    fetch_twse_attention_stocks, fetch_twse_disposal_stocks, fetch_tpex_disposal_stocks,
    check_disposal_attention_status, fetch_twse_material_announcements,
    filter_self_compiled_announcements,
    fetch_pe_history, fetch_institutional_history, fetch_revenue_history_lagged,
    _lookup_lagged_revenue,
    # 【R95新增】查1~14自動化重構第二步搬進來的一整組（回測引擎本體+
    # 即時掃描共用的條件判斷邏輯），見warroom_core.py「十一、查1~查14+
    # 情報雷達 回測引擎本體」章節的說明。
    DEFAULT_THRESHOLDS, get_threshold, PE_LANDMINE,
    evaluate_single_condition, evaluate_scan_conditions,
    detect_k_line_patterns_v152, fetch_twii_regime_history, fetch_twii_price_history,
    compute_higher_high_low_streak, fetch_financial_health,
    detect_bollinger_overheat, detect_attack_streak_reversal,
    _filter_backtest_one_stock, run_filter_backtest,
    summarize_filter_backtest, summarize_filter_backtest_walkforward,
    # 【R95續】情報雷達回測——compute_forward_return直接沿用；
    # run_intel_radar_backtest改名匯入，因為v160.py自己還留了一個同名的
    # 薄包裝函式(負責撈Supabase rows後才呼叫這裡)，兩者用途不同不能同名。
    compute_forward_return,
    run_intel_radar_backtest as _core_run_intel_radar_backtest,
    # 【R97搬進共用模組】safe_float/fetch_shares_outstanding/
    # fetch_market_turnover_ranking_with_value 原本只在這個檔案，候選池
    # 篩選(排程端)也需要，搬進core.py共用，見該處說明。
    safe_float, fetch_shares_outstanding, fetch_market_turnover_ranking_with_value,
    apply_custom_factor_weights,
    fetch_stock_price_and_value_history, compute_interval_turnover,
)

# 【R98續110新增，深層系統檢視：dashangdao.py拆檔第一階段】
# 39個純函式（零全域依賴/零跨函式呼叫/零st.*依賴，逐一用co_names驗證過）
# 搬到獨立檔案，減少單一檔案行數。詳見dashangdao_helpers.py開頭說明。
import dashangdao_helpers
from dashangdao_helpers import (
    fetch_market_turnover_ranking, _style_pnl_columns, _ensure_schema,
    build_card_text_report, compute_trail_stop, safe_json_write, _clean_symbol,
    calc_real_profit, calc_real_profit_v2, build_short_trade_zones, calc_volume_change,
    _roc_date_to_display, parse_broker_csv, _sort_key, _yf_ticker,
    evaluate_overnight_gate, apply_timeframe_resonance, compute_risk_metrics,
    summarize_calibration, build_rotation_advice, calc_rsi, calc_bias,
    calc_disposal_risk_proxy, _fmt_closing_strength, _fmt_volume_followthrough,
    _fmt_pullback_health, _fmt_rebound_health, _fmt_trend_regime_tag,
    _fmt_order_book_pressure, _fmt_today_liquidity, _fmt_day_trader_and_margin,
    _fmt_vwap_position, _fmt_daytrade_verdict_banner, _fmt_main_force_cost, _fmt_vwap,
    _pick_col, _detect_mops_industry, build_backtest_advice, assess_filter_stability,
    # 【R98續110第二輪，這批因為第一輪已解決部分依賴而變得可搬】
    _classify_dividend_date, _clean_symbol_keyed_dict, _fmt_daytrade_summary,
    _format_live_date_human, get_intraday_projection, summarize_calibration_by_broker,
    # 【R98續110第三輪，含依賴注入用的連線物件相關函式】
    _backtest_one_stock, _call_with_hard_timeout, _get_cached_disposal_attention_lists,
    _get_sb_call_executor, _get_smart_cache_store, _is_ok_value, _reason_to_label,
    calc_inst_streak_vwap, calc_weekly_resonance,
    estimate_main_force_cost, fetch_broker_avg_price, fetch_day_trading_info_cached,
    fetch_industry_map, fetch_margin_balance_history, fetch_margin_diff,
    fetch_market_gainers_with_industry, fetch_stock_names, fetch_trading_calendar,
    get_db_stats, get_inst_data_from_db, get_latest_big_holder, get_market_regime,
    get_time_weighted_vol_ratio, list_backtest_runs, load_backtest_summary,
    load_filter_backtest_summary, save_backtest_run, save_filter_backtest_run,
    # 【R98續110第四輪】
    _fetch_big_holder_with_recursion_impl, _fetch_finmind_dividend_impl, _sb_safe,
    fetch_all_institutional_by_date, get_current_or_last_trading_date,
    get_disposal_attention_badge, get_last_trading_date, run_signal_backtest,
    # 【R98續110第五輪】
    _sb_fetch_all, compute_and_store_industry_pe, compute_and_store_industry_revenue,
    get_big_holder_trend, get_broker_data_maturity, get_industry_pe_stats,
    get_industry_revenue_stats, get_latest_big_holder_ratio, get_symbol_performance,
    get_todays_broker_flow_progress, log_intel_performance, log_watchlist_entry,
    push_all_local_to_supabase, sb_get_config, sb_get_cost_calibration, sb_get_data_cache,
    sb_get_manual_trade_log, sb_get_system_holdings, sb_get_system_occupied,
    sb_insert_system_portfolio, sb_load_user_state, sb_log_big_holder_weekly,
    sb_log_broker_flows, sb_log_cost_calibration, sb_log_manual_trade, sb_log_system_run,
    sb_save_user_state, sb_set_config, sb_set_data_cache, sb_update_peak_price,
    sb_upsert_big_holder, sb_upsert_inst_holding, system_apply_add_reduce, system_apply_exits,
    # 【R98續110第六輪】
    _get_overnight_macro_uncached, _smart_cached_call, get_concentration_percentile,
    get_intel_accuracy_summary, get_manual_vs_system_pk, get_system_capital,
    get_system_portfolio_stats, get_trail_config, list_intel_sources, load_rotation_cache,
    safe_upsert_big_holder, save_rotation_cache, sync_from_supabase_on_boot,
    # 【R98續110第七輪】
    fetch_big_holder_with_recursion, fetch_financial_health_cached,
    fetch_finmind_dividend_fallback, fetch_finmind_revenue, fetch_listed_only_codes,
    _fetch_finmind_revenue_impl,
    # 【R98續110第八輪】
    _exit_reason_zh, _expand_blood_line, _get_live_quotes_cached, analyze_intel_image,
    build_valuation, discover_nim_models, get_all_traded_symbols, get_db_conn,
    get_overnight_macro, get_scan_pool_ordered, init_sqlite_db,
)


import warroom_core as _wc

# 【R60新增】版本相容性檢查——這個bug已真實發生兩次(ImportError跟
# determine_signal()缺參數TypeError，且都被ThreadPoolExecutor的except
# 吞掉、畫面只顯示「全部抓價失敗」)。啟動當下直接檢查版本號，不符就明講停住。
_REQUIRED_CORE_VERSION = 113
if getattr(_wc, "CORE_VERSION", 0) < _REQUIRED_CORE_VERSION:
    st.error(
        f"⚠️ warroom_core.py 版本不同步：這份 warroom_v160.py 需要 "
        f"CORE_VERSION >= {_REQUIRED_CORE_VERSION}，但目前部署的 warroom_core.py "
        f"是 {getattr(_wc, 'CORE_VERSION', '未知（太舊，還沒有這個版本號）')}。"
        f"\n\n請確認 GitHub repo 裡的 warroom_core.py 也已經換成最新版——"
        f"這兩個檔案永遠要一起更新，缺一個都會讓程式在執行到一半才報錯，"
        f"不會馬上看出來。"
    )
    st.stop()

# 【新增】讓子執行緒也能使用 st.cache_data（否則多執行緒掃描時快取會失效並噴警告）
try:
    from streamlit.runtime.scriptrunner import add_script_run_ctx, get_script_run_ctx
except Exception:  # 舊版 Streamlit 相容
    def add_script_run_ctx(*a, **k): return None
    def get_script_run_ctx(*a, **k): return None

# ==============================================================================
# 一、 系統最高安全防禦與法規合規宣告
# ==============================================================================
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
warnings.filterwarnings('ignore')

# 【V160 Round39】GOV_HEADERS 已搬進 warroom_core.py（見檔案開頭的 import），
# 這裡不再重複定義，避免兩邊的 headers/session 設定各自漂移。

USER_DB_FILE = "54088_database.json"
SQLITE_DB_FILE = "54088_inst_history.db"

# 【任務一】API錯誤極致透明化：統一錯誤字串，禁止用0.0帶過
# 【V160】建置版本標記——側邊欄顯示，一眼確認雲端跑的是不是最新檔。
# 每次交付新檔案時必須同步更新這兩行。
BUILD_VERSION = "作戰室 正式版 v1.0 (2026-09-02 R98續82：修復GITHUB_TOKEN/Telegram推播secrets讀取誤導性文字+統一改用_find_secret_anywhere)"
BUILD_NOTES = "R98續18~19：總指揮官指示「a方案不行就用b，不要硬性執著」處理interest_coverage一直是null的問題。沒有繼續猜候選欄位名，改用GitHub Actions實際跑live query，拿台積電(一般業)+國泰金(金融業)最新一期財報的完整type/origin_name清單，結果透過system_config表讀回確認：FinMind的TaiwanStockFinancialStatements(綜合損益表)不管一般業或金融業都沒有InterestExpense這個獨立科目，利息費用被併在TotalNonoperatingIncomeAndExpense(營業外收入及支出)裡沒有拆分出來；金融業甚至連OperatingIncome/GrossProfit這種一般業概念都沒有(用NetInterestIncome/NetNonInterestIncome取代)。這是資料源結構性限制，不是欄位名猜錯，繼續猜不會有結果。改用「流動比率」(CurrentAssets/CurrentLiabilities，同一次live query確認一般業公司這兩個欄位都直接存在)取代利息保障倍數當短期償債能力指標，fetch_financial_health()/compute_financial_risk_score()/stage_financial_health_scan()/篩選器UI/單檔戰卡深度財報顯示全部同步更新，已用獨立腳本驗證新評分邏輯正確。Supabase新增current_ratio欄位，interest_coverage欄位保留但註記停用(避免破壞既有schema)。臨時診斷stage(diag_fin_fields)已拿掉，是階段性任務用完即丟，不留在正式stage清單裡。"

# 【V160】掃描條件代號 → 完整條件敘述 的對照表。
# 總指揮官回報：血統只顯示「查13」看不出當初是用什麼條件掃到的。
# 這張表在建構掃描條件清單時填入，戰卡渲染時用來補上完整說明（滑鼠移上去可看）。
SCAN_COMMAND_MAP = {}

# 【V160 新增】出場原因中文對照。總指揮官回報畫面上直接顯示英文代碼（take_profit等）
# 不好判讀。這裡集中管理一份對照表，所有顯示出場原因的地方都呼叫 _exit_reason_zh()，
# 不要各自寫自己的翻譯（避免以後改一個地方漏改別的地方，字典分散在多處會對不齊）。
EXIT_REASON_ZH = {
    'stop_loss': '🔴 停損',
    'take_profit': '🟢 停利',
    'trail_stop': '📈 移動停利',
    'manual': '🧪 手動平倉',
    'duplicate_skip': '⏭️ 重複略過',
    'duplicate_holding_cleanup': '🧹 重複持倉清除',
    'duplicate_cleanup_0719': '🧹 歷史重複清理',
    'duplicate_closed_cleanup_0719': '🧹 歷史重複清理',
}


# 【V160 Round39】COMMON_BROKER_BRANCHES 已搬進 warroom_core.py，這裡直接
# import（見檔案開頭），跟排程端共用同一份清單。

ERR_RATE_LIMIT = "[⛔ API限流]"
ERR_NO_DATA    = "[📭 官方未公佈]"
ERR_CONN       = "[🔌 連線失敗]"
# 【V160新增】FinMind部分資料集限backer/sponsor付費方案(千張大戶/券商
# 分點)，原本會被歸類成「限流」誤導成「等一下再查就好」，用獨立標籤區分。
ERR_PERMISSION = "[🔒 需付費方案]"

# 估價模型參數（可自行調整）
PE_FAIR_MULT   = 15.0   # 合理本益比
PE_DREAM_MULT  = 20.0   # 樂觀本益比
YIELD_DEF_RATE = 0.05   # 殖利率防守價：以 5% 殖利率回推
# 【R95】PE_LANDMINE/DEFAULT_THRESHOLDS/get_threshold()已搬進
# warroom_core.py共用，這裡直接import沿用。DEF_LINE_ATR_MULT維持0.5
# （1.5已明確否決，見開發歷程.md）。
# 【V160新增】記住每檔股票上次成功的市場後綴(.TW/.TWO)，process層級
# 存活。是「開機要等5-10分鐘」的第二個根因(原本每次都從.TW開始試，
# 上櫃股前兩次注定逾時失敗)，純粹加速用，猜錯仍會照跑完整四種嘗試。
_EXT_HINT = {}

# 【R98續71新增，總指揮官提供的除錯log發現Yahoo端限流導致登入速覽
# 花快2分鐘】跨symbol共享的yfinance熔斷器狀態——process層級存活，
# 多個ThreadPoolExecutor worker平行讀寫這個字典時理論上有race
# condition，但這只是「大約」的失敗計數器，用來觸發保護機制，不要求
# 精確計數，簡單字典操作即可，不需要額外加threading.Lock增加複雜度。
_YF_CIRCUIT_BREAKER = {'consecutive_fails': 0, 'open_until': 0}

# ==============================================================================
# 二、 資料庫架構（SQLite + 原子寫入 JSON + 防崩潰鎖）
# ==============================================================================
DB_LOCK = threading.Lock()


# 【R96修復，重大bug：SQLite連線每次互動都重開從未關閉】原本沒有
# @st.cache_resource導致連線越疊越多、鎖爭用。包一層cache_resource，
# process生命週期只會真正執行一次。
init_sqlite_db = st.cache_resource(init_sqlite_db)

SQLITE_CONN = init_sqlite_db()

_LAST_GOOD_LOCK = threading.Lock()
_LAST_GOOD_REVENUE = {}


# ==============================================================================
# 二之二、Supabase 雲端大腦 — 雙軌架構 (V160新增)
# ------------------------------------------------------------------------------
# 讀取走本機SQLite，寫入雙寫SQLite+Supabase，開機從Supabase回填本機。
# 降級保護：連線失敗自動退回純本機模式。
# ==============================================================================
SUPABASE_ENABLED = False
SUPABASE_CONN = None
_SUPABASE_INIT_MSG = "尚未初始化"


def _init_supabase():
    """
    嘗試建立 Supabase 連線。任何一步失敗都安全降級為 None，並記錄原因。
    回傳 (client_or_None, enabled_bool, message)。
    """
    try:
        from supabase import create_client
    except Exception:
        return None, False, "supabase 套件未安裝（純本機模式運行）"
    try:
        url = st.secrets["supabase"]["SUPABASE_URL"]
        key = st.secrets["supabase"]["SUPABASE_KEY"]
    except Exception:
        return None, False, "secrets 未設定 supabase 區塊（純本機模式運行）"
    if not url or not key or "你的專案" in str(url):
        return None, False, "secrets 的 SUPABASE_URL/KEY 尚未填入有效值（純本機模式運行）"
    try:
        client = create_client(url, key)
        return client, True, "Supabase 雙軌已啟用"
    except Exception as e:
        # 【R96資安修正】原本直接把例外內容(e)塞進要顯示在UI上的訊息——
        # requests/httpx這類網路函式庫的連線例外，訊息內容常常會包含
        # 完整的請求URL(在這裡就是SUPABASE_URL，等於直接洩漏Supabase
        # 專案端點給任何登入這個系統的人看)。改成print完整例外到伺服器
        # log供總指揮官自己排查，UI上顯示的訊息不含任何例外內容本身。
        print(f"[Supabase初始化-診斷] 連線建立失敗：{type(e).__name__}: {e}")
        return None, False, "Supabase 連線建立失敗，降級純本機模式（詳細原因已寫入伺服器log）"


@st.cache_resource
def get_supabase():
    """全域快取的 Supabase client（含啟用狀態與訊息）。"""
    client, enabled, msg = _init_supabase()
    return {"client": client, "enabled": enabled, "msg": msg}


_sb_pack = get_supabase()
SUPABASE_CONN = _sb_pack["client"]
SUPABASE_ENABLED = _sb_pack["enabled"]
_SUPABASE_INIT_MSG = _sb_pack["msg"]


_SB_CALL_EXECUTOR = None


# ---- 雙寫：三大法人籌碼 ----
# ---- 雙寫：千張大戶 ----
# ---- 開機同步：從 Supabase 回填本機 SQLite ----
# ---- 系統設定表：可在網頁上調整的參數（例如每日系統選股總額） ----
def synthesize_three_way_review(card_text, review_a, review_b, review_c):
    """
    【V160 B#12】三方會審總結：把原始戰卡數據 + 三份外部AI分析，
    餵給 NVIDIA API 做整合總結（在戰情室內完成，不用再開外部AI）。
    """
    if not NVIDIA_API_KEY:
        return "⚠️ NVIDIA 未連線（API key 未設定），無法產生總結。"
    try:
        client = OpenAI(base_url="https://integrate.api.nvidia.com/v1", api_key=NVIDIA_API_KEY)
    except Exception as e:
        return f"⚠️ NVIDIA client 建立失敗：{e}"
    prompt = (f"以下是一檔股票的原始戰情數據，以及三份來自不同AI的分析報告。"
              f"請你以首席戰略幕僚身分，整合三方觀點，指出共識與分歧，並給出最終明確的操作結論。\n\n"
              f"=== 原始戰情數據 ===\n{card_text}\n\n"
              f"=== A分析 ===\n{review_a}\n\n=== B分析 ===\n{review_b}\n\n=== C分析 ===\n{review_c}\n\n"
              f"請用繁體中文輸出：【三方共識】、【三方分歧】、【最終操作結論與進出場價位】")
    for model_id in get_nim_models():
        try:
            completion = client.chat.completions.create(
                model=model_id,
                messages=[{"role": "system", "content": "你是台灣股市首席戰略幕僚，整合多方分析給出果斷結論。繁體中文。"},
                          {"role": "user", "content": prompt}],
                # 【V160 修復】15s 對推理型模型太短：總指揮官回報 5 個模型有 4 個
                # 「連線逾時(15s)」。DeepSeek/GLM 這類模型跑一份戰略分析
                # 經常要 30~60 秒，逾時是被我們自己切斷的，不是模型壞掉。
                temperature=0.3, max_tokens=1500, timeout=90
            )
            return completion.choices[0].message.content
        except Exception:
            continue
    return "⚠️ NVIDIA 三個模型都無法使用，無法產生總結。"


# 【R95續】compute_forward_return已搬進warroom_core.py共用，直接沿用import。


def run_intel_radar_backtest(selected_intel_cmds, cross_window_days=7):
    """
    【R95】核心比對邏輯已經搬進warroom_core.py（排程版每週自動回測校準也要
    共用同一套），這裡是網頁版的薄包裝——負責從Supabase撈原始rows，實際
    比對邏輯呼叫core.py那份。
    """
    if not SUPABASE_ENABLED:
        return []
    rows = _sb_fetch_all("intel_performance")
    if not rows:
        return []
    return _core_run_intel_radar_backtest(rows, selected_intel_cmds, cross_window_days)


# ==============================================================================
# 二之四、系統自主選股模擬倉引擎 (V160 A階段)
# ------------------------------------------------------------------------------
# 全自動選股+進出場，同時做多做空兩個模擬倉。22:00訊號產生，隔日9:01執行。
# 出場：跌破防守線停損/觸及短線停利點停利，空單反向。
# ==============================================================================
# 【R97續4移除，總指揮官確認：git歷史查證過是真死碼】system_select_
# candidates()/system_build_entries() 這兩個函式原本配一顆網頁手動測試
# 選股按鈕，2026-07-29該按鈕連同呼叫端被移除時，函式本體殘留下來，
# 且當時留言誤寫「排程仍在用」——實際查證system_scheduler.py完全沒有
# import這個檔案，排程有自己獨立的stage_signal()，跟這兩個函式無關。
# 這裡直接清掉，避免以後有人被那句誤導的留言騙到，以為它還活著。


def system_check_exits(config_payload):
    """
    【V160 A階段】檢查系統持倉是否觸發出場（出場規則B）。
    多單：現價跌破防守線→停損，或觸及短線停利點→停利。
    空單：現價漲破防守線(進場價上方停損)→停損，或跌到目標→停利。
    回傳觸發出場的清單。
    """
    holdings = sb_get_system_holdings('holding')
    trail_cfg = get_trail_config()
    exits = []
    for h in holdings:
        code = h.get('symbol')
        c = calculate_signals_worker(code, config_payload)
        if not c or c.get('error'):
            continue
        cur = float(c.get('price', 0) or 0)
        if cur <= 0:
            continue
        side = h.get('side', 'long')
        entry = float(h.get('entry_price', 0) or 0)
        defl = float(h.get('def_line', 0) or 0)
        tp = float(h.get('take_profit', 0) or 0)

        # 【V160延伸4】更新進場後極值並算移動停利線，peak_price沒值時用
        # 進場價當起點。
        # 【V160上線前健檢修復】原本讀c.get('atr')，但戰卡實際key是'atr_val'，
        # 導致ATR移動停利功能從未真正運作過(靜默失敗)。
        _atr = float(c.get('atr_val', 0) or 0)
        _stored_peak = float(h.get('peak_price', 0) or 0)
        if side == 'long':
            _peak = max(_stored_peak if _stored_peak > 0 else entry, cur)
        else:
            _peak = min(_stored_peak if _stored_peak > 0 else entry, cur) if entry > 0 else cur
        if trail_cfg['enabled'] and abs(_peak - _stored_peak) > 1e-9:
            sb_update_peak_price(h['id'], round(_peak, 2))
        _trail_line, _trail_on = (compute_trail_stop(
            side, entry, _peak, _atr, trail_cfg['mult'], trail_cfg['activate_mult'])
            if trail_cfg['enabled'] else (0.0, False))

        exit_reason = None
        if side == 'long':
            # 移動停利啟動後，用「較高的那條線」當停損——移動停利只會收緊、不會放鬆，
            # 避免出現「因為啟用移動停利反而讓停損變寬」這種本末倒置的情況。
            _eff_stop = max(defl, _trail_line) if _trail_on else defl
            if _eff_stop > 0 and cur <= _eff_stop:
                exit_reason = 'trail_stop' if (_trail_on and _trail_line >= defl) else 'stop_loss'
            elif tp > 0 and cur >= tp and not _trail_on:
                # 移動停利啟動後就不再用固定停利點出場（那正是它要解決的「提早下車」問題）
                exit_reason = 'take_profit'
        else:  # short
            # 空單：漲破進場價3%停損，跌破進場價5%停利（不依賴 tp 欄位，用固定幅度）
            _fixed_stop = entry * 1.03 if entry > 0 else 0.0
            _eff_stop = min(_fixed_stop, _trail_line) if (_trail_on and _trail_line > 0) else _fixed_stop
            if _eff_stop > 0 and cur >= _eff_stop:
                exit_reason = 'trail_stop' if (_trail_on and _trail_line <= _fixed_stop) else 'stop_loss'
            elif entry > 0 and cur <= entry * 0.95 and not _trail_on:
                exit_reason = 'take_profit'
        if exit_reason:
            shares = int(h.get('shares', 0) or 0)
            if side == 'long':
                pnl = (cur - entry) * shares * 1000
            else:
                pnl = (entry - cur) * shares * 1000
            roi = (pnl / (entry * shares * 1000) * 100) if entry > 0 and shares > 0 else 0.0
            exits.append({**h, 'exit_price': cur, 'exit_reason': exit_reason,
                          'realized_pnl': round(pnl, 0), 'realized_roi': round(roi, 2)})
    return exits


def system_check_add_reduce(config_payload):
    """
    【V160 新功能】依訊號判斷加碼/攤平（兩者都做）。回傳待執行的加減碼動作清單。
    規則（每檔各上限一次，避免無限加碼燒光資金）：
    - 順勢加碼：多單「已獲利(>2%)」且「訊號再轉強(評分≥4)」且「尚未加碼過」→ 加碼
    - 逆勢攤平：多單「接近防守線(現價在防守線1~5%上方)」但「訊號未完全轉空(評分>-3)」
      且「尚未攤平過」→ 攤平
    - 空單邏輯鏡像相反。
    加碼資金來源：剩餘資金平分（用 get_system_capital / 當前持倉數估算每檔可加額度）。
    """
    holdings = sb_get_system_holdings('holding')
    if not holdings:
        return []
    # 剩餘資金估算：每日總額扣掉已投入，平分給「還能加碼的檔數」
    daily_cap = get_system_capital()
    invested = sum(float(h.get('capital', 0) or 0) for h in holdings)
    remaining = max(0, daily_cap - invested)
    actions = []
    for h in holdings:
        code = h.get('symbol')
        c = calculate_signals_worker(code, config_payload)
        if not c or c.get('error'):
            continue
        cur = float(c.get('price', 0) or 0)
        if cur <= 0:
            continue
        side = h.get('side', 'long')
        entry = float(h.get('entry_price', 0) or 0)
        defl = float(h.get('def_line', 0) or 0)
        score = c.get('score', 0)
        add_count = int(h.get('add_count', 0) or 0)       # 已加碼次數
        reduce_count = int(h.get('reduce_count', 0) or 0) # 已攤平次數
        roi_now = ((cur - entry) / entry * 100) if side == 'long' and entry > 0 else \
                  ((entry - cur) / entry * 100) if entry > 0 else 0

        action = None
        if side == 'long':
            # 順勢加碼：已獲利 + 訊號再轉強 + 沒加碼過
            if roi_now > 2.0 and score >= 4 and add_count < 1:
                action = 'add'
            # 逆勢攤平：接近防守線但未完全轉空 + 沒攤平過
            elif defl > 0 and defl < cur <= defl * 1.05 and score > -3 and reduce_count < 1:
                action = 'reduce'
        else:  # short
            if roi_now > 2.0 and score <= -4 and add_count < 1:
                action = 'add'
            elif entry > 0 and entry * 0.95 <= cur < entry and score < 3 and reduce_count < 1:
                action = 'reduce'

        if action:
            # 加碼張數：用剩餘資金平分（保守估：剩餘 / 目前持倉檔數 / 股價）
            per_add = remaining / max(1, len(holdings))
            add_shares = int(per_add / (cur * 1000)) if cur > 0 else 0
            if add_shares < 1:
                add_shares = 1
            actions.append({
                'id': h['id'], 'symbol': code, 'side': side, 'action': action,
                'price': cur, 'add_shares': add_shares, 'score': score, 'roi_now': round(roi_now, 2),
                'old_shares': int(h.get('shares', 0) or 0), 'old_entry': entry,
                'add_count': add_count, 'reduce_count': reduce_count,
            })
    return actions


def init_session_state():
    defaults = {
        'db_loaded': False, 'pinned_stocks': {"2303": "手動強制加入", "5871": "手動強制加入"},
        'portfolio': {}, 'revenue_override': {}, 'dividend_override': {},
        'bigholder_override': {}, 'scan_results': [], 'scan_mode': "",
        'active_key_index': 0, 'single_ai_trigger': "", 'single_ai_report': {},
        'intelligence_pool': {}, 'analysis_history': {}, 'last_refresh': time.time(),
        'last_uploaded_csv': None, 'trigger_scan': False,
        'anomaly_snapshot': {}, 'anomaly_log': [],
        'sb_synced': False, 'sb_sync_result': (0, 0),
        'authenticated': False, 'cloud_hydrated': False,
        'observe_stocks': {}, 'card_cache': {}, 'card_cache_token': ''
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


init_session_state()


def load_and_isolate_db():
    if not st.session_state.get('db_loaded', False):
        if os.path.exists(USER_DB_FILE):
            try:
                with open(USER_DB_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    # 【R95續23】本機JSON備份跟雲端是同一份資料的兩個副本，
                    # 一樣可能帶著$前綴髒污，這裡套用同一個清洗，跟
                    # hydrate_state_from_cloud()保持一致。
                    st.session_state.pinned_stocks = _clean_symbol_keyed_dict(
                        data.get("pinned_stocks", st.session_state.pinned_stocks))
                    st.session_state.observe_stocks = data.get("observe_stocks", {})
                    st.session_state.portfolio = _clean_symbol_keyed_dict(data.get("portfolio", {}))
                    st.session_state.revenue_override = _clean_symbol_keyed_dict(
                        data.get("revenue_override", {}))
                    st.session_state.dividend_override = _clean_symbol_keyed_dict(
                        data.get("dividend_override", {}))
                    st.session_state.bigholder_override = _clean_symbol_keyed_dict(
                        data.get("bigholder_override", {}))
                    st.session_state.intelligence_pool = _clean_symbol_keyed_dict(
                        data.get("intelligence_pool", {}))
                    st.session_state.analysis_history = data.get("analysis_history", {})
            except Exception:
                pass

        now_ts = datetime.now(TAIPEI_TZ).timestamp()
        for d_dict in [st.session_state.revenue_override,
                       st.session_state.bigholder_override,
                       st.session_state.dividend_override]:
            for k in list(d_dict.keys()):
                if now_ts - d_dict[k].get('ts', now_ts) > 7 * 86400:
                    del d_dict[k]
        st.session_state.db_loaded = True


def save_local_db_isolated():
    payload = {
        "pinned_stocks": st.session_state.get('pinned_stocks', {}),
        "observe_stocks": st.session_state.get('observe_stocks', {}),
        "portfolio": st.session_state.get('portfolio', {}),
        "revenue_override": st.session_state.get('revenue_override', {}),
        "dividend_override": st.session_state.get('dividend_override', {}),
        "bigholder_override": st.session_state.get('bigholder_override', {}),
        "intelligence_pool": st.session_state.get('intelligence_pool', {}),
        "analysis_history": st.session_state.get('analysis_history', {})
    }
    safe_json_write(USER_DB_FILE, payload)
    # 【V160 第二階段】狀態同步雲端：整包使用者狀態寫進 Supabase user_state 表，
    # 這樣換裝置登入、或容器清空後，都能從雲端把雷達/持倉/情報讀回來。
    sb_save_user_state(payload)


# ==============================================================================
# 二之三、使用者狀態雲端化 + 登入牆 (V160第二階段)
# ------------------------------------------------------------------------------
# 雷達/持倉/情報等狀態改成同時存Supabase user_state表，登入即有資料。
# 降級保護：Supabase沒連上時退回本機JSON模式。
# ==============================================================================
USER_STATE_KEY = "commander_main"   # 單一使用者，固定一把 key


def _find_secret_anywhere(key):
    """
    【R84新增】TOML的區塊(section)行為容易讓人誤踩——已經實際發生過一次：
    總指揮官把GITHUB_TOKEN/GITHUB_REPO加在SUPABASE_URL/SUPABASE_KEY後面，
    結果因為前面有個[supabase]區塊標題，這兩行被自動歸類進supabase這個
    區塊底下，不在最外層，導致st.secrets.get("GITHUB_TOKEN")找不到。

    與其每次多一個新secrets就要祈禱使用者剛好加在正確位置、或是每次多寫
    一個寫死的分類名稱去試，這裡直接掃過st.secrets最外層的每一個欄位——
    如果最外層直接就有這個key就用；如果某個欄位底下還有子欄位（代表是
    一個區塊），也一併往裡面找一層。這樣不管使用者把新secrets加在檔案
    的哪個區塊底下，都找得到，不用要求使用者對TOML格式的區塊行為有
    正確理解才能設定成功。

    回傳找到的值（字串），或空字串（真的哪裡都找不到）。
    """
    try:
        _direct = st.secrets.get(key, "")
        if _direct:
            return _direct
        for _top_key in st.secrets.keys():
            _val = st.secrets[_top_key]
            if hasattr(_val, 'get'):
                _found = _val.get(key, "")
                if _found:
                    return _found
    except Exception:
        pass
    return ""


def trigger_github_workflow(stage):
    """
    【R81新增】遠端觸發GitHub Actions排程——這是解決「TDCC/HiStock健康度檢查
    從網頁版直接連線失敗」的正確修法，不是重試同一條會被擋的路。

    查證過根因：GitHub Actions的stage_big_holder排程實際成功寫入4019檔資料
    （總指揮官提供的Actions日誌截圖證實），但網頁版(Streamlit Cloud)直接連
    TDCC/HiStock卻連線失敗或拿到空殼頁面——兩者用的是不同的雲端IP，這些
    網站很可能對Streamlit Cloud的IP範圍有特殊處理(不一定是明確封鎖，也可能
    是回應精簡版頁面)。與其讓網頁版自己直接連(會一直撞到同一個問題)，改成
    讓網頁版呼叫GitHub API，遠端啟動同一個排程工作流程——實際執行的還是
    GitHub Actions的IP，不會被擋。

    需要在Streamlit secrets設定：
      GITHUB_TOKEN：有 "Actions: write" 權限的GitHub Personal Access Token
      GITHUB_REPO：格式 "使用者名稱/repo名稱"（例如 "yourname/54088-warroom"）
    沒設定這兩個secrets時，回傳(False, "說明訊息")，不會拋例外。

    回傳 (成功與否, 訊息字串)。成功只代表「請求已送出」，不代表工作流程本身
    跑完成功——GitHub Actions是非同步的，實際執行結果要去Actions頁面看，
    這裡不假裝能立即知道最終結果。
    """
    _gh_token = _find_secret_anywhere("GITHUB_TOKEN")
    _gh_repo = _find_secret_anywhere("GITHUB_REPO")
    if not _gh_token or not _gh_repo:
        return False, ("尚未設定 GITHUB_TOKEN / GITHUB_REPO 這兩個secrets，無法遠端觸發。"
                       "去GitHub帳號設定申請一組有Actions寫入權限的Personal Access Token，"
                       "填進Streamlit secrets即可啟用這個功能。")
    try:
        _url = f"https://api.github.com/repos/{_gh_repo}/actions/workflows/system_scheduler.yml/dispatches"
        _headers = {"Authorization": f"Bearer {_gh_token}", "Accept": "application/vnd.github+json"}
        _resp = requests.post(_url, headers=_headers, timeout=15,
                              json={"ref": "main", "inputs": {"stage": stage}})
        if _resp.status_code == 204:
            return True, "已送出觸發請求，GitHub Actions會在幾秒內開始執行（實際執行結果要去Actions頁面確認）。"
        return False, f"觸發失敗：HTTP {_resp.status_code}，{_resp.text[:200]}"
    except Exception as e:
        return False, f"觸發失敗：{e}"


def hydrate_state_from_cloud():
    """
    開機時（每 session 一次）從雲端把使用者狀態灌進 session_state。
    雲端有資料就用雲端的（較新、跨裝置一致）；雲端沒有就維持本機 JSON 載入的結果。
    """
    if not SUPABASE_ENABLED:
        return False
    cloud = sb_load_user_state()
    if not cloud or not isinstance(cloud, dict):
        return False
    # 【R95續23】股票代號dict如果帶$前綴髒污，會讓後續每個用到代號的地方
    # 對假代號打API浪費重試時間。在載入當下就清洗，不用後面各自補丁。
    _symbol_keyed = {"pinned_stocks", "portfolio", "revenue_override", "dividend_override",
                     "bigholder_override", "intelligence_pool"}
    for k in ("pinned_stocks", "observe_stocks", "portfolio", "revenue_override", "dividend_override",
              "bigholder_override", "intelligence_pool", "analysis_history"):
        if k in cloud and cloud[k]:
            st.session_state[k] = (_clean_symbol_keyed_dict(cloud[k]) if k in _symbol_keyed else cloud[k])
    return True


def require_login():
    """
    登入牆：未登入時顯示密碼輸入畫面並 st.stop() 擋住後續所有 UI。
    密碼沿用 secrets 的 commander_pin（總指揮官選 A：一個密碼走天下）。

    【R98續70新增，總指揮官指示資安分權：主帳號 vs 唯讀訪客帳號】原本
    只有一個密碼，任何拿到這組密碼的人（包括未來Playwright自動化診斷
    腳本需要存進GitHub Secret的那組）都擁有完全相同的總指揮官等級權限
    ——一旦外洩，最壞情況是被拿去亂改資料/亂觸發排程，不只是「被看到
    畫面」而已。

    新增第二組VIEWER_PIN比對：命中VIEWER_PIN時，session_state裡標記
    'user_role'='viewer'（命中COMMANDER_PIN標記'admin'），後續UI各處
    可以用is_admin()這個輔助函式判斷要不要顯示/啟用「會修改資料」的
    按鈕。VIEWER_PIN未設定（空字串）時，這條路徑直接跳過不比對，不會
    讓「輸入空密碼」被誤判成viewer登入成功——維持向下相容，總指揮官
    沒設定這個secret前，系統行為跟修改前完全一樣。
    """
    if st.session_state.get('authenticated', False):
        return
    st.markdown("<h1 style='text-align:center; color:#f1c40f; margin-top:60px;'>🚀 作戰室 正式版 v1.0</h1>",
                unsafe_allow_html=True)
    st.markdown("<p style='text-align:center; color:#888;'>總指揮官身分驗證</p>", unsafe_allow_html=True)
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        pin_input = st.text_input("請輸入指揮密碼", type="password", key="login_pin_input")
        if st.button("🔓 登入作戰室", use_container_width=True):
            if pin_input == str(COMMANDER_PIN):
                st.session_state['authenticated'] = True
                st.session_state['user_role'] = 'admin'
                # 登入成功當下，從雲端灌一次狀態（跨裝置一致）
                hydrated = hydrate_state_from_cloud()
                st.session_state['cloud_hydrated'] = hydrated
                st.rerun()
            elif VIEWER_PIN and pin_input == str(VIEWER_PIN):
                st.session_state['authenticated'] = True
                st.session_state['user_role'] = 'viewer'
                hydrated = hydrate_state_from_cloud()
                st.session_state['cloud_hydrated'] = hydrated
                st.rerun()
            else:
                st.error("密碼錯誤，請重新輸入。")
    st.stop()


def is_admin():
    """
    【R98續70新增】判斷目前登入身分是不是總指揮官等級——viewer角色
    (例如Playwright自動化診斷腳本、未來要開放的唯讀訪客)呼叫這個函式
    會回傳False，UI各處可以用這個結果決定要不要隱藏/disable「會修改
    資料」的按鈕。沒有登入過（理論上不該發生，因為require_login()會
    先擋住）或角色標記遺失時，保守回傳False（寧可誤擋住admin一次要求
    重新登入，也不要誤放行viewer去動到不該碰的功能）。
    """
    return st.session_state.get('user_role', '') == 'admin'


load_and_isolate_db()

# 【V160】開機時從 Supabase 同步一次籌碼到本機（每個 session 只跑一次，避免每次 rerun 都打雲端）
if SUPABASE_ENABLED and not st.session_state.get('sb_synced', False):
    # 【V160修復】Supabase 45天籌碼資料回填本機——容器睡眠後重登入等於全新
    # session要整批重跑，這是雲端同步架構的已知取捨。0-100%進度條顯示階段。
    _boot_prog = st.progress(0.0, text="☁️ 準備從雲端回填資料...")

    def _boot_progress_cb(pct, label):
        """給 sync_from_supabase_on_boot 回報進度用。pct 是 0.0~1.0。"""
        try:
            _boot_prog.progress(min(1.0, max(0.0, pct)), text=f"☁️ {label}（{pct*100:.0f}%）")
        except Exception:
            pass   # 進度條更新失敗不該讓整個開機流程掛掉

    _inst_n, _bh_n = sync_from_supabase_on_boot(progress_cb=_boot_progress_cb)
    _boot_prog.progress(1.0, text=f"✅ 回填完成（籌碼 {_inst_n:,} 筆、大戶 {_bh_n:,} 筆）")
    time.sleep(0.3)
    _boot_prog.empty()
    st.session_state['sb_synced'] = True
    st.session_state['sb_sync_result'] = (_inst_n, _bh_n)

API_READY, FINMIND_READY = True, True
try:
    COMMANDER_PIN = st.secrets.radar_secrets.commander_pin
    # 【R98續70新增】唯讀訪客密碼，未設定時給空字串——require_login()
    # 裡有明確判斷"VIEWER_PIN and pin_input == ..."，空字串是falsy，
    # 不會讓這條路徑意外生效，維持向下相容。
    VIEWER_PIN = st.secrets.radar_secrets.get("viewer_pin", "").strip()
    NVIDIA_API_KEY = st.secrets.radar_secrets.get("nvidia_api_key", "").strip()
    if not NVIDIA_API_KEY:
        API_READY = False

    SECRET_FINMIND = st.secrets.radar_secrets.get("finmind_token", "")
    FINMIND_TOKENS = [k.strip() for k in SECRET_FINMIND.split(",") if k.strip()]
    if not FINMIND_TOKENS or FINMIND_TOKENS[0] == "":
        FINMIND_TOKENS, FINMIND_READY = [""], False

    # 【R98續新增，總指揮官指示：隔夜總經HUD/開盤前閘門最徹底解法】
    # 網頁端讀密鑰統一用st.secrets（不是os.environ——這是Streamlit Cloud
    # 的機制，跟GitHub Actions的os.environ.get(secrets.XXX)是完全不同的
    # 兩套，這裡若沿用os.environ.get會在Streamlit Cloud上永遠讀不到值，
    # 是本次修改中發現並修正的一個潛在連動性錯誤）。未設定時給空字串，
    # 呼叫端(_fetch_finnhub)本來就會對空字串優雅降級回yfinance，不會壞掉。
    FINNHUB_TOKEN = st.secrets.radar_secrets.get("finnhub_token", "").strip()
    # 【R98續29新增，總指揮官方向：永豐金補位TWSE MIS查不到的即時報價】
    # 同一套模式讀取——未設定時給空字串，呼叫端據此判斷要不要嘗試這條
    # 備援路徑，不會因為沒設定就整個壞掉。
    SHIOAJI_API_KEY = st.secrets.radar_secrets.get("shioaji_api_key", "").strip()
    SHIOAJI_SECRET_KEY = st.secrets.radar_secrets.get("shioaji_secret_key", "").strip()
except Exception:
    API_READY, FINMIND_READY, COMMANDER_PIN, NVIDIA_API_KEY, FINMIND_TOKENS = False, False, "54088", "", [""]
    VIEWER_PIN = ""
    FINNHUB_TOKEN = ""
    SHIOAJI_API_KEY, SHIOAJI_SECRET_KEY = "", ""

# 【R98續110新增，深層系統檢視：拆檔，依賴注入】
# dashangdao_helpers.py裡搬過去的函式（第一輪39個/第二輪6個/第三輪29個，
# 共74個)有一部分需要SQLITE_CONN/DB_LOCK/SUPABASE_ENABLED/SUPABASE_CONN/
# FINMIND_TOKENS這幾個連線物件——但這些物件在主畫面script流程裡也被
# 直接引用了47+8+2+1次，沒辦法把初始化本身搬走。改用依賴注入：
# dashangdao_helpers.py裡宣告同名的模組層級佔位符(預設None/空值)，這裡
# 在確定所有連線都初始化完成後，把「真正的值」寫入dashangdao_helpers
# 模組的命名空間——Python函式查找全域變數是在「被呼叫的當下」才查找，
# 不是定義的當下就固定，所以這個注入只要發生在「任何一個搬移過的函式
# 真正被呼叫之前」就有效，不需要更動任何一個呼叫點的程式碼(已用最小
# 範例驗證過這個模式可靠)。
dashangdao_helpers.SQLITE_CONN = SQLITE_CONN
dashangdao_helpers.DB_LOCK = DB_LOCK
dashangdao_helpers.SUPABASE_ENABLED = SUPABASE_ENABLED
dashangdao_helpers.SUPABASE_CONN = SUPABASE_CONN
dashangdao_helpers.FINMIND_TOKENS = FINMIND_TOKENS
# 【R98續110第八輪擴充】同一套依賴注入橋接，這次加入NVIDIA/Shioaji金鑰
dashangdao_helpers.NVIDIA_API_KEY = NVIDIA_API_KEY
dashangdao_helpers.SHIOAJI_API_KEY = SHIOAJI_API_KEY
dashangdao_helpers.SHIOAJI_SECRET_KEY = SHIOAJI_SECRET_KEY


def fetch_finmind_stock_price(symbol, days_back=200):
    """
    【V160 Round37 新增】用 FinMind TaiwanStockPrice 抓個股日線，取代 yfinance
    作為主要價格來源。

    背景：yfinance 對台股（不只指數，個股也一樣）的資料更新有系統性延遲問題——
    round31-36 一路追查大盤指數卡在過時資料，最後發現同一個病根也出現在每一張
    戰卡的股價上（總指揮官回報 聯電/加高/友達 都卡在7/21，實際已經是7/22甚至
    7/23）。FinMind 是這整個專案從一開始就在用、證實穩定的資料源（籌碼、營收、
    財報都靠它），這裡改用同一套基礎設施抓股價，不再把「最常用、最需要準確」
    的價格資料押注在 yfinance 這個已經證實對台股有延遲問題的來源上。

    回傳格式刻意跟 yfinance 版本一致（DatetimeIndex + Open/High/Low/Close/Volume），
    這樣呼叫端完全不用改，可以直接替換。抓不到回 None，呼叫端會退回 yfinance。
    """
    try:
        token = get_active_fm_token()
        url = 'https://api.finmindtrade.com/api/v4/data'
        start_date = (datetime.now(TAIPEI_TZ) - timedelta(days=days_back)).strftime('%Y-%m-%d')
        params = {'dataset': 'TaiwanStockPrice', 'data_id': symbol, 'start_date': start_date}
        if token:
            params['token'] = token
        # 【R96調整】這是全App呼叫頻率最高的查詢路徑，收緊成max_retries=1/
        # timeout=5（換一組憑證本身就等同再試一次），3組×1次×5秒=最壞15秒，
        # 跟其他呼叫頻率低、正確性優先的資料分開處理，不影響那些呼叫端。
        payload = _finmind_get(url, params, max_retries=1, timeout=5)
        df = pd.DataFrame(payload.get('data', []))
        if df.empty:
            return None
        df['date'] = pd.to_datetime(df['date'])
        df = df.set_index('date').sort_index()
        df = df.rename(columns={'open': 'Open', 'max': 'High', 'min': 'Low',
                                'close': 'Close', 'Trading_Volume': 'Volume'})
        for _c in ('Open', 'High', 'Low', 'Close', 'Volume'):
            if _c not in df.columns:
                return None   # 欄位對不上就誠實放棄，不要用不完整的資料
        df['Volume'] = df['Volume'] / 1000.0   # 股 → 張，跟 yfinance 路徑單位一致
        df = df[df['Volume'] > 0].dropna(subset=['Close'])
        return df[['Open', 'High', 'Low', 'Close', 'Volume']] if len(df) > 20 else None
    except FinMindAPIError as _e:
        print(f"[fetch_finmind_stock_price-診斷] FinMind抓價失敗：{type(_e).__name__}: {_e}")
        return None
    except Exception as _e:
        print(f"[fetch_finmind_stock_price-診斷] 非預期例外：{type(_e).__name__}: {_e}")
        return None


@st.cache_data(ttl=180, show_spinner=False)
def get_real_stock_data_yfinance(symbol):
    # 【R95續24】先試FinMind失敗才退回yfinance，在多筆真實股票上花到
    # 11-16秒，是速覽瓶頸之一。內層包智慧快取，recheck_interval=1800秒。
    return _smart_cached_call(f"price_hist:{symbol}", lambda: _fetch_real_stock_data_impl(symbol),
                              recheck_interval=1800, fail_retry=120)


def _fetch_real_stock_data_impl(symbol):
    # 【V160 Round37關鍵修復】yfinance對台股資料有系統性延遲(股價卡在
    # 舊日期)，round31-36查到大盤指數、這次證實個股價格也同病根。改用
    # FinMind當主要來源，yfinance降級為備援。
    # 【R97續17新增，R97續18簡化】原本這裡自己判斷「FinMind是否已知額度
    # 用盡」，跟_finmind_get()共用入口的判斷重複維護兩份——已經改成在
    # _finmind_get()統一把關（見該函式docstring），這裡不用再自己判斷，
    # fetch_finmind_stock_price()額度用盡時會自然快速回傳None，直接往下
    # 試yfinance即可，不用兩處各自維護一份「是否跳過」的邏輯。
    _fm_hist = fetch_finmind_stock_price(symbol)
    if _fm_hist is not None and len(_fm_hist) > 20:
        try:
            info = {}   # FinMind沒有等同yfinance .info的公司基本資料，留空
            # 保留跟yfinance路徑一致的函式名稱參與快取key，但這裡直接回傳FinMind結果
            return _fm_hist.tail(120), info
        except Exception:
            pass   # 理論上不會走到這裡，防禦性保留，失敗就繼續往下試yfinance

    # 【R98續71新增，總指揮官提供的除錯log發現：登入速覽花快2分鐘，
    # log裡連續出現4次"Cookie/crumb fetch failed (RetryError)"】查證
    # 確認這是yfinance套件內部取得cookie/crumb的前置步驟失敗+重試，
    # 我們自己設的timeout=4秒只控制最終那次history()請求，管不到這段
    # 前置步驟——且這是per-symbol各自觸發，16-20檔股票每檔都各自重新
    # 嘗試一次crumb，即使每次只多花1-2秒，疊加起來就是log裡看到的
    # 顯著延遲。這通常反映Yahoo Finance端當下限流(429)，短時間內同一
    # 對外IP重試大概率會繼續失敗。
    #
    # 用熔斷器模式(Circuit Breaker)：跨symbol共享一個全域計數器，連續
    # 3次yfinance整體失敗(不分是哪個symbol)，就判定"Yahoo那邊現在有
    # 問題"，接下來2分鐘內直接跳過yfinance查詢(只依賴前面FinMind的
    # 結果，FinMind也沒有就誠實回傳None)，不要讓後面還沒查的股票繼續
    # 各自浪費時間去撞同一道限流牆。2分鐘後自動解除保護，重新嘗試
    # (不是永久跳過，Yahoo限流通常是短暫的)。
    _now = time.time()
    _circuit = _YF_CIRCUIT_BREAKER
    if _now < _circuit.get('open_until', 0):
        return None, {}

    # 【V160關鍵修復】原本沒有@st.cache_data，每次互動都對yfinance重打
    # 網路請求，是「開機要等5分鐘」的根因。加ttl=180快取+記住上次成功格式。
    _hint = _EXT_HINT.get(symbol)
    _ext_order = [_hint] + [e for e in (".TW", ".TWO") if e != _hint] if _hint else [".TW", ".TWO"]

    # 【R96調整】拿掉「有無session」這層重試——兩者面對同一個Yahoo端點/
    # 同一個對外IP，重試成功率極低，等於雙倍時間換極低額外成功率。只保留
    # 「兩種副檔名」(.TW/.TWO)這個真正有意義的差異，單檔最壞等待時間
    # 從16秒降到8秒。
    _this_call_succeeded = False
    for ext in _ext_order:
        try:
            tk = yf.Ticker(symbol + ext, session=_SESSION)
            # auto_adjust=False → 保留實際成交價，與券商報價一致
            # 【V160 修復】這是掃描/戰卡最高頻呼叫的函式，原本沒設 timeout，
            # 一檔卡住就可能拖累整個掃描/開機流程。加上逾時保護。
            # 【R96調整：8秒→4秒】見上方說明。
            hist = tk.history(period="6mo", auto_adjust=False, timeout=4).dropna(subset=['Close'])
            hist = hist[hist['Volume'] > 0]
            if hist.empty or len(hist) <= 20:
                continue
            hist = hist.copy()
            hist['Volume'] = hist['Volume'] / 1000.0   # 股 → 張
            try:
                info = tk.info
            except Exception:
                info = {}
            _EXT_HINT[symbol] = ext   # 記住這次成功的格式，下次直接先試
            _circuit['consecutive_fails'] = 0   # 【R98續71】成功了，重置熔斷器計數
            return hist.tail(120), info
        except Exception:
            continue
    # 【R98續71新增】這次(兩種副檔名都試過)整批失敗，累計熔斷器計數，
    # 連續3次(不分symbol，全域累計)就判定Yahoo端當下有問題，開啟保護
    # 2分鐘，接下來的股票直接跳過不用再各自浪費時間嘗試。
    _circuit['consecutive_fails'] = _circuit.get('consecutive_fails', 0) + 1
    if _circuit['consecutive_fails'] >= 3:
        _circuit['open_until'] = time.time() + 120
        print(f"[yfinance熔斷器] 連續{_circuit['consecutive_fails']}次失敗，"
              f"接下來2分鐘直接跳過yfinance查詢，不再逐檔浪費時間嘗試。")
    return None, {}


def compute_industry_rotation(codes, stock_to_ind, min_members=3, max_scan=250, progress_callback=None):
    """
    【V160 延伸1】族群輪動熱力圖：算出各產業在 1日／5日／20日 的平均漲跌幅與資金集中度。

    為什麼這是投報率最高的一項：這是籌碼K線的核心賣點之一（產業即時、資金流向），
    但我們用「既有的產業分類 + 既有的股價資料」就能做，不需要任何付費 API。

    對勝率的實際幫助：個股會漲通常是因為整個族群在動。先確認族群趨勢再選個股，
    等於多一層過濾，能降低「選對股但選錯時機」的虧損。

    ⚠️ 誠實限制：這是「同產業分類」的族群強弱，不是真正的供應鏈上下游關聯。
    抓不到資料的股票直接略過，不用0填補（那會把整個產業的平均拉偏）。

    【R52新增】原本fetch失敗被_fetch_one吃掉(except Exception: hist=None)，
    完全靜默——如果FinMind/yfinance那端剛好在這次掃描全部失敗，使用者只會看到
    「沒有產業達到最低檔數門檻」這個誤導訊息（聽起來像是「產業成員太少」，
    但真正原因其實是「每一檔都抓失敗」，兩者需要的下一步完全不同）。
    現在額外回傳一份診斷字典，把「抓成功幾檔／抓失敗幾檔／最後一個錯誤長怎樣」
    攤開，呼叫端可以在結果為空時，區分「本來就沒幾檔」跟「其實都在抓失敗」。

    回傳 (rows, diag)：rows 同原本；diag = {'total':int, 'ok':int, 'fail':int,
    'last_error':str}。
    """
    _diag = {'total': 0, 'ok': 0, 'fail': 0, 'last_error': ''}
    if not codes or not stock_to_ind:
        return [], _diag
    # 控制掃描量：產業輪動看的是族群趨勢，不需要掃全市場每一檔
    pool = list(codes)[:max_scan]
    by_ind = {}
    for code in pool:
        ind = stock_to_ind.get(code)
        if ind:
            by_ind.setdefault(ind, []).append(code)
    # 成員太少的產業統計上沒有代表性，直接不列（不是填0）
    by_ind = {k: v for k, v in by_ind.items() if len(v) >= min_members}
    if not by_ind:
        return [], _diag

    all_codes = [code for members in by_ind.values() for code in members]
    _total_codes = len(all_codes)
    _diag['total'] = _total_codes
    _hist_cache = {}
    _err_cache = {}
    _done_lock = threading.Lock()
    _done_codes = [0]
    _ctx = get_script_run_ctx()

    def _fetch_one(code):
        # 讓子執行緒掛上 Streamlit context，st.cache_data 才會生效
        # （跟 calculate_signals_worker 用同一套做法）
        if _ctx is not None:
            try:
                add_script_run_ctx(threading.current_thread(), _ctx)
            except Exception:
                pass
        try:
            hist, _ = get_real_stock_data_yfinance(code)
            return code, hist, None
        except Exception as e:
            return code, None, f"{type(e).__name__}: {e}"

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        _futures = {executor.submit(_fetch_one, code): code for code in all_codes}
        for _future in concurrent.futures.as_completed(_futures):
            _code, _hist, _err = _future.result()
            _hist_cache[_code] = _hist
            if _err:
                _err_cache[_code] = _err
            with _done_lock:
                _done_codes[0] += 1
                _dc = _done_codes[0]
            if progress_callback:
                progress_callback(_dc, _total_codes)

    rows = []
    for ind, members in by_ind.items():
        r1, r5, r20, vols = [], [], [], []
        for code in members:
            hist = _hist_cache.get(code)
            if hist is None or len(hist) < 21:
                _diag['fail'] += 1
                if code in _err_cache:
                    _diag['last_error'] = f"{code}: {_err_cache[code]}"
                elif not _diag['last_error']:
                    _diag['last_error'] = f"{code}: 抓到的K棒不足21根（可能是新股或FinMind/yfinance都查無資料）"
                continue
            try:
                closes = hist['Close']
                c0 = float(closes.iloc[-1])
                if c0 <= 0:
                    _diag['fail'] += 1
                    continue
                c1 = float(closes.iloc[-2])
                c5 = float(closes.iloc[-6])
                c20 = float(closes.iloc[-21])
                if c1 > 0:
                    r1.append((c0 - c1) / c1 * 100)
                if c5 > 0:
                    r5.append((c0 - c5) / c5 * 100)
                if c20 > 0:
                    r20.append((c0 - c20) / c20 * 100)
                # 成交值 = 收盤 × 成交量（張），當作資金流向的代理
                vols.append(float(hist['Volume'].iloc[-1]) * c0)
                _diag['ok'] += 1
            except (IndexError, ValueError, TypeError) as e:
                _diag['fail'] += 1
                _diag['last_error'] = f"{code}: {type(e).__name__}: {e}"
                continue
        if not r5:
            continue
        rows.append({
            '產業': ind,
            '檔數': len(r5),
            '1日%': round(sum(r1) / len(r1), 2) if r1 else None,
            '5日%': round(sum(r5) / len(r5), 2),
            '20日%': round(sum(r20) / len(r20), 2) if r20 else None,
            '成交值(億)': round(sum(vols) / 1e8, 2) if vols else None,
        })
    rows.sort(key=lambda x: x['5日%'], reverse=True)
    # 資金集中度：各產業成交值佔本次統計總成交值的比重
    total_val = sum(r['成交值(億)'] or 0 for r in rows)
    for r in rows:
        r['資金佔比%'] = (round((r['成交值(億)'] or 0) / total_val * 100, 2)
                        if total_val > 0 else None)
    return rows, _diag



def get_active_fm_token():
    idx = st.session_state.get('active_key_index', 0) % max(1, len(FINMIND_TOKENS))
    return FINMIND_TOKENS[idx]


# ==============================================================================
# 【R47】FinMind多帳號輪替+額度追蹤已搬進warroom_core.py共用，這裡只需要
# 把token清單餵給set_finmind_tokens()。
# ==============================================================================
set_finmind_tokens(FINMIND_TOKENS)


# ==============================================================================
# 三、 基礎運算與 API 取資料核心
# ==============================================================================
# 【R97】safe_float本體已搬進warroom_core.py，上面import區已經拉進來，
# 這裡不再重複定義（原本這裡跟core.py各自一份，是本檔案開頭module
# docstring警告過的「同一套邏輯分散維護」問題的另一個實例）。


_SMART_CACHE_STORE = {}


def fetch_twse_dividends():
    """
    【V160 關鍵修復】除權息預告表一直抓不到資料，原因跟營收/大戶是同一類 bug：
    端點路徑和欄位名稱都對不上證交所實際的 API schema。

    錯的地方：
      - URL 少了 `_ALL` 尾碼（`TWT48U` 不是有效端點，`TWT48U_ALL` 才是）
      - 欄位名稱寫的是中文（'股票代號'／'現金股利'／'除權息日期'），
        但這個 openapi 端點實際回傳的是英文欄位：
        Date／Code／Name／Exdividend／StockDividendRatio／
        SubscriptionRatio／CashDividend／SharesOffered 等
    中文欄位在英文回應裡永遠找不到 → item.get(...) 全部回傳空字串／0 →
    畫面上永遠「無日期」，不是資料真的沒有，是根本沒讀到欄位。
    """
    divs = {}
    try:
        res = _SESSION.get("https://openapi.twse.com.tw/v1/exchangeReport/TWT48U_ALL", timeout=5)
        if res.status_code == 200:
            for item in res.json():
                c = str(item.get('Code', '')).strip()
                if len(c) == 4:
                    cash_div = safe_float(item.get('CashDividend', 0))
                    stock_div = safe_float(item.get('StockDividendRatio', 0))
                    divs[c] = {'date': str(item.get('Date', '')).strip(),
                               'cash': cash_div, 'stock': stock_div}
    except Exception as _e:
        print(f"[fetch_twse_dividends-診斷] 抓股利資料失敗：{type(_e).__name__}: {_e}")
        pass
    return divs


def sync_broker_flows_batch(symbols_to_fetch, max_symbols=None, consecutive_fail_limit=8, progress_cb=None):
    """
    【R95續11新增】網頁版直接連HiStock、批次補跑全市場券商分點——這是
    「用網頁版的IP去抓，而不是靠GitHub Actions」這條路的核心邏輯，跟排程版
    stage_broker_flows用同一支fetch_histock_branch_data、同一套連續失敗
    斷路器設計（見system_scheduler.py的說明），差別只在於執行的地方換成
    網頁版（已證實這個IP目前連得到HiStock，GitHub Actions這組IP目前連不到）。

    max_symbols：這次最多抓幾檔就停（不是「一定要抓完全部」，讓使用者能
    自己決定要跑多久——網頁版分頁一旦關掉就會中斷，抓太多不划算；跑幾批、
    每批抓多少，交給總指揮官自己控制，比我們幫你決定要好）。

    連續失敗達consecutive_fail_limit時提早中止，理由跟排程版斷路器一致：
    如果連這個平常暢通的路徑這次也開始連續失敗，代表HiStock這次可能真的
    是全站有問題，不是特定一個IP的事，硬撐著抓完只是浪費時間。

    回傳 dict：{ok_count, fail_count, tested_count, aborted_early, done_now}
    done_now是這次真的成功寫入的代號清單，供呼叫端顯示。
    """
    ok_count, fail_count, consecutive_fail = 0, 0, 0
    aborted_early = False
    done_now = []
    today = datetime.now(TAIPEI_TZ).strftime('%Y-%m-%d')
    targets = symbols_to_fetch[:max_symbols] if max_symbols else symbols_to_fetch
    total = len(targets)

    for i, code in enumerate(targets):
        if progress_cb:
            try:
                progress_cb(i, total, code)
            except Exception:
                pass
        df = fetch_branch_data_with_fallback(code, today)
        if df is None or df.empty:
            fail_count += 1
            consecutive_fail += 1
            if consecutive_fail >= consecutive_fail_limit:
                aborted_early = True
                break
            continue
        consecutive_fail = 0
        try:
            rows = [{
                'symbol': code, 'log_date': today,
                'broker_name': str(r['broker_name']),
                'buy_shares': int(r['buy_shares']), 'sell_shares': int(r['sell_shares']),
                'net_shares': int(r['net_shares']),
            } for _, r in df.iterrows()]
            SUPABASE_CONN.table("broker_flows").upsert(
                rows, on_conflict="symbol,log_date,broker_name").execute()
            ok_count += 1
            done_now.append(code)
        except Exception:
            fail_count += 1
        # 【R95續12/續20】網頁版抓HiStock固定間隔改小範圍隨機，且加大到
        # 2~4秒——實測發現連續抓45檔後34檔開始失敗，像是短時間請求量
        # 累積觸發限制，加大間隔用時間換穩定性（全市場拉長到40-70分鐘）。
        time.sleep(random.uniform(2.0, 4.0))

    if progress_cb:
        try:
            progress_cb(ok_count + fail_count, total, "完成")
        except Exception:
            pass
    return {
        'ok_count': ok_count, 'fail_count': fail_count,
        'tested_count': ok_count + fail_count, 'aborted_early': aborted_early,
        'done_now': done_now,
    }


@st.cache_data(ttl=300, show_spinner=False)
def get_broker_continuity(symbol, min_days=2):
    """
    【R67新增】分點連續性分析——這是累積分點資料後才能回答的核心問題。

    把這檔股票所有存過的分點紀錄按券商分組，算出每家分點：
      - 出現天數：這家分點在幾天的日報表裡進過前段班
      - 累計買超：這幾天加總下來是淨買還是淨賣
      - 連續買超天數：從最近一天往回數，連續買超幾天（這是判斷「真建倉」
        最直接的訊號——隔日沖的特徵就是買一天、隔天就變賣超）
      - 隔日沖名單命中：這家是否在已知隔日沖分點名單裡

    判讀邏輯（誠實標註這是啟發式判斷，不是精算）：
      - 連續買超≥3天且累計淨買為正 → 🔴疑似真建倉
      - 出現天數≥3天但累計淨買接近0（在±20%總買進之間）→ 🔄疑似隔日沖/來回洗
      - 其他 → ⚪資料不足以判斷

    【R75新增】對作分點偵測——原本只有「隔日沖名單命中」這個靜態名單比對，
    這裡加上真正的模式偵測：同一天裡，如果買超最多的分點跟賣超最多的分點，
    量體很接近（誤差在20%以內），這是「對作」的典型特徵——可能是同一批
    資金透過不同分點左手倒右手（製造成交量或洗價），不是真正的多空交鋒。
    這個判讀直接用broker_flows裡已經有的資料算，不用多抓任何資料。

    回傳 (list of dict, 對作警示list)：前者依累計買超由大到小排序（原本的
    分點列表），後者是額外的「哪幾天疑似對作」清單；都可能是空list。
    """
    if not SUPABASE_ENABLED:
        return [], []

    def _do():
        return (SUPABASE_CONN.table("broker_flows").select("*")
                .eq("symbol", str(symbol)).order("log_date", desc=True)
                .limit(2000).execute())
    ok, res = _sb_safe(_do)
    rows = res.data if (ok and res is not None and getattr(res, "data", None)) else []
    if not rows:
        return [], []

    df = pd.DataFrame(rows)

    # 【R95續29修復】缺口偵測改用已經在抓的股價資料當「這幾天有沒有真的
    # 開盤」的真相來源，不用另外維護假日清單去猜週末/國定假日/颱風假。
    try:
        _hist, _ = get_real_stock_data_yfinance(symbol)
        _trading_dates = (set(_hist.index.strftime('%Y-%m-%d')) if _hist is not None and not _hist.empty
                          else None)
    except Exception:
        _trading_dates = None

    _dyn_brokers = get_dynamic_day_trader_brokers(SUPABASE_CONN) if SUPABASE_CONN else {}
    out = []
    for broker, grp in df.groupby('broker_name'):
        grp = grp.sort_values('log_date', ascending=False)
        if len(grp) < min_days:
            continue
        _net_total = int(grp['net_shares'].sum())
        _buy_total = int(grp['buy_shares'].sum())
        # 連續買超天數：從最新一天往回數，遇到第一個非買超、或遇到真正的
        # 交易日缺口就停。缺口本身也會打斷連續性（缺口期間到底是買是賣
        # 我們並不知道，不能假裝是連續的），不是只有真的查到賣超才會打斷。
        _streak, _has_gap = 0, False
        _prev_date = None
        for _, _row in grp.iterrows():
            _this_date = pd.to_datetime(_row['log_date'])
            if _prev_date is not None:
                if _trading_dates is not None:
                    _between = pd.date_range(_this_date + pd.Timedelta(days=1),
                                             _prev_date - pd.Timedelta(days=1))
                    _real_gap = any(d.strftime('%Y-%m-%d') in _trading_dates for d in _between)
                else:
                    # 股價資料抓不到時（少見），退回保守的日曆天數估計法，
                    # 沒有交易日曆依據時寧可少報缺口，也不要亂猜。
                    _real_gap = (_prev_date - _this_date).days > 4
                if _real_gap:
                    _has_gap = True
                    break
            if _row['net_shares'] > 0:
                _streak += 1
                _prev_date = _this_date
            else:
                break
        # 判讀
        if _streak >= 3 and _net_total > 0:
            _verdict = "🔴 疑似真建倉（連續買超）"
            if _has_gap:
                _verdict += "（連續天數中有資料缺口，實際連續性存疑）"
        elif len(grp) >= 3 and _buy_total > 0 and abs(_net_total) < _buy_total * 0.2:
            _verdict = "🔄 疑似隔日沖／來回洗（進出相抵）"
        else:
            _verdict = "⚪ 資料不足以判斷"
        out.append({
            '券商': broker, '出現天數': len(grp),
            '累計買超(張)': round(_net_total / 1000, 1),
            '連續買超天數': _streak,
            '判讀': _verdict + ("　⚠️名單命中" if check_day_trader_alert(broker, _dyn_brokers) else ""),
        })
    out.sort(key=lambda x: x['累計買超(張)'], reverse=True)

    # 【R75新增】對作分點偵測：逐日檢查買超龍頭跟賣超龍頭的量體是否接近
    pair_alerts = []
    for log_date, day_grp in df.groupby('log_date'):
        _buyers = day_grp[day_grp['net_shares'] > 0].sort_values('net_shares', ascending=False)
        _sellers = day_grp[day_grp['net_shares'] < 0].sort_values('net_shares')
        if _buyers.empty or _sellers.empty:
            continue
        top_buy = _buyers.iloc[0]
        top_sell = _sellers.iloc[0]
        _buy_amt, _sell_amt = float(top_buy['net_shares']), abs(float(top_sell['net_shares']))
        if _buy_amt <= 0 or _sell_amt <= 0:
            continue
        _ratio = min(_buy_amt, _sell_amt) / max(_buy_amt, _sell_amt)
        if _ratio >= 0.8:  # 量體誤差在20%以內才算「接近」
            pair_alerts.append({
                '日期': log_date,
                '買超分點': str(top_buy['broker_name']), '買超(張)': round(_buy_amt / 1000, 1),
                '賣超分點': str(top_sell['broker_name']), '賣超(張)': round(_sell_amt / 1000, 1),
                '量體接近度': f"{_ratio*100:.0f}%",
            })
    pair_alerts.sort(key=lambda x: x['日期'], reverse=True)
    return out, pair_alerts




def analyze_broker_csv(df, vol_today_shares=None):
    """
    【V160 新增：單檔分點CSV拖曳區「隔日沖照妖鏡」】把解析出來的分點明細，
    彙總成「隔日沖照妖鏡」需要的統計數字。

    vol_today_shares：當日總成交股數。優先用呼叫端傳入的真實成交量；沒傳的話
    退回「用這份CSV自己買進股數加總」估算（買賣日報表理論上買=賣，用買方
    加總當作總量的估計值，跟真正的成交量會有微小落差但同一個量級，抓不到
    真正成交量時的合理備援，不是憑空編造）。

    回傳 dict：總成交量、前五大買超彙總、隔日沖分點買超彙總與佔比、週轉率
    （需另外傳入發行股數才會算，見呼叫端）。
    """
    if df is None or df.empty:
        return None
    g = df.groupby('券商').agg(買進=('買進股數', 'sum'), 賣進=('賣出股數', 'sum'))
    g = g.rename(columns={'賣進': '賣出'})
    g['買超股數'] = g['買進'] - g['賣出']
    g = g.sort_values('買超股數', ascending=False)

    total_shares = vol_today_shares if vol_today_shares else int(df['買進股數'].sum())

    top5 = g.head(5)
    top5_buy_shares = int(top5['買超股數'].clip(lower=0).sum())
    concentration_pct = round(top5_buy_shares / total_shares * 100, 2) if total_shares > 0 else None

    # 隔日沖警示：買超為正的分點裡，命中已知名單(靜態+動態)的加總買超 ÷ 當日總量
    _dyn_brokers2 = get_dynamic_day_trader_brokers(SUPABASE_CONN) if SUPABASE_CONN else {}
    day_trader_buy_shares = int(sum(
        row['買超股數'] for broker, row in g.iterrows()
        if row['買超股數'] > 0 and check_day_trader_alert(broker, _dyn_brokers2)
    ))
    day_trader_pct = round(day_trader_buy_shares / total_shares * 100, 2) if total_shares > 0 else None

    top5_table = [{'券商': idx, '買超張': round(row['買超股數'] / 1000, 1)}
                  for idx, row in top5.iterrows()]

    return {
        'total_shares': total_shares, 'top5_table': top5_table,
        'concentration_pct': concentration_pct,
        'day_trader_buy_shares': day_trader_buy_shares, 'day_trader_pct': day_trader_pct,
    }


# 【R97搬進共用模組，見warroom_core.py】fetch_market_turnover_ranking原本
# 只在這裡，候選池篩選(system_scheduler.py新增的stage_build_intraday_pool)
# 也需要用同一份成交值排行，搬進core.py共用，這裡改成從core import。



def check_data_source_health(token=None, progress_callback=None):
    """
    【V160 新增】資料源健康度檢查——直接針對「靜默失敗」這個結構性風險。

    背景：round 6/7/9 連續三次踩到同一種坑——證交所改欄位名、營收參數矛盾、
    資料源本質限制，畫面上全都只顯示「查無資料」，沒人知道底層其實壞了，
    每次都拖了好幾輪才從畫面異常反推出來。這個函式把「壞掉」跟「本來就沒資料」
    分開，讓問題在發生當天就被發現，而不是等你察覺畫面怪怪的。

    檢查方式：對每個資料源打一次最小成本的請求，用「一定會有值的已知標的」驗證，
    回傳每個來源的 ok/失敗原因。刻意不做重試——這裡要偵測的是狀態，不是要救援。

    【R51新增】progress_callback(done, total)——每測完一個資料源就回呼一次，
    供呼叫端畫真正的進度條，取代原本「一顆轉圈圈，20-40秒完全看不出測到第幾個」。

    回傳 list of dict: {name, ok, detail}
    """
    results = []
    _TOTAL_CHECKS = 13
    _done = [0]

    def _add(name, ok, detail):
        results.append({'name': name, 'ok': bool(ok), 'detail': str(detail)})
        _done[0] += 1
        if progress_callback:
            progress_callback(_done[0], _TOTAL_CHECKS)

    # 1) yfinance 股價（整個系統的地基，壞了什麼都不用談）
    try:
        hist, _ = get_real_stock_data_yfinance('2330')
        _add('yfinance 股價', hist is not None and len(hist) > 20,
             f"取得 {len(hist) if hist is not None else 0} 根K棒")
    except Exception as e:
        _add('yfinance 股價', False, f"例外：{e}")

    # 2) FinMind 法人（用單檔模式測，因為「全市場模式」是付費方案專屬）
    try:
        url = 'https://api.finmindtrade.com/api/v4/data'
        params = {'dataset': 'TaiwanStockInstitutionalInvestorsBuySell',
                  'data_id': '2330',
                  'start_date': (datetime.now(TAIPEI_TZ) - timedelta(days=10)).strftime('%Y-%m-%d')}
        if token:
            params['token'] = token
        _payload = _finmind_get(url, params)
        _n = len(_payload.get('data', []))
        _add('FinMind 法人(單檔)', _n > 0, f"2330 近10天取得 {_n} 列")
    except FinMindAPIError as e:
        _add('FinMind 法人(單檔)', False, f"{_reason_to_label(e.reason)}（{e.reason}）")
    except Exception as e:
        _add('FinMind 法人(單檔)', False, f"例外：{e}")

    # 3) FinMind 月營收（2330 一定有營收，抓不到就是壞了）
    try:
        rev = fetch_finmind_revenue('2330', token)
        _add('FinMind 月營收', bool(rev and rev.get('ok')),
             rev.get('month', '無回應') if rev else '無回應')
    except Exception as e:
        _add('FinMind 月營收', False, f"例外：{e}")

    # 3.5) 【R95續17新增】FinMind分K資料集(TaiwanStockKBar)權限探測——文件
    # 沒寫免費方案能不能用，用最小成本(單檔單日)實測一次直接給答案。
    # permission_denied明確分辨「權限不足」跟「其他原因失敗」。
    try:
        _kbar_url = 'https://api.finmindtrade.com/api/v4/data'
        _kbar_date = get_current_or_last_trading_date()
        _kbar_params = {'dataset': 'TaiwanStockKBar', 'data_id': '2330', 'start_date': _kbar_date}
        if token:
            _kbar_params['token'] = token
        _kbar_payload = _finmind_get(_kbar_url, _kbar_params)
        _kbar_n = len(_kbar_payload.get('data', []))
        _add('FinMind 分K資料(TaiwanStockKBar)', _kbar_n > 0,
             f"✅ 免費方案可用！2330 {_kbar_date} 取得 {_kbar_n} 根分K（影響：9:30三關盤中策略可行性）"
             if _kbar_n > 0 else f"回應但無資料（可能非交易日）：{_kbar_date}")
    except FinMindAPIError as e:
        if e.reason == 'permission_denied':
            _add('FinMind 分K資料(TaiwanStockKBar)', False,
                 f"❌ 需付費方案才能用（{e.detail}）——9:30三關策略需要改走自建5分K方案")
        else:
            # 【R95續17修復】原本只顯示{reason}，把e.detail(實際HTTP狀態碼+
            # 回應內容片段)丟掉了，這才是「連線失敗查不出所以然」的根因。
            _add('FinMind 分K資料(TaiwanStockKBar)', False,
                 f"{_reason_to_label(e.reason)}（{e.reason}：{e.detail}）")
    except Exception as e:
        _add('FinMind 分K資料(TaiwanStockKBar)', False, f"例外：{e}")

    # 4) 證交所除權息預告表（欄位名稱改過一次，最容易再壞的地方）
    try:
        divs = fetch_twse_dividends()
        _add('證交所除權息表', isinstance(divs, dict) and len(divs) > 0,
             f"取得 {len(divs) if divs else 0} 檔")
    except Exception as e:
        _add('證交所除權息表', False, f"例外：{e}")

    # 5) 成交值排行（掃描池排序依賴這個）
    try:
        rank = fetch_market_turnover_ranking()
        _add('全市場成交值排行', len(rank) > 100, f"取得 {len(rank)} 檔")
    except Exception as e:
        _add('全市場成交值排行', False, f"例外：{e}")

    # 6) 產業分類（族群輪動依賴這個）
    try:
        s2i, _ = fetch_industry_map()
        _add('FinMind 產業分類', len(s2i) > 100, f"取得 {len(s2i)} 檔")
    except Exception as e:
        _add('FinMind 產業分類', False, f"例外：{e}")

    # 7) 【R75新增】TDCC千張大戶——測試opendata.tdcc.com.tw(R70查證過的
    # 免費路徑)還通不通，第三方網站沒有服務保證，這裡明講影響範圍。
    try:
        # 【R76修復】上一輪timeout=15違背了函式自己docstring的道理（全市場
        # CSV檔案不小，函式預設值30秒是為此考量），改成不覆寫用函式預設值。
        _raw = fetch_tdcc_holding_csv_direct()
        _ok7 = _raw is not None and len(_raw) > 1000
        _add('TDCC 千張大戶(opendata)', _ok7,
             f"取得 {len(_raw) if _raw else 0} bytes（影響：千張大戶趨勢因子、"
             f"排程週六自動抓取）")
    except Exception as e:
        _add('TDCC 千張大戶(opendata)', False, f"例外：{e}（影響：千張大戶趨勢因子）")

    # 8) 【R75/R93修復】HiStock券商分點健康度——最脆弱的資料源，改成看
    # 總長度+全文搜尋表格關鍵字，才能分辨「被擋掉」還是「表格在更後面」。
    try:
        _df8 = fetch_histock_branch_data('2330', timeout=15)
        _ok8 = _df8 is not None and not _df8.empty
        if _ok8:
            _add('HiStock 券商分點', True,
                 f"2330取得 {len(_df8)} 家分點（影響：分點連續性分析、排程每日自動抓取）")
        else:
            # 【R94新增】本地電腦缺lxml套件時pd.read_html()會拋ImportError，
            # 之前被吞掉、跟「表格結構不符」長得一樣，誤導成懷疑IP被擋。
            # 這裡直接測一次，一眼看出是不是這個原因。
            try:
                import lxml  # noqa: F401
                _lxml_ok = True
            except ImportError:
                _lxml_ok = False
            if not _lxml_ok:
                _add('HiStock 券商分點', False,
                     "❌缺少lxml套件！pandas.read_html()需要這個套件才能解析HTML表格，"
                     "沒裝的話每次都會失敗、但錯誤訊息容易被誤判成IP被擋或網站改版。"
                     "請確認requirements.txt有列出lxml，這不是網站或連線問題，是部署"
                     "環境設定問題。（影響：分點連續性分析、排程每日自動抓取）")
            else:
                _diag_detail = "解析失敗，原因不明"
                try:
                    _diag_headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                     "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
                    _diag_r = _SESSION.get("https://histock.tw/stock/branch.aspx?no=2330",
                                           headers=_diag_headers, timeout=15)
                    _diag_len = len(_diag_r.text)
                    _diag_markers = ["券商分點買賣日報", "券商名稱", "買張", "賣張"]
                    _diag_found = {m: (m in _diag_r.text) for m in _diag_markers}
                    _all_found = all(_diag_found.values())
                    if _all_found:
                        # 【R95續21加強診斷】關鍵字找得到但還是失敗，代表問題
                        # 比「表格順序換了」更深，直接印出每張表的欄位清單，
                        # 不用再猜是換了欄位命名還是根本沒用<table>包。
                        try:
                            _diag_tables = pd.read_html(io.StringIO(_diag_r.text))
                            _diag_table_cols = [list(t.columns) for t in _diag_tables]
                        except Exception as _diag_te:
                            _diag_table_cols = [f"pd.read_html本身就失敗：{_diag_te}"]
                        _diag_detail = (f"HTTP {_diag_r.status_code}，內容長度{_diag_len}字元，"
                                       f"表格關鍵字全部找得到，lxml套件也確認有裝，"
                                       f"pd.read_html()解析出{len(_diag_table_cols) if isinstance(_diag_table_cols, list) and not isinstance(_diag_table_cols[0], str) else '?'}"
                                       f"張表——每張表的欄位：{str(_diag_table_cols)[:600]}")
                    else:
                        _missing = [m for m, f in _diag_found.items() if not f]
                        _diag_detail = (f"HTTP {_diag_r.status_code}，內容長度{_diag_len}字元，"
                                       f"缺少關鍵字：{_missing}（內容長度太短或缺關鍵字，"
                                       f"代表拿到的很可能不是真正的分點頁面，可能是被擋或跳轉到"
                                       f"其他頁面）。回應前200字：{_diag_r.text[:200]!r}")
                except Exception as _diag_e:
                    _diag_detail = f"連診斷請求都失敗：{_diag_e}"
                _add('HiStock 券商分點', False,
                     f"取得0家分點，{_diag_detail}（影響：分點連續性分析、排程每日自動抓取——"
                     f"這是最依賴第三方網站結構的資料源，最容易因對方改版或封鎖雲端IP而失效）")
    except Exception as e:
        _add('HiStock 券商分點', False, f"例外：{e}（影響：分點連續性分析）")

    # 9) 【R79新增】處置股/注意股+重大訊息——三個端點一起測，任一個能連上
    # 就算部分正常，畫面上分開列出來，不會因為其中一個掛掉就整組判失敗。
    try:
        _att9 = fetch_twse_attention_stocks(timeout=15)
        _add('TWSE 注意股', _att9 is not None, f"取得 {len(_att9) if _att9 else 0} 筆（影響：注意股警示）")
    except Exception as e:
        _add('TWSE 注意股', False, f"例外：{e}")
    try:
        _disp9 = fetch_twse_disposal_stocks(timeout=15)
        _add('TWSE 處置股', _disp9 is not None, f"取得 {len(_disp9) if _disp9 else 0} 筆（影響：處置股警示）")
    except Exception as e:
        _add('TWSE 處置股', False, f"例外：{e}")
    try:
        _tpex9 = fetch_tpex_disposal_stocks(timeout=15)
        _add('TPEx 處置股', _tpex9 is not None, f"取得 {len(_tpex9) if _tpex9 else 0} 筆（影響：上櫃處置股警示）")
    except Exception as e:
        _add('TPEx 處置股', False, f"例外：{e}")
    try:
        _ann9 = fetch_twse_material_announcements(timeout=15)
        _add('TWSE 重大訊息', _ann9 is not None,
             f"取得 {len(_ann9) if _ann9 else 0} 筆（影響：自結財報推播）")
    except Exception as e:
        _add('TWSE 重大訊息', False, f"例外：{e}")

    return results


@st.cache_data(ttl=86400, show_spinner=False)
def get_industry_leader_proxy(ind, exclude_code=None):
    """
    【R95新增】戰情速覽固定顯示個股的產業龍頭，供對照觀察——同一個「沒有免費
    市值資料」的限制（見同產業族群強弱面板的說明），這裡用同一套「今日成交值
    (現價×成交量)代理指標」找出該產業裡交投最熱絡的一檔，當作「近似龍頭」。
    掛@st.cache_data(ttl=86400)——一天只需要真的算一次，戰情速覽每次互動
    重跑整支程式時不會重複對15檔同業各打一次資料請求。
    回傳 (leader_code, leader_name) 或 (None, None)（查無資料時）。

    【R96調整：15檔→5檔】這個函式內部是序列迴圈逐一查同業價格，且完全沒有
    自己的逾時/併發保護——冷快取(容器剛重開機)時，如果呼叫端(戰情速覽的
    龍頭補列)一次對多個不同產業並行呼叫這個函式，每個產業各自序列查最多
    15檔同業，疊加起來很容易在短時間內對yfinance/Yahoo發出上百次請求，
    總指揮官反映因此觸發大量「possibly delisted」失敗（很可能是Yahoo端的
    防機器人限流，不是這些股票真的有問題）。這裡只是「找出成交值最高的
    一檔當代理龍頭」，5檔候選跟15檔候選找到的近似龍頭多半是同一檔（成交值
    最熱絡的股票通常不會排在產業清單很後面），用5檔大幅降低每次查詢的
    請求量，換取「這個錦上添花功能」不再拖累/連累主體資料的穩定性。
    """
    _, ind_to_stocks = fetch_industry_map()
    # 【R96新增——固定龍頭對照表，取代逐次動態查詢】對錶上有的產業直接零
    # 成本查表回傳，不打任何API。這份表已搬進warroom_core.py的FIXED_
    # INDUSTRY_LEADERS（排程端也要用，單一事實來源，這裡引用共用版本）。
    if ind in FIXED_INDUSTRY_LEADERS:
        _fixed_code, _fixed_name = FIXED_INDUSTRY_LEADERS[ind]
        if _fixed_code != exclude_code:
            return _fixed_code, _fixed_name
        # 龍頭剛好就是自己（例如查台積電本身在半導體業的龍頭）——這種情況
        # 顯示「自己是龍頭」沒有意義，退回下面動態查詢找族群裡的第二名。

    peers = [s for s in ind_to_stocks.get(ind, []) if s != exclude_code and s in TW_STOCK_NAMES][:5]
    best_code, best_turnover = None, -1.0
    for p in peers:
        hp, _ = get_real_stock_data_yfinance(p)
        if hp is not None and len(hp) >= 1:
            try:
                _turnover = float(hp['Close'].iloc[-1]) * float(hp['Volume'].iloc[-1])
            except Exception:
                continue
            if _turnover > best_turnover:
                best_turnover, best_code = _turnover, p
    if best_code is None:
        return None, None
    return best_code, TW_STOCK_NAMES.get(best_code, best_code)


TW_STOCK_NAMES = fetch_stock_names()
DIVIDEND_DB = fetch_twse_dividends()
# 【V160修復】族群輪動清單原本照FinMind API原始順序(未排序)，「前N檔」
# 是任意子集沒有代表性。改成依股票代碼數字排序，至少是穩定可重現的子集，
# 零額外成本(非完美解，理想上該按成交量/市值排序)。
GLOBAL_MARKET_CODES = sorted(TW_STOCK_NAMES.keys(), key=_sort_key)

# 【R98續110第八輪】TW_STOCK_NAMES/GLOBAL_MARKET_CODES是真正的執行期資料
# (fetch_stock_names()查來的)，一樣用依賴注入。SCAN_COMMAND_MAP是可變
# 字典(在別處用SCAN_COMMAND_MAP[key]=value動態填入)，這裡做「一次性」
# 注入同一個物件參照就足夠——之後不管在dashangdao.py哪裡對它做的修改，
# 因為是同一個dict物件，dashangdao_helpers.py那邊會自動同步看到，不需要
# 每次修改後都重新注入一次。
dashangdao_helpers.TW_STOCK_NAMES = TW_STOCK_NAMES
dashangdao_helpers.GLOBAL_MARKET_CODES = GLOBAL_MARKET_CODES
dashangdao_helpers.SCAN_COMMAND_MAP = SCAN_COMMAND_MAP


# 【R95續26】拿掉@st.cache_data，改用函式內的_smart_cached_call——理由見
# 函式docstring：@st.cache_data會把「失敗時回傳的空集合」誤當成成功結果
# 鎖住6小時，這是這輪抓到的重大bug。

# 【V160 Round39】fetch_twse_mis_batch/_safe_mis_float已搬進warroom_
# core.py，這裡直接import。_get_live_quotes_cached是網頁版專屬15秒快取，
# 排程端不需要(一次性腳本不會有重複呼叫問題)。

def attach_live_quotes(cards_map, fetch_intraday_extras=False):
    """
    【V160 Round38 新增】幫一批已經算好的戰卡（持倉/雷達/觀察）疊加「即時報價」
    顯示層，解決總指揮官反映的「戰卡股價跟不上盤中變化」問題。

    【R96架構調整】原本用一個全域的側邊欄「波段/當沖模式」開關，決定要不要
    多查VWAP/9:30三關這兩項需要額外Supabase查詢的資料——總指揮官實測後
    指出：這樣切換「兩者資料沒有太大變化」，因為五檔/反彈健康度/流動性
    這些當沖真正需要的東西，本來就不受這個開關影響、任何時候都會顯示。
    真正該用開關控制的，其實是「現在是在看戰情速覽這種大批量表格，還是
    在看單一檔的完整戰卡」——前者要快、後者不在乎多查一點。這裡改成由
    呼叫端明確傳入fetch_intraday_extras（不是猜的、不是全域狀態），
    True時才會多查VWAP/9:30三關；戰情速覽這種大批量呼叫維持False（快），
    查看單一檔完整戰卡的呼叫端傳True（資料完整）。拿掉全域模式開關後，
    不用再擔心「使用者忘記切換模式」這種問題。

    刻意設計：只加 live_price/live_time/live_change_pct 這幾個新欄位，
    **完全不動 c['price']/c['gain'] 這些既有欄位**——那些是技術指標、評分、
    出場檢查、模擬倉損益在用的，這次的問題只是「顯示跟不上」，不是「判斷邏輯
    要即時」，動了判斷用的價格反而會帶來新的風險（例如評分算出來的分數突然
    跟畫面上其他還沒更新的東西對不上）。即時報價純粹是多顯示一行給你看，
    不影響任何決策計算。

    只有一次批次網路呼叫（不管幾檔股票），符合證交所端點的頻率限制考量。

    【R92修復】總指揮官回報：一次查8檔，即時報價只有2檔查得到，按重新整理
    也不會補齊。查出根因：原本_EXT_HINT.get(code)沒有值時，直接猜"tse"
    （預設多數股票是上市）——如果那檔股票其實是上櫃(otc)，猜錯的話對
    TWSE MIS查詢會用錯交易所前綴，那一檔就完全查不到資料，而_EXT_HINT
    只有在「該股票剛好在別的地方走過yfinance fallback路徑」時才會被動
    補上，不是每次都會發生，這解釋了「重新整理也不會補齊」——因為問題
    根本不是網路暫時失敗，是猜錯之後這次呼叫本來就查不到。

    改成優先查fetch_listed_only_codes()（已經是快取6小時的既有資料集，
    不用多打API）：在這個集合裡→確定是上市(tse)；不在→視為上櫃(otc)，
    不再靠運氣猜。_EXT_HINT仍然保留當作次要來源（例如興櫃股不在
    fetch_listed_only_codes的上市清單裡，但可能之前yfinance fallback時
    已經正確判斷過）。
    """
    if not cards_map:
        return cards_map
    try:
        _listed_set = fetch_listed_only_codes()
    except Exception as e:
        print(f"[attach_live_quotes-診斷] fetch_listed_only_codes失敗，退回_EXT_HINT猜測："
              f"{type(e).__name__}: {e}")
        _listed_set = set()
    pairs = []
    for code in cards_map:
        if _listed_set:
            ex = "tse" if code in _listed_set else "otc"
        else:
            # fetch_listed_only_codes整個抓失敗時的保底：退回原本的_EXT_HINT
            # 猜測法，總比完全不查好，但誠實承認這種情況下準確度較低。
            _hint = _EXT_HINT.get(code)
            ex = "otc" if _hint == ".TWO" else "tse"
        pairs.append((code, ex))
    # 【R96新增，診斷用】總指揮官反映部分股票的即時報價長期顯示"—"，懷疑
    # 是交易所判斷錯誤——這裡把這次批次查詢用的tse/otc完整記錄下來，跟
    # fetch_twse_mis_batch內部「這批完全沒回應」的診斷log對照，就能直接
    # 確認是不是判斷錯了：如果某代號這裡猜tse、但那個代號其實是otc股，
    # 這批查詢對TWSE伺服器來說等於查一個根本不存在的組合，會落在
    # 「完全沒回應」那個分支，兩邊log的代號應該要能對得上。
    print(f"[attach_live_quotes-診斷] 本次交易所判斷（前20筆）：{pairs[:20]}"
          f"{'...(還有' + str(len(pairs)-20) + '筆)' if len(pairs) > 20 else ''}")
    try:
        live = _get_live_quotes_cached(tuple(sorted(pairs)))
    except Exception as e:
        print(f"[戰卡即時報價] 批次抓取失敗：{e}")
        live = {}
    # 【R95續14修復】查不到「這一刻」成交時，退回沿用「上一次真的查到」的
    # 那筆資料(含真實時間戳，不是冒充現在)，不再直接顯示「—」誤會成沒資料。
    # 快取存在session_state，跟著瀏覽器session活。
    _last_cache = st.session_state.setdefault('_last_live_quote_cache', {})
    # 【R97續4新增，總指揮官要求：冷啟動也能秒補上一筆】_last_cache是
    # session_state，container剛重開機/使用者第一次進站時是空的，這種
    # 情況下即使上一個使用者一分鐘前才成功抓過同一檔，這次還是會顯示
    # "—"，要重新輪詢到才會補上。這裡加一層跨session的持久化快取
    # (live_quote_cache表)——只對這次session_state裡完全沒有的代號才
    # 查(避免每次都多打Supabase)，查到就當退回值使用，並標記
    # live_is_carried_persistent=True（跟同session內的⏳沿用要分開標示，
    # 這筆可能是幾分鐘前甚至上一個交易時段的資料，可信度更低，要更明顯
    # 提醒使用者這不是「這個session剛查到過」那麼新鮮）。
    _persistent_cache = {}
    if SUPABASE_CONN is not None:
        _need_persistent = [c for c in cards_map if c not in _last_cache]
        if _need_persistent:
            try:
                _pc_res = (SUPABASE_CONN.table("live_quote_cache")
                          .select("symbol,price,quote_time,quote_date,change_pct,"
                                  "open,high,low,prev_close,updated_at")
                          .in_("symbol", _need_persistent).execute())
                for _row in (_pc_res.data or []):
                    _persistent_cache[_row['symbol']] = _row
            except Exception as e:
                print(f"[即時報價-持久化快取] 批次查詢失敗（不影響其他功能，退回原本"
                      f"「—」的誠實顯示）：{type(e).__name__}: {e}")
    _persistent_writeback = []   # 這次成功查到的，迴圈跑完後一次批次寫回，不逐檔寫
    # 【R96新增，Step 5五檔節奏】跟即時報價共用同一批網路請求，fetch_twse_
    # mis_batch已多回傳bids/asks。prev_bids存session_state供is_thickening
    # 判斷墊高趨勢。
    _prev_bids_cache = st.session_state.setdefault('_prev_order_book_bids', {})

    # 【R96新增，累積清單第7項】批次查詢今天的5分K bars供VWAP計算，一次
    # IN查詢不逐檔查。
    # 【R96修復，效能回歸bug，見開發歷程.md】原本沒限定只在需要時才查，
    # 是速覽變慢的根因，改用fetch_intraday_extras參數控制。
    _bars_by_code = {}
    if SUPABASE_CONN is not None and cards_map and fetch_intraday_extras:
        try:
            _today_str = get_current_or_last_trading_date()
            _res = (SUPABASE_CONN.table("intraday_5min_bars")
                    .select("symbol,bar_time,open,high,low,close,volume,outer_volume,inner_volume")
                    .eq("trade_date", _today_str)
                    .in_("symbol", list(cards_map.keys()))
                    .execute())
            for row in (_res.data or []):
                _bars_by_code.setdefault(row['symbol'], []).append(row)
        except Exception as e:
            print(f"[VWAP] 批次查詢5分K失敗：{e}")

    # 【R96新增】批次查詢今天的5分K三關（查15）判斷結果，system_scheduler.py
    # 算好寫進Supabase，這裡只讀取。同樣只在fetch_intraday_extras=True時查，
    # 一次IN查詢拿齊全部代號。
    _gate_results_by_code = {}
    if SUPABASE_CONN is not None and cards_map and fetch_intraday_extras:
        try:
            _today_str = get_current_or_last_trading_date()
            _gres = (SUPABASE_CONN.table("intraday_gate_results")
                    .select("symbol,direction,overall_verdict,overall_label,gate1_verdict,gate2_verdict,gate3_verdict,detail")
                    .eq("trade_date", _today_str)
                    .in_("symbol", list(cards_map.keys()))
                    .execute())
            for row in (_gres.data or []):
                _gate_results_by_code[row['symbol']] = row
        except Exception as e:
            print(f"[9:30三關-讀取] 批次查詢失敗：{e}")

    for code, c in cards_map.items():
        # 【R96新增，當沖模式】不管這次即時報價有沒有查到（q是否為None），
        # 9:30三關的結果都先掛上去——那是排程另外算好的，不依賴這次即時
        # 報價成不成功。
        c['intraday_gate'] = _gate_results_by_code.get(code)
        q = live.get(code)
        if q and q.get('ok'):
            # 這次真的查到最新成交，用最新的，同時更新快取供下次沒查到時沿用。
            c['live_price'] = q['price']
            c['live_time'] = q.get('time', '')
            c['live_date'] = q.get('date', '')
            c['live_change_pct'] = q.get('change_pct')
            c['live_is_carried'] = False
            # 【R97修復，見開發歷程.md「開高低對不上排查」章節】總指揮官
            # 實測抓到：yfinance的每日資料在盤中對某些股票會慢一整天才
            # 更新，導致open_today/high_today/low_today/prev_close這幾個
            # 欄位(算法是拿hist最後一列當「今天」)實際上顯示的是前一天的
            # 資料，跟真實奇摩股市的今天開高低對不起來。這裡即時報價本來
            # 就有真正即時的open/high/low/prev_close欄位(來自mis.twse.com.tw
            # 這個真正即時的來源)，查到的話優先覆蓋這幾格，不再讓它們
            # 依賴容易延遲一整天的yfinance每日資料——跟「即時價優先於
            # 決策基準價」是同一個道理，只是這次補齊到開高低這幾格。
            if q.get('open') is not None:
                c['open_today'] = q['open']
            if q.get('high') is not None:
                c['high_today'] = q['high']
            if q.get('low') is not None:
                c['low_today'] = q['low']
            if q.get('prev_close') is not None:
                c['prev_close'] = q['prev_close']
            _last_cache[code] = {
                'price': q['price'], 'time': q.get('time', ''),
                'date': q.get('date', ''), 'change_pct': q.get('change_pct'),
                'open': q.get('open'), 'high': q.get('high'),
                'low': q.get('low'), 'prev_close': q.get('prev_close'),
            }
            # 【R97續4新增】這次真的查到，順便累積供迴圈跑完後批次寫回
            # live_quote_cache，供下一個冷啟動的session/使用者沿用。
            _persistent_writeback.append({
                'symbol': code, 'price': q['price'], 'quote_time': q.get('time', ''),
                'quote_date': q.get('date', ''), 'change_pct': q.get('change_pct'),
                'open': q.get('open'), 'high': q.get('high'),
                'low': q.get('low'), 'prev_close': q.get('prev_close'),
                'updated_at': datetime.now(TAIPEI_TZ).isoformat(),
            })
            try:
                _bids, _asks = q.get('bids', []), q.get('asks', [])
                # 【R96新增，內外盤成交比率】用_bars_by_code(前面已批次
                # 查過)加總outer_volume/inner_volume，補完附件38的完整判斷。
                # 沒有內外盤資料時函式自動退回只看掛單厚度的partial版本。
                _today_bars_for_ob = _bars_by_code.get(code)
                _outer_sum = _inner_sum = None
                if _today_bars_for_ob:
                    _outer_sum = sum(float(b.get('outer_volume') or 0) for b in _today_bars_for_ob)
                    _inner_sum = sum(float(b.get('inner_volume') or 0) for b in _today_bars_for_ob)
                c['order_book'] = evaluate_order_book_pressure(
                    _bids, _asks, prev_bids=_prev_bids_cache.get(code),
                    outer_volume=_outer_sum, inner_volume=_inner_sum)
                if _bids:
                    _prev_bids_cache[code] = _bids
            except Exception as e:
                print(f"[attach_live_quotes-診斷] {code} 五檔買盤結構計算失敗：{type(e).__name__}: {e}")
                c['order_book'] = None
            # 【R96新增，累積清單第9項】今日流動性過濾器——跟五檔共用
            # 同一次請求，用即時累計量對比戰卡已算好的vol_5d_mean。
            try:
                c['liquidity'] = evaluate_today_liquidity_by_avg(
                    q.get('volume_cum'), c.get('vol_5d_mean'))
            except Exception as e:
                print(f"[attach_live_quotes-診斷] {code} 今日流動性計算失敗：{type(e).__name__}: {e}")
                c['liquidity'] = None
            # 【R96新增，累積清單第7項】Step 1收盤強弱升級版——用今天的
            # 5分K反推近似VWAP，跟原本的高低區間百分位是互補的兩種角度。
            try:
                _today_bars = _bars_by_code.get(code)
                _vwap = calc_intraday_vwap_from_bars(_today_bars) if _today_bars else None
                c['vwap_position'] = evaluate_vwap_position(q.get('price'), _vwap)
            except Exception as e:
                print(f"[attach_live_quotes-診斷] {code} VWAP位置計算失敗：{type(e).__name__}: {e}")
                c['vwap_position'] = None
            # 【R96新增】當沖操作建議整合層——這是這張卡此刻所有當沖
            # 相關欄位第一次全部到齊的時間點，在這裡統一綜合，不用使用者
            # 自己一項一項比對數字。
            try:
                c['daytrade_recommendation'] = evaluate_daytrade_recommendation({
                    'trend_gate': c.get('trend_gate'),
                    'intraday_gate': c.get('intraday_gate'),
                    'pullback_health': c.get('pullback_health'),
                    'closing_strength': c.get('closing_strength'),
                    'volume_followthrough': c.get('volume_followthrough'),
                    'rebound_health': c.get('rebound_health'),
                    'day_trader_ratio': c.get('day_trader_ratio'),
                    'margin_regime': c.get('margin_regime'),
                    'vwap_position': c.get('vwap_position'),
                    'order_book': c.get('order_book'),
                    'rsi_dual': c.get('rsi_dual'),
                    'liquidity': c.get('liquidity'),
                })
            except Exception as e:
                print(f"[attach_live_quotes-診斷] {code} 當沖操作建議整合失敗：{type(e).__name__}: {e}")
                c['daytrade_recommendation'] = None
        elif code in _last_cache:
            # 這次沒有最新成交，沿用上一次真的查到的那筆——時間戳也是沿用
            # 那筆「當時」的時間，不是現在，畫面上會誠實顯示是幾點的資料。
            #
            # 【R98續67新增，總指揮官反映戰情速覽出現8小時前的異常時間戳】
            # 查明是R98續66修復Shioaji時區bug前寫進這份session快取的舊
            # 資料殘留——機制本身設計合理(沿用比顯示"—"好、且有誠實標示)，
            # 但沒有「太舊就不要再沿用」的保護，導致有bug的舊快取污染
            # 可能會被無限期一直沿用下去，直到剛好又抓到新資料為止。這裡
            # 加上30分鐘的沿用時限——盤中報價本來就不該延遲這麼久，超過
            # 這個時限的舊快取，寧可誠實顯示「查不到」，不要繼續假裝是
            # 堪用的參考資料，也能讓類似這次的殘留污染問題自然在30分鐘內
            # 停止影響畫面，不用等使用者自己發現才重新整理。
            _prev = _last_cache[code]
            _prev_date = _prev.get('date', '')
            _is_stale = True
            if _prev_date == datetime.now(TAIPEI_TZ).strftime('%Y%m%d') and _prev.get('time'):
                try:
                    _prev_dt = datetime.strptime(
                        f"{_prev_date} {_prev['time']}", '%Y%m%d %H:%M:%S').replace(tzinfo=TAIPEI_TZ)
                    # 【R98續67修復，獨立測試抓到的邊界bug】原本用
                    # (now - prev_dt) > 1800單方向判斷「太舊」，但這次
                    # 真實案例的time是異常地「超前」於現在(8小時時區偏移
                    # 導致顯示的時刻比現在還晚)，(now - prev_dt)會是負數，
                    # 負數不會大於1800，反而被誤判成「沒有過期」，這個
                    # 防護對這次真實情境完全沒作用。改用絕對值：不管是
                    # 「太舊(過去)」還是「時刻異常超前(看起來像未來)」，
                    # 只要跟現在的時間差距超過30分鐘，都是不可信的異常
                    # 資料，一律不沿用——正常的即時報價time本來就不該
                    # 比現在的查詢時間還晚，這種情況本身就是異常訊號。
                    _is_stale = abs((datetime.now(TAIPEI_TZ) - _prev_dt).total_seconds()) > 1800
                except (ValueError, TypeError):
                    _is_stale = True
            if _is_stale:
                pass   # 太舊，誠實跳過沿用，這格維持原本查詢失敗時的預設值(通常顯示"—")
            else:
                c['live_price'] = _prev['price']
                c['live_time'] = _prev['time']
                c['live_date'] = _prev['date']
                c['live_change_pct'] = _prev['change_pct']
                c['live_is_carried'] = True
                # 【R97新增】開高低/昨收也一併沿用上次真的查到的即時值，跟
                # live_price同一套邏輯，不要這幾格繼續退回容易延遲一天的
                # yfinance每日資料。
                if _prev.get('open') is not None:
                    c['open_today'] = _prev['open']
                if _prev.get('high') is not None:
                    c['high_today'] = _prev['high']
                if _prev.get('low') is not None:
                    c['low_today'] = _prev['low']
                if _prev.get('prev_close') is not None:
                    c['prev_close'] = _prev['prev_close']
        elif code in _persistent_cache:
            # 【R97續4新增】這個session從沒查到過，但跨session的持久化
            # 快取有上一次(可能是別的使用者、或這個session更早的頁面)真的
            # 查到的那筆——沿用，並用🧊(不是⏳)明確標示「這不是這次頁面
            # 期間查到的，是冷啟動補的，可能已經有一段時間」，可信度標示
            # 要跟同session內的⏳沿用區分開。
            #
            # 【R98續69新增，總指揮官反映「重新整理後問題依然存在」】
            # 這是真正的根因——這個live_quote_cache表存在Supabase(跨
            # session)，完全不受瀏覽器重新整理影響(重新整理只會清空
            # session_state裡的_last_live_quote_cache，這張Supabase表
            # 完全不受影響)。R98續67只修了session內的_last_cache，這裡
            # 是第三個獨立的快取層，同樣完全沒有時效性判斷，一旦寫入了
            # 帶bug的舊資料(例如R98續66修復前寫入的異常時間戳)，會一直
            # 被沿用到「剛好又有一次新的成功查詢覆蓋它」為止，可能持續
            # 好幾天。加上同一套30分鐘時限防護，邏輯跟_last_cache那邊
            # 完全一致(包含用abs()處理「異常超前」的情況，不是只判斷
            # 「太舊」)。
            _pc = _persistent_cache[code]
            _pc_date = _pc.get('quote_date', '')
            _pc_is_stale = True
            if _pc_date == datetime.now(TAIPEI_TZ).strftime('%Y%m%d') and _pc.get('quote_time'):
                try:
                    _pc_dt = datetime.strptime(
                        f"{_pc_date} {_pc['quote_time']}", '%Y%m%d %H:%M:%S').replace(tzinfo=TAIPEI_TZ)
                    _pc_is_stale = abs((datetime.now(TAIPEI_TZ) - _pc_dt).total_seconds()) > 1800
                except (ValueError, TypeError):
                    _pc_is_stale = True
            if not _pc_is_stale:
                c['live_price'] = _pc['price']
                c['live_time'] = _pc.get('quote_time', '')
                c['live_date'] = _pc.get('quote_date', '')
                c['live_change_pct'] = _pc.get('change_pct')
                c['live_is_carried'] = True
                c['live_is_carried_persistent'] = True   # 供畫面顯示🧊而不是⏳
                if _pc.get('open') is not None:
                    c['open_today'] = _pc['open']
                if _pc.get('high') is not None:
                    c['high_today'] = _pc['high']
                if _pc.get('low') is not None:
                    c['low_today'] = _pc['low']
                if _pc.get('prev_close') is not None:
                    c['prev_close'] = _pc['prev_close']
        # 三種情況都沒有(從來沒查到過這檔的即時成交，連持久化快取都沒有)：
        # 維持原樣不加欄位，畫面上該欄位仍然是"—"——這種情況下顯示"—"
        # 才是誠實的，不是bug，因為根本沒有任何一筆真實成交可以沿用，
        # 很可能是這檔股票整個系統從沒成功查過一次(新加入雷達/剛掛牌)。
        else:
            print(f"[即時報價-診斷] {code}：這次沒查到，session快取跟持久化快取"
                  f"都沒有上一筆可沿用——這檔股票整個系統目前沒有任何一筆"
                  f"成功查到過的即時報價紀錄。")

    # 【R97續4新增】批次寫回這次成功查到的，供下次冷啟動沿用。放在迴圈
    # 外一次upsert，不逐檔寫，不增加額外的Supabase呼叫次數。任何失敗都
    # 不影響本次畫面顯示——這只是「幫下一次」，不是這次判斷邏輯的一部分。
    if SUPABASE_CONN is not None and _persistent_writeback:
        try:
            SUPABASE_CONN.table("live_quote_cache").upsert(
                _persistent_writeback, on_conflict="symbol").execute()
        except Exception as e:
            print(f"[即時報價-持久化快取] 批次寫回失敗（不影響本次畫面顯示）："
                  f"{type(e).__name__}: {e}")
    return cards_map


def fetch_finmind_taiex():
    """
    【V160 Round37 新增】用 FinMind TaiwanVariousIndicators5Seconds 抓台股加權指數。
    這是 FinMind 官方的加權指數資料集，用我們整個專案本來就在用、證實穩定的
    FinMind 基礎設施，不再讓大盤指數繼續依賴已經證實對台股有延遲問題的
    yfinance 備援。

    【查證過的正確參數格式】官方文件範例：不用帶 data_id，只帶
    dataset=TaiwanVariousIndicators5Seconds + start_date；欄位是 date(str) +
    TAIEX(float64) —— 不是猜測，是照官方 API 參考文件核對過的。

    回傳 (指數值, 前一筆指數值, 日期字串) 或 None（抓不到時誠實放棄，呼叫端會退回其他來源）。
    """
    try:
        token = get_active_fm_token()
        url = 'https://api.finmindtrade.com/api/v4/data'
        start_date = (datetime.now(TAIPEI_TZ) - timedelta(days=10)).strftime('%Y-%m-%d')
        params = {'dataset': 'TaiwanVariousIndicators5Seconds', 'start_date': start_date}
        if token:
            params['token'] = token
        payload = _finmind_get(url, params)
        df = pd.DataFrame(payload.get('data', []))
        if df.empty or 'date' not in df.columns or 'TAIEX' not in df.columns:
            return None
        df['date'] = pd.to_datetime(df['date'])
        df = df.sort_values('date').dropna(subset=['TAIEX'])
        # 【V160】這個資料集是「每5秒一筆」，同一天可能有很多筆，只取每天最後一筆
        # 當作該日收盤指標，避免把盤中某個瞬間誤當成收盤值
        df = df.groupby(df['date'].dt.date).last().reset_index(drop=True)
        if len(df) < 1:
            return None
        latest = df.iloc[-1]
        prev_val = float(df.iloc[-2]['TAIEX']) if len(df) >= 2 else None
        return float(latest['TAIEX']), prev_val, pd.Timestamp(latest['date']).strftime('%m/%d')
    except FinMindAPIError as _e:
        print(f"[fetch_finmind_taiex-診斷] FinMind抓大盤指數失敗：{type(_e).__name__}: {_e}")
        return None
    except Exception as _e:
        print(f"[fetch_finmind_taiex-診斷] 非預期例外：{type(_e).__name__}: {_e}")
        return None


@st.cache_data(ttl=20, show_spinner=False)
def get_market_weather_real():
    """
    【V160 Round38 修復】總指揮官反映：FinMind/證交所MI_INDEX/yfinance 全部都是
    「收盤後才更新」的資料源，不管換哪一個都不會有真正的盤中即時性——這不是
    哪個來源做得好不好，是這整批來源從設計上就是給「日頻決策」用的。
    改用證交所「基本市況報導」即時端點（約5秒更新一次）當最優先層，這是
    確認過的、真正意義上的「即時」，不是「收盤後比較快更新」。
    快取時間也從300秒縮到20秒，符合「即時」這個定位該有的更新頻率。
    優先順序：即時報價(新) → FinMind → 證交所官方 → yfinance備援。
    """
    # 第一優先層（新，真正即時）：證交所即時報價端點，加權指數代號t00
    # 【R98續63修復】改用fetch_live_quotes_resilient()加上重試機制——
    # 大盤指數Shioaji(永豐金券商API)本身不會有這筆資料，備援對這裡意義
    # 不大，但「重試2次」的韌性仍然有幫助，且統一全系統呼叫方式，不留
    # 這一處還在用沒有重試機制的舊版函式。
    try:
        _live, _ = fetch_live_quotes_resilient([("t00", "tse")])
        if "t00" in _live and _live["t00"]["change_pct"] is not None:
            _q = _live["t00"]
            _arrow = "▲" if _q["change_pt"] > 0 else ("▼" if _q["change_pt"] < 0 else "▬")
            _color = "#ff4d4d" if _q["change_pt"] > 0 else ("#00c853" if _q["change_pt"] < 0 else "#999")
            _time_tag = f"・{_q['time']}" if _q.get('time') else ""
            return (f"{_q['price']:,.0f} ({_arrow} {abs(_q['change_pt']):,.0f}點 | "
                    f"{_q['change_pct']:+.2f}%)（即時{_time_tag}）", _color, _q['change_pct'])
    except Exception as e:
        print(f"[大盤氣象-即時報價] 失敗：{e}")

    # 第零層：FinMind 官方加權指數資料集，用整個專案已驗證穩定的基礎設施
    try:
        _fm_result = fetch_finmind_taiex()
        if _fm_result is not None:
            _c_idx, _prev_idx, _fm_date = _fm_result
            if _prev_idx and _prev_idx > 0:
                _chg_pt = round(_c_idx - _prev_idx, 2)
                _chg_pct = round((_chg_pt / _prev_idx) * 100, 2)
                _arrow = "▲" if _chg_pt > 0 else ("▼" if _chg_pt < 0 else "▬")
                _color = "#ff4d4d" if _chg_pt > 0 else ("#00c853" if _chg_pt < 0 else "#999")
                return f"{_c_idx:,.0f} ({_arrow} {abs(_chg_pt):,.0f}點 | {_chg_pct:+.2f}%)", _color, _chg_pct
            else:
                return f"{_c_idx:,.0f}（{_fm_date}，漲跌資料暫缺）", "#ccc", 0.0
    except Exception as e:
        print(f"[大盤氣象-FinMind] 失敗：{e}")

    # 主要來源：證交所官方每日指數（依名稱比對，不用脆弱的陣列位置）
    # 【V160 Round36/37】FinMind是第一層，證交所+yfinance雙重備援當更下層
    # 安全網，三層備援疊起來才會真的顯示不出來。
    def _fetch_twse_index(date_str):
        """對證交所 MI_INDEX 查單一天的發行量加權股價指數，查不到回 None。"""
        resp = _SESSION.get("https://www.twse.com.tw/exchangeReport/MI_INDEX",
                            params={"response": "json", "date": date_str, "type": "IND"}, timeout=6)
        data = resp.json()
        for row in data.get("data1", []) or data.get("data9", []):
            if isinstance(row, list) and len(row) >= 2 and "發行量加權股價指數" in str(row[0]):
                c_idx = float(str(row[1]).replace(",", ""))
                # 漲跌欄位格式可能因官方API版本而異，這裡保守解析：
                # 抓不到方向就顯示中性（灰色、無箭頭），優先確保「指數數值」本身正確，
                # 不冒險猜錯漲跌方向誤導判斷。
                change_pt, change_pct = 0.0, 0.0
                arrow, color = "●", "#ccc"
                try:
                    change_str = str(row[2]) if len(row) > 2 else ""
                    m = re.search(r'-?\d[\d,]*\.?\d*', change_str.replace(",", ""))
                    if m:
                        change_pt = float(m.group())
                        change_pct = round((change_pt / (c_idx - change_pt)) * 100, 2) if (c_idx - change_pt) else 0.0
                        arrow = "▲" if change_pt > 0 else ("▼" if change_pt < 0 else "▬")
                        color = "#ff4d4d" if change_pt > 0 else ("#00c853" if change_pt < 0 else "#999")
                except Exception:
                    pass
                return c_idx, change_pt, change_pct, arrow, color
        return None

    try:
        today_str = datetime.now(TAIPEI_TZ).strftime('%Y%m%d')
        result = _fetch_twse_index(today_str)
        _used_fallback_date = False
        if result is None:
            # 今天查不到（盤中/開盤前的正常情況）→ 改查最近一個交易日，
            # 一樣是官方權威資料，只是不是「今天」的
            _last_td = get_last_trading_date().replace('-', '')
            result = _fetch_twse_index(_last_td)
            _used_fallback_date = True
        if result is not None:
            c_idx, change_pt, change_pct, arrow, color = result
            _date_tag = "（昨日資料）" if _used_fallback_date else ""
            return f"{c_idx:,.0f} ({arrow} {abs(change_pt):,.0f}點 | {change_pct:+.2f}%){_date_tag}", color, change_pct
    except Exception as e:
        # 【V160 新增】主要來源失敗時原本完全靜默，跟這個專案一貫在抓的
        # 「靜默失敗」是同一個病灶。這裡不改變行為（還是會落到備援），
        # 只是留一筆log，之後真的要查為什麼掉到備援時才有線索可查。
        print(f"[大盤氣象-主要來源] 失敗或無資料：{e}")
    # 備援：yfinance ^TWII
    # 【V160 Round34修復】原本用fast_info+執行緒逾時包裝，daemon thread會
    # 卡住共用_SESSION連線拖累所有yfinance呼叫。改回history()原生timeout。
    try:
        tk = _yf_ticker("^TWII")
        hist = tk.history(period="10d", timeout=6)
        if not hist.empty and len(hist) >= 2:
            c_idx, prev_idx = float(hist['Close'].iloc[-1]), float(hist['Close'].iloc[-2])
            change_pt = round(c_idx - prev_idx, 2)
            change_pct = round((change_pt / prev_idx) * 100, 2) if prev_idx else 0.0
            arrow = "▲" if change_pt > 0 else ("▼" if change_pt < 0 else "▬")
            color = "#ff4d4d" if change_pt > 0 else ("#00c853" if change_pt < 0 else "#999")
            # 【V160 新增】誠實標示資料日期，抓到的不是今天就明講，不要冒充成即時數字
            _last_bar_date = hist.index[-1].strftime('%m/%d')
            _today_md = datetime.now(TAIPEI_TZ).strftime('%m/%d')
            _stale_tag = f"（備援來源・{_last_bar_date}資料）" if _last_bar_date != _today_md else "（備援來源）"
            return f"{c_idx:,.0f} ({arrow} {abs(change_pt):,.0f}點 | {change_pct:+.2f}%){_stale_tag}", color, change_pct
    except Exception:
        pass
    # 【V160 Round35新增】第三層備援：直接複用位階濾網(get_market_regime)
    # 已經成功抓到的^TWII收盤價——兩個函式本來就都在抓同一個^TWII，只是
    # 各自獨立快取。get_market_regime有自己的快取，這裡呼叫幾乎零成本。
    try:
        _regime = get_market_regime()
        if _regime.get('known') and _regime.get('close', 0) > 0:
            _c = float(_regime['close'])
            # 位階濾網沒有「昨收」，只能顯示指數值本身，不顯示漲跌（誠實：沒有的資料不編）
            return f"{_c:,.0f}（指數值，漲跌資料暫缺）", "#ccc", 0.0
    except Exception:
        pass
    return "大盤數據暫時無法取得（稍後自動重試）", "#888", 0.0


weather_str, weather_color, global_twii_gain = get_market_weather_real()
MARKET_REGIME = get_market_regime()


_OVERNIGHT_MACRO_MEM_CACHE = {"data": None, "fetched_ts": 0.0}
_OVERNIGHT_MACRO_MEM_TTL_SEC = 120  # 2分鐘內重複呼叫直接用記憶體，不重打8檔yfinance



# ==============================================================================
# 四、 動態技術指標與 ATR 交易邏輯
# ==============================================================================
def render_kline_chart(symbol, hist, key_suffix=""):
    """
    【V160 新功能】互動式K線圖：蠟燭線 + 5/20/60MA + 成交量 + MACD動能 + RSI。
    用 plotly 畫，Streamlit 內建支援。補上「數據卡片流缺視覺化K線」的短板。
    hist: get_real_stock_data_yfinance 回傳的 OHLCV DataFrame。

    【R80新增】key_suffix：同一檔股票有可能同時出現在「持倉」跟「雷達/
    觀察區」等不同區塊，render_action_buttons在同一次腳本執行裡可能被
    呼叫多次、都傳同一個code——如果這裡的widget key只用symbol，會撞成
    Streamlit的重複key錯誤(StreamlitDuplicateElementId)，而這種例外如果
    沒被外層try/except接住，會直接讓整張卡片後面的內容全部消失。呼叫端
    傳入btn_suffix（每個區塊各自不同）就能避免這個問題。

    【R79新增】三項強化（競品比較後總指揮官要求補齊的視覺化缺口）：
      1. 布林通道疊圖——MA20±2倍標準差，跟戰卡文字已經在顯示的布林上軌
         數字對應，原本只有文字沒有畫在圖上。
      2. 多時間框架切換（日K/週K/月K）——用pandas resample把日K重新聚合，
         不需要額外打API，同一份hist資料就能做三種時間粒度。
      3. 手繪趨勢線——用Plotly原生的shape-drawing功能(modeBarButtonsToAdd)，
         不需要額外套件或自訂元件，工具列會多出畫線/擦除的按鈕，畫的線是
         這次瀏覽階段暫存的（重新整理頁面就會消失，不會佔用資料庫空間）。
    RSI(14)副圖原本就已經存在（第4個子圖），不是這次新增的。
    """
    try:
        import plotly.graph_objects as go
        from plotly.subplots import make_subplots
    except ImportError:
        st.warning("K線圖需要 plotly 套件。請在 requirements.txt 加入 plotly 後重新部署。")
        return
    if hist is None or len(hist) < 5:
        st.caption("股價資料不足，無法繪製K線圖。")
        return

    # 【R79新增，R80補上key_suffix】多時間框架切換——用selectbox讓你選日/
    # 週/月，用同一份hist重新聚合，不用多打任何API。
    _tf = st.radio("時間粒度", ["日K", "週K", "月K"], horizontal=True,
                   key=f"kline_timeframe_{symbol}{key_suffix}")
    _resample_rule = {"日K": None, "週K": "W-FRI", "月K": "ME"}[_tf]

    # 【V160修復】K線圖蠟燭擠在左邊、右邊空白——Plotly日期軸遇到重複/不
    # 連續日期會照日期跨度畫X軸而非K棒數量。防禦性修復：在_full源頭先
    # 排序去重，並把X軸改成類別軸雙重保險。
    _full = hist[~hist.index.duplicated(keep='last')].sort_index().copy()

    # 【R79新增】週K/月K：用pandas resample重新聚合OHLCV，聚合規則要符合
    # 真實K棒定義（開盤取第一筆、收盤取最後一筆、高低取區間極值、成交量加總）。
    if _resample_rule:
        _full = _full.resample(_resample_rule).agg({
            'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last', 'Volume': 'sum',
        }).dropna(subset=['Close'])

    _ema12 = _full['Close'].ewm(span=12, adjust=False).mean()
    _ema26 = _full['Close'].ewm(span=26, adjust=False).mean()
    _dif = _ema12 - _ema26                          # DIF（快線）
    _dea = _dif.ewm(span=9, adjust=False).mean()    # DEA/MACD（慢線）
    _osc = _dif - _dea                              # 柱狀體（動能）
    _full['DIF'], _full['DEA'], _full['OSC'] = _dif, _dea, _osc

    # 【R79新增】布林通道：MA20 ± 2倍標準差，用完整歷史算才準，這裡MA20
    # 剛好跟既有的中軌均線重疊，不用額外畫一條中軌線。
    _boll_std = _full['Close'].rolling(20).std()
    _full['BOLL_MID'] = _full['Close'].rolling(20).mean()
    _full['BOLL_UP'] = _full['BOLL_MID'] + 2 * _boll_std
    _full['BOLL_DOWN'] = _full['BOLL_MID'] - 2 * _boll_std

    _n_show = 60 if not _resample_rule else min(len(_full), 52)  # 週K/月K顯示長一點的期間更有意義
    df = _full.tail(_n_show).copy()
    df['MA5'] = _full['Close'].rolling(5).mean().tail(_n_show)
    df['MA20'] = _full['Close'].rolling(20).mean().tail(_n_show)
    df['MA60'] = _full['Close'].rolling(60).mean().tail(_n_show)
    # 【V160 新增】RSI 用完整歷史算（14日需要足夠資料才準），再取近60日顯示，
    # 沿用既有的 calc_rsi() 函式，跟戰卡上顯示的 RSI(14) 是同一套算法，不會兩邊對不上。
    _full['RSI'] = calc_rsi(_full, period=14)
    df['RSI'] = _full['RSI'].tail(_n_show)

    # 【R88新增】KD(9)——沿用回測引擎已經在用的同一套算法(9日RSV+平滑)，
    # 跟RSI疊在同一個副圖，避免圖表拉得太長。
    _low_min = _full['Low'].rolling(9).min()
    _high_max = _full['High'].rolling(9).max()
    _rsv = (_full['Close'] - _low_min) / (_high_max - _low_min + 1e-9) * 100
    _full['K'] = _rsv.bfill().ffill().ewm(com=2, adjust=False).mean()
    _full['D'] = _full['K'].ewm(com=2, adjust=False).mean()
    df['K'] = _full['K'].tail(_n_show)
    df['D'] = _full['D'].tail(_n_show)

    # 四個子圖：K線 / 成交量 / MACD / RSI+KD
    fig = make_subplots(rows=4, cols=1, shared_xaxes=True,
                        vertical_spacing=0.025, row_heights=[0.46, 0.16, 0.19, 0.19])

    # 【R96修復】X軸用type='category'(避免週末/假日空隙壓縮視覺)時，Plotly
    # 會把df.index(完整pandas Timestamp物件)直接字串化當類別標籤，預設格式
    # 含奈秒("2026-05-20T00:00:00.000000000")，又長又佔版面——總指揮官反映
    # 「日期顯示太長，占畫面太多」正是這個原因。這裡先把索引格式化成短
    # 字串("2026-05-20"或當天內有時分時用"05/20 09:30")，後面所有x=df.index
    # 都會自動吃到這個已經格式化過的短索引，不用逐一修改每一個add_trace。
    df.index = (df.index.strftime('%Y-%m-%d')
               if len(df) < 2 or (df.index[1] - df.index[0]).total_seconds() >= 86400
               else df.index.strftime('%m/%d %H:%M'))

    # K線（台股習慣：紅漲綠跌）
    fig.add_trace(go.Candlestick(
        x=df.index, open=df['Open'], high=df['High'], low=df['Low'], close=df['Close'],
        increasing_line_color='#ff4d4d', decreasing_line_color='#00c853', name='K線'), row=1, col=1)

    # 均線
    for ma, color in [('MA5', '#f1c40f'), ('MA20', '#00d2ff'), ('MA60', '#e84393')]:
        fig.add_trace(go.Scatter(x=df.index, y=df[ma], line=dict(color=color, width=1.2),
                                name=ma), row=1, col=1)

    # 【R79新增】布林通道——上下軌用細虛線，不搶過K線跟均線的視覺焦點，
    # 中軌不重複畫（跟MA20是同一條線，畫兩次沒有意義只會讓圖更亂）。
    fig.add_trace(go.Scatter(x=df.index, y=df['BOLL_UP'], line=dict(color='#888', width=0.9, dash='dot'),
                            name='布林上軌'), row=1, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df['BOLL_DOWN'], line=dict(color='#888', width=0.9, dash='dot'),
                            name='布林下軌', fill='tonexty', fillcolor='rgba(136,136,136,0.06)'), row=1, col=1)

    # 成交量（顏色跟漲跌一致）
    vol_colors = ['#ff4d4d' if c >= o else '#00c853' for c, o in zip(df['Close'], df['Open'])]
    fig.add_trace(go.Bar(x=df.index, y=df['Volume'], marker_color=vol_colors,
                        name='成交量(張)'), row=2, col=1)

    # 【V160 新增】MACD：DIF快線 + DEA慢線 + 動能柱狀體（紅漲綠跌）
    osc_colors = ['#ff4d4d' if v >= 0 else '#00c853' for v in df['OSC']]
    fig.add_trace(go.Bar(x=df.index, y=df['OSC'], marker_color=osc_colors, name='MACD柱'), row=3, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df['DIF'], line=dict(color='#f1c40f', width=1),
                            name='DIF'), row=3, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df['DEA'], line=dict(color='#00d2ff', width=1),
                            name='DEA'), row=3, col=1)

    # 【V160 新增】RSI(14)：70/30 參考線標示超買超賣區
    fig.add_trace(go.Scatter(x=df.index, y=df['RSI'], line=dict(color='#e84393', width=1.3),
                            name='RSI(14)'), row=4, col=1)
    # 【R88新增】KD(9)——跟RSI疊在同一個副圖，K用實線、D用虛線區分，
    # 顏色跟MACD的DIF/DEA故意不同，避免圖例混淆。
    fig.add_trace(go.Scatter(x=df.index, y=df['K'], line=dict(color='#00d2ff', width=1),
                            name='K值'), row=4, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df['D'], line=dict(color='#00d2ff', width=1, dash='dot'),
                            name='D值'), row=4, col=1)
    fig.add_hline(y=70, line=dict(color='#ff4d4d', width=0.8, dash='dot'), row=4, col=1)
    fig.add_hline(y=30, line=dict(color='#00c853', width=0.8, dash='dot'), row=4, col=1)

    fig.update_layout(
        height=760, template='plotly_dark', paper_bgcolor='#0e1117', plot_bgcolor='#0e1117',
        margin=dict(l=10, r=10, t=30, b=10), showlegend=True,
        legend=dict(orientation='h', yanchor='bottom', y=1.02, xanchor='right', x=1),
        xaxis_rangeslider_visible=False,
        title=dict(text=f"{symbol} {TW_STOCK_NAMES.get(symbol, '')} {_tf} 近{_n_show}期K線+布林+MACD+RSI",
                  font=dict(size=14, color='#f1c40f')),
        # 【R79新增】預設拖曳模式改成畫線工具，配合下面config的modeBar按鈕，
        # 想單純平移縮放的話，工具列上點選那個游標圖示的按鈕切回去即可。
        dragmode='drawline',
        newshape=dict(line=dict(color='#f1c40f', width=2)),
    )
    for _r in (1, 2, 3, 4):
        # type='category'：x軸只看「第幾根K棒」不看「實際日期差幾天」，
        # 徹底消除週末/假日空隙或任何日期不連續造成的視覺壓縮問題，
        # 不管背後資料乾不乾淨，畫出來一定是等距分佈。
        fig.update_xaxes(gridcolor='#1a2030', type='category', row=_r, col=1)
        fig.update_yaxes(gridcolor='#1a2030', row=_r, col=1)
    fig.update_yaxes(title_text="MACD", row=3, col=1)
    fig.update_yaxes(title_text="RSI/KD", range=[0, 100], row=4, col=1)
    # 【R79新增】手繪趨勢線——Plotly原生支援，不用額外套件。畫的線只存在
    # 這次瀏覽階段(重新整理頁面會消失)，純粹是給你盤中盯盤時輔助畫趨勢線
    # 用，不會佔用資料庫空間，也不會影響任何評分邏輯。
    st.plotly_chart(fig, use_container_width=True, key=f"kline_{symbol}_{_tf}{key_suffix}",
                    config={'modeBarButtonsToAdd': ['drawline', 'drawopenpath', 'eraseshape'],
                           'displaylogo': False})
    st.caption("💡 工具列有畫線工具（滑鼠移到圖表右上角），可以手繪趨勢線輔助判斷；"
              "橡皮擦圖示能擦掉畫錯的線。畫的線只在這次瀏覽階段有效，重新整理頁面會消失。")


# 【V160 Round39】calculate_atr/detect_k_line_patterns_v152/build_trade_
# zones已搬進warroom_core.py，直接import，跟排程端共用同一套邏輯。


# ==============================================================================
# 五、【任務二】法人連續買賣超真實成本 (VWAP) + 估價模型
# ==============================================================================
# 【V160 Round39】score_zone1_fundamental/score_zone2_technical/
# score_zone3_chips/_fmt_zone_summary已搬進warroom_core.py，直接import。


# 【V160 Round39】determine_signal已搬進warroom_core.py，直接import。


# ==============================================================================
# 六、 核心訊號與戰區聚合
# ==============================================================================
# 【R97修復，見開發歷程.md「稽核發現的真bug」章節】fetch_day_trading_info
# 搬進warroom_core.py共用模組時，原本掛在它上面的@st.cache_data(...)裝飾器
# 沒有一起清掉，變成孤兒誤植到下面calculate_signals_worker頭上——這個worker
# 函式本來就不該被快取（逐執行緒呼叫，每次都要吃當下最新資料），而且它接收
# 的ctx（Streamlit執行緒上下文）本身含有鎖物件，Streamlit想序列化它當快取
# key就會炸出「cannot pickle '_thread.RLock' object」，正是總指揮官這次
# 回報的錯誤。
#
# 這裡補一個本機端的快取包裝，恢復原本「6小時快取，同一天同一檔只真的打
# 一次FinMind」的行為——warroom_core.py本身禁止import streamlit，快取
# 裝飾器沒辦法留在那邊，只能在有streamlit可用的這一側重新包一層。
def _compute_bs_diff_for_web(symbol):
    """
    【R98新增】網頁端算買賣家數差代理指標的小包裝——帶進程內記憶體快取
    (5分鐘)，避免戰情速覽這種大批量呼叫時每檔都多打一次Supabase拖慢速覽。
    查不到/失敗/SUPABASE未啟用一律回None(對應的因子會靜默跳過)。
    """
    if SUPABASE_CONN is None:
        return None
    _cache = st.session_state.setdefault('_bs_diff_web_cache', {})
    _now = time.time()
    _hit = _cache.get(symbol)
    if _hit and (_now - _hit[1]) < 300:
        return _hit[0]
    _result = None
    try:
        _bf_latest = (SUPABASE_CONN.table("broker_flows").select("log_date")
                      .eq("symbol", symbol).order("log_date", desc=True).limit(1).execute())
        if _bf_latest.data:
            _bf_date = _bf_latest.data[0]["log_date"]
            _bs = compute_buyer_seller_branch_diff_proxy(SUPABASE_CONN, symbol, _bf_date)
            _result = _bs.get("diff_proxy")
    except Exception as e:
        print(f"[_compute_bs_diff_for_web] {symbol} 失敗（不影響評分，因子靜默跳過）："
              f"{type(e).__name__}: {e}")
    _cache[symbol] = (_result, _now)
    return _result


def _get_financial_risk_score_for_web(symbol):
    """
    【R98續17新增，總指揮官方向C：價值面融合進短波段判斷】網頁端讀
    financial_health_snapshot.risk_score的小包裝，跟_compute_bs_diff_
    for_web同一套設計(進程內記憶體快取5分鐘，避免戰情速覽大批量呼叫
    時每檔都多打一次Supabase)。

    risk_score是排程(stage_financial_health_scan)已經算好存進DB的，
    這裡純讀取，不重新呼叫fetch_financial_health/compute_financial_
    risk_score(那兩個都要打FinMind，網頁端即時算太貴)。查不到(還沒
    被排程掃到這一季、或不在掃描範圍內)一律回None，對應的
    financial_risk因子會靜默跳過，不假裝知道。
    """
    if SUPABASE_CONN is None:
        return None
    _cache = st.session_state.setdefault('_fin_risk_web_cache', {})
    _now = time.time()
    _hit = _cache.get(symbol)
    if _hit and (_now - _hit[1]) < 300:
        return _hit[0]
    _result = None
    try:
        _res = (SUPABASE_CONN.table("financial_health_snapshot").select("risk_score")
                .eq("symbol", symbol).limit(1).execute())
        if _res.data and _res.data[0].get("risk_score") is not None:
            _result = int(_res.data[0]["risk_score"])
    except Exception as e:
        print(f"[_get_financial_risk_score_for_web] {symbol} 失敗（不影響評分，"
              f"financial_risk因子靜默跳過）：{type(e).__name__}: {e}")
    _cache[symbol] = (_result, _now)
    return _result


def calculate_signal_with_timeout(symbol, config, timeout_sec=25):
    """
    【R98續14新增，總指揮官反映戰卡展開後「有診斷文字但沒有小人在跑」——
    這代表calculate_signals_worker真的卡住了(不是渲染問題)，很可能是
    FinMind/yfinance當下嚴重延遲甚至掛住。這裡用thread+join(timeout=)
    包一層硬性逾時，讓使用者最壞情況下也只需要等timeout_sec秒，就會
    看到明確的「計算逾時」訊息，不會再面對永遠不知道「還在跑」還是
    「真的壞了」的空白畫面。

    刻意只在呼叫端加這層防護，完全不動calculate_signals_worker/底層
    抓價邏輯本身——那是總指揮官要求先擱置、之後才要正式處理的P0升級
    範圍（compute_full_signal_for徹底升級），這裡只是「等多久」的
    防護網，不是「怎麼抓資料」的架構調整。

    回傳格式跟calculate_signals_worker一致：逾時時回傳只有code/name/
    error三個key的dict（跟資料抓取失敗時的格式一致，呼叫端不用另外
    判斷「是逾時還是資料失敗」，render_stock_card_ui已經有處理這種
    格式的早期return警告）。
    """
    _result_holder = {}

    def _worker():
        try:
            _result_holder['value'] = calculate_signals_worker(symbol, config)
        except Exception as e:
            _result_holder['error'] = e

    try:
        _ctx = get_script_run_ctx()
    except Exception:
        _ctx = None
    _t = threading.Thread(target=_worker, daemon=True)
    if _ctx is not None:
        try:
            add_script_run_ctx(_t, _ctx)
        except Exception:
            pass
    _t.start()
    _t.join(timeout=timeout_sec)
    if _t.is_alive():
        # 執行緒還在跑，代表真的逾時了——thread設daemon=True，放著讓它
        # 自己在背景結束(通常是網路請求最終會timeout)，不強制殺掉
        # (Python沒有安全的方式強制終止執行緒)，但已經不等它了。
        return {"code": symbol, "name": TW_STOCK_NAMES.get(symbol, symbol),
                "error": f"計算逾時（超過{timeout_sec}秒還沒完成，通常是FinMind/"
                         f"yfinance當下嚴重延遲或被限流），已放棄等待，過一陣子"
                         f"再試一次；如果持續逾時，去側欄「🩺資料源健康度檢查」"
                         f"確認資料源狀態"}
    if 'error' in _result_holder:
        raise _result_holder['error']
    return _result_holder.get('value')


def calculate_signals_worker(symbol, config, ctx=None):
    # 讓子執行緒掛上 Streamlit context，st.cache_data 才會生效
    if ctx is not None:
        try:
            add_script_run_ctx(threading.current_thread(), ctx)
        except Exception:
            pass

    # 【R95續22，深度計時診斷】戰情速覽卡3分鐘以上，續21移除當沖查詢+
    # 補PE快取後仍未解決，這裡對每支會打外部API/DB的呼叫各別計時，只印
    # 總結避免診斷本身變成新負擔。config['perf_diag']=True時才計時。
    _perf_diag = config.get('perf_diag', False)
    _perf_t0 = time.time()
    _perf_marks = {}

    def _perf_mark(_stage):
        if _perf_diag:
            _now = time.time()
            _perf_marks[_stage] = round(_now - _perf_mark._last, 3)
            _perf_mark._last = _now
    _perf_mark._last = _perf_t0

    token = config.get('token')                     # 【修復】原本誤寫成 fm_token
    rev_override = config.get('rev_override', {})
    bh_override = config.get('bh_override', {})
    div_override = config.get('div_override', {})
    dividend_db = config.get('dividend_db', {})
    stock_names = config.get('stock_names', {})
    enable_doomsday = config.get('enable_doomsday', False)
    market_bull = config.get('market_bull', True)

    f_single = t_single = d_single = margin_diff = 0.0
    f_5d = t_5d = f_10d = t_10d = 0.0
    f_pct = t_pct = f_5d_pct = t_5d_pct = f_10d_pct = t_10d_pct = 0.0
    # 【R58新增】法人持續性因子的精確版：連續3天外資買超（不是5日/10日方向
    # 一致這個代理）。None代表資料不足3天，無法判斷（不是False=沒有連續買超，
    # 兩者意義不同，None讓因子函式自己決定要不要退回代理版）。
    foreign_buy_streak3 = None
    big_holder, big_holder_date = 0.0, ""
    latest_db_date = ""
    has_margin = False
    f_vwap = t_vwap = None

    hist, info = get_real_stock_data_yfinance(symbol)
    _perf_mark('yfinance日K+info')
    if hist is None or len(hist) < 21:
        # 【R60】原本error只存True，看不出卡在哪一步，改成具體描述
        # (例如「FinMind+yfinance都抓不到」vs「K棒不足21根」是不同狀況)。
        _reason = ("get_real_stock_data_yfinance回傳None（FinMind+yfinance都抓不到資料）"
                   if hist is None else f"抓到的K棒只有{len(hist)}根，不足21根門檻")
        return {"code": symbol, "name": stock_names.get(symbol, symbol), "error": _reason}

    curr_price = float(hist['Close'].iloc[-1])
    prev_price = float(hist['Close'].iloc[-2])
    open_price = float(hist['Open'].iloc[-1])
    gain = ((curr_price - prev_price) / prev_price) * 100 if prev_price > 0 else 0.0

    # 【V160 Round36】跟大盤指數同病根：yfinance日K最後一筆有時沒及時
    # 更新，戰卡「現價」可能是前一交易日收盤。不重蹈round31-32碰fast_info
    # 的覆轍(已證實會卡死整頁)，改成老實記錄「這個價格是哪一天的」供顯示。
    price_date = hist.index[-1].strftime('%m/%d')
    price_is_stale = price_date != get_current_or_last_trading_date()[5:].replace('-', '/')

    # 昨日強勢（供「查8」使用）
    prev2_price = float(hist['Close'].iloc[-3])
    prev_gain = ((prev_price - prev2_price) / prev2_price) * 100 if prev2_price > 0 else 0.0
    is_yesterday_strong = prev_gain > 5.0

    vol_today = int(hist['Volume'].iloc[-1])
    vol_yesterday = int(hist['Volume'].iloc[-2])

    # 【V157 修復】總量增縮列與爆量比列，現在共用同一套「今日推估全天量」基準，
    # 不再發生「總量顯示量縮、爆量比卻顯示爆量」這種自相矛盾的狀況。
    is_intraday, projected_vol_today, time_ratio = get_intraday_projection(vol_today)
    vol_for_compare = projected_vol_today if is_intraday else vol_today
    vol_change_str = calc_volume_change(vol_for_compare, vol_yesterday)
    if is_intraday:
        vol_change_str += " (今日累計推估至收盤，尚未定案)"

    prev_5_vol = hist['Volume'].iloc[-6:-1]
    vol_5d_mean = max(1, int(prev_5_vol.mean())) if len(prev_5_vol) > 0 else vol_today

    if is_intraday:
        vol_ratio = vol_for_compare / vol_5d_mean if vol_5d_mean > 0 else 0.0
        # 開盤剛過幾分鐘時 time_ratio 被下限鎖在 0.05，估算值本來就不穩，加註提醒
        stability_note = " ⚠️數據不穩" if time_ratio <= 0.05 else ""
        vol_ratio_label = f"爆量比: {vol_ratio:.1f}x (盤中估算{stability_note})"
    else:
        vol_ratio = vol_today / vol_5d_mean if vol_5d_mean > 0 else 0.0
        vol_ratio_label = f"爆量比: {vol_ratio:.1f}x"

    ma5 = float(hist['Close'].tail(5).mean())
    ma20 = float(hist['Close'].tail(20).mean())
    ma60 = float(hist['Close'].tail(60).mean()) if len(hist) >= 60 else float(hist['Close'].mean())

    exp1, exp2 = hist['Close'].ewm(span=12, adjust=False).mean(), hist['Close'].ewm(span=26, adjust=False).mean()
    macd_hist = (exp1 - exp2) - (exp1 - exp2).ewm(span=9, adjust=False).mean()
    macd_val = float(macd_hist.iloc[-1]) if not macd_hist.empty and pd.notna(macd_hist.iloc[-1]) else 0.0
    macd_str = f"多方動能 ({macd_val:+.2f})" if macd_val > 0 else f"空方動能 ({macd_val:+.2f})"
    macd_color = "#ff4d4d" if macd_val > 0 else "#00FF00"

    low_min, high_max = hist['Low'].rolling(9).min(), hist['High'].rolling(9).max()
    rsv = (hist['Close'] - low_min) / (high_max - low_min + 1e-9) * 100
    calc_k = rsv.bfill().ffill().ewm(com=2, adjust=False).mean()
    calc_d = calc_k.ewm(com=2, adjust=False).mean()
    # 【R96新增，校驗發現的缺口修復】原本K/D值只塞進kdj_str顯示字串，
    # 查1.主升段突擊只能對字串做文字比對，沒辦法檢查K值50以上/以下
    # (附件06核心洞察)。這裡把k_val/d_val存成獨立欄位供查1直接讀取。
    k_val = round(float(calc_k.iloc[-1]), 1) if pd.notna(calc_k.iloc[-1]) else None
    d_val = round(float(calc_d.iloc[-1]), 1) if pd.notna(calc_d.iloc[-1]) else None
    kdj_str = (f"金叉 (K:{calc_k.iloc[-1]:.1f})" if calc_k.iloc[-1] > calc_d.iloc[-1]
               else f"死叉 (K:{calc_k.iloc[-1]:.1f})")

    rsi_val = float(calc_rsi(hist).iloc[-1]) if pd.notna(calc_rsi(hist).iloc[-1]) else 50.0
    bias_val = float(calc_bias(hist).iloc[-1]) if pd.notna(calc_bias(hist).iloc[-1]) else 0.0
    atr_val = calculate_atr(hist)

    is_open_high_close_low = (open_price > prev_price) and (curr_price < open_price)

    # 【R96新增】趨勢/趨勢中休息/盤整三態分類+RSI雙版本判斷——RSI均值
    # 回歸版跟動能追蹤版是兩套相反哲學，裁決結果是都對、依股票當下狀態
    # 切換使用。放在戰卡運算最前段，因為顯示要放在戰卡最前面。
    _rsi_series = calc_rsi(hist)
    rsi_prev_val = (float(_rsi_series.iloc[-2])
                    if len(_rsi_series) >= 2 and pd.notna(_rsi_series.iloc[-2]) else None)
    trend_regime = classify_trend_regime(ma5, ma20, ma60, hist=hist)
    rsi_dual = evaluate_rsi_dual_mode(rsi_val, rsi_prev=rsi_prev_val, regime=trend_regime)

    # 【V160】爆量下殺偵測：爆量比>=2.0 且 當日收黑 且 跌幅明顯 且 收在當日低點附近
    # → 典型主力出貨型態，供 determine_signal 強制撤退規則使用。
    day_high = float(hist['High'].iloc[-1])
    day_low = float(hist['Low'].iloc[-1])
    _day_range = day_high - day_low
    close_near_low = (_day_range > 0 and (curr_price - day_low) / _day_range <= 0.35)
    is_volume_dump = bool(vol_ratio >= get_threshold('vol_ratio_surge') and curr_price < open_price and gain < -1.0 and close_near_low)

    # 【R96新增】收盤強弱代查——策略框架圖「波段續抱資格三關·第三關」：
    # 收盤落在當日高低區間前25%（高檔）→明天有戲；後25%（低檔）→今天該走。
    # 純顯示用的獨立判斷，不影響 is_volume_dump／determine_signal 既有邏輯。
    closing_strength = evaluate_closing_strength(open_price, day_high, day_low, curr_price)

    # 【R96新增】量能達標代查——Step 2：創新高時成交量有沒有跟得上
    # (>=攻擊量80%健康／<50%沒人承接)，純顯示用不影響既有評分邏輯。
    try:
        volume_followthrough = evaluate_volume_followthrough(hist)
    except Exception as e:
        print(f"[calculate_signals_worker-診斷] {symbol} 量能達標判斷失敗：{type(e).__name__}: {e}")
        volume_followthrough = None

    # 【R96新增】拉回體檢母關——Step 3，這裡日線戰卡用mode='swing'，
    # mode='intraday'留給5分K使用，函式本身已支援不用重寫。
    try:
        pullback_health = evaluate_pullback_health(hist, mode='swing')
    except Exception as e:
        print(f"[calculate_signals_worker-診斷] {symbol} 拉回體檢判斷失敗：{type(e).__name__}: {e}")
        pullback_health = None

    # 【R96新增，累積清單第6項】反彈健康度——附件28修正版：反彈階段
    # 量縮=虛跌可等、量增彈不回=賣壓未減必須走，跟拉回體檢是對稱的一組。
    try:
        rebound_health = evaluate_rebound_health(hist)
    except Exception as e:
        print(f"[calculate_signals_worker-診斷] {symbol} 反彈健康度判斷失敗：{type(e).__name__}: {e}")
        rebound_health = None

    # 首根長紅（供「查1」主升段突擊使用）：今紅、昨黑、實體 > 0.5 ATR
    o1, c1 = float(hist['Open'].iloc[-2]), prev_price
    body_ref = atr_val if atr_val > 0 else curr_price * 0.02
    is_first_red = (curr_price > open_price) and (c1 < o1) and (abs(curr_price - open_price) > body_ref * 0.5)

    # ---- 籌碼（SQLite 近 30 日） ----
    inst_df = get_inst_data_from_db(symbol, 30)
    _perf_mark('籌碼DB查詢')
    if not inst_df.empty:
        latest = inst_df.iloc[0]
        latest_db_date = str(latest['date'])
        f_single = safe_float(latest['foreign_buy'])
        t_single = safe_float(latest['trust_buy'])
        d_single = safe_float(latest['dealer_buy'])
        # 【R95修復】has_margin原本是「abs(margin_diff)>0」，把「真的
        # 抓到、變化是0」跟「根本沒抓到」混為一談。改用pd.notna()判斷
        # 「這筆是不是真的有資料」。
        has_margin = pd.notna(latest['margin'])
        margin_diff = safe_float(latest['margin']) if has_margin else 0.0

        f_pct = (f_single / vol_today * 100) if vol_today > 0 else 0.0
        t_pct = (t_single / vol_today * 100) if vol_today > 0 else 0.0

        df_5d = inst_df.head(5)
        df_10d = inst_df.head(10)
        f_5d, t_5d = float(df_5d['foreign_buy'].sum()), float(df_5d['trust_buy'].sum())
        f_10d, t_10d = float(df_10d['foreign_buy'].sum()), float(df_10d['trust_buy'].sum())

        # 【R58新增】法人持續性因子精確版：inst_df逐日明細依日期新到舊
        # 排序，直接檢查最新3天是否每天都外資買超。資料不到3天時明講
        # None，不假裝「不是連續買超」。
        df_3d = inst_df.head(3)
        foreign_buy_streak3 = (bool((df_3d['foreign_buy'] > 0).all())
                                if len(df_3d) >= 3 else None)

        vol_5d_sum = max(1, vol_5d_mean * 5)
        vol_10d_sum = max(1, vol_5d_mean * 10)
        f_5d_pct = f_5d / vol_5d_sum * 100
        t_5d_pct = t_5d / vol_5d_sum * 100
        f_10d_pct = f_10d / vol_10d_sum * 100
        t_10d_pct = t_10d / vol_10d_sum * 100

        # 【任務二】連續買賣超真實成本 VWAP
        f_vwap = calc_inst_streak_vwap(inst_df, hist, 'foreign_buy')
        t_vwap = calc_inst_streak_vwap(inst_df, hist, 'trust_buy')

        # 【R96新增，累積清單第8項】投信季底作帳警示——t_vwap本來就
        # 算好連續天數(days)跟方向(side)，直接拿來用，只在投信買超時檢查。
        try:
            if t_vwap and t_vwap.get('side') == '買超':
                season_end_warning = check_institutional_season_end_warning(
                    hist.index[-1], buy_streak_days=t_vwap.get('days', 0))
            else:
                season_end_warning = {"warning": False, "reason": None}
        except Exception as e:
            print(f"[calculate_signals_worker-診斷] {symbol} 投信季底作帳警示判斷失敗：{type(e).__name__}: {e}")
            season_end_warning = {"warning": False, "reason": None}
    else:
        season_end_warning = {"warning": False, "reason": None}

    db_bh = get_latest_big_holder(symbol)
    _perf_mark('大戶DB查詢')
    if db_bh:
        big_holder, big_holder_date = db_bh['percent'], db_bh['date']
    if symbol in bh_override and bh_override[symbol]:
        big_holder = bh_override[symbol].get('ratio', big_holder)
        big_holder_date = f"自訂 {bh_override[symbol].get('date', '')}"

    # ---- 營收 ----
    manual_mode = False
    rev_ok = True
    if symbol in rev_override and rev_override[symbol]:
        ov = rev_override[symbol]
        rev_yoy, rev_mom, rev_month, manual_mode = ov.get('yoy', 0.0), ov.get('mom', 0.0), ov.get('month', "自訂"), True
    else:
        fm_rev = fetch_finmind_revenue(symbol, token)
        rev_yoy, rev_mom, rev_month = fm_rev['yoy'], fm_rev['mom'], fm_rev['month']
        rev_ok = fm_rev.get('ok', True)
        if fm_rev.get('stale'):
            rev_month = f"{rev_month} (沿用)"
    _perf_mark('月營收(快取/FinMind)')

    # ---- 股利 ----
    cash_div = 0.0
    manual_div_mode = False
    if symbol in div_override:
        ov = div_override[symbol]
        div_display, div_yield, manual_div_mode = ov.get('display', "自訂資料"), ov.get('yield', 0.0), True
        cash_div = ov.get('cash', 0.0)
    else:
        div_info = dividend_db.get(symbol)
        if div_info:
            cash_div = div_info.get('cash', 0.0)
            d_stock = div_info.get('stock', 0.0)
            div_date_str = div_info.get('date', '')
            div_yield = (cash_div / curr_price) * 100 if curr_price > 0 else 0.0
            # 【V160 修復】原始數字是浮點數運算結果，直接印會出現 0.01999999
            # 這種假精度尾數（總指揮官回報看起來很亂）。四捨五入到小數點後2位，
            # 對股利金額來說已經足夠精確，畫面也乾淨。
            cash_div_disp = round(cash_div, 2)
            d_stock_disp = round(d_stock, 2)
            div_amount_str = (f"息 {cash_div_disp}元 + 權 {d_stock_disp}元"
                              if d_stock_disp > 0 else f"息 {cash_div_disp}元")
            # 【V160 新增】總指揮官回報：只顯示原始日期（如 1150729）看不出這是
            # 「已經除完的過去日期」還是「還沒到的未來日期」，要自己心算比對很麻煩。
            # 這裡明確判讀狀態，直接講結論，不要你猜。
            _div_date_disp = _roc_date_to_display(div_date_str)
            _div_status = _classify_dividend_date(div_date_str)
            if _div_status == 'past':
                div_display = f"✅ 已除權息完（{_div_date_disp}）| {div_amount_str}"
            elif _div_status == 'future':
                div_display = f"📅 預定 {_div_date_disp} | {div_amount_str}"
            else:
                # 日期格式不明或缺漏，但金額有抓到——照實講，不猜狀態
                div_display = f"{div_date_str or '日期未知'} | {div_amount_str}"
        else:
            # 【V160 新增】TWSE 預告表查無此股（可能已過所有近期除權息週期，事件過去後
            # 就從預告表移除了）——先試 FinMind 股利政策表當備援，那邊是永久紀錄不會消失。
            fm_div = fetch_finmind_dividend_fallback(symbol, token)
            if fm_div.get('ok'):
                cash_div = fm_div['cash']
                d_stock_fb = fm_div['stock']
                div_yield = (cash_div / curr_price) * 100 if curr_price > 0 else 0.0
                cash_div_disp, d_stock_disp = round(cash_div, 2), round(d_stock_fb, 2)
                div_amount_str = (f"息 {cash_div_disp}元 + 權 {d_stock_disp}元"
                                  if d_stock_disp > 0 else f"息 {cash_div_disp}元")
                _fb_date_disp = _roc_date_to_display(fm_div['ex_date'])
                _fb_status = _classify_dividend_date(fm_div['ex_date'])
                if _fb_status == 'past':
                    div_display = f"✅ 已除權息完（{_fb_date_disp}）| {div_amount_str}（來源：股利政策表）"
                elif _fb_status == 'future':
                    div_display = f"📅 預定 {_fb_date_disp} | {div_amount_str}（來源：股利政策表）"
                else:
                    div_display = f"{div_amount_str}（來源：股利政策表，日期未知）"
            else:
                cash_div = safe_float(info.get('dividendRate', 0.0)) if info else 0.0
                div_yield = (cash_div / curr_price) * 100 if curr_price > 0 else 0.0
                div_display = (f"無日期 | 息 {cash_div}元" if cash_div > 0
                              else "近期無除權息公告（預告表與股利政策表皆查無資料）")

    # ---- 估價模型（V157：優先用歷史 PE 百分位，樣本不足才退回固定倍數） ----
    _perf_mark('股利(快取/FinMind/TWSE)')
    # 【R95續21/R96補上跨容器重啟持久化】fetch_pe_history原本只有記憶體
    # 快取，重新部署就整個歸零。這輪接上跟月營收/股利同一套Supabase持久化，
    # DataFrame轉JSON安全dict存取(date欄位轉字串)，不動_smart_cached_call本體。
    def _fetch_pe_history_cacheable(_symbol=symbol, _token=token):
        _df = fetch_pe_history(_symbol, _token)
        if _df is None or _df.empty:
            return {'ok': False, 'records': []}
        _df2 = _df.copy()
        if 'date' in _df2.columns:
            _df2['date'] = _df2['date'].astype(str)
        return {'ok': True, 'records': _df2.to_dict('records')}

    _pe_cached = _smart_cached_call(f"pe_hist:{symbol}", _fetch_pe_history_cacheable,
                                    recheck_interval=21600, fail_retry=300, use_shared_cache=True)
    if _pe_cached and _pe_cached.get('ok') and _pe_cached.get('records'):
        pe_hist_df = pd.DataFrame(_pe_cached['records'])
        if 'date' in pe_hist_df.columns:
            pe_hist_df['date'] = pd.to_datetime(pe_hist_df['date'])
    else:
        pe_hist_df = None
    _perf_mark('本益比(快取/FinMind)')
    val = build_valuation(info, curr_price, rev_yoy if rev_ok else None, f_5d, cash_div, pe_hist_df)

    zones = build_trade_zones(curr_price, ma5, ma20, atr_val, hist)
    # 【V160 延伸3】多時間框架共振：用既有日線 resample 成週線，不額外打 API
    weekly = calc_weekly_resonance(hist)
    # 【V160 延伸2】主力成本免費替代估計（VWAP + 爆量日均價），純用既有資料
    mf_cost = estimate_main_force_cost(hist, inst_df, big_holder)
    # 【R96新增，累積清單第1+2項】趨勢資格硬閘門——股價連續3天收在月線下方
    # 時，不管加權總分多高都無條件出場。這裡先算出來，傳進determine_signal
    # 讓apply_override_rules強制覆蓋分數。
    trend_gate = evaluate_trend_qualification_gate(hist)
    # 【R98續2新增，總指揮官指示：曾提及可選補但未執行】週線版連續3根黑K
    # 提示——跟上面的日線版trend_gate是不同用途：日線版是強制出場硬閘門
    # (已接進determine_signal)，這個週線版是阿水一式長期投資法的warning
    # 等級提示，不強制否決評分，只在卡片上額外顯示，供長期持股判斷參考。
    weekly_trend_gate = evaluate_weekly_trend_gate(hist)
    # 【R96新增，累積清單第5項】當沖佔比+融資餘額籌碼濾網——依附件26。
    # 這兩個都要多打FinMind查詢，各自都有獨立try/except，任一個失敗不
    # 影響另一個或影響戰卡其他部分正常顯示。
    day_trader_ratio = None
    try:
        _dt_info = fetch_day_trading_info_cached(symbol)
        if _dt_info and _dt_info.get('day_trade_volume') is not None:
            # FinMind的Volume是「股」，vol_today是「張」，除以1000統一單位
            day_trader_ratio = evaluate_day_trader_ratio(
                _dt_info['day_trade_volume'] / 1000.0, vol_today)
    except Exception as e:
        print(f"[calculate_signals_worker-診斷] {symbol} 當沖佔比判斷失敗：{type(e).__name__}: {e}")
        day_trader_ratio = None

    margin_regime = None
    try:
        _cur_bal, _bal_hist = fetch_margin_balance_history(
            symbol, token, latest_db_date or get_current_or_last_trading_date())
        if _cur_bal is not None:
            margin_regime = evaluate_margin_balance_regime(_cur_bal, _bal_hist)
    except Exception as e:
        print(f"[calculate_signals_worker-診斷] {symbol} 融資水位判斷失敗：{type(e).__name__}: {e}")
        margin_regime = None

    signal_text, color_border, score, reasons = determine_signal(
        curr_price, ma5, ma20, f_single, vol_ratio, is_open_high_close_low, zones['buffer_pct'],
        gain=gain, enable_doomsday=enable_doomsday,
        market_bull=market_bull, landmine=val['landmine'], is_volume_dump=is_volume_dump,
        # 【V160 R41新增】接上新因子需要的資料(ma60/t_single/f_5d/f_10d/
        # rev_mom/rev_yoy)，這幾個變數本來就已經算好，只是之前沒傳給
        # determine_signal。
        ma60=ma60, trust_buy=t_single, foreign_buy_5d=f_5d, foreign_buy_10d=f_10d,
        rev_mom=rev_mom if rev_ok else None, rev_yoy=rev_yoy if rev_ok else None,
        foreign_buy_streak3=foreign_buy_streak3,
        trend_gate_triggered=trend_gate.get('triggered', False),
        # 【R98新增】連續遞增突破——用hist['High']/hist['Low']算，跟
        # trend_gate同一份hist，不多抓資料。
        higher_high_low_streak=compute_higher_high_low_streak(hist['High'], hist['Low']),
        # 【R98新增】過熱煞車+連續攻擊熄燈反轉——同樣用hist，不多抓資料。
        is_overheated=bool(detect_bollinger_overheat(hist).get("is_overheated")),
        attack_reversal_triggered=bool(detect_attack_streak_reversal(hist).get("reversal_triggered")),
        # 【R98新增】買賣家數差代理指標——網頁端用SUPABASE_CONN查該股最新
        # 一天broker_flows算，查不到傳None(因子靜默跳過)。
        buyer_seller_diff_proxy=_compute_bs_diff_for_web(symbol),
        # 【R98續17新增，總指揮官方向C：價值面融合進短波段判斷】財務體質
        # 風險分數——讀stage_financial_health_scan排程已經算好存進DB的
        # risk_score，不即時重算(太貴)。查不到(還沒被掃到)傳None，因子
        # 靜默跳過。
        financial_risk_score=_get_financial_risk_score_for_web(symbol),
    )
    signal_bg = "#3a1515" if "攻擊" in signal_text else ("#153a20" if "防守" in signal_text else "#332b00")

    detected_patterns = detect_k_line_patterns_v152(hist, atr_val)
    disposal_risk = calc_disposal_risk_proxy(hist, vol_ratio)

    closes = hist['Close'].tail(7).tolist()
    while len(closes) < 7:
        closes.append(closes[-1] if closes else 0)
    # 【R96修復】原本第一個字元是純空白" "(不是視覺上的最短長條，是真的
    # 看不見的空格)——如果剛好有幾天收盤價落在這7天最低值(min_p本身就
    # 一定至少有一天會落在這個值)，那幾天的柱狀圖會直接「消失」，不是真的
    # 天數不夠，是被空白字元蓋掉了，這正是總指揮官反映「近7日只看到4根」
    # 的根因。改用▁(最短但仍然視覺可見的長條字元)取代空白，7天一定都會
    # 有東西畫出來。
    bars, min_p, max_p = "▁▂▃▄▅▆▇█", min(closes), max(closes)
    rng = max_p - min_p if max_p != min_p else 1e-9
    spark_html = "".join([
        f"<span style='color:{'#ff4d4d' if i > 0 and closes[i] > closes[i-1] else ('#00FF00' if i > 0 and closes[i] < closes[i-1] else '#888')}; font-weight:bold;'>"
        f"{bars[max(0, min(7, int((closes[i] - min_p) / rng * 7)))]}</span>" for i in range(7)])

    intraday_trend = ("📉 開高走低·弱勢收下" if is_open_high_close_low
                      else ("🔥 帶量長紅突破" if gain > 2.5 and vol_ratio > 1.2 else "⚖️ 溫和震盪換手"))

    # 【R63新增】現股當沖資格——官方名單，不是自己猜的。
    # 【R95續21，fast_mode】戰情速覽這種大批量場景不需要當沖資格，只在
    # 使用者點開詳細戰卡才會看到。fast_mode=True時直接跳過這次呼叫。
    fast_mode = config.get('fast_mode', False)
    if fast_mode:
        _day_trading = None
    else:
        try:
            _day_trading = fetch_day_trading_info_cached(symbol)
        except Exception as e:
            print(f"[calculate_signals_worker-診斷] {symbol} 當沖資格查詢失敗：{type(e).__name__}: {e}")
            _day_trading = None
    _perf_mark('當沖資格(fast_mode時跳過)')

    if _perf_diag:
        _total = round(time.time() - _perf_t0, 2)
        # 只印「總耗時 + 各階段耗時」單行總結，並把最慢的階段特別標出來，
        # 一眼看出瓶頸在哪一支呼叫。這行會進Streamlit Cloud的log主控台。
        _slowest = max(_perf_marks.items(), key=lambda kv: kv[1]) if _perf_marks else ('無', 0)
        _detail = "｜".join(f"{k}:{v}s" for k, v in _perf_marks.items())
        print(f"[速覽計時] {symbol} 總{_total}s（最慢：{_slowest[0]} {_slowest[1]}s）｜{_detail}")

    return {
        "code": symbol, "name": stock_names.get(symbol, symbol), "price": curr_price, "gain": gain, "error": False,
        "price_date": price_date, "price_is_stale": price_is_stale,
        "day_trading": _day_trading,
        # 【V160 新增】今日開高低——總指揮官回報：有總量/量比，但看不到今天的開盤價與盤中高低點。
        # 這三個值本來就在 hist 最後一列裡，只是先前沒有帶進戰卡。
        "open_today": round(open_price, 2),
        "high_today": round(float(hist['High'].iloc[-1]), 2),
        "low_today": round(float(hist['Low'].iloc[-1]), 2),
        "prev_close": round(prev_price, 2),
        "vol": vol_today, "vol_5d_mean": vol_5d_mean, "vol_change_str": vol_change_str,
        "vol_ratio": vol_ratio, "vol_ratio_label": vol_ratio_label,
        "ma5": ma5, "ma20": ma20, "ma60": ma60,
        "macd_str": macd_str, "macd_color": macd_color, "kdj_str": kdj_str,
        "k_val": k_val, "d_val": d_val,  # 【R96新增】校驗補上，供查1判斷50門檻用
        "rsi_val": rsi_val, "bias_val": bias_val, "atr_val": atr_val,
        "f_buy": f_single, "t_buy": t_single, "d_buy": d_single,
        "margin_diff": margin_diff, "has_margin": has_margin,
        "big_holder": big_holder, "big_holder_date": big_holder_date,
        "f_5d": f_5d, "t_5d": t_5d, "f_10d": f_10d, "t_10d": t_10d,
        "f_pct": f_pct, "t_pct": t_pct,
        "f_5d_pct": f_5d_pct, "t_5d_pct": t_5d_pct, "f_10d_pct": f_10d_pct, "t_10d_pct": t_10d_pct,
        "f_vwap": f_vwap, "t_vwap": t_vwap,
        "atk_zone": zones['atk_zone'], "def_line": zones['def_line'], "buffer_pct": zones['buffer_pct'],
        "trail_stop": zones['trail_stop'], "trail_active": zones['trail_active'],
        "weekly": weekly,   # 【V160 延伸3】週線趨勢，供決策橫幅共振判斷用
        "weekly_trend_gate": weekly_trend_gate,   # 【R98續2新增】週線版連3黑K警示
        "mf_cost": mf_cost,  # 【V160 延伸2】主力成本免費替代估計
        "bb_upper": zones['bb_upper'], "high_20": zones['high_20'],
        "rev_yoy": rev_yoy, "rev_mom": rev_mom, "rev_month": rev_month, "rev_ok": rev_ok,
        "div_display": div_display, "div_yield": div_yield, "manual_div_mode": manual_div_mode,
        "eps": val['eps'], "pe": val['pe'], "fair_price": val['fair_price'],
        "dream_price": val['dream_price'], "cheap_price": val['cheap_price'], "def_price": val['def_price'],
        "pe_percentile": val['pe_percentile'], "pe_p25": val['pe_p25'], "pe_p50": val['pe_p50'],
        "pe_p75": val['pe_p75'], "pe_hist_ok": val['pe_hist_ok'], "pe_extreme": val['pe_extreme'],
        "value_score": val['value_score'], "landmine": val['landmine'],
        "is_first_red": is_first_red, "is_yesterday_strong": is_yesterday_strong,
        "disposal_risk": disposal_risk,
        "blood_line": config.get('pinned_stocks', {}).get(symbol, "手動強制加入"),
        "signal_text": signal_text, "color_border": color_border, "signal_bg": signal_bg,
        "score": score, "reasons": reasons, "sparkline_html": spark_html,
        "latest_db_date": latest_db_date, "intraday_str": intraday_trend,
        "manual_mode": manual_mode, "detected_patterns": detected_patterns,
        "closing_strength": closing_strength,  # 【R96新增】收盤強弱代查結果
        "volume_followthrough": volume_followthrough,  # 【R96新增】量能達標代查結果
        "pullback_health": pullback_health,  # 【R96新增】拉回體檢母關結果
        "rebound_health": rebound_health,  # 【R96新增】累積清單第6項：反彈健康度
        "season_end_warning": season_end_warning,  # 【R96新增】累積清單第8項：投信季底作帳警示
        "day_trader_ratio": day_trader_ratio, "margin_regime": margin_regime,  # 【R96新增】累積清單第5項
        "trend_regime": trend_regime, "rsi_dual": rsi_dual,  # 【R96新增】三態分類+RSI雙版本
        "trend_gate": trend_gate,  # 【R96新增】趨勢資格硬閘門結果
    }


# ==============================================================================
# 七、 視覺渲染引擎 (HTML 強制扁平化防 Markdown 斷行)
# ==============================================================================
def render_stock_card_ui(c, is_portfolio=False, profit=0, roi=0, ent_p=0):
    # 【R98修復，總指揮官反映「戰卡點擊沒反應」+「這行間距太大，頁面拉得很攏長」】
    # 根因找到了：calculate_signals_worker在抓不到資料時（hist為None或K棒
    # 不足21根，通常是FinMind+yfinance當下都失敗/限流）回傳的是
    # {"code":..., "name":..., "error": "原因"}這種只有3個key的極簡dict——
    # 呼叫端(route2/smart_money/持倉/雷達等所有戰卡入口)一律用
    # `if _card: render_stock_card_ui(_card)`判斷，而這個error dict本身
    # 是非空dict，`if _card`還是True，所以還是會呼叫進來這裡。但下面
    # 所有欄位都是用c.get(key, 預設值)在讀，缺的欄位全部悄悄退回0/False/
    # 空字串——結果不是報錯、也不是顯示錯誤訊息，而是整張卡片用一堆
    # 「0」「中性」「—」撐出一大片幾乎空白但版面照樣完整展開的畫面。
    # 使用者觀感上就是「按了戰卡沒反應」（其實有反應，只是反應是一片
    # 空白），同時解釋了「間距太大、頁面拉得很攏長」——那些空白正是被
    # 撐開的空白卡片本身，不是CSS間距問題。
    # 依R62誠實顯示原則：查無資料就該誠實講，不該用0冒充「這檔評分是0」。
    if c.get('error') and not any(k in c for k in ('score', 'signal_text', 'price')):
        st.warning(f"⚠️ {c.get('name', c.get('code', ''))} 這次抓不到完整資料，"
                  f"沒辦法算出戰卡（原因：{c.get('error')}）。通常是FinMind額度用盡"
                  f"+yfinance同時限流或抓不到（K棒不足21根門檻），過一陣子再試一次，"
                  f"或稍後看側欄「🩺資料源健康度檢查」確認資料源是否正常。")
        return

    gain_v = float(c.get('gain', 0))
    gain_c = '#ff4d4d' if gain_v > 0 else ('#00FF00' if gain_v < 0 else '#aaaaaa')
    gain_b = '#3a1515' if gain_v > 0 else ('#153a20' if gain_v < 0 else '#333333')
    portfolio_header = (f"<div style='font-size:14px; margin-bottom:8px; color:#eeeeee;'>持倉成本: {ent_p} | 損益: "
                        f"<strong style='color:{'#ff4d4d' if profit > 0 else '#00FF00'};'>{int(profit):+,} 元</strong> "
                        f"({roi:+.2f}%)</div>") if is_portfolio else ""

    rev_ok = c.get('rev_ok', True)
    yoy_val = c.get('rev_yoy') if rev_ok else None
    mom_val = c.get('rev_mom') if rev_ok else None
    if yoy_val is None:
        yoy_txt, mom_txt, yoy_color, mom_color = "—", "—", "#888", "#888"
    else:
        yoy_val, mom_val = float(yoy_val), float(mom_val)
        yoy_txt, mom_txt = f"{yoy_val:.1f}%", f"{mom_val:.1f}%"
        yoy_color = "#ff4d4d" if yoy_val > 0 else ("#00FF00" if yoy_val < 0 else "#00d2ff")
        mom_color = "#ff4d4d" if mom_val > 0 else "#00FF00"

    sig_t = c.get('signal_text', '')
    # 【V160 B#1+#2】動詞化決策 + 進場價格區間：把系統術語翻成秒讀動詞，並附具體價格帶
    _def_line = float(c.get('def_line', 0) or 0)
    _atk = float(c.get('atk_zone', 0) or 0)
    _price = float(c.get('price', 0) or 0)
    if '偏多攻擊' in sig_t:
        verdict_word, verdict_color, verdict_bg = "🔥 建議進攻", "#ff4d4d", "#3a1515"
        verdict_action = f"參考區間 {_def_line:.1f}〜{_atk:.1f}｜跌破 {_def_line:.1f} 停損"
        # 【R98續24修復，總指揮官反映聯茂(6213)案例：參考區間485.9~565.9，
        # 但即時價已經583，還顯示「建議進攻」會誤導人去追高】根因：這個
        # 區間是用c['price']（決策基準價，約3分鐘更新一次）算的_def_line/
        # _atk，但畫面最上方大字顯示的是c['live_price']（每次查詢就更新，
        # 更即時）——遇到急拉/漲停這類快速噴出的股票，即時價可能早就
        # 衝出決策基準價當時算出的區間上緣，變成「畫面在講3分鐘前的判斷，
        # 但你手上看到的是已經噴出去的即時價」，這時候「建議進攻」四個字
        # 沒有變化會讓人誤以為現在583還能追。這裡加一個誠實提醒，不改變
        # 底層評分（評分本身可能依然合理，只是進場區間的參考意義已經
        # 改變），只在畫面上把這個落差講清楚，讓使用者自己判斷要不要追。
        if c.get('live_price') is not None and _atk > 0 and float(c['live_price']) > _atk:
            verdict_action += (f"｜⚠️即時價{float(c['live_price']):.1f}已超出區間上緣"
                              f"（此區間是{_price:.1f}時算出的，非即時追價建議，"
                              f"此刻追高風險已跟原始判斷情境不同）")
    elif '觀察偏多' in sig_t:
        verdict_word, verdict_color, verdict_bg = "🟡 觀望偏多", "#ffab00", "#332b00"
        verdict_action = f"站穩 {_price:.1f} 且量能回穩再進，防守 {_def_line:.1f}"
    elif '偏空防守' in sig_t:
        verdict_word, verdict_color, verdict_bg = "🔵 建議撤退", "#2979ff", "#152a3a"
        verdict_action = f"已轉空｜持有者減碼，空手勿接刀"
    elif '轉弱謹慎' in sig_t:
        verdict_word, verdict_color, verdict_bg = "⚠️ 轉弱警戒", "#ff9100", "#3a2a15"
        # 【V160 修復】原本一律寫「跌破 X 應出場」，但當現價已經在防守線之下（急跌股均線落後），
        # 這句話變成馬後炮（它已經跌破了卻叫你等跌破）。改成依現價 vs 防守線動態判斷：
        # 已跌破→提示結構已破、應檢視出場；還在防守線上→才是「跌破 X 應出場」的預警。
        if _def_line > 0 and _price < _def_line:
            verdict_action = f"已跌破 {_def_line:.1f} 均線防線｜結構已轉弱，反彈無力應出場"
        else:
            verdict_action = f"結構轉弱｜守住 {_def_line:.1f}，跌破應出場"
    else:
        verdict_word, verdict_color, verdict_bg = "⚖️ 中性等待", "#888", "#222"
        verdict_action = f"無明確方向｜突破 {_atk:.1f} 或跌破 {_def_line:.1f} 再表態"

    # 【V160 延伸3】多時間框架共振：用週線趨勢調整日線結論。
    # 只降級不升級——升級等於重複計算同一個訊號並放大部位風險。
    # 週線資料不足時完全不調整、也不顯示，不假裝有判斷。
    _weekly = c.get('weekly', {}) or {}
    # 【V160新增】三個戰區各自的小結論，刻意獨立計算允許彼此矛盾——
    # 「基本面便宜但技術面轉弱」這種分歧，混成總分就會被平均掉看不見。
    _fh_for_score = st.session_state.get(f'fin_health_{c.get("code")}')
    _z1_badge, _z1_color, _z1_reason = score_zone1_fundamental(c, _fh_for_score)
    _z2_badge, _z2_color, _z2_reason = score_zone2_technical(c)
    _z3_badge, _z3_color, _z3_reason = score_zone3_chips(c)
    _adj_verdict, _reso_note = apply_timeframe_resonance(verdict_word, c.get('score', 0), _weekly)
    if _adj_verdict != verdict_word:
        # 降級後要一併換色，否則會出現「文字寫觀望、底色仍是進攻紅」的矛盾
        _vmap = {
            "🔥 建議進攻": ("#ff4d4d", "#3a1515"),
            "🟡 觀望偏多": ("#ffab00", "#332b00"),
            "🔵 建議撤退": ("#2979ff", "#152a3a"),
            "⚠️ 轉弱警戒": ("#ff9100", "#3a2a15"),
            "⚖️ 中性等待": ("#888", "#222"),
        }
        verdict_word = _adj_verdict
        verdict_color, verdict_bg = _vmap.get(_adj_verdict, ("#888", "#222"))
    if _reso_note:
        verdict_action = f"{verdict_action}<br><span style='color:#7ab8ff;'>{_reso_note}</span>"

    k_patterns = c.get('detected_patterns', [])
    if k_patterns:
        _kt = k_patterns[0].get('text', '')
        _kicon = "📉" if '黑' in _kt else ("🌀" if _kt == '壓縮盤整' else "🔥")
        k_text = f"{_kicon} {_kt}"
    else:
        k_text = "⚖️ 無明顯型態"
    k_tags = f"<span class='k-tag'>{k_text}</span>"
    # 【R96新增】三態徽章放在最前面，總指揮官明確要求——這是「判斷框架」
    # 本身，要先看到現在是哪一態，才能正確解讀後面其他所有判斷。
    k_tags = _fmt_trend_regime_tag(c) + k_tags
    if c.get('landmine'):
        k_tags += ("<span class='m-tooltip k-tag' style='background:#5a1010; color:#ff8080;'>💀 基本面地雷警告"
                   "<span class='m-tooltiptext'>同時滿足：估值落在自身歷史最貴區間（或PE>30）、最新月營收年減、外資近5日賣超。"
                   "高估值 + 基本面轉差 + 籌碼失守，屬於典型的高處不勝寒結構。</span></span>")

    # 【R98續2新增，總指揮官指示：馬上處理，不要等之後可以考慮】週線版
    # 連續3黑K提示（阿水一式長期投資法規則）——原本擺在需要先點「查詢
    # 深度財報」按鈕才看得到的地方，不理想（這個判斷本身不依賴財報資料）。
    # 移到這裡跟k_tags同一個「每張卡片都常駐渲染」的區塊，不需要任何
    # 按鈕觸發，比照地雷警告(landmine)標籤同樣的呈現模式。
    _wtg = c.get('weekly_trend_gate') or {}
    if _wtg.get('warning_triggered'):
        k_tags += ("<span class='m-tooltip k-tag' style='background:#4a1f00; color:#ffab70;'>📅 週線長期持股警示"
                   f"<span class='m-tooltiptext'>{_wtg.get('reason', '')}</span></span>")

    # 【V159 新增】PE百分位極端值提示：跟地雷不同，不要求基本面轉差，
    # 純粹標示「估值已經遠離自己3年常態」，常見於重大題材重估行情。
    if c.get('pe_extreme') and not c.get('landmine'):
        pctl_disp = c.get('pe_percentile')
        k_tags += (f"<span class='m-tooltip k-tag' style='background:#1a2a4a; color:#7ab8ff;'>⚡ 估值遠離歷史常態"
                   f"<span class='m-tooltiptext'>目前PE落在近3年歷史第{pctl_disp:.0f}百分位，屬於極端偏高。"
                   f"常見於重大題材重估（如新合作案、供應鏈題材發酵），不必然代表基本面轉差，"
                   f"但建議對照近期消息面，確認題材是否具體、能否支撐目前估值，再判斷是否追高。</span></span>")

    # 【V157 新增】簡化版處置/注意股風險提示，明確標註非官方模型，避免使用者誤以為是精算結果
    d_risk = c.get('disposal_risk') or {}
    if d_risk.get('level') == 'high':
        k_tags += (f"<span class='m-tooltip k-tag' style='background:#5a3d10; color:#ffcc66;'>🚨 處置風險提示（簡化版）"
                   f"<span class='m-tooltiptext'>近6個營業日累計漲跌 {d_risk.get('six_day_gain', 0):+.1f}%，激進程度偏高。"
                   f"這只是用「六日累計漲跌+成交量異常」做的簡化代理指標，<b>不是</b>證交所官方判定模型"
                   f"（官方規則涉及近百項法規細節），僅供留意，請勿單獨依賴此標籤做交易決策。</span></span>")
    elif d_risk.get('level') == 'watch':
        k_tags += (f"<span class='m-tooltip k-tag' style='background:#3d3510; color:#e6c34d;'>⚠️ 波動偏大（簡化版）"
                   f"<span class='m-tooltiptext'>近6個營業日累計漲跌 {d_risk.get('six_day_gain', 0):+.1f}%，"
                   f"波動程度已略高於平常，非官方處置判定，僅供參考。</span></span>")

    # 【R79新增】處置股/注意股徽章——真正對照TWSE/TPEx官方公告，不是上面
    # calc_disposal_risk_proxy那個簡化代理指標。兩者並存：上面那個是「激進
    # 程度提醒」，這個是「官方真的已經公告」，意義不同，都顯示不衝突。
    k_tags += get_disposal_attention_badge(c.get('code', ''))

    # 【R96新增，累積清單第8項】投信季底作帳警示——投信連續買超如果發生在
    # 季底前，可能是作帳行情不是真的看好，作帳結束(季底一過)可能倒貨。
    _sew = c.get('season_end_warning') or {}
    if _sew.get('warning'):
        k_tags += (f"<span class='m-tooltip k-tag' style='background:#4a3010; color:#ffb84d;'>"
                   f"📅 留意季底作帳<span class='m-tooltiptext'>{_sew.get('reason', '')}</span></span>")

    # 【R63新增】現股當沖資格徽章——用FinMind的TaiwanStockDayTrading官方名單，
    # 不是猜的。查無資料時誠實不顯示（不確定就不標，不假裝知道）。
    _dt = c.get('day_trading')
    if _dt and _dt.get('eligible'):
        if _dt.get('buy_after_sale') == '*':
            k_tags += (f"<span class='m-tooltip k-tag' style='background:#10401a; color:#7CFC9A;'>🎯 可當沖（僅先買後賣）"
                       f"<span class='m-tooltiptext'>官方當沖標的名單（FinMind，{_dt.get('date','')}）。"
                       f"該股目前被列入「暫停先賣後買」，當日只能先買後賣，不能先賣後買，但現股當沖本身仍可執行。"
                       f"實際能否先賣後買，仍以你券商的帳戶資格與券源為準。</span></span>")
        else:
            k_tags += (f"<span class='m-tooltip k-tag' style='background:#10401a; color:#7CFC9A;'>🎯 可當沖"
                       f"<span class='m-tooltiptext'>官方當沖標的名單（FinMind，{_dt.get('date','')}），"
                       f"先買後賣、先賣後買皆可。實際能否先賣後買，仍以你券商的帳戶資格與券源為準，"
                       f"這裡只反映交易所對這檔股票本身的資格。</span></span>")

    vol_ratio = float(c.get('vol_ratio', 0))
    price, ma5, ma20 = float(c.get('price', 0)), float(c.get('ma5', 0)), float(c.get('ma20', 0))
    if vol_ratio > 1.5:
        vol_semantic = "⚠️破線殺盤" if price < ma20 else ("🔥帶量上攻" if price > ma5 else "⚠️爆量震盪")
    elif vol_ratio < 0.6:
        vol_semantic = "🧊量縮沉澱"
    else:
        vol_semantic = "⚖️溫和換手"

    tooltip_vol = ("<span class='m-tooltiptext'>爆量比 = 今日量 ÷ 前5日均量。小於0.6為量縮沉澱（多空觀望），"
                   "0.8~1.2為正常換手，大於1.5為爆量（需搭配位階判斷是攻擊或倒貨）。</span>")
    tags_html = (f"<div style='display:flex; flex-wrap:wrap; gap:6px; align-items:center; margin-top:5px;'>"
                 f"<span class='m-tooltip' style='white-space:nowrap; display:inline-block; background:#2a2a2a; padding:2px 8px; border-radius:4px; font-size:12px; color:#e67e22;'>"
                 f"{c.get('vol_ratio_label')} [{vol_semantic}]{tooltip_vol}</span>"
                 f"<span style='white-space:nowrap; display:inline-block; background:#2a2a2a; padding:2px 8px; border-radius:4px; font-size:12px; color:#00FF00;'>"
                 f"{c.get('intraday_str')}</span></div>")

    rsi_v, bias_v = float(c.get('rsi_val', 0)), float(c.get('bias_val', 0))
    # 【R96新增】接上三態雙版本RSI判斷，取代原本單純的>70/<30二分。
    # rsi_dual為None時退回原本二分版，不會讓RSI這行顯示不出東西。
    _rsi_dual = c.get('rsi_dual')
    if _rsi_dual and _rsi_dual.get('verdict') != 'neutral':
        rsi_color = {"strong": "#ff4d4d", "weak": "#00c853"}.get(_rsi_dual['verdict'], "#555")
        rsi_txt = _rsi_dual.get('label', '⚖️整理')
        tooltip_rsi = (f"<span class='m-tooltiptext'>{_rsi_dual.get('detail', '')}</span>")
    elif _rsi_dual:
        rsi_color = "#555"
        rsi_txt = _rsi_dual.get('label', '⚖️整理')
        tooltip_rsi = (f"<span class='m-tooltiptext'>{_rsi_dual.get('detail', '')}</span>")
    else:
        rsi_color = "#ff4d4d" if rsi_v > 70 else ("#00c853" if rsi_v < 30 else "#555")
        rsi_txt = "🔴超買" if rsi_v > 70 else ("🟢超賣" if rsi_v < 30 else "⚖️整理")
        tooltip_rsi = ("<span class='m-tooltiptext'>相對強弱指標。大於70超買（追高風險升高，但強勢股可鈍化），"
                       "小於30超賣（短線反彈機率高）。實戰：RSI由50向上突破且帶量，是波段轉強的起手式。"
                       "（目前無法判斷趨勢/盤整狀態，暫用傳統版）</span>")
    bias_color = "#ff4d4d" if bias_v > 5 else ("#2979ff" if bias_v < -5 else "")
    bias_txt = "🔴過熱" if bias_v > 5 else ("🔵超跌" if bias_v < -5 else "")

    rsi_html = (f"<span class='m-tooltip'>RSI(14): <strong style='color:#fff;'>{rsi_v:.1f}</strong> "
                f"<span style='background:{rsi_color}; color:#fff; padding:2px 6px; border-radius:4px; font-weight:bold; font-size:11px;'>{rsi_txt}</span>{tooltip_rsi}</span>")

    tooltip_bias = ("<span class='m-tooltiptext'>股價與20MA的距離。起漲醞釀期通常貼近均線(0%~2%)。"
                    "大於+5%短線過熱（追價風險高，宜等回測均線）；小於-5%超跌（易有反彈，但需確認不是崩跌趨勢）。</span>")
    bias_html = (f"<span class='m-tooltip'>乖離率(20): <strong style='color:{bias_color if bias_color else '#fff'};'>{bias_v:+.2f}%</strong>"
                 + (f" <span style='background:{bias_color}; color:#fff; padding:2px 6px; border-radius:4px; font-weight:bold; font-size:11px;'>{bias_txt}</span>" if bias_txt else "")
                 + f"{tooltip_bias}</span>")

    db_date = str(c.get('latest_db_date', '') or '')
    display_date, warn_icon = " (尚無資料)", ""
    if db_date:
        try:
            dt_obj = datetime.strptime(db_date, "%Y-%m-%d")
            display_date = f" {dt_obj.strftime('%m/%d')}({['一','二','三','四','五','六','日'][dt_obj.weekday()]})"
            tooltip_warn = "<span class='m-tooltiptext'>證交所尚未更新今日籌碼，此為系統尋獲之最新一筆歷史資料。</span>"
            warn_icon = "" if db_date == datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d") else f"<span class='m-tooltip'> ⚠️{tooltip_warn}</span>"
        except Exception:
            display_date = f" ({db_date})"

    # 【R96修復】總指揮官抓到：外資/投信單日買賣超數字顏色原本寫死紅色，
    # 不管買超還是賣超都一樣，賣超(負數)顯示紅色違反這個app「紅漲綠跌」
    # 的既有慣例(賣超是偏空訊號，該用綠色，不是紅色)。5日/10日數字則原本
    # 完全沒上色。這裡統一依正負號決定顏色，買超(正數)紅、賣超(負數)綠、
    # 剛好0用灰色，外資/投信/單日/5日/10日全部套用同一套規則。
    def _inst_color(v):
        v = float(v or 0)
        return "#ff4d4d" if v > 0 else ("#00c853" if v < 0 else "#888")
    _f_color, _f5_color, _f10_color = (_inst_color(c.get('f_buy')), _inst_color(c.get('f_5d')),
                                       _inst_color(c.get('f_10d')))
    _t_color, _t5_color, _t10_color = (_inst_color(c.get('t_buy')), _inst_color(c.get('t_5d')),
                                       _inst_color(c.get('t_10d')))

    bh_val = c.get('big_holder', 0.0)
    bh_display = f"{bh_val}%" if isinstance(bh_val, (int, float)) and bh_val > 0 else str(bh_val or ERR_NO_DATA)

    sig_t = c.get('signal_text', '')
    if '攻擊' in sig_t:
        sig_tip = "實戰：帶量突破均線糾結、法人同步進場，動能強勁。可順勢operate，但務必用防守線控管。"
    elif '防守' in sig_t or '警告' in sig_t or '轉弱' in sig_t:
        sig_tip = "實戰：可能高檔倒貨、爆量下殺或破線轉弱。已持有者減碼，空手者勿接刀。"
    else:
        sig_tip = "實戰：目前盤整或溫和換手，無明確單向動能。等突破或跌破再表態。"
    tooltip_sig = (f"<span class='m-tooltiptext'><b>[評分級距說明]</b><br>🔥 偏多攻擊 (>= 3分)<br>🟡 觀察偏多 (1~2分)<br>"
                   f"⚖️ 中立震盪 (0分)<br>⚠️ 轉弱謹慎 (-1~-2分)<br>🔵 偏空防守 (<=-3分)"
                   f"<hr style='margin:4px 0; border-color:#9fb3c8;'>{sig_tip}</span>")

    vs = int(c.get('value_score', 0))
    vs_color = "#00c853" if vs >= 60 else ("#f1c40f" if vs >= 40 else "#ff4d4d")
    tooltip_vs = ("<span class='m-tooltiptext'>⚠️這是「綜合評分」不是純估值分數：同時混合了本益比位階、"
                  "營收年增動能、殖利率、外資5日籌碼進出等多個面向加權而成。所以分數高不代表「便宜」，"
                  "而是「估值+動能+籌碼」整體有利；>=60 綜合面偏多，<40 偏弱或體質轉差。看純估值請直接看上方PE百分位。</span>")

    eps_v = float(c.get('eps', 0) or 0)
    pe_v = float(c.get('pe', 0) or 0)
    pe_hist_ok = bool(c.get('pe_hist_ok'))
    pe_pctl = c.get('pe_percentile')
    pe_txt = f"{pe_v:.1f}" if pe_v > 0 else "—"
    fair_txt = f"{c.get('fair_price')}" if float(c.get('fair_price', 0) or 0) > 0 else "—"
    dream_txt = f"{c.get('dream_price')}" if float(c.get('dream_price', 0) or 0) > 0 else "—"
    cheap_txt = f"{c.get('cheap_price')}" if float(c.get('cheap_price', 0) or 0) > 0 else "—"
    defp_txt = f"{c.get('def_price')}" if float(c.get('def_price', 0) or 0) > 0 else "—"

    # 【V157】估價模型改用歷史PE百分位，每個數字各自掛獨立tooltip。
    # 【V160 R42】同業PE中位數——「自己歷史百分位」問相對過去貴不貴，
    # 「同業中位數」問相對同產業貴不貴，兩個維度一起看才完整。
    _ind_stats = get_industry_pe_stats()
    _stock_to_ind_lookup, _ = fetch_industry_map()   # 這個函式本身有24小時快取，這裡呼叫幾乎零成本
    _ind_name = _stock_to_ind_lookup.get(c.get('code', ''))
    _peer_txt = ""
    if _ind_name and _ind_name in _ind_stats and pe_v > 0:
        _peer = _ind_stats[_ind_name]
        _peer_txt = (f"｜<span class='m-tooltip' style='color:#b39ddb;'>同業中位數 {_peer['median_pe']:.1f}"
                    f"<span class='m-tooltiptext'>「{_ind_name}」產業目前有{_peer['sample_count']}檔樣本"
                    f"（{_peer.get('updated_date','')}全市場掃描時計算），中位數PE={_peer['median_pe']:.1f}。"
                    f"這跟上面的「自己歷史百分位」是不同維度：一個問「比自己過去貴嗎」，"
                    f"這個問「比同業貴嗎」——電子股/傳產股的估值水準天生不同，只跟自己比看不出來。"
                    f"樣本需要先跑過一次全市場掃描才會有，且產業樣本<5檔不會顯示。</span></span>")

    if pe_hist_ok and pe_pctl is not None:
        pctl_color = "#00c853" if pe_pctl <= 30 else ("#ff4d4d" if pe_pctl >= 70 else "#f1c40f")
        pctl_txt = f"<strong style='color:{pctl_color};'>PE百分位 {pe_pctl:.0f}%</strong>"
        tooltip_pctl = (f"<span class='m-tooltiptext'>目前 PE={pe_txt} 落在這檔股票近3年歷史分布的第 {pe_pctl:.0f} 百分位"
                        f"（0%=近3年最便宜，100%=近3年最貴）。百分位法用個股自己的歷史區間比較，"
                        f"比套一個死的PE倍數更合理——電子股跟傳產股的合理本益比天差地遠。</span>")
        pe_html = f"PE <strong style='color:#fff;'>{pe_txt}</strong> <span class='m-tooltip'>({pctl_txt}){tooltip_pctl}</span>{_peer_txt}"
        tooltip_cheap = "<span class='m-tooltiptext'>近3年PE第25百分位 × EPS，股價來到這裡代表用歷史相對便宜的估值買進。</span>"
        tooltip_fair = "<span class='m-tooltiptext'>近3年PE中位數 × EPS，股價的歷史「常態」估值中樞參考。</span>"
        tooltip_dream = "<span class='m-tooltiptext'>近3年PE第75百分位 × EPS，股價來到這裡代表市場已用相對樂觀的估值定價，追高風險上升。</span>"
    elif eps_v > 0:
        # 【R95】eps>0但PE歷史樣本不足（新股/資料源缺漏）——這種情況「退回估算」
        # 這句話是真的：build_valuation在eps>0時會用EPS×固定倍數算出fair/dream_price，
        # 所以下面便宜價/合理價/樂觀價會有數字，這則訊息準確。
        pe_html = f"PE <strong style='color:#fff;'>{pe_txt}</strong> <span style='color:#888; font-size:11px;'>(樣本不足，退回估算)</span>{_peer_txt}"
        tooltip_cheap = ""
        tooltip_fair = f"<span class='m-tooltiptext'>歷史PE樣本不足（可能是新股或資料源缺漏），暫用 EPS×{int(PE_FAIR_MULT)} 粗略估算合理價，準確度較低。</span>"
        tooltip_dream = f"<span class='m-tooltiptext'>歷史PE樣本不足，暫用 EPS×{int(PE_DREAM_MULT)} 粗略估算樂觀價，準確度較低。</span>"
        cheap_txt = "—"
    else:
        # 【R95修復】eps<=0時原本假裝「樣本不足，退回估算」，但build_
        # valuation根本不會算fair/dream_price，改成講真正原因：無正EPS，
        # 本益比法本身就不適用。
        pe_html = f"PE <strong style='color:#fff;'>{pe_txt}</strong> <span style='color:#888; font-size:11px;'>(無正EPS，本益比法不適用)</span>{_peer_txt}"
        tooltip_cheap = "<span class='m-tooltiptext'>公司目前無正EPS（虧損或無獲利資料），本益比估價法不適用，暫無法算出便宜價。</span>"
        tooltip_fair = "<span class='m-tooltiptext'>公司目前無正EPS（虧損或無獲利資料），本益比估價法不適用，暫無法算出合理價。</span>"
        tooltip_dream = "<span class='m-tooltiptext'>公司目前無正EPS（虧損或無獲利資料），本益比估價法不適用，暫無法算出樂觀價。</span>"
        cheap_txt = "—"

    tooltip_defp = (f"<span class='m-tooltiptext'>現金股利 ÷ {int(YIELD_DEF_RATE*100)}%殖利率回推的防守價。"
                    f"現價跌破此價時，長線存股資金通常會進場承接，具一定支撐意義。</span>")

    trail_txt = f"{c.get('trail_stop')}" if float(c.get('trail_stop', 0) or 0) > 0 else "—"
    trail_state = "🟢有效保護" if c.get('trail_active') else "🔴已跌破"
    bb_txt = f"{c.get('bb_upper')}" if float(c.get('bb_upper', 0) or 0) > 0 else "—"
    tooltip_trail = ("<span class='m-tooltiptext'>動態移動停利 = 近20日最高價 − 1.5×ATR。股價創新高時停利線同步上移，"
                     "跌破即代表趨勢轉弱，鎖住波段獲利。「已跌破」表示現價已低於此線，短多結構受損。</span>")
    tooltip_bb = "<span class='m-tooltiptext'>布林通道上軌 = 20MA + 2倍標準差，作為短線滿足點/壓力參考。</span>"

    html_lines = [
        # 【R96架構調整】拿掉「當沖模式角標」——不再區分波段/當沖模式，
        # 只有「這張卡有沒有拿到當沖延伸資料」的差別，不需要角標。
        (f"""<div style="border:2px solid {c.get('color_border')}; border-radius:8px; padding:15px; """
         f"""background:#16191f; margin-bottom:12px; color:#eeeeee;">"""),
        portfolio_header,
        f"""<div style="display:flex; justify-content:space-between; align-items:center;">""",
        f"""<span style="font-weight:bold; font-size:19px; color:#ffffff; display:flex; align-items:center; flex-wrap:wrap; gap:6px;">""",
        f"""{c.get('name')} <span style="color:#00d2ff; font-size:15px;">({c.get('code')})</span>{k_tags}</span>""",
        f"""<span style="font-size:13px; color:#f1c40f; white-space:nowrap;" title="{_expand_blood_line(c.get('blood_line', ''))}">{_expand_blood_line(c.get('blood_line', ''))}</span></div>""",
        # 【V160 Round38/R62/R64/R96，見開發歷程.md】即時報價獨立一行
        # 放在主價格正上方，跟決策基準價分開顯示，大字優先顯示即時價。
        # 【R98續74重大修正，總指揮官反映「🟢即時更新」跟下面「資料為
        # XX收盤（非即時）」同時出現看不懂】查證發現真正原因：這顆圓點
        # 的顏色/emoji，判斷依據是live_change_pct(漲跌方向：跌=綠漲=紅，
        # 台灣習慣紅漲綠跌)，完全跟「這筆資料是不是真的新鮮」無關！
        # 文字寫「即時更新」但視覺顏色卻是漲跌色，這是語意脫鉤的真bug——
        # 原本雖然有⏳(沿用)/🧊(跨session持久化)這兩個標記，但藏在時間
        # 字串前面很不顯眼，容易被大圓點吸走注意力。改成：圓點顏色改
        # 反映「資料新鮮度」(真正剛抓到=綠、⏳沿用=黃、🧊持久化=橘)，
        # 漲跌方向另外用▲▼箭頭清楚標示，不要用同一個視覺元素混合表達
        # 兩種不相關的資訊；文字標題也改成更誠實的措辭，不是沿用資料
        # 時還講「更新」這種暗示「剛剛才發生」的字眼。
        (lambda _has_live=(c.get('live_price') is not None): (
            (lambda _is_carried=c.get('live_is_carried'), _is_persistent=c.get('live_is_carried_persistent'): (
                f"""<div style="font-size:13px; margin-top:6px; margin-bottom:-2px;">"""
                f"""<span style="color:{'#ffab00' if _is_persistent else ('#f1c40f' if _is_carried else '#00e676')};">"""
                f"""{'🧊 沿用較舊資料' if _is_persistent else ('⏳ 沿用上次查到' if _is_carried else '🟢 剛查到的即時價')}"""
                f"""</span>"""
                f""" <span style="color:{'#ff4d4d' if (c.get('live_change_pct') or 0) > 0 else ('#00e676' if (c.get('live_change_pct') or 0) < 0 else '#aaaaaa')};">"""
                f"""{'▲' if (c.get('live_change_pct') or 0) > 0 else ('▼' if (c.get('live_change_pct') or 0) < 0 else '─')}</span>"""
                + (f""" ・{c['live_time']}""" if c.get('live_time') else "")
                + f"""</div>"""
            ))()
        ) if _has_live else "")(),
        # 【V160 Round36 新增，R50排版修復，R64位置調整】總指揮官回報股價跟實際收盤
        # 有落差，查出是yfinance資料偶爾晚一天更新——這裡誠實標示「這個價格實際上
        # 是哪天的」，不讓過時資料悄悄冒充成即時價格誤導判斷。只在真的過時時才顯示。
        (f"""<div style="font-size:12px; color:#ffab00; margin-top:2px; margin-bottom:2px; """
         f"""white-space:nowrap; overflow:hidden; text-overflow:ellipsis;" """
         f"""title="價格資料為 {c.get('price_date','')} 收盤（非最新交易日，資料來源延遲）">"""
         f"""⚠️ 資料為{c.get('price_date','')}收盤（非即時，點此看說明）</div>"""
         if c.get('price_is_stale') else ""),
        f"""<div style="display:flex; justify-content:space-between; align-items:flex-end; margin:10px 0;">""",
        (lambda _has_live=(c.get('live_price') is not None): (
            (lambda _bp, _bpct: (
                f"""<div style="display:flex; align-items:center;">"""
                f"""<span style="font-size:32px; font-weight:bold; color:#ffffff;">{_bp:.2f}</span>"""
                f"""<span style="font-size:15px; color:{'#ff4d4d' if _bpct > 0 else ('#00e676' if _bpct < 0 else '#aaaaaa')}; """
                f"""background:{'#3a1515' if _bpct > 0 else ('#153a20' if _bpct < 0 else '#333333')}; padding:3px 8px; """
                f"""border-radius:4px; margin-left:10px; font-weight:bold;">{_bpct:+.2f}%</span></div>"""
            ))(c['live_price'] if _has_live else float(c.get('price', 0)),
               (c.get('live_change_pct') or 0.0) if _has_live else gain_v)
        ))(),
        # 【R96新增】決策基準價小字備註——大字現在顯示即時價，這裡標註
        # 判斷邏輯實際根據哪個價格算的(每3分鐘更新，不是跟即時價同步)。
        (f"""<div style="font-size:11px; color:#888; margin-top:-6px; margin-bottom:6px;">"""
         f"""決策基準價 {float(c.get('price', 0)):.2f}（判斷/評分依據，約3分鐘更新一次）</div>"""
         if c.get('live_price') is not None else ""),
        f"""<div style="font-size:14px; display:flex; align-items:center; color:#ccc;">近7日: {c.get('sparkline_html')}</div></div>""",
        # 【R96架構調整】拿掉「只在當沖模式才插入」的判斷，改成永遠呼叫，
        # 顯示內容取決於有沒有fetch_intraday_extras=True的完整資料。
        _fmt_daytrade_summary(c),
        # 【R96調整】順序改成：當沖摘要→當沖建議→波段建議，兩者仍分開
        # 顯示、分開判斷邏輯。當沖建議橫幅用evaluate_daytrade_
        # recommendation()獨立整合層，沒有資料時完全不顯示。
        _fmt_daytrade_verdict_banner(c),
        # 【V160 B#1+#2】秒讀決策橫幅：價格正下方，動詞+進場價格區間。
        # 【R96新增】明確標註「📈波段建議」，決策橫幅分區域顯示波段/當沖。
        (f"""<div style="background:{verdict_bg}; border:1px solid {verdict_color}; border-radius:6px; padding:10px 12px; margin-bottom:10px;">"""
         f"""<div style="font-size:10px; color:#888; margin-bottom:2px;">📈 波段建議</div>"""
         f"""<div style="display:flex; justify-content:space-between; align-items:center;"><span style="font-size:18px; font-weight:bold; color:{verdict_color};">{verdict_word}</span><span style="font-size:11px; color:#888;">評分 {c.get('score')}</span></div><div style="font-size:12px; color:#ddd; margin-top:4px;">{verdict_action}</div></div>"""),
        f"""<div style="background:#0e1117; padding:8px; border-radius:4px; margin-bottom:10px;">""",
        # 【V160 新增】今日開高低一行——盤中可看到開盤價與當日高低區間，
        # 收盤後就是當日完整的 OHLC。相對昨收上色（紅漲綠跌，台股慣例）。
        (lambda _o, _h, _l, _pc: (
            f"""<div style="font-size:13px; margin-bottom:4px; color:#bbb;">"""
            f"""開: <strong style="color:{'#ff4d4d' if _o > _pc else ('#00c853' if _o < _pc else '#bbb')};">{_o}</strong> | """
            f"""高: <strong style="color:#ff4d4d;">{_h}</strong> | """
            f"""低: <strong style="color:#00c853;">{_l}</strong> | """
            f"""昨收: {_pc}</div>"""
        ) if (_o and _h and _l and _pc) else "")(
            c.get('open_today'), c.get('high_today'), c.get('low_today'), c.get('prev_close')),
        f"""<div style="font-size:13px; margin-bottom:4px;">總量: {c.get('vol'):,.0f}張 | {c.get('vol_change_str')}</div>""",
        tags_html,
        f"""</div>""",

        f"""<div class="zone-box zone-1"><div class="zone-title">❤️ 第一戰區：基本、財報與估價</div>""",
        f"""<div style="font-size:13px; margin-bottom:4px;">營收 年增 <strong style="color:#ffffff;">({c.get('rev_month')})</strong>: <strong style="color:{yoy_color};">{yoy_txt}</strong> | 月增: <strong style="color:{mom_color};">{mom_txt}</strong></div>""",
        f"""<div style="font-size:13px; margin-bottom:4px;">除權息資訊: <strong style="color:#d200ff;">{c.get('div_display')} (殖利率: {float(c.get('div_yield', 0)):.1f}%)</strong></div>""",
        f"""<div style="font-size:13px; margin-bottom:4px;">{pe_html} | <span class='m-tooltip'>便宜價{tooltip_cheap}</span> <strong style="color:#00e676;">{cheap_txt}</strong> | <span class='m-tooltip'>合理價{tooltip_fair}</span> <strong style="color:#00c853;">{fair_txt}</strong> | <span class='m-tooltip'>樂觀價{tooltip_dream}</span> <strong style="color:#ff4d4d;">{dream_txt}</strong></div>""",
        f"""<div style="font-size:13px; margin-bottom:4px;"><span class='m-tooltip'>殖利率防守價{tooltip_defp}</span>: <strong style="color:#00d2ff;">{defp_txt}</strong></div>""",
        f"""<div style="font-size:13px;"><span class='m-tooltip'>戰情室價值分數{tooltip_vs}</span>: <strong style="color:{vs_color}; font-size:15px;">{vs} 分</strong></div>""",
        _fmt_zone_summary(_z1_badge, _z1_color, _z1_reason),
        """</div>""",

        f"""<div class="zone-box zone-2"><div class="zone-title">⚔️ 第二戰區：技術、防守與移動停利</div>""",
        f"""<div style="font-size:13px; margin-bottom:4px; display:flex; justify-content:space-between;">""",
        f"""<span>5MA: <b style="color:#ffffff;">{float(c.get('ma5', 0)):.1f}</b></span><span>20MA: <b style="color:#ffffff;">{float(c.get('ma20', 0)):.1f}</b></span><span>60MA: <b style="color:#ffffff;">{float(c.get('ma60', 0)):.1f}</b></span></div>""",
        f"""<div style="font-size:13px; margin-bottom:4px; line-height:2.2;">MACD 動能: <strong style="color:{c.get('macd_color')}; margin-right:15px;">{c.get('macd_str')}</strong>{rsi_html} <span style="margin-left:15px;">{bias_html}</span></div>""",
        f"""<div style="font-size:12px; color:#aaa; margin-top:6px; border-top:1px dashed #444; padding-top:4px;">""",
        f"""<span class='m-tooltip' style='color:#ff4d4d;'>短線停利點:<span class='m-tooltiptext'>現價加上1倍ATR，是價格「可能達到」的上緣壓力參考。持有多單者可參考在此附近分批停利，不是建議買入價。真正要進場，仍應以訊號與防守線為準。</span></span> {c.get('atk_zone')} | <span class='m-tooltip' style='color:#00FF00;'>防守停損:<span class='m-tooltiptext'>MA5扣除0.5倍ATR波動緩衝，避開隨機洗盤。跌破代表短多結構破壞。</span></span> {c.get('def_line')} (緩衝 {c.get('buffer_pct')}%, <span class='m-tooltip'>ATR={float(c.get('atr_val', 0)):.2f}<span class='m-tooltiptext'>真實波動幅度，衡量近14日日均震幅。ATR越大代表洗盤越兇，停損需拉寬。</span></span>)</div>""",
        f"""<div style="font-size:12px; color:#aaa; margin-top:4px;"><span class='m-tooltip' style='color:#f1c40f;'>動態移動停利{tooltip_trail}</span>: <strong style="color:#f1c40f;">{trail_txt}</strong> ({trail_state}, 近20高 {c.get('high_20')}) | <span class='m-tooltip' style='color:#d200ff;'>布林上軌{tooltip_bb}</span>: <strong style="color:#d200ff;">{bb_txt}</strong></div>""",
        # 【V160修復】原本只有週線明確偏多/偏空且日線也同向時才顯示提示，
        # 其餘情況完全不顯示、像功能沒運作。改成固定顯示一行週線狀態。
        (f"""<div style="font-size:12px; color:#7ab8ff; margin-top:4px;">"""
         f"""📐 週線趨勢: <strong>{ {'bull':'📈 偏多','bear':'📉 偏空','neutral':'➖ 盤整','unknown':'❓ 資料不足'}.get(_weekly.get('trend','unknown'), '❓ 資料不足') }</strong>"""
         + (f""" (收盤 {_weekly.get('close')} / MA5 {_weekly.get('ma5')} / MA10 {_weekly.get('ma10')})"""
            if _weekly.get('trend') not in (None, 'unknown') else "")
         + """</div>"""),
        _fmt_zone_summary(_z2_badge, _z2_color, _z2_reason),
        """</div>""",

        f"""<div class="zone-box zone-3"><div class="shadow-box"><div class="zone-title">📊 第三戰區：三大法人、真實成本與主力籌碼</div>""",
        f"""<div style="font-size:13px; margin-bottom:4px;"><b>[外資]</b> 單日<span style="color:#f1c40f;">({display_date}{warn_icon})</span>: <strong style="color:{_f_color};">{int(c.get('f_buy', 0)):+,}張 ({float(c.get('f_pct', 0)):+.2f}%)</strong><br><span style="color:#888;">　5日</span> <strong style="color:{_f5_color};">{int(c.get('f_5d', 0)):+,}張 ({float(c.get('f_5d_pct', 0)):+.2f}%)</strong> ｜ <span style="color:#888;">10日</span> <strong style="color:{_f10_color};">{int(c.get('f_10d', 0)):+,}張 ({float(c.get('f_10d_pct', 0)):+.2f}%)</strong></div>""",
        _fmt_vwap(c, 'f_vwap', '外資連續買賣超成本', '#ff4d4d'),
        f"""<div style="font-size:13px; margin:6px 0 4px 0;"><b>[投信]</b> 單日<span style="color:#f1c40f;">({display_date}{warn_icon})</span>: <strong style="color:{_t_color};">{int(c.get('t_buy', 0)):+,}張 ({float(c.get('t_pct', 0)):+.2f}%)</strong><br><span style="color:#888;">　5日</span> <strong style="color:{_t5_color};">{int(c.get('t_5d', 0)):+,}張 ({float(c.get('t_5d_pct', 0)):+.2f}%)</strong> ｜ <span style="color:#888;">10日</span> <strong style="color:{_t10_color};">{int(c.get('t_10d', 0)):+,}張 ({float(c.get('t_10d_pct', 0)):+.2f}%)</strong></div>""",
        _fmt_vwap(c, 't_vwap', '投信連續買賣超成本', '#f1c40f'),
        (lambda _bh_ratio_result=get_latest_big_holder_ratio(c.get('code')),
                _bh_result=get_big_holder_trend(c.get('code')): (
            # 【R85修復】原本永遠顯示bh_display(FinMind付費限定，永遠
            # 「官方未公佈」)。改成優先顯示TDCC最新一週實際比例，TDCC也
            # 沒資料才退回FinMind那個。
            f"""<div style="font-size:12px; border-top:1px dashed #444; padding-top:6px; margin-top:6px; display:flex; justify-content:space-between; color:#aaa;"><span>千張大戶({_bh_ratio_result[1] or c.get('big_holder_date') or ERR_NO_DATA}): <strong style="color:#00d2ff;">"""
            + (f"""{_bh_ratio_result[0]:.2f}%""" if _bh_ratio_result[0] is not None else bh_display)
            + f"""</strong>"""
            + ({'up': """<span style="color:#ff4d4d;"> ↑趨勢集中</span>""",
                'down': """<span style="color:#00e676;"> ↓趨勢分散</span>""",
                'flat': """<span style="color:#888;"> →趨勢平穩</span>"""}.get(_bh_result[0], ""))
            # 【R75新增】連續分數——三態只講方向，這裡加上「每週平均變化幾個
            # 百分點」，同樣是up，+0.05%/週跟+0.8%/週力道差很多，三態看不出來。
            + (f"""<span style="color:#666; font-size:11px;"> ({_bh_result[2]:+.2f}%/週)</span>"""
               if _bh_result[2] is not None else "")
            + (f"""<span style="color:#555; font-size:11px;"> (累積中 {_bh_result[1]}/3週)</span>"""
               if _bh_result[0] is None and _bh_result[1] and _bh_result[1] > 0 else "")
            # 【R90新增】散戶（十張以下）比例——同一份TDCC資料原本就有，
            # 跟大戶比例並列顯示，看籌碼是往大戶集中還是散戶籌碼在增加。
            + (f"""<span style="color:#888; font-size:11px;"> ｜散戶{_bh_ratio_result[2]:.1f}%</span>"""
               if _bh_ratio_result[2] is not None else "")
            # 【R95修復】自營商/融資增減這行原本整段用固定的color:#aaa（灰色），
            # 完全沒有跟畫面上其他買賣超數字一樣做紅漲綠跌上色。這裡補上，
            # 顏色邏輯跟外資/投信那兩行一致：買超(正)紅、賣超(負)綠。
            + (lambda _d=int(c.get('d_buy', 0)), _m=int(c.get('margin_diff', 0)): (
                f"""</span><span>自營商: <strong style="color:{'#ff4d4d' if _d > 0 else ('#00e676' if _d < 0 else '#aaa')};">{_d:+,}張</strong>"""
                f""" | 融資增減: <strong style="color:{'#ff4d4d' if _m > 0 else ('#00e676' if _m < 0 else '#aaa')};">{_m:+,}張</strong>"""
                f"""{' <span style=\"color:#888; font-size:11px;\">(未同步)</span>' if not c.get('has_margin') else ''}</span></div>"""
            ))()
        ))(),
        _fmt_main_force_cost(c),
        _fmt_closing_strength(c),
        _fmt_volume_followthrough(c),
        _fmt_pullback_health(c),
        _fmt_rebound_health(c),
        _fmt_order_book_pressure(c),
        _fmt_today_liquidity(c),
        _fmt_day_trader_and_margin(c),
        _fmt_vwap_position(c),
        _fmt_zone_summary(_z3_badge, _z3_color, _z3_reason),
        """</div></div>""",

        f"""<div style="background:{c.get('signal_bg')}; padding:10px; border-radius:5px; text-align:center; margin-top:8px;"><span class='m-tooltip' style="color:{c.get('color_border')}; font-size:15px; font-weight:bold;">決策判定：{sig_t}{tooltip_sig}</span><div style="font-size:12px; color:#888; margin-top:4px;">(評分 {c.get('score')} | {' / '.join(c.get('reasons', []))})</div></div></div>"""
    ]
    return "".join(html_lines)


# ==============================================================================
# 八、 SQLite 雙軌籌碼寫入管線
# ==============================================================================
def process_mops_csv(uploaded_files):
    """
    【R98續33新增，總指揮官方向：MOPS財報自助上傳】跟process_twse_csv()
    同一套設計哲學——總指揮官用真人瀏覽器從MOPS(t163sb04)下載CSV後，
    直接拖進這裡上傳，系統自動解析+寫進mops_financial_snapshot，不用
    再透過對話一批一批貼SQL(那個方式很消耗資源，這輪總指揮官親自反映
    的問題)。

    自動判斷產業別(_detect_mops_industry)，安全處理Big5編碼，用CSV本身
    的「年度」「季別」欄位算出quarter_end_date/disclosure_date_est
    (不用總指揮官手動輸入)，批次寫進Supabase(每批200筆，避免單次
    request過大)。
    """
    if SUPABASE_CONN is None:
        st.warning("Supabase未連線，無法寫入。")
        return

    success_files, total_rows = 0, 0
    all_records = []
    file_summaries = []

    for file_bytes in uploaded_files:
        raw_bytes = file_bytes.getvalue()
        try:
            decoded_content = raw_bytes.decode('big5', errors='ignore')
        except Exception as e:
            st.warning(f"⚠️ {file_bytes.name} 編碼解析失敗，跳過：{e}")
            continue

        try:
            reader = csv.DictReader(io.StringIO(decoded_content))
            cols = reader.fieldnames or []
            detected = _detect_mops_industry(cols)
            if detected is None:
                st.warning(f"⚠️ {file_bytes.name} 辨識不出產業別(表頭：{cols[:5]}…)，跳過此檔。")
                continue
            industry_note, revenue_col, gp_col, oi_col, ni_col, eps_col = detected
            if eps_col is None:
                st.warning(f"⚠️ {file_bytes.name}（判斷為{industry_note}）找不到EPS欄位，跳過此檔。")
                continue

            file_records = []
            year_roc, season = None, None
            for row in reader:
                row = {k.strip(): v for k, v in row.items() if k}
                sym = (row.get('公司代號') or '').strip()
                if not sym or not sym[0].isdigit():
                    continue
                try:
                    year_roc = int((row.get('年度') or '0').strip())
                    season = int((row.get('季別') or '0').strip())
                except ValueError:
                    continue
                file_records.append({
                    'symbol': sym, 'year_roc': year_roc, 'season': season,
                    'revenue': safe_float(row.get(revenue_col)) if revenue_col else None,
                    'gross_profit': safe_float(row.get(gp_col)) if gp_col else None,
                    'operating_income': safe_float(row.get(oi_col)) if oi_col else None,
                    'net_income': safe_float(row.get(ni_col)) if ni_col else None,
                    'eps': safe_float(row.get(eps_col)) if eps_col else None,
                    'market': 'sii',
                })
            if not file_records or year_roc is None:
                st.warning(f"⚠️ {file_bytes.name}（{industry_note}）解析不到任何有效資料列，跳過。")
                continue

            # quarter_end_date/disclosure_date_est從CSV本身的年度/季別算出來，
            # 不用總指揮官手動輸入。
            year_ad = year_roc + 1911
            season_end_map = {1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)}
            m, d = season_end_map.get(season, (12, 31))
            quarter_end = date(year_ad, m, d)
            disclosure_est = quarter_end + timedelta(days=45)
            for rec in file_records:
                rec['quarter_end_date'] = quarter_end.isoformat()
                rec['disclosure_date_est'] = disclosure_est.isoformat()

            all_records.extend(file_records)
            file_summaries.append(f"{file_bytes.name}：判斷為{industry_note}，"
                                  f"民國{year_roc}年第{season}季，{len(file_records)}筆")
            success_files += 1
        except Exception as e:
            st.warning(f"⚠️ {file_bytes.name} 解析失敗：{e}")

    if not all_records:
        st.warning("這批檔案完全沒有解析出可用的資料，沒有寫入任何內容。")
        return

    # 依symbol/year_roc/season去重，同一批裡如果意外重複，後面的覆蓋前面的
    # (理論上不該發生，因為每個檔案是不同產業別的公司，這裡只是防禦性處理)。
    dedup = {}
    for rec in all_records:
        key = (rec['symbol'], rec['year_roc'], rec['season'])
        dedup[key] = rec
    all_records = list(dedup.values())

    BATCH = 200
    write_errors = []
    for i in range(0, len(all_records), BATCH):
        batch = all_records[i:i + BATCH]
        try:
            SUPABASE_CONN.table("mops_financial_snapshot").upsert(
                batch, on_conflict="symbol,year_roc,season").execute()
            total_rows += len(batch)
        except Exception as e:
            write_errors.append(f"第{i // BATCH + 1}批寫入失敗：{e}")

    for s in file_summaries:
        st.caption(f"✓ {s}")
    if write_errors:
        for e in write_errors:
            st.warning(f"⚠️ {e}")
    if total_rows > 0:
        # 【R98續34修復，總指揮官反映「上傳完不知道存到哪一季」】原本
        # 只顯示筆數+成功，沒有明確講是哪一年哪一季——這裡從實際寫入的
        # 資料算出真正涵蓋的季度(可能不只一季，理論上一次可以混合上傳
        # 不同季度的檔案)，用「民國115年第2季」這種人話講清楚。
        _season_names = {1: '第一季', 2: '第二季', 3: '第三季', 4: '第四季'}
        _seasons_written = sorted({(r['year_roc'], r['season']) for r in all_records})
        _seasons_str = "、".join(f"民國{yr}年{_season_names.get(sn, f'第{sn}季')}"
                                for yr, sn in _seasons_written)
        st.success(f"✅ 成功解析 {success_files} 份檔案、寫入 {total_rows:,} 筆財報資料！\n\n"
                   f"📅 這次寫入的季度：**{_seasons_str}**")
        time.sleep(2)
        st.rerun()


def process_twse_csv(uploaded_files):
    success_files, total_rows = 0, 0
    for file_bytes in uploaded_files:
        raw_bytes = file_bytes.getvalue()
        try:
            decoded_content = raw_bytes.decode('big5', errors='ignore')
        except Exception:
            continue
        try:
            first_line = decoded_content.split('\n')[0]
            date_match = re.search(r'(\d+)年(\d+)月(\d+)日', first_line)
            file_date = (f"{int(date_match.group(1)) + 1911}-{date_match.group(2).zfill(2)}-{date_match.group(3).zfill(2)}"
                         if date_match else get_last_trading_date())

            df = pd.read_csv(io.StringIO(decoded_content), skiprows=1, thousands=',')
            cols = list(df.columns)

            # 【修復】原本 d_col 用 ('自營商','自行買賣') 比對，會先命中「自營商買進股數(自行買賣)」而非買賣超欄
            code_col = _pick_col(cols, ['代號'])
            f_col = _pick_col(cols, ['外陸資', '買賣超']) or _pick_col(cols, ['外資', '買賣超'], ['自營'])
            t_col = _pick_col(cols, ['投信', '買賣超'])
            d_col = _pick_col(cols, ['自營商', '買賣超'], ['自行買賣', '避險']) or _pick_col(cols, ['自營商', '買賣超'])

            if not code_col or not f_col:
                st.warning(f"⚠️ 欄位辨識失敗，跳過此檔（可辨識欄位：{cols[:6]}…）")
                continue

            batch_args = []
            for _, row in df.iterrows():
                code = str(row[code_col]).strip()
                if len(code) == 4 and code.isdigit():
                    # safe_float 已修復負號，賣超才不會被誤記成買超
                    f_buy = int(safe_float(row[f_col]) / 1000)
                    t_buy = int(safe_float(row[t_col]) / 1000) if t_col else 0
                    d_buy = int(safe_float(row[d_col]) / 1000) if d_col else 0
                    batch_args.append((file_date, code, f_buy, t_buy, d_buy))

            with DB_LOCK:
                # 【R95修復】margin原本硬寫0.0，這批T86資料沒有融資欄位，
                # 第一次INSERT會誤種成0.0(不是NULL)，讓has_margin誤判成
                # 「已同步、剛好是0」。改成NULL。
                SQLITE_CONN.executemany('''
                    INSERT INTO inst_holding (date, symbol, foreign_buy, trust_buy, dealer_buy, margin, big_holder, big_holder_date)
                    VALUES (?, ?, ?, ?, ?, NULL, 0.0, '')
                    ON CONFLICT(date, symbol) DO UPDATE SET
                        foreign_buy=excluded.foreign_buy,
                        trust_buy=excluded.trust_buy,
                        dealer_buy=excluded.dealer_buy;
                ''', batch_args)
                SQLITE_CONN.commit()
            # 【V160 雙寫】同一批資料寫進 Supabase（盡力而為，失敗不影響本機）
            sb_upsert_inst_holding([
                {"date": a[0], "symbol": a[1], "foreign_buy": a[2], "trust_buy": a[3], "dealer_buy": a[4]}
                for a in batch_args
            ])
            success_files += 1
            total_rows += len(batch_args)
        except Exception as e:
            st.warning(f"⚠️ 解析失敗：{e}")

    if success_files > 0:
        st.success(f"✅ 成功強填 {success_files} 份日報、共 {total_rows:,} 檔籌碼至大腦！")
        time.sleep(1)
        st.rerun()


def sync_single_stock_finmind(code, progress_cb=None):
    """
    【R95新增progress_cb】總指揮官多次反映「單檔同步/深度財報等按鈕按下去只有
    一顆小人在跑，看不出進度，超過5分鐘還在跑會以為當機了」。這裡比照
    sync_from_supabase_on_boot()已經在用的progress_cb(pct, label)寫法
    （同一套介面，呼叫端不用學新東西），在四個子查詢（籌碼/融資/大戶/營收）
    各自完成時回報一次百分比，UI端就能畫真正的0~100%進度條，不再只是乾等。
    沒傳progress_cb時完全不影響原本行為。
    """
    def _report(pct, label):
        if progress_cb:
            try:
                progress_cb(pct, label)
            except Exception:
                pass
    try:
        _report(0.05, "準備同步")
        target_date = get_last_trading_date()
        token = get_active_fm_token()
        url = 'https://api.finmindtrade.com/api/v4/data'

        inst_success, inst_err_reason = False, None
        base_payload = {'foreign': 0, 'trust': 0, 'dealer': 0}
        inst_hist_rows = []   # 【V160】這檔近40天的法人歷史，供 5日/10日 加總用

        try:
            # 【V160關鍵修復】原本start_date只帶單一天，上櫃股外資單日/
            # 5日/10日數字永遠一樣。改成帶start_date不帶end_date，往前推
            # 40天跟只抓一天是同一次API呼叫，額度成本相同。
            _hist_start = (datetime.strptime(target_date, '%Y-%m-%d')
                           - timedelta(days=40)).strftime('%Y-%m-%d')
            params = {'dataset': 'TaiwanStockInstitutionalInvestorsBuySell',
                      'data_id': code, 'start_date': _hist_start}
            if token:
                params['token'] = token
            payload = _finmind_get(url, params)
            df = pd.DataFrame(payload.get('data', []))
            df['net'] = (pd.to_numeric(df['buy'], errors='coerce').fillna(0)
                         - pd.to_numeric(df['sell'], errors='coerce').fillna(0))
            piv = df.pivot_table(index='date', columns='name', values='net', aggfunc='sum')
            piv = piv.sort_index()

            # 【V160】把整段歷史逐日收集起來，稍後跟單日結果一起批次寫入資料庫，
            # 這樣 5日/10日 才有多列可以加總。最後一列（最新交易日）仍回填
            # base_payload 供畫面即時顯示。
            for _d in piv.index:
                _row = piv.loc[_d]
                inst_hist_rows.append((
                    str(_d), code,
                    int(_row['Foreign_Investor'] / 1000) if 'Foreign_Investor' in piv.columns else 0,
                    int(_row['Investment_Trust'] / 1000) if 'Investment_Trust' in piv.columns else 0,
                    int(_row['Dealer'] / 1000) if 'Dealer' in piv.columns else 0,
                ))

            if 'Foreign_Investor' in piv.columns:
                base_payload['foreign'] = int(piv['Foreign_Investor'].iloc[-1] / 1000)
            if 'Investment_Trust' in piv.columns:
                base_payload['trust'] = int(piv['Investment_Trust'].iloc[-1] / 1000)
            if 'Dealer' in piv.columns:
                base_payload['dealer'] = int(piv['Dealer'].iloc[-1] / 1000)
            inst_success = True
        except FinMindAPIError as e:
            inst_err_reason = e.reason
        _report(0.30, "籌碼查詢完成，查詢融資中")

        margin_val = fetch_margin_diff(code, token, target_date)
        _report(0.50, "融資查詢完成，查詢千張大戶中")

        bh_result = fetch_big_holder_with_recursion(code, token, target_date)
        bh_success = False
        if bh_result and bh_result.get('error') is None:
            bh_success = safe_upsert_big_holder(code, bh_result['big_holder_date'], bh_result['big_holder'])
        _report(0.70, "大戶查詢完成，寫入資料庫中")

        if inst_success:
            with DB_LOCK:
                SQLITE_CONN.execute('''
                    INSERT INTO inst_holding (date, symbol, foreign_buy, trust_buy, dealer_buy, margin, big_holder, big_holder_date)
                    VALUES (?, ?, ?, ?, ?, ?, 0.0, '')
                    ON CONFLICT(date, symbol) DO UPDATE SET
                        foreign_buy=excluded.foreign_buy,
                        trust_buy=excluded.trust_buy,
                        dealer_buy=excluded.dealer_buy,
                        margin=CASE WHEN excluded.margin IS NOT NULL THEN excluded.margin ELSE inst_holding.margin END;
                ''', (target_date, code, base_payload['foreign'], base_payload['trust'],
                      base_payload['dealer'],
                      # 【R95修復】原本float(margin_val or 0.0)會把「抓取
                      # 失敗(None)」跟「真的抓到、剛好是0」存成同一個0.0。
                      # 現在保留None讓SQLite存NULL，CASE WHEN改看「有沒有值」。
                      (float(margin_val) if margin_val is not None else None)))

                # 【V160 修復】連同近40天歷史一起寫入，否則資料庫只有一列，
                # 5日/10日 加總會等於單日（總指揮官在 6488 上發現的症狀）。
                # margin 不覆寫（歷史融資另有來源），故這裡固定帶 0 並保留原值。
                if inst_hist_rows:
                    # 【R95修復】同上——這批40天歷史沒有融資資料，第一次INSERT
                    # 的margin改存NULL，不要硬寫0.0偽裝成「已確認是0」。
                    SQLITE_CONN.executemany('''
                        INSERT INTO inst_holding (date, symbol, foreign_buy, trust_buy, dealer_buy, margin, big_holder, big_holder_date)
                        VALUES (?, ?, ?, ?, ?, NULL, 0.0, '')
                        ON CONFLICT(date, symbol) DO UPDATE SET
                            foreign_buy=excluded.foreign_buy,
                            trust_buy=excluded.trust_buy,
                            dealer_buy=excluded.dealer_buy;
                    ''', inst_hist_rows)
                SQLITE_CONN.commit()
            # 【V160 雙寫】歷史批次也推上雲端，換裝置/重新部署後才不會又只剩一天
            if inst_hist_rows:
                sb_upsert_inst_holding([
                    {"date": r[0], "symbol": r[1], "foreign_buy": r[2],
                     "trust_buy": r[3], "dealer_buy": r[4]}
                    for r in inst_hist_rows
                ])
            # 【V160 雙寫】單檔同步結果同步進 Supabase
            # 【R95修復】同樣保留None，不要把「抓取失敗」偽裝成「真的是0」。
            sb_upsert_inst_holding([{
                "date": target_date, "symbol": code,
                "foreign_buy": base_payload['foreign'], "trust_buy": base_payload['trust'],
                "dealer_buy": base_payload['dealer'],
                "margin": float(margin_val) if margin_val is not None else None
            }])

        # 【V160關鍵修復】「單檔精準同步」按鈕從頭到尾沒呼叫過營收抓取
        # 函式，只同步了籌碼+融資+大戶三項。現在讓按鈕真的也查一次月營收。
        _report(0.80, "查詢月營收中")
        rev_success = False
        try:
            rev_cache_key = f"revenue:{code}:{token}"
            _rev_cache = _get_smart_cache_store()
            _rev_cache.pop(rev_cache_key, None)   # 強制這次重查，不用舊快取（含舊失敗）
            rev_data = fetch_finmind_revenue(code, token)
            rev_success = bool(rev_data and rev_data.get('ok'))
        except Exception:
            rev_success = False
        _report(1.0, "同步完成")

        # 【R95修復】原本parts寫死列出「籌碼」不管實際成不成功，判斷失敗
        # 原因的邏輯放在return之後變死碼。改成parts只列真正成功的項目。
        error_map = {'rate_limited': ERR_RATE_LIMIT, 'timeout': "⏱️ 連線逾時",
                     'connection_error': ERR_CONN, 'empty_data': ERR_NO_DATA}
        parts = []
        if inst_success:
            parts.append("籌碼")
        if margin_val is not None:
            parts.append("融資")
        if bh_success:
            parts.append("大戶")
        if rev_success:
            parts.append("營收")

        any_success = bool(parts)
        msg = f"同步完成 ({'+'.join(parts)})" if parts else "同步失敗"
        if not inst_success:
            msg += f"，❌籌碼失敗({error_map.get(inst_err_reason, inst_err_reason or '未知原因')})"
        if not bh_success:
            msg += "，⏳大戶無資料"
        if not rev_success:
            msg += "，⏳營收無資料"
        return any_success, msg
    except Exception as e:
        return False, f"連線異常 ({e})"


# ==============================================================================
# 九、 NVIDIA NIM 引擎
# ==============================================================================
# 【V160】模型catalog會變動，改成「自動探索」：優先呼叫/v1/models端點抓
# 當前可用模型清單，抓失敗才退回靜態候選清單。
# 【R97】NIM_FALLBACK_MODELS本體搬進warroom_core.py共用（排程端也要用同一份
# 候選清單，見開發歷程.md），這裡不再重複定義，上面import區已經拉進來。
# 偏好順序關鍵字：抓到 catalog 後，優先挑名字含這些關鍵字的聊天模型
NIM_PREFERRED_KEYWORDS = ["deepseek", "llama-3.3", "glm", "kimi", "qwen", "nemotron", "mistral"]


def get_nim_models():
    """
    取得當前要用的模型清單（自動探索優先）。
    【V160 新功能】如果使用者在側邊欄手動選過偏好模型，把它排到最前面優先嘗試，
    其餘自動偵測到的模型仍保留在後面當備援——選的那個萬一剛好失效，不會整個掛掉，
    會自動退回下一個可用模型。
    """
    models = discover_nim_models()
    try:
        preferred_short = st.session_state.get('preferred_nim_model')
    except Exception:
        preferred_short = None
    if preferred_short:
        matched = [m for m in models if m.split('/')[-1] == preferred_short]
        if matched:
            rest = [m for m in models if m not in matched]
            return matched + rest
    return models


NIM_MODELS = NIM_FALLBACK_MODELS   # 相容舊引用；實際呼叫改用 get_nim_models()


def analyze_intel_article(content, candidate_codes):
    """
    ⚠️【Round30 起未從UI呼叫 — 總指揮官實測後回報「完全抓不到重點」，品質不合用】⚠️

    保留這段程式碼的原因：總指揮官改用「自己另外找AI工具做摘要，再把摘要貼進
    戰報內容」這個更可靠的工作流程，所以UI上拿掉了呼叫這個函式的按鈕，但邏輯本身
    （NIM呼叫鏈、候選代號防幻覺過濾、JSON解析防禦）沒有問題，之後如果想換一個
    更強的模型或改寫prompt再試一次，直接復用這段就好，不用重寫。

    用 AI 分析貼上的情報文章：生成重點摘要 + 從候選代號中判斷哪些是文章「真正在
    討論」的標的。

    關鍵設計：candidate_codes 是既有 regex/股名比對抓出來的候選清單（已經
    限縮在 TW_STOCK_NAMES 的合法代號範圍內），AI 只能從這個清單裡「篩選」，
    不能自己新增清單外的代號——就算 AI 誤判或幻覺，最壞情況也只是漏勾一檔
    真正相關的（使用者仍可在多選框手動加回），不會無中生有出不存在的代號。
    這比讓 AI 自由抓代號安全得多。

    回傳 dict: {ok, error, summary, relevant_codes, reasons, model}
    """
    if not NVIDIA_API_KEY:
        return {'ok': False, 'error': '未配置 NVIDIA API 金鑰', 'summary': None,
                'relevant_codes': [], 'reasons': {}}
    if not candidate_codes:
        return {'ok': False, 'error': '沒有候選代號可供AI判斷（字串比對階段就沒抓到任何候選）',
                'summary': None, 'relevant_codes': [], 'reasons': {}}

    client = OpenAI(base_url="https://integrate.api.nvidia.com/v1", api_key=NVIDIA_API_KEY)
    cand_str = "、".join(f"{c}({TW_STOCK_NAMES.get(c, '')})" for c in candidate_codes)
    prompt = (
        f"以下是一篇財經/股票報告的原文，以及系統用字串比對抓出的候選標的清單"
        f"（候選清單可能包含誤判，例如公司名稱剛好跟一般詞彙撞名，例如用「海灣」"
        f"形容線型走勢，不代表在講海灣這檔股票）。\n\n"
        f"候選標的：{cand_str}\n\n"
        f"報告原文：\n{content[:4000]}\n\n"
        f"請完成兩件事：\n"
        f"1. 用100字內的繁體中文摘要這篇報告的重點\n"
        f"2. 從候選標的清單中，判斷哪些是文章「真正在討論」的投資標的。"
        f"只能從候選清單裡挑，不能自己新增清單外的代號，不確定的寧可不選。\n\n"
        f"請務必只輸出以下JSON格式，不要有其他文字、不要markdown code block：\n"
        f'{{"summary": "摘要內容", "relevant": [{{"code": "代號", "reason": "為什麼相關(15字內)"}}]}}'
    )
    errors = []
    for model_id in get_nim_models():
        try:
            completion = client.chat.completions.create(
                model=model_id,
                messages=[{"role": "system",
                          "content": "你是專業的財經文本分析助手。嚴格只輸出JSON，不要markdown code block，不要任何額外說明文字。"},
                          {"role": "user", "content": prompt}],
                temperature=0.1, max_tokens=800, timeout=60
            )
            raw = completion.choices[0].message.content.strip()
            raw = re.sub(r'^```(?:json)?\s*|\s*```$', '', raw)   # 防禦：部分模型仍會包code block
            parsed = json.loads(raw)
            relevant = parsed.get('relevant', [])
            rel_codes = [r['code'] for r in relevant if isinstance(r, dict) and r.get('code') in candidate_codes]
            reasons = {r['code']: r.get('reason', '') for r in relevant
                      if isinstance(r, dict) and r.get('code') in candidate_codes}
            return {'ok': True, 'error': None, 'summary': parsed.get('summary', ''),
                    'relevant_codes': rel_codes, 'reasons': reasons, 'model': model_id.split('/')[-1]}
        except Exception as e:
            emsg = str(e).lower()
            short = model_id.split('/')[-1]
            if '404' in emsg or 'not found' in emsg:
                errors.append(f"{short}: 模型不存在")
            elif '429' in emsg or 'rate' in emsg:
                errors.append(f"{short}: 限流/額度不足")
            elif 'timeout' in emsg:
                errors.append(f"{short}: 連線逾時")
            else:
                errors.append(f"{short}: 解析失敗或例外")
            continue
    return {'ok': False, 'error': "；".join(errors), 'summary': None, 'relevant_codes': [], 'reasons': {}}


def execute_single_stock_ai(c, direction='long'):
    """
    【R97重大修復+擴充，見開發歷程.md「NVIDIA AI推演重新設計」章節】

    第一輪修復：把「依序嘗試5個模型」改成「平行送出、哪個先回來就用哪個」
    ——邏輯本體已經搬進warroom_core.py的call_ai_models_parallel()共用，
    這裡只是薄包裝，準備好本地的NVIDIA_API_KEY/模型清單再呼叫。

    這輪擴充：①prompt現在會帶入系統A評分(score/signal_text/reasons)跟
    5分K三關結果(c.get('intraday_gate'))，不再只給AI看戰卡表面數字——
    prompt組裝邏輯也搬進core.py的build_ai_strategy_prompt()共用，跟
    排程端(system_scheduler.py)產生的AI推演用同一套組裝規則，不會兩邊
    寫法不一致。②新增direction參數，空方候選呼叫時傳'short'，prompt
    會自動改用空方語氣、額外要求AI評估軋空/反彈風險。
    """
    system_prompt, user_prompt = build_ai_strategy_prompt(
        c, direction=direction, gate_result=c.get('intraday_gate'))
    ok, result = call_ai_models_parallel(
        system_prompt, user_prompt, NVIDIA_API_KEY, models=get_nim_models(), timeout=30)
    if ok:
        return result
    return (f"⚠️ NVIDIA {result}\n\n"
            f"若全是「模型不存在」，代表 NVIDIA NIM 上的模型ID已更新，需更換 NIM_MODELS 清單。"
            f"若全是「連線逾時」，代表 Streamlit Cloud 到 NVIDIA NIM 的連線本身有問題，"
            f"不是單一模型的問題，建議直接查NVIDIA NIM服務狀態。")



# ==============================================================================
# 九之二、命中率回測引擎 (V158新增，V159擴充查1~查12完整濾網回測)
# ------------------------------------------------------------------------------
# 核心「無未來函數」骨架：用第i天收盤產生訊號，量測第i+3/i+10天未來報酬。
# 詳細範圍/簡化項目見開發歷程.md。evaluate_single_condition等已搬進
# warroom_core.py，這裡直接沿用import。
# ==============================================================================


# ==============================================================================
# 九之三、查1~查12 完整濾網回測（V159新增，R86補上查3）
# ------------------------------------------------------------------------------
# 完整回測：查1,2,3,4,5,6,8,9,10,12。簡化版：查11(殖利率)用現在股利資料
# 回推歷史。不支援：情報雷達/黃金交叉(無歷史時間戳)。
# ==============================================================================
# 【R95】回測引擎四個函式已搬進warroom_core.py，這裡直接沿用import，
# DIVIDEND_DB/token改成呼叫端傳入。
# ==============================================================================
# 九之四、盤中異常偵測 (V159新增，陽春版：僅網頁內顯示)
# ------------------------------------------------------------------------------
# 靠開著分頁自動重新整理偵測，比較「這次」跟「上一次」快照，只抓新突破
# 門檻的股票。
# ==============================================================================
def notify_telegram_web(text):
    """
    【R67新增】網頁版的Telegram推播——排程端(system_scheduler.py)早就有推播，
    但網頁版的盤中異常偵測一直只顯示在畫面上的banner，人不在電腦前就等於沒有。
    總指揮官這輪要求補上Telegram推播（Line維持不做）。

    讀跟排程端同一組secrets（TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID），
    支援st.secrets的兩種常見放法：頂層或放在radar_secrets底下。
    沒設定或送失敗都靜默回False，絕不讓推播失敗影響畫面顯示——
    推播是加分項，不是主要功能。
    """
    try:
        # 【R98續82修復，總指揮官反映「Telegram推播未送出：可能是沒設定
        # TELEGRAM_BOT_TOKEN」】原本只支援「頂層或radar_secrets底下」
        # 兩種放法，如果總指揮官把這兩個金鑰放進其他分類區塊(例如跟
        # SUPABASE放一起)，會讀不到——跟GITHUB_TOKEN先前遇到的同一種
        # TOML區塊陷阱(R84)。改用同一套_find_secret_anywhere()，不管
        # 放在哪個區塊底下都找得到，不用要求總指揮官對TOML格式有正確
        # 理解才能設定成功。
        _tok = _find_secret_anywhere("TELEGRAM_BOT_TOKEN")
        _chat = _find_secret_anywhere("TELEGRAM_CHAT_ID")
        if not _tok or not _chat:
            return False
        _r = _SESSION.post(f"https://api.telegram.org/bot{_tok}/sendMessage",
                           json={"chat_id": _chat, "text": text}, timeout=8)
        return _r.status_code == 200
    except Exception:
        return False


def detect_intraday_anomalies(current_cards):
    prev = st.session_state.get('anomaly_snapshot', {})
    alerts = []
    new_snapshot = {}
    for c in current_cards:
        code = c.get('code', '')
        if not code:
            continue
        vr = float(c.get('vol_ratio', 0) or 0)
        gain = float(c.get('gain', 0) or 0)
        p = prev.get(code, {})
        prev_vr = float(p.get('vol_ratio', 0) or 0)
        prev_gain = float(p.get('gain', 0) or 0)

        if vr >= 2.0 and prev_vr < 2.0:
            alerts.append(f"🔥 {c.get('name')}({code}) 爆量比剛突破 2.0x（現在 {vr:.1f}x）")
        if gain >= 5.0 and prev_gain < 5.0:
            alerts.append(f"🚀 {c.get('name')}({code}) 漲幅剛突破 +5%（現在 {gain:+.2f}%）")
        if gain <= -5.0 and prev_gain > -5.0:
            alerts.append(f"📉 {c.get('name')}({code}) 跌幅剛突破 -5%（現在 {gain:+.2f}%）")

        new_snapshot[code] = {'vol_ratio': vr, 'gain': gain}

    st.session_state['anomaly_snapshot'] = new_snapshot
    st.session_state.setdefault('anomaly_log', [])
    if alerts:
        # 【R96修復，見開發歷程.md時區bug章節】異常偵測時間戳記原本用
        # datetime.now()沒指定時區，改成一次搜尋整支檔案所有含時分的
        # datetime.now()呼叫，全部一次修好。
        ts = datetime.now(TAIPEI_TZ).strftime('%H:%M:%S')
        for a in alerts:
            st.session_state['anomaly_log'].insert(0, f"[{ts}] {a}")
        st.session_state['anomaly_log'] = st.session_state['anomaly_log'][:30]   # 只留最近30則
    return alerts


# ==============================================================================
# 十、 CSS 與 UI 側邊欄
# ==============================================================================
st.markdown("""<style>
div[data-testid="stSidebar"] { background-color: #12141a !important; border-right: 1px solid #333 !important; }
div[data-testid="stButton"] > button { background-color: #1e1e24 !important; border: 1px solid #444 !important; }
div[data-testid="stButton"] > button p { color: #00d2ff !important; font-weight: bold !important; font-size: 14px !important; }
.hud-box { background: linear-gradient(135deg, #1a1c23 0%, #0d1117 100%); border-radius: 10px; padding: 15px; border-left: 5px solid #ff4d4d; margin-bottom: 20px;}
.zone-box { background: #11141c; border: 1px solid #2c3e50; border-left: 4px solid #2c3e50; border-radius: 6px; padding: 12px 12px 12px 14px; margin-bottom: 12px; color:#eeeeee;}
.zone-1 { border-left-color: #e84393; }
.zone-2 { border-left-color: #00d2ff; }
.zone-3 { border-left-color: #f1c40f; }
.zone-title { color: #00d2ff; font-weight: bold; font-size: 13px; margin-bottom: 8px; border-bottom: 1px solid #2c3e50; padding-bottom: 5px; }
.k-tag { font-size:13px; background:#2c3e50; padding:3px 8px; border-radius:5px; color:#f1c40f; white-space: nowrap; display: inline-block; margin-left:8px; }
.data-chip { display:inline-block; background:#1a2030; border:1px solid #2c3e50; border-radius:4px; padding:2px 7px; margin:2px 3px 2px 0; font-size:12px; }
/* V157 修復：原本 left:50%+translateX(-50%) 置中展開，觸發文字靠近卡片左緣時
   tooltip 左半部會直接衝出邊界被裁切。改為左錨定（貼齊觸發文字左緣向右展開）
   並用 min(...) 限制最大寬度不超過視窗可視範圍，同時保留自動換行避免溢出。 */
.m-tooltip { position: relative; display: inline-block; border-bottom: 1px dotted #888; cursor: help; }
.m-tooltip .m-tooltiptext { visibility: hidden; width: max-content; max-width: min(220px, 78vw); background-color: #333; color: #fff; text-align: left; border-radius: 6px; padding: 10px; position: absolute; z-index: 999; bottom: 125%; left: 50%; transform: translateX(-50%); opacity: 0; transition: opacity 0.3s; font-size: 12px; font-weight: normal; line-height:1.6; overflow-wrap: break-word; word-break: break-word;}
.m-tooltip:hover .m-tooltiptext { visibility: visible; opacity: 1; }
/* 【R96新增】往下展開的說明框變體，只給卡片最頂端的徽章用（例如趨勢
   三態徽章）——共用的.m-tooltip往上展開(bottom:125%)，位在卡片最頂端
   的徽章上方常常沒有足夠空間，說明文字會被螢幕邊界切掉看不全。這裡
   新增一個往下展開的版本，不動到.m-tooltip本身，避免影響其他已經正常
   運作、有足夠上方空間的既有說明框。 */
.m-tooltip-down .m-tooltiptext { top: 125%; bottom: auto; }
/* 【R98新增，總指揮官反映「每一個股名跟下一個股名的間格太長，一個頁面
   看不到幾檔」】st.divider()預設上下margin偏大(約1rem)，波段候選/主力
   偵測這類逐檔清單每一列都用一次st.divider()當分隔線，一長串下來
   把整個清單拉得很長。這裡直接把<hr>(st.divider()渲染出來的元素)的
   上下margin壓小，不影響分隔線本身的視覺功能(還是有一條線分隔每一
   檔)，只是間距變窄，同一個畫面能看到更多檔股票。用!important蓋過
   Streamlit內建的margin設定。 */
hr { margin: 6px 0 !important; }
/* 同樣道理，st.columns()產生的橫向區塊(例如股名+加入雷達按鈕那一列)
   跟下一個元素之間也有偏大的預設margin-bottom，一併壓小。 */
div[data-testid="stHorizontalBlock"] { margin-bottom: 2px !important; }
</style>""", unsafe_allow_html=True)

# 【V160 第二階段】登入牆：未通過驗證前，擋住後續所有 UI（側邊欄、主畫面）
require_login()

with st.sidebar:
    st.markdown("<h2 style='color:#f1c40f; text-align:center;'>⚙️ 戰略控制台</h2>", unsafe_allow_html=True)

    # 【R97續22新增，方案三版面重構：側邊欄導航+主區單頁聚焦】
    # 總指揮官反映35個功能面板全部平鋪、盤中核心跟維運工具混在一起、
    # 排序太雜。查證後發現「維運工具」(補跑分點/清理殘留/健康監控/
    # GITHUB診斷/門檻調整/備份還原/說明書等)本來就已經在這個側邊欄裡，
    # 真正雜亂的是主畫面那35個panel全部無分組平鋪，捲動距離很長，
    # 核心的戰情速覽甚至被排在最後面。
    #
    # 修法：主畫面依內容性質分成三大類，這裡放一個選擇器決定主畫面
    # 現在只渲染哪一類——不只是視覺上分組，被跳過的分類「連程式碼都
    # 不會執行」(下面每個panel區塊外層都包了`if nav_section == "X":`)，
    # 這代表沒被選到的分類，裡面的DB查詢/網路請求/運算全部不會發生，
    # 順帶大幅減少單次頁面重繪的負擔——這是「業界共識裡漸進式揭露」的
    # 標準做法，不是單純的視覺整理。
    #
    # 【零風險原則】完全沒有搬動任何panel的物理程式碼位置——只在每個
    # 區塊外面包一層if、整體多縮排一級，區塊之間原本的執行順序、變數
    # 定義先後關係(例如config_payload要先定義才能被後面panel使用)完全
    # 不變。這樣做的代價是犧牲一點「畫面上panel按分類排列整齊」的效果
    # (同一分類裡的panel物理順序還是照原本檔案順序，不是重新排序過的)，
    # 換到的是「零風險」——任何原本存在的隱性前後依賴都不會被破壞。
    st.markdown("---")
    nav_section = st.radio(
        "📍 主畫面顯示",
        ["🔴 盤中作戰", "📊 策略回測", "📖 情報覆盤"],
        key="main_nav_section",
        help="只渲染選中的分類，其他分類的查詢/運算完全不執行，"
             "切換分類不會影響雷達/持倉/設定等已儲存的資料。")
    nav_section = nav_section[2:]   # 去掉前面的emoji，跟下面各區塊的if判斷字串一致
    st.markdown("---")

    # 【R96架構調整，見開發歷程.md】拿掉全域「波段/當沖模式」切換，改用
    # attach_live_quotes()的fetch_intraday_extras參數在各呼叫端明確控制。

    if st.button("🔄 強制重整畫面", use_container_width=True):
        st.session_state.last_refresh = time.time()
        st.rerun()

    # 【V160 新增】建置版本標記：確認雲端跑的到底是不是最新檔
    # 【R55修復】總指揮官反映側欄只要看版本號就好，不需要每次都攤開一大段
    # 說明——BUILD_NOTES改放進收合的expander，預設不顯示，需要回顧細節時自己展開。
    # 【R96再簡化】總指揮官進一步確認：連收合的展開區塊都不需要了，顯示
    # 版本號就足夠——BUILD_NOTES這個詳細版本歷程改成只留在程式碼變數裡
    # (供之後查閱用)，介面上不再顯示，UI更精簡。
    st.caption(f"🏷️ 建置版本：{BUILD_VERSION}")

    # 【V160 新增】登出按鈕（總指揮官回報找不到登出功能）
    if st.button("🚪 登出", use_container_width=True):
        st.session_state['authenticated'] = False
        st.rerun()

    # 【R59新增】雲端還原狀態——把「這次登入有沒有從雲端讀回持倉/雷達」
    # 直接攤出來，並補一顆真正呼叫hydrate_state_from_cloud()的重試按鈕
    # (原本「強制重整畫面」只有st.rerun()，沒有真的重新讀取)。
    if not SUPABASE_ENABLED:
        st.caption("☁️ 雲端還原：⚠️ 未連線（Supabase沒接上，清單只存在本機，"
                  "容器重啟/重新部署會清空）")
    elif st.session_state.get('cloud_hydrated'):
        st.caption("☁️ 雲端還原：✅ 成功")
    else:
        st.caption("☁️ 雲端還原：⚠️ 失敗或雲端目前沒有存過資料")
    if st.button("☁️ 重新從雲端還原持倉/雷達/觀察清單", use_container_width=True):
        _hydrated = hydrate_state_from_cloud()
        st.session_state['cloud_hydrated'] = _hydrated
        if _hydrated:
            st.success("✅ 已從雲端重新讀回")
        else:
            st.warning("這次還是沒讀到——確認Supabase有連線、且雲端user_state表"
                      "確實存過資料（例如換了新的Supabase專案，雲端本來就是空的）。")
        time.sleep(1)
        st.rerun()

    # 【R91新增】門檻校準、自動選股排程不用每次進GitHub手動觸發，重用
    # R81的trigger_github_workflow，各加一顆專屬按鈕。
    _col_r91a, _col_r91b = st.columns(2)
    if _col_r91a.button("🎯 立即跑門檻校準掃描", use_container_width=True,
                        help="觸發GitHub Actions的threshold_calibration階段，不用等每月1號"):
        with st.spinner("正在觸發GitHub Actions..."):
            _ok, _msg = trigger_github_workflow("threshold_calibration")
            if _ok:
                st.success(f"✅ {_msg}")
            else:
                st.warning(f"⚠️ {_msg}")
    if _col_r91b.button("📈 立即跑自動選股", use_container_width=True,
                        help="觸發GitHub Actions的signal階段，不用等每天22:00"):
        with st.spinner("正在觸發GitHub Actions..."):
            _ok, _msg = trigger_github_workflow("signal")
            if _ok:
                st.success(f"✅ {_msg}")
            else:
                st.warning(f"⚠️ {_msg}")

    # 【R95續11/22】券商分點：GitHub Actions連不上HiStock，改成只抓持倉+
    # 雷達清單(遠低於限流門檻)，放棄全市場。斷點續傳沿用Supabase進度真相。
    st.markdown("<span class='m-tooltip' style='font-size:12px; color:#888;'>"
               "ⓘ 只會抓持倉+雷達清單，不是全市場"
               "<span class='m-tooltiptext'>只抓你關心的：持倉+雷達清單，不是全市場，"
               "避免燒光免費額度。</span></span>", unsafe_allow_html=True)
    with st.expander("📊 補跑今日券商分點", expanded=False):
        st.caption("只抓你的持倉+雷達清單（通常幾十檔，遠低於HiStock的限流門檻，"
                   "可一次抓完、每天穩定更新）。已實測全市場1078檔會一直撞限流"
                   "（連續抓~35檔就開始失敗），所以改成只抓真正會看分點的那幾檔。"
                   "**分頁必須保持開啟**——關掉或斷線會中斷，但已抓到的不會作廢，"
                   "下次點擊只補「今天還缺的」，不會從頭重來。")
        # 見前一輪(續13)的教訓：expander內的網路/DB查詢一律用按鈕觸發+
        # session_state快取包住，不能一進到這段程式碼就無條件執行。
        if st.button("🔍 查詢目前進度（今天已抓幾檔）", key="bf_check_progress_btn"):
            with st.spinner("查詢股票池與目前進度中..."):
                # 【R95續22】改方向二：股票池從get_scan_pool_ordered()(全市場)
                # 換成「持倉+雷達清單」——這兩個清單就是使用者真正關心的股票，
                # 數量遠低於限流門檻。用set去重、排序後當這次的抓取範圍。
                _bf_pool = sorted(set(list(st.session_state.get('portfolio', {}).keys())
                                      + list(st.session_state.get('pinned_stocks', {}).keys())))
                _bf_done, _bf_remaining = get_todays_broker_flow_progress(_bf_pool)
                st.session_state['bf_pool'] = _bf_pool
                st.session_state['bf_remaining'] = _bf_remaining
                st.session_state['bf_done_count'] = len(_bf_done)

        if 'bf_remaining' in st.session_state and not st.session_state.get('bf_pool'):
            st.warning("你的持倉跟雷達清單目前都是空的，沒有需要抓分點的股票。"
                      "先把關心的股票加進雷達或持倉，再回來補分點。")
        elif 'bf_remaining' in st.session_state:
            _bf_pool = st.session_state['bf_pool']
            _bf_remaining = st.session_state['bf_remaining']
            st.info(f"你的持倉+雷達清單共 {len(_bf_pool)} 檔，今天已抓 {st.session_state['bf_done_count']} 檔、"
                   f"還缺 {len(_bf_remaining)} 檔。")
            if _bf_remaining:
                # 【R95續22】清單通常只有幾十檔可能少於50，slider的min(50)
                # 會大於max報錯。<=50時不給slider直接抓完，多於50才給slider。
                if len(_bf_remaining) <= 50:
                    _bf_batch_size = len(_bf_remaining)
                    st.caption(f"剩餘 {len(_bf_remaining)} 檔不多，這次會一次抓完。")
                else:
                    _bf_batch_size = st.slider("這次最多抓幾檔（抓完可以再點一次繼續抓剩下的）",
                                               50, len(_bf_remaining),
                                               min(150, len(_bf_remaining)), 10, key="bf_batch_size")
                if st.button(f"🚀 開始補跑（最多 {_bf_batch_size} 檔）", key="bf_run_btn",
                            use_container_width=True):
                    _bf_prog = st.progress(0.0, text="準備開始...")

                    def _bf_cb(done, total, code):
                        _pct = done / total if total else 0
                        _bf_prog.progress(min(1.0, _pct), text=f"補跑券商分點中 {done}/{total}（{code}）")

                    _bf_result = sync_broker_flows_batch(_bf_remaining, max_symbols=_bf_batch_size,
                                                         progress_cb=_bf_cb)
                    _bf_prog.empty()
                    # 這批跑完後進度真的變了，清掉快取的查詢結果，逼下次
                    # 展開時要求重新按查詢——避免顯示過期的剩餘數字。
                    for _k in ('bf_pool', 'bf_remaining', 'bf_done_count'):
                        st.session_state.pop(_k, None)
                    if _bf_result['aborted_early']:
                        st.warning(f"⚠️ 連續8檔失敗後提早中止（已測{_bf_result['tested_count']}檔，"
                                  f"成功{_bf_result['ok_count']}檔）。這次連網頁版都開始連續失敗，"
                                  f"可能是HiStock這次真的整站有狀況，不只是GitHub Actions那邊的問題，"
                                  f"建議稍後再試。")
                    else:
                        st.success(f"✅ 本批完成：成功 {_bf_result['ok_count']} 檔、"
                                  f"失敗 {_bf_result['fail_count']} 檔（共測試{_bf_result['tested_count']}檔）。"
                                  f"還缺 {len(_bf_remaining) - _bf_result['tested_count']} 檔，"
                                  f"可以再點一次「查詢目前進度」確認、繼續補。")
            else:
                st.success("✅ 你關心的股票今天券商分點都抓齊了，不用補跑。")
        else:
            st.caption("點上面按鈕查詢後才會顯示進度（避免每次頁面重整都自動打API拖慢速度）。")

    # 【R97續17新增，總指揮官問「平日測試殘留會如何回報、如何刪除」】
    # stage_cleanup_test_residue()對平日資料量異常偏低的(table,trade_date)
    # 組合寫進cleanup_flags表（不敢自動刪，怕誤刪正式資料）。這裡讀出來
    # 顯示成清單，人工看過理由跟筆數後，覺得真的是測試殘留才按按鈕刪，
    # 不用手動改SQL腳本填日期。
    if SUPABASE_CONN is not None:
        with st.expander("🧹 測試殘留資料清理（平日異常筆數，需人工確認）", expanded=False):
            st.caption("週末的殘留資料系統會自動清除，不會出現在這裡。這裡列的是「平日」"
                      "trade_date資料量明顯低於近期正常水準的組合——可能是被中斷的測試，"
                      "也可能只是那天剛好資料真的比較少（例如假日前半天盤），系統無法100%"
                      "確定，所以只回報不自動刪，看過理由後自行判斷要不要刪除。")
            try:
                _cf_res = (SUPABASE_CONN.table("cleanup_flags").select("*")
                          .eq("status", "pending").order("flagged_at", desc=True).execute())
                _cf_rows = _cf_res.data or []
            except Exception as _cf_e:
                _cf_rows = []
                st.caption(f"⚠️ 查詢失敗：{_cf_e}")

            if not _cf_rows:
                st.success("目前沒有待確認的異常項目。")
            else:
                for _cf in _cf_rows:
                    _cf_col1, _cf_col2, _cf_col3 = st.columns([4, 1, 1])
                    with _cf_col1:
                        st.markdown(f"**{_cf['table_name']}** ｜ {_cf['trade_date']} ｜ "
                                  f"目前{_cf['row_count']}筆（近期中位數{_cf.get('median_count', '—')}筆）")
                        st.caption(_cf.get("reason", ""))
                    with _cf_col2:
                        if st.button("🗑 確認刪除", key=f"cf_del_{_cf['id']}"):
                            try:
                                _del_col = ("symbol" if _cf['table_name']
                                          in ("twse_market_snapshot", "intraday_5min_bars") else "id")
                                SUPABASE_CONN.table(_cf['table_name']).delete().eq(
                                    "trade_date", _cf['trade_date']).execute()
                                SUPABASE_CONN.table("cleanup_flags").update(
                                    {"status": "deleted", "resolved_at": datetime.now(timezone.utc).isoformat()}
                                ).eq("id", _cf['id']).execute()
                                st.success(f"✅ 已刪除 {_cf['table_name']} 的 {_cf['trade_date']} 資料。")
                                time.sleep(0.5)
                                st.rerun()
                            except Exception as _cf_del_e:
                                st.error(f"刪除失敗：{_cf_del_e}")
                    with _cf_col3:
                        if st.button("👁 忽略（保留）", key=f"cf_dismiss_{_cf['id']}"):
                            try:
                                SUPABASE_CONN.table("cleanup_flags").update(
                                    {"status": "dismissed", "resolved_at": datetime.now(timezone.utc).isoformat()}
                                ).eq("id", _cf['id']).execute()
                                st.rerun()
                            except Exception as _cf_dis_e:
                                st.error(f"更新失敗：{_cf_dis_e}")
                    st.divider()

    # 【R97續21新增，總指揮官要求：脆弱性要有監控，不能等到很久之後才發現
    # 壞了】run_data_health_checks()寫進data_health_alerts的異常項目，
    # 在這裡呈現成清單——不用等Telegram訊息被滑掉才想起來查，網頁上隨時
    # 看得到目前系統有哪些資料品質警訊還沒處理。
    if SUPABASE_CONN is not None:
        with st.expander("🩺 資料健康監控（自動偵測「有資料但值異常」）", expanded=False):
            st.caption("每天21:40自動檢查關鍵表/欄位——不是看「有沒有資料列」，是看"
                      "「值本身有沒有意義」。這個機制的存在，就是因為法人籌碼欄位曾經"
                      "全市場長期都是0、卻因為表不是空的而被誤判成正常，一路沒被發現。")
            try:
                _dh_res = (SUPABASE_CONN.table("data_health_alerts").select("*")
                          .eq("status", "pending").order("last_seen_at", desc=True).execute())
                _dh_rows = _dh_res.data or []
            except Exception as _dh_e:
                _dh_rows = []
                st.caption(f"⚠️ 查詢失敗：{_dh_e}")

            if not _dh_rows:
                st.success("目前沒有偵測到異常，所有監控規則都正常。")
            else:
                for _dh in _dh_rows:
                    _dh_col1, _dh_col2 = st.columns([5, 1])
                    with _dh_col1:
                        st.markdown(f"**⚠️ {_dh['table_name']}.{_dh.get('column_name', '')}**")
                        st.caption(_dh.get("detail", ""))
                        st.caption(f"首次發現：{_dh.get('first_seen_at', '')[:16]} ｜"
                                  f"最近一次：{_dh.get('last_seen_at', '')[:16]}")
                    with _dh_col2:
                        if st.button("✅ 已處理", key=f"dh_resolve_{_dh['id']}"):
                            try:
                                SUPABASE_CONN.table("data_health_alerts").update(
                                    {"status": "resolved",
                                     "resolved_at": datetime.now(timezone.utc).isoformat()}
                                ).eq("id", _dh['id']).execute()
                                st.rerun()
                            except Exception as _dh_res_e:
                                st.error(f"更新失敗：{_dh_res_e}")
                    st.divider()

    # 【R82新增，R96資安修正】診斷用——原本會顯示token開頭/結尾幾個字元+
    # 完整repo名稱，總指揮官指出：這個系統是「一個密碼走天下」，沒有總
    # 指揮官跟其他使用者的角色區分，如果密碼分享給第三方協助測試，任何
    # 有密碼的人都能看到這些內容片段——即使只是開頭結尾幾個字元，也會
    # 降低token被猜中/比對的難度，repo全名更是直接暴露攻擊目標。改成
    # 只顯示「有沒有讀到」的布林狀態，完全不透露任何內容片段，診斷「secrets
    # 有沒有設定成功」這個核心用途還是保留，只是不再洩漏內容本身。
    with st.expander("🔍 診斷：GITHUB_TOKEN/GITHUB_REPO 是否已正確設定", expanded=False):
        # 【R84修復】改用_find_secret_anywhere——原本只查最外層+radar_
        # secrets，實際案例是值被歸類進[supabase]區塊，這個函式掃過所有
        # 區塊都找得到。
        _diag_token = _find_secret_anywhere("GITHUB_TOKEN")
        _diag_repo = _find_secret_anywhere("GITHUB_REPO")
        st.caption("✅ GITHUB_TOKEN 讀到了" if _diag_token else "❌ GITHUB_TOKEN 完全沒讀到（空字串或不存在）")
        st.caption("✅ GITHUB_REPO 讀到了" if _diag_repo else "❌ GITHUB_REPO 完全沒讀到（空字串或不存在）")

        # 【R83新增，R96資安修正】兩輪重打都讀不到，可能是被放進某個分類
        # 底下或存檔沒生效。原本會列出st.secrets完整鍵值結構(欄位名稱)，
        # 這裡同樣收斂：只回報「有沒有找到目標欄位」，不列出完整的欄位
        # 清單結構——欄位名稱本身雖然不是密鑰內容，但完整結構清單一樣
        # 透露了這個系統設定了哪些第三方服務(NVIDIA/FinMind/Supabase等)，
        # 對第三方使用者來說是不必要的資訊揭露。
        st.markdown("**st.secrets 掃描結果**")
        try:
            _top_keys = list(st.secrets.keys())
            _found_at = []
            for _k in _top_keys:
                _v = st.secrets[_k]
                if hasattr(_v, 'keys') and ('GITHUB_TOKEN' in _v.keys() or 'GITHUB_REPO' in _v.keys()):
                    _found_at.append(_k)
            if 'GITHUB_TOKEN' in _top_keys or 'GITHUB_REPO' in _top_keys:
                st.caption("✅ 兩個欄位都在最外層（標準位置）")
            elif _found_at:
                # 【R98續82修正，總指揮官反映這段文字誤導成「讀不到」】
                # 上面已經明確顯示「✅讀到了」(_find_secret_anywhere能
                # 找到任何位置的值)，這段原本寫「需要移到最外層才讀得到」
                # 措辭矛盾——功能本身完全正常，只是擺放位置不是標準TOML
                # 最外層，不是「讀不到」。改成準確反映實際狀況，不誤導。
                st.caption(f"ℹ️ 欄位放在「{'/'.join(_found_at)}」這個分類底下，不是最外層——"
                          f"但系統用更聰明的方式找到了(見上面兩個✅)，**功能完全正常，不用搬動**。"
                          f"純粹想整理成標準TOML格式的話可以移到最外層，但不是必要。")
            else:
                st.caption("❌ 完全找不到這兩個欄位，代表存檔沒有真的生效")
        except Exception as _list_e:
            # 【R96資安修正】統一標準，完整例外內容改印到伺服器log。
            print(f"[secrets掃描-診斷] 發生例外：{type(_list_e).__name__}: {_list_e}")
            st.error("掃描st.secrets時發生例外（詳細原因已寫入伺服器log）。")

        st.caption("如果這裡兩個都顯示❌，代表secrets真的沒被讀進來（格式問題或"
                  "還沒重啟生效）；如果這裡都顯示✅但按鈕還是失敗，代表token本身"
                  "權限不足或repo名稱不對，是不同的問題，需要進一步排查時再麻煩"
                  "告訴我，不需要把這個診斷區塊的截圖直接貼出來（避免意外外流）。")

    # 【R64新增，R96改成永遠開啟，R96再調整】定時喚醒——Streamlit Cloud容器
    # 閒置會被回收，背景ping減少被判定閒置的機會。純HEAD請求，不夾帶或恢復
    # 登入狀態，登出後iframe隨畫面移除，計時器不會在背景繼續跑。
    # 【R96調整】原本這裡有一段說明文字用st.caption顯示，總指揮官反映這種
    # 「系統內建、使用者不用管」的機制不用在主畫面佔位置說明，程式碼註解
    # 講清楚就好，UI上只留一個簡短狀態旗標，實際顯示位置搬到下面「系統
    # 連線狀態」那個區塊統一呈現。
    _keepalive_on = True
    if _keepalive_on:
        components.html(
            """<script>
            (function() {
                setInterval(function() {
                    fetch(window.location.href, {method: 'HEAD', cache: 'no-store'}).catch(function(){});
                }, 600000);  // 每10分鐘一次，避免打太頻繁反而造成額外負擔
            })();
            </script>""", height=0)

    # 【V160 新增】FinMind 額度輪替狀態，讓「現在用第幾組帳號」看得見，
    # 不用猜是不是還卡在第一組（先前輪替根本沒接上，額度只有 600 而非 1500）
    with st.expander("🔑 FinMind 額度狀態", expanded=False):
        # 【R97修復，總指揮官抓到：這裡一直沒改，才會看起來「沒什麼變化」】
        # 之前只把get_fm_real_quota_status()接進排程端的候選池邏輯，這個
        # 網頁版面板一直呼叫的是舊的估計版get_fm_quota_status()，兩個是
        # 不同函式，難怪修好真實版之後這裡完全看不出差異——不是沒修好，
        # 是這裡從來沒有真的接上新函式。這次直接改成優先顯示真實數字，
        # 查詢本身失敗時才退回舊的估計值當備援，並清楚標示哪個是真的、
        # 哪個是估的，不會再讓兩者混在一起看不出差別。
        _real_quota = get_fm_real_quota_status()
        if _real_quota["total_remaining"] is not None:
            st.caption("✅ 以下是 FinMind 伺服器端的真實數字（不是估計值）：")
            for _i, _t in enumerate(_real_quota["tokens"]):
                if _t.get("used") is not None:
                    st.caption(f"帳號{_i + 1}：已用 {_t['used']}/{_t['limit']} 次，"
                              f"剩餘 {_t['remaining']} 次")
                else:
                    st.caption(f"帳號{_i + 1}：查詢失敗（{_t.get('note', '未知原因')}）")
            st.caption(f"總剩餘（不含訪客額度）：{_real_quota['total_remaining']} 次")
        else:
            st.caption("⚠️ 真實額度查詢暫時失敗，改顯示本工具自己回推的估計值：")
            for _row in get_fm_quota_status():
                st.caption(_row)
        st.caption("額度鏈：帳號1(600) → 帳號2(600) → 訪客(300) = 1500/小時"
                  "（訪客額度沒有對應帳號token，真實查詢查不到，只能用估計值）")

    with st.expander("📥 [主攻] 官方 CSV 籌碼強填中樞", expanded=False):
        uploaded_csvs = st.file_uploader("拖曳證交所三大法人 CSV (T86)", type=['csv'],
                                         accept_multiple_files=True, key="csv_up_v3")
        if uploaded_csvs and st.button("🚀 批次強制解析回填至 SQLite", use_container_width=True):
            process_twse_csv(uploaded_csvs)

        # 【V160改版】FinMind「不帶data_id的全市場模式」是付費方案專屬，
        # 改成只同步「你實際在看的股票」(持倉+雷達+觀察，通常30-100檔)，
        # 逐檔同步在免費額度600次/小時內。
        st.divider()
        st.markdown("**🔄 批次同步我關注的股票籌碼（含上櫃）**")
        st.caption("證交所 T86 CSV 只涵蓋上市，上櫃股（6xxx等）沒有批次來源。"
                   "這顆按鈕會把你的**持倉＋雷達＋觀察清單**裡的股票逐檔同步籌碼"
                   "（每檔含近40天歷史，5日/10日才算得出來）。"
                   "⚠️ FinMind 的「一次抓全市場」模式需要付費方案，免費帳號無法使用，"
                   "所以這裡改成只同步你實際關注的標的——通常30-100檔，額度綽綽有餘。")

        _watch_codes = []
        for _sec in ('portfolio', 'pinned_stocks', 'observe_stocks'):
            _watch_codes.extend(list(st.session_state.get(_sec, {}).keys()))
        _watch_codes = sorted(set(_watch_codes))
        _otc_in_list = [c for c in _watch_codes if c.startswith(('4', '5', '6', '8'))]
        st.caption(f"目前清單共 **{len(_watch_codes)}** 檔"
                   f"（其中 {len(_otc_in_list)} 檔可能是上櫃/中小型股，最需要這個同步）")

        if st.button("🔄 開始批次同步", key="batch_sync_btn", use_container_width=True,
                     disabled=not _watch_codes):
            # 【V160修復】按鈕被踢回登入畫面——原本序列迴圈逐一呼叫
            # FinMind，全部跑完要好幾分鐘不中斷，容易讓Streamlit Cloud判定
            # 逾時。改成跟持倉/雷達同一套ThreadPoolExecutor，8檔同時處理。
            _prog = st.progress(0.0, text=f"⚙️ 同步中 0/{len(_watch_codes)}")
            _ok_n, _fail = 0, []
            _bs_ctx = get_script_run_ctx()
            _bs_done = 0
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
                def _sync_with_ctx(code):
                    if _bs_ctx:
                        add_script_run_ctx(threading.current_thread(), _bs_ctx)
                    return sync_single_stock_finmind(code)
                _bs_futures = {executor.submit(_sync_with_ctx, c): c for c in _watch_codes}
                for future in concurrent.futures.as_completed(_bs_futures):
                    _c = _bs_futures[future]
                    _bs_done += 1
                    _prog.progress(_bs_done / len(_watch_codes),
                                  text=f"⚙️ 同步中 {_bs_done}/{len(_watch_codes)}"
                                       f"（{_bs_done/len(_watch_codes)*100:.0f}%）")
                    try:
                        _ok, _msg = future.result()
                        if _ok:
                            _ok_n += 1
                        else:
                            _fail.append(f"{_c}({_msg})")
                    except Exception as e:
                        _fail.append(f"{_c}({type(e).__name__})")
            _prog.progress(1.0, text="完成")
            if _ok_n:
                st.success(f"✅ 成功同步 {_ok_n}/{len(_watch_codes)} 檔")
            if _fail:
                st.warning(f"⚠️ {len(_fail)} 檔失敗：" + "、".join(_fail[:8])
                           + ("..." if len(_fail) > 8 else ""))
            time.sleep(1)
            st.rerun()

    # 【R88新增】門檻參數調整面板——透過get_threshold()統一讀取函式，
    # 直接影響calc_disposal_risk_proxy/is_volume_dump/查1/9/10濾網比對，
    # 調了馬上生效不用重啟app。
    with st.expander("🎛️ 門檻參數調整（影響查1/9/10濾網、爆量/處置風險判斷）", expanded=False):
        st.caption("這裡調整的數字會立即影響戰卡評分跟濾網比對邏輯——調整前建議先去"
                  "「📊門檻校準結果」分頁看過敏感度曲線，選落在「高原區」的值，"
                  "不要只挑單一次掃描表現最好但可能是孤峰的數字。")
        _th_col1, _th_col2 = st.columns(2)
        _new_vol_low = _th_col1.number_input(
            "量縮沉澱門檻（查10用，預設0.6）", min_value=0.1, max_value=1.0,
            value=float(get_threshold('vol_ratio_low')), step=0.05, key="threshold_override_vol_ratio_low")
        _new_vol_surge = _th_col2.number_input(
            "爆量門檻（查1/9用，預設2.0）", min_value=1.0, max_value=5.0,
            value=float(get_threshold('vol_ratio_surge')), step=0.1, key="threshold_override_vol_ratio_surge")
        _th_col3, _th_col4 = st.columns(2)
        _new_gain_watch = _th_col3.number_input(
            "六日累計漲跌｜watch門檻（預設20）", min_value=5, max_value=50,
            value=int(get_threshold('six_day_gain_watch')), step=1, key="threshold_override_six_day_gain_watch")
        _new_gain_high = _th_col4.number_input(
            "六日累計漲跌｜high門檻（預設32）", min_value=10, max_value=80,
            value=int(get_threshold('six_day_gain_high')), step=1, key="threshold_override_six_day_gain_high")
        if st.button("↩️ 全部還原成預設值", key="threshold_reset_btn", use_container_width=True):
            for _k in DEFAULT_THRESHOLDS:
                st.session_state.pop(f'threshold_override_{_k}', None)
            st.success("已還原成預設值。")
            time.sleep(1)
            st.rerun()
        st.caption("這些調整存在瀏覽器session裡，重新整理頁面或關掉分頁就會消失、"
                  "回到預設值——如果測出一組更好的門檻、想長期採用，要回頭跟我說，"
                  "由我把數字改進程式碼裡的DEFAULT_THRESHOLDS常數，這樣才會變成"
                  "永久生效、對所有人都適用的新預設值。")

    with st.expander("🩺 資料源健康度檢查", expanded=False):
        st.caption("**這個功能是為了解決「靜默失敗」**：先前除權息欄位改名、營收參數矛盾這類問題，"
                   "畫面上都只顯示「查無資料」，看不出是資料源壞了還是本來就沒資料，"
                   "每次都拖很久才發現。這裡逐一實測每個資料源，直接告訴你誰活著、誰壞了。")
        if st.button("🩺 立即檢查所有資料源", key="health_check_btn", use_container_width=True):
            _hc_t0 = time.time()
            _hc_prog = st.progress(0.0, text="逐一測試各資料源中 0/6")

            def _hc_cb(done, total):
                _hc_prog.progress(done / total, text=f"逐一測試各資料源中 {done}/{total}（{done/total*100:.0f}%）")

            _health = check_data_source_health(get_active_fm_token(), progress_callback=_hc_cb)
            _hc_prog.empty()
            st.session_state['health_check_meta'] = {
                'count': len(_health), 'elapsed': time.time() - _hc_t0,
                'ts': datetime.now(TAIPEI_TZ).strftime('%H:%M:%S'),
            }
            _bad = [h for h in _health if not h['ok']]
            if not _bad:
                st.success(f"✅ 全部 {len(_health)} 個資料源正常")
            else:
                st.error(f"❌ {len(_bad)} 個資料源異常，需要處理")
            st.dataframe(pd.DataFrame([{
                '資料源': h['name'],
                '狀態': '✅ 正常' if h['ok'] else '❌ 異常',
                '詳情': h['detail'],
            } for h in _health]), use_container_width=True, hide_index=True)
            # 【R77新增】手機窄螢幕上，這張表的「詳情」欄常常被切掉、要橫向
            # 捲動才看得到——好幾輪的截圖都只看到「狀態」欄，看不到失敗原因。
            # 失敗的項目額外用純文字條列一次，不用捲動就看得到完整內容。
            if _bad:
                st.markdown("**⚠️ 異常項目詳情（手機版表格容易看不到，這裡列一次）**")
                for h in _bad:
                    st.caption(f"❌ **{h['name']}**：{h['detail']}")
        _hc_meta = st.session_state.get('health_check_meta')
        if _hc_meta:
            st.caption(f"🕐 上次檢查：{_hc_meta['count']} 項，共花 {_hc_meta['elapsed']:.1f} 秒（{_hc_meta['ts']}）")

    # 【R98新增，總指揮官要求：不用再翻Streamlit Cloud原始log才能查問題】
    # 上面「立即檢查」是主動探測（點了才測一次），這裡改成讀既有的
    # data_source_health_log表——這張表是排程端(Finnhub/FinMind)跟網頁端
    # (Finnhub_web/TWSE MIS即時報價)發生「異常」時各自寫進來的歷史紀錄，
    # 不用主動點按鈕，平常運作中發生的問題（尤其是「很多股票即時報價抓
    # 不到」這種要在盤中當下才重現的狀況）本來就會被動記下來，直接來這裡
    # 查即可，不用再另外調Streamlit Cloud/GitHub Actions的原始console log。
    with st.expander("📋 資料源異常歷史紀錄（不用翻log，直接查這裡）", expanded=False):
        st.caption("只顯示「異常」的紀錄（例如TWSE即時報價疑似被限流、Finnhub查詢失敗），"
                   "正常運作不會產生紀錄，所以這裡空白是好事。"
                   "來源標記：twse_mis_web=網頁端即時報價、finnhub/finnhub_web=排程端/"
                   "網頁端隔夜總經HUD、finmind_taiex=大盤20MA判斷。")
        _hlog_days = st.slider("查最近幾天", 1, 14, 3, key="hlog_days_sld")
        if st.button("🔍 查詢異常歷史", key="hlog_query_btn", use_container_width=True):
            if SUPABASE_CONN is None:
                st.warning("Supabase未連線，無法查詢。")
            else:
                try:
                    _hlog_from = (datetime.now(TAIPEI_TZ) - timedelta(days=_hlog_days)).strftime('%Y-%m-%d')
                    _hlog_res = (SUPABASE_CONN.table("data_source_health_log")
                                .select("*").eq("ok", False)
                                .gte("log_date", _hlog_from)
                                .order("id", desc=True).limit(200).execute())
                    st.session_state['hlog_rows'] = _hlog_res.data or []
                except Exception as _hlog_e:
                    st.warning(f"查詢失敗：{_hlog_e}")
                    st.session_state['hlog_rows'] = []
        _hlog_rows = st.session_state.get('hlog_rows')
        if _hlog_rows is not None:
            if not _hlog_rows:
                st.success(f"✅ 最近 {_hlog_days} 天沒有任何異常紀錄。")
            else:
                st.dataframe(pd.DataFrame([{
                    '日期': r.get('log_date'), '來源': r.get('source'),
                    '對象': r.get('symbol'), '說明': r.get('note', ''),
                } for r in _hlog_rows]), use_container_width=True, hide_index=True)
                st.caption(f"共 {len(_hlog_rows)} 筆異常紀錄（最多顯示200筆，依時間新到舊排序）。")
        else:
            st.caption("點上面按鈕查詢（避免每次展開都自動打Supabase）。")

    with st.expander("📊 資料庫完整度與備份還原", expanded=False):
        # 【V160新增】開機回填天數設定——45天回填視窗隨資料累積越撈越多，
        # 讓你自己權衡登入速度vs本機快取涵蓋範圍，不影響Supabase完整歷史。
        _cur_refill = int(float(sb_get_config('boot_refill_days', '45')))
        st.markdown("**⚙️ 開機回填天數設定**")
        st.caption("每次重新登入（尤其容器休眠後重啟）都會把這個天數內的籌碼資料從雲端"
                   "回填到本機，資料量越大等越久。縮小天數能加快登入，"
                   "不影響 Supabase 雲端的完整歷史——只是本機快取涵蓋範圍變小。")
        _new_refill = st.slider("回填天數", 7, 90, _cur_refill, 7, key="boot_refill_sld")
        if _new_refill != _cur_refill and st.button("💾 儲存並套用（下次登入生效）",
                                                     key="save_refill_days"):
            sb_set_config('boot_refill_days', str(_new_refill), '開機回填天數')
            st.success(f"✅ 已設定為 {_new_refill} 天，下次登入生效")

        db_days, db_details = get_db_stats()
        if db_days == 0:
            st.warning("⚠️ 目前大腦無籌碼資料")
        else:
            st.write(f"當前儲存天數共: {db_days} 天")
            # 【V160】總指揮官回報「前天31天、今天28天，為什麼變少」——這裡講清楚機制：
            st.caption("ℹ️ 這是**本機**資料庫的天數，不是雲端。Streamlit Cloud 每次重新部署都會"
                       "清空本機檔案，開機時只從雲端回填「最近45天內」的資料，所以天數會隨"
                       "部署與時間視窗滑動而變動。要看完整歷史請以 Supabase 雲端為準；"
                       "若覺得本機缺資料，按上方「🔼一鍵補推」可把本機補回雲端（反向補回會在開機自動做）。")
            with st.container(height=150):
                for detail in db_details:
                    st.caption(f"📅 {detail[0]}: 已存 {detail[1]} 檔籌碼")

        # 【V160】雲端同步狀態 + 手動補推
        st.divider()
        st.markdown("### ☁️ 雲端同步 (Supabase)")
        if not SUPABASE_ENABLED:
            st.caption(f"目前純本機模式：{_SUPABASE_INIT_MSG}")
        else:
            st.caption("本機資料若比雲端新（例如雙寫上線前匯入的舊資料、或Supabase當機期間漏寫），"
                       "可用下方按鈕把本機全部資料補推到雲端，兩邊同步。重複推不會產生重複列。")
            if st.button("🔼 一鍵補推本機資料到雲端", use_container_width=True):
                _push_t0 = time.time()
                _push_prog = st.progress(0)
                _push_status = st.empty()

                def _push_cb(kind, done, total):
                    label = "籌碼" if kind == 'inst' else "大戶"
                    _push_status.caption(f"補推{label}：{done}/{total}")
                    _push_prog.progress(min(1.0, done / max(1, total)))

                _ip, _bp = push_all_local_to_supabase(progress_cb=_push_cb)
                _push_prog.empty()
                _push_status.empty()
                st.session_state['push_scan_meta'] = {
                    'count': _ip + _bp, 'elapsed': time.time() - _push_t0,
                    'ts': datetime.now(TAIPEI_TZ).strftime('%H:%M:%S'),
                }
                if _ip or _bp:
                    st.success(f"✅ 補推完成：籌碼 {_ip:,} 筆、大戶 {_bp:,} 筆已同步到雲端")
                else:
                    st.warning("沒有補推任何資料（可能本機無資料，或Supabase連線異常）。")
            _push_meta = st.session_state.get('push_scan_meta')
            if _push_meta:
                st.caption(f"🕐 上次補推：共 {_push_meta['count']:,} 筆，花 {_push_meta['elapsed']:.1f} 秒（{_push_meta['ts']}）")

        st.divider()
        st.markdown("### 🔄 強制清除快取重新查詢")
        st.caption("千張大戶／月營收現在的邏輯：一旦抓到成功的數字，會固定保留著，"
                  "之後每30分鐘才檢查一次有沒有新資料（新的月營收、新一週的大戶數字）；"
                  "檢查失敗也不會清空舊數字，會繼續顯示上次抓到的，不會忽有忽無。"
                  "如果你想立即強制重新檢查，按下方按鈕。")
        if st.button("🔄 清除大戶／營收快取，立即重查", use_container_width=True):
            _get_smart_cache_store().clear()
            st.success("✅ 快取已清除，重新整理畫面後會強制重查最新資料")
            time.sleep(0.5)
            st.rerun()


    st.divider()
    min_volume_filter = st.slider("最低 5 日波段均量門檻 (張)", 0, 5000, 500, 100)
    scan_pool_size = st.slider("全市場掃描池大小 (檔)", 100, 1200, 300, 100)
    enable_doomsday_lock = st.checkbox("💀 開啟末日鎔斷防護鎖", value=False)
    # 【R96調整】總指揮官指出：這種「該不該執行」的行為不該是每次登入
    # 都要自己記得勾選的UI開關，應該內建成系統固定行為。位階風控濾網
    # 改成永遠開啟，不再顯示checkbox。
    enable_market_filter = True

    if MARKET_REGIME['known']:
        # 【R65修復】跟主HUD同一個問題：站上20MA(多方)原本是綠、跌破20MA原本是紅，
        # 跟app紅漲綠跌的慣例相反，這裡一併對調。
        _mk_c = "#ff4d4d" if MARKET_REGIME['bull'] else "#00c853"
        _mk_t = "站上 20MA (多方環境)" if MARKET_REGIME['bull'] else "跌破 20MA (訊號強制降級)"
        st.markdown(f"<div style='font-size:12px; color:{_mk_c};'>大盤 {MARKET_REGIME['close']:,.0f} / "
                    f"20MA {MARKET_REGIME['ma20']:,.0f}（{MARKET_REGIME['dev']:+.1f}%）<br>{_mk_t}</div>",
                    unsafe_allow_html=True)
    else:
        st.caption("大盤位階：資料抓取中（暫不降級）")

    st.divider()
    # 【R96調整】開啟自動輪詢、異常推播Telegram不該是每次登入要記得勾選
    # 的UI開關，改成永遠開啟、固定3分鐘間隔，運作方式完全沒變。
    # 【R96再調整】原本這裡有「📡盤中自動輪詢（陽春版）」的標題+一段說明
    # caption佔主畫面版面，總指揮官反映這種系統內建、使用者不用操作的
    # 機制不用在側邊欄常駐佔位置說明，程式碼註解講清楚就好。UI上不再
    # 顯示這個區塊標題跟說明文字，實際運作狀態統一搬到下面「系統連線
    # 狀態」區塊，用簡短的一行文字呈現「有在穩定運作」即可。
    auto_poll_enabled = True
    poll_interval_min = 3
    # 【R96調整】原本套件沒裝時會用st.caption在側邊欄中途冒出一段警語，
    # 總指揮官反映不用顯示在介面上。改成用旗標記錄結果，併入下面「系統
    # 連線狀態」那個統一狀態行——沒裝套件時狀態行會誠實顯示🔴，不會
    # 假裝正常運作，但不會在側邊欄中途另外冒出一段獨立的警語文字。
    st.session_state['_autorefresh_pkg_ok'] = True
    if auto_poll_enabled:
        # 【R96調整】異常推播Telegram永遠開啟，不再顯示checkbox。
        st.session_state["push_anomaly_telegram"] = True
        try:
            from streamlit_autorefresh import st_autorefresh
            st_autorefresh(interval=poll_interval_min * 60 * 1000, key="autorefresh_timer")
        except ImportError:
            st.session_state['_autorefresh_pkg_ok'] = False

    st.divider()
    commands_list = ["查1.主升段突擊", "查2.魚頭慢伏支撐", "查3.價值投資與循環", "查4.投信作帳集團股",
                     "查5.籌碼外資霸王色", "查6.營收雙增爆發突破", "查8.昨日強勢動能延續",
                     "查9.均線糾結爆量突破", "查10.籌碼沉澱量縮潛伏", "查11.除權息尋寶雷達",
                     "查12.K線型態尋寶型"]

    intel_pool = st.session_state.get('intelligence_pool', {})
    existing_sources = set([src for info in intel_pool.values()
                            if isinstance(info, dict) for src in info.get("sources", [])])
    base_idx = 13
    for src in sorted(list(existing_sources)):
        commands_list.append(f"查{base_idx}. 情報雷達：{src}")
        SCAN_COMMAND_MAP[f"查{base_idx}"] = f"情報雷達：{src}"
        base_idx += 1
    if existing_sources:
        commands_list.append(f"查{base_idx}. 🏆 情報黃金交叉")
        SCAN_COMMAND_MAP[f"查{base_idx}"] = "🏆 情報黃金交叉（多個情報來源同時指向）"

    selected_cmds = st.multiselect("🎯 戰略掃描條件 (可複選交集)", commands_list, default=[])
    selected_k_patterns = []
    if any("查12" in cmd for cmd in selected_cmds):
        with st.container(border=True):
            if st.checkbox("🔥 長紅吞噬 / 低檔長紅"): selected_k_patterns.append("長紅")
            if st.checkbox("🔥 紅三兵強勢推推"): selected_k_patterns.append("紅三兵")
            if st.checkbox("💀 長黑吞噬頂部出貨"): selected_k_patterns.append("長黑")
            if st.checkbox("💀 黑三兵弱勢跌破"): selected_k_patterns.append("黑三兵")

    # 【V160 新增】全市場掃描本身也支援評分範圍篩選（不只是雷達/觀察區列表篩選），
    # 跟戰略掃描條件(查X)一起AND生效，掃描時就直接排除範圍外的，不用先掃完再篩。
    scan_score_range = st.slider("📊 掃描評分範圍篩選（只保留評分落在此區間的結果）", -10, 10, (-10, 10),
                                 key="scan_score_range")

    if st.button("🚀 執行全市場並行高速掃描", use_container_width=True, type="primary"):
        if not selected_cmds:
            st.warning("請先選擇至少一項戰略條件。")
        else:
            st.session_state.trigger_scan = True

    with st.expander("📖 統籌戰術解密說明書", expanded=False):
        st.markdown("""<div style="font-size:13px; color:#ffffff; background:#1e1e24; padding:15px; border-radius:8px;">
        <b style='color:#f1c40f;'>🎯 建議每日操作流程（提升勝率的搭配順序）</b><br><br>
        <b style='color:#00d2ff;'>①開盤前（8:55前）</b>：先看最上方「隔夜總經」HUD，確認開盤前閘門是否顯示暫緩。
        隔夜劇變時，當天寧可保守，不強求進場。<br>
        <b style='color:#00d2ff;'>②全市場掃描</b>：按「執行全市場並行高速掃描」，可疊加「戰略掃描條件」縮小範圍
        （查1~查12任選複選）。掃出來的先進<b>觀察區</b>，不要直接進常態雷達——避免雷達被還沒驗證過的股票稀釋。<br>
        <b style='color:#00d2ff;'>③交叉驗證</b>：對觀察區裡有興趣的標的，去「訊號命中率回測實驗室」查它過去3年
        該訊號的真實勝率／樣本數（樣本<30筆別信）；有情報來源的，去「情報來源準確度」看該來源歷史準不準。
        兩邊都過關，才升級到常態雷達。<br>
        <b style='color:#00d2ff;'>④決策</b>：常態雷達卡片最上方的動詞橫幅（建議進攻/觀望/撤退）+ 進場價格區間，
        是秒讀決策的核心，不用每次都展開三戰區細節。<br>
        <b style='color:#00d2ff;'>⑤持倉管理</b>：進場後移到持倉模擬倉，防守線／短線停利點會持續更新，跌破防守線是
        結構破壞的訊號。<br>
        <b style='color:#00d2ff;'>⑥每週回顧</b>：看「手動 vs 系統查詢 勝率PK」跟「系統自主選股（做多vs做空）」，
        比較這陣子是你的直覺準、還是系統的量化濾網準，用來校準自己該多信哪一邊。</div>""", unsafe_allow_html=True)

        st.markdown("""<div style="font-size:13px; color:#ffffff; background:#1e1e24; padding:15px; border-radius:8px; margin-top:10px;">
        <b style='color:#f1c40f;'>🛡️ V160 戰情室濾網大公開</b><br>
        <b style='color:#00d2ff;'>查1.</b> 首根長紅(今紅昨黑·實體>0.5ATR) + 爆量>=2.0 + KDJ金叉<br>
        <b style='color:#00d2ff;'>查2.</b> 股價站上季線(60MA) + 爆量>=1.2<br>
        <b style='color:#00d2ff;'>查3.</b> 價值分數>=60 + 無基本面地雷<br>
        <b style='color:#00d2ff;'>查4.</b> 投信單日買超>0<br>
        <b style='color:#00d2ff;'>查5.</b> 外資買超 + 融資減少(未同步融資者視為通過)<br>
        <b style='color:#00d2ff;'>查6.</b> 營收 YoY 年增 > 20%<br>
        <b style='color:#00d2ff;'>查8.</b> 昨日漲幅 > 5%<br>
        <b style='color:#00d2ff;'>查9.</b> 今日爆量比 >= 2.0x<br>
        <b style='color:#00d2ff;'>查10.</b> 爆量比 <= 0.6 (量縮>40%) + 融資減少<br>
        <b style='color:#00d2ff;'>查11.</b> 現金殖利率 >= 4.5%<br>
        <b style='color:#00d2ff;'>查12.</b> 特定K線型態 (ATR動態判定)<br>
        <b style='color:#f1c40f;'>查13+.</b> 情報雷達：只掃該來源綁定過的標的<br>
        <b style='color:#f1c40f;'>黃金交叉.</b> 同時被 2 個以上情報來源提及</div>""", unsafe_allow_html=True)

        st.markdown("""<div style="font-size:13px; color:#ffffff; background:#1e1e24; padding:15px; border-radius:8px; margin-top:10px;">
        <b style='color:#f1c40f;'>🧪 三個驗證工具，各自該用在什麼時候</b><br><br>
        <b style='color:#00d2ff;'>訊號命中率回測實驗室</b>：驗證「技術面訊號本身」準不準——某檔股票過去出現
        這個訊號時，後續3/10日漲跌如何。適合用在：你想加一檔股票進雷達前，先確認這檔股票的訊號歷史上靠不靠譜。<br>
        <b style='color:#00d2ff;'>情報來源準確度</b>：驗證「消息來源」準不準——股癌/法說會/券商報告這些來源，
        過去報過的股票後續表現如何。適合用在：你手上有多個情報來源，想知道該優先信哪個。<br>
        <b style='color:#00d2ff;'>手動vs系統勝率PK</b>：驗證「選股方式」準不準——你自己手動挑的 vs 系統演算法篩的，
        誰的歷史報酬比較好。適合用在：定期（建議每週）檢視，決定這陣子該多聽自己的判斷還是多信系統。<br><br>
        三者是不同層次：訊號驗證「這檔股票該不該信」、來源驗證「這則消息該不該信」、PK驗證「這個選股方法該不該信」，
        建議都用，各司其職，不是互相替代。</div>""", unsafe_allow_html=True)

    st.divider()
    st.markdown("<div style='font-size:12px; font-weight:bold; margin-bottom:5px;'>📡 系統連線狀態</div>",
                unsafe_allow_html=True)
    _sb_icon = "🟢" if SUPABASE_ENABLED else "⚪"
    _sb_sync = st.session_state.get('sb_sync_result', (0, 0))
    # 【R96調整】自動輪詢的狀態改依_autorefresh_pkg_ok這個旗標動態顯示——
    # 套件沒裝時誠實顯示🔴，不再另外用獨立的st.caption警語佔版面。
    _autorefresh_icon = "🟢" if st.session_state.get('_autorefresh_pkg_ok', True) else "🔴"
    st.markdown(f"<div style='font-size:11px;'>{'🟢' if API_READY else '🔴'} NVIDIA NIM<br>"
                f"{'🟢' if FINMIND_READY else '🔴'} FinMind 線路<br>"
                f"{_sb_icon} Supabase 雲端大腦<br>"
                f"{_autorefresh_icon} 盤中自動輪詢（3分鐘）＋保持喚醒（10分鐘）</div>", unsafe_allow_html=True)
    if SUPABASE_ENABLED:
        st.caption(f"雙軌已啟用｜開機回填 籌碼{_sb_sync[0]}筆／大戶{_sb_sync[1]}筆")
    else:
        st.caption(f"純本機模式：{_SUPABASE_INIT_MSG}")

    # 【V160 新功能】NVIDIA 模型手動選擇（預設仍是自動偵測優先 DeepSeek，但可手動切換）
    if API_READY:
        _discovered = get_nim_models()
        if _discovered:
            _model_short_names = [m.split('/')[-1] for m in _discovered]
            _prev_choice = st.session_state.get('preferred_nim_model', _model_short_names[0])
            _default_idx = _model_short_names.index(_prev_choice) if _prev_choice in _model_short_names else 0
            _picked = st.selectbox("🤖 AI推演偏好模型", _model_short_names, index=_default_idx,
                                   help="預設優先用DeepSeek(邏輯較強)。可手動切換成清單裡其他偵測到的模型。"
                                        "如果你選的那個剛好失效，系統會自動退回清單裡其他可用模型，不會整個掛掉。")
            st.session_state['preferred_nim_model'] = _picked
        # 【R98續76新增，總指揮官指示：解決NVIDIA模型下架問題】discover_
        # nim_models()本身有1小時快取，NVIDIA那邊如果剛好暫時不穩定
        # (連線逾時，不是模型真的下架)，快取住失敗結果要等1小時才會
        # 自動重新查——按這顆按鈕清除快取、強制立刻重新查詢，不用等。
        if st.button("🔄 立即重新偵測NVIDIA可用模型", key="btn_refresh_nim_models",
                    help="懷疑AI推演連不上時按這個，會立刻重新查詢NVIDIA目前真正可用的模型"
                         "清單，不用等1小時快取自動過期。"):
            discover_nim_models.clear()
            st.success("已清除快取，下次AI推演會重新偵測目前可用的模型。")


    # 【R95續13】按鈕移到側邊欄最底部收合展開區，不常用不佔常用功能空間。
    # 【R78/R81】排程補救按鈕——Streamlit Cloud的IP連TDCC會失敗，改成呼叫
    # GitHub API遠端觸發同一個排程，用不會被擋的路徑執行。
    with st.expander("🔧 千張大戶排程補救（不常用，已有週六自動排程）", expanded=False):
        st.caption("千張大戶本來就有每週六自動排程抓取，這顆只在你不想等到週六時才需要按。")
        if st.button("🔄 立即補跑千張大戶（觸發GitHub Actions，不等週六排程）",
                    key="bh_catchup_btn", use_container_width=True):
            with st.spinner("正在觸發GitHub Actions..."):
                _ok, _msg = trigger_github_workflow("big_holder")
                if _ok:
                    st.success(f"✅ {_msg}")
                else:
                    st.warning(f"⚠️ {_msg}")

    with st.expander("💾 備份還原（雲端已自動同步，這裡僅供緊急還原用）", expanded=False):
        st.caption("雷達／持倉／情報／人工覆寫都已經自動同步進 Supabase 雲端，"
                   "平常不需要手動備份。這裡保留給萬一雲端出狀況時的緊急還原用，"
                   "建議一週手動存一次當保險即可，不用每天做。")
        col_dl1, col_dl2 = st.columns(2)
        with col_dl1:
            if os.path.exists(USER_DB_FILE):
                with open(USER_DB_FILE, "rb") as f:
                    st.download_button("📄 下載設定檔", f.read(), "54088_database.json",
                                       "application/json", use_container_width=True)
        with col_dl2:
            if os.path.exists(SQLITE_DB_FILE):
                with open(SQLITE_DB_FILE, "rb") as f:
                    st.download_button("🗄️ 下載籌碼庫", f.read(), "54088_inst_history.db",
                                       "application/octet-stream", use_container_width=True)

        st.divider()
        uploaded_json = st.file_uploader("上傳 54088_database.json", type=['json'], key="restore_json_v1")
        uploaded_db = st.file_uploader("上傳 54088_inst_history.db", type=['db'], key="restore_db_v1")
        if st.button("🚀 執行實體大腦覆蓋還原", use_container_width=True):
            if uploaded_json:
                with open(USER_DB_FILE, "wb") as f:
                    f.write(uploaded_json.getbuffer())
                st.success("📄 設定檔覆蓋成功！")
            if uploaded_db:
                # 【R96修復】原本SQLITE_CONN=get_db_conn()沒加global宣告，
                # init_sqlite_db()加@st.cache_resource後掩蓋路徑失效，改成
                # 呼叫.clear()讓快取失效，下次rerun才會真的開新連線。
                try:
                    SQLITE_CONN.close()
                except Exception:
                    pass
                with open(SQLITE_DB_FILE, "wb") as f:
                    f.write(uploaded_db.getbuffer())
                init_sqlite_db.clear()
                st.success("🗄️ 籌碼庫全面覆蓋還原成功！")
            time.sleep(1)
            st.rerun()

    # 【R71調整】千張大戶已改由system_scheduler.py每週六自動抓取，手動
    # 上傳降級成備用，移到側欄最底下。保留原因：①排程還沒抓過的當週資料
    # 想先手動補上 ②萬一自動化路徑哪天被擋，還有備援。
    with st.expander("📊 千張大戶（備用：手動上傳CSV）", expanded=False):
        st.caption("千張大戶現在預設由排程自動處理——每週六台灣時間10:00，"
                  "system_scheduler.py 會自動向TDCC官方要當週資料、算比例、"
                  "存進歷史，不用你手動做這件事。")
        st.caption("這裡是備用入口，兩種情況才需要用：①想先手動補上「這週排程"
                  "還沒跑」的最新資料；②排程萬一因為官方網址改版而失效時的備援。")
        st.markdown("下載處：[opendata.tdcc.com.tw/getOD.ashx?id=1-5]"
                   "(https://opendata.tdcc.com.tw/getOD.ashx?id=1-5)"
                   "（瀏覽器打開會直接下載CSV，每週六早上更新，只保留當週最新一份，"
                   "沒辦法回溯抓取已經過去的週次）")
        _th_file = st.file_uploader("拖曳集保戶股權分散表CSV", type=['csv'], key="tdcc_holding_csv")
        _th_week = st.date_input("這份資料是哪一週的？（存進歷史用，預設今天）",
                                 value=datetime.now(TAIPEI_TZ).date(), key="tdcc_week_date")
        if _th_file is not None and st.button("💾 解析並存入千張大戶歷史",
                                              use_container_width=True, key="tdcc_holding_save"):
            _th_df = parse_tdcc_holding_csv(_th_file.read())
            if _th_df is None or _th_df.empty:
                st.warning("⚠️ 解析失敗——請確認這份CSV是集保結算所股權分散表原始檔案，"
                          "沒有被Excel等軟體另存新檔改過編碼或欄位。")
            else:
                _th_ratios = compute_big_holder_ratios(_th_df)
                _th_small_ratios = compute_small_holder_ratios(_th_df)  # 【R90新增】
                _th_saved = sb_log_big_holder_weekly(_th_ratios, _th_week.strftime('%Y-%m-%d'),
                                                     small_ratios=_th_small_ratios)
                if _th_saved:
                    st.success(f"✅ 已存入 {_th_saved} 檔股票的千張大戶＋散戶比例（{_th_week}）。"
                              f"累積到3週以上，戰卡就會開始顯示趨勢。")
                else:
                    st.warning("寫入失敗（Supabase未連線？或尚未執行 "
                              "supabase_migration_r69_big_holder.sql 建立 big_holder_weekly 表）")

        st.divider()
        # 【R86新增】手動回補歷史——TDCC官方查詢頁面自動抓取會撞CSRF驗證，
        # 但真人瀏覽手動查是合法的。給快速輸入介面，一口氣補齊過去幾週歷史。
        st.markdown("**⚡ 快速手動回補單一股票的歷史比例**")
        st.caption("去 [tdcc.com.tw官方查詢頁]"
                  "(https://www.tdcc.com.tw/portal/zh/smWeb/qryStock) 手動查詢過去某一週"
                  "的股權分散表，把「1000張以上」那個級距的百分比抄過來這裡——"
                  "這是你自己動手查、自己打字輸入，不是程式自動抓取，不受CSRF限制。"
                  "想快速補齊過去5-10週歷史的話，重複這個動作幾次即可。")
        _bf_col1, _bf_col2, _bf_col3 = st.columns(3)
        _bf_code = _bf_col1.text_input("股票代號", key="bh_manual_code", placeholder="例如 2330")
        _bf_ratio = _bf_col2.number_input("千張大戶比例(%)", min_value=0.0, max_value=100.0,
                                          step=0.01, key="bh_manual_ratio")
        _bf_date = _bf_col3.date_input("這是哪一週的資料", key="bh_manual_date")
        if st.button("💾 存入這一筆歷史", key="bh_manual_save", use_container_width=True):
            if not _bf_code.strip():
                st.warning("請輸入股票代號。")
            else:
                _bf_saved = sb_log_big_holder_weekly({_bf_code.strip(): _bf_ratio},
                                                     _bf_date.strftime('%Y-%m-%d'))
                if _bf_saved:
                    st.success(f"✅ 已存入 {_bf_code.strip()} 在 {_bf_date} 這週的比例"
                              f"（{_bf_ratio}%）。")
                else:
                    st.warning("寫入失敗（Supabase未連線？）")

    # 【R98續34新增，總指揮官指示：①要放在側欄最下面，不要卡在中間
    # ②上傳完要清楚顯示存到哪一季，且要能隨時查、不是只看一閃而過的
    # 成功訊息】改到整個側欄最後一個面板，並且新增「目前資料庫已有的
    # 季度」持續顯示區塊——不用等剛上傳完那一刻的st.success()訊息，
    # 隨時打開這個面板都查得到目前mops_financial_snapshot裡實際有哪些
    # 季度、各幾檔，徹底解決「忘記存到哪一季」這個問題。
    with st.expander("📥 MOPS財報批次上傳（自助上傳，避免消耗對話資源）", expanded=False):
        st.caption("流程：真人瀏覽器打開 https://mops.twse.com.tw/mops/web/t163sb04 ，"
                  "選好市場別/民國年/季別查詢，把5~6張產業別表格分別存成CSV後，"
                  "直接拖進來這裡，系統會自動辨識產業別、解析、寫進資料庫——"
                  "不用再透過對話一批一批貼SQL。")
        _mops_csvs = st.file_uploader("拖曳MOPS財報CSV（可一次選多個檔案，一次通常是5~6個不同產業別）",
                                      type=['csv'], accept_multiple_files=True, key="mops_csv_up_v1")
        if _mops_csvs and is_admin() and st.button("🚀 批次解析並寫入 mops_financial_snapshot", use_container_width=True,
                                    key="mops_csv_process_btn"):
            process_mops_csv(_mops_csvs)
        elif _mops_csvs and not is_admin():
            st.caption("⚠️ 上傳寫入功能僅總指揮官權限可用，這裡目前是唯讀訪客身分。")

        st.divider()
        st.markdown("**📊 目前資料庫已有的季度**（隨時可查，不用擔心忘記存到哪一季）")
        if SUPABASE_CONN is None:
            st.caption("Supabase未連線，無法查詢。")
        else:
            try:
                _mops_all_rows = _sb_fetch_all("mops_financial_snapshot")
                if not _mops_all_rows:
                    st.caption("目前資料庫裡還沒有任何MOPS財報資料。")
                else:
                    _season_names2 = {1: '第一季', 2: '第二季', 3: '第三季', 4: '第四季'}
                    _mops_season_counts = {}
                    for _r in _mops_all_rows:
                        _key = (_r.get('year_roc'), _r.get('season'))
                        _mops_season_counts[_key] = _mops_season_counts.get(_key, 0) + 1
                    for (_yr, _sn), _cnt in sorted(_mops_season_counts.items(), reverse=True):
                        st.caption(f"民國{_yr}年{_season_names2.get(_sn, f'第{_sn}季')}：**{_cnt}** 檔")
            except Exception as _mq_e:
                st.caption(f"⚠️ 查詢失敗：{_mq_e}")


def render_portfolio_quickview():
    """
    【R98續104新增，總指揮官指示：要一個「類似戰情速覽方式」的輕量持倉表，
    不用切入總持倉（那個入口會拖慢整體載入速度），但要能直接改成本價／張數、
    也要看得到損益】

    背景：既有的「持倉總覽（可直接編輯張數／成本價）」(R98續77) 雖然功能對，
    但它的資料來源是展開總持倉那條路徑裡、對每一檔持倉都跑一次
    calculate_signals_worker()（完整技術評分＋籌碼查詢）＋
    attach_live_quotes(fetch_intraday_extras=True)（額外查VWAP/9:30三關）
    ——這是完整戰卡等級的重運算，總指揮官要看這張表，得先等這整套跑完，
    這正是「開了影響整體載入速度」的根因。

    這裡改成完全不依賴calculate_signals_worker：
      - 股票名稱：直接讀TW_STOCK_NAMES（開機時已經算好的全域字典，零成本）
      - 上市/上櫃：讀fetch_listed_only_codes()（6小時快取，既有資料，不用
        多打API）
      - 現價：只呼叫一次fetch_live_quotes_resilient()批次查（跟戰情速覽
        用的是同一套「TWSE MIS+重試+永豐金備援」邏輯，但這裡不疊加任何
        技術指標/籌碼查詢，是目前系統裡最輕量的批次報價方式）
      - 損益：calc_real_profit_v2()，純數學計算，零額外成本

    放在主畫面最上方（大將軍智慧HUD正下方），不用點開任何東西就看得到，
    完全獨立於「展開總持倉」那條路徑，兩者互不影響、可以同時存在。
    """
    _portfolio = st.session_state.get('portfolio', {})
    if not _portfolio:
        return

    with st.expander(f"💰 持倉速覽（輕量版，共{len(_portfolio)}檔，不用展開總持倉）", expanded=True):
        # 【R98續105修復，總指揮官實測反映：打字/改張數當下就卡約1分鐘】
        # 根因：st.data_editor只要有任何互動（打一個字、按+/-），Streamlit
        # 就會把整支script從頭重新執行一次——這是Streamlit的既有行為，不是
        # bug。原本這裡沒有做任何快取，代表「使用者純粹想改一格數字」這個
        # 動作，也會意外觸發一次全新的fetch_live_quotes_resilient()批次
        # 網路查詢，這才是卡住的真正原因（不是編輯本身慢，是編輯誤觸發了
        # 不必要的重新查詢）。
        #
        # 修法：把查到的報價存進session_state當快取，只有在①第一次開啟
        # ②持倉的股票清單真的變了（買新的/賣掉舊的）③超過TTL（60秒）
        # ④使用者明確按下「重新查詢」時，才真的打一次API；純粹編輯儲存格
        # 觸發的重新執行，直接複用快取，不再重新查詢。
        _PQ_CACHE_KEY = 'pq_live_quote_cache'
        _PQ_TTL_SECONDS = 60
        _pq_codes_set = frozenset(_portfolio.keys())
        _pq_cache = st.session_state.get(_PQ_CACHE_KEY)
        _pq_cache_valid = (
            _pq_cache is not None
            and _pq_cache.get('codes') == _pq_codes_set
            and (time.time() - _pq_cache.get('ts', 0)) < _PQ_TTL_SECONDS
        )

        _pq_force_refresh = st.button("🔄 重新查詢即時報價", key="pq_force_refresh_btn")

        if _pq_cache_valid and not _pq_force_refresh:
            _pq_live, _pq_diag = _pq_cache['live'], _pq_cache['diag']
        else:
            try:
                _listed_set = fetch_listed_only_codes()
            except Exception as _e:
                print(f"[持倉速覽-輕量版] fetch_listed_only_codes失敗，退回猜測：{type(_e).__name__}: {_e}")
                _listed_set = set()

            _pq_pairs = [(code, 'tse' if (code in _listed_set or not _listed_set) else 'otc')
                         for code in _pq_codes_set]
            try:
                _pq_live, _pq_diag = fetch_live_quotes_resilient(
                    _pq_pairs, shioaji_api_key=SHIOAJI_API_KEY, shioaji_secret_key=SHIOAJI_SECRET_KEY)
                st.session_state[_PQ_CACHE_KEY] = {
                    'codes': _pq_codes_set, 'live': _pq_live, 'diag': _pq_diag, 'ts': time.time()}
            except Exception as _e:
                st.caption(f"⚠️ 即時報價查詢失敗，暫時無法顯示持倉速覽：{type(_e).__name__}: {_e}")
                return

        _pq_age = time.time() - st.session_state.get(_PQ_CACHE_KEY, {}).get('ts', time.time())
        st.caption(f"報價快取於 {_pq_age:.0f} 秒前查詢（{_PQ_TTL_SECONDS}秒內編輯不會重新查詢，"
                  f"避免每次改數字都卡住；想看最新價再按上面「重新查詢」）")

        # 【R98續108新增，總指揮官指示：方向欄位改顯示當下評分比多/空更清楚】
        # verdict徽章(🔥建議進攻/🟡觀望偏多/🔵建議撤退/⚠️轉弱警戒)的判斷邏輯
        # 完全只依賴卡片資料的signal_text欄位(見render_stock_card_ui())，
        # 而這份資料在戰情速覽(render_quick_overview)已經算好、存進
        # st.session_state['_qo_per_stock_cache']——因為持倉股票本來就
        # 有包含在_all_codes清單裡（標記"持倉"）。這裡直接讀這份既有快取，
        # 不重新呼叫任何評分函式，零額外運算/網路成本。
        def _verdict_badge(code):
            _card = st.session_state.get('_qo_per_stock_cache', {}).get(code)
            _sig_t = (_card or {}).get('signal_text', '') if _card else ''
            if '偏多攻擊' in _sig_t:   return '🔥 建議進攻'
            if '觀察偏多' in _sig_t:   return '🟡 觀望偏多'
            if '偏空防守' in _sig_t:   return '🔵 建議撤退'
            if '轉弱謹慎' in _sig_t:   return '⚠️ 轉弱警戒'
            return '（尚無評分）'   # 戰情速覽還沒算到這檔時的誠實標示，不瞎猜

        _pq_rows = []
        for code, p_data in _portfolio.items():
            _live_data = _pq_live.get(code) or {}
            _price = safe_float(_live_data.get('price', 0.0))
            _ent_p = safe_float(p_data.get('entry_price', 0.0))
            _side = p_data.get('side', 'long')
            _qty = safe_float(p_data.get('qty', 1))
            _profit, _roi = calc_real_profit_v2(_ent_p, _price, _qty, side=_side) if _price > 0 else (0, 0)
            _pq_rows.append({
                '代號': code, '名稱': TW_STOCK_NAMES.get(code, code),
                '多/空': '多' if _side == 'long' else '空',
                '當下評分': _verdict_badge(code),
                '現價': _price if _price > 0 else '查詢中',
                '成本價': _ent_p, '張數': _qty,
                '損益': round(_profit, 0) if _price > 0 else '—',
                '損益%': round(_roi, 2) if _price > 0 else '—',
            })

        if not _pq_rows:
            st.caption("目前沒有持倉。")
            return

        _pq_df = pd.DataFrame(_pq_rows)
        _pq_edited = st.data_editor(
            _pq_df, use_container_width=True, hide_index=True, key="pf_quickview_editor",
            disabled=['代號', '名稱', '多/空', '當下評分', '現價', '損益', '損益%'],
            column_config={
                '成本價': st.column_config.NumberColumn(format="%.2f", step=0.01),
                '張數': st.column_config.NumberColumn(format="%.0f", step=1),
            })
        if st.button("💾 儲存持倉速覽的修改（張數／成本價）", key="pf_quickview_save",
                     use_container_width=True):
            for _, _row in _pq_edited.iterrows():
                _code = _row['代號']
                if _code in st.session_state.portfolio:
                    st.session_state.portfolio[_code]['entry_price'] = float(_row['成本價'])
                    st.session_state.portfolio[_code]['qty'] = float(_row['張數'])
            save_local_db_isolated()
            st.success("✅ 已儲存持倉速覽的修改。")
            time.sleep(0.6)
            st.rerun()

        if _pq_diag and _pq_diag.get('mass_no_trade'):
            st.caption("⚠️ 這批報價的查無交易比例偏高，可能是盤前/收盤後時段，現價會顯示「查詢中」。")


# ==============================================================================
# 十一、 主畫面
# ==============================================================================
st.title("🚀 作戰室 正式版 v1.0")

# 【R97移動，總指揮官確認：開盤前最重要的兩塊資訊要放最上面】原本這兩個
# HUD區塊(大盤氣象+隔夜總經)排在9:30三關查詢/候選池/勝率報表這些面板
# 之後，開盤前該先看的東西反而要往下滑才看得到。這裡搬到標題正下方，
# 一打開畫面就先看到「今天大盤/隔夜美股是什麼氣氛」，再往下看細節面板。
# 這兩塊依賴的MARKET_REGIME/weather_color/weather_str/get_overnight_macro()
# 都是模組層級就算好的全域資料，跟下面config_payload無關，往前搬不影響
# 任何計算順序。
# 【R65修復】原本「站上20MA」是綠色、「跌破20MA」是紅色——這跟整個app其餘地方
# 「紅漲綠跌」的台股慣例是反的(紅色在這個app裡代表「漲/多方」，不是危險警示)。
# 總指揮官反映「跌破或恐慌的，就要用綠色的」，這裡跟下面的_gate_color一起對調。
_regime_badge = ("<span style='color:#ff4d4d;'>站上20MA</span>" if MARKET_REGIME['bull']
                 else "<span style='color:#00c853;'>跌破20MA·多方訊號降級</span>") if MARKET_REGIME['known'] else "<span style='color:#888;'>資料源異常，暫時無法判斷（非持續計算中，5分鐘後自動重試）</span>"
st.markdown(f"""<div class='hud-box'>
    <div style='color:#f1c40f; font-size:16px; font-weight:bold; margin-bottom:4px;'>📊 大將軍智慧 HUD 總覽</div>
    <div style='color:#ddd; font-size:14px;'><b>大盤氣象：</b> <span style='color:{weather_color}; font-weight:bold;'>上市大盤 {weather_str}</span> | <b>位階濾網：</b> {_regime_badge}</div>
</div>""", unsafe_allow_html=True)

# 【V160 A階段】隔夜總經 HUD：台股先行指標
_macro = get_overnight_macro()
_macro_chips = []
for _name in ('那斯達克', '標普500', '費城半導體', '那斯達克期貨', '標普期貨', '台積電ADR', '聯電ADR', '美元台幣'):
    _d = _macro.get(_name, {})
    if _d.get('ok'):
        _mc = "#ff4d4d" if _d['pct'] > 0 else ("#00c853" if _d['pct'] < 0 else "#999")
        _val_fmt = f"{_d['value']:,.2f}" if _name in ('美元台幣', '台積電ADR', '聯電ADR') else f"{_d['value']:,.0f}"
        _pt = _d.get('pt_change', 0)
        _pt_fmt = f"{abs(_pt):,.2f}" if _name in ('美元台幣', '台積電ADR', '聯電ADR') else f"{abs(_pt):,.0f}"
        _arrow = "▲" if _pt > 0 else ("▼" if _pt < 0 else "▬")
        # 【R98新增】is_carried=True代表這是Yahoo限流時用app_data_cache遞補的
        # 最後成功值，不是這次真的抓到的——加🧊標記誠實提示（跟即時報價沿用
        # 同一個冰塊圖示語意），避免使用者誤以為是這一刻的即時數字。
        _carry_tag = "🧊" if _d.get('is_carried') else ""
        # 【R98續新增】用ETF代理指數時加📐標記，誠實提示這不是原始指數，
        # 是QQQ/SPY/SOXX這些高度追蹤該指數的ETF漲跌%（見_fetch_finnhub
        # 說明），跟🧊(舊資料遞補)是不同維度的誠實標示，可能同時出現。
        _proxy_tag = "📐" if _d.get('is_etf_proxy') else ""
        _macro_chips.append(f"<span style='margin-right:14px;'><b>{_name}</b> {_carry_tag}{_proxy_tag}{_val_fmt} "
                            f"<span style='color:{_mc};'>({_arrow}{_pt_fmt} | {_d['pct']:+.2f}%)</span></span>")
    else:
        _note = _d.get('note', '連線中')
        _macro_chips.append(f"<span style='margin-right:14px; color:#9fb3c8;'><b>{_name}</b> {_note}</span>")
_gate_status, _gate_reason = evaluate_overnight_gate(_macro, market_bull=MARKET_REGIME.get('bull', True))
# 【V160 R43 更新】三態顏色對應：黃=對沖模式(中性)不變。
# 【R65修復】原本 bull(多頭順風)=綠、panic(恐慌熔斷)=紅，跟app其餘地方紅漲綠跌
# 的慣例相反，這裡對調：bull(看漲)=紅、panic(看跌/恐慌)=綠。
_gate_color = {"bull": "#ff4d4d", "hedge": "#ffab00", "panic": "#00c853"}.get(_gate_status, "#888")
# 【V160 簡化】日期只在標題後面標一次（美股系列共用同一個收盤日，不用每個指標各標一次，
# 避免畫面太擁擠）；不再另外顯示「查看時間」，手機本身就有時鐘不需要重複。
_macro_date = _macro.get('那斯達克', {}).get('data_date', '')
# 【V160 修復】原本 #666 在深色背景上幾乎看不見（總指揮官回報「文字不明顯、顏色太淺」），
# 提亮到 #9fb3c8 並加大一級字，仍維持次要資訊的視覺層級、不搶主指標的注意力。
_date_tag = f"<span style='color:#ffd479; font-size:13px; font-weight:600;'>（美股 {_macro_date} 收盤）</span>" if _macro_date else ""
st.markdown(f"""<div class='hud-box' style='margin-top:-4px;'>
    <div style='color:#7ab8ff; font-size:14px; font-weight:bold; margin-bottom:4px;'>🌙 隔夜總經 {_date_tag} <span style='color:{_gate_color}; font-size:12px;'>｜開盤前閘門：{_gate_reason}</span></div>
    <div style='color:#666; font-size:11px; margin-bottom:2px;'>📐=ETF代理指數(非原始指數本身) 🧊=Yahoo限流時的最後成功值遞補</div>
    <div style='color:#ddd; font-size:13px;'>{''.join(_macro_chips)}</div>
</div>""", unsafe_allow_html=True)

# 【R98續80新增，總指揮官指示：隔夜自動分析報告，跟總指揮官自己手動
# 查詢的部分完全分開】收盤後排程(stage_nightly_analysis_report)寫進
# nightly_analysis_report表，這裡限時顯示：run_date那次的排程結果，
# 只在「隔天08:30前」可見，過了08:30就自動隱藏(但資料庫不刪除，仍
# 保留歷史)，除非總指揮官按了「加入永久保存」。用淡藍色系(延續下面
# 「隔夜總經」區塊的既有配色，兩者都是「收盤後自動產生」的資訊，視覺
# 風格保持一致，但明確用獨立的框線+標題跟總指揮官自己手動查詢的區塊
# 區隔開)。不放在任何nav_section判斷底下，不管切到哪個分類都看得到，
# 才符合「傍晚統一看到」的直覺期待。
if SUPABASE_CONN is not None:
    try:
        _now_taipei = datetime.now(TAIPEI_TZ)
        _nr_res = (SUPABASE_CONN.table("nightly_analysis_report")
                  .select("*").order("run_date", desc=True).order("created_at", desc=True)
                  .limit(50).execute())
        _nr_rows = _nr_res.data or []
        _nr_visible = []
        for r in _nr_rows:
            if r.get("saved_permanently"):
                _nr_visible.append(r)
                continue
            try:
                _run_dt = datetime.strptime(r["run_date"], "%Y-%m-%d").date()
                _cutoff = TAIPEI_TZ.localize(datetime.combine(
                    _run_dt + timedelta(days=1), datetime.min.time().replace(hour=8, minute=30)))
                if _now_taipei < _cutoff:
                    _nr_visible.append(r)
            except (ValueError, TypeError):
                continue

        if _nr_visible:
            st.markdown(
                f"""<div style="border:2px solid #7ab8ff; border-radius:10px; padding:14px; """
                f"""background:#0f1620; margin-bottom:14px;">"""
                f"""<div style='color:#7ab8ff; font-size:15px; font-weight:bold; margin-bottom:8px;'>"""
                f"""🌙 隔夜自動分析報告（{_nr_visible[0]['run_date']}收盤後排程產出，"""
                f"""隔天08:30前限時顯示，不是即時資料）</div></div>""", unsafe_allow_html=True)
            for r in _nr_visible:
                with st.expander(f"{r['section_title']}（{r['run_date']}）"
                                 + ("　📌已永久保存" if r.get("saved_permanently") else ""),
                                 expanded=False):
                    st.markdown(r["content_markdown"])
                    if not r.get("saved_permanently"):
                        if st.button("📌 加入永久保存（不會在08:30後消失）",
                                    key=f"nr_save_{r['id']}", use_container_width=True):
                            SUPABASE_CONN.table("nightly_analysis_report").update(
                                {"saved_permanently": True}).eq("id", r["id"]).execute()
                            st.success("已永久保存，不會再自動隱藏。")
                            st.rerun()
    except Exception as _nr_e:
        print(f"[隔夜分析報告-診斷] 查詢/顯示失敗：{type(_nr_e).__name__}: {_nr_e}")

# 【R98續95新增，總指揮官指示：隔夜自動掃描系統UI(查X指令排程化)】
# 跟上面的nightly_analysis_report是兩個不同系統(那個是文字分析報告，
# 這個是股票篩選掃描結果)——刻意用不同的視覺語言(淡紫色系，跟上面的
# 淡藍色/大盤黃色都不同)明確區隔，不管切到哪個nav_section分類都看
# 得到。時間窗口比照08:50這個更早的cutoff(比nightly_analysis_report
# 的08:30再晚20分鐘，因為總指揮官原始要求是08:50)。
if SUPABASE_CONN is not None:
    try:
        # 【R98續107修復，總指揮官實測反映：改持倉速覽的價格還是卡40秒】
        # 根因追查：這整個隔夜掃描區塊放在主畫面最上層、沒有被任何按鈕
        # 保護，Streamlit只要頁面上「任何地方」有互動觸發重新執行
        # （不管是不是持倉速覽本身），這裡都會跟著重新查一次Supabase
        # ——這個問題原本就存在（overnight_scan_results一直沒快取），
        # 這次新增的歷史命中率查詢（filter_backtest_weekly_results）
        # 又疊加了一次，兩個一起造成明顯延遲。
        #
        # 隔夜掃描資料本來就是一天只更新一次（收盤後排程寫入），歷史
        # 命中率是一週才更新一次——用5分鐘TTL快取完全安全，不會看到
        # 過期資料，卻能讓「編輯持倉速覽」這類跟這個區塊完全無關的互動
        # 不再意外觸發重新查詢。
        _OS_CACHE_KEY = 'os_scan_and_winrate_cache'
        _OS_TTL_SECONDS = 300
        _os_cache = st.session_state.get(_OS_CACHE_KEY)
        if _os_cache and (time.time() - _os_cache.get('ts', 0)) < _OS_TTL_SECONDS:
            _os_rows = _os_cache['os_rows']
            _win_rate_map = _os_cache['win_rate_map']
        else:
            _os_res = (SUPABASE_CONN.table("overnight_scan_results")
                      .select("*").order("scan_date", desc=True)
                      .order("score", desc=True).limit(200).execute())
            _os_rows = _os_res.data or []

            _win_rate_map = {}
            try:
                _fb_res = (SUPABASE_CONN.table("filter_backtest_weekly_results")
                          .select("filter_name,sample_count,win_rate_3d,avg_return_3d,run_date")
                          .order("id", desc=True).limit(500).execute())
                for _fb_r in (_fb_res.data or []):
                    _fn = _fb_r.get("filter_name")
                    if _fn and _fn not in _win_rate_map:   # 已按id desc排序，第一筆就是最新
                        _win_rate_map[_fn] = _fb_r
            except Exception as _fb_e:
                print(f"[隔夜掃描-歷史命中率] 查詢filter_backtest_weekly_results失敗，"
                      f"不影響掃描結果本體顯示：{type(_fb_e).__name__}: {_fb_e}")

            st.session_state[_OS_CACHE_KEY] = {
                'os_rows': _os_rows, 'win_rate_map': _win_rate_map, 'ts': time.time()}

        # 【以下維持原本邏輯】時間窗口判斷用即時的now()，不放進快取裡，
        # 確保08:50這個cutoff還是精準的，不會因為用了快取資料而算錯。
        _now_taipei_os = datetime.now(TAIPEI_TZ)
        _os_visible = []
        for r in _os_rows:
            try:
                _run_dt = datetime.strptime(r["scan_date"], "%Y-%m-%d").date()
                _cutoff = TAIPEI_TZ.localize(datetime.combine(
                    _run_dt + timedelta(days=1), datetime.min.time().replace(hour=8, minute=50)))
                if _now_taipei_os < _cutoff:
                    _os_visible.append(r)
            except (ValueError, TypeError):
                continue

        if _os_visible:
            _os_scan_date = _os_visible[0]['scan_date']
            _existing_radar = set(st.session_state.get('pinned_stocks', {}).keys()) | \
                              set(st.session_state.get('observe_stocks', {}).keys())

            def _win_rate_badge(cmd):
                """回傳單一查X條件的歷史命中率小字串，查無資料或樣本<10時誠實標註。"""
                _wr = _win_rate_map.get(cmd)
                if not _wr:
                    return "尚無回測資料"
                if _wr.get("sample_count", 0) < 10:
                    return f"樣本僅{_wr['sample_count']}筆，不足採信"
                return f"3日勝率{_wr['win_rate_3d']}%（{_wr['sample_count']}筆，均報酬{_wr['avg_return_3d']:+.2f}%）"

            st.markdown(
                f"""<div style="border:2px solid #b48eff; border-radius:10px; padding:14px; """
                f"""background:#170f22; margin-bottom:14px;">"""
                f"""<div style='color:#b48eff; font-size:15px; font-weight:bold; margin-bottom:8px;'>"""
                f"""🔮 隔夜自動掃描（{_os_scan_date}收盤後排程掃描全市場，"""
                f"""隔天08:50前限時顯示，跟你自己手動查詢的結果完全分開）</div></div>""",
                unsafe_allow_html=True)

            def _render_scan_row_table(rows, key_prefix):
                """把一批掃描結果組成表格+逐列加入雷達按鈕。"""
                _tbl_rows = []
                for r in rows:
                    _in_radar = r['symbol'] in _existing_radar
                    _cmds = r.get('matched_commands', [])
                    _tbl_rows.append({
                        '代號': r['symbol'], '現價': r.get('price'),
                        '評分': r.get('score'),
                        '命中條件': '+'.join(c.replace('查', '') for c in _cmds),
                        '歷史3日勝率': ' ｜ '.join(_win_rate_badge(c) for c in _cmds),
                        '狀態': '✅已在雷達中' if _in_radar else '',
                    })
                st.dataframe(pd.DataFrame(_tbl_rows), use_container_width=True, hide_index=True)
                _not_in_radar = [r['symbol'] for r in rows if r['symbol'] not in _existing_radar]
                if _not_in_radar:
                    _picked = st.multiselect(f"選擇要加入雷達的股票", _not_in_radar,
                                             default=[], key=f"{key_prefix}_pick")
                    if st.button(f"➕ 加入雷達（已選{len(_picked)}檔）", key=f"{key_prefix}_add",
                                use_container_width=True, disabled=not _picked):
                        old = st.session_state.get('pinned_stocks', {})
                        new_dict = {c: "隔夜自動掃描" for c in _picked}
                        for c, v in old.items():
                            if c not in new_dict:
                                new_dict[c] = v
                        st.session_state['pinned_stocks'] = new_dict
                        save_local_db_isolated()
                        for c in _picked:
                            log_watchlist_entry(c, "overnight_scan")
                        st.success(f"✅ 已加入常態雷達：{', '.join(_picked)}")
                        time.sleep(0.6)
                        st.rerun()

            # 重疊區：命中2個以上條件的股票，訊號較強，優先呈現
            _overlap_rows = [r for r in _os_visible if len(r.get('matched_commands', [])) >= 2]
            _single_rows = [r for r in _os_visible if len(r.get('matched_commands', [])) < 2]

            if _overlap_rows:
                with st.expander(f"🔥 重疊區（命中2個以上條件，訊號較強，共{len(_overlap_rows)}檔）", expanded=True):
                    _render_scan_row_table(_overlap_rows, "os_overlap")

            # 依單一查X條件分類顯示
            # 【R98續105新增】依歷史3日勝率高到低排序（樣本<10筆的排最後），
            # 標題直接帶出勝率，不用點開才知道——這是P1-1的核心價值：讓你
            # 知道該優先看哪個條件的結果，不是每個條件平等對待。
            _by_command = {}
            for r in _single_rows:
                for cmd in r.get('matched_commands', []):
                    _by_command.setdefault(cmd, []).append(r)

            def _cmd_sort_key(item):
                _cmd, _rows = item
                _wr = _win_rate_map.get(_cmd)
                if not _wr or _wr.get("sample_count", 0) < 10:
                    return (1, 0)   # 樣本不足，排最後
                return (0, -_wr["win_rate_3d"])   # 勝率高的排前面

            for cmd, rows in sorted(_by_command.items(), key=_cmd_sort_key):
                _badge = _win_rate_badge(cmd)
                with st.expander(f"{cmd}（{len(rows)}檔｜歷史{_badge}）", expanded=False):
                    _render_scan_row_table(rows, f"os_{cmd}")
    except Exception as _os_e:
        print(f"[隔夜自動掃描-診斷] 查詢/顯示失敗：{type(_os_e).__name__}: {_os_e}")

# 【R96新增】時段自動選關(Step 4)——依台灣現在時間提示該看時間軸的哪一
# 關，只在主畫面頂部顯示一次。available=False老實標注「還沒接上」。
if nav_section == "盤中作戰":
    try:
        _gate_info = determine_active_intraday_gate()
        if _gate_info['gate'] not in ('closed',):
            _gate_color = "#00e676" if _gate_info['available'] else "#888"
            # 【R97續23修復，總指揮官要求：長說明改浮動標籤，頁面更整齊】
            # note維持簡短、只講結論，詳細規則說明(如果gate有提供tooltip
            # 欄位)改用既有的.m-tooltip系統做成ⓘ浮動提示，平常不佔頁面
            # 空間，需要時hover/點擊才看得到，不強迫每個人每次都看一長串。
            _gate_tip = _gate_info.get("tooltip", "")
            _gate_tip_html = (f"<span class='m-tooltip'>ⓘ<span class='m-tooltiptext'>"
                             f"{_gate_tip}</span></span>" if _gate_tip else "")
            st.markdown(f'<div style="font-size:13px; color:#aaa; margin-bottom:8px;">'
                        f'⏱️ 當日續抱時間軸：<strong style="color:{_gate_color};">{_gate_info["label"]}</strong>'
                        f' —— {_gate_info["note"]} {_gate_tip_html}</div>', unsafe_allow_html=True)

            # 【R97新增，總指揮官要求：時間軸不該只是導覽指標，要直接彙整
            # 顯示判斷結果】只在「盤中即時：五檔掛單節奏」這個時段(intraday，
            # 10:15-13:00)顯示——把候選池+持倉+雷達清單的五檔判斷彙整成一張
            # 小表，不用逐一點開每張戰卡才看得到。
            if _gate_info['gate'] == 'intraday' and SUPABASE_CONN is not None:
                _tl_date = get_current_or_last_trading_date()
                _tl_symbols = set(st.session_state.get('portfolio', {}).keys()) \
                    | set(st.session_state.get('pinned_stocks', {}).keys())
                try:
                    _tl_pool = (SUPABASE_CONN.table("intraday_candidate_pool")
                               .select("symbol").eq("trade_date", _tl_date).execute())
                    _tl_symbols |= {r['symbol'] for r in (_tl_pool.data or [])}
                except Exception:
                    pass
                if _tl_symbols:
                    try:
                        # 【R97續16修復，總指揮官實測：11:40盤中戰情速覽/五檔彙整表
                        # 大量顯示昨天13:30收盤舊資料，重新整理也沒用】根因查證：
                        # 證交所mis.twse.com.tw即時報價端點有「5秒內最多3次請求，
                        # 超過會暫時鎖IP」的限制（社群長期驗證的公開資訊）。這裡
                        # 原本是全站唯一一處「完全沒有快取、直接打fetch_twse_
                        # mis_batch」的地方——Streamlit每次使用者互動（勾選/展開/
                        # 切換分頁）都會整支重跑一次，五檔彙整表只要在盤中掛著，
                        # 使用者正常操作幾下畫面，很容易就在幾秒內打好幾次這個
                        # 端點，疊加戰情速覽/大盤氣象HUD同時也在打，合計輕易超過
                        # 「5秒3次」的門檻，一旦被鎖，這次session剩下的時間即使
                        # 按重新整理也沒用（鎖的是IP，不是快取，清快取無效），
                        # 於是所有面板一起卡在鎖IP前最後一次成功抓到的舊資料
                        # （這解釋了為什麼戰情速覽也一起停在昨天13:30）。
                        #
                        # 這裡改走跟attach_live_quotes同一個15秒共用快取
                        # (_get_live_quotes_cached)——多個面板在同一次/相近的
                        # rerun裡，只要查的是同一批代號，就只會真的打一次
                        # TWSE，不會各自獨立發送請求。另外原本用「tse+otc各查
                        # 一次」暴力猜測法，等於每檔股票的請求量直接翻倍，這裡
                        # 改用跟attach_live_quotes一致的fetch_listed_only_codes()
                        # 精確判斷，一檔只查一次，從源頭降低這個端點的呼叫量。
                        try:
                            _tl_listed = fetch_listed_only_codes()
                        except Exception:
                            _tl_listed = set()
                        if _tl_listed:
                            _tl_pairs = [(s, "tse" if s in _tl_listed else "otc") for s in _tl_symbols]
                        else:
                            _tl_pairs = [(s, 'tse') for s in _tl_symbols] + [(s, 'otc') for s in _tl_symbols]
                        _tl_quotes = _get_live_quotes_cached(tuple(sorted(_tl_pairs)))
                        # 【R97續16新增，診斷】方便下次若又發生「大量沒回應」時，
                        # 一眼判斷是不是又被鎖IP（症狀：這裡查的symbol數量正常，
                        # 但_tl_quotes回來的數量遠少於預期）。
                        if _tl_symbols and len(_tl_quotes) < len(_tl_symbols) * 0.5:
                            print(f"[五檔彙整-診斷] 查{len(_tl_symbols)}檔，只回應{len(_tl_quotes)}檔"
                                  f"（<50%），若非開盤前/剛開盤，可能是TWSE MIS端點暫時限流。")
                        # 【R97續4新增，總指揮官要求：彙整表也要做到full判斷，不用
                        # 逼使用者跳去個股戰卡】跟attach_live_quotes同一套做法——
                        # 一次IN查詢批次拿這批股票今天的5分K，逐檔加總outer_volume/
                        # inner_volume，傳給evaluate_order_book_pressure就能升級成
                        # full判斷(真買/偷出貨)，不用逐檔查、不多花額外API成本。
                        _tl_outer_inner = {}
                        if SUPABASE_CONN is not None:
                            try:
                                _tl_bars_res = (SUPABASE_CONN.table("intraday_5min_bars")
                                                .select("symbol,outer_volume,inner_volume")
                                                .eq("trade_date", _tl_date)
                                                .in_("symbol", list(_tl_symbols))
                                                .execute())
                                for _row in (_tl_bars_res.data or []):
                                    _s = _row['symbol']
                                    _o, _i = _tl_outer_inner.get(_s, (0.0, 0.0))
                                    _tl_outer_inner[_s] = (_o + float(_row.get('outer_volume') or 0),
                                                           _i + float(_row.get('inner_volume') or 0))
                            except Exception as _tl_bars_e:
                                print(f"[五檔彙整-外內盤] 批次查詢5分K失敗（退回僅厚度判斷）：{_tl_bars_e}")
                        _tl_rows = []
                        for _sym in sorted(_tl_symbols):
                            _q = _tl_quotes.get(_sym)
                            if not _q:
                                continue
                            _outer_sum, _inner_sum = _tl_outer_inner.get(_sym, (None, None))
                            _ob = evaluate_order_book_pressure(_q.get('bids', []), _q.get('asks', []),
                                                               outer_volume=_outer_sum, inner_volume=_inner_sum)
                            if _ob.get('verdict') == 'unknown':
                                continue
                            _ob_label = _ob.get('label', '')
                            if _ob.get('data_completeness') == 'full':
                                _ob_label += '（已確認）'
                            # 【R97續23簡化】partial情境的label本身已經改成白話
                            # (「買方掛單較多（未確認真買）」)，不用再額外加後綴，
                            # 避免文字重複又佔版面。
                            _tl_rows.append({'代號': _sym, '名稱': TW_STOCK_NAMES.get(_sym, _sym),
                                             '現價': _q.get('price'), '五檔判斷': _ob_label})
                        if _tl_rows:
                            st.dataframe(pd.DataFrame(_tl_rows), use_container_width=True, hide_index=True,
                                        height=min(300, 40 + 35 * len(_tl_rows)))
                            st.markdown("<span class='m-tooltip' style='font-size:12px; color:#888;'>"
                                      "ⓘ「未確認」是什麼意思？"
                                      "<span class='m-tooltiptext'>只判斷掛單厚不厚，還沒確認成交是打在"
                                      "買價還是賣價，可能是假買盤真出貨。標「已確認」的才是真的疊加過"
                                      "內外盤成交比率驗證，可信度較高。詳細判斷請點開個股戰卡"
                                      "「五檔買盤結構」。</span></span>", unsafe_allow_html=True)
                        else:
                            st.caption("目前持倉/雷達/候選池標的都還沒有可用的五檔資料，稍後重新整理再看。")
                    except Exception as _tl_e:
                        st.caption(f"五檔彙整查詢失敗（不影響其他功能）：{_tl_e}")
    except Exception:
        pass   # 時段提示是輔助資訊，任何例外都不該影響主畫面正常顯示

    # 【R96新增，累積清單第4項】漲幅榜族群性市場regime閘門——每天算一次
    # 漲幅榜前10名是否集中同一族群，只顯示一次不用每張卡重複算。
    try:
        _gainers = fetch_market_gainers_with_industry()
        _concentration = evaluate_market_gainer_concentration(_gainers)
        if _concentration['verdict'] != 'unknown':
            _conc_color = "#ff4d4d" if _concentration['verdict'] == 'concentrated' else "#888"
            _conc_extra = (f"（{_concentration['dominant_industry']} {_concentration['dominant_count']}檔）"
                           if _concentration.get('dominant_industry') else "")
            st.markdown(f'<div style="font-size:13px; color:#aaa; margin-bottom:8px;">'
                        f'📊 今日族群性：<strong style="color:{_conc_color};">{_concentration["label"]}</strong>'
                        f'{_conc_extra}</div>', unsafe_allow_html=True)
    except Exception:
        pass   # 市場regime是輔助資訊，任何例外都不該影響主畫面正常顯示

    # 【R97移動，總指揮官確認：放在「今日族群性」下面】原本這個開關在檔案
    # 很後面（三關查詢/候選池/勝率報表三個面板之後），跟其他開盤前該先看的
    # 資訊分散在不同地方。移到這裡後，跟大盤氣象/隔夜總經/族群性排在一起，
    # 開盤前一次看完所有「先設定好」的東西，往下才是各種細節查詢面板。
    # 【V160 B#11】速覽模式開關
    # 【R50修復】預設改成True——常態持倉/模擬倉區塊原本不管展開收合都會執行
    # ThreadPoolExecutor平行運算，拖慢開機速度，改預設開速覽兼顧簡潔與速度。
    st.checkbox("⚡ 速覽模式：所有標的（持倉+雷達+觀察）攤平成一張總表，5秒掃完全部",
                value=st.session_state.get('quick_overview_mode', True), key="quick_overview_mode")

    # 【R96新增，「三關查詢」指令】掃描今天5分K三關(查15)判斷結果，只列出
    # 「通過」的股票，沒通過或還在等資料的一律不顯示。直接查intraday_gate_
    # results整張表篩verdict='pass'，這張表本來就只有持倉+雷達清單的資料。
if nav_section == "盤中作戰":
    with st.expander("🎯 9:30三關查詢（只列出通過的股票，10:00為最後檢查點）", expanded=False):
        if SUPABASE_CONN is None:
            st.caption("Supabase未連線，無法查詢三關結果。")
        else:
            try:
                _today_str_gate = get_current_or_last_trading_date()
                _gate_scan_res = (SUPABASE_CONN.table("intraday_gate_results")
                                  .select("symbol,direction,overall_verdict,overall_label,"
                                          "gate1_verdict,gate2_verdict,detail")
                                  .eq("trade_date", _today_str_gate)
                                  .eq("overall_verdict", "pass")
                                  .execute())
                _passed_rows = _gate_scan_res.data or []
                if not _passed_rows:
                    st.caption("目前沒有股票通過三關（可能是今天還沒到09:30，或今天沒有股票"
                              "同時通過第一、二關——這是正常情況，不代表查詢功能故障）。")
                else:
                    # 【R97修復】原本沒有選取direction、且下面用symbol當key組字典會
                    # 讓同一天同一檔股票的多方/空方兩筆結果互相覆蓋，只顯示其中一筆。
                    # 現在改成symbol+方向分開顯示，不會再靜默漏掉另一筆。
                    _display_rows = []
                    for r in _passed_rows:
                        _sym = r['symbol']
                        _dir = r.get('direction', 'long')
                        _display_rows.append({
                            '代號': _sym,
                            '名稱': TW_STOCK_NAMES.get(_sym, _sym),
                            '方向': '🔴多方' if _dir == 'long' else '🟢空方',
                            '結論': r.get('overall_label', ''),
                            '第一關': r.get('gate1_verdict', '—'),
                            '第二關': r.get('gate2_verdict', '—') or '（資料不足）',
                        })
                    st.dataframe(pd.DataFrame(_display_rows), use_container_width=True, hide_index=True)
                    st.caption(f"共 {len(_display_rows)} 檔通過。第三關（拉回體檢）目前輪詢窗口到10:00，"
                              "資料量仍有限，這裡的「通過」只涵蓋第一、二關確認過的部分，"
                              "第三關結果請個別點開完整戰卡查看當沖摘要區。空方目前只支援前兩關"
                              "（第三關反彈健康度尚未支援）。")
            except Exception as e:
                st.caption(f"查詢失敗：{e}（可能是尚未執行supabase_migration_r96_intraday_gate.sql建表）")

    # ==============================================================================
    # 【R97新增，見開發歷程.md】當沖候選池顯示 + 波段/當沖、自動/人工 勝率報表
    # ==============================================================================
if nav_section == "盤中作戰":
    with st.expander("🎯 持倉雷達當沖篩選（週轉率+系統A評分）", expanded=False):
        if SUPABASE_CONN is None:
            st.caption("Supabase未連線，無法查詢候選池。")
        else:
            try:
                _pool_date = get_current_or_last_trading_date()
                _pool_res = (SUPABASE_CONN.table("intraday_candidate_pool")
                            .select("symbol,direction,source,score,turnover_pct,overheated,note")
                            .eq("trade_date", _pool_date)
                            .execute())
                _pool_rows_ui = _pool_res.data or []
                if not _pool_rows_ui:
                    st.caption("今天候選池是空的（可能是排程還沒跑、或Stage2/補位掃描都沒有篩出"
                              "任何標的——這是正常情況，不代表功能故障）。")
                else:
                    _pool_display = []
                    for r in _pool_rows_ui:
                        _pool_display.append({
                            '代號': r['symbol'],
                            '名稱': TW_STOCK_NAMES.get(r['symbol'], r['symbol']),
                            '方向': '🔴多方' if r.get('direction') == 'long' else '🟢空方',
                            '來源': {'turnover_score': '週轉率+評分', 'momentum_supplement': '開盤補位'}
                                    .get(r.get('source'), r.get('source', '—')),
                            # 【R98續82修正，總指揮官反映「系統A評分顯示
                            # None卻依然列入候選」】查證確認不是bug，是
                            # 資料忠實反映：這些股票是透過「開盤補位」
                            # 機制進來的，不是透過系統A評分篩選，本來就
                            # 沒有這個分數。顯示英文"None"容易誤以為程式
                            # 出錯，改成更清楚的中文提示。
                            '系統A評分': r.get('score') if r.get('score') is not None else '（補位標的，無評分）',
                            '區間週轉率': f"{r.get('turnover_pct')}%" if r.get('turnover_pct') is not None else '—',
                            '過熱': '⚠️' if r.get('overheated') else '',
                            '備註': r.get('note', ''),
                        })
                    st.dataframe(pd.DataFrame(_pool_display), use_container_width=True, hide_index=True)
                    st.caption(f"共 {len(_pool_display)} 檔候選，這份名單會併入09:24-10:00的5分K三關輪詢"
                              "（跟持倉/雷達清單取聯集）。備註欄若出現「⚠️事件標記」代表命中十大"
                              "事件分類裡的標記類事件，供人工複核，不影響是否進候選池的判斷；"
                              "命中否決類事件（增資減資/募資計劃/經營權之爭併購/內部人買賣）的"
                              "標的已經被直接排除，不會出現在這份清單。")
            except Exception as e:
                st.caption(f"查詢失敗：{e}（可能是尚未執行supabase_migration_r97_intraday_auto_trading.sql建表）")

if nav_section == "策略回測":
    with st.expander("📊 勝率報表：波段 vs 當沖／自動 vs 人工", expanded=False):
        if SUPABASE_CONN is None:
            st.caption("Supabase未連線，無法查詢勝率報表。")
        else:
            try:
                _wr_res = (SUPABASE_CONN.table("system_portfolio")
                          .select("trade_type,trigger_source,side,status,realized_pnl,realized_roi")
                          .eq("status", "closed")
                          .execute())
                _wr_rows = _wr_res.data or []
                if not _wr_rows:
                    st.caption("目前沒有任何已平倉的紀錄可供統計（勝率報表只計算已結束的交易，"
                              "持倉中的部位不計入）。")
                else:
                    # 【R97新增】自動 vs 人工的判斷依據：trigger_source開頭是'scheduler_'
                    # 的是系統自動觸發（scheduler_signal=波段自動選股、
                    # scheduler_intraday=當沖自動執行），其餘（含None/空字串，代表
                    # 手動在網頁版操作或早期沒有這個欄位的舊資料）一律歸類「人工」。
                    def _classify_trigger(ts):
                        # 【R97修復】stage_signal寫入的是"scheduler"（沒有底線），
                        # stage_intraday_execute寫入的是"scheduler_intraday"——
                        # 兩種格式不一致，原本只判斷"scheduler_"開頭會漏判
                        # "scheduler"這個值，把波段自動選股誤歸類成人工。
                        # 改成只要以"scheduler"開頭就算自動，涵蓋兩種格式。
                        return "自動" if str(ts or "").startswith("scheduler") else "人工"

                    _stats = {}
                    for r in _wr_rows:
                        _tt = r.get("trade_type") or "swing"
                        _trig = _classify_trigger(r.get("trigger_source"))
                        _key = (_tt, _trig)
                        _s = _stats.setdefault(_key, {"count": 0, "win": 0, "pnl_sum": 0.0, "roi_sum": 0.0})
                        _s["count"] += 1
                        _pnl = r.get("realized_pnl") or 0
                        _roi = r.get("realized_roi") or 0
                        if _pnl > 0:
                            _s["win"] += 1
                        _s["pnl_sum"] += _pnl
                        _s["roi_sum"] += _roi

                    _report_rows = []
                    for (tt, trig), s in sorted(_stats.items()):
                        _win_rate = round(s["win"] / s["count"] * 100, 1) if s["count"] else 0
                        _avg_roi = round(s["roi_sum"] / s["count"], 2) if s["count"] else 0
                        _report_rows.append({
                            '模式': '波段' if tt == 'swing' else '當沖',
                            '觸發方式': trig,
                            '筆數': s["count"],
                            '勝率': f"{_win_rate}%",
                            '平均報酬率': f"{_avg_roi:+.2f}%",
                            '損益加總': round(s["pnl_sum"], 0),
                        })
                    st.dataframe(pd.DataFrame(_report_rows), use_container_width=True, hide_index=True)
                    st.caption(f"統計範圍：全部已平倉紀錄共 {len(_wr_rows)} 筆。「自動」指"
                              "trigger_source以scheduler_開頭的紀錄（波段自動選股/當沖自動執行）；"
                              "「人工」涵蓋網頁版手動操作，以及R97之前沒有這個欄位的舊資料"
                              "（無法區分是否為人工，保守歸類人工）。樣本數過少時（例如個位數）"
                              "勝率數字參考價值有限，建議累積更多交易紀錄後再下結論。")
            except Exception as e:
                st.caption(f"查詢失敗：{e}（可能是system_portfolio缺trigger_source/trade_type欄位，"
                          "需要先執行相關migration）")

    # 【R97續21新增，回測工作台第一版】用system_portfolio已經累積的322筆真實
    # 已平倉交易(見對話紀錄「回測工作台資料現況查證」)畫權益曲線/最大回撤，
    # 不是「用調整過的權重回測歷史因子表現」那種進階功能——factor_snapshot
    # 才剛建、還沒有歷史資料，勉強做那個只會是一條看起來很短、沒有參考價值
    # 的曲線。這裡務實地先把「已經真實發生過的交易」視覺化做好，資料是誠實
    # 的、有意義的，等factor_snapshot累積夠天數，同一個頁面架構可以直接
    # 加上「模擬套用新權重」的對比曲線，不用重做。
if nav_section == "策略回測":
    with st.expander("📈 回測工作台：權益曲線與最大回撤", expanded=False):
        st.caption("用system_portfolio已平倉的真實交易畫出來，不是模擬回測——這是"
                  "系統/您自己實際做過的每一筆交易，誠實反映到目前為止的表現。"
                  "（用調整過的因子權重回測「如果當初用不同權重會怎樣」是進階功能，"
                  "需要factor_snapshot累積更多天數才有意義，目前這張表剛上線，"
                  "還沒有足夠歷史，之後累積夠了會直接加進這個頁面。）")

        if SUPABASE_CONN is None:
            st.caption("Supabase未連線，無法查詢。")
        else:
            try:
                _bt_res = (SUPABASE_CONN.table("system_portfolio")
                          .select("symbol,side,trade_type,trigger_source,entry_date,exit_date,"
                                 "realized_pnl,realized_roi")
                          .eq("status", "closed").execute())
                _bt_rows = _bt_res.data or []
            except Exception as _bt_e:
                _bt_rows = []
                st.caption(f"查詢失敗：{_bt_e}")

            if not _bt_rows:
                st.info("目前沒有已平倉交易紀錄可供回測。")
            else:
                def _bt_classify_trigger(ts):
                    return "自動" if str(ts or "").startswith("scheduler") else "人工"

                # 篩選器——跟上面勝率報表用同一套分類邏輯，維度一致不會讓總指揮官
                # 看到兩邊數字對不上而困惑
                _bt_f1, _bt_f2, _bt_f3 = st.columns(3)
                with _bt_f1:
                    _bt_type_filter = st.selectbox("模式", ["全部", "波段", "當沖"], key="bt_type_filter")
                with _bt_f2:
                    _bt_trig_filter = st.selectbox("觸發方式", ["全部", "自動", "人工"], key="bt_trig_filter")
                with _bt_f3:
                    _bt_side_filter = st.selectbox("方向", ["全部", "做多", "做空"], key="bt_side_filter")

                _bt_filtered = []
                for r in _bt_rows:
                    _tt = "波段" if (r.get("trade_type") or "swing") == "swing" else "當沖"
                    _trig = _bt_classify_trigger(r.get("trigger_source"))
                    _side = "做多" if r.get("side") == "long" else "做空"
                    if _bt_type_filter != "全部" and _tt != _bt_type_filter:
                        continue
                    if _bt_trig_filter != "全部" and _trig != _bt_trig_filter:
                        continue
                    if _bt_side_filter != "全部" and _side != _bt_side_filter:
                        continue
                    _bt_filtered.append(r)

                if not _bt_filtered:
                    st.warning("目前篩選條件下沒有符合的交易紀錄，請放寬篩選。")
                else:
                    # 依出場日期排序（權益曲線要按時間累積，出場日期代表這筆交易
                    # 真正「實現」損益的時間點，比進場日期更適合當X軸）
                    _bt_filtered.sort(key=lambda r: r.get("exit_date") or r.get("entry_date") or "")

                    _cum_pnl = 0.0
                    _peak = 0.0
                    _max_drawdown = 0.0
                    _equity_curve = []
                    _wins = 0
                    _gross_profit = 0.0
                    _gross_loss = 0.0
                    for r in _bt_filtered:
                        _pnl = float(r.get("realized_pnl") or 0)
                        _cum_pnl += _pnl
                        _peak = max(_peak, _cum_pnl)
                        _dd = _peak - _cum_pnl
                        _max_drawdown = max(_max_drawdown, _dd)
                        _equity_curve.append({
                            "date": r.get("exit_date") or r.get("entry_date"),
                            "symbol": r.get("symbol"), "cum_pnl": _cum_pnl, "pnl": _pnl,
                        })
                        if _pnl > 0:
                            _wins += 1
                            _gross_profit += _pnl
                        elif _pnl < 0:
                            _gross_loss += abs(_pnl)

                    _n = len(_bt_filtered)
                    _win_rate = _wins / _n * 100 if _n else 0
                    _profit_factor = (_gross_profit / _gross_loss) if _gross_loss > 0 else float("inf")

                    _bt_m1, _bt_m2, _bt_m3, _bt_m4 = st.columns(4)
                    _bt_m1.metric("總損益", f"{_cum_pnl:+,.0f}")
                    _bt_m2.metric("勝率", f"{_win_rate:.1f}%")
                    _bt_m3.metric("獲利因子", f"{_profit_factor:.2f}" if _profit_factor != float("inf") else "∞")
                    _bt_m4.metric("最大回撤", f"{-_max_drawdown:,.0f}")
                    st.caption(f"共{_n}筆已平倉交易。獲利因子=總獲利÷總虧損，>1代表整體是賺的，"
                              "數字越大代表賺賠比越健康。最大回撤=權益曲線從波峰回落的最大金額。")

                    try:
                        import plotly.graph_objects as go
                        _dates = [e["date"] for e in _equity_curve]
                        _cums = [e["cum_pnl"] for e in _equity_curve]
                        _fig = go.Figure()
                        _fig.add_trace(go.Scatter(x=_dates, y=_cums, mode="lines+markers",
                                                 name="累積損益", line=dict(color="#2979ff", width=2)))
                        _fig.update_layout(title="權益曲線（依出場日期累積）",
                                          xaxis_title="出場日期", yaxis_title="累積損益",
                                          height=350, margin=dict(l=10, r=10, t=40, b=10))
                        st.plotly_chart(_fig, use_container_width=True)
                    except Exception as _bt_chart_e:
                        st.caption(f"圖表繪製失敗：{_bt_chart_e}")

                    with st.expander("查看逐筆交易明細", expanded=False):
                        _bt_detail_df = pd.DataFrame([
                            {"出場日期": e["date"], "代號": e["symbol"], "單筆損益": round(e["pnl"], 0),
                             "累積損益": round(e["cum_pnl"], 0)}
                            for e in _equity_curve
                        ])
                        st.dataframe(_bt_detail_df, use_container_width=True, hide_index=True)

    # 【V160 修復】config_payload 提前到這裡定義（原本放在檔案很後面，導致「系統自主選股」
    # 面板呼叫時 config_payload 還沒被賦值，觸發 NameError）。所需材料（enable_doomsday_lock、
    # enable_market_filter 等側邊欄開關）在上方側邊欄區塊已經賦值完成，這裡引用是安全的。
config_payload = {
    'token': get_active_fm_token(),
    'rev_override': st.session_state.revenue_override,
    'bh_override': st.session_state.bigholder_override,
    'div_override': st.session_state.dividend_override,
    'dividend_db': DIVIDEND_DB,
    'stock_names': TW_STOCK_NAMES,
    'pinned_stocks': st.session_state.pinned_stocks,
    'enable_doomsday': enable_doomsday_lock,
    'market_bull': (MARKET_REGIME['bull'] or not enable_market_filter),
}

# 【R97移動】大盤氣象HUD+隔夜總經已經搬到檔案最上面(標題正下方)，
# 速覽模式開關也已經搬到「今日族群性」下面，這裡都不再重複渲染。

# 【R97續11新增，路線2雙重確認追蹤面板】讀route2_watchlist（stage_
# route2_confirm_scan每天09:10寫入），純讀表不現場運算，速度不受影響。
# 不受gate時段限制，全天都顯示（跟五檔判斷彙整表那種盤中限定不同——
# 這是早上盤前確認的結果，收盤前都有參考價值）。
if nav_section == "盤中作戰":
    if SUPABASE_CONN is not None:
        with st.expander("🎯 波段候選：開盤驗證通過（雙重確認）", expanded=False):
            st.caption("條件：昨晚全市場波段評分達±6門檻 且 今天開盤後價格方向確實照劇本走 "
                      "且 週轉率≥2%（週轉率是最近一個已收盤交易日往前算10天，不是即時數字）。")
            try:
                _r2_date = get_current_or_last_trading_date()
                _r2_res = (SUPABASE_CONN.table("route2_watchlist").select("*")
                          .eq("trade_date", _r2_date).order("night_score", desc=True).execute())
                _r2_rows = _r2_res.data or []
            except Exception as _r2_e:
                _r2_rows = []
                st.caption(f"⚠️ 查詢失敗：{_r2_e}")

            if not _r2_rows:
                st.info("今天沒有股票同時通過波段評分+開盤確認+週轉率篩選，或今天stage_"
                       "route2_confirm_scan還沒執行。")
            else:
                for _r2 in _r2_rows:
                    _r2_sym = _r2["symbol"]
                    _r2_dir_label = "🔴多方" if _r2["direction"] == "long" else "🔵空方"
                    _r2_col1, _r2_col2 = st.columns([5, 1])
                    with _r2_col1:
                        st.markdown(f"**{_r2_sym} {TW_STOCK_NAMES.get(_r2_sym, '')}** ｜{_r2_dir_label}"
                                  f" ｜波段評分 {_r2['night_score']} ｜今日開盤 "
                                  f"{'+' if (_r2['today_gain_pct'] or 0) >= 0 else ''}{_r2['today_gain_pct']}% "
                                  f"｜週轉率 {_r2['turnover_pct']}%")
                    with _r2_col2:
                        if st.button("➕加入雷達", key=f"r2_pin_{_r2_sym}"):
                            if _r2_sym not in st.session_state.pinned_stocks:
                                st.session_state.pinned_stocks[_r2_sym] = "路線2雙重確認"
                                log_watchlist_entry(_r2_sym, "路線2雙重確認")
                                save_local_db_isolated()
                                st.success(f"✅ {_r2_sym} 已加入雷達")
                                time.sleep(0.5)
                                st.rerun()
                            else:
                                st.caption("已在雷達中")

                    # 【R98續16，總指揮官決策】戰卡展開後即使加了25秒硬性逾時
                    # 也還是不出資料——證實這條「盤中即時算完整戰卡」的路徑，
                    # 在目前的資料源(FinMind額度47%上限+yfinance限流)現實下就是
                    # 不可靠，硬留著只是給使用者一個永遠點不出東西的按鈕。依總
                    # 指揮官指示，這裡直接把「查看完整戰卡」整段拿掉，只保留
                    # 「加入雷達」——加入雷達之後，那些股票會走持倉/雷達區的
                    # 正常渲染路徑，那條路徑本來就有完整的即時報價/備援機制，
                    # 不受這裡的問題影響。等之後P0主線(compute_full_signal_for
                    # 徹底升級成TWSE MIS優先)完工、報價引擎變可靠後，再評估要
                    # 不要把這個即時戰卡入口加回來。

                    # 個股歷史觸發記錄（勝率+後續價格參考，不是嚴謹統計）
                    try:
                        _r2_hist_res = (SUPABASE_CONN.table("route2_watchlist").select("trade_date,night_score,today_gain_pct,direction")
                                        .eq("symbol", _r2_sym).lt("trade_date", _r2_date)
                                        .order("trade_date", desc=True).limit(5).execute())
                        _r2_hist = _r2_hist_res.data or []
                        if _r2_hist:
                            _r2_hist_lines = []
                            for _h in _r2_hist:
                                _r2_hist_lines.append(f"{_h['trade_date']}：波段{_h['night_score']}"
                                                      f"，觸發時開盤{_h['today_gain_pct']:+.2f}%")
                            st.caption(f"📜 歷史觸發記錄（僅供參考，樣本數少不代表統計顯著）：" + "／".join(_r2_hist_lines))
                    except Exception:
                        pass
                    st.divider()

    # 【R97續14新增，方案B：單一清單＋標籤式主力偵測面板；R97續15重大效能修復】
    # 讀smart_money_candidates（stage_smart_money_scan每天22:30寫入），純讀表。
    # 跟路線2面板平行放置，不混進候選池/戰情速覽裡。
    #
    # 排序邏輯是這個面板的核心：先按patterns陣列長度（符合幾個維度）由多到少
    # 排，同樣數量的再按週轉率高低排——符合越多維度排越前面，對應「多重確認、
    # 訊號疊加」的精神，不是隨便排序。
    #
    # 【R97續15重大效能修復，總指揮官實測：頁面卡20分鐘還在載入】根因是
    # Streamlit的`with st.expander(...)`區塊body「不管展開或收合，每次頁面
    # rerender都會完整執行一次」——expander只控制視覺顯示、不延遲程式執行。
    # 上一版把calculate_signals_worker（每檔都會打yfinance/FinMind/Supabase
    # 的完整戰卡運算）直接放進每一列的expander裡，主力偵測當天配對到231檔，
    # 等於一進頁面就同步跑231次完整戰卡運算，把整頁拖死。路線2面板用同樣
    # 寫法卻沒事，純粹因為路線2幾乎每天都是0~數檔（±6波段+開盤確認+週轉率
    # 三重篩選很嚴），那個latent bug從來沒被觸發到；主力偵測動輒200+檔，
    # 一次就引爆。
    #
    # 這裡修兩件事：
    # 1. 戰卡改「點擊才算」：不再把calculate_signals_worker放進無條件執行的
    #    expander body，改成每列一個按鈕，點下去才把該檔存進session_state，
    #    頁面只對「被選中的那一檔」算一次戰卡並顯示，其餘230檔完全不算。
    # 2. 清單顯示上限：就算不算戰卡，一次rerender 231列badge+按鈕對Streamlit
    #    的DOM也是負擔，預設只顯示訊號最強的前SMART_MONEY_DISPLAY_LIMIT檔
    #    （排序已經把多維度+高週轉率的排最前面），要看更多用slider放寬。
    _SMART_MONEY_TAG_SHORT = {
        "週轉率高的熱門股": ("週轉率高", "#3B82F6"),           # 藍色系
        "週轉率逐步墊高": ("逐步墊高", "#F59E0B"),              # 黃色系
        "週轉率異常(主力關注)": ("冷門爆量", "#EF4444"),         # 紅色系
        "週轉率高的反轉股(均線糾結)": ("量縮反轉", "#10B981"),   # 綠色系
    }
    SMART_MONEY_DISPLAY_LIMIT = 30


    @st.cache_data(ttl=300, show_spinner=False)
    def _load_smart_money_candidates(trade_date):
        """
        【R97續15新增，登入速度第一優先】主力偵測清單的唯一資料來源。
        加5分鐘快取——同一個看盤時段內反覆勾選濾網/切換套餐都不會重打
        Supabase，只在快取過期或換日期時查一次。回傳list[dict]（已enrich）。
        篩選全部在記憶體內對這份快取結果做，零現場運算、零額外DB往返。

        【R97續23新增，總指揮官要求：全市場波段評分濾網】額外join
        market_signal_snapshot(stage_signal每天22:00全市場都會算好寫入的
        波段評分快照)——純讀表，不多打任何API/FinMind，跟其他enrich
        欄位(法人/MA/突破/營收)同一個模式。這個評分「當天內固定不變」，
        是收盤後用當天收盤價算好存進資料庫的一個快照值，不會因為盤中
        股價跳動而即時變動，要等隔天22:00排程用新的收盤價重新計算才會
        更新——這點要跟總指揮官說清楚，避免誤以為是即時分數。
        """
        if SUPABASE_CONN is None:
            return []
        try:
            res = (SUPABASE_CONN.table("smart_money_candidates").select("*")
                  .eq("trade_date", trade_date).execute())
            rows = res.data or []
            if rows:
                try:
                    _score_res = (SUPABASE_CONN.table("market_signal_snapshot")
                                 .select("symbol,score").eq("trade_date", trade_date).execute())
                    _score_map = {r["symbol"]: r.get("score") for r in (_score_res.data or [])}
                    for r in rows:
                        r["swing_score"] = _score_map.get(r["symbol"])
                except Exception:
                    for r in rows:
                        r["swing_score"] = None
            # patterns陣列長度DESC → 週轉率DESC（多重確認優先）
            rows.sort(key=lambda r: (len(r.get("patterns") or []), r.get("turnover_pct") or 0),
                     reverse=True)
            return rows
        except Exception:
            return []


    # 預設套餐：每個套餐設定「要勾哪些濾網 + 要保留哪些維度」。名稱→設定。
    # 對應對話紀錄「主力偵測收斂設計」裡我以操盤角度建議的三個套餐。
    _SMART_MONEY_PRESETS = {
        "主力默默進場": {   # 冷門轉強，勝率型
            "patterns": ["週轉率異常(主力關注)"],
            "f_inst": True, "f_ma20": True,
            "f_streak": False, "f_capital": False, "f_break": False, "f_rev": False, "f_multi": False,
        },
        "起漲突破": {       # 動能型
            "patterns": ["週轉率高的熱門股"],
            "f_break": True, "f_rev": True,
            "f_inst": False, "f_streak": False, "f_capital": False, "f_ma20": False, "f_multi": False,
        },
        "投信布局": {       # 波段型
            "patterns": ["週轉率逐步墊高"],
            "f_capital": True, "f_streak": True,
            "f_inst": False, "f_ma20": False, "f_break": False, "f_rev": False, "f_multi": False,
        },
    }
    _SMART_FILTER_KEYS = ["smart_f_inst", "smart_f_streak", "smart_f_capital",
                          "smart_f_ma20", "smart_f_break", "smart_f_rev", "smart_f_multi",
                          "smart_f_swing"]
    _ALL_SMART_PATTERNS = list(_SMART_MONEY_TAG_SHORT.keys())


    def _apply_smart_preset(name):
        """把某個套餐的設定寫進session_state（在widget建立前呼叫，rerun後生效）。"""
        cfg = _SMART_MONEY_PRESETS.get(name)
        if not cfg:
            return
        st.session_state["smart_f_inst"] = cfg.get("f_inst", False)
        st.session_state["smart_f_streak"] = cfg.get("f_streak", False)
        st.session_state["smart_f_capital"] = cfg.get("f_capital", False)
        st.session_state["smart_f_ma20"] = cfg.get("f_ma20", False)
        st.session_state["smart_f_break"] = cfg.get("f_break", False)
        st.session_state["smart_f_rev"] = cfg.get("f_rev", False)
        st.session_state["smart_f_multi"] = cfg.get("f_multi", False)
        st.session_state["smart_f_swing"] = cfg.get("f_swing", False)
        st.session_state["smart_pattern_sel"] = list(cfg.get("patterns", _ALL_SMART_PATTERNS))


    def _smart_row_passes(r):
        """對一列（已enrich）套用目前勾選的濾網，回傳True=通過。純記憶體判斷。"""
        # 維度篩選：至少符合所選維度之一
        _sel_patterns = st.session_state.get("smart_pattern_sel", _ALL_SMART_PATTERNS)
        if _sel_patterns and not (set(r.get("patterns") or []) & set(_sel_patterns)):
            return False
        if st.session_state.get("smart_f_multi") and len(r.get("patterns") or []) < 2:
            return False
        if st.session_state.get("smart_f_inst"):
            _v = r.get("inst_net_5d")
            if _v is None or _v <= 0:
                return False
        if st.session_state.get("smart_f_streak"):
            if (r.get("foreign_streak") or 0) < 3 and (r.get("trust_streak") or 0) < 3:
                return False
        if st.session_state.get("smart_f_capital"):
            # 股本<50億：股本(元)=發行股數×面額10 → 發行股數<5億股
            _sh = r.get("shares")
            if _sh is None or _sh >= 5_0000_0000:
                return False
        if st.session_state.get("smart_f_ma20"):
            if not r.get("above_ma20"):
                return False
        if st.session_state.get("smart_f_break"):
            if not r.get("broke_20d_high"):
                return False
        if st.session_state.get("smart_f_rev"):
            _rv = r.get("rev_yoy")
            if _rv is None or _rv <= 0:
                return False
        if st.session_state.get("smart_f_swing"):
            # 【R97續23新增】全市場波段評分≥6(偏多攻擊門檻，跟stage_signal
            # 自動選股用的同一個門檻一致)。當天內固定不變的快照值，見
            # _load_smart_money_candidates()的說明。
            _sw = r.get("swing_score")
            if _sw is None or _sw < 6:
                return False
        return True


    if SUPABASE_CONN is not None:
        with st.expander("🔍 主力偵測：四維度訊號清單", expanded=False):
            st.caption("四維度粗篩（CMoney選股法）已在半夜掃描時套過『流動性≥1億+排除處置注意股』"
                      "硬地板。下方再用『指令』收斂——單一維度會篩出一大堆，疊上籌碼/型態/基本面"
                      "才精準。純讀已算好的表，篩選不影響頁面速度。")

            # 首次載入自動套用「主力默默進場」套餐，避免一進來就看到一大堆
            if "smart_filters_init" not in st.session_state:
                _apply_smart_preset("主力默默進場")
                st.session_state["smart_filters_init"] = True

            # ── 預設套餐（一鍵套用）──
            st.markdown("**預設套餐**（一鍵套用，之後仍可自行微調下面的指令）：")
            _pc1, _pc2, _pc3 = st.columns(3)
            if _pc1.button("🥷 主力默默進場", key="smart_preset_1", use_container_width=True):
                _apply_smart_preset("主力默默進場"); st.rerun()
            if _pc2.button("🚀 起漲突破", key="smart_preset_2", use_container_width=True):
                _apply_smart_preset("起漲突破"); st.rerun()
            if _pc3.button("🏦 投信布局", key="smart_preset_3", use_container_width=True):
                _apply_smart_preset("投信布局"); st.rerun()

            # ── 維度選擇 ──
            st.multiselect(
                "保留哪些維度（符合任一即可）",
                options=_ALL_SMART_PATTERNS,
                format_func=lambda p: _SMART_MONEY_TAG_SHORT.get(p, (p,))[0],
                key="smart_pattern_sel")

            # ── 指令（可自由組合的濾網）──
            st.markdown("**指令**（可任意勾選組合）：")
            _fc1, _fc2 = st.columns(2)
            with _fc1:
                st.checkbox("三大法人近5日淨買超>0", key="smart_f_inst")
                st.checkbox("外資或投信連買≥3日", key="smart_f_streak")
                st.checkbox("股本<50億（小型股易噴）", key="smart_f_capital")
                st.checkbox("需符合≥2個維度", key="smart_f_multi")
            with _fc2:
                st.checkbox("站上MA20", key="smart_f_ma20")
                st.checkbox("突破近20日高", key="smart_f_break")
                st.checkbox("月營收年增>0", key="smart_f_rev")
                st.checkbox("全市場波段評分≥6（偏多攻擊）", key="smart_f_swing",
                          help="來自stage_signal每天22:00全市場都會算的波段評分快照"
                               "（純讀表，不額外打API/FinMind）。當天內固定不變，"
                               "要等隔天22:00排程用新收盤價重算才會更新，不是即時"
                               "隨盤中股價跳動的分數。")

            # ── 讀快取 + 記憶體篩選 ──
            _sm_date = get_current_or_last_trading_date()
            _sm_all = _load_smart_money_candidates(_sm_date)
            _sm_total = len(_sm_all)
            _sm_rows = [r for r in _sm_all if _smart_row_passes(r)]

            st.divider()
            if _sm_total == 0:
                st.info("今天沒有股票通過四維度+硬地板，或今天stage_smart_money_scan還沒執行。")
            elif not _sm_rows:
                st.warning(f"全市場共 {_sm_total} 檔通過粗篩，但目前的指令組合篩完後 0 檔。"
                          f"可放寬指令、換個套餐、或減少勾選的維度。")
            else:
                st.success(f"粗篩 {_sm_total} 檔 → 指令收斂後 **{len(_sm_rows)}** 檔"
                          + (f"（清單過長，只顯示訊號最強的前 {SMART_MONEY_DISPLAY_LIMIT} 檔）"
                             if len(_sm_rows) > SMART_MONEY_DISPLAY_LIMIT else ""))

                for _sm in _sm_rows[:SMART_MONEY_DISPLAY_LIMIT]:
                    _sm_sym = _sm["symbol"]
                    # 額外把籌碼/型態資訊也秀出來，讓總指揮官不用展開戰卡就能初判
                    _extra = []
                    if _sm.get("inst_net_5d") is not None:
                        _extra.append(f"法人5日{_sm['inst_net_5d']:+.0f}張")
                    if _sm.get("above_ma20"):
                        _extra.append("站上MA20")
                    if _sm.get("broke_20d_high"):
                        _extra.append("突破20日高")
                    if _sm.get("rev_yoy") is not None:
                        _extra.append(f"營收YoY{_sm['rev_yoy']:+.0f}%")
                    if _sm.get("swing_score") is not None:
                        _extra.append(f"波段評分{_sm['swing_score']:+.0f}")
                    _extra_str = "｜".join(_extra)

                    _sm_col1, _sm_col2 = st.columns([5, 1])
                    with _sm_col1:
                        st.markdown(f"**{_sm_sym} {TW_STOCK_NAMES.get(_sm_sym, '')}** "
                                  f"｜週轉率 {_sm['turnover_pct']}% ｜5日量比 {_sm['vol_ratio_5d']}")
                        _sm_badges = []
                        for _p in (_sm.get("patterns") or []):
                            _short, _color = _SMART_MONEY_TAG_SHORT.get(_p, (_p, "#6B7280"))
                            _sm_badges.append(
                                f"<span style='background:{_color};color:white;padding:2px 8px;"
                                f"border-radius:10px;font-size:0.8em;margin-right:4px'>{_short}</span>")
                        st.markdown("".join(_sm_badges), unsafe_allow_html=True)
                        if _extra_str:
                            st.caption(_extra_str)
                    with _sm_col2:
                        if st.button("➕加入雷達", key=f"smart_pin_{_sm_sym}"):
                            if _sm_sym not in st.session_state.pinned_stocks:
                                st.session_state.pinned_stocks[_sm_sym] = "主力偵測"
                                log_watchlist_entry(_sm_sym, "主力偵測")
                                save_local_db_isolated()
                                st.success(f"✅ {_sm_sym} 已加入雷達")
                                time.sleep(0.5)
                                st.rerun()
                            else:
                                st.caption("已在雷達中")

                    # 【R98續16，總指揮官決策】跟路線2面板一致——即時算完整戰卡
                    # 這條路徑在目前資料源現實下不可靠（即使加了25秒硬性逾時
                    # 也還是常常算不出來），直接把「戰卡」按鈕整個拿掉，只保留
                    # 「加入雷達」。加入雷達後走持倉/雷達區的正常渲染路徑（那條
                    # 路徑有完整的即時報價/備援機制，不受這裡影響）。這個面板
                    # 動輒200+檔，拿掉戰卡運算同時也一併消除了R97續15那個效能
                    # 隱憂，是雙贏。等P0報價引擎升級後再評估是否加回。
                    st.divider()

        # 【R97續21新增，多因子權重可視化(深版)】讀factor_snapshot（半夜排程
        # 已經算好、5分鐘快取），全部運算在記憶體內做，零網路延遲，拖滑桿
        # 即時看候選池怎麼變。詳見warroom_core.py的run_additive_factors_
        # detailed()/apply_custom_factor_weights()說明。
        _FACTOR_LABELS = {
            "ma_position": "均線位置", "foreign_buy": "外資買賣超",
            "volume_ratio": "量能", "open_high_close_low": "開高走低",
            "buffer_pct": "防守線緩衝", "landmine": "基本面地雷",
            "ma_compression_breakout": "均線糾結突破", "institutional_resonance": "法人共振",
            "institutional_persistence": "法人持續性", "revenue_momentum": "營收動能",
        }
        _FACTOR_COL_MAP = {   # UI用簡短名稱 → factor_snapshot實際欄位名
            "ma_position": "f_ma_position", "foreign_buy": "f_foreign_buy",
            "volume_ratio": "f_volume_ratio", "open_high_close_low": "f_open_high_close_low",
            "buffer_pct": "f_buffer_pct", "landmine": "f_landmine",
            "ma_compression_breakout": "f_ma_compression_breakout",
            "institutional_resonance": "f_institutional_resonance",
            "institutional_persistence": "f_institutional_persistence",
            "revenue_momentum": "f_revenue_momentum",
        }

        @st.cache_data(ttl=300, show_spinner=False)
        def _load_factor_snapshot(trade_date):
            """讀factor_snapshot——純讀表，5分鐘快取，同一個看盤時段內拖幾次
            滑桿都不會重打資料庫。"""
            if SUPABASE_CONN is None:
                return []
            try:
                res = (SUPABASE_CONN.table("factor_snapshot").select("*")
                      .eq("trade_date", trade_date).execute())
                return res.data or []
            except Exception:
                return []

        if SUPABASE_CONN is not None:
            with st.expander("⚖️ 多因子權重可視化（即時調整，全市場零延遲重算）", expanded=False):
                st.caption("每個因子的判斷規則本身不能調（例如「站穩多頭+2」這個規則邏輯"
                          "不變），能調的是這個因子命中後「分數打幾折/放大幾倍」。拖滑桿"
                          "全市場即時重算候選池，資料是半夜排程已經算好的，這裡純數學"
                          "運算，零網路延遲。")

                _fw_date = get_current_or_last_trading_date()
                _fw_rows = _load_factor_snapshot(_fw_date)

                if not _fw_rows:
                    st.info(f"{_fw_date} 還沒有factor_snapshot資料，可能是這個功能上線後"
                           f"還沒跑過排程（下次22:00選股排程執行後會開始累積），或今天"
                           f"還沒收盤。")
                else:
                    # 讀已儲存的權重當滑桿初始值，沒存過就全部預設1.0
                    _saved_weights_raw = sb_get_config('factor_weights_json', '')
                    try:
                        _saved_weights = json.loads(_saved_weights_raw) if _saved_weights_raw else {}
                    except Exception:
                        _saved_weights = {}

                    st.markdown("**因子權重倍率**（0=完全關閉，1=原始權重，2=放大兩倍）：")
                    _fw_weights = {}
                    _fw_col1, _fw_col2 = st.columns(2)
                    _factor_names = list(_FACTOR_LABELS.keys())
                    for i, fname in enumerate(_factor_names):
                        _target_col = _fw_col1 if i % 2 == 0 else _fw_col2
                        with _target_col:
                            _default_w = float(_saved_weights.get(fname, 1.0))
                            _fw_weights[fname] = st.slider(
                                _FACTOR_LABELS[fname], 0.0, 2.0, _default_w, 0.1,
                                key=f"fw_slider_{fname}")

                    # 全市場即時重算——純記憶體運算，不打任何網路請求
                    _fw_results = []
                    for row in _fw_rows:
                        _detail = {fname: (row.get(_FACTOR_COL_MAP[fname]) or 0)
                                  for fname in _factor_names}
                        _new_score = apply_custom_factor_weights(_detail, _fw_weights)
                        _fw_results.append({
                            "symbol": row["symbol"],
                            "default_score": row.get("total_score_default_weight") or 0,
                            "new_score": _new_score,
                        })

                    _default_long = sum(1 for r in _fw_results if r["default_score"] >= 6)
                    _default_short = sum(1 for r in _fw_results if r["default_score"] <= -6)
                    _new_long = sum(1 for r in _fw_results if r["new_score"] >= 6)
                    _new_short = sum(1 for r in _fw_results if r["new_score"] <= -6)

                    st.divider()
                    _fw_m1, _fw_m2 = st.columns(2)
                    _fw_m1.metric("偏多攻擊候選(≥6分)", _new_long, delta=_new_long - _default_long)
                    _fw_m2.metric("偏空防守候選(≤-6分)", _new_short, delta=_new_short - _default_short)
                    st.caption(f"共{len(_fw_results)}檔，原始權重(全1.0)下：偏多{_default_long}檔／"
                              f"偏空{_default_short}檔。")

                    # 新增/移除的候選名單，讓總指揮官具體看到「調整這組權重實際影響了誰」
                    _default_long_syms = {r["symbol"] for r in _fw_results if r["default_score"] >= 6}
                    _new_long_syms = {r["symbol"] for r in _fw_results if r["new_score"] >= 6}
                    _added_long = _new_long_syms - _default_long_syms
                    _removed_long = _default_long_syms - _new_long_syms
                    if _added_long or _removed_long:
                        st.markdown("**偏多候選變化：**")
                        if _added_long:
                            st.caption(f"➕ 新增：{', '.join(sorted(_added_long)[:20])}"
                                      + (f"（等{len(_added_long)}檔）" if len(_added_long) > 20 else ""))
                        if _removed_long:
                            st.caption(f"➖ 移除：{', '.join(sorted(_removed_long)[:20])}"
                                      + (f"（等{len(_removed_long)}檔）" if len(_removed_long) > 20 else ""))

                    st.divider()
                    _fw_save_col, _fw_reset_col = st.columns(2)
                    with _fw_save_col:
                        if st.button("💾 儲存這組權重", key="fw_save_btn", use_container_width=True):
                            try:
                                sb_set_config('factor_weights_json', json.dumps(_fw_weights),
                                            "多因子權重可視化——各因子的自訂權重倍率")
                                st.success("已儲存，下次打開這個面板會用這組權重當滑桿初始值。")
                            except Exception as _fw_save_e:
                                st.error(f"儲存失敗：{_fw_save_e}")
                    with _fw_reset_col:
                        if st.button("↩️ 全部還原成1.0", key="fw_reset_btn", use_container_width=True):
                            for fname in _factor_names:
                                st.session_state[f"fw_slider_{fname}"] = 1.0
                            st.rerun()

if nav_section == "策略回測":
    with st.expander("🤖 系統自主選股模擬倉（做多 vs 做空 勝率PK）", expanded=False):
        st.caption("系統每天自動全市場選股、自動進出場，同時跑做多和做空兩個模擬倉。你不用干預，"
                   "只看它選了哪些、報酬如何。與你手動選股對照，看誰的勝率高。")

        # 資金設定（可調，存 system_config）
        _sys_cap = get_system_capital()
        _new_cap = st.number_input("每日系統選股總額（元，依當天入選檔數平分）", min_value=10000,
                                   max_value=10000000, value=_sys_cap, step=50000, key="sys_capital_input")
        if _new_cap != _sys_cap:
            if st.button("💾 更新總額設定", key="save_sys_cap"):
                if sb_set_config('system_pick_daily_capital', int(_new_cap), '系統自主選股每日投入總額'):
                    st.success(f"✅ 已更新為 {_new_cap:,} 元")
                    time.sleep(0.5)
                    st.rerun()
                else:
                    st.warning("更新失敗（Supabase 未連線？）")

        # 【V160 延伸4】ATR 移動停利設定
        _tc = get_trail_config()
        with st.expander("📈 ATR 移動停利設定（提高賺賠比，預設關閉）", expanded=False):
            st.caption("原本的出場規則是「固定停利點，一碰到就出場」，這會在大波段行情裡提早下車。"
                       "移動停利改成：價格往有利方向走時，停損線跟著抬高，只有回檔超過 N×ATR 才出場。"
                       "⚠️ 誠實說明：這提高的是**賺賠比**，不是勝率——它甚至可能小幅降低勝率"
                       "（部分原本會碰到固定停利的單，改成回檔出場時價格較低）。所以預設關閉，"
                       "建議你開啟後跑一個月，用上方績效表跟現在的數字對照，自己決定要不要留。")
            _t_on = st.checkbox("啟用 ATR 移動停利", value=_tc['enabled'], key="trail_on_cb")
            _t_mult = st.slider("回檔幾倍 ATR 出場（越大抱越久、回吐越多）", 1.0, 4.0,
                                _tc['mult'], 0.5, key="trail_mult_sld")
            _t_act = st.slider("獲利幾倍 ATR 才啟動（太小會被正常波動洗掉）", 0.5, 3.0,
                               _tc['activate_mult'], 0.5, key="trail_act_sld")
            if st.button("💾 儲存移動停利設定", key="save_trail_cfg", use_container_width=True):
                sb_set_config('trail_stop_enabled', '1' if _t_on else '0', 'ATR移動停利開關')
                sb_set_config('trail_stop_mult', str(_t_mult), 'ATR移動停利回檔倍數')
                sb_set_config('trail_stop_activate_mult', str(_t_act), 'ATR移動停利啟動門檻倍數')
                st.success("✅ 已儲存")
                time.sleep(0.5)
                st.rerun()
            if _tc['enabled']:
                st.info(f"目前啟用中：獲利超過 {_tc['activate_mult']}×ATR 後啟動，"
                        f"回檔 {_tc['mult']}×ATR 出場（出場原因會標記為 trail_stop，"
                        f"可在績效細節表裡跟 stop_loss／take_profit 分開比較）。")

        # 【V160】總指揮官確認排程已正常運作，移除手動測試選股按鈕。


        # 檢查出場
        if st.button("🔄 檢查並執行自動出場（出場規則B）", key="check_sys_exits", use_container_width=True):
            with st.spinner("檢查所有持倉是否觸發停損/停利..."):
                _exits = system_check_exits(config_payload)
                if _exits:
                    system_apply_exits(_exits)
                    st.success(f"✅ {len(_exits)} 檔觸發出場：" +
                               "、".join(f"{e['symbol']}({_exit_reason_zh(e['exit_reason'])},{e['realized_roi']:+.1f}%)" for e in _exits))
                else:
                    st.info("目前沒有持倉觸發出場條件。")
            time.sleep(1)
            st.rerun()

        # 【V160 新功能】檢查並執行加碼/攤平（依訊號判斷，每檔各上限一次）
        if st.button("➕➖ 檢查並執行加碼/攤平", key="check_add_reduce", use_container_width=True):
            with st.spinner("檢查所有持倉是否符合加碼/攤平條件..."):
                _acts = system_check_add_reduce(config_payload)
                if _acts:
                    system_apply_add_reduce(_acts)
                    _add_list = [f"{a['symbol']}(加碼{a['add_shares']}張)" for a in _acts if a['action'] == 'add']
                    _red_list = [f"{a['symbol']}(攤平{a['add_shares']}張)" for a in _acts if a['action'] == 'reduce']
                    _msg = "✅ "
                    if _add_list:
                        _msg += "順勢加碼：" + "、".join(_add_list) + "　"
                    if _red_list:
                        _msg += "逆勢攤平：" + "、".join(_red_list)
                    st.success(_msg)
                else:
                    st.info("目前沒有持倉符合加碼/攤平條件（或都已達各自上限一次）。")
            time.sleep(1)
            st.rerun()

        st.divider()
        # 績效統計
        _stats = get_system_portfolio_stats()
        st.markdown("**📊 系統模擬倉績效（已實現）**")
        _perf_df = pd.DataFrame([
            {'方向': '🔴 做多', **_stats['long_closed']},
            {'方向': '🔵 做空', **_stats['short_closed']},
        ])
        st.dataframe(_style_pnl_columns(_perf_df, ['平均報酬%', '總損益']),
                     use_container_width=True, hide_index=True)

        # 【V160 新增】總指揮官回報：績效摘要只有多空兩列總計，看不到細節操作
        # （哪幾檔、什麼時候進出、賺賠多少）。加一個可展開的明細表。
        _closed_list = _stats.get('closed', [])
        if _closed_list:
            with st.expander(f"🔎 查看已實現績效細節（共 {len(_closed_list)} 筆已結算）", expanded=False):
                _side_filter = st.radio("篩選方向", ["全部", "🔴做多", "🔵做空"],
                                        horizontal=True, key="perf_detail_side_filter")
                _rows = _closed_list
                if _side_filter == "🔴做多":
                    _rows = [r for r in _rows if r.get('side') == 'long']
                elif _side_filter == "🔵做空":
                    _rows = [r for r in _rows if r.get('side') == 'short']
                _detail_df = pd.DataFrame([{
                    '方向': '🔴做多' if r.get('side') == 'long' else '🔵做空',
                    '代號': r.get('symbol'),
                    '名稱': (TW_STOCK_NAMES.get(r.get('symbol'))
                            or (r.get('name') if r.get('name') != r.get('symbol') else None)
                            or r.get('symbol')),
                    '來源': '🧪手動' if (r.get('trigger_source') or 'manual') == 'manual' else '🤖排程',
                    '進場日': r.get('entry_date'), '進場價': r.get('entry_price'),
                    '出場日': r.get('exit_date'), '出場價': r.get('exit_price'),
                    '損益': r.get('realized_pnl'), '報酬%': r.get('realized_roi'),
                    '出場原因': _exit_reason_zh(r.get('exit_reason')),
                } for r in sorted(_rows, key=lambda r: r.get('exit_date') or '', reverse=True)])
                st.dataframe(_style_pnl_columns(_detail_df, ['損益', '報酬%']),
                            use_container_width=True, hide_index=True)
        st.caption(f"目前持倉中：{_stats['holding_count']} 檔")
        if _stats['holding']:
            # 【V160 修復】方向欄位改用顏色圖示（🔴做多／🔵做空），跟上面績效摘要表用同一套視覺語言，
            # 一眼掃色就能分辨多空，不用每行重複讀「多」「空」文字。
            _hold_df = pd.DataFrame([{
                '方向': '🔴' if h.get('side') == 'long' else '🔵',
                '代號': h.get('symbol'),
                # 【V160 修復】建倉當下若 TaiwanStockInfo 名稱表沒抓到，name 會退回成代號，
                # 畫面就變成「代號、名稱」兩欄都是數字。這裡在顯示時用最新的名稱表回填，
                # 名稱表也沒有才顯示代號。
                '名稱': (TW_STOCK_NAMES.get(h.get('symbol'))
                         or (h.get('name') if h.get('name') != h.get('symbol') else None)
                         or h.get('symbol')),
                # 【V160 新增】來源：分辨這筆是你手動測試建的，還是 GitHub Actions 排程自動建的。
                '來源': '🧪手動' if (h.get('trigger_source') or 'manual') == 'manual' else '🤖排程',
                '進場日': h.get('entry_date'), '進場價': h.get('entry_price'),
                '張數': h.get('shares'), '防守線': h.get('def_line'), '停利點': h.get('take_profit'),
                '選股理由': h.get('select_reason', '—'),
            } for h in _stats['holding']])
            st.dataframe(_hold_df, use_container_width=True, hide_index=True)
            st.caption("🔴=做多／🔵=做空｜🧪手動=你按測試鈕建的，🤖排程=GitHub Actions 自動建的。"
                      "選股理由記錄了每檔當初為什麼被系統選中，"
                      "之後某檔勝率高，就能回頭分析它的共同特徵，優化選股邏輯。")

            # 【V160 新增】單檔績效查詢：看某一檔在模擬倉裡的完整進出與累計成績
            with st.expander("🔍 單檔績效查詢（某一檔幫我賺多少／賠多少）", expanded=False):
                # 【V160 修復】原本要手動打代號才能查，但根本不知道有哪幾檔交易過可以查。
                # 改成列出「所有有交易紀錄的標的」讓你直接選，仍保留輸入框給知道代號的情況。
                _traded = get_all_traded_symbols()
                if _traded:
                    _sym_opts = ["—"] + [f"{s} {n}（{c}筆）" for s, n, c in _traded]
                    _sym_map = {f"{s} {n}（{c}筆）": s for s, n, c in _traded}
                    _sym_pick = st.selectbox(f"選擇標的（共 {len(_traded)} 檔有交易紀錄）",
                                             _sym_opts, key="sym_perf_pick")
                    _sym_q = _sym_map.get(_sym_pick, "")
                else:
                    st.caption("目前系統模擬倉沒有任何交易紀錄。")
                    _sym_q = ""
                _sym_manual = st.text_input("或直接輸入代號查詢", key="sym_perf_q", placeholder="例如 2409")
                if _sym_manual.strip():
                    _sym_q = _sym_manual.strip()
                if _sym_q:
                    _cl, _hd, _stt = get_symbol_performance(_sym_q.strip())
                    if not _cl and not _hd:
                        st.info(f"{_sym_q.strip()} 在系統模擬倉沒有任何紀錄。")
                    else:
                        q1, q2, q3, q4 = st.columns(4)
                        q1.metric("已結算筆數", _stt['closed_count'])
                        q2.metric("持倉中", _stt['holding_count'])
                        q3.metric("勝率%", _stt['win_rate'] if _stt['win_rate'] is not None else "—")
                        q4.metric("累計損益", f"{_stt['total_pnl']:,.0f}"
                                  if _stt['closed_count'] else "—")
                        if _stt['avg_roi'] is not None:
                            st.caption(f"平均報酬率 {_stt['avg_roi']:+.2f}%")
                        if _cl:
                            st.markdown("**已結算紀錄**")
                            _cl_df = pd.DataFrame([{
                                '方向': '🔴做多' if r.get('side') == 'long' else '🔵做空',
                                '來源': '🧪手動' if (r.get('trigger_source') or 'manual') == 'manual' else '🤖排程',
                                '進場日': r.get('entry_date'), '進場價': r.get('entry_price'),
                                '出場日': r.get('exit_date'), '出場價': r.get('exit_price'),
                                '損益': r.get('realized_pnl'), '報酬%': r.get('realized_roi'),
                                '出場原因': _exit_reason_zh(r.get('exit_reason')),
                            } for r in _cl])
                            st.dataframe(_style_pnl_columns(_cl_df, ['損益', '報酬%']),
                                        use_container_width=True, hide_index=True)
                        if _hd:
                            st.markdown("**持倉中**")
                            st.dataframe(pd.DataFrame([{
                                '方向': '🔴做多' if r.get('side') == 'long' else '🔵做空',
                                '來源': '🧪手動' if (r.get('trigger_source') or 'manual') == 'manual' else '🤖排程',
                                '進場日': r.get('entry_date'), '進場價': r.get('entry_price'),
                                '張數': r.get('shares'), '狀態': r.get('status'),
                            } for r in _hd]), use_container_width=True, hide_index=True)

            # 【V160 新功能】手動平倉／刪除：之前完全沒有手動介入的方式，只能等自動出場條件觸發。
            st.markdown("**🛠️ 手動平倉／刪除持倉**")

            # 【V160 新增】批次刪除手動測試持倉。總指揮官回報：一筆一筆刪太慢——
            # 手動測試按鈕經常一次建好幾筆（如截圖 5 筆），逐一選單挑選刪除很沒效率。
            # 只鎖定 trigger_source='manual' 的持倉，避免手滑連排程真實持倉一起刪掉。
            _manual_holds = [h for h in _stats['holding']
                             if (h.get('trigger_source') or 'manual') == 'manual']
            if _manual_holds:
                with st.expander(f"🧹 批次刪除手動測試持倉（共 {len(_manual_holds)} 筆）", expanded=False):
                    st.caption("只列出來源＝🧪手動的持倉；🤖排程建立的不會出現在這裡，避免誤刪真實紀錄。")
                    _batch_opts = {
                        f"#{h['id']} {'🔴' if h.get('side')=='long' else '🔵'}{h.get('symbol')} "
                        f"進場{h.get('entry_price')} {h.get('shares')}張 ({h.get('entry_date')})": h['id']
                        for h in _manual_holds
                    }
                    _batch_picked = st.multiselect("勾選要刪除的持倉（可多選）",
                                                   list(_batch_opts.keys()), key="batch_del_manual")
                    if _batch_picked and st.button(
                            f"🗑️ 確認刪除選中的 {len(_batch_picked)} 筆（不留紀錄，不計入勝率）",
                            key="batch_del_manual_btn", use_container_width=True):
                        _ids_to_del = [_batch_opts[k] for k in _batch_picked]
                        def _do_batch_delete():
                            return (SUPABASE_CONN.table("system_portfolio")
                                    .delete().in_("id", _ids_to_del).execute())
                        ok, _ = _sb_safe(_do_batch_delete)
                        if ok:
                            st.success(f"✅ 已刪除 {len(_ids_to_del)} 筆手動測試持倉")
                        else:
                            st.warning("批次刪除失敗，請稍後再試。")
                        time.sleep(1)
                        st.rerun()

            _hold_labels = {
                f"#{h['id']} {'🔴' if h.get('side')=='long' else '🔵'}{h.get('symbol')} "
                f"進場{h.get('entry_price')} {h.get('shares')}張 ({h.get('entry_date')})": h
                for h in _stats['holding']
            }
            _picked_label = st.selectbox("選擇要操作的持倉", ["—"] + list(_hold_labels.keys()),
                                         key="manual_holding_pick")
            if _picked_label != "—":
                _picked_h = _hold_labels[_picked_label]
                mc1, mc2 = st.columns(2)
                if mc1.button("✅ 手動平倉（用現價結算損益，計入勝率統計）", key="manual_close_btn",
                              use_container_width=True):
                    _cc = calculate_signal_with_timeout(_picked_h['symbol'], config_payload, timeout_sec=25)
                    _cur = float(_cc.get('price', 0) or 0) if _cc and not _cc.get('error') else 0.0
                    if _cur <= 0:
                        st.warning("抓不到現價，無法結算，請稍後再試。")
                    else:
                        _entry = float(_picked_h.get('entry_price', 0) or 0)
                        _sh = int(_picked_h.get('shares', 0) or 0)
                        if _picked_h.get('side') == 'long':
                            _pnl = (_cur - _entry) * _sh * 1000
                        else:
                            _pnl = (_entry - _cur) * _sh * 1000
                        _roi = (_pnl / (_entry * _sh * 1000) * 100) if _entry > 0 and _sh > 0 else 0.0
                        system_apply_exits([{**_picked_h, 'exit_price': _cur, 'exit_reason': 'manual',
                                             'realized_pnl': round(_pnl, 0), 'realized_roi': round(_roi, 2)}])
                        st.success(f"✅ {_picked_h['symbol']} 已手動平倉，損益 {_pnl:+,.0f} 元 ({_roi:+.1f}%)，計入勝率統計")
                        time.sleep(1)
                        st.rerun()
                if mc2.button("🗑️ 直接刪除（不留紀錄，不計入勝率）", key="manual_delete_btn",
                              use_container_width=True):
                    def _do_delete():
                        return SUPABASE_CONN.table("system_portfolio").delete().eq("id", _picked_h['id']).execute()
                    ok, _ = _sb_safe(_do_delete)
                    if ok:
                        st.success(f"✅ {_picked_h['symbol']} 已刪除")
                    else:
                        st.warning("刪除失敗，請稍後再試。")
                    time.sleep(1)
                    st.rerun()
                st.caption("💡 手動平倉：用現價結算損益，跟自動出場一樣計入勝率統計（適合你想主動了結一筆）。"
                          "直接刪除：整筆紀錄消失、不計入任何統計（適合測試資料想清掉重來）。")

if nav_section == "策略回測":
    with st.expander("🤖 自動排程風控履歷", expanded=False):
        # 【V160 R44新增】排程執行履歷——直接在網頁看每天各階段執行結果，
        # 讀既有的system_run_log整理成時間序表格，沒有新增資料來源。
        def _fetch_run_log(limit=60):
            def _do():
                return (SUPABASE_CONN.table("system_run_log").select("*")
                        .order("run_date", desc=True).order("id", desc=True).limit(limit).execute())
            ok, res = _sb_safe(_do)
            return res.data if (ok and res is not None and getattr(res, "data", None)) else []

        _log_rows = _fetch_run_log()
        if not _log_rows:
            st.caption("尚無排程執行紀錄（排程還沒真正跑過，或 Supabase 未連線）。")
        else:
            _stage_zh = {"signal": "🌙22:00選股", "gate": "☀️08:55總經閘門",
                         "morning_exit": "📈09:15早盤出場", "tail_entry": "⚡13:20尾盤進場", "health": "🩺健康檢查"}
            _log_df = pd.DataFrame([{
                "日期": r.get("run_date"), "階段": _stage_zh.get(r.get("stage"), r.get("stage")),
                "狀態": r.get("gate_status"), "選出/執行檔數": r.get("executed_count") or r.get("picked_count") or 0,
                "說明": r.get("note", ""),
            } for r in _log_rows])
            st.dataframe(_log_df, use_container_width=True, hide_index=True)
            st.caption("📋 每一列是排程某個階段執行完的結果紀錄。閘門狀態：🟢bull(多頭順風)／"
                      "🟡hedge(對沖模式)／🚨panic(恐慌熔斷)——這三態決定當天13:20要執行哪一側的候選標的。")

if nav_section == "策略回測":
    with st.expander("📈 風報比／最大拉回／資金曲線（策略體檢）", expanded=False):
        # 【V160 R44 新增】不只看勝率，看報酬背後的風險代價——風報比評估策略的
        # 真實期望值，MDD評估抗壓性，資金曲線對照大盤驗證是否真的有超額報酬。
        _sample_source = st.radio("統計範圍", ["系統模擬倉", "我自己的手動交易", "兩者合併"],
                                  horizontal=True, key="risk_metrics_source")
        _sys_closed = get_system_portfolio_stats().get('closed', [])
        _manual_closed = sb_get_manual_trade_log()

        if _sample_source == "系統模擬倉":
            _trades_for_metrics = _sys_closed
        elif _sample_source == "我自己的手動交易":
            _trades_for_metrics = _manual_closed
        else:
            _trades_for_metrics = _sys_closed + _manual_closed

        # 【R67新增】把「當下持倉的未實現損益」一起納入MDD計算，解除原本
        # 「只算已平倉、數字過度樂觀」的限制。用MIS即時報價一次批次抓所有持倉
        # 的現價（一支API call，成本很低），算出每檔的未實現報酬率。
        _open_for_mdd = []
        try:
            _open_raw = (get_system_portfolio_stats().get('holding', [])
                         if _sample_source != "我自己的手動交易" else [])
            if _open_raw:
                # 【R97續19修復，深度複查抓到】原本tse/otc全部猜'tse'，持有的
                # 是上櫃股時這裡會查不到即時價，MDD計算悄悄漏掉那幾檔的未實現
                # 損益——跟attach_live_quotes同一套精確判斷(fetch_listed_only_
                # codes)，不再用猜的。
                try:
                    _mdd_listed = fetch_listed_only_codes()
                except Exception:
                    _mdd_listed = set()
                if _mdd_listed:
                    _pairs = [(str(h.get('symbol')), "tse" if str(h.get('symbol')) in _mdd_listed else "otc")
                             for h in _open_raw if h.get('symbol')]
                else:
                    _pairs = [(str(h.get('symbol')), 'tse') for h in _open_raw if h.get('symbol')]
                # 【R98續63修復】改用含Shioaji備援的共用函式，這是持倉未
                # 實現損益計算，需要可靠的即時報價。
                _live_map, _ = fetch_live_quotes_resilient(
                    _pairs, shioaji_api_key=SHIOAJI_API_KEY,
                    shioaji_secret_key=SHIOAJI_SECRET_KEY) if _pairs else ({}, {})
                for _h in _open_raw:
                    _sym = str(_h.get('symbol', ''))
                    _entry = float(_h.get('entry_price', 0) or 0)
                    _q = _live_map.get(_sym) or {}
                    _now = _q.get('price')
                    if _entry > 0 and _now:
                        _r = (float(_now) - _entry) / _entry * 100
                        if _h.get('side') == 'short':
                            _r = -_r      # 做空方向相反：跌才是賺
                        _open_for_mdd.append({'realized_roi': _r})
        except Exception:
            _open_for_mdd = []      # 抓不到即時價就退回只算已平倉，不讓這段拖垮整個面板

        _metrics = compute_risk_metrics(_trades_for_metrics, min_samples=10,
                                        open_positions=_open_for_mdd or None)

        if not _metrics['ready']:
            st.info(f"📊 樣本累積中：{_metrics['sample_count']}/{_metrics['min_samples']} 筆已結算交易。"
                   f"樣本太少時風報比/MDD容易被單一極端值扭曲，累積到{_metrics['min_samples']}筆才會顯示數字。")
        else:
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("風報比(盈虧比)", f"{_metrics['profit_factor']:.2f}" if _metrics['profit_factor'] else "—",
                      help="平均獲利金額 ÷ 平均虧損金額。>1代表贏的時候贏得比輸的時候多，數字越高越好。")
            m2.metric("勝率%", f"{_metrics['win_rate']:.1f}%")
            # 【R67】主要顯示改成「含未實現」——那才是真正該看的風險數字；
            # 已平倉MDD降級成delta附註，兩個都看得到，但不再讓樂觀的那個當主角。
            if _metrics.get('max_drawdown_incl_open') is not None:
                m3.metric("最大拉回(含未實現)", f"{_metrics['max_drawdown_incl_open']:.1f}%",
                          delta=f"已平倉 {_metrics['max_drawdown_pct']:.1f}%", delta_color="off",
                          help="把「當下持倉的浮動損益」接在資金曲線最後算出來的拉回——"
                               "回答的是「如果現在全部清掉，從歷史最高點到現在總共回落多少」。"
                               "這會抓到「已平倉看起來很賺，但現在抱著大虧部位不認賠」這種"
                               "純已平倉MDD完全看不到的危險狀況。")
            else:
                m3.metric("最大拉回(已平倉)", f"{_metrics['max_drawdown_pct']:.1f}%",
                          help="目前沒有持倉、或即時報價抓不到，所以只能算已平倉MDD。")
            m4.metric("已結算筆數", _metrics['sample_count'])
            if _metrics.get('max_drawdown_incl_open') is not None:
                st.caption(f"✅ 最大拉回已納入當下 {_metrics['open_count']} 檔持倉的未實現損益"
                          f"（合計 {_metrics['open_unrealized_roi']:+.2f}%）。"
                          f"這解除了先前「只算已平倉、數字偏樂觀」的限制。"
                          f"仍存在的近似：我們沒有每日持倉市值歷史，所以是把「現在」這一個點"
                          f"接在曲線末端，不是重建持倉期間每一天的完整波動——"
                          f"抓得到「現在正在虧」，抓不到「中途曾經虧更多但又拉回來」。")
            else:
                st.caption("⚠️ 目前顯示的是「已平倉MDD」——沒有持倉、或這次即時報價抓不到，"
                          "所以沒有未實現損益可以納入。")

            # 【R98續110新增，深層系統檢視P2-2：最大拉回計算精確化】
            # 上面的MDD不管哪個版本，本質上都只在「事件發生當下」取樣
            # （平倉時、或現在這一刻），不是每天都有資料點，抓不到「持倉
            # 期間中途曾經更深的拉回」。這裡改用portfolio_value_snapshot
            # 排程（每天17:35收盤後記錄一次）累積的每日快照，樣本夠多
            # （30天以上）時才會顯示——不夠時誠實顯示累積進度，不會假裝
            # 有精確依據，也不影響上面近似值繼續正常顯示。
            if SUPABASE_CONN is not None:
                try:
                    _snap_res = (SUPABASE_CONN.table("portfolio_value_snapshot")
                                .select("snapshot_date,total_equity_pct").execute())
                    _true_mdd = compute_true_mdd_from_snapshots(_snap_res.data or [])
                    st.markdown("---")
                    st.markdown("**📐 真正的最大拉回（每日市值快照版，累積中）**")
                    if not _true_mdd['ready']:
                        st.info(f"📊 每日快照累積中：{_true_mdd['sample_count']}/{_true_mdd['min_samples']} 天。"
                               f"累積到{_true_mdd['min_samples']}天後，這裡會顯示用「每天實際權益」"
                               f"算出的真正peak-to-trough最大拉回——能抓到上面近似值抓不到的"
                               f"「持倉期間中途曾經更深的拉回」，但需要時間累積，不是改完馬上就有。")
                    else:
                        st.metric("真正最大拉回（每日快照）", f"{_true_mdd['max_drawdown_pct']:.1f}%",
                                 help=f"用{_true_mdd['sample_count']}天的每日持倉市值快照算出的"
                                      f"真正peak-to-trough拉回，不是只在平倉/現在這一刻取樣的近似值。")
                except Exception as _snap_e:
                    print(f"[真正MDD顯示] 查詢portfolio_value_snapshot失敗，不影響上方近似值顯示："
                          f"{type(_snap_e).__name__}: {_snap_e}")

            # 資金曲線 vs 大盤對照圖
            try:
                import plotly.graph_objects as go
                _ec = _metrics['equity_curve']
                _dates = [pt['date'] for pt in _ec]
                _strategy_ret = [pt['cum_return'] for pt in _ec]

                # 【R91修復】R67新增的「含未實現MDD」會在equity_curve最後多塞一筆
                # 偽日期標籤，跟大盤對照迴圈的pd.Timestamp()轉換衝突拋例外，拖垮
                # 整張圖表。修法：轉換失敗的項目直接跳過大盤對照，策略線照樣正常畫出。
                #
                # 【R97續21e修復，總指揮官截圖抓到：這張圖表其實一直在報錯】
                # 上面R91修的只是「日期字串轉換失敗」這一種情況(例如"現在(含未實現)"
                # 這種偽標籤轉不成Timestamp)，但實際發生的是第二種情況：字串轉換
                # 「成功」了，但yfinance回傳的_twii_hist索引帶時區(Asia/Taipei)，
                # pd.Timestamp(d)轉出來的卻不帶時區——兩者做<=比較時pandas直接
                # 拋TypeError('Invalid comparison between dtype=datetime64[ns, tz]
                # and Timestamp')，這個錯誤發生在原本的try區塊之外（那個try只包住
                # 轉換本身，沒包住後面的比較），所以還是會被最外層except接住，
                # 導致「策略線也照樣正常畫出」這句話沒有兌現——整張圖跟著大盤對照
                # 一起死掉。
                #
                # 修法：拿到_twii_close後立刻把索引統一成tz-naive（.tz_localize(None)），
                # 之後全程都是tz-naive對tz-naive，不會再有這個衝突；同時把「找
                # eligible」這段比較也包進try/except，任何一筆比對失敗只影響
                # 那一筆用前一筆頂替，不會拖垮整段迴圈。
                _twii_ret = None
                _real_dates = [d for d in _dates if d != '現在(含未實現)']
                if _real_dates:
                    _twii_hist = _yf_ticker("^TWII").history(start=_real_dates[0], end=_real_dates[-1], timeout=8)
                    if not _twii_hist.empty:
                        _twii_close = _twii_hist['Close']
                        if _twii_close.index.tz is not None:
                            _twii_close = _twii_close.tz_localize(None)
                        _base = float(_twii_close.iloc[0])
                        _twii_ret_series = ((_twii_close - _base) / _base * 100)
                        # 用merge_asof概念對齊：每個策略交易日，找當時最新的大盤累積報酬率
                        _twii_ret = []
                        for d in _dates:
                            try:
                                _d_ts = pd.Timestamp(d)
                                if _d_ts.tz is not None:
                                    _d_ts = _d_ts.tz_localize(None)
                                _eligible = _twii_ret_series[_twii_ret_series.index <= _d_ts]
                                _twii_ret.append(float(_eligible.iloc[-1]) if len(_eligible) else 0.0)
                            except Exception:
                                # 這一筆日期轉換或比對失敗(例如"現在(含未實現)"這種
                                # 偽標籤，或任何未預期的時區/型別問題)，用前一筆頂著，
                                # 不讓整條線斷掉、也不讓單一個點拖垮整段迴圈。
                                _twii_ret.append(_twii_ret[-1] if _twii_ret else 0.0)

                fig = go.Figure()
                fig.add_trace(go.Scatter(x=_dates, y=_strategy_ret, mode='lines+markers',
                                         name='策略累積報酬%', line=dict(color='#00d2ff', width=2)))
                if _twii_ret is not None:
                    fig.add_trace(go.Scatter(x=_dates, y=_twii_ret, mode='lines',
                                             name='大盤(^TWII)累積報酬%', line=dict(color='#888', width=1.5, dash='dot')))
                fig.update_layout(template='plotly_dark', height=350, margin=dict(l=10, r=10, t=30, b=10),
                                  legend=dict(orientation='h', y=1.1), xaxis=dict(type='category'))
                st.plotly_chart(fig, use_container_width=True)
                if _twii_ret is None:
                    st.caption("（大盤對照資料暫時抓不到，只顯示策略本身的資金曲線）")
            except Exception as e:
                st.caption(f"資金曲線圖繪製失敗：{e}")

if nav_section == "情報覆盤":
    with st.expander("🏭 族群輪動熱力圖（找出資金正在流入哪個產業）", expanded=False):
        st.caption("個股會漲通常是因為整個族群在動。先確認族群趨勢再選個股，等於多一層過濾，"
                   "能降低「選對股但選錯時機」的虧損。這項功能完全使用既有的免費資料"
                   "（產業分類 + 股價），不需要付費 API。")
        # 【R59新增】掃描結果原本只存session_state，換分頁就遺失。這裡先去
        # Supabase撈上次存的快取顯示，要更新才需要按「計算族群輪動」。
        if st.session_state.get('rotation_rows') is None:
            _cached_rows, _cached_meta = load_rotation_cache()
            if _cached_rows is not None:
                st.session_state['rotation_rows'] = _cached_rows
                st.session_state['rotation_scan_meta'] = _cached_meta

        _rot_n = st.slider("掃描檔數（越多越完整，但耗時越久）", 50, 500, 150, 50, key="rot_scan_n")
        if st.button("🔄 計算族群輪動", key="rot_calc_btn", use_container_width=True):
            _s2i, _i2s = fetch_industry_map()
            if not _s2i:
                st.warning("產業分類資料抓取失敗（FinMind TaiwanStockInfo 未回應），無法計算。")
            else:
                # 【R50修復】改用真正的百分比進度條取代原本的st.spinner（轉圈圈的跑步
                # 小人完全看不出進度，掃400檔跟掃50檔視覺上一樣，等的人不知道還要多久）。
                _rot_t0 = time.time()
                _rot_prog = st.progress(0.0, text=f"掃描 {_rot_n} 檔股票、彙整產業強弱中 0%")

                def _rot_cb(done, total):
                    _pct = done / total if total else 0
                    _rot_prog.progress(_pct, text=f"掃描 {_rot_n} 檔股票、彙整產業強弱中 "
                                                  f"{done}/{total}（{_pct*100:.0f}%）")

                _rot_rows, _rot_diag = compute_industry_rotation(
                    get_scan_pool_ordered()[0][:_rot_n], _s2i, max_scan=_rot_n,
                    progress_callback=_rot_cb)
                _rot_prog.empty()
                st.session_state['rotation_rows'] = _rot_rows
                st.session_state['rotation_diag'] = _rot_diag
                # 【R50新增，R59改成含完整日期】記錄這次掃描的檔數與耗時，畫成浮動
                # 標籤——原本只存時分秒，快取隔天顯示會誤以為是今天掃的，改存完整
                # 日期時間。
                _rot_meta = {
                    'count': _rot_n, 'elapsed': time.time() - _rot_t0,
                    'ts': datetime.now(TAIPEI_TZ).strftime('%Y-%m-%d %H:%M:%S'),
                }
                st.session_state['rotation_scan_meta'] = _rot_meta
                if _rot_rows:
                    # 只在真的掃到資料時才存快取，避免這次剛好掃描失敗，把之前
                    # 存好的正常結果洗掉——快取的意義是「至少留一份能看的」，
                    # 不該被一次失敗的重掃摧毀。
                    save_rotation_cache(_rot_rows, _rot_meta)

        _rot_meta = st.session_state.get('rotation_scan_meta')
        if _rot_meta:
            st.caption(f"🕐 上次掃描：{_rot_meta['count']} 檔，共花 {_rot_meta['elapsed']:.1f} 秒"
                      f"（{_rot_meta.get('ts', '')}，這份結果會保留到你下次按「計算族群輪動」"
                      f"為止，不會自動過期重掃）")

        _rot_rows = st.session_state.get('rotation_rows')
        if _rot_rows:
            # 【V160 新增：雙引擎族群透視】合併已存的營收YoY統計——這份資料來自
            # 「最近一次全市場掃描」順便算好、存在Supabase的，不是這次點按鈕
            # 才現算，所以是零額外API成本的合併，純粹讀取。
            _rev_stats = get_industry_revenue_stats()
            for _r in _rot_rows:
                _rs = _rev_stats.get(_r['產業'])
                if _rs:
                    _r['yoy_mean'] = _rs['yoy_mean']
                    _r['yoy_median'] = _rs['yoy_median']
                    _r['rev_sample_count'] = _rs['sample_count']
                    _r['營收YoY(平均/中位)%'] = f"{_rs['yoy_mean']:.1f} / {_rs['yoy_median']:.1f}（{_rs['sample_count']}檔）"
                else:
                    _r['yoy_mean'] = _r['yoy_median'] = _r['rev_sample_count'] = None
                    _r['營收YoY(平均/中位)%'] = "—（樣本不足或尚未掃描）"

            _rot_df = pd.DataFrame(_rot_rows)
            # 用背景色階呈現強弱（紅=強、綠=弱，符合台股習慣）
            try:
                _styler = _rot_df.style.background_gradient(subset=['5日%'], cmap='RdYlGn_r')
                # 【V160 新增】營收YoY欄位的背景色以 yoy_median 為基準（反映真實產業
                # 健康度，不是均值——均值會被少數飆股拉偏，中位數才是「過半數公司」
                # 的真實狀況，這正是這個功能設計的核心目的）。
                if _rot_df['yoy_median'].notna().any():
                    _styler = _styler.background_gradient(subset=['yoy_median'], cmap='RdYlGn_r')
                _display_cols = ['產業', '檔數', '1日%', '5日%', '20日%', '成交值(億)', '資金佔比%', '營收YoY(平均/中位)%']
                _styled = _styler.format(precision=2, subset=[c for c in _rot_df.columns if c not in
                                                               ('產業', '營收YoY(平均/中位)%')])
                st.dataframe(_styled, use_container_width=True, hide_index=True,
                            column_order=_display_cols)
            except Exception:
                # styler 需要 matplotlib，沒有就退回普通表格，不讓功能整個掛掉
                st.dataframe(_rot_df, use_container_width=True, hide_index=True)
            st.caption("💡 營收YoY「平均/中位」欄位來自最近一次全市場掃描時順便計算存下的數字，"
                      "不是這次即時抓取——所以此欄位可能比上面的價量欄位「舊」一點，正常現象。"
                      "平均數會被極端飆股拉偏，中位數才反映「過半數公司」的真實狀況，"
                      "兩者落差大時代表族群漲勢可能只是少數個股在拉。")
            st.markdown("#### 🧭 輪動判讀")
            for _line in build_rotation_advice(_rot_rows):
                st.markdown(_line)
        elif _rot_rows == []:
            _diag = st.session_state.get('rotation_diag') or {}
            if _diag.get('total', 0) > 0 and _diag.get('ok', 0) == 0:
                # 【R52】這才是「跑完但沒有任何資料」的真正情況——不是產業成員太少，
                # 是抓價格資料整批失敗。把失敗數字跟最後一個實際錯誤攤開，不再只顯示
                # 一句聽起來像「本來就沒資料」、但誤導了真正原因的通用訊息。
                st.error(f"⚠️ 掃描了 {_diag['total']} 檔，但全部 {_diag['fail']} 檔都抓不到股價資料"
                        f"（不是「產業成員太少」，是股價資料源本身這次全部失敗）。")
                if _diag.get('last_error'):
                    st.caption(f"最後一筆失敗訊息（僅供參考，不代表每檔原因都一樣）：{_diag['last_error']}")
                st.caption("可能原因：FinMind/yfinance這次剛好都連不上（可以去🩺資料源健康度檢查/"
                          "🔑FinMind額度狀態確認）；或掃描檔數暫時超過股價資料源能承受的並發量，"
                          "可以先調小掃描檔數再試一次。")
            else:
                st.info("沒有產業達到最低檔數門檻（每個產業至少3檔），試著加大掃描檔數。")

    with st.expander("🩺 財務體質篩選器（方向C：價值面融合，R98續17新增）", expanded=True):
        # 【R98續17新增，總指揮官方向C決策：戰情室從純短波段籌碼系統，
        # 融合進價值評估/體質評估的第二支柱】這個面板讀
        # financial_health_snapshot（stage_financial_health_scan排程
        # 已經算好的毛利率/ROE/現金流品質/負債比/流動比率/自由現金流
        # +財務風險綜合評分）+ twse_market_snapshot（全市場每日同步的
        # 殖利率/PE/PB/收盤價），依門檻篩選出體質健康的候選股。
        #
        # 【重要，誠實揭露範圍限制】掃描範圍不是全市場，是跟broker_flows
        # 同一個「系統關注範圍」（持倉+雷達+波段候選+當沖候選+週轉率宇宙），
        # 且排程是分批+斷點續傳（每次20檔），範圍會隨時間逐漸擴大，現在
        # 看到的檔數不代表這是全部符合條件的股票，只是「目前系統已經
        # 掃過、且符合條件」的子集合。
        st.caption("讀取財報體質排程(`stage_financial_health_scan`)已經掃過的股票，用財務風險"
                  "綜合評分(ROE/現金流品質/負債比/流動比率/自由現金流五個維度)+殖利率門檻"
                  "篩選。⚠️範圍限制：只涵蓋系統關注範圍(持倉+雷達+候選池)，不是全市場，且排程"
                  "分批進行中，檔數會隨時間增加。")

        _fh_scr_c1, _fh_scr_c2 = st.columns(2)
        with _fh_scr_c1:
            _fh_scr_risk = st.multiselect("財務風險等級", ["低風險", "中風險", "高風險"],
                                          default=["低風險", "中風險"], key="fh_screener_risk")
        with _fh_scr_c2:
            _fh_scr_yield = st.radio("殖利率門檻", ["不限", "≥3%", "≥5%", "≥7%"],
                                     horizontal=True, key="fh_screener_yield")

        if st.button("🔍 開始篩選", key="fh_screener_btn", use_container_width=True):
            if SUPABASE_CONN is None:
                st.warning("Supabase未連線，無法查詢。")
            else:
                try:
                    _fh_all = (SUPABASE_CONN.table("financial_health_snapshot")
                              .select("symbol,quarter_date,gross_margin,roe,cash_quality,"
                                     "debt_ratio,current_ratio,free_cash_flow,risk_score,risk_level")
                              .execute())
                    _fh_rows = _fh_all.data or []
                    _fh_syms = [r["symbol"] for r in _fh_rows]
                    _mkt_map = {}
                    if _fh_syms:
                        # 【效能考量】只查這批symbol的「最新一天」快照，不是整段
                        # 歷史——twse_market_snapshot是全市場每日表，先找出最新
                        # 交易日，再用.in_()限定symbol範圍查那一天的資料，避免
                        # 撈出不需要的歷史列。
                        _latest_date_res = (SUPABASE_CONN.table("twse_market_snapshot")
                                           .select("trade_date").order("trade_date", desc=True)
                                           .limit(1).execute())
                        if _latest_date_res.data:
                            _latest_trade_date = _latest_date_res.data[0]["trade_date"]
                            _mkt_res = (SUPABASE_CONN.table("twse_market_snapshot")
                                       .select("symbol,close_price,dividend_yield,pe,pb_ratio")
                                       .eq("trade_date", _latest_trade_date)
                                       .in_("symbol", _fh_syms).execute())
                            _mkt_map = {r["symbol"]: r for r in (_mkt_res.data or [])}
                    st.session_state['fh_screener_rows'] = _fh_rows
                    st.session_state['fh_screener_mkt'] = _mkt_map
                except Exception as _fh_scr_e:
                    st.warning(f"查詢失敗：{_fh_scr_e}")
                    st.session_state['fh_screener_rows'] = []
                    st.session_state['fh_screener_mkt'] = {}

        _fh_rows = st.session_state.get('fh_screener_rows')
        if _fh_rows is not None:
            _mkt_map = st.session_state.get('fh_screener_mkt', {})
            _yield_min = {"不限": None, "≥3%": 3.0, "≥5%": 5.0, "≥7%": 7.0}[_fh_scr_yield]
            _filtered = []
            for r in _fh_rows:
                _level = r.get("risk_level")
                # risk_level為None代表這6個指標全部缺值(compute_financial_
                # risk_score回傳None的情況)，篩選器誠實地不把它算進任何
                # 風險等級桶——既不算低風險(沒證據代表健康)，也不該被排除
                # 顯示在外面看不到，這裡選擇不納入結果(而不是預設當低風險)，
                # 避免資料缺失被誤讀成體質良好。
                if _level not in _fh_scr_risk:
                    continue
                _mkt = _mkt_map.get(r["symbol"], {})
                _dy = _mkt.get("dividend_yield")
                if _yield_min is not None and (_dy is None or _dy < _yield_min):
                    continue
                _filtered.append({
                    "股票": f"{r['symbol']} {TW_STOCK_NAMES.get(r['symbol'], '')}",
                    "財務風險": f"{r.get('risk_score', '—')}分（{r.get('risk_level', '—')}）",
                    "殖利率%": _dy if _dy is not None else "—",
                    "現價": _mkt.get("close_price", "—"),
                    "ROE%": r.get("roe", "—"),
                    "毛利率%": r.get("gross_margin", "—"),
                    "負債比%": r.get("debt_ratio", "—"),
                    "現金流品質": r.get("cash_quality", "—"),
                    "季度": r.get("quarter_date", "—"),
                })
            if not _filtered:
                st.info(f"目前系統已掃描範圍內（共{len(_fh_rows)}檔），沒有符合篩選條件的股票。"
                       "可以放寬條件，或等排程繼續擴大掃描範圍。")
            else:
                _filtered.sort(key=lambda x: (x["殖利率%"] if isinstance(x["殖利率%"], (int, float)) else -1),
                              reverse=True)
                st.dataframe(pd.DataFrame(_filtered), use_container_width=True, hide_index=True)
                st.caption(f"共{len(_filtered)}檔符合條件（系統已掃描範圍共{len(_fh_rows)}檔）。"
                          "⚖️財務風險評分是本系統自行設計的綜合分數，非任何第三方Z-Score公式的"
                          "重現，僅供參考，不是投資建議。")

    with st.expander("🚦 大盤位階燈號（方向C：價值面融合，R98續22新增）", expanded=False):
        # 【R98續22新增，總指揮官方向C：CMoney/艾蜜莉「景氣指標」概念的
        # 本地版】完全不需要新的資料源——twse_market_snapshot本來就是
        # 全市場每日同步的PE/PB/殖利率快照，已經累積將近一年的每日歷史，
        # 直接拿來算「今天的全市場中位數PE/PB/殖利率，落在過去這段時間
        # 分布的第幾百分位」，就是一個誠實、可驗證、不用外部資料源的
        # 「現在貴不貴」燈號。
        #
        # 【誠實揭露限制】twse_market_snapshot目前只有約一年的歷史
        # (2025-08-20起)，CMoney附件4的範例是抓2015-2023將近8年資料算
        # 百分位，統計顯著性天差地遠——這裡的燈號只能說是「相對過去約
        # 一年的位階」，不是「相對長期景氣循環的位階」，隨著這張表逐日
        # 累積，未來統計基礎會越來越紮實，但現在要誠實標註這個限制，
        # 不能讓總指揮官誤以為這跟CMoney的多年期版本是同一個量級的參考。
        st.caption("完全沿用既有的twse_market_snapshot(全市場每日PE/PB/殖利率快照)，"
                  "不需要任何新資料源。算法：今天全市場中位數PE/PB/殖利率，"
                  "落在過去約一年每日分布的第幾百分位。"
                  "⚠️目前歷史僅約一年（2025-08-20起累積），統計基礎會隨時間增加而更紮實，"
                  "現階段只能反映「相對近一年的位階」，不是長期景氣循環位階。")

        if st.button("🔍 計算目前大盤位階", key="market_gauge_btn", use_container_width=True):
            if SUPABASE_CONN is None:
                st.warning("Supabase未連線，無法查詢。")
            else:
                try:
                    # 【R98續22，重要修復】supabase-py單次查詢預設最多回傳
                    # 1000筆(見_sb_fetch_all()的既有註解)——twse_market_
                    # snapshot全市場每日快照，一年下來輕鬆超過20萬筆，
                    # 原本用.limit(300000)完全沒用，只會拿到前1000筆
                    # (可能只涵蓋1天多一點的資料，百分位算出來會嚴重
                    # 失真)。改用跟_sb_fetch_all()一樣的.range()分頁模式，
                    # 只選需要的4個欄位減少傳輸量。
                    _mg_rows = []
                    _mg_start = 0
                    _mg_page = 1000
                    while True:
                        _mg_res = (SUPABASE_CONN.table("twse_market_snapshot")
                                  .select("trade_date,pe,pb_ratio,dividend_yield")
                                  .range(_mg_start, _mg_start + _mg_page - 1).execute())
                        _batch = _mg_res.data or []
                        _mg_rows.extend(_batch)
                        if len(_batch) < _mg_page or _mg_start > 500000:
                            break
                        _mg_start += _mg_page
                    _mg_df = pd.DataFrame(_mg_rows)
                    st.session_state['market_gauge_df'] = _mg_df
                except Exception as _mg_e:
                    st.warning(f"查詢失敗：{_mg_e}")
                    st.session_state['market_gauge_df'] = None

        _mg_df = st.session_state.get('market_gauge_df')
        if _mg_df is not None and not _mg_df.empty:
            try:
                # 每個交易日算中位數(比平均數抗極端值干擾，個股PE偶爾會
                # 出現異常暴衝的離群值，中位數比較能代表「一般股票」的
                # 估值水準)。
                for c in ('pe', 'pb_ratio', 'dividend_yield'):
                    _mg_df[c] = pd.to_numeric(_mg_df[c], errors='coerce')
                _mg_daily = _mg_df[(_mg_df['pe'] > 0) & (_mg_df['pe'] < 200)].groupby('trade_date').agg(
                    median_pe=('pe', 'median'),
                    median_pb=('pb_ratio', 'median'),
                    median_yield=('dividend_yield', 'median'),
                ).reset_index().sort_values('trade_date')

                if len(_mg_daily) < 20:
                    st.info(f"目前只有{len(_mg_daily)}個交易日的資料，樣本太少，"
                           "百分位計算意義不大，先不顯示燈號，等資料多累積一些天數再來看。")
                else:
                    _today_row = _mg_daily.iloc[-1]
                    _n_days = len(_mg_daily)

                    def _percentile_rank(series, value):
                        """value在series裡贏過幾%的天數(0-100)。"""
                        return float((series < value).sum()) / len(series) * 100

                    _pe_pct = _percentile_rank(_mg_daily['median_pe'], _today_row['median_pe'])
                    _pb_pct = _percentile_rank(_mg_daily['median_pb'], _today_row['median_pb'])
                    _yield_pct = _percentile_rank(_mg_daily['median_yield'], _today_row['median_yield'])

                    # 【方向性】PE/PB是「越低越便宜」，百分位低=便宜；
                    # 殖利率是「越高越便宜」(股價相對股利便宜)，百分位
                    # 高=便宜——三個指標統一換算成「便宜度」(0-100，
                    # 100=歷史最便宜)才能加總平均，不能直接拿百分位
                    # 數字加總(方向不一致會加錯)。
                    _pe_cheapness = 100 - _pe_pct
                    _pb_cheapness = 100 - _pb_pct
                    _yield_cheapness = _yield_pct
                    _composite = (_pe_cheapness + _pb_cheapness + _yield_cheapness) / 3

                    if _composite >= 70:
                        _verdict, _color = "🟢 便宜", "#00c853"
                    elif _composite >= 40:
                        _verdict, _color = "🟡 合理", "#ffab00"
                    else:
                        _verdict, _color = "🔴 昂貴", "#ff5252"

                    st.markdown(f"### <span style='color:{_color}'>{_verdict}</span>　"
                              f"綜合便宜度 {_composite:.0f}/100", unsafe_allow_html=True)
                    # 【R98續41新增，總指揮官指示：圓形量表視覺化】跟財務風險
                    # 評分同一套做法，這裡也換成圓形量表——三段區間對應
                    # 便宜/合理/昂貴，跟上面文字判斷共用同一組70/40門檻。
                    try:
                        import plotly.graph_objects as go
                        _mg_fig = go.Figure(go.Indicator(
                            mode="gauge+number",
                            value=_composite,
                            number={'suffix': "/100", 'font': {'size': 28}},
                            gauge={
                                'axis': {'range': [0, 100], 'tickwidth': 1},
                                'bar': {'color': _color},
                                'steps': [
                                    {'range': [0, 40], 'color': '#4a1f1f'},
                                    {'range': [40, 70], 'color': '#4a3a12'},
                                    {'range': [70, 100], 'color': '#1b3a2f'},
                                ],
                                'threshold': {'line': {'color': _color, 'width': 3},
                                             'thickness': 0.8, 'value': _composite},
                            },
                        ))
                        _mg_fig.update_layout(height=180, margin=dict(l=20, r=20, t=30, b=10),
                                              paper_bgcolor='rgba(0,0,0,0)',
                                              font={'color': _color, 'family': "Arial"})
                        st.plotly_chart(_mg_fig, use_container_width=True, key="market_valuation_gauge")
                    except Exception:
                        pass  # 量表畫不出來時，上面已經有文字版判斷可看，不影響資訊完整性
                    st.caption(f"樣本：近{_n_days}個交易日（{_mg_daily['trade_date'].iloc[0]} ～ "
                              f"{_today_row['trade_date']}）")

                    _mg_c1, _mg_c2, _mg_c3 = st.columns(3)
                    _mg_c1.metric("全市場中位數PE", f"{_today_row['median_pe']:.1f}倍",
                                 f"贏過{_pe_pct:.0f}%的日子(越低越便宜)")
                    _mg_c2.metric("全市場中位數PB", f"{_today_row['median_pb']:.2f}倍",
                                 f"贏過{_pb_pct:.0f}%的日子(越低越便宜)")
                    _mg_c3.metric("全市場中位數殖利率", f"{_today_row['median_yield']:.2f}%",
                                 f"贏過{_yield_pct:.0f}%的日子(越高越便宜)")

                    _mg_chart_df = _mg_daily.tail(120).copy()
                    st.line_chart(_mg_chart_df.set_index('trade_date')['median_pe'],
                                 use_container_width=True)
                    st.caption("近120個交易日全市場中位數PE走勢（越低代表當時越便宜）。")
            except Exception as _mg_calc_e:
                st.warning(f"計算失敗：{_mg_calc_e}")
        elif _mg_df is not None:
            st.info("查詢結果是空的，可能Supabase連線正常但twse_market_snapshot這張表本身沒有資料。")

    with st.expander("🔐 金鑰使用量異常監控（R98續31新增）", expanded=False):
        # 【R98續31新增，總指揮官方向：防範類似Zeabur環境變數外洩事件
        # (2026-08-27，攻擊者取得平台內部憑證讀取大量使用者的環境變數，
        # AI服務金鑰遭盜用額度)】我們的secrets放在Streamlit Cloud/GitHub
        # 這兩個平台，如果哪天發生類似事件，第一個能察覺的訊號通常是
        # 「額度消耗速度異常」——排程(每天10:30台灣時間，接在早盤5分K
        # 輪詢+三關確認之後)會定期記錄FinMind真實已用次數，這裡讀歷史
        # 紀錄畫成趨勢圖，用量突然暴衝的話這裡看得到，且會同時發
        # Telegram警訊，不用一直手動盯著這個面板。
        st.caption("排程(每天約台灣時間10:30)定期記錄FinMind真實額度使用量(官方伺服器端"
                  "數字，不是估計值)，累積歷史後可以看出用量趨勢——正常情況應該是穩定、"
                  "可預期的模式，如果某天突然暴衝，可能代表金鑰外洩被盜用，排程本身也會"
                  "在偵測到異常時主動發Telegram警訊，不需要每天都來看這裡。"
                  "⚠️範圍限制：目前只有FinMind有官方真實額度查詢端點，Finnhub/永豐金/"
                  "NVIDIA都還沒有對應的監控機制。")
        if st.button("🔍 查詢用量歷史", key="key_usage_query_btn", use_container_width=True):
            if SUPABASE_CONN is None:
                st.warning("Supabase未連線，無法查詢。")
            else:
                try:
                    _ku_res = (SUPABASE_CONN.table("api_key_usage_snapshot")
                              .select("checked_at,source,token_label,used_count,limit_count")
                              .eq("source", "finmind").order("checked_at", desc=True)
                              .limit(200).execute())
                    st.session_state['key_usage_rows'] = _ku_res.data or []
                except Exception as _ku_e:
                    st.warning(f"查詢失敗：{_ku_e}")
                    st.session_state['key_usage_rows'] = []

        _ku_rows = st.session_state.get('key_usage_rows')
        if _ku_rows is not None:
            if not _ku_rows:
                st.info("目前還沒有任何歷史紀錄——排程要等到明天上午10:30左右才會第一次"
                       "記錄，之後累積幾天就能看出趨勢。")
            else:
                _ku_df = pd.DataFrame(_ku_rows)
                _ku_df['checked_at'] = pd.to_datetime(_ku_df['checked_at'])
                for _label in _ku_df['token_label'].unique():
                    _sub = _ku_df[_ku_df['token_label'] == _label].sort_values('checked_at')
                    st.markdown(f"**{_label}**")
                    st.line_chart(_sub.set_index('checked_at')[['used_count', 'limit_count']],
                                 use_container_width=True)
                st.dataframe(_ku_df[['checked_at', 'token_label', 'used_count', 'limit_count']]
                            .sort_values('checked_at', ascending=False),
                            use_container_width=True, hide_index=True)

if nav_section == "策略回測":
    with st.expander("📊 情報來源準確度 & 選股勝率PK (V160)", expanded=False):
        pk_tab1, pk_tab2 = st.tabs(["📰 情報來源準確度", "👤vs🤖 選股勝率PK"])

        with pk_tab1:
            st.caption("追蹤每個情報來源／標籤，情報發布後 3/10/20 日的實際報酬與勝率。無未來函數，未到期的自動略過。")
            _custom_d = st.number_input("自訂回顧天數（選填，例如看 60 日後）", min_value=0, max_value=120, value=0, step=5,
                                        key="intel_custom_days")
            if st.button("🔍 計算情報準確度", key="calc_intel_acc", use_container_width=True):
                _ia_t0 = time.time()
                _ia_prog = st.progress(0.0, text="補算各情報的歷史報酬中 0%")

                def _ia_cb(done, total):
                    _ia_prog.progress(done / total, text=f"補算各情報的歷史報酬中 {done}/{total}（{done/total*100:.0f}%）")

                src_df, tag_df = get_intel_accuracy_summary(
                    custom_days=_custom_d if _custom_d > 0 else None, progress_callback=_ia_cb)
                _ia_prog.empty()
                st.session_state['intel_acc_scan_meta'] = {
                    'count': len(src_df) if not src_df.empty else 0,
                    'elapsed': time.time() - _ia_t0, 'ts': datetime.now(TAIPEI_TZ).strftime('%H:%M:%S'),
                }
                if src_df.empty:
                    st.info("尚無情報紀錄，或 Supabase 未連線。先去情報注入面板存幾筆情報，過幾天再回來看。")
                else:
                    st.markdown("**依來源**")
                    st.dataframe(src_df, use_container_width=True, hide_index=True)
                    if not tag_df.empty:
                        st.markdown("**依標籤**")
                        st.dataframe(tag_df, use_container_width=True, hide_index=True)
            _ia_meta = st.session_state.get('intel_acc_scan_meta')
            if _ia_meta:
                st.caption(f"🕐 上次計算：共花 {_ia_meta['elapsed']:.1f} 秒（{_ia_meta['ts']}）")

        with pk_tab2:
            st.caption("比較「你手動加入」vs「系統查詢加入」的標的，從加入日到今天的報酬率與勝率，看誰的選股比較準。")
            if st.button("⚔️ 計算勝率PK", key="calc_pk", use_container_width=True):
                _pk_t0 = time.time()
                _pk_prog = st.progress(0.0, text="比對兩種選股方式的歷史績效中 0%")

                def _pk_cb(done, total):
                    _pk_prog.progress(done / total, text=f"比對兩種選股方式的歷史績效中 {done}/{total}（{done/total*100:.0f}%）")

                pk_df = get_manual_vs_system_pk(progress_callback=_pk_cb)
                _pk_prog.empty()
                st.session_state['pk_scan_meta'] = {'elapsed': time.time() - _pk_t0,
                                                     'ts': datetime.now(TAIPEI_TZ).strftime('%H:%M:%S')}
                if pk_df.empty:
                    st.info("尚無加入紀錄，或 Supabase 未連線。之後每次加入雷達會記錄加入日，累積一段時間再回來看。")
                else:
                    st.dataframe(pk_df, use_container_width=True, hide_index=True)
                    st.caption("樣本數太少時參考價值有限，建議累積 1-2 週的加入紀錄再看。")
            _pk_meta = st.session_state.get('pk_scan_meta')
            if _pk_meta:
                st.caption(f"🕐 上次計算：共花 {_pk_meta['elapsed']:.1f} 秒（{_pk_meta['ts']}）")

if nav_section == "策略回測":
    with st.expander("🧪 訊號命中率回測實驗室 (V158/V159)", expanded=False):
        bt_tab1, bt_tab2, bt_tab3 = st.tabs(["📈 技術訊號回測", "🎯 查1~查12 完整濾網回測",
                                            "📊 門檻校準結果（自動排程）"])

        with bt_tab1:
            st.caption("驗證範圍：價量＋均線＋大盤位階技術訊號。不含法人籌碼／基本面成分，"
                       "無未來函數——用當天收盤產生訊號，量測 3 日／10 日後的實際報酬。")

            bt_default_pool = sorted(set(list(st.session_state.get('pinned_stocks', {}).keys())
                                         + list(st.session_state.get('portfolio', {}).keys())))
            bt_stock_input = st.text_input(
                "回測股票池（逗號分隔，預設帶入你的雷達+持倉清單）",
                value=",".join(bt_default_pool) if bt_default_pool else "2330,2303,2317",
                key="bt_stock_input"
            )
            bt_c1, bt_c2, bt_c3 = st.columns(3)
            bt_years = bt_c1.slider("回測年數", 1, 5, 2, key="bt_years")
            bt_atr_mults_raw = bt_c2.text_input("ATR倍數(可多組,逗號分隔)", value="0.5,1.0,1.5",
                                                key="bt_atr_mults", help="會分別跑一次，方便比較哪個倍數的防守線比較合理")
            bt_doomsday = bt_c3.checkbox("納入末日熔斷", value=False, key="bt_doomsday")
            bt_market_regime = st.checkbox("納入大盤20MA位階濾網", value=True, key="bt_market_regime")

            if st.button("🚀 執行回測", key="bt_run_btn", use_container_width=True):
                bt_codes = [s.strip() for s in bt_stock_input.split(',') if s.strip()]
                try:
                    bt_mults = [float(x.strip()) for x in bt_atr_mults_raw.split(',') if x.strip()]
                except ValueError:
                    bt_mults = [0.5]
                    st.warning("ATR倍數格式有誤，改用預設值 0.5")

                if not bt_codes or not bt_mults:
                    st.warning("請至少輸入一檔股票代號與一組 ATR 倍數。")
                else:
                    for mult in bt_mults:
                        st.markdown(f"#### ATR 倍數 = {mult}")
                        bt_progress = st.progress(0)
                        bt_status = st.empty()

                        def _bt_progress_cb(done, total, code):
                            bt_status.caption(f"回測進度：{done}/{total}（{code}）")
                            bt_progress.progress(done / total)

                        all_rows, summary_df = run_signal_backtest(
                            bt_codes, bt_years, mult, bt_doomsday, bt_market_regime,
                            progress_callback=_bt_progress_cb, token=get_active_fm_token()
                        )
                        bt_progress.empty()
                        bt_status.empty()

                        if summary_df.empty:
                            st.warning(f"ATR={mult}：沒有產出任何有效樣本，請確認股票代號或資料區間。")
                            continue

                        st.dataframe(summary_df, use_container_width=True, hide_index=True)
                        run_id = save_backtest_run(bt_codes, bt_years, mult, bt_doomsday, bt_market_regime, all_rows)
                        st.caption(f"已寫入 SQLite（run_id={run_id}），下方「歷史回測紀錄」可隨時回顧。")

                    st.markdown("""
    **戰略判讀提示**
    - 勝率低於50%但平均報酬為正 → 該訊號屬於「大賺小賠」型，不代表訊號不好。
    - 偏多訊號的10日防守擊穿率若明顯偏高 → 代表這組ATR倍數對這批股票太緊，容易被正常洗盤掃出場，可以調高倍數再測一次比較。
    - 這裡測的是技術面單獨的表現；正式版訊號還會疊加法人籌碼與地雷警告，實際勝率可能與此不同。
                    """)

            st.divider()
            st.markdown("##### 📜 歷史回測紀錄")
            bt_runs_df = list_backtest_runs(mode='technical')
            if bt_runs_df.empty:
                st.caption("尚無回測紀錄。")
            else:
                st.dataframe(bt_runs_df, use_container_width=True, hide_index=True)
                bt_pick_id = st.selectbox("選一筆 run_id 回顧摘要", bt_runs_df['run_id'].tolist(), key="bt_pick_run")
                if bt_pick_id:
                    bt_hist_summary = load_backtest_summary(bt_pick_id)
                    # 【V160】8.1 回測完要有「所以我該怎麼做」的總結，不能只丟一張表
                    _bt_advice = build_backtest_advice(bt_hist_summary)
                    if not bt_hist_summary.empty:
                        st.dataframe(bt_hist_summary, use_container_width=True, hide_index=True)
                        # 【V160】8.1 表格下方直接給結論，不用自己解讀數字
                        st.markdown("#### 🧭 總結建議")
                        for _line in _bt_advice:
                            st.markdown(_line)

        with bt_tab2:
            st.caption("【V159，R86新增查3】驗證範圍：✅ 完整點對點回測（含正確揭露時序）：查1/2/3/4/5/6/8/9/10/12 "
                       "｜ ⚠️ 簡化版：查11（用現在股利資料回推，非逐年精確股利）、查3的股利加分部分"
                       "（同樣用現在股利當全期間常數，不是逐年精確股利，但這只影響最多±15分，不是決定性因素） "
                       "｜ ✅【R95新增】情報雷達/情報黃金交叉：重用intel_performance累積的手動情報紀錄"
                       "（含補登日期），無未來函數，可與查1~14放進同一張表比較命中率。")

            fb_default_pool = sorted(set(list(st.session_state.get('pinned_stocks', {}).keys())
                                         + list(st.session_state.get('portfolio', {}).keys())))
            fb_stock_input = st.text_input(
                "回測股票池（逗號分隔，預設帶入你的雷達+持倉清單，樣本較少較快；可自行改成更大的清單）",
                value=",".join(fb_default_pool) if fb_default_pool else "2330,2303,2317",
                key="fb_stock_input"
            )
            fb_years = st.slider("回測年數", 1, 5, 2, key="fb_years")
            fb_available_cmds = ["查1.主升段突擊", "查2.魚頭慢伏支撐", "查4.投信作帳集團股",
                                 "查5.籌碼外資霸王色", "查6.營收雙增爆發突破", "查8.昨日強勢動能延續",
                                 "查9.均線糾結爆量突破", "查10.籌碼沉澱量縮潛伏",
                                 "查11.除權息尋寶雷達 (簡化版)", "查12.K線型態尋寶型"]
            # 【R95新增】情報類條件——選項直接從intel_performance實際出現過的來源
            # 列出來，不是憑空給輸入框讓使用者亂打，避免打錯字永遠比對不到樣本。
            _fb_intel_sources = list_intel_sources()
            fb_intel_cmds = [f"情報雷達：{s}" for s in _fb_intel_sources]
            if _fb_intel_sources:
                fb_available_cmds = fb_available_cmds + fb_intel_cmds + ["🏆 情報黃金交叉（多個情報來源同時指向）"]
            fb_selected = st.multiselect("要回測的濾網條件（可多選，每個會分開統計各自的命中率）",
                                         fb_available_cmds, default=["查6.營收雙增爆發突破", "查9.均線糾結爆量突破"],
                                         key="fb_selected_cmds")
            fb_k_patterns = []
            if any("查12" in c for c in fb_selected):
                fb_k_patterns = st.multiselect("查12 要測哪些K線型態", ["長紅", "紅三兵", "長黑", "黑三兵"],
                                               default=["長紅"], key="fb_k_patterns")
            fb_market_regime = st.checkbox("納入大盤20MA位階濾網（破20MA的日子不納入樣本）",
                                           value=True, key="fb_market_regime")
            fb_intel_selected = [c for c in fb_selected if "情報雷達：" in c or "情報黃金交叉" in c]
            fb_tech_selected = [c for c in fb_selected if c not in fb_intel_selected]
            if fb_intel_selected:
                st.caption("ℹ️ 情報類條件的樣本來自你手動記錄的intel_performance，跟股票池/年數設定無關"
                           "（用的是每一則情報自己記錄的日期跟股票），選了幾個情報條件、樣本數就是"
                           "intel_performance裡對應的紀錄數，可能跟上面股票池不重疊。")

            if st.button("🚀 執行完整濾網回測", key="fb_run_btn", use_container_width=True):
                fb_codes = [s.strip() for s in fb_stock_input.split(',') if s.strip()]
                fb_cmds_clean = [c.replace(" (簡化版)", "") for c in fb_tech_selected]
                if not fb_codes and not fb_cmds_clean and not fb_intel_selected:
                    st.warning("請至少輸入一檔股票代號，並選擇至少一個濾網條件。")
                elif not fb_cmds_clean and not fb_intel_selected:
                    st.warning("請至少選擇一個濾網條件。")
                else:
                    fb_progress = st.progress(0)
                    fb_status = st.empty()

                    def _fb_progress_cb(done, total, code):
                        fb_status.caption(f"回測進度：{done}/{total}（{code}，含法人/營收歷史API拉取，較慢屬正常）")
                        fb_progress.progress(done / total)

                    fb_rows = []
                    if fb_cmds_clean and fb_codes:
                        fb_rows, _ = run_filter_backtest(
                            fb_codes, fb_years, fb_cmds_clean, fb_k_patterns, fb_market_regime,
                            token=get_active_fm_token(), dividend_db=DIVIDEND_DB,
                            progress_callback=_fb_progress_cb
                        )
                    if fb_intel_selected:
                        fb_status.caption("回測進度：情報雷達/黃金交叉計算中（逐筆查詢股價，數量多時較慢）...")
                        fb_rows = fb_rows + run_intel_radar_backtest(fb_intel_selected)
                    fb_summary = summarize_filter_backtest(fb_rows)
                    fb_progress.empty()
                    fb_status.empty()

                    if fb_summary.empty:
                        st.warning("沒有產出任何有效樣本，請確認股票代號、資料區間、濾網條件是否過於嚴格，"
                                  "或情報類條件是否有足夠已到期的intel_performance紀錄。")
                    else:
                        # 【R98續36新增，總指揮官對照外部App截圖反映「樣本太少卻用滿版
                        # 顯示100%命中率」的問題】用pandas Styler把樣本不足30筆的整列
                        # 淡化+標紅字，讓「這個命中率不能信」這件事在畫面上一眼就看到，
                        # 不是只靠底下文字提示或一個容易被忽略的布林欄位。
                        def _style_insufficient_sample(row):
                            if not row.get('樣本是否足夠', True):
                                return ['color: #ff6b6b; opacity: 0.55;'] * len(row)
                            return [''] * len(row)
                        st.dataframe(fb_summary.style.apply(_style_insufficient_sample, axis=1),
                                    use_container_width=True, hide_index=True)
                        st.caption("⚠️ 紅色淡化字的列代表樣本數<30筆——命中率/最大拉回在這種樣本量下"
                                  "容易被單一極端值主導，統計上還不夠可靠，僅供方向參考。")
                        fb_run_id = save_filter_backtest_run(fb_codes, fb_years, fb_rows)
                        st.caption(f"已寫入 SQLite（run_id={fb_run_id}）。")
                        st.markdown("""
    **戰略判讀提示**
    - 樣本數太少（例如個位數）的濾網，命中率參考價值有限，建議擴大股票池或拉長年數再看一次。
    - 同一個濾網在不同年數（1年 vs 3年）下命中率差異很大，代表這個條件對市況（多頭/空頭年）敏感，不是穩定訊號。
                        """)

                        # 【R77新增】滾動驗證(Walk-Forward)——重用剛剛已經抓好的fb_rows，
                        # 不用多打任何API，換一種切法看「這個濾網的命中率在不同期間
                        # 穩不穩定」，這是判斷門檻是不是「高原區」還是「孤峰」的關鍵。
                        _wf_df = summarize_filter_backtest_walkforward(fb_rows)
                        if not _wf_df.empty:
                            st.divider()
                            st.markdown("##### 🎯 滾動驗證（Walk-Forward）——每個濾網跨時期穩不穩定")
                            st.caption("把剛剛的回測結果按時間切成連續窗口，各自算一次命中率。"
                                      "同一個濾網如果每個窗口命中率都差不多，代表是真正穩定的訊號；"
                                      "如果某幾個窗口特別高、其他窗口卻很低，代表這個濾網可能只在"
                                      "特定市況下有效，不是普遍可信的門檻。")
                            _stability_df = assess_filter_stability(_wf_df)
                            st.markdown("**穩定性總覽**")
                            st.dataframe(_stability_df, use_container_width=True, hide_index=True)
                            with st.expander("查看每個窗口的詳細命中率", expanded=False):
                                st.dataframe(_wf_df, use_container_width=True, hide_index=True)
                            st.caption("⚠️ 標準差門檻（15/25個百分點）是合理但主觀的起始值，"
                                      "不是精算出來的鐵律——這份判讀是輔助你做決定的參考，"
                                      "最終要不要調整程式碼裡的門檻，還是要你自己看過數字再決定。")

            st.divider()
            st.markdown("##### 📜 歷史回測紀錄")
            fb_runs_df = list_backtest_runs(mode='filter')
            if fb_runs_df.empty:
                st.caption("尚無回測紀錄。")
            else:
                st.dataframe(fb_runs_df, use_container_width=True, hide_index=True)
                fb_pick_id = st.selectbox("選一筆 run_id 回顧摘要", fb_runs_df['run_id'].tolist(), key="fb_pick_run")
                if fb_pick_id:
                    fb_hist_summary = load_filter_backtest_summary(fb_pick_id)
                    if not fb_hist_summary.empty:
                        st.dataframe(fb_hist_summary, use_container_width=True, hide_index=True)

        with bt_tab3:
            # 【R87新增】門檻敏感度掃描結果——system_scheduler.py每月1號自動
            # 跑，這裡只負責讀Supabase顯示。目前只涵蓋爆量比、六日累計漲跌，
            # 不是完整12濾網門檻校準。
            st.caption("每月1號由排程自動跑一次爆量比、六日累計漲跌兩組門檻的敏感度掃描，"
                      "這裡只負責顯示結果，不會即時計算。範圍：目前只涵蓋這兩個門檻，"
                      "不是完整12濾網的自動校準。")
            if not SUPABASE_ENABLED:
                st.warning("Supabase未連線，無法讀取校準結果。")
            else:
                _tc_type = st.radio("看哪一組門檻", ["爆量比 (vol_ratio)", "六日累計漲跌 (six_day_gain)"],
                                    horizontal=True, key="tc_type_pick")
                _tc_type_key = "vol_ratio" if "vol_ratio" in _tc_type else "six_day_gain"
                try:
                    _tc_res = (SUPABASE_CONN.table("threshold_calibration_results").select("*")
                              .eq("threshold_type", _tc_type_key).order("run_date", desc=True)
                              .limit(50).execute())
                    _tc_rows = _tc_res.data if _tc_res and _tc_res.data else []
                except Exception as _tc_e:
                    _tc_rows = []
                    st.warning(f"讀取失敗（可能是尚未執行 supabase_migration_r87_threshold_calibration.sql "
                              f"建表）：{_tc_e}")
                if not _tc_rows:
                    st.info("目前還沒有掃描結果——排程要到每月1號才會自動跑第一次，"
                           "或者你可以透過側欄的GitHub Actions觸發功能立即手動執行一次"
                           "（stage選threshold_calibration）。")
                else:
                    _tc_df = pd.DataFrame(_tc_rows)
                    _tc_latest_date = _tc_df['run_date'].max()
                    _tc_latest = _tc_df[_tc_df['run_date'] == _tc_latest_date].sort_values('threshold_value')
                    st.markdown(f"**最新一次掃描（{_tc_latest_date}）**")
                    st.dataframe(_tc_latest[['threshold_value', 'sample_count', 'win_rate', 'avg_return']],
                                use_container_width=True, hide_index=True)
                    st.line_chart(_tc_latest.set_index('threshold_value')[['win_rate']])
                    st.caption("💡 判讀提示：如果曲線在某個門檻附近平穩(高原區)，代表那一帶都是可信賴的門檻；"
                              "如果曲線忽高忽低、單一點暴衝，代表那個門檻可能是樣本不足或巧合造成的孤峰，"
                              "不建議直接採用。決定要不要調整程式碼裡的門檻常數，還是要你自己看過這份數據"
                              "再決定，系統不會自動修改任何判斷邏輯。")

if nav_section == "情報覆盤":
    with st.expander("📋 情報注入面板", expanded=False):
        intel_source = st.selectbox("來源", ["股癌", "財經新聞", "法說會", "券商報告", "其他"], key="intel_source")
        intel_tag = st.text_input("標籤", key="intel_tag", placeholder="例如：財報公布、法人動向")
        # 【R88新增】補登過去日期的情報——原本永遠用「現在」當時間戳，導致
        # 算「情報準不準」的基準價抓錯。加日期選擇器，預設今天。
        intel_backdate = st.date_input("這則情報的日期（預設今天，補登舊資料時請改成正確日期）",
                                       value=datetime.now(TAIPEI_TZ).date(), key="intel_backdate")

        # 【V160新增】上傳截圖→AI辨識文字→填回文字框，加快手動輸入速度。
        # 只做「辨識文字」，不讓AI順便判斷相關標的(round29教訓：一次做太多
        # 推理品質不穩定)。
        _intel_img = st.file_uploader("📸 上傳截圖（選填，AI會辨識文字並填入下方文字框）",
                                      type=['png', 'jpg', 'jpeg'], key="intel_img_upload")
        if _intel_img is not None:
            if st.button("🖼️ AI 辨識圖片文字", key="intel_img_ocr_btn", use_container_width=True):
                with st.spinner("AI 辨識圖片中..."):
                    _ocr_res = analyze_intel_image(_intel_img.getvalue(),
                                                   mime_type=_intel_img.type or 'image/jpeg')
                if _ocr_res['ok']:
                    # 【V160 修復】跟round29同一個坑：text_area一旦有key，之後渲染時
                    # value只在session_state沒有值時才生效，必須在按鈕觸發的當下
                    # 直接寫入session_state[key]，widget下一次渲染才會讀到新值。
                    st.session_state['intel_content'] = _ocr_res['text']
                    st.success(f"✅ 辨識完成（{_ocr_res.get('model','')}），已填入下方文字框，"
                              f"請檢查辨識結果是否正確，可直接編輯修正")
                else:
                    st.warning(f"⚠️ 圖片辨識失敗：{_ocr_res['error']}，請改用手動貼上文字")

        intel_content = st.text_area("貼上報告內容（系統會自動偵測4碼代號，不用手打格式）", key="intel_content", height=150)

        # 【V160 B#12】自動偵測代號：抓內文所有4碼數字 + 比對已知股名，列出候選讓使用者確認
        _auto_codes = []
        if intel_content.strip():
            _digit_hits = set(re.findall(r'\b(\d{4})\b', intel_content))
            _digit_hits |= set(re.findall(r"\[標的代號:\s*(\d{4})\]", intel_content))  # 舊格式也相容
            # 名稱比對：內文出現的股名也抓出來
            for _c, _n in TW_STOCK_NAMES.items():
                if _n and _n in intel_content:
                    _digit_hits.add(_c)
            _auto_codes = sorted([c for c in _digit_hits if c in TW_STOCK_NAMES])

        # 【V160關鍵修復】原本偵測太寬鬆，改成偵測結果是「建議候選」，儲存前
        # 要使用者自己勾選確認。
        #
        # 【V160 Round30】AI重點摘要功能移除——實測品質不符合門檻，函式保留
        # 但不再從UI呼叫。
        if intel_content.strip():
            if _auto_codes:
                st.caption(f"🎯 自動偵測到 {len(_auto_codes)} 檔候選，請確認要綁定哪些"
                           f"（誤判的請取消勾選，例如文章用詞剛好跟股名撞名）：")
                _confirmed_codes = st.multiselect(
                    "確認要綁定的標的", options=_auto_codes,
                    default=_auto_codes,
                    format_func=lambda c: f"{c}（{TW_STOCK_NAMES.get(c, '')}）",
                    key="intel_confirm_codes")
            else:
                _confirmed_codes = []
                st.caption("⚠️ 內文中沒有偵測到可辨識的4碼代號或已知股名")
        else:
            _confirmed_codes = []

        if st.button("💾 儲存情報", key="intel_save_btn"):
            if intel_content.strip():
                if _confirmed_codes:
                    # 【R88新增】用選好的日期(可能是補登的過去日期)當這則情報的時間戳，
                    # 不再永遠寫死「現在」。時間部分固定00:00——補登的舊資料本來就
                    # 不知道精確到分鐘的時間，誠實只記錄到日期，不假裝有更精細的資訊。
                    _intel_time_str = intel_backdate.strftime("%Y-%m-%d") + " 00:00"
                    _intel_date_str = intel_backdate.strftime("%Y-%m-%d")
                    for ticker in _confirmed_codes:
                        st.session_state.intelligence_pool.setdefault(ticker, {"sources": [], "history": []})
                        if intel_source not in st.session_state.intelligence_pool[ticker]["sources"]:
                            st.session_state.intelligence_pool[ticker]["sources"].append(intel_source)
                        st.session_state.intelligence_pool[ticker]["history"].append({
                            "time": _intel_time_str,
                            "tag": intel_tag, "content": intel_content})
                        # 【V160 B#13，R88補上backdate】情報準確度追蹤：記錄基準價供之後算報酬
                        log_intel_performance(ticker, intel_source, intel_tag, intel_date=_intel_date_str)
                    save_local_db_isolated()
                    st.success(f"已綁定 {len(_confirmed_codes)} 檔標的並寫入實體大腦"
                              f"（日期：{_intel_date_str}）！")
                else:
                    st.warning("未勾選任何標的，無法綁定。請在上方候選清單中確認至少一檔。")
            else:
                st.warning("內容不能為空")

        # 【V160修復】25檔攤平成一張多選清單看不出批次。用(tag, time)當「批次」
        # 還原出「這次匯入存了哪些股票」，讓你先選批次再看那批的股票。
        _bound = st.session_state.get('intelligence_pool', {})
        if _bound:
            st.divider()
            st.markdown(f"**🗂️ 已綁定標的管理（目前共 {len(_bound)} 檔）**")

            _batch_groups = {}   # (tag, time) -> {'codes': [...], 'preview': str}
            for _c, _info in _bound.items():
                for _h in _info.get('history', []):
                    _bkey = (_h.get('tag', '（無標籤）'), _h.get('time', ''))
                    _g = _batch_groups.setdefault(_bkey, {'codes': [], 'preview': _h.get('content', '')[:40]})
                    if _c not in _g['codes']:
                        _g['codes'].append(_c)

            _batch_options = ["🔍 全部標的（不分批次）"] + sorted(
                _batch_groups.keys(), key=lambda k: k[1], reverse=True)   # 最新批次排前面

            def _batch_label(k):
                if k == "🔍 全部標的（不分批次）":
                    return f"{k}（共 {len(_bound)} 檔）"
                tag, t = k
                g = _batch_groups[k]
                return f"📦 {tag} @ {t}（{len(g['codes'])} 檔）"

            _selected_batch = st.selectbox("先選要管理的批次", options=_batch_options,
                                           format_func=_batch_label, key="intel_batch_pick")

            if _selected_batch == "🔍 全部標的（不分批次）":
                _scope_codes = sorted(_bound.keys())
                _scope_batch_key = None
            else:
                _scope_codes = sorted(_batch_groups[_selected_batch]['codes'])
                _scope_batch_key = _selected_batch
                st.caption(f"這批內容開頭：「{_batch_groups[_selected_batch]['preview']}...」")

            _to_remove = st.multiselect(
                "這個批次裡的標的（可多選要移除的）", options=_scope_codes,
                default=_scope_codes if _scope_batch_key else [],   # 選了特定批次時，預設全選方便一鍵清掉整批
                format_func=lambda c: f"{c}（{TW_STOCK_NAMES.get(c, c)}）｜共{len(_bound.get(c, {}).get('history', []))}則情報",
                key=f"intel_remove_select_{hash(_selected_batch)}")

            _rm_col1, _rm_col2 = st.columns(2)
            with _rm_col1:
                _btn_label = "🗑️ 移除勾選的標的（僅這批）" if _scope_batch_key else "🗑️ 移除勾選的標的（整檔含所有批次）"
                if st.button(_btn_label, key="intel_remove_btn",
                            disabled=not _to_remove, use_container_width=True):
                    for c in _to_remove:
                        if _scope_batch_key is not None:
                            # 【V160】只移除這個批次的那幾則情報，不動同一檔股票在
                            # 其他批次留下的紀錄——避免因為「這批匯入錯了」就連帶
                            # 誤刪這檔股票在別次真正正確的情報。
                            tag, t = _scope_batch_key
                            hist = st.session_state.intelligence_pool.get(c, {}).get('history', [])
                            st.session_state.intelligence_pool[c]['history'] = [
                                h for h in hist if not (h.get('tag') == tag and h.get('time') == t)]
                            if not st.session_state.intelligence_pool[c]['history']:
                                st.session_state.intelligence_pool.pop(c, None)
                        else:
                            st.session_state.intelligence_pool.pop(c, None)
                    save_local_db_isolated()
                    st.success(f"已移除 {len(_to_remove)} 檔標的" + ("（僅此批次）" if _scope_batch_key else ""))
                    st.rerun()
            with _rm_col2:
                if st.button("🧹 一次清空全部（不分批次）", key="intel_clear_all_btn", use_container_width=True):
                    st.session_state['intel_clear_confirm'] = True
            if st.session_state.get('intel_clear_confirm'):
                st.warning(f"⚠️ 確定要清空全部 {len(_bound)} 檔已綁定標的嗎？這個動作無法復原。")
                _cc1, _cc2 = st.columns(2)
                with _cc1:
                    if st.button("✅ 確定清空", key="intel_clear_confirm_btn", use_container_width=True):
                        st.session_state.intelligence_pool = {}
                        save_local_db_isolated()
                        st.session_state['intel_clear_confirm'] = False
                        st.success("已清空全部已綁定標的")
                        st.rerun()
                with _cc2:
                    if st.button("取消", key="intel_clear_cancel_btn", use_container_width=True):
                        st.session_state['intel_clear_confirm'] = False
                        st.rerun()

if nav_section == "盤中作戰":
    def resolve_input_to_codes(raw):
        """
        【V160】把使用者輸入（可含多個代號/名稱，逗號或空白分隔）解析成股票代號清單。
        回傳 (codes, ambiguous_msgs)。ambiguous_msgs 是模糊比對到多筆時的提示。
        """
        codes, ambiguous = [], []
        tokens = re.split(r'[,\s，、]+', raw.strip())
        for tok in tokens:
            tok = tok.strip()
            if not tok:
                continue
            digit_codes = re.findall(r'\b\d{4}\b', tok)
            if digit_codes:
                codes.extend(digit_codes)
                continue
            # 名稱精確比對
            exact = [code for code, name in TW_STOCK_NAMES.items() if name == tok]
            if exact:
                codes.append(exact[0])
                continue
            # 名稱模糊比對
            fuzzy = [code for code, name in TW_STOCK_NAMES.items() if tok in name]
            if len(fuzzy) == 1:
                codes.append(fuzzy[0])
            elif len(fuzzy) > 1:
                ambiguous.append(f"「{tok}」模糊比對到多筆：" + ', '.join(f'{m}({TW_STOCK_NAMES[m]})' for m in fuzzy[:5]))
            else:
                ambiguous.append(f"「{tok}」找不到對應代號")
        # 去重保序
        seen, uniq = set(), []
        for c in codes:
            if c not in seen:
                seen.add(c); uniq.append(c)
        return uniq, ambiguous


    def _add_codes_to(target_key, codes, label):
        """把 codes 加進 target_key（pinned_stocks 或 observe_stocks），加入前驗證報價。
        【V160】新加入的股票排在最前面（看盤時新標的一眼可見）。

        【R96修復】原本用序列for迴圈逐一驗證每個代號的報價——get_real_stock_
        data_yfinance()內部有FinMind失敗才退回yfinance、.TW/.TWO兩種副檔名
        嘗試的重試邏輯，單一代號最壞情況可能要好幾秒到十幾秒，總指揮官反映
        「加入兩檔要等3分鐘以上」正是這種序列等待疊加起來的結果。改用
        ThreadPoolExecutor平行驗證，跟這個專案其餘地方（calculate_signals_
        worker批次運算、產業龍頭查詢等）同一套模式，不會因為代號數量增加
        而線性拖慢。
        """
        added, failed = [], []
        if not codes:
            return
        _ctx = get_script_run_ctx()

        def _validate_one(_code):
            if _ctx is not None:
                try:
                    add_script_run_ctx(threading.current_thread(), _ctx)
                except Exception:
                    pass
            try:
                hist_check, _ = get_real_stock_data_yfinance(_code)
                return _code, (hist_check is not None and len(hist_check) >= 21)
            except Exception as e:
                print(f"[_add_codes_to-診斷] {_code} 驗證報價失敗：{type(e).__name__}: {e}")
                return _code, False

        with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(codes))) as _executor:
            _futures = [_executor.submit(_validate_one, c) for c in codes]
            for _fut in concurrent.futures.as_completed(_futures):
                _code, _ok = _fut.result()
                if _ok:
                    added.append(_code)
                    log_watchlist_entry(_code, "manual")   # 【V160 B#14】記錄手動加入
                else:
                    failed.append(_code)
        # 保持跟原本輸入順序一致（平行完成順序不等於輸入順序，排最前面要照使用者
        # 輸入的順序排，不是誰先驗證完誰排前面）
        added = [c for c in codes if c in set(added)]
        if added:
            # 新加入的排最前面：新 codes 先放，再接原本的（去除重複）
            old = st.session_state.get(target_key, {})
            new_dict = {}
            for c in added:
                new_dict[c] = "手動加入"
            for c, v in old.items():
                if c not in new_dict:
                    new_dict[c] = v
            st.session_state[target_key] = new_dict
            save_local_db_isolated()
            st.success(f"✅ 已加入{label}（排最前）：{', '.join(added)}")
            time.sleep(0.6)
            st.rerun()
        if failed:
            st.error(f"⚠️ 這些代號抓不到有效報價（興櫃/冷門/剛下市/資料源暫缺），已略過：{', '.join(failed)}")


    search_input = st.text_input("🔍 手動股票代號/名稱輸入框（可一次多檔，用逗號分隔，如：2330,2303,聯電）", "")
    _add_c1, _add_c2 = st.columns(2)
    with _add_c1:
        add_observe_clicked = st.button("👁️ 加入觀察區", use_container_width=True,
                                        help="先丟著看幾天的候選，不列入長期追蹤。之後覺得可以再升級到常態雷達。")
    with _add_c2:
        add_radar_clicked = st.button("🎯 直接加入常態雷達", use_container_width=True,
                                      help="確定要長期盯盤的核心標的。")

    if add_observe_clicked or add_radar_clicked:
        q = search_input.strip()
        if not q:
            st.warning("請先輸入至少一個代號或名稱。")
        else:
            codes, ambiguous = resolve_input_to_codes(q)
            for msg in ambiguous:
                st.warning("⚠️ " + msg)
            if codes:
                if add_observe_clicked:
                    _add_codes_to('observe_stocks', codes, "觀察區")
                else:
                    _add_codes_to('pinned_stocks', codes, "常態雷達")
            elif not ambiguous:
                st.error("⚠️ 找不到任何有效代號。提示：中文名只認得證交所清單裡的股票，冷門股請直接輸入4碼代號。")


    def render_action_buttons(card, code, is_portfolio, section_key='pinned_stocks'):
        btn_suffix = "_port" if is_portfolio else ("_obs" if section_key == 'observe_stocks' else "_pin")
        st.session_state.analysis_history.setdefault(code, {'nv_history': [], 'gm_history': [], 'cl_history': []})

        # 【R80修復】K線圖按鈕跟同產業族群這兩段完全沒有try/except保護，是
        # 「底部區塊看不到」的真正根因。整個函式從這裡到結尾全部包住，不管
        # 未來新增什麼功能都不會再讓卡片下半部消失。
        try:
            if st.button("📈 K線圖（含MA5/20/60＋布林通道＋成交量＋MACD＋RSI）",
                         key=f"kline_face_{code}{btn_suffix}", use_container_width=True):
                st.session_state[f'show_kline_{code}'] = not st.session_state.get(f'show_kline_{code}', False)
            if st.session_state.get(f'show_kline_{code}'):
                # 【V160 修復】render_kline_chart(symbol, hist) 需要兩個參數，
                # 先前只傳 code 導致 TypeError。跟展開區內那顆用同一套取資料方式。
                with st.spinner("繪製K線圖中..."):
                    _khist_face, _ = get_real_stock_data_yfinance(code)
                    render_kline_chart(code, _khist_face, key_suffix=btn_suffix)
        except Exception as _kline_e:
            # 【R96資安修正】這個區塊會呼叫yfinance抓股價繪圖，網路例外訊息
            # 可能包含請求細節，不直接顯示在UI上，完整內容改印到伺服器log。
            print(f"[K線圖繪製-診斷] 失敗：{type(_kline_e).__name__}: {_kline_e}")
            st.error("⚠️ K線圖繪製失敗，不影響卡片其他部分（詳細原因已寫入伺服器log）。")

        try:
            with st.expander("🏭 同產業族群強弱（簡化版，非供應鏈圖譜）", expanded=False):
                # 【R97續24修復，總指揮官要求全面性檢查API/快取使用狀況才
                # 抓到的隱藏成本】這個面板原本是「一進expander就無條件執行」
                # ——st.expander的body不管展開或收合都會執行(R96已知的坑)，
                # 這裡對每張股票卡片最多查15檔同業的yfinance資料，代表
                # 「不管使用者有沒有要看這個功能」，每次渲染卡片都要付出
                # 這筆成本。fetch_industry_map()/get_real_stock_data_
                # yfinance()本身雖然各自有st.cache_data保護(1天/3分鐘)，
                # 重複查詢有快取效益，但「初次登入、快取還是冷的」時，
                # 這仍然是強制成本，不是使用者要看才算的懶載入設計。
                # 改成按鈕觸發，真正做到「沒人點就不算」。
                _peer_cache_key = f"peer_strength_{code}{btn_suffix}"
                if st.button("📊 計算同產業族群強弱", key=f"peer_calc_btn_{code}{btn_suffix}",
                           use_container_width=True):
                    st.session_state[_peer_cache_key] = True
                if st.session_state.get(_peer_cache_key):
                    stock_to_ind, ind_to_stocks = fetch_industry_map()
                    ind = stock_to_ind.get(code)
                    if not ind:
                        st.caption("查無此股票的產業分類資料（FinMind TaiwanStockInfo 未提供）。")
                    else:
                        st.caption(f"產業分類：{ind}｜這是「同產業分類」不是真正的上下游供應鏈關聯，"
                                   f"用來快速看同族群個股今日強弱、抓輪動股。")
                        # 【R95修復】ind_to_stocks順序來自FinMind原始順序，不是市值
                        # 排序。真正市值資料是付費限定，改用「今日成交值」當免費代理
                        # 指標標記交易最熱絡的一檔，誠實標註非真正市值排名。
                        peers = [s for s in ind_to_stocks.get(ind, []) if s != code and s in TW_STOCK_NAMES][:15]
                        peer_rows = []
                        for p in peers:
                            hp, _ = get_real_stock_data_yfinance(p)
                            if hp is not None and len(hp) >= 2:
                                _pc = float(hp['Close'].iloc[-1])
                                _prev = float(hp['Close'].iloc[-2])
                                # 【V160緊急修復】沒有防呆「前一天收盤價是0」的
                                # 情況直接當分母，導致ZeroDivisionError整頁崩潰。
                                # 誠實跳過這檔算不出漲跌%的同業，不拖垮整個面板。
                                if _prev <= 0:
                                    continue
                                pg = (_pc - _prev) / _prev * 100
                                _turnover_value = _pc * float(hp['Volume'].iloc[-1])
                                peer_rows.append({'代號': p, '名稱': TW_STOCK_NAMES.get(p, p),
                                                  '現價': round(_pc, 2), '漲跌%': round(pg, 2),
                                                  '_turnover': _turnover_value})
                        # 【R96修復，重大邏輯錯誤，見開發歷程.md】自己是固定龍頭
                        # (FIXED_INDUSTRY_LEADERS)時，不再排除自己去挑一個不具代表性
                        # 的假龍頭比較，改成顯示「本身即為產業龍頭」說明。
                        _is_self_fixed_leader = (ind in FIXED_INDUSTRY_LEADERS
                                                 and FIXED_INDUSTRY_LEADERS[ind][0] == code)
                        if _is_self_fixed_leader:
                            st.info(f"👑 {card.get('name', code)}（{code}）本身即為「{ind}」的產業龍頭"
                                   f"（固定龍頭對照表登記），沒有上層龍頭可以比較領先/落後——"
                                   f"下面仍列出同業排行供參考，但不套用「領先龍頭過多」這類判斷"
                                   f"（那是給跟風股用的，不適用於龍頭股本身）。")
                        if peer_rows:
                            _leader_code = max(peer_rows, key=lambda r: r['_turnover'])['代號']
                            for r in peer_rows:
                                r['名稱'] = ("👑 " + r['名稱']) if r['代號'] == _leader_code else r['名稱']
                                del r['_turnover']
                            peer_df = pd.DataFrame(peer_rows).sort_values('漲跌%', ascending=False).reset_index(drop=True)
                            st.dataframe(peer_df, use_container_width=True, hide_index=True)
                            st.caption("👑 標記今日成交值(現價×成交量)最大者，當作族群內交投最熱絡個股的"
                                       "免費代理指標——不是真正的市值排名（市值資料在FinMind是付費限定），"
                                       "僅供快速參考，非嚴謹產業龍頭認定。")
                            # 【R96新增，族群強弱獨立面板】接上5分K三關第二關的
                            # evaluate_gate2_leader_deviation()，同一套1.5倍偏離門檻，
                            # 兩種時間尺度共用。自己是固定龍頭時跳過這段判斷。
                            _leader_row = next((r for r in peer_rows if r['代號'] == _leader_code), None)
                            _my_gain = card.get('gain')
                            if not _is_self_fixed_leader and _leader_row is not None and _my_gain is not None:
                                try:
                                    _deviation = evaluate_gate2_leader_deviation(
                                        float(_my_gain), float(_leader_row['漲跌%']))
                                    _dcolor = {"pass": "#ff4d4d", "fail": "#00e676",
                                              "unknown": "#aaa"}.get(_deviation['verdict'], "#aaa")
                                    st.markdown(f"<div style='margin-top:8px; padding:8px; "
                                               f"border-left:3px solid {_dcolor}; background:#1a1a1a;'>"
                                               f"<strong style='color:{_dcolor};'>{_deviation['label']}</strong>"
                                               f"<div style='font-size:12px; color:#aaa; margin-top:4px;'>"
                                               f"{_deviation['detail']}</div></div>", unsafe_allow_html=True)
                                except Exception:
                                    pass
                        else:
                            st.caption("同產業標的目前沒有可用的即時資料。")
        except Exception as _peer_e:
            # 【R96資安修正】這個區塊內部會呼叫yfinance查詢同業股價，網路例外
            # 訊息可能包含請求細節，不直接顯示在UI上，完整內容改印到伺服器log。
            print(f"[同產業族群面板-診斷] 發生錯誤：{type(_peer_e).__name__}: {_peer_e}")
            st.error("⚠️ 同產業族群面板發生錯誤，不影響卡片其他部分（詳細原因已寫入伺服器log）。")

        # 【R76修復】展開區標題改明講內容涵蓋分點/同步，避免誤以為功能消失。
        # 【R78修復】整個展開區內容包成一個try/except——最後一道防線，避免
        # 任何未來新增的功能忘記加防呆時拖垮整張卡片。
        with st.expander("⚙️ 資料校正／單檔同步／分點分析／人工覆寫", expanded=True):
            try:
                if is_admin() and st.button("🚀 執行單檔精準同步 (籌碼+融資+大戶)", key=f"btn_sync_single_{code}{btn_suffix}",
                             use_container_width=True):
                    # 【R95修復】改用st.progress()+progress_cb取代st.spinner()，
                    # 四個子查詢各自完成時真的推進百分比，不是假動畫。
                    _sync_prog = st.progress(0.0, text=f"正在同步 {code}（0%）")

                    def _sync_cb(pct, label):
                        _sync_prog.progress(min(1.0, max(0.0, pct)), text=f"{label}（{int(pct * 100)}%）")

                    success, msg = sync_single_stock_finmind(code, progress_cb=_sync_cb)
                    _sync_prog.empty()
                    if success:
                        st.success(f"✅ {code} {msg}！")
                        # 【V160】同步後自動重整，免得還要手動按重新整理才看到最新資料
                        st.rerun()
                    else:
                        st.warning(f"⚠️ {code} {msg}")
                    time.sleep(1.5)
                    st.rerun()

                # 【V160 新增：單檔分點CSV拖曳區「隔日沖照妖鏡」，R72加註自動化說明】
                st.markdown("<div style='font-size:13px; font-weight:bold; color:#f1c40f; margin-top:10px;'>"
                            "📂 單檔分點CSV拖曳區（隔日沖照妖鏡＋週轉率）</div>", unsafe_allow_html=True)
                st.caption("【R72】排程現在會每個交易日收盤後自動幫「系統模擬倉持倉＋你的常態持倉／"
                          "雷達清單」抓分點資料（資料源：HiStock，免費、不用登入），下面的"
                          "「🔍分點連續性分析」會自動累積、不用你手動處理。這個CSV上傳區保留當備援："
                          "①想查排程沒追蹤到的股票；②HiStock哪天改版失效時的退路。")
                st.caption("到證交所買賣日報表查詢系統（bsr.twse.com.tw/bshtm/）查這檔股票、下載CSV，"
                           "拖曳上傳即可一次拿到全部分點明細——比手動輸入5家完整，但需要你先去下載"
                           "（官方有機器人驗證擋自動化，只能手動查）。跟下面的手動輸入5家是互補關係："
                           "有CSV時用CSV，臨時沒下載時用手動輸入。")
                _csv_file = st.file_uploader("拖曳證交所分點CSV", type=['csv'],
                                             key=f"broker_csv_{code}{btn_suffix}")

                # 【R78新增】排程補救按鈕——今天自動排程剛好沒抓到這一檔時，
                # 直接手動補一次，用跟排程完全同一套邏輯(fetch_branch_data_
                # with_fallback，FinMind優先失敗才退回HiStock)。
                # 【R81補充】先試網頁版直接連線，失敗才顯示GitHub Actions備援。
                if is_admin() and st.button(f"🔄 立即補跑今天的{code}分點（FinMind優先，不等排程）",
                            key=f"histock_catchup_{code}{btn_suffix}", use_container_width=True):
                    with st.spinner(f"正在查詢{code}今日分點資料（FinMind優先，失敗才試HiStock）..."):
                        _hs_df = fetch_branch_data_with_fallback(code, datetime.now(TAIPEI_TZ).strftime('%Y-%m-%d'))
                        if _hs_df is None or _hs_df.empty:
                            st.warning("⚠️ FinMind跟網頁版直連HiStock都失敗——可能是Streamlit Cloud的IP被HiStock"
                                      "特殊處理（已證實TDCC有這個問題，HiStock可能也一樣），"
                                      "或FinMind這個資料集目前帳號等級沒有權限。"
                                      "改用下面的按鈕觸發GitHub Actions（用不會被擋的IP執行，"
                                      "但會抓全市場、比較慢，這檔的資料明天應該就會有）。")
                            st.session_state[f'histock_direct_failed_{code}'] = True
                        elif not SUPABASE_ENABLED:
                            st.warning("Supabase未連線，無法存入歷史。")
                        else:
                            try:
                                _hs_rows = [{
                                    'symbol': code, 'log_date': datetime.now(TAIPEI_TZ).strftime('%Y-%m-%d'),
                                    'broker_name': str(r['broker_name']),
                                    'buy_shares': int(r['buy_shares']), 'sell_shares': int(r['sell_shares']),
                                    'net_shares': int(r['net_shares']),
                                } for _, r in _hs_df.iterrows()]
                                SUPABASE_CONN.table("broker_flows").upsert(
                                    _hs_rows, on_conflict="symbol,log_date,broker_name").execute()
                                st.success(f"✅ 已補跑成功，存入 {len(_hs_rows)} 筆分點紀錄。")
                                time.sleep(1)
                                st.rerun()
                            except Exception as _hs_e:
                                # 【R96資安修正】Supabase寫入例外訊息可能包含連線URL，
                                # 不直接顯示在UI上，完整內容改印到伺服器log。
                                print(f"[券商分點補跑-診斷] 寫入失敗：{type(_hs_e).__name__}: {_hs_e}")
                                st.warning("寫入失敗（詳細原因已寫入伺服器log，非資料本身有誤，"
                                          "可能是暫時性連線問題，稍後可以再試一次）。")

                if st.session_state.get(f'histock_direct_failed_{code}'):
                    if is_admin() and st.button("🔄 改用GitHub Actions觸發全市場分點抓取（較慢但不會被擋）",
                                key=f"histock_gh_catchup_{code}{btn_suffix}", use_container_width=True):
                        with st.spinner("正在觸發GitHub Actions..."):
                            _ok, _msg = trigger_github_workflow("broker_flows")
                            if _ok:
                                st.success(f"✅ {_msg}")
                            else:
                                st.warning(f"⚠️ {_msg}")

                if _csv_file is not None:
                    _csv_df = parse_broker_csv(_csv_file.read())
                    if _csv_df is None or _csv_df.empty:
                        st.warning("⚠️ 解析失敗——請確認這份CSV是證交所買賣日報表查詢系統下載的原始檔案，"
                                  "沒有被Excel等軟體另存新檔改過編碼。")
                    else:
                        _vol_today = card.get('vol')
                        _vol_today_shares = int(_vol_today * 1000) if _vol_today else None
                        _analysis = analyze_broker_csv(_csv_df, _vol_today_shares)
                        if _analysis:
                            _a1, _a2 = st.columns(2)
                            with _a1:
                                _conc = _analysis['concentration_pct']
                                _conc_color = "#ff4d4d" if _conc and _conc > 5.0 else "#888"
                                st.markdown(f"<div style='color:{_conc_color};'>📊 前5大集中度：<b>{_conc}%</b></div>",
                                           unsafe_allow_html=True)
                            with _a2:
                                # 【R97續19修復，總指揮官要求全面深度複查抓到】這裡原本沒帶
                                # sb=SUPABASE_CONN，是全站唯一一處呼叫fetch_shares_outstanding
                                # 卻完全繞過180天快取+失敗退避機制的地方——每次上傳CSV分析都
                                # 直接打FinMind，跟R97續14/續18想解決的問題（額度浪費、拖慢
                                # 其他功能）是同一個病灶，只是這裡漏掉沒補到。
                                _shares_out = fetch_shares_outstanding(code, get_active_fm_token(),
                                                                       sb=SUPABASE_CONN)
                                if _shares_out and _analysis['total_shares']:
                                    _turnover = round(_analysis['total_shares'] / _shares_out * 100, 2)
                                    st.markdown(f"🔄 週轉率：<b>{_turnover}%</b>", unsafe_allow_html=True)
                                else:
                                    st.caption("週轉率：發行股數抓不到，無法計算")

                            # 【隔日沖照妖鏡】>20%時亮紅色大標籤警告，符合規格書要求
                            _dt_pct = _analysis['day_trader_pct']
                            if _dt_pct is not None and _dt_pct > 20.0:
                                st.markdown(
                                    f"<div style='background:#7a1010; border:2px solid #ff4d4d; border-radius:6px; "
                                    f"padding:10px; margin-top:8px;'>"
                                    f"<b style='color:#ff4d4d; font-size:15px;'>🚨 隔日沖佔比 {_dt_pct}%</b><br>"
                                    f"<span style='color:#ffcccc; font-size:12px;'>疑似隔日沖分點買超佔當日成交量"
                                    f"超過20%，明日開高走低倒貨風險偏高，留意進場時機。</span></div>",
                                    unsafe_allow_html=True)
                            elif _dt_pct is not None:
                                st.caption(f"隔日沖佔比：{_dt_pct}%（低於20%警戒門檻）")

                            with st.expander("查看前5大買超分點明細", expanded=False):
                                st.dataframe(pd.DataFrame(_analysis['top5_table']),
                                            use_container_width=True, hide_index=True)
                            st.caption("⚠️ 分點底下客戶眾多，出現在買超榜不代表這筆一定是隔日沖操作——"
                                      "這是警示參考，不是確定的判決。")

                            # 【R67新增】把這份分點資料存下來，累積成歷史。這是分點功能
                            # 真正的價值所在：單看一天只知道「今天誰買最多」，累積幾天後
                            # 才能回答「這家是連續建倉還是買完就跑」。
                            _bf_date = st.date_input(
                                "這份CSV是哪一天的資料？（存進歷史用，預設今天）",
                                value=datetime.now(TAIPEI_TZ).date(), key=f"bf_date_{code}{btn_suffix}")
                            if is_admin() and st.button("💾 存入分點歷史（累積後可看連續性分析）",
                                         key=f"bf_save_{code}{btn_suffix}", use_container_width=True):
                                _saved = sb_log_broker_flows(code, _bf_date.strftime('%Y-%m-%d'), _csv_df)
                                if _saved:
                                    st.success(f"✅ 已存入 {_saved} 筆分點紀錄（{_bf_date}）。"
                                              f"多存幾天之後，下面的連續性分析才會有判斷力。")
                                    time.sleep(1)
                                    st.rerun()
                                else:
                                    st.warning("寫入失敗（Supabase未連線？或尚未執行 "
                                              "supabase_migration_r67_broker_flows.sql 建立 broker_flows 表）")

                # 【R67新增，R77加防護】分點連續性分析——不放在「上傳CSV」的if
                # 裡，沒上傳新CSV也該看得到過去累積的分析結果。外面包try/except
                # 避免連線問題讓其他區塊也一起消失。
                try:
                    _bf_rows, _bf_pairs = get_broker_continuity(code)
                except Exception as _bf_e:
                    _bf_rows, _bf_pairs = [], []
                    st.caption(f"（分點連續性分析暫時無法載入，不影響下面其他功能：{_bf_e}）")
                if _bf_rows:
                    # 【R95續26新增】分點成熟度標示——累積天數不足10個交易日時
                    # 明確標「僅供參考」，天數是誠實的事實陳述不是猜測。
                    try:
                        _bf_days, _bf_mature = get_broker_data_maturity(code)
                    except Exception:
                        _bf_days, _bf_mature = 0, True   # 查詢失敗時不额外顯示警語，避免誤導成「一定不足」
                    _bf_maturity_label = (f"（已累積 {_bf_days} 個交易日）" if _bf_mature
                                          else f"（僅累積 {_bf_days} 個交易日，未達10日，趨勢判讀僅供參考）")
                    with st.expander(f"🔍 分點連續性分析（已累積 {len(_bf_rows)} 家分點的多日紀錄）"
                                     f"{'' if _bf_days == 0 else ' ' + _bf_maturity_label}",
                                     expanded=False):
                        if _bf_days and not _bf_mature:
                            st.warning(f"⚠️ 這檔股票的分點資料目前只累積了{_bf_days}個交易日（未達10日）——"
                                      f"分點只能往後累積、沒有歷史回補，剛開始關注的股票需要一段時間才能看出"
                                      f"真正的連續買賣趨勢，這段期間的判讀請保守看待。")
                        st.caption("這是分點資料累積後才能回答的問題：誰是連續買進的真主力、"
                                  "誰是買一天隔天就倒的隔日沖。連續買超天數是從最近一天往回數，"
                                  "遇到第一個賣超日就停。")
                        st.dataframe(pd.DataFrame(_bf_rows), use_container_width=True, hide_index=True)
                        st.caption("⚠️ 判讀邏輯是啟發式規則（連續買超≥3天且累計淨買為正→疑似真建倉；"
                                  "出現≥3天但買賣幾乎相抵→疑似隔日沖），不是精算模型。同一分點底下"
                                  "客戶眾多，也可能是多個不相干的人剛好都在買，請當作參考而非結論。")

                        # 【R75新增】對作分點警示——原本只有「隔日沖名單命中」這個靜態
                        # 名單比對，這裡新增真正的模式偵測：同一天買超龍頭跟賣超龍頭
                        # 量體接近，疑似左手倒右手。
                        if _bf_pairs:
                            st.markdown("**⚠️ 疑似對作分點（同日買超/賣超龍頭量體接近）**")
                            st.dataframe(pd.DataFrame(_bf_pairs), use_container_width=True, hide_index=True)
                            st.caption("量體接近≥80%才會列在這裡。這是模式偵測，不是證據——"
                                      "兩個分點剛好同一天買賣量接近，也可能只是巧合（大盤震盪時"
                                      "常見），不代表真的是同一批資金操作。")

                        # 【R75/R77修復】分點連續性視覺化改長條圖，一眼看出力道對比。
                        # st.bar_chart的color參數格式跨版本不完全相容，加try/except
                        # 避免一個小圖表壞掉拖垮整張卡片後面所有內容。
                        try:
                            _viz_df = pd.DataFrame(_bf_rows).head(10)
                            if not _viz_df.empty:
                                st.markdown("**分點累計買超力道圖（前10家，依累計買超排序）**")
                                _viz_chart_df = _viz_df.set_index('券商')[['累計買超(張)']]
                                st.bar_chart(_viz_chart_df)
                        except Exception as _viz_e:
                            st.caption(f"（長條圖繪製失敗，不影響上面的表格資料：{_viz_e}）")

                # 【V160 延伸2 校正機制】總指揮官提出的構想：把「猜測」變成「有已知誤差範圍的估計」
                st.markdown("<div style='font-size:13px; font-weight:bold; color:#f1c40f; margin-top:10px;'>"
                            "📐 主力成本校正（輸入籌碼K線前五大券商買均價，系統自動取平均並比較誰更準）</div>",
                            unsafe_allow_html=True)
                _mf = card.get('mf_cost') or {}
                _our_est = _mf.get('heavy_vwap') or _mf.get('vwap20')
                if _our_est:
                    st.caption(f"我們的估計（爆量均價優先，其次VWAP20）：**{_our_est}** 元。"
                               f"到籌碼K線「買方Top15」查前五大券商的買均價，連同券商名稱一起填進來，"
                               f"系統會自動算五家均值、記錄每家的誤差，累積後還能比較「哪家券商的數字"
                               f"跟我們的估計比較一致」。")
                    st.caption("⚠️ 誠實說明：這比較的是「哪家券商數字比較貼近我們的估計」，"
                              "不是絕對客觀的準確度——我們沒有標準答案可以核對，只能互相參照。")

                    # 【V160 R41 新增】天期選擇器：讓歷史校正紀錄能區分「這是短線建倉
                    # 還是波段建倉」的均價，不同天期混在一起統計會互相稀釋，之後覆盤時
                    # 才能看出「這家券商在20日波段特別準，但5日極短線誤差較大」這種細節。
                    _hold_period = st.selectbox("這次填的是哪個天期的建倉成本？",
                                                ["5日", "10日", "20日", "60日"],
                                                key=f"cal_period_{code}{btn_suffix}",
                                                help="對應你在籌碼K線查詢時選的統計天數")

                    # 【V160】3組擴為5組——同一檔股票的前五大買方，不是全台前五大券商
                    # （後者對特定股票不見得相關，見說明文字）。5家平均能再降低雜訊，
                    # 邊際效益超過5家後遞減，所以停在5不繼續往上加。
                    #
                    # 【R98續102新增，總指揮官指示補上先前列為「之後有空再做」的優化】
                    # 查HiStock均價當預設值帶入，總指揮官依然可以直接修改/覆蓋，
                    # 不是強制鎖定，純粹省去「已經有系統知道的資料還要重打一次」
                    # 這個負擔。用code+btn_suffix當快取key，避免同一次頁面重繪
                    # 重複查詢。
                    _top5_cache_key = f"top5_broker_{code}{btn_suffix}"
                    if _top5_cache_key not in st.session_state:
                        st.session_state[_top5_cache_key] = fetch_broker_avg_price(code, limit=5)
                    _top5_prefill = st.session_state[_top5_cache_key]
                    if _top5_prefill:
                        st.caption(f"💡 已自動帶入HiStock均價當預設值(共{len(_top5_prefill)}家)，"
                                  f"可直接修改或覆蓋，不是強制鎖定。")

                    # 【R98續103新增，總指揮官指示：實作「額外展開完整清單」】
                    # 總指揮官問「極致能到多少」，查證發現排程端抓HiStock沒有
                    # 硬性上限(頁面呈現多少存多少)，實測最多見過28家——5家上限
                    # 純粹是版面設計選擇(5個並排欄位)，不是資料不夠。這裡額外
                    # 提供一個展開區塊，用表格呈現資料庫裡實際抓到的完整清單
                    # (上限30，涵蓋實測過的最大值再留餘裕)，不佔用平常的版面，
                    # 總指揮官需要看更完整全貌時才展開，平常維持簡潔的5欄快速
                    # 輸入不受影響。
                    with st.expander(f"📋 查看完整清單（不限5家，資料庫裡實際抓到的全部）",
                                     expanded=False):
                        _full_cache_key = f"full_broker_{code}{btn_suffix}"
                        if _full_cache_key not in st.session_state:
                            st.session_state[_full_cache_key] = fetch_broker_avg_price(code, limit=30)
                        _full_list = st.session_state[_full_cache_key]
                        if _full_list:
                            st.caption(f"共{len(_full_list)}家券商有均價資料(依買超張數排序)——"
                                      f"這裡純顯示參考，如果想把某家納入上面5欄的計算，"
                                      f"直接在上面的下拉選單裡手動選那家券商即可。")
                            st.dataframe(
                                pd.DataFrame([{
                                    '券商': r['broker_name'], '買均價': r['avg_price'],
                                    '買超張數': r['net_shares'],
                                } for r in _full_list]),
                                use_container_width=True, hide_index=True)
                        else:
                            st.caption("這檔目前查無均價資料(可能還沒有分點歷史、或HiStock均價還沒抓到)。")

                    _b_cols = st.columns(5)
                    _brokers = []
                    for _i in range(5):
                        with _b_cols[_i]:
                            # 【R98續102】如果這一欄有對應的HiStock預抓資料，用它當
                            # selectbox的預設選中值+number_input的預設數字；沒有的
                            # 話維持原本「（未選擇）」+0.0的空白狀態，行為完全不變。
                            _prefill_name = _top5_prefill[_i]["broker_name"] if _i < len(_top5_prefill) else None
                            _prefill_price = _top5_prefill[_i]["avg_price"] if _i < len(_top5_prefill) else None
                            _select_options = ["（未選擇）"] + COMMON_BROKER_BRANCHES + ["✏️ 其他（手動輸入）"]
                            if _prefill_name and _prefill_name in COMMON_BROKER_BRANCHES:
                                _default_idx = _select_options.index(_prefill_name)
                            elif _prefill_name:
                                # 預抓到的券商名稱不在常見清單裡，退回「其他手動輸入」
                                # 那個選項，並用text_input的value直接帶入名稱
                                _default_idx = len(_select_options) - 1
                            else:
                                _default_idx = 0
                            # 【V160 新增】券商名稱改用下拉選單，避免手打錯字（總指揮官回報的需求）。
                            # 清單外的分點選「其他（手動輸入）」，下面會多跳出一個輸入框，
                            # 不會因為不在清單裡就選不了。
                            _bpick = st.selectbox(f"券商{_i+1}", _select_options,
                                                  index=_default_idx,
                                                  key=f"cal_bpick_{_i}_{code}{btn_suffix}")
                            if _bpick == "✏️ 其他（手動輸入）":
                                _bname = st.text_input("輸入券商/分點名稱", key=f"cal_bname_{_i}_{code}{btn_suffix}",
                                                       value=(_prefill_name or "") if _prefill_name and _prefill_name not in COMMON_BROKER_BRANCHES else "",
                                                       placeholder="例如 凱基-台中")
                            elif _bpick == "（未選擇）":
                                _bname = ""
                            else:
                                _bname = _bpick
                            _bprice = st.number_input(f"買均價", min_value=0.0, step=0.1, format="%.2f",
                                                      value=float(_prefill_price) if _prefill_price else 0.0,
                                                      key=f"cal_bprice_{_i}_{code}{btn_suffix}")
                            # 【V160 R41 新增】買超張數——這是算籌碼集中度的分子(前5大買超
                            # 張數加總 ÷ 當日總成交量)，也是判斷「買超第一名是不是隔日沖
                            # 分點」需要的資料(要知道誰的張數最高才知道誰是第一名)。
                            _prefill_shares = _top5_prefill[_i]["net_shares"] if _i < len(_top5_prefill) else None
                            _bshares = st.number_input(f"買超張數", min_value=0, step=1,
                                                       value=int(_prefill_shares) if _prefill_shares else 0,
                                                       key=f"cal_bshares_{_i}_{code}{btn_suffix}")
                            if _bname.strip() and _bprice > 0:
                                _brokers.append((_bname.strip(), _bprice, _bshares))

                    # 【V160 R41新增，R66升級】籌碼集中度+隔日沖警示——只在這裡
                    # 顯示，不進排程自動選股評分。
                    # 【R66】累積到10筆歷史後改用「這次比過去百分之幾高」取代
                    # 死板的5%門檻，不足10筆時仍用5%當保底。
                    _total_shares_input = sum(s for _, _, s in _brokers if s > 0)
                    _concentration = None
                    if _total_shares_input > 0:
                        _vol_today = float(card.get('vol', 0) or 0)
                        if _vol_today > 0:
                            _concentration = _total_shares_input / _vol_today * 100
                            _pctl, _hist_n = get_concentration_percentile(code, _concentration)
                            if _pctl is not None:
                                _conc_color = "#ff4d4d" if _pctl >= 80 else "#888"
                                _conc_note = (f" ⚠️ 高於這檔股票過去{_hist_n}筆紀錄的{_pctl:.0f}%"
                                             if _pctl >= 80 else f"（這檔股票歷史第{_pctl:.0f}百分位，基於{_hist_n}筆紀錄）")
                            else:
                                _conc_color = "#ff4d4d" if _concentration > 5.0 else "#888"
                                _conc_note = ((' ⚠️ 超過5%起跑門檻（樣本不足10筆前的保底門檻）')
                                             if _concentration > 5.0 else '（樣本不足10筆，暫用5%保底門檻，累積夠了會自動改跟自己歷史比）')
                            st.markdown(f"<div style='font-size:13px; color:{_conc_color};'>"
                                       f"📊 籌碼集中度（前5大買超張數/當日成交量）：<b>{_concentration:.2f}%</b>"
                                       f"{_conc_note}</div>",
                                       unsafe_allow_html=True)
                        else:
                            st.caption("（當日成交量資料不足，無法計算集中度）")

                        # 隔日沖警示：找出買超張數最高的那家，比對是否命中已知名單(靜態+動態)
                        _top_buyer = max(_brokers, key=lambda x: x[2]) if _brokers else None
                        _dyn_brokers3 = get_dynamic_day_trader_brokers(SUPABASE_CONN) if SUPABASE_CONN else {}
                        if _top_buyer and _top_buyer[2] > 0 and check_day_trader_alert(_top_buyer[0], _dyn_brokers3):
                            _tier_note = _dyn_brokers3.get(_top_buyer[0], "")
                            _tier_str = f"（動態統計：{_tier_note}）" if _tier_note else ""
                            st.warning(f"⚠️ 買超第一名「{_top_buyer[0]}」疑似隔日沖分點{_tier_str}——"
                                      f"同一分點底下客戶眾多，這不代表這筆一定是隔日沖操作，"
                                      f"但今天大買、留意隔天是否開高倒貨。")

                        # 【R98新增，總指揮官指示：隔日沖佔比欄位自動化】用broker_flows
                        # 批次資料算隔日沖佔比，不用再手動上傳CSV。這裡跟前面的
                        # check_day_trader_alert警示是互補：前者只看買超第一名單一
                        # 分點，這裡算的是「所有命中名單的分點合計」佔前15大買超的
                        # 比重，資訊更完整。
                        if SUPABASE_CONN:
                            try:
                                _latest_date_res = (SUPABASE_CONN.table("broker_flows")
                                                     .select("log_date").eq("symbol", code)
                                                     .order("log_date", desc=True).limit(1).execute())
                                _latest_bf_date = (_latest_date_res.data[0]["log_date"]
                                                   if _latest_date_res.data else None)
                            except Exception:
                                _latest_bf_date = None
                            if _latest_bf_date:
                                _dt_ratio = compute_day_trader_ratio_from_broker_flows(
                                    SUPABASE_CONN, code, _latest_bf_date, dynamic_brokers=_dyn_brokers3)
                                if _dt_ratio["ratio_pct"] is not None:
                                    _r = _dt_ratio["ratio_pct"]
                                    _r_color = "#ff4d4d" if _r > 20.0 else "#888"
                                    st.markdown(
                                        f"<div style='color:{_r_color}; font-size:13px;'>"
                                        f"📐 隔日沖佔比(前15大買超口徑，{_latest_bf_date})：<b>{_r}%</b>"
                                        f"{'　⚠️超過20%警戒門檻' if _r > 20.0 else ''}"
                                        f"　<span style='color:#666; font-size:11px;'>"
                                        f"(命中分點：{'、'.join(_dt_ratio['matched_brokers']) or '無'})"
                                        f"</span></div>", unsafe_allow_html=True)
                                    st.caption("⚠️ 此比重基準是broker_flows前15大買超合計，"
                                              "不是當日總成交量——與CSV手動分析的「佔當日總成交量」"
                                              "定義不同，僅供互相參考，不可直接比較數字。")

                                # 【R98新增，總指揮官指示：買賣家數差代理指標】
                                _bs_diff = compute_buyer_seller_branch_diff_proxy(
                                    SUPABASE_CONN, code, _latest_bf_date)
                                if _bs_diff["diff_proxy"] is not None:
                                    _bs_color = "#00c853" if _bs_diff["is_concentrated_proxy"] else "#888"
                                    st.markdown(
                                        f"<div style='color:{_bs_color}; font-size:13px;'>"
                                        f"⚖️ 買賣家數差(代理)：買{_bs_diff['buyer_branch_count']}分點 "
                                        f"− 賣{_bs_diff['seller_branch_count']}分點 = "
                                        f"<b>{_bs_diff['diff_proxy']:+d}</b>"
                                        f"{'　🎯籌碼集中(代理判定)' if _bs_diff['is_concentrated_proxy'] else ''}"
                                        f"</div>", unsafe_allow_html=True)
                                    st.caption("⚠️ 代理指標——broker_flows只有前15大分點，"
                                              "算的是「分點家數」不是CMoney原版定義的「帳戶數」，"
                                              "僅供方向性參考，數值不可直接比較CMoney原版報告的數字。")

                    if is_admin() and st.button("💾 記錄校正（自動算均值＋逐家分開記錄）",
                                 key=f"cal_save_{code}{btn_suffix}", use_container_width=True):
                        if len(_brokers) >= 1:
                            _prices_only = [(n, p) for n, p, _s in _brokers]
                            _avg = round(sum(p for _, p in _prices_only) / len(_prices_only), 2)
                            _ok_all = True
                            for _bname, _bprice, _bshares in _brokers:
                                _ok_all = sb_log_cost_calibration(
                                    code, _our_est, _bprice, "券商個別", _bname,
                                    buy_shares=_bshares if _bshares > 0 else None,
                                    holding_period=_hold_period) and _ok_all
                            _ok_all = sb_log_cost_calibration(
                                code, _our_est, _avg, "五家均值", "五家均值",
                                holding_period=_hold_period, concentration_pct=_concentration) and _ok_all
                            if _ok_all:
                                _err = (_our_est - _avg) / _avg * 100 if _avg else 0
                                st.success(f"✅ 已記錄 {len(_brokers)} 家券商＋均值（{_hold_period}天期）：我們 {_our_est} "
                                          f"vs 均值 {_avg}，誤差 {_err:+.1f}%")
                                time.sleep(1)
                                st.rerun()
                            else:
                                st.warning("部分寫入失敗（Supabase 未連線？或尚未執行 supabase_migration_extensions.sql "
                                          "新增 broker_name 欄位）")
                        else:
                            st.warning("請至少填一組「券商名稱＋買均價」。")

                    _cal_rows = sb_get_cost_calibration(code)
                    _cal_sum = summarize_calibration(_cal_rows)
                    if _cal_sum:
                        st.caption(f"📊 這檔已校正 {_cal_sum['count']} 筆｜平均絕對誤差 "
                                   f"**{_cal_sum['mean_abs_err']}%**｜誤差≤10%的比例 "
                                   f"{_cal_sum['within_10pct']}%｜{_cal_sum['bias']}")
                        _by_broker = summarize_calibration_by_broker(_cal_rows)
                        if len(_by_broker) > 1:
                            st.markdown("**券商準確度排行（越前面跟我們的估計越接近）**")
                            st.dataframe(pd.DataFrame([
                                {'券商': b, '筆數': s['count'], '平均絕對誤差%': s['mean_abs_err'],
                                 '誤差≤10%比例': s['within_10pct'], '偏差方向': s['bias']}
                                for b, s in _by_broker.items()
                            ]), use_container_width=True, hide_index=True)
                else:
                    st.caption("目前這檔的主力成本估計不可用（股價資料不足），無法校正。")

                # 【V160 新增】深度財報分析（毛利率/ROE/現金流品質），按需查詢不進批次掃描
                st.markdown("<div style='font-size:13px; font-weight:bold; color:#00c853; margin-top:10px;'>"
                            "📊 深度財報分析（毛利率／ROE／現金流品質）</div>", unsafe_allow_html=True)
                st.caption("這三個指標定位是「30秒判斷要不要繼續看」的快篩，不是要取代財報狗的完整"
                           "多年度趨勢分析——真的要做投資決策，仍建議去財報狗查完整資料再確認。")
                if st.button("📊 查詢深度財報", key=f"fin_health_btn_{code}{btn_suffix}",
                             use_container_width=True):
                    # 【R95修復】原本st.spinner()整段查詢完全沒有進度，容易
                    # 超過5分鐘讓使用者以為沒反應。改用st.progress()，三張表
                    # 查完各自推進一次百分比。
                    _fh_prog = st.progress(0.0, text="查詢深度財報中（0%）")

                    def _fh_cb(pct, label):
                        _fh_prog.progress(min(1.0, max(0.0, pct)), text=f"{label}（{int(pct * 100)}%）")

                    _fh = fetch_financial_health_cached(code, get_active_fm_token(), progress_cb=_fh_cb)
                    _fh_prog.empty()
                    st.session_state[f'fin_health_{code}'] = _fh

                _fh = st.session_state.get(f'fin_health_{code}')
                if _fh:
                    _fh_c1, _fh_c2, _fh_c3 = st.columns(3)
                    _fh_c1.metric("毛利率", f"{_fh['gross_margin']}%" if _fh['gross_margin'] is not None else "—")
                    _fh_c2.metric("ROE(年化估計)", f"{_fh['roe']}%" if _fh['roe'] is not None else "—")
                    _fh_c3.metric("營業現金流/淨利", f"{_fh['cash_quality']}x" if _fh['cash_quality'] is not None else "—")
                    if _fh.get('quarter_date'):
                        st.caption(f"資料季度：{_fh['quarter_date']}")
                    if _fh.get('cash_quality_note'):
                        st.caption(_fh['cash_quality_note'])

                    # 【R98續2新增，總指揮官指示：財報體質P2】負債比/流動比率/
                    # 自由現金流3項新指標。
                    # 【R98續19修正】原本是「利息保障倍數」，已確認FinMind
                    # 資料源沒有利息費用這個獨立科目，改用流動比率——見
                    # fetch_financial_health()裡的完整說明。
                    _fh_d1, _fh_d2, _fh_d3 = st.columns(3)
                    _fh_d1.metric("負債比", f"{_fh['debt_ratio']}%" if _fh.get('debt_ratio') is not None else "—")
                    _fh_d2.metric("流動比率",
                                 f"{_fh['current_ratio']}%" if _fh.get('current_ratio') is not None else "—")
                    _fh_d3.metric("自由現金流",
                                 f"{_fh['free_cash_flow']:,.0f}千元" if _fh.get('free_cash_flow') is not None else "—")

                    # 財務風險綜合評分（本系統自行設計，非CMoney報告提及的
                    # 「股魚」原版Z-Score公式重現，見compute_financial_risk_score說明）
                    _risk = compute_financial_risk_score(_fh)
                    if _risk:
                        _risk_color = {"低風險": "#00c853", "中風險": "#ffab00", "高風險": "#ff4d4d"}[_risk['level']]
                        # 【R98續41新增，總指揮官指示：圓形量表視覺化】呼應總指揮官
                        # 提供的參考截圖(外部App用圓形量表呈現0-100分數，比純文字/
                        # st.metric數字更直覺)——用Plotly的go.Indicator(gauge+
                        # number)畫圓形量表，分數本身沒有改變、判斷邏輯完全沒動，
                        # 純粹是同一個數字換一種呈現方式。三段顏色區間(綠/黃/紅)
                        # 對應低/中/高風險，跟現有的_risk_color判斷邏輯共用同一組
                        # 門檻，不會有「量表顏色」跟「文字判斷」兜不起來的風險。
                        try:
                            import plotly.graph_objects as go
                            _risk_fig = go.Figure(go.Indicator(
                                mode="gauge+number",
                                value=_risk['score'],
                                number={'suffix': "分", 'font': {'size': 28}},
                                gauge={
                                    'axis': {'range': [0, 100], 'tickwidth': 1},
                                    'bar': {'color': _risk_color},
                                    'steps': [
                                        {'range': [0, 40], 'color': '#1b3a2f'},
                                        {'range': [40, 70], 'color': '#4a3a12'},
                                        {'range': [70, 100], 'color': '#4a1f1f'},
                                    ],
                                    'threshold': {'line': {'color': _risk_color, 'width': 3},
                                                 'thickness': 0.8, 'value': _risk['score']},
                                },
                            ))
                            _risk_fig.update_layout(height=180, margin=dict(l=20, r=20, t=30, b=10),
                                                    paper_bgcolor='rgba(0,0,0,0)',
                                                    font={'color': _risk_color, 'family': "Arial"})
                            st.markdown(f"<div style='color:{_risk_color}; font-size:14px; font-weight:bold;'>"
                                        f"⚖️ 財務風險綜合評分（{_risk['level']}）</div>", unsafe_allow_html=True)
                            st.plotly_chart(_risk_fig, use_container_width=True,
                                           key=f"risk_gauge_{code}")
                        except Exception as _gauge_e:
                            # 量表畫不出來時(理論上不該發生，但防禦性處理)，優雅退回
                            # 原本的純文字顯示，不讓整張戰卡因為畫圖失敗而壞掉。
                            st.markdown(f"<div style='color:{_risk_color}; font-size:14px; font-weight:bold;'>"
                                        f"⚖️ 財務風險綜合評分：{_risk['score']}分（{_risk['level']}）</div>",
                                        unsafe_allow_html=True)
                        st.caption(f"依據：{', '.join(_risk['available_indicators'])}"
                                  + (f"｜缺資料：{', '.join(_risk['missing_indicators'])}"
                                     if _risk['missing_indicators'] else "")
                                  + "（本系統自行設計的綜合評分，非特定第三方Z-Score公式的重現，"
                                    "指標數量不同的股票之間分數不完全可比）")

                    # 【R98續2新增】3種股價估值模型——用既有fetch_pe_history()
                    # 抓最新一筆PER/PBR/殖利率，不重複造輪子。
                    # 【R98續35新增，方案A】本益比法改用MOPS真實近四季合計EPS
                    # (fetch_latest_real_eps)，比反推法精確。查不到真實EPS時
                    # compute_valuation_models內部會自動退回原本的反推邏輯。
                    _pe_hist = fetch_pe_history(code, get_active_fm_token(), years=1)
                    if _pe_hist is not None and not _pe_hist.empty:
                        _latest_row = _pe_hist.sort_values('date').iloc[-1]
                        _cur_price = card.get('price', 0.0)
                        _real_eps_info = fetch_latest_real_eps(code, SUPABASE_CONN)
                        _real_ttm_eps = _real_eps_info.get('ttm_eps') if _real_eps_info else None
                        _val_models = compute_valuation_models(
                            _cur_price, _latest_row.get('PER'), _latest_row.get('PBR'),
                            _latest_row.get('dividend_yield'), _fh.get('roe'),
                            real_ttm_eps=_real_ttm_eps)
                        _verdict_label = {'undervalued': '💰低估', 'fair': '⚖️合理', 'overvalued': '🔥高估'}
                        st.markdown("<div style='font-size:13px; font-weight:bold; color:#00d2ff; "
                                    "margin-top:8px;'>📐 3種股價估值模型（僅供參考，非投資建議）</div>",
                                    unsafe_allow_html=True)
                        _vm_c1, _vm_c2, _vm_c3 = st.columns(3)
                        for _vm_col, _vm_key, _vm_name in (
                            (_vm_c1, 'pe_method', '本益比法'),
                            (_vm_c2, 'yield_method', '殖利率法'),
                            (_vm_c3, 'k_value_method', 'K值法'),
                        ):
                            _vm = _val_models.get(_vm_key)
                            if _vm:
                                _v_label = _verdict_label.get(_vm['verdict'], '')
                                _vm_col.metric(_vm_name, f"{_vm['fair_price']}", _v_label)
                            else:
                                _vm_col.metric(_vm_name, "—")
                        # 【R98續35】誠實揭露本益比法用的是真實EPS還是反推估算值。
                        _pe_method = _val_models.get('pe_method')
                        if _pe_method and _pe_method.get('eps_source') == 'real_ttm':
                            _eps_note = (f"✅ 本益比法用的是MOPS真實近{_real_eps_info['seasons_used']}季"
                                        f"合計EPS {_pe_method['eps_estimate']}元"
                                        + ("" if _real_eps_info['is_ttm_complete']
                                           else f"（⚠️目前只有{_real_eps_info['seasons_used']}季資料，"
                                                f"不是完整近四季，合理價會偏低，建議補齊更多季度）"))
                        else:
                            _eps_note = "ℹ️ 本益比法用的是「現價÷本益比」反推的估算EPS（資料庫還沒有這檔的真實財報）"
                        st.caption(_eps_note)
                        st.caption("⚠️ 3種模型皆為粗略估值框架（本益比法預設倍數14、殖利率法預設"
                                  "期望殖利率6%、K值法預設期望ROE 10%），不是精確目標價，工具終究"
                                  "只是工具，無法取代投資判斷。")

                    # 【R98續23新增，總指揮官方向C：河流圖】完全沿用twse_
                    # market_snapshot既有的(close_price, pe)每日快照反推
                    # 隱含EPS+估值帶，不需要額外資料源。
                    # 【R98續23新增，總指揮官方向C：河流圖】改用FinMind
                    # TaiwanStockPER多年歷史(fetch_pe_history在sb=None
                    # 時直接查FinMind，跟_backtest_one_stock()同一個已
                    # 驗證過的路徑)，不用twse_market_snapshot那個目前
                    # 歷史還太淺(多數個股僅5~25天pe資料)的表。
                    _river = compute_valuation_river(code, get_active_fm_token(), years=3)
                    if _river is not None:
                        _river_verdict_color = {
                            '低估': '#00c853', '偏低': '#69f0ae', '合理': '#ffab00',
                            '偏高': '#ff8a65', '高估': '#ff5252',
                        }[_river['verdict']]
                        st.markdown("<div style='font-size:13px; font-weight:bold; color:#00d2ff; "
                                    "margin-top:8px;'>🌊 河流圖（PE歷史百分位，R98續23新增）</div>",
                                    unsafe_allow_html=True)
                        st.markdown(f"目前PE {_river['today_pe']:.1f}倍　"
                                  f"<span style='color:{_river_verdict_color}; font-weight:bold;'>"
                                  f"{_river['verdict']}</span>"
                                  f"（相對自己近{_river['n_days']}個交易日的PE分布）",
                                  unsafe_allow_html=True)
                        _th = _river['thresholds']
                        _river_chart_df = pd.DataFrame({
                            '本益比(PE)': _river['series'],
                            '20%低估線': _th['p20'], '40%偏低線': _th['p40'],
                            '60%合理線': _th['p60'], '80%偏高線': _th['p80'],
                        })
                        st.line_chart(_river_chart_df, use_container_width=True)
                        st.caption("藍線(本益比)高於「80%偏高線」代表目前PE貴過這段歷史80%的交易日；"
                                  "低於「20%低估線」代表便宜過80%的交易日。"
                                  "⚠️直接對本益比本身做歷史百分位（不是反推股價估值帶），"
                                  "只反映「跟自己過去比貴不貴」，不代表合理股價，"
                                  "也不是精確目標價，僅供方向參考。")
                    elif SUPABASE_CONN is not None:
                        st.caption("🌊 河流圖：目前查不到這檔股票足夠的PE歷史資料"
                                  "（可能是興櫃股、新上市股，或FinMind這次查詢暫時失敗）。")
                elif f'fin_health_{code}' in st.session_state:
                    # 【R98續72修復，總指揮官反映「查詢深度財報失敗無反應」】原本
                    # 這裡固定顯示「可能是興櫃股或資料尚未公佈」，但查log發現真正
                    # 原因常常是FinMind額度用盡(rate_limited)，這個訊息完全沒反映
                    # 真正原因，容易讓總指揮官誤以為系統壞掉。改成先判斷FinMind
                    # 額度現況，額度用盡時給出「稍後再試」這種明確、可行動的訊息，
                    # 不是額度問題時才顯示原本「可能是興櫃股」的說法。
                    if is_finmind_likely_exhausted():
                        st.warning("⚠️ 查詢失敗：FinMind今日額度可能已用盡(這幾天大量MOPS回補"
                                  "+財報查詢消耗較多)，不是這檔股票真的沒有資料。建議稍後"
                                  "(額度通常隔天重置)再查一次，或明天再試。")
                    else:
                        st.caption("查無財報資料（可能是興櫃股或資料尚未公佈）。")

                st.markdown("<div style='font-size:13px; font-weight:bold; color:#00d2ff; margin-top:10px;'>✏️ 人工覆寫 (7日後自動過期恢復)</div>",
                            unsafe_allow_html=True)
                m_cols = st.columns([1, 1, 1])
                m_month = m_cols[0].text_input("月份", value="06月", key=f"my_mo_{code}{btn_suffix}")
                _cur_yoy = card.get('rev_yoy')
                m_y = m_cols[1].number_input("營收年增(%)", -100.0, 1000.0,
                                             float(_cur_yoy) if _cur_yoy is not None else 0.0, 0.1,
                                             key=f"my_y_{code}{btn_suffix}")

                b_cols = st.columns([2, 1])
                _cur_bh = card.get('big_holder')
                b_ratio = b_cols[0].number_input("大戶比例(%)", 0.0, 100.0,
                                                 float(_cur_bh) if isinstance(_cur_bh, (int, float)) else 0.0, 0.1,
                                                 key=f"my_bh_{code}{btn_suffix}")
                b_date = b_cols[1].text_input("大戶日期", value=datetime.now(TAIPEI_TZ).strftime("%m/%d"),
                                              key=f"my_b_date_{code}{btn_suffix}")

                b1, b2 = st.columns(2)
                if b1.button("✅ 寫入覆寫", key=f"btn_override_{code}{btn_suffix}", use_container_width=True):
                    now_ts = datetime.now(TAIPEI_TZ).timestamp()
                    st.session_state.revenue_override[code] = {
                        'yoy': m_y, 'mom': card.get('rev_mom') if card.get('rev_mom') is not None else 0.0,
                        'month': m_month, 'ts': now_ts}
                    if b_ratio > 0:
                        st.session_state.bigholder_override[code] = {'ratio': b_ratio, 'date': b_date, 'ts': now_ts}
                        safe_upsert_big_holder(code, f"{datetime.now(TAIPEI_TZ).year}-{b_date.replace('/', '-')}", b_ratio)
                    save_local_db_isolated()
                    st.success("資料鎖定成功！")
                    time.sleep(0.5)
                    st.rerun()
                if b2.button("🗑️ 解除鎖定", key=f"btn_clear_ov_{code}{btn_suffix}", use_container_width=True):
                    st.session_state.revenue_override.pop(code, None)
                    st.session_state.bigholder_override.pop(code, None)
                    save_local_db_isolated()
                    st.success("已解除人工資料，恢復 API 模式！")
                    time.sleep(0.5)
                    st.rerun()

                if st.button("🤖 解鎖 NVIDIA 戰略推演", key=f"ai_single_{code}{btn_suffix}", use_container_width=True):
                    st.session_state.single_ai_trigger = code
                    # 【R97修復】原本st.spinner只有一句不會變的文字，最壞情況要等
                    # 2.5分鐘（5個模型逾時修好後），畫面看起來像當機。改用st.status
                    # 明確標示「正在嘗試哪個模型」，總指揮官等待時能看到進度而不是
                    # 猜測系統死了沒有。
                    with st.status("NVIDIA 輪替陣列推演中...", expanded=True) as _ai_status:
                        st.caption("依序嘗試多個模型，找到第一個可用的就會回傳結果，"
                                  "單一模型最多等30秒後自動換下一個。")
                        rep = execute_single_stock_ai(
                            card, direction=(card.get('intraday_gate') or {}).get('direction', 'long'))
                        st.session_state.single_ai_report[code] = rep
                        # 【V160 修復】只有「成功的推演」才存進歷史時光膠囊。失敗訊息（模型下架/連線逾時
                        # 等）不存，否則歷史區會被一堆「三個模型都無法使用」的錯誤訊息塞滿、變得雜亂。
                        _is_error = ('無法使用' in rep or '模型不存在' in rep or 'Error code' in rep
                                     or rep.strip().startswith('⚠️'))
                        if not _is_error:
                            st.session_state.analysis_history[code]['nv_history'].append(
                                {"time": datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d %H:%M"), "report": rep})
                            save_local_db_isolated()
                        _ai_status.update(label="推演完成" if not _is_error else "推演失敗",
                                          state="complete" if not _is_error else "error")
                    st.info(rep)

                # 【V160 B#12】戰卡一鍵匯出純文字（可複製貼到外部 Gemini/Claude/NVIDIA 網頁版）
                if st.button("📋 匯出戰卡純文字（供外部AI分析）", key=f"export_txt_{code}{btn_suffix}", use_container_width=True):
                    st.session_state[f'card_text_{code}'] = build_card_text_report(card)
                if st.session_state.get(f'card_text_{code}'):
                    st.text_area("複製以下全文，貼到外部AI分析：", value=st.session_state[f'card_text_{code}'],
                                 height=200, key=f"card_text_area_{code}{btn_suffix}")

                # 【V160新功能】互動式K線圖(純用yfinance股價，不需付費資料源)
                # 【V160】K線圖按鈕已搬到戰卡最外層，這裡不再重複放。


            except Exception as _panel_e:
                # 【R96資安修正】這個區塊包含NVIDIA API呼叫等網路請求，例外訊息
                # 可能包含請求細節，不直接顯示在UI上，完整內容改印到伺服器log。
                print(f"[戰卡展開區塊-診斷] 內部發生錯誤：{type(_panel_e).__name__}: {_panel_e}")
                st.error("⚠️ 這個展開區塊內部發生錯誤，不影響卡片其他部分（詳細原因已寫入伺服器log）。")
        with st.expander("📥 貼上外部網頁版情報與裁決 (三方會審區)", expanded=False):
            c1, c2 = st.columns(2)
            nv_val = c1.text_area("📝 NVIDIA (DeepSeek)", height=80, key=f"nv_txt_{code}{btn_suffix}")
            gm_val = c2.text_area("📝 Gemini 分析", height=80, key=f"gm_txt_{code}{btn_suffix}")
            cl_val = st.text_area("👑 Claude 總裁決 (將存入歷史)", height=80, key=f"cl_txt_{code}{btn_suffix}")

            # 【V160 B#12】三方會審一鍵總結：把三份外部分析+原始戰卡數據，用NVIDIA整合成最終結論
            if st.button("⚖️ NVIDIA 三方會審總結", key=f"synth_{code}{btn_suffix}", use_container_width=True):
                if nv_val or gm_val or cl_val:
                    with st.spinner("整合三方分析中..."):
                        _ctext = build_card_text_report(card)
                        _summary = synthesize_three_way_review(_ctext, nv_val or "（無）", gm_val or "（無）", cl_val or "（無）")
                    st.session_state[f'synth_result_{code}'] = _summary
                else:
                    st.warning("請至少貼上一份外部分析再產生總結。")
            if st.session_state.get(f'synth_result_{code}'):
                st.success("【三方會審總結】")
                st.info(st.session_state[f'synth_result_{code}'])

            if st.button("💾 儲存 Claude 裁決至時光膠囊", key=f"save_cl_{code}{btn_suffix}", use_container_width=True):
                if cl_val:
                    ts = datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d %H:%M")
                    st.session_state.analysis_history[code]['cl_history'].append({
                        "time": ts, "report": cl_val,
                        "snapshot": f"收盤:{card.get('price'):.2f} | 外資5日:{card.get('f_5d'):.0f}張 | 爆量:{card.get('vol_ratio'):.1f}x | 價值分:{card.get('value_score')}"
                    })
                    if gm_val:
                        st.session_state.analysis_history[code]['gm_history'].append({"time": ts, "report": gm_val})
                    save_local_db_isolated()
                    st.success("✅ 已寫入時光膠囊！")
                    time.sleep(0.5)
                    st.rerun()
                else:
                    st.warning("請先輸入 Claude 裁決報告！")

        hist_pack = st.session_state.analysis_history[code]
        if hist_pack['nv_history'] or hist_pack['cl_history'] or hist_pack['gm_history']:
            with st.expander("🗂️ 歷史時光膠囊覆盤區", expanded=False):
                # 【V160 修復】顯示時也過濾掉舊的錯誤訊息（之前版本存進去的「模型無法使用」等），
                # 讓畫面乾淨；並提供清空按鈕，讓使用者能一鍵清掉累積的雜亂紀錄。
                def _clean_hist(items):
                    out = []
                    for h in items:
                        r = h.get('report', '')
                        if ('無法使用' in r or '模型不存在' in r or 'Error code' in r
                                or r.strip().startswith('⚠️')):
                            continue
                        out.append(h)
                    return out
                _nv = _clean_hist(hist_pack['nv_history'])
                _gm = _clean_hist(hist_pack['gm_history'])
                _cl = _clean_hist(hist_pack['cl_history'])
                if st.button("🧹 清空這檔的歷史紀錄", key=f"clear_hist_{code}{btn_suffix}"):
                    st.session_state.analysis_history[code] = {'nv_history': [], 'gm_history': [], 'cl_history': []}
                    save_local_db_isolated()
                    st.rerun()
                h1, h2, h3 = st.tabs(["NVIDIA", "Gemini", "Claude"])
                with h1:
                    if _nv:
                        for h in reversed(_nv[-5:]):
                            st.info(f"**{h['time']}**\n\n{h['report']}")
                    else:
                        st.caption("尚無成功的推演紀錄。")
                with h2:
                    if _gm:
                        for h in reversed(_gm[-5:]):
                            st.info(f"**{h['time']}**\n\n{h['report']}")
                    else:
                        st.caption("尚無紀錄。")
                with h3:
                    if _cl:
                        for h in reversed(_cl[-10:]):
                            st.success(f"**{h['time']}**\n\n{h['report']}")
                    else:
                        st.caption("尚無紀錄。")

        m_cols = st.columns(2)
        if is_portfolio:
            # 【V160 R44新增】移除前可選填出場價，記一筆完整交易供風報比/MDD/
            # 資金曲線統計用。不強迫填，留白或0就是原本行為直接移除不留紀錄。
            _exit_price_input = st.number_input(
                "出場價格（選填，填了會記錄這筆交易供風報比/MDD統計；留0不記錄）",
                min_value=0.0, step=0.1, format="%.2f", key=f"exit_price_{code}{btn_suffix}")
            if m_cols[0].button("從持倉移除", key=f"del_port_{code}{btn_suffix}", use_container_width=True):
                if _exit_price_input > 0:
                    _p_data = st.session_state.portfolio.get(code, {})
                    _entry_p = safe_float(_p_data.get('entry_price', 0))
                    _qty = safe_float(_p_data.get('qty', 1)) or 1
                    # 【向下相容】沒有side欄位的舊持倉(這次改動之前建立的)一律當做多，
                    # 這是既有資料的唯一合理預設——它們建立時系統只支援做多。
                    _side = _p_data.get('side', 'long')
                    _logged = sb_log_manual_trade(code, _entry_p, _exit_price_input, _qty, side=_side)
                    if _logged:
                        st.success(f"✅ 已記錄 {code} 這筆交易（{_entry_p}→{_exit_price_input}）")
                st.session_state.portfolio.pop(code, None)
                save_local_db_isolated()
                st.rerun()
        else:
            # 【V160】依所在區塊決定「移除」要從哪個清單刪（觀察區 vs 常態雷達）
            this_section = section_key or 'pinned_stocks'
            remove_label = "移出觀察區" if this_section == 'observe_stocks' else "移出雷達"

            # 【V160 新增：觀察區轉持倉支援做空】方向選擇器——若戰卡當下判定是
            # 偏空防守/轉弱謹慎，預設自動選做空；其他情況預設做多。使用者永遠
            # 可以自己改，這只是省去每次手動切換的預設值。
            _sig = card.get('signal_text', '')
            _default_short = ('偏空防守' in _sig) or ('轉弱' in _sig)
            _side_pick = st.radio("轉入持倉的方向", ["🔴 做多 (LONG)", "🔵 做空 (SHORT)"],
                                  index=1 if _default_short else 0,
                                  key=f"side_pick_{code}{btn_suffix}", horizontal=True)
            _side_val = "short" if "做空" in _side_pick else "long"

            if m_cols[0].button("轉移至持倉", key=f"mov_pin_{code}{btn_suffix}", use_container_width=True):
                st.session_state.portfolio[code] = {"entry_price": card.get('price', 0.0), "qty": 1,
                                                     "side": _side_val}
                # 【R98續27修復，總指揮官反映KeyError】原本直接
                # st.session_state[this_section].pop(code, None)，假設
                # this_section這個key一定已經存在——但「查詢深度財報」的
                # 快速查詢入口(quick_overview)呼叫這個函式時傳的
                # section_key='quick_overview_pick'，這個key從來沒有被
                # 初始化成session_state裡的dict過(這張卡片本來就不是從
                # 雷達/觀察區來的，只是使用者臨時查詢單一檔)。改用.get()
                # 給預設空dict，key不存在時pop空dict自然什麼都不做，不會
                # 再對一個根本不存在的list硬要「移除」而炸掉——這也才是
                # 語意正確的行為，這張卡片本來就沒有在任何清單裡，「移出」
                # 這個動作對它而言本來就該是no-op。
                st.session_state.get(this_section, {}).pop(code, None)
                save_local_db_isolated()
                # 【R98續77新增，總指揮官反映「按轉移至此倉沒反應」】邏輯
                # 本身沒問題(資料確實有正確寫入，總指揮官在常態持倉模擬倉
                # 確實看到了)，問題是這裡直接st.rerun()、完全沒有明確的
                # 成功提示——rerun後這張卡片因為已經移出清單而直接消失，
                # 使用者很難注意到到底發生了什麼，誤以為按了沒反應。跟
                # 系統其他地方(例如_add_codes_to新增股票)已經確立的「成功
                # 提示+短暫停留+才rerun」慣例不一致，這裡補上，行為一致。
                st.success(f"✅ 已轉移 {code} 至持倉（{'做多' if _side_val == 'long' else '做空'}），"
                          f"預設1張、成本價{card.get('price', 0.0)}，記得去「總指揮常態持倉」調整正確張數。")
                time.sleep(0.8)
                st.rerun()
            # 【R98續27新增，連動修復】上面的KeyError修好後，這裡還有一個
            # 語意問題：quick_overview_pick這種「臨時查詢單一檔」的卡片
            # 本來就不在雷達/觀察區清單裡，卻還是會顯示「移出雷達」按鈕，
            # 誤導使用者以為這張卡片真的在雷達清單裡。只在this_section是
            # 真正有在維護的清單(pinned_stocks/observe_stocks)時才顯示
            # 這個按鈕，臨時查詢卡片不顯示，語意才正確。
            if this_section in ('pinned_stocks', 'observe_stocks'):
                if m_cols[1].button(remove_label, key=f"del_pin_{code}{btn_suffix}", use_container_width=True):
                    st.session_state.get(this_section, {}).pop(code, None)
                    save_local_db_isolated()
                    st.rerun()


    # ==============================================================================
    # 十一之二、清單管理區塊（V160：觀察區/常態雷達 共用，含搜尋/篩選/批次勾選刪除/快取）
    # ==============================================================================
    # 決策判定分類（供篩選下拉；對應 determine_signal 的五種輸出）
    VERDICT_OPTIONS = ["🔥 偏多攻擊", "🟡 觀察偏多", "⚖️ 中立震盪", "⚠️ 轉弱謹慎", "🔵 偏空防守"]


    def compute_cards_cached(codes, config_payload, cache_token):
        """
        算出一組 codes 的卡片，並用 session_state 快取。cache_token 改變才重算，
        否則直接用快取——這樣使用者勾選/搜尋/篩選時不會每次都重算 yfinance（避免頓）。
        回傳 {code: card_dict}（只含成功算出的）。

        【R96新增】這裡兩處attach_live_quotes都傳fetch_intraday_extras=True——
        這個函式算的是「持倉/雷達/觀察」區塊的完整戰卡（渲染成一張一張的完整
        box，不是戰情速覽那種精簡表格），跟「查看單一檔完整戰卡」屬於同一類
        情境：檔數通常不多（用戶自己在追蹤的持倉+雷達），多查VWAP/9:30三關
        這兩項成本可以接受，資料完整比省那一點查詢時間更重要。

        【V160 修復】總指揮官回報開機/重整要等5分鐘。這裡原本重算時是序列迴圈
        （一檔算完才算下一檔），改用跟「全市場掃描」引擎完全相同、已經驗證過的
        ThreadPoolExecutor 平行處理模式——8檔同時算，理論上能把這段時間縮到
        約1/8。搭配 get_real_stock_data_yfinance 新增的 st.cache_data 快取
        （見該函式註解），這是這輪對開機速度影響最大的兩個修復。
        """
        cache = st.session_state.get('card_cache', {})
        if st.session_state.get('card_cache_token', '') == cache_token and cache:
            # 【V160 Round38修復】即時報價要獨立於「技術指標算過就不重算」的
            # 快取之外，否則即時報價會永遠停在第一次算出來的瞬間。attach_live_
            # quotes有自己獨立的15秒快取，跟卡片快取解耦。
            return attach_live_quotes({c: cache[c] for c in codes if c in cache},
                                      fetch_intraday_extras=True)
        # token 變了或無快取 → 重算全部（平行處理）
        # 【V160】加上 0-100% 進度條（總指揮官要求取代 spinner）：平行處理時
        # 用 as_completed 逐一回報完成數量，所以百分比是真實進度不是估計值。
        result = {}
        ctx = get_script_run_ctx()
        _total = len(codes)
        _prog = st.progress(0.0, text=f"⚙️ 計算戰卡中 0/{_total}") if _total else None
        _done = 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            future_to_code = {executor.submit(calculate_signals_worker, code, config_payload, ctx): code
                              for code in codes}
            for future in concurrent.futures.as_completed(future_to_code):
                code = future_to_code[future]
                _done += 1
                if _prog is not None:
                    _pct = _done / _total
                    _prog.progress(_pct, text=f"⚙️ 計算戰卡中 {_done}/{_total}（{_pct*100:.0f}%）")
                try:
                    c = future.result()
                except Exception as e:
                    print(f"[compute_cards_cached-診斷] {code} 計算戰卡失敗，這檔跳過："
                          f"{type(e).__name__}: {e}")
                    continue
                if c and not c.get('error'):
                    result[code] = c
        if _prog is not None:
            _prog.empty()
        st.session_state['card_cache'] = result
        st.session_state['card_cache_token'] = cache_token
        return attach_live_quotes(result, fetch_intraday_extras=True)


    def render_list_section(section_key, title, config_payload, is_observe=False):
        """
        渲染一個清單區塊（觀察區 or 常態雷達），含控制列：
        搜尋框 + 決策判定篩選 + 批次勾選刪除。兩區共用這個函數。
        回傳這區成功算出的卡片 list（供盤中異常偵測收集）。
        """
        stocks_dict = st.session_state.get(section_key, {})
        if not stocks_dict:
            return []

        codes = list(stocks_dict.keys())
        # 快取 token：用「這區的代號集合 + 手動重整旗標」當 key，代號沒變就吃快取不重算
        cache_token = f"{section_key}:{','.join(sorted(codes))}:{st.session_state.get('last_refresh', 0)}"
        cards_map = compute_cards_cached(codes, config_payload, cache_token)

        with st.expander(title, expanded=True):
            # ---- 控制列 ----
            ctrl1, ctrl2, ctrl3 = st.columns([2, 2, 1.3])
            kw = ctrl1.text_input("🔍 搜尋", key=f"search_{section_key}", placeholder="代號或名稱",
                                  label_visibility="collapsed")
            verdict_filter = ctrl2.selectbox("決策判定篩選", ["全部"] + VERDICT_OPTIONS,
                                             key=f"vfilter_{section_key}", label_visibility="collapsed")
            del_clicked = ctrl3.button("🗑️ 刪除勾選", key=f"delsel_{section_key}", use_container_width=True)

            # 【V160 新增】評分範圍篩選（跟決策判定、關鍵字搜尋三者疊加生效）
            score_range = st.slider("📊 評分範圍篩選（只顯示評分落在此區間的標的）", -10, 10, (-10, 10),
                                    key=f"scorerange_{section_key}")

            # 【V160新增】快速批次刪除：改用下拉多選清單，不用捲動看卡片。
            # 卡片旁勾選框仍保留，兩者共用同一個session_state選取集合。
            #
            # 【R97補做，見開發歷程.md「龍頭刪除保護」章節】這是總指揮官之前
            # 就提過、但一直沒回覆要不要補的第二處——戰情速覽的「⚡速覽快速
            # 刪除」已經有龍頭警示，這裡（持倉/雷達/觀察卡片區塊自己的快速
            # 批次刪除）當時漏了，這次一併補上，跟戰情速覽同一套邏輯：
            # 警示+二次確認，不是硬性擋死（理由同前——龍頭判定用寫死對照表，
            # 跟這裡的清單無關，刪掉卡片不影響三關族群強弱判斷，只是少了
            # 分組顯示）。
            _quick_opts = [f"{c} {TW_STOCK_NAMES.get(c, '')}" for c in codes]
            _quick_map = {f"{c} {TW_STOCK_NAMES.get(c, '')}": c for c in codes}
            _stock_to_ind_bulkdel, _ = fetch_industry_map()
            _ind_members_bulkdel = {}
            for _c in codes:
                _ind = _stock_to_ind_bulkdel.get(_c) if _stock_to_ind_bulkdel else None
                if _ind:
                    _ind_members_bulkdel.setdefault(_ind, []).append(_c)

            with st.expander(f"⚡ 快速批次刪除（不用捲動找卡片，共 {len(codes)} 檔）", expanded=False):
                _quick_picked = st.multiselect("勾選要刪除的標的（可搜尋，可多選）",
                                               _quick_opts, key=f"quick_del_{section_key}")

                _picked_leader_warnings_bulkdel = []
                if _quick_picked:
                    _picked_codes_bulkdel = {_quick_map[k] for k in _quick_picked}
                    for _c in _picked_codes_bulkdel:
                        _ind = _stock_to_ind_bulkdel.get(_c) if _stock_to_ind_bulkdel else None
                        _fixed_leader = FIXED_INDUSTRY_LEADERS.get(_ind) if _ind else None
                        _fixed_leader_code = _fixed_leader[0] if _fixed_leader else None
                        if _ind and _fixed_leader_code == _c:
                            _siblings = [s for s in _ind_members_bulkdel.get(_ind, [])
                                        if s != _c and s not in _picked_codes_bulkdel]
                            if _siblings:
                                _sib_names = '、'.join(f"{s} {TW_STOCK_NAMES.get(s, '')}" for s in _siblings)
                                _picked_leader_warnings_bulkdel.append(
                                    f"⚠️ {_c} {TW_STOCK_NAMES.get(_c, '')} 是「{_ind}」的龍頭比較基準，"
                                    f"這裡還有同產業標的沒有一起刪除：{_sib_names}。")

                _confirm_leader_bulkdel = True
                if _picked_leader_warnings_bulkdel:
                    st.warning(
                        "\n\n".join(_picked_leader_warnings_bulkdel) +
                        "\n\n說明：刪除龍頭卡片不影響三關第二關（族群強弱）判斷邏輯，只是少了分組顯示。"
                        "如果只是想清掉這張卡片，請勾選下面確認後再刪除。")
                    _confirm_leader_bulkdel = st.checkbox("我了解上述影響，仍要刪除勾選的龍頭股",
                                                           key=f"confirm_leader_bulkdel_{section_key}")

                _del_disabled_bulkdel = bool(_picked_leader_warnings_bulkdel) and not _confirm_leader_bulkdel
                if _quick_picked and st.button(f"🗑️ 確認刪除選中的 {len(_quick_picked)} 檔",
                                               key=f"quick_del_btn_{section_key}",
                                               use_container_width=True,
                                               disabled=_del_disabled_bulkdel):
                    _to_del_quick = {_quick_map[k] for k in _quick_picked}
                    for c in _to_del_quick:
                        st.session_state[section_key].pop(c, None)
                    save_local_db_isolated()
                    st.session_state.pop(f"confirm_leader_bulkdel_{section_key}", None)
                    st.success(f"🗑️ 已刪除 {len(_to_del_quick)} 檔")
                    time.sleep(0.5)
                    st.rerun()

            # ---- 過濾（搜尋 + 決策判定 + 評分範圍 疊加生效）----
            kw = (kw or "").strip()
            filtered = []
            for code in codes:
                c = cards_map.get(code)
                if not c:
                    continue
                if kw:
                    name = TW_STOCK_NAMES.get(code, "")
                    if kw not in code and kw not in name:
                        continue
                if verdict_filter != "全部" and c.get('signal_text', '') != verdict_filter:
                    continue
                _sc = c.get('score', 0)
                if not (score_range[0] <= _sc <= score_range[1]):
                    continue
                filtered.append(code)

            # ---- 批次刪除：收集勾選 ----
            sel_key = f"selected_{section_key}"
            if sel_key not in st.session_state:
                st.session_state[sel_key] = set()

            if del_clicked:
                to_del = set(st.session_state[sel_key])
                if to_del:
                    for c in to_del:
                        st.session_state[section_key].pop(c, None)
                    st.session_state[sel_key] = set()
                    save_local_db_isolated()
                    st.success(f"🗑️ 已刪除 {len(to_del)} 檔")
                    time.sleep(0.5)
                    st.rerun()
                else:
                    st.warning("尚未勾選任何標的。")

            if not filtered:
                st.caption("（沒有符合搜尋/篩選條件的標的）")
                return list(cards_map.values())

            st.caption(f"顯示 {len(filtered)} / 共 {len(codes)} 檔"
                       + (f"｜勾選 {len(st.session_state[sel_key])} 檔待刪" if st.session_state[sel_key] else ""))

            # ---- 卡片渲染（雙欄）----
            cols, idx = st.columns(2), 0
            for code in filtered:
                c = cards_map[code]
                with cols[idx % 2]:
                    # 右上角勾選框（批次刪除用）
                    checked = st.checkbox(f"勾選刪除 {code} {TW_STOCK_NAMES.get(code, '')}",
                                          key=f"chk_{section_key}_{code}",
                                          value=(code in st.session_state[sel_key]))
                    if checked:
                        st.session_state[sel_key].add(code)
                    else:
                        st.session_state[sel_key].discard(code)

                    st.markdown(render_stock_card_ui(c), unsafe_allow_html=True)

                    # 觀察區專屬：升級到常態雷達
                    if is_observe:
                        if st.button("⬆️ 升級到常態雷達", key=f"promote_{code}", use_container_width=True):
                            # 【V160 修復】保留原始來源血統；升級後排最前面
                            _orig = st.session_state.observe_stocks.get(code, "手動加入")
                            _new_pin = {code: f"{_orig}→經觀察區"}
                            for _c, _v in st.session_state.pinned_stocks.items():
                                if _c != code:
                                    _new_pin[_c] = _v
                            st.session_state.pinned_stocks = _new_pin
                            st.session_state.observe_stocks.pop(code, None)
                            st.session_state[sel_key].discard(code)
                            save_local_db_isolated()
                            st.success(f"⬆️ {code} 已升級到常態雷達")
                            time.sleep(0.5)
                            st.rerun()
                    render_action_buttons(c, code, False, section_key=section_key)
                idx += 1

            return list(cards_map.values())


    def render_quick_overview(all_codes_with_source, config_payload, industry_map=None, leader_map=None):
        """
        【V160 B#11】戰情室速覽模式：把持倉/雷達/觀察區所有股票攤平成一張精簡總表，
        一眼掃完所有標的的決策判定，不用一張張滑卡片。
        all_codes_with_source: list of (code, source_label)

        【V160 關鍵修復】原本這裡是序列迴圈，而且呼叫端還會為了「盤中異常偵測」
        把同一批股票的 calculate_signals_worker 再重算一次——等於同樣的資料
        算兩遍。改成平行運算 + 回傳算好的結果給呼叫端直接重複使用，不用重算。
        回傳 {code: card_dict}（只含成功算出的），呼叫端可以直接拿來用。

        【R96新增】industry_map/leader_map：總指揮官要求「龍頭底下要接同產業
        個股」——例如龍頭大立光底下，要接玉晶光、亞光這種同族群持股，不要全部
        打散照評分高低排。這兩個字典由呼叫端（龍頭補列那段本來就已經算好
        產業對照跟哪個代號被選為龍頭）傳進來，這裡只負責依此重新排序，
        不重新查詢——沒有額外API成本。兩者留None時（例如速覽以外的其他呼叫端
        還沒接上這個功能）完全退回原本純評分排序，不影響既有行為。
        """
        codes = [code for code, _ in all_codes_with_source]
        source_map = dict(all_codes_with_source)
        results = {}
        # 【V160 B#11】戰情室速覽模式：把持倉/雷達/觀察區所有股票攤平成一張精簡總表。
        _qo_t0 = time.time()
        _qo_fail_count = 0
        _qo_last_err = ''
        # 【R97修復，見開發歷程.md「速覽刪除5分鐘排查」章節】原本用「整份
        # sorted(codes)字串」當快取鍵——只要watchlist內容變動(刪除/新增
        # 任何一檔)，這把鍵就會整個不一樣，導致快取整批失效，逼著「剩下沒動
        # 過的股票」也要重新算一次。總指揮官實測：刪除3檔後，畫面卡了快5分鐘
        # ——這正是這個機制造成的，不是刪除操作本身慢，是刪除觸發了全體重算。
        #
        # 改成「逐檔快取」：st.session_state['_qo_per_stock_cache']是
        # {code: card_dict}的字典，每次渲染時，先看清單裡每一檔「有沒有」
        # 已經算過的快取，有就直接沿用，只有「這次清單裡出現、但快取裡沒有」
        # 的股票(通常是新加入watchlist的)才需要真的送進ThreadPoolExecutor
        # 平行運算——刪除股票不會讓剩下的股票被牽連重算，因為它們的快取
        # entry根本沒被動到。
        #
        # 【R98續69新增，總指揮官指示「這個快取完全沒有過期機制」的優化
        # 建議】原本_qo_per_stock_cache只有「使用者手動按重新整理」才會
        # 清空，理論上可能無限期沿用(例如跨越好幾個交易日都沒被清空)。
        # 這裡存的是calculate_signals_worker整組訊號計算結果(技術指標/
        # 評分等，不只是即時報價)，不像即時報價需要分鐘級的新鮮度，但也
        # 不該跨日還在用——日K收盤價這類基準資料一天只變一次，隔天開盤
        # 後還在用昨天算的結果就是明顯過時的訊號。改用「跨日自動失效」：
        # 快取裡額外記錄「這批是哪一天算的」，現在的日期一旦不同，整批
        # 視為過期強制重算，不用等使用者自己發現、手動按按鈕才清空。
        _qo_cache_date = st.session_state.get('_qo_per_stock_cache_date', '')
        _qo_today_str = datetime.now(TAIPEI_TZ).strftime('%Y%m%d')
        _qo_force_refresh = st.session_state.pop('_qo_force_refresh', False)
        if _qo_cache_date != _qo_today_str:
            _qo_force_refresh = True   # 跨日了，不管使用者有沒有手動按，強制視為需要重新整理
        _qo_per_stock_cache = {} if _qo_force_refresh else st.session_state.get('_qo_per_stock_cache', {})
        _qo_cached_codes = [c for c in codes if c in _qo_per_stock_cache]
        _qo_missing_codes = [c for c in codes if c not in _qo_per_stock_cache]

        for _c in _qo_cached_codes:
            results[_c] = _qo_per_stock_cache[_c]
        if _qo_cached_codes and not _qo_missing_codes:
            st.caption(f"（{len(_qo_cached_codes)}檔全部沿用已算好的快取，watchlist組成沒有"
                      f"真正新增標的——想要最新資料可以按下面「🔄重新整理速覽」）")
        elif _qo_missing_codes:
            if _qo_cached_codes:
                st.caption(f"（{len(_qo_cached_codes)}檔沿用快取，只重新計算{len(_qo_missing_codes)}檔"
                          f"新標的——不會因為刪除/新增少數幾檔就讓其他沒變動的標的也重算一次）")
            codes_to_compute = _qo_missing_codes
            _qo_ctx = get_script_run_ctx()
            _qo_prog = st.progress(0.0, text=f"⚙️ 速覽計算中 0/{len(codes_to_compute)}")
            # 【R95續15新增】漸進式顯示——原本要等全部算完才畫出第一列，等就是
            # 好幾分鐘毫無反應。加簡易表格placeholder，每算完一檔就畫一次
            # (不含即時報價，那個仍維持批次呼叫)，全部算完後被完整版取代。
            _qo_partial_placeholder = st.empty()
            _qo_done = 0
            # 【R95續21新增】戰情速覽用專屬fast_mode設定跳過當沖資格查詢，
            # 用淺複製避免影響其他呼叫端。
            # 【R95續22】同時打開perf_diag印出分階段計時到log，診斷「速覽卡
            # 3分鐘」用，開銷極小可保留當常態觀測。
            _qo_config = dict(config_payload)
            _qo_config['fast_mode'] = True
            _qo_config['perf_diag'] = True
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
                futures = {executor.submit(calculate_signals_worker, code, _qo_config, _qo_ctx): code
                          for code in codes_to_compute}
                for future in concurrent.futures.as_completed(futures):
                    code = futures[future]
                    _qo_done += 1
                    _qo_prog.progress(_qo_done / len(codes_to_compute),
                                      text=f"⚙️ 速覽計算中 {_qo_done}/{len(codes_to_compute)}"
                                           f"（{_qo_done/len(codes_to_compute)*100:.0f}%）")
                    try:
                        c = future.result()
                        if c and not c.get('error'):
                            results[code] = c
                        else:
                            _qo_fail_count += 1
                            _err = (c or {}).get('error', '回傳空結果(None)，函式內部可能提早return')
                            _qo_last_err = f"{code}: {_err}"
                            print(f"[戰情速覽] {code} 計算失敗：{_err}")
                    except Exception as e:
                        _qo_fail_count += 1
                        _qo_last_err = f"{code}: {type(e).__name__}: {e}"
                        print(f"[戰情速覽] {code} 計算拋出例外：{type(e).__name__}: {e}")
                        continue
                    if results:
                        _partial_rows = []
                        for _pc, _pv in results.items():
                            _psig = _pv.get('signal_text', '')
                            if '偏多攻擊' in _psig: _pverdict = "🔥進攻"
                            elif '觀察偏多' in _psig: _pverdict = "🟡觀望"
                            elif '偏空防守' in _psig: _pverdict = "🔵撤退"
                            elif '轉弱謹慎' in _psig: _pverdict = "⚠️警戒"
                            else: _pverdict = "⚖️中性"
                            _partial_rows.append({
                                '判定': _pverdict, '代號': _pc, '名稱': TW_STOCK_NAMES.get(_pc, _pc),
                                '現價': round(float(_pv.get('price', 0) or 0), 2),
                                '漲跌%': round(float(_pv.get('gain', 0) or 0), 2),
                                '評分': _pv.get('score', 0),
                            })
                        _qo_partial_placeholder.dataframe(
                            pd.DataFrame(_partial_rows).sort_values('評分', ascending=False).reset_index(drop=True),
                            use_container_width=True, hide_index=True)
            _qo_prog.empty()
            _qo_partial_placeholder.empty()   # 完整版(含即時報價/配色)接下來會取代這個簡易版
            if _qo_fail_count == len(codes_to_compute) and _qo_fail_count > 0:
                # 全部都失敗，不是部分失敗——這種「全軍覆沒」的情況才值得直接
                # 在畫面上留一筆樣本錯誤，讓不用查log也能看到線索。
                st.session_state['qo_last_fail_sample'] = _qo_last_err
            # 【R97修復】改成合併進逐檔快取，不是整批覆蓋——只把這次新算好的
            # codes_to_compute結果merge進_qo_per_stock_cache，原本已經在
            # 快取裡、這次沿用的那些股票不受影響。存的是即時報價疊加「之前」
            # 的版本——即時報價本來就該每次都重新疊加最新的，不該被這個快取
            # 鎖住，所以attach_live_quotes()還是留在下面、快取範圍之外，
            # 每次都會重跑。
            for _new_code in codes_to_compute:
                if _new_code in results:
                    _qo_per_stock_cache[_new_code] = results[_new_code]
            st.session_state['_qo_per_stock_cache'] = _qo_per_stock_cache
            st.session_state['_qo_per_stock_cache_date'] = _qo_today_str

        # 【V160 Round38】速覽模式是「快速看一眼決定要不要進場」的核心場景，
        # 這裡也要接上即時報價。
        # 【R96】刻意不傳fetch_intraday_extras(維持預設False)——速覽維持精簡
        # 快速，要看完整當沖資訊去點「查看完整戰卡」。
        results = attach_live_quotes(results)
        print(f"[戰情速覽-計時] {len(codes)}檔，計算+attach_live_quotes共花 "
              f"{round(time.time() - _qo_t0, 2)} 秒（此行以前包含平行運算全部N檔的"
              f"calculate_signals_worker、龍頭補列、即時報價批次查詢）")

        rows = []
        for code, c in results.items():
            source = source_map.get(code, '')
            sig = c.get('signal_text', '')
            if '偏多攻擊' in sig: verdict = "🔥進攻"
            elif '觀察偏多' in sig: verdict = "🟡觀望"
            elif '偏空防守' in sig: verdict = "🔵撤退"
            elif '轉弱謹慎' in sig: verdict = "⚠️警戒"
            else: verdict = "⚖️中性"
            rows.append({
                # 【R95續25】欄位順序改成「來源」放第一、「評分」放第二——
                # 手機版表格原本要滑到最右邊才看得到來源，字典插入順序調整。
                '來源': source,
                '評分': c.get('score', 0),
                '判定': verdict, '代號': code, '名稱': TW_STOCK_NAMES.get(code, code),
                # 【R98續24修復，總指揮官指示】即時日期/即時時間移到現價
                # 前面——先看這筆報價是不是今天的、幾點抓的，再看價格本身，
                # 順序上更符合「先確認新不新鮮，再看數字」的閱讀習慣。
                # 即時日期改成人話：等於今天就直接顯示「今日」，不是今天
                # 就顯示「8月25日」這種好讀格式，不再是原始的20260825
                # 數字字串（那種格式要心算轉換才知道是不是今天，總指揮官
                # 反映看不懂容易誤判）。
                '即時日期': _format_live_date_human(c.get('live_date', '')),
                '即時時間': ((f"{'🧊' if c.get('live_is_carried_persistent') else '⏳'}{c.get('live_time','')}"
                            if c.get('live_is_carried') else c.get('live_time', ''))
                            if c.get('live_time') else "—"),
                # 【R53修復】原本「現價」沒標示是哪天的——極端行情下技術指標
                # 用的基準價可能還停在前一天，現在直接標出日期一眼看得到。
                '現價': round(float(c.get('price', 0) or 0), 2),
                # 【R98修復，總指揮官反映速覽表格欄位太雜】現價日期／漲跌%（收盤基準的
                # 舊欄位）對盤中速覽的實用性低，總指揮官明確表示「可以不用」——拿掉，
                # 換成上面確實需要的「即時日期」，跟既有的「即時時間」一起讓即時報價
                # 的日期+時間都看得到（不只是時間，日期也要，避免沿用到隔天還誤判成
                # 今天剛查到的）。這兩欄仍保留在c字典裡（price_date/gain沒有被刪除，
                # 只是不放進這張速覽表格），其他用到這兩個值做判斷/計算的地方不受影響。
                # 【R96再修復】上一輪的「🕐退回顯示日線收盤價」是錯誤修法，已撤回——
                # 總指揮官指出這違反R62當時定案的原則：「查無成交價寧可誠實顯示
                # —，不假裝有資料」，日線收盤價（可能是昨天的）冒充即時價，等於
                # 重蹈R62的覆轍。真正該做的是讓_last_cache（這個session裡最近
                # 一次真的抓到的成交價+真實時間）確實生效，不是換一種方式造假。
                # 這裡改回誠實顯示"—"，但加強了attach_live_quotes內部的診斷log
                # （見下方batch fetch那段），方便查出_last_cache為什麼是空的。
                '即時': round(c['live_price'], 2) if c.get('live_price') is not None else "—",
                '即時漲跌%': round(c['live_change_pct'], 2) if c.get('live_change_pct') is not None else "—",
                # 【V160 新增】今日開/高/低，速覽模式一眼看出當日振幅與現價在區間的位置
                '開': c.get('open_today'),
                '高': c.get('high_today'),
                '低': c.get('low_today'),
                # 【V160】總指揮官回報：只有外資5日不夠判斷，法人動能要看多天期才知道是
                # 「單日突襲」還是「持續買盤」。四個欄位一起看：若 5日≈10日，代表買盤集中在
                # 最近幾天（動能新鮮）；若 5日遠小於10日，代表買盤在更早之前、近期已停手。
                '外資5日': int(c.get('f_5d', 0) or 0),
                '外資10日': int(c.get('f_10d', 0) or 0),
                '投信5日': int(c.get('t_5d', 0) or 0),
                '投信10日': int(c.get('t_10d', 0) or 0),
                '爆量比': round(float(c.get('vol_ratio', 0) or 0), 1),
                '防守線': c.get('def_line', 0),
            })
        # 【R96新增】依產業分組排序——龍頭排最上面，底下接同產業其他持股。
        # 只有「龍頭本身也在表格裡」的產業才分組，避免出現龍頭底下沒同產業
        # 持股的無意義分組。
        if rows and industry_map and leader_map:
            _present_codes = {r['代號'] for r in rows}
            _groupable_inds = {ind for ind, ld_code in leader_map.items() if ld_code in _present_codes}
            for r in rows:
                r['_ind'] = industry_map.get(r['代號'])
                r['_is_leader'] = bool(r['_ind'] and leader_map.get(r['_ind']) == r['代號'])
            _group_best = {}
            for r in rows:
                if r['_ind'] in _groupable_inds:
                    _group_best[r['_ind']] = max(_group_best.get(r['_ind'], -999), r['評分'])

            def _qo_sort_key(r):
                _ind = r['_ind']
                if _ind in _groupable_inds:
                    return (0, -_group_best[_ind], _ind, 0 if r['_is_leader'] else 1, -r['評分'])
                return (1, 0, '', 0, -r['評分'])

            rows.sort(key=_qo_sort_key)
            for r in rows:
                # 加一欄「產業」讓分組看得出來；非龍頭的族群成員名稱前面加「└」
                # 縮排標記，一眼看出這檔是掛在上一列龍頭底下的同族群持股。
                r['產業'] = r.pop('_ind') or ''
                _is_ld = r.pop('_is_leader')
                if r['產業'] in _groupable_inds and not _is_ld:
                    r['名稱'] = f"└ {r['名稱']}"
                elif _is_ld:
                    r['名稱'] = f"👑 {r['名稱']}"
        if not rows:
            # 【R59修復】原本把「清單本身是空的」跟「清單有股票但抓價失敗」
            # 混成一句話，兩者該做的事完全不同，這裡分開講清楚。
            if not codes:
                st.warning("⚠️ 持倉/雷達/觀察清單目前是空的（不是抓價失敗，是清單本身沒有股票）。"
                          "如果你記得清單裡應該要有股票，這通常代表登入後雲端資料還原失敗——"
                          "去側欄按「☁️ 重新從雲端還原持倉/雷達/觀察清單」，並確認上面"
                          "「雲端還原」狀態是不是顯示✅成功。")
            else:
                _fail_sample = st.session_state.get('qo_last_fail_sample', '')
                st.warning(f"⚠️ 清單有 {len(codes)} 檔，但這次全部抓價失敗（不是清單是空的）。"
                          "⚠️ 注意：🩺資料源健康度檢查測的是「單一探測請求通不通」，不是這條完整計算流程"
                          "本身有沒有問題——健康度檢查全綠不代表這裡一定沒事，兩者是不同層次的檢查。"
                          + (f"\n\n最後一筆失敗訊息（僅供參考）：{_fail_sample}" if _fail_sample else ""))
            return results
        # 【R96調整】有產業分組時rows已排好序，不能再用sort_values覆蓋掉。
        # 沒有分組資訊時維持原本純評分排序。
        if industry_map and leader_map:
            df = pd.DataFrame(rows).reset_index(drop=True)
        else:
            df = pd.DataFrame(rows).sort_values('評分', ascending=False).reset_index(drop=True)

        # 【R54修復】pandas Styler預設精度6位小數，跟Python值本身round(x,2)
        # 無關，這是畫面「100000」詭異數字的成因。用函式逐格判斷型別再格式化
        # (precision=遇到"—"字串會整欄format失敗)。
        _fmt_cols = ['現價', '即時', '即時漲跌%', '開', '高', '低', '爆量比', '防守線']

        def _fmt2(v):
            if isinstance(v, bool):
                return v
            if isinstance(v, (int, float)):
                try:
                    return f"{v:.2f}"
                except Exception:
                    return v
            return v   # 字串（例如"—"）原樣保留，不硬套數字格式

        # 【R53修復】台股慣例紅漲綠跌，原本兩個漲跌%欄位是純黑白數字，掃一眼看不出
        # 誰漲誰跌，得逐格讀數字。顏色跟戰卡本身用的紅#ff4d4d／綠#00FF00是同一組，
        # 視覺語言一致。
        def _gain_color(v):
            try:
                v = float(v)
            except (TypeError, ValueError):
                return ''
            if v > 0:
                return 'color: #ff4d4d; font-weight: bold;'
            if v < 0:
                return 'color: #00e676; font-weight: bold;'
            return ''

        # 【R96新增，總指揮官反映速覽模式也要支援一鍵刪除】跟持倉/雷達/觀察區
        # 完整卡片模式共用同一種UI設計(下拉多選+確認刪除按鈕)。刻意只開放
        # 雷達跟觀察兩種來源——持倉是真實交易部位，誤刪風險太高，不放進這種
        # 快速批次操作，要刪持倉請去持倉區塊本身的介面操作，那裡有更明確的
        # 上下文。source欄位(rows裡的'來源')本來就有記錄每檔股票是從哪個
        # session_state字典來的，直接對照刪除，不用重新查一次。
        _qo_source_key_map = {"雷達": "pinned_stocks", "觀察": "observe_stocks"}
        _qo_del_candidates = [(row['代號'], row['名稱'], row['來源']) for row in rows
                              if row['來源'] in _qo_source_key_map]

        # 【R97新增，總指揮官要求：龍頭股刪除保護】原本這裡完全沒有任何檢查，
        # 龍頭股跟一般股票刪除起來沒有差別——總指揮官反映「清單裡還有相關個股時，
        # 龍頭應該要刪不掉」，這裡補上偵測+二次確認。
        #
        # 【刻意的設計取捨：警示，不是強制擋死】龍頭判定用FIXED_INDUSTRY_LEADERS
        # （寫死對照表，見warroom_core.py），跟watchlist裡有沒有這張卡片完全無關——
        # 就算把龍頭卡片刪掉，system_scheduler.py的intraday_kbar階段仍然會另外
        # 把固定龍頭代號併入輪詢清單（見leader_symbols/leader_of的獨立組裝邏輯），
        # 9:30三關第二關（族群強弱比較）不會因為這裡刪掉卡片就跑不出來。真正
        # 會受影響的只有「戰情速覽」畫面本身的👑分組顯示會消失。既然底層判斷邏輯
        # 不受影響，這裡選擇「警示+二次確認」而不是完全擋死不給刪——如果總指揮官
        # 要更嚴格的硬性阻擋（多選清單裡直接不能勾龍頭股），跟我說一聲，可以改。
        _industry_members = {}
        for _r in rows:
            _ind_name = _r.get('產業', '')
            if _ind_name:
                _industry_members.setdefault(_ind_name, []).append(_r['代號'])

        if _qo_del_candidates:
            with st.expander(f"⚡ 速覽快速刪除（僅雷達/觀察，共 {len(_qo_del_candidates)} 檔可刪）", expanded=False):
                _qo_del_opts = [f"{code} {name}（{src}）" for code, name, src in _qo_del_candidates]
                _qo_del_map = {f"{code} {name}（{src}）": (code, src) for code, name, src in _qo_del_candidates}
                _qo_picked = st.multiselect("勾選要刪除的標的（可搜尋，可多選）", _qo_del_opts,
                                            key="qo_quick_del")

                # 龍頭警示：勾選項目裡有龍頭股，且清單裡還有同產業其他標的
                # 「沒有」一起被勾選要刪，才需要警示（如果連同族群其他標的
                # 一起全刪，就不算「刪不乾淨」的問題，不用特別警示）。
                _picked_leader_warnings = []
                if _qo_picked:
                    _picked_codes_this_batch = {_qo_del_map[_opt][0] for _opt in _qo_picked}
                    for _opt in _qo_picked:
                        _p_code, _p_src = _qo_del_map[_opt]
                        _p_row = next((r for r in rows if r['代號'] == _p_code), None)
                        if _p_row and str(_p_row.get('名稱', '')).startswith('👑'):
                            _ind_name = _p_row.get('產業', '')
                            _siblings = [c for c in _industry_members.get(_ind_name, [])
                                        if c != _p_code and c not in _picked_codes_this_batch]
                            if _siblings:
                                _sib_names = '、'.join(f"{c} {TW_STOCK_NAMES.get(c, '')}" for c in _siblings)
                                _picked_leader_warnings.append(
                                    f"⚠️ {_p_code} {TW_STOCK_NAMES.get(_p_code, '')} 是「{_ind_name}」的龍頭比較基準，"
                                    f"清單裡還有同產業標的沒有一起刪除：{_sib_names}。")

                _confirm_leader_del = True
                if _picked_leader_warnings:
                    st.warning(
                        "\n\n".join(_picked_leader_warnings) +
                        "\n\n說明：刪除龍頭卡片不會影響9:30三關第二關（族群強弱）的判斷邏輯"
                        "——排程端另外用固定龍頭清單輪詢，跟這裡的watchlist無關。純粹是這裡"
                        "刪掉之後，戰情速覽畫面不會再顯示這個產業的👑分組。如果只是想清掉這張卡片"
                        "、不是要換掉對照基準，請勾選下面確認後再刪除。")
                    _confirm_leader_del = st.checkbox("我了解上述影響，仍要刪除勾選的龍頭股",
                                                       key="qo_confirm_leader_del")

                _del_btn_disabled = bool(_picked_leader_warnings) and not _confirm_leader_del
                if _qo_picked and st.button(f"🗑️ 確認刪除選中的 {len(_qo_picked)} 檔",
                                            key="qo_quick_del_btn", use_container_width=True,
                                            disabled=_del_btn_disabled):
                    _qo_del_count = 0
                    for _opt in _qo_picked:
                        _code, _src = _qo_del_map[_opt]
                        _skey = _qo_source_key_map[_src]
                        if st.session_state.get(_skey, {}).pop(_code, None) is not None:
                            _qo_del_count += 1
                    save_local_db_isolated()
                    st.session_state.pop("qo_confirm_leader_del", None)
                    st.success(f"🗑️ 已刪除 {_qo_del_count} 檔")
                    time.sleep(0.5)
                    st.rerun()

        try:
            # 【R98修復】'漲跌%'欄位已經拿掉（見上方rows組裝處），subset要跟著只保留
            # 實際存在的欄位，否則df.style.map/applymap會對不存在的欄位丟KeyError，
            # 整段被外層except吃掉、表格整個退化成沒有顏色的樣子，且不容易被發現。
            _color_subset = [c for c in ('漲跌%', '即時漲跌%') if c in df.columns]
            try:
                _styled = df.style.map(_gain_color, subset=_color_subset)
            except AttributeError:
                # 舊版pandas(<2.1)沒有.map，退回已棄用但還能用的.applymap
                _styled = df.style.applymap(_gain_color, subset=_color_subset)
            _styled = _styled.format({c: _fmt2 for c in _fmt_cols if c in df.columns})
            # 【R56修復】R54加的on_select點列選取在Streamlit Cloud上沒反應，
            # 拿掉改用下拉選單當唯一入口，避免讓人誤以為表格可以點。
            st.dataframe(_styled, use_container_width=True, hide_index=True)
        except Exception:
            # styler 需要 matplotlib 或格式不合時，退回無顏色版本，不讓表格整個顯示不出來
            st.dataframe(df, use_container_width=True, hide_index=True)

        st.caption(f"共 {len(df)} 檔｜🔥進攻 {sum('進攻' in r['判定'] for r in rows)} 檔"
                   f"｜🔵撤退 {sum('撤退' in r['判定'] for r in rows)} 檔｜依評分高→低排序")
        # 【R95續27新增】現在速覽結果會沿用session_state快取、不再每次互動都重算，
        # 這顆按鈕給使用者主動要最新資料的管道——按下去會清掉快取，這次rerun
        # 就會真的重新去抓一次全部股票。
        if st.button("🔄 重新整理速覽（重新抓取全部股票最新資料）", key="qo_force_refresh_btn"):
            st.session_state['_qo_force_refresh'] = True
            st.rerun()
        # 【R64修復】原本這段說明用st.caption整段寫出來，固定佔用版面——總指揮官
        # 反映這種說明性文字應該做成浮動標籤，不用整個攤開。改用跟戰卡同一套
        # .m-tooltip浮動提示（滑鼠移過去/長按才展開），平常只佔一行的空間。
        st.markdown(
            """<div style="font-size:12px; color:#888;">"""
            """<span class="m-tooltip">💡 現價／即時是什麼意思？（滑鼠移過去看說明）"""
            """<span class="m-tooltiptext">「現價」是技術指標/評分用的基準價（日K收盤，"""
            """盤中可能還停在前一天）；「即時」是證交所即時報價（約5秒更新一次，"""
            """「即時日期＋即時時間」是實際抓到那一筆成交的日期跟時間，不是現在的時間——"""
            """如果即時日期不是今天，代表這檔今天還沒成交，看到的是上一個有成交交易日的"""
            """舊資料）。劇烈行情（例如跌停鎖死）兩者都可能跟你手機看到的價格有落差，"""
            """以券商軟體的即時報價為準，這裡的數字只做輔助判斷。</span></span></div>""",
            unsafe_allow_html=True)

        # 【R53/R95續27】下拉選單只是選股票，按下「📄查看完整戰卡」才真的對
        # 這一檔單獨做完整計算(fast_mode=False)，不會連帶重算watchlist其他檔。
        _qo_pick_opts = ["—"] + [f"{r['代號']} {r['名稱']}" for r in rows]
        _qo_pick = st.selectbox("👆 選擇要查看單檔完整戰卡的股票（選好後按下面按鈕才會載入）",
                                _qo_pick_opts, key="qo_card_pick")
        if _qo_pick != "—":
            _qo_pick_code = _qo_pick.split(" ")[0]
            # 【R96修復——重大bug，總指揮官抓到】原本用if st.button(...)直接
            # 包住整個卡片渲染+render_action_buttons，這是Streamlit的經典陷阱：
            # if st.button(...)這個條件只在「剛好是這次點擊了這個按鈕」的那
            # 一輪重新執行才成立。卡片內部任何其他按鈕（解鎖NVIDIA戰略推演、
            # 匯出戰卡純文字）本身也是st.button，一被點擊就會觸發Streamlit
            # 整支程式重新執行——但那一輪「查看完整戰卡」這個按鈕沒有被按，
            # 條件變回False，整張卡片(含它裡面剛點的按鈕結果)就整個消失，
            # 看起來像是「跳回查看單一檔完整戰卡的選擇畫面」。
            # 修法：把算好的卡片存進session_state，用「session_state裡有沒有
            # 這一檔已經載入過的資料」來決定要不要顯示，不再單純依賴「這次
            # 重新執行剛好是不是按鈕被點的那一次」。
            if st.button(f"📄 查看 {_qo_pick} 完整戰卡", key="qo_load_full_card_btn"):
                with st.spinner(f"正在載入 {_qo_pick_code} 完整戰卡（含當沖資格等速覽沒算的欄位）..."):
                    _qo_full_config = dict(config_payload)
                    _qo_full_config['fast_mode'] = False   # 明確要求完整深度，不是速覽的簡化版
                    try:
                        # 【R98續16】沿用25秒硬性逾時保護(見calculate_signal_with_
                        # timeout說明)——這個下拉選單入口跟被拿掉的波段候選/主力
                        # 偵測戰卡是不同的東西(這裡是總指揮官從速覽表格主動選一檔
                        # 深入看)，總指揮官沒要求拿掉，予以保留；但底層同樣會呼叫
                        # calculate_signals_worker，一樣可能卡住，所以套上同樣的
                        # 逾時保護，避免這個入口也出現「永遠載入中」的空白。
                        # 注意：這個入口需要fast_mode=False的完整深度計算，比波段
                        # 候選那種預設計算更花時間，逾時放寬到40秒。
                        _qo_pick_card = calculate_signal_with_timeout(
                            _qo_pick_code, _qo_full_config, timeout_sec=40)
                    except Exception as _e:
                        _qo_pick_card = None
                        st.warning(f"⚠️ {_qo_pick_code} 載入失敗：{type(_e).__name__}: {_e}——"
                                  f"稍後再試一次，如果持續失敗麻煩告訴我。")
                if _qo_pick_card and not _qo_pick_card.get('error'):
                    # 【R96】明確要求「完整戰卡」，fetch_intraday_extras=True，
                    # 資料完整——這正是總指揮官這輪確認的「查看單一檔完整戰卡才
                    # 顯示全部當沖資訊」那個情境本身。
                    # 【R98續68新增，總指揮官反映「執行完沒有看到任何東西就
                    # 停住」】原本這行沒有try/except保護，如果attach_live_
                    # quotes()內部(這幾輪剛調整過報價抓取順序)拋出例外，會
                    # 導致整段渲染意外中斷，畫面卡住、沒有任何錯誤訊息可看，
                    # 使用者完全不知道發生什麼事。加上防護，就算即時報價
                    # 抓取失敗，至少卡片本身(技術指標/評分等)還能正常顯示，
                    # 不會因為這一步失敗就整個沒東西。
                    try:
                        _qo_pick_card = attach_live_quotes(
                            {_qo_pick_code: _qo_pick_card}, fetch_intraday_extras=True)[_qo_pick_code]
                    except Exception as _alq_e:
                        print(f"[速覽單檔戰卡] {_qo_pick_code} attach_live_quotes失敗"
                              f"(卡片其餘資料仍會顯示)：{type(_alq_e).__name__}: {_alq_e}")
                        st.caption(f"⚠️ 即時報價這次沒抓到（{type(_alq_e).__name__}），"
                                  f"下面顯示的是技術指標/評分，不含最新即時價。")
                    # 【R96新增】存進session_state，讓卡片內部按鈕觸發的重新執行
                    # 也能繼續正確顯示這張卡片，不會消失。
                    st.session_state['_qo_loaded_card'] = {'code': _qo_pick_code, 'card': _qo_pick_card}
                elif _qo_pick_card is not None:
                    st.session_state.pop('_qo_loaded_card', None)
                    st.warning(f"⚠️ {_qo_pick_code} 這次算不出來（{_qo_pick_card.get('error', '原因不明')}）"
                              f"——稍後再試一次，如果同一檔持續算不出來麻煩告訴我。")

            # 【R96新增】不管這次重新執行是不是「載入」按鈕觸發的，只要
            # session_state裡有這一檔已經載入過的資料，就繼續顯示——這是修好
            # 上面陷阱的關鍵：卡片內部按鈕(NVIDIA/匯出文字)點擊後的重新執行，
            # 會走到這裡而不是上面的if區塊，但一樣能正確顯示卡片。
            _qo_loaded = st.session_state.get('_qo_loaded_card')
            if _qo_loaded and _qo_loaded.get('code') == _qo_pick_code and _qo_loaded.get('card'):
                st.markdown(render_stock_card_ui(_qo_loaded['card']), unsafe_allow_html=True)
                # 【R90修復】卡片底部收合區塊看不到——不是例外處理問題，
                # 是速覽模式下拉選單選股票這條路徑從R53建立以來就漏掉這一行。
                render_action_buttons(_qo_loaded['card'], _qo_pick_code, False, section_key='quick_overview_pick')
        else:
            # 【R96新增】選單切回「—」或換選別檔時，清掉上一檔的殘留資料，
            # 避免使用者切換股票後，畫面還短暫顯示上一檔的舊卡片。
            st.session_state.pop('_qo_loaded_card', None)

        return results


    _monitor_cards = []   # 【V159】收集雷達+持倉這輪算出來的卡片，供盤中異常偵測使用

    # 【V160 B#11】速覽模式：開關已移到標題正下方，這裡只讀取狀態
    _quick_mode = st.session_state.get('quick_overview_mode', False)

    if _quick_mode:
        # 速覽：把持倉+雷達+觀察區全部攤平成一張表
        _all_codes = ([(c, "持倉") for c in st.session_state.get('portfolio', {}).keys()]
                      + [(c, "雷達") for c in st.session_state.get('pinned_stocks', {}).keys()]
                      + [(c, "觀察") for c in st.session_state.get('observe_stocks', {}).keys()])
        # 【R95新增】戰情速覽固定放個股龍頭以便觀察，還沒加進清單的龍頭
        # 自動補一筆「👑龍頭觀察」。
        # 【R96新增】leader_map等在try區塊外先給空字典預設值，避免NameError。
        _stock_to_ind_qo, _qo_leader_map = {}, {}
        try:
            _seen_codes = {c for c, _ in _all_codes}
            _leader_additions, _leader_seen_this_pass = [], set()
            _stock_to_ind_qo, _ = fetch_industry_map()   # 本身有24小時快取，重複呼叫幾乎零成本
            # 【R96修復，重大效能問題】原本序列迴圈逐檔查產業龍頭，冷快取時
            # 是「速覽卡3分鐘以上」的根因。改用ThreadPoolExecutor(8條)平行處理。
            _leader_ctx = get_script_run_ctx()

            def _leader_lookup_worker(_c, _ind):
                if _leader_ctx is not None:
                    try:
                        add_script_run_ctx(threading.current_thread(), _leader_ctx)
                    except Exception:
                        pass
                try:
                    return _c, get_industry_leader_proxy(_ind, exclude_code=_c)
                except Exception as _e:
                    print(f"[戰情速覽-龍頭補列] {_c} 查詢龍頭失敗，跳過這一檔：{type(_e).__name__}: {_e}")
                    return _c, (None, None)

            _leader_tasks = []
            for _c, _tag in list(_all_codes):
                _ind = _stock_to_ind_qo.get(_c)
                if _ind:
                    _leader_tasks.append((_c, _ind))
            if _leader_tasks:
                # 【R96再修復，時間預算上限】8條執行緒平行跑仍可能等很久，
                # 且短時間連續請求容易觸發Yahoo限流。改成12秒時間預算，超過
                # 就拿已查完的結果補列，其餘背景自然結束。
                _leader_executor = concurrent.futures.ThreadPoolExecutor(max_workers=8)
                _leader_futures = [_leader_executor.submit(_leader_lookup_worker, _c, _ind)
                                   for _c, _ind in _leader_tasks]
                _leader_done, _leader_not_done = concurrent.futures.wait(_leader_futures, timeout=12)
                if _leader_not_done:
                    print(f"[戰情速覽-龍頭補列] 時間預算12秒到，{len(_leader_not_done)}/"
                          f"{len(_leader_futures)}檔龍頭查詢還沒完成，先不等，"
                          f"直接用已完成的{len(_leader_done)}筆結果繼續。")
                _leader_executor.shutdown(wait=False)
                for _fut in _leader_done:
                    try:
                        _c, (_ld_code, _ld_name) = _fut.result()
                    except Exception:
                        continue
                    if not _ld_code:
                        continue
                    # 【R96新增】不管龍頭是不是已經在清單裡，都要記住「這個
                    # 產業的龍頭是誰」，分組排序需要這份對照。
                    _ld_ind = _stock_to_ind_qo.get(_c)
                    if _ld_ind:
                        _qo_leader_map[_ld_ind] = _ld_code
                    if _ld_code not in _seen_codes and _ld_code not in _leader_seen_this_pass:
                        _leader_additions.append((_ld_code, "👑龍頭觀察"))
                        _leader_seen_this_pass.add(_ld_code)
            _all_codes += _leader_additions
        except Exception as _e:
            print(f"[戰情速覽-龍頭補列] 整批查詢失敗：{type(_e).__name__}: {_e}")
            pass   # 龍頭補列是加分功能，查詢失敗不該影響速覽表本體正常顯示
        st.markdown("### ⚡ 戰情速覽")
        # 【V160 修復】原本這裡在 render_quick_overview 算完之後，又用序列迴圈把
        # 同一批股票重算一次給 monitor_cards 用——現在改成直接複用回傳結果，
        # 不重算，這是速覽模式「明明有平行處理過但還是慢」的另一半原因。
        # 【R98續108新增，總指揮官反映：加了快取後編輯還是卡約30秒，經過
        # 逐一查證HUD/大盤氣象/隔夜總經/戰情速覽本身的快取都正常，靜態
        # 讀程式碼已經找不到明確嫌疑，改用計時診斷——下次卡住時可以直接
        # 去Streamlit Cloud的Manage app→Logs看這幾行印出的秒數，精準定位
        # 是哪一段，不用再靠猜的。】
        _diag_t0 = time.time()
        _qo_results = render_quick_overview(_all_codes, config_payload,
                                            industry_map=_stock_to_ind_qo, leader_map=_qo_leader_map)
        _monitor_cards.extend(_qo_results.values())
        print(f"[效能診斷] render_quick_overview（戰情速覽本體）耗時 {time.time()-_diag_t0:.1f} 秒")

        # 【R98續104新增，總指揮官指示：位置放在戰情速覽底下】
        _diag_t1 = time.time()
        render_portfolio_quickview()
        print(f"[效能診斷] render_portfolio_quickview（持倉速覽）耗時 {time.time()-_diag_t1:.1f} 秒")
    else:
        if st.session_state.get('portfolio', {}):
            with st.expander("💼 總指揮常態持倉模擬倉", expanded=True):
                # 【V160關鍵修復】「開機卡在只跑出1-2檔」的根因——持倉清單
                # 原本逐檔序列迴圈，round23平行化雷達/觀察區時漏掉這段。改用
                # 同一套ThreadPoolExecutor先平行算完，再照順序渲染卡片。
                _pf_items = list(st.session_state.portfolio.items())
                _pf_codes = [code for code, _ in _pf_items]

                # 【R98續105新增，總指揮官實測反映：改張數/成本價當下卡約1分鐘】
                # 根因跟輕量版持倉速覽是同一類問題——st.data_editor任何互動
                # 都會讓Streamlit整支script重跑一次，這裡卻沒有快取，導致
                # 「純粹編輯一格」也會意外觸發一次全新的ThreadPoolExecutor
                # 完整技術面/籌碼運算+即時報價查詢。修法：把整批運算結果存進
                # session_state，只有①持倉股票清單真的變了②超過90秒③使用者
                # 明確按下重新整理，才真的重新運算；純編輯儲存格觸發的重新
                # 執行，直接複用快取。
                _PF_CACHE_KEY = 'pf_full_compute_cache'
                _PF_TTL_SECONDS = 90
                _pf_codes_set = frozenset(_pf_codes)
                _pf_cache = st.session_state.get(_PF_CACHE_KEY)
                _pf_cache_valid = (
                    _pf_cache is not None
                    and _pf_cache.get('codes') == _pf_codes_set
                    and (time.time() - _pf_cache.get('ts', 0)) < _PF_TTL_SECONDS
                )
                _pf_force_refresh = st.button(
                    "🔄 重新整理持倉完整運算（技術面／籌碼／即時報價）", key="pf_force_refresh_btn")

                if _pf_cache_valid and not _pf_force_refresh:
                    _pf_results = _pf_cache['results']
                else:
                    _pf_ctx = get_script_run_ctx()
                    _pf_results = {}
                    if _pf_codes:
                        _pf_prog = st.progress(0.0, text=f"⚙️ 計算持倉中 0/{len(_pf_codes)}")
                        _pf_done = 0
                        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
                            _pf_futures = {executor.submit(calculate_signals_worker, code, config_payload, _pf_ctx): code
                                           for code in _pf_codes}
                            for future in concurrent.futures.as_completed(_pf_futures):
                                code = _pf_futures[future]
                                _pf_done += 1
                                _pf_prog.progress(_pf_done / len(_pf_codes),
                                                  text=f"⚙️ 計算持倉中 {_pf_done}/{len(_pf_codes)}（{_pf_done/len(_pf_codes)*100:.0f}%）")
                                try:
                                    _pf_results[code] = future.result()
                                except Exception:
                                    _pf_results[code] = None
                        _pf_prog.empty()

                    # 【V160 Round38】持倉不走compute_cards_cached，是獨立平行運算，
                    # 即時報價要在這裡單獨接一次。
                    # 【R96】持倉是完整戰卡渲染，fetch_intraday_extras=True。
                    _pf_results = attach_live_quotes({k: v for k, v in _pf_results.items() if v},
                                                     fetch_intraday_extras=True)
                    st.session_state[_PF_CACHE_KEY] = {
                        'codes': _pf_codes_set, 'results': _pf_results, 'ts': time.time()}

                _pf_age = time.time() - st.session_state.get(_PF_CACHE_KEY, {}).get('ts', time.time())
                st.caption(f"持倉運算快取於 {_pf_age:.0f} 秒前（{_PF_TTL_SECONDS}秒內編輯不會重新整批運算）")

                # 【R98續77新增，總指揮官指示：需要一個跟速覽模式一樣的
                # 持倉總覽表，能快速看到張數/損益，且要能輸入張數/成本價】
                # 查證確認全系統只有「轉移至持倉」那個按鈕會寫入portfolio，
                # 且寫死qty=1、entry_price=當下現價，之後完全沒有任何UI
                # 能修改——這是真實、完全缺失的功能。用st.data_editor()做
                # 一個可編輯總覽表，放在逐一展開完整戰卡「之前」，不用
                # 展開每一張卡片才能看到損益，也不用透過任何表單、直接在
                # 表格格子裡改數字即可，改完按下面的按鈕存檔。
                if _pf_items:
                    st.markdown("**📋 持倉總覽（可直接編輯張數／成本價）**")
                    _pf_table_rows = []
                    for code, p_data in _pf_items:
                        c = _pf_results.get(code)
                        if not c or c.get('error'):
                            continue
                        _ent_p = safe_float(p_data.get('entry_price', c.get('price')))
                        _side = p_data.get('side', 'long')
                        _qty = safe_float(p_data.get('qty', 1))
                        _profit, _roi = calc_real_profit_v2(
                            _ent_p, float(c.get('price', 0.0)), _qty, side=_side)
                        _pf_table_rows.append({
                            '代號': code, '名稱': c.get('name', ''),
                            '方向': '多' if _side == 'long' else '空',
                            '現價': c.get('price', 0.0), '成本價': _ent_p, '張數': _qty,
                            '損益': round(_profit, 0), '損益%': round(_roi, 2),
                        })
                    if _pf_table_rows:
                        _pf_df = pd.DataFrame(_pf_table_rows)
                        _pf_edited = st.data_editor(
                            _pf_df, use_container_width=True, hide_index=True, key="pf_overview_editor",
                            disabled=['代號', '名稱', '方向', '現價', '損益', '損益%'],
                            column_config={
                                '成本價': st.column_config.NumberColumn(format="%.2f", step=0.01),
                                '張數': st.column_config.NumberColumn(format="%.0f", step=1),
                            })
                        if st.button("💾 儲存持倉總覽的修改（張數／成本價）", key="pf_overview_save",
                                    use_container_width=True):
                            for _, _row in _pf_edited.iterrows():
                                _code = _row['代號']
                                if _code in st.session_state.portfolio:
                                    st.session_state.portfolio[_code]['entry_price'] = float(_row['成本價'])
                                    st.session_state.portfolio[_code]['qty'] = float(_row['張數'])
                            save_local_db_isolated()
                            st.success("✅ 已儲存持倉總覽的修改。")
                            time.sleep(0.6)
                            st.rerun()
                    st.divider()

                cols, idx = st.columns(2), 0
                for code, p_data in _pf_items:
                    c = _pf_results.get(code)
                    if c and not c.get('error'):
                        _monitor_cards.append(c)
                        ent_p = safe_float(p_data.get('entry_price', c.get('price')))
                        # 【V160 新增：觀察區轉持倉支援做空】向下相容——這次改動之前建立的
                        # 持倉沒有side欄位，一律預設'long'(它們建立時系統只支援做多，
                        # 這是唯一合理的預設，不會讓既有持倉顯示跑掉)。
                        _side = p_data.get('side', 'long')
                        profit, roi = calc_real_profit_v2(
                            ent_p, float(c.get('price', 0.0)), safe_float(p_data.get('qty', 1)), side=_side)
                        with cols[idx % 2]:
                            _badge = "🔴 多單" if _side == 'long' else "🔵 空單"
                            _badge_color = "#ff4d4d" if _side == 'long' else "#2979ff"
                            st.markdown(f"<div style='font-size:12px; font-weight:bold; color:{_badge_color};'>"
                                       f"{_badge}</div>", unsafe_allow_html=True)
                            # 【V160新增：觀察區轉持倉支援做空】做空持倉顯示
                            # 鏡像版防守線/移動停利，戰卡本身def_line/trail_stop
                            # 是做多方向計算，這裡另算方向正確的版本。快取幾乎
                            # 必定命中，不會是額外網路呼叫。
                            if _side == 'short':
                                _s_ma5 = safe_float(c.get('ma5', 0))
                                _s_atr = safe_float(c.get('atr_val', 0))
                                if _s_ma5 > 0 and _s_atr > 0:
                                    _s_hist, _ = get_real_stock_data_yfinance(code)
                                    _s_zones = build_short_trade_zones(float(c.get('price', 0)), _s_ma5, _s_atr, _s_hist)
                                    st.markdown(
                                        f"<div style='font-size:12px; color:#9fb3c8; margin-bottom:4px;'>"
                                        f"🛡️ 做空防守線 {_s_zones['def_line']}（站上則停損）"
                                        + (f" ｜ 📉移動停利 {_s_zones['trail_stop']}（站上則回補）"
                                           if _s_zones['trail_active'] else "")
                                        + "</div>", unsafe_allow_html=True)
                            st.markdown(render_stock_card_ui(c, True, profit, roi, ent_p), unsafe_allow_html=True)
                            render_action_buttons(c, code, True)
                        idx += 1

        # 【V160】觀察區（先丟著看的候選，不列入長期追蹤）
        _obs_cards = render_list_section('observe_stocks', "👁️ 觀察區（候選標的，尚未列入長期追蹤）",
                                         config_payload, is_observe=True)
        _monitor_cards.extend(_obs_cards)

        # 【V160】常態觀測雷達（確定長期盯盤的核心清單）
        _radar_cards = render_list_section('pinned_stocks', "🎯 總指揮常態觀測雷達防線",
                                           config_payload, is_observe=False)
        _monitor_cards.extend(_radar_cards)

    # 【V159】盤中異常偵測：陽春版，只在網頁內顯示，不推播
    if _monitor_cards:
        _new_alerts = detect_intraday_anomalies(_monitor_cards)
        if _new_alerts:
            st.markdown(
                "<div style='background:#7a1010; border:2px solid #ff4d4d; border-radius:6px; "
                "padding:12px; margin-bottom:15px;'>"
                "<div style='background:#ff4d4d; color:#ffffff; font-weight:bold; font-size:14px; "
                "padding:4px 10px; border-radius:4px; display:inline-block; margin-bottom:8px;'>🚨 盤中異常偵測（這次輪詢新出現）</div>"
                "<div style='color:#ffffff; font-size:13px; line-height:1.8;'>"
                + "<br>".join(_new_alerts) + "</div></div>", unsafe_allow_html=True)
            # 【R67新增】Telegram推播——沿用detect_intraday_anomalies已經做過
            # 的去重邏輯，只回報「這次輪詢新出現」的異常，不會重複推播騷擾。
            if st.session_state.get('push_anomaly_telegram', True):
                _pushed = notify_telegram_web(
                    "🚨 [盤中異常偵測] " + datetime.now(TAIPEI_TZ).strftime('%Y-%m-%d %H:%M') + "\n"
                    + "\n".join(_new_alerts))
                if not _pushed:
                    st.caption("（Telegram推播未送出：可能是沒設定TELEGRAM_BOT_TOKEN/"
                              "TELEGRAM_CHAT_ID，或這次連線失敗。畫面上的警示不受影響。）")
    if st.session_state.get('anomaly_log'):
        with st.expander(f"📜 異常偵測紀錄（本次瀏覽階段，共 {len(st.session_state['anomaly_log'])} 則）", expanded=False):
            for _log_line in st.session_state['anomaly_log']:
                st.caption(_log_line)



    # ------------------------------------------------------------------
    # 掃描引擎
    # ------------------------------------------------------------------
    if st.session_state.get('trigger_scan', False):
        st.session_state.trigger_scan = False
        st.session_state.scan_results = []

        intel_pool = st.session_state.get('intelligence_pool', {})
        intel_cmds = [c for c in selected_cmds if "情報雷達：" in c or "情報黃金交叉" in c]

        if intel_cmds:
            target_pool = [c for c in intel_pool.keys() if c in TW_STOCK_NAMES] or list(intel_pool.keys())
        else:
            # 【V160】掃描池改依當日成交值排序，取「最值得看的N檔」而非「代碼最小的N檔」
            _pool_ordered, _pool_by_value = get_scan_pool_ordered()
            target_pool = _pool_ordered[:scan_pool_size]
            if not _pool_by_value:
                st.caption("ℹ️ 成交值排行暫時取不到（假日或端點異常），本次掃描池退回代碼順序。")

        results = []
        _all_valid_cards = []   # 【R42新增】不分是否通過篩選條件，全部納入，供同業PE中位數統計用
        progress_bar = st.progress(0)
        status_text = st.empty()
        ctx = get_script_run_ctx()

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            future_to_code = {executor.submit(calculate_signals_worker, code, config_payload, ctx): code
                              for code in target_pool}
            total = max(1, len(target_pool))
            for i, future in enumerate(concurrent.futures.as_completed(future_to_code)):
                status_text.markdown(
                    f"<div style='color:#00d2ff; font-size:13px; font-weight:bold;'>📡 並行高速掃描進度: "
                    f"{i+1}/{total} ({int((i+1)/total*100)}%)</div>", unsafe_allow_html=True)
                progress_bar.progress((i + 1) / total)

                try:
                    card = future.result()
                except Exception:
                    continue
                if not card or card.get('error', False):
                    continue
                _all_valid_cards.append(card)

                code = card.get('code', '')
                c_vol = float(card.get('vol', 0) or 0)
                if c_vol < min_volume_filter:
                    continue

                c_sources = set(intel_pool.get(code, {}).get('sources', []))
                _score_range = st.session_state.get('scan_score_range', (-10, 10))
                _c_score = card.get('score', 0)
                if not (_score_range[0] <= _c_score <= _score_range[1]):
                    continue
                if evaluate_scan_conditions(selected_cmds, card, c_sources, selected_k_patterns):
                    results.append(card)

        progress_bar.empty()
        status_text.empty()
        results.sort(key=lambda x: x.get('score', 0), reverse=True)
        st.session_state.scan_results = results
        st.session_state.scan_mode = " + ".join([cmd.split('.')[0] for cmd in selected_cmds])

        # 【V160 R42 新增】PE同業中位數——只在「真正的全市場掃描」時算(不是情報雷達
        # 那種小範圍掃描，樣本數不夠、算出來的中位數沒有意義)。算好直接存Supabase，
        # 之後讀取(get_industry_pe_stats)不用重新算，登入登出都吃現成的。
        if not intel_cmds and len(_all_valid_cards) >= 20:
            _stock_to_ind, _ = fetch_industry_map()
            if _stock_to_ind:
                compute_and_store_industry_pe(_all_valid_cards, _stock_to_ind)
                get_industry_pe_stats.clear()   # 清掉讀取快取，下次戰卡顯示能立刻吃到新算好的數字
                # 【V160 新增：雙引擎族群透視】營收YoY平均/中位數統計，跟PE同業中位數
                # 同一次全市場掃描順便算，不用另外再掃一次——複用同一份_stock_to_ind、
                # 同一份_all_valid_cards，零額外API成本。
                compute_and_store_industry_revenue(_all_valid_cards, _stock_to_ind)
                get_industry_revenue_stats.clear()

    if st.session_state.get('scan_results', []):
        st.markdown(f"### ⚡ 【{st.session_state.scan_mode}】交叉篩選戰果 ({len(st.session_state.scan_results)} 檔符合)")
        if st.button("➕ 批次部署並強制寫入常態追蹤雷達", use_container_width=True):
            for card in st.session_state.scan_results:
                _ccode = card.get('code', '')
                st.session_state.pinned_stocks[_ccode] = st.session_state.scan_mode
                log_watchlist_entry(_ccode, st.session_state.scan_mode)   # 【V160 B#14】記錄系統查詢加入
            save_local_db_isolated()
            st.success("✅ 成功綁定血統並永久存檔。")
            time.sleep(0.5)
            st.rerun()

        cols = st.columns(2)
        for idx, card in enumerate(st.session_state.scan_results):
            with cols[idx % 2]:
                st.markdown(render_stock_card_ui(card), unsafe_allow_html=True)
                # 【R90修復】同一個問題的第二個漏接處——查X掃描結果的卡片格狀
                # 顯示，一樣從來沒呼叫過render_action_buttons。
                render_action_buttons(card, card.get('code', ''), False, section_key='scan_results')

    # ==============================================================================
    # 歷史版本CHANGELOG（V155→V159完整記錄已搬進開發歷程.md，這裡不重複）
    # ==============================================================================



