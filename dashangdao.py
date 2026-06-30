import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime
import re
import time
import json
import os
import requests

# ==========================================
# 基礎配置與狀態初始化
# ==========================================
st.set_page_config(layout="wide", page_title="54088 - 戰情室 V50.0", initial_sidebar_state="expanded")

try:
    COMMANDER_PIN = st.secrets["radar_secrets"]["commander_pin"]
    raw_keys = st.secrets["radar_secrets"]["gemini_api_key"]
    GEMINI_API_KEYS = [k.strip() for k in raw_keys.split(",") if k.strip()]
except KeyError:
    st.error("🚨 [致命錯誤] 雲端保險箱 (Secrets) 未設定或設定錯誤！請檢查 Streamlit Cloud 後台設定。")
    st.stop()

USER_DB_FILE = "54088_database.json" 

if 'ai_mode' not in st.session_state: st.session_state.ai_mode = "快速 (Flash)"
if 'scan_results' not in st.session_state: st.session_state.scan_results = []
if 'scan_mode' not in st.session_state: st.session_state.scan_mode = ""
if 'ai_report' not in st.session_state: st.session_state.ai_report = ""
if 'temp_intel' not in st.session_state: st.session_state.temp_intel = []
if 'portfolio' not in st.session_state: st.session_state.portfolio = {}
if 'pinned_stocks' not in st.session_state: st.session_state.pinned_stocks = {}
if 'active_key_index' not in st.session_state: st.session_state.active_key_index = 0
if 'line_token' not in st.session_state: st.session_state.line_token = ""

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

def send_line_notify(message):
    token = st.session_state.get('line_token', '').strip()
    if not token: return
    url = "https://notify-api.line.me/api/notify"
    headers = {"Authorization": f"Bearer {token}"}
    data = {"message": f"\n{message}"}
    try: requests.post(url, headers=headers, data=data, timeout=5)
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
            else: st.error("❌ 密碼錯誤")
    st.stop()

# ==========================================
# 視覺與樣式定義
# ==========================================
st.markdown('''<style>
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
.tactical-summary { background: #000; border-top: 1px dashed #444; margin-top: 10px; padding: 10px; font-size: 14px; color: #f1c40f; font-weight: bold; border-radius: 5px; line-height: 1.5;}
.tactical-danger { background: #153a20; border-top: 1px dashed #2ecc71; margin-top: 10px; padding: 10px; font-size: 15px; color: #00FF00; font-weight: bold; border-radius: 5px; line-height: 1.5;}
.metric-grid { display: flex; gap: 15px; flex-wrap: wrap; font-size: 13px; color: #ccc; margin-bottom: 10px; background: #10141d; padding: 12px; border-radius: 6px; border: 1px solid #333;}
.ai-report-box { background: #1a1a24; border-left: 5px solid #d200ff; padding: 20px; border-radius: 8px; margin-bottom: 20px; border: 1px solid #d200ff40; font-size: 15px; line-height: 1.6; font-family: sans-serif;}
.key-status-ok { color: #00FF00; font-weight: bold; font-size: 13px; word-break: break-all;}
.key-status-fail { color: #ff4d4d; font-weight: bold; font-size: 13px; word-break: break-all;}
</style>''', unsafe_allow_html=True)

# ==========================================
# 資料獲取與演算法模組
# ==========================================
def get_safe_session():
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Cache-Control": "no-cache, no-store, must-revalidate"
    })
    return session

def safe_float(val):
    try:
        s = str(val).replace(',', '').replace('-', '').strip()
        return float(s) if s else 0.0
    except: return 0.0

@st.cache_data(ttl=86400, show_spinner=False)
def fetch_stock_names():
    api_names = {}
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        res = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL", timeout=10, headers=headers)
        if res.status_code == 200:
            for item in res.json():
                c = str(item.get('Code', '')).strip()
                n = str(item.get('Name', '')).strip()
                if len(c) == 4 and c.isdigit() and n: api_names[c] = n
    except: pass
    try:
        res2 = requests.get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_peratio_analysis", timeout=10, headers=headers)
        if res2.status_code == 200:
            for item in res2.json():
                c = str(item.get('SecuritiesCompanyCode', '')).strip()
                n = str(item.get('CompanyName', '')).strip()
                if len(c) == 4 and c.isdigit() and n: api_names[c] = n
    except: pass
    fallbacks = {"2330":"台積電", "2303":"聯電", "2317":"鴻海", "2454":"聯發科", "2382":"廣達", "2603":"長榮", "1519":"華城", "3017":"奇鋐", "3324":"雙鴻", "2313":"華通", "3231":"緯創", "2356":"英業達", "1558":"伸興工業", "2412":"中華電信"}
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

@st.cache_data(ttl=86400, show_spinner=False)
def fetch_fundamentals():
    db = {}
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        res1 = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL", timeout=10, headers=headers)
        if res1.status_code == 200:
            for item in res1.json():
                code = str(item.get('Code', '')).strip()
                if len(code) == 4 and code.isdigit():
                    db[code] = {'PE': safe_float(item.get('PeRatio')), 'PB': safe_float(item.get('PbRatio')), 'Yield': safe_float(item.get('DividendYield'))}
    except: pass
    try:
        res2 = requests.get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_peratio_analysis", timeout=10, headers=headers)
        if res2.status_code == 200:
            for item in res2.json():
                code = str(item.get('SecuritiesCompanyCode', '')).strip()
                if len(code) == 4 and code.isdigit():
                    db[code] = {'PE': safe_float(item.get('PERatio')), 'PB': safe_float(item.get('PBRatio')), 'Yield': safe_float(item.get('DividendYield'))}
    except: pass
    return db

@st.cache_data(ttl=3600, show_spinner=False)
def fetch_institutional_data():
    inst_db = {}
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        res = requests.get("https://openapi.twse.com.tw/v1/fund/T86_ALL", timeout=10, headers=headers)
        if res.status_code == 200:
            for item in res.json():
                code = str(item.get('Code', '')).strip()
                inst_db[code] = {'foreign': int(safe_float(item.get('ForeignDifference'))), 'trust': int(safe_float(item.get('InvestmentTrustDifference')))}
    except: pass
    try:
        res2 = requests.get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_3itrade_hedge", timeout=10, headers=headers)
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

# 🚨 V50.0 智能備援引擎：確保 PE 和 PB 同時被精準補齊
@st.cache_data(ttl=3600, show_spinner=False)
def get_yf_fundamentals(symbol):
    try:
        tk = yf.Ticker(symbol + ".TW")
        info = tk.info
        if 'trailingPE' not in info and 'priceToBook' not in info:
            tk = yf.Ticker(symbol + ".TWO")
            info = tk.info
        pe = safe_float(info.get('trailingPE', 0.0))
        pb = safe_float(info.get('priceToBook', 0.0))
        yld = safe_float(info.get('dividendYield', 0.0)) * 100
        return pe, pb, yld
    except:
        return 0.0, 0.0, 0.0

@st.cache_data(ttl=60, show_spinner=False)
def get_market_weather():
    try:
        session = get_safe_session()
        tk = yf.Ticker("^TWII", session=session)
        twii = tk.history(period="3mo").dropna(subset=['Close'])
        try:
            live_twii = tk.history(period="1d", interval="1m").dropna(subset=['Close'])
            if not live_twii.empty and not twii.empty:
                live_close = float(live_twii['Close'].iloc[-1])
                last_date = twii.index[-1].date()
                live_date = live_twii.index[-1].date()
                if live_date > last_date:
                    new_row = twii.iloc[-1].copy()
                    new_row['Close'] = live_close
                    twii.loc[live_twii.index[-1]] = new_row
                elif live_date == last_date:
                    twii.loc[twii.index[-1], 'Close'] = live_close
        except: pass

        if twii.empty: return "[大盤連線異常]", "#888", False, False, 0.0
        c_idx = float(twii['Close'].iloc[-1])
        prev_idx = float(twii['Close'].iloc[-2])
        twii_gain = ((c_idx - prev_idx) / prev_idx) * 100
        ma20 = float(twii['Close'].rolling(20).mean().iloc[-1])
        is_panic = (twii_gain <= -3.0) or (c_idx < float(twii['Close'].rolling(60).mean().iloc[-1]) * 0.95)
        display_str = f"加權指數: {c_idx:,.0f} 點 ({twii_gain:+.2f}%)"
        if is_panic: return f"[恐慌斷頭潮] ({display_str})", "#00FF00", c_idx > ma20, True, twii_gain
        elif c_idx > ma20: return f"[多頭順風環境] ({display_str})", "#ff4d4d", True, False, twii_gain
        else: return f"[空頭震盪環境] ({display_str})", "#f1c40f", False, False, twii_gain
    except: return "[大盤資料獲取中...]", "#888", False, False, 0.0

weather_str, weather_color, is_bull_market, is_panic, global_twii_gain = get_market_weather()

@st.cache_data(ttl=60, show_spinner=False)
def get_stock_data(symbol):
    session = get_safe_session()
    for ext in [".TW", ".TWO"]:
        try:
            tk = yf.Ticker(symbol + ext, session=session)
            hist = tk.history(period="1y").dropna(subset=['
