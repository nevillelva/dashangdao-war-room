# ==============================================================================
# 54088 戰情室 V156 — 量化擴張 · 神盾修復版
# 相對 V155 的變更請見檔尾 CHANGELOG
# ==============================================================================
import streamlit as st
import streamlit.components.v1 as components
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, time as dt_time, timezone
# 【R96修復，見開發歷程.md時區bug章節】Streamlit Cloud系統時鐘是UTC，
# 需要精確時分比對的地方一律用datetime.now(TAIPEI_TZ)。
TAIPEI_TZ = timezone(timedelta(hours=8))
import re
import time
import random
import json
import os
import io
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
    DAY_TRADER_BROKERS, check_day_trader_alert,
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
    calc_intraday_vwap_from_bars, evaluate_vwap_position,  # 【R96新增】累積清單第7項
    fetch_industry_map_raw, FIXED_INDUSTRY_LEADERS,  # 【R96新增】5分K三關共用
    determine_signal, score_zone1_fundamental, score_zone2_technical,
    score_zone3_chips, _fmt_zone_summary,
    fetch_twse_mis_batch, _safe_mis_float,
    FinMindAPIError, set_finmind_tokens, get_fm_quota_status,
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
    detect_k_line_patterns_v152, fetch_twii_regime_history,
    _filter_backtest_one_stock, run_filter_backtest,
    summarize_filter_backtest, summarize_filter_backtest_walkforward,
    # 【R95續】情報雷達回測——compute_forward_return直接沿用；
    # run_intel_radar_backtest改名匯入，因為v160.py自己還留了一個同名的
    # 薄包裝函式(負責撈Supabase rows後才呼叫這裡)，兩者用途不同不能同名。
    compute_forward_return,
    run_intel_radar_backtest as _core_run_intel_radar_backtest,
)
import warroom_core as _wc

# 【R60新增】版本相容性檢查——這個bug已真實發生兩次(ImportError跟
# determine_signal()缺參數TypeError，且都被ThreadPoolExecutor的except
# 吞掉、畫面只顯示「全部抓價失敗」)。啟動當下直接檢查版本號，不符就明講停住。
_REQUIRED_CORE_VERSION = 103
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
BUILD_VERSION = "作戰室 正式版 v1.0 (2026-08-07 R95續29：分點缺口偵測改用真實交易日曆(颱風假/臨時休市感知)/自建5分K回溯驗證)"
BUILD_NOTES = "R94：總指揮官實測本地電腦沒裝lxml時，pd.read_html()拋ImportError——這個例外之前被parse_histock_branch_html的except Exception一起吞掉，跟「表格結構真的不符」長得一模一樣，都是回傳None、健康度顯示0家分點，導致連續好幾輪都在懷疑IP被擋或網站改版，卻沒人想到可能只是requirements.txt漏列這個套件這麼單純的原因。這輪把ImportError單獨接住往上拋，不再跟其他錯誤混在一起；fetch_histock_branch_data明確印出「缺少解析套件」的訊息；健康度檢查新增明確的lxml可用性測試，放在最前面優先檢查，一眼就能看出是不是這個原因。已用模擬ImportError的方式驗證整條錯誤訊息鏈路正確。總指揮官需要做的事：確認repo裡的requirements.txt有列出lxml，如果沒有要加上去並重新部署——這是本輪懷疑的最可能根因，但仍待總指揮官確認部署環境的requirements.txt實際內容才能100%定案。"

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


def _exit_reason_zh(reason):
    """把出場原因代碼轉成中文。代碼不在對照表裡就照原樣顯示，不隱藏、不猜。"""
    if not reason:
        return '—'
    return EXIT_REASON_ZH.get(reason, str(reason))


def _style_pnl_columns(df, cols):
    """
    【V160 新增】損益/報酬%欄位上色：紅=正（賺）、綠=負（賠），符合台股「紅漲綠跌」慣例。
    總指揮官回報：目前這些數字都沒有顏色，要一個個讀數字判斷正負很難一眼掃過去。

    用 pandas Styler 上色；若環境缺 matplotlib（Styler某些功能依賴它）導致失敗，
    優雅退回不上色的原始表格，不讓這個裝飾性功能搞掛整個績效表的顯示。
    """
    def _color(val):
        try:
            v = float(val)
        except (TypeError, ValueError):
            return ''
        if v > 0:
            return 'color: #ff4d4d; font-weight: bold;'
        if v < 0:
            return 'color: #00c853; font-weight: bold;'
        return ''
    try:
        _valid = [c for c in cols if c in df.columns]
        # 【V160 修復】Styler 會取消 Streamlit 原本的自動數字格式化，導致
        # 100.0 被顯示成 100.000000（總指揮官回報「數字太長佔版面」）。
        # 這裡明確指定四捨五入到小數點後2位。用 na_rep 避免空值顯示成 nan。
        return (df.style
                  .map(_color, subset=_valid)
                  .format(precision=2, na_rep="—", thousands=","))
    except Exception:
        try:
            # 舊版 pandas 用 applymap（新版才有 map），兩個都試一次
            return (df.style
                      .applymap(_color, subset=[c for c in cols if c in df.columns])
                      .format(precision=2, na_rep="—", thousands=","))
        except Exception:
            return df   # 上色失敗就退回原始表格，不讓功能整個掛掉

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
def _expand_blood_line(bl):
    """
    【V160】把血統字串裡的「查N」換成完整條件敘述。

    總指揮官回報：只看到「查13」不知道當初是用什麼條件掃到這檔的，
    之後要回頭檢討「哪種條件選出來的股票勝率高」就無從查起。
    對照表若還沒建好（例如尚未按過掃描），就原樣回傳，不編造。
    """
    if not bl:
        return ""
    out = str(bl)
    for tag, desc in sorted(SCAN_COMMAND_MAP.items(), key=lambda kv: -len(kv[0])):
        if tag in out:
            out = out.replace(tag, f"{tag}（{desc}）")
    return out


@st.cache_data(ttl=86400, show_spinner=False)
def fetch_trading_calendar():
    """
    【R67新增】台股官方交易日曆——解除「只處理週末、國定假日仍可能落空」
    這個已知限制。

    FinMind的TaiwanStockTradingDate是免費方案可用的資料集（已查證官方文件），
    列出所有實際有開盤的日期，直接涵蓋農曆年、颱風假、補班日這些用「週幾」
    永遠算不出來的情況。快取24小時——交易日曆一天查一次綽綽有餘。

    回傳日期字串的set；抓不到回傳None，呼叫端會退回原本的週末判斷邏輯
    （degrade成舊行為，不會整個壞掉）。
    """
    try:
        _start = (datetime.now() - timedelta(days=400)).strftime('%Y-%m-%d')
        payload = _finmind_get('https://api.finmindtrade.com/api/v4/data',
                               {'dataset': 'TaiwanStockTradingDate', 'start_date': _start},
                               max_retries=2, timeout=15)
        rows = payload.get('data', [])
        if not rows:
            return None
        _dates = {str(r.get('date', ''))[:10] for r in rows if r.get('date')}
        return _dates or None
    except Exception as e:
        print(f"[fetch_trading_calendar-診斷] 抓交易日曆失敗：{type(e).__name__}: {e}")
        return None


def get_current_or_last_trading_date():
    """
    【V160 新增】回傳「今天若是交易日就用今天，否則往前找到最近的交易日」。

    get_last_trading_date() 是固定從「昨天」起算往前找，適合用在「要抓已收盤資料」
    的情境；但建倉日不一樣 —— 平日盤中/盤後建倉就該記今天。
    週六日或非交易時段執行時，才往前retreat到最近交易日，
    避免把建倉日寫成 07/18(六) 這種沒開盤的日期。

    【R67修復】原本只用weekday()判斷週末，國定假日（農曆年、清明、颱風假等）
    會落空——例如農曆年封關期間建倉，日期會被寫成一個根本沒開盤的日子。
    現在優先查FinMind官方交易日曆(fetch_trading_calendar)，查不到才退回
    原本的週末判斷。交易日曆只涵蓋過去，所以「今天」如果比日曆最後一天還新
    （例如今天剛好是還沒被收錄的最新交易日），也會退回週末判斷，不會誤判成
    假日往前跳。
    """
    _cal = fetch_trading_calendar()
    d = datetime.now()
    if _cal:
        _newest = max(_cal)
        # 只有當「今天」落在日曆涵蓋範圍內時才信任日曆；超出範圍代表日曆還沒
        # 更新到今天，這時用日曆判斷會誤把今天當成假日，不如退回週末邏輯。
        if d.strftime('%Y-%m-%d') <= _newest:
            for _ in range(30):     # 最多往前找30天，避免資料異常時無限迴圈
                if d.strftime('%Y-%m-%d') in _cal:
                    return d.strftime('%Y-%m-%d')
                d -= timedelta(days=1)
            d = datetime.now()      # 30天內都找不到 → 資料有問題，退回週末邏輯
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.strftime('%Y-%m-%d')


def get_last_trading_date():
    """
    【R67修復】同樣補上國定假日處理：固定從「昨天」起算往前找最近的交易日。
    優先用官方交易日曆，抓不到才退回原本的週末判斷。
    """
    _cal = fetch_trading_calendar()
    d = datetime.now() - timedelta(days=1)
    if _cal:
        for _ in range(30):
            if d.strftime('%Y-%m-%d') in _cal:
                return d.strftime('%Y-%m-%d')
            d -= timedelta(days=1)
        d = datetime.now() - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.strftime('%Y-%m-%d')


@st.cache_resource
# 【V160 Round39】get_safe_session/_SESSION已搬進warroom_core.py共用。
#
# 【V160新增，Round34起未使用】執行緒層級逾時包裝在@st.cache_data環境下
# 會卡住共用_SESSION連線，已改回原生timeout參數，這裡保留定義供參考，
# 不要再拿它包用到_SESSION的呼叫。
def _call_with_hard_timeout(fn, timeout_sec=5):
    """在獨立執行緒跑 fn()，超過 timeout_sec 秒就丟 TimeoutError，
    不管 fn 本身有沒有提供 timeout 參數都能強制擋住。

    【重要，自己測試時抓到的坑】原本用 ThreadPoolExecutor 實作，結果實測
    發現：ThreadPoolExecutor 產生的執行緒預設不是 daemon，Python直譯器
    結束時有一個全域的 atexit 機制會等「所有 ThreadPoolExecutor 建立過的
    執行緒」真正跑完才讓程式退出——就算對這個executor呼叫shutdown(wait=False)
    也豁免不了這個全域行為。實測時我自己的測試腳本因此直接卡死超時，
    完全重現了fast_info沒設逾時導致整個Streamlit程式卡住的那個bug，只是
    這次是我自己的timeout-wrapper又踩了一次同一種坑。
    改用 threading.Thread(daemon=True)：daemon執行緒不會被那個全域atexit
    等待，就算底層呼叫真的永遠不回應，這條執行緒會被丟著（不會真的被殺掉，
    但也不會回頭卡住呼叫端或拖累程式退出），呼叫端會準時在 timeout_sec 秒後
    拿到 TimeoutError 繼續往下走備援邏輯。
    """
    result_q = queue.Queue(maxsize=1)

    def _worker():
        try:
            result_q.put(('ok', fn()))
        except Exception as e:
            result_q.put(('error', e))

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    try:
        status, payload = result_q.get(timeout=timeout_sec)
    except queue.Empty:
        raise TimeoutError(f"呼叫超過 {timeout_sec} 秒未回應")
    if status == 'error':
        raise payload
    return payload

# 【V160新增】記住每檔股票上次成功的市場後綴(.TW/.TWO)，process層級
# 存活。是「開機要等5-10分鐘」的第二個根因(原本每次都從.TW開始試，
# 上櫃股前兩次注定逾時失敗)，純粹加速用，猜錯仍會照跑完整四種嘗試。
_EXT_HINT = {}

# ==============================================================================
# 二、 資料庫架構（SQLite + 原子寫入 JSON + 防崩潰鎖）
# ==============================================================================
DB_LOCK = threading.Lock()


def get_db_conn():
    conn = sqlite3.connect(SQLITE_DB_FILE, check_same_thread=False, timeout=15)
    conn.execute('PRAGMA journal_mode=WAL')
    return conn


def _ensure_schema(conn):
    conn.execute('''
        CREATE TABLE IF NOT EXISTS inst_holding (
            date TEXT, symbol TEXT,
            foreign_buy REAL, trust_buy REAL, dealer_buy REAL,
            margin REAL, big_holder REAL, big_holder_date TEXT,
            PRIMARY KEY (date, symbol)
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS big_holder_history (
            code TEXT, date TEXT, percent REAL,
            PRIMARY KEY (code, date)
        )
    ''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_inst_symbol ON inst_holding(symbol, date DESC)')

    # 【V158 新增】命中率回測持久化：一次 run 對應多筆訊號明細，結果永久保存，
    # 不用每次重開網頁就砍掉重測，也能拿不同 ATR 倍數的歷史 run 互相比較。
    conn.execute('''
        CREATE TABLE IF NOT EXISTS backtest_runs (
            run_id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_time TEXT, stock_list TEXT, years INTEGER,
            atr_multiplier REAL, enable_doomsday INTEGER, use_market_regime INTEGER,
            sample_count INTEGER, mode TEXT DEFAULT 'technical'
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS backtest_signals (
            run_id INTEGER, stock TEXT, date TEXT, signal TEXT,
            future_3d_ret REAL, future_10d_ret REAL, is_breached INTEGER, filter_name TEXT
        )
    ''')
    # 【V159】舊版 V158 建出來的 DB 沒有 mode / filter_name 欄位，CREATE TABLE IF NOT EXISTS
    # 不會幫已存在的表補欄位，這裡用 ALTER TABLE 做遷移安全升級；欄位已存在時會丟例外，忽略即可。
    for alter_sql in ("ALTER TABLE backtest_runs ADD COLUMN mode TEXT DEFAULT 'technical'",
                      "ALTER TABLE backtest_signals ADD COLUMN filter_name TEXT"):
        try:
            conn.execute(alter_sql)
        except Exception:
            pass
    conn.execute('CREATE INDEX IF NOT EXISTS idx_bt_run ON backtest_signals(run_id)')
    conn.commit()


def init_sqlite_db():
    with DB_LOCK:
        conn = get_db_conn()
        _ensure_schema(conn)
        return conn


# 【R96修復，重大bug：SQLite連線每次互動都重開從未關閉】原本沒有
# @st.cache_resource導致連線越疊越多、鎖爭用。包一層cache_resource，
# process生命週期只會真正執行一次。
init_sqlite_db = st.cache_resource(init_sqlite_db)

SQLITE_CONN = init_sqlite_db()

_LAST_GOOD_LOCK = threading.Lock()
_LAST_GOOD_REVENUE = {}


def safe_upsert_big_holder(code, date_str, percent_value):
    is_valid = (percent_value is not None and percent_value != ''
                and isinstance(percent_value, (int, float)) and percent_value > 0.0)
    if not is_valid:
        return False
    local_ok = False
    with DB_LOCK:
        try:
            SQLITE_CONN.execute("""
                INSERT INTO big_holder_history (code, date, percent) VALUES (?, ?, ?)
                ON CONFLICT(code, date) DO UPDATE SET percent = excluded.percent
            """, (code, date_str, percent_value))
            SQLITE_CONN.commit()
            local_ok = True
        except Exception:
            local_ok = False
    # 【V160 雙寫】雲端寫失敗不影響本機結果（盡力而為，不阻斷主流程）
    sb_upsert_big_holder(code, date_str, percent_value)
    return local_ok


def get_latest_big_holder(code):
    with DB_LOCK:
        try:
            cursor = SQLITE_CONN.cursor()
            cursor.execute(
                "SELECT date, percent FROM big_holder_history WHERE code = ? AND percent > 0 ORDER BY date DESC LIMIT 1",
                (code,))
            row = cursor.fetchone()
            if row:
                return {'date': row[0], 'percent': row[1]}
            return None
        except Exception:
            return None


def get_db_stats():
    with DB_LOCK:
        try:
            cursor = SQLITE_CONN.cursor()
            cursor.execute("SELECT COUNT(DISTINCT date) FROM inst_holding")
            days = cursor.fetchone()[0]
            cursor.execute("SELECT date, COUNT(symbol) FROM inst_holding GROUP BY date ORDER BY date DESC LIMIT 5")
            details = cursor.fetchall()
            return days, details
        except Exception:
            return 0, []


def get_inst_data_from_db(symbol, limit=30):
    """【擴充】預設抓 30 日，供連續買賣超 VWAP 回推使用。"""
    with DB_LOCK:
        try:
            df = pd.read_sql(
                'SELECT * FROM inst_holding WHERE symbol=? ORDER BY date DESC LIMIT ?',
                SQLITE_CONN, params=(symbol, limit))
            return df
        except Exception:
            return pd.DataFrame()


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
        return None, False, f"Supabase 連線建立失敗，降級純本機模式：{e}"


@st.cache_resource
def get_supabase():
    """全域快取的 Supabase client（含啟用狀態與訊息）。"""
    client, enabled, msg = _init_supabase()
    return {"client": client, "enabled": enabled, "msg": msg}


_sb_pack = get_supabase()
SUPABASE_CONN = _sb_pack["client"]
SUPABASE_ENABLED = _sb_pack["enabled"]
_SUPABASE_INIT_MSG = _sb_pack["msg"]


def _sb_safe(fn, *args, _timeout=15, **kwargs):
    """
    包裝所有 Supabase 呼叫：未啟用直接回 None，發生例外只記警告不中斷主流程。
    回傳 (ok_bool, result_or_None)。

    【R95續7新增_timeout防呆】supabase-py底層client建立時沒有指定明確的
    網路逾時，正常情況下底層httpx有自己的預設值，但總指揮官回報「登入後
    小人跑好幾分鐘卡住」，追查發現登入按鈕點下去的當下就會呼叫
    hydrate_state_from_cloud()→sb_load_user_state()→這裡，這是整個開機
    流程裡「連進度條都還沒機會畫出來」的最早一個Supabase呼叫，如果底層
    連線真的卡住（網路異常、DNS問題等），使用者會看到畫面完全沒反應、
    不知道是卡在哪裡。這裡用一個獨立執行緒＋join逾時，幫「每一個」Supabase
    呼叫都加上一道最後防線——不管supabase-py底層實際版本的timeout設定是
    什麼，15秒內沒回來就當作失敗、放行讓主流程繼續（本機/雲端資料不同步
    總比整個畫面卡死好，而且下次rerun還會再試一次）。這是共用包裝函式，
    修好這裡等於所有呼叫端（開機同步、單檔同步、情報準確度...幾十個呼叫
    點）一次性受益，不用一個一個去追。
    """
    if not SUPABASE_ENABLED or SUPABASE_CONN is None:
        return False, None
    _executor = _get_sb_call_executor()
    try:
        future = _executor.submit(fn, *args, **kwargs)
        return True, future.result(timeout=_timeout)
    except concurrent.futures.TimeoutError:
        print(f"[Supabase 警告] {getattr(fn, '__name__', 'call')} 逾時（{_timeout}秒），已放棄本次呼叫")
        return False, None
    except Exception as e:
        try:
            print(f"[Supabase 警告] {getattr(fn, '__name__', 'call')} 失敗: {e}")
        except Exception:
            pass
        return False, None


_SB_CALL_EXECUTOR = None


def _get_sb_call_executor():
    """
    【R95續7新增】_sb_safe共用的小型執行緒池，只負責幫Supabase呼叫加逾時
    防護，不是給一般平行運算用。獨立一個小池子，跟頁面裡其他地方
    (掃描/回測)自己開的ThreadPoolExecutor完全不共用，避免互相搶執行緒
    額度、也避免每次呼叫都重新建立執行緒池的開銷。

    【R95續19修復——真正的登入後戰情速覽卡住根因】原本這裡只給4條執行緒，
    當初(續7)設計時只是為了保護登入路徑那種零星、低頻的Supabase呼叫。
    但續15把_smart_cached_call接上Supabase共享快取後，戰情速覽這種
    「8檔股票平行算」的場景，變成8個worker各自要幫營收/股利各打一次
    Supabase查詢（還沒算寫入），全部擠著搶同一個只有4條執行緒的池子——
    等於把原本8路平行的效果，在Supabase這一段打回接近4路甚至更少，
    total等待時間疊加起來，這正是總指揮官反映「戰情速覽10檔要等超過
    2分鐘、連第一列都畫不出來」的真正根因，比純粹「FinMind本身慢」更
    直接：不是外部API慢，是我們自己家裡的執行緒池不夠用、大家在排隊。
    改成16條，15秒的逾時防護完全不變，只是讓更多呼叫能同時進行、減少
    排隊等待。
    """
    global _SB_CALL_EXECUTOR
    if _SB_CALL_EXECUTOR is None:
        _SB_CALL_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=16, thread_name_prefix="sb_safe")
    return _SB_CALL_EXECUTOR


# ---- 雙寫：三大法人籌碼 ----
def sb_upsert_inst_holding(rows):
    """
    rows: list of dict，每筆含 date/symbol/foreign_buy/trust_buy/dealer_buy/margin。
    對應 Supabase inst_holding 表，用 (date, symbol) 為衝突鍵做 upsert。
    【V160】分批寫入（每批 500 筆），避免單次 payload 過大被拒或逾時。
    """
    def _do_batch(batch_payload):
        return SUPABASE_CONN.table("inst_holding").upsert(batch_payload, on_conflict="date,symbol").execute()

    payload = []
    for r in rows:
        payload.append({
            "date": r["date"], "symbol": r["symbol"],
            "foreign_buy": r.get("foreign_buy", 0), "trust_buy": r.get("trust_buy", 0),
            "dealer_buy": r.get("dealer_buy", 0),
            # 【R95修復】margin預設值改成None(不是0)——呼叫端大多不帶這個
            # key代表「不動融資欄位」，default=0會把「不知道」誤寫成「變化是0」。
            "margin": r.get("margin", None),
            "big_holder": r.get("big_holder", 0), "big_holder_date": r.get("big_holder_date", ""),
        })

    all_ok = True
    BATCH = 500
    for i in range(0, len(payload), BATCH):
        ok, _ = _sb_safe(_do_batch, payload[i:i + BATCH])
        all_ok = all_ok and ok
    return all_ok


# ---- 雙寫：千張大戶 ----
def sb_upsert_big_holder(code, date_str, percent_value):
    def _do():
        data = {"code": code, "date": date_str, "percent": percent_value}
        return SUPABASE_CONN.table("big_holder_history").upsert(data, on_conflict="code,date").execute()
    ok, _ = _sb_safe(_do)
    return ok


# ---- 開機同步：從 Supabase 回填本機 SQLite ----
def _sb_fetch_all(table_name, gte_col=None, gte_val=None, page_size=1000, max_seconds=None):
    """
    【V160 修復】supabase-py 單次查詢預設最多回傳 1000 筆。這裡用 .range() 分頁，
    一批一批撈直到撈完，突破 1000 筆上限。回傳所有 row 的 list。
    任何一批失敗就停止並回傳目前已撈到的資料（盡力而為，不中斷主流程）。

    【R95續7新增】max_seconds：可選的總耗時安全上限——開機回填隨著資料量
    自然增長（尤其這輪修好幾個「以前一直失敗、現在開始正常寫入」的資料源
    之後，累積速度只會更快），分頁撈取的批次數會跟著變多，總指揮官反映
    「登入後小人跑好幾分鐘卡住」，這是最直接的防線：撈到這個秒數還沒撈完，
    就帶著目前已經撈到的資料誠實提早結束，不讓開機流程被無上限地拖住。
    只在明確傳入時生效，不傳(None)完全維持原本行為(例如手動補推那種要求
    完整性優先於速度的場景)。
    """
    all_rows = []
    start = 0
    _t0 = time.time()
    while True:
        if max_seconds is not None and (time.time() - _t0) > max_seconds:
            break
        def _do():
            q = SUPABASE_CONN.table(table_name).select("*")
            if gte_col is not None and gte_val is not None:
                q = q.gte(gte_col, gte_val)
            # range 是包含兩端的閉區間，所以每批抓 page_size 筆
            return q.range(start, start + page_size - 1).execute()
        ok, res = _sb_safe(_do)
        if not ok or res is None or not getattr(res, "data", None):
            break
        batch = res.data
        all_rows.extend(batch)
        if len(batch) < page_size:   # 最後一批（不足一頁）→ 撈完了
            break
        start += page_size
        if start > 500000:           # 安全上限，避免異常情況無限迴圈
            break
    return all_rows


def sync_from_supabase_on_boot(days_back=None, progress_cb=None):
    """
    App 開機時呼叫一次：把 Supabase 上最近 days_back 天的籌碼 + 大戶資料，
    回填本機 SQLite。這樣就算 Streamlit Cloud 容器把本機 DB 清空，開機一次就補回。
    只在 Supabase 啟用時執行；未啟用直接跳過（純本機模式）。
    回傳補回的筆數 (inst_rows, bh_rows)，失敗回 (0, 0)。

    【V160】days_back 改為可從 system_config 調整（側邊欄「⚙️開機回填天數設定」），
    預設仍是45天。總指揮官若覺得每次重開容器等太久，可以縮小這個天數換取更快登入——
    這只影響「本機讀取快取」的涵蓋範圍，Supabase 雲端的完整歷史不受影響，
    之後要看更久的資料，個股同步/查詢仍會即時從雲端補齊。

    【V160】progress_cb：可選的進度回報函式，簽名 progress_cb(pct, label)，
    pct 是 0.0~1.0。總指揮官要求把 spinner 換成百分比進度條，這是資料來源。
    沒傳就完全不影響原本行為（純本機模式或排程呼叫時就不需要）。
    """
    def _report(pct, label):
        if progress_cb:
            try:
                progress_cb(pct, label)
            except Exception:
                pass   # 進度回報失敗不該影響實際同步
    if days_back is None:
        try:
            days_back = int(float(sb_get_config('boot_refill_days', '45')))
        except (TypeError, ValueError):
            days_back = 45
    if not SUPABASE_ENABLED or SUPABASE_CONN is None:
        return 0, 0
    cutoff = (datetime.now() - timedelta(days=days_back)).strftime('%Y-%m-%d')
    inst_rows = bh_rows = 0
    _report(0.05, "連線雲端中")

    # 【V160修復】用分頁撈取45天內全部籌碼。
    # 【R95續7】max_seconds=20——開機同步是使用者等待的關鍵路徑，寧可提早
    # 結束、資料撈不完整(下次會補)，也不要卡好幾分鐘。
    _report(0.15, "下載籌碼資料中")
    inst_data = _sb_fetch_all("inst_holding", gte_col="date", gte_val=cutoff, max_seconds=20)
    _report(0.45, f"寫入籌碼資料（{len(inst_data):,} 筆）")
    if inst_data:
        try:
            # 【V160效能修復】改用executemany()批次寫入取代逐列execute()，減少
            # Python層呼叫次數，數十倍加速。
            # 【R95修復】margin預設值改成None，維持Supabase上NULL的語意。
            _rows_tuples = [
                (r.get("date"), r.get("symbol"), r.get("foreign_buy", 0), r.get("trust_buy", 0),
                 r.get("dealer_buy", 0), r.get("margin", None), r.get("big_holder", 0),
                 r.get("big_holder_date", ""))
                for r in inst_data
            ]
            with DB_LOCK:
                # 【R95續7修復】原本ON CONFLICT無條件覆蓋margin，Supabase
                # NULL時會把本機真實數字洗成NULL。改用COALESCE，NULL時保留
                # 本機原值，只有Supabase真的有數字才覆蓋。
                SQLITE_CONN.executemany('''
                    INSERT INTO inst_holding (date, symbol, foreign_buy, trust_buy, dealer_buy, margin, big_holder, big_holder_date)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(date, symbol) DO UPDATE SET
                        foreign_buy=excluded.foreign_buy, trust_buy=excluded.trust_buy,
                        dealer_buy=excluded.dealer_buy,
                        margin=COALESCE(excluded.margin, inst_holding.margin)
                ''', _rows_tuples)
                SQLITE_CONN.commit()
            inst_rows = len(inst_data)
        except Exception as e:
            print(f"[Supabase 開機同步] 回填 inst_holding 失敗: {e}")

    _report(0.70, "下載大戶資料中")
    bh_data = _sb_fetch_all("big_holder_history", gte_col="date", gte_val=cutoff, max_seconds=15)
    _report(0.85, f"寫入大戶資料（{len(bh_data):,} 筆）")
    if bh_data:
        try:
            # 同樣改用 executemany，過濾邏輯（percent>0）先在 Python list 端做完
            _bh_tuples = [(r.get("code"), r.get("date"), r.get("percent"))
                         for r in bh_data if r.get("percent") and r.get("percent") > 0]
            if _bh_tuples:
                with DB_LOCK:
                    SQLITE_CONN.executemany('''
                        INSERT INTO big_holder_history (code, date, percent) VALUES (?, ?, ?)
                        ON CONFLICT(code, date) DO UPDATE SET percent=excluded.percent
                    ''', _bh_tuples)
                    SQLITE_CONN.commit()
            bh_rows = len(bh_data)
        except Exception as e:
            print(f"[Supabase 開機同步] 回填 big_holder_history 失敗: {e}")

    return inst_rows, bh_rows


# ---- 系統設定表：可在網頁上調整的參數（例如每日系統選股總額） ----
def sb_get_config(config_key, default=None):
    """讀系統設定；Supabase 未啟用或查無資料時回 default。"""
    def _do():
        return SUPABASE_CONN.table("system_config").select("config_value").eq("config_key", config_key).limit(1).execute()
    ok, res = _sb_safe(_do)
    if ok and res is not None and getattr(res, "data", None):
        try:
            return res.data[0]["config_value"]
        except Exception:
            return default
    return default


def push_all_local_to_supabase(progress_cb=None):
    """
    【V160】手動補推：把本機 SQLite 的全部籌碼 + 大戶資料補推到 Supabase。
    用途：雙寫功能上線前匯入的舊資料、或 Supabase 當機期間漏寫的資料，一鍵補平。
    upsert 以主鍵為衝突鍵，重複推不會產生重複列（冪等）。
    回傳 (inst_pushed, bh_pushed)。
    """
    if not SUPABASE_ENABLED or SUPABASE_CONN is None:
        return 0, 0
    inst_pushed = bh_pushed = 0

    # 籌碼
    with DB_LOCK:
        try:
            inst_df = pd.read_sql('SELECT * FROM inst_holding', SQLITE_CONN)
        except Exception:
            inst_df = pd.DataFrame()
    if not inst_df.empty:
        rows = inst_df.to_dict('records')
        BATCH = 500
        for i in range(0, len(rows), BATCH):
            batch = rows[i:i + BATCH]
            def _do_inst():
                return SUPABASE_CONN.table("inst_holding").upsert(batch, on_conflict="date,symbol").execute()
            ok, _ = _sb_safe(_do_inst)
            if ok:
                inst_pushed += len(batch)
            if progress_cb:
                progress_cb('inst', min(i + BATCH, len(rows)), len(rows))

    # 大戶
    with DB_LOCK:
        try:
            bh_df = pd.read_sql('SELECT * FROM big_holder_history WHERE percent > 0', SQLITE_CONN)
        except Exception:
            bh_df = pd.DataFrame()
    if not bh_df.empty:
        rows = bh_df.to_dict('records')
        BATCH = 500
        for i in range(0, len(rows), BATCH):
            batch = rows[i:i + BATCH]
            def _do_bh():
                return SUPABASE_CONN.table("big_holder_history").upsert(batch, on_conflict="code,date").execute()
            ok, _ = _sb_safe(_do_bh)
            if ok:
                bh_pushed += len(batch)
            if progress_cb:
                progress_cb('bh', min(i + BATCH, len(rows)), len(rows))

    return inst_pushed, bh_pushed


def log_intel_performance(symbol, source, tag, intel_date=None):
    """
    【V160 B#13】情報準確度追蹤：情報輸入當下只記錄一筆待辦，base_price 留 0，
    之後由「計算情報準確度」時再補抓歷史基準價（用 intel_date 當天的收盤）。
    【V160 效能修復】不在儲存當下同步抓 yfinance 報價——10檔各抓一次會讓儲存卡好幾分鐘。

    【R88新增】intel_date：允許補登過去的情報（例如手上有幾天前的舊報告，
    想馬上驗證當時的判斷準不準，不用等到「現在才輸入」導致基準日期算錯）。
    不傳就沿用原本行為(用今天)，向下相容既有呼叫端。
    """
    def _do():
        data = {"symbol": symbol, "source": source, "tag": tag,
                "intel_date": intel_date or datetime.now().strftime('%Y-%m-%d'), "base_price": 0.0}
        return SUPABASE_CONN.table("intel_performance").insert(data).execute()
    _sb_safe(_do)


def build_card_text_report(c):
    """
    【V160 B#12】把整張戰卡轉成純文字報告，供一鍵複製貼到外部AI分析。
    包含三大戰區所有關鍵數據。
    """
    lines = []
    lines.append(f"【{c.get('name')} ({c.get('code')}) 戰情快照】")
    lines.append(f"現價 {c.get('price')} | 漲跌 {c.get('gain')}% | 決策判定 {c.get('signal_text')}（評分{c.get('score')}）")
    lines.append("")
    lines.append("[第一戰區 基本財報估價]")
    lines.append(f"營收年增 {c.get('rev_yoy')}% ({c.get('rev_month')}) | 月增 {c.get('rev_mom')}%")
    lines.append(f"PE {c.get('pe')}（歷史百分位 {c.get('pe_percentile')}%）| EPS {c.get('eps')}")
    lines.append(f"便宜價 {c.get('cheap_price')} | 合理價 {c.get('fair_price')} | 樂觀價 {c.get('dream_price')} | 殖利率防守價 {c.get('def_price')}")
    lines.append(f"殖利率 {c.get('div_yield')}% | 綜合價值分數 {c.get('value_score')} | 地雷 {'是' if c.get('landmine') else '否'}")
    lines.append("")
    lines.append("[第二戰區 技術防守]")
    lines.append(f"5MA {c.get('ma5')} | 20MA {c.get('ma20')} | 60MA {c.get('ma60')}")
    lines.append(f"MACD {c.get('macd_str')} | RSI {c.get('rsi_val')} | 乖離率 {c.get('bias_val')}%")
    lines.append(f"短線停利點 {c.get('atk_zone')} | 防守停損 {c.get('def_line')}（緩衝 {c.get('buffer_pct')}%）| ATR {c.get('atr_val')}")
    lines.append(f"動態移動停利 {c.get('trail_stop')} | 布林上軌 {c.get('bb_upper')} | 爆量比 {c.get('vol_ratio')}")
    lines.append("")
    lines.append("[第三戰區 三大法人籌碼]")
    lines.append(f"外資 單日 {c.get('f_buy')}張 | 5日 {c.get('f_5d')}張 | 10日 {c.get('f_10d')}張")
    lines.append(f"投信 單日 {c.get('t_buy')}張 | 5日 {c.get('t_5d')}張 | 10日 {c.get('t_10d')}張")
    lines.append(f"自營商 {c.get('d_buy')}張 | 融資增減 {c.get('margin_diff')}張 | 千張大戶 {c.get('big_holder')}%")
    lines.append("")
    lines.append("請以台灣股市操盤幕僚身分，針對以上數據做多空分析與明日進出場建議。")
    return "\n".join(lines)


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


def get_intel_accuracy_summary(custom_days=None, progress_callback=None):
    """
    【V160 B#13】情報來源準確度彙總：依「來源」分組，算 3/10/20 日（+自訂天數）平均報酬與勝率。
    從 Supabase intel_performance 讀所有紀錄，即時補算報酬（無未來函數）。

    【R51新增】progress_callback(done, total)——compute_forward_return每筆都要真的
    打一次yfinance，紀錄一多就不快，原本完全看不出算到第幾筆。
    """
    if not SUPABASE_ENABLED:
        return pd.DataFrame(), pd.DataFrame()
    rows = _sb_fetch_all("intel_performance")
    if not rows:
        return pd.DataFrame(), pd.DataFrame()

    windows = [3, 10, 20]
    if custom_days and custom_days not in windows:
        windows.append(custom_days)

    enriched = []
    _total = len(rows)
    for _i, r in enumerate(rows):
        sym, src, tag = r.get('symbol'), r.get('source', '未知'), r.get('tag', '')
        bp, idate = r.get('base_price'), r.get('intel_date')
        rec = {'symbol': sym, 'source': src, 'tag': tag}
        for w in windows:
            rec[f'ret_{w}'] = compute_forward_return(sym, bp, idate, w)
        enriched.append(rec)
        if progress_callback:
            progress_callback(_i + 1, _total)
    edf = pd.DataFrame(enriched)

    def _summarize(group_col):
        out = []
        for key, sub in edf.groupby(group_col):
            row = {group_col: key, '樣本數': len(sub)}
            for w in windows:
                col = f'ret_{w}'
                valid = sub[col].dropna()
                if len(valid) > 0:
                    row[f'{w}日勝率%'] = round((valid > 0).mean() * 100, 1)
                    row[f'{w}日均報酬%'] = round(valid.mean(), 2)
                else:
                    row[f'{w}日勝率%'] = None
                    row[f'{w}日均報酬%'] = None
            out.append(row)
        return pd.DataFrame(out)

    return _summarize('source'), _summarize('tag')


def list_intel_sources():
    """
    【R95新增】情報雷達回測支援用：列出intel_performance裡出現過的所有不重複
    來源(source)，供「完整濾網回測」頁籤的多選清單使用。只抓原始欄位，不觸發
    compute_forward_return的yfinance查價(那個成本留到真的按下「執行回測」才付)，
    所以這裡很便宜，可以放心在頁籤一展開就呼叫。
    """
    if not SUPABASE_ENABLED:
        return []
    rows = _sb_fetch_all("intel_performance")
    if not rows:
        return []
    return sorted({r.get('source', '未知') for r in rows if r.get('source')})


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


def get_manual_vs_system_pk(progress_callback=None):
    """
    【V160 B#14】手動加入 vs 系統查詢 勝率PK：從 watchlist_entry_log 讀取，
    依 source_type（manual vs 查X）分兩組，算「加入日到今天」的報酬率與勝率。

    【R51新增】progress_callback(done, total)——每筆都要真的打一次yfinance。
    """
    if not SUPABASE_ENABLED:
        return pd.DataFrame()
    rows = _sb_fetch_all("watchlist_entry_log")
    if not rows:
        return pd.DataFrame()

    manual_rets, system_rets = [], []
    _total = len(rows)
    for _i, r in enumerate(rows):
        sym, stype, edate, eprice = r.get('symbol'), r.get('source_type', ''), r.get('entry_date'), r.get('entry_price')
        try:
            tk = _yf_ticker(f"{sym}.TW")
            hist = tk.history(period="1y", timeout=8).dropna(subset=['Close'])
            if hist.empty:
                tk = _yf_ticker(f"{sym}.TWO")
                hist = tk.history(period="1y", timeout=8).dropna(subset=['Close'])
            if hist.empty:
                continue
            # entry_price 為 0 → 從歷史補 entry_date 當天（或次一交易日）收盤
            if not eprice or eprice <= 0:
                hist_idx = hist.copy()
                hist_idx.index = hist_idx.index.strftime('%Y-%m-%d')
                after = [d for d in hist_idx.index if d >= edate]
                if not after:
                    continue
                eprice = float(hist_idx.loc[after[0], 'Close'])
            if not eprice or eprice <= 0:
                continue
            cur_price = float(hist['Close'].iloc[-1])
            ret = (cur_price - eprice) / eprice * 100
            if stype == 'manual':
                manual_rets.append(ret)
            else:
                system_rets.append(ret)
        except Exception:
            continue
        finally:
            if progress_callback:
                progress_callback(_i + 1, _total)

    def _stats(rets, label):
        if not rets:
            return {'選股方式': label, '樣本數': 0, '平均報酬%': None, '勝率%': None}
        import statistics
        return {'選股方式': label, '樣本數': len(rets),
                '平均報酬%': round(statistics.mean(rets), 2),
                '勝率%': round(sum(1 for x in rets if x > 0) / len(rets) * 100, 1)}

    return pd.DataFrame([_stats(manual_rets, '👤 手動選股'), _stats(system_rets, '🤖 系統查詢')])


def log_watchlist_entry(symbol, source_type):
    """【V160 B#14】記錄一檔加入雷達的來源(manual/查X)、日期，供勝率PK。
    【V160 效能】不在當下抓報價（勝率PK計算時再從歷史補 entry_date 收盤），避免加入卡頓。"""
    def _do():
        data = {"symbol": symbol, "source_type": source_type,
                "entry_date": datetime.now().strftime('%Y-%m-%d'), "entry_price": 0.0, "is_active": 1}
        return SUPABASE_CONN.table("watchlist_entry_log").insert(data).execute()
    _sb_safe(_do)


def sb_set_config(config_key, config_value, description=""):
    def _do():
        data = {"config_key": config_key, "config_value": str(config_value), "description": description}
        return SUPABASE_CONN.table("system_config").upsert(data, on_conflict="config_key").execute()
    ok, _ = _sb_safe(_do)
    return ok


# ==============================================================================
# 二之四、系統自主選股模擬倉引擎 (V160 A階段)
# ------------------------------------------------------------------------------
# 全自動選股+進出場，同時做多做空兩個模擬倉。22:00訊號產生，隔日9:01執行。
# 出場：跌破防守線停損/觸及短線停利點停利，空單反向。
# ==============================================================================
def get_system_capital():
    """讀每日系統選股總額（可在網頁調整，存 system_config）。預設30萬。"""
    v = sb_get_config('system_pick_daily_capital', '300000')
    try:
        return int(float(v))
    except Exception:
        return 300000


def get_trail_config():
    """
    【V160 延伸4】ATR 移動停利設定。存 system_config，可在網頁開關與調參。

    為什麼要有這個功能：原本的出場規則B是「固定停利點」，一碰到就出場，
    這會在真正的大波段行情裡提早砍掉獲利（賺賠比被壓低）。移動停利改成
    「隨著價格往有利方向走，停損線跟著往上抬，只有回檔超過 N×ATR 才出場」，
    讓賺的單能抱久一點。

    注意：移動停利提高的是「賺賠比」，不是「勝率」——實務上它甚至可能小幅
    降低勝率（因為部分原本會碰到固定停利的單，改成回檔出場時價格較低）。
    所以做成可開關，讓總指揮官能自己A/B比較，而不是我單方面替你決定。

    回傳 dict：enabled（是否啟用）、mult（回檔幾倍ATR出場）、activate_mult（獲利幾倍ATR才啟動）。
    """
    enabled = sb_get_config('trail_stop_enabled', '0') == '1'
    try:
        mult = float(sb_get_config('trail_stop_mult', '2.0'))
    except (TypeError, ValueError):
        mult = 2.0
    try:
        act = float(sb_get_config('trail_stop_activate_mult', '1.0'))
    except (TypeError, ValueError):
        act = 1.0
    return {'enabled': enabled, 'mult': mult, 'activate_mult': act}


def compute_trail_stop(side, entry, peak, atr, mult=2.0, activate_mult=1.0):
    """
    【V160 延伸4】計算移動停利線。回傳 (停利線, 是否已啟動)。

    設計重點（刻意寫清楚，讓判斷邏輯可被檢視）：
      1. peak = 進場後的「最高價」（做多）或「最低價」（做空），是單調的——
         只會往有利方向更新，不會退回去。這是移動停利的核心語意，
         跟戰卡上那個「近20日最高-1.5ATR」不同（那是滾動窗，不綁進場點）。
      2. 只有在獲利超過 activate_mult × ATR 之後才啟動，否則一進場就掛一條
         很近的停損線，等於把正常波動當成出場訊號，會被洗掉。
      3. 未啟動時回傳 (0, False)，呼叫端就沿用原本的固定防守線，不會變成沒有停損。
    """
    if atr <= 0 or entry <= 0 or peak <= 0:
        return 0.0, False
    if side == 'long':
        # 獲利幅度不足 → 還不啟動
        if peak - entry < activate_mult * atr:
            return 0.0, False
        return round(peak - mult * atr, 2), True
    else:  # short：peak 存的是進場後最低價
        if entry - peak < activate_mult * atr:
            return 0.0, False
        return round(peak + mult * atr, 2), True


def sb_update_peak_price(position_id, peak):
    """把最新的進場後極值寫回 Supabase，讓移動停利線能單調前進。"""
    def _do():
        return (SUPABASE_CONN.table("system_portfolio")
                .update({"peak_price": peak}).eq("id", position_id).execute())
    _sb_safe(_do)


def sb_insert_system_portfolio(entries):
    """批次寫入系統模擬倉持倉。"""
    if not entries:
        return False
    def _do():
        return SUPABASE_CONN.table("system_portfolio").insert(entries).execute()
    ok, _ = _sb_safe(_do)
    return ok


def sb_get_system_holdings(status='holding'):
    """讀系統模擬倉持倉。"""
    def _do():
        return SUPABASE_CONN.table("system_portfolio").select("*").eq("status", status).execute()
    ok, res = _sb_safe(_do)
    if ok and res is not None and getattr(res, "data", None):
        return res.data
    return []


def sb_get_system_occupied():
    """
    【V160 修復】取得「已被佔用」的標的集合，同時涵蓋 holding（已持倉）與 pending（待執行）。

    為什麼需要這個：原本選股只排除 status='holding' 的標的，但排程流程是
    22:00 選股寫入 pending → 隔日 9:01 才轉 holding。若同一天選股跑了兩次
    （手動測試 + 排程各一次），第二次看不到第一次留下的 pending 紀錄，
    就會對同一檔重複建倉，隔日兩筆一起轉 holding、之後各出場一次
    （症狀：Telegram 出場通知同一檔出現兩次、獲利%完全相同）。

    回傳 (occupied_long, occupied_short) 兩個 set。
    """
    def _do():
        return (SUPABASE_CONN.table("system_portfolio")
                .select("symbol,side,status")
                .in_("status", ["holding", "pending"]).execute())
    ok, res = _sb_safe(_do)
    rows = res.data if (ok and res is not None and getattr(res, "data", None)) else []
    occ_long = {r.get('symbol') for r in rows if r.get('side') == 'long' and r.get('symbol')}
    occ_short = {r.get('symbol') for r in rows if r.get('side') == 'short' and r.get('symbol')}
    return occ_long, occ_short


def sb_log_system_run(run_date, stage, picked, executed, gate_status, note):
    def _do():
        data = {"run_date": run_date, "stage": stage, "picked_count": picked,
                "executed_count": executed, "gate_status": gate_status, "note": note}
        return SUPABASE_CONN.table("system_run_log").insert(data).execute()
    _sb_safe(_do)


def system_select_candidates(config_payload, scan_pool, top_n=5):
    """
    【V160 A階段】系統自動選股：掃描 scan_pool，回傳 (long_candidates, short_candidates)。
    做多候選：決策判定「偏多攻擊」(評分>=3)，排除地雷/處置風險。
    做空候選：決策判定「偏空防守」(評分<=-3)，排除處置風險。
    各依評分絕對值排序取前 top_n。
    【V160 修復】排除已經持有中的標的（同方向），避免重複執行時對同一檔重複加碼、
    產生像「加高被買兩次、進場價還不一樣」這種重複持倉。
    【V160 修復2】排除範圍從「只看 holding」擴大為「holding + pending」，
    因為 pending（已選股、待隔日開盤執行）也已經佔用了這檔標的的名額，
    否則同一天選股跑兩次會產生兩筆重複倉。
    """
    held_long, held_short = sb_get_system_occupied()

    longs, shorts = [], []
    for code in scan_pool:
        c = calculate_signals_worker(code, config_payload)
        if not c or c.get('error'):
            continue
        sig = c.get('signal_text', '')
        score = c.get('score', 0)
        d_risk = (c.get('disposal_risk') or {}).get('level', 'none')
        if d_risk == 'high':      # 排除處置風險高的
            continue
        if '偏多攻擊' in sig and score >= 3 and not c.get('landmine') and code not in held_long:
            longs.append(c)
        elif '偏空防守' in sig and score <= -3 and code not in held_short:
            shorts.append(c)
    longs.sort(key=lambda x: x.get('score', 0), reverse=True)
    shorts.sort(key=lambda x: x.get('score', 0))
    return longs[:top_n], shorts[:top_n]


def system_build_entries(candidates, side, run_date, total_capital, trigger_source='manual'):
    """把候選轉成進場明細（依檔數平分資金，用開盤價/現價當進場價）。
    【V160】同時記錄選股理由，供之後分析高勝率標的的共同特徵。
    【V160 修復】防守線/停利點原本不分方向，直接套用做多式技術指標（MA5-0.5ATR當防守、
    price+1ATR當停利），這對做空來說方向是顛倒的——做空的防守線應該在進場價「上方」
    （漲破才停損），停利點應該在「下方」（跌破才停利）。現在依 side 給對應方向的正確數值，
    跟 system_check_exits 實際使用的出場規則（多單 defl<cur→停損／short entry×1.03→停損）
    保持一致，畫面顯示的數字才不會誤導。
    """
    if not candidates:
        return []
    per_capital = total_capital / len(candidates)
    entries = []
    for c in candidates:
        price = float(c.get('price', 0) or 0)
        if price <= 0:
            continue
        shares = int(per_capital / (price * 1000))   # 張數（1張=1000股）
        if shares < 1:
            shares = 1
        reasons = c.get('reasons', [])
        reason_text = (f"{c.get('signal_text', '')}（評分{c.get('score')}）｜"
                       f"{'、'.join(reasons) if reasons else '技術面達標'}｜"
                       f"爆量比{float(c.get('vol_ratio', 0) or 0):.1f} RSI{float(c.get('rsi_val', 0) or 0):.0f} "
                       f"外資5日{float(c.get('f_5d', 0) or 0):+.0f}張")
        if side == 'long':
            def_line = float(c.get('def_line', 0) or 0)       # 進場價下方，跌破停損
            take_profit = float(c.get('atk_zone', 0) or 0)    # 進場價上方，觸及停利
        else:
            def_line = round(price * 1.03, 2)                 # 做空：進場價上方，漲破停損
            take_profit = round(price * 0.95, 2)              # 做空：進場價下方，跌破停利
        entries.append({
            "symbol": c.get('code'), "name": c.get('name'),
            "entry_date": run_date, "entry_price": price, "shares": shares,
            "capital": round(shares * price * 1000, 0),
            "def_line": def_line,
            "take_profit": take_profit,
            "status": "holding", "side": side,   # 'long' or 'short'
            "select_reason": reason_text,   # 【V160】選股理由
            # 【V160 新增】來源標記：manual=網頁手動測試鈕，scheduler=排程自動。
            # 讓你能分辨績效表裡哪些是真正的自動化成果。
            "trigger_source": trigger_source,
        })
    return entries


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


def system_apply_exits(exits):
    """把出場更新寫回 Supabase（status→closed）。"""
    for e in exits:
        def _do():
            return SUPABASE_CONN.table("system_portfolio").update({
                "status": "closed", "exit_date": datetime.now().strftime('%Y-%m-%d'),
                "exit_price": e['exit_price'], "exit_reason": e['exit_reason'],
                "realized_pnl": e['realized_pnl'], "realized_roi": e['realized_roi'],
            }).eq("id", e['id']).execute()
        _sb_safe(_do)


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


def system_apply_add_reduce(actions):
    """
    把加減碼動作寫回 Supabase：更新張數、重算加權平均進場成本、累加加/減碼次數。
    加權平均：新成本 = (舊張數×舊成本 + 加碼張數×加碼價) / 總張數
    """
    for a in actions:
        old_shares = a['old_shares']
        add_shares = a['add_shares']
        old_entry = a['old_entry']
        add_price = a['price']
        new_shares = old_shares + add_shares
        new_avg = ((old_shares * old_entry + add_shares * add_price) / new_shares) if new_shares > 0 else old_entry
        update_fields = {
            "shares": new_shares,
            "entry_price": round(new_avg, 2),
            "capital": round(new_shares * new_avg * 1000, 0),
        }
        if a['action'] == 'add':
            update_fields["add_count"] = a['add_count'] + 1
        else:
            update_fields["reduce_count"] = a['reduce_count'] + 1

        def _do():
            return SUPABASE_CONN.table("system_portfolio").update(update_fields).eq("id", a['id']).execute()
        _sb_safe(_do)


def get_system_portfolio_stats():
    """
    【V160 A階段】系統模擬倉績效統計：分多空兩組，算已實現勝率/報酬 + 未實現持倉。
    回傳 dict。
    """
    holding = sb_get_system_holdings('holding')
    closed = sb_get_system_holdings('closed')

    def _side_stats(records, side):
        subset = [r for r in records if r.get('side') == side]
        if not subset:
            return {'筆數': 0, '勝率%': None, '平均報酬%': None, '總損益': 0}
        rois = [float(r.get('realized_roi', 0) or 0) for r in subset]
        pnls = [float(r.get('realized_pnl', 0) or 0) for r in subset]
        wins = sum(1 for x in rois if x > 0)
        return {'筆數': len(subset), '勝率%': round(wins / len(subset) * 100, 1),
                '平均報酬%': round(sum(rois) / len(rois), 2), '總損益': round(sum(pnls), 0)}

    return {
        'long_closed': _side_stats(closed, 'long'),
        'short_closed': _side_stats(closed, 'short'),
        'holding_count': len(holding),
        'holding': holding,
        'closed': closed,   # 【V160 新增】原始已結算清單，供績效摘要表的細節展開用
    }


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


def safe_json_write(filepath, data):
    dir_name = os.path.dirname(os.path.abspath(filepath)) or "."
    with tempfile.NamedTemporaryFile('w', dir=dir_name, delete=False, suffix='.tmp', encoding='utf-8') as tmp:
        json.dump(data, tmp, ensure_ascii=False, indent=4)
        tmp_path = tmp.name
    os.replace(tmp_path, filepath)


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

        now_ts = datetime.now().timestamp()
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


def sb_save_user_state(payload):
    """把整包使用者狀態 upsert 進 Supabase user_state 表（單筆 JSONB）。"""
    def _do():
        data = {"state_key": USER_STATE_KEY, "state_value": payload}
        return SUPABASE_CONN.table("user_state").upsert(data, on_conflict="state_key").execute()
    ok, _ = _sb_safe(_do)
    return ok


def sb_load_user_state():
    """從 Supabase 讀回使用者狀態；未啟用或查無資料回 None。"""
    def _do():
        return SUPABASE_CONN.table("user_state").select("state_value").eq("state_key", USER_STATE_KEY).limit(1).execute()
    ok, res = _sb_safe(_do)
    if ok and res is not None and getattr(res, "data", None):
        try:
            return res.data[0]["state_value"]
        except Exception:
            return None
    return None


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


def _clean_symbol(raw):
    """
    【R95續23新增】股票代號清洗——跟system_scheduler.py的_clean_symbol是
    同一個問題的兩邊各一份修復（兩個檔案不共用模組，scheduler獨立在
    GitHub Actions跑）。這次總指揮官的log截圖顯示網頁版本身也在對
    "$5304"這種帶$前綴的代號打yfinance，每個代號連續失敗4次（.TW/.TWO
    各重試一次）——這代表雲端存的portfolio/pinned_stocks資料本身就帶著
    $前綴髒污，網頁版直接原樣載入使用，從未清洗過。這是續6只修了排程端
    症狀、沒發現網頁版有同一個病根的地方，見下面_clean_symbol_keyed_dict
    在hydrate_state_from_cloud()套用的地方。
    """
    s = str(raw).strip()
    if s.startswith('$'):
        s = s[1:].strip()
    return s


def _clean_symbol_keyed_dict(d):
    """
    把一個「以股票代號為key」的dict，key清洗過(去除$前綴等)後回傳新dict。
    清洗後兩個key撞在一起是可接受的（後面覆蓋前面），理論上不該同時存在
    "$2330"和"2330"兩個代表同一檔股票的key。
    """
    if not isinstance(d, dict):
        return d
    return {_clean_symbol(k): v for k, v in d.items()}


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
                # 登入成功當下，從雲端灌一次狀態（跨裝置一致）
                hydrated = hydrate_state_from_cloud()
                st.session_state['cloud_hydrated'] = hydrated
                st.rerun()
            else:
                st.error("密碼錯誤，請重新輸入。")
    st.stop()


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
    NVIDIA_API_KEY = st.secrets.radar_secrets.get("nvidia_api_key", "").strip()
    if not NVIDIA_API_KEY:
        API_READY = False

    SECRET_FINMIND = st.secrets.radar_secrets.get("finmind_token", "")
    FINMIND_TOKENS = [k.strip() for k in SECRET_FINMIND.split(",") if k.strip()]
    if not FINMIND_TOKENS or FINMIND_TOKENS[0] == "":
        FINMIND_TOKENS, FINMIND_READY = [""], False
except Exception:
    API_READY, FINMIND_READY, COMMANDER_PIN, NVIDIA_API_KEY, FINMIND_TOKENS = False, False, "54088", "", [""]


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
        start_date = (datetime.now() - timedelta(days=days_back)).strftime('%Y-%m-%d')
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
def safe_float(val):
    """
    【重大修復】V155 的 safe_float 會用 .replace('-', '') 把負號整個刪掉，
    導致證交所 CSV 的「賣超」被寫成「買超」，籌碼方向全面反向。
    這裡改為正確解析正負號與會計括號負值。
    """
    if val is None:
        return 0.0
    try:
        if pd.isna(val):
            return 0.0
    except Exception:
        pass
    s = str(val).strip().upper()
    if s in ('', '-', '--', 'NA', 'N/A', 'NONE', 'NAN'):
        return 0.0
    s = s.replace(',', '').replace(' ', '')
    if s.startswith('(') and s.endswith(')'):   # 會計負值 (1,234)
        s = '-' + s[1:-1]
    m = re.search(r'-?\d+(?:\.\d+)?', s)
    try:
        return float(m.group()) if m else 0.0
    except Exception:
        return 0.0


def calc_real_profit(cost, price, qty=1):
    if cost <= 0 or price <= 0:
        return 0, 0
    buy_val = cost * qty * 1000
    sell_val = price * qty * 1000
    profit = (sell_val - buy_val
              - max(20, int(buy_val * 0.001425))
              - max(20, int(sell_val * 0.001425))
              - int(sell_val * 0.003))
    return profit, (profit / buy_val) * 100 if buy_val > 0 else 0


def calc_real_profit_v2(entry_price, current_price, qty=1, side='long'):
    """
    【V160 新增：觀察區轉持倉做空支援】方向感知的損益計算，取代原本
    只支援做多的 calc_real_profit（該函式保留不變，向下相容——凡是沒傳
    side的舊呼叫端行為完全不受影響）。

    台灣證交稅（賣出時課徵0.3%）的課稅時機依方向而不同：
      做多：買進(entry)不課稅，賣出(exit)才課稅——稅算在 exit_val 上。
      做空：放空賣出(entry，你是先賣)才課稅，回補買進(exit)不課稅——
            稅算在 entry_val 上。這不是隨便選的，是台灣證券交易稅的
            實際課稅規則（賣出動作本身觸發課稅，不分是「多單出場賣」
            還是「空單進場放空賣」，都是賣出動作）。

    手續費（買賣雙邊各0.1425%，最低20元）維持雙邊都收，這點多空一致。

    回傳 (損益金額, 報酬率%)，報酬率以進場成本(entry_val)為分母，
    多空兩邊定義一致，可以直接放在同一張表格裡比較。
    """
    if entry_price <= 0 or current_price <= 0:
        return 0, 0
    entry_val = entry_price * qty * 1000
    exit_val = current_price * qty * 1000
    fee_entry = max(20, int(entry_val * 0.001425))
    fee_exit = max(20, int(exit_val * 0.001425))
    if side == 'short':
        tax = int(entry_val * 0.003)
        profit = entry_val - exit_val - fee_entry - fee_exit - tax
    else:
        tax = int(exit_val * 0.003)
        profit = exit_val - entry_val - fee_entry - fee_exit - tax
    roi = (profit / entry_val * 100) if entry_val > 0 else 0
    return profit, roi


def build_short_trade_zones(current_price, ma5, atr, hist=None):
    """
    【V160 新增：觀察區轉持倉做空支援】做空持倉的防守線／移動停利計算，
    做多版本(build_trade_zones，在warroom_core.py)的鏡像對照。

    做空短線防守線 = MA5 + DEF_LINE_ATR_MULT×ATR（0.5倍，總指揮官確認沿用
    跟做多同一個倍數，不採用規格書原本建議的1.5倍——這個決定在R39就確認過
    一次，這裡延續同一個決定，不重新引入分歧）。現價「站上」這條線代表
    走勢轉強、做空該停損。

    做空移動停利 = 20日最低價 + 1.5×ATR——這裡刻意採用「跟現有做多版本
    完全相同的參數」(20日窗口、1.5倍ATR)，但方向鏡像：做多版本是
    「20日最高價 − 1.5×ATR」(停利線在現價之下、隨價格上漲往上移動、
    保護多單獲利)；做空要保護的是「價格下跌」的獲利，所以停利線必須
    在現價之上、隨價格下跌往下移動——對應公式是「20日最低價 + 1.5×ATR」，
    不是把做多公式原封不動照抄（那樣方向會反過來，變成停利線在現價下方，
    對做空毫無意義）。這個鏡像關係已經在回覆總指揮官時說明過。

    回傳跟 build_trade_zones 對稱的欄位名，方便UI共用同一套顯示邏輯。
    """
    def_line = round(ma5 + DEF_LINE_ATR_MULT * atr, 2)
    atk_zone = round(current_price - atr, 2)   # 做空的「進攻延伸區」對稱地往下

    trail_stop, low_20 = 0.0, 0.0
    if hist is not None and len(hist) >= 20:
        low_20 = float(hist['Low'].tail(20).min())
        trail_stop = round(low_20 + 1.5 * atr, 2)

    # 做空的移動停利只有在「現價仍低於停利線」時才是有效的持股保護
    trail_active = bool(trail_stop > 0 and current_price < trail_stop)

    return {'atk_zone': atk_zone, 'def_line': def_line, 'atr': round(atr, 2),
            'trail_stop': trail_stop, 'trail_active': trail_active, 'low_20': round(low_20, 2)}


def calc_volume_change(today_vol_lots, yesterday_vol_lots):
    vol_diff = today_vol_lots - yesterday_vol_lots
    vol_pct = ((vol_diff / yesterday_vol_lots) * 100) if yesterday_vol_lots else 0.0
    if vol_diff > 0:
        label, icon = f"量增 +{vol_diff:,.0f}張", "🔥"
    elif vol_diff < 0:
        label, icon = f"量縮 {vol_diff:,.0f}張", "🧊"
    else:
        label, icon = "量平", "➖"
    return f"{icon} {label} | {vol_pct:+.1f}%"


_SMART_CACHE_STORE = {}


def _get_smart_cache_store():
    """
    process-wide 持久字典，跨頁面重整/跨使用者session都共用同一份（不像
    session_state 每次重新整理就重置）。用來實作「已知的成功值固定保留，只有真的
    抓到新資料才覆蓋」的快取邏輯。

    【R95續15重大修復】這個函式原本寫成「return {}」——每次呼叫都建立一個全新的
    空字典就直接回傳，等於從來沒有真正保存過任何東西！上面那段docstring講的
    「process-wide持久」根本沒有實現，`_smart_cached_call`每次都拿到空字典、
    entry永遠是None、永遠判定成「快取沒命中」，直接穿透去打FinMind——這個
    「智慧快取」機制從被寫出來的那天起就從未真正快取過一筆資料，這輪追查
    「戰情速覽10檔要4-5分鐘」才把這個地基問題挖出來，影響的不只是戰情速覽，
    是所有呼叫fetch_finmind_revenue/fetch_finmind_dividend_fallback等函式的
    地方，全部都在承受這個bug。
    改成真的回傳同一個模組層級全域字典，才會有實際的持久效果。
    """
    return _SMART_CACHE_STORE


def _is_ok_value(v):
    """判斷一筆結果是不是成功：優先看'ok'欄位，沒有的話看'error'欄位，都沒有就當成功。"""
    if isinstance(v, dict):
        if 'ok' in v:
            return bool(v.get('ok'))
        if 'error' in v:
            return v.get('error') is None
    # 【R95續21】DataFrame真假值是ambiguous的，bool(v)會直接拋ValueError。
    # DataFrame/Series改用「非None且非空」判斷，其餘型別維持bool(v)。
    if isinstance(v, (pd.DataFrame, pd.Series)):
        return v is not None and not v.empty
    # 【R95續24】(hist, info)二元組回傳值接上快取時，bool(v)只看元組
    # 有沒有元素、跟內容無關，會讓失敗結果被誤判成功。改成看第一個
    # 元素(慣例是主要資料)是不是有效。
    if isinstance(v, tuple) and len(v) >= 1:
        first = v[0]
        if isinstance(first, (pd.DataFrame, pd.Series)):
            return first is not None and not first.empty
        return first is not None
    return bool(v)


def sb_get_data_cache(cache_key):
    """
    【R95續15新增】跟_get_smart_cache_store()的process-wide記憶體快取是互補
    關係，不是取代：記憶體快取撐的是「同一個容器運作期間」，但Streamlit Cloud
    的容器會因為重新部署、休眠喚醒等原因重啟，記憶體就整個歸零。這裡把同一批
    「查了不會馬上變」的資料（月營收、股利）額外多存一份進Supabase，撐過容器
    重啟這一關——新容器起來的第一次查詢，能先問Supabase有沒有還夠新鮮的資料，
    不用每次重新部署後都要重新熱身一次。

    回傳 (value, updated_ts)：value是存的內容(dict)，updated_ts是Unix時間戳，
    查無資料或Supabase未啟用回傳 (None, None)。
    """
    if not SUPABASE_ENABLED:
        return None, None

    def _do():
        return (SUPABASE_CONN.table("app_data_cache").select("value,updated_at")
                .eq("cache_key", cache_key).limit(1).execute())
    ok, res = _sb_safe(_do)
    if ok and res and getattr(res, 'data', None):
        row = res.data[0]
        try:
            ts = datetime.fromisoformat(row['updated_at'].replace('Z', '+00:00')).timestamp()
        except Exception:
            ts = None
        return row.get('value'), ts
    return None, None


def sb_set_data_cache(cache_key, value):
    """寫入/更新Supabase共享快取，盡力而為(失敗不影響主流程，_sb_safe已經處理)。"""
    if not SUPABASE_ENABLED:
        return

    def _do():
        return (SUPABASE_CONN.table("app_data_cache")
                .upsert({"cache_key": cache_key, "value": value,
                        "updated_at": datetime.now(timezone.utc).isoformat()},
                       on_conflict="cache_key").execute())
    _sb_safe(_do)


def _smart_cached_call(cache_key, fetch_fn, recheck_interval=1800, fail_retry=120, use_shared_cache=False):
    """
    【V160】千張大戶／月營收這類資料，本質上是「有新的才會變，沒新的就固定不動」
    （營收一個月才更新一次、大戶一週才更新一次），所以快取邏輯改成：
    - 已經抓到成功值 → 這個值會被「固定保留」，之後每隔 recheck_interval（預設30分鐘）
      才去檢查一次「有沒有新資料出來」；檢查成功且真的有新值，才覆蓋舊值。
    - 如果那次檢查剛好失敗（暫時性問題）→ 繼續沿用上一次成功的舊值顯示，
      不會突然從「有數字」變回「官方未公佈」，畫面不會忽有忽無。
    - 只有「從來沒有成功過」的情況，才會顯示查詢失敗，而且會用較短的 fail_retry
      （預設2分鐘）鼓勵盡快重試，直到第一次成功為止。

    【R95續15新增use_shared_cache】記憶體沒有(容器剛重啟)時，多問一次Supabase
    共享快取——這一層只在記憶體真的沒有東西時才會問(不會每次都多打一次
    Supabase，記憶體命中的話跟原本一樣快)。只有明確傳true的呼叫端(目前是
    月營收/股利)才會用這一層，避免所有呼叫者都被迫多一次Supabase往返。
    """
    store = _get_smart_cache_store()
    now_ts = time.time()
    entry = store.get(cache_key)

    # 還沒到重新檢查的時間點 → 不管上次是成功還失敗，直接沿用，不打API
    if entry and (now_ts - entry['checked_ts']) < entry.get('recheck', recheck_interval):
        return entry['value']

    if use_shared_cache and not entry:
        _shared_value, _shared_ts = sb_get_data_cache(cache_key)
        if _shared_value is not None and _shared_ts and (now_ts - _shared_ts) < recheck_interval:
            store[cache_key] = {'value': _shared_value, 'checked_ts': _shared_ts, 'recheck': recheck_interval}
            return _shared_value

    new_value = fetch_fn()
    if _is_ok_value(new_value):
        # 查詢成功：覆蓋成新值（可能是全新資料，也可能剛好跟舊值一樣，都沒關係）
        store[cache_key] = {'value': new_value, 'checked_ts': now_ts, 'recheck': recheck_interval}
        if use_shared_cache:
            sb_set_data_cache(cache_key, new_value)
        return new_value

    # 這次查詢失敗：如果之前有成功過的舊值，繼續沿用舊值顯示，只是縮短下次重試間隔
    if entry and _is_ok_value(entry['value']):
        store[cache_key] = {'value': entry['value'], 'checked_ts': now_ts, 'recheck': fail_retry}
        return entry['value']

    # 從來沒有成功過 → 顯示這次的失敗結果，但很快就會重試
    store[cache_key] = {'value': new_value, 'checked_ts': now_ts, 'recheck': fail_retry}
    return new_value


def _reason_to_label(reason):
    if reason == 'rate_limited':
        return ERR_RATE_LIMIT
    if reason == 'permission_denied':
        return ERR_PERMISSION
    if reason in ('timeout', 'connection_error', 'http_error'):
        return ERR_CONN
    return ERR_NO_DATA


def _fetch_finmind_revenue_impl(symbol, token, max_lookback=1200):
    """
    【V160 關鍵修復】月營收年增/月增改為「自己算」。

    根因：舊版讀 row['revenue_YearOnYearRatio'] 和 row['revenue_MonthOverMonthRatio']，
    但依 FinMind 官方 schema，TaiwanStockMonthRevenue 只有
    date / stock_id / country / revenue / revenue_month / revenue_year / create_time
    —— 那兩個比率欄位根本不存在。每一列都取到 None，被 pd.isna() 全部略過，
    所以這個功能 100% 永遠回「查無資料」，跟快取、跟帳號額度都無關。

    正確做法：抓原始 revenue，自己算
      月增 MoM = (本月 - 上月) / 上月 × 100
      年增 YoY = (本月 - 去年同月) / 去年同月 × 100
    因為 YoY 需要去年同月，起始回看天數必須 >= 400 天（舊版 120 天連一年都不到）。
    """
    url = 'https://api.finmindtrade.com/api/v4/data'
    lookback = 500                      # 至少涵蓋去年同月（YoY 需要）
    df = None
    last_err = "empty_data"
    while df is None and lookback <= max_lookback:
        start_date = (datetime.now() - timedelta(days=lookback)).strftime('%Y-%m-%d')
        params = {'dataset': 'TaiwanStockMonthRevenue', 'data_id': symbol, 'start_date': start_date}
        try:
            payload = _finmind_get(url, params)
            tmp = pd.DataFrame(payload.get('data', []))
            if not tmp.empty:
                df = tmp
            else:
                lookback *= 2
        except FinMindAPIError as e:
            last_err = e.reason
            if last_err in ('rate_limited', 'permission_denied'):
                break                   # 換帳號已在底層試過，這裡不再重打
            lookback *= 2

    if df is not None and not df.empty and 'revenue' in df.columns:
        d = df.copy()
        d['revenue'] = pd.to_numeric(d['revenue'], errors='coerce')
        d['revenue_year'] = pd.to_numeric(d.get('revenue_year'), errors='coerce')
        d['revenue_month'] = pd.to_numeric(d.get('revenue_month'), errors='coerce')
        d = d.dropna(subset=['revenue', 'revenue_year', 'revenue_month'])
        if not d.empty:
            # 用「營收所屬年月」排序，不是用公布日期（公布日可能同月多筆）
            d = d.sort_values(['revenue_year', 'revenue_month'])
            d = d.drop_duplicates(subset=['revenue_year', 'revenue_month'], keep='last')
            # 建索引方便查上月／去年同月
            by_ym = {(int(r['revenue_year']), int(r['revenue_month'])): float(r['revenue'])
                     for _, r in d.iterrows()}
            latest = d.iloc[-1]
            y, m = int(latest['revenue_year']), int(latest['revenue_month'])
            cur = float(latest['revenue'])

            prev_y, prev_m = (y - 1, 12) if m == 1 else (y, m - 1)
            prev_rev = by_ym.get((prev_y, prev_m))
            last_year_rev = by_ym.get((y - 1, m))

            mom = round((cur - prev_rev) / prev_rev * 100, 2) if prev_rev else None
            yoy = round((cur - last_year_rev) / last_year_rev * 100, 2) if last_year_rev else None

            if yoy is not None or mom is not None:
                result = {'yoy': yoy, 'mom': mom, 'month': f"{m:02d}月",
                          'stale': False, 'ok': True}
                with _LAST_GOOD_LOCK:
                    _LAST_GOOD_REVENUE[symbol] = result
                return result
            last_err = "empty_data"      # 有資料但湊不出可比較的基期

    with _LAST_GOOD_LOCK:
        last_good = _LAST_GOOD_REVENUE.get(symbol)
    if last_good:
        stale = dict(last_good)
        stale['stale'] = True
        return stale

    # 【任務一】不再用 0.0 混過去，明確標示失敗原因
    return {'yoy': None, 'mom': None, 'month': _reason_to_label(last_err), 'stale': False, 'ok': False}


def fetch_financial_health(symbol, token, progress_cb=None):
    """
    【V160 新增】深度財報分析：毛利率、ROE、營業現金流品質。

    背景：總指揮官問財報狗免費版能查的 ROE/毛利/現金流我們能不能做。
    查證後確認可行：FinMind 的綜合損益表/資產負債表/現金流量表都是免費資料集
    （跟我們已經在用的月營收表同等級，data_id 模式免費，只有「一次拿全市場」
    才需要付費會員，我們一直都是一檔一檔查，不受影響）。

    刻意只做三個指標，不做財報狗那種50+指標的全套：
      1. 毛利率 = 毛利/營收：反映定價能力與競爭優勢，是最基本也最重要的一個
      2. ROE（用最近一季稅後淨利年化 / 母公司權益）：反映股東資金的使用效率
      3. 現金流品質 = 營業現金流 / 稅後淨利：這是財報狗的招牌指標之一，
         比率遠低於1代表「帳上有賺錢但收不到現金」，是財報作假或營運品質
         惡化的早期警訊，比單看EPS更難被美化

    這三個是「30秒判斷要不要繼續看」等級的重點指標，不是要取代財報狗的深度研究，
    定位仍是快篩——真的要做投資決策，還是建議去財報狗查完整的多年度趨勢。

    回傳 dict 或 None（資料不足時誠實回報，不編造）。

    【R95新增progress_cb】這裡依序打3個FinMind資料集，每個都要走一次完整的
    「多憑證×多重試」流程，總指揮官反映「查詢深度財報點了沒反應、超過5分鐘」——
    查證後這其實是3個查詢疊加的等待時間，UI端只有一顆spinner、看不出進度，
    容易被誤會成「沒反應」。這裡在每個資料集查完時回報一次進度；同時把
    max_retries從預設3降到2——單一使用者互動式查詢不需要跟背景批次排程一樣
    在同一組憑證上重試3次，換一組憑證（外層_finmind_get已經會做）通常比在
    同一組上多等一次重試更快找到能用的憑證。
    """
    def _report(pct, label):
        if progress_cb:
            try:
                progress_cb(pct, label)
            except Exception as _e:
                print(f"[fetch_financial_health-診斷] 單一財報項目解析失敗(跳過這項繼續)：{type(_e).__name__}: {_e}")
                pass

    def _fetch(dataset, stock_id):
        url = 'https://api.finmindtrade.com/api/v4/data'
        params = {'dataset': dataset, 'data_id': stock_id,
                  'start_date': (datetime.now() - timedelta(days=450)).strftime('%Y-%m-%d')}
        if token:
            params['token'] = token
        try:
            payload = _finmind_get(url, params, max_retries=2, timeout=8)
            return pd.DataFrame(payload.get('data', []))
        except FinMindAPIError as _e:
            print(f"[fetch_financial_health-診斷] FinMind抓財報失敗：{type(_e).__name__}: {_e}")
            return pd.DataFrame()
        except Exception as _e:
            print(f"[fetch_financial_health-診斷] 非預期例外：{type(_e).__name__}: {_e}")
            return pd.DataFrame()

    def _latest(df, type_name):
        """從長格式表(date/stock_id/type/value)取出某個type的最新一筆數值。"""
        if df.empty or 'type' not in df.columns:
            return None
        sub = df[df['type'] == type_name]
        if sub.empty:
            return None
        sub = sub.sort_values('date')
        return safe_float(sub.iloc[-1]['value']), str(sub.iloc[-1]['date'])

    _report(0.05, "查詢綜合損益表中")
    fs = _fetch('TaiwanStockFinancialStatements', symbol)
    _report(0.40, "查詢資產負債表中")
    bs = _fetch('TaiwanStockBalanceSheet', symbol)
    _report(0.70, "查詢現金流量表中")
    cf = _fetch('TaiwanStockCashFlowsStatement', symbol)
    _report(0.95, "整理財報指標中")

    if fs.empty and bs.empty and cf.empty:
        return None

    # 【注意】FinMind綜合損益表沒有直接叫"Revenue"的欄位，改用最穩健作法：
    # 損益表沒有明確營收欄位時，改用月營收表當季加總值當分母，找不到就誠實
    # 回報缺料。
    gp = _latest(fs, 'GrossProfit')
    rev_candidates = ['Revenue', 'OperatingRevenue', 'NetRevenue']
    rev = None
    for rc in rev_candidates:
        rev = _latest(fs, rc)
        if rev:
            break
    net_income = _latest(fs, 'IncomeAfterTaxes')
    equity = _latest(bs, 'EquityAttributableToOwnersOfParent')
    op_cash = _latest(cf, 'CashFlowsFromOperatingActivities')

    result = {'quarter_date': None, 'gross_margin': None, 'roe': None,
              'cash_quality': None, 'cash_quality_note': None, 'ok': False}

    if gp and rev and rev[0] and rev[0] != 0:
        result['gross_margin'] = round(gp[0] / rev[0] * 100, 1)
        result['quarter_date'] = gp[1]
        result['ok'] = True

    if net_income and equity and equity[0] and equity[0] != 0:
        # 單季淨利年化（×4）/ 權益，是近似值不是精確年度ROE，但用來快篩方向足夠
        result['roe'] = round(net_income[0] * 4 / equity[0] * 100, 1)
        result['quarter_date'] = result['quarter_date'] or net_income[1]
        result['ok'] = True

    if op_cash and net_income and net_income[0]:
        ratio = op_cash[0] / net_income[0]
        result['cash_quality'] = round(ratio, 2)
        if net_income[0] > 0 and ratio < 0.5:
            result['cash_quality_note'] = "⚠️ 營業現金流遠低於淨利，獲利品質可能不佳"
        elif net_income[0] > 0 and op_cash[0] < 0:
            result['cash_quality_note'] = "🔴 帳上有賺錢但營業現金流是負的，需留意"
        elif ratio >= 1:
            result['cash_quality_note'] = "✅ 營業現金流優於淨利，獲利品質良好"
        result['ok'] = True

    _report(1.0, "完成")
    return result if result['ok'] else None


def fetch_financial_health_cached(symbol, token, progress_cb=None):
    """
    【V160】按需查詢的包裝層。財報一季才更新一次，不需要跟著全市場掃描一起打，
    那樣400檔掃描會多消耗1200次API額度（3張表×400檔），對免費額度是災難性的浪費。
    改成只有使用者在戰卡展開查詢時才呼叫，並用長效快取（6小時才重查一次）記住結果，
    同一次使用中重複展開同一檔不會重複打API。

    【R95新增】progress_cb只在真的需要重新查詢（快取沒命中）時才會被觸發到，
    快取命中時本來就是毫秒級，不需要進度條。
    """
    cache_key = f"fin_health:{symbol}"
    return _smart_cached_call(cache_key, lambda: fetch_financial_health(symbol, token, progress_cb=progress_cb),
                              recheck_interval=21600, fail_retry=300)


def fetch_finmind_revenue(symbol, token, max_lookback=1200):
    """
    【V160】改用智慧快取（成功20小時／失敗2分鐘），取代原本固定TTL的 st.cache_data。
    月營收本來就是月頻資料，收盤後到隔天開盤前完全不會變，長時間快取成功結果很安全；
    失敗時短快取則讓查詢能快速自我修復，不會卡住一整天。

    【V160 關鍵修復】這裡原本預設 max_lookback=400，但內層 _fetch_finmind_revenue_impl
    的起始回看天數是 500（算年增需要去年同月）。while 迴圈條件是
    `lookback <= max_lookback`，500 <= 400 一開始就是假，
    導致迴圈一次都沒跑、連一次 API 都沒打，就直接回報「查無資料」。
    這個 bug 讓月營收從功能上線後就 100% 必然失敗，跟快取、跟帳號額度、
    跟股票代號完全無關——不管抓哪一檔都一樣會踩到。
    現在改成 1200，跟內層函式自己的預設值一致，且 1200 > 500 起跳值，迴圈才會真的執行。

    【R95續15】cache_key不再包含token——營收資料本身跟「用哪一組憑證查到的」
    無關，只跟symbol有關；原本帶token進去，多帳號輪替時同一檔股票會因為
    這次剛好輪到哪組token而對應到不同的cache_key，讓快取意外失效、重複打API。
    """
    cache_key = f"revenue:{symbol}"
    # 【R96修復】docstring寫「成功20小時」但原本沒把recheck_interval傳
    # 進去，實際用了函式預設值30分鐘，導致月營收每30分鐘就回頭真的打一次
    # FinMind。補上72000秒(20小時)，符合docstring原本的設計意圖。
    return _smart_cached_call(cache_key, lambda: _fetch_finmind_revenue_impl(symbol, token, max_lookback),
                              recheck_interval=72000, use_shared_cache=True)


def _fetch_big_holder_with_recursion_impl(code, token, target_date, initial_lookback=20, max_lookback=180):
    url = 'https://api.finmindtrade.com/api/v4/data'
    target_dt = datetime.strptime(target_date, "%Y-%m-%d")
    lookback = initial_lookback
    last_err = "empty_data"
    while lookback <= max_lookback:
        start_date = (target_dt - timedelta(days=lookback)).strftime('%Y-%m-%d')
        params = {'dataset': 'TaiwanStockHoldingSharesPer', 'data_id': code,
                  'start_date': start_date, 'end_date': target_date}
        if token:
            params['token'] = token
        try:
            payload = _finmind_get(url, params)
            raw = payload.get('data', [])
            if raw:
                df = pd.DataFrame(raw)
                # 【V160關鍵修復】HoldingSharesLevel是FinMind官方schema的
                # 字串級距('1-999'等)，舊寫法pd.to_numeric()必然變NaN、dropna
                # 把整張表刪光，這才是「千張大戶永久顯示未公佈」的真正根因。
                # 改成解析每個級距下界，挑>=1,000,000股(1000張)的級距加總。
                df['_lower'] = df['HoldingSharesLevel'].apply(_parse_holding_level_lower)
                df = df.dropna(subset=['_lower'])
                if not df.empty:
                    latest_date_all = df['date'].max()
                    day_df = df[df['date'] == latest_date_all]
                    if not day_df.empty:
                        # 千張＝1000張＝1,000,000股；取下界達標的所有級距
                        big = day_df[day_df['_lower'] >= 1_000_000]
                        if big.empty:
                            # 保險：若 schema 改版導致沒有任何級距達標，
                            # 退而取當日最高級距（維持舊有意圖，不會整個失效）
                            big = day_df[day_df['_lower'] == day_df['_lower'].max()]
                        df = big
                if not df.empty:
                    latest_date = df['date'].max()
                    pct = round(df[df['date'] == latest_date]['percent'].sum(), 2)
                    return {'big_holder': pct,
                            'big_holder_date': latest_date,
                            'is_stale': latest_date != target_date,
                            'error': None}
            last_err = "empty_data"
        except FinMindAPIError as e:
            last_err = e.reason
            if last_err == 'rate_limited':
                break
        lookback *= 2

    label = _reason_to_label(last_err)
    return {'big_holder': label, 'big_holder_date': label, 'is_stale': False, 'error': label}


def fetch_big_holder_with_recursion(code, token, target_date, initial_lookback=20, max_lookback=180):
    """
    【V160】改用智慧快取（成功20小時／失敗2分鐘），取代原本固定TTL的 st.cache_data。
    千張大戶是週頻資料，收盤後到隔天開盤前不會變，長時間快取成功結果很安全；
    失敗時短快取則讓查詢能快速自我修復。
    """
    cache_key = f"big_holder:{code}:{token}:{target_date}"
    return _smart_cached_call(cache_key, lambda: _fetch_big_holder_with_recursion_impl(
        code, token, target_date, initial_lookback, max_lookback))


@st.cache_data(ttl=86400, show_spinner=False)
def _fetch_finmind_dividend_impl(symbol, token, max_lookback=1200):
    """
    【V160 新增】TWSE 除權除息「預告」表（TWT48U_ALL）是前瞻性的，只列近期即將發生的事件——
    事件一旦過了，通常就會從表裡被移除，不會保留歷史。所以「已經除完權息、但已經是
    幾天前甚至更早」的股票（總指揮官回報的南亞科、環球晶就是這種情況）在預告表裡
    會直接查無此股，顯示「無近期資訊」，但這不是抓取失敗，是這個資料源本質上的限制。

    備援：FinMind 的股利政策表 TaiwanStockDividend 是「已公告股利」的永久紀錄，不會
    隨事件過去而消失，用來補這個缺口。取最近一筆公告，加總現金股利兩個子項
    （盈餘轉增資 + 法定盈餘公積），用除息交易日判斷過去/未來。
    """
    url = 'https://api.finmindtrade.com/api/v4/data'
    lookback = 500
    df = None
    last_err = "empty_data"
    while df is None and lookback <= max_lookback:
        start_date = (datetime.now() - timedelta(days=lookback)).strftime('%Y-%m-%d')
        params = {'dataset': 'TaiwanStockDividend', 'data_id': symbol, 'start_date': start_date}
        try:
            payload = _finmind_get(url, params)
            tmp = pd.DataFrame(payload.get('data', []))
            if not tmp.empty:
                df = tmp
            else:
                lookback *= 2
        except FinMindAPIError as e:
            last_err = e.reason
            if last_err in ('rate_limited', 'permission_denied'):
                break
            lookback *= 2

    if df is not None and not df.empty:
        d = df.copy()
        # 用公告日期排序取最新一筆已公告的股利政策
        sort_col = 'AnnouncementDate' if 'AnnouncementDate' in d.columns else 'date'
        d = d.sort_values(sort_col)
        latest = d.iloc[-1]
        cash = (safe_float(latest.get('CashEarningsDistribution', 0))
                + safe_float(latest.get('CashStatutorySurplus', 0)))
        stock = (safe_float(latest.get('StockEarningsDistribution', 0))
                + safe_float(latest.get('StockStatutorySurplus', 0)))
        ex_date = str(latest.get('CashExDividendTradingDate') or
                     latest.get('StockExDividendTradingDate') or '').strip()
        if cash > 0 or stock > 0:
            return {'cash': cash, 'stock': stock, 'ex_date': ex_date, 'ok': True}
        last_err = "empty_data"

    return {'cash': 0.0, 'stock': 0.0, 'ex_date': '', 'ok': False,
            'reason': _reason_to_label(last_err)}


def fetch_finmind_dividend_fallback(symbol, token, max_lookback=1200):
    # 【R95續15】cache_key拿掉token，理由同fetch_finmind_revenue。
    # 【R96修復】recheck_interval同樣缺漏，股利公告是低頻資料，補上20小時。
    cache_key = f"dividend_fallback:{symbol}"
    return _smart_cached_call(cache_key, lambda: _fetch_finmind_dividend_impl(symbol, token, max_lookback),
                              recheck_interval=72000, use_shared_cache=True)


def _roc_date_to_display(date_str):
    """
    【V160 新增】把日期字串轉成好讀的西元日期。同時處理兩種來源格式：
      - TWSE 預告表：民國年 YYYMMDD（例：'1150729' = 2026-07-29）
      - FinMind 股利政策表：西元 ISO 格式（例：'2026-07-29'，本身已經可讀，原樣回傳）
    格式不對就照原樣回傳，不猜。
    """
    s = str(date_str).strip()
    if len(s) == 10 and s[4] == '-' and s[7] == '-':   # 已經是西元 ISO 格式
        return s
    if len(s) == 7 and s.isdigit():
        roc_y, m, d = int(s[:3]), int(s[3:5]), int(s[5:7])
        return f"{roc_y + 1911}-{m:02d}-{d:02d}"
    if len(s) == 8 and s.isdigit():   # 保險：萬一哪天格式改回西元年
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return s


def _classify_dividend_date(date_str):
    """
    【V160 新增】判斷這個除權息日期是「已經過去」還是「還沒到」，回傳 'past'／'future'／'unknown'。
    同時處理民國格式（TWSE）與西元ISO格式（FinMind 備援來源）。
    總指揮官回報：原本只顯示一串數字日期，要自己心算比對今天日期才知道是不是已經除完了，
    容易誤判成「還沒資料」。這裡直接把結論算出來。
    """
    s = str(date_str).strip()
    try:
        if len(s) == 10 and s[4] == '-' and s[7] == '-':
            div_date = datetime.strptime(s, '%Y-%m-%d').date()
        elif len(s) == 7 and s.isdigit():
            roc_y, m, d = int(s[:3]), int(s[3:5]), int(s[5:7])
            div_date = datetime(roc_y + 1911, m, d).date()
        elif len(s) == 8 and s.isdigit():
            div_date = datetime(int(s[:4]), int(s[4:6]), int(s[6:8])).date()
        else:
            return 'unknown'
        return 'past' if div_date < datetime.now().date() else 'future'
    except (ValueError, TypeError):
        return 'unknown'


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


@st.cache_data(ttl=86400, show_spinner=False)
def fetch_stock_names():
    """
    【V160 修復】名稱對照表改以 FinMind TaiwanStockInfo 為主源。

    先前只用 TWSE BWIBBU_ALL（本益比/殖利率/淨值比）＋ TPEx 本益比分析當來源，
    但那兩個端點只涵蓋「有本益比資料」的個股 —— 虧損股、無配息股會被排除。
    這造成兩個問題：
      (1) 名稱查不到就退回顯示代號（總指揮官看到 2409 名稱欄顯示 2409）
      (2) 更嚴重：GLOBAL_MARKET_CODES 是直接取這份表的 keys，
          等於「全市場掃描池」從一開始就漏掉這些個股，根本掃不到。
    改用 TaiwanStockInfo（涵蓋上市/上櫃/興櫃全市場）當主源，
    原本兩個端點降為補充，抓不到名稱時仍退回顯示代號，不編造。
    """
    names = {}
    # 主源：FinMind TaiwanStockInfo（全市場）
    try:
        payload = _finmind_get('https://api.finmindtrade.com/api/v4/data',
                               {'dataset': 'TaiwanStockInfo'}, max_retries=2, timeout=15)
        for item in payload.get('data', []) or []:
            c = str(item.get('stock_id', '')).strip()
            n = str(item.get('stock_name', '')).strip()
            if len(c) == 4 and c.isdigit() and n:
                names[c] = n
    except Exception as e:
        print(f"[fetch_stock_names-診斷] 主源(FinMind TaiwanStockInfo)失敗，"
              f"退回TWSE/TPEx備援：{type(e).__name__}: {e}")

    # 備援：TWSE / TPEx 公開端點
    for url in ["https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL",
                "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_peratio_analysis"]:
        try:
            res = _SESSION.get(url, timeout=5)
            if res.status_code == 200:
                for item in res.json():
                    c = str(item.get('Code', item.get('SecuritiesCompanyCode', ''))).strip()
                    n = str(item.get('Name', item.get('CompanyName', ''))).strip()
                    if len(c) == 4 and c.isdigit() and n:
                        names.setdefault(c, n)
        except Exception as e:
            print(f"[fetch_stock_names-診斷] 備援端點{url}失敗：{type(e).__name__}: {e}")
    for k, v in {"2330": "台積電", "2303": "聯電", "2317": "鴻海", "2308": "台達電",
                 "5871": "中租-KY", "3481": "群創", "2454": "聯發科",
                 "2409": "友達"}.items():
        names.setdefault(k, v)
    if len(names) < 100:
        # 【R96新增，診斷用】全市場正常應該有數千檔，如果最後總數異常少，
        # 代表三層來源可能全部大幅失敗，只剩硬寫的8檔在撐——這種情況
        # 光看單一層的log可能會漏看，這裡加一個總結性的警示。
        print(f"[fetch_stock_names-診斷] ⚠️ 最終只收集到{len(names)}檔名稱"
              f"（正常應有數千檔），三層來源可能都出了問題，請檢查上面各層的診斷log。")
    return names


@st.cache_data(ttl=21600, show_spinner=False)
def fetch_shares_outstanding(symbol, token=None):
    """
    【V160 新增：單檔分點CSV拖曳區】取得發行股數，供週轉率計算用
    （週轉率 = 當日成交股數 ÷ 發行股數）。

    查證過程記錄：一開始以為股本資料要付費（`TaiwanStockMarketValue` 市值表
    確實是Backer/Sponsor限定），但查證FinMind完整資料集列表後發現
    `TaiwanStockShareholding`（外資持股表）本來就有 `NumberOfSharesIssued`
    （發行股數）欄位——這個資料集是「單檔查詢免費」（跟我們已經在用的月營收表
    同等級，只有「一次拿全市場」才需要付費），不是專門為週轉率新開的付費功能。

    回傳最新一筆的發行股數（int）或 None（抓不到時誠實回報，不編造）。
    """
    url = 'https://api.finmindtrade.com/api/v4/data'
    params = {'dataset': 'TaiwanStockShareholding', 'data_id': symbol,
              'start_date': (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')}
    if token:
        params['token'] = token
    try:
        payload = _finmind_get(url, params, max_retries=2, timeout=8)
        df = pd.DataFrame(payload.get('data', []))
        if df.empty or 'NumberOfSharesIssued' not in df.columns:
            return None
        df = df.sort_values('date')
        latest = pd.to_numeric(df['NumberOfSharesIssued'], errors='coerce').dropna()
        return int(latest.iloc[-1]) if len(latest) else None
    except FinMindAPIError as _e:
        print(f"[fetch_shares_outstanding-診斷] FinMind抓股本失敗：{type(_e).__name__}: {_e}")
        return None
    except Exception as _e:
        print(f"[fetch_shares_outstanding-診斷] 非預期例外：{type(_e).__name__}: {_e}")
        return None


def get_todays_broker_flow_progress(pool):
    """
    【R95續11新增】網頁版「補跑今日全市場分點」的斷點續傳核心——不用另外
    維護一個「上次跑到第幾檔」的游標，直接把Supabase裡`broker_flows`當天
    已經存在的symbol集合當成進度真相：已經有紀錄的代表做過了，沒有的就是
    還沒抓。這樣不管是第一次點、還是分頁斷線後重新點，邏輯完全一樣——
    永遠只抓「今天還缺的」，天生支援斷點續傳，不需要額外的狀態管理。

    回傳 (done_set, remaining_list)：done_set是今天已經有紀錄的代號集合，
    remaining_list是pool裡還沒抓的部分，維持pool原本的順序。
    """
    if not SUPABASE_ENABLED or not pool:
        return set(), list(pool)
    today = datetime.now().strftime('%Y-%m-%d')

    def _do():
        return (SUPABASE_CONN.table("broker_flows").select("symbol")
                .eq("log_date", today).execute())
    ok, res = _sb_safe(_do)
    done = {r['symbol'] for r in (res.data if (ok and res and getattr(res, 'data', None)) else [])}
    remaining = [c for c in pool if c not in done]
    return done, remaining


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
    today = datetime.now().strftime('%Y-%m-%d')
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


def sb_log_broker_flows(symbol, log_date, df, top_n=15):
    """
    【R67新增】把分點CSV解析出來的每日分點進出存進Supabase，讓分點資料
    從「看完就丟」變成「會累積的歷史」。

    這是券商分點功能真正的價值所在：單獨看一天，你只知道「今天誰買最多」；
    累積幾天之後才能回答真正重要的問題——「這家分點是連續好幾天在買（真的
    在建倉），還是買完隔天就倒貨（隔日沖）」。籌碼K線的招牌功能就是這個，
    而這個用免費的證交所CSV+自己累積就能做到，不需要FinMind的付費分點API
    （已查證FinMind的TaiwanStockTradingDailyReport是sponsor會員限定）。

    只存前top_n名買超+前top_n名賣超的分點——一份買賣日報表動輒上百家分點，
    全存會讓資料表迅速膨脹，而尾巴那些買賣各幾張的分點對判斷完全沒有意義。

    回傳成功寫入的筆數；Supabase未連線或寫入失敗回傳0（不拋例外，
    不影響畫面上已經算好的當日分析）。
    """
    if df is None or df.empty or not SUPABASE_ENABLED:
        return 0
    try:
        g = df.groupby('券商').agg(買進=('買進股數', 'sum'), 賣出=('賣出股數', 'sum'))
        g['買超股數'] = g['買進'] - g['賣出']
        g = g.sort_values('買超股數', ascending=False)
        _picked = pd.concat([g.head(top_n), g.tail(top_n)])
        _picked = _picked[~_picked.index.duplicated(keep='first')]
        rows = [{
            'symbol': str(symbol), 'log_date': str(log_date),
            'broker_name': str(idx),
            'buy_shares': int(r['買進']), 'sell_shares': int(r['賣出']),
            'net_shares': int(r['買超股數']),
        } for idx, r in _picked.iterrows()]
        if not rows:
            return 0

        def _do():
            return SUPABASE_CONN.table("broker_flows").upsert(
                rows, on_conflict="symbol,log_date,broker_name").execute()
        ok, _ = _sb_safe(_do)
        return len(rows) if ok else 0
    except Exception:
        return 0


def get_broker_data_maturity(symbol):
    """
    【R95續26新增】分點成熟度標示——總指揮官先前提出的疑慮：分點資料只能
    往後累積、沒有歷史回補，剛開始關注的股票資料天數不足，拿來判斷連續買超
    /隔日沖的趨勢容易失真。但畫面上原本完全沒有標示「這檔的分點資料到底
    累積了幾天」，使用者沒辦法自己判斷這次的分點判讀可不可信。

    這裡查這檔股票在broker_flows裡有幾個「不同的log_date」（不是幾家分點，
    是累積了幾個交易日），回傳(天數, 是否足夠成熟)。門檻抓10個交易日——
    這是一個合理但主觀的起始值，可以之後再調整，不是精算出來的門檻。
    """
    if not SUPABASE_ENABLED:
        return 0, False

    def _do():
        return SUPABASE_CONN.table("broker_flows").select("log_date").eq("symbol", symbol).execute()
    ok, res = _sb_safe(_do)
    if not ok or not res or not getattr(res, 'data', None):
        return 0, False
    _days = len({r['log_date'] for r in res.data if r.get('log_date')})
    return _days, _days >= 10


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
            '判讀': _verdict + ("　⚠️名單命中" if check_day_trader_alert(broker) else ""),
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




def sb_log_big_holder_weekly(ratios, week_date, small_ratios=None):
    """
    【R69新增，R90補上散戶比例】存進Supabase big_holder_weekly表，累積成
    歷史，才能算趨勢。一次CSV通常涵蓋全市場1000+檔，全存沒問題（一週一筆，
    資料量遠比分點CSV小很多）。回傳成功寫入筆數；Supabase未連線或失敗
    回傳0，不拋例外。

    【R90新增】small_ratios：選填，散戶（十張以下）比例的dict，格式跟
    ratios一樣。不傳就只存大戶比例（向下相容既有呼叫端，例如手動快速
    回補單一數字時通常只有大戶那一個數字）。
    """
    if not ratios or not SUPABASE_ENABLED:
        return 0
    try:
        small_ratios = small_ratios or {}
        rows = [{'symbol': s, 'week_date': str(week_date), 'ratio_pct': r,
                'small_holder_pct': small_ratios.get(s)}
                for s, r in ratios.items()]

        def _do():
            return SUPABASE_CONN.table("big_holder_weekly").upsert(
                rows, on_conflict="symbol,week_date").execute()
        ok, _ = _sb_safe(_do)
        return len(rows) if ok else 0
    except Exception:
        return 0


@st.cache_data(ttl=3600, show_spinner=False)
def get_latest_big_holder_ratio(symbol):
    """
    【R85新增】直接查big_holder_weekly最新一筆——這是解決「戰卡千張大戶顯示
    官方未公佈，但玩股網明明看得到」這個混淆的正解。

    查證後發現戰卡上「千張大戶」那行其實疊了兩個完全不同的資料來源：
    ①舊的FinMind欄位(TaiwanStockHoldingSharesPer)，這是最一開始查證過的
    付費限定資料集，永遠會是「官方未公佈」，跟後來做的TDCC自動化完全
    無關；②新的TDCC趨勢徽章，需要累積滿3週才顯示判讀。兩者疊在同一行，
    讓人以為整個功能沒用，其實是舊欄位天生卡死、新欄位還在累積。

    這個函式直接給「最新一週的實際比例數字」，不用等3週累積趨勢才有東西
    可看——只要排程跑過一次，馬上就有真實數字可以顯示，用這個取代永遠
    卡住的FinMind欄位。

    【R90新增】順便回傳散戶（十張以下）比例——同一筆weekly資料本來就有
    這個欄位（R90新增small_holder_pct），不用另外查一次。

    回傳 (ratio_pct, week_date, small_holder_pct)，任一筆缺資料時對應位置
    是None。
    """
    if not SUPABASE_ENABLED:
        # 【R95修復】原本這裡回傳2個值(None, None)，但呼叫端一律用
        # _bh_ratio_result[0]/[1]/[2]三個索引解讀（見render_stock_card_ui），
        # Supabase沒連線時會直接IndexError、把整張卡片的渲染中斷掉。
        return None, None, None

    def _do():
        return (SUPABASE_CONN.table("big_holder_weekly").select("*")
                .eq("symbol", str(symbol)).order("week_date", desc=True).limit(1).execute())
    ok, res = _sb_safe(_do)
    rows = res.data if (ok and res is not None and getattr(res, "data", None)) else []
    if not rows:
        return None, None, None
    _small = rows[0].get('small_holder_pct')
    return (float(rows[0]['ratio_pct']), rows[0]['week_date'],
            float(_small) if _small is not None else None)


def get_big_holder_trend(symbol, min_weeks=3):
    """
    【R69新增，R75升級為連續分數】千張大戶趨勢因子——這是舊交接文件待辦
    項目，之前卡在FinMind的千張大戶資料集是付費限定；現在改用TDCC官方CSV
    自動化，不再卡住。

    【R75】原本只有up/down/flat三態，總指揮官指出這樣看不出力道——「這週
    比例+0.05%」跟「這週+0.8%」都會被歸類成同一個「up」，但兩者的意義
    差很多。這裡加上slope_per_week：用簡單線性迴歸(x=第幾週、y=比例)算出
    「平均每週變化幾個百分點」，這是連續數字，不是三個籠統的類別，之後
    要接進評分引擎當因子輸入也比三態更有鑑別力。三態分類保留（給畫面快速
    判讀用），連續分數是額外附加，不是取代。

    回傳 (trend, weeks_count, slope_per_week)：
      trend：'up'(比例上升，籌碼往大戶集中)／'down'(比例下降，籌碼分散)／
             'flat'(變化不明顯)／None(資料不足，還沒累積到min_weeks筆)。
      weeks_count：目前累積了幾週的資料，供畫面顯示「累積中 X/N週」。
      slope_per_week：每週平均變化幾個百分點（正=集中中、負=分散中），
             資料不足時為None。

    判讀用首尾比較（不是複雜的迴歸）決定trend分類，差距要超過0.5個百分點
    才算有意義的變化——單週波動0.1~0.2%是正常雜訊，不該被講成「趨勢」。
    slope_per_week則是給想看力道細節的人用的連續數字，不受這個0.5門檻限制。
    """
    if not SUPABASE_ENABLED:
        return None, 0, None

    def _do():
        return (SUPABASE_CONN.table("big_holder_weekly").select("*")
                .eq("symbol", str(symbol)).order("week_date", desc=True).limit(20).execute())
    ok, res = _sb_safe(_do)
    rows = res.data if (ok and res is not None and getattr(res, "data", None)) else []
    if len(rows) < min_weeks:
        return None, len(rows), None
    rows = sorted(rows, key=lambda r: r['week_date'])
    ratios = [float(r['ratio_pct']) for r in rows]
    diff = ratios[-1] - ratios[0]
    if diff > 0.5:
        trend = 'up'
    elif diff < -0.5:
        trend = 'down'
    else:
        trend = 'flat'

    # 【R75新增】連續分數：簡單最小二乘法算斜率，不需要額外套件。
    n = len(ratios)
    xs = list(range(n))
    x_mean = sum(xs) / n
    y_mean = sum(ratios) / n
    _num = sum((xs[i] - x_mean) * (ratios[i] - y_mean) for i in range(n))
    _den = sum((xs[i] - x_mean) ** 2 for i in range(n))
    slope_per_week = round(_num / _den, 3) if _den > 0 else 0.0
    return trend, len(rows), slope_per_week


def parse_broker_csv(raw_bytes):
    """
    【V160 新增：單檔分點CSV拖曳區「隔日沖照妖鏡」】解析證交所買賣日報表查詢
    系統（bsr.twse.com.tw/bshtm/）下載的CSV——這個格式是總指揮官提供2303.csv
    範例檔驗證過的：Big5編碼、每行「兩筆記錄並排」(序號,券商,價格,買進股數,
    賣出股數,,序號,券商,價格,買進股數,賣出股數)，不是單純一列一筆的標準CSV。

    正確性驗證原理：買賣日報表裡「總買進股數必定等於總賣出股數」（每一筆成交
    都有一個買方一個賣方）——解析完後這兩個數字若相等，代表解析完整、
    沒有漏行也沒有重複計算。

    回傳 DataFrame[券商, 買進股數, 賣出股數]（單一券商在同一份報表可能出現在
    多個價位，這裡先回傳明細，彙總留給呼叫端依需求處理），或 None（解析失敗，
    例如檔案不是這個格式）。
    """
    try:
        text = raw_bytes.decode('big5', errors='ignore')
    except Exception:
        return None
    lines = text.split('\n')
    if len(lines) < 4:
        return None
    recs = []
    for ln in lines[3:]:   # 前3行是標題列
        parts = [p.strip() for p in ln.split(',')]
        if len(parts) < 5:
            continue
        for blk in (parts[0:5], parts[6:11] if len(parts) >= 11 else []):
            if len(blk) < 5 or not blk[1]:
                continue
            try:
                recs.append({'券商': blk[1], '買進股數': int(blk[3] or 0), '賣出股數': int(blk[4] or 0)})
            except (ValueError, IndexError):
                continue
    if not recs:
        return None
    return pd.DataFrame(recs)


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

    # 隔日沖警示：買超為正的分點裡，命中已知名單的加總買超 ÷ 當日總量
    day_trader_buy_shares = int(sum(
        row['買超股數'] for broker, row in g.iterrows()
        if row['買超股數'] > 0 and check_day_trader_alert(broker)
    ))
    day_trader_pct = round(day_trader_buy_shares / total_shares * 100, 2) if total_shares > 0 else None

    top5_table = [{'券商': idx, '買超張': round(row['買超股數'] / 1000, 1)}
                  for idx, row in top5.iterrows()]

    return {
        'total_shares': total_shares, 'top5_table': top5_table,
        'concentration_pct': concentration_pct,
        'day_trader_buy_shares': day_trader_buy_shares, 'day_trader_pct': day_trader_pct,
    }


@st.cache_data(ttl=86400, show_spinner=False)
def fetch_all_institutional_by_date(target_date, token=None):
    """
    ⚠️【目前未啟用 — 需要 FinMind 付費方案】⚠️

    這個函式用 FinMind「不帶 data_id 的全市場模式」一次抓當日整個市場的三大法人。
    Round19 建置時我假設這是免費功能，**這個假設是錯的**——總指揮官實測後回報
    http_error，查證確認免費帳號呼叫這個模式會收到 "Your level is free." 錯誤，
    那是 sponsor/backer 付費方案專屬的功能。

    保留這段程式碼的原因：如果哪天升級 FinMind 付費方案，把側邊欄的批次同步
    改回呼叫這個函式就能立刻用（一次呼叫解決全市場，比逐檔同步有效率得多）。
    在那之前，側邊欄改用「批次同步我關注的股票」——逐檔呼叫免費的單檔模式，
    只涵蓋持倉/雷達/觀察清單，額度完全在免費方案內。

    回傳 (rows, error_reason)。
    """
    url = 'https://api.finmindtrade.com/api/v4/data'
    # 【V160修復】全市場模式查證FinMind官方文件範例只傳start_date、不傳
    # end_date，改成只傳start_date、拿到結果後自己過濾目標日期，行為可預期。
    params = {'dataset': 'TaiwanStockInstitutionalInvestorsBuySell', 'start_date': target_date}
    if token:
        params['token'] = token
    try:
        payload = _finmind_get(url, params)
        df = pd.DataFrame(payload.get('data', []))
        if df.empty:
            return [], "FinMind 回傳空結果（可能該日尚未公布，或選到非交易日）"
        if 'date' in df.columns:
            df = df[df['date'].astype(str) == str(target_date)]
        if df.empty:
            return [], f"回應中沒有 {target_date} 這天的資料（可能該日尚未公布）"
        df['net'] = (pd.to_numeric(df['buy'], errors='coerce').fillna(0)
                     - pd.to_numeric(df['sell'], errors='coerce').fillna(0))
        piv = df.pivot_table(index=['date', 'stock_id'], columns='name',
                             values='net', aggfunc='sum').reset_index()
        rows = []
        for _, r in piv.iterrows():
            sym = str(r['stock_id']).strip()
            if not sym:
                continue
            rows.append({
                'date': str(r['date']),
                'symbol': sym,
                'foreign_buy': int(float(r.get('Foreign_Investor', 0) or 0) / 1000),
                'trust_buy': int(float(r.get('Investment_Trust', 0) or 0) / 1000),
                'dealer_buy': int(float(r.get('Dealer', 0) or 0) / 1000),
            })
        return rows, None
    except FinMindAPIError as e:
        # 【V160】把實際的 HTTP 狀態碼一起顯示出來——例如 402 代表方案權限不足、
        # 403 代表拒絕存取，兩者的處理方式完全不同，只寫「連線失敗」看不出差別。
        return [], f"API錯誤：{_reason_to_label(e.reason)}｜{e.reason}｜{e.detail}"
    except Exception as e:
        return [], f"例外：{type(e).__name__}: {e}"


def fetch_market_turnover_ranking():
    """
    【V160 新增】抓全市場「當日成交值」排行，用來把掃描池排序成「最值得看的前N檔」。

    解決的問題：GLOBAL_MARKET_CODES 原本只按股票代碼數字排序（round 14 的修正），
    所以「前400檔」其實是代碼小的400檔，跟「值不值得掃描」無關——
    代碼1101的水泥股不見得比代碼6488的環球晶更該進掃描池。

    做法：兩支免費官方端點各一次呼叫，各自涵蓋上市/上櫃全部個股：
      上市：TWSE STOCK_DAY_ALL（個股日成交資訊，含成交金額）
      上櫃：TPEx tpex_mainboard_daily_close_quotes（上櫃日收盤行情）
    依成交值由大到小排序回傳代碼清單。任一邊失敗就只用另一邊，兩邊都失敗回空 list
    （呼叫端會退回原本的代碼排序，不會整個壞掉）。
    """
    ranked = []

    # 上市
    try:
        res = _SESSION.get("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL", timeout=8)
        if res.status_code == 200:
            for item in res.json():
                code = str(item.get('Code', '')).strip()
                if len(code) != 4 or not code.isdigit():
                    continue
                val = safe_float(item.get('TradeValue', 0))
                if val > 0:
                    ranked.append((code, val))
    except Exception as e:
        print(f"[成交值排行] 上市端點失敗：{e}")

    # 上櫃
    try:
        res = _SESSION.get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes",
                           timeout=8)
        if res.status_code == 200:
            for item in res.json():
                code = str(item.get('SecuritiesCompanyCode', item.get('Code', ''))).strip()
                if len(code) != 4 or not code.isdigit():
                    continue
                # 櫃買欄位名稱與證交所不同，兩種都試（含千分位逗號要先清掉）
                raw = item.get('TradingAmount', item.get('TradeValue', 0))
                val = safe_float(str(raw).replace(',', ''))
                if val > 0:
                    ranked.append((code, val))
    except Exception as e:
        print(f"[成交值排行] 上櫃端點失敗：{e}")

    ranked.sort(key=lambda x: x[1], reverse=True)
    return [c for c, _ in ranked]


@st.cache_data(ttl=1800, show_spinner=False)
def fetch_market_gainers_with_industry():
    """
    【R96新增，累積清單第4項】抓全市場漲跌幅排行 + 對照產業分類，供
    evaluate_market_gainer_concentration()判斷「今天漲幅榜是不是集中在
    同一個族群」使用。複用fetch_market_turnover_ranking()同樣的兩個免費
    端點（TWSE STOCK_DAY_ALL + TPEx daily quotes），這兩個端點本身就有
    Change(漲跌價差)欄位可以算漲跌幅，不新增任何資料源依賴。

    【誠實的技術限制，總指揮官部署後請留意】TWSE OpenAPI的Change欄位
    格式沒有查到權威文件明確保證絕對乾淨（例如平盤日是否會用特殊字元
    表示），這裡用正規表示式只抓「數字+正負號+小數點」部分，解析失敗
    的那一檔直接跳過、不強行湊一個可能錯誤的漲跌幅——這代表如果這個
    欄位真的有意料外的格式，最壞情況是「漏掉幾檔」，不會是「算出錯誤
    的漲跌幅」。這個函式部署後建議實際跑一次，確認抓到的漲跌幅數字跟
    真實市場對得上，這是我這邊沒有網路連線能力驗證的部分。

    掛@st.cache_data(ttl=1800)——漲跌幅排行30分鐘內不用重複抓，跟其他
    「全市場一次性」端點的快取邏輯一致。

    回傳 list of (code, gain_pct, industry)，資料抓取失敗時回傳空list
    （呼叫端會自然得到evaluate_market_gainer_concentration的'unknown'
    判斷，不會整個壞掉）。
    """
    import re
    gainers = []

    def _parse_change_pct(change_raw, close):
        if close is None or close <= 0:
            return None
        m = re.search(r'[-+]?\d+\.?\d*', str(change_raw))
        if not m:
            return None
        try:
            change = float(m.group())
        except ValueError:
            return None
        prev_close = close - change
        if prev_close <= 0:
            return None
        return change / prev_close * 100

    try:
        res = _SESSION.get("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL", timeout=8)
        if res.status_code == 200:
            for item in res.json():
                code = str(item.get('Code', '')).strip()
                if len(code) != 4 or not code.isdigit():
                    continue
                close = safe_float(item.get('ClosingPrice', 0))
                gain_pct = _parse_change_pct(item.get('Change', ''), close)
                if gain_pct is not None:
                    gainers.append((code, gain_pct))
    except Exception as e:
        print(f"[漲幅榜族群性] 上市端點失敗：{e}")

    try:
        res = _SESSION.get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes",
                           timeout=8)
        if res.status_code == 200:
            for item in res.json():
                code = str(item.get('SecuritiesCompanyCode', item.get('Code', ''))).strip()
                if len(code) != 4 or not code.isdigit():
                    continue
                close = safe_float(str(item.get('Close', item.get('ClosingPrice', 0))).replace(',', ''))
                gain_pct = _parse_change_pct(item.get('Change', item.get('DiffPrice', '')), close)
                if gain_pct is not None:
                    gainers.append((code, gain_pct))
    except Exception as e:
        print(f"[漲幅榜族群性] 上櫃端點失敗：{e}")

    if not gainers:
        return []

    stock_to_ind, _ = fetch_industry_map_raw()
    return [(code, gain, stock_to_ind.get(code)) for code, gain in gainers]



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
                  'start_date': (datetime.now() - timedelta(days=10)).strftime('%Y-%m-%d')}
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
def fetch_industry_map():
    """
    【V160 R47 修復】這個函式本來就該有 @st.cache_data(ttl=86400)——程式裡至少
    5處呼叫端的註解都寫著「這個函式本身有24小時快取，呼叫幾乎零成本」
    （render_stock_card_ui 每張戰卡都呼叫一次），但裝飾器本身在某次改動中遺失，
    註解沒有跟著移除，變成一個「大家都以為有快取、實際上沒有」的陷阱。
    後果：render_stock_card_ui 每渲染一張卡片就真的打一次 FinMind TaiwanStockInfo
    （全市場批次端點），全市場掃描結果一次渲染幾十~幾百張卡片=幾十~幾百次
    不必要的重複API呼叫，短時間內燒光真實token額度，落到訪客300/hr也很快
    跟著用盡——這才是R46修好token分類後，「產業分類資料抓取失敗」「PE同業/
    營收同業空白」「掃描到一半沒跑完」這幾個症狀還繼續出現的真正原因：
    R46修的是「壞token時要不要換下一組」，這裡的問題是「根本不該一直打同一
    個請求」，兩個是不同層次的bug，R46沒有、也不可能覆蓋到這個。
    加回裝飾器後，這份全市場代碼→產業對照表一天只會真的向FinMind要一次，
    同一個session/24小時內其餘呼叫全部吃快取，API用量從「每卡一次」變回
    「每天一次」。

    【V159 新增，簡化版產業鏈】用 FinMind TaiwanStockInfo 一次性批次拉取產業分類，
    取代真正的供應鏈上下游圖譜（那個要維護一份供應鏈關聯資料庫，工程量太大）。
    這裡只做「同產業分類」，用來快速看同族群個股今日強弱，滿足「找同族群輪動股」
    這個實際需求的大部分場景，但不是真正的上下游供應鏈關聯。
    回傳 (stock_to_industry, industry_to_stocks) 兩個字典。
    """
    return fetch_industry_map_raw()


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
def _sort_key(code):
    try:
        return (0, int(code))   # 純數字代碼優先，按數值排序
    except ValueError:
        return (1, code)        # 非純數字（如带字母的代碼）排在後面，字母序
GLOBAL_MARKET_CODES = sorted(TW_STOCK_NAMES.keys(), key=_sort_key)


# 【R95續26】拿掉@st.cache_data，改用函式內的_smart_cached_call——理由見
# 函式docstring：@st.cache_data會把「失敗時回傳的空集合」誤當成成功結果
# 鎖住6小時，這是這輪抓到的重大bug。
def fetch_listed_only_codes():
    """
    【V160 Round39 新增】取得「上市」(twse) 股票代號集合，供自動掃描池過濾用。

    背景：總指揮官決定自動掃描池只掃上市，上櫃股需要評估時手動加入雷達/觀察區
    即可（那條路徑完全不受這個過濾影響）。原因：(1) 上櫃籌碼資料覆蓋率一直
    不如上市完整（inst_holding主要來源是上市T86 CSV）；(2) 縮小掃描範圍讓
    選股速度更快。

    用 FinMind TaiwanStockInfo 的 type 欄位判斷（twse=上市／tpex=上櫃），
    這是我們已經在用的同一個資料集(fetch_industry_map也是抓這個)，沒有額外
    打新的API。抓不到時回傳空集合，呼叫端會誠實地不過濾（不假裝知道哪些是
    上市，避免誤刪整個掃描池）。

    【R95續26重大修復】原本這裡是`@st.cache_data(ttl=21600)`直接包住整個
    函式，而函式內部自己把例外吞掉、回傳空集合——這代表只要FinMind剛好有
    一次暫時性失敗（這整個對話反覆證實過FinMind在盤中尖峰時段確實會不穩），
    Streamlit的快取機制會把這個「空集合」當成「成功回傳的結果」鎖住快取
    6小時！總指揮官回報「10點左右，15檔即時報價只有1檔查得到，重新整理
    也沒用」——追出來正是這個：一旦這個函式在某一刻不巧失敗過一次，接下來
    6小時內，attach_live_quotes()對每一檔股票的上市/上櫃判斷全部會退回
    不準確的猜測法，猜錯的那些直接查不到任何即時報價，不管重新整理幾次
    都一樣（因為問題不是這次查詢失敗，是「這次查詢用的判斷依據」6小時內
    都是錯的）。
    改用智慧快取（成功值長效保留、失敗2分鐘後就能重試，不會把一次失敗
    誤鎖成6小時的錯誤結果）——跟月營收/股利/本益比/股價這幾個資料源同一套
    已經驗證過的修復模式。
    """
    def _do_fetch():
        payload = _finmind_get('https://api.finmindtrade.com/api/v4/data',
                               {'dataset': 'TaiwanStockInfo'}, max_retries=2, timeout=20)
        df = pd.DataFrame(payload.get('data', []))
        if df.empty or 'type' not in df.columns:
            return set()
        return set(df.loc[df['type'] == 'twse', 'stock_id'])

    try:
        return _smart_cached_call("listed_only_codes", _do_fetch,
                                  recheck_interval=21600, fail_retry=10)
    except Exception as _e:
        print(f"[fetch_listed_only_codes-診斷] 抓上市代號清單失敗(將退回_EXT_HINT猜測)：{type(_e).__name__}: {_e}")
        return set()


def get_scan_pool_ordered():
    """
    【V160 新增】把掃描池改成「依當日成交值由大到小」排序。

    為什麼重要：掃描池滑桿設300檔時，取的應該是「最值得看的300檔」，
    而不是「代碼數字最小的300檔」。成交值是最直接的「市場關注度」代理指標——
    成交值大代表有資金在裡面，才有籌碼訊號可言；冷門股就算技術面型態漂亮，
    也常因為量太小而無法成交或滑價嚴重。

    抓不到排行時（假日、端點異常）誠實退回原本的代碼排序，不讓功能整個停擺。
    快取6小時，一天最多打2次，額度成本可忽略。

    【V160 Round39 新增】只保留上市(twse)標的——這個過濾只影響「自動掃描池」
    本身，不影響你手動加進雷達/觀察區的上櫃股（那些走完全不同的路徑，加入時
    不會經過這個函式）。抓不到上市清單時（fetch_listed_only_codes回傳空集合）
    不過濾，避免誤刪整個掃描池。
    """
    ranked = fetch_market_turnover_ranking()
    if not ranked:
        pool, _used_turnover = GLOBAL_MARKET_CODES, False
    else:
        known = set(TW_STOCK_NAMES.keys())
        ordered = [c for c in ranked if c in known]
        # 排行裡沒出現的（當日無成交等）接在後面，確保沒有股票被永久排除
        rest = [c for c in GLOBAL_MARKET_CODES if c not in set(ordered)]
        pool, _used_turnover = ordered + rest, True

    listed = fetch_listed_only_codes()
    if listed:
        pool = [c for c in pool if c in listed]
    return pool, _used_turnover



# 【V160 Round39】fetch_twse_mis_batch/_safe_mis_float已搬進warroom_
# core.py，這裡直接import。_get_live_quotes_cached是網頁版專屬15秒快取，
# 排程端不需要(一次性腳本不會有重複呼叫問題)。

@st.cache_data(ttl=15, show_spinner=False)
def _get_live_quotes_cached(pairs_tuple):
    """
    【V160 Round38】fetch_twse_mis_batch 的短快取包裝——15秒內同一批代號的
    重複請求（例如你連續點了幾次畫面互動，Streamlit 每次互動都會重跑整支程式）
    直接吃快取，不會每次都真的打證交所端點，兼顧「夠即時」跟「不要打太兇」。
    pairs_tuple 必須是 tuple 不是 list，st.cache_data 才能拿去當快取key。
    """
    return fetch_twse_mis_batch(list(pairs_tuple))


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
                    .select("symbol,overall_verdict,overall_label,gate1_verdict,gate2_verdict,gate3_verdict,detail")
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
            _last_cache[code] = {
                'price': q['price'], 'time': q.get('time', ''),
                'date': q.get('date', ''), 'change_pct': q.get('change_pct'),
            }
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
            _prev = _last_cache[code]
            c['live_price'] = _prev['price']
            c['live_time'] = _prev['time']
            c['live_date'] = _prev['date']
            c['live_change_pct'] = _prev['change_pct']
            c['live_is_carried'] = True
        # 兩種情況都沒有(從來沒查到過這檔的即時成交)：維持原樣不加欄位，
        # 畫面上該欄位仍然是"—"——這種情況下顯示"—"才是誠實的，不是
        # bug，因為根本沒有任何一筆真實成交可以沿用。
        else:
            # 【R96新增，診斷用】正常情況下這次沒查到，_last_cache應該
            # 還留著上次成功的那筆。連_last_cache都沒有，代表這個session
            # 從頭到尾都沒成功抓到過，這行log能直接分辨兩種情況。
            print(f"[即時報價-診斷] {code}：這次沒查到，_last_cache也沒有上一筆"
                  f"可沿用——這個session從頭到尾都沒成功抓到過這檔的即時報價。")
    return cards_map


def _yf_ticker(sym):
    """新版 yfinance 對 requests.Session 有相容性問題，做雙軌降級。"""
    try:
        return yf.Ticker(sym, session=_SESSION)
    except Exception:
        return yf.Ticker(sym)


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
        start_date = (datetime.now() - timedelta(days=10)).strftime('%Y-%m-%d')
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
    try:
        _live = fetch_twse_mis_batch([("t00", "tse")])
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
        today_str = datetime.now().strftime('%Y%m%d')
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
            _today_md = datetime.now().strftime('%m/%d')
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


@st.cache_data(ttl=300, show_spinner=False)
def get_market_regime():
    """【任務二】大盤位階風控濾網：TWII 收盤 vs 20MA。"""
    try:
        tk = _yf_ticker("^TWII")
        # 【V160修復】原本這個函式沒設timeout，網路壅塞時會無上限卡住
        # 拖累整個開機流程。加上6秒逾時，抓不到就走except分支。
        hist = tk.history(period="3mo", timeout=6)
        hist = hist.dropna(subset=['Close'])
        if len(hist) >= 20:
            # 【V160 Round34修復】跟大盤氣象同一個根因，移除fast_info改回單純用
            # 日K最後一筆收盤——這裡的取捨跟大盤氣象不同：位階濾網是拿收盤價跟
            # 20MA比，就算延遲一天影響有限，穩定性優先於即時性。
            ma20 = float(hist['Close'].tail(20).mean())
            close = float(hist['Close'].iloc[-1])
            dev = (close - ma20) / ma20 * 100 if ma20 else 0.0
            return {'close': close, 'ma20': ma20, 'bull': close >= ma20,
                    'dev': dev, 'known': True}
    except Exception:
        pass
    # 抓不到大盤時「不降級」，避免誤殺；但明確標示未知
    return {'close': 0.0, 'ma20': 0.0, 'bull': True, 'dev': 0.0, 'known': False}


weather_str, weather_color, global_twii_gain = get_market_weather_real()
MARKET_REGIME = get_market_regime()


@st.cache_data(ttl=600, show_spinner=False)
def get_overnight_macro():
    """
    【V160 A階段】隔夜總經 HUD：抓那斯達克、標普500、費半SOX、美元台幣、TSM/UMC ADR。
    這些是台股（尤其電子權值）的先行指標，供開盤前判斷+系統選股閘門使用。
    每個標的獨立 try + 5秒逾時，抓不到就標示、不影響其他標的、也不會拖慢整體載入。
    【V160 移除】台指期(FITX=F)已移除——Yahoo沒有可靠的免費台指期即時資料，這類期貨
    即時報價通常是券商付費API才有，長期顯示「無資料」對總指揮官沒有實質幫助，直接拿掉。
    開盤前閘門改用那斯達克/標普/費半/NQ期貨/ES期貨判斷，準確度已足夠。
    """
    tickers = {
        '那斯達克': '^IXIC',
        '標普500': '^GSPC',
        '費城半導體': '^SOX',
        '美元台幣': 'TWD=X',
        '台積電ADR': 'TSM',
        '聯電ADR': 'UMC',
        '那斯達克期貨': 'NQ=F',    # 【V160新增】幾乎24小時交易，比昨日美股收盤更即時反映當下情緒
        '標普期貨': 'ES=F',
    }
    out = {}
    for name, sym in tickers.items():
        try:
            tk = _yf_ticker(sym)
            hist = tk.history(period="5d", timeout=5).dropna(subset=['Close'])
            if len(hist) >= 2:
                cur, prev = float(hist['Close'].iloc[-1]), float(hist['Close'].iloc[-2])
                pct = (cur - prev) / prev * 100 if prev else 0.0
                pt_change = cur - prev
                data_date = hist.index[-1].strftime('%m/%d')
                out[name] = {'value': cur, 'pct': round(pct, 2), 'pt_change': round(pt_change, 2),
                            'data_date': data_date, 'ok': True}
            else:
                out[name] = {'value': 0, 'pct': 0, 'pt_change': 0, 'data_date': '', 'ok': False}
        except Exception:
            out[name] = {'value': 0, 'pct': 0, 'pt_change': 0, 'data_date': '', 'ok': False}
    return out


def evaluate_overnight_gate(macro, market_bull=True):
    """
    【V160 R43 更新】開盤前總經閘門——跟排程端 system_scheduler.py 的
    classify_gate_mode 改用同一套三態設計（多頭順風/對沖模式/恐慌熔斷），
    取代原本的binary正常/暫緩。這裡是網頁版HUD的純顯示用途，不直接下單
    （真正的下單決策在排程那邊），但用同一套判斷邏輯、同一組門檻，避免
    使用者在網頁上看到「隔夜平穩」，但排程那邊其實已經進入對沖或熔斷模式
    的認知落差。

    回傳 (status, reason)，status: 'bull' / 'hedge' / 'panic'（配合舊有
    呼叫端預期的2元組格式，只是status的可能值從2種變成3種）。
    """
    if not macro:
        return 'bull', '無隔夜資料，預設多頭順風'

    sox = macro.get('費城半導體', {})
    tsm = macro.get('台積電ADR', {})
    sox_pct = sox.get('pct') if sox.get('ok') else None
    tsm_pct = tsm.get('pct') if tsm.get('ok') else None

    if (sox_pct is not None and sox_pct <= -2.0) or (tsm_pct is not None and tsm_pct <= -2.5):
        _sox_disp = f"{sox_pct:+.1f}%" if sox_pct is not None else "無資料"
        _tsm_disp = f"{tsm_pct:+.1f}%" if tsm_pct is not None else "無資料"
        return 'panic', f"🚨 恐慌熔斷：費半{_sox_disp}／台積電ADR{_tsm_disp}"
    elif sox_pct is not None and -1.9 <= sox_pct <= -0.5 and not market_bull:
        return 'hedge', f"🟡 對沖模式：費半{sox_pct:+.1f}%且大盤破20MA"
    else:
        return 'bull', '🟢 多頭順風：隔夜平穩或上漲'



@st.cache_data(ttl=120, show_spinner=False)
def calc_weekly_resonance(hist):
    """
    【V160 延伸3】多時間框架共振：把日線資料重新取樣成週線，判斷週線趨勢方向。

    為什麼要這個：目前所有訊號都基於日線。日線雜訊大，常出現「日線轉強但其實
    只是下降趨勢裡的反彈」。加上週線確認，能過濾掉相當比例的假突破——這是最
    經典的假訊號過濾器，也是「買在反彈」與「買在反轉」的分水嶺。

    成本考量：刻意用既有的日線資料 resample，不另外呼叫 yfinance 抓週線，
    所以這個功能完全不增加 API 負擔與載入時間。

    回傳 dict：
      trend: 'bull'／'bear'／'neutral'／'unknown'（資料不足時誠實回 unknown，不猜）
      close/ma5/ma10: 週線數值
      bars: 實際可用的週線根數（讓呼叫端知道樣本夠不夠）
    """
    unknown = {'trend': 'unknown', 'close': 0.0, 'ma5': 0.0, 'ma10': 0.0, 'bars': 0}
    if hist is None or len(hist) < 50:
        # 週線MA10需要10根週線＝約50個交易日，不足就誠實說不知道
        return unknown
    try:
        wk = hist.resample('W').agg({
            'Open': 'first', 'High': 'max', 'Low': 'min',
            'Close': 'last', 'Volume': 'sum'}).dropna(subset=['Close'])
        if len(wk) < 10:
            return unknown
        wk_ma5 = wk['Close'].rolling(5).mean()
        wk_ma10 = wk['Close'].rolling(10).mean()
        close = float(wk['Close'].iloc[-1])
        ma5 = float(wk_ma5.iloc[-1]) if pd.notna(wk_ma5.iloc[-1]) else 0.0
        ma10 = float(wk_ma10.iloc[-1]) if pd.notna(wk_ma10.iloc[-1]) else 0.0
        if ma5 <= 0 or ma10 <= 0:
            return unknown
        # MA5 斜率：跟上一根比，判斷週線動能方向
        prev_ma5 = float(wk_ma5.iloc[-2]) if pd.notna(wk_ma5.iloc[-2]) else ma5
        rising = ma5 > prev_ma5

        if close > ma5 and ma5 > ma10 and rising:
            trend = 'bull'
        elif close < ma5 and ma5 < ma10 and not rising:
            trend = 'bear'
        else:
            trend = 'neutral'
        return {'trend': trend, 'close': round(close, 2), 'ma5': round(ma5, 2),
                'ma10': round(ma10, 2), 'bars': len(wk)}
    except Exception:
        return unknown


def apply_timeframe_resonance(verdict, score, weekly):
    """
    【V160 延伸3】用週線趨勢調整日線結論，回傳 (調整後verdict, 說明字串或None)。

    調整規則（刻意保守，只降級不升級）：
      - 日線看多但週線走空 → 降級（這是「反彈而非反轉」的典型樣態）
      - 日線看空但週線走多 → 降級空方力道（避免在多頭回檔時搶空）
      - 週線資料不足(unknown) → 完全不調整，並且不顯示共振資訊，不假裝有判斷
    刻意「只降級不升級」的原因：升級等於放大部位風險，而週線同向本來就已經
    反映在日線分數裡了，再加成會變成重複計算同一個訊號。
    """
    wt = weekly.get('trend', 'unknown')
    if wt == 'unknown':
        return verdict, None
    bullish_verdicts = ('🔥 建議進攻', '🟡 觀望偏多')
    bearish_verdicts = ('🔵 建議撤退', '⚠️ 轉弱警戒')

    if verdict in bullish_verdicts and wt == 'bear':
        return '🟡 觀望偏多' if verdict == '🔥 建議進攻' else '⚖️ 中性等待', \
               "⛰️ 週線仍空：日線轉強但週線結構未翻多，較可能是反彈而非反轉，已降級"
    if verdict in bearish_verdicts and wt == 'bull':
        return '⚖️ 中性等待' if verdict == '🔵 建議撤退' else '⚖️ 中性等待', \
               "⛰️ 週線仍多：日線轉弱但週線結構仍多頭，較可能是回檔而非轉空，已降級"
    if verdict in bullish_verdicts and wt == 'bull':
        return verdict, "✅ 日週同步偏多：多時間框架共振，訊號可信度較高"
    if verdict in bearish_verdicts and wt == 'bear':
        return verdict, "✅ 日週同步偏空：多時間框架共振，訊號可信度較高"
    return verdict, None


def estimate_main_force_cost(hist, inst_df=None, big_holder_pct=None):
    """
    【V160 延伸2】主力成本的「免費替代估計」。

    背景：真正的主力成本要靠券商分點資料（籌碼K線的招牌功能），但 FinMind 的
    分點資料集限 sponsor 付費方案。這裡用免費資料做合理近似。

    三個估計來源（各有不同的成本語意，刻意分開列出而不是混成一個數字，
    因為它們代表不同的東西，混在一起會失去可解讀性）：
      1. VWAP20／VWAP60：成交量加權平均價 = 「整體市場的平均成本」。
         這是最穩健的代理，因為大資金的成交必然反映在成交量權重上。
      2. 近期爆量日均價：只取成交量前25%的交易日算加權均價。
         大單進場通常伴隨爆量，所以這個數字更偏向「大戶的成本」而非散戶。
      3. 籌碼集中度變化：大戶持股比例的變化方向（需要有大戶資料才算得出來）。

    ⚠️ 這是「估計」不是「實際分點成本」，準確度需要靠校正機制驗證
    （見 sb_log_cost_calibration）。抓不到就回 None，不編造數字。

    回傳 dict 或 None。
    """
    if hist is None or len(hist) < 20:
        return None
    try:
        df = hist.copy()
        # 典型價（TP）比單純用收盤更接近真實成交分布
        tp = (df['High'] + df['Low'] + df['Close']) / 3.0
        vol = df['Volume']

        def _vwap(n):
            t, v = tp.tail(n), vol.tail(n)
            tot = float(v.sum())
            return round(float((t * v).sum() / tot), 2) if tot > 0 else None

        vwap20, vwap60 = _vwap(20), _vwap(min(60, len(df)))

        # 【注意】原本用r_vol>=quantile(0.75)，成交量分布偏斜時會失真
        # (例如83%的值都相同，quantile會落在該值，>=把全部都選進來)。
        # 改用nlargest直接取前N大，不受分布形狀影響。
        recent = df.tail(min(60, len(df)))
        r_tp = (recent['High'] + recent['Low'] + recent['Close']) / 3.0
        r_vol = recent['Volume']
        _n_heavy = max(3, int(len(r_vol) * 0.25))
        if len(r_vol) >= 8 and float(r_vol.sum()) > 0:
            heavy_idx = r_vol.nlargest(_n_heavy).index
            hv_tot = float(r_vol.loc[heavy_idx].sum())
            heavy_vwap = (round(float((r_tp.loc[heavy_idx] * r_vol.loc[heavy_idx]).sum() / hv_tot), 2)
                          if hv_tot > 0 else None)
            heavy_days = len(heavy_idx)
        else:
            heavy_vwap, heavy_days = None, 0

        cur = float(df['Close'].iloc[-1])
        # 現價相對各成本的乖離：正=市場平均在賺，負=市場平均套牢
        def _dev(base):
            return round((cur - base) / base * 100, 2) if base and base > 0 else None

        return {
            'vwap20': vwap20, 'vwap60': vwap60,
            'heavy_vwap': heavy_vwap, 'heavy_days': heavy_days,
            'dev_vwap20': _dev(vwap20), 'dev_vwap60': _dev(vwap60),
            'dev_heavy': _dev(heavy_vwap),
            'big_holder_pct': big_holder_pct,
            'current': round(cur, 2),
        }
    except Exception:
        return None


def sb_log_cost_calibration(symbol, our_estimate, actual_value, source_note="", broker_name=None,
                            buy_shares=None, holding_period=None, concentration_pct=None):
    """
    【V160 延伸2 校正機制】記錄一筆「我們的估計 vs 你從籌碼K線抄回來的實際值」。

    這是總指揮官提出的構想，我認為它比功能本身更有價值：它把「猜測」變成
    「有已知誤差範圍的估計」。累積夠多筆之後，就能回答「我們的主力成本估計
    平均差多少%」——如果誤差穩定在10%內就可以信任，如果忽大忽小代表這個
    估計法在某些股票上不適用，而這個資訊本身就有用。

    【V160 新增】broker_name：記錄這筆數字是哪家券商的買均價（或"三家均值"），
    讓 summarize_calibration_by_broker 能分券商統計，回答「哪家券商的買均價
    跟我們的估計比較一致」。

    【V160 R41 新增】buy_shares：這家券商當日買超張數，用來算籌碼集中度
    （前5大買超張數 / 當日總成交量）——只走「方案A」，只影響戰卡顯示，
    不進排程自動選股評分，避免400檔裡只有少數幾檔有這個資料造成分數
    不可比。holding_period：天期標記（5日/10日/20日/60日），讓歷史校正
    紀錄能區分「這家券商在哪個天期建倉的均價比較準」，之後覆盤時能看出
    例如「這家券商在20日波段的均價特別準，但5日極短線的誤差比較大」。
    兩者皆選填(None時不影響既有欄位)，向下相容既有呼叫端。

    【R66新增】concentration_pct：當次算出來的籌碼集中度(前5大買超張數/
    當日總成交量)，只存在"五家均值"那筆(source_note=='五家均值')，避免
    同一天存6筆重複值。這是舊交接文件待辦「籌碼集中度跟自己歷史比」的
    資料基礎——之前只有算完當場顯示、沒有存下來，永遠只能用寫死的5%
    門檻，因為沒有歷史數字可以比。存下來後，累積到10筆同一檔的紀錄，
    就能改用「這次比這檔股票過去的百分之幾高」取代死板的5%。
    """
    def _do():
        return SUPABASE_CONN.table("cost_calibration").insert({
            "symbol": str(symbol),
            "log_date": datetime.now().strftime('%Y-%m-%d'),
            "our_estimate": float(our_estimate),
            "actual_value": float(actual_value),
            "error_pct": round((float(our_estimate) - float(actual_value))
                               / float(actual_value) * 100, 2) if float(actual_value) else None,
            "source_note": source_note,
            "broker_name": broker_name,
            "buy_shares": float(buy_shares) if buy_shares is not None else None,
            "holding_period": holding_period,
            "concentration_pct": float(concentration_pct) if concentration_pct is not None else None,
        }).execute()
    ok, _ = _sb_safe(_do)
    return ok


def get_concentration_percentile(symbol, today_pct):
    """
    【R66新增】舊交接文件待辦：籌碼集中度「跟自己歷史比」機制。

    讀這檔股票過去存過的concentration_pct(排除今天剛存的那筆)，如果累積
    不到10筆，誠實回傳None——不足以支撐百分位判斷，呼叫端應該退回原本的
    固定5%門檻，不要用樣本太少的百分位假裝精確。累積到10筆以上，才計算
    「今天這個集中度，比過去百分之幾的紀錄都高」。

    回傳 (percentile, history_count)；percentile為0-100的數字，None代表
    樣本不足或查無資料。
    """
    rows = sb_get_cost_calibration(symbol)
    if not rows:
        return None, 0
    _hist = [r.get('concentration_pct') for r in rows
             if r.get('source_note') == '五家均值' and r.get('concentration_pct') is not None]
    if len(_hist) < 10:
        return None, len(_hist)
    _below = sum(1 for v in _hist if v < today_pct)
    _pctl = round(_below / len(_hist) * 100, 1)
    return _pctl, len(_hist)


def summarize_calibration_by_broker(rows):
    """
    【V160 新增】把校正紀錄按券商分組，回答總指揮官的問題：
    「前五大券商裡，哪家的買均價數字跟我們的估計比較一致？」

    ⚠️ 誠實說明這個比較的真正意義：我們沒有「絕對正確」的主力成本可以當標準答案，
    能比的只是「哪家券商的買均價，長期下來跟我們的免費估計法算出的數字比較接近」。
    這回答的是「哪家券商的數字最貼近我們的估計」，不是「哪家券商客觀上最準」——
    如果我們的估計法本身有系統性偏差，這個排名也會跟著偏。這點必須先講清楚，
    不能讓這個功能看起來像在下一個它給不出的結論。

    回傳 dict: {券商名稱: {筆數, 平均絕對誤差, 系統性偏差}}，依平均絕對誤差排序（越準排越前面）。
    """
    if not rows:
        return {}
    by_broker = {}
    for r in rows:
        b = r.get('broker_name') or r.get('source_note') or '未分類'
        by_broker.setdefault(b, []).append(r)
    out = {}
    for b, rs in by_broker.items():
        s = summarize_calibration(rs)
        if s:
            out[b] = s
    return dict(sorted(out.items(), key=lambda kv: kv[1]['mean_abs_err']))


def sb_log_manual_trade(symbol, entry_price, exit_price, qty, entry_date=None, side='long'):
    """
    【V160 R44 新增，V160 後續擴充做空】記錄一筆你自己手動持倉的完整交易
    （進場→出場），供風報比/MDD/資金曲線統計用。之前「從持倉移除」是直接
    刪除，沒有留下任何紀錄——這代表「你自己選股的績效」完全算不出來，
    只有系統模擬倉才有數字可看。

    這裡不影響「從持倉移除」原本的行為（沒填出場價一樣可以直接移除，這筆
    就不記錄，不強迫），只是多一個「順便記一筆」的選項。

    【觀察區轉持倉支援做空】side參數決定損益方向，直接複用
    calc_real_profit_v2（跟持倉卡片顯示用的是同一套計算，不重複寫一份
    可能算法會漂移的邏輯）。
    """
    if exit_price <= 0 or entry_price <= 0:
        return False
    pnl, roi = calc_real_profit_v2(entry_price, exit_price, qty, side=side)
    def _do():
        return SUPABASE_CONN.table("manual_trade_log").insert({
            "symbol": str(symbol), "entry_date": entry_date or datetime.now().strftime('%Y-%m-%d'),
            "exit_date": datetime.now().strftime('%Y-%m-%d'), "side": side,
            "entry_price": float(entry_price), "exit_price": float(exit_price), "qty": float(qty),
            "realized_pnl": round(pnl, 0), "realized_roi": round(roi, 2),
        }).execute()
    ok, _ = _sb_safe(_do)
    return ok


def sb_get_manual_trade_log():
    """讀取你自己手動交易的完整結算紀錄，供風報比/MDD/資金曲線用。"""
    def _do():
        return SUPABASE_CONN.table("manual_trade_log").select("*").order("exit_date").execute()
    ok, res = _sb_safe(_do)
    return res.data if (ok and res is not None and getattr(res, "data", None)) else []


def compute_risk_metrics(closed_trades, min_samples=10, open_positions=None):
    """
    【V160 R44 新增】風報比(盈虧比) + 最大拉回(MDD) + 累積報酬率曲線。

    風報比 = 已平倉平均獲利金額 / 平均虧損金額（絕對值）——數字越高代表
    「贏的時候贏得比輸的時候輸得多」，是比單純勝率更能反映策略真實期望值
    的指標（勝率60%但賺1賠3，整體還是虧錢；勝率40%但賺3賠1，整體是賺錢的）。

    最大拉回(MDD) = 用平倉紀錄依時間序累加成淨值曲線，找出「從最高點到
    最低點」的最大跌幅百分比。

    【R67改善】原本的限制：MDD只計入已平倉損益，沒把「還沒平倉的浮動虧損」
    算進去，數字會比真實風險樂觀（少算了抱著虧損部位不賣那段時間的痛苦）。
    完整解法需要「每天記錄持倉市值」的歷史，我們沒有；但有一個實務上有效的
    近似：把「當下持倉的未實現損益」當作淨值曲線的最後一個點接上去。

    這樣算出來的 max_drawdown_incl_open 回答的是真正該問的問題——
    「如果現在把所有部位清掉，我從歷史最高點到現在總共回落多少」。
    它會抓到「已平倉看起來很賺，但現在抱著三檔大虧的股票不肯認賠」這種
    最危險的情況，那正是純已平倉MDD完全看不到的盲點。

    open_positions：list of dict，每筆要有 realized_roi 欄位語意的未實現
    報酬率（呼叫端算好傳進來，這裡不重算，避免跟畫面上的損益數字不一致）。
    不傳就維持原本只算已平倉的行為，完全向下相容。

    樣本數 < min_samples(預設10) 時不給任何數字——回傳 sample_count 讓
    呼叫端顯示「累積中 X/10筆」，不是假裝有統計意義的結果硬要顯示出來。
    """
    n = len(closed_trades)
    if n < min_samples:
        return {'ready': False, 'sample_count': n, 'min_samples': min_samples}

    rois = [float(t.get('realized_roi', 0) or 0) for t in closed_trades]
    pnls = [float(t.get('realized_pnl', 0) or 0) for t in closed_trades]
    wins = [x for x in pnls if x > 0]
    losses = [x for x in pnls if x < 0]

    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = abs(sum(losses) / len(losses)) if losses else 0.0
    profit_factor = round(avg_win / avg_loss, 2) if avg_loss > 0 else None

    # 依exit_date排序，累加報酬率曲線算MDD
    sorted_trades = sorted(closed_trades, key=lambda t: t.get('exit_date', ''))
    cum_ret = 0.0
    equity_curve = []
    peak = 0.0
    max_dd = 0.0
    for t in sorted_trades:
        cum_ret += float(t.get('realized_roi', 0) or 0)
        equity_curve.append({'date': t.get('exit_date', ''), 'cum_return': round(cum_ret, 2)})
        peak = max(peak, cum_ret)
        dd = peak - cum_ret
        max_dd = max(max_dd, dd)

    win_rate = round(sum(1 for x in pnls if x > 0) / n * 100, 1)

    # 【R67新增】把當下持倉的未實現損益接在曲線最後，算出含未實現的MDD
    max_dd_incl_open = None
    open_unrealized_roi = None
    if open_positions:
        open_unrealized_roi = sum(float(p.get('realized_roi', 0) or 0) for p in open_positions)
        _cum_now = cum_ret + open_unrealized_roi
        _peak_incl = max(peak, _cum_now)
        max_dd_incl_open = round(max(max_dd, _peak_incl - _cum_now), 2)
        equity_curve.append({'date': '現在(含未實現)', 'cum_return': round(_cum_now, 2)})

    return {
        'ready': True, 'sample_count': n,
        'profit_factor': profit_factor, 'avg_win': round(avg_win, 0), 'avg_loss': round(avg_loss, 0),
        'win_rate': win_rate, 'max_drawdown_pct': round(max_dd, 2), 'equity_curve': equity_curve,
        'max_drawdown_incl_open': max_dd_incl_open,
        'open_unrealized_roi': round(open_unrealized_roi, 2) if open_unrealized_roi is not None else None,
        'open_count': len(open_positions) if open_positions else 0,
    }


def sb_get_cost_calibration(symbol=None):
    """讀取校正紀錄。symbol=None 讀全部（用來算整體平均誤差）。"""
    def _do():
        q = SUPABASE_CONN.table("cost_calibration").select("*")
        if symbol:
            q = q.eq("symbol", str(symbol))
        return q.order("log_date", desc=True).limit(500).execute()
    ok, res = _sb_safe(_do)
    return res.data if (ok and res is not None and getattr(res, "data", None)) else []


def compute_and_store_industry_pe(cards, stock_to_ind, min_members=5):
    """
    【V160 R42 新增】PE 同業中位數——只在全市場掃描時算一次，存進 Supabase，
    之後登入登出都直接讀已存的數字，不用每次重跑（總指揮官明確要求：
    「不用每次登入都要跑一次」）。

    設計取捨：這裡故意選「全市場掃描時順便算」而不是「每檔戰卡各自查一次
    同業資料」——後者要為每檔額外抓同業資料，API用量會大增、拖慢戰卡載入；
    前者反正掃描時400檔資料都已經算好在手上，分組算中位數幾乎零成本。
    代價：沒跑過全市場掃描的話，同業中位數就是空的（不會假裝有資料）。

    同產業樣本 < min_members(預設5) 時不存這個產業——樣本太少的中位數沒有
    統計意義，寧可不顯示也不要給一個看起來很專業但不可信的數字。

    cards：本次掃描算出來的戰卡 dict 清單（不分是否通過篩選條件，全部納入——
    篩選條件是「使用者想看哪些」，跟「這檔股票該不該算進同業統計」是兩件事）。
    stock_to_ind：代號→產業分類字典（來自既有的 fetch_industry_map）。
    """
    from collections import defaultdict
    by_ind = defaultdict(list)
    for c in cards:
        code = c.get('code', '')
        pe = c.get('pe')
        ind = stock_to_ind.get(code)
        if ind and pe and pe > 0:
            by_ind[ind].append(pe)

    today = datetime.now().strftime('%Y-%m-%d')
    stored = 0
    for ind, pe_list in by_ind.items():
        if len(pe_list) < min_members:
            continue
        median_pe = round(float(pd.Series(pe_list).median()), 1)

        def _do(_ind=ind, _median=median_pe, _n=len(pe_list)):
            return SUPABASE_CONN.table("industry_pe_stats").upsert({
                "industry": _ind, "median_pe": _median, "sample_count": _n,
                "updated_date": today,
            }, on_conflict="industry").execute()
        ok, _ = _sb_safe(_do)
        if ok:
            stored += 1
    return stored


@st.cache_data(ttl=3600, show_spinner=False)
def get_industry_pe_stats():
    """
    讀取已存的同業PE中位數（不重新計算，只讀 Supabase）。回傳
    {industry: {median_pe, sample_count, updated_date}}。抓不到時回空字典，
    呼叫端會誠實地不顯示同業比較，不編造數字。快取1小時——這個數字變動
    很慢（要跑過一次新的全市場掃描才會變），不需要每次都重新查。
    """
    def _do():
        return SUPABASE_CONN.table("industry_pe_stats").select("*").execute()
    ok, res = _sb_safe(_do)
    if not (ok and res is not None and getattr(res, "data", None)):
        return {}
    return {row["industry"]: {"median_pe": row["median_pe"], "sample_count": row["sample_count"],
                              "updated_date": row.get("updated_date", "")}
            for row in res.data}


def compute_and_store_industry_revenue(cards, stock_to_ind, min_members=5):
    """
    【V160 新增：雙引擎族群透視】產業營收YoY「平均數 vs 中位數」統計——
    只在全市場掃描時算一次，存進 Supabase，之後族群輪動熱力圖直接讀現成
    數字，不用每次點開熱力圖都額外打上百次 FinMind API。跟 R42 的PE同業
    中位數用同一套設計（compute_and_store_industry_pe），這裡是同樣模式
    套用到營收YoY，多算一個「平均數」是因為這次要拿平均vs中位數互相對照，
    戳破「少數飆股拉動整個族群、其實過半數公司沒成長」這種假族群起漲。

    平均數(yoy_mean)代表極端爆發力——少數飆股會把它拉得很高。
    中位數(yoy_median)代表產業普及率——不受極端值影響，反映「過半數公司」
    的真實狀況。兩者一起看，才看得出「族群普遍成長」跟「少數龍頭硬拉」的差別。

    同產業樣本 < min_members(預設5) 時不存——樣本太少的平均/中位數沒有
    統計意義，理由跟PE同業中位數一致，這裡沿用同一個門檻不另外發明一個。
    """
    from collections import defaultdict
    by_ind = defaultdict(list)
    for c in cards:
        code = c.get('code', '')
        yoy = c.get('rev_yoy')
        ind = stock_to_ind.get(code)
        if ind and yoy is not None:
            by_ind[ind].append(float(yoy))

    today = datetime.now().strftime('%Y-%m-%d')
    stored = 0
    for ind, yoy_list in by_ind.items():
        if len(yoy_list) < min_members:
            continue
        s = pd.Series(yoy_list)
        yoy_mean = round(float(s.mean()), 1)
        yoy_median = round(float(s.median()), 1)

        def _do(_ind=ind, _mean=yoy_mean, _median=yoy_median, _n=len(yoy_list)):
            return SUPABASE_CONN.table("industry_revenue_stats").upsert({
                "industry": _ind, "yoy_mean": _mean, "yoy_median": _median,
                "sample_count": _n, "updated_date": today,
            }, on_conflict="industry").execute()
        ok, _ = _sb_safe(_do)
        if ok:
            stored += 1
    return stored


@st.cache_data(ttl=3600, show_spinner=False)
def get_industry_revenue_stats():
    """
    讀取已存的產業營收YoY平均/中位數統計（不重新計算，只讀 Supabase）。
    回傳 {industry: {yoy_mean, yoy_median, sample_count, updated_date}}。
    抓不到時回空字典，呼叫端誠實地不顯示營收欄，不編造數字。
    """
    def _do():
        return SUPABASE_CONN.table("industry_revenue_stats").select("*").execute()
    ok, res = _sb_safe(_do)
    if not (ok and res is not None and getattr(res, "data", None)):
        return {}
    return {row["industry"]: {"yoy_mean": row["yoy_mean"], "yoy_median": row["yoy_median"],
                              "sample_count": row["sample_count"], "updated_date": row.get("updated_date", "")}
            for row in res.data}


def summarize_calibration(rows):
    """
    把校正紀錄整理成可讀的準確度摘要。
    回傳 dict：筆數、平均絕對誤差%、中位數誤差%、是否偏高/偏低（有系統性偏差就講出來）。
    """
    if not rows:
        return None
    errs = [float(r['error_pct']) for r in rows if r.get('error_pct') is not None]
    if not errs:
        return None
    abs_errs = sorted(abs(e) for e in errs)
    n = len(abs_errs)
    median_abs = abs_errs[n // 2] if n % 2 else (abs_errs[n // 2 - 1] + abs_errs[n // 2]) / 2
    mean_signed = sum(errs) / len(errs)
    # 系統性偏差判定：平均帶符號誤差明顯偏離0，代表估計法一致地高估或低估
    if mean_signed > 3:
        bias = "系統性高估"
    elif mean_signed < -3:
        bias = "系統性低估"
    else:
        bias = "無明顯系統性偏差"
    return {
        'count': len(errs),
        'mean_abs_err': round(sum(abs_errs) / len(abs_errs), 2),
        'median_abs_err': round(median_abs, 2),
        'mean_signed_err': round(mean_signed, 2),
        'bias': bias,
        'within_10pct': round(100.0 * sum(1 for e in abs_errs if e <= 10) / len(abs_errs), 1),
    }


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


def build_rotation_advice(rows):
    """
    【V160 延伸1】把熱力圖數字轉成「所以我該往哪找股票」的結論。
    判讀標準寫死並公開，讓你知道建議怎麼來的，不是黑箱。

    【V160 新增：雙引擎族群透視】加入「平均數 vs 中位數」夾擊判讀——
    均值代表極端爆發力(少數飆股拉動)，中位數代表產業普及率(過半數公司的
    真實狀況)。兩者一起看能戳破「假族群起漲」：均值很高但中位數很低，
    代表只有少數龍頭在漲、底層公司其實沒跟上。

    只有 rev_sample_count 有值(該產業至少5檔有YoY資料)的產業才會套用這三條
    規則——樣本不足的產業，均值/中位數本身就不可信，套用判讀規則只會產生
    誤導性的結論，不如不判讀。

    【優先順序，總指揮官確認過】「衰退偽裝」比「龍頭領漲」優先檢查——
    這兩條規則在數學上會重疊(龍頭領漲要求median<5%，衰退偽裝要求median<0%，
    median<0必然也<5)，衰退偽裝是風險警告，蓋過樂觀解讀比較安全，
    不能讓兩條同時成立時系統只顯示比較好聽的那個。
    """
    if not rows:
        return ["資料不足，無法判讀族群輪動。"]
    out = []
    strong = [r for r in rows if r['5日%'] is not None and r['5日%'] > 2]
    weak = [r for r in rows if r['5日%'] is not None and r['5日%'] < -2]
    # 短期轉強：5日明顯強於20日 → 資金剛開始流入，屬於「起漲」型態
    turning = [r for r in rows
               if r['5日%'] is not None and r['20日%'] is not None
               and r['5日%'] > 1 and r['5日%'] > r['20日%']]

    if turning:
        names = "、".join(f"{r['產業']}({r['5日%']:+.1f}%)" for r in turning[:3])
        out.append(f"🚀 **資金剛流入（5日強於20日，起漲型態）**：{names} "
                   f"—— 這類族群短期動能剛轉強，是選股優先掃描的方向。")
    if strong:
        names = "、".join(f"{r['產業']}({r['5日%']:+.1f}%)" for r in strong[:3])
        out.append(f"🔥 **近5日最強族群**：{names} —— 順勢做多優先在這裡面找。")
    if weak:
        names = "、".join(f"{r['產業']}({r['5日%']:+.1f}%)" for r in weak[:3])
        out.append(f"🔵 **近5日最弱族群**：{names} —— 做多要避開；如果你做空，這裡是主戰場。")
    if not strong and not weak:
        out.append("⚖️ 各產業近5日漲跌都在 ±2% 內，沒有明顯的族群輪動，"
                   "這種盤選股要更依賴個股本身的訊號，族群過濾幫助有限。")

    # 【R95新增】資金佔比＋動能組合訊號——單看動能，小池子噴出5%跟真正
    # 主力大金流噴出5%看起來一樣強，這裡疊上資金佔比(今天成交值佔全市場
    # 比例)。門檻：資金佔比>=5%且5日%>2%，合理但主觀的起始值。
    combo = [r for r in rows
             if r.get('資金佔比%') is not None and r['資金佔比%'] >= 5
             and r['5日%'] is not None and r['5日%'] > 2]
    if combo:
        combo_sorted = sorted(combo, key=lambda r: r['資金佔比%'], reverse=True)
        names = "、".join(f"{r['產業']}(資金佔比{r['資金佔比%']:.1f}%／5日{r['5日%']:+.1f}%)"
                         for r in combo_sorted[:3])
        out.append(f"💰 **資金重兵＋動能雙強**：{names} —— 這不只是噴出來的小池子，"
                   f"是真正有大量資金駐紮、同時動能也轉強的族群，比單看5日%的訊號更有份量。")

    # 【V160 新增】平均vs中位數夾擊判讀——只對有足夠營收樣本的產業套用
    for r in rows:
        if r.get('rev_sample_count') is None:
            continue
        d5, mean, median = r['5日%'], r.get('yoy_mean'), r.get('yoy_median')
        if d5 is None or mean is None or median is None or d5 <= 1.5:
            continue
        if median < 0:
            out.append(f"⚠️ **衰退偽裝族群：{r['產業']}**（5日{d5:+.1f}%，"
                       f"營收YoY均值{mean:+.1f}%／中位數{median:+.1f}%）—— "
                       f"熱錢正在炒作，但過半數公司營收處於衰退。此為純籌碼資金戰，"
                       f"隨時有獲利了結崩盤風險，操作需嚴守技術面停損，見好就收。")
        elif mean > 15 and median < 5:
            out.append(f"🚀 **龍頭領漲族群：{r['產業']}**（5日{d5:+.1f}%，"
                       f"營收YoY均值{mean:+.1f}%／中位數{median:+.1f}%）—— "
                       f"少數極端飆股拉動整個產業，底層半數公司其實未見成長。"
                       f"操作必須『強者恆強只買龍頭』，切忌盲目追價同族群的無基之彈跟風股。")
        elif median > 10 and mean > 10:
            out.append(f"🌟 **全面繁榮族群：{r['產業']}**（5日{d5:+.1f}%，"
                       f"營收YoY均值{mean:+.1f}%／中位數{median:+.1f}%）—— "
                       f"資金湧入且過半數公司營收強勁。產業雨露均霑，不僅龍頭強勢，"
                       f"佈局二線落後補漲股也具備基本面保護傘。")

    out.append("＿＿＿\n提醒：這是「同產業分類」的族群強弱，不是真正的供應鏈上下游關聯；"
               "且統計只涵蓋本次掃描池內的股票，不是全市場普查。營收YoY統計來自"
               "最近一次全市場掃描，樣本<5檔的產業不顯示營收判讀。")
    return out


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
    _fm_hist = fetch_finmind_stock_price(symbol)
    if _fm_hist is not None and len(_fm_hist) > 20:
        try:
            info = {}   # FinMind沒有等同yfinance .info的公司基本資料，留空
            # 保留跟yfinance路徑一致的函式名稱參與快取key，但這裡直接回傳FinMind結果
            return _fm_hist.tail(120), info
        except Exception:
            pass   # 理論上不會走到這裡，防禦性保留，失敗就繼續往下試yfinance

    # 【V160關鍵修復】原本沒有@st.cache_data，每次互動都對yfinance重打
    # 網路請求，是「開機要等5分鐘」的根因。加ttl=180快取+記住上次成功格式。
    _hint = _EXT_HINT.get(symbol)
    _ext_order = [_hint] + [e for e in (".TW", ".TWO") if e != _hint] if _hint else [".TW", ".TWO"]

    # 【R96調整】拿掉「有無session」這層重試——兩者面對同一個Yahoo端點/
    # 同一個對外IP，重試成功率極低，等於雙倍時間換極低額外成功率。只保留
    # 「兩種副檔名」(.TW/.TWO)這個真正有意義的差異，單檔最壞等待時間
    # 從16秒降到8秒。
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
            return hist.tail(120), info
        except Exception:
            continue
    return None, {}


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


def calc_rsi(df, period=14):
    delta = df['Close'].diff()
    gain = delta.where(delta > 0, 0).rolling(period).mean()
    loss = -delta.where(delta < 0, 0).rolling(period).mean()
    rs = gain / (loss + 1e-9)
    return 100 - (100 / (1 + rs))


def calc_bias(df, period=20):
    ma = df['Close'].rolling(period).mean()
    return (df['Close'] - ma) / (ma + 1e-9) * 100


# 【V160 Round39】calculate_atr/detect_k_line_patterns_v152/build_trade_
# zones已搬進warroom_core.py，直接import，跟排程端共用同一套邏輯。


# ==============================================================================
# 五、【任務二】法人連續買賣超真實成本 (VWAP) + 估價模型
# ==============================================================================
def calc_inst_streak_vwap(inst_df, hist, col='foreign_buy'):
    """
    從最新一日往回推，找出同方向的「連續買超（或賣超）」區間，
    以該期間每日『典型價 (H+L+C)/3』對法人自身張數加權，算出真實持有成本。
    回傳 None 表示資料不足。
    """
    if inst_df is None or inst_df.empty or hist is None or len(hist) == 0:
        return None

    price_map = {}
    for idx, row in hist.iterrows():
        try:
            d = idx.strftime('%Y-%m-%d')
        except Exception:
            continue
        price_map[d] = (float(row['High']) + float(row['Low']) + float(row['Close'])) / 3.0

    df = inst_df.sort_values('date', ascending=False)
    rows, sign = [], 0
    for _, r in df.iterrows():
        v = safe_float(r.get(col, 0))
        if v == 0:
            break                      # 買賣超為 0 視為斷點
        s = 1 if v > 0 else -1
        if sign == 0:
            sign = s
        elif s != sign:
            break                      # 方向翻轉 → 連續區間結束
        d = str(r['date'])
        p = price_map.get(d)
        if p is None:
            break                      # 找不到對應價格，寧可停止也不亂估
        rows.append((v, p))

    if not rows:
        return None
    total_lots = sum(abs(v) for v, _ in rows)
    if total_lots <= 0:
        return None
    vwap = sum(abs(v) * p for v, p in rows) / total_lots
    net = sum(v for v, _ in rows)
    # 【R76修復】標籤寫「賣超」但數字是正的，兩者矛盾。改成標籤直接讀
    # net自己的正負號，不管迴圈裡的sign變數對不對，畫面上永遠保證一致。
    return {'side': '買超' if net > 0 else '賣超', 'sign': (1 if net > 0 else -1),
            'days': len(rows), 'lots': int(round(net)), 'vwap': round(vwap, 2)}


def build_valuation(info, curr_price, rev_yoy, f_5d, cash_div, pe_hist_df=None):
    """
    【V157 升級】戰情室專屬估價模型。
    - 有足夠歷史 PE 樣本（>=60筆）時：用「現在 PE 的歷史百分位」評分，
      並用 25/50/75 百分位 × EPS 算出便宜價／合理價／樂觀價。
    - 樣本不足時（新股、資料源沒有）：退回 V156 的固定倍數，並標記 pe_hist_ok=False，
      UI 端會提示「样本不足，退回估算」，不會假裝有精確依據。
    - 殖利率防守價：現金股利 ÷ 目標殖利率（不變）。
    - 地雷：PE 落在自身歷史最貴 20% 區間（或樣本不足時 PE > 30）且營收衰退且法人賣超。
    """
    # 【R96修復，重大bug：PE估價系統性失效，見開發歷程.md】原本EPS只有
    # yfinance一個來源，FinMind成功時info是空字典導致eps永遠0。改成缺值
    # 時退回用pe_hist_df反推(現價÷最新PER)。
    eps = safe_float(info.get('trailingEps', 0)) if info else 0.0
    if eps <= 0 and pe_hist_df is not None and not pe_hist_df.empty and 'PER' in pe_hist_df.columns:
        try:
            _per_df = pe_hist_df.dropna(subset=['PER'])
            _per_df = _per_df[_per_df['PER'] > 0]
            if 'date' in _per_df.columns:
                _per_df = _per_df.sort_values('date')
            if not _per_df.empty and curr_price > 0:
                _latest_per = float(_per_df['PER'].iloc[-1])
                if _latest_per > 0:
                    eps = round(curr_price / _latest_per, 2)
        except Exception:
            pass   # 反推失敗就維持eps=0，呼叫端原本就有「無正EPS」的正確退回行為
    pe = round(curr_price / eps, 1) if eps > 0 and curr_price > 0 else 0.0

    percentile = None
    pe_p25 = pe_p50 = pe_p75 = 0.0
    fair_price = dream_price = cheap_price = 0.0
    pe_hist_ok = False

    valid_pe = None
    if pe_hist_df is not None and not pe_hist_df.empty and 'PER' in pe_hist_df.columns:
        valid_pe = pe_hist_df['PER'].dropna()
        valid_pe = valid_pe[valid_pe > 0]

    if valid_pe is not None and len(valid_pe) >= 60:
        pe_hist_ok = True
        pe_p25 = round(float(valid_pe.quantile(0.25)), 1)
        pe_p50 = round(float(valid_pe.quantile(0.50)), 1)
        pe_p75 = round(float(valid_pe.quantile(0.75)), 1)
        if pe > 0:
            percentile = round(float((valid_pe < pe).mean() * 100), 1)
        if eps > 0:
            cheap_price = round(pe_p25 * eps, 2)
            fair_price = round(pe_p50 * eps, 2)
            dream_price = round(pe_p75 * eps, 2)
    elif eps > 0:
        fair_price = round(eps * PE_FAIR_MULT, 2)
        dream_price = round(eps * PE_DREAM_MULT, 2)

    def_price = round(cash_div / YIELD_DEF_RATE, 2) if cash_div > 0 else 0.0

    score = 40
    if percentile is not None:
        if percentile <= 20:   score += 30     # 現在的估值落在自己歷史最便宜兩成
        elif percentile <= 40: score += 18
        elif percentile <= 60: score += 5
        elif percentile <= 80: score -= 10
        else:                  score -= 20     # 落在自己歷史最貴兩成
    elif eps > 0:
        if pe <= 12:   score += 20
        elif pe <= 18: score += 10
        elif pe > PE_LANDMINE: score -= 12
    else:
        score -= 15                                   # 虧損或無 EPS 資料

    if rev_yoy is not None:
        if rev_yoy > 20:  score += 22
        elif rev_yoy > 0: score += 12
        elif rev_yoy < -10: score -= 18
        elif rev_yoy < 0:   score -= 10

    div_y = (cash_div / curr_price * 100) if curr_price > 0 else 0.0
    if div_y >= 4.5:  score += 15
    elif div_y >= 3.0: score += 8

    # 【V160修正】拿掉外資5日買超/賣超的加減分——籌碼面混進基本面價值分數
    # 會讓第一戰區結論不純粹，外資因子改由第三戰區獨立評分。地雷判定仍保留
    # f_5d，因為那是刻意設計的跨面向複合警訊。
    score = int(max(0, min(100, score)))

    is_expensive = (percentile is not None and percentile >= 80) or (percentile is None and eps > 0 and pe > PE_LANDMINE)
    landmine = bool(is_expensive and (rev_yoy is not None and rev_yoy < 0) and f_5d < 0)

    # 【V159 新增】PE百分位極端值提示：跟地雷警告不同，這裡不要求營收衰退或法人賣超，
    # 單純標示「現在的估值已經遠遠偏離自己過去3年的常態」，常見於重大題材重估
    # （例如被納入新供應鏈、合作題材發酵），不代表基本面轉差，只是提醒去對照消息面。
    pe_extreme = bool(percentile is not None and percentile >= 95)

    return {'eps': round(eps, 2), 'pe': pe, 'pe_percentile': percentile,
            'pe_p25': pe_p25, 'pe_p50': pe_p50, 'pe_p75': pe_p75, 'pe_hist_ok': pe_hist_ok,
            'fair_price': fair_price, 'dream_price': dream_price, 'cheap_price': cheap_price,
            'def_price': def_price, 'value_score': score, 'landmine': landmine,
            'pe_extreme': pe_extreme, 'div_y': round(div_y, 2)}


# 【V160 Round39】score_zone1_fundamental/score_zone2_technical/
# score_zone3_chips/_fmt_zone_summary已搬進warroom_core.py，直接import。


def calc_disposal_risk_proxy(hist, vol_ratio):
    """
    【V157 新增，簡化版風險提示，非官方模型】
    證交所實際的注意股／處置股判定，涉及證券交易法規約 9 項主法條、12 項副法條，
    且門檻依股價級距、上市／上櫃分別調整，本系統沒有能力也不打算重現完整規則。
    這裡只用市場最常被引用的「六個營業日累計漲跌幅 + 成交量異常倍增」作為粗略代理，
    純粹是「這檔股票最近激進程度已經到需要提高警覺」的提醒，不是精準預測，
    也不保證與官方公告一致，請勿單獨依賴此標籤做交易決策。
    """
    if hist is None or len(hist) < 7:
        return {'flag': False, 'level': 'none', 'six_day_gain': 0.0}
    close6 = float(hist['Close'].iloc[-7])
    close0 = float(hist['Close'].iloc[-1])
    six_day_gain = ((close0 - close6) / close6 * 100) if close6 > 0 else 0.0
    abs_gain = abs(six_day_gain)

    # 【R88新增】改讀可調整門檻，不再寫死數字——側欄「🎛️門檻參數調整」
    # 面板改的值，這裡會直接生效。
    _gain_high = get_threshold('six_day_gain_high')
    _gain_watch = get_threshold('six_day_gain_watch')
    _vol_surge = get_threshold('vol_ratio_surge')

    if abs_gain >= _gain_high or (abs_gain >= _gain_watch and vol_ratio >= _vol_surge):
        level = 'high'
    elif abs_gain >= _gain_watch or (abs_gain >= _gain_watch * 0.6 and vol_ratio >= _vol_surge * 0.9):
        level = 'watch'
    else:
        level = 'none'

    return {'flag': level != 'none', 'level': level, 'six_day_gain': round(six_day_gain, 1)}


@st.cache_data(ttl=3600, show_spinner=False)
def _get_cached_disposal_attention_lists():
    """
    【R79新增】真正對照官方公告的處置股/注意股判斷——這是calc_disposal_
    risk_proxy() docstring裡明講「本系統沒有能力也不打算重現完整規則」
    那句話的解方，不是重現規則，是直接查官方公布的名單。

    快取1小時——這三份官方清單一天內變化不大，不用每次渲染卡片都重打
    三次API。回傳(attention_list, disposal_twse_list, disposal_tpex_list)，
    任一份抓不到就是None，呼叫端(check_disposal_attention_status)會正確
    處理成「無法確認」而不是「確認沒有」。
    """
    return (fetch_twse_attention_stocks(), fetch_twse_disposal_stocks(),
            fetch_tpex_disposal_stocks())


def get_disposal_attention_badge(symbol):
    """
    【R79新增】給戰卡用的處置/注意股徽章——包一層，讓呼叫端不用自己管快取
    跟三份清單的組裝細節。回傳HTML片段字串，沒有警示或查不到資料時回傳
    空字串(不顯示任何東西，不是顯示"正常"這種容易被誤讀成"已確認安全"
    的訊息——查不到官方資料時，誠實的作法是不顯示，不是宣稱安全)。
    """
    try:
        _att, _disp_t, _disp_x = _get_cached_disposal_attention_lists()
        status = check_disposal_attention_status(symbol, _att, _disp_t, _disp_x)
    except Exception:
        return ""
    if status.get('disposal'):
        return (f"<span class='m-tooltip k-tag' style='background:#5a0d0d; color:#ff6b6b;'>"
               f"🚨 處置股<span class='m-tooltiptext'>{status.get('detail', '')}"
               f"（資料源：TWSE/TPEx官方公告，非本系統推測）</span></span>")
    if status.get('attention'):
        return (f"<span class='m-tooltip k-tag' style='background:#3d3510; color:#e6c34d;'>"
               f"⚠️ 注意股<span class='m-tooltiptext'>{status.get('detail', '')}"
               f"（資料源：TWSE官方公告，非本系統推測）</span></span>")
    return ""


# 【V160 Round39】determine_signal已搬進warroom_core.py，直接import。


# ==============================================================================
# 六、 核心訊號與戰區聚合
# ==============================================================================
def get_intraday_projection(vol_today):
    """
    【V157 新增】統一的「今日推估全天量」計算，讓總量列的量增縮判斷跟爆量比
    使用同一套基準，不再各算各的。
    回傳 (is_intraday, projected_vol_today, time_ratio)：
    - is_intraday=False 時，projected_vol_today 就是 vol_today 本身（已收盤或非交易日）。
    - time_ratio 過小（剛開盤）時的估算值波動很大，UI 端會加註警語，不單獨隱藏數字。

    【R96修復——重大bug】原本用datetime.now()（沒有指定時區），在Streamlit
    Cloud的UTC執行環境下，會把「現在UTC時間」誤當「現在台灣時間」直接跟
    09:00/13:30比較——台灣整段交易時段(09:00-13:30)換算成UTC是
    (01:00-05:30)，永遠落在這裡寫死的09:00門檻之前，導致「盤中量能推估」
    在真正的盤中時段反而一直誤判成「還沒開盤」，回傳projected_vol_today=0，
    這正是總指揮官反映「爆量比顯示0.0x、量縮-100%」這個離譜數字的根因。
    改用datetime.now(TAIPEI_TZ)明確取得正確時區的當下時間，不管執行環境
    系統時鐘是哪個時區，這裡都會拿到正確的台灣時間。
    """
    now = datetime.now(TAIPEI_TZ)
    if now.weekday() >= 5:
        return False, vol_today, 1.0
    start_time = datetime.combine(now.date(), dt_time(9, 0), tzinfo=TAIPEI_TZ)
    end_time = datetime.combine(now.date(), dt_time(13, 30), tzinfo=TAIPEI_TZ)
    if now < start_time:
        return True, 0.0, 0.0
    if now > end_time:
        return False, vol_today, 1.0
    elapsed_mins = (now - start_time).total_seconds() / 60.0
    time_ratio = max(0.05, elapsed_mins / 270.0)   # 下限 0.05，避免開盤瞬間除以極小值失真爆表
    projected = vol_today / time_ratio
    return True, projected, time_ratio


def get_time_weighted_vol_ratio(vol_today, vol_5ma):
    _, projected_vol, _ = get_intraday_projection(vol_today)
    return projected_vol / vol_5ma if vol_5ma > 0 else 0.0


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def fetch_day_trading_info(symbol):
    """
    【R63新增】查詢個股「現股當沖」資格——用FinMind的TaiwanStockDayTrading
    資料集，這是交易所官方認定的當沖標的名單，不是我們自己用波動猜的。
    這個資料集的「單檔查詢」模式是免費方案就能用的（跟其他多數FinMind資料集
    一樣，只有「一次拿全市場」的批次模式才需要付費方案），所以逐檔查詢可行，
    但這代表每張戰卡都要多打一次FinMind——快取6小時，同一天內同一檔只會真的
    打一次。

    【誠實的限制】這個資料集列出的是「當天有被列入當沖統計」的標的，如果
    查不到資料，可能是「這檔真的不能當沖」，也可能是「這幾天剛好都沒有當沖
    成交量、雖然有資格但沒被列進來」——兩者從API本身無法100%區分，所以查
    無資料時回傳None、不是False，呼叫端不該把「查無資料」講成「確定不能
    當沖」。BuyAfterSale欄位：'*'=暫停先賣後買(當日僅能先買後賣，仍可當沖)；
    'Y'或空白=先買後賣、先賣後買皆可。

    回傳 dict {'eligible': True, 'buy_after_sale': str, 'date': str,
    'day_trade_volume': float或None} 或 None。

    【R96新增】day_trade_volume——FinMind文件確認這個資料集本來就有
    Volume欄位（當沖成交量，單位：股），原本只取BuyAfterSale沒取這個
    欄位，這次補上供評估當沖佔比使用（累積清單第5項），不用新增任何
    API依賴，同一次查詢多拿一個欄位而已。
    """
    try:
        _start = (datetime.now() - timedelta(days=10)).strftime('%Y-%m-%d')
        payload = _finmind_get('https://api.finmindtrade.com/api/v4/data',
                               {'dataset': 'TaiwanStockDayTrading', 'data_id': symbol,
                                'start_date': _start}, max_retries=2, timeout=10)
        rows = payload.get('data', [])
        if not rows:
            return None
        latest = rows[-1]  # FinMind依日期升冪排列，最後一筆是最新
        return {'eligible': True, 'buy_after_sale': str(latest.get('BuyAfterSale', '') or ''),
                'date': latest.get('date', ''),
                'day_trade_volume': safe_float(latest.get('Volume')) if latest.get('Volume') is not None else None}
    except Exception as _e:
        print(f"[fetch_day_trading_info-診斷] 抓當沖資格失敗：{type(_e).__name__}: {_e}")
        return None


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
    # 【R96新增，累積清單第5項】當沖佔比+融資餘額籌碼濾網——依附件26。
    # 這兩個都要多打FinMind查詢，各自都有獨立try/except，任一個失敗不
    # 影響另一個或影響戰卡其他部分正常顯示。
    day_trader_ratio = None
    try:
        _dt_info = fetch_day_trading_info(symbol)
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
    )
    signal_bg = "#3a1515" if "攻擊" in signal_text else ("#153a20" if "防守" in signal_text else "#332b00")

    detected_patterns = detect_k_line_patterns_v152(hist, atr_val)
    disposal_risk = calc_disposal_risk_proxy(hist, vol_ratio)

    closes = hist['Close'].tail(7).tolist()
    while len(closes) < 7:
        closes.append(closes[-1] if closes else 0)
    bars, min_p, max_p = " ▂▃▄▅▆▇█", min(closes), max(closes)
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
            _day_trading = fetch_day_trading_info(symbol)
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
def _fmt_closing_strength(c):
    """
    【R96新增】收盤強弱代查的顯示區塊——策略框架圖「波段續抱資格三關·
    第三關」：收盤位置決定強弱。跟 _fmt_main_force_cost 同一種「單獨一小塊，
    抓不到就明講」風格，缺值時不畫這塊（正常不會缺值，因為 open/high/low/
    close 是戰卡運算最早期就一定會有的資料，這裡防呆純粹避免舊快取資料
    沒有這個欄位時整頁崩潰）。

    【R96追加】標籤加上滑動說明（跟PE等既有欄位同一套m-tooltip機制）——
    總指揮官反映光看「收高檔(100%)」這種數字，沒有上下文解釋，看不懂
    這個百分比代表什麼意思。
    """
    cs = c.get('closing_strength')
    if not cs:
        return ""
    color = {"strong": "#ff4d4d", "weak": "#00e676", "neutral": "#aaa"}.get(cs.get('verdict'), "#aaa")
    shadow_tag = (' <span style="color:#f1c40f; font-size:11px;">⚠️長上影</span>'
                  if cs.get('has_long_upper_shadow') else "")
    _tip = ("收盤價落在「當日最高價～最低價」區間裡的百分位：100%＝收在當日最高點，"
            "0%＝收在當日最低點。≥75%（前25%高檔區）→明天有戲；≤25%（後25%低檔區）"
            "→今天該走；其餘為中段區。")
    return (f'<div style="font-size:12px; border-top:1px dashed #444; padding-top:6px; '
            f'margin-top:6px; color:#aaa;">'
            f"<span class='m-tooltip'>📍 收盤強弱<span class='m-tooltiptext'>{_tip}</span></span>："
            f'<strong style="color:{color};">{cs.get("label")}（{cs.get("pct")}%）</strong>'
            f'{shadow_tag}<div style="font-size:11px; color:#888; margin-top:2px;">'
            f'{cs.get("detail")}</div></div>')


def _fmt_volume_followthrough(c):
    """
    【R96新增】量能達標代查的顯示區塊——策略框架圖「波段續抱資格三關·
    第二關」：股價創新高，成交量是否跟得上。跟 _fmt_closing_strength 同一種
    「單獨一小塊，抓不到就明講」風格。verdict='unknown'（找不到攻擊K棒基準）
    時只用灰色淡淡顯示一行提示，不用紅綠強調色，避免讓「沒有基準可比較」
    看起來像是某種警訊——那只是「還沒有夠格的攻擊K棒可以拿來比較」，跟
    weak（有基準、但量能真的不足）意義不同，顏色要分開。

    【R96追加】標籤加上滑動說明，理由跟 _fmt_closing_strength 一致。
    """
    vf = c.get('volume_followthrough')
    if not vf:
        return ""
    _tip = ("先找出近20個交易日內最近一根「攻擊K棒」（爆量收紅的起漲點），比較「今天成交量」"
            "占「攻擊K棒成交量」的百分比——但只有在今天創近20日新高時才判斷："
            "≥80%→量能達標，有新資金進場；<50%→量能不足，沒人願意高檔承接；"
            "沒創新高時這一關先不適用（不是不合格，是還沒輪到判斷）。")
    if vf.get('verdict') == 'unknown':
        return (f'<div style="font-size:11px; color:#666; border-top:1px dashed #444; '
                f'padding-top:6px; margin-top:6px;">'
                f"<span class='m-tooltip'>📊 量能達標<span class='m-tooltiptext'>{_tip}</span></span>："
                f'{vf.get("detail")}</div>')
    color = {"strong": "#ff4d4d", "weak": "#00e676", "neutral": "#aaa"}.get(vf.get('verdict'), "#aaa")
    _ratio_txt = f"{vf.get('ratio_pct')}%" if vf.get('ratio_pct') is not None else "—"
    return (f'<div style="font-size:12px; border-top:1px dashed #444; padding-top:6px; '
            f'margin-top:6px; color:#aaa;">'
            f"<span class='m-tooltip'>📊 量能達標<span class='m-tooltiptext'>{_tip}</span></span>："
            f'<strong style="color:{color};">{vf.get("label")}（{_ratio_txt}）</strong>'
            f'<div style="font-size:11px; color:#888; margin-top:2px;">'
            f'{vf.get("detail")}</div></div>')


def _fmt_pullback_health(c):
    """
    【R96新增】拉回體檢母關的顯示區塊——策略框架圖整合Step 3，合併新A-1
    (盤中)/新B-1(波段)。跟前兩關（收盤強弱/量能達標）同一種顯示風格。
    verdict='unknown'時（找不到攻擊基準，或攻擊K棒本身就是最新一根、
    還沒有拉回可以體檢）同樣用灰色淡淡顯示，不用紅綠強調色，理由跟
    _fmt_volume_followthrough一致。

    【R96追加】標籤加上滑動說明，理由跟前兩關一致。這裡固定用swing模式的
    說明文字（目前戰卡日線版只跑swing模式），intraday模式的說明留給之後
    5分K版本的顯示函式另外處理，不在這裡混講兩種模式增加混淆。
    """
    ph = c.get('pullback_health')
    if not ph:
        return ""
    _tip = ("先找出近20個交易日內最近一根「攻擊K棒」，以那根K棒本身的最高價～最低價"
            "為0%~100%的參考範圍，看現在的價格拉回到這個範圍的第幾%位置（超過100%代表"
            "現在價格已經比攻擊K棒當時的最高點還高）：≥50%（守住一半以上）→續抱合格；"
            "<33%（跌破三分之一）或跌破攻擊K棒最低點（起漲點）→出場訊號。")
    if ph.get('verdict') == 'unknown':
        return (f'<div style="font-size:11px; color:#666; border-top:1px dashed #444; '
                f'padding-top:6px; margin-top:6px;">'
                f"<span class='m-tooltip'>🔄 拉回體檢<span class='m-tooltiptext'>{_tip}</span></span>："
                f'{ph.get("detail")}</div>')
    color = {"strong": "#ff4d4d", "weak": "#00e676", "neutral": "#aaa"}.get(ph.get('verdict'), "#aaa")
    _price_txt = f"{ph.get('price_pct')}%位置" if ph.get('price_pct') is not None else "—"
    return (f'<div style="font-size:12px; border-top:1px dashed #444; padding-top:6px; '
            f'margin-top:6px; color:#aaa;">'
            f"<span class='m-tooltip'>🔄 拉回體檢<span class='m-tooltiptext'>{_tip}</span></span>："
            f'<strong style="color:{color};">{ph.get("label")}（{_price_txt}）</strong>'
            f'<div style="font-size:11px; color:#888; margin-top:2px;">'
            f'{ph.get("detail")}</div></div>')


def _fmt_rebound_health(c):
    """
    【R96新增】反彈健康度的顯示區塊——累積清單第6項，依批次五分析修正版：
    急殺當下量大是正常生理反應，真正的判斷點在「反彈階段」的量。跟
    _fmt_pullback_health是對稱的一組（一個看多頭攻擊後拉回，一個看空頭
    急殺後反彈），顯示風格一致。
    """
    rh = c.get('rebound_health')
    if not rh:
        return ""
    _tip = ("先找出近20個交易日內最近一根「急殺K棒」（爆量收黑），比較之後反彈階段的"
            "平均量 ÷ 急殺當天的量：<70%（反彈量縮）→賣壓在減輕，虛跌可以等；"
            "≥100%（反彈量增）但股價彈不回去→賣壓沒減輕，有人趁反彈倒貨，該走就走。")
    if rh.get('verdict') == 'unknown':
        return (f'<div style="font-size:11px; color:#666; border-top:1px dashed #444; '
                f'padding-top:6px; margin-top:6px;">'
                f"<span class='m-tooltip'>📉 反彈健康度<span class='m-tooltiptext'>{_tip}</span></span>："
                f'{rh.get("detail")}</div>')
    color = {"strong": "#ff4d4d", "weak": "#00e676", "neutral": "#aaa"}.get(rh.get('verdict'), "#aaa")
    _vt = f"{rh.get('vol_ratio_pct')}%" if rh.get('vol_ratio_pct') is not None else "—"
    return (f'<div style="font-size:12px; border-top:1px dashed #444; padding-top:6px; '
            f'margin-top:6px; color:#aaa;">'
            f"<span class='m-tooltip'>📉 反彈健康度<span class='m-tooltiptext'>{_tip}</span></span>："
            f'<strong style="color:{color};">{rh.get("label")}（{_vt}）</strong>'
            f'<div style="font-size:11px; color:#888; margin-top:2px;">'
            f'{rh.get("detail")}</div></div>')


def _fmt_trend_regime_tag(c):
    """
    【R96新增】趨勢/趨勢中休息/盤整三態徽章——總指揮官明確要求放在戰卡
    最前面（跟股票名稱同一行），因為這是「判斷框架」本身：同一個訊號
    在不同態下意義不同（例如RSI偏低，趨勢股是空頭佔優、盤整股是超賣
    機會、趨勢中休息是正常整理），要先知道現在是哪一態，後面看其他
    判斷才有正確的參考框架。跟k_tags同一種徽章樣式，掛在同一行。

    trend_regime為None時（均線缺值，通常是上市時間太短不足60日均線）
    回傳空字串，不畫這個徽章——沒有足夠資料時，不該顯示一個看似確定
    的分類。
    """
    regime = c.get('trend_regime')
    if not regime:
        return ""
    _cfg = {
        'trending': ("🚀 趨勢股", "#4a1515", "#ff8080",
                     "MA5/20/60分散(未糾結)，有明確趨勢方向，RSI判斷用動能追蹤版。"),
        'trend_resting': ("😴 趨勢中休息", "#4a3a10", "#f1c40f",
                          "均線短期糾結，但過去約半年內曾出現明顯漲幅、且還沒被大部分回吃，"
                          "研判是大趨勢中的健康整理，不是真的沒方向。RSI判斷用趨勢休息版"
                          "（比動能版保守、比均值回歸版謹慎）。"),
        'ranging': ("📦 區間盤整", "#1a3a4a", "#5ac8fa",
                    "均線糾結，且過去約半年內沒有出現明顯趨勢（或曾經有漲幅但已被大部分"
                    "回吃），研判是真正的區間震盪，RSI判斷用均值回歸版（高了留意回檔、"
                    "低了留意反彈）。"),
    }
    if regime not in _cfg:
        return ""
    label, bg, fg, tip = _cfg[regime]
    # 【R96修復】徽章位在卡片最頂端，加m-tooltip-down這個class覆蓋展開
    # 方向(往下展開)，避免說明文字被螢幕邊界切掉。m-tooltip本身要保留，
    # 兩個class要同時掛在同一個span上。
    return (f"<span class='m-tooltip m-tooltip-down k-tag' style='background:{bg}; color:{fg};'>{label}"
            f"<span class='m-tooltiptext'>{tip}</span></span>")


def _fmt_order_book_pressure(c):
    """
    【R96新增】五檔買盤結構的顯示區塊——策略框架圖整合Step 5（新A-3／
    附件38）。跟前三關同一種顯示風格。這一關資料只在盤中才會有（收盤後
    /非交易時段查不到掛單，attach_live_quotes那邊查不到即時報價時
    c.get('order_book')就會是None，這裡直接不畫這塊，不強行顯示過時的
    盤中資料）。
    """
    ob = c.get('order_book')
    if not ob:
        return ""
    # 【R96修復——文字沒跟上功能升級】原本這裡的tooltip還寫著「還沒做到
    # 成交是打在買價還是賣價...系統還沒接上」，但內外盤成交比率這個功能
    # 上幾輪已經做完了（見evaluate_order_book_pressure的outer_volume/
    # inner_volume參數）——總指揮官反映看到這個舊警語，會誤以為功能還沒
    # 做，這裡改成依data_completeness動態顯示正確的完整度說明，不再是
    # 寫死的「還沒接上」。
    if ob.get('data_completeness') == 'full':
        _tip = ("五檔委買（買方掛單）總張數 ÷ 五檔委賣（賣方掛單）總張數：≥1.5倍→買盤掛單墊高；"
                "≤0.67倍→賣盤掛單較重。同時已疊加外盤/內盤成交比率（tick rule逐筆分類）：買盤墊高"
                "+外盤成交為主=真買；買盤雖厚但內盤成交為主=疑似偷出貨，可信度較高的完整判斷。")
    else:
        _tip = ("五檔委買（買方掛單）總張數 ÷ 五檔委賣（賣方掛單）總張數：≥1.5倍→買盤掛單墊高；"
                "≤0.67倍→賣盤掛單較重；其餘為均衡。⚠️ 這次沒有拿到外盤/內盤成交比率資料"
                "（可能是今天5分K還沒收集到足夠資料、或尚未執行supabase_migration_r96_"
                "outer_inner_volume.sql），只做到「掛單厚不厚」，判斷還不完整，僅供參考。")
    if ob.get('verdict') == 'unknown':
        return (f'<div style="font-size:11px; color:#666; border-top:1px dashed #444; '
                f'padding-top:6px; margin-top:6px;">'
                f"<span class='m-tooltip'>📖 五檔買盤<span class='m-tooltiptext'>{_tip}</span></span>："
                f'{ob.get("detail")}</div>')
    color = {"strong": "#ff4d4d", "weak": "#00e676", "neutral": "#aaa"}.get(ob.get('verdict'), "#aaa")
    _ratio_txt = f"{ob.get('depth_ratio')}倍" if ob.get('depth_ratio') is not None else "—"
    _thicken_tag = ""
    if ob.get('is_thickening') is True:
        _thicken_tag = ' <span style="color:#f1c40f; font-size:11px;">📈買盤墊高中</span>'
    return (f'<div style="font-size:12px; border-top:1px dashed #444; padding-top:6px; '
            f'margin-top:6px; color:#aaa;">'
            f"<span class='m-tooltip'>📖 五檔買盤<span class='m-tooltiptext'>{_tip}</span></span>："
            f'<strong style="color:{color};">{ob.get("label")}（{_ratio_txt}）</strong>'
            f'{_thicken_tag}<div style="font-size:11px; color:#888; margin-top:2px;">'
            f'{ob.get("detail")}</div></div>')


def _fmt_today_liquidity(c):
    """
    【R96新增】今日流動性過濾器的顯示區塊——累積清單第9項。跟五檔買盤
    同一種顯示風格，資料只在有即時報價時才會有（attach_live_quotes
    查不到即時累計量時c.get('liquidity')就會是None，這裡直接不畫這塊）。
    """
    liq = c.get('liquidity')
    if not liq:
        return ""
    _tip = ("今天累計到目前為止的真實成交量 ÷ 近5日平均成交量：≥60%→流動性充足，"
            "可積極找標的；≤30%→量能清淡，滑價大，進場容易被磨損，建議觀望。")
    if liq.get('verdict') == 'unknown':
        return ""   # 資料不足時安靜不顯示，不強行畫一個灰色空白區塊
    color = {"adequate": "#ff4d4d", "thin": "#00e676", "moderate": "#aaa"}.get(liq.get('verdict'), "#aaa")
    _pct_txt = f"{liq.get('pct_of_avg')}%" if liq.get('pct_of_avg') is not None else "—"
    return (f'<div style="font-size:12px; border-top:1px dashed #444; padding-top:6px; '
            f'margin-top:6px; color:#aaa;">'
            f"<span class='m-tooltip'>💧 今日流動性<span class='m-tooltiptext'>{_tip}</span></span>："
            f'<strong style="color:{color};">{liq.get("label")}（{_pct_txt}）</strong>'
            f'<div style="font-size:11px; color:#888; margin-top:2px;">'
            f'{liq.get("detail")}</div></div>')


def _fmt_day_trader_and_margin(c):
    """
    【R96新增】當沖佔比+融資餘額籌碼濾網的顯示區塊——累積清單第5項，
    依附件26。兩個判斷合併在同一塊顯示（都屬於「市場情緒」這個主題），
    任一個沒有資料就只顯示有資料的那個，兩個都沒有就整塊不顯示。
    """
    dtr = c.get('day_trader_ratio')
    mgr = c.get('margin_regime')
    _parts = []

    if dtr and dtr.get('verdict') != 'unknown':
        _color = {"strong": "#ff4d4d", "weak": "#00e676", "neutral": "#aaa"}.get(dtr.get('verdict'), "#aaa")
        _parts.append(f'<div>當沖佔比：<strong style="color:{_color};">{dtr.get("label")}'
                      f'（{dtr.get("ratio_pct")}%）</strong></div>')
    if mgr and mgr.get('verdict') != 'unknown':
        _color = {"strong": "#ff4d4d", "weak": "#00e676", "neutral": "#aaa"}.get(mgr.get('verdict'), "#aaa")
        _parts.append(f'<div>融資水位：<strong style="color:{_color};">{mgr.get("label")}</strong></div>')

    if not _parts:
        return ""

    _tip = ("依附件26：融資餘額低檔/下降+當沖佔比<30%=散戶還沒進場、情緒偏冷，續抱空間還在；"
            "融資餘額創高+當沖佔比>40%=散戶大量進場接盤、投機過熱，主力容易趁高檔出貨。")
    return (f'<div style="font-size:12px; border-top:1px dashed #444; padding-top:6px; '
            f'margin-top:6px; color:#aaa;">'
            f"<span class='m-tooltip'>🎯 籌碼情緒<span class='m-tooltiptext'>{_tip}</span></span>："
            f'<div style="font-size:11px; margin-top:2px;">{"".join(_parts)}</div></div>')


def _fmt_vwap_position(c):
    """
    【R96新增】VWAP位置的顯示區塊——累積清單第7項，Step 1收盤強弱的補充
    判斷角度（用均價線，不是用當日高低區間百分位）。只在有5分K資料時
    才會有值（attach_live_quotes批次查Supabase算出來的），沒有資料時
    安靜不顯示。
    """
    vp = c.get('vwap_position')
    if not vp or vp.get('verdict') == 'unknown':
        return ""
    color = {"strong": "#ff4d4d", "weak": "#00e676"}.get(vp.get('verdict'), "#aaa")
    _tip = ("用今天的5分K反推近似VWAP（成交量加權平均價），現價站上VWAP=多方守住，"
            "明天有機會延續；跌破VWAP=空方壓境，該注意風險。（依附件29「收盤前30分鐘的方向表態」，"
            "均價線是當天多空的分水嶺）")
    return (f'<div style="font-size:12px; border-top:1px dashed #444; padding-top:6px; '
            f'margin-top:6px; color:#aaa;">'
            f"<span class='m-tooltip'>📐 VWAP位置<span class='m-tooltiptext'>{_tip}</span></span>："
            f'<strong style="color:{color};">{vp.get("label")}（{vp.get("deviation_pct"):+.2f}%）</strong>'
            f'<div style="font-size:11px; color:#888; margin-top:2px;">VWAP≈{vp.get("vwap")}</div></div>')


def _fmt_daytrade_verdict_banner(c):
    """
    【R96新增】當沖建議橫幅——顯示evaluate_daytrade_recommendation()的
    綜合結論，跟波段建議橫幅並列但分開顯示、分開的顏色邏輯（不是同一套
    determine_signal()評分）。verdict值對應色彩沿用這個app既有的紅漲綠跌
    慣例：積極/偏多用紅色系，避開/否決用藍色系（呼應「🔵偏空防守」的
    既有配色），中性/資料不足用灰色。

    沒有daytrade_recommendation資料時（精簡路徑，fetch_intraday_extras=
    False，例如戰情速覽——雖然速覽根本不會呼叫這個函式，但持倉/雷達的
    完整卡片萬一這次沒查到當沖延伸資料，也不該顯示一個誤導的橫幅）
    完全不顯示，不留空白區塊或錯誤的「資料不足」大字報。
    """
    dr = c.get('daytrade_recommendation')
    if not dr or dr.get('verdict') == 'unknown':
        return ""

    _style = {
        'veto': ("#0d2b5c", "#2979ff", "🔵"),
        'avoid': ("#0d2b5c", "#2979ff", "🔵"),
        'watch_negative': ("#3a2f0d", "#f1c40f", "🟡"),
        'neutral': ("#2a2a2a", "#aaaaaa", "⚪"),
        'watch_positive': ("#3a2f0d", "#f1c40f", "🟡"),
        'aggressive': ("#5c1a0d", "#ff4d4d", "🔥"),
    }.get(dr['verdict'], ("#2a2a2a", "#aaaaaa", "⚪"))
    _bg, _color, _icon = _style

    _score_txt = f"分數 {dr['score']:+d}" if dr.get('score') is not None else ""
    _veto_note = f"（{dr['veto_reason']}）" if dr.get('veto_reason') else ""

    return (f'<div style="background:{_bg}; border:1px solid {_color}; border-radius:6px; '
            f'padding:10px 12px; margin-bottom:10px;">'
            f'<div style="font-size:10px; color:#888; margin-bottom:2px;">⚡ 當沖建議</div>'
            f'<div style="display:flex; justify-content:space-between; align-items:center;">'
            f'<span style="font-size:18px; font-weight:bold; color:{_color};">'
            f'{_icon} {dr["label"]}{_veto_note}</span>'
            f'<span style="font-size:11px; color:#888;">{_score_txt}</span></div>'
            f'<div style="font-size:12px; color:#ddd; margin-top:4px;">{dr.get("detail", "")}</div></div>')


def _fmt_daytrade_summary(c):
    """
    【R96架構調整】當沖摘要區——現在每次渲染完整戰卡都會呼叫（不再需要
    先切換「當沖模式」）。把當沖時效性最高的幾項資訊濃縮成單行、集中
    顯示在卡片價格區正下方——原本三大戰區完整保留在下面當詳細參考，
    這裡不刪減、不取代任何既有資訊，純粹是「加一個更快能看到重點的
    捷徑視窗」。

    這塊會顯示多少內容，取決於呼叫端在attach_live_quotes()有沒有傳
    fetch_intraday_extras=True：True時（查看單一檔完整戰卡、持倉/雷達
    區塊）VWAP跟9:30三關才會有資料；戰情速覽的精簡表格根本不會呼叫
    render_stock_card_ui()（那是表格不是完整卡片），所以這個函式也不
    會在那裡被呼叫，不用擔心速覽變慢。

    設計原則：每一項都用「有資料才顯示該行，沒資料完全不留空行」的方式
    處理，避免波段股票或非交易時段查看時，這塊變成一堆「資料不足」的
    灰色雜訊——寧可整塊看起來精簡，也不要塞滿等待中的提示佔版面。
    只有當「一項都沒有資料」時，才顯示一行極簡的等待提示，而不是完全
    不顯示這個區塊（讓使用者知道這是有在運作的功能、只是現在沒東西，
    不是功能故障）。
    """
    _rows = []

    # 9:30三關（查15）——排程算好、Supabase讀取的結果
    _gate = c.get('intraday_gate')
    if _gate and _gate.get('overall_verdict'):
        _gv = _gate['overall_verdict']
        _gcolor = {"pass": "#ff4d4d", "fail": "#00e676"}.get(_gv, "#888")
        _rows.append(f'<div>⏱️ 9:30三關：<strong style="color:{_gcolor};">'
                     f'{_gate.get("overall_label", "")}</strong></div>')

    # 五檔盤口（Step 5，本來就已經算好，這裡複用）
    ob = c.get('order_book')
    if ob and ob.get('verdict') not in (None, 'unknown'):
        _obcolor = {"strong": "#ff4d4d", "weak": "#00e676", "neutral": "#aaa"}.get(ob.get('verdict'), "#aaa")
        _ratio_txt = f"{ob.get('depth_ratio')}倍" if ob.get('depth_ratio') is not None else "—"
        _rows.append(f'<div>📖 五檔盤口：<strong style="color:{_obcolor};">'
                     f'{ob.get("label")}（{_ratio_txt}）</strong></div>')

    # VWAP位置（累積清單第7項，複用）
    vp = c.get('vwap_position')
    if vp and vp.get('verdict') != 'unknown':
        _vpcolor = {"strong": "#ff4d4d", "weak": "#00e676"}.get(vp.get('verdict'), "#aaa")
        _rows.append(f'<div>📐 VWAP：<strong style="color:{_vpcolor};">'
                     f'{vp.get("label")}（{vp.get("deviation_pct"):+.2f}%）</strong></div>')

    # 反彈健康度（累積清單第6項，複用——當沖更需要這種盤中急殺後的即時判斷）
    rh = c.get('rebound_health')
    if rh and rh.get('verdict') not in (None, 'unknown'):
        _rhcolor = {"strong": "#ff4d4d", "weak": "#00e676", "neutral": "#aaa"}.get(rh.get('verdict'), "#aaa")
        _rows.append(f'<div>📉 反彈健康度：<strong style="color:{_rhcolor};">'
                     f'{rh.get("label")}</strong></div>')

    # 今日流動性（累積清單第9項，複用——當沖尤其該避開清淡盤）
    liq = c.get('liquidity')
    if liq and liq.get('verdict') != 'unknown':
        _liqcolor = {"adequate": "#ff4d4d", "thin": "#00e676", "moderate": "#aaa"}.get(liq.get('verdict'), "#aaa")
        _rows.append(f'<div>💧 流動性：<strong style="color:{_liqcolor};">'
                     f'{liq.get("label")}</strong></div>')

    if not _rows:
        return ('<div style="background:#12161c; border:1px dashed #444; border-radius:6px; '
                'padding:8px 10px; margin-bottom:10px; font-size:12px; color:#666;">'
                '⚡ 當沖摘要：目前沒有可顯示的盤中資料（可能是非交易時段，或今天'
                '5分K資料還沒開始收集）。</div>')

    return (f'<div style="background:#12161c; border:1px solid #3a3f4a; border-radius:6px; '
            f'padding:8px 10px; margin-bottom:10px;">'
            f'<div style="font-size:12px; color:#f1c40f; font-weight:bold; margin-bottom:4px;">'
            f'⚡ 當沖摘要</div>'
            f'<div style="font-size:12px; color:#ddd; line-height:1.9;">{"".join(_rows)}</div></div>')


def _fmt_main_force_cost(c):
    """
    【V160 延伸2】主力成本免費替代估計的顯示區塊。

    刻意把三個數字分開列而不是合成一個「主力成本」：它們語意不同——
    VWAP20/60 是「整體市場平均成本」，爆量日均價才偏向「大資金成本」。
    合成一個數字會讓你無法判斷該信哪個，也無法跟籌碼K線對照校正。
    抓不到就明講「資料不足」，不填假數字。
    """
    mf = c.get('mf_cost')
    if not mf:
        return ('<div style="font-size:12px; color:#888; border-top:1px dashed #444; '
                'padding-top:6px; margin-top:6px;">📐 主力成本估計：股價資料不足，無法估算</div>')

    def _one(label, val, dev, tip):
        if val is None:
            return f'<span style="color:#666;">{label} —</span>'
        dev_color = "#ff4d4d" if (dev or 0) > 0 else ("#00c853" if (dev or 0) < 0 else "#888")
        dev_txt = f'<span style="color:{dev_color};">({dev:+.1f}%)</span>' if dev is not None else ""
        return (f"<span class='m-tooltip' style='color:#aaa;'>{label}"
                f"<span class='m-tooltiptext'>{tip}</span></span> "
                f"<strong style='color:#00d2ff;'>{val}</strong> {dev_txt}")

    parts = [
        _one("VWAP20", mf.get('vwap20'), mf.get('dev_vwap20'),
             "近20日成交量加權平均價＝短期市場平均成本。現價高於它代表短線持有者平均在賺。"),
        _one("VWAP60", mf.get('vwap60'), mf.get('dev_vwap60'),
             "近60日成交量加權平均價＝中期市場平均成本，比VWAP20更能代表波段持有者的成本。"),
        _one(f"爆量均價({mf.get('heavy_days', 0)}日)", mf.get('heavy_vwap'), mf.get('dev_heavy'),
             "只取近60日成交量最大的25%個交易日算加權均價。大單進場通常伴隨爆量，"
             "所以這個數字比一般VWAP更偏向「大資金的成本」，是分點主力成本的免費近似。"),
    ]
    return (f'<div style="font-size:12px; border-top:1px dashed #444; padding-top:6px; '
            f'margin-top:6px; color:#aaa;">📐 <b style="color:#f1c40f;">主力成本估計</b>'
            f'<span style="color:#666;">（免費替代，非分點實際成本）</span><br>'
            f'{" ｜ ".join(parts)}</div>')


def _fmt_vwap(c, key, label, color):
    """把 VWAP 區塊壓成單行 HTML；無資料時明確顯示原因，不用 0 帶過。"""
    v = c.get(key)
    price = float(c.get('price', 0) or 0)
    tip = ("<span class='m-tooltiptext'>回推法人「連續同方向買/賣超」區間，以每日典型價(H+L+C)/3"
           "對法人張數加權，估算其真實平均成本。現價低於買超成本＝法人套牢，反彈易遇解套賣壓；"
           "現價高於買超成本＝法人有浮額獲利，拉抬意願較高。</span>")
    if not v:
        return (f"<div style='font-size:12px; color:#a8bccf;'>{label}: <span class='m-tooltip'>"
                f"— 需先同步近日籌碼{tip}</span></div>")
    dev = ((price - v['vwap']) / v['vwap'] * 100) if v['vwap'] > 0 else 0.0
    dev_c = "#ff4d4d" if dev > 0 else "#00FF00"
    # 【R96修復】原本這裡「連續N日(±X張)」的顏色用呼叫端傳進來的固定color參數
    # （外資固定紅、投信固定黃），不管實際是買超還是賣超都一樣——「連續賣超」
    # 顯示紅色，違反這個app「紅漲綠跌」的既有慣例（賣超是偏空訊號，該用綠色）。
    # 改成依v['side']判斷：買超用紅、賣超用綠，不再用呼叫端傳入的color（那個
    # 參數保留給呼叫端未來若有其他用途，這裡先不用它決定這個顏色）。
    _side_color = "#ff4d4d" if v['side'] == '買超' else "#00c853"
    return (f"<div style='font-size:12px; color:#bbb;'><span class='m-tooltip'>{label}{tip}</span>: "
            f"連續{v['side']} <strong style='color:{_side_color};'>{v['days']}日 ({v['lots']:+,}張)</strong> | "
            f"成本 <strong style='color:#00d2ff;'>{v['vwap']:.2f}元</strong> | "
            f"現價乖離 <strong style='color:{dev_c};'>{dev:+.1f}%</strong></div>")


def render_stock_card_ui(c, is_portfolio=False, profit=0, roi=0, ent_p=0):
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
            warn_icon = "" if db_date == datetime.now().strftime("%Y-%m-%d") else f"<span class='m-tooltip'> ⚠️{tooltip_warn}</span>"
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
        (lambda _has_live=(c.get('live_price') is not None): (
            f"""<div style="font-size:13px; margin-top:6px; margin-bottom:-2px; """
            f"""color:{'#ff4d4d' if (c.get('live_change_pct') or 0) > 0 else ('#00e676' if (c.get('live_change_pct') or 0) < 0 else '#aaaaaa')};">"""
            f"""{'🔴' if (c.get('live_change_pct') or 0) > 0 else ('🟢' if (c.get('live_change_pct') or 0) < 0 else '⚪')} 即時更新"""
            + (f""" ・{'⏳' if c.get('live_is_carried') else ''}{c['live_time']}""" if c.get('live_time') else "")
            + f"""</div>"""
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
def _pick_col(cols, must_all, must_none=()):
    for c in cols:
        s = str(c)
        if all(k in s for k in must_all) and not any(k in s for k in must_none):
            return c
    return None


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


def fetch_margin_diff(code, token, target_date):
    """【新增】融資增減（張）。V155 的 margin 永遠是 0，導致查5/查10 永遠掃不到東西。"""
    url = 'https://api.finmindtrade.com/api/v4/data'
    start = (datetime.strptime(target_date, "%Y-%m-%d") - timedelta(days=10)).strftime('%Y-%m-%d')
    params = {'dataset': 'TaiwanStockMarginPurchaseShortSale', 'data_id': code,
              'start_date': start, 'end_date': target_date}
    if token:
        params['token'] = token
    try:
        payload = _finmind_get(url, params)
        df = pd.DataFrame(payload.get('data', [])).sort_values('date')
        if df.empty:
            return None
        last = df.iloc[-1]
        today_bal = safe_float(last.get('MarginPurchaseTodayBalance', 0))
        yest_bal = safe_float(last.get('MarginPurchaseYesterdayBalance', 0))
        return today_bal - yest_bal
    except FinMindAPIError as _e:
        print(f"[fetch_margin_diff-診斷] FinMind抓融資增減失敗：{type(_e).__name__}: {_e}")
        return None


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def fetch_margin_balance_history(code, token, target_date, lookback_days=20):
    """
    【R96新增，累積清單第5項】抓近lookback_days天的融資餘額序列，供
    evaluate_margin_balance_regime()判斷「今天餘額相對近期是高檔還是
    低檔」使用。跟fetch_margin_diff同一個資料集(TaiwanStockMarginPurchase
    ShortSale)，這裡只是多抓一段時間範圍、多取MarginPurchaseTodayBalance
    這個欄位（這個資料集本身就有，只是fetch_margin_diff只算了「今天-昨天」
    的差額，沒有把整段序列傳出去），不新增任何API依賴。

    掛6小時快取——融資餘額一天只公告一次，不用重複抓。

    回傳 (current_balance, balance_history) — current_balance是最新一筆，
    balance_history是「不含最新一筆」的近期序列（給evaluate_margin_
    balance_regime當比較基準用，不能把「今天」也混進「近期」裡比較，
    否則今天跟自己比較沒有意義）。抓不到資料時回傳(None, [])。
    """
    url = 'https://api.finmindtrade.com/api/v4/data'
    start = (datetime.strptime(target_date, "%Y-%m-%d") - timedelta(days=lookback_days + 15)).strftime('%Y-%m-%d')
    params = {'dataset': 'TaiwanStockMarginPurchaseShortSale', 'data_id': code,
              'start_date': start, 'end_date': target_date}
    if token:
        params['token'] = token
    try:
        payload = _finmind_get(url, params)
        df = pd.DataFrame(payload.get('data', [])).sort_values('date')
        if df.empty or 'MarginPurchaseTodayBalance' not in df.columns:
            return None, []
        balances = df['MarginPurchaseTodayBalance'].apply(safe_float).dropna().tolist()
        if not balances:
            return None, []
        current = balances[-1]
        history = balances[-(lookback_days + 1):-1] if len(balances) > 1 else []
        return current, history
    except FinMindAPIError as _e:
        print(f"[fetch_margin_balance_history-診斷] FinMind抓融資餘額歷史失敗：{type(_e).__name__}: {_e}")
        return None, []


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
NIM_FALLBACK_MODELS = [
    "deepseek-ai/deepseek-v3.2",
    "meta/llama-3.3-70b-instruct",
    "moonshotai/kimi-k2.5-instruct",
    "zai/glm-5.1",
    "qwen/qwen3-coder-480b",
]
# 偏好順序關鍵字：抓到 catalog 後，優先挑名字含這些關鍵字的聊天模型
NIM_PREFERRED_KEYWORDS = ["deepseek", "llama-3.3", "glm", "kimi", "qwen", "nemotron", "mistral"]


@st.cache_data(ttl=3600, show_spinner=False)
def discover_nim_models():
    """
    【V160】呼叫 NIM /v1/models 自動探索當前可用模型清單。
    成功回傳挑選後的模型ID list（依偏好排序），失敗回退靜態 fallback。
    快取1小時，避免每次推演都打一次。
    """
    if not NVIDIA_API_KEY:
        return NIM_FALLBACK_MODELS
    try:
        import requests as _rq
        resp = _rq.get("https://integrate.api.nvidia.com/v1/models",
                       headers={"Authorization": f"Bearer {NVIDIA_API_KEY}"}, timeout=8)
        if resp.status_code != 200:
            return NIM_FALLBACK_MODELS
        data = resp.json().get("data", [])
        all_ids = [m.get("id", "") for m in data if m.get("id")]
        if not all_ids:
            return NIM_FALLBACK_MODELS
        # 依偏好關鍵字挑選聊天型模型（排除embed/rerank/vision/ocr等非聊天模型）
        # 【V160修復】也排除純程式碼模型與小參數模型(避免擠掉真正能用的大模型)。
        exclude = ("embed", "rerank", "ocr", "vision", "riva", "bio", "diffusion", "guard",
                   "vila", "tts", "asr", "coder", "-1.5b", "-3b", "-6.7b", "-7b", "-8b")
        picked = []
        for kw in NIM_PREFERRED_KEYWORDS:
            for mid in all_ids:
                low = mid.lower()
                if kw in low and not any(x in low for x in exclude) and mid not in picked:
                    picked.append(mid)
        # 至少保底幾個；若挑不到就用 fallback
        return picked[:5] if picked else NIM_FALLBACK_MODELS
    except Exception:
        return NIM_FALLBACK_MODELS


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


def analyze_intel_image(image_bytes, mime_type='image/jpeg'):
    """
    【V160 新增】上傳截圖（例如股癌節目截圖、券商報告截圖）→ AI辨識圖片文字內容，
    填回情報注入面板的文字框，加快手動輸入的速度。

    刻意設計：這裡只做「圖片轉文字」，不讓AI在同一次呼叫裡順便判斷相關標的
    ——round29 的教訓是「AI一次做太多推理判斷」品質不穩定（總指揮官實測後
    回報摘要抓不到重點）。這次拆開：AI只負責它真正擅長的「看圖辨字」，
    辨識出來的文字填回文字框後，走的是既有、已經驗證過的規則比對＋人工
    確認多選框流程（round28），不是重新發明一套判斷邏輯。

    回傳 dict: {ok, error, text, model}
    """
    if not NVIDIA_API_KEY:
        return {'ok': False, 'error': '未配置 NVIDIA API 金鑰', 'text': None}

    b64 = base64.b64encode(image_bytes).decode('utf-8')
    data_url = f"data:{mime_type};base64,{b64}"
    client = OpenAI(base_url="https://integrate.api.nvidia.com/v1", api_key=NVIDIA_API_KEY)

    # 視覺模型跟純文字模型是分開的catalog，這裡用專門支援圖片輸入的模型，
    # 不能沿用 get_nim_models() 抓到的純文字模型清單
    vision_models = ["meta/llama-3.2-90b-vision-instruct", "meta/llama-3.2-11b-vision-instruct"]
    errors = []
    for model_id in vision_models:
        try:
            completion = client.chat.completions.create(
                model=model_id,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "請完整辨識這張圖片裡的所有文字內容（繁體中文），"
                                                 "原封不動照抄出來，不要摘要、不要省略、不要加自己的評論。"
                                                 "如果圖片裡有股票代號或公司名稱，務必逐字辨識準確。"},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                }],
                temperature=0.1, max_tokens=1500, timeout=60
            )
            text = completion.choices[0].message.content.strip()
            if text:
                return {'ok': True, 'error': None, 'text': text, 'model': model_id.split('/')[-1]}
            errors.append(f"{model_id.split('/')[-1]}: 回傳空白")
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
    return {'ok': False, 'error': "；".join(errors), 'text': None}


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


def execute_single_stock_ai(c):
    if not NVIDIA_API_KEY:
        return "未配置 NVIDIA API 金鑰"
    client = OpenAI(base_url="https://integrate.api.nvidia.com/v1", api_key=NVIDIA_API_KEY)
    bh = c.get('big_holder', 0)
    bh_str = f"{bh}%" if isinstance(bh, (int, float)) else str(bh)
    fv = c.get('f_vwap')
    fv_str = f"外資連續{fv['side']}{fv['days']}日，成本{fv['vwap']}元" if fv else "外資連續買賣超成本：無資料"
    yoy = c.get('rev_yoy')
    yoy_str = f"{yoy:.1f}%" if yoy is not None else "官方未公佈"

    prompt = (f"請以首席戰略幕僚身分，對 {c['name']} ({c['code']}) 進行冷血多空推演。"
              f"現價:{c['price']:.2f} | 漲跌:{c['gain']:.2f}% | 營收YoY:{yoy_str} | "
              f"PE:{c.get('pe')} | 價值分數:{c.get('value_score')} | 地雷:{'是' if c.get('landmine') else '否'} | "
              f"外資5日:{c['f_5d']:.0f}張 | {fv_str} | 大戶比例:{bh_str} | MACD:{c['macd_str']} | "
              f"防守線:{c.get('def_line')} | 移動停利:{c.get('trail_stop')}。"
              f"請分四段繁體輸出：【第一戰區財報估價小結】、【第二戰區技術面小結】、"
              f"【第三戰區籌碼成本小結】、【總指揮明日戰略總結】")
    errors = []
    for model_id in get_nim_models():
        try:
            completion = client.chat.completions.create(
                model=model_id,
                messages=[{"role": "system", "content": "你是一位冷血的台灣股市操盤幕僚。所有輸出嚴格使用繁體中文，並使用台灣金融專有名詞。直擊核心。"},
                          {"role": "user", "content": prompt}],
                temperature=0.2, max_tokens=1200, timeout=90
            )
            return f"【{model_id.split('/')[-1]} 提供分析】\n\n{completion.choices[0].message.content}"
        except Exception as e:
            # 【V160】分類錯誤，讓使用者知道是模型失效/限流/逾時，而不是籠統的「全面癱瘓」
            emsg = str(e).lower()
            short = model_id.split('/')[-1]
            if '404' in emsg or 'not found' in emsg or 'does not exist' in emsg:
                errors.append(f"{short}: 模型不存在(已下架)")
            elif '429' in emsg or 'rate' in emsg or 'quota' in emsg:
                errors.append(f"{short}: 限流/額度不足")
            elif 'timeout' in emsg or 'timed out' in emsg:
                errors.append(f"{short}: 連線逾時(90s)")
            else:
                errors.append(f"{short}: {str(e)[:40]}")
            continue
    return ("⚠️ NVIDIA 三個模型都無法使用，逐一狀態：\n- " + "\n- ".join(errors)
            + "\n\n若全是「模型不存在」，代表 NVIDIA NIM 上的模型ID已更新，需更換 NIM_MODELS 清單。")


# ==============================================================================
# 九之二、命中率回測引擎 (V158新增，V159擴充查1~查12完整濾網回測)
# ------------------------------------------------------------------------------
# 核心「無未來函數」骨架：用第i天收盤產生訊號，量測第i+3/i+10天未來報酬。
# 詳細範圍/簡化項目見開發歷程.md。evaluate_single_condition等已搬進
# warroom_core.py，這裡直接沿用import。
# ==============================================================================
def _backtest_one_stock(stock_code, years, atr_multiplier, enable_doomsday, twii_regime, token=""):
    """
    單一股票的訊號回測迴圈，回傳該股所有訊號日的明細 list[dict]。

    【V160 R42 修復】原本這裡完全沒有籌碼/營收資料——foreign_buy寫死0、
    landmine寫死False，代表R41新增的均線糾結+爆量/法人共振/法人持續性/
    營收動能四個因子，在回測時全部因為缺資料而不觸發，回測結果只反映了
    技術面因子。這裡改抓真實歷史籌碼(fetch_institutional_history)+營收
    (fetch_revenue_history_lagged)——這兩個函式是_filter_backtest_one_stock
    (查X條件回測)已經在用、驗證過disclosure-lag正確處理的既有函式，直接
    重用不重新造輪子。這樣R42的回測校準才是真的在測R41的完整評分邏輯，
    不是只測了一部分。

    【R66補上landmine】舊交接文件記錄的剩餘缺口：landmine需要PE百分位歷史，
    現在用fetch_pe_history取得，並在下方迴圈用「只看這個日期之前的PE值」
    算rolling百分位（避免用到未來資料、造成回測膨風的look-ahead bias）。
    樣本不足60筆時percentile維持None，改走下面R68補上的PE>30備援。
    【R68修復】上一輪誤判PE>30備援路徑「需要EPS歷史」，重查後發現判斷錯了：
    TaiwanStockPER資料集直接就有PER數值，pe_hist裡本來就有，不需要另外抓
    EPS反推。現在樣本不足60筆時會直接拿pe_hist當天的PE跟30比，跟即時版
    (calculate_signals_worker的is_expensive)完全對齊，不再有殘餘限制。
    """
    try:
        tk_obj = yf.Ticker(f"{stock_code}.TW", session=_SESSION)
        df = tk_obj.history(period=f"{years}y", auto_adjust=False, timeout=10)
        if df.empty:
            tk_obj = yf.Ticker(f"{stock_code}.TWO", session=_SESSION)
            df = tk_obj.history(period=f"{years}y", auto_adjust=False, timeout=10)
        df = df.dropna(subset=['Close'])
        if df.empty or len(df) < 40:
            return []
    except Exception:
        return []

    df = df.copy()
    df['MA5'] = df['Close'].rolling(5).mean()
    df['MA20'] = df['Close'].rolling(20).mean()
    df['MA60'] = df['Close'].rolling(60).mean()
    df['Vol_5MA'] = df['Volume'].rolling(5).mean()
    df['ATR'] = calculate_atr(df, 14)
    date_strs = df.index.strftime('%Y-%m-%d')

    # 【R42新增】真實歷史籌碼+營收，取代原本寫死的0/False
    inst_hist = fetch_institutional_history(stock_code, years, token)
    rev_hist = fetch_revenue_history_lagged(stock_code, years, token)
    # 【R66新增】重用fetch_pe_history多抓3年當rolling lookback緩衝——
    # 不多抓的話回測區間最前面幾年會因為歷史不足60筆永遠算不出百分位。
    pe_hist = None
    _pe_hist_df = fetch_pe_history(stock_code, token, years=years + 3)
    if _pe_hist_df is not None and not _pe_hist_df.empty and 'PER' in _pe_hist_df.columns:
        _s = _pe_hist_df.dropna(subset=['PER']).set_index('date')['PER']
        _s = _s[_s > 0].sort_index()
        pe_hist = _s if not _s.empty else None

    rows = []
    for i in range(20, len(df) - 10):
        curr_price = float(df['Close'].iloc[i])
        open_price = float(df['Open'].iloc[i])
        prev_price = float(df['Close'].iloc[i - 1])
        ma5 = float(df['MA5'].iloc[i])
        ma20 = float(df['MA20'].iloc[i])
        ma60_v = df['MA60'].iloc[i]
        ma60 = float(ma60_v) if pd.notna(ma60_v) else None
        vol_today = float(df['Volume'].iloc[i])
        vol_5ma = float(df['Vol_5MA'].iloc[i])
        atr = float(df['ATR'].iloc[i]) if pd.notna(df['ATR'].iloc[i]) else 0.0
        if pd.isna(ma5) or pd.isna(ma20) or pd.isna(vol_5ma) or vol_5ma <= 0:
            continue

        vol_ratio = vol_today / vol_5ma
        # 【修復】沿用正式版定義（開盤高於昨收、收盤低於今開），而非「單純收黑K」
        is_open_high_close_low = (open_price > prev_price) and (curr_price < open_price)

        def_line = ma5 - (atr * atr_multiplier)
        buffer_pct = ((curr_price - def_line) / curr_price) * 100 if curr_price > 0 else 0.0
        gain = ((curr_price - prev_price) / prev_price) * 100 if prev_price > 0 else 0.0

        market_bull = True
        if twii_regime is not None:
            d = date_strs[i]
            if d in twii_regime.index:
                market_bull = bool(twii_regime.loc[d])

        # 【R42新增】查當天的真實法人買賣超（單日），以及過去5/10日加總
        foreign_buy, trust_buy, f_5d, f_10d = 0.0, None, None, None
        if inst_hist is not None:
            _d = date_strs[i]
            if _d in inst_hist.index:
                foreign_buy = float(inst_hist.loc[_d].get('f_buy', 0.0) or 0.0)
                trust_buy = float(inst_hist.loc[_d].get('t_buy', 0.0) or 0.0)
            # 過去5/10個交易日的外資買超加總（用位置索引，不是日期索引，
            # 避免非交易日造成的視窗長度誤差）
            _window_dates = date_strs[max(0, i - 9): i + 1]
            _avail = inst_hist.reindex(_window_dates)['f_buy'].fillna(0.0) if not inst_hist.empty else None
            if _avail is not None and len(_avail) > 0:
                f_10d = float(_avail.sum())
                f_5d = float(_avail.tail(5).sum())

        rev_yoy, rev_mom = _lookup_lagged_revenue(rev_hist, df.index[i]) if rev_hist is not None else (None, None)

        # 【R66/R68】歷史PE百分位只用「回測日期之前」的PE值，避免用未來
        # 資訊判斷過去。PE>30備援路徑：TaiwanStockPER本身就給PER數值，
        # 不需要另外抓EPS歷史反推。
        pe_percentile = None
        _pe_raw = None
        if pe_hist is not None:
            _d = date_strs[i]
            if _d in pe_hist.index:
                _cur_pe = pe_hist.loc[_d]
                if isinstance(_cur_pe, pd.Series):  # 同一天理論上不會重複，防呆保留
                    _cur_pe = _cur_pe.iloc[-1]
                _pe_raw = float(_cur_pe)
                _window = pe_hist[pe_hist.index < _d]
                if len(_window) >= 60:
                    pe_percentile = round(float((_window < _pe_raw).mean() * 100), 1)
        is_expensive_hist = ((pe_percentile is not None and pe_percentile >= 80)
                             or (pe_percentile is None and _pe_raw is not None and _pe_raw > PE_LANDMINE))
        landmine_hist = bool(is_expensive_hist and (rev_yoy is not None and rev_yoy < 0)
                             and (f_5d is not None and f_5d < 0))

        signal_text, _, _, _ = determine_signal(
            curr_price, ma5, ma20, foreign_buy=foreign_buy, vol_ratio=vol_ratio,
            is_open_high_close_low=is_open_high_close_low, buffer_pct=buffer_pct,
            gain=gain, enable_doomsday=enable_doomsday, market_bull=market_bull, landmine=landmine_hist,
            ma60=ma60, trust_buy=trust_buy, foreign_buy_5d=f_5d, foreign_buy_10d=f_10d,
            rev_mom=rev_mom, rev_yoy=rev_yoy,
        )

        future_3d_ret = (float(df['Close'].iloc[i + 3]) - curr_price) / curr_price * 100 if curr_price > 0 else 0.0
        future_10d_ret = (float(df['Close'].iloc[i + 10]) - curr_price) / curr_price * 100 if curr_price > 0 else 0.0
        future_window = df.iloc[i + 1: i + 11]
        is_breached = bool((future_window['Low'] < def_line).any())

        rows.append({
            'stock': stock_code, 'date': date_strs[i], 'signal': signal_text,
            'future_3d_ret': round(future_3d_ret, 2), 'future_10d_ret': round(future_10d_ret, 2),
            'is_breached': is_breached
        })
    return rows




def run_signal_backtest(stock_list, years, atr_multiplier, enable_doomsday, use_market_regime,
                         progress_callback=None, max_workers=8, token=""):
    """
    批次回測引擎（多執行緒抓歷史資料，沿用掃描功能同一套並行模式）。
    回傳 (all_rows, summary_df)。

    【V160 R42】新增 token 參數，往下傳給 _backtest_one_stock 抓真實歷史
    籌碼+營收資料——沒有token也能跑(FinMind免費額度可用guest tier)，只是
    額度較低，多檔一起回測時容易碰到限流，建議有token時盡量帶。
    """
    twii_regime = fetch_twii_regime_history(years) if use_market_regime else None
    all_rows = []
    total = max(1, len(stock_list))

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_backtest_one_stock, code, years, atr_multiplier,
                                   enable_doomsday, twii_regime, token): code for code in stock_list}
        for i, future in enumerate(concurrent.futures.as_completed(futures)):
            if progress_callback:
                progress_callback(i + 1, total, futures[future])
            try:
                all_rows.extend(future.result())
            except Exception:
                continue

    if not all_rows:
        return all_rows, pd.DataFrame()

    res_df = pd.DataFrame(all_rows)
    summary_rows = []
    for sig in ["🔥 偏多攻擊", "🟡 觀察偏多", "⚖️ 中立震盪", "⚠️ 轉弱謹慎", "🔵 偏空防守"]:
        subset = res_df[res_df['signal'] == sig]
        count = len(subset)
        if count == 0:
            summary_rows.append({'訊號': sig, '樣本數': 0, '3日勝率%': None, '3日平均報酬%': None,
                                 '10日平均報酬%': None, '10日防守擊穿率%': None})
            continue
        win_rate_3d = (subset['future_3d_ret'] > 0).mean() * 100
        avg_ret_3d = subset['future_3d_ret'].mean()
        avg_ret_10d = subset['future_10d_ret'].mean()
        breach_rate = subset['is_breached'].mean() * 100
        summary_rows.append({
            '訊號': sig, '樣本數': count, '3日勝率%': round(win_rate_3d, 1),
            '3日平均報酬%': round(avg_ret_3d, 2), '10日平均報酬%': round(avg_ret_10d, 2),
            '10日防守擊穿率%': round(breach_rate, 1)
        })
    return all_rows, pd.DataFrame(summary_rows)


def save_backtest_run(stock_list, years, atr_multiplier, enable_doomsday, use_market_regime, all_rows):
    """把這次回測結果寫進 SQLite，永久保存，不用每次重開網頁就砍掉重測。"""
    with DB_LOCK:
        cur = SQLITE_CONN.execute('''
            INSERT INTO backtest_runs (run_time, stock_list, years, atr_multiplier,
                enable_doomsday, use_market_regime, sample_count, mode)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'technical')
        ''', (datetime.now(TAIPEI_TZ).strftime('%Y-%m-%d %H:%M'), ','.join(stock_list), years,
              atr_multiplier, int(enable_doomsday), int(use_market_regime), len(all_rows)))
        run_id = cur.lastrowid
        SQLITE_CONN.executemany('''
            INSERT INTO backtest_signals (run_id, stock, date, signal, future_3d_ret, future_10d_ret, is_breached)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', [(run_id, r['stock'], r['date'], r['signal'], r['future_3d_ret'],
               r['future_10d_ret'], int(r['is_breached'])) for r in all_rows])
        SQLITE_CONN.commit()
    return run_id


def list_backtest_runs(limit=20, mode=None):
    with DB_LOCK:
        try:
            if mode:
                return pd.read_sql(
                    'SELECT run_id, run_time, stock_list, years, atr_multiplier, enable_doomsday, '
                    'use_market_regime, sample_count, mode FROM backtest_runs WHERE mode=? '
                    'ORDER BY run_id DESC LIMIT ?', SQLITE_CONN, params=(mode, limit))
            return pd.read_sql(
                'SELECT run_id, run_time, stock_list, years, atr_multiplier, enable_doomsday, '
                'use_market_regime, sample_count, mode FROM backtest_runs ORDER BY run_id DESC LIMIT ?',
                SQLITE_CONN, params=(limit,))
        except Exception:
            return pd.DataFrame()


def get_all_traded_symbols():
    """
    【V160 新增】列出系統模擬倉裡「有交易紀錄」的全部標的（去重），供單檔績效查詢用選單挑選。

    總指揮官回報：要手動輸入代號才能查，但根本不知道有哪幾檔做過交易可以查。
    這裡回傳 (symbol, name, 筆數) 的清單，依最近進場日排序在前，方便找最近交易的標的。
    """
    def _do():
        return (SUPABASE_CONN.table("system_portfolio")
                .select("symbol,name,entry_date").execute())
    ok, res = _sb_safe(_do)
    rows = res.data if (ok and res is not None and getattr(res, "data", None)) else []
    latest_date, count = {}, {}
    for r in rows:
        sym = r.get('symbol')
        if not sym:
            continue
        count[sym] = count.get(sym, 0) + 1
        d = r.get('entry_date') or ''
        if d > latest_date.get(sym, ''):
            latest_date[sym] = d
    symbols = sorted(count.keys(), key=lambda s: latest_date.get(s, ''), reverse=True)
    return [(s, TW_STOCK_NAMES.get(s, s), count[s]) for s in symbols]


def get_symbol_performance(symbol):
    """
    【V160 新增】單檔績效查詢：這檔股票在系統模擬倉裡的所有進出紀錄與累計成績。

    總指揮官回報：績效表只有多空總計，看不到「某一檔到底幫我賺多少賠多少」。
    回傳 (已結算列表, 持倉中列表, 統計dict)。抓不到就回空，不編造數字。
    """
    def _do():
        return (SUPABASE_CONN.table("system_portfolio").select("*")
                .eq("symbol", str(symbol).strip()).execute())
    ok, res = _sb_safe(_do)
    rows = res.data if (ok and res is not None and getattr(res, "data", None)) else []
    closed = [r for r in rows if r.get('status') == 'closed']
    holding = [r for r in rows if r.get('status') in ('holding', 'pending')]
    wins = [r for r in closed if float(r.get('realized_pnl') or 0) > 0]
    total_pnl = sum(float(r.get('realized_pnl') or 0) for r in closed)
    stats = {
        'closed_count': len(closed),
        'holding_count': len(holding),
        'win_rate': round(100.0 * len(wins) / len(closed), 1) if closed else None,
        'total_pnl': round(total_pnl, 0),
        'avg_roi': round(sum(float(r.get('realized_roi') or 0) for r in closed) / len(closed), 2)
                   if closed else None,
    }
    return closed, holding, stats


def build_backtest_advice(summary_df):
    """
    【V160 新增】把回測數字轉成「所以我該怎麼做」的總結建議。

    總指揮官回報：回測跑完只給一張表，還要自己解讀。這裡直接把結論講白：
    哪個訊號值得照做、哪個訊號在這檔股票身上不準、樣本夠不夠。

    判讀標準（刻意寫死並公開，讓你知道建議是怎麼來的，不是黑箱）：
      勝率 ≥ 60% 且樣本 ≥ 10 → 值得照做
      勝率 45~60%           → 跟丟銅板差不多，需搭配其他條件
      勝率 < 45% 且樣本 ≥ 10 → 這檔在此訊號上反指標，反向思考
      樣本 < 10             → 樣本太少，不做結論（不是「不準」，是「不知道」）
    """
    if summary_df is None or summary_df.empty:
        return ["樣本不足，無法產生建議。"]

    good, bad, weak, thin = [], [], [], []
    for _, r in summary_df.iterrows():
        sig = r.get('訊號', '')
        n = int(r.get('樣本數', 0) or 0)
        wr = r.get('10日勝率%', r.get('3日勝率%'))
        if n < 10 or wr is None or (isinstance(wr, float) and pd.isna(wr)):
            thin.append(f"{sig}（樣本{n}）")
            continue
        wr = float(wr)
        if wr >= 60:
            good.append(f"{sig}：勝率 {wr:.0f}%／樣本 {n}")
        elif wr < 45:
            bad.append(f"{sig}：勝率 {wr:.0f}%／樣本 {n}")
        else:
            weak.append(f"{sig}：勝率 {wr:.0f}%／樣本 {n}")

    out = []
    if good:
        out.append("✅ **值得照做**：" + "；".join(good)
                   + " —— 這些訊號在這檔股票上歷史命中率夠高，出現時可提高信心。")
    if bad:
        out.append("🔄 **反指標**：" + "；".join(bad)
                   + " —— 勝率低於擲硬幣，這檔在此訊號出現時反而常走反向，別照做。")
    if weak:
        out.append("⚖️ **不具參考性**：" + "；".join(weak)
                   + " —— 接近隨機，單看這個訊號等於沒有優勢，必須搭配籌碼或大盤條件。")
    if thin:
        out.append("📭 **樣本不足**：" + "、".join(thin)
                   + " —— 樣本太少不下結論。這是「還不知道」，不是「不準」，可拉長回測年數再看。")
    if not (good or bad):
        out.append("⚠️ 整體結論：這檔股票沒有任何訊號達到可信賴的勝率水準，"
                   "代表它的走勢對這套技術訊號不敏感，建議別把它當主力標的。")
    out.append("＿＿＿\n提醒：以上只是這**單一檔股票**的歷史統計，"
               "不等於整體策略勝率，也不保證未來重現。要看策略整體表現請用「手動vs系統PK」。")
    return out


def load_backtest_summary(run_id):
    with DB_LOCK:
        try:
            df = pd.read_sql('SELECT * FROM backtest_signals WHERE run_id=?', SQLITE_CONN, params=(run_id,))
        except Exception:
            return pd.DataFrame()
    if df.empty:
        return df
    summary_rows = []
    for sig in ["🔥 偏多攻擊", "🟡 觀察偏多", "⚖️ 中立震盪", "⚠️ 轉弱謹慎", "🔵 偏空防守"]:
        subset = df[df['signal'] == sig]
        count = len(subset)
        if count == 0:
            continue
        summary_rows.append({
            '訊號': sig, '樣本數': count,
            '3日勝率%': round((subset['future_3d_ret'] > 0).mean() * 100, 1),
            '3日平均報酬%': round(subset['future_3d_ret'].mean(), 2),
            '10日平均報酬%': round(subset['future_10d_ret'].mean(), 2),
            '10日防守擊穿率%': round(subset['is_breached'].mean() * 100, 1)
        })
    return pd.DataFrame(summary_rows)


# ==============================================================================
# 九之三、查1~查12 完整濾網回測（V159新增，R86補上查3）
# ------------------------------------------------------------------------------
# 完整回測：查1,2,3,4,5,6,8,9,10,12。簡化版：查11(殖利率)用現在股利資料
# 回推歷史。不支援：情報雷達/黃金交叉(無歷史時間戳)。
# ==============================================================================
# 【R95】回測引擎四個函式已搬進warroom_core.py，這裡直接沿用import，
# DIVIDEND_DB/token改成呼叫端傳入。
def assess_filter_stability(walkforward_df):
    """
    【R77新增】把滾動驗證的結果，濃縮成「這個濾網穩不穩定」的判讀，不用
    自己盯著一堆數字猜。

    判讀邏輯：算每個濾網在所有窗口間命中率的標準差。標準差小＝各期間表現
    接近（穩定，是「高原區」）；標準差大＝某些期間好、某些期間差（不穩定，
    可能只是特定市場環境下的「孤峰」巧合，不是普遍有效的訊號）。

    這是簡單的統計判讀，不是複雜模型——標準差門檻(15/25個百分點)是合理但
    主觀的起始值，之後可以根據實際觀察到的分佈調整，不是寫死不能改的鐵律。

    回傳DataFrame[濾網條件, 窗口數, 命中率平均%, 命中率標準差, 穩定性判讀]，
    依命中率平均由高到低排序。
    """
    if walkforward_df.empty:
        return pd.DataFrame()
    out = []
    for f, grp in walkforward_df.groupby('濾網條件'):
        rates = grp['3日勝率%']
        n = len(rates)
        mean_rate = round(rates.mean(), 1)
        std_rate = round(rates.std(), 1) if n > 1 else None
        if n < 2:
            verdict = "⚪ 只有1個窗口，還無法判斷穩定性"
        elif std_rate is not None and std_rate < 15:
            verdict = "🟢 穩定（高原區，各期間表現接近）"
        elif std_rate is not None and std_rate < 25:
            verdict = "🟡 中等波動（部分期間效果較弱）"
        else:
            verdict = "🔴 高度不穩定（疑似孤峰，可能只在特定市況有效）"
        out.append({
            '濾網條件': f, '窗口數': n, '命中率平均%': mean_rate,
            '命中率標準差': std_rate if std_rate is not None else '—',
            '穩定性判讀': verdict,
        })
    return pd.DataFrame(out).sort_values('命中率平均%', ascending=False)


def save_filter_backtest_run(stock_list, years, all_rows):
    with DB_LOCK:
        cur = SQLITE_CONN.execute('''
            INSERT INTO backtest_runs (run_time, stock_list, years, sample_count, mode)
            VALUES (?, ?, ?, ?, 'filter')
        ''', (datetime.now(TAIPEI_TZ).strftime('%Y-%m-%d %H:%M'), ','.join(stock_list), years, len(all_rows)))
        run_id = cur.lastrowid
        SQLITE_CONN.executemany('''
            INSERT INTO backtest_signals (run_id, stock, date, future_3d_ret, future_10d_ret, filter_name)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', [(run_id, r['stock'], r['date'], r['future_3d_ret'], r['future_10d_ret'], r['filter'])
              for r in all_rows])
        SQLITE_CONN.commit()
    return run_id


def load_filter_backtest_summary(run_id):
    with DB_LOCK:
        try:
            df = pd.read_sql('SELECT * FROM backtest_signals WHERE run_id=?', SQLITE_CONN, params=(run_id,))
        except Exception:
            return pd.DataFrame()
    if df.empty or 'filter_name' not in df.columns:
        return pd.DataFrame()
    df = df.dropna(subset=['filter_name']).rename(columns={'filter_name': 'filter'})
    if df.empty:
        return df
    return summarize_filter_backtest(df.to_dict('records'))


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
        _tok = _chat = ""
        try:
            _tok = st.secrets.get("TELEGRAM_BOT_TOKEN", "") or ""
            _chat = st.secrets.get("TELEGRAM_CHAT_ID", "") or ""
        except Exception:
            pass
        if not _tok or not _chat:
            try:
                _rs = st.secrets.get("radar_secrets", {})
                _tok = _tok or _rs.get("TELEGRAM_BOT_TOKEN", "") or _rs.get("telegram_bot_token", "")
                _chat = _chat or _rs.get("TELEGRAM_CHAT_ID", "") or _rs.get("telegram_chat_id", "")
            except Exception:
                pass
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
.m-tooltip .m-tooltiptext { visibility: hidden; width: max-content; max-width: min(220px, 78vw); background-color: #333; color: #fff; text-align: left; border-radius: 6px; padding: 10px; position: absolute; z-index: 999; bottom: 125%; left: 0; transform: translateX(0); opacity: 0; transition: opacity 0.3s; font-size: 12px; font-weight: normal; line-height:1.6; overflow-wrap: break-word; word-break: break-word;}
.m-tooltip:hover .m-tooltiptext { visibility: visible; opacity: 1; }
/* 【R96新增】往下展開的說明框變體，只給卡片最頂端的徽章用（例如趨勢
   三態徽章）——共用的.m-tooltip往上展開(bottom:125%)，位在卡片最頂端
   的徽章上方常常沒有足夠空間，說明文字會被螢幕邊界切掉看不全。這裡
   新增一個往下展開的版本，不動到.m-tooltip本身，避免影響其他已經正常
   運作、有足夠上方空間的既有說明框。 */
.m-tooltip-down .m-tooltiptext { top: 125%; bottom: auto; }
</style>""", unsafe_allow_html=True)

# 【V160 第二階段】登入牆：未通過驗證前，擋住後續所有 UI（側邊欄、主畫面）
require_login()

with st.sidebar:
    st.markdown("<h2 style='color:#f1c40f; text-align:center;'>⚙️ 戰略控制台</h2>", unsafe_allow_html=True)

    # 【R96架構調整，見開發歷程.md】拿掉全域「波段/當沖模式」切換，改用
    # attach_live_quotes()的fetch_intraday_extras參數在各呼叫端明確控制。

    if st.button("🔄 強制重整畫面", use_container_width=True):
        st.session_state.last_refresh = time.time()
        st.rerun()

    # 【V160 新增】建置版本標記：確認雲端跑的到底是不是最新檔
    # 【R55修復】總指揮官反映側欄只要看版本號就好，不需要每次都攤開一大段
    # 說明——BUILD_NOTES改放進收合的expander，預設不顯示，需要回顧細節時自己展開。
    st.caption(f"🏷️ 建置版本：{BUILD_VERSION}")
    with st.expander("本版重點（詳細說明）", expanded=False):
        st.caption(BUILD_NOTES)

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

    # 【R82新增】診斷用——直接顯示程式實際讀到什麼(只顯示前後幾個字元+
    # 長度，不洩漏完整token)，不用互相猜測是哪裡出錯。
    with st.expander("🔍 診斷：程式實際讀到的GITHUB_TOKEN/GITHUB_REPO", expanded=False):
        # 【R84修復】改用_find_secret_anywhere——原本只查最外層+radar_
        # secrets，實際案例是值被歸類進[supabase]區塊，這個函式掃過所有
        # 區塊都找得到。
        _diag_token = _find_secret_anywhere("GITHUB_TOKEN")
        _diag_repo = _find_secret_anywhere("GITHUB_REPO")
        if _diag_token:
            st.caption(f"✅ GITHUB_TOKEN 讀到了，長度{len(_diag_token)}字元，"
                      f"開頭「{_diag_token[:6]}」結尾「{_diag_token[-4:]}」")
        else:
            st.caption("❌ GITHUB_TOKEN 完全沒讀到（空字串或不存在）")
        if _diag_repo:
            st.caption(f"✅ GITHUB_REPO 讀到了，內容是「{_diag_repo}」")
        else:
            st.caption("❌ GITHUB_REPO 完全沒讀到（空字串或不存在）")

        # 【R83新增】兩輪重打都讀不到，可能是被放進某個分類底下或存檔沒
        # 生效。直接列出st.secrets完整鍵值結構(只列名稱，不顯示密鑰內容)。
        st.markdown("**st.secrets 實際結構（只列欄位名稱，不含任何密鑰內容）**")
        try:
            _top_keys = list(st.secrets.keys())
            st.caption(f"最外層總共有 {len(_top_keys)} 個欄位：{_top_keys}")
            for _k in _top_keys:
                _v = st.secrets[_k]
                if hasattr(_v, 'keys'):  # 這個欄位底下還有子欄位（代表是一個分類區塊）
                    st.caption(f"　└「{_k}」是一個分類區塊，裡面有：{list(_v.keys())}")
        except Exception as _list_e:
            st.error(f"列出st.secrets結構時發生例外：{_list_e}")
        st.caption("看上面這份清單裡有沒有出現「GITHUB_TOKEN」「GITHUB_REPO」——"
                  "如果它們出現在某個分類區塊底下（不是在最外層那份清單裡），"
                  "代表存的時候不小心放進了[分類]底下，要移到最外層才讀得到；"
                  "如果整份清單裡完全找不到這兩個字，代表存檔沒有真的生效。")

        st.caption("如果這裡兩個都顯示❌，代表secrets真的沒被讀進來（格式問題或"
                  "還沒重啟生效）；如果這裡都顯示✅但按鈕還是失敗，代表token本身"
                  "權限不足或repo名稱不對，是不同的問題，把這個診斷區塊的截圖"
                  "給我就能確定是哪一種。")

    # 【R64新增，R96改成永遠開啟】定時喚醒——Streamlit Cloud容器閒置會被回收，
    # 背景ping減少被判定閒置的機會。純HEAD請求，不夾帶或恢復登入狀態，登出
    # 後iframe隨畫面移除，計時器不會在背景繼續跑。
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
        st.caption("每10分鐘背景ping一次，純粹讓容器保持運作，不會延長或恢復登入狀態。"
                  "登出後這個元件不會再被渲染，計時器隨畫面一起移除。")

    # 【V160 新增】FinMind 額度輪替狀態，讓「現在用第幾組帳號」看得見，
    # 不用猜是不是還卡在第一組（先前輪替根本沒接上，額度只有 600 而非 1500）
    with st.expander("🔑 FinMind 額度狀態", expanded=False):
        for _row in get_fm_quota_status():
            st.caption(_row)
        st.caption("額度鏈：帳號1(600) → 帳號2(600) → 訪客(300) = 1500/小時")

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
    st.markdown("<div style='font-size:12px; font-weight:bold;'>📡 盤中自動輪詢（陽春版）</div>", unsafe_allow_html=True)
    # 【R96調整】開啟自動輪詢、異常推播Telegram也不該是每次登入要記得
    # 勾選的UI開關，改成永遠開啟、固定3分鐘間隔，運作方式完全沒變。
    auto_poll_enabled = True
    poll_interval_min = 3
    st.caption(f"📡 自動輪詢已內建開啟，每{poll_interval_min}分鐘偵測一次雷達/持倉清單的價量異常"
              f"（依賴這個分頁開著才會運作，分頁關掉就不會繼續監控，這是Streamlit Cloud"
              f"免費版的既有限制，不是本次調整改變的）。")
    if auto_poll_enabled:
        # 【R96調整】異常推播Telegram永遠開啟，不再顯示checkbox。
        st.session_state["push_anomaly_telegram"] = True
        try:
            from streamlit_autorefresh import st_autorefresh
            st_autorefresh(interval=poll_interval_min * 60 * 1000, key="autorefresh_timer")
        except ImportError:
            st.caption("⚠️ 需先安裝 `streamlit-autorefresh` 套件才能自動重新整理；"
                       "沒裝的話請手動按重新整理來輪詢。")

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
    st.markdown(f"<div style='font-size:11px;'>{'🟢' if API_READY else '🔴'} NVIDIA NIM<br>"
                f"{'🟢' if FINMIND_READY else '🔴'} FinMind 線路<br>"
                f"{_sb_icon} Supabase 雲端大腦</div>", unsafe_allow_html=True)
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
                                 value=datetime.now().date(), key="tdcc_week_date")
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


# ==============================================================================
# 十一、 主畫面
# ==============================================================================
st.title("🚀 作戰室 正式版 v1.0")

# 【R96新增】時段自動選關(Step 4)——依台灣現在時間提示該看時間軸的哪一
# 關，只在主畫面頂部顯示一次。available=False老實標注「還沒接上」。
try:
    _gate_info = determine_active_intraday_gate()
    if _gate_info['gate'] not in ('closed',):
        _gate_color = "#00e676" if _gate_info['available'] else "#888"
        st.markdown(f'<div style="font-size:13px; color:#aaa; margin-bottom:8px;">'
                    f'⏱️ 當日續抱時間軸：<strong style="color:{_gate_color};">{_gate_info["label"]}</strong>'
                    f' —— {_gate_info["note"]}</div>', unsafe_allow_html=True)
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

# 【R96新增，「三關查詢」指令】掃描今天5分K三關(查15)判斷結果，只列出
# 「通過」的股票，沒通過或還在等資料的一律不顯示。直接查intraday_gate_
# results整張表篩verdict='pass'，這張表本來就只有持倉+雷達清單的資料。
with st.expander("🎯 9:30三關查詢（只列出通過的股票，10:00為最後檢查點）", expanded=False):
    if SUPABASE_CONN is None:
        st.caption("Supabase未連線，無法查詢三關結果。")
    else:
        try:
            _today_str_gate = get_current_or_last_trading_date()
            _gate_scan_res = (SUPABASE_CONN.table("intraday_gate_results")
                              .select("symbol,overall_verdict,overall_label,gate1_verdict,gate2_verdict,detail")
                              .eq("trade_date", _today_str_gate)
                              .eq("overall_verdict", "pass")
                              .execute())
            _passed_rows = _gate_scan_res.data or []
            if not _passed_rows:
                st.caption("目前沒有股票通過三關（可能是今天還沒到09:30，或今天沒有股票"
                          "同時通過第一、二關——這是正常情況，不代表查詢功能故障）。")
            else:
                _display_rows = []
                for r in _passed_rows:
                    _sym = r['symbol']
                    _display_rows.append({
                        '代號': _sym,
                        '名稱': TW_STOCK_NAMES.get(_sym, _sym),
                        '結論': r.get('overall_label', ''),
                        '第一關': r.get('gate1_verdict', '—'),
                        '第二關': r.get('gate2_verdict', '—') or '（資料不足）',
                    })
                st.dataframe(pd.DataFrame(_display_rows), use_container_width=True, hide_index=True)
                st.caption(f"共 {len(_display_rows)} 檔通過。第三關（拉回體檢）目前輪詢窗口到10:00，"
                          "資料量仍有限，這裡的「通過」只涵蓋第一、二關確認過的部分，"
                          "第三關結果請個別點開完整戰卡查看當沖摘要區。")
        except Exception as e:
            st.caption(f"查詢失敗：{e}（可能是尚未執行supabase_migration_r96_intraday_gate.sql建表）")

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
        _macro_chips.append(f"<span style='margin-right:14px;'><b>{_name}</b> {_val_fmt} "
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
    <div style='color:#ddd; font-size:13px;'>{''.join(_macro_chips)}</div>
</div>""", unsafe_allow_html=True)

# 【V160 B#11】速覽模式開關（放在標題正下方）
# 【R50修復】預設改成True——常態持倉/模擬倉區塊原本不管展開收合都會執行
# ThreadPoolExecutor平行運算，拖慢開機速度，改預設開速覽兼顧簡潔與速度。
st.checkbox("⚡ 速覽模式：所有標的（持倉+雷達+觀察）攤平成一張總表，5秒掃完全部",
            value=st.session_state.get('quick_overview_mode', True), key="quick_overview_mode")

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
                _cc = calculate_signals_worker(_picked_h['symbol'], config_payload)
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
            _pairs = [(str(h.get('symbol')), 'tse') for h in _open_raw if h.get('symbol')]
            _live_map = fetch_twse_mis_batch(_pairs) if _pairs else {}
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

        # 資金曲線 vs 大盤對照圖
        try:
            import plotly.graph_objects as go
            _ec = _metrics['equity_curve']
            _dates = [pt['date'] for pt in _ec]
            _strategy_ret = [pt['cum_return'] for pt in _ec]

            # 【R91修復】R67新增的「含未實現MDD」會在equity_curve最後多塞一筆
            # 偽日期標籤，跟大盤對照迴圈的pd.Timestamp()轉換衝突拋例外，拖垮
            # 整張圖表。修法：轉換失敗的項目直接跳過大盤對照，策略線照樣正常畫出。
            _twii_ret = None
            _real_dates = [d for d in _dates if d != '現在(含未實現)']
            if _real_dates:
                _twii_hist = _yf_ticker("^TWII").history(start=_real_dates[0], end=_real_dates[-1], timeout=8)
                if not _twii_hist.empty:
                    _twii_close = _twii_hist['Close']
                    _base = float(_twii_close.iloc[0])
                    _twii_ret_series = ((_twii_close - _base) / _base * 100)
                    # 用merge_asof概念對齊：每個策略交易日，找當時最新的大盤累積報酬率
                    _twii_ret = []
                    for d in _dates:
                        try:
                            _d_ts = pd.Timestamp(d)
                        except (ValueError, TypeError):
                            # 這個日期轉換不了(例如"現在(含未實現)"這種偽標籤)，
                            # 用目前為止最新一筆大盤報酬頂著，不讓整條線斷掉，
                            # 也不讓這一個點拖垮整段迴圈。
                            _twii_ret.append(_twii_ret[-1] if _twii_ret else 0.0)
                            continue
                        _eligible = _twii_ret_series[_twii_ret_series.index <= _d_ts]
                        _twii_ret.append(float(_eligible.iloc[-1]) if len(_eligible) else 0.0)

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

def save_rotation_cache(rot_rows, meta):
    """
    【R59新增】把族群輪動掃描結果存進Supabase system_config（跟其他系統設定
    共用同一張表），跨session/跨裝置/重新整理都能直接看到上次掃描結果，不用
    每次都重新燒一次FinMind/yfinance額度——這是總指揮官明確要求的：至少保留
    一天可看，不然每次重按都要重新花時間掃。存快取失敗不影響這次畫面顯示，
    只是代表下次得重新掃一次，不阻斷任何流程。
    """
    try:
        payload = json.dumps({'rows': rot_rows, 'meta': meta}, ensure_ascii=False)
        sb_set_config('rotation_scan_cache', payload, description='族群輪動熱力圖上次掃描結果快取（R59）')
    except Exception:
        pass


def load_rotation_cache():
    """讀回上次掃描結果；找不到或格式壞掉時回 (None, None)，呼叫端據此判斷要不要顯示。"""
    raw = sb_get_config('rotation_scan_cache')
    if not raw:
        return None, None
    try:
        data = json.loads(raw)
        return data.get('rows'), data.get('meta')
    except Exception:
        return None, None


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
                    st.dataframe(fb_summary, use_container_width=True, hide_index=True)
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

with st.expander("📋 情報注入面板", expanded=False):
    intel_source = st.selectbox("來源", ["股癌", "財經新聞", "法說會", "券商報告", "其他"], key="intel_source")
    intel_tag = st.text_input("標籤", key="intel_tag", placeholder="例如：財報公布、法人動向")
    # 【R88新增】補登過去日期的情報——原本永遠用「現在」當時間戳，導致
    # 算「情報準不準」的基準價抓錯。加日期選擇器，預設今天。
    intel_backdate = st.date_input("這則情報的日期（預設今天，補登舊資料時請改成正確日期）",
                                   value=datetime.now().date(), key="intel_backdate")

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
        st.error(f"⚠️ K線圖繪製失敗，不影響卡片其他部分：{_kline_e}")

    try:
        with st.expander("🏭 同產業族群強弱（簡化版，非供應鏈圖譜）", expanded=False):
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
        st.error(f"⚠️ 同產業族群面板發生錯誤，不影響卡片其他部分：{_peer_e}")

    # 【R76修復】展開區標題改明講內容涵蓋分點/同步，避免誤以為功能消失。
    # 【R78修復】整個展開區內容包成一個try/except——最後一道防線，避免
    # 任何未來新增的功能忘記加防呆時拖垮整張卡片。
    with st.expander("⚙️ 資料校正／單檔同步／分點分析／人工覆寫", expanded=False):
        try:
            if st.button("🚀 執行單檔精準同步 (籌碼+融資+大戶)", key=f"btn_sync_single_{code}{btn_suffix}",
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
            if st.button(f"🔄 立即補跑今天的{code}分點（FinMind優先，不等排程）",
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
                                'symbol': code, 'log_date': datetime.now().strftime('%Y-%m-%d'),
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
                            st.warning(f"寫入失敗：{_hs_e}")

            if st.session_state.get(f'histock_direct_failed_{code}'):
                if st.button("🔄 改用GitHub Actions觸發全市場分點抓取（較慢但不會被擋）",
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
                            _shares_out = fetch_shares_outstanding(code, get_active_fm_token())
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
                            value=datetime.now().date(), key=f"bf_date_{code}{btn_suffix}")
                        if st.button("💾 存入分點歷史（累積後可看連續性分析）",
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
                _b_cols = st.columns(5)
                _brokers = []
                for _i in range(5):
                    with _b_cols[_i]:
                        # 【V160 新增】券商名稱改用下拉選單，避免手打錯字（總指揮官回報的需求）。
                        # 清單外的分點選「其他（手動輸入）」，下面會多跳出一個輸入框，
                        # 不會因為不在清單裡就選不了。
                        _bpick = st.selectbox(f"券商{_i+1}", ["（未選擇）"] + COMMON_BROKER_BRANCHES
                                              + ["✏️ 其他（手動輸入）"],
                                              key=f"cal_bpick_{_i}_{code}{btn_suffix}")
                        if _bpick == "✏️ 其他（手動輸入）":
                            _bname = st.text_input("輸入券商/分點名稱", key=f"cal_bname_{_i}_{code}{btn_suffix}",
                                                   placeholder="例如 凱基-台中")
                        elif _bpick == "（未選擇）":
                            _bname = ""
                        else:
                            _bname = _bpick
                        _bprice = st.number_input(f"買均價", min_value=0.0, step=0.1, format="%.2f",
                                                  key=f"cal_bprice_{_i}_{code}{btn_suffix}")
                        # 【V160 R41 新增】買超張數——這是算籌碼集中度的分子(前5大買超
                        # 張數加總 ÷ 當日總成交量)，也是判斷「買超第一名是不是隔日沖
                        # 分點」需要的資料(要知道誰的張數最高才知道誰是第一名)。
                        _bshares = st.number_input(f"買超張數", min_value=0, step=1,
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

                    # 隔日沖警示：找出買超張數最高的那家，比對是否命中已知名單
                    _top_buyer = max(_brokers, key=lambda x: x[2]) if _brokers else None
                    if _top_buyer and _top_buyer[2] > 0 and check_day_trader_alert(_top_buyer[0]):
                        st.warning(f"⚠️ 買超第一名「{_top_buyer[0]}」疑似隔日沖分點——"
                                  f"同一分點底下客戶眾多，這不代表這筆一定是隔日沖操作，"
                                  f"但今天大買、留意隔天是否開高倒貨。")

                if st.button("💾 記錄校正（自動算均值＋逐家分開記錄）",
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
            elif f'fin_health_{code}' in st.session_state:
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
            b_date = b_cols[1].text_input("大戶日期", value=datetime.now().strftime("%m/%d"),
                                          key=f"my_b_date_{code}{btn_suffix}")

            b1, b2 = st.columns(2)
            if b1.button("✅ 寫入覆寫", key=f"btn_override_{code}{btn_suffix}", use_container_width=True):
                now_ts = datetime.now().timestamp()
                st.session_state.revenue_override[code] = {
                    'yoy': m_y, 'mom': card.get('rev_mom') if card.get('rev_mom') is not None else 0.0,
                    'month': m_month, 'ts': now_ts}
                if b_ratio > 0:
                    st.session_state.bigholder_override[code] = {'ratio': b_ratio, 'date': b_date, 'ts': now_ts}
                    safe_upsert_big_holder(code, f"{datetime.now().year}-{b_date.replace('/', '-')}", b_ratio)
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
                with st.spinner("NVIDIA 輪替陣列推演中..."):
                    rep = execute_single_stock_ai(card)
                    st.session_state.single_ai_report[code] = rep
                    # 【V160 修復】只有「成功的推演」才存進歷史時光膠囊。失敗訊息（模型下架/連線逾時
                    # 等）不存，否則歷史區會被一堆「三個模型都無法使用」的錯誤訊息塞滿、變得雜亂。
                    _is_error = ('無法使用' in rep or '模型不存在' in rep or 'Error code' in rep
                                 or rep.strip().startswith('⚠️'))
                    if not _is_error:
                        st.session_state.analysis_history[code]['nv_history'].append(
                            {"time": datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d %H:%M"), "report": rep})
                        save_local_db_isolated()
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
            st.error(f"⚠️ 這個展開區塊內部發生錯誤，不影響卡片其他部分：{_panel_e}")
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
            st.session_state[this_section].pop(code, None)
            save_local_db_isolated()
            st.rerun()
        if m_cols[1].button(remove_label, key=f"del_pin_{code}{btn_suffix}", use_container_width=True):
            st.session_state[this_section].pop(code, None)
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
        _quick_opts = [f"{c} {TW_STOCK_NAMES.get(c, '')}" for c in codes]
        _quick_map = {f"{c} {TW_STOCK_NAMES.get(c, '')}": c for c in codes}
        with st.expander(f"⚡ 快速批次刪除（不用捲動找卡片，共 {len(codes)} 檔）", expanded=False):
            _quick_picked = st.multiselect("勾選要刪除的標的（可搜尋，可多選）",
                                           _quick_opts, key=f"quick_del_{section_key}")
            if _quick_picked and st.button(f"🗑️ 確認刪除選中的 {len(_quick_picked)} 檔",
                                           key=f"quick_del_btn_{section_key}",
                                           use_container_width=True):
                _to_del_quick = {_quick_map[k] for k in _quick_picked}
                for c in _to_del_quick:
                    st.session_state[section_key].pop(c, None)
                save_local_db_isolated()
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
    # 【R96新增，總指揮官反映「查看log沒看到每個載入的秒數」】原本沒有
    # 「整批速覽總共花多久」這種摘要計時，只有單檔內部細部計時。這裡加
    # 最外層總計時，log清楚印出「這批N檔總共花X秒」，之後變慢一眼可查。
    _qo_t0 = time.time()
    # 【R60新增】原本例外被整個吞掉(except Exception: continue)，導致
    # 「9檔全部抓價失敗但健康度檢查全綠」這種矛盾狀況查不出來——健康度
    # 測的是單一探測請求，不是完整流程有沒有例外。現在把失敗原因記下來。
    _qo_fail_count = 0
    _qo_last_err = ''
    # 【R95續27新增，重大效能修復】原本這個函式無條件每次互動都重算全部14+
    # 檔，浪費效能。改成用watchlist排序後內容當快取鍵存進session_state，
    # 沒變就沿用；提供手動「🔄重新整理速覽」按鈕主動重算。
    _qo_cache_key = "|".join(sorted(codes))
    _qo_cached = st.session_state.get('_qo_results_cache')
    _qo_force_refresh = st.session_state.pop('_qo_force_refresh', False)
    if (not _qo_force_refresh) and _qo_cached and _qo_cached.get('key') == _qo_cache_key:
        results = _qo_cached['results']
        st.caption(f"（沿用上次算好的速覽結果，watchlist沒有變動——想要最新資料可以按下面"
                  f"「🔄重新整理速覽」）")
    elif codes:
        _qo_ctx = get_script_run_ctx()
        _qo_prog = st.progress(0.0, text=f"⚙️ 速覽計算中 0/{len(codes)}")
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
                      for code in codes}
            for future in concurrent.futures.as_completed(futures):
                code = futures[future]
                _qo_done += 1
                _qo_prog.progress(_qo_done / len(codes),
                                  text=f"⚙️ 速覽計算中 {_qo_done}/{len(codes)}（{_qo_done/len(codes)*100:.0f}%）")
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
        if _qo_fail_count == len(codes) and _qo_fail_count > 0:
            # 全部都失敗，不是部分失敗——這種「全軍覆沒」的情況才值得直接
            # 在畫面上留一筆樣本錯誤，讓不用查log也能看到線索。
            st.session_state['qo_last_fail_sample'] = _qo_last_err
        # 【R95續27】把這次算好的基礎結果存進快取（存的是即時報價疊加「之前」
        # 的版本——即時報價本來就該每次都重新疊加最新的，不該被這個快取鎖住，
        # 所以attach_live_quotes()還是留在下面、快取範圍之外，每次都會重跑）。
        st.session_state['_qo_results_cache'] = {'key': _qo_cache_key, 'results': dict(results)}

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
            # 【R53修復】原本「現價」沒標示是哪天的——極端行情下技術指標
            # 用的基準價可能還停在前一天，現在直接標出日期一眼看得到。
            '現價': round(float(c.get('price', 0) or 0), 2),
            '現價日期': (f"⚠️{c.get('price_date','?')}" if c.get('price_is_stale')
                        else c.get('price_date', '')),
            '漲跌%': round(float(c.get('gain', 0) or 0), 2),
            # 【R96再修復】上一輪的「🕐退回顯示日線收盤價」是錯誤修法，已撤回——
            # 總指揮官指出這違反R62當時定案的原則：「查無成交價寧可誠實顯示
            # —，不假裝有資料」，日線收盤價（可能是昨天的）冒充即時價，等於
            # 重蹈R62的覆轍。真正該做的是讓_last_cache（這個session裡最近
            # 一次真的抓到的成交價+真實時間）確實生效，不是換一種方式造假。
            # 這裡改回誠實顯示"—"，但加強了attach_live_quotes內部的診斷log
            # （見下方batch fetch那段），方便查出_last_cache為什麼是空的。
            '即時': round(c['live_price'], 2) if c.get('live_price') is not None else "—",
            '即時漲跌%': round(c['live_change_pct'], 2) if c.get('live_change_pct') is not None else "—",
            # 【R53新增，R95續14補上沿用標示】即時報價的實際抓取時間——跟現價
            # 日期同樣的道理，時間標出來，才看得出「這個113.5是不是已經是
            # 5分鐘前的舊資料」。
            '即時時間': ((f"⏳{c.get('live_time','')}" if c.get('live_is_carried') else c.get('live_time', ''))
                        if c.get('live_time') else "—"),
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
    _fmt_cols = ['現價', '漲跌%', '即時', '即時漲跌%', '開', '高', '低', '爆量比', '防守線']

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
    if _qo_del_candidates:
        with st.expander(f"⚡ 速覽快速刪除（僅雷達/觀察，共 {len(_qo_del_candidates)} 檔可刪）", expanded=False):
            _qo_del_opts = [f"{code} {name}（{src}）" for code, name, src in _qo_del_candidates]
            _qo_del_map = {f"{code} {name}（{src}）": (code, src) for code, name, src in _qo_del_candidates}
            _qo_picked = st.multiselect("勾選要刪除的標的（可搜尋，可多選）", _qo_del_opts,
                                        key="qo_quick_del")
            if _qo_picked and st.button(f"🗑️ 確認刪除選中的 {len(_qo_picked)} 檔",
                                        key="qo_quick_del_btn", use_container_width=True):
                _qo_del_count = 0
                for _opt in _qo_picked:
                    _code, _src = _qo_del_map[_opt]
                    _skey = _qo_source_key_map[_src]
                    if st.session_state.get(_skey, {}).pop(_code, None) is not None:
                        _qo_del_count += 1
                save_local_db_isolated()
                st.success(f"🗑️ 已刪除 {_qo_del_count} 檔")
                time.sleep(0.5)
                st.rerun()

    try:
        try:
            _styled = df.style.map(_gain_color, subset=['漲跌%', '即時漲跌%'])
        except AttributeError:
            # 舊版pandas(<2.1)沒有.map，退回已棄用但還能用的.applymap
            _styled = df.style.applymap(_gain_color, subset=['漲跌%', '即時漲跌%'])
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
        """盤中可能還停在前一天，「現價日期」欄位標⚠️代表不是最新交易日）；「即時」"""
        """是證交所即時報價（約5秒更新一次，「即時時間」是實際抓到的那一刻，不是"""
        """現在的時間）。劇烈行情（例如跌停鎖死）兩者都可能跟你手機看到的價格有落差，"""
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
                _qo_full_ctx = get_script_run_ctx()
                try:
                    _qo_pick_card = calculate_signals_worker(_qo_pick_code, _qo_full_config, _qo_full_ctx)
                except Exception as _e:
                    _qo_pick_card = None
                    st.warning(f"⚠️ {_qo_pick_code} 載入失敗：{type(_e).__name__}: {_e}——"
                              f"稍後再試一次，如果持續失敗麻煩告訴我。")
            if _qo_pick_card and not _qo_pick_card.get('error'):
                # 【R96】明確要求「完整戰卡」，fetch_intraday_extras=True，
                # 資料完整——這正是總指揮官這輪確認的「查看單一檔完整戰卡才
                # 顯示全部當沖資訊」那個情境本身。
                _qo_pick_card = attach_live_quotes(
                    {_qo_pick_code: _qo_pick_card}, fetch_intraday_extras=True)[_qo_pick_code]
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
    _qo_results = render_quick_overview(_all_codes, config_payload,
                                        industry_map=_stock_to_ind_qo, leader_map=_qo_leader_map)
    _monitor_cards.extend(_qo_results.values())
else:
    if st.session_state.get('portfolio', {}):
        with st.expander("💼 總指揮常態持倉模擬倉", expanded=True):
            # 【V160關鍵修復】「開機卡在只跑出1-2檔」的根因——持倉清單
            # 原本逐檔序列迴圈，round23平行化雷達/觀察區時漏掉這段。改用
            # 同一套ThreadPoolExecutor先平行算完，再照順序渲染卡片。
            _pf_items = list(st.session_state.portfolio.items())
            _pf_codes = [code for code, _ in _pf_items]
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
