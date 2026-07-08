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
st.set_page_config(layout="wide", page_title="54088 戰情室 V131", initial_sidebar_state="expanded")
st.toast("✅ [系統提示] V131 全面解鎖除權息與修復版 啟動成功！")

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

# 💥 V131 [法說會與除權息雙棲抓取引擎]
@st.cache_data(ttl=86400, show_spinner=False)
def fetch_twse_earnings_and_dividends():
    calls, divs = {}, {}
    try:
        r1 = requests.get("https://openapi.twse.com.tw/v1/opendata/t187ap46_L", headers=GOV_HEADERS, verify=False, timeout=10)
        if r1.status_code == 200:
            for item in r1.json():
                c = str(item.get('公司代號', '')).strip()
                calls[c] = str(item.get('召開法人說明會日期', '')).strip()
    except: pass
    try:
        r2 = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/TWT48U", headers=GOV_HEADERS, verify=False, timeout=10)
        if r2.status_code == 200:
            for item in r2.json():
                c = str(item.get('股票代號', '')).strip()
                if len(c) == 4:
                    divs[c] = {
                        'date': str(item.get('除權息日期', '')).strip(),
                        'cash': item.get('現金股利', '0'),
                        'stock': item.get('無償配股', '0')
                    }
    except: pass
    return calls, divs

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

# 💥 V131 [Bug 根除] 修復 API 單拉時，多週期籌碼掛 0 的致命錯誤
@st.cache_data(ttl=3600, show_spinner=False)
def fetch_recent_chips_rescue(symbol, token_string=""):
    f_cb = t_cb = f_cs = t_cs = 0
    f_vb = t_vb = f_vs = t_vs = 0.0
    f_latest = t_latest = 0
    finmind_success = False
    m_chips = {'f_5d': 0, 't_5d': 0, 'f_10d': 0, 't_10d': 0, 'f_20d': 0, 't_20d': 0}
    
    start_date = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
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
                    for i in range(min(20, len(f_series))):
                        val = int(f_series.iloc[i] / 1000)
                        if i < 20: m_chips['f_20d'] += val
                        if i < 10: m_chips['f_10d'] += val
                        if i < 5:  m_chips['f_5d']  += val
                    for val in f_series:
                        if val > 0 and f_cs == 0: f_cb += 1; f_vb += val / 1000
                        elif val < 0 and f_cb == 0: f_cs += 1; f_vs += val / 1000
                        else: break
                if not t_series.empty:
                    t_latest = int(t_series.iloc[0] / 1000)
                    for i in range(min(20, len(t_series))):
                        val = int(t_series.iloc[i] / 1000)
                        if i < 20: m_chips['t_20d'] += val
                        if i < 10: m_chips['t_10d'] += val
                        if i < 5:  m_chips['t_5d']  += val
                    for val in t_series:
                        if val > 0 and t_cs == 0: t_cb += 1; t_vb += val / 1000
                        elif val < 0 and t_cb == 0: t_cs += 1; t_vs += val / 1000
                        else: break
    except Exception: pass
    return f_cb, t_cb, f_cs, t_cs, f_latest, t_latest, int(f_vb), int(t_vb), int(f_vs), int(t_vs), finmind_success, m_chips

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
EARNINGS_CALL_DB, DIVIDEND_DB = fetch_twse_earnings_and_dividends() # 💥 V131 雙棲引擎啟動

# ==========================================
# 6. 本地大腦與 V130 自動瘦身防護
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
                if len(st.session_state.inst_history) > 30:
                    sorted_dates = sorted(st.session_state.inst_history.keys(), reverse=True)
                    st.session_state.inst_history = {d: st.session_state.inst_history[d] for d in sorted_dates[:30]}
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
# 7. 核心運算引擎 (V131 全面解鎖除權息與修復)
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
    vol_10d = max(hist_df['Volume'].iloc[-11:-1].mean() / 1000, 0.01) if len(hist_df) >= 11 else vol_5d
    vol_20d = max(hist_df['Volume'].iloc[-21:-1].mean() / 1000, 0.01) if len(hist_df) >= 21 else vol_5d
    
    rs_score = gain - twii_gain
    margin_diff = MARGIN_DB.get(symbol, 0.0)
    
    f_cb = t_cb = f_cs = t_cs = 0
    f_vb = t_vb = f_vs = t_vs = 0
    fm_status = ""
    m_chips = {'f_5d': 0, 't_5d': 0, 'f_10d': 0, 't_10d': 0, 'f_20d': 0, 't_20d': 0}
    
    # 💥 V131 徹底解決籌碼掛 0 的 Bug (現在 `fetch_recent_chips_rescue` 會完美回傳 m_chips)
    if not is_scan:
        f_cb, t_cb, f_cs, t_cs, display_f, display_t, f_vb, t_vb, f_vs, t_vs, finmind_success, m_chips = fetch_recent_chips_rescue(symbol, SECRET_FINMIND)
        fm_status = "<span style='color:#00FF00;'>[🟢 FinMind連線正常]</span>" if finmind_success else "<span style='color:#ff4d4d;'>[🔴 FinMind限速/無資料]</span>"
    else:
        display_f = display_t = 0
        fm_status = "<span style='color:#f1c40f;'>[🟡 掃描模式(無動態籌碼)]</span>"
        if st.session_state.inst_history:
            sorted_dates = sorted(st.session_state.inst_history.keys(), reverse=True)
            f_b_broken = f_s_broken = t_b_broken = t_s_broken = False
            for i, d in enumerate(sorted_dates):
                d_f = st.session_state.inst_history[d].get(symbol, {}).get('foreign', 0)
                d_t = st.session_state.inst_history[d].get(symbol, {}).get('trust', 0)
                if d == sorted_dates[0]:
                    display_f = d_f
                    display_t = d_t
                if i < 20: m_chips['f_20d'] += d_f; m_chips['t_20d'] += d_t
                if i < 10: m_chips['f_10d'] += d_f; m_chips['t_10d'] += d_t
                if i < 5:  m_chips['f_5d']  += d_f; m_chips['t_5d']  += d_t
                
                if d_f > 0 and not f_b_broken: f_cb+=1; f_vb+=d_f; f_s_broken=True
                elif d_f < 0 and not f_s_broken: f_cs+=1; f_vs+=d_f; f_b_broken=True
                else: f_b_broken = f_s_broken = True
                if d_t > 0 and not t_b_broken: t_cb+=1; t_vb+=d_t; t_s_broken=True
                elif d_t < 0 and not t_s_broken: t_cs+=1; t_vs+=d_t; t_b_broken=True
                else: t_b_broken = t_s_broken = True

    inst_net = display_f + display_t
    retail_net = margin_diff
    
    f_pct_today = (display_f / vol * 100) if vol > 0 else 0
    t_pct_today = (display_t / vol * 100) if vol > 0 else 0
    margin_pct_today = (margin_diff / vol * 100) if vol > 0 else 0
    
    f_pct_5d  = (m_chips['f_5d'] / (vol_5d * 5) * 100) if vol_5d > 0 else 0
    t_pct_5d  = (m_chips['t_5d'] / (vol_5d * 5) * 100) if vol_5d > 0 else 0
    f_pct_10d = (m_chips['f_10d'] / (vol_10d * 10) * 100) if vol_10d > 0 else 0
    t_pct_10d = (m_chips['t_10d'] / (vol_10d * 10) * 100) if vol_10d > 0 else 0
    f_pct_20d = (m_chips['f_20d'] / (vol_20d * 20) * 100) if vol_20d > 0 else 0
    t_pct_20d = (m_chips['t_20d'] / (vol_20d * 20) * 100) if vol_20d > 0 else 0
    
    ai_chip_summary = ""
    if m_chips['f_5d'] > 0 and m_chips['t_5d'] > 0 and margin_diff < 0: ai_chip_summary = f"🔥 [AI總結] 融資(散戶)退場，外資與投信近 5 日同步低接吃貨，籌碼呈絕對安定。"
    elif m_chips['f_10d'] < 0 and margin_diff > 0: ai_chip_summary = f"⚠️ [AI總結] 外資近 10 日高檔持續出貨，且融資(散戶)反向進場接刀，籌碼渙散請提防。"
    elif f_cb >= 3 and t_cb >= 3: ai_chip_summary = f"🚀 [AI總結] 內外資巨頭同步霸盤！外資連買 {f_cb} 天，投信連買 {t_cb} 天，隨時準備發動。"
    elif f_cs >= 3 and t_cs >= 3: ai_chip_summary = f"💀 [AI總結] 內外資巨頭同步大逃殺！連賣超過 3 天，切勿進場接刀。"
    elif m_chips['f_5d'] > 0 and margin_diff < 0: ai_chip_summary = f"✅ [AI總結] 散戶減少，外資近 5 日佈局買超，籌碼集中度提升。"
    else: ai_chip_summary = f"⚖️ [AI總結] 尚未出現明顯多空對抗特徵，資金處於觀望拉鋸戰。"

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
    
    # 💥 V131 [盲區解鎖] 財報日與法說會雙棲判定
    now = datetime.now()
    now_m, now_d = now.month, now.day
    if now_m < 3 or (now_m == 3 and now_d <= 31): statutory_deadline = "年報(03/31前)"
    elif now_m < 5 or (now_m == 5 and now_d <= 15): statutory_deadline = "Q1財報(05/15前)"
    elif now_m < 8 or (now_m == 8 and now_d <= 14): statutory_deadline = "Q2財報(08/14前)"
    elif now_m < 11 or (now_m == 11 and now_d <= 14): statutory_deadline = "Q3財報(11/14前)"
    else: statutory_deadline = "年報(隔年03/31前)"

    call_date = EARNINGS_CALL_DB.get(symbol)
    earnings_display = f"🔥 {call_date} 法說會" if call_date else statutory_deadline

    # 💥 V131 [除權息資訊植入]
    div_data = DIVIDEND_DB.get(symbol)
    if div_data:
        div_display = f"除息日:{div_data['date']} | 現金:{div_data['cash']} | 配股:{div_data['stock']}"
        div_cash = safe_float(div_data['cash'])
        div_stock = safe_float(div_data['stock'])
        div_yield = (div_cash / curr * 100) if curr > 0 else 0
        div_date_raw = div_data['date']
    else:
        div_display = "無近期除息資訊"
        div_yield = 0.0
        div_cash = 0.0
        div_stock = 0.0
        div_date_raw = ""
    
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

    tactical_summary = f"""<div style="background:#15203a; border-left: 4px solid #00d2ff; padding: 12px; margin-top: 5px; border-radius: 4px;"><span style="color:#00d2ff; font-weight:bold; font-size:15px;">[📊 戰情解析中樞]</span><br><span style="color:#ccc;">A. 體質診斷：股價季線防守於 {ma60:.1f}，評估為{val_shield}。</span><br><span style="color:#ccc;">B. 動能狀態：短線下影線支撐強度: {lower_shadow_pct:.1f}%。</span><br><span style="color:#f1c40f; font-weight:bold; display:block; margin-top:6px;">[🎯 戰局判定]：不破開盤生死線 ({open_p:.2f}) 則結構未散。</span>{tactical_action_override}</div>"""

    return {
        "name": stock_name, "code": symbol, "price": curr, "gain": gain,
        "open": open_p, "high": high_p, "low": low_p, "vol": vol, "vol_5d": vol_5d, "rs_score": rs_score,
        "cost_label": "季線防守", "cost": round(ma60, 1), 
        "signal": signal_text, "color": color_border, 
        "signal_bg": signal_bg, "ai_tags_dict": ai_tags_dict, "tactical_summary": tactical_summary,
        "st_buy": st_buy, "st_stop": st_stop, "lt_buy": lt_buy, "lt_stop": lt_stop,
        "kdj_str": kdj_str, "macd_str": macd_str, "macd_color": macd_color, "vol_ratio": vol_ratio, "val_score": score,
        "val_shield": val_shield, "is_action_needed": is_action_needed, "is_crash_alert": is_crash_alert,
        "chip_battle_str": chip_battle_str, "f_buy": display_f, "t_buy": display_t, "margin_diff": margin_diff, "rev_growth": rev_growth, "earnings_display": earnings_display,
        "sector": get_industry_label_wrapper(symbol), "sparkline": sparkline_str, "lower_shadow_pct": lower_shadow_pct,
        "is_first_red": is_first_red_trigger, "is_vol_breakout": (vol_ratio >= 2.0), "is_yesterday_strong": is_yesterday_strong,
        "f_pct_today": f_pct_today, "t_pct_today": t_pct_today, "margin_pct_today": margin_pct_today,
        "m_chips": m_chips, "f_pct_5d": f_pct_5d, "t_pct_5d": t_pct_5d, "f_pct_10d": f_pct_10d, "t_pct_10d": t_pct_10d, 
        "f_pct_20d": f_pct_20d, "t_pct_20d": t_pct_20d, "ai_chip_summary": ai_chip_summary,
        "div_display": div_display, "div_cash": div_cash, "div_stock": div_stock, "div_yield": div_yield, "div_date_raw": div_date_raw
    }

def generate_ai_report(command_name, candidates):
    if not GEMINI_API_KEYS or not GEMINI_API_KEYS[0]: return "⚠️ [系統提示] 未配置有效的 API 金鑰。"
    if not candidates: return "⚠️ [系統提示] 目前沒有符合條件的標的。"
    candidates = sorted(candidates, key=lambda x: x['vol_ratio'], reverse=True)[:5]
    lite_data = []
    for c in candidates: 
        lite_data.append({'代號': c['code'], '名稱': c['name'], '價格': c['price'], '漲幅': c['gain'], '特徵': [t['text'] for t in c.get('ai_tags_dict', [])]})
    prompt = f"你是首席戰略幕僚。總指揮使用戰術：【{command_name}】。名單(已精選前5檔爆量)：\n{json.dumps(lite_data, ensure_ascii=False)}\n請針對這幾檔給出具體沙盤推演，並標示關鍵防守價位。"
    key_statuses = check_api_keys(GEMINI_API_KEYS, st.session_state.ai_mode)
    start_idx = st.session_state.active_key_index
    for i in range(len(GEMINI_API_KEYS)):
        idx = (start_idx + i) % len(GEMINI_API_KEYS)
        if key_statuses[idx]["status"] == "OK":
            try:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{key_statuses[idx]['model']}:generateContent?key={GEMINI_API_KEYS[idx]}"
                res = requests.post(url, headers={'Content-Type': 'application/json'}, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=25)
                if res.status_code == 200:
                    text = res.json()['candidates'][0]['content']['parts'][0]['text']
                    return f"**([啟動 {key_statuses[idx]['model']} 核心運算])**\n\n{text}"
            except Exception: pass
    return "❌ 所有金鑰皆無法使用。"

# ==========================================
# 8. UI 裝甲級 CSS 與卡片渲染 (V131 支援雙新兵器版面)
# ==========================================
st.markdown("""<style>
:root { color-scheme: dark !important; }
html, body, [class*="css"] { color-scheme: dark !important; }
.stApp { background-color: #0b0c0f !important; color: #fff !important; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
div[data-testid="stSidebar"], section[data-testid="stSidebar"] { background-color: #12141a !important; border-right: 1px solid #333 !important; }
p, label, .stMarkdown p { color: #ffffff; }

div[data-testid="stButton"] > button, div[data-testid="stDownloadButton"] > button, div[data-testid="stBaseButton-secondary"], div[data-testid="stBaseButton-primary"] { background-color: #1e1e24 !important; border: 1px solid #444 !important; transition: all 0.2s ease-in-out; }
div[data-testid="stButton"] > button p, div[data-testid="stDownloadButton"] > button p, div[data-testid="stButton"] > button span { color: #00d2ff !important; font-weight: bold !important; font-size: 15px !important; text-shadow: 1px 1px 2px rgba(0,0,0,0.5); }
.scan-btn div[data-testid="stButton"] > button p { color: #ff4d4d !important; }

div[data-testid="stAlert"] { background-color: #e6e9ef !important; border-left: 4px solid #00FF00 !important; }
div[data-testid="stAlert"] * { color: #000000 !important; font-weight: bold !important; }
div[data-testid="stCheckbox"] label p { color: #00FF00 !important; font-size: 15px !important; font-weight: bold !important; background-color: #153a20; padding: 4px 8px; border-radius: 4px; border: 1px solid #00FF00; }
.stSelectbox label p, .stSlider label p { color: #00d2ff !important; font-weight: bold !important; font-size: 15px !important; }

div[data-testid="stExpander"] div[role="button"] { background-color: #1a1c23 !important; border: 1px solid #444 !important; }
div[data-testid="stExpander"] div[role="button"] p, div[data-testid="stExpander"] summary p, div[data-testid="stExpander"] summary span { color: #00d2ff !important; font-weight: bold !important; font-size: 15px !important; text-shadow: 1px 1px 2px rgba(0,0,0,0.8); }
div[data-testid="stExpanderDetails"] { background-color: #0d1117 !important; color: #fff !important; }
details summary { background-color: #1a1c23 !important; border: 1px solid #444 !important; border-radius: 6px !important; padding: 10px !important; }
details { border: none !important; box-shadow: none !important; margin-bottom: 5px !important;}

.hud-box { background: linear-gradient(135deg, #1a1c23 0%, #0d1117 100%); border-radius: 10px; padding: 15px; border-left: 5px solid #ff4d4d; box-shadow: 0 4px 15px rgba(0,0,0,0.5); margin-bottom: 20px;}
.hud-title { color: #f1c40f; font-size: 14px; font-weight: bold; margin-bottom: 10px; border-bottom: 1px solid #333;}
.hud-metric { display: flex; justify-content: space-between; align-items: center; margin-bottom: 5px;}
.tag-base { display: inline-block; padding: 4px 8px; border-radius: 4px; font-size: 13px; font-weight: bold; margin: 0 5px 5px 0; }
.tag-red { background: #3a1515; color: #ff4d4d; border: 1px solid #e74c3c; }
.tag-green { background: #153a20; color: #00FF00; border: 1px solid #2ecc71; }
.tag-blue { background: #15203a; color: #00d2ff; border: 1px solid #3498db; }
.tag-purple { background: #2a153a; color: #d200ff; border: 1px solid #9b59b6; }
.metric-grid { display: flex; gap: 15px; flex-wrap: wrap; font-size: 13px; color: #ccc; margin-bottom: 10px; background: #10141d; padding: 12px; border-radius: 6px; border: 1px solid #333;}
.tactical-summary { background: #000; border-top: 1px dashed #444; margin-top: 10px; padding: 12px; font-size: 14px; color: #ddd; border-radius: 5px;}
.tactical-danger { background: #153a20; border-top: 1px dashed #2ecc71; margin-top: 10px; padding: 12px; font-size: 14px; color: #ddd; border-radius: 5px;}
</style>""", unsafe_allow_html=True)

# 採用安全多行寫法，絕對杜絕括號截斷問題
def draw_card(d, ui_key_prefix, is_portfolio=False, p_data=None):
    if not d: return
    gain_color = '#ff4d4d' if d['gain'] > 0 else ('#00FF00' if d['gain'] < 0 else '#aaaaaa')
    gain_bg = '#3a1515' if d['gain'] > 0 else ('#153a20' if d['gain'] < 0 else '#333333')
    
    tags_html = ""
    for tag_dict in d.get('ai_tags_dict', []):
        tags_html += f"<span class='tag-base {tag_dict['class']}' title='{tag_dict['title']}'>{tag_dict['text']}</span> "
        
    port_html = ""
    if is_portfolio and p_data:
        prof, pct, fb, fs, tax = calc_real_profit(p_data['entry_price'], d['price'], p_data['qty'])
        prof_color = '#ff4d4d' if prof > 0 else ('#00FF00' if prof < 0 else '#aaaaaa')
        port_html = f"<div style='background:#10141d; padding:10px; border-radius:6px; margin-bottom:12px; border:1px solid #333;'><div style='display:flex; justify-content:space-between; margin-bottom:4px;'><span style='color:#aaa; font-size:13px;'>進場單價: <strong style='color:#f1c40f;'>{p_data['entry_price']}</strong></span><span style='color:#aaa; font-size:13px;'>持有張數: <strong style='color:#f1c40f;'>{p_data['qty']} 張</strong></span></div><div style='display:flex; justify-content:space-between; margin-bottom:4px;'><span style='color:#888; font-size:12px;'>預估手續費: {fb+fs} 元</span><span style='color:#888; font-size:12px;'>證券交易稅: {tax} 元</span></div><div style='border-top:1px dashed #333; margin:6px 0;'></div><div style='display:flex; justify-content:space-between; align-items:center;'><span style='color:#ddd; font-size:14px;'>淨損益 (扣除息費):</span><strong style='color:{prof_color}; font-size:16px;'>{int(prof):+,} 元 ({pct:+.2f}%)</strong></div></div>"
    
    is_alert = d.get('is_crash_alert', False)
    if is_alert and (is_portfolio or ui_key_prefix.startswith('pin_')):
        alert_banner = "<div style='background-color:#00FF00; color:#000; padding:5px; text-align:center; font-weight:bold; font-size:15px; border-radius:4px; margin-bottom:10px; letter-spacing:1px;'>🚨 [系統強制警報] 跌幅過大或破5日線，請立即檢視戰損！</div>"
        d['color'] = "#00FF00"
    else: 
        alert_banner = ""
    
    kdj_color = "#ff4d4d" if "金" in d['kdj_str'] or "上" in d['kdj_str'] else "#00FF00"
    
    rev_val = d.get('rev_growth')
    try:
        if rev_val is None or abs(float(rev_val)) < 0.01: 
            rev_display = "<span style='color:#888;'>API未提供</span>"
        else: 
            rev_display = f"{float(rev_val):.1f}%"
    except:
        rev_display = "<span style='color:#888;'>API未提供</span>"

    # 💥 V131: 網格加上除權息與法說財報日
    metric_grid = f"<div class='metric-grid'><div style='width:100%; margin-bottom:6px; display:flex; justify-content:space-between;'><span>近7日走勢: <strong style='color:#00d2ff; font-size:16px; letter-spacing:2px;'>{d.get('sparkline', '')}</strong></span><span>價值分數: <strong style='color:#00d2ff; font-size:15px;'>{d['val_score']} 分</strong> <span style='color:#888;'>({d['val_shield']})</span></span></div><div style='width:100%; border-top: 1px dashed #444; margin-bottom:6px; padding-top:6px; display:flex; gap:15px; flex-wrap:wrap;'><span>開盤: <strong style='color:#fff;'>{d['open']:.2f}</strong></span><span>最高: <strong style='color:#fff;'>{d['high']:.2f}</strong></span><span>最低: <strong style='color:#fff;'>{d['low']:.2f}</strong></span><span>總量: <strong style='color:#f1c40f;'>{d['vol']:,} 張</strong></span></div><div style='width:100%; border-top: 1px dashed #444; margin-bottom:6px;'></div><div style='width:100%; display:flex; justify-content:space-between; margin-bottom:4px;'><span style='flex:1;'>短線戰略: <strong style='color:#f1c40f;'>{d['st_buy']}</strong> (防禦: <span style='color:#00FF00;'>{d['st_stop']}</span>)</span><span style='flex:1;'>長線戰略: <strong style='color:#00d2ff;'>{d['lt_buy']}</strong> (防禦: <span style='color:#00FF00;'>{d['lt_stop']}</span>)</span></div><div style='width:100%; border-top: 1px dashed #444; margin-top:4px; margin-bottom:6px;'></div><div style='width:100%; display:flex; flex-wrap:wrap; gap:10px; align-items:center;'><span>多空趨勢: <strong style='color:{d['macd_color']};'>{d['macd_str']}</strong></span><span>KDJ: <strong style='color:{kdj_color};'>{d['kdj_str']}</strong></span><span>爆量比: <strong style='color:#e67e22;'>{d['vol_ratio']:.1f}x</strong></span><span>營收年增: <strong style='color:#00d2ff;'>{rev_display}</strong></span><span>財報(法說): <strong style='color:#f1c40f;'>{d['earnings_display']}</strong></span><span>除權息: <strong style='color:#d200ff;'>{d['div_display']}</strong></span></div></div>"
    
    summary_class = "tactical-danger" if d['is_action_needed'] else "tactical-summary"
    
    html_str = f"<div style='border: 2px solid {d['color']}; border-radius: 8px; padding: 15px; background-color: #16191f; margin-bottom: 5px;'>{alert_banner}{port_html}<div style='display:flex; justify-content:space-between; align-items:center; margin-bottom:5px;'><span style='font-weight:bold; font-size:18px;'>{d['name']} ({d['code']}) <span style='font-size:12px; color:#aaa; background:#333; padding:2px 6px; border-radius:4px; font-weight:normal;'>{d.get('sector', '綜合')}</span></span><span style='color:#888; font-size:12px;'>{d['cost_label']}: {d['cost']}</span></div><div style='font-size:32px; font-weight:bold; margin-bottom: 10px; display:flex; gap:12px;'>{d['price']:.2f} <span style='font-size:16px; color:{gain_color}; background-color:{gain_bg}; padding:4px 10px; border-radius:6px;'>{d['gain']:+.1f}%</span></div><div style='margin-bottom: 10px;'>{tags_html}</div>{metric_grid}<div style='background:{d['signal_bg']}; padding:10px; border-radius:6px; text-align:center; margin-bottom:10px; border: 1px solid {d['color']}40;'><strong style='color:{d['color']}; font-size:16px;'>{d['signal']}</strong></div><div class='{summary_class}'>{d['tactical_summary']}</div></div>"
    st.markdown(html_str, unsafe_allow_html=True)
    
    with st.expander("📉 [今日最新] 戰損與多週期籌碼診斷", expanded=False):
        c1, c2 = st.columns(2)
        with c1:
            st.markdown(f"**[當日] 外資淨買賣超:** <span style='color:{'#ff4d4d' if d['f_buy']>0 else '#00FF00'}'>{d['f_buy']:,} 張 (佔比 {d['f_pct_today']:.1f}%)</span>", unsafe_allow_html=True)
            st.markdown(f"**[當日] 投信淨買賣超:** <span style='color:{'#ff4d4d' if d['t_buy']>0 else '#00FF00'}'>{d['t_buy']:,} 張 (佔比 {d['t_pct_today']:.1f}%)</span>", unsafe_allow_html=True)
            st.markdown(f"**[當日] 融資(散戶)增減:** <span style='color:{'#f1c40f'}'>{d['margin_diff']:,} 張 (佔比 {d['margin_pct_today']:.1f}%)</span>", unsafe_allow_html=True)
        with c2:
            mc = d['m_chips']
            st.markdown(f"**[近 5 日] 外資:** {mc['f_5d']:,} 張 ({d['f_pct_5d']:.1f}%) | **投信:** {mc['t_5d']:,} 張 ({d['t_pct_5d']:.1f}%)", unsafe_allow_html=True)
            st.markdown(f"**[近 10日] 外資:** {mc['f_10d']:,} 張 ({d['f_pct_10d']:.1f}%) | **投信:** {mc['t_10d']:,} 張 ({d['t_pct_10d']:.1f}%)", unsafe_allow_html=True)
            st.markdown(f"**[近 20日] 外資:** {mc['f_20d']:,} 張 ({d['f_pct_20d']:.1f}%) | **投信:** {mc['t_20d']:,} 張 ({d['t_pct_20d']:.1f}%)", unsafe_allow_html=True)
        st.markdown("<hr style='margin:10px 0; border:1px dashed #444;'>", unsafe_allow_html=True)
        st.markdown(f"<span style='font-size:14px;'>{d['ai_chip_summary']}</span>", unsafe_allow_html=True)

    ai_prompt = f"請以首席 AI 幕僚身分，深度解析以下標的並給出具體沙盤推演：\n【標的】{d['name']} ({d['code']})\n【現況】現價 {d['price']} (單日漲幅 {d['gain']:+.2f}%)\n【位階】{d['cost_label']}防守價 {d['cost']}\n【技術面】多空趨勢: {d['macd_str']} / KDJ: {d['kdj_str']} / 爆量比: {d['vol_ratio']:.1f}x\n【系統判定】{d['signal']}\n【戰情中樞短評】\n- 體質分數：{d['val_score']} 分 {d['val_shield']}\n\n總指揮指示：我目前想伏擊或持有該檔標的，請給我最冷血客觀的明日應對策略。"
    with st.expander(f"🤖 [傳送至 AI 幕僚] 點此展開 {d['name']} 專屬分析數據包", expanded=False):
        st.markdown("<span style='color:#00d2ff; font-size:13px;'>💡 請點擊下方區塊右上角的「複製圖示」，直接貼上與我對話：</span>", unsafe_allow_html=True)
        st.code(ai_prompt, language="markdown")

    if is_portfolio:
        with st.expander("⚙️ [庫存管理] 執行平倉", expanded=False):
            if st.button(f"🗑️ 單檔平倉刪除 {d['code']}", key=f"del_port_btn_{d['code']}", use_container_width=True):
                del st.session_state.portfolio[d['code']]; save_local_db(); st.rerun()
    elif ui_key_prefix.startswith('pin_'):
        with st.expander("⚙️ [雷達管理] 轉移模擬倉與刪除", expanded=False):
            st.markdown("📥 **轉換至模擬倉 (設定買進價與張數)**")
            c_ep, c_eq = st.columns(2)
            buy_p = c_ep.number_input("買進單價", value=float(d['price']), step=0.1, key=f"bp_{d['code']}")
            buy_q = c_eq.number_input("買進張數", value=1, min_value=1, step=1, key=f"bq_{d['code']}")
            if st.button("📥 [確認建立部位]", key=f"buy_{d['code']}", use_container_width=True):
                st.session_state.portfolio[d['code']] = {'entry_price': buy_p, 'qty': buy_q}
                del st.session_state.pinned_stocks[d['code']]; save_local_db(); st.rerun()
            st.markdown("---")
            if st.button(f"❌ 單檔刪除追蹤 {d['code']}", key=f"del_pin_btn_{d['code']}", use_container_width=True):
                del st.session_state.pinned_stocks[d['code']]; save_local_db(); st.rerun()

# ==========================================
# 9. 側邊欄控制台
# ==========================================
with st.sidebar:
    if st.button("🔄 [強制全域更新]", use_container_width=True, type="primary", key="update_top"):
        get_market_weather.clear(); get_stock_data.clear(); fetch_fundamentals.clear(); check_finmind_api_status.clear(); fetch_stockhomes_gooaye_intelligence.clear()
        fetch_twse_earnings_and_dividends.clear(); st.session_state.show_download = False; st.rerun()

    st.markdown("<h2 style='color:#f1c40f; text-align:center;'>⚙️ 戰略控制台</h2>", unsafe_allow_html=True)
    
    st.markdown("---")
    st.markdown("<h4 style='color:#00d2ff;'>🛡️ 末日鎔斷防護鎖</h4>", unsafe_allow_html=True)
    enable_doomsday_lock = st.checkbox("開啟防護 (大盤重挫時強制過濾買訊)", value=False)
    
    st.markdown("---")
    st.markdown("<h4 style='color:#00d2ff;'>🎙️ 股癌戰情雷達與大腦</h4>", unsafe_allow_html=True)
    st.markdown(f"""
    <div style='background:#1a1c23; padding:12px; border-radius:6px; border-left:4px solid #f1c40f; color:#ffffff; font-size:14px; line-height:1.6;'>
    🎙️ <b>逐字稿觀測站</b>: <span style='color:#aaa;'>stockhomes.org</span><br>
    🔥 <b>最新探測集數</b>: <strong style='color:#f1c40f;'>EP{LATEST_EPISODE}</strong><br>
    📦 <b>當前獵殺範圍</b>: <strong style='color:#00FF00;'>近 5 集 (EP{LATEST_EPISODE} ~ EP{LATEST_EPISODE-4})</strong><br>
    💎 <b>已解碼跨產業標的</b>: <strong style='color:#00d2ff;'>{len(GOOAYE_INTEL_DB)} 檔</strong>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("---")
    st.markdown("<h4 style='color:#00d2ff;'>📡 FinMind 智能補穿引擎</h4>", unsafe_allow_html=True)
    target_date = get_finmind_target_date()
    if st.session_state.inst_history:
        mem_dates = sorted(list(st.session_state.inst_history.keys()), reverse=True)
        if mem_dates[0] >= target_date: target_date = mem_dates[0]

    current_day_data = st.session_state.inst_history.get(target_date, {})
    missing_codes = [c for c in GLOBAL_MARKET_CODES if c not in current_day_data]
    total_codes = len(GLOBAL_MARKET_CODES)
    
    db_days = len(st.session_state.inst_history)
    db_full_days = sum(1 for d in st.session_state.inst_history.values() if len(d) >= total_codes - 50)
    st.markdown(f"""
    <div style='font-size:14px; color:#aaa; margin-bottom:10px; background:#1a1c23; padding:10px; border-radius:5px;'>
    📊 資料庫完整度: <strong style='color:#f1c40f; font-size:16px;'>{db_full_days}/{db_days}</strong><br>
    📅 最新交易日: <strong style='color:#00FF00;'>{target_date}</strong><br>
    ⚠️ 目前缺漏檔數: <strong style='color:#ff4d4d;'>{len(missing_codes)}</strong> 檔
    </div>
    """, unsafe_allow_html=True)
    
    if missing_codes:
        if st.button("🚀 [一鍵執行遺失補齊]", use_container_width=True, type="primary"):
            bar = st.progress(0); status_text = st.empty(); success_count = fail_count = 0
            target_codes_to_fetch = missing_codes[:300]
            start_date_query = (datetime.now() - timedelta(days=20)).strftime('%Y-%m-%d')
            current_token_idx = 0
            for i, code in enumerate(target_codes_to_fetch):
                status_text.text(f"📡 鎖定目標: {code} ({i+1}/{len(target_codes_to_fetch)})...")
                success_for_code = False
                while current_token_idx < len(FINMIND_TOKENS):
                    token = FINMIND_TOKENS[current_token_idx]
                    url = 'https://api.finmindtrade.com/api/v4/data'
                    params = {'dataset': 'TaiwanStockInstitutionalInvestorsBuySell', 'data_id': code, 'start_date': start_date_query}
                    if token: params['token'] = token
                    try:
                        res = requests.get(url, params=params, timeout=5)
                        if res.status_code == 200 and res.json().get('msg') == 'success':
                            if target_date not in st.session_state.inst_history: st.session_state.inst_history[target_date] = {}
                            if code not in st.session_state.inst_history[target_date]: st.session_state.inst_history[target_date][code] = {'foreign': 0, 'trust': 0}
                            df = pd.DataFrame(res.json().get('data', []))
                            if not df.empty:
                                df['net'] = pd.to_numeric(df['buy'], errors='coerce').fillna(0) - pd.to_numeric(df['sell'], errors='coerce').fillna(0)
                                pivoted = df.pivot_table(index='date', columns='name', values='net', aggfunc='sum').sort_index(ascending=False)
                                f_series = pivoted['Foreign_Investor'].fillna(0) if 'Foreign_Investor' in pivoted.columns else pivoted[[c for c in pivoted.columns if 'Foreign' in c or '外資' in c]].sum(axis=1) if [c for c in pivoted.columns if 'Foreign' in c or '外資' in c] else pd.Series(dtype=float)
                                t_series = pivoted['Investment_Trust'].fillna(0) if 'Investment_Trust' in pivoted.columns else pivoted[[c for c in pivoted.columns if 'Trust' in c or '投信' in c]].sum(axis=1) if [c for c in pivoted.columns if 'Trust' in c or '投信' in c] else pd.Series(dtype=float)
                                if not f_series.empty: st.session_state.inst_history[target_date][code]['foreign'] = int(f_series.iloc[0] / 1000)
                                if not t_series.empty: st.session_state.inst_history[target_date][code]['trust'] = int(t_series.iloc[0] / 1000)
                            success_for_code = True; break 
                        else: current_token_idx += 1
                    except Exception: current_token_idx += 1
                if success_for_code: 
                    success_count += 1
                    if success_count % 20 == 0: save_local_db()
                else:
                    fail_count += 1
                    if current_token_idx >= len(FINMIND_TOKENS): break
                bar.progress(min((i + 1) / len(target_codes_to_fetch), 1.0)); time.sleep(0.1)
            status_text.empty(); save_local_db(); st.success(f"✅ 補齊完畢！成功: {success_count} 檔 | 失敗: {fail_count} 檔。"); time.sleep(2); st.rerun()

    st.markdown("---")
    st.markdown("<h4 style='color:#00d2ff;'>💾 戰情備份與還原大腦</h4>", unsafe_allow_html=True)
    if st.button("📦 1. 打包目前記憶體", use_container_width=True):
        st.session_state.export_json = json.dumps({"pinned_stocks": st.session_state.pinned_stocks, "portfolio": st.session_state.portfolio, "inst_history": st.session_state.inst_history}, ensure_ascii=False, indent=4)
        if 'backup_counter' not in st.session_state: st.session_state.backup_counter = 1
        else: st.session_state.backup_counter += 1
        st.session_state.download_filename = f"{datetime.now().strftime('%Y-%m%d')}_{st.session_state.backup_counter}.json"
        st.session_state.show_download = True
        st.success(f"✅ 打包完成！檔名: {st.session_state.download_filename}")
        
    if st.session_state.get('show_download', False):
        st.download_button(label="⬇️ 2. 點此下載 JSON 備份", data=st.session_state.export_json, file_name=st.session_state.download_filename, mime="application/json", use_container_width=True, type="primary")

    uploaded_file = st.file_uploader("📤 上傳戰情備份 (還原記憶)", type=['json'])
    if uploaded_file is not None:
        if st.button("⚠️ [確認覆蓋並還原記憶體]", use_container_width=True):
            try:
                raw_json = uploaded_file.getvalue().decode("utf-8")
                imported_data = json.loads(raw_json)
                st.session_state.pinned_stocks = imported_data.get("pinned_stocks", {})
                st.session_state.portfolio = imported_data.get("portfolio", {})
                if "inst_history" in imported_data: st.session_state.inst_history = imported_data["inst_history"] 
                save_local_db(); st.success("✅ 實體備份資料還原成功！請按『強制全域更新』刷新畫面。")
            except Exception as e: st.error(f"檔案解析失敗: {e}")

    st.markdown("---")
    intel_input = st.text_area("🔍 雷達手動匯入 (輸入代碼或名稱)", placeholder="如：2330 聯電 加高...\n也可直接貼上 AI 報告內容")
    if st.button("🚀 [強制解析並匯入雷達]", use_container_width=True):
        if intel_input.strip():
            found_codes = set(re.findall(r'\b\d{4}\b', intel_input))
            for code, name in TW_STOCK_NAMES.items():
                if name in intel_input and len(name) >= 2: found_codes.add(code)
            if found_codes:
                for c in found_codes: st.session_state.pinned_stocks[c] = {}
                save_local_db(); st.rerun()
            else: st.warning("⚠️ 找不到對應的股票代碼或名稱。")

    st.markdown("---")
    # 💥 V131 [除權息尋寶設定區]
    st.markdown("<h4 style='color:#00d2ff;'>💎 除權息尋寶設定 (搭配指令十一)</h4>", unsafe_allow_html=True)
    div_time_filter = st.selectbox("除權息時間過濾", ["近 15 日即將除權息", "近 30 日即將除權息", "不限時間"])
    div_type_filter = st.selectbox("股利發放類型", ["現金與股票皆可", "僅限配發現金股利"])
    min_yield_filter = st.slider("最低現金殖利率 (%)", 0.0, 15.0, 4.0, 0.5)

    st.markdown("---")
    scan_scope = st.selectbox("🌐 掃描範圍", ["全市場 1700+ 檔", "電子/半導體/光電", "生技醫療", "金融保險", "航運/汽車", "觀光/民生百貨", "傳產/營造/鋼鐵"])
    min_volume_filter = st.slider("⚖️ 最低 5 日均量 (張)：", 0, 5000, 500, 100)

    def get_scope_codes(scope):
        if "全市場" in scope: return GLOBAL_MARKET_CODES
        elif "電子" in scope: return [c for c in GLOBAL_MARKET_CODES if c.startswith(('23','24','30','31','32','33','34','35','36','49','52','53','54','61','62','64','80','81','82'))]
        elif "生技" in scope: return [c for c in GLOBAL_MARKET_CODES if c.startswith(('17', '41', '47', '65'))]
        elif "金融" in scope: return [c for c in GLOBAL_MARKET_CODES if c.startswith(('28', '58'))]
        elif "航運" in scope: return [c for c in GLOBAL_MARKET_CODES if c.startswith(('22', '26'))]
        elif "觀光" in scope: return [c for c in GLOBAL_MARKET_CODES if c.startswith(('27', '29', '12', '14'))] 
        elif "傳產" in scope: return [c for c in GLOBAL_MARKET_CODES if c.startswith(('11', '13', '15', '16', '20', '25'))]
        return GLOBAL_MARKET_CODES

    def run_command_scan(cmd_name, scope, min_vol):
        results = []
        codes = get_scope_codes(scope)
        bar = st.progress(0)
        status = st.empty()
        invalid_signals = ["[📉 空頭觀望]", "[高檔觀望]", "[⚠️ 拉回整理]", "[💀 觸發停損]", "[🚨 撤退警告]", "[⚠️ 盤整洗盤陷阱]"]
        for i, c in enumerate(codes):
            if i % 3 == 0: status.text(f"雷達鎖定與過濾中... ({i}/{len(codes)})")
            d = calculate_signals(c, get_stock_data(c), is_panic_global=is_panic, twii_gain=global_twii_gain, is_scan=True)
            
            if enable_doomsday_lock and is_panic:
                if d and d['vol_5d'] >= min_vol and not d['is_action_needed'] and d['signal'] not in invalid_signals:
                    if d.get('rev_growth', 0) > 20.0: pass 
                    else: d = None

            if d and d['vol_5d'] >= min_vol and not d['is_action_needed']: 
                # 💥 V131: 指令十一過濾邏輯
                if cmd_name == "指令十一":
                    if d['div_cash'] > 0 and d['div_yield'] >= min_yield_filter:
                        is_valid = True
                        if "僅限配發現金" in div_type_filter and d['div_stock'] > 0: is_valid = False
                        match = re.search(r'(\d+)年(\d+)月(\d+)日', d['div_date_raw'])
                        if match and is_valid:
                            y, m, day = int(match.group(1)) + 1911, int(match.group(2)), int(match.group(3))
                            try:
                                div_date = datetime(y, m, day)
                                days_to_div = (div_date - datetime.now()).days
                                if "15 日" in div_time_filter and not (0 <= days_to_div <= 15): is_valid = False
                                if "30 日" in div_time_filter and not (0 <= days_to_div <= 30): is_valid = False
                                if days_to_div < 0: is_valid = False 
                            except: pass
                        elif "不限" not in div_time_filter: is_valid = False
                        if is_valid: results.append(d)
                
                elif d['signal'] not in invalid_signals:
                    if cmd_name == "指令一" and d['is_first_red'] and d['is_vol_breakout'] and ("金叉" in d['kdj_str'] or "金叉" in d['macd_str']): results.append(d)
                    elif cmd_name == "指令二" and (d['price'] > d['cost']) and (d['gain'] < 2.0) and (d['price'] < d['cost'] * 1.1) and (d['vol_ratio'] >= 1.2): results.append(d)
                    elif cmd_name == "指令三" and d['val_score'] >= 60: results.append(d)
                    elif cmd_name == "指令四" and d['t_buy'] > 0 and any("集團" in t['text'] or "熱門" in t.get('title','') for t in d.get('ai_tags_dict', [])): results.append(d) 
                    elif cmd_name == "指令五" and d['f_buy'] > 0 and d['margin_diff'] < 0: results.append(d) 
                    elif cmd_name == "指令六" and any("盾牌" in t['text'] for t in d.get('ai_tags_dict', [])): results.append(d)
                    elif cmd_name == "指令七" and c in GOOAYE_INTEL_DB: results.append(d)
                    elif cmd_name == "指令八" and d['is_yesterday_strong']: results.append(d)
                    elif cmd_name == "指令九" and any("糾結" in t.get('title', '') for t in d.get('ai_tags_dict', [])): results.append(d)
                    elif cmd_name == "指令十" and d['vol_ratio'] <= 0.6 and d['margin_diff'] < 0: results.append(d)
                    elif cmd_name == "常規": results.append(d)
            bar.progress(min((i + 1) / len(codes), 1.0))
        bar.empty(); status.empty()
        return results

    with st.expander("📖 [戰術總覽說明書] 點此展開", expanded=False):
        st.markdown("""
        * **💎 [指令十一] 除權息尋寶雷達：** 配合上方設定，篩選即將除息的高殖利率股。
        * **🎙️ [指令七] 股癌戰情雷達：** 鎖定 stockhomes 最新集數點名個股。
        * **⚔️ [指令一] 主升段突擊：** 同時滿足金叉、爆量上攻，且為起漲第一根。
        * **🐟 [指令二] 魚頭潛伏期：** 長線站穩季線，近期盤整貼近支撐且增量。
        * **🔄 [指令三] 價值投資與循環：** 價值分數大於 60 分 (低 PE/PB、高殖利率)。
        * **🔥 [指令四] 投信作帳集團股：** 鎖定「投信買超」＋「所屬大型集團」。
        * **💪 [指令五] 籌碼霸王色：** 「外資連買3天以上」且「融資減少」的集中股。
        * **📈 [指令六] 營收雙增爆發：** 單月營收高成長(>20%)。
        * **⚡ [指令八] 昨日強勢延續：** 前一交易日漲幅超過 5%。
        * **🎯 [指令九] 均線糾結突破：** 5/10/20日均線黏合且放量突破。
        * **🤫 [指令十] 籌碼沉澱量縮：** 成交量急縮至均量60%以下，且融資減少。
        * **🔎 [常規掃描] 黃金起漲與魚身：** 過濾破線股，保留所有安全標的。
        """, unsafe_allow_html=True)

    st.markdown("<div class='cmd-btn'>", unsafe_allow_html=True)
    if st.button("💎 [指令十一] 除權息尋寶雷達", use_container_width=True):
        st.session_state.scan_results = run_command_scan("指令十一", scan_scope, min_volume_filter)
        st.session_state.scan_mode = "cmd_11"
    if st.button("🎙️ [指令七] 股癌戰情雷達", use_container_width=True):
        st.session_state.scan_results = run_command_scan("指令七", scan_scope, min_volume_filter)
        st.session_state.scan_mode = "cmd_7"
    if st.button("⚔️ [指令一] 主升段突擊", use_container_width=True):
        st.session_state.scan_results = run_command_scan("指令一", scan_scope, min_volume_filter)
        st.session_state.scan_mode = "cmd_1"
    if st.button("🐟 [指令二] 魚頭潛伏期", use_container_width=True):
        st.session_state.scan_results = run_command_scan("指令二", scan_scope, min_volume_filter)
        st.session_state.scan_mode = "cmd_2"
    if st.button("🔄 [指令三] 價值投資與循環", use_container_width=True):
        st.session_state.scan_results = run_command_scan("指令三", scan_scope, min_volume_filter)
        st.session_state.scan_mode = "cmd_3"
    if st.button("🔥 [指令四] 投信作帳集團股", use_container_width=True):
        st.session_state.scan_results = run_command_scan("指令四", scan_scope, min_volume_filter)
        st.session_state.scan_mode = "cmd_4"
    if st.button("💪 [指令五] 籌碼霸王色", use_container_width=True):
        st.session_state.scan_results = run_command_scan("指令五", scan_scope, min_volume_filter)
        st.session_state.scan_mode = "cmd_5"
    if st.button("📈 [指令六] 營收雙增爆發", use_container_width=True):
        st.session_state.scan_results = run_command_scan("指令六", scan_scope, min_volume_filter)
        st.session_state.scan_mode = "cmd_6"
    if st.button("⚡ [指令八] 昨日強勢延續", use_container_width=True):
        st.session_state.scan_results = run_command_scan("指令八", scan_scope, min_volume_filter)
        st.session_state.scan_mode = "cmd_8"
    if st.button("🎯 [指令九] 均線糾結突破", use_container_width=True):
        st.session_state.scan_results = run_command_scan("指令九", scan_scope, min_volume_filter)
        st.session_state.scan_mode = "cmd_9"
    if st.button("🤫 [指令十] 籌碼沉澱量縮", use_container_width=True):
        st.session_state.scan_results = run_command_scan("指令十", scan_scope, min_volume_filter)
        st.session_state.scan_mode = "cmd_10"
    st.markdown("</div>", unsafe_allow_html=True)
    
    st.markdown("<div class='scan-btn'>", unsafe_allow_html=True)
    if st.button("🔎 [常規掃描] 黃金起漲與魚身", use_container_width=True):
        st.session_state.scan_results = run_command_scan("常規", scan_scope, min_volume_filter)
        st.session_state.scan_mode = "golden"
    st.markdown("</div>", unsafe_allow_html=True)

    st.markdown("<h4 style='color:#00FF00 !important; margin-top:20px; text-align:center;'>🗄️ 系統連線狀態</h4>", unsafe_allow_html=True)
    with st.expander("📡 FinMind 籌碼管線狀態"):
        fm_statuses = check_finmind_api_status(FINMIND_TOKENS)
        status_html = "<div style='font-size:12px;'>"
        for s in fm_statuses:
            color_class = "key-status-ok" if s['status'] == "OK" else "key-status-fail"
            status_html += f"<div>Key ({s['key']}): <span class='{color_class}'>{s['msg']}</span></div>"
        status_html += "</div>"
        st.markdown(status_html, unsafe_allow_html=True)

    with st.expander("🔑 Google AI 金鑰狀態"):
        key_statuses = check_api_keys(GEMINI_API_KEYS, st.session_state.ai_mode)
        status_html = "<div style='font-size:12px;'>"
        for s in key_statuses:
            color_class = "key-status-ok" if s['status'] == "OK" else "key-status-fail"
            status_html += f"<div>Key #{s['index']} ({s['key']}): <span class='{color_class}'>{s['msg']}</span></div>"
        status_html += "</div>"
        st.markdown(status_html, unsafe_allow_html=True)

    st.markdown("---")
    if st.button("🚪 [安全登出系統]", use_container_width=True):
        st.session_state.authenticated = False
        if "auth" in st.query_params: del st.query_params["auth"]
        st.rerun()

    st.markdown("<br><br>", unsafe_allow_html=True)
    if st.button("🔄 [強制全域更新]", use_container_width=True, type="primary", key="update_bottom"):
        get_market_weather.clear(); get_stock_data.clear(); fetch_fundamentals.clear(); check_finmind_api_status.clear()
        fetch_stockhomes_gooaye_intelligence.clear(); fetch_twse_earnings_and_dividends.clear()
        st.session_state.temp_intel = []; st.session_state.show_download = False; st.rerun()

# ==========================================
# 10. 畫面主架構渲染
# ==========================================
col_nav1, col_nav2 = st.columns([8, 2])
with col_nav1: st.markdown("<h1 style='color:#FFB300; margin: 0;'>🚀 54088 戰情室 V131</h1>", unsafe_allow_html=True)

port_loaded_cards, pin_loaded_cards = {}, {}
for code, p in st.session_state.portfolio.items():
    d = calculate_signals(code, get_stock_data(code), portfolio_data=p, is_panic_global=is_panic, twii_gain=global_twii_gain, is_scan=False)
    if d: port_loaded_cards[code] = d
for code in st.session_state.pinned_stocks:
    d = calculate_signals(code, get_stock_data(code), portfolio_data=None, is_panic_global=is_panic, twii_gain=global_twii_gain, is_scan=False)
    if d: pin_loaded_cards[code] = d

total_unrealized = 0
for code, d in port_loaded_cards.items():
    p_profit, _, _, _, _ = calc_real_profit(st.session_state.portfolio[code]['entry_price'], d['price'], st.session_state.portfolio[code]['qty'])
    total_unrealized += p_profit

hotspot_html = ""
if st.session_state.scan_results:
    sectors = [d['sector'] for d in st.session_state.scan_results if d and 'sector' in d]
    if len(sectors) > 0:
        top_sectors = Counter(sectors).most_common(3)
        hotspot_str = " | ".join([f"{s[0]} ({int(s[1]/len(sectors)*100)}%)" for s in top_sectors])
        hotspot_html = f"<div style='margin-top:10px; background:#3a1515; border:1px solid #ff4d4d; padding:8px; border-radius:5px; color:#fff; font-size:14px;'><strong style='color:#ff4d4d;'>🚨 [雷達資金熱區]</strong> {hotspot_str}</div>"

st.markdown(f"""<div class='hud-box'><div class='hud-title'><span>📊 大將軍戰情總覽 (HUD)</span></div><div style='background:#1a1c23; padding:10px; border-radius:5px; border-left:3px solid {weather_color}; margin-bottom:10px; font-size:14px; color:#ddd;'><strong>[今日大盤風向]</strong> {weather_str}</div><div style='background:#1a1c23; padding:10px; border-radius:5px; border-left:3px solid #f1c40f; margin-bottom:10px; font-size:14px; color:#ddd;'><strong>[📅 季節作帳行事曆]</strong> {quarter_info}</div><div class='hud-metric'><span style='color:#aaa;'>📦 庫存 / 雷達數量</span> <strong style='color:#fff;'>{len(port_loaded_cards)} / {len(pin_loaded_cards)} 檔</strong></div><div class='hud-metric'><span style='color:#aaa;'>💰 總未實現淨損益</span> <strong style='color:{'#ff4d4d' if total_unrealized>=0 else '#00FF00'}; font-size:18px;'>{int(total_unrealized):+,.0f} 元</strong></div>{hotspot_html}</div>""", unsafe_allow_html=True)

# --- 模擬倉 ---
if st.session_state.portfolio:
    with st.expander("💼 總指揮持倉 (模擬倉)", expanded=False):
        st.markdown(f"<div style='color:#f1c40f; margin-bottom:10px; font-weight:bold; font-size: 16px;'>📦 目前持有 {len(st.session_state.portfolio)} 檔</div>", unsafe_allow_html=True)
        st.markdown("<div style='background:#1a1c23; padding:10px; border-radius:6px; border:1px solid #ff4d4d; margin-bottom:15px;'>", unsafe_allow_html=True)
        jump_port = st.multiselect("🔍 快速尋找 (下拉選擇持倉標的以濾出單檔)", options=list(st.session_state.portfolio.keys()), format_func=lambda x: f"{x} {TW_STOCK_NAMES.get(x, x)}")
        del_port_cols = st.columns([8, 2])
        with del_port_cols[0]: port_to_del = st.multiselect("🗑️ 批次平倉 (點此下拉選擇)", options=list(st.session_state.portfolio.keys()), format_func=lambda x: f"{x} {TW_STOCK_NAMES.get(x, x)}")
        with del_port_cols[1]:
            st.write("")
            if st.button("🗑️ 執行平倉", use_container_width=True) and port_to_del:
                for c in port_to_del: del st.session_state.portfolio[c]
                save_local_db(); st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)

        for code, p_data in list(st.session_state.portfolio.items()):
            if jump_port and code not in jump_port: continue 
            d = port_loaded_cards.get(code)
            if d:
                prof, pct, fb, fs, tax = calc_real_profit(p_data['entry_price'], d['price'], p_data['qty'])
                prof_emoji = '🔴' if prof > 0 else ('🟢' if prof < 0 else '⚪')
                with st.expander(f"{prof_emoji} {d['name']} ({d['code']}) | 淨損益: {int(prof):+,} 元", expanded=False):
                    draw_card(d, f"port_{code}", is_portfolio=True, p_data=p_data)

# --- 觀測雷達 ---
if st.session_state.pinned_stocks:
    with st.expander("🎯 觀測雷達", expanded=True):
        st.markdown(f"<div style='color:#00d2ff; margin-bottom:10px; font-weight:bold; font-size: 16px;'>📡 目前追蹤 {len(st.session_state.pinned_stocks)} 檔</div>", unsafe_allow_html=True)
        st.markdown("<div style='background:#1a1c23; padding:10px; border-radius:6px; border:1px solid #ff4d4d; margin-bottom:15px;'>", unsafe_allow_html=True)
        jump_pin = st.multiselect("🔍 快速尋找 (下拉選擇雷達標的以濾出單檔)", options=list(st.session_state.pinned_stocks.keys()), format_func=lambda x: f"{x} {TW_STOCK_NAMES.get(x, x)}")
        del_pin_cols = st.columns([8, 2])
        with del_pin_cols[0]: pin_to_del = st.multiselect("🗑️ 批次刪除追蹤 (點此下拉選擇)", options=list(st.session_state.pinned_stocks.keys()), format_func=lambda x: f"{x} {TW_STOCK_NAMES.get(x, x)}")
        with del_pin_cols[1]:
            st.write("")
            if st.button("🗑️ 執行刪除", use_container_width=True) and pin_to_del:
                for c in pin_to_del: del st.session_state.pinned_stocks[c]
                save_local_db(); st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)

        all_pin_tags = set()
        for code, d in pin_loaded_cards.items():
            if d: 
                for t in d.get('ai_tags_dict', []): all_pin_tags.add(t['text'])
        pin_selected_tags = []
        if all_pin_tags: pin_selected_tags = st.multiselect("🏷️ 標籤濾網 (觀測雷達)", sorted(list(all_pin_tags)), placeholder="點此選擇標籤進行過濾... (留空則顯示全部)")

        cols = st.columns(2)
        idx = 0
        for code in list(st.session_state.pinned_stocks.keys()):
            if jump_pin and code not in jump_pin: continue 
            d = pin_loaded_cards.get(code)
            if d:
                card_tags = [t['text'] for t in d.get('ai_tags_dict', [])]
                if not pin_selected_tags or any(t in card_tags for t in pin_selected_tags):
                    with cols[idx % 2]: 
                        draw_card(d, f"pin_{code}")
                    idx += 1

# --- 掃描結果區 ---
if st.session_state.get('scan_mode'):
    st.markdown("<h2 style='color:#00d2ff;'>⚡ 初篩結果</h2>", unsafe_allow_html=True)
    all_scan_tags = set()
    for d in st.session_state.scan_results:
        if d: 
            for t in d.get('ai_tags_dict', []): all_scan_tags.add(t['text'])
    scan_selected_tags = []
    if all_scan_tags: scan_selected_tags = st.multiselect("🏷️ 標籤濾網 (初篩區)", sorted(list(all_scan_tags)), placeholder="點此選擇標籤進行過濾... (留空則顯示全部)")
    filtered_scan_results = []
    for d in st.session_state.scan_results:
        if d:
            card_tags = [t['text'] for t in d.get('ai_tags_dict', [])]
            if not scan_selected_tags or any(t in card_tags for t in scan_selected_tags):
                filtered_scan_results.append(d)

    st.markdown("<div style='background:#10141d; padding:15px; border-radius:6px; border:1px solid #00d2ff; margin-bottom:15px;'>", unsafe_allow_html=True)
    if st.button("🤖 [AI 幕僚] 懶人戰術打包 (請 AI 從下方清單挑選最精銳 3-5 檔)", type="primary", use_container_width=True):
        with st.spinner("AI 幕僚正在深度解析籌碼與型態..."):
            st.session_state.ai_report = generate_ai_report(st.session_state.scan_mode, filtered_scan_results)
    if st.session_state.get('ai_report'):
        st.markdown(f"<div class='ai-report-box'>{st.session_state.ai_report}</div>", unsafe_allow_html=True)
        st.markdown("<p style='color:#00d2ff; font-weight:bold;'>👇 快速複製區 (請點擊右上方圖示複製，貼至側邊欄匯入)</p>", unsafe_allow_html=True)
        st.code(st.session_state.ai_report, language="markdown")
    st.markdown("</div>", unsafe_allow_html=True)

    st.markdown("<div style='background:#10141d; padding:10px; border-radius:6px; border:1px solid #333; margin-bottom:15px;'>", unsafe_allow_html=True)
    if st.button("➕ 將下方【已勾選】標的批次加入雷達", use_container_width=True):
        added_count = 0
        for d in filtered_scan_results:
            if d and st.session_state.get(f"chk_batch_{d['code']}", False):
                st.session_state.pinned_stocks[d['code']] = {}
                added_count += 1
        if added_count > 0: save_local_db(); st.rerun()
    st.markdown("</div>", unsafe_allow_html=True)

    cols = st.columns(2)
    idx = 0
    for d in filtered_scan_results:
        if d['code'] not in st.session_state.portfolio and d['code'] not in st.session_state.pinned_stocks:
            with cols[idx % 2]:
                st.checkbox(f"✅ 勾選追蹤 {d['code']} {d['name']}", key=f"chk_batch_{d['code']}")
                draw_card(d, f"scan_{idx}")
            idx += 1
