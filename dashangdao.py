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

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
warnings.filterwarnings('ignore')

GOV_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*"
}

# ==========================================
# 1. 基礎配置與全域金鑰
# ==========================================
st.set_page_config(layout="wide", page_title="54088 戰情室 V129.34", initial_sidebar_state="expanded")
st.toast("✅ [系統提示] V129.34 陣型校準與情報獨立版 啟動成功！")

EVENT_CALENDAR = {"2330": "⚠️ 7/16 法說會 (留意先進封裝指引)"}
USER_DB_FILE = "54088_database.json" 
FUNDAMENTALS_DB_FILE = "54088_fundamentals_cache.json"
INST_HISTORY_FILE = "54088_inst_history_v2.json"

try:
    COMMANDER_PIN = st.secrets["radar_secrets"]["commander_pin"]
    raw_keys = st.secrets["radar_secrets"]["gemini_api_key"]
    GEMINI_API_KEYS = [k.strip() for k in raw_keys.split(",") if k.strip()]
    SECRET_FINMIND = st.secrets["radar_secrets"].get("finmind_token", "")
    FINMIND_TOKENS = [k.strip() for k in SECRET_FINMIND.split(",") if k.strip()]
    if not FINMIND_TOKENS: FINMIND_TOKENS = [None]
except KeyError:
    st.error("❌ [致命錯誤] 雲端保險箱 (Secrets) 未設定！請檢查 Streamlit Cloud。")
    st.stop()

# ==========================================
# 2. 會話狀態 (Session State) 與登入防護
# ==========================================
if 'authenticated' not in st.session_state: st.session_state.authenticated = (st.query_params.get("auth") == "54088")
if 'ai_mode' not in st.session_state: st.session_state.ai_mode = "⚡ 快速 (Flash)"
if 'scan_results' not in st.session_state: st.session_state.scan_results = []
if 'scan_mode' not in st.session_state: st.session_state.scan_mode = ""
if 'ai_report' not in st.session_state: st.session_state.ai_report = ""
if 'portfolio' not in st.session_state: st.session_state.portfolio = {}
if 'pinned_stocks' not in st.session_state: st.session_state.pinned_stocks = {}
if 'active_key_index' not in st.session_state: st.session_state.active_key_index = 0
if 'inst_history' not in st.session_state: st.session_state.inst_history = {} 

now_month = datetime.now().month
if now_month in [1, 2, 3]: quarter_info = "🌱 第一季 (Q1作帳) | 佈局窗：2月中旬至3月初 | 撤退線：3月底前最後四天"
elif now_month in [4, 5, 6]: quarter_info = "☀️ 第二季 (Q2作帳) | 佈局窗：5月中旬至6月初 | 撤退線：6月底前最後四天"
elif now_month in [7, 8, 9]: quarter_info = "🍁 第三季 (Q3作帳) | 佈局窗：8月中旬至9月初 | 撤退線：9月底前最後四天"
else: quarter_info = "❄️ 第四季 (Q4作帳) | 佈局窗：11月中旬至12月初 | 撤退線：12月底前最後四天"

if not st.session_state.authenticated:
    st.markdown("<h2 style='text-align: center; color: #444; margin-top: 10vh; letter-spacing: 5px;'>🔒 SYSTEM LOCKED</h2>", unsafe_allow_html=True)
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        pwd = st.text_input("輸入授權密碼", type="password")
        if st.button("解鎖系統 🔓", use_container_width=True):
            if pwd == COMMANDER_PIN: 
                st.session_state.authenticated = True
                st.query_params["auth"] = "54088"
                st.rerun()
            else: st.error("❌ 密碼錯誤")
    st.stop()

# ==========================================
# 3. 基礎工具函數
# ==========================================
def get_safe_session():
    session = requests.Session()
    session.headers.update(GOV_HEADERS)
    return session

def safe_float(val):
    if pd.isna(val) or val is None or str(val).strip() == '': return 0.0
    try:
        s = str(val).upper().replace(',', '').replace('-', '').strip()
        s = re.sub(r'[^\d.]', '', s)
        return float(s) if s else 0.0
    except Exception: return 0.0

def calc_real_profit(cost, price, qty):
    if cost <= 0: return 0, 0, 0, 0, 0
    buy_val = cost * qty * 1000
    sell_val = price * qty * 1000
    fee_buy = max(20, int(buy_val * 0.001425))
    fee_sell = max(20, int(sell_val * 0.001425))
    tax_sell = int(sell_val * 0.003)
    profit = sell_val - buy_val - fee_buy - fee_sell - tax_sell
    return profit, (profit/buy_val)*100 if buy_val > 0 else 0, fee_buy, fee_sell, tax_sell

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
    elif c.startswith(('23', '24', '30', '31', '35', '80')): return "電子半導體"
    elif c.startswith('25'): return "建材營造"
    elif c.startswith('26'): return "航運業"
    elif c.startswith('27'): return "觀光餐旅"
    elif c.startswith(('28', '58')): return "金融保險"
    elif c.startswith('29'): return "貿易百貨"
    return "綜合類股"

def generate_sparkline(prices):
    if not prices or len(prices) < 2: return ""
    bars = " ▂▃▄▅▆▇█"
    min_p, max_p = min(prices), max(prices)
    if max_p == min_p: return "▃" * len(prices)
    sparkline = ""
    for p in prices:
        idx = int((p - min_p) / (max_p - min_p + 1e-9) * 7)
        idx = max(0, min(7, idx))
        sparkline += bars[idx]
    return sparkline

# ==========================================
# 4. API 與全域資料庫抓取函數
# ==========================================
@st.cache_data(ttl=86400, show_spinner=False)
def fetch_tw_revenue():
    rev_db = {}
    try:
        res1 = requests.get("https://openapi.twse.com.tw/v1/opendata/t187ap05_L", headers=GOV_HEADERS, verify=False, timeout=15)
        if res1.status_code == 200:
            for item in res1.json():
                c = str(item.get('公司代號', '')).strip()
                g = safe_float(item.get('當月營收較去年當月增減百分比', 0))
                if len(c) == 4: rev_db[c] = g
    except: pass
    try:
        res2 = requests.get("https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap05_O", headers=GOV_HEADERS, verify=False, timeout=15)
        if res2.status_code == 200:
            for item in res2.json():
                c = str(item.get('公司代號', '')).strip()
                g = safe_float(item.get('當月營收較去年當月增減百分比', 0))
                if len(c) == 4: rev_db[c] = g
    except: pass
    return rev_db

@st.cache_data(ttl=86400, show_spinner=False)
def fetch_stock_names():
    api_names = {}
    try:
        res = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL", headers=GOV_HEADERS, verify=False, timeout=15)
        if res.status_code == 200:
            for item in res.json():
                c = str(item.get('Code', '')).strip()
                n = str(item.get('Name', '')).strip()
                if len(c) == 4 and c.isdigit() and n: api_names[c] = n
    except: pass
    try:
        res2 = requests.get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_peratio_analysis", headers=GOV_HEADERS, verify=False, timeout=15)
        if res2.status_code == 200:
            for item in res2.json():
                c = str(item.get('SecuritiesCompanyCode', '')).strip()
                n = str(item.get('CompanyName', '')).strip()
                if len(c) == 4 and c.isdigit() and n: api_names[c] = n
    except: pass
    fallbacks = {"2330":"台積電", "2303":"聯電", "2317":"鴻海", "8182":"加高", "1519":"華城", "1227":"佳格", "1101":"台泥"}
    for k, v in fallbacks.items():
        if k not in api_names: api_names[k] = v
    return api_names

@st.cache_data(ttl=3600, show_spinner=False)
def fetch_margin_data():
    margin_db = {}
    try:
        res = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/MI_MARGN_ALL", headers=GOV_HEADERS, verify=False, timeout=15)
        if res.status_code == 200:
            for item in res.json():
                code = str(item.get('Code', '')).strip()
                tb = safe_float(item.get('MarginPurchaseTodayBalance', 0))
                yb = safe_float(item.get('MarginPurchaseYesterdayBalance', 0))
                margin_db[code] = tb - yb
    except: pass
    try:
        res2 = requests.get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_margin_trading", headers=GOV_HEADERS, verify=False, timeout=15)
        if res2.status_code == 200:
            for item in res2.json():
                code = str(item.get('SecuritiesCompanyCode', '')).strip()
                tb = safe_float(item.get('MarginPurchaseCurrentBalance', 0))
                yb = safe_float(item.get('MarginPurchasePreviousBalance', 0))
                margin_db[code] = tb - yb
    except: pass
    return margin_db

def load_local_fundamentals():
    if os.path.exists(FUNDAMENTALS_DB_FILE):
        try:
            with open(FUNDAMENTALS_DB_FILE, "r", encoding="utf-8") as f: return json.load(f)
        except: return {}
    return {}

def save_local_fundamentals(db):
    if len(db) > 500:
        try:
            with open(FUNDAMENTALS_DB_FILE, "w", encoding="utf-8") as f: json.dump(db, f, ensure_ascii=False)
        except: pass

@st.cache_data(ttl=3600, show_spinner=False)
def fetch_fundamentals():
    db = load_local_fundamentals() 
    new_db = {}
    try:
        res1 = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL", headers=GOV_HEADERS, verify=False, timeout=15)
        if res1.status_code == 200:
            for item in res1.json():
                code = str(item.get('Code', '')).strip()
                if len(code) == 4 and code.isdigit():
                    new_db[code] = {'PE': safe_float(item.get('PeRatio')), 'PB': safe_float(item.get('PbRatio')), 'Yield': safe_float(item.get('DividendYield'))}
    except: pass
    try:
        res2 = requests.get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_peratio_analysis", headers=GOV_HEADERS, verify=False, timeout=15)
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
def get_finmind_and_deep_fundamentals(symbol, token_string, curr_price):
    pe = pb = yld = roe = margin = rev_growth = 0.0
    earnings_date_str = "未知"
    session = get_safe_session()
    for ext in [".TW", ".TWO"]:
        try:
            url = f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/{symbol}{ext}?modules=summaryDetail,defaultKeyStatistics,financialData,calendarEvents"
            res = session.get(url, timeout=5)
            if res.status_code == 200:
                data = res.json().get('quoteSummary', {}).get('result', [])
                if data:
                    summary = data[0].get('summaryDetail', {})
                    stats = data[0].get('defaultKeyStatistics', {})
                    cal = data[0].get('calendarEvents', {})
                    def _ext(d, k):
                        val = d.get(k, {})
                        return float(val.get('raw', 0.0)) if isinstance(val, dict) else 0.0
                    pe = _ext(summary, 'trailingPE') or _ext(stats, 'forwardPE')
                    pb = _ext(stats, 'priceToBook') or _ext(summary, 'priceToBook')
                    yld = _ext(summary, 'dividendYield') or _ext(summary, 'trailingAnnualDividendYield')
                    yld = yld * 100 if yld > 0 else 0.0
                    if cal and 'earnings' in cal:
                        edates = cal['earnings'].get('earningsDate', [])
                        if edates and isinstance(edates, list) and 'raw' in edates[0]:
                            earnings_date_str = datetime.fromtimestamp(edates[0]['raw']).strftime('%Y-%m-%d')
                    if abs(pe - curr_price) < 0.1: pe = 0.0
                    if abs(pb - curr_price) < 0.1: pb = 0.0
                    if pe > 0 or pb > 0: return pe, pb, yld, roe, margin, rev_growth, earnings_date_str
        except Exception: pass
    
    url = "https://api.finmindtrade.com/api/v4/data"
    date_str = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
    tokens = [t.strip() for t in token_string.split(',') if t.strip()]
    auth_methods = [None] + tokens
    for auth in auth_methods:
        params = {"dataset": "TaiwanStockPER", "data_id": symbol, "start_date": date_str}
        if auth: params["token"] = auth
        try:
            res = requests.get(url, params=params, timeout=5)
            if res.status_code == 200:
                data = res.json()
                if data.get('msg') == 'success' and data.get('data'):
                    latest = data['data'][-1]
                    pe = safe_float(latest.get('PER', 0))
                    pb = safe_float(latest.get('PBR', 0))
                    yld = safe_float(latest.get('dividend_yield', 0))
                    if pe > 0 or pb > 0: return pe, pb, yld, 0.0, 0.0, 0.0, earnings_date_str
        except Exception: pass
    return 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, earnings_date_str

@st.cache_data(ttl=3600, show_spinner=False)
def fetch_recent_chips_rescue(symbol, token_string=""):
    f_cb = t_cb = f_cs = t_cs = 0
    f_vb = t_vb = f_vs = t_vs = 0.0
    f_latest = t_latest = 0
    finmind_success = False
    
    start_date = (datetime.now() - timedelta(days=20)).strftime('%Y-%m-%d')
    url = 'https://api.finmindtrade.com/api/v4/data'
    params = {'dataset': 'TaiwanStockInstitutionalInvestorsBuySell', 'data_id': symbol, 'start_date': start_date}
    if token_string: params['token'] = token_string.split(',')[0].strip()
    try:
        res = requests.get(url, params=params, timeout=5)
        if res.status_code == 200 and res.json().get('msg') == 'success':
            finmind_success = True
            df = pd.DataFrame(res.json().get('data', []))
            if not df.empty:
                df['net'] = pd.to_numeric(df['buy'], errors='coerce').fillna(0) - pd.to_numeric(df['sell'], errors='coerce').fillna(0)
                pivoted = df.pivot_table(index='date', columns='name', values='net', aggfunc='sum').sort_index(ascending=False)
                f_series = pd.Series(dtype=float)
                if 'Foreign_Investor' in pivoted.columns: f_series = pivoted['Foreign_Investor'].fillna(0)
                else:
                    f_cols = [c for c in pivoted.columns if 'Foreign' in c or '外資' in c]
                    if f_cols: f_series = pivoted[[c for c in f_cols if 'dealer' not in c.lower() and '自營' not in c]].sum(axis=1)

                t_series = pd.Series(dtype=float)
                if 'Investment_Trust' in pivoted.columns: t_series = pivoted['Investment_Trust'].fillna(0)
                else:
                    t_cols = [c for c in pivoted.columns if 'Trust' in c or '投信' in c]
                    if t_cols: t_series = pivoted[t_cols].sum(axis=1)
                
                if not f_series.empty:
                    f_latest = int(f_series.iloc[0] / 1000)
                    for val in f_series:
                        if val > 0 and f_cs == 0: f_cb += 1; f_vb += val / 1000
                        elif val < 0 and f_cb == 0: f_cs += 1; f_vs += val / 1000
                        else: break
                if not t_series.empty:
                    t_latest = int(t_series.iloc[0] / 1000)
                    for val in t_series:
                        if val > 0 and t_cs == 0: t_cb += 1; t_vb += val / 1000
                        elif val < 0 and t_cb == 0: t_cs += 1; t_vs += val / 1000
                        else: break
    except Exception: pass
    return f_cb, t_cb, f_cs, t_cs, f_latest, t_latest, int(f_vb), int(t_vb), int(f_vs), int(t_vs), finmind_success

@st.cache_data(ttl=15, show_spinner=False)
def get_market_weather():
    try:
        session = get_safe_session()
        tk_twii = yf.Ticker("^TWII", session=session)
        twii = tk_twii.history(period="3mo").dropna(subset=['Close'])
        tk_twoii = yf.Ticker("^TWOII", session=session)
        twoii = tk_twoii.history(period="1mo").dropna(subset=['Close'])
        try:
            live_twii = tk_twii.history(period="1d", interval="1m").dropna(subset=['Close'])
            if not live_twii.empty and not twii.empty: twii.loc[twii.index[-1], 'Close'] = float(live_twii['Close'].iloc[-1])
            live_twoii = tk_twoii.history(period="1d", interval="1m").dropna(subset=['Close'])
            if not live_twoii.empty and not twoii.empty: twoii.loc[twoii.index[-1], 'Close'] = float(live_twoii['Close'].iloc[-1])
        except Exception: pass

        if twii.empty: return "⚠️ [大盤連線異常]", "#888", False, False, 0.0
        
        c_idx = float(twii['Close'].iloc[-1])
        prev_idx = float(twii['Close'].iloc[-2])
        twii_pt = c_idx - prev_idx
        twii_gain = (twii_pt / prev_idx) * 100 if prev_idx > 0 else 0.0
        
        ma20 = float(twii['Close'].rolling(20).mean().iloc[-1])
        
        two_gain = two_pt = two_curr = 0.0
        if len(twoii) >= 2:
            two_curr = float(twoii['Close'].iloc[-1])
            two_prev = float(twoii['Close'].iloc[-2])
            two_pt = two_curr - two_prev
            two_gain = (two_pt / two_prev) * 100 if two_prev > 0 else 0.0

        is_panic = (twii_gain <= -3.0) or (c_idx < ma20 * 0.95)
        weather_prefix = f"[{'💀 恐慌斷頭潮' if is_panic else ('📈 多頭順風環境' if c_idx > ma20 else '📉 空頭震盪環境')}]"
        
        twii_color_tag = "#ff4d4d" if twii_pt >= 0 else "#00FF00"
        two_color_tag = "#ff4d4d" if two_pt >= 0 else "#00FF00"
        twii_sign = "+" if twii_pt >= 0 else ""
        two_sign = "+" if two_pt >= 0 else ""
        
        display_str = f"上市: <span style='color:{twii_color_tag}; font-weight:bold;'>{c_idx:,.0f} ({twii_sign}{twii_pt:,.0f}點 | {twii_sign}{twii_gain:.2f}%)</span> | 上櫃: <span style='color:{two_color_tag}; font-weight:bold;'>{two_curr:,.2f} ({two_sign}{two_pt:,.2f}點 | {two_sign}{two_gain:.2f}%)</span>"
        weather_color = "#00FF00" if is_panic else ("#ff4d4d" if c_idx > ma20 else "#f1c40f")
        weather_str = f"<span style='color:{weather_color};'>{weather_prefix}</span> {display_str}"
        return weather_str, weather_color, c_idx > ma20, is_panic, twii_gain
    except Exception: return "⏳ [大盤資料獲取中...]", "#888", False, False, 0.0

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
            except Exception: pass
            if not hist.empty and len(hist) > 26: return hist, 0.0, 0.0, 0.0
        except Exception: pass
    return None

@st.cache_data(ttl=300, show_spinner=False)
def check_api_keys(keys, mode):
    status = []
    for i, k in enumerate(keys):
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models?key={k}"
            res = requests.get(url, timeout=5)
            working_model = "gemini-1.5-flash"
            if res.status_code == 200:
                models = res.json().get('models', [])
                valid_models = [m.get('name', '').replace('models/', '') for m in models if 'generateContent' in m.get('supportedGenerationMethods', [])]
                target = "flash" if "快速" in mode else "pro"
                for m_name in valid_models:
                    if target in m_name.lower(): working_model = m_name; break
            ping_url = f"https://generativelanguage.googleapis.com/v1beta/models/{working_model}:generateContent?key={k}"
            res2 = requests.post(ping_url, headers={'Content-Type': 'application/json'}, json={"contents": [{"parts": [{"text": "ping"}]}]}, timeout=10)
            if res2.status_code == 200: status.append({"index": i, "key": f"...{k[-4:]}", "status": "OK", "msg": f"✅ {working_model}", "model": working_model})
            else: status.append({"index": i, "key": f"...{k[-4:]}", "status": "FAIL", "msg": "❌ 限額耗盡", "model": working_model})
        except: status.append({"index": i, "key": f"...{k[-4:]}", "status": "FAIL", "msg": "❌ 連線失敗", "model": "gemini-1.5-flash"})
    return status

@st.cache_data(ttl=60, show_spinner=False)
def check_finmind_api_status(tokens_list):
    res = []
    if not tokens_list or tokens_list == [None] or tokens_list == [""]: 
        return [{"key": "無", "status": "WARN", "msg": "⚠️ 未設定 (免費用戶限制 300次/時)"}]
    for i, k in enumerate(tokens_list):
        if not k: continue
        masked = f"{k[:4]}...{k[-4:]}" if len(k) > 8 else "***"
        url = "https://api.finmindtrade.com/api/v4/data"
        params = {"dataset": "TaiwanStockPER", "data_id": "2330", "start_date": (datetime.now() - timedelta(days=5)).strftime('%Y-%m-%d'), "token": k}
        try:
            req = requests.get(url, params=params, timeout=5)
            if req.status_code == 200:
                data = req.json()
                if data.get("msg") == "success": res.append({"key": masked, "status": "OK", "msg": "✅ 已連線 (金鑰有效)"})
                else: res.append({"key": masked, "status": "FAIL", "msg": f"❌ {data.get('msg')}"})
            else: res.append({"key": masked, "status": "FAIL", "msg": f"❌ 連線異常 ({req.status_code})"})
        except: res.append({"key": masked, "status": "FAIL", "msg": "❌ 連線超時或失敗"})
    return res

@st.cache_data(ttl=1800, show_spinner=False)
def fetch_stockhomes_gooaye_intelligence():
    intel_db = {}
    latest_detected_ep = 676 
    session = get_safe_session()
    
    for extra_ep in range(latest_detected_ep, latest_detected_ep + 5):
        test_url = f"https://stockhomes.org/gooaye-ep{extra_ep}.html"
        try:
            t_res = session.get(test_url, timeout=3)
            if t_res.status_code == 200: latest_detected_ep = extra_ep 
        except: break

    for ep in range(latest_detected_ep, latest_detected_ep - 5, -1):
        target_ep_url = f"https://stockhomes.org/gooaye-ep{ep}.html"
        try:
            res = session.get(target_ep_url, timeout=5)
            if res.status_code == 200:
                html_content = res.text
                for code, name in TW_STOCK_NAMES.items():
                    if len(name) >= 2 and name in html_content:
                        if code not in intel_db: intel_db[code] = []
                        intel_db[code].append(ep)
        except: pass
        
    return intel_db, latest_detected_ep

# ==========================================
# 5. 全域變數強制載入
# ==========================================
TW_REVENUE_DB = fetch_tw_revenue()
TW_STOCK_NAMES = fetch_stock_names()
MARGIN_DB = fetch_margin_data()
FUNDAMENTAL_DB = fetch_fundamentals()
GLOBAL_MARKET_CODES = list(TW_STOCK_NAMES.keys())
weather_str, weather_color, is_bull_market, is_panic, global_twii_gain = get_market_weather()
GOOAYE_INTEL_DB, LATEST_EPISODE = fetch_stockhomes_gooaye_intelligence()

# ==========================================
# 6. 本地大腦
# ==========================================
def load_local_db():
    if os.path.exists(USER_DB_FILE):
        try:
            with open(USER_DB_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if "pinned_stocks" in data: st.session_state.pinned_stocks = data["pinned_stocks"]
                if "portfolio" in data: st.session_state.portfolio = data["portfolio"]
        except Exception: pass
    if os.path.exists(INST_HISTORY_FILE):
        try:
            with open(INST_HISTORY_FILE, "r", encoding="utf-8") as f:
                st.session_state.inst_history = json.load(f)
        except Exception: pass

if 'db_loaded' not in st.session_state:
    load_local_db()
    st.session_state.db_loaded = True

def save_local_db():
    payload = { "pinned_stocks": st.session_state.pinned_stocks, "portfolio": st.session_state.portfolio }
    try:
        with open(USER_DB_FILE, "w", encoding="utf-8") as f: json.dump(payload, f, ensure_ascii=False, indent=4)
        if st.session_state.inst_history:
            with open(INST_HISTORY_FILE, "w", encoding="utf-8") as f: json.dump(st.session_state.inst_history, f, ensure_ascii=False)
    except Exception: pass

def get_finmind_target_date():
    start_date = (datetime.now() - timedelta(days=5)).strftime('%Y-%m-%d')
    url = 'https://api.finmindtrade.com/api/v4/data'
    params = {'dataset': 'TaiwanStockInstitutionalInvestorsBuySell', 'data_id': '2330', 'start_date': start_date}
    if FINMIND_TOKENS and FINMIND_TOKENS[0]: params['token'] = FINMIND_TOKENS[0]
    try:
        res = requests.get(url, params=params, timeout=5)
        if res.status_code == 200:
            data = res.json()
            if data.get('msg') == 'success' and data.get('data'): return data['data'][-1]['date']
    except: pass
    now = datetime.now()
    if now.hour > 21 or (now.hour == 21 and now.minute >= 30): return now.strftime('%Y-%m-%d')
    return (now - timedelta(days=1)).strftime('%Y-%m-%d')

# ==========================================
# 7. 核心運算引擎 
# ==========================================
def calculate_signals(symbol, data_tuple, portfolio_data=None, is_panic_global=False, twii_gain=0.0, is_scan=False):
    INTERNAL_SECTORS_DB = {
        "華新集團": ["1605", "2492", "2344", "6116", "5469", "6191", "2408", "5305"],
        "國巨集團": ["2327", "5339", "6271", "6422", "8043", "2456"],
        "鴻海集團": ["2317", "2354", "2328", "3413", "6414", "4958", "3149", "2314", "6451", "5243"],
        "聯電集團": ["2303", "2337", "3035", "3037", "2458", "3227", "3014", "8054"],
        "台積電集團": ["2330", "5347", "3443", "6789", "3374"],
        "AI與伺服器": ["2382", "3231", "2356", "2376", "2317", "6669", "3017", "3324", "2421", "3483"],
        "重電綠能": ["1519", "1513", "1514", "1503", "1609", "6806"],
        "半導體設備": ["3131", "3583", "3680", "6187", "6196", "6640", "3413"]
    }

    if data_tuple is None or len(data_tuple) != 4: return None
    hist_df, _, _, _ = data_tuple
    if hist_df is None or hist_df.empty or len(hist_df) < 26: return None
    
    stock_name = TW_STOCK_NAMES.get(symbol, symbol)
    curr = float(hist_df['Close'].iloc[-1])
    recent_closes = hist_df['Close'].tail(7).tolist()
    sparkline_str = generate_sparkline(recent_closes)

    fund_info = FUNDAMENTAL_DB.get(symbol, {})
    pe = fund_info.get('PE', 0.0)
    pb = fund_info.get('PB', 0.0)
    yld = fund_info.get('Yield', 0.0)
    roe = margin = rev_growth_yahoo = 0.0
    earnings_date = "未知"

    if not is_scan:
        pe_api, pb_api, yld_api, roe, margin, rev_growth_yahoo, earnings_date = get_finmind_and_deep_fundamentals(symbol, SECRET_FINMIND, curr)
        if pe == 0.0: pe = pe_api
        if pb == 0.0: pb = pb_api
        if yld == 0.0: yld = yld_api

    rev_growth = TW_REVENUE_DB.get(symbol, None)
    if rev_growth is None: rev_growth = rev_growth_yahoo if abs(rev_growth_yahoo) > 0.01 else None

    score = 50
    if 0 < pe < 15: score += 20
    elif pe > 25: score -= 15
    if 0 < pb < 1.5: score += 20
    elif pb > 3.0: score -= 15
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
    margin_diff = MARGIN_DB.get(symbol, 0.0)
    
    f_cb = t_cb = f_cs = t_cs = 0
    f_vb = t_vb = f_vs = t_vs = 0
    fm_status = ""
    
    if not is_scan:
        f_cb, t_cb, f_cs, t_cs, f_latest, t_latest, f_vb, t_vb, f_vs, t_vs, finmind_success = fetch_recent_chips_rescue(symbol, SECRET_FINMIND)
        display_f = f_latest
        display_t = t_latest
        fm_status = "<span style='color:#00FF00;'>[🟢 FinMind連線正常]</span>" if finmind_success else "<span style='color:#ff4d4d;'>[🔴 FinMind限速/無資料]</span>"
    else:
        display_f = display_t = 0
        fm_status = "<span style='color:#f1c40f;'>[🟡 掃描模式(無動態籌碼)]</span>"
        if st.session_state.inst_history:
            sorted_dates = sorted(st.session_state.inst_history.keys(), reverse=True)
            f_b_broken = f_s_broken = t_b_broken = t_s_broken = False
            for d in sorted_dates:
                d_f = st.session_state.inst_history[d].get(symbol, {}).get('foreign', 0)
                d_t = st.session_state.inst_history[d].get(symbol, {}).get('trust', 0)
                if d == sorted_dates[0]:
                    display_f = d_f
                    display_t = d_t
                if d_f > 0 and not f_b_broken: f_cb+=1; f_vb+=d_f; f_s_broken=True
                elif d_f < 0 and not f_s_broken: f_cs+=1; f_vs+=d_f; f_b_broken=True
                else: f_b_broken = f_s_broken = True
                if d_t > 0 and not t_b_broken: t_cb+=1; t_vb+=d_t; t_s_broken=True
                elif d_t < 0 and not t_s_broken: t_cs+=1; t_vs+=d_t; t_b_broken=True
                else: t_b_broken = t_s_broken = True

    inst_net = display_f + display_t
    retail_net = margin_diff
    
    if f_cs >= 3 and t_cs >= 3: chip_battle_str = f"💀 <strong style='color:#ff4d4d;'>[連續集中賣壓]</strong> 內外資巨頭同步大逃殺，外資連賣 {f_cs} 天、投信連賣 {t_cs} 天，籌碼極度渙散，切勿進場接刀！"
    elif f_cs >= 3 and t_cb > 0: chip_battle_str = f"⚔️ <strong style='color:#f1c40f;'>[土洋激烈對戰]</strong> 外資已連續倒貨 {f_cs} 天 (共 {abs(f_vs):,} 張)，投信短線進場護盤低接，籌碼進入拉鋸戰！"
    elif f_cb >= 3 and t_cs > 0: chip_battle_str = f"⚔️ <strong style='color:#f1c40f;'>[土洋激烈對戰]</strong> 外資連 {f_cb} 日重倉點火 (共 {f_vb:,} 張)，投信逢高獲利了結，留意籌碼換手狀況！"
    elif f_cb >= 3 and t_cb >= 3 and retail_net < 0: chip_battle_str = f"🚀 <strong style='color:#00FF00;'>[籌碼大換手]</strong> 散戶恐慌殺出(融資減少)，法人全面接管籌碼(外資連{f_cb}買、投信連{t_cb}買)，準備發動！"
    elif f_cb >= 3 and t_cb >= 3: chip_battle_str = f"🔥 <strong style='color:#00FF00;'>[巨頭強勢霸盤]</strong> 內外資同步重倉點火！外資狂囤 {f_vb:,} 張，投信狂囤 {t_vb:,} 張！"
    else:
        if inst_net > 0 and retail_net < 0: chip_battle_str = f"✅ <strong style='color:#00FF00;'>[籌碼集中]</strong> 大戶吃貨，散戶退場！"
        elif inst_net < 0 and retail_net > 0: chip_battle_str = f"💀 <strong style='color:#ff4d4d;'>[危險陷阱]</strong> 大戶倒貨，散戶接刀！"
        elif inst_net > 0 and retail_net > 0: chip_battle_str = f"⚠️ <strong style='color:#f1c40f;'>[籌碼發散]</strong> 土洋散戶齊買，留意追高風險。"
        elif inst_net < 0 and retail_net < 0: chip_battle_str = f"📉 <strong style='color:#00FF00;'>[全面潰散]</strong> 大戶散戶多殺多，建議避開。"
        else: chip_battle_str = f"⚖️ <span style='color:#ccc;'>[籌碼觀望] 尚未出現明顯對抗特徵。</span>"

    ma5 = hist_df['Close'].rolling(5).mean().iloc[-1]
    ma20 = hist_df['Close'].rolling(20).mean().iloc[-1]
    ma60 = hist_df['Close'].rolling(60).mean().iloc[-1] if len(hist_df) >= 60 else ma20

    is_crash_alert = (gain <= -3.0) or (curr < ma5)
    is_whipsaw = (high_p > ma5) and (curr < open_p) and (gain < -1.0)
    is_ma_bullish = (curr > ma5) and (ma5 > ma20) and (ma20 > ma60)

    low_min = hist_df['Low'].rolling(9).min()
    high_max = hist_df['High'].rolling(9).max()
    rsv = (hist_df['Close'] - low_min) / (high_max - low_min + 1e-9) * 100
    calc_k = rsv.bfill().ffill().fillna(50).ewm(com=2, adjust=False).mean()
    calc_d = calc_k.bfill().ffill().fillna(50).ewm(com=2, adjust=False).mean()
    k = calc_k.iloc[-1] if not pd.isna(calc_k.iloc[-1]) else 50
    d_val = calc_d.iloc[-1] if not pd.isna(calc_d.iloc[-1]) else 50
    kdj_str = "金叉" if (k < 50 and calc_k.iloc[-2] <= calc_d.iloc[-2] and k > d_val) else ("向上" if k > d_val else "向下")

    exp1 = hist_df['Close'].ewm(span=12, adjust=False).mean()
    exp2 = hist_df['Close'].ewm(span=26, adjust=False).mean()
    macd_hist = (exp1 - exp2) - (exp1 - exp2).ewm(span=9, adjust=False).mean()
    macd_val = macd_hist.iloc[-1] if not macd_hist.empty and not pd.isna(macd_hist.iloc[-1]) else 0.0
    macd_str = "📈 多方動能增強 (紅柱)" if macd_val > 0 else "📉 空方動能增強 (綠柱)"
    macd_color = "#ff4d4d" if macd_val > 0 else "#00FF00"

    is_fake_breakout = (vol_ratio >= 2.0) and ((high_p - max(open_p, curr) > abs(curr - open_p) * 1.5) and (high_p > ma5))
    is_first_red_trigger = (gain > 0) and (curr > open_p) and (curr > ma5) and (prev < ma5)

    is_yesterday_strong = False
    if len(hist_df) > 2:
        prev_prev = float(hist_df['Close'].iloc[-3])
        if prev_prev > 0: is_yesterday_strong = ((prev - prev_prev) / prev_prev) * 100 > 5.0

    is_golden_start = is_first_red_trigger and (vol_ratio >= 2.0 and gain >= 2.0) and (kdj_str == "金叉")

    ai_tags_dict = []
    event_tag = EVENT_CALENDAR.get(symbol, "")
    if event_tag: ai_tags_dict.append({"text": event_tag, "class": "tag-purple", "title": "近期重大事件"})
    
    tactical_action_override = ""
    if is_whipsaw: 
        ai_tags_dict.append({"text": "⚠️ 盤整洗盤陷阱", "class": "tag-green", "title": "主力拉高後倒貨。操作準則：空手者【嚴禁進場】，持倉者【跌破開盤價即刻停損】。"})
        tactical_action_override = "<br><span style='color:#f1c40f;'>🚨 [行動準則] 遭遇洗盤陷阱：空手者切勿進場；持倉者若跌破今日開盤價，請立即停損退場！</span>"
    elif curr < ma5 and ma5 < ma20: ai_tags_dict.append({"text": "💀 衰退作頭", "class": "tag-green", "title": "跌破均線，趨勢轉弱，建議避開"})
    elif is_ma_bullish: ai_tags_dict.append({"text": "🔴 主升狂飆", "class": "tag-red", "title": "短中長天期均線呈現多頭排列，處於主升段，動能強勁"})
    elif curr > ma5 and ma5 > ma20 and vol_ratio >= 1.5: ai_tags_dict.append({"text": "🟡 突破發動", "class": "tag-red", "title": "剛站上短期均線且放量，表態發動"})
    elif curr > ma60 and curr < ma20 and vol_ratio <= 0.8: ai_tags_dict.append({"text": "🟢 築底潛伏", "class": "tag-blue", "title": "長線多頭但短線量縮回測，適合潛伏"})

    if symbol in GOOAYE_INTEL_DB:
        eps = GOOAYE_INTEL_DB[symbol]
        ai_tags_dict.append({"text": f"🎙️ 股癌點名 EP{eps[0]}", "class": "tag-purple", "title": f"歷史點名紀錄集數: {list(eps)}"})

    for g_name, codes in INTERNAL_SECTORS_DB.items():
        if symbol in codes: ai_tags_dict.append({"text": f"J. {g_name}", "class": "tag-purple", "title": "所屬大型集團或強勢熱門產業"}); break
    
    if f_cb > 0 and t_cb > 0: ai_tags_dict.append({"text": f"💎 土洋齊買 (外連{f_cb} / 投連{t_cb})", "class": "tag-purple", "title": f"外資囤 {f_vb:,.0f} 張，投信囤 {t_vb:,.0f} 張，籌碼極度集中"})
    else:
        if f_cb >= 3: ai_tags_dict.append({"text": f"💰 外資連 {f_cb} 買 | 囤 {f_vb:,.0f} 張", "class": "tag-purple", "title": f"外資連續買超大於3天，共囤貨 {f_vb:,.0f} 張"})
        if t_cb >= 3: ai_tags_dict.append({"text": f"🏦 投信連 {t_cb} 買 | 囤 {t_vb:,.0f} 張", "class": "tag-purple", "title": f"投信連續買超大於3天，共囤貨 {t_vb:,.0f} 張"})
        if f_cs >= 3: ai_tags_dict.append({"text": f"🩸 外資連 {f_cs} 賣 | 倒 {abs(f_vs):,.0f} 張", "class": "tag-green", "title": f"外資連續賣超大於3天，共倒貨 {abs(f_vs):,.0f} 張"})
        if t_cs >= 3: ai_tags_dict.append({"text": f"🩸 投信連 {t_cs} 賣 | 倒 {abs(t_vs):,.0f} 張", "class": "tag-green", "title": f"投信連續賣超大於3天，共倒貨 {abs(t_vs):,.0f} 張"})
    if display_t >= 400 and not (f_cb > 0 and t_cb > 0): ai_tags_dict.append({"text": "K. 投信作帳", "class": "tag-purple", "title": "投信單日大買超過 400 張，具備作帳行情潛力"})
    
    if rev_growth is not None and rev_growth > 20.0: ai_tags_dict.append({"text": "🛡️ 營收雙增盾牌", "class": "tag-red", "title": "單月營收較去年同期顯著成長(>20%)，具備基本面防護"})

    if is_golden_start: ai_tags_dict.append({"text": "🔥 第一根爆量起漲 (雙金叉)", "class": "tag-red", "title": "同時符合KDJ與MACD金叉，且今日爆量轉強起漲，強勢訊號"})
    else:
        if is_first_red_trigger: ai_tags_dict.append({"text": "A. 起漲第一根", "class": "tag-red", "title": "今日首度帶量突破 5 日線，趨勢可能反轉向上"})
        if kdj_str == "金叉": ai_tags_dict.append({"text": "KDJ金叉", "class": "tag-red", "title": "短線動能轉強，KDJ指標呈現黃金交叉"})
        if vol_ratio >= 2.0 and gain >= 2.0: ai_tags_dict.append({"text": "爆量上攻", "class": "tag-red", "title": "成交量大於 5 日均量 2 倍以上，且股價上漲，有主力介入"})
        
    if rs_score >= 1.5 and gain >= -1.0: ai_tags_dict.append({"text": "E. 逆勢抗跌", "class": "tag-blue", "title": "大盤弱勢時，個股相對強勢抗跌，籌碼穩定"})
    if is_fake_breakout: ai_tags_dict.append({"text": "假突破(避雷針)", "class": "tag-green", "title": "爆量但留極長上影線，主力可能假拉高真出貨，風險極高"})
    if curr < ma5: ai_tags_dict.append({"text": "跌破5日線", "class": "tag-green", "title": "短線防線跌破，趨勢轉弱，須留意停損"})

    entry_price = float(portfolio_data.get('entry_price', 0.0)) if portfolio_data else 0.0
    roi_pct = ((curr - entry_price) / entry_price) * 100 if entry_price > 0 else 0.0
    
    st_buy = f"{ma5:.1f} ~ {curr:.1f}" if curr > ma5 else f"{hist_df['Low'].tail(10).min():.1f} ~ {curr:.1f}"
    st_stop = str(round(curr * 0.98, 1))
    lt_buy = f"{ma60:.1f} ~ {ma20:.1f}" if curr > ma60 else "不建議佈局"
    lt_stop = str(round(ma60 * 0.95, 1)) if curr > ma60 else "N/A"

    if is_whipsaw: signal_text, color_border, signal_bg = "[⚠️ 盤整洗盤陷阱]", "#f1c40f", "#332b00"
    elif curr > ma60 and curr > ma5: signal_text, color_border, signal_bg = "[🔥 偏多操作]", "#ff4d4d", "#3a1515"
    elif curr > ma60 and curr <= ma5: signal_text, color_border, signal_bg = "[⚠️ 拉回整理]", "#f1c40f", "#332b00"
    else: signal_text, color_border, signal_bg = "[📉 空頭觀望]", "#00FF00", "#153a20"

    is_action_needed = (curr < ma5 or is_fake_breakout) or (entry_price > 0 and roi_pct <= -10.0)
    if entry_price > 0 and roi_pct <= -10.0: signal_text, color_border, signal_bg = "[💀 觸發停損]", "#00FF00", "#153a20"
    elif (curr < ma5 or is_fake_breakout) and not is_whipsaw: signal_text, color_border, signal_bg = f"[🚨 撤退警告]", "#00FF00", "#153a20"
        
    wave_range = (high_p - low_p) + 1e-9
    lower_shadow_pct = (min(open_p, curr) - low_p) / wave_range * 100

    tactical_summary = f"""<div style="background:#15203a; border-left: 4px solid #00d2ff; padding: 12px; margin-top: 5px; border-radius: 4px;"><span style="color:#00d2ff; font-weight:bold; font-size:15px;">[📊 戰情解析中樞]</span><br><span style="color:#ccc;">A. 體質診斷：股價季線防守於 {ma60:.1f}，評估為{val_shield}。</span><br><span style="color:#ccc;">B. 動能狀態：短線下影線支撐強度: {lower_shadow_pct:.1f}%。</span><br><span style="color:#ccc;">C. 籌碼對抗 {fm_status}：大戶(法人) {inst_net:,} 張 vs 散戶(融資) {retail_net:,.0f} 張</span><br>{chip_battle_str}<br><span style="color:#f1c40f; font-weight:bold; display:block; margin-top:6px;">[🎯 戰局判定]：不破開盤生死線 ({open_p:.2f}) 則結構未散。若觸發警報請立即檢閱戰損診斷。</span>{tactical_action_override}</div>"""

    return {
        "name": stock_name, "code": symbol, "price": curr, "gain": gain,
        "open": open_p, "high": high_p, "low": low_p, "vol": vol, "vol_5d": vol_5d, "rs_score": rs_score,
        "cost_label": "季線防守", "cost": round(ma60, 1), 
        "signal": signal_text, "color": color_border, 
        "signal_bg": signal_bg, "ai_tags_dict": ai_tags_dict, "tactical_summary": tactical_summary,
        "st_buy": st_buy, "st_stop": st_stop, "lt_buy": lt_buy, "lt_stop": lt_stop,
        "kdj_str": kdj_str, "macd_str": macd_str, "macd_color": macd_color, "vol_ratio": vol_ratio, "val_score": score,
        "val_shield": val_shield, "is_action_needed": is_action_needed, "is_crash_alert": is_crash_alert,
        "chip_battle_str": chip_battle_str, "f_buy": display_f, "t_buy": display_t, "margin_diff": margin_diff, "rev_growth": rev_growth, "earnings_date": earnings_date,
        "sector": get_industry_label_wrapper(symbol), "sparkline": sparkline_str, "lower_shadow_pct": lower_shadow_pct,
        "is_first_red": is_first_red_trigger, "is_vol_breakout": (vol_ratio >= 2.0), "is_yesterday_strong": is_yesterday_strong
    }

def generate_ai_report(command_name, candidates):
    if not GEMINI_API_KEYS or not GEMINI_API_KEYS[0]: return "⚠️ [系統提示] 未配置有效的 API 金鑰。"
    if not candidates: return "⚠️ [系統提示] 目前沒有符合條件的標的。"
    lite_data = []
    for c in candidates[:15]: 
        lite_data.append({'代號': c['code'], '名稱': c['name'], '價格': c['price'], '漲幅': c['gain'], '特徵': [t['text'] for t in c.get('ai_tags_dict', [])], '籌碼戰局': c.get('chip_battle_str', '')})
    prompt = f"你是首席戰略幕僚。總指揮使用戰術：【{command_name}】。名單：\n{json.dumps(lite_data, ensure_ascii=False)}"
    key_statuses = check_api_keys(GEMINI_API_KEYS, st.session_state.ai_mode)
    start_idx = st.session_state.active_key_index
    for i in range(len(GEMINI_API_KEYS)):
        idx = (start_idx + i) % len(GEMINI_API_KEYS)
        if key_statuses[idx]["status"] == "OK":
            return f"**([啟動 {key_statuses[idx]['model']} 核心運算])**\n\n[🤖 AI 幕僚戰術打包回報成功]"
    return "❌ 所有金鑰皆無法使用。"

# ==========================================
# 8. UI 裝甲級 CSS 與卡片渲染
# ==========================================
st.markdown("""<style>
:root { color-scheme: dark !important; }
html, body, [class*="css"] { color-scheme: dark !important; }
.stApp { background-color: #0b0c0f !important; color: #fff !important; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
div[data-testid="stSidebar"], section[data-testid="stSidebar"] { background-color: #12141a !important; border-right: 1px solid #333 !important; }
p, label, .stMarkdown p { color: #ffffff; }

div[data-testid="stButton"] > button { background-color: #1e1e24 !important; border: 1px solid #444 !important; }
div[data-testid="stButton"] > button p { color: #00d2ff !important; font-weight: bold !important; text-shadow: 1px 1px 2px rgba(0,0,0,0.5);}
div[data-testid="stAlert"] { background-color: #e6e9ef !important; border-left: 4px solid #00FF00 !important; }
div[data-testid="stAlert"] * { color: #000000 !important; font-weight: bold !important; }
div[data-testid="stCheckbox"] label p { color: #00FF00 !important; font-size: 15px !important; font-weight: bold !important; background-color: #153a20; padding: 4px 8px; border-radius: 4px; border: 1px solid #00FF00; }
.stSelectbox label p, .stSlider label p { color: #00d2ff !important; font-weight: bold !important; }

/* 絕對高亮 Expander (修正紫黑色迷彩 Bug) */
div[data-testid="stExpander"] div[role="button"] p, div[data-testid="stExpander"] summary p, div[data-testid="stExpander"] summary span { color: #00d2ff !important; font-weight: bold !important; font-size: 15px !important; text-shadow: 1px 1px 2px rgba(0,0,0,0.8); }
details summary { background-color: #1a1c23 !important; border: 1px solid #444 !important; border-radius: 6px !important; padding: 10px !important; }

.hud-box { background: linear-gradient(135deg, #1a1c23 0%, #0d1117 100%); border-radius: 10px; padding: 15px; border-left: 5px solid #ff4d4d; box-shadow: 0 4px 15px rgba(0,0,0,0.5); margin-bottom: 20px;}
.hud-title { color: #f1c40f; font-size: 14px; font-weight: bold; margin-bottom: 10px; border-bottom: 1px solid #333; padding-bottom: 5px;}
.hud-metric { display: flex; justify-content: space-between; align-items: center; margin-bottom: 5px;}
.tag-base { display: inline-block; padding: 4px 8px; border-radius: 4px; font-size: 13px; font-weight: bold; margin: 0 5px 5px 0; }
.tag-red { background: #3a1515; color: #ff4d4d; border: 1px solid #e74c3c; }
.tag-green { background: #153a20; color: #00FF00; border: 1px solid #2ecc71; }
.tag-blue { background: #15203a; color: #00d2ff; border: 1px solid #3498db; }
.tag-purple { background: #2a153a; color: #d200ff; border: 1px solid #9b59b6; }
.metric-grid { display: flex; gap: 15px; flex-wrap: wrap; font-size: 13px; color: #ccc; background: #10141d; padding: 12px; border-radius: 6px; border: 1px solid #333; margin-bottom: 10px;}
.tactical-summary { background: #000; border-top: 1px dashed #444; margin-top: 10px; padding: 12px; font-size: 14px; color: #ddd; border-radius: 5px;}
.key-status-ok { color: #00FF00; font-weight: bold; font-size: 13px; }
.key-status-fail { color: #ff4d4d; font-weight: bold; font-size: 13px; }
</style>""", unsafe_allow_html=True)

def draw_card(d, ui_key_prefix, is_portfolio=False, p_data=None):
    if not d: return
    gain_color = '#ff4d4d' if d['gain'] > 0 else ('#00FF00' if d['gain'] < 0 else '#aaaaaa')
    gain_bg = '#3a1515' if d['gain'] > 0 else ('#153a20' if d['gain'] < 0 else '#333333')
    tags_html = "".join([f"<span class='tag-base {t['class']}'>{t['text']}</span> " for t in d.get('ai_tags_dict',
