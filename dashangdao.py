import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import re
import time
import json
import os
import requests
import warnings
from collections import Counter
import urllib3
import concurrent.futures

# ==============================================================================
# 一、 系統最高安全防禦與法規合規宣告 (Lock Mandates)
# ==============================================================================
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
warnings.filterwarnings('ignore')

GOV_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*"
}

USER_DB_FILE = "54088_database.json" 
INST_HISTORY_FILE = "54088_inst_history_v30d.json"

# ==============================================================================
# 二、 記憶體全域安全隔離初始化 (徹底消除 AttributeError 與 SyntaxError)
# ==============================================================================
if 'db_loaded' not in st.session_state:
    st.session_state['db_loaded'] = False
if 'pinned_stocks' not in st.session_state:
    st.session_state['pinned_stocks'] = {"2303": {}, "5871": {}, "2308": {}, "3481": {}}
if 'portfolio' not in st.session_state:
    st.session_state['portfolio'] = {}
if 'inst_history' not in st.session_state:
    st.session_state['inst_history'] = {}
if 'scan_results' not in st.session_state:
    st.session_state['scan_results'] = []
if 'scan_mode' not in st.session_state:
    st.session_state['scan_mode'] = ""
if 'active_key_index' not in st.session_state:
    st.session_state['active_key_index'] = 0
if 'ai_report' not in st.session_state:
    st.session_state['ai_report'] = ""

def load_and_isolate_db():
    if not st.session_state.get('db_loaded', False):
        if os.path.exists(USER_DB_FILE):
            try:
                with open(USER_DB_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    st.session_state['pinned_stocks'] = data.get("pinned_stocks", {})
                    st.session_state['portfolio'] = data.get("portfolio", {})
            except Exception:
                pass
        if os.path.exists(INST_HISTORY_FILE):
            try:
                with open(INST_HISTORY_FILE, "r", encoding="utf-8") as f:
                    st.session_state['inst_history'] = json.load(f)
                    if len(st.session_state['inst_history']) > 30:
                        sorted_dates = sorted(st.session_state['inst_history'].keys(), reverse=True)
                        st.session_state['inst_history'] = {d: st.session_state['inst_history'][d] for d in sorted_dates[:30]}
            except Exception:
                pass
        st.session_state['db_loaded'] = True

def save_local_db_isolated():
    payload = {
        "pinned_stocks": st.session_state.get('pinned_stocks', {}), 
        "portfolio": st.session_state.get('portfolio', {})
    }
    try:
        with open(USER_DB_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=4)
        if st.session_state.get('inst_history', {}):
            with open(INST_HISTORY_FILE, "w", encoding="utf-8") as f:
                json.dump(st.session_state['inst_history'], f, ensure_ascii=False)
    except Exception:
        pass

load_and_isolate_db()

try:
    COMMANDER_PIN = st.secrets["radar_secrets"]["commander_pin"]
    raw_keys = st.secrets["radar_secrets"]["gemini_api_key"]
    GEMINI_API_KEYS = [k.strip() for k in raw_keys.split(",") if k.strip()]
    SECRET_FINMIND = st.secrets["radar_secrets"].get("finmind_token", "")
    FINMIND_TOKENS = [k.strip() for k in SECRET_FINMIND.split(",") if k.strip()]
    if not FINMIND_TOKENS: FINMIND_TOKENS = [""]
except KeyError:
    COMMANDER_PIN = "54088"
    GEMINI_API_KEYS = [""]
    FINMIND_TOKENS = [""]

def safe_float(val):
    if pd.isna(val) or val is None or str(val).strip() == '': return 0.0
    try:
        s = str(val).upper().replace(',', '').replace('-', '').strip()
        s = re.sub(r'[^\d.]', '', s)
        return float(s) if s else 0.0
    except Exception: return 0.0

def get_industry_label_wrapper(code):
    c = str(code)
    if c.startswith('11'): return "水泥工業"
    elif c.startswith('12'): return "食品工業"
    elif c.startswith('13'): return "塑膠工業"
    elif c.startswith('14'): return "紡織纖維"
    elif c.startswith('15'): return "電機機械"
    elif c.startswith('16'): return "電器電纜"
    elif c.startswith(('17', '41', '47', '65')): return "生技醫療"
    elif c.startswith('20'): return "鋼鐵工業"
    elif c.startswith('22'): return "汽車工業"
    elif c.startswith(('23', '24', '30', '31', '35', '80', '64')): return "電子半導體"
    elif c.startswith('25'): return "建材營造"
    elif c.startswith('26'): return "航運業"
    elif c.startswith(('28', '58')): return "金融保險"
    return "綜合類股"

# ==============================================================================
# 三、 真實與安全備援資料源管線 (Real API with 沙盒 Fallback)
# ==============================================================================
@st.cache_resource
def get_safe_session():
    session = requests.Session()
    session.headers.update(GOV_HEADERS)
    return session

@st.cache_data(ttl=86400, show_spinner=False)
def fetch_tw_revenue():
    rev_db = {}
    for url in ["https://openapi.twse.com.tw/v1/opendata/t187ap05_L", "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap05_O"]:
        try:
            res = requests.get(url, headers=GOV_HEADERS, verify=False, timeout=3)
            if res.status_code == 200:
                for item in res.json():
                    c = str(item.get('公司代號', '')).strip()
                    if len(c) == 4:
                        yoy = safe_float(item.get('當月營收較去年當月增減百分比', 0))
                        mom = safe_float(item.get('上月比較增減(%)', 0))
                        rev_db[c] = {'yoy': yoy, 'mom': mom}
        except Exception: pass
    return rev_db

@st.cache_data(ttl=86400, show_spinner=False)
def fetch_stock_names():
    names = {}
    for url in ["https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL", "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_peratio_analysis"]:
        try:
            res = requests.get(url, headers=GOV_HEADERS, verify=False, timeout=3)
            if res.status_code == 200:
                for item in res.json():
                    c = str(item.get('Code', item.get('SecuritiesCompanyCode', ''))).strip()
                    n = str(item.get('Name', item.get('CompanyName', ''))).strip()
                    if len(c) == 4 and c.isdigit() and n: names[c] = n
        except Exception: pass
    fallbacks = {"2330":"台積電", "2303":"聯電", "2317":"鴻海", "2308":"台達電", "5871":"中租-KY", "3481":"群創", "2454":"聯發科", "1101":"台泥"}
    for k, v in fallbacks.items():
        if k not in names: names[k] = v
    return names

@st.cache_data(ttl=86400, show_spinner=False)
def fetch_twse_dividends():
    divs = {}
    try:
        res = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/TWT48U", headers=GOV_HEADERS, verify=False, timeout=3)
        if res.status_code == 200:
            for item in res.json():
                c = str(item.get('股票代號', '')).strip()
                if len(c) == 4: divs[c] = {'date': str(item.get('除權息日期', '')).strip(), 'cash': safe_float(item.get('現金股利', 0))}
    except Exception: pass
    return divs

TW_STOCK_NAMES = fetch_stock_names()
TW_REVENUE_DB = fetch_tw_revenue()
DIVIDEND_DB = fetch_twse_dividends()
GLOBAL_MARKET_CODES = list(TW_STOCK_NAMES.keys())

@st.cache_data(ttl=60, show_spinner=False)
def get_market_weather_real():
    try:
        tk = yf.Ticker("^TWII", session=get_safe_session())
        hist = tk.history(period="10d")
        if not hist.empty:
            c_idx = float(hist['Close'].iloc[-1])
            prev_idx = float(hist['Close'].iloc[-2])
            pt = c_idx - prev_idx
            gain = (pt / prev_idx) * 100
            ma20 = float(hist['Close'].mean())
            is_panic = gain <= -2.5 or c_idx < ma20 * 0.95
            w_str = f"上市 <span style='color:{'#ff4d4d' if gain>0 else '#00FF00'}; font-weight:bold;'>{c_idx:,.0f} ({gain:+.2f}%)</span>"
            return w_str, is_panic, gain
    except Exception: pass
    return "API連線中...", False, 0.0

@st.cache_data(ttl=120, show_spinner=False)
def get_real_stock_data_yfinance(symbol):
    session = get_safe_session()
    for ext in [".TW", ".TWO"]:
        try:
            tk = yf.Ticker(symbol + ext, session=session)
            hist = tk.history(period="3mo", timeout=3).dropna(subset=['Close'])
            hist_1m = tk.history(period="1d", interval="1m", timeout=2).dropna(subset=['Close'])
            if not hist.empty and len(hist) > 10:
                return hist.tail(30), hist_1m, tk.info
        except Exception: pass
    
    np.random.seed(int(symbol))
    mock_close = np.random.uniform(20, 200, 30)
    mock_open = mock_close * np.random.uniform(0.98, 1.02, 30)
    mock_high = np.maximum(mock_close, mock_open) * 1.01
    mock_low = np.minimum(mock_close, mock_open) * 0.99
    mock_vol = np.random.uniform(500000, 5000000, 30)
    idx = pd.date_range(end=datetime.now(), periods=30, freq='B')
    df_mock = pd.DataFrame({"Open": mock_open, "High": mock_high, "Low": mock_low, "Close": mock_close, "Volume": mock_vol}, index=idx)
    mock_info = {"debtToEquity": 45.0, "operatingCashflow": 5000000, "netIncome": 4000000, "targetMeanPrice": mock_close[-1]*1.2}
    return df_mock, df_mock.tail(1), mock_info

weather_str, is_panic, global_twii_gain = get_market_weather_real()

# ==============================================================================
# 四、 視覺化與型態學演算法核心 (Bi-Color Sparkline & K-Line)
# ==============================================================================
def generate_bi_color_sparkline(closes_list):
    if not closes_list or len(closes_list) < 2: return "<span style='color:#888;'>▃</span>"
    bars = " ▂▃▄▅▆▇█"
    min_p, max_p = min(closes_list), max(closes_list)
    rng = max_p - min_p if max_p != min_p else 1e-9
    html_sparkline = ""
    for i in range(len(closes_list)):
        val = closes_list[i]
        idx = max(0, min(7, int((val - min_p) / rng * 7)))
        if i == 0:
            color = "#888888"
        else:
            if closes_list[i] > closes_list[i-1]: color = "#ff4d4d"
            elif closes_list[i] < closes_list[i-1]: color = "#00FF00"
            else: color = "#aaaaaa"
        html_sparkline += f"<span style='color:{color}; font-weight:bold;'>{bars[idx]}</span>"
    return html_sparkline

def detect_k_line_patterns_v133(df):
    patterns = []
    if len(df) < 5: return patterns
    c0, c1, c2 = float(df['Close'].iloc[-1]), float(df['Close'].iloc[-2]), float(df['Close'].iloc[-3])
    o0, o1, o2 = float(df['Open'].iloc[-1]), float(df['Open'].iloc[-2]), float(df['Open'].iloc[-3])
    body0 = abs(c0 - o0)
    
    if (c0 > o0) and body0 > (c0 * 0.025):
        if (c1 < o1) and c0 > o1 and o0 < c1:
            patterns.append({"text": "長紅吞噬", "class": "tag-red"})
        else:
            patterns.append({"text": "低檔長紅", "class": "tag-red"})
    if (c0 > o0) and (c1 > o1) and (c2 > o2) and (c0 > c1 > c2):
        patterns.append({"text": "紅三兵", "class": "tag-red"})
        
    if (c0 < o0) and body0 > (c0 * 0.025):
        if (c1 > o1) and c0 < o1 and o0 > c1:
            patterns.append({"text": "長黑吞噬", "class": "tag-green"})
        else:
            patterns.append({"text": "高檔長黑", "class": "tag-green"})
    if (c0 < o0) and (c1 < o1) and (c2 < o2) and (c0 < c1 < c2):
        patterns.append({"text": "黑三兵", "class": "tag-green"})
        
    return patterns

def get_intraday_trend(df_1m):
    if df_1m is None or df_1m.empty: return "▰▰▰▱▱ 盤整"
    op = float(df_1m['Open'].iloc[0])
    cl = float(df_1m['Close'].iloc[-1])
    hi = float(df_1m['High'].max())
    lo = float(df_1m['Low'].min())
    if cl > op and cl >= hi * 0.99: return "▰▰▰▰▰ 開低走高·強勢收上"
    if cl < op and cl <= lo * 1.01: return "▱▱▱▱▱ 開高走低·弱勢收下"
    if cl > op: return "▰▰▰▱▱ 震盪走高"
    return "▰▱▱▱▱ 震盪偏弱"

# ==============================================================================
# 五、 核心運算晶片與「五大戰區」數據聚合
# ==============================================================================
def calculate_comprehensive_signals(symbol, enable_doomsday=False):
    hist, hist_1m, info = get_real_stock_data_yfinance(symbol)
    if hist is None or hist.empty:
        return None
    
    curr
