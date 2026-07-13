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
        self.reason = reason  
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
# 二、 資料庫架構升級 (SQLite + 原子寫入 JSON + 防崩潰鎖)
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
        'intelligence_pool': {}, 'analysis_history': {}, 'last_refresh': time.time()
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

# 【完整載入 API Key，防止 NameError 當機】
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
# 三、 基礎運算與 API 取資料核心
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
    return f"{icon} {label} | {vol_pct:+.1f}%"

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

# 【已修復：補回大盤絕對點數函數】
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

# 【已修復：補回 yfinance 取值，且統一將 Volume 轉為張】
@st.cache_data(ttl=120, show_spinner=False)
def get_real_stock_data_yfinance(symbol):
    for ext in [".TW", ".TWO"]:
        try:
            tk = yf.Ticker(symbol + ext, session=_SESSION)
            hist = tk.history(period="6mo", timeout=4).dropna(subset=['Close'])
            hist = hist[hist['Volume'] > 0]
            hist['Volume'] = hist['Volume'] / 1000.0
            if not hist.empty and len(hist) > 20: 
                return hist.tail(90), tk.info
        except: pass
    return None, {}

# ==============================================================================
# 四、 動態技術指標與 ATR 交易邏輯重構
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
    if current_price > ma5 > ma20: score += 2; reasons.append("站穩多頭")
    elif current_price > ma5: score += 1; reasons.append("站上5MA")
    elif current_price < ma5: score -= 2; reasons.append("跌破5MA")
    
    if foreign_buy > 0: score += 1; reasons.append(f"外資買進")
    elif foreign_buy < 0: score -= 1; reasons.append(f"外資賣出")
    
    if vol_ratio < 0.6: score -= 1; reasons.append("量縮力竭")
    elif vol_ratio > 2.0: score += 1; reasons.append("爆量")
    
    if is_open_high_close_low: score -= 2; reasons.append("開高走低")
    if buffer_pct < 1.0: score -= 1; reasons.append(f"緩衝僅{buffer_pct:.1f}%")
    
    if score >= 3: return "🔥 偏多攻擊", "#ff4d4d", score, reasons
    elif score >= 1: return "🟡 觀察偏多", "#ffab00", score, reasons
    elif score <= -3: return "🔵 偏空防守", "#2979ff", score, reasons
    elif score <= -1: return "⚠️ 轉弱謹慎", "#ff9100", score, reasons
    else: return "⚖️ 中立震盪", "#888", score, reasons

# ==============================================================================
# 五、 核心訊號與戰區聚合核心 (支援 SQLite)
# ==============================================================================
def get_inst_data_from_db(symbol, limit=10):
    try:
        df = pd.read_sql('SELECT * FROM inst_holding WHERE symbol=? ORDER BY date DESC LIMIT ?', SQLITE_CONN, params=(symbol, limit))
        return df
    except Exception: return pd.DataFrame()

# 【已修復：強悍的行內 CSS 標籤渲染防斷行】
def render_rsi_tag(rsi_value):
    if rsi_value > 70: tag = "<span style='background:#ff4d4d; color:#fff; padding:2px 6px; border-radius:4px; font-weight:bold; white-space:nowrap; display:inline-block;'>🔴超買</span>"
    elif rsi_value < 30: tag = "<span style='background:#00c853; color:#fff; padding:2px 6px; border-radius:4px; font-weight:bold; white-space:nowrap; display:inline-block;'>🟢超賣</span>"
    else: tag = "<span style='background:#555; color:#fff; padding:2px 6px; border-radius:4px; font-weight:bold; white-space:nowrap; display:inline-block;'>⚖️整理</span>"
    return f"<span class='m-tooltip'>RSI(14): {rsi_value:.1f} {tag}<span class='m-tooltiptext'>大於70超買，小於30超賣</span></span>"

def render_bias_tag(bias_value):
    if bias_value > 5: tag = "<span style='background:#ff4d4d; color:#fff; padding:2px 6px; border-radius:4px; font-weight:bold; white-space:nowrap; display:inline-block;'>🔴過熱</span>"
    elif bias_value < -5: tag = "<span style='background:#2979ff; color:#fff; padding:2px 6px; border-radius:4px; font-weight:bold; white-space:nowrap; display:inline-block;'>🔵超跌</span>"
    else: tag = ""
    return f"<span class='m-tooltip'>乖離率(20): {bias_value:+.2f}% {tag}<span class='m-tooltiptext'>大於+5%短線過熱，小於-5%短線超跌</span></span>"

def calculate_comprehensive_signals(symbol, config_payload=None, enable_doomsday=False):
    f_single = t_single = d_single = margin_diff = 0.0
    f_5d = t_5d = f_10d = t_10d = 0
    f_pct = t_pct = f_5d_pct = t_5d_pct = f_10d_pct = t_10d_pct = 0.0
    big_holder, big_holder_date = 0.0, ""
    latest_db_date = ""
    
    # 支援多執行緒安全傳遞
    bh_override = config_payload.get('bh_override', {}) if config_payload else getattr(st.session_state, 'bigholder_override', {})
    rev_override = config_payload.get('rev_override', {}) if config_payload else getattr(st.session_state, 'revenue_override', {})
    div_override = config_payload.get('div_override', {}) if config_payload else getattr(st.session_state, 'dividend_override', {})
    active_token = config_payload.get('token', FINMIND_TOKENS[0]) if config_payload else FINMIND_TOKENS[getattr(st.session_state, 'active_key_index', 0)]
    
    hist_pack = get_real_stock_data_yfinance(symbol)
    if hist_pack is None or not hist_pack[0] is not None: return {"code": symbol, "name": TW_STOCK_NAMES.get(symbol, symbol), "error": True}
    hist, info = hist_pack
    
    curr_price, prev_price, open_price = float(hist['Close'].iloc[-1]), float(hist['Close'].iloc[-2]), float(hist['Open'].iloc[-1])
    gain = ((curr_price - prev_price) / prev_price) * 100 if prev_price > 0 else 0
    vol_today, vol_yesterday = int(hist['Volume'].iloc[-1]), int(hist['Volume'].iloc[-2])
    vol_change_str = calc_volume_change(vol_today, vol_yesterday)
    vol_5d_mean = max(1, int(hist['Volume'].tail(5).mean()))
    vol_ratio = vol_today / vol_5d_mean if vol_5d_mean > 0 else 0
    
    ma5, ma20, ma60 = float(hist['Close'].tail(5).mean()), float(hist['Close'].tail(20).mean()), float(hist['Close'].mean())
    exp1, exp2 = hist['Close'].ewm(span=12, adjust=False).mean(), hist['Close'].ewm(span=26, adjust=False).mean()
    macd_hist = (exp1 - exp2) - (exp1 - exp2).ewm(span=9, adjust=False).mean()
    macd_val = macd_hist.iloc[-1] if not macd_hist.empty else 0
    macd_str, macd_color = (f"多方動能 ({macd_val:+.2f})", "#ff4d4d") if macd_val > 0 else (f"空方動能 ({macd_val:+.2f})", "#00FF00")
    
    low_min, high_max = hist['Low'].rolling(9).min(), hist['High'].rolling(9).max()
    rsv = (hist['Close'] - low_min) / (high_max - low_min + 1e-9) * 100
    calc_k, calc_d = rsv.bfill().ffill().ewm(com=2, adjust=False).mean(), rsv.bfill().ffill().ewm(com=2, adjust=False).mean().ewm(com=2, adjust=False).mean()
    kdj_str = "金叉向上" if not calc_k.empty and calc_k.iloc[-1] > calc_d.iloc[-1] else "死叉向下"
    
    rsi_val = calc_rsi(hist).iloc[-1]
    bias_val = calc_bias(hist).iloc[-1]
    atr_val = calculate_atr(hist)
    
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
        
        f_5d_pct = (f_5d / (vol_5d_mean*5) * 100) if vol_5d_mean > 0 else 0.0
        t_5d_pct = (t_5d / (vol_5d_mean*5) * 100) if vol_5d_mean > 0 else 0.0

    db_bh = get_latest_big_holder(symbol)
    if db_bh:
        big_holder, big_holder_date = db_bh['percent'], db_bh['date']
        
    if symbol in bh_override:
        big_holder = bh_override[symbol].get('ratio', big_holder)
        big_holder_date = f"自訂 {bh_override[symbol].get('date', '')}"

    manual_mode = False
    if symbol in rev_override:
        rev_yoy, rev_mom, rev_month, manual_mode = rev_override[symbol].get('yoy', 0.0), rev_override[symbol].get('mom', 0.0), rev_override[symbol].get('month', "自訂"), True
    else:
        fm_rev = fetch_finmind_revenue(symbol, active_token)
        rev_yoy, rev_mom, rev_month = fm_rev['yoy'], fm_rev['mom'], fm_rev['month']
        if fm_rev.get('stale'): rev_month += " (沿用)"

    manual_div_mode = False
    if symbol in div_override:
        div_display, div_yield, manual_div_mode = div_override[symbol].get('display', "自訂資料"), div_override[symbol].get('yield', 0.0), True
    else:
        div_info = DIVIDEND_DB.get(symbol)
        div_date_str = ""
        if div_info:
            d_cash, d_stock, div_date_str = div_info.get('cash', 0.0), div_info.get('stock', 0.0), div_info.get('date', '')
            div_yield = (d_cash / curr_price) * 100 if curr_price > 0 else 0.0
            div_display = f"{div_date_str} | 息 {d_cash}元 + 權 {d_stock}元" if d_stock > 0 else f"{div_date_str} | 息 {d_cash}元"
        else:
            d_cash = safe_float(info.get('dividendRate', 0.0))
            div_yield = (d_cash / curr_price) * 100 if curr_price > 0 else 0.0
            div_display = f"無日期 | 息 {d_cash}元" if d_cash > 0 else "無近期資訊"

    zones = build_trade_zones(curr_price, ma5, ma20, atr_val)
    signal_text, color_border, score, reasons = determine_signal(curr_price, ma5, ma20, f_single, vol_ratio, is_open_high_close_low, zones['buffer_pct'])
    signal_bg = "#3a1515" if "攻擊" in signal_text else ("#153a20" if "防守" in signal_text else "#332b00")
    
    detected_patterns = detect_k_line_patterns_v152(hist, atr_val)
    
    closes = hist['Close'].tail(7).tolist()
    while len(closes) < 7: closes.append(closes[-1] if closes else 0)
    bars, min_p, max_p = " ▂▃▄▅▆▇█", min(closes), max(closes)
    rng = max_p - min_p if max_p != min_p else 1e-9
    spark_html = "".join([f"<span style='color:{'#ff4d4d' if i>0 and closes[i]>closes[i-1] else ('#00FF00' if i>0 and closes[i]<closes[i-1] else '#888')}; font-weight:bold;'>{bars[max(0, min(7, int((closes[i] - min_p) / rng * 7)))]}</span>" for i in range(7)])
    
    intraday_trend = "📉 開高走低·弱勢收下" if is_open_high_close_low else "🔥 溫和震盪/收紅"
    
    return {
        "code": symbol, "name": TW_STOCK_NAMES.get(symbol, symbol), "price": curr_price, "gain": gain, "error": False,
        "vol": vol_today, "vol_change_str": vol_change_str, "vol_ratio": vol_ratio,
        "ma5": ma5, "ma20": ma20, "ma60": ma60, "macd_str": macd_str, "macd_color": macd_color, "kdj_str": kdj_str,
        "rsi_val": rsi_val, "bias_val": bias_val, "atr_val": atr_val,
        "f_buy": f_single, "t_buy": t_single, "d_buy": d_single, "margin_diff": margin_diff, "big_holder": big_holder, "big_holder_date": big_holder_date, 
        "f_5d": f_5d, "t_5d": t_5d, "f_10d": f_10d, "t_10d": t_10d, "f_pct": f_pct, "t_pct": t_pct, 
        "f_5d_pct": f_5d_pct, "t_5d_pct": t_5d_pct, "f_10d_pct": f_10d_pct, "t_10d_pct": t_10d_pct,
        "atk_zone": zones['atk_zone'], "def_line": zones['def_line'], "buffer_pct": zones['buffer_pct'],
        "rev_yoy": rev_yoy, "rev_mom": rev_mom, "rev_month": rev_month, 
        "div_display": div_display, "div_yield": div_yield, "manual_div_mode": manual_div_mode,
        "blood_line": getattr(st.session_state, 'pinned_stocks', {}).get(symbol, "手動強制加入"),
        "signal_text": signal_text, "color_border": color_border, "signal_bg": signal_bg,
        "score": score, "reasons": reasons,
        "sparkline_html": spark_html, "latest_db_date": latest_db_date,
        "intraday_str": intraday_trend, "manual_mode": manual_mode,
        "is_first_red": (gain > 0 and curr_price > open_price and curr_price > ma5 and prev_price < ma5),
        "is_yesterday_strong": (gain > 0 and len(hist)>2 and ((prev_price - float(hist['Close'].iloc[-3]))/float(hist['Close'].iloc[-3])*100 > 5.0)),
        "detected_patterns": detected_patterns
    }

# ==============================================================================
# 六、 SQLite 雙軌籌碼備援管線 
# ==============================================================================
def process_twse_csv(uploaded_files):
    success_files = 0
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
        token = FINMIND_TOKENS[getattr(st.session_state, 'active_key_index', 0)]
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
            with DB_LOCK:
                SQLITE_CONN.execute('''
                    INSERT INTO inst_holding (date, symbol, foreign_buy, trust_buy, dealer_buy, margin, big_holder, big_holder_date)
                    VALUES (?, ?, ?, ?, ?, 0.0, 0.0, '')
                    ON CONFLICT(date, symbol) DO UPDATE SET 
                        foreign_buy=excluded.foreign_buy, 
                        trust_buy=excluded.trust_buy, 
                        dealer_buy=excluded.dealer_buy;
                ''', (target_date, code, base_payload['foreign'], base_payload['trust'], base_payload['dealer']))
                SQLITE_CONN.commit()
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
            return f"【{model_id.split('/')[-1]} 提供分析】\n\n{completion.choices[0].message.content}"
        except Exception: continue
    return "⚠️ NVIDIA API 全面癱瘓或限流。"

# ==============================================================================
# 八、 全網專屬 CSS 行動端觸控懸浮裝甲配置
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
                SQLITE_CONN.close()
                with open(SQLITE_DB_FILE, "wb") as f: f.write(uploaded_db.getbuffer())
                SQLITE_CONN = get_db_conn()
                st.success("🗄️ 籌碼庫全面覆蓋還原成功！")
            time.sleep(1.5); st.rerun()
                
    st.divider()
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
        <b style='color:#f1c40f;'>🛡️ V154.0 戰情室濾網大公開</b><br>
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
st.title("🚀 54088 戰情室 V154.0 終極鋼鐵防護版")

st.markdown(f"""<div class='hud-box'>
    <div style='color:#f1c40f; font-size:16px; font-weight:bold; margin-bottom:4px;'>📊 大將軍智慧 HUD 總覽</div>
    <div style='color:#ddd; font-size:14px;'><b>大盤氣象：</b> <span style='color:{weather_color}; font-weight:bold;'>上市大盤 {weather_str}</span> | <b>安全狀態：</b> V154.0 鋼鐵防線隔離版</div>
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

search_input = st.text_input("🔍 手動股票代號/名稱輸入框 (如: 2330 或 聯電)", "")
if st.button("➕ 強制加入常態觀測雷達", use_container_width=True):
    if search_input.strip():
        found_codes = []
        for code, name in TW_STOCK_NAMES.items():
            if name == search_input.strip():
                found_codes.append(code); break
        if not found_codes:
            matches = [code for code, name in TW_STOCK_NAMES.items() if search_input.strip() in name]
            if len(matches) == 1: found_codes.append(matches[0])
            elif len(matches) > 1: st.warning(f"⚠️ 模糊偵測到多筆標的，請輸入精確代號：{', '.join([f'{m}({TW_STOCK_NAMES[m]})' for m in matches[:5]])}")
        
        if found_codes:
            for c in found_codes: st.session_state.pinned_stocks[c] = "手動強制加入"
            save_local_db_isolated(); st.rerun()
        else: st.error("⚠️ 找不到對應的股票代號或名稱，請重新輸入。")

def render_stock_card_ui(c, is_portfolio=False, profit=0, roi=0, ent_p=0):
    gain_c = '#ff4d4d' if float(c.get('gain',0)) > 0 else ('#00FF00' if float(c.get('gain',0)) < 0 else '#aaaaaa')
    gain_b = '#3a1515' if float(c.get('gain',0)) > 0 else ('#153a20' if float(c.get('gain',0)) < 0 else '#333333')
    portfolio_header = f"<div style='font-size:14px; margin-bottom:8px; color:#eeeeee;'>持倉成本: {ent_p} | 損益: <strong style='color:{'#ff4d4d' if profit>0 else '#00FF00'};'>{int(profit):+,} 元</strong> ({roi:+.2f}%)</div>" if is_portfolio else ""
    
    yoy_val, mom_val = float(c.get('rev_yoy',0)), float(c.get('rev_mom',0))
    yoy_color = "#ff4d4d" if yoy_val > 0 else ("#00FF00" if yoy_val < 0 else "#00d2ff")

    k_patterns = c.get('detected_patterns', [])
    k_text = f"{'📉' if '黑' in k_patterns[0].get('text', '') else '🔥'} {k_patterns[0].get('text')}" if k_patterns else "⚖️ 壓縮盤整"
    k_tags = f"<span class='k-tag'>{k_text}</span>"

    vol_ratio = float(c.get('vol_ratio', 0))
    price = float(c.get('price', 0))
    ma5 = float(c.get('ma5', 0))
    ma20 = float(c.get('ma20', 0))
    
    if vol_ratio > 1.5: vol_semantic = "⚠️ 破線爆量殺盤疑慮" if price < ma20 else ("🔥 帶量突破上攻" if price > ma5 else "⚠️ 高檔爆量震盪")
    elif vol_ratio < 0.6: vol_semantic = "🧊 量縮洗盤沉澱"
    else: vol_semantic = "⚖️ 溫和換手"

    tags_html = f"""
    <div style='display:flex; flex-wrap:wrap; gap:6px; align-items:center; margin-top:5px;'>
        <span class='m-tooltip' style='white-space:nowrap; display:inline-block; background:#2a2a2a; padding:2px 8px; border-radius:4px; font-size:12px; color:#e67e22;'>
            爆量比: {vol_ratio:.1f}x [{vol_semantic}]
            <span class='m-tooltiptext'>當日量除以五日均量。<br>0.8~1.2 為正常換手，<br>大於 1.5 為爆量，<br>小於 0.6 為量縮</span>
        </span>
        <span style='white-space:nowrap; display:inline-block; background:#2a2a2a; padding:2px 8px; border-radius:4px; font-size:12px; color:#00FF00;'>{c.get('intraday_str')}</span>
    </div>
    """

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
        <span>5MA: <b style="color:#ffffff;">{float(c.get('ma5',0)):.1f}</b></span><span>20MA:
