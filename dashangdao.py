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
import urllib3
import concurrent.futures
from openai import OpenAI  
import copy
import tempfile
import sqlite3
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
OLD_INST_HISTORY_FILE = "54088_inst_history_v30d.json"

# ==============================================================================
# 🚀 核心防線：實體交易日與 API 連線池 (含自動重試)
# ==============================================================================
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
        total=3,
        backoff_factor=0.6,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"]
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount('https://', adapter)
    session.mount('http://', adapter)
    return session

_SESSION = get_safe_session()

# ==============================================================================
# 二、 資料庫架構升級 (SQLite + 原子寫入 JSON)
# ==============================================================================
def get_db_conn():
    conn = sqlite3.connect(SQLITE_DB_FILE, check_same_thread=False)
    conn.execute('PRAGMA journal_mode=WAL')
    return conn

def init_sqlite_db():
    conn = get_db_conn()
    conn.execute('''
        CREATE TABLE IF NOT EXISTS inst_holding (
            date TEXT, symbol TEXT,
            foreign_buy REAL, trust_buy REAL, dealer_buy REAL,
            margin REAL, big_holder REAL, big_holder_date TEXT,
            PRIMARY KEY (date, symbol)
        )
    ''')
    conn.commit()
    
    # 舊版 JSON 無痛轉移至 SQLite
    if os.path.exists(OLD_INST_HISTORY_FILE):
        try:
            with open(OLD_INST_HISTORY_FILE, "r", encoding="utf-8") as f:
                old_data = json.load(f)
            for d, stocks in old_data.items():
                for code, payload in stocks.items():
                    conn.execute('''
                        INSERT OR IGNORE INTO inst_holding 
                        (date, symbol, foreign_buy, trust_buy, dealer_buy, margin, big_holder, big_holder_date)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (d, code, payload.get('foreign', 0), payload.get('trust', 0), payload.get('dealer', 0), 
                          payload.get('margin', 0), payload.get('big_holder', 0.0), payload.get('big_holder_date', '')))
            conn.commit()
            os.rename(OLD_INST_HISTORY_FILE, OLD_INST_HISTORY_FILE + ".bak")
        except Exception:
            pass
    return conn

SQLITE_CONN = init_sqlite_db()

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
        
        # 實作人工覆寫 7 日賞味期限自動解除
        now_ts = datetime.now().timestamp()
        for d_dict in [st.session_state.revenue_override, st.session_state.bigholder_override, st.session_state.dividend_override]:
            for k in list(d_dict.keys()):
                if now_ts - d_dict[k].get('ts', now_ts) > 7 * 86400:
                    del d_dict[k]
                    
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
# 三、 真實大數據晶片核心與 API 遞迴溯源
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
    fee_buy = max(20, int(buy_val * 0.001425))
    fee_sell = max(20, int(sell_val * 0.001425))
    tax_sell = int(sell_val * 0.003)
    profit = sell_val - buy_val - fee_buy - fee_sell - tax_sell
    return profit, (profit / buy_val) * 100 if buy_val > 0 else 0

def _finmind_get(dataset, data_id, start_date, token, end_date=None, timeout=6):
    params = {'dataset': dataset, 'data_id': data_id, 'start_date': start_date}
    if end_date: params['end_date'] = end_date
    if token: params['token'] = token
    try:
        res = _SESSION.get('https://api.finmindtrade.com/api/v4/data', params=params, timeout=timeout)
    except requests.exceptions.RequestException as e:
        return None, f"network_error:{e}"
    if res.status_code == 429: return None, "rate_limited"
    if res.status_code != 200: return None, f"http_{res.status_code}"
    try: payload = res.json()
    except ValueError: return None, "bad_json"
    if payload.get('msg') != 'success': return None, f"api_msg:{payload.get('msg')}"
    data = payload.get('data', [])
    if not data: return None, "empty_data"
    return pd.DataFrame(data), None

@st.cache_data(ttl=86400, show_spinner=False)
def fetch_finmind_revenue(symbol, token, max_lookback=400):
    lookback = 120
    df, err = None, None
    while df is None and lookback <= max_lookback:
        start_date = (datetime.now() - timedelta(days=lookback)).strftime('%Y-%m-%d')
        df, err = _finmind_get('TaiwanStockMonthRevenue', symbol, start_date, token)
        if df is None: lookback *= 2 

    if df is not None:
        df = df.sort_values('date')
        for _, row in df[::-1].iterrows():
            yoy_raw, mom_raw = row.get('revenue_YearOnYearRatio'), row.get('revenue_MonthOverMonthRatio')
            if yoy_raw is None or mom_raw is None: continue
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
            gain = ((c_idx - prev_idx) / prev_idx) * 100
            w_str = f"上市 <span style='color:{'#ff4d4d' if gain>0 else '#00FF00'}; font-weight:bold;'>{c_idx:,.0f} ({gain:+.2f}%)</span>"
            return w_str, False, gain
    except: pass
    return "<span style='color:#888;'>大盤連線中...</span>", False, 0.0

@st.cache_data(ttl=120, show_spinner=False)
def get_real_stock_data_yfinance(symbol):
    for ext in [".TW", ".TWO"]:
        try:
            tk = yf.Ticker(symbol + ext, session=_SESSION)
            hist = tk.history(period="6mo", timeout=4).dropna(subset=['Close'])
            hist = hist[hist['Volume'] > 0]
            hist_1m = tk.history(period="1d", interval="1m", timeout=3).dropna(subset=['Close'])
            if not hist.empty and len(hist) > 20: return hist.tail(90), hist_1m, tk.info
        except: pass
    return None, None, {}

weather_str, is_panic, global_twii_gain = get_market_weather_real()

# ==============================================================================
# 四、 動態技術指標庫 (RSI, Bollinger, BIAS, ATR)
# ==============================================================================
def calc_rsi(df, period=14):
    delta = df['Close'].diff()
    gain = delta.where(delta > 0, 0).rolling(period).mean()
    loss = -delta.where(delta < 0, 0).rolling(period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def calc_bollinger(df, period=20, num_std=2):
    mid = df['Close'].rolling(period).mean()
    std = df['Close'].rolling(period).std()
    return mid, mid + num_std * std, mid - num_std * std

def calc_bias(df, period=20):
    ma = df['Close'].rolling(period).mean()
    return (df['Close'] - ma) / ma * 100

def calc_atr(df, period=14):
    high_low = df['High'] - df['Low']
    high_close = (df['High'] - df['Close'].shift()).abs()
    low_close = (df['Low'] - df['Close'].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return tr.rolling(period).mean()

def detect_k_line_patterns_v151(df):
    patterns = []
    if len(df) < 5: return patterns
    atr_val = calc_atr(df).iloc[-1]
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

def get_intraday_trend(df_1m):
    if df_1m is None or df_1m.empty: return "無即時看盤資料"
    op, cl = float(df_1m['Open'].iloc[0]), float(df_1m['Close'].iloc[-1])
    hi, lo = float(df_1m['High'].max()), float(df_1m['Low'].min())
    if cl > op and cl >= hi * 0.99: return "開低走高·強勢收上"
    if cl < op and cl <= lo * 1.01: return "開高走低·弱勢收下"
    if cl > op: return "震盪走高"
    return "震盪偏弱"

# ==============================================================================
# 五、 核心訊號與戰區聚合核心 (支援 SQLite)
# ==============================================================================
def get_inst_data_from_db(symbol, limit=10):
    try:
        df = pd.read_sql('SELECT * FROM inst_holding WHERE symbol=? ORDER BY date DESC LIMIT ?', SQLITE_CONN, params=(symbol, limit))
        return df
    except Exception: return pd.DataFrame()

def calculate_comprehensive_signals(symbol, enable_doomsday=False):
    f_single = t_single = d_single = margin_diff = 0.0
    f_5d = t_5d = f_10d = t_10d = 0
    f_pct = t_pct = f_5d_pct = t_5d_pct = f_10d_pct = t_10d_pct = 0.0
    big_holder, big_holder_date = 0.0, ""
    
    hist, hist_1m, info = get_real_stock_data_yfinance(symbol)
    if hist is None or hist.empty: return {"code": symbol, "name": TW_STOCK_NAMES.get(symbol, symbol), "error": True}
    
    curr_price, prev_price, open_price = float(hist['Close'].iloc[-1]), float(hist['Close'].iloc[-2]), float(hist['Open'].iloc[-1])
    gain = ((curr_price - prev_price) / prev_price) * 100 if prev_price > 0 else 0
    vol_today = int(hist['Volume'].iloc[-1] / 1000)
    vol_yesterday = max(1, int(hist['Volume'].iloc[-2] / 1000))
    vol_change_pct = ((vol_today - vol_yesterday) / vol_yesterday) * 100 if vol_yesterday > 0 else 0
    vol_5d_sum = int(hist['Volume'].tail(5).sum() / 1000)
    vol_10d_sum = int(hist['Volume'].tail(10).sum() / 1000)
    vol_5d_mean = max(1, vol_5d_sum / 5)
    vol_ratio = vol_today / vol_5d_mean if vol_5d_mean > 0 else 0
    
    ma5, ma20, ma60 = float(hist['Close'].tail(5).mean()), float(hist['Close'].tail(20).mean()), float(hist['Close'].mean())
    exp1, exp2 = hist['Close'].ewm(span=12, adjust=False).mean(), hist['Close'].ewm(span=26, adjust=False).mean()
    macd_hist = (exp1 - exp2) - (exp1 - exp2).ewm(span=9, adjust=False).mean()
    macd_val = macd_hist.iloc[-1] if not macd_hist.empty else 0
    macd_str, macd_color = ("多方動能", "#ff4d4d") if macd_val > 0 else ("空方動能", "#00FF00")
    
    low_min, high_max = hist['Low'].rolling(9).min(), hist['High'].rolling(9).max()
    rsv = (hist['Close'] - low_min) / (high_max - low_min + 1e-9) * 100
    calc_k, calc_d = rsv.bfill().ffill().ewm(com=2, adjust=False).mean(), rsv.bfill().ffill().ewm(com=2, adjust=False).mean().ewm(com=2, adjust=False).mean()
    kdj_str = "金叉向上" if not calc_k.empty and calc_k.iloc[-1] > calc_d.iloc[-1] else "死叉向下"
    
    rsi_val = calc_rsi(hist).iloc[-1]
    bias_val = calc_bias(hist).iloc[-1]
    
    inst_df = get_inst_data_from_db(symbol, 10)
    if not inst_df.empty:
        latest = inst_df.iloc[0]
        f_single, t_single, d_single, margin_diff = latest['foreign_buy'], latest['trust_buy'], latest['dealer_buy'], latest['margin']
        big_holder, big_holder_date = latest['big_holder'], latest['big_holder_date']
        
        f_pct = (f_single / vol_today * 100) if vol_today > 0 else 0.0
        t_pct = (t_single / vol_today * 100) if vol_today > 0 else 0.0
        
        df_5d = inst_df.head(5)
        f_5d, t_5d = df_5d['foreign_buy'].sum(), df_5d['trust_buy'].sum()
        f_10d, t_10d = inst_df['foreign_buy'].sum(), inst_df['trust_buy'].sum()
        
        f_5d_pct = (f_5d / vol_5d_sum * 100) if vol_5d_sum > 0 else 0.0
        t_5d_pct = (t_5d / vol_5d_sum * 100) if vol_5d_sum > 0 else 0.0
        f_10d_pct = (f_10d / vol_10d_sum * 100) if vol_10d_sum > 0 else 0.0
        t_10d_pct = (t_10d / vol_10d_sum * 100) if vol_10d_sum > 0 else 0.0

    override_bh = getattr(st.session_state, 'bigholder_override', {})
    if symbol in override_bh and override_bh[symbol]:
        big_holder = override_bh[symbol].get('ratio', big_holder)
        big_holder_date = f"自訂 {override_bh[symbol].get('date', '')}"

    override_db = getattr(st.session_state, 'revenue_override', {})
    manual_mode = False
    if symbol in override_db and override_db[symbol]:
        rev_yoy, rev_mom, rev_month, manual_mode = override_db[symbol].get('yoy', 0.0), override_db[symbol].get('mom', 0.0), override_db[symbol].get('month', "自訂"), True
    else:
        fm_token = FINMIND_TOKENS[getattr(st.session_state, 'active_key_index', 0)]
        fm_rev = fetch_finmind_revenue(symbol, fm_token)
        rev_yoy, rev_mom, rev_month = fm_rev['yoy'], fm_rev['mom'], fm_rev['month']
        if fm_rev.get('stale'): rev_month += " (沿用舊資料)"

    override_div = getattr(st.session_state, 'dividend_override', {})
    manual_div_mode = False
    if symbol in override_div:
        div_display, div_yield, manual_div_mode = override_div[symbol].get('display', "自訂資料"), override_div[symbol].get('yield', 0.0), True
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

    atk_zone, def_line = ("空手觀望 (破線)", "全面破線，嚴守紀律")
    if curr_price >= ma5: atk_zone, def_line = f"{ma5:.1f} ~ 現價", f"跌破 {ma5:.1f}"
    elif curr_price >= ma20: atk_zone, def_line = f"{ma20:.1f} ~ 現價", f"跌破 {ma20:.1f}"

    multi_bull, multi_bear = [], []
    if curr_price > ma5: multi_bull.append("站上5日線")
    else: multi_bear.append("跌破5日線")
    if f_single > 0: multi_bull.append("外資買超")
    if margin_diff < 0: multi_bull.append("融資減少(沉澱)")
    if rev_yoy > 20.0: multi_bull.append("營收雙增")
    
    detected_patterns = detect_k_line_patterns_v151(hist)
        
    signal_text = "[🔥 偏多攻擊]" if (curr_price > ma5 and f_single > 0) else ("[🚨 撤退警告]" if curr_price < ma5 else "[⚠️ 整理觀望]")
    color_border = "#ff4d4d" if "攻擊" in signal_text else ("#00FF00" if "警告" in signal_text else "#f1c40f")
    signal_bg = "#3a1515" if "攻擊" in signal_text else ("#153a20" if "警告" in signal_text else "#332b00")
    
    # 產生 Sparkline (HTML)
    closes = hist['Close'].tail(7).tolist()
    while len(closes) < 7: closes.append(closes[-1] if closes else 0)
    bars, min_p, max_p = " ▂▃▄▅▆▇█", min(closes), max(closes)
    rng = max_p - min_p if max_p != min_p else 1e-9
    spark_html = "".join([f"<span style='color:{'#ff4d4d' if i>0 and closes[i]>closes[i-1] else ('#00FF00' if i>0 and closes[i]<closes[i-1] else '#888')}; font-weight:bold;'>{bars[max(0, min(7, int((closes[i] - min_p) / rng * 7)))]}</span>" for i in range(7)])
    
    return {
        "code": symbol, "name": TW_STOCK_NAMES.get(symbol, symbol), "price": curr_price, "gain": gain, "error": False,
        "vol": vol_today, "vol_change_pct": vol_change_pct, "vol_ratio": vol_ratio,
        "ma5": ma5, "ma20": ma20, "ma60": ma60, "macd_str": macd_str, "macd_color": macd_color, "kdj_str": kdj_str,
        "rsi_val": rsi_val, "bias_val": bias_val,
        "f_buy": f_single, "t_buy": t_single, "d_buy": d_single, "margin_diff": margin_diff, "big_holder": big_holder, "big_holder_date": big_holder_date, 
        "f_5d": f_5d, "t_5d": t_5d, "f_10d": f_10d, "t_10d": t_10d, "f_pct": f_pct, "t_pct": t_pct, 
        "f_5d_pct": f_5d_pct, "t_5d_pct": t_5d_pct, "f_10d_pct": f_10d_pct, "t_10d_pct": t_10d_pct,
        "atk_zone": atk_zone, "def_line": def_line,
        "rev_yoy": rev_yoy, "rev_mom": rev_mom, "rev_month": rev_month, 
        "div_display": div_display, "div_yield": div_yield, "manual_div_mode": manual_div_mode,
        "blood_line": getattr(st.session_state, 'pinned_stocks', {}).get(symbol, "手動強制加入"),
        "signal_text": signal_text, "color_border": color_border, "signal_bg": signal_bg,
        "sparkline_html": spark_html, 
        "intraday_str": get_intraday_trend(hist_1m), "manual_mode": manual_mode,
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
            
            for index, row in df.iterrows():
                code = str(row[code_col]).strip()
                if len(code) == 4 and code.isdigit():
                    f_buy = int(safe_float(row[f_col]) / 1000) if f_col else 0
                    t_buy = int(safe_float(row[t_col]) / 1000) if t_col else 0
                    d_buy = int(safe_float(row[d_col]) / 1000) if d_col else 0
                    SQLITE_CONN.execute('''
                        INSERT INTO inst_holding (date, symbol, foreign_buy, trust_buy, dealer_buy, margin, big_holder, big_holder_date)
                        VALUES (?, ?, ?, ?, ?, COALESCE((SELECT margin FROM inst_holding WHERE date=? AND symbol=?), 0), 
                        COALESCE((SELECT big_holder FROM inst_holding WHERE date=? AND symbol=?), 0.0), 
                        COALESCE((SELECT big_holder_date FROM inst_holding WHERE date=? AND symbol=?), ''))
                        ON CONFLICT(date, symbol) DO UPDATE SET foreign_buy=excluded.foreign_buy, trust_buy=excluded.trust_buy, dealer_buy=excluded.dealer_buy;
                    ''', (file_date, code, f_buy, t_buy, d_buy, file_date, code, file_date, code, file_date, code))
            SQLITE_CONN.commit()
            success_files += 1
        except Exception: pass
            
    if success_files > 0:
        st.success(f"✅ 成功強填 {success_files} 份日報至大腦！")
        time.sleep(1); st.rerun()

def sync_single_stock_finmind(code):
    target_date = get_last_trading_date()
    token = FINMIND_TOKENS[getattr(st.session_state, 'active_key_index', 0)]
    
    inst_success, bh_success = False, False
    base_payload = {'foreign':0, 'trust':0, 'dealer':0, 'margin':0, 'big_holder':0.0, 'big_holder_date': ''}
    
    # 抓三大法人
    df, err = _finmind_get('TaiwanStockInstitutionalInvestorsBuySell', code, target_date, token)
    if df is not None:
        df['net'] = pd.to_numeric(df['buy'], errors='coerce').fillna(0) - pd.to_numeric(df['sell'], errors='coerce').fillna(0)
        piv = df.pivot_table(index='date', columns='name', values='net', aggfunc='sum')
        if 'Foreign_Investor' in piv.columns: base_payload['foreign'] = int(piv['Foreign_Investor'].iloc[-1]/1000)
        if 'Investment_Trust' in piv.columns: base_payload['trust'] = int(piv['Investment_Trust'].iloc[-1]/1000)
        if 'Dealer' in piv.columns: base_payload['dealer'] = int(piv['Dealer'].iloc[-1]/1000)
        inst_success = True

    # 抓大戶 (遞迴溯源 90 天)
    lookback = 20
    b_df = None
    target_dt = datetime.strptime(target_date, "%Y-%m-%d")
    while b_df is None and lookback <= 90:
        start_date = (target_dt - timedelta(days=lookback)).strftime('%Y-%m-%d')
        b_df, _ = _finmind_get('TaiwanStockHoldingSharesPer', code, start_date, token, end_date=target_date)
        if b_df is None: lookback *= 2

    if b_df is not None and not b_df.empty:
        latest_date = b_df['date'].max()
        subset = b_df[(b_df['date'] == latest_date) & (b_df['HoldingSharesLevel'] >= 15)]
        base_payload['big_holder'] = round(subset['percent'].sum(), 2)
        base_payload['big_holder_date'] = latest_date[-5:].replace('-','/')
        bh_success = True
    else:
        last_good = st.session_state.get(f'_last_good_bigholder_{code}')
        if last_good:
            base_payload['big_holder'] = last_good.get('big_holder', 0)
            base_payload['big_holder_date'] = last_good.get('big_holder_date', '') + " (沿用)"
            bh_success = True

    if inst_success or bh_success:
        SQLITE_CONN.execute('''
            INSERT OR REPLACE INTO inst_holding (date, symbol, foreign_buy, trust_buy, dealer_buy, margin, big_holder, big_holder_date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (target_date, code, base_payload['foreign'], base_payload['trust'], base_payload['dealer'], 0, base_payload['big_holder'], base_payload['big_holder_date']))
        SQLITE_CONN.commit()
        st.session_state[f'_last_good_bigholder_{code}'] = base_payload
        fetch_finmind_revenue.clear() 
        return True, "籌碼與大戶同步完成 (套用溯源防護)"
    return False, "API 拒絕連線或查無資料"

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
.m-tooltip { position: relative; border-bottom: 1px dashed #00d2ff; cursor: pointer; display: inline-block; color: #00d2ff; font-weight: bold; }
.m-tooltip .m-tooltiptext {
    visibility: hidden; width: max-content; max-width: 240px; background-color: #1f242d; color: #ffffff;
    text-align: left; border-radius: 6px; padding: 8px 12px; position: absolute;
    z-index: 999; bottom: 135%; left: 50%; transform: translateX(-50%); opacity: 0; transition: opacity 0.2s;
    font-size: 12px; line-height: 1.5; font-weight: normal; box-shadow: 0px 5px 15px rgba(0,0,0,0.8); border: 1px solid #00d2ff;
}
.m-tooltip .m-tooltiptext::after { content: ""; position: absolute; top: 100%; left: 50%; margin-left: -5px; border-width: 5px; border-style: solid; border-color: #1f242d transparent transparent transparent; }
.m-tooltip:hover .m-tooltiptext, .m-tooltip:active .m-tooltiptext { visibility: visible; opacity: 1; }
</style>""", unsafe_allow_html=True)

# ----------------- 九、 側邊欄控制台 -----------------
with st.sidebar:
    st.markdown("<h2 style='color:#f1c40f; text-align:center;'>⚙️ 戰略控制台</h2>", unsafe_allow_html=True)
    if st.button("🔄 強制重整畫面", use_container_width=True):
        st.session_state.last_refresh = time.time(); st.rerun()
        
    with st.expander("📥 [主攻] 官方 CSV 籌碼強填中樞", expanded=False):
        uploaded_csvs = st.file_uploader("拖曳證交所三大法人 CSV", type=['csv'], accept_multiple_files=True)
        if uploaded_csvs and st.button("🚀 批次強制解析回填至 SQLite", use_container_width=True):
            process_twse_csv(uploaded_csvs)
            
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
            
    if st.button("🚀 執行全市場並行高速掃描 (多執行緒)", use_container_width=True, type="primary"):
        if not selected_cmds: st.warning("請先選擇至少一項戰略條件。")
        else: st.session_state.trigger_scan = True

    with st.expander("📖 統籌戰術解密說明書", expanded=False):
        st.markdown("""<div style="font-size:13px; color:#ffffff; background:#1e1e24; padding:15px; border-radius:8px;">
        <b style='color:#f1c40f;'>🛡️ V151 戰情室濾網邏輯大公開</b><br>
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
        <b style='color:#00d2ff;'>查12.</b> 觸發特定K線型態 (ATR 動態判定)</div>""", unsafe_allow_html=True)

# ==============================================================================
# 十、 主畫面：UI 渲染與三方會審區塊
# ==============================================================================
st.title("🚀 54088 戰情室 V151.0 絕對防禦版 (多工/SQLite/新指標)")

def render_commander_stock_card(c, is_portfolio=False, profit=0, roi=0, ent_p=0):
    gain_c = '#ff4d4d' if float(c.get('gain',0)) > 0 else ('#00FF00' if float(c.get('gain',0)) < 0 else '#aaaaaa')
    gain_b = '#3a1515' if float(c.get('gain',0)) > 0 else ('#153a20' if float(c.get('gain',0)) < 0 else '#333333')
    vol_c = '#ff4d4d' if float(c.get('vol_change_pct',0)) > 0 else '#00FF00'
    vol_t = f"爆量 {float(c.get('vol_change_pct',0)):+.1f}%" if float(c.get('vol_change_pct',0)) > 0 else f"量縮 {float(c.get('vol_change_pct',0)):.1f}%"
    portfolio_header = f"<div style='font-size:14px; margin-bottom:8px; color:#eeeeee;'>持倉成本: {ent_p} | 損益: <strong style='color:{'#ff4d4d' if profit>0 else '#00FF00'};'>{int(profit):+,} 元</strong> ({roi:+.2f}%)</div>" if is_portfolio else ""
    
    yoy_val, mom_val = float(c.get('rev_yoy',0)), float(c.get('rev_mom',0))
    yoy_color = "#ff4d4d" if yoy_val > 0 else ("#00FF00" if yoy_val < 0 else "#00d2ff")
    
    vol_ratio, price, ma5, ma20 = float(c.get('vol_ratio', 0)), float(c.get('price', 0)), float(c.get('ma5', 0)), float(c.get('ma20', 0))
    vol_semantic = ""
    if vol_ratio > 1.5:
        if price < ma20: vol_semantic = "<span style='color:#ff4d4d; font-size:12px; margin-left:6px;'>[⚠️ 破線爆量殺盤疑慮]</span>"
        elif price > ma5: vol_semantic = "<span style='color:#00FF00; font-size:12px; margin-left:6px;'>[🔥 帶量突破上攻]</span>"
        else: vol_semantic = "<span style='color:#f1c40f; font-size:12px; margin-left:6px;'>[⚠️ 高檔爆量震盪]</span>"
    elif vol_ratio < 0.6: vol_semantic = "<span style='color:#00d2ff; font-size:12px; margin-left:6px;'>[🧊 量縮洗盤沉澱]</span>"

    k_patterns = c.get('detected_patterns', [])
    if k_patterns:
        k_text = k_patterns[0].get('text', '')
        k_tags = f"<span style='font-size:13px; background:#2c3e50; padding:3px 8px; border-radius:5px; color:#f1c40f; margin-left:12px;'>{'📉' if '黑' in k_text else '🔥'} {k_text}</span>"
    else: k_tags = f"<span style='font-size:13px; background:#2c3e50; padding:3px 8px; border-radius:5px; color:#aaaaaa; margin-left:12px;'>⚖️ 壓縮盤整</span>"

    html = f"""
<div style="border:2px solid {c.get('color_border')}; border-radius:8px; padding:15px; background:#16191f; margin-bottom:12px; color:#eeeeee;">
{portfolio_header}
<div style="display:flex; justify-content:space-between; align-items:center;">
<span style="font-weight:bold; font-size:19px; color:#ffffff; display:flex; align-items:center;">{c.get('name')} <span style="color:#00d2ff; margin-left:5px;">({c.get('code')})</span>{k_tags}</span>
<span style="font-size:13px; color:#f1c40f;">{c.get('blood_line', '')}</span>
</div>
<div style="display:flex; justify-content:space-between; align-items:flex-end; margin:10px 0;">
    <div style="display:flex; align-items:center;"><span style="font-size:32px; font-weight:bold; color:#ffffff;">{float(c.get('price',0)):.2f}</span><span style="font-size:15px; color:{gain_c}; background:{gain_b}; padding:3px 8px; border-radius:4px; margin-left:10px; font-weight:bold;">{float(c.get('gain',0)):+.2f}%</span></div>
    <div style="font-size:14px; display:flex; align-items:center; color:#ccc;">近7日: {c.get('sparkline_html')}</div>
</div>
<div style="background:#0e1117; padding:8px; border-radius:4px; margin-bottom:10px;">
    <div style="font-size:13px; margin-bottom:4px;">總量: <b style="color:#ffffff;">{int(c.get('vol',0)):,} K張</b> (<span style="color:{vol_c}; font-weight:bold;">{vol_t}</span>)</div>
    <div style="font-size:13px; display:flex; justify-content:space-between;">
        <span>爆量比: <strong style="color:#e67e22;">{vol_ratio:.1f}x</strong>{vol_semantic}</span>
        <span style="color:#00FF00; font-weight:bold;">{c.get('intraday_str')}</span>
    </div>
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
    <div style="display:flex; justify-content:space-between; font-size:13px; margin-bottom:4px;">
        <span>MACD 動能: <strong style="color:{c.get('macd_color')};">{c.get('macd_str')}</strong></span>
        <span>RSI(14): <strong style="color:{'#ff4d4d' if c.get('rsi_val',0)>70 else ('#00FF00' if c.get('rsi_val',0)<30 else '#00d2ff')};">{c.get('rsi_val',0):.1f}</strong></span>
        <span>乖離率(20): <strong style="color:{'#ff4d4d' if c.get('bias_val',0)>0 else '#00FF00'};">{c.get('bias_val',0):.2f}%</strong></span>
    </div>
    <div style="font-size:12px; color:#aaa; margin-top:6px; border-top:1px dashed #444; padding-top:4px;">
        <span style="color:#ff4d4d;">進攻參考:</span> {c.get('atk_zone', '無')} | <span style="color:#00FF00;">防守停損:</span> {c.get('def_line', '無')}
    </div>
</div>
<div class="zone-box">
    <div class="shadow-box">
        <div class="zone-title">📊 第三戰區：三大法人與主力籌碼 (SQLite)</div>
        <div style="font-size:13px; margin-bottom:4px;"><b>[外資]</b> 單日: <strong style="color:#ff4d4d;">{int(c.get('f_buy',0)):+,}張 (佔 {float(c.get('f_pct',0)):.1f}%)</strong> | 5日: <strong>{int(c.get('f_5d',0)):+,}張</strong></div>
        <div style="font-size:13px; margin-bottom:6px;"><b>[投信]</b> 單日: <strong style="color:#ff4d4d;">{int(c.get('t_buy',0)):+,}張 (佔 {float(c.get('t_pct',0)):.1f}%)</strong> | 5日: <strong>{int(c.get('t_5d',0)):+,}張</strong></div>
        <div style="font-size:12px; border-top:1px dashed #444; padding-top:6px; display:flex; justify-content:space-between; color:#aaa;">
            <span>千張大戶({c.get('big_holder_date')}): <strong style="color:#00d2ff;">{c.get('big_holder',0.0)}%</strong></span>
            <span>自營商: {int(c.get('d_buy',0)):+,}張</span>
        </div>
    </div>
</div>
<div style="background:{c.get('signal_bg')}; padding:10px; border-radius:5px; text-align:center; margin-top:8px;"><strong style="color:{c.get('color_border')}; font-size:15px;">決策判定：{c.get('signal_text')}</strong></div>
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
                else: st.warning(f"⚠️ {code} 同步狀態: {msg}")
                time.sleep(1); st.rerun() 
            
        st.markdown("<div style='font-size:13px; font-weight:bold; color:#00d2ff; margin-top:10px;'>✏️ 人工覆寫 (7日後自動過期恢復)</div>", unsafe_allow_html=True)
        m_cols = st.columns([1, 1, 1])
        m_month = m_cols[0].text_input("月份", value="06月", key=f"my_mo_{code}{btn_suffix}")
        m_y = m_cols[1].number_input("營收年增(%)", -100.0, 1000.0, float(card.get('rev_yoy', 0.0)), 0.1, key=f"my_y_{code}{btn_suffix}")
        b_ratio = m_cols[2].number_input("大戶比例(%)", 0.0, 100.0, float(card.get('big_holder', 0.0)), 0.1, key=f"my_bh_{code}{btn_suffix}")
        
        if st.button("✅ 寫入覆寫保護", key=f"btn_override_{code}{btn_suffix}", use_container_width=True):
            now_ts = datetime.now().timestamp()
            st.session_state.revenue_override[code] = {'yoy': m_y, 'mom': card.get('rev_mom', 0.0), 'month': m_month, 'ts': now_ts}
            st.session_state.bigholder_override[code] = {'ratio': b_ratio, 'date': datetime.now().strftime("%m/%d"), 'ts': now_ts}
            save_local_db_isolated(); st.success("資料鎖定成功 (7日後自動失效)！"); time.sleep(0.5); st.rerun()
            
        if st.button("🤖 解鎖 NVIDIA 戰略推演", key=f"ai_single_{code}{btn_suffix}", use_container_width=True):
            st.session_state.single_ai_trigger = code
            with st.spinner("NVIDIA 輪替陣列推演中..."):
                rep = execute_single_stock_ai_推演(card)
                st.session_state.single_ai_report[code] = rep
                st.session_state.analysis_history[code]['nv_history'].append({"time": datetime.now().strftime("%Y-%m-%d %H:%M"), "report": rep})
                save_local_db_isolated()

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

# --- 模擬倉與雷達區 ---
if getattr(st.session_state, 'portfolio', {}):
    with st.expander("💼 總指揮常態持倉模擬倉", expanded=True):
        cols, idx = st.columns(2), 0
        for code, p_data in list(st.session_state.portfolio.items()):
            c = calculate_comprehensive_signals(code, False)
            if c and not c.get('error'):
                ent_p = safe_float(p_data.get('entry_price', c.get('price')))
                profit, roi = calc_real_profit(ent_p, float(c.get('price', 0.0)), safe_float(p_data.get('qty', 1)))
                with cols[idx % 2]:
                    st.markdown(render_commander_stock_card(c, True, profit, roi, ent_p), unsafe_allow_html=True)
                    render_action_buttons(c, code, True)
                idx += 1

if getattr(st.session_state, 'pinned_stocks', {}):
    with st.expander("🎯 總指揮常態觀測雷達防線", expanded=True):
        cols, idx = st.columns(2), 0
        for code in list(st.session_state.pinned_stocks.keys()):
            c = calculate_comprehensive_signals(code, False)
            if c and not c.get('error'):
                with cols[idx % 2]:
                    st.markdown(render_commander_stock_card(c), unsafe_allow_html=True)
                    render_action_buttons(c, code, False)
                idx += 1

# --- 多執行緒掃描區 ---
if getattr(st.session_state, 'trigger_scan', False):
    st.session_state.trigger_scan = False
    st.session_state.scan_results.clear()
    
    results = []
    target_pool = GLOBAL_MARKET_CODES[:300] 
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    # 導入 ThreadPoolExecutor 解決 I/O 等待問題
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        future_to_code = {executor.submit(calculate_comprehensive_signals, c, enable_doomsday_lock): c for c in target_pool}
        for i, future in enumerate(concurrent.futures.as_completed(future_to_code)):
            c = future_to_code[future]
            status_text.markdown(f"<div style='color:#00d2ff; font-size:13px; font-weight:bold;'>📡 並行掃描進度: {i+1}/{len(target_pool)} ({int((i+1)/len(target_pool)*100)}%)</div>", unsafe_allow_html=True)
            progress_bar.progress((i + 1) / len(target_pool))
            
            try: card = future.result()
            except Exception: continue
            
            c_vol = float(card.get('vol', 0) or 0)
            c_price = float(card.get('price', 0) or 0)
            c_ma60 = float(card.get('ma60', 0) or 0)
            c_vol_ratio = float(card.get('vol_ratio', 0) or 0)
            c_vol_chg = float(card.get('vol_change_pct', 0) or 0)
            c_tbuy = int(card.get('t_buy', 0) or 0)
            c_fbuy = int(card.get('f_buy', 0) or 0)
            c_margin = int(card.get('margin_diff', 0) or 0)
            c_rev_yoy = float(card.get('rev_yoy', 0) or 0)
            c_kdj = str(card.get('kdj_str', ''))
            
            if card and not card.get('error', False) and c_vol >= (min_volume_filter / 1000):
                meets_all = True
                for cmd in selected_cmds:
                    if "查1." in cmd and not (card.get('is_first_red') and c_vol_ratio >= 2.0 and "金叉" in c_kdj): meets_all = False
                    elif "查2." in cmd and not (c_price > c_ma60 and c_vol_ratio >= 1.2): meets_all = False
                    elif "查4." in cmd and not (c_tbuy > 0): meets_all = False
                    elif "查5." in cmd and not (c_fbuy > 0 and c_margin < 0): meets_all = False
                    elif "查6." in cmd and not (c_rev_yoy > 20): meets_all = False
                    elif "查8." in cmd and not (card.get('is_yesterday_strong')): meets_all = False
                    elif "查9." in cmd and not (c_vol_ratio >= 2.0): meets_all = False
                    elif "查10." in cmd and not (c_vol_chg < -40 and c_margin < 0): meets_all = False
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
