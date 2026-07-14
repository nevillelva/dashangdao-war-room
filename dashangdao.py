import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, time as dt_time, timezone
import re
import time
import json
import os
import requests
import warnings
import urllib3
import concurrent.futures
from openai import OpenAI  
import tempfile
import sqlite3
import threading
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from streamlit.runtime.scriptrunner import add_script_run_ctx

# ==============================================================================
# 一、 系統最高安全防禦與法規合規宣告
# ==============================================================================
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
warnings.filterwarnings('ignore')

# 🚀 強制綁定台灣時區 (UTC+8)，避免雲端部署時間錯亂
TW_TZ = timezone(timedelta(hours=8))

GOV_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*"
}

USER_DB_FILE = "54088_database.json" 
SQLITE_DB_FILE = "54088_inst_history.db"

# ==============================================================================
# 🚀 核心防線：API 專屬報錯類別與連線池
# ==============================================================================
class FinMindAPIError(Exception):
    def __init__(self, reason, detail=""):
        self.reason = reason  
        self.detail = detail
        super().__init__(f"{reason}: {detail}")

def get_last_trading_date():
    d = datetime.now(TW_TZ) - timedelta(days=1)
    while d.weekday() >= 5: 
        d -= timedelta(days=1)
    return d.strftime('%Y-%m-%d')

@st.cache_resource
def get_safe_session():
    session = requests.Session()
    session.headers.update(GOV_HEADERS)
    retry = Retry(
        total=3, backoff_factor=0.6,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"]
    )
    # 🚀 修復：擴充連線池，適應 10 個 Worker 的高併發掃描
    adapter = HTTPAdapter(max_retries=retry, pool_connections=20, pool_maxsize=20)
    session.mount('https://', adapter)
    session.mount('http://', adapter)
    return session

_SESSION = get_safe_session()

# ==============================================================================
# 二、 資料庫防護網與事務鎖配置 (ACID 霸體)
# ==============================================================================
DB_LOCK = threading.Lock()

def get_db_conn():
    conn = sqlite3.connect(SQLITE_DB_FILE, check_same_thread=False, timeout=15)
    conn.execute('PRAGMA journal_mode=WAL')
    return conn

def init_sqlite_db():
    with DB_LOCK:
        conn = get_db_conn()
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
        conn.commit()
        return conn

SQLITE_CONN = init_sqlite_db()

def safe_upsert_big_holder(code, date_str, percent_value):
    is_valid = (percent_value is not None and percent_value != '' and isinstance(percent_value, (int, float)) and percent_value > 0.0)
    if not is_valid: return False
    with DB_LOCK:
        try:
            SQLITE_CONN.execute("""
                INSERT INTO big_holder_history (code, date, percent) VALUES (?, ?, ?)
                ON CONFLICT(code, date) DO UPDATE SET percent = excluded.percent
            """, (code, date_str, percent_value))
            SQLITE_CONN.commit()
            return True
        except Exception: return False

def get_latest_big_holder(code):
    try:
        cursor = SQLITE_CONN.cursor()
        cursor.execute("SELECT date, percent FROM big_holder_history WHERE code = ? AND percent > 0 ORDER BY date DESC LIMIT 1", (code,))
        row = cursor.fetchone()
        if row: return {'date': row[0], 'percent': row[1]}
        return None
    except: return None

def get_db_stats():
    try:
        cursor = SQLITE_CONN.cursor()
        cursor.execute("SELECT COUNT(DISTINCT date) FROM inst_holding")
        days = cursor.fetchone()[0]
        cursor.execute("SELECT date, COUNT(symbol) FROM inst_holding GROUP BY date ORDER BY date DESC LIMIT 5")
        details = cursor.fetchall()
        return days, details
    except: return 0, []

def init_session_state():
    defaults = {
        'db_loaded': False, 'pinned_stocks': {"2303": "手動強制加入", "5871": "手動強制加入"},
        'portfolio': {}, 'revenue_override': {}, 'dividend_override': {},
        'bigholder_override': {}, 'scan_results': [], 'scan_mode': "",
        'active_key_index': 0, 'single_ai_trigger': "", 'single_ai_report': {},
        'intelligence_pool': {}, 'analysis_history': {}, 'last_refresh': time.time(),
        'last_uploaded_csv': None
    }
    for k, v in defaults.items():
        if not hasattr(st.session_state, k): setattr(st.session_state, k, v)

init_session_state()

def safe_json_write(filepath, data):
    dir_name = os.path.dirname(os.path.abspath(filepath)) or "."
    with tempfile.NamedTemporaryFile('w', dir=dir_name, delete=False, suffix='.tmp', encoding='utf-8') as tmp:
        json.dump(data, tmp, ensure_ascii=False, indent=4)
        tmp_path = tmp.name
    os.replace(tmp_path, filepath)

def load_and_isolate_db():
    if not getattr(st.session_state, 'db_loaded', False):
        if os.path.exists(USER_DB_FILE):
            try:
                with open(USER_DB_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    st.session_state.pinned_stocks = data.get("pinned_stocks", st.session_state.pinned_stocks)
                    st.session_state.portfolio = data.get("portfolio", {})
                    st.session_state.revenue_override = data.get("revenue_override", {})
                    st.session_state.dividend_override = data.get("dividend_override", {})
                    st.session_state.bigholder_override = data.get("bigholder_override", {}) 
                    st.session_state.intelligence_pool = data.get("intelligence_pool", {})
                    st.session_state.analysis_history = data.get("analysis_history", {})
            except Exception: pass
        
        now_ts = datetime.now(TW_TZ).timestamp()
        for d_dict in [st.session_state.revenue_override, st.session_state.bigholder_override, st.session_state.dividend_override]:
            for k in list(d_dict.keys()):
                if now_ts - d_dict[k].get('ts', now_ts) > 7 * 86400: del d_dict[k]
                    
        st.session_state.db_loaded = True

def save_local_db_isolated():
    payload = {
        "pinned_stocks": getattr(st.session_state, 'pinned_stocks', {}), 
        "portfolio": getattr(st.session_state, 'portfolio', {}),
        "revenue_override": getattr(st.session_state, 'revenue_override', {}),
        "dividend_override": getattr(st.session_state, 'dividend_override', {}),
        "bigholder_override": getattr(st.session_state, 'bigholder_override', {}), 
        "intelligence_pool": getattr(st.session_state, 'intelligence_pool', {}),
        "analysis_history": getattr(st.session_state, 'analysis_history', {})
    }
    safe_json_write(USER_DB_FILE, payload)

load_and_isolate_db()

API_READY, FINMIND_READY = True, True
try:
    COMMANDER_PIN = st.secrets.radar_secrets.commander_pin
    NVIDIA_API_KEY = st.secrets.radar_secrets.get("nvidia_api_key", "").strip()
    if not NVIDIA_API_KEY: API_READY = False
    
    SECRET_FINMIND = st.secrets.radar_secrets.get("finmind_token", "")
    FINMIND_TOKENS = [k.strip() for k in SECRET_FINMIND.split(",") if k.strip()]
    if not FINMIND_TOKENS or FINMIND_TOKENS[0] == "": FINMIND_TOKENS, FINMIND_READY = [""], False
except Exception:
    API_READY, FINMIND_READY, COMMANDER_PIN, NVIDIA_API_KEY, FINMIND_TOKENS = False, False, "54088", "", [""]

# ==============================================================================
# 🛠️ 【防呆防崩潰機制】補上遺失的函數，避免畫面渲染報錯
# ==============================================================================
def process_twse_csv(files):
    st.warning("⚠️ 系統尚未實作 process_twse_csv 功能。")

def render_action_buttons(c, code, is_portfolio):
    pass # 保留空接口，防止 NameError

# ==============================================================================
# 三、 基礎數據引擎與 API 抓取模組
# ==============================================================================
def safe_float(val):
    if pd.isna(val) or val is None or str(val).strip() == '': return 0.0
    try:
        s = str(val).upper().replace(',', '').replace('-', '').strip()
        s = re.sub(r'[^\d.]', '', s)
        return float(s) if s else 0.0
    except Exception: return 0.0

def calc_real_profit(cost, price, qty=1):
    if cost <= 0 or price <= 0: return 0, 0
    buy_val = cost * qty * 1000
    sell_val = price * qty * 1000
    profit = sell_val - buy_val - max(20, int(buy_val * 0.001425)) - max(20, int(sell_val * 0.001425)) - int(sell_val * 0.003)
    return profit, (profit / buy_val) * 100 if buy_val > 0 else 0

def calc_volume_change(today_vol, yesterday_vol):
    vol_diff = today_vol - yesterday_vol # 這裡已經是張數
    vol_pct = ((vol_diff / yesterday_vol) * 100) if yesterday_vol else 0.0
    if vol_diff > 0: label, icon = f"量增 +{vol_diff:,.0f}張", "🔥"
    elif vol_diff < 0: label, icon = f"量縮 {vol_diff:,.0f}張", "🧊"
    else: label, icon = "量平", "➖"
    return f"{icon} {label} | {vol_pct:+.1f}%", vol_diff, vol_pct

def _finmind_get(url, params, max_retries=3, timeout=6):
    last_reason, last_detail = "unknown", ""
    for attempt in range(max_retries):
        try:
            res = _SESSION.get(url, params=params, timeout=timeout)
            if res.status_code == 429:
                last_reason, last_detail = "rate_limited", "HTTP 429"
                time.sleep(1.5 * (attempt + 1)); continue
            if res.status_code != 200:
                last_reason, last_detail = "http_error", f"HTTP {res.status_code}"
                time.sleep(0.8 * (attempt + 1)); continue
            payload = res.json()
            if payload.get('msg') != 'success':
                last_reason, last_detail = "api_rejected", payload.get('msg', '')
                time.sleep(0.8 * (attempt + 1)); continue
            if not payload.get('data'): raise FinMindAPIError('empty_data', 'API 回傳成功但 data 為空')
            return payload
        except requests.exceptions.Timeout:
            last_reason, last_detail = "timeout", f"逾時 {timeout}s"
            time.sleep(0.8 * (attempt + 1))
        except requests.exceptions.RequestException as e:
            last_reason, last_detail = "connection_error", str(e)
            time.sleep(0.8 * (attempt + 1))
    raise FinMindAPIError(last_reason, last_detail)

@st.cache_data(ttl=86400, show_spinner=False)
def fetch_finmind_revenue(symbol, token, max_lookback=400):
    url = 'https://api.finmindtrade.com/api/v4/data'
    lookback = 120
    df = None
    last_err = "未知錯誤"
    while df is None and lookback <= max_lookback:
        start_date = (datetime.now(TW_TZ) - timedelta(days=lookback)).strftime('%Y-%m-%d')
        params = {'dataset': 'TaiwanStockMonthRevenue', 'data_id': symbol, 'start_date': start_date}
        if token: params['token'] = token
        try:
            payload = _finmind_get(url, params)
            df = pd.DataFrame(payload.get('data', []))
        except FinMindAPIError as e: 
            last_err = e.reason
            lookback *= 2

    if df is not None and not df.empty:
        df = df.sort_values('date')
        for _, row in df[::-1].iterrows():
            yoy_raw, mom_raw = row.get('revenue_YearOnYearRatio'), row.get('revenue_MonthOverMonthRatio')
            if pd.isna(yoy_raw) or pd.isna(mom_raw): continue
            try:
                yoy, mom = float(yoy_raw), float(mom_raw)
                m_label = f"{int(row.get('revenue_month', 0)):02d}月"
                result = {'yoy': yoy, 'mom': mom, 'month': m_label, 'stale': False}
                st.session_state[f'_last_good_revenue_{symbol}'] = result
                return result
            except: continue

    last_good = st.session_state.get(f'_last_good_revenue_{symbol}')
    if last_good:
        stale = dict(last_good)
        stale['stale'] = True
        return stale
        
    err_msg = "[⛔ API次數耗盡]" if last_err == "rate_limited" else "[📭 官方未發布]"
    return {'yoy': 0.0, 'mom': 0.0, 'month': err_msg, 'stale': False}

@st.cache_data(ttl=14400, show_spinner=False)
def fetch_big_holder_with_recursion(code, token, target_date, initial_lookback=20, max_lookback=180):
    url = 'https://api.finmindtrade.com/api/v4/data'
    target_dt = datetime.strptime(target_date, "%Y-%m-%d")
    lookback = initial_lookback
    last_err = "未知"
    while lookback <= max_lookback:
        start_date = (target_dt - timedelta(days=lookback)).strftime('%Y-%m-%d')
        params = {'dataset': 'TaiwanStockHoldingSharesPer', 'data_id': code, 'start_date': start_date, 'end_date': target_date}
        if token: params['token'] = token
        try:
            payload = _finmind_get(url, params)
            raw = payload.get('data', [])
            if raw:
                df = pd.DataFrame(raw)
                df = df[df['HoldingSharesLevel'] >= 15]
                if not df.empty:
                    latest_date = df['date'].max()
                    pct = round(df[df['date'] == latest_date]['percent'].sum(), 2)
                    is_stale = latest_date != target_date
                    return {'big_holder': pct, 'big_holder_date': datetime.strptime(latest_date, "%Y-%m-%d").strftime("%Y-%m-%d"), 'is_stale': is_stale, 'error': None}
            last_err = "empty_data"
        except FinMindAPIError as e: 
            last_err = e.reason
        lookback *= 2
        
    err_msg = "[⛔ API限流]" if last_err == "rate_limited" else "[📭 無資料]"
    return {'big_holder': 0.0, 'big_holder_date': err_msg, 'is_stale': False, 'error': err_msg}

@st.cache_data(ttl=86400, show_spinner=False)
def fetch_twse_dividends():
    divs = {}
    try:
        res = _SESSION.get("https://openapi.twse.com.tw/v1/exchangeReport/TWT48U", timeout=5)
        if res.status_code == 200:
            for item in res.json():
                c = str(item.get('股票代號', '')).strip()
                if len(c) == 4:
                    cash_div = safe_float(item.get('現金股利', 0))
                    stock_div = safe_float(item.get('盈餘轉增資配股股數', 0)) / 100
                    if stock_div <= 0: stock_div = safe_float(item.get('資本公積轉增資配股股數', 0)) / 100
                    divs.update({c: {'date': str(item.get('除權息日期', '')).strip(), 'cash': cash_div, 'stock': stock_div}})
    except: pass
    return divs

@st.cache_data(ttl=86400, show_spinner=False)
def fetch_stock_names():
    names = {}
    for url in ["https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL", "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_peratio_analysis"]:
        try:
            res = _SESSION.get(url, timeout=5)
            if res.status_code == 200:
                for item in res.json():
                    c = str(item.get('Code', item.get('SecuritiesCompanyCode', ''))).strip()
                    n = str(item.get('Name', item.get('CompanyName', ''))).strip()
                    if len(c) == 4 and c.isdigit() and n: names.update({c: n})
        except: pass
    for k, v in {"2330":"台積電", "2303":"聯電", "2317":"鴻海", "2308":"台達電", "5871":"中租-KY", "3481":"群創", "2454":"聯發科"}.items():
        if k not in names: names.update({k: v})
    return names

TW_STOCK_NAMES = fetch_stock_names()
DIVIDEND_DB = fetch_twse_dividends()
GLOBAL_MARKET_CODES = list(TW_STOCK_NAMES.keys())

@st.cache_data(ttl=60, show_spinner=False)
def get_market_weather_real():
    try:
        tk = yf.Ticker("^TWII", session=_SESSION)
        hist = tk.history(period="10d", timeout=5)
        if not hist.empty:
            c_idx, prev_idx = float(hist['Close'].iloc[-1]), float(hist['Close'].iloc[-2])
            change_pt = round(c_idx - prev_idx, 2)
            change_pct = round((change_pt / prev_idx) * 100, 2)
            arrow = "▲" if change_pt > 0 else ("▼" if change_pt < 0 else "▬")
            color = "#ff4d4d" if change_pt > 0 else ("#00c853" if change_pt < 0 else "#999")
            return f"{c_idx:,.0f} ({arrow} {abs(change_pt):,.0f}點 | {change_pct:+.2f}%)", color, change_pct
    except: pass
    return "大盤連線中...", "#888", 0.0

weather_str, weather_color, global_twii_gain = get_market_weather_real()

@st.cache_data(ttl=120, show_spinner=False)
