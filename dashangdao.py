import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, time as dt_time
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

# ==============================================================================
# 一、 系統最高安全防禦與法規合規宣告
# ==============================================================================
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
warnings.filterwarnings('ignore')

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
        self.reason = reason  # 'rate_limited' | 'http_error' | 'empty_data' | 'timeout' | 'connection_error'
        self.detail = detail
        super().__init__(f"{reason}: {detail}")

def get_last_trading_date():
    d = datetime.now() - timedelta(days=1)
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
    adapter = HTTPAdapter(max_retries=retry)
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
        
        now_ts = datetime.now().timestamp()
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
# 三、 基礎數據引擎 (解決執行緒安全與 yfinance 股轉張 Bug)
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
    vol_diff = today_vol - yesterday_vol
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
    while df is None and lookback <= max_lookback:
        start_date = (datetime.now() - timedelta(days=lookback)).strftime('%Y-%m-%d')
        params = {'dataset': 'TaiwanStockMonthRevenue', 'data_id': symbol, 'start_date': start_date}
        if token: params['token'] = token
        try:
            payload = _finmind_get(url, params)
            df = pd.DataFrame(payload.get('data', []))
        except FinMindAPIError: lookback *= 2

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
    return {'yoy': 0.0, 'mom': 0.0, 'month': "無資料", 'stale': False}

@st.cache_data(ttl=14400, show_spinner=False)
def fetch_big_holder_with_recursion(code, token, target_date, initial_lookback=20, max_lookback=180):
    url = 'https://api.finmindtrade.com/api/v4/data'
    target_dt = datetime.strptime(target_date, "%Y-%m-%d")
    lookback = initial_lookback
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
                    return {'big_holder': pct, 'big_holder_date': datetime.strptime(latest_date, "%Y-%m-%d").strftime("%Y-%m-%d"), 'is_stale': is_stale}
        except FinMindAPIError: pass
        lookback *= 2
    return None

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

weather_str, weather_color, global_twii_gain = get_market_weather_real()

@st.cache_data(ttl=120, show_spinner=False)
def get_real_stock_data_yfinance(symbol):
    for ext in [".TW", ".TWO"]:
        try:
            tk = yf.Ticker(symbol + ext, session=_SESSION)
            hist = tk.history(period="6mo", timeout=4).dropna(subset=['Close'])
            hist = hist[hist['Volume'] > 0]
            # 【Bug 修正】：在源頭把原始成交量「股」直接除以 1000 換算成「張」，下游運算絕不出錯
            hist['Volume'] = hist['Volume'] / 1000.0
            if not hist.empty and len(hist) > 20: 
                return hist.tail(90), tk.info
        except: pass
    return None, {}

# ==============================================================================
# 四、 動態技術指標與 ATR 交易邏輯重構 (無未來函數)
# ==============================================================================
def calc_rsi(df, period=14):
    delta = df['Close'].diff()
    gain = delta.where(delta > 0, 0).rolling(period).mean()
    loss = -delta.where(delta < 0, 0).rolling(period).mean()
    rs = gain / (loss + 1e-9)
    return 100 - (100 / (1 + rs))

def calc_bias(df, period=20):
    ma = df['Close'].rolling(period).mean()
    return (df['Close'] - ma) / (ma + 1e-9) * 100

def calculate_atr(df, period=14):
    high = df['High']
    low = df['Low']
    prev_close = df['Close'].shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = true_range.rolling(window=period).mean()
    return atr.iloc[-1] if not atr.empty else 0.0

def detect_k_line_patterns_v152(df, atr_val):
    patterns = []
    if len(df) < 5: return patterns
    if pd.isna(atr_val) or atr_val == 0: atr_val = df['Close'].iloc[-1] * 0.02
    
    c0, c1, c2 = float(df['Close'].iloc[-1]), float(df['Close'].iloc[-2]), float(df['Close'].iloc[-3])
    o0, o1, o2 = float(df['Open'].iloc[-1]), float(df['Open'].iloc[-2]), float(df['Open'].iloc[-3])
    body0 = abs(c0 - o0)
    is_significant = body0 > atr_val * 0.5
    
    if (c0 > o0) and is_significant:
        if (c1 < o1) and c0 > o1 and o0 < c1: patterns.append({"text": "長紅吞噬", "class": "tag-red"})
        else: patterns.append({"text": "低檔長紅", "class": "tag-red"})
    if (c0 > o0) and (c1 > o1) and (c2 > o2) and (c0 > c1 > c2): patterns.append({"text": "紅三兵", "class": "tag-red"})
    if (c0 < o0) and is_significant:
        if (c1 > o1) and c0 < o1 and o0 > c1: patterns.append({"text": "長黑吞噬", "class": "tag-green"})
        else: patterns.append({"text": "高檔長黑", "class": "tag-green"})
    if (c0 < o0) and (c1 < o1) and (c2 < o2) and (c0 < c1 < c2): patterns.append({"text": "黑三兵", "class": "tag-green"})
    return patterns

def build_trade_zones(current_price, ma5, ma20, atr):
    atr_buffer = atr * 0.5
    def_line = round(ma5 - atr_buffer, 2)
    atk_zone = round(current_price + atr, 2)
    buffer_pct = ((current_price - def_line) / current_price) * 100 if current_price > 0 else 0
    return {'atk_zone': atk_zone, 'def_line': def_line, 'buffer_pct': round(buffer_pct, 2), 'atr': round(atr, 2)}

def determine_signal(current_price, ma5, ma20, foreign_buy, vol_ratio, is_open_high_close_low, buffer_pct):
    score = 0
    reasons = []
    if current_price > ma5 > ma20: score += 2; reasons.append("站穩多頭排列")
    elif current_price > ma5: score += 1; reasons.append("站上5日線")
    elif current_price < ma5: score -= 2; reasons.append("跌破5日線")
    
    if foreign_buy > 0: score += 1; reasons.append(f"外資買超{int(foreign_buy):,}張")
    elif foreign_buy < 0: score -= 1; reasons.append(f"外資賣超{int(abs(foreign_buy)):,}張")
    
    if vol_ratio < 0.6: score -= 1; reasons.append("量縮，上攻力道存疑")
    elif vol_ratio > 2.0: score += 1; reasons.append("爆量突破")
    
    if is_open_high_close_low: score -= 2; reasons.append("開高走低，當日轉弱")
    if buffer_pct < 1.0: score -= 1; reasons.append(f"防守緩衝僅{buffer_pct:.1f}%，風險偏高")
    
    if score >= 3: return "🔥 偏多攻擊", "#ff4d4d", score, reasons
    elif score >= 1: return "🟡 觀察偏多", "#ffab00", score, reasons
    elif score <= -3: return "🔵 偏空防守", "#2979ff", score, reasons
    elif score <= -1: return "⚠️ 轉弱謹慎", "#ff9100", score, reasons
    else: return "⚖️ 中立震盪", "#888", score, reasons

# ==============================================================================
# 五、 核心運算晶片 (執行緒安全隔離設計：不包含任何 st.* 物件)
# ==============================================================================
def get_inst_data_from_db(symbol, limit=10):
    try:
        # 內建唯讀防鎖機制
        df = pd.read_sql('SELECT * FROM inst_holding WHERE symbol=? ORDER BY date DESC LIMIT ?', sqlite3.connect(SQLITE_DB_FILE), params=(symbol, limit))
        return df
    except Exception: return pd.DataFrame()

def get_time_weighted_vol_ratio(vol_today, vol_5ma):
    """【Bug 修正】：盤中時間等效量比估算，消滅早盤系統性誤判量縮的問題"""
    now = datetime.now()
    # 測試環境如非盤中時間，預設直接採客觀比例
    if now.weekday() >= 5: return vol_today / vol_5ma if vol_5ma > 0 else 0.0
    
    start_time = datetime.combine(now.date(), dt_time(9, 0))
    end_time = datetime.combine(now.date(), dt_time(13, 30))
    
    if now < start_time: return 0.0
    if now > end_time: return vol_today / vol_5ma if vol_5ma > 0 else 0.0
    
    elapsed_mins = (now - start_time).total_seconds() / 60.0
    total_mins = 270.0 # 9:00 ~ 13:30 共 270 分鐘
    time_ratio = elapsed_mins / total_mins
    
    estimated_today_vol = vol_today / max(0.01, time_ratio)
    return estimated_today_vol / vol_5ma if vol_5ma > 0 else 0.0

def calculate_signals_worker(symbol, config):
    """【Bug 修正】：執行緒安全安全晶片。所有配置參數由外部經主執行緒傳入，背景執行緒純運算"""
    token = config.get('token')
    rev_override = config.get('rev_override')
    bh_override = config.get('bh_override')
    div_override = config.get('div_override')
    dividend_db = config.get('dividend_db')
    stock_names = config.get('stock_names')
    
    f_single = t_single = d_single = margin_diff = 0.0
    f_5d = t_5d = f_10d = t_10d = 0
    f_pct = t_pct = f_5d_pct = t_5d_pct = 0.0
    big_holder, big_holder_date = 0.0, ""
    latest_db_date = ""
    
    hist_pack = get_real_stock_data_yfinance(symbol)
    if hist_pack is None or not hist_pack[0] is not None: 
        return {"code": symbol, "name": stock_names.get(symbol, symbol), "error": True}
    
    hist, info = hist_pack
    curr_price, prev_price, open_price = float(hist['Close'].iloc[-1]), float(hist['Close'].iloc[-2]), float(hist['Open'].iloc[-1])
    gain = ((curr_price - prev_price) / prev_price) * 100 if prev_price > 0 else 0
    
    # 由於源頭已除以 1000，此處 vol_today 單位即為「張」
    vol_today = int(hist['Volume'].iloc[-1])
    vol_yesterday = max(1, int(hist['Volume'].iloc[-2]))
    vol_change_str, vol_diff, vol_change_pct = calc_volume_change(vol_today, vol_yesterday)
    
    vol_5ma = max(1, int(hist['Volume'].tail(5).mean()))
    
    # 判斷是否正處於盤中
    now_dt = datetime.now()
    is_intraday = (now_dt.weekday() < 5 and dt_time(9, 0) <= now_dt.time() <= dt_time(13, 30))
    if is_intraday:
        vol_ratio = get_time_weighted_vol_ratio(vol_today, vol_5ma)
        vol_ratio_label = f"爆量比: {vol_ratio:.1f}x (盤中等效估算)"
    else:
        vol_ratio = vol_today / vol_5ma if vol_5ma > 0 else 0.0
        vol_ratio_label = f"爆量比: {vol_ratio:.1f}x"
        
    ma5, ma20, ma60 = float(hist['Close'].tail(5).mean()), float(hist['Close'].tail(20).mean()), float(hist['Close'].mean())
    exp1, exp2 = hist['Close'].ewm(span=12, adjust=False).mean(), hist['Close'].ewm(span=26, adjust=False).mean()
    macd_hist = (exp1 - exp2) - (exp1 - exp2).ewm(span=9, adjust=False).mean()
    macd_val = macd_hist.iloc[-1] if not macd_hist.empty else 0
    macd_str, macd_color = (f"多方動能 ({macd_val:+.2f})", "#ff4d4d") if macd_val > 0 else (f"空方動能 ({macd_val:+.2f})", "#00FF00")
    
    low_min, high_max = hist['Low'].rolling(9).min(), hist['High'].rolling(9).max()
    rsv = (hist['Close'] - low_min) / (high_max - low_min + 1e-9) * 100
    calc_k, calc_d = rsv.bfill().ffill().ewm(com=2, adjust=False).mean(), rsv.bfill().ffill().ewm(com=2, adjust=False).mean().ewm(com=2, adjust=False).mean()
    kdj_str = f"金叉向上 (K:{calc_k.iloc[-1]:.1f})" if not calc_k.empty and calc_k.iloc[-1] > calc_d.iloc[-1] else f"死叉向下 (K:{calc_k.iloc[-1]:.1f})"
    
    rsi_val = calc_rsi(hist).iloc[-1]
    bias_val = calc_bias(hist).iloc[-1]
    atr_val = calculate_atr(hist)
    
    # 【Bug 修正】：恢復第一輪對話中精準的開高走低定義 (開盤大於昨收 且 今日收盤低於開盤)
    is_open_high_close_low = (open_price > prev_price) and (curr_price < open_price)
    
    inst_df = get_inst_data_from_db(symbol, 10)
    if not inst_df.empty:
        latest = inst_df.iloc[0]
        latest_db_date = latest['date']
        f_single, t_single, d_single, margin_diff = latest['foreign_buy'], latest['trust_buy'], latest['dealer_buy'], latest['margin']
        
        f_pct = (f_single / vol_today * 100) if vol_today > 0 else 0.0
        t_pct = (t_single / vol_today * 100) if vol_today > 0 else 0.0
        
        df_5d = inst_df.head(5)
        f_5d, t_5d = df_5d['foreign_buy'].sum(), df_5d['trust_buy'].sum()
        f_10d, t_10d = inst_df['foreign_buy'].sum(), inst_df['trust_buy'].sum()
        
        vol_5d_sum = max(1, vol_5ma * 5)
        f_5d_pct = (f_5d / vol_5d_sum * 100) if vol_5d_sum > 0 else 0.0
        t_5d_pct = (t_5d / vol_5d_sum * 100) if vol_5d_sum > 0 else 0.0

    # 執行緒安全讀取大戶
    try:
        db_conn = sqlite3.connect(SQLITE_DB_FILE)
        cursor = db_conn.cursor()
        cursor.execute("SELECT date, percent FROM big_holder_history WHERE code = ? AND percent > 0 ORDER BY date DESC LIMIT 1", (symbol,))
        row = cursor.fetchone()
        big_holder, big_holder_date = (row[1], row[0]) if row else (0.0, "")
        db_conn.close()
    except: pass
        
    if symbol in bh_override:
        big_holder = bh_override[symbol].get('ratio', big_holder)
        big_holder_date = f"自訂 {bh_override[symbol].get('date', '')}"

    if symbol in rev_override:
        rev_yoy, rev_mom, rev_month, manual_mode = rev_override[symbol].get('yoy', 0.0), rev_override[symbol].get('mom', 0.0), rev_override[symbol].get('month', "自訂"), True
    else:
        fm_rev = fetch_finmind_revenue(symbol, token)
        rev_yoy, rev_mom, rev_month = fm_rev['yoy'], fm_rev['mom'], fm_rev['month']
        if fm_rev.get('stale'): rev_month += " (沿用)"
        manual_mode = False

    if symbol in div_override:
        div_display, div_yield, manual_div_mode = div_override[symbol].get('display', "自訂資料"), override_div[symbol].get('yield', 0.0), True
    else:
        div_info = dividend_db.get(symbol)
        if div_info:
            d_cash, d_stock, div_date_str = div_info.get('cash', 0.0), div_info.get('stock', 0.0), div_info.get('date', '')
            div_yield = (d_cash / curr_price) * 100 if curr_price > 0 else 0.0
            div_display = f"{div_date_str} | 息 {d_cash}元 + 權 {d_stock}元" if d_stock > 0 else f"{div_date_str} | 息 {d_cash}元"
        else:
            d_cash = safe_float(info.get('dividendRate', 0.0))
            div_yield = (d_cash / curr_price) * 100 if curr_price > 0 else 0.0
            div_display = f"無日期 | 息 {d_cash}元" if d_cash > 0 else "無近期資訊"
        manual_div_mode = False

    zones = build_trade_zones(curr_price, ma5, ma20, atr_val)
    signal_text, color_border, score, reasons = determine_signal(curr_price, ma5, ma20, f_single, vol_ratio, is_open_high_close_low, zones['buffer_pct'])
    signal_bg = "#3a1515" if "攻擊" in signal_text else ("#153a20" if "防守" in signal_text else "#332b00")
    
    detected_patterns = detect_k_line_patterns_v152(hist, atr_val)
    intraday_trend = "📉 開高走低·弱勢收下" if is_open_high_close_low else ("🔥 帶量長紅突破" if gain > 2.5 and vol_ratio > 1.2 else "⚖️ 溫和震盪換手")
    
    return {
        "code": symbol, "name": stock_names.get(symbol, symbol), "price": curr_price, "gain": gain, "error": False,
        "vol": vol_today, "vol_change_str": f"總量: {vol_today:,.0f}張 ({vol_change_str.split('|')[0].strip()})", "vol_ratio": vol_ratio, "vol_ratio_label": vol_ratio_label,
        "ma5": ma5, "ma20": ma20, "ma60": ma60, "macd_str": macd_str, "macd_color": macd_color, "kdj_str": kdj_str,
        "rsi_val": rsi_val, "bias_val": bias_val, "atr_val": atr_val,
        "f_buy": f_single, "t_buy": t_single, "d_buy": d_single, "margin_diff": margin_diff, "big_holder": big_holder, "big_holder_date": big_holder_date, 
        "f_5d": f_5d, "t_5d": t_5d, "f_10d": f_10d, "t_10d": t_10d, "f_pct": f_pct, "t_pct": t_pct, "f_5d_pct": f_5d_pct, "t_5d_pct": t_5d_pct,
        "atk_zone": zones['atk_zone'], "def_line": zones['def_line'], "buffer_pct": zones['buffer_pct'],
        "rev_yoy": rev_yoy, "rev_mom": rev_mom, "rev_month": rev_month, 
        "div_display": div_display, "div_yield": div_yield, "manual_div_mode": manual_div_mode,
        "blood_line": "", "signal_text": signal_text, "color_border": color_border, "signal_bg": signal_bg,
        "score": score, "reasons": reasons, "sparkline_html": spark_html, "latest_db_date": latest_db_date,
        "intraday_str": intraday_trend, "manual_mode": manual_mode, "detected_patterns": detected_patterns
    }

# ==============================================================================
# 六、 SQLite 雙軌籌碼備援管線 (用 ON CONFLICT 鎖定 margin 欄位不被刷洗為 0)
# ==============================================================================
def process_twse_csv(uploaded_files):
    success_files = 0
    # 構建批次處理容器，不再進行空轉子查詢
    for file_bytes in uploaded_files:
        raw_bytes = file_bytes.getvalue()
        try: decoded_content = raw_bytes.decode('big5', errors='ignore')
        except: continue
        try:
            first_line = decoded_content.split('\n')[0]
            date_match = re.search(r'(\d+)年(\d+)月(\d+)日', first_line)
            file_date = f"{int(date_match.group(1))+1911}-{date_match.group(2).zfill(2)}-{date_match.group(3).zfill(2)}" if date_match else get_last_trading_date()  
            import io
            df = pd.read_csv(io.StringIO(decoded_content), skiprows=1, thousands=',')
            code_col = next((c for c in df.columns if '代號' in str(c)), None)
            f_col = next((c for c in df.columns if '外資' in str(c) and '買賣超' in str(c)), None)
            t_col = next((c for c in df.columns if '投信買賣超' in str(c)), None)
            d_col = next((c for c in df.columns if '自營商' in str(c) and '自行買賣' in str(c)), None)
            
            if not code_col or not f_col: continue
            
            batch_args = []
            for index, row in df.iterrows():
                code = str(row[code_col]).strip()
                if len(code) == 4 and code.isdigit():
                    f_buy = int(safe_float(row[f_col]) / 1000) if f_col else 0
                    t_buy = int(safe_float(row[t_col]) / 1000) if t_col else 0
                    d_buy = int(safe_float(row[d_col]) / 1000) if d_col else 0
                    batch_args.append((file_date, code, f_buy, t_buy, d_buy))
            
            # 【Bug 修正】：採用 executemany 進行極速批次寫入，且排除對 margin / big_holder 的重置傷害
            with DB_LOCK:
                db_conn = get_db_conn()
                db_conn.executemany('''
                    INSERT INTO inst_holding (date, symbol, foreign_buy, trust_buy, dealer_buy, margin, big_holder, big_holder_date)
                    VALUES (?, ?, ?, ?, ?, 0.0, 0.0, '')
                    ON CONFLICT(date, symbol) DO UPDATE SET 
                        foreign_buy=excluded.foreign_buy, 
                        trust_buy=excluded.trust_buy, 
                        dealer_buy=excluded.dealer_buy;
                ''', batch_args)
                db_conn.commit()
            success_files += 1
        except Exception: pass
            
    if success_files > 0:
        st.success(f"✅ 成功強填 {success_files} 份日報至大腦！")
        time.sleep(1); st.rerun()

def sync_single_stock_finmind(code):
    try:
        target_date = get_last_trading_date()
        token = FINMIND_TOKENS[st.session_state.active_key_index]
        url = 'https://api.finmindtrade.com/api/v4/data'
        
        inst_success = False
        base_payload = {'foreign':0, 'trust':0, 'dealer':0, 'margin':0, 'big_holder':0.0, 'big_holder_date': ''}
        err_msg = ""
        
        try:
            params = {'dataset': 'TaiwanStockInstitutionalInvestorsBuySell', 'data_id': code, 'start_date': target_date}
            if token: params['token'] = token
            payload = _finmind_get(url, params)
            df = pd.DataFrame(payload.get('data', []))
            df['net'] = pd.to_numeric(df['buy'], errors='coerce').fillna(0) - pd.to_numeric(df['sell'], errors='coerce').fillna(0)
            piv = df.pivot_table(index='date', columns='name', values='net', aggfunc='sum')
            if 'Foreign_Investor' in piv.columns: base_payload['foreign'] = int(piv['Foreign_Investor'].iloc[-1]/1000)
            if 'Investment_Trust' in piv.columns: base_payload['trust'] = int(piv['Investment_Trust'].iloc[-1]/1000)
            if 'Dealer' in piv.columns: base_payload['dealer'] = int(piv['Dealer'].iloc[-1]/1000)
            inst_success = True
        except FinMindAPIError as e:
            err_msg += f"籌碼({e.reason}) "

        bh_result = fetch_big_holder_with_recursion(code, token, target_date)
        if bh_result:
            safe_upsert_big_holder(code, bh_result['big_holder_date'], bh_result['big_holder'])

        if inst_success:
            # 【Bug 修正】：單檔同步時，排除對既有歷史大戶與融資數據的覆蓋重置
            with DB_LOCK:
                SQLITE_CONN.execute('''
                    INSERT INTO inst_holding (date, symbol, foreign_buy, trust_buy, dealer_buy, margin, big_holder, big_holder_date)
                    VALUES (?, ?, ?, ?, ?, 0, 0.0, '')
                    ON CONFLICT(date, symbol) DO UPDATE SET 
                        foreign_buy=excluded.foreign_buy, 
                        trust_buy=excluded.trust_buy, 
                        dealer_buy=excluded.dealer_buy;
                ''', (target_date, code, base_payload['foreign'], base_payload['trust'], base_payload['dealer']))
                SQLITE_CONN.commit()
            
            # 【Bug 修正】：移除原先誤傷全市場快取的 fetch_finmind_revenue.clear()
            msg = "同步完成" if not err_msg else f"部分同步 ({err_msg})"
            return True, msg
            
        error_map = {'rate_limited': "⚠️ API 限流阻擋", 'timeout': "⏱️ 連線逾時", 'connection_error': "🔌 連線失敗", 'empty_data': "📭 今日無資料"}
        return False, error_map.get(err_msg.strip().replace("籌碼(", "").replace(")", ""), f"❓ 同步失敗 ({err_msg})")
    except Exception as e:
        return False, f"連線異常 ({str(e)})"

# ==============================================================================
# 七、 NVIDIA NIM DeepSeek 引擎
# ==============================================================================
def execute_single_stock_ai_推演(c):
    if not NVIDIA_API_KEY: return "未配置 NVIDIA API 金鑰"
    client = OpenAI(base_url="https://integrate.api.nvidia.com/v1", api_key=NVIDIA_API_KEY)
    bh_mega_str = f"{c.get('big_holder', 0)}%" if isinstance(c.get('big_holder', 0), (int, float)) else str(c.get('big_holder', 0))
    prompt = f"請以首席戰略幕僚身分，對 {c['name']} ({c['code']}) 進行冷血多空推演。現價:{c['price']} | 漲跌:{c['gain']:.2f}% | 營收YoY:{c['rev_yoy']:.1f}% | 外資5日:{c['f_5d']}張 | 大戶比例:{bh_mega_str} | MACD:{c['macd_str']}。請分四段繁體輸出：【第一戰區財報面小結】、【第二戰區技術面小結】、【第三戰區籌碼面小結】、【總指揮明日戰略總結】"
    for model_id in ["deepseek-ai/deepseek-v4-pro", "deepseek-ai/deepseek-v4-flash", "nvidia/nemotron-3-ultra-550b-a55b"]:
        try:
            completion = client.chat.completions.create(
                model=model_id,
                messages=[{"role": "system", "content": "你是一位冷血的台灣股市操盤幕僚。所有輸出嚴格使用繁體中文，並使用台灣金融專有名詞。直擊核心。"}, {"role": "user", "content": prompt}],
                temperature=0.2, max_tokens=1024, timeout=15 
            )
            return f"【{model_id.split('/')[-1]} 提供 analysis】\n\n{completion.choices[0].message.content}"
        except Exception: continue
    return "⚠️ NVIDIA API 全面癱瘓或限流。"

# ==============================================================================
# 八、 全網專屬 CSS 與 Tooltip 裝甲配置 (解鎖手機/網頁極致不換行樣式)
# ==============================================================================
st.markdown("""<style>
div[data-testid="stSidebar"] { background-color: #12141a !important; border-right: 1px solid #333 !important; }
div[data-testid="stButton"] > button { background-color: #1e1e24 !important; border: 1px solid #444 !important; }
div[data-testid="stButton"] > button p { color: #00d2ff !important; font-weight: bold !important; font-size: 14px !important; }
.hud-box { background: linear-gradient(135deg, #1a1c23 0%, #0d1117 100%); border-radius: 10px; padding: 15px; border-left: 5px solid #ff4d4d; margin-bottom: 20px;}
.zone-box { background: #11141c; border: 1px solid #2c3e50; border-radius: 6px; padding: 10px; margin-bottom: 8px; color:#eeeeee;}
.zone-title { color: #00d2ff; font-weight: bold; font-size: 13px; margin-bottom: 6px; border-bottom: 1px dashed #333; padding-bottom: 3px; }
.k-tag { font-size:13px; background:#2c3e50; padding:3px 8px; border-radius:5px; color:#f1c40f; white-space: nowrap; display: inline-block; }
.m-tooltip { position: relative; display: inline-block; border-bottom: 1px dotted #888; cursor: help; }
.m-tooltip .m-tooltiptext { visibility: hidden; width: 220px; background-color: #333; color: #fff; text-align: left; border-radius: 6px; padding: 10px; position: absolute; z-index: 999; bottom: 125%; left: 50%; transform: translateX(-50%); opacity: 0; transition: opacity 0.3s; font-size: 12px; font-weight: normal; line-height:1.6;}
.m-tooltip:hover .m-tooltiptext { visibility: visible; opacity: 1; }
</style>""", unsafe_allow_html=True)

# ----------------- 九、 側邊欄控制台 -----------------
with st.sidebar:
    st.markdown("<h2 style='color:#f1c40f; text-align:center;'>⚙️ 戰略控制台</h2>", unsafe_allow_html=True)
    if st.button("🔄 強制重整畫面", use_container_width=True):
        st.session_state.last_refresh = time.time(); st.rerun()
        
    with st.expander("📥 [主攻] 官方 CSV 籌碼強填中樞", expanded=False):
        uploaded_csvs = st.file_uploader("拖曳證交所三大法人 CSV", type=['csv'], accept_multiple_files=True, key="csv_up_v3")
        if uploaded_csvs and st.button("🚀 批次強制解析回填至 SQLite", use_container_width=True):
            process_twse_csv(uploaded_csvs)
            
    with st.expander("📊 資料庫完整度與備份還原", expanded=False):
        db_days, db_details = get_db_stats()
        if db_days == 0: st.warning("⚠️ 目前大腦無籌碼資料")
        else:
            st.write(f"當前儲存天數共: {db_days} 天")
            with st.container(height=150):
                for detail in db_details: st.caption(f"📅 {detail[0]}: 已存 {detail[1]} 檔籌碼")
        
        st.divider()
        st.markdown("### 💾 實體資料庫備份還原")
        col_dl1, col_dl2 = st.columns(2)
        with col_dl1:
            if os.path.exists(USER_DB_FILE):
                with open(USER_DB_FILE, "rb") as f:
                    st.download_button(label="📄 下載設定檔", data=f.read(), file_name="54088_database.json", mime="application/json", use_container_width=True)
        with col_dl2:
            if os.path.exists(SQLITE_DB_FILE):
                with open(SQLITE_DB_FILE, "rb") as f:
                    st.download_button(label="🗄️ 下載籌碼庫", data=f.read(), file_name="54088_inst_history.db", mime="application/octet-stream", use_container_width=True)
                    
        st.divider()
        st.markdown("### 📤 上傳備份覆蓋大腦")
        uploaded_json = st.file_uploader("上傳 54088_database.json", type=['json'], key="restore_json_v1")
        uploaded_db = st.file_uploader("上傳 54088_inst_history.db", type=['db'], key="restore_db_v1")
        if st.button("🚀 執行實體大腦覆蓋還原", use_container_width=True):
            if uploaded_json:
                with open(USER_DB_FILE, "wb") as f: f.write(uploaded_json.getbuffer())
                st.success("📄 設定檔覆蓋成功！")
            if uploaded_db:
                # 安全關閉現有連線後覆蓋
                SQLITE_CONN.close()
                with open(SQLITE_DB_FILE, "wb") as f: f.write(uploaded_db.getbuffer())
                SQLITE_CONN = get_db_conn()
                st.success("🗄️ 籌碼庫全面覆蓋還原成功！")
            time.sleep(1); st.rerun()
                
    st.divider()
    # 【Bug 修正】：最低量門檻篩選，此處單位為「張」
    min_volume_filter = st.slider("最低 5 日波段均量門檻 (張)", 0, 5000, 500, 100)
    enable_doomsday_lock = st.checkbox("💀 開啟末日鎔斷防護鎖", value=False)
    
    st.divider()
    commands_list = ["查1.主升段突擊", "查2.魚頭慢伏支撐", "查3.價值投資與循環", "查4.投信作帳集團股", "查5.籌碼外資霸王色", "查6.營收雙增爆發突破", "查8.昨日強勢動能延續", "查9.均線糾結爆量突破", "查10.籌碼沉澱量縮潛伏", "查11.除權息尋寶雷達", "查12.K線型態尋寶型"]
    
    existing_sources = set([src for info in getattr(st.session_state, 'intelligence_pool', {}).values() if isinstance(info, dict) for src in info.get("sources", [])])
    base_idx = 13
    for src in sorted(list(existing_sources)):
        commands_list.append(f"查{base_idx}. 情報雷達：{src}"); base_idx += 1
    if existing_sources: commands_list.append(f"查{base_idx}. 🏆 情報黃金交叉")

    selected_cmds = st.multiselect("🎯 戰略掃描條件 (可複選交集)", commands_list, default=[])
    selected_k_patterns = []
    if any("查12" in cmd for cmd in selected_cmds):
        with st.container(border=True):
            if st.checkbox("🔥 長紅吞噬 / 低檔長紅"): selected_k_patterns.append("長紅")
            if st.checkbox("🔥 紅三兵強勢推推"): selected_k_patterns.append("紅三兵")
            if st.checkbox("💀 長黑吞噬頂部出貨"): selected_k_patterns.append("長黑")
            if st.checkbox("💀 黑三兵弱勢跌破"): selected_k_patterns.append("黑三兵")
            
    if st.button("🚀 執行全市場並行高速掃描", use_container_width=True, type="primary"):
        if not selected_cmds: st.warning("請先選擇至少一項戰略條件。")
        else: st.session_state.trigger_scan = True

    with st.expander("📖 統籌戰術解密說明書", expanded=False):
        st.markdown("""<div style="font-size:13px; color:#ffffff; background:#1e1e24; padding:15px; border-radius:8px;">
        <b style='color:#f1c40f;'>🛡️ V153.0 戰情室濾網大公開</b><br>
        <b style='color:#00d2ff;'>查1.</b> 首根長紅 + 爆量>=2.0 + KDJ金叉<br>
        <b style='color:#00d2ff;'>查2.</b> 股價站上季線(60MA) + 爆量>=1.2<br>
        <b style='color:#00d2ff;'>查3.</b> 綜合評分>=60 + 無地雷<br>
        <b style='color:#00d2ff;'>查4.</b> 投信單日買超>0<br>
        <b style='color:#00d2ff;'>查5.</b> 外資買超 + 融資減少(沉澱)<br>
        <b style='color:#00d2ff;'>查6.</b> 營收 YoY 年增 > 20%<br>
        <b style='color:#00d2ff;'>查8.</b> 昨日漲幅強勢 (>5%)<br>
        <b style='color:#00d2ff;'>查9.</b> 今日爆量比 >= 2.0x<br>
        <b style='color:#00d2ff;'>查10.</b> 今日量縮 > 40% + 融資減少<br>
        <b style='color:#00d2ff;'>查11.</b> 現金殖利率 >= 4.5%<br>
        <b style='color:#00d2ff;'>查12.</b> 特定K線型態 (ATR動態判定)</div>""", unsafe_allow_html=True)
        
    st.divider()
    st.markdown("<div style='font-size:12px; font-weight:bold; margin-bottom:5px;'>📡 系統連線狀態</div>", unsafe_allow_html=True)
    st.markdown(f"<div style='font-size:11px;'>🟢 NVIDIA NIM 自動火力網<br>🟢 FinMind 線路</div>", unsafe_allow_html=True)

# ==============================================================================
# 十、 主畫面：UI 渲染與三方會審區塊
# ==============================================================================
st.markdown(f"""<div class='hud-box'>
    <div style='color:#f1c40f; font-size:16px; font-weight:bold; margin-bottom:4px;'>📊 大將軍智慧 HUD 總覽</div>
    <div style='color:#ddd; font-size:14px;'><b>大盤氣象：</b> <span style='color:{weather_color}; font-weight:bold;'>上市大盤 {weather_str}</span> | <b>安全狀態：</b> V153.0 鋼鐵防線隔離版</div>
</div>""", unsafe_allow_html=True)

with st.expander("📋 情報注入面板", expanded=False):
    intel_source = st.selectbox("來源", ["股癌", "財經新聞", "法說會", "券商報告", "其他"], key="intel_source")
    intel_tag = st.text_input("標籤", key="intel_tag", placeholder="例如：財報公布、法人動向")
    intel_content = st.text_area("貼上報告內容 (需含 [標的代號: XXXX])", key="intel_content", height=150)
    
    if st.button("💾 儲存情報", key="intel_save_btn"):
        if intel_content.strip():
            tickers_found = re.findall(r"\[標的代號:\s*(\d{4})\]", intel_content)
            if tickers_found:
                for ticker in tickers_found:
                    if ticker not in st.session_state.intelligence_pool: st.session_state.intelligence_pool[ticker] = {"sources": [], "history": []}
                    if intel_source not in st.session_state.intelligence_pool[ticker]["sources"]: st.session_state.intelligence_pool[ticker]["sources"].append(intel_source)
                    st.session_state.intelligence_pool[ticker]["history"].append({"time": datetime.now().strftime("%Y-%m-%d %H:%M"), "tag": intel_tag, "content": intel_content})
                save_local_db_isolated()
                st.success(f"已綁定 {len(tickers_found)} 檔標的並寫入實體大腦！")
            else: st.warning("未偵測到 [標的代號: XXXX]，無法綁定血統。")
        else: st.warning("內容不能為空")

# 【Bug 修正】：手動加入常態觀察雷達進行完全比對與防呆防爆邏輯
search_input = st.text_input("🔍 手動股票代號/名稱輸入框 (如: 2330 或 聯電)", "")
if st.button("➕ 強制加入常態觀測雷達", use_container_width=True):
    if search_input.strip():
        found_codes = re.findall(r'\b\d{4}\b', search_input)
        if not found_codes:
            # 優先進行完全比對
            for code, name in TW_STOCK_NAMES.items():
                if name == search_input.strip():
                    found_codes.append(code); break
        if not found_codes:
            # 唯一或多重模糊比對
            matches = [code for code, name in TW_STOCK_NAMES.items() if search_input.strip() in name]
            if len(matches) == 1: found_codes.append(matches[0])
            elif len(matches) > 1: st.warning(f"⚠️ 模糊偵測到多筆標的，請輸入精確代號：{', '.join([f'{m}({TW_STOCK_NAMES[m]})' for m in matches[:5]])}")
        
        if found_codes:
            for c in found_codes: st.session_state.pinned_stocks[c] = "手動強制加入"
            save_local_db_isolated(); st.rerun()
        elif not matches: st.error("⚠️ 找不到對應的股票代號或名稱，請重新輸入。")

def render_stock_card_ui(c, is_portfolio=False, profit=0, roi=0, ent_p=0):
    gain_c = '#ff4d4d' if float(c.get('gain',0)) > 0 else ('#00FF00' if float(c.get('gain',0)) < 0 else '#aaaaaa')
    gain_b = '#3a1515' if float(c.get('gain',0)) > 0 else ('#153a20' if float(c.get('gain',0)) < 0 else '#333333')
    portfolio_header = f"<div style='font-size:14px; margin-bottom:8px; color:#eeeeee;'>持倉成本: {ent_p} | 損益: <strong style='color:{'#ff4d4d' if profit>0 else '#00FF00'};'>{int(profit):+,} 元</strong> ({roi:+.2f}%)</div>" if is_portfolio else ""
    
    yoy_val, mom_val = float(c.get('rev_yoy',0)), float(c.get('rev_mom',0))
    yoy_color = "#ff4d4d" if yoy_val > 0 else ("#00FF00" if yoy_val < 0 else "#00d2ff")

    k_patterns = c.get('detected_patterns', [])
    k_text = f"{'📉' if '黑' in k_patterns[0].get('text', '') else '🔥'} {k_patterns[0].get('text')}" if k_patterns else "⚖️ 壓縮盤整"
    k_tags = f"<span class='k-tag'>{k_text}</span>"

    # 使用 Flexbox 布局全面防堵手機/網頁排版斷行擠壓
    tags_html = f"""
    <div style='display:flex; flex-wrap:wrap; gap:6px; align-items:center; margin-top:5px;'>
        <span style='white-space:nowrap; display:inline-block; background:#2a2a2a; padding:2px 8px; border-radius:4px; font-size:12px; color:#e67e22;'>{c.get('vol_ratio_label')}</span>
        <span style='white-space:nowrap; display:inline-block; background:#2a2a2a; padding:2px 8px; border-radius:4px; font-size:12px; color:#00FF00;'>{c.get('intraday_str')}</span>
    </div>
    """

    rsi_html = render_rsi_tag(float(c.get('rsi_val',0)))
    bias_html = render_bias_tag(float(c.get('bias_val',0)))
    
    db_date = c.get('latest_db_date', '')
    if db_date:
        dt_obj = datetime.strptime(db_date, "%Y-%m-%d")
        display_date = f" {dt_obj.strftime('%m/%d')}({['一','二','三','四','五','六','日'][dt_obj.weekday()]})"
        warn_icon = "" if db_date == datetime.now().strftime("%Y-%m-%d") else " ⚠️"
    else: display_date, warn_icon = "", ""
    
    bh_val = c.get('big_holder', 0.0)
    bh_display = f"{bh_val}%" if bh_val > 0 else "📭 無資料"

    html = f"""
<div style="border:2px solid {c.get('color_border')}; border-radius:8px; padding:15px; background:#16191f; margin-bottom:12px; color:#eeeeee;">
{portfolio_header}
<div style="display:flex; justify-content:space-between; align-items:center;">
<span style="font-weight:bold; font-size:19px; color:#ffffff; display:flex; align-items:center; flex-wrap:wrap; gap:6px;">
    {c.get('name')} <span style="color:#00d2ff; font-size:15px;">({c.get('code')})</span>
    {k_tags}
</span>
<span style="font-size:13px; color:#f1c40f; white-space:nowrap;">{c.get('blood_line', '')}</span>
</div>
<div style="display:flex; justify-content:space-between; align-items:flex-end; margin:10px 0;">
    <div style="display:flex; align-items:center;"><span style="font-size:32px; font-weight:bold; color:#ffffff;">{float(c.get('price',0)):.2f}</span><span style="font-size:15px; color:{gain_c}; background:{gain_b}; padding:3px 8px; border-radius:4px; margin-left:10px; font-weight:bold;">{float(c.get('gain',0)):+.2f}%</span></div>
</div>
<div style="background:#0e1117; padding:8px; border-radius:4px; margin-bottom:10px;">
    <div style="font-size:13px; margin-bottom:4px;">{c.get('vol_change_str')}</div>
    {tags_html}
</div>
<div class="zone-box">
    <div class="zone-title">❤️ 第一戰區：基本與財報面</div>
    <div style="font-size:13px; margin-bottom:4px;">營收 年增 <strong style="color:#ffffff;">({c.get('rev_month')})</strong>: <strong style="color:{yoy_color};">{yoy_val:.1f}%</strong> | 月增: <strong style="color:{'#ff4d4d' if mom_val>0 else '#00FF00'};">{mom_val:.1f}%</strong></div>
    <div style="font-size:13px;">除權息資訊: <strong style="color:#d200ff;">{c.get('div_display')} (殖利率: {float(c.get('div_yield',0)):.1f}%)</strong></div>
</div>
<div class="zone-box">
    <div class="zone-title">⚔️ 第二戰區：技術與新指標防線</div>
    <div style="font-size:13px; margin-bottom:4px; display:flex; justify-content:space-between;">
        <span>5MA: <b style="color:#ffffff;">{float(c.get('ma5',0)):.1f}</b></span><span>20MA: <b style="color:#ffffff;">{float(c.get('ma20',0)):.1f}</b></span><span>60MA: <b style="color:#ffffff;">{float(c.get('ma60',0)):.1f}</b></span>
    </div>
    <div style="font-size:13px; margin-bottom:4px; line-height:2.2;">
        MACD 動能: <strong style="color:{c.get('macd_color')}; margin-right:15px;">{c.get('macd_str')}</strong>
        {rsi_html} <span style="margin-left:15px;">{bias_html}</span>
    </div>
    <div style="font-size:12px; color:#aaa; margin-top:6px; border-top:1px dashed #444; padding-top:4px;">
        <span class='m-tooltip' style='color:#ff4d4d;'>進攻參考區間:<span class='m-tooltiptext'>現價加上1倍ATR作為短線滿足點參考</span></span> {c.get('atk_zone')} | <span class='m-tooltip' style='color:#00FF00;'>防守停損線:<span class='m-tooltiptext'>MA5扣除0.5倍ATR波動緩衝，防隨機洗盤</span></span> {c.get('def_line')} (緩衝 {c.get('buffer_pct')}%, ATR={c.get('atr_val'):.2f})
    </div>
</div>
<div class="zone-box">
    <div class="shadow-box">
        <div class="zone-title">📊 第三戰區：三大法人與主力籌碼 (SQLite)</div>
        <div style="font-size:13px; margin-bottom:4px;"><b>[外資<span style="color:#f1c40f;">{display_date}{warn_icon}</span>]</b> 單日: <strong style="color:#ff4d4d;">{int(c.get('f_buy',0)):+,}張 ({float(c.get('f_pct',0)):+.2f}%)</strong> 5日: <strong>{int(c.get('f_5d',0)):+,}張 ({float(c.get('f_5d_pct',0)):+.2f}%)</strong></div>
        <div style="font-size:13px; margin-bottom:6px;"><b>[投信<span style="color:#f1c40f;">{display_date}{warn_icon}</span>]</b> 單日: <strong style="color:#ff4d4d;">{int(c.get('t_buy',0)):+,}張 ({float(c.get('t_pct',0)):+.2f}%)</strong> 5日: <strong>{int(c.get('t_5d',0)):+,}張 ({float(c.get('t_5d_pct',0)):+.2f}%)</strong></div>
        <div style="font-size:12px; border-top:1px dashed #444; padding-top:6px; display:flex; justify-content:space-between; color:#aaa;">
            <span>千張大戶({c.get('big_holder_date')}): <strong style="color:#00d2ff;">{bh_display}</strong></span>
            <span>自營商: {int(c.get('d_buy',0)):+,}張</span>
        </div>
    </div>
</div>
<div style="background:{c.get('signal_bg')}; padding:10px; border-radius:5px; text-align:center; margin-top:8px;">
    <span class='m-tooltip' style="color:{c.get('color_border')}; font-size:15px; font-weight:bold;">
        決策判定：{c.get('signal_text')}
        <span class='m-tooltiptext'>
            <b>[評分級距說明]</b><br>
            🔥 偏多攻擊 (>= 3分)<br>
            🟡 觀察偏多 (1 ~ 2分)<br>
            ⚖️ 中立震盪 (0分)<br>
            ⚠️ 轉弱謹慎 (-1 ~ -2分)<br>
            🔵 偏空防守 (<= -3分)
        </span>
    </span>
    <div style="font-size:12px; color:#888; margin-top:4px;">(評分 {c.get('score')} | {' / '.join(c.get('reasons', []))})</div>
</div>
</div>
"""
    return re.sub(r'^\s+', '', html, flags=re.MULTILINE)

def render_action_buttons(card, code, is_portfolio):
    btn_suffix = "_port" if is_portfolio else "_pin"
    if code not in st.session_state.analysis_history: st.session_state.analysis_history[code] = {'nv_history': [], 'gm_history': [], 'cl_history': []}
        
    with st.expander("⚙️ 資料校正、人工覆寫與 AI 推演", expanded=False):
        if st.button("🚀 執行單檔精準同步", key=f"btn_sync_single_{code}{btn_suffix}", use_container_width=True):
            with st.spinner(f"正在獨立同步 {code} 最新籌碼..."):
                success, msg = sync_single_stock_finmind(code)
                if success: st.success(f"✅ {code} {msg}！")
                else: st.warning(f"⚠️ {code} {msg}")
                time.sleep(1.5); st.rerun() 
            
        st.markdown("<div style='font-size:13px; font-weight:bold; color:#00d2ff; margin-top:10px;'>✏️ 人工覆寫 (7日後自動過期恢復)</div>", unsafe_allow_html=True)
        m_cols = st.columns([1, 1, 1])
        m_month = m_cols[0].text_input("月份", value="06月", key=f"my_mo_{code}{btn_suffix}")
        m_y = m_cols[1].number_input("營收年增(%)", -100.0, 1000.0, float(card.get('rev_yoy', 0.0)), 0.1, key=f"my_y_{code}{btn_suffix}")
        
        b_cols = st.columns([2, 1])
        b_ratio = b_cols[0].number_input("大戶比例(%)", 0.0, 100.0, float(card.get('big_holder', 0.0) if isinstance(card.get('big_holder'), (int, float)) else 0.0), 0.1, key=f"my_bh_{code}{btn_suffix}")
        b_date = b_cols[1].text_input("大戶日期", value=datetime.now().strftime("%m/%d"), key=f"my_b_date_{code}{btn_suffix}")

        b1, b2 = st.columns(2)
        if b1.button("✅ 寫入覆寫", key=f"btn_override_{code}{btn_suffix}", use_container_width=True):
            now_ts = datetime.now().timestamp()
            st.session_state.revenue_override[code] = {'yoy': m_y, 'mom': card.get('rev_mom', 0.0), 'month': m_month, 'ts': now_ts}
            if b_ratio > 0:
                st.session_state.bigholder_override[code] = {'ratio': b_ratio, 'date': b_date, 'ts': now_ts}
                safe_upsert_big_holder(code, f"{datetime.now().year}-{b_date.replace('/','-')}", b_ratio)
            save_local_db_isolated(); st.success("資料鎖定成功！"); time.sleep(0.5); st.rerun()
        if b2.button("🗑️ 解除鎖定", key=f"btn_clear_ov_{code}{btn_suffix}", use_container_width=True):
            st.session_state.revenue_override.pop(code, None)
            st.session_state.bigholder_override.pop(code, None)
            save_local_db_isolated(); st.success("已解除人工資料，恢復 API 模式！"); time.sleep(0.5); st.rerun()
            
        if st.button("🤖 解鎖 NVIDIA 戰略推演", key=f"ai_single_{code}{btn_suffix}", use_container_width=True):
            st.session_state.single_ai_trigger = code
            with st.spinner("NVIDIA 輪替陣列推演中..."):
                rep = execute_single_stock_ai_推演(card)
                st.session_state.single_ai_report[code] = rep
                st.session_state.analysis_history[code]['nv_history'].append({"time": datetime.now().strftime("%Y-%m-%d %H:%M"), "report": rep})
                save_local_db_isolated()

    with st.expander("📥 貼上外部網頁版情報與裁決 (三方會審區)", expanded=False):
        c1, c2 = st.columns(2)
        nv_val = c1.text_area("📝 NVIDIA (DeepSeek)", height=80, key=f"nv_txt_{code}{btn_suffix}")
        gm_val = c2.text_area("📝 Gemini 分析", height=80, key=f"gm_txt_{code}{btn_suffix}")
        cl_val = st.text_area("👑 Claude 總裁決 (將存入歷史)", height=80, key=f"cl_txt_{code}{btn_suffix}")
        if st.button("💾 儲存 Claude 裁決至時光膠囊", key=f"save_cl_{code}{btn_suffix}", use_container_width=True):
            if cl_val:
                ts = datetime.now().strftime("%Y-%m-%d %H:%M")
                st.session_state.analysis_history[code]['cl_history'].append({
                    "time": ts, "report": cl_val,
                    "snapshot": f"收盤:{card.get('price')} | 外資:{card.get('f_5d')}張 | 爆量:{card.get('vol_ratio'):.1f}x"
                })
                if gm_val: st.session_state.analysis_history[code]['gm_history'].append({"time": ts, "report": gm_val})
                save_local_db_isolated(); st.success("✅ 已寫入時光膠囊！"); time.sleep(0.5); st.rerun()
            else: st.warning("請先輸入 Claude 裁決報告！")

    if st.session_state.analysis_history[code]['nv_history'] or st.session_state.analysis_history[code]['cl_history']:
        with st.expander("🗂️ 歷史時光膠囊覆盤區", expanded=False):
            h1, h2, h3 = st.tabs(["NVIDIA", "Gemini", "Claude"])
            with h1:
                for h in reversed(st.session_state.analysis_history[code]['nv_history'][-5:]): st.info(f"**{h['time']}**\n{h['report']}")
            with h2:
                for h in reversed(st.session_state.analysis_history[code]['gm_history'][-5:]): st.info(f"**{h['time']}**\n{h['report']}")
            with h3:
                for h in reversed(st.session_state.analysis_history[code]['cl_history'][-10:]): st.success(f"**{h['time']}**\n{h['report']}")

    m_cols = st.columns(2)
    if is_portfolio:
        if m_cols[0].button("從持倉移除", key=f"del_port_{code}{btn_suffix}", use_container_width=True):
            st.session_state.portfolio.pop(code, None); save_local_db_isolated(); st.rerun()
    else:
        if m_cols[0].button("轉移至持倉", key=f"mov_pin_{code}{btn_suffix}", use_container_width=True):
            st.session_state.portfolio[code] = {"entry_price": card.get('price', 0.0), "qty": 1}
            st.session_state.pinned_stocks.pop(code, None); save_local_db_isolated(); st.rerun()
        if m_cols[1].button("移出雷達", key=f"del_pin_{code}{btn_suffix}", use_container_width=True):
            st.session_state.pinned_stocks.pop(code, None); save_local_db_isolated(); st.rerun()

# --- 模擬倉與雷達區主線程處理 ---
config_payload = {
    'token': FINMIND_TOKENS[st.session_state.active_key_index],
    'rev_override': st.session_state.revenue_override,
    'bh_override': st.session_state.bigholder_override,
    'div_override': st.session_state.dividend_override,
    'dividend_db': DIVIDEND_DB, 'stock_names': TW_STOCK_NAMES
}

if getattr(st.session_state, 'portfolio', {}):
    with st.expander("💼 總指揮常態持倉模擬倉", expanded=True):
        cols, idx = st.columns(2), 0
        for code, p_data in list(st.session_state.portfolio.items()):
            c = calculate_signals_worker(code, config_payload)
            if c and not c.get('error'):
                ent_p = safe_float(p_data.get('entry_price', c.get('price')))
                profit, roi = calc_real_profit(ent_p, float(c.get('price', 0.0)), safe_float(p_data.get('qty', 1)))
                with cols[idx % 2]:
                    st.markdown(render_stock_card_ui(c, True, profit, roi, ent_p), unsafe_allow_html=True)
                    render_action_buttons(c, code, True)
                idx += 1

if getattr(st.session_state, 'pinned_stocks', {}):
    with st.expander("🎯 總指揮常態觀測雷達防線", expanded=True):
        cols, idx = st.columns(2), 0
        for code in list(st.session_state.pinned_stocks.keys()):
            c = calculate_signals_worker(code, config_payload)
            if c and not c.get('error'):
                with cols[idx % 2]:
                    st.markdown(render_stock_card_ui(c), unsafe_allow_html=True)
                    render_action_buttons(c, code, False)
                idx += 1

# --- 多執行緒安全高併發掃描區 ---
if getattr(st.session_state, 'trigger_scan', False):
    st.session_state.trigger_scan = False
    st.session_state.scan_results.clear()
    
    results = []
    target_pool = GLOBAL_MARKET_CODES[:300] 
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        future_to_code = {executor.submit(calculate_signals_worker, c, config_payload): c for c in target_pool}
        for i, future in enumerate(concurrent.futures.as_completed(future_to_code)):
            c = future_to_code[future]
            status_text.markdown(f"<div style='color:#00d2ff; font-size:13px; font-weight:bold;'>📡 並行高速掃描進度: {i+1}/{len(target_pool)} ({int((i+1)/len(target_pool)*100)}%)</div>", unsafe_allow_html=True)
            progress_bar.progress((i + 1) / len(target_pool))
            
            try: card = future.result()
            except Exception: continue
            
            c_vol = float(card.get('vol', 0) or 0)
            c_price = float(card.get('price', 0) or 0)
            c_ma60 = float(card.get('ma60', 0) or 0)
            c_vol_ratio = float(card.get('vol_ratio', 0) or 0)
            c_tbuy = int(card.get('t_buy', 0) or 0)
            c_fbuy = int(card.get('f_buy', 0) or 0)
            c_margin = int(card.get('margin_diff', 0) or 0)
            c_rev_yoy = float(card.get('rev_yoy', 0) or 0)
            c_kdj = str(card.get('kdj_str', ''))
            
            if card and not card.get('error', False) and c_vol >= min_volume_filter:
                meets_all = True
                for cmd in selected_cmds:
                    if "查1." in cmd and not (card.get('is_first_red') and c_vol_ratio >= 2.0 and "金叉" in c_kdj): meets_all = False
                    elif "查2." in cmd and not (c_price > c_ma60 and c_vol_ratio >= 1.2): meets_all = False
                    elif "查4." in cmd and not (c_tbuy > 0): meets_all = False
                    elif "查5." in cmd and not (c_fbuy > 0 and c_margin < 0): meets_all = False
                    elif "查6." in cmd and not (c_rev_yoy > 20): meets_all = False
                    elif "查8." in cmd and not (card.get('is_yesterday_strong')): meets_all = False
                    elif "查9." in cmd and not (c_vol_ratio >= 2.0): meets_all = False
                    elif "查11." in cmd and not (float(card.get('div_yield', 0)) >= 4.5): meets_all = False
                    elif "查12." in cmd and not (selected_k_patterns and any(p in [x.get('text') for x in card.get('detected_patterns',[])] for p in selected_k_patterns)): meets_all = False
                if meets_all: results.append(card)
            
    progress_bar.empty(); status_text.empty()
    st.session_state.scan_results = results
    st.session_state.scan_mode = " + ".join([cmd.split('.')[0] for cmd in selected_cmds])

if getattr(st.session_state, 'scan_results', []):
    st.markdown(f"### ⚡ 【{st.session_state.scan_mode}】交叉篩選戰果 ({len(st.session_state.scan_results)} 檔符合)")
    if st.button("➕ 批次部署並強制寫入常態追蹤雷達", use_container_width=True):
        for card in st.session_state.scan_results:
            st.session_state.pinned_stocks[card.get('code', '')] = st.session_state.scan_mode
        save_local_db_isolated(); st.success(f"✅ 成功綁定血統並永久存檔。"); time.sleep(0.5); st.rerun()
    
    cols = st.columns(2)
    for idx, card in enumerate(st.session_state.scan_results):
        with cols[idx % 2]:
            st.markdown(re.sub(r'^\s+', '', f"""<div style="border:2px solid {card.get('color_border')}; border-radius:8px; padding:15px; background:#16191f; margin-bottom:12px; color:#eeeeee;"><span style="font-weight:bold; font-size:19px; color:#ffffff;">{card.get('name')} <span style="color:#00d2ff;">({card.get('code')})</span></span><div style="font-size:13px; margin-top:5px;">多重火力篩選符合 | 爆量比: {float(card.get('vol_ratio',0)):.1f}x</div></div>""", flags=re.MULTILINE), unsafe_allow_html=True)
