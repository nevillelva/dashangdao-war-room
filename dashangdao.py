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

# 關閉 Pandas 運算警告，確保版面純淨
warnings.filterwarnings('ignore')

# ==========================================
# 基礎配置與狀態初始化
# ==========================================
st.set_page_config(layout="wide", page_title="54088 - 戰情室 V78.0", initial_sidebar_state="expanded")

# [系統防護] 全面啟用雲端保險箱 (Secrets)
try:
    COMMANDER_PIN = st.secrets["radar_secrets"]["commander_pin"]
    raw_keys = st.secrets["radar_secrets"]["gemini_api_key"]
    GEMINI_API_KEYS = [k.strip() for k in raw_keys.split(",") if k.strip()]
    SECRET_FINMIND = st.secrets["radar_secrets"].get("finmind_token", "")
except KeyError:
    st.error("[致命錯誤] 雲端保險箱 (Secrets) 未設定或設定錯誤！請檢查 Streamlit Cloud 後台設定。")
    st.stop()

USER_DB_FILE = "54088_database.json" 
FUNDAMENTALS_DB_FILE = "54088_fundamentals_cache.json"

if 'ai_mode' not in st.session_state: st.session_state.ai_mode = "快速 (Flash)"
if 'scan_results' not in st.session_state: st.session_state.scan_results = []
if 'scan_mode' not in st.session_state: st.session_state.scan_mode = ""
if 'ai_report' not in st.session_state: st.session_state.ai_report = ""
if 'temp_intel' not in st.session_state: st.session_state.temp_intel = []
if 'portfolio' not in st.session_state: st.session_state.portfolio = {}
if 'pinned_stocks' not in st.session_state: st.session_state.pinned_stocks = {}
if 'active_key_index' not in st.session_state: st.session_state.active_key_index = 0

if 'db_loaded' not in st.session_state:
    if os.path.exists(USER_DB_FILE):
        try:
            with open(USER_DB_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                st.session_state.pinned_stocks = data.get("pinned_stocks", {})
                st.session_state.portfolio = data.get("portfolio", {})
        except: pass
    st.session_state.db_loaded = True

def save_db():
    try:
        with open(USER_DB_FILE, "w", encoding="utf-8") as f: 
            json.dump({
                "pinned_stocks": st.session_state.pinned_stocks, 
                "portfolio": st.session_state.portfolio
            }, f, ensure_ascii=False, indent=4)
    except: pass

# ==========================================
# 身份驗證
# ==========================================
if 'authenticated' not in st.session_state: 
    st.session_state.authenticated = (st.query_params.get("auth") == "54088")

if not st.session_state.authenticated:
    st.markdown("<h2 style='text-align: center; color: #444; margin-top: 10vh; letter-spacing: 5px;'>SYSTEM LOCKED</h2>", unsafe_allow_html=True)
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        pwd = st.text_input("輸入授權密碼", type="password")
        if st.button("系統解鎖", use_container_width=True):
            if pwd == COMMANDER_PIN: 
                st.session_state.authenticated = True
                st.rerun()
            else: st.error("[錯誤] 密碼錯誤")
    st.stop()

# ==========================================
# 視覺與樣式定義 (軍規極簡版)
# ==========================================
st.markdown("""<style>
.stApp { background-color: #0b0c0f !important; color: #fff !important; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
div[data-testid="stSidebar"] { background-color: #12141a !important; border-right: 1px solid #333 !important; }
div[data-testid="stButton"] > button { background-color: #1e1e24 !important; border: 1px solid #444 !important; transition: all 0.2s ease-in-out; }
div[data-testid="stButton"] > button p { color: #ffffff !important; font-weight: bold !important; font-size: 15px !important; }
.scan-btn div[data-testid="stButton"] > button { background-color: #3a1515 !important; border: 2px solid #ff4d4d !important; margin-bottom: 5px;}
.scan-btn div[data-testid="stButton"] > button p { color: #ff4d4d !important; font-weight: bold !important; }
.cmd-btn div[data-testid="stButton"] > button { background-color: #15203a !important; border: 2px solid #00d2ff !important; margin-bottom: 5px;}
.cmd-btn div[data-testid="stButton"] > button p { color: #00d2ff !important; font-weight: bold !important; }
.hud-box { background: linear-gradient(135deg, #1a1c23 0%, #0d1117 100%); border-radius: 10px; padding: 15px; border-left: 5px solid #ff4d4d; box-shadow: 0 4px 15px rgba(0,0,0,0.5); margin-bottom: 20px;}
.hud-title { color: #f1c40f; font-size: 14px; font-weight: bold; margin-bottom: 10px; border-bottom: 1px solid #333; padding-bottom: 5px;}
.hud-metric { display: flex; justify-content: space-between; align-items: center; margin-bottom: 5px;}
.health-bar-bg { width: 100%; background-color: #333; border-radius: 5px; height: 8px; margin-top: 5px; overflow: hidden;}
.health-bar-fill-red { height: 100%; background-color: #ff4d4d; transition: width 0.5s ease;}
.health-bar-fill-green { height: 100%; background-color: #00FF00; transition: width 0.5s ease;}
.tag-red { background: #3a1515; padding: 4px 8px; border-radius: 4px; font-size: 13px; color: #ff4d4d; border: 1px solid #e74c3c; display: inline-block; margin: 0 5px 5px 0; font-weight: bold; }
.tag-green { background: #153a20; padding: 4px 8px; border-radius: 4px; font-size: 13px; color: #00FF00; border: 1px solid #2ecc71; display: inline-block; margin: 0 5px 5px 0; font-weight: bold; }
.tag-gray { background: #222; padding: 4px 8px; border-radius: 4px; font-size: 13px; color: #aaa; border: 1px solid #555; display: inline-block; margin: 0 5px 5px 0; font-weight: bold; }
.tag-blue { background: #15203a; padding: 4px 8px; border-radius: 4px; font-size: 13px; color: #00d2ff; border: 1px solid #3498db; display: inline-block; margin: 0 5px 5px 0; font-weight: bold; }
.tag-purple { background: #2a153a; padding: 4px 8px; border-radius: 4px; font-size: 13px; color: #d200ff; border: 1px solid #9b59b6; display: inline-block; margin: 0 5px 5px 0; font-weight: bold; }
.tactical-summary { background: #000; border-top: 1px dashed #444; margin-top: 10px; padding: 12px; font-size: 14px; color: #ddd; border-radius: 5px; line-height: 1.6;}
.tactical-danger { background: #153a20; border-top: 1px dashed #2ecc71; margin-top: 10px; padding: 12px; font-size: 14px; color: #ddd; border-radius: 5px; line-height: 1.6;}
.metric-grid { display: flex; gap: 15px; flex-wrap: wrap; font-size: 13px; color: #ccc; margin-bottom: 10px; background: #10141d; padding: 12px; border-radius: 6px; border: 1px solid #333;}
.ai-report-box { background: #1a1a24; border-left: 5px solid #d200ff; padding: 20px; border-radius: 8px; margin-bottom: 20px; border: 1px solid #d200ff40; font-size: 15px; line-height: 1.6; font-family: sans-serif;}
.key-status-ok { color: #00FF00; font-weight: bold; font-size: 13px; word-break: break-all;}
.key-status-fail { color: #ff4d4d; font-weight: bold; font-size: 13px; word-break: break-all;}
div[data-baseweb="select"] > div { background-color: #1a1a24 !important; border: 1px solid #444 !important; }
</style>""", unsafe_allow_html=True)

# ==========================================
# 資料獲取與演算法模組
# ==========================================
def get_safe_session():
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Accept": "text/html,application/json"
    })
    return session

def safe_float(val):
    if pd.isna(val) or val is None or str(val).strip() == '': return 0.0
    try:
        s = str(val).upper().replace(',', '').replace('-', '').replace('N/A', '').strip()
        s = re.sub(r'[^\d.]', '', s)
        return float(s) if s else 0.0
    except: return 0.0

@st.cache_data(ttl=86400, show_spinner=False)
def fetch_stock_names():
    api_names = {}
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        res = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL", timeout=5, headers=headers)
        if res.status_code == 200:
            for item in res.json():
                c = str(item.get('Code', '')).strip()
                n = str(item.get('Name', '')).strip()
                if len(c) == 4 and c.isdigit() and n: api_names[c] = n
    except: pass
    try:
        res2 = requests.get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_peratio_analysis", timeout=5, headers=headers)
        if res2.status_code == 200:
            for item in res2.json():
                c = str(item.get('SecuritiesCompanyCode', '')).strip()
                n = str(item.get('CompanyName', '')).strip()
                if len(c) == 4 and c.isdigit() and n: api_names[c] = n
    except: pass
    fallbacks = {"2330":"台積電", "2303":"聯電", "2317":"鴻海", "2454":"聯發科", "2382":"廣達", "2603":"長榮", "1519":"華城", "3017":"奇鋐", "3324":"雙鴻", "2313":"華通", "3231":"緯創", "2356":"英業達", "1558":"伸興工業", "2412":"中華電信", "3260":"威剛", "3008":"大立光"}
    for k, v in fallbacks.items():
        if k not in api_names: api_names[k] = v
    return api_names

@st.cache_data(ttl=86400, show_spinner=False)
def get_fallback_name(symbol):
    try:
        res = requests.get(f"https://tw.stock.yahoo.com/quote/{symbol}", timeout=3, headers={"User-Agent": "Mozilla/5.0"})
        if res.status_code == 200:
            match = re.search(r'<title>(.*?)\(', res.text)
            if match:
                name = match.group(1).strip()
                if name and not name.isdigit(): return name
    except: pass
    return symbol

def load_local_fundamentals():
    if os.path.exists(FUNDAMENTALS_DB_FILE):
        try:
            with open(FUNDAMENTALS_DB_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except: return {}
    return {}

def save_local_fundamentals(db):
    if len(db) > 500:
        try:
            with open(FUNDAMENTALS_DB_FILE, "w", encoding="utf-8") as f:
                json.dump(db, f, ensure_ascii=False)
        except: pass

@st.cache_data(ttl=3600, show_spinner=False)
def fetch_fundamentals():
    db = load_local_fundamentals() 
    new_db = {}
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        res1 = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL", timeout=5, headers=headers)
        if res1.status_code == 200:
            for item in res1.json():
                code = str(item.get('Code', '')).strip()
                if len(code) == 4 and code.isdigit():
                    new_db[code] = {'PE': safe_float(item.get('PeRatio')), 'PB': safe_float(item.get('PbRatio')), 'Yield': safe_float(item.get('DividendYield'))}
    except: pass
    try:
        res2 = requests.get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_peratio_analysis", timeout=5, headers=headers)
        if res2.status_code == 200:
            for item in res2.json():
                code = str(item.get('SecuritiesCompanyCode', '')).strip()
                if len(code) == 4 and code.isdigit():
                    pe = item.get('PeRatio') or item.get('PERatio') or item.get('PriceEarningRatio')
                    pb = item.get('PbRatio') or item.get('PBRatio') or item.get('PriceBookRatio')
                    yld = item.get('DividendYield') or item.get('Yield')
                    new_db[code] = {'PE': safe_float(pe), 'PB': safe_float(pb), 'Yield': safe_float(yld)}
    except: pass
    
    if len(new_db) > 500:
        db.update(new_db)
        save_local_fundamentals(db)
        
    return db

@st.cache_data(ttl=3600, show_spinner=False)
def fetch_institutional_data():
    inst_db = {}
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        res = requests.get("https://openapi.twse.com.tw/v1/fund/T86_ALL", timeout=5, headers=headers)
        if res.status_code == 200:
            for item in res.json():
                code = str(item.get('Code', '')).strip()
                inst_db[code] = {'foreign': int(safe_float(item.get('ForeignDifference'))), 'trust': int(safe_float(item.get('InvestmentTrustDifference')))}
    except: pass
    try:
        res2 = requests.get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_3itrade_hedge", timeout=5, headers=headers)
        if res2.status_code == 200:
            for item in res2.json():
                code = str(item.get('SecuritiesCompanyCode', '')).strip()
                f_diff = int(safe_float(item.get('ForeignInvestorsDifference')))
                t_diff = int(safe_float(item.get('InvestmentTrustDifference')))
                if code in inst_db:
                    inst_db[code]['foreign'] += f_diff
                    inst_db[code]['trust'] += t_diff
                else:
                    inst_db[code] = {'foreign': f_diff, 'trust': t_diff}
    except: pass
    return inst_db

TW_STOCK_NAMES = fetch_stock_names()
FUNDAMENTAL_DB = fetch_fundamentals()
INST_DB = fetch_institutional_data()
GLOBAL_MARKET_CODES = list(TW_STOCK_NAMES.keys())

@st.cache_data(ttl=3600, show_spinner=False)
def get_finmind_and_deep_fundamentals(symbol, token_string, curr_price):
    pe = pb = yld = roe = margin = rev_growth = 0.0
    
    session = get_safe_session()
    for ext in [".TW", ".TWO"]:
        try:
            url = f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/{symbol}{ext}?modules=summaryDetail,defaultKeyStatistics,financialData"
            res = session.get(url, timeout=3)
            if res.status_code == 200:
                data = res.json().get('quoteSummary', {}).get('result', [])
                if data:
                    summary = data[0].get('summaryDetail', {})
                    stats = data[0].get('defaultKeyStatistics', {})
                    financials = data[0].get('financialData', {})
                    
                    def _ext(d, k):
                        val = d.get(k, {})
                        return float(val.get('raw', 0.0)) if isinstance(val, dict) else 0.0
                        
                    pe = _ext(summary, 'trailingPE') or _ext(stats, 'forwardPE')
                    pb = _ext(stats, 'priceToBook') or _ext(summary, 'priceToBook')
                    yld = _ext(summary, 'dividendYield') or _ext(summary, 'trailingAnnualDividendYield')
                    yld = yld * 100 if yld > 0 else 0.0
                    
                    roe = _ext(financials, 'returnOnEquity') * 100
                    margin = _ext(financials, 'grossMargins') * 100
                    rev_growth = _ext(financials, 'revenueGrowth') * 100
                    
                    if abs(pe - curr_price) < 0.1: pe = 0.0
                    if abs(pb - curr_price) < 0.1: pb = 0.0
                    if pe > 0 or pb > 0:
                        return pe, pb, yld, roe, margin, rev_growth
        except: pass

    url = "https://api.finmindtrade.com/api/v4/data"
    date_str = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
    tokens = [t.strip() for t in token_string.split(',') if t.strip()]
    auth_methods = [None] + tokens
    for auth in auth_methods:
        params = {"dataset": "TaiwanStockPER", "data_id": symbol, "start_date": date_str}
        if auth: params["token"] = auth
        try:
            res = requests.get(url, params=params, timeout=3)
            if res.status_code == 200:
                data = res.json()
                if data.get('msg') == 'success' and data.get('data'):
                    latest = data['data'][-1]
                    pe = safe_float(latest.get('PER', 0))
                    pb = safe_float(latest.get('PBR', 0))
                    yld = safe_float(latest.get('dividend_yield', 0))
                    if pe > 0 or pb > 0: 
                        return pe, pb, yld, 0.0, 0.0, 0.0
        except: pass
        
    return 0.0, 0.0, 0.0, 0.0, 0.0, 0.0

@st.cache_data(ttl=60, show_spinner=False)
def get_market_weather():
    try:
        session = get_safe_session()
        tk_twii = yf.Ticker("^TWII", session=session)
        twii = tk_twii.history(period="3mo").dropna(subset=['Close'])
        tk_twoii = yf.Ticker("^TWOII", session=session)
        twoii = tk_twoii.history(period="1mo").dropna(subset=['Close'])
        
        try:
            live_twii = tk_twii.history(period="1d", interval="1m").dropna(subset=['Close'])
            if not live_twii.empty and not twii.empty:
                twii.loc[twii.index[-1], 'Close'] = float(live_twii['Close'].iloc[-1])
        except: pass

        if twii.empty: return "[大盤連線異常]", "#888", False, False, 0.0
        
        c_idx = float(twii['Close'].iloc[-1])
        prev_idx = float(twii['Close'].iloc[-2])
        twii_gain = ((c_idx - prev_idx) / prev_idx) * 100
        ma20 = float(twii['Close'].rolling(20).mean().iloc[-1])
        
        two_gain = 0.0
        if len(twoii) >= 2:
            two_curr = float(twoii['Close'].iloc[-1])
            two_prev = float(twoii['Close'].iloc[-2])
            two_gain = ((two_curr - two_prev) / two_prev) * 100

        is_panic = (twii_gain <= -3.0) or (c_idx < float(twii['Close'].rolling(60).mean().iloc[-1]) * 0.95)
        display_str = f"上市: {c_idx:,.0f} ({twii_gain:+.2f}%) | 上櫃: ({two_gain:+.2f}%)"
        
        if is_panic: return f"[恐慌斷頭潮] {display_str}", "#00FF00", c_idx > ma20, True, twii_gain
        elif c_idx > ma20: return f"[多頭順風環境] {display_str}", "#ff4d4d", True, False, twii_gain
        else: return f"[空頭震盪環境] {display_str}", "#f1c40f", False, False, twii_gain
    except: return "[大盤資料獲取中...]", "#888", False, False, 0.0

weather_str, weather_color, is_bull_market, is_panic, global_twii_gain = get_market_weather()

@st.cache_data(ttl=60, show_spinner=False)
def get_stock_data(symbol):
    session = get_safe_session()
    for ext in [".TW", ".TWO"]:
        try:
            tk = yf.Ticker(symbol + ext, session=session)
            hist = tk.history(period="1y").dropna(subset=['Close'])
            if hist.empty: continue
            try:
                live_data = tk.history(period="1d", interval="1m").dropna(subset=['Close'])
                if not live_data.empty:
                    live_close = float(live_data['Close'].iloc[-1])
                    live_high = float(live_data['High'].max())
                    live_low = float(live_data['Low'].min())
                    live_vol = float(live_data['Volume'].sum())
                    last_date = hist.index[-1].date()
                    live_date = live_data.index[-1].date()
                    if live_date > last_date:
                        new_row = hist.iloc[-1].copy()
                        new_row['Open'] = float(live_data['Open'].iloc[0])
                        new_row['High'] = live_high
                        new_row['Low'] = live_low
                        new_row['Close'] = live_close
                        new_row['Volume'] = live_vol
                        hist.loc[live_data.index[-1]] = new_row
                    elif live_date == last_date:
                        hist.loc[hist.index[-1], 'Close'] = live_close
                        hist.loc[hist.index[-1], 'High'] = max(float(hist.iloc[-1]['High']), live_high)
                        hist.loc[hist.index[-1], 'Low'] = min(float(hist.iloc[-1]['Low']), live_low)
                        hist.loc[hist.index[-1], 'Volume'] = max(float(hist.iloc[-1]['Volume']), live_vol)
            except: pass
            
            if not hist.empty and len(hist) > 26:
                return hist, 0.0, 0.0, 0.0
        except: pass
    return None

def calculate_signals(symbol, data_tuple, portfolio_data=None, is_panic_global=False, twii_gain=0.0, is_scan=False):
    if data_tuple is None or len(data_tuple) != 4: return None
    hist_df, _, _, _ = data_tuple
    if hist_df is None or hist_df.empty or len(hist_df) < 26: return None
    
    stock_name = TW_STOCK_NAMES.get(symbol, symbol)
    if stock_name == symbol or str(stock_name).isdigit():
        stock_name = get_fallback_name(symbol)
        TW_STOCK_NAMES[symbol] = stock_name 

    curr = float(hist_df['Close'].iloc[-1])

    fund_info = FUNDAMENTAL_DB.get(symbol, {})
    pe = fund_info.get('PE', 0.0)
    pb = fund_info.get('PB', 0.0)
    yld = fund_info.get('Yield', 0.0)
    roe = margin = rev_growth = 0.0

    if not is_scan:
        pe_api, pb_api, yld_api, roe, margin, rev_growth = get_finmind_and_deep_fundamentals(symbol, SECRET_FINMIND, curr)
        if pe == 0.0: pe = pe_api
        if pb == 0.0: pb = pb_api
        if yld == 0.0: yld = yld_api
        if pe > 0 or pb > 0:
            FUNDAMENTAL_DB[symbol] = {'PE': pe, 'PB': pb, 'Yield': yld}
            save_local_fundamentals(FUNDAMENTAL_DB)

    score = 50
    if 0 < pe < 15: score += 20
    elif pe > 25: score -= 15
    if 0 < pb < 1.5: score += 20
    elif pb > 3.0: score -= 15
    if yld >= 5.0: score += 10
    score = max(0, min(100, score))

    if pe == 0.0 and pb == 0.0: val_shield = "[無基本面]"; score = 0
    elif score >= 70: val_shield = "[價值低估]"
    elif score <= 40: val_shield = "[估值過高]"
    else: val_shield = "[估值適中]"

    prev = max(float(hist_df['Close'].iloc[-2]), 0.001)
    open_p = float(hist_df['Open'].iloc[-1])
    high_p = float(hist_df['High'].iloc[-1])
    low_p = float(hist_df['Low'].iloc[-1])
    gain = ((curr - prev) / prev) * 100
    
    vol = int(hist_df['Volume'].iloc[-1] / 1000)
    vol_5d = max(hist_df['Volume'].iloc[-6:-1].mean() / 1000, 0.01)
    vol_ratio = vol / vol_5d if vol_5d > 0 else 1.0
    
    rs_score = gain - twii_gain
    is_anti_drop = (rs_score >= 1.5 and gain >= -1.0)
    
    f_buy = t_buy = 0
    chip_conc = 0.0
    inst_tag = ""
    if symbol in INST_DB:
        f_buy = INST_DB[symbol].get('foreign', 0)
        t_buy = INST_DB[symbol].get('trust', 0)
        if f_buy > 0 and t_buy > 0: inst_tag = "G. 土洋齊買"
        elif t_buy > 0: inst_tag = "H. 投信買超"
        elif f_buy > 0: inst_tag = "I. 外資買超"
        
        inst_buy_total = f_buy + t_buy
        if vol > 0 and inst_buy_total > 0:
            chip_conc = (inst_buy_total / vol) * 100
    
    has_inst_support = inst_tag in ["G. 土洋齊買", "H. 投信買超"]
    
    calc_df = hist_df.copy()
    recent_low = calc_df['Low'].tail(10).min()
    
    ma5 = calc_df['Close'].rolling(min(5, len(calc_df))).mean().iloc[-1]
    ma10 = calc_df['Close'].rolling(min(10, len(calc_df))).mean().iloc[-1]
    ma20 = calc_df['Close'].rolling(min(20, len(calc_df))).mean().iloc[-1]
    ma60 = calc_df['Close'].rolling(min(60, len(calc_df))).mean().iloc[-1] if len(calc_df) >= 60 else ma20
    ma240 = calc_df['Close'].rolling(min(240, len(calc_df))).mean().iloc[-1] if len(calc_df) >= 240 else ma60

    low_min = calc_df['Low'].rolling(min(9, len(calc_df))).min()
    high_max = calc_df['High'].rolling(min(9, len(calc_df))).max()
    rsv = (calc_df['Close'] - low_min) / (high_max - low_min + 1e-9) * 100
    calc_df['K'] = rsv.bfill().ffill().fillna(50).ewm(com=2, adjust=False).mean()
    calc_df['D'] = calc_df['K'].bfill().ffill().fillna(50).ewm(com=2, adjust=False).mean()
    k = calc_df['K'].iloc[-1] if not pd.isna(calc_df['K'].iloc[-1]) else 50
    d_val = calc_df['D'].iloc[-1] if not pd.isna(calc_df['D'].iloc[-1]) else 50
    is_kdj_golden = (k < 50) and (calc_df['K'].iloc[-2] <= calc_df['D'].iloc[-2]) and (k > d_val)
    is_kdj_dead = (k > 70) and (calc_df['K'].iloc[-2] >= calc_df['D'].iloc[-2]) and (k < d_val)
    kdj_str = "金叉" if is_kdj_golden else ("死叉" if is_kdj_dead else ("向上" if k > d_val else "向下"))

    exp1 = calc_df['Close'].ewm(span=12, adjust=False).mean()
    exp2 = calc_df['Close'].ewm(span=26, adjust=False).mean()
    macd = exp1 - exp2
    macd_signal = macd.ewm(span=9, adjust=False).mean()
    macd_hist = macd - macd_signal
    macd_val = macd_hist.iloc[-1] if not macd_hist.empty and not pd.isna(macd_hist.iloc[-1]) else 0.0
    macd_prev = macd_hist.iloc[-2] if len(macd_hist) >= 2 and not pd.isna(macd_hist.iloc[-2]) else 0.0
    is_macd_golden = (macd_prev <= 0) and (macd_val > 0)
    is_macd_dead = (macd_prev >= 0) and (macd_val < 0)
    macd_str = "金叉" if is_macd_golden else ("死叉" if is_macd_dead else ("紅柱" if macd_val > 0 else "綠柱"))

    delta = calc_df['Close'].diff()
    gain_series = delta.where(delta > 0, 0.0)
    loss_series = -delta.where(delta < 0, 0.0)
    avg_gain = gain_series.ewm(alpha=1/14, adjust=False).mean()
    avg_loss = loss_series.ewm(alpha=1/14, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    calc_df['RSI'] = 100 - (100 / (1 + rs))
    calc_df['RSI'].fillna(50, inplace=True)
    rsi_val = calc_df['RSI'].iloc[-1]

    bb_std = calc_df['Close'].rolling(20).std().iloc[-1]
    if pd.isna(bb_std): bb_std = 0.0
    bb_up = ma20 + (2 * bb_std)
    bb_down = ma20 - (2 * bb_std)

    pattern_str = "[區間盤整]"
    if curr >= bb_up and vol_ratio >= 1.5: pattern_str = "[強勢突破上軌]"
    elif curr <= bb_down: pattern_str = "[弱勢破底]"
    elif is_kdj_golden and rsi_val > 40 and curr > ma5: pattern_str = "[W底起漲型態]"
    elif rsi_val > 80: pattern_str = "[短線極度超買]"
    elif rsi_val < 20: pattern_str = "[短線極度超賣]"

    is_ma_bullish = (curr > ma5) and (ma5 > ma20) and (ma20 > ma60)
    is_vol_breakout = vol_ratio >= 2.0 and gain >= 2.0
    is_stealth = (curr > ma60) and (gain < 2.0) and (curr < ma60 * 1.1) and (vol_ratio >= 1.2)
    is_yield_def = (curr > ma240) and (curr < ma60 * 1.05) and (yld >= 5.0)
    
    body = abs(curr - open_p)
    upper_shadow = high_p - max(open_p, curr)
    is_shooting_star = (upper_shadow > body * 1.5) and (high_p > ma5)
    is_fake_breakout = (vol_ratio >= 2.0) and is_shooting_star
    is_break_ma5 = curr < ma5

    start_signals = []
    if is_kdj_golden: start_signals.append("KDJ金叉")
    if is_macd_golden: start_signals.append("MACD金叉")
    if is_vol_breakout: start_signals.append("爆量上攻")
    retreat_signals = []
    if is_fake_breakout: retreat_signals.append("假突破(避雷針)")
    if is_kdj_dead or is_macd_dead: retreat_signals.append("高檔死叉")
    if is_break_ma5: retreat_signals.append("跌破5日線")

    entry_price = float(portfolio_data.get('entry_price', 0.0)) if portfolio_data else 0.0
    roi_pct = ((curr - entry_price) / entry_price) * 100 if entry_price > 0 else 0.0
    
    open_price = open_p
    st_stop_val = round(curr * 0.98, 1)
    st_stop = str(st_stop_val)

    if curr > ma5:
        st_buy = f"{round(ma5, 1)} ~ {round(curr, 1)}"
    else:
        st_buy = f"{round(recent_low, 1)} ~ {round(curr, 1)}"

    if curr > ma60:
        lt_buy = f"{round(ma60, 1)} ~ {round(ma20, 1)}"
        lt_stop = str(round(ma60 * 0.95, 1))
    else:
        lt_buy = "不建議佈局"
        lt_stop = "N/A"

    is_momentum_healthy = (k > d_val) or (macd_val > 0)
    
    ma_explain = f"股價立於季線 ({round(ma60,1)}) 之上，長線多頭。" if curr > ma60 else f"股價跌破季線 ({round(ma60,1)})，長線空頭。"
    kdj_explain = "短線動能強勁" if is_kdj_golden or k > d_val else "短線動能轉弱"
    macd_explain = "中長線趨勢向上" if macd_val > 0 else "中長線趨勢向下"

    if curr > ma60 and curr > ma5:
        if is_momentum_healthy:
            signal_text = "[偏多操作]"
            color_border = "#ff4d4d"
            signal_bg = "#3a1515"
            decision_text = "多方強勢，長短線皆多，趨勢向上。"
            conflict_text = "指標健康，可沿 5 日線伺機佈局短單或波段。"
        else:
            signal_text = "[高檔觀望]"
            color_border = "#f1c40f"
            signal_bg = "#332b00"
            decision_text = "價格偏多，但動能指標已開始轉弱。"
            conflict_text = "[防守警告] 股價創高但 KDJ/MACD 顯示上攻力道衰退，留意追高洗盤風險，建議短線空手。"
            st_buy = "動能衰退，建議短線空手"
            
    elif curr > ma60 and curr <= ma5:
        signal_text = "[拉回整理]"
        color_border = "#f1c40f"
        signal_bg = "#332b00"
        decision_text = "長線多頭下的短線拉回整理。"
        conflict_text = "短線指標降溫，屬健康回檔。波段資金可等待量縮回測季線支撐再佈局。"
        st_buy = "跌破短均線，短線不建議進場"
        
    elif curr <= ma60 and curr > ma5:
        signal_text = "[跌深反彈]"
        color_border = "#3498db"
        signal_bg = "#15203a"
        decision_text = "長線空頭格局下的技術性反彈。"
        if not is_momentum_healthy:
            conflict_text = "[警告] 弱勢反彈且短線指標再度轉弱！上方有季線強大解套賣壓，嚴格禁止進場。"
            st_buy = "反彈力道衰竭，絕對空手"
        else:
            conflict_text = "指標出現金叉反彈，受制於長線空頭，僅適合嚴格設定停損的短線快進快出。"
            
    else: 
        signal_text = "[空頭觀望]"
        color_border = "#00FF00"
        signal_bg = "#153a20"
        decision_text = "長短線皆空，趨勢全面向下。"
        conflict_text = "均線與指標全數偏空，毫無底層支撐，絕對嚴禁摸底猜低。"
        st_buy = "絕對禁止買進"
        lt_buy = "絕對禁止買進"

    chip_text = f"<br><span style='color:#ccc;'>D. 籌碼流向：外資投信單日買進 {f_buy+t_buy:,} 張 (佔總量 {chip_conc:.1f}%)</span>" if chip_conc > 0 else ""
    fin_text = f"<br><span style='color:#ccc;'>E. 財報透視：ROE {roe:.1f}% | 毛利率 {margin:.1f}% | 營收成長 {rev_growth:.1f}%</span>" if roe != 0.0 or margin != 0.0 else ""

    tactical_summary = f"""
    <div style="background:#15203a; border-left: 4px solid #00d2ff; padding: 12px; margin-top: 5px; border-radius: 4px;">
    <span style="color:#00d2ff; font-weight:bold; font-size:15px;">[戰情解析中樞]</span><br>
    <span style="color:#ccc;">A. 體質診斷：{ma_explain} 價值評估為{val_shield}。</span><br>
    <span style="color:#ccc;">B. 動能狀態：{kdj_explain}，且{macd_explain}。</span><br>
    <span style="color:#ccc;">C. 技術型態：RSI {rsi_val:.1f} {pattern_str}。</span>{chip_text}{fin_text}<br>
    <span style="color:#f1c40f; font-weight:bold;">[最終戰術]：{decision_text} {conflict_text}</span>
    </div>
    """

    is_action_needed = False
    is_golden_signal = False
    
    if entry_price > 0 and roi_pct <= -10.0:
        signal_text, color_border, signal_bg = "[觸發停損]", "#00FF00", "#153a20"; is_action_needed = True
    elif retreat_signals:
        signal_text, color_border, signal_bg = f"[撤退警告]", "#00FF00", "#153a20"; is_action_needed = True
    elif is_panic_global and curr <= ma60 * 1.05:
        signal_text, color_border, signal_bg = "[斷頭潮 左側重壓]", "#ff4d4d", "#3a1515"; is_golden_signal = True
    elif start_signals:
        signal_text, color_border, signal_bg = f"[起漲點火]", "#ff4d4d", "#3a1515"; is_golden_signal = True
    elif is_ma_bullish:
        signal_text, color_border, signal_bg = "[多頭確立]", "#ff4d4d", "#3a1515"; is_golden_signal = True
        
    if is_anti_drop and has_inst_support and not retreat_signals and not (entry_price > 0 and roi_pct <= -10.0):
        if signal_text in ["[高檔觀望]", "[拉回整理]"]:
            signal_text = "[抗跌籌碼防禦]"; color_border = "#3498db"; signal_bg = "#15203a"; is_golden_signal = True
            
    ai_tags = []
    if inst_tag: ai_tags.append(inst_tag)
    if is_anti_drop: ai_tags.append("E. 逆勢抗跌")
    elif rs_score <= -2.0 and gain < 0: ai_tags.append("F. 弱於大盤")
    if start_signals: ai_tags.append("A. 起漲第一根")
    if retreat_signals: ai_tags.append("B. 撤退警報")
    if is_ma_bullish: ai_tags.append("C. 均線多頭")
    if len(ai_tags) == 0: ai_tags.append("D. 量縮整理")

    return {
        "name": stock_name, "code": symbol, "price": curr, "gain": gain,
        "open": open_p, "high": high_p, "low": low_p, "vol": vol, "vol_5d": vol_5d, "rs_score": rs_score,
        "cost_label": "季線防守", "cost": round(ma60, 1), 
        "signal": signal_text, "color": color_border, 
        "signal_bg": signal_bg, "ai_tags": ai_tags, "tactical_summary": tactical_summary,
        "st_buy": st_buy, "st_stop": st_stop, "lt_buy": lt_buy, "lt_stop": lt_stop,
        "start_signals": "無" if not start_signals else ", ".join(start_signals),
        "retreat_signals": "無" if not retreat_signals else ", ".join(retreat_signals),
        "kdj_str": kdj_str, "macd_str": macd_str, "vol_ratio": vol_ratio, "val_score": score,
        "val_shield": val_shield, "pe": round(pe,1) if pe>0 else "N/A", "pb": round(pb,2) if pb>0 else "N/A", "yld": round(yld,1) if yld>0 else "N/A",
        "is_golden": is_golden_signal, 
        "is_action_needed": is_action_needed,
        "is_first_red": bool(start_signals), 
        "is_stealth": is_stealth,            
        "is_yield": is_yield_def             
    }

def calc_real_profit(cost, price, qty):
    if cost <= 0: return 0, 0
    buy_val = cost * qty * 1000
    sell_val = price * qty * 1000
    profit = sell_val - buy_val - max(20, int(buy_val * 0.001425)) - max(20, int(sell_val * 0.001425)) - int(sell_val * 0.003)
    return profit, (profit/buy_val)*100 if buy_val > 0 else 0

# ==========================================
# AI 神經元生成引擎 (V78.0 精準捕捉 429 錯誤)
# ==========================================
@st.cache_data(ttl=300, show_spinner=False)
def check_api_keys(keys, mode):
    status = []
    for i, k in enumerate(keys):
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models?key={k}"
            res = requests.get(url, timeout=5)
            working_model = None
            
            if res.status_code == 200:
                models = res.json().get('models', [])
                valid_models = [m.get('name', '').replace('models/', '') for m in models if 'generateContent' in m.get('supportedGenerationMethods', [])]
                
                target = "flash" if "快速" in mode else "pro"
                
                for m_name in valid_models:
                    if target in m_name.lower():
                        working_model = m_name
                        break
                        
                if not working_model and valid_models:
                    working_model = valid_models[0]
            
            if not working_model:
                working_model = "gemini-1.5-flash"
            
            ping_url = f"https://generativelanguage.googleapis.com/v1beta/models/{working_model}:generateContent?key={k}"
            headers = {'Content-Type': 'application/json'}
            payload = {"contents": [{"parts": [{"text": "ping"}]}]}
            ping_res = requests.post(ping_url, headers=headers, json=payload, timeout=10)
            
            if ping_res.status_code == 200:
                status.append({"index": i, "key": f"...{k[-4:]}", "status": "OK", "msg": f"[連線成功] {working_model}", "model": working_model})
            else:
                err = ping_res.json().get('error', {}).get('message', '未知錯誤')
                # 🚨 V78.0 攔截 Quota 錯誤，顯示友善軍規訊息
                if "quota" in err.lower() or "exceeded" in err.lower():
                    status.append({"index": i, "key": f"...{k[-4:]}", "status": "FAIL", "msg": "[彈藥耗盡] 免費額度已達上限", "model": working_model})
                else:
                    status.append({"index": i, "key": f"...{k[-4:]}", "status": "FAIL", "msg": f"[異常] {err[:20]}...", "model": working_model})
        except requests.exceptions.RequestException:
            status.append({"index": i, "key": f"...{k[-4:]}", "status": "FAIL", "msg": "[網路連線逾時或失敗]", "model": None})
        except Exception as e:
            status.append({"index": i, "key": f"...{k[-4:]}", "status": "FAIL", "msg": f"[系統錯誤] {str(e)[:20]}", "model": None})
    return status

def generate_ai_report(command_name, candidates):
    if not GEMINI_API_KEYS or not GEMINI_API_KEYS[0]: return "[系統提示] 雲端保險箱未配置有效的 API 金鑰。"
    
    lite_data = [{ '代號': c['code'], '名稱': c['name'], '價格': c['price'], '漲幅': c['gain'], '特徵': c['ai_tags'], 'KDJ': c['kdj_str'] } for c in candidates[:15]]
    
    prompt = f"""
    你是首席戰略幕僚。總指揮下達戰術：【{command_name}】。
    
    【嚴格紀律規範】
    1. 所有文字必須使用「繁體中文」。
    2. 強制清除 Emoji 濾網，報告中「絕對禁止」出現任何 Emoji 或表情符號。
    3. 必須使用大寫英文字母 (A., B., C.) 作為股票列舉的標籤。
    
    分析以下標的清單：{json.dumps(lite_data, ensure_ascii=False)}
    請挑選最精銳的 3 檔股票。回報格式需直接輸出，不需廢話：
    [AI 幕僚戰術報告：{command_name}]
    A. [股票代號 名稱] 
       - 入選理由與題材：(說明為何入選)
       - 總指揮觀測重點：(提醒進場或停損關鍵)
    (依此類推列出 B, C 兩檔)
    """
    
    key_statuses = check_api_keys(GEMINI_API_KEYS, st.session_state.ai_mode)
    start_idx = st.session_state.active_key_index
    last_error = ""
    
    for i in range(len(GEMINI_API_KEYS)):
        idx = (start_idx + i) % len(GEMINI_API_KEYS)
        k_stat = key_statuses[idx]
        
        if k_stat["status"] == "OK":
            key = GEMINI_API_KEYS[idx]
            model = k_stat["model"]
            try:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
                headers = {'Content-Type': 'application/json'}
                payload = {"contents": [{"parts": [{"text": prompt}]}]}
                res = requests.post(url, headers=headers, json=payload, timeout=25)
                
                if res.status_code == 200:
                    data = res.json()
                    text = data['candidates'][0]['content']['parts'][0]['text']
                    if idx != st.session_state.active_key_index:
                        st.session_state.active_key_index = idx
                    return f"**([啟動 {model} 核心運算])**\n\n{text}"
                else:
                    last_error = res.json().get('error', {}).get('message', '未知錯誤')
                    if "429" in str(res.status_code) or "quota" in last_error.lower():
                        k_stat["status"] = "FAIL" 
                        continue 
            except Exception as e:
                last_error = str(e)
                
    return f"[後勤告急] 所有金鑰皆無法使用或額度耗盡。最後錯誤：{last_error}"

# ==========================================
# 高階卡片渲染模組
# ==========================================
def draw_card(d, ui_key_prefix, is_portfolio=False, p_data=None):
    if not d: return
    gain_color = '#ff4d4d' if d['gain'] > 0 else ('#00FF00' if d['gain'] < 0 else '#aaaaaa')
    gain_bg = '#3a1515' if d['gain'] > 0 else ('#153a20' if d['gain'] < 0 else '#333333')
    tags_html = ""
    for tag in d.get('ai_tags', []):
        if '起漲' in tag or '多頭' in tag: tags_html += f"<span class='tag-red'>{tag}</span>"
        elif '撤退' in tag or '警報' in tag or '弱於' in tag: tags_html += f"<span class='tag-green'>{tag}</span>"
        elif '抗跌' in tag: tags_html += f"<span class='tag-blue'>{tag}</span>"
        elif '買超' in tag or '齊買' in tag: tags_html += f"<span class='tag-purple'>{tag}</span>"
        else: tags_html += f"<span class='tag-gray'>{tag}</span>"
    port_html = f"<div style='background:#10141d; padding:10px; border-radius:6px; margin-bottom:12px;'><span style='color:#aaa; font-size:13px;'>進場價：<strong style='color:#f1c40f;'>{p_data['entry_price']}</strong> | 數量：{p_data['qty']} 張</span></div>" if is_portfolio and p_data else ""
    
    metric_grid = f"""<div class='metric-grid'>
<div style="width:100%; margin-bottom:6px; display:flex; justify-content:space-between;">
<span>價值分數: <strong style="color:#00d2ff; font-size:15px;">{d['val_score']} 分</strong> <span style="color:#888;">({d['val_shield']} | PE:{d['pe']} PB:{d['pb']} 殖利率:{d['yld']}%)</span></span>
</div>
<div style="width:100%; border-top: 1px dashed #444; margin-bottom:6px; padding-top:6px; display:flex; gap:15px; flex-wrap:wrap;">
<span>開盤: <strong style="color:#fff;">{d['open']:.2f}</strong></span>
<span>最高: <strong style="color:#fff;">{d['high']:.2f}</strong></span>
<span>最低: <strong style="color:#fff;">{d['low']:.2f}</strong></span>
<span>總量: <strong style="color:#f1c40f;">{d['vol']:,} 張</strong></span>
</div>
<div style="width:100%; border-top: 1px dashed #444; margin-bottom:6px;"></div>
<div style="width:100%; display:flex; justify-content:space-between; margin-bottom:4px;">
<span style="flex:1;">短線戰略: <strong style="color:#f1c40f;">{d['st_buy']}</strong> (保險絲: <span style="color:#00FF00;">{d['st_stop']}</span>)</span>
<span style="flex:1;">長線戰略: <strong style="color:#00d2ff;">{d['lt_buy']}</strong> (保險絲: <span style="color:#00FF00;">{d['lt_stop']}</span>)</span>
</div>
<div style="width:100%; border-top: 1px dashed #444; margin-top:4px; margin-bottom:6px;"></div>
<span>攻擊訊號: <strong style="color:#ff4d4d;">{d['start_signals']}</strong></span>
<span>撤退風險: <strong style="color:#00FF00;">{d['retreat_signals']}</strong></span>
<span>KDJ/MACD: <strong style="color:#00d2ff;">{d['kdj_str']} / {d['macd_str']}</strong></span>
<span>爆量比: <strong style="color:#e67e22;">{d['vol_ratio']:.1f}x</strong></span>
</div>"""
    
    summary_class = "tactical-danger" if d['is_action_needed'] else "tactical-summary"
    st.markdown(f"""<div style="border: 2px solid {d['color']}; border-radius: 8px; padding: 15px; background-color: #16191f; margin-bottom: 5px;">
{port_html}
<div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:5px;">
<span style="font-weight:bold; font-size:18px;">{d['name']} ({d['code']})</span>
<span style="color:#888; font-size:12px;">{d['cost_label']}: {d['cost']}</span>
</div>
<div style="font-size:32px; font-weight:bold; margin-bottom: 10px; display:flex; gap:12px;">{d['price']:.2f} <span style="font-size:16px; color:{gain_color}; background-color:{gain_bg}; padding:4px 10px; border-radius:6px;">{d['gain']:+.1f}%</span></div>
<div style="margin-bottom: 10px;">{tags_html}</div>
{metric_grid}
<div style="background:{d['signal_bg']}; padding:10px; border-radius:6px; text-align:center; margin-bottom:10px; border: 1px solid {d['color']}40;"><strong style="color:{d['color']}; font-size:16px;">{d['signal']}</strong></div>
<div class="{summary_class}">{d['tactical_summary']}</div>
</div>""", unsafe_allow_html=True)

# ==========================================
# 側邊欄控制台
# ==========================================
with st.sidebar:
    st.markdown("<h2 style='color:#f1c40f; text-align:center;'>戰略控制台</h2>", unsafe_allow_html=True)
    
    st.markdown("<h4 style='color:#00FF00; margin-top:10px;'>智核火力等級</h4>", unsafe_allow_html=True)
    new_ai_mode = st.radio("選擇 AI 運算模式：", ["快速 (Flash)", "深度 (Pro)"], index=0 if st.session_state.ai_mode == "快速 (Flash)" else 1, label_visibility="collapsed")
    if new_ai_mode != st.session_state.ai_mode:
        st.session_state.ai_mode = new_ai_mode
        check_api_keys.clear()
        st.rerun()
        
    st.markdown("<h4 style='color:#f1c40f; margin-top:10px;'>流動性防禦濾網</h4>", unsafe_allow_html=True)
    min_volume_filter = st.slider("最低 5 日均量 (張)：", min_value=0, max_value=5000, value=500, step=100)
    
    st.markdown("<h4 style='color:#d200ff; margin-top:10px;'>金鑰火力監測</h4>", unsafe_allow_html=True)
    key_statuses = check_api_keys(GEMINI_API_KEYS, st.session_state.ai_mode)
    
    status_html = "<div style='background:#1a1a24; padding:10px; border-radius:5px; border:1px solid #333; margin-bottom:10px;'>"
    for s in key_statuses:
        status_text = "正常" if s['status'] == "OK" else "異常"
        color_class = "key-status-ok" if s['status'] == "OK" else "key-status-fail"
        status_html += f"<div>[{status_text}] Key #{s['index']} ({s['key']}): <span class='{color_class}'>{s['msg']}</span></div>"
    status_html += "</div>"
    st.markdown(status_html, unsafe_allow_html=True)

    st.markdown("<h4 style='color:#00FF00; margin-top:10px;'>FinMind 備援狀態</h4>", unsafe_allow_html=True)
    if st.button("[檢測 FinMind 殘彈量]", use_container_width=True):
        fm_tokens = [t.strip() for t in SECRET_FINMIND.split(',') if t.strip()]
        st.markdown("<div style='background:#1a1a24; padding:10px; border-radius:5px; border:1px solid #333; margin-bottom:10px;'>", unsafe_allow_html=True)
        st.markdown(f"<div>[訪客通道] 免費備援: <span class='key-status-ok'>300 次/小時</span></div>", unsafe_allow_html=True)
        if not fm_tokens:
            st.markdown(f"<div>[金鑰通道] 未配置專屬金鑰</div>", unsafe_allow_html=True)
        else:
            for i, token in enumerate(fm_tokens):
                url = "https://api.web.finmindtrade.com/v2/user_info"
                headers = {"Authorization": f"Bearer {token}"}
                try:
                    res = requests.get(url, headers=headers, timeout=5)
                    if res.status_code == 200:
                        data = res.json()
                        used = data.get("user_count", 0)
                        limit = data.get("api_request_limit", 600)
                        remain = limit - used
                        color_class = "key-status-ok" if remain > 50 else "key-status-fail"
                        st.markdown(f"<div>[金鑰 #{i+1}] 剩餘 <span class='{color_class}'>{remain}</span> 次 (消耗: {used} / 總量: {limit})</div>", unsafe_allow_html=True)
                    else:
                        st.markdown(f"<div>[金鑰 #{i+1}] <span class='key-status-fail'>讀取失敗 ({res.status_code})</span></div>", unsafe_allow_html=True)
                except:
                    st.markdown(f"<div>[金鑰 #{i+1}] <span class='key-status-fail'>連線逾時</span></div>", unsafe_allow_html=True)
        st.markdown("</div>", unsafe_allow_html=True)

    st.markdown("<div style='background:#16191f; padding:10px; border-radius:8px; border: 1px solid #3498db; margin-top:10px; margin-bottom:10px;'><h4 style='color:#3498db; margin-top:0px; font-size:14px;'>情報與搜尋引擎</h4>", unsafe_allow_html=True)
    intel_input = st.text_area("輸入模糊字詞或代號 (例如 '電' 或 '2330'):", placeholder="自動比對並匯入右側情報區...")
    if st.button("[強制解析並匯入]", use_container_width=True):
        if intel_input.strip():
            found_codes = set(re.findall(r'\b\d{4}\b', intel_input))
            for code, name in TW_STOCK_NAMES.items():
                if intel_input in name or intel_input in code:
                    found_codes.add(code)
            zh_words = re.findall(r'[\u4e00-\u9fa5]{2,}', intel_input)
            for word in zh_words:
                for code, name in TW_STOCK_NAMES.items():
                    if word in name: found_codes.add(code)
            if found_codes:
                for c in found_codes:
                    if c not in [x['code'] for x in st.session_state.temp_intel]:
                        st.session_state.temp_intel.append({'code': c})
                st.rerun()
            else: st.error("[系統回報] 查無符合的股票名稱或代號！")
        else: st.warning("[系統回報] 請先輸入文字再按下按鈕！")
    st.markdown("</div>", unsafe_allow_html=True)

    st.markdown("---")
    st.markdown("<h4 style='color:#00d2ff;'>全域掃描指令 (AI 驅動)</h4>", unsafe_allow_html=True)
    scan_scope = st.selectbox("掃描範圍", ["電子/半導體/光電", "全市場 1700+ 檔", "傳產/機電/重電", "航運/觀光百貨", "金融/保險", "生技/醫療"])
    
    def get_scope_codes(scope):
        if "全市場" in scope: return GLOBAL_MARKET_CODES
        elif "電子" in scope: return [c for c in GLOBAL_MARKET_CODES if c.startswith(('23','24','30','31','32','33','34','35','36','49','52','53','54','61','62','64','80','81','82'))]
        elif "傳產" in scope: return [c for c in GLOBAL_MARKET_CODES if c.startswith(('11','12','13','14','15','16','17','18','19','20','21','22','99'))]
        elif "航運" in scope: return [c for c in GLOBAL_MARKET_CODES if c.startswith(('26','27'))]
        elif "金融" in scope: return [c for c in GLOBAL_MARKET_CODES if c.startswith(('28','58'))]
        elif "生技" in scope: return [c for c in GLOBAL_MARKET_CODES if c.startswith(('17','41','47','65'))]
        return GLOBAL_MARKET_CODES

    def run_command_scan(cmd_name, scope, min_vol):
        results = []
        codes = get_scope_codes(scope)
        bar = st.progress(0)
        status = st.empty()
        
        invalid_signals = ["[空頭觀望]", "[高檔觀望]", "[拉回整理]", "[觸發停損]", "[撤退警告]"]
        
        for i, c in enumerate(codes):
            if i % 3 == 0: status.text(f"雷達鎖定與過濾中... ({i}/{len(codes)})")
            d = calculate_signals(c, get_stock_data(c), is_panic_global=is_panic, twii_gain=global_twii_gain, is_scan=True)
            
            if d and d['vol_5d'] >= min_vol and not d['is_action_needed']: 
                if d['signal'] not in invalid_signals:
                    if cmd_name == "指令一" and d['is_first_red']: results.append(d)
                    elif cmd_name == "指令二" and d['is_stealth']: results.append(d)
                    elif cmd_name == "指令三" and d['is_yield']: results.append(d)
                    elif cmd_name == "常規": results.append(d)
                    
            bar.progress(min((i + 1) / len(codes), 1.0))
        bar.empty(); status.empty()
        return results

    st.markdown("<div class='cmd-btn'>", unsafe_allow_html=True)
    if st.button("[指令一] 主升段突擊", help="【狙擊部隊】\n嚴選今日剛發生 KDJ/MACD 金叉或爆量突破的標的。\n只抓發動攻擊的第一根紅K，不追已漲到半山腰的魚身。", use_container_width=True):
        raw_results = run_command_scan("指令一", scan_scope, min_volume_filter)
        st.session_state.ai_report = generate_ai_report("指令一：主升段突擊", raw_results) 
        st.session_state.scan_results = raw_results
        st.session_state.scan_mode = "cmd_1"
        
    if st.button("[指令二] 魚頭潛伏期", help="【伏擊部隊】\n嚴選長線站穩季線，但近期盤整貼近支撐且開始微幅增量的標的。\n抓出主力底部偷偷吃貨、尚未正式發動的「魚頭」。", use_container_width=True):
        raw_results = run_command_scan("指令二", scan_scope, min_volume_filter)
        st.session_state.ai_report = generate_ai_report("指令二：魚頭潛伏期", raw_results)
        st.session_state.scan_results = raw_results
        st.session_state.scan_mode = "cmd_2"
        
    if st.button("[指令三] 季節與循環", help="【防禦部隊】\n嚴選股價在年線之上、靠近季線，且殖利率大於 5% 的標的。\n尋找長線具備高息保護傘的價值低估股。", use_container_width=True):
        raw_results = run_command_scan("指令三", scan_scope, min_volume_filter)
        st.session_state.ai_report = generate_ai_report("指令三：季節與循環", raw_results)
        st.session_state.scan_results = raw_results
        st.session_state.scan_mode = "cmd_3"
    st.markdown("</div>", unsafe_allow_html=True)
    
    st.markdown("---")
    st.markdown("<h4 style='color:#ff4d4d;'>常規雷達掃描</h4>", unsafe_allow_html=True)
    st.markdown("<div class='scan-btn'>", unsafe_allow_html=True)
    if st.button("[常規掃描] 黃金起漲與魚身", help="【網羅全軍】\n過濾掉破線與空頭的股票，保留所有安全的標的。\n包含剛起漲、潛伏中與正在衝鋒的部隊，範圍最廣。", use_container_width=True):
        st.session_state.scan_results = run_command_scan("常規", scan_scope, min_volume_filter) 
        st.session_state.scan_mode = "golden"; st.session_state.ai_report = ""
    st.markdown("</div>", unsafe_allow_html=True)

# ==========================================
# 主戰情室畫面渲染
# ==========================================
col_nav1, col_nav2, col_nav3 = st.columns([5, 1, 1])
with col_nav1: st.markdown("<h1 style='color:#FFB300; margin: 0;'>54088 戰情室 V78.0 (彈藥庫精準偵測版)</h1>", unsafe_allow_html=True)
with col_nav2:
    if st.button("[強制更新 / 抓取最新報價]", use_container_width=True): 
        get_market_weather.clear()
        get_stock_data.clear()
        check_api_keys.clear()
        fetch_fundamentals.clear() 
        fetch_institutional_data.clear()
        get_finmind_and_deep_fundamentals.clear()
        st.session_state.temp_intel = [] 
        st.rerun() 
with col_nav3:
    if st.button("[鎖定系統]", use_container_width=True): st.session_state.authenticated = False; st.rerun()

port_loaded_cards, pin_loaded_cards = {}, {}
for code, p in st.session_state.portfolio.items():
    d = calculate_signals(code, get_stock_data(code), portfolio_data=p, is_panic_global=is_panic, twii_gain=global_twii_gain, is_scan=False)
    if d: port_loaded_cards[code] = d
for code in st.session_state.pinned_stocks:
    d = calculate_signals(code, get_stock_data(code), portfolio_data=None, is_panic_global=is_panic, twii_gain=global_twii_gain, is_scan=False)
    if d: pin_loaded_cards[code] = d

total_unrealized, action_needed, golden_targets = 0, 0, 0
for code, d in port_loaded_cards.items():
    p_profit, _ = calc_real_profit(st.session_state.portfolio[code]['entry_price'], d['price'], st.session_state.portfolio[code]['qty'])
    total_unrealized += p_profit
    if d.get('is_action_needed'): action_needed += 1
for code, d in pin_loaded_cards.items():
    if d.get('is_golden'): golden_targets += 1

st.markdown(f"""
<div class='hud-box'>
<div class='hud-title' style='display:flex; justify-content:space-between;'><span>大將軍戰情總覽 (HUD)</span><span style='color:{weather_color};'>{weather_str}</span></div>
<div style='background:#1a1c23; padding:10px; border-radius:5px; border-left:3px solid {weather_color}; margin-bottom:10px; font-size:14px; color:#ddd;'>
<strong>[今日大盤風向]</strong> {weather_str.split('] ')[0] + ']'}
</div>
<div class='hud-metric'><span style='color:#aaa;'>庫存 / 雷達數量</span> <strong style='color:#fff;'>{len(port_loaded_cards)} / {len(pin_loaded_cards)} 檔</strong></div>
<div class='hud-metric'><span style='color:#aaa;'>總未實現損益</span> <strong style='color:{'#ff4d4d' if total_unrealized>0 else '#00FF00'}; font-size:18px;'>{total_unrealized:+,.0f} 元</strong></div>
<div class='health-bar-bg'><div class='{'health-bar-fill-red' if total_unrealized >= 0 else 'health-bar-fill-green'}' style='width: {max(0, min(100, 50 + (total_unrealized / 50000) * 50))}%;'></div></div>
<div class='hud-metric' style='margin-top:10px; padding-top:10px; border-top:1px dashed #333;'><span style='color:#ff4d4d;'>雷達可狙擊：<strong>{golden_targets} 檔</strong></span><span style='color:#00FF00;'>庫存需撤退：<strong>{action_needed} 檔</strong></span></div>
</div>
""", unsafe_allow_html=True)

if st.session_state.temp_intel:
    col1, col2 = st.columns([8, 2])
    with col1: st.markdown("<h3 style='color:#00d2ff; margin-top:20px; border-bottom: 2px solid #00d2ff; padding-bottom:5px;'>情報觀測區</h3>", unsafe_allow_html=True)
    with col2:
        if st.button("[清除情報區]", use_container_width=True):
            st.session_state.temp_intel = []
            st.rerun()
            
    cols = st.columns(2)
    for i, item in enumerate(list(st.session_state.temp_intel)):
        code = item['code']
        d = calculate_signals(code, get_stock_data(code), portfolio_data=None, is_panic_global=is_panic, twii_gain=global_twii_gain, is_scan=False)
        if d:
            with cols[i % 2]: 
                draw_card(d, f"temp_{code}")
                def add_to_radar(target_code=code):
                    st.session_state.pinned_stocks[target_code] = {}
                    st.session_state.temp_intel = [x for x in st.session_state.temp_intel if x['code'] != target_code]
                    save_db()
                st.button("[移至雷達區]", key=f"pin_temp_{code}", on_click=add_to_radar, use_container_width=True)

if st.session_state.get('ai_report'):
    st.markdown("<h2 style='color:#d200ff; margin-top:20px; border-bottom: 2px solid #d200ff; padding-bottom:5px;'>AI 戰略報告</h2>", unsafe_allow_html=True)
    st.markdown(f"<div class='ai-report-box'>{st.session_state.ai_report}</div>", unsafe_allow_html=True)

if st.session_state.portfolio:
    st.markdown("<h2 style='color:#ff4d4d; margin-top:20px; border-bottom: 2px solid #ff4d4d; padding-bottom:5px;'>總指揮持倉</h2>", unsafe_allow_html=True)
    cols = st.columns(2)
    for i, (code, p_data) in enumerate(list(st.session_state.portfolio.items())):
        d = port_loaded_cards.get(code)
        if d:
            with cols[i % 2]:
                draw_card(d, f"port_{code}", is_portfolio=True, p_data=p_data)
                def sell_stock(target_code=code):
                    del st.session_state.portfolio[target_code]
                    save_db()
                st.button("[賣出平倉]", key=f"sell_{code}", on_click=sell_stock, use_container_width=True)

if st.session_state.pinned_stocks:
    col_r1, col_r2 = st.columns([6, 4])
    with col_r1: 
        st.markdown("<h2 style='color:#f1c40f; margin-top:20px; border-bottom: 2px solid #f1c40f; padding-bottom:5px;'>觀測雷達</h2>", unsafe_allow_html=True)
    with col_r2:
        to_delete = st.multiselect("批次刪除選單：", options=list(st.session_state.pinned_stocks.keys()), format_func=lambda x: f"{x} {TW_STOCK_NAMES.get(x, x)}", placeholder="選擇要移除的標的...")
        if st.button("[執行批次刪除]", use_container_width=True):
            if to_delete:
                for c in to_delete:
                    del st.session_state.pinned_stocks[c]
                save_db()
                st.rerun()
            else:
                st.warning("請先選擇要刪除的標的")
            
    cols = st.columns(2)
    for i, code in enumerate(list(st.session_state.pinned_stocks.keys())):
        d = pin_loaded_cards.get(code)
        if d:
            with cols[i % 2]:
                draw_card(d, f"pin_{code}")
                c1, c2 = st.columns(2)
                def buy_stock(target_code=code, target_price=d['price']):
                    st.session_state.portfolio[target_code] = {'entry_price': target_price, 'qty': 1}
                    del st.session_state.pinned_stocks[target_code]
                    save_db()
                def del_stock(target_code=code):
                    del st.session_state.pinned_stocks[target_code]
                    save_db()
                c1.button("[買進庫存]", key=f"buy_{code}", on_click=buy_stock, use_container_width=True)
                c2.button("[刪除追蹤]", key=f"del_{code}", on_click=del_stock, use_container_width=True)

if scan_mode_current := st.session_state.get('scan_mode', ""):
    st.markdown("<h2 style='color:#00d2ff; margin-top:30px; border-bottom: 2px solid #00d2ff; padding-bottom:5px;'>初篩結果</h2>", unsafe_allow_html=True)
    
    available_tags = ["A. 起漲第一根", "C. 均線多頭", "D. 量縮整理", "E. 逆勢抗跌", "G. 土洋齊買", "H. 投信買超", "I. 外資買超"]
    st.markdown("<div style='margin-bottom: 15px;'>", unsafe_allow_html=True)
    selected_tags = st.multiselect("🎯 動態二次過濾 (可多選，畫面即時篩選，無須重新掃描)：", available_tags, placeholder="選擇您要的戰術標籤進行精準狙擊...")
    st.markdown("</div>", unsafe_allow_html=True)

    if not st.session_state.scan_results:
        st.warning("[系統提示] 掃描完畢，目前無標的符合條件。")
    else:
        filtered_results = []
        for x in st.session_state.scan_results:
            if x['code'] in st.session_state.portfolio or x['code'] in st.session_state.pinned_stocks:
                continue
            
            if selected_tags:
                if not all(tag in x['ai_tags'] for tag in selected_tags):
                    continue
                    
            filtered_results.append(x)

        if not filtered_results:
            st.warning("[系統提示] 套用二次濾網後，無標的符合您的精準條件。請嘗試減少標籤數量。")
        else:
            cols = st.columns(2)
            for i, d in enumerate(filtered_results):
                with cols[i % 2]: 
                    draw_card(d, f"scan_{i}")
                    def add_scan_to_radar(target_code=d['code']):
                        st.session_state.pinned_stocks[target_code] = {}
                        save_db()
                    st.button("[移至雷達區]", key=f"add_scan_{d['code']}", on_click=add_scan_to_radar, use_container_width=True)
