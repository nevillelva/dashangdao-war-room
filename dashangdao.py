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

warnings.filterwarnings('ignore')

# ==========================================
# 基礎配置與狀態初始化
# ==========================================
st.set_page_config(layout="wide", page_title="54088 戰情室 V111.0", initial_sidebar_state="expanded")

st.toast("✅ [系統提示] V111.0 籌碼管線絕對對齊版 啟動成功！")

try:
    COMMANDER_PIN = st.secrets["radar_secrets"]["commander_pin"]
    raw_keys = st.secrets["radar_secrets"]["gemini_api_key"]
    GEMINI_API_KEYS = [k.strip() for k in raw_keys.split(",") if k.strip()]
    SECRET_FINMIND = st.secrets["radar_secrets"].get("finmind_token", "")
    SUPABASE_URL = st.secrets["radar_secrets"].get("supabase_url", "").strip()
    SUPABASE_KEY = st.secrets["radar_secrets"].get("supabase_key", "").strip()
except KeyError:
    st.error("❌ [致命錯誤] 雲端保險箱 (Secrets) 未設定或設定錯誤！請檢查 Streamlit Cloud 後台設定。")
    st.stop()

USER_DB_FILE = "54088_database.json" 
FUNDAMENTALS_DB_FILE = "54088_fundamentals_cache.json"
INST_HISTORY_FILE = "54088_inst_history_v2.json"

if 'ai_mode' not in st.session_state: st.session_state.ai_mode = "⚡ 快速 (Flash)"
if 'scan_results' not in st.session_state: st.session_state.scan_results = []
if 'scan_mode' not in st.session_state: st.session_state.scan_mode = ""
if 'ai_report' not in st.session_state: st.session_state.ai_report = ""
if 'temp_intel' not in st.session_state: st.session_state.temp_intel = []
if 'portfolio' not in st.session_state: st.session_state.portfolio = {}
if 'pinned_stocks' not in st.session_state: st.session_state.pinned_stocks = {}
if 'active_key_index' not in st.session_state: st.session_state.active_key_index = 0
if 'export_data' not in st.session_state: st.session_state.export_data = ""

now_month = datetime.now().month
if now_month in [1, 2, 3]: quarter_info = "🌱 第一季 (Q1作帳) | 佈局窗：2月中旬至3月初 | 撤退線：3月底前最後四天"
elif now_month in [4, 5, 6]: quarter_info = "☀️ 第二季 (Q2作帳) | 佈局窗：5月中旬至6月初 | 撤退線：6月底前最後四天"
elif now_month in [7, 8, 9]: quarter_info = "🍁 第三季 (Q3作帳) | 佈局窗：8月中旬至9月初 | 撤退線：9月底前最後四天"
else: quarter_info = "❄️ 第四季 (Q4作帳) | 佈局窗：11月中旬至12月初 | 撤退線：12月底前最後四天"

# ==========================================
# 雲端資料庫 Supabase 讀寫模組 
# ==========================================
def load_db():
    if SUPABASE_URL and SUPABASE_KEY:
        try:
            headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
            res = requests.get(f"{SUPABASE_URL}/rest/v1/user_data?id=eq.1", headers=headers, timeout=5)
            if res.status_code == 200 and len(res.json()) > 0:
                db_data = res.json()[0].get("data", {})
                st.session_state.pinned_stocks = db_data.get("pinned_stocks", {})
                st.session_state.portfolio = db_data.get("portfolio", {})
                cloud_inst_history = db_data.get("inst_history", {})
                if cloud_inst_history:
                    with open(INST_HISTORY_FILE, "w", encoding="utf-8") as f:
                        json.dump(cloud_inst_history, f, ensure_ascii=False)
                return True
        except Exception: pass
    
    if os.path.exists(USER_DB_FILE):
        try:
            with open(USER_DB_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                st.session_state.pinned_stocks = data.get("pinned_stocks", {})
                st.session_state.portfolio = data.get("portfolio", {})
        except Exception: pass
    return False

if 'db_loaded' not in st.session_state:
    load_db()
    st.session_state.db_loaded = True

def save_db():
    local_inst_history = {}
    if os.path.exists(INST_HISTORY_FILE):
        try:
            with open(INST_HISTORY_FILE, "r", encoding="utf-8") as f:
                local_inst_history = json.load(f)
        except Exception: pass

    payload = {
        "pinned_stocks": st.session_state.pinned_stocks, 
        "portfolio": st.session_state.portfolio,
        "inst_history": local_inst_history
    }
    
    if SUPABASE_URL and SUPABASE_KEY:
        try:
            headers = {
                "apikey": SUPABASE_KEY, 
                "Authorization": f"Bearer {SUPABASE_KEY}",
                "Content-Type": "application/json",
                "Prefer": "return=minimal, resolution=merge-duplicates"
            }
            body = {"id": 1, "data": payload}
            requests.post(f"{SUPABASE_URL}/rest/v1/user_data", headers=headers, json=body, timeout=5)
        except Exception: pass
        
    try:
        with open(USER_DB_FILE, "w", encoding="utf-8") as f: 
            json.dump(payload, f, ensure_ascii=False, indent=4)
    except Exception: pass

if 'authenticated' not in st.session_state: 
    st.session_state.authenticated = (st.query_params.get("auth") == "54088")

if not st.session_state.authenticated:
    st.markdown("<h2 style='text-align: center; color: #444; margin-top: 10vh; letter-spacing: 5px;'>🔒 SYSTEM LOCKED</h2>", unsafe_allow_html=True)
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        pwd = st.text_input("輸入授權密碼", type="password")
        if st.button("解鎖系統 🔓", use_container_width=True):
            if pwd == COMMANDER_PIN: 
                st.session_state.authenticated = True
                st.rerun()
            else: st.error("❌ 密碼錯誤")
    st.stop()

st.markdown("""<style>
.stApp { background-color: #0b0c0f !important; color: #fff !important; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
div[data-testid="stSidebar"] { background-color: #12141a !important; border-right: 1px solid #333 !important; }
div[data-testid="stButton"] > button { background-color: #1e1e24 !important; border: 1px solid #444 !important; transition: all 0.2s ease-in-out; }
div[data-testid="stButton"] > button p { color: #ffffff !important; font-weight: bold !important; font-size: 15px !important; }
.scan-btn div[data-testid="stButton"] > button { background-color: #3a1515 !important; border: 2px solid #ff4d4d !important; margin-bottom: 5px;}
.scan-btn div[data-testid="stButton"] > button p { color: #ff4d4d !important; font-weight: bold !important; }
.cmd-btn div[data-testid="stButton"] > button { background-color: #15203a !important; border: 2px solid #00d2ff !important; margin-bottom: 5px;}
.cmd-btn div[data-testid="stButton"] > button p { color: #00d2ff !important; font-weight: bold !important; }
.event-btn div[data-testid="stButton"] > button { background-color: #2a153a !important; border: 2px solid #d200ff !important; margin-bottom: 5px;}
.event-btn div[data-testid="stButton"] > button p { color: #d200ff !important; font-weight: bold !important; }
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
</style>""", unsafe_allow_html=True)

# ==========================================
# 核心計算與資料函數
# ==========================================
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

def get_safe_session():
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Cache-Control": "no-cache"
    })
    return session

def safe_float(val):
    if pd.isna(val) or val is None or str(val).strip() == '': return 0.0
    try:
        s = str(val).upper().replace(',', '').replace('-', '').strip()
        s = re.sub(r'[^\d.]', '', s)
        return float(s) if s else 0.0
    except Exception: return 0.0

@st.cache_data(ttl=86400, show_spinner=False)
def fetch_stock_names():
    api_names = {}
    try:
        res = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL", timeout=5, headers={"User-Agent": "Mozilla/5.0"})
        if res.status_code == 200:
            for item in res.json():
                c = str(item.get('Code', '')).strip()
                n = str(item.get('Name', '')).strip()
                if len(c) == 4 and c.isdigit() and n: api_names[c] = n
    except Exception: pass
    try:
        res2 = requests.get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_peratio_analysis", timeout=5, headers={"User-Agent": "Mozilla/5.0"})
        if res2.status_code == 200:
            for item in res2.json():
                c = str(item.get('SecuritiesCompanyCode', '')).strip()
                n = str(item.get('CompanyName', '')).strip()
                if len(c) == 4 and c.isdigit() and n: api_names[c] = n
    except Exception: pass
    fallbacks = {"2330":"台積電", "2303":"聯電", "2317":"鴻海", "2454":"聯發科", "2382":"廣達", "2603":"長榮", "1519":"華城", "3017":"奇鋐", "3324":"雙鴻", "2313":"華通", "3231":"緯創", "2356":"英業達", "3008":"大立光"}
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
    except Exception: pass
    return symbol

@st.cache_data(ttl=3600, show_spinner=False)
def fetch_margin_data():
    margin_db = {}
    try:
        res = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/MI_MARGN_ALL", timeout=5, headers={"User-Agent": "Mozilla/5.0"})
        if res.status_code == 200:
            for item in res.json():
                margin_db[str(item.get('Code', '')).strip()] = safe_float(item.get('MarginPurchaseDifference'))
    except Exception: pass
    return margin_db

# ==========================================
# [V111.0 絕對修復] 法人籌碼 API 欄位強制對齊
# ==========================================
@st.cache_data(ttl=3600, show_spinner=False)
def fetch_institutional_data():
    inst_db = {}
    
    # 1. 台灣證交所 (上市) T86_ALL
    try:
        res = requests.get("https://openapi.twse.com.tw/v1/fund/T86_ALL", timeout=5, headers={"User-Agent": "Mozilla/5.0"})
        if res.status_code == 200:
            for item in res.json():
                code = str(item.get('Code', '')).strip()
                # 官方欄位為 ForeignTradeShares (股數), 需除以 1000 換算張數
                f_shares = safe_float(item.get('ForeignTradeShares', 0))
                t_shares = safe_float(item.get('TrustTradeShares', 0))
                inst_db[code] = {'foreign': int(f_shares / 1000), 'trust': int(t_shares / 1000)}
    except Exception: pass
    
    # 2. 櫃買中心 (上櫃) tpex_mainboard_3itrade_hedge
    try:
        res2 = requests.get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_3itrade_hedge", timeout=5, headers={"User-Agent": "Mozilla/5.0"})
        if res2.status_code == 200:
            for item in res2.json():
                code = str(item.get('SecuritiesCompanyCode', '')).strip()
                # 官方欄位為 ForeignInvestorsDifference (股數), 需除以 1000
                f_diff = safe_float(item.get('ForeignInvestorsDifference', 0))
                t_diff = safe_float(item.get('InvestmentTrustDifference', 0))
                if code in inst_db:
                    inst_db[code]['foreign'] += int(f_diff / 1000)
                    inst_db[code]['trust'] += int(t_diff / 1000)
                else:
                    inst_db[code] = {'foreign': int(f_diff / 1000), 'trust': int(t_diff / 1000)}
    except Exception: pass
    
    today_str = datetime.now().strftime("%Y-%m-%d")
    history_db = {}
    if os.path.exists(INST_HISTORY_FILE):
        try:
            with open(INST_HISTORY_FILE, "r", encoding="utf-8") as f:
                history_db = json.load(f)
        except Exception: pass
    
    data_updated = False
    if inst_db:
        history_db[today_str] = inst_db
        sorted_dates = sorted(history_db.keys(), reverse=True)
        if len(sorted_dates) > 20:
            for d in sorted_dates[20:]: history_db.pop(d, None)
        try:
            with open(INST_HISTORY_FILE, "w", encoding="utf-8") as f:
                json.dump(history_db, f, ensure_ascii=False)
            data_updated = True
        except Exception: pass
        
    if data_updated: save_db()
        
    return inst_db, history_db

TW_STOCK_NAMES = fetch_stock_names()
MARGIN_DB = fetch_margin_data()
INST_DB, INST_HISTORY = fetch_institutional_data()
GLOBAL_MARKET_CODES = list(TW_STOCK_NAMES.keys())

def load_local_fundamentals():
    if os.path.exists(FUNDAMENTALS_DB_FILE):
        try:
            with open(FUNDAMENTALS_DB_FILE, "r", encoding="utf-8") as f: return json.load(f)
        except Exception: return {}
    return {}

def save_local_fundamentals(db):
    if len(db) > 500:
        try:
            with open(FUNDAMENTALS_DB_FILE, "w", encoding="utf-8") as f: json.dump(db, f, ensure_ascii=False)
        except Exception: pass

@st.cache_data(ttl=3600, show_spinner=False)
def fetch_fundamentals():
    db = load_local_fundamentals() 
    new_db = {}
    try:
        res1 = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL", timeout=5, headers={"User-Agent": "Mozilla/5.0"})
        if res1.status_code == 200:
            for item in res1.json():
                code = str(item.get('Code', '')).strip()
                if len(code) == 4 and code.isdigit():
                    new_db[code] = {'PE': safe_float(item.get('PeRatio')), 'PB': safe_float(item.get('PbRatio')), 'Yield': safe_float(item.get('DividendYield'))}
    except Exception: pass
    try:
        res2 = requests.get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_peratio_analysis", timeout=5, headers={"User-Agent": "Mozilla/5.0"})
        if res2.status_code == 200:
            for item in res2.json():
                code = str(item.get('SecuritiesCompanyCode', '')).strip()
                if len(code) == 4 and code.isdigit():
                    pe = item.get('PeRatio') or item.get('PERatio') or item.get('PriceEarningRatio')
                    pb = item.get('PbRatio') or item.get('PBRatio') or item.get('PriceBookRatio')
                    yld = item.get('DividendYield') or item.get('Yield')
                    new_db[code] = {'PE': safe_float(pe), 'PB': safe_float(pb), 'Yield': safe_float(yld)}
    except Exception: pass
    
    if len(new_db) > 500:
        db.update(new_db)
        save_local_fundamentals(db)
    return db

FUNDAMENTAL_DB = fetch_fundamentals()

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
                    if pe > 0 or pb > 0: return pe, pb, yld, roe, margin, rev_growth
        except Exception: pass

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
                    if pe > 0 or pb > 0: return pe, pb, yld, 0.0, 0.0, 0.0
        except Exception: pass
    return 0.0, 0.0, 0.0, 0.0, 0.0, 0.0

@st.cache_data(ttl=1800, show_spinner=False)
def fetch_stock_news(symbol):
    news_list = []
    session = get_safe_session()
    for ext in [".TW", ".TWO"]:
        try:
            tk = yf.Ticker(symbol + ext, session=session)
            news_items = tk.news
            if news_items:
                for n in news_items[:3]:
                    pub_time = datetime.fromtimestamp(n['providerPublishTime']).strftime('%Y-%m-%d %H:%M')
                    news_list.append(f"[{pub_time}] {n['title']}")
                break
        except Exception: pass
    return news_list

@st.cache_data(ttl=3600, show_spinner=False)
def fetch_recent_chips_rescue(symbol, token_string=""):
    f_consec = t_consec = f_latest = t_latest = 0
    start_date = (datetime.now() - timedelta(days=20)).strftime('%Y-%m-%d')
    url = 'https://api.finmindtrade.com/api/v4/data'
    params = {'dataset': 'TaiwanStockInstitutionalInvestorsBuySell', 'data_id': symbol, 'start_date': start_date}
    
    if token_string: params['token'] = token_string.split(',')[0].strip()
        
    try:
        res = requests.get(url, params=params, timeout=5)
        if res.status_code == 200 and res.json().get('msg') == 'success':
            df = pd.DataFrame(res.json().get('data', []))
            if not df.empty:
                df['net'] = pd.to_numeric(df['buy'], errors='coerce').fillna(0) - pd.to_numeric(df['sell'], errors='coerce').fillna(0)
                pivoted = df.pivot_table(index='date', columns='name', values='net', aggfunc='sum').sort_index(ascending=False)
                
                f_cols = [c for c in pivoted.columns if 'Foreign' in c or '外資' in c]
                t_cols = [c for c in pivoted.columns if 'Trust' in c or '投信' in c]
                
                if f_cols:
                    f_series = pivoted[f_cols[0]].fillna(0)
                    f_latest = int(f_series.iloc[0] / 1000) if not f_series.empty else 0
                    for val in f_series:
                        if val > 0: f_consec += 1
                        elif val <= 0: break
                
                if t_cols:
                    t_series = pivoted[t_cols[0]].fillna(0)
                    t_latest = int(t_series.iloc[0] / 1000) if not t_series.empty else 0
                    for val in t_series:
                        if val > 0: t_consec += 1
                        elif val <= 0: break
    except Exception: pass
    return f_consec, t_consec, f_latest, t_latest

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
            live_twoii = tk_twoii.history(period="1d", interval="1m").dropna(subset=['Close'])
            if not live_twoii.empty and not twoii.empty:
                twoii.loc[twoii.index[-1], 'Close'] = float(live_twoii['Close'].iloc[-1])
        except Exception: pass

        if twii.empty: return "⚠️ [大盤連線異常]", "#888", False, False, 0.0
        
        c_idx = float(twii['Close'].iloc[-1])
        prev_idx = float(twii['Close'].iloc[-2])
        
        twii_pt = c_idx - prev_idx
        twii_gain = (twii_pt / prev_idx) * 100 if prev_idx > 0 else 0.0
        ma20 = float(twii['Close'].rolling(20).mean().iloc[-1])
        
        two_gain = 0.0
        two_pt = 0.0
        two_curr = 0.0
        if len(twoii) >= 2:
            two_curr = float(twoii['Close'].iloc[-1])
            two_prev = float(twoii['Close'].iloc[-2])
            two_pt = two_curr - two_prev
            two_gain = (two_pt / two_prev) * 100 if two_prev > 0 else 0.0

        is_panic = (twii_gain <= -3.0) or (c_idx < float(twii['Close'].rolling(60).mean().iloc[-1]) * 0.95)
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
            except Exception: pass
            if not hist.empty and len(hist) > 26: return hist, 0.0, 0.0, 0.0
        except Exception: pass
    return None

def calculate_signals(symbol, data_tuple, portfolio_data=None, is_panic_global=False, twii_gain=0.0, is_scan=False):
    INTERNAL_SECTORS_DB = {
        "華新集團": ["1605", "2492", "2344", "6116", "5469", "6191", "2408", "5305"],
        "國巨集團": ["2327", "5339", "6271", "6422", "8043", "2456"],
        "鴻海集團": ["2317", "2354", "2328", "3413", "6414", "4958", "3149", "2314", "6451", "5243"],
        "聯電集團": ["2303", "2337", "3035", "3037", "2458", "3227", "3014", "8054"],
        "台積電集團": ["2330", "5347", "3443", "6789", "3374"],
        "AI與伺服器": ["2382", "3231", "2356", "2376", "2317", "6669", "3017", "3324", "2421", "3483"],
        "重電綠能": ["1519", "1513", "1514", "1503", "1609", "6806"],
        "半導體設備": ["3131", "3583", "3680", "6187", "6196", "6640", "3413"],
        "矽光子CPO": ["3363", "3450", "4979", "3163", "3234", "6451", "6442"]
    }

    if data_tuple is None or len(data_tuple) != 4: return None
    hist_df, _, _, _ = data_tuple
    if hist_df is None or hist_df.empty or len(hist_df) < 26: return None
    
    stock_name = TW_STOCK_NAMES.get(symbol, symbol)
    if stock_name == symbol or str(stock_name).isdigit():
        stock_name = get_fallback_name(symbol)
        TW_STOCK_NAMES[symbol] = stock_name 

    curr = float(hist_df['Close'].iloc[-1])
    recent_closes = hist_df['Close'].tail(7).tolist()
    sparkline_str = generate_sparkline(recent_closes)

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
    if len(hist_df) >= 3:
        prev_prev = max(float(hist_df['Close'].iloc[-3]), 0.001)
        yesterday_gain = ((prev - prev_prev) / prev_prev) * 100
    else: yesterday_gain = 0.0
    is_yesterday_strong = yesterday_gain >= 5.0

    open_p = float(hist_df['Open'].iloc[-1])
    high_p = float(hist_df['High'].iloc[-1])
    low_p = float(hist_df['Low'].iloc[-1])
    gain = ((curr - prev) / prev) * 100
    
    vol = int(hist_df['Volume'].iloc[-1] / 1000)
    vol_5d = max(hist_df['Volume'].iloc[-6:-1].mean() / 1000, 0.01)
    vol_ratio = vol / vol_5d if vol_5d > 0 else 1.0
    is_vol_contraction = vol_ratio <= 0.6
    
    rs_score = gain - twii_gain
    is_anti_drop = (rs_score >= 1.5 and gain >= -1.0)
    
    margin_diff = MARGIN_DB.get(symbol, 0.0)
    is_margin_decrease = margin_diff < 0.0
    
    f_buy = INST_DB.get(symbol, {}).get('foreign', 0)
    t_buy = INST_DB.get(symbol, {}).get('trust', 0)
    f_consec = t_consec = f_latest = t_latest = 0
    
    if not is_scan:
        f_consec, t_consec, f_latest, t_latest = fetch_recent_chips_rescue(symbol, SECRET_FINMIND)
    else:
        if symbol in INST_DB:
            sorted_dates = sorted(INST_HISTORY.keys(), reverse=True)
            f_broken = t_broken = False
            for d in sorted_dates:
                d_data = INST_HISTORY[d].get(symbol, {})
                d_f_buy = d_data.get('foreign', 0)
                d_t_buy = d_data.get('trust', 0)
                if not f_broken:
                    if d_f_buy > 0: f_consec += 1
                    else: f_broken = True
                if not t_broken:
                    if d_t_buy > 0: t_consec += 1
                    else: t_broken = True
                if f_broken and t_broken: break

    display_f_buy = f_buy if f_buy != 0 else f_latest
    display_t_buy = t_buy if t_buy != 0 else t_latest
    
    inst_tag = "D. 量縮整理"
    if display_f_buy > 0 and display_t_buy > 0: inst_tag = "G. 土洋齊買"
    elif display_t_buy > 0: inst_tag = "H. 投信買超"
    elif display_f_buy > 0: inst_tag = "I. 外資買超"
    
    inst_buy_total = display_f_buy + display_t_buy
    chip_conc = (inst_buy_total / vol * 100) if vol > 0 else 0.0
    is_chips_clean = (margin_diff < -500) and (inst_buy_total > 500)
    has_inst_support = inst_tag in ["G. 土洋齊買", "H. 投信買超"]
    
    recent_low = hist_df['Low'].tail(10).min()
    ma5 = hist_df['Close'].rolling(5).mean().iloc[-1]
    ma10 = hist_df['Close'].rolling(10).mean().iloc[-1]
    ma20 = hist_df['Close'].rolling(20).mean().iloc[-1]
    ma60 = hist_df['Close'].rolling(60).mean().iloc[-1] if len(hist_df) >= 60 else ma20
    ma240 = hist_df['Close'].rolling(min(240, len(hist_df))).mean().iloc[-1] if len(hist_df) >= 240 else ma60

    is_crash_alert = (gain <= -3.0) or (curr < ma5)

    is_ma_bullish = (curr > ma5) and (ma5 > ma20) and (ma20 > ma60)
    is_vol_breakout = vol_ratio >= 2.0 and gain >= 2.0
    is_stealth = (curr > ma60) and (gain < 2.0) and (curr < ma60 * 1.1) and (vol_ratio >= 1.2)
    is_yield_def = (curr > ma240) and (curr < ma60 * 1.05) and (yld >= 5.0) if len(hist_df)>=240 else False
    is_rev_burst = rev_growth > 20.0
    
    is_20d_high = curr >= hist_df['High'].tail(20).max()
    ma_max = max(ma5, ma10, ma20)
    ma_min = min(ma5, ma10, ma20)
    is_ribbon_tight = (ma_max - ma_min) / ma_min < 0.03 if ma_min > 0 else False
    is_ribbon_breakout = is_ribbon_tight and curr > ma_max and open_p < ma_max

    low_min = hist_df['Low'].rolling(9).min()
    high_max = hist_df['High'].rolling(9).max()
    rsv = (hist_df['Close'] - low_min) / (high_max - low_min + 1e-9) * 100
    calc_k = rsv.bfill().ffill().fillna(50).ewm(com=2, adjust=False).mean()
    calc_d = calc_k.bfill().ffill().fillna(50).ewm(com=2, adjust=False).mean()
    k = calc_k.iloc[-1] if not pd.isna(calc_k.iloc[-1]) else 50
    d_val = calc_d.iloc[-1] if not pd.isna(calc_d.iloc[-1]) else 50
    is_kdj_golden = (k < 50) and (calc_k.iloc[-2] <= calc_d.iloc[-2]) and (k > d_val)
    is_kdj_dead = (k > 70) and (calc_k.iloc[-2] >= calc_d.iloc[-2]) and (k < d_val)
    kdj_str = "金叉" if is_kdj_golden else ("死叉" if is_kdj_dead else ("向上" if k > d_val else "向下"))

    exp1 = hist_df['Close'].ewm(span=12, adjust=False).mean()
    exp2 = hist_df['Close'].ewm(span=26, adjust=False).mean()
    macd = exp1 - exp2
    macd_signal = macd.ewm(span=9, adjust=False).mean()
    macd_hist = macd - macd_signal
    macd_val = macd_hist.iloc[-1] if not macd_hist.empty and not pd.isna(macd_hist.iloc[-1]) else 0.0
    macd_prev = macd_hist.iloc[-2] if len(macd_hist) >= 2 and not pd.isna(macd_hist.iloc[-2]) else 0.0
    is_macd_golden = (macd_prev <= 0) and (macd_val > 0)
    is_macd_dead = (macd_prev >= 0) and (macd_val < 0)
    macd_str = "金叉" if is_macd_golden else ("死叉" if is_macd_dead else ("紅柱" if macd_val > 0 else "綠柱"))

    delta = hist_df['Close'].diff()
    gain_series = delta.where(delta > 0, 0.0)
    loss_series = -delta.where(delta < 0, 0.0)
    avg_gain = gain_series.ewm(alpha=1/14, adjust=False).mean()
    avg_loss = loss_series.ewm(alpha=1/14, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi_series = 100 - (100 / (1 + rs))
    rsi_val = rsi_series.fillna(50).iloc[-1]

    bb_std = hist_df['Close'].rolling(20).std().iloc[-1]
    if pd.isna(bb_std): bb_std = 0.0
    bb_up = ma20 + (2 * bb_std)
    bb_down = ma20 - (2 * bb_std)

    pattern_str = "[區間盤整]"
    if curr >= bb_up and vol_ratio >= 1.5: pattern_str = "[強勢突破上軌]"
    elif curr <= bb_down: pattern_str = "[弱勢破底]"
    elif is_kdj_golden and rsi_val > 40 and curr > ma5: pattern_str = "[W底起漲型態]"
    elif rsi_val > 80: pattern_str = "[短線極度超買]"
    elif rsi_val < 20: pattern_str = "[短線極度超賣]"
    
    is_shooting_star = (high_p - max(open_p, curr) > abs(curr - open_p) * 1.5) and (high_p > ma5)
    is_fake_breakout = (vol_ratio >= 2.0) and is_shooting_star

    start_signals = []
    if is_kdj_golden: start_signals.append("KDJ金叉")
    if is_macd_golden: start_signals.append("MACD金叉")
    if is_vol_breakout: start_signals.append("爆量上攻")
    retreat_signals = []
    if is_fake_breakout: retreat_signals.append("假突破(避雷針)")
    if is_kdj_dead or is_macd_dead: retreat_signals.append("高檔死叉")
    if curr < ma5: retreat_signals.append("跌破5日線")

    entry_price = float(portfolio_data.get('entry_price', 0.0)) if portfolio_data else 0.0
    roi_pct = ((curr - entry_price) / entry_price) * 100 if entry_price > 0 else 0.0
    
    st_buy = f"{ma5:.1f} ~ {curr:.1f}" if curr > ma5 else f"{recent_low:.1f} ~ {curr:.1f}"
    st_stop = str(round(curr * 0.98, 1))
    lt_buy = f"{ma60:.1f} ~ {ma20:.1f}" if curr > ma60 else "不建議佈局"
    lt_stop = str(round(ma60 * 0.95, 1)) if curr > ma60 else "N/A"

    if curr > ma60 and curr > ma5:
        signal_text, color_border, signal_bg = "[🔥 偏多操作]", "#ff4d4d", "#3a1515"
    elif curr > ma60 and curr <= ma5:
        signal_text, color_border, signal_bg = "[⚠️ 拉回整理]", "#f1c40f", "#332b00"
    else: 
        signal_text, color_border, signal_bg = "[📉 空頭觀望]", "#00FF00", "#153a20"

    ai_tags = []
    for g_name, codes in INTERNAL_SECTORS_DB.items():
        if symbol in codes: ai_tags.append(f"J. {g_name}"); break
            
    if display_t_buy >= 400: ai_tags.append("K. 投信作帳")
    if f_consec >= 3: ai_tags.append("L. 外資連買")
    if t_consec >= 3: ai_tags.append("M. 投信連買")
    if chip_conc >= 10.0: ai_tags.append("N. 異常大量吸籌")
    if is_rev_burst: ai_tags.append("O. 營收動能爆發")
    if is_chips_clean: ai_tags.append("P. 融資退潮換手")
    if is_vol_contraction: ai_tags.append("Q. 區間極度量縮")
    if is_margin_decrease: ai_tags.append("R. 融資退潮")
    if is_yesterday_strong: ai_tags.append("昨日強勢")
    if is_20d_high: ai_tags.append("創20日新高")
    if is_ribbon_breakout: ai_tags.append("均線糾結突破")
        
    if inst_tag != "D. 量縮整理": ai_tags.append(inst_tag)
    if is_anti_drop: ai_tags.append("E. 逆勢抗跌")
    elif rs_score <= -2.0 and gain < 0: ai_tags.append("F. 弱於大盤")
    
    for s in start_signals: ai_tags.append(f"{s}")
    for s in retreat_signals: ai_tags.append(f"{s}")
    
    if is_ma_bullish: ai_tags.append("C. 均線多頭")
    if len(ai_tags) == 0: ai_tags.append("D. 量縮整理")

    is_action_needed = retreat_signals or (entry_price > 0 and roi_pct <= -10.0)
    is_first_red_trigger = (gain > 0) and (curr > open_p) and (curr > ma5) and (prev < ma5)
    if is_first_red_trigger: ai_tags.append("A. 起漲第一根")
    
    if entry_price > 0 and roi_pct <= -10.0:
        signal_text, color_border, signal_bg = "[💀 觸發停損]", "#00FF00", "#153a20"
    elif retreat_signals:
        signal_text, color_border, signal_bg = f"[🚨 撤退警告]", "#00FF00", "#153a20"
        
    chip_text = f"<br><span style='color:#ccc;'>D. 籌碼流向：法人淨買賣超 {display_f_buy+display_t_buy:,} 張 | 融資增減 {margin_diff:,.0f} 張</span>"
    chip_text += f" <strong style='color:#d200ff;'>[外資連買 {f_consec} 天 | 投信連買 {t_consec} 天]</strong>"
    chip_text += f"<div style='font-size:11px; color:#666; margin-top:4px;'>* 本系統與官方同步，最新單日籌碼通常於 16:30 後發布，若遇延遲請點擊【強制全域更新】。</div>"

    wave_range = (high_p - low_p) + 1e-9
    lower_shadow_pct = (min(open_p, curr) - low_p) / wave_range * 100

    tactical_summary = f"""
    <div style="background:#15203a; border-left: 4px solid #00d2ff; padding: 12px; margin-top: 5px; border-radius: 4px;">
    <span style="color:#00d2ff; font-weight:bold; font-size:15px;">[📊 戰情解析中樞]</span><br>
    <span style="color:#ccc;">A. 體質診斷：股價季線防守於 {ma60:.1f}，評估為{val_shield}。</span><br>
    <span style="color:#ccc;">B. 動能狀態：短線下影線支撐強度: {lower_shadow_pct:.1f}%。</span>{chip_text}<br>
    <span style="color:#f1c40f; font-weight:bold;">[🎯 戰局判定]：不破開盤生死線 ({open_p:.2f}) 則結構未散。若觸發警報請立即檢閱戰損診斷。</span>
    </div>
    """

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
        "is_golden": "起漲" in signal_text or "多頭" in signal_text or "偏多" in signal_text, 
        "is_action_needed": is_action_needed, "is_crash_alert": is_crash_alert,
        "is_first_red": is_first_red_trigger, "is_vol_breakout": is_vol_breakout,
        "is_yesterday_strong": is_yesterday_strong, "is_ribbon_breakout": is_ribbon_breakout,
        "is_vol_contraction": is_vol_contraction, "is_margin_decrease": is_margin_decrease,
        "is_stealth": is_stealth, "is_yield": is_yield_def, 
        "chip_conc": chip_conc, "f_consec": f_consec, "t_consec": t_consec,
        "f_buy": display_f_buy, "t_buy": display_t_buy, "is_chips_clean": is_chips_clean,
        "sector": get_industry_label_wrapper(symbol), "sparkline": sparkline_str, 
        "lower_shadow_pct": lower_shadow_pct, "margin_diff": margin_diff
    }

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
                status.append({"index": i, "key": f"...{k[-4:]}", "status": "OK", "msg": f"✅ [連線成功] {working_model}", "model": working_model})
            else:
                err = ping_res.json().get('error', {}).get('message', '未知錯誤')
                if "quota" in err.lower() or "exceeded" in err.lower():
                    status.append({"index": i, "key": f"...{k[-4:]}", "status": "FAIL", "msg": "❌ [彈藥耗盡] 免費額度已達上限", "model": working_model})
                else:
                    status.append({"index": i, "key": f"...{k[-4:]}", "status": "FAIL", "msg": f"❌ [異常] {err[:20]}...", "model": working_model})
        except Exception as e:
            status.append({"index": i, "key": f"...{k[-4:]}", "status": "FAIL", "msg": f"❌ [系統錯誤] {str(e)[:20]}", "model": None})
    return status

def generate_ai_report(command_name, candidates, is_event_driven=False):
    if not GEMINI_API_KEYS or not GEMINI_API_KEYS[0]: return "⚠️ [系統提示] 雲端保險箱未配置有效的 API 金鑰。"
    
    if is_event_driven:
        prompt = f"""
        你是首席戰略幕僚。總指揮下達戰術：【{command_name}】。
        以下為觀測雷達中標的的最新重大新聞與突發事件：
        {json.dumps(candidates, ensure_ascii=False)}
        請針對有抓到新聞的股票，直接分析這些事件對「明天開盤股價」的潛在衝擊。
        格式需直接輸出，不需廢話：
        [AI 盤後突發事件解密]
        A. [股票代號 名稱] 
           - 突發事件研判：(說明新聞屬性是利多、利空還是中性)
           - 開盤衝擊預測：(明日開盤可能引發的資金行為預測)
        
        【嚴格紀律規範】
        1. 所有文字必須使用「繁體中文」。
        2. 必須使用大寫英文字母 (A., B., C.) 作為股票列舉的標籤。
        """
    else:
        lite_data = [{ '代號': c['code'], '名稱': c['name'], '價格': c['price'], '漲幅': c['gain'], '特徵': c['ai_tags'], 'KDJ': c['kdj_str'] } for c in candidates[:15]]
        prompt = f"""
        你是首席戰略幕僚。總指揮下達戰術：【{command_name}】。
        
        【核心交易鐵律】(分析時必須融入以下觀念)
        1. 不看表面漲跌，只盯大戶換手。早盤爆量震盪視為洗盤，不破「開盤價生死線」，多方骨架未散。
        2. 防禦保險絲制度：進場必設停損，破線像機器人冷血砍單，將風險鎖在 1.5%~3% 內。
        3. 13:18 獵殺劇本：嚴禁早盤追高，尾盤 13:18 確認踩穩開盤價再行伏擊。
        4. 強勢股回測狙擊：漲停絕不追加，若炸開漲停回測開盤價有守，才可建底倉。
        
        【嚴格紀律規範】
        1. 所有文字必須使用「繁體中文」。
        2. 必須使用大寫英文字母 (A., B., C.) 作為股票列舉的標籤。
        
        分析以下標的清單：{json.dumps(lite_data, ensure_ascii=False)}
        請挑選最精銳的 3 檔股票。回報格式需直接輸出，不需廢話：
        [🧠 AI 幕僚戰術報告：{command_name}]
        A. [股票代號 名稱] 
           - 入選理由與題材：(說明為何入選)
           - 總指揮觀測重點：(提醒進場或停損關鍵，套用鐵律思維)
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
                
    return f"❌ [後勤告急] 所有金鑰皆無法使用或額度耗盡。最後錯誤：{last_error}"

def draw_card(d, ui_key_prefix, is_portfolio=False, p_data=None):
    if not d: return
    gain_color = '#ff4d4d' if d['gain'] > 0 else ('#00FF00' if d['gain'] < 0 else '#aaaaaa')
    gain_bg = '#3a1515' if d['gain'] > 0 else ('#153a20' if d['gain'] < 0 else '#333333')
    
    tags_html = ""
    for tag in d.get('ai_tags', []):
        if '起漲' in tag or '多頭' in tag or '爆量' in tag or '金叉' in tag or '突破' in tag or '新高' in tag: tags_html += f"<span class='tag-red'>{tag}</span>"
        elif '撤退' in tag or '警報' in tag or '退潮' in tag or '假突破' in tag or '死叉' in tag: tags_html += f"<span class='tag-green'>{tag}</span>"
        elif '抗跌' in tag or '營收' in tag or '量縮' in tag: tags_html += f"<span class='tag-blue'>{tag}</span>"
        elif '買超' in tag or '齊買' in tag or '作帳' in tag or '集團' in tag or '連買' in tag or '吸籌' in tag: tags_html += f"<span class='tag-purple'>{tag}</span>"
        else: tags_html += f"<span class='tag-gray'>{tag}</span>"
        
    port_html = ""
    if is_portfolio and p_data:
        prof, pct, fb, fs, tax = calc_real_profit(p_data['entry_price'], d['price'], p_data['qty'])
        prof_color = '#ff4d4d' if prof > 0 else ('#00FF00' if prof < 0 else '#aaaaaa')
        port_html = f"""<div style='background:#10141d; padding:10px; border-radius:6px; margin-bottom:12px; border:1px solid #333;'>
            <div style='display:flex; justify-content:space-between; margin-bottom:4px;'>
                <span style='color:#aaa; font-size:13px;'>進場單價: <strong style='color:#f1c40f;'>{p_data['entry_price']}</strong></span>
                <span style='color:#aaa; font-size:13px;'>持有張數: <strong style='color:#f1c40f;'>{p_data['qty']} 張</strong></span>
            </div>
            <div style='display:flex; justify-content:space-between; margin-bottom:4px;'>
                <span style='color:#888; font-size:12px;'>預估手續費: {fb+fs} 元</span>
                <span style='color:#888; font-size:12px;'>證券交易稅: {tax} 元</span>
            </div>
            <div style='border-top:1px dashed #333; margin:6px 0;'></div>
            <div style='display:flex; justify-content:space-between; align-items:center;'>
                <span style='color:#ddd; font-size:14px;'>淨損益 (扣除息費):</span>
                <strong style='color:{prof_color}; font-size:16px;'>{int(prof):+,} 元 ({pct:+.2f}%)</strong>
            </div>
        </div>"""
    
    is_alert = d.get('is_crash_alert', False)
    if is_alert and (is_portfolio or ui_key_prefix.startswith('pin_')):
        alert_banner = "<div style='background-color:#00FF00; color:#000; padding:5px; text-align:center; font-weight:bold; font-size:15px; border-radius:4px; margin-bottom:10px; letter-spacing:1px;'>🚨 [系統強制警報] 跌幅過大或破5日線，請立即檢視戰損！</div>"
        d['color'] = "#00FF00"
    else:
        alert_banner = ""
    
    kdj_color = "#ff4d4d" if "金" in d['kdj_str'] or "上" in d['kdj_str'] else "#00FF00"
    macd_color = "#ff4d4d" if "金" in d['macd_str'] or "紅" in d['macd_str'] else "#00FF00"
    
    metric_grid = f"""<div class='metric-grid'>
<div style="width:100%; margin-bottom:6px; display:flex; justify-content:space-between;">
<span>近7日走勢: <strong style="color:#00d2ff; font-size:16px; letter-spacing:2px;">{d.get('sparkline', '')}</strong></span>
<span>價值分數: <strong style="color:#00d2ff; font-size:15px;">{d['val_score']} 分</strong> <span style="color:#888;">({d['val_shield']})</span></span>
</div>
<div style="width:100%; border-top: 1px dashed #444; margin-bottom:6px; padding-top:6px; display:flex; gap:15px; flex-wrap:wrap;">
<span>開盤: <strong style="color:#fff;">{d['open']:.2f}</strong></span>
<span>最高: <strong style="color:#fff;">{d['high']:.2f}</strong></span>
<span>最低: <strong style="color:#fff;">{d['low']:.2f}</strong></span>
<span>總量: <strong style="color:#f1c40f;">{d['vol']:,} 張</strong></span>
</div>
<div style="width:100%; border-top: 1px dashed #444; margin-bottom:6px;"></div>
<div style="width:100%; display:flex; justify-content:space-between; margin-bottom:4px;">
<span style="flex:1;">短線戰略: <strong style="color:#f1c40f;">{d['st_buy']}</strong> (防禦: <span style="color:#00FF00;">{d['st_stop']}</span>)</span>
<span style="flex:1;">長線戰略: <strong style="color:#00d2ff;">{d['lt_buy']}</strong> (防禦: <span style="color:#00FF00;">{d['lt_stop']}</span>)</span>
</div>
<div style="width:100%; border-top: 1px dashed #444; margin-top:4px; margin-bottom:6px;"></div>
<span>攻擊訊號: <strong style="color:#ff4d4d;">{d['start_signals']}</strong></span>
<span>撤退風險: <strong style="color:#00FF00;">{d['retreat_signals']}</strong></span>
<span>KDJ/MACD: <strong style="color:{kdj_color};">{d['kdj_str']}</strong> / <strong style="color:{macd_color};">{d['macd_str']}</strong></span>
<span>爆量比: <strong style="color:#e67e22;">{d['vol_ratio']:.1f}x</strong></span>
</div>"""
    
    summary_class = "tactical-danger" if d['is_action_needed'] else "tactical-summary"
    st.markdown(f"""<div style="border: 2px solid {d['color']}; border-radius: 8px; padding: 15px; background-color: #16191f; margin-bottom: 5px;">
{alert_banner}
{port_html}
<div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:5px;">
<span style="font-weight:bold; font-size:18px;">{d['name']} ({d['code']}) <span style="font-size:12px; color:#aaa; background:#333; padding:2px 6px; border-radius:4px; font-weight:normal;">{d.get('sector', '綜合')}</span></span>
<span style="color:#888; font-size:12px;">{d['cost_label']}: {d['cost']}</span>
</div>
<div style="font-size:32px; font-weight:bold; margin-bottom: 10px; display:flex; gap:12px;">{d['price']:.2f} <span style="font-size:16px; color:{gain_color}; background-color:{gain_bg}; padding:4px 10px; border-radius:6px;">{d['gain']:+.1f}%</span></div>
<div style="margin-bottom: 10px;">{tags_html}</div>
{metric_grid}
<div style="background:{d['signal_bg']}; padding:10px; border-radius:6px; text-align:center; margin-bottom:10px; border: 1px solid {d['color']}40;"><strong style="color:{d['color']}; font-size:16px;">{d['signal']}</strong></div>
<div class="{summary_class}">{d['tactical_summary']}</div>
</div>""", unsafe_allow_html=True)

# ==========================================
# 主戰情室畫面渲染
# ==========================================
col_nav1, col_nav2 = st.columns([8, 2])
with col_nav1: st.markdown("<h1 style='color:#FFB300; margin: 0;'>🚀 54088 戰情室 V111.0</h1>", unsafe_allow_html=True)

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

st.markdown(f"""
<div class='hud-box'>
<div class='hud-title'><span>📊 大將軍戰情總覽 (HUD)</span></div>
<div style='background:#1a1c23; padding:10px; border-radius:5px; border-left:3px solid {weather_color}; margin-bottom:10px; font-size:14px; color:#ddd;'>
<strong>[今日大盤風向]</strong> {weather_str}
</div>
<div class='hud-metric'><span style='color:#aaa;'>📦 庫存 / 雷達數量</span> <strong style='color:#fff;'>{len(port_loaded_cards)} / {len(pin_loaded_cards)} 檔</strong></div>
<div class='hud-metric'><span style='color:#aaa;'>💰 總未實現淨損益</span> <strong style='color:{'#ff4d4d' if total_unrealized>=0 else '#00FF00'}; font-size:18px;'>{int(total_unrealized):+,.0f} 元</strong></div>
</div>
""", unsafe_allow_html=True)

with st.sidebar:
    st.markdown("<h2 style='color:#f1c40f; text-align:center;'>⚙️ 戰略控制台</h2>", unsafe_allow_html=True)
    
    if st.button("🔄 [強制全域更新]", use_container_width=True, type="primary"):
        get_market_weather.clear()
        get_stock_data.clear()
        check_api_keys.clear()
        fetch_fundamentals.clear() 
        fetch_institutional_data.clear()
        fetch_margin_data.clear()
        get_finmind_and_deep_fundamentals.clear()
        fetch_stock_news.clear()
        fetch_recent_chips_rescue.clear()
        st.session_state.temp_intel = [] 
        st.rerun() 
        
    st.markdown("---")
    intel_input = st.text_area("🔍 手動單檔搜尋 / 貼上AI戰報", placeholder="輸入代碼(如2330)...")
    if st.button("[強制解析並匯入]", use_container_width=True):
        if intel_input.strip():
            found_codes = set(re.findall(r'\b\d{4}\b', intel_input))
            if found_codes:
                for c in found_codes: st.session_state.pinned_stocks[c] = {}
                save_db(); st.rerun()

    st.markdown("---")
    st.markdown("<h4 style='color:#00FF00; margin-top:10px;'>🗄️ 資料庫連線狀態</h4>", unsafe_allow_html=True)
    if SUPABASE_URL and SUPABASE_KEY:
        st.markdown("<div style='background:#1a1a24; padding:10px; border-radius:5px; border:1px solid #333; margin-bottom:10px;'><span class='key-status-ok'>✅ [穩定] Supabase 雲端軍火庫已連線</span></div>", unsafe_allow_html=True)
    else:
        st.markdown("<div style='background:#1a1a24; padding:10px; border-radius:5px; border:1px solid #333; margin-bottom:10px;'><span class='key-status-fail'>❌ [脫機] 本地實體硬碟模式</span></div>", unsafe_allow_html=True)

    st.markdown("<h4 style='color:#d200ff; margin-top:10px;'>🔑 金鑰火力監測</h4>", unsafe_allow_html=True)
    key_statuses = check_api_keys(GEMINI_API_KEYS, st.session_state.ai_mode)
    status_html = "<div style='background:#1a1a24; padding:10px; border-radius:5px; border:1px solid #333; margin-bottom:10px;'>"
    for s in key_statuses:
        status_text = "正常" if s['status'] == "OK" else "異常"
        color_class = "key-status-ok" if s['status'] == "OK" else "key-status-fail"
        status_html += f"<div>[{status_text}] Key #{s['index']} ({s['key']}): <span class='{color_class}'>{s['msg']}</span></div>"
    status_html += "</div>"
    st.markdown(status_html, unsafe_allow_html=True)

    st.markdown("---")
    scan_scope = st.selectbox("🌐 掃描範圍", ["全市場 1700+ 檔", "電子/半導體/光電"])
    min_volume_filter = st.slider("⚖️ 最低 5 日均量 (張)：", 0, 5000, 500, 100)
    
    with st.expander("🗄️ [數據庫盤點] 檢查法人歷史記憶"):
        if os.path.exists(INST_HISTORY_FILE):
            with open(INST_HISTORY_FILE, "r", encoding="utf-8") as f:
                hist_data = json.load(f)
            st.write(f"目前儲存天數: {len(hist_data)} 天")
        else:
            st.write("目前資料庫檔案尚無歷史數據紀錄")

    def get_scope_codes(scope):
        if "全市場" in scope: return GLOBAL_MARKET_CODES
        elif "電子" in scope: return [c for c in GLOBAL_MARKET_CODES if c.startswith(('23','24','30','31','32','33','34','35','36','49','52','53','54','61','62','64','80','81','82'))]
        return GLOBAL_MARKET_CODES

    def run_command_scan(cmd_name, scope, min_vol):
        results = []
        codes = get_scope_codes(scope)
        bar = st.progress(0)
        status = st.empty()
        invalid_signals = ["[📉 空頭觀望]", "[高檔觀望]", "[⚠️ 拉回整理]", "[💀 觸發停損]", "[🚨 撤退警告]"]
        for i, c in enumerate(codes):
            if i % 3 == 0: status.text(f"雷達鎖定與過濾中... ({i}/{len(codes)})")
            d = calculate_signals(c, get_stock_data(c), is_panic_global=is_panic, twii_gain=global_twii_gain, is_scan=True)
            if d and d['vol_5d'] >= min_vol and not d['is_action_needed']: 
                if d['signal'] not in invalid_signals:
                    if cmd_name == "指令一" and d['is_first_red'] and d['is_vol_breakout'] and ("金叉" in d['kdj_str'] or "金叉" in d['macd_str']): results.append(d)
                    elif cmd_name == "指令二" and d['is_stealth']: results.append(d)
                    elif cmd_name == "指令三" and d['is_yield']: results.append(d)
                    elif cmd_name == "指令四" and any(tag.startswith(("J.", "K.")) for tag in d['ai_tags']): results.append(d)
                    elif cmd_name == "指令五" and (d.get('chip_conc', 0) >= 8.0 or d.get('f_consec', 0) >= 3 or d.get('t_consec', 0) >= 3 or (d.get('f_buy',0) + d.get('t_buy',0) >= 800) or d.get('is_chips_clean')): results.append(d)
                    elif cmd_name == "指令六" and d.get('is_rev_burst'): results.append(d)
                    elif cmd_name == "指令八" and d.get('is_yesterday_strong'): results.append(d)
                    elif cmd_name == "指令九" and d.get('is_ribbon_breakout'): results.append(d)
                    elif cmd_name == "指令十" and d.get('is_vol_contraction') and d.get('is_margin_decrease'): results.append(d)
                    elif cmd_name == "常規": results.append(d)
            bar.progress(min((i + 1) / len(codes), 1.0))
        bar.empty(); status.empty()
        return results

    st.markdown("<div class='cmd-btn'>", unsafe_allow_html=True)
    if st.button("⚔️ [指令一] 主升段突擊", use_container_width=True):
        st.session_state.scan_results = run_command_scan("指令一", scan_scope, min_volume_filter)
        st.session_state.scan_mode = "cmd_1"
    with st.expander("📖 [戰術解密] 指令一"): st.write("必須同時滿足金叉、爆量上攻，且為起漲第一根。")
    
    if st.button("🐟 [指令二] 魚頭潛伏期", use_container_width=True):
        st.session_state.scan_results = run_command_scan("指令二", scan_scope, min_volume_filter)
        st.session_state.scan_mode = "cmd_2"
    with st.expander("📖 [戰術解密] 指令二"): st.write("長線站穩季線，近期盤整貼近支撐且增量。")
    
    if st.button("🔄 [指令三] 季節與循環", use_container_width=True):
        st.session_state.scan_results = run_command_scan("指令三", scan_scope, min_volume_filter)
        st.session_state.scan_mode = "cmd_3"
    with st.expander("📖 [戰術解密] 指令三"): st.write("股價在年線之上、靠近季線，且殖利率大於 5%。")
    
    if st.button("🔥 [指令四] 作帳與熱門族群", use_container_width=True):
        st.session_state.scan_results = run_command_scan("指令四", scan_scope, min_volume_filter)
        st.session_state.scan_mode = "cmd_4"
    with st.expander("📖 [戰術解密] 指令四"): st.write("鎖定六大集團與熱門產業，以及投信重倉買超標的。")
    
    if st.button("💪 [指令五] 籌碼霸王色", use_container_width=True):
        st.session_state.scan_results = run_command_scan("指令五", scan_scope, min_volume_filter)
        st.session_state.scan_mode = "cmd_5"
    with st.expander("📖 [戰術解密] 指令五"): st.write("外資投信連續買進，或融資大減法人接手。")
    
    if st.button("📈 [指令六] 營收雙增爆發", use_container_width=True):
        st.session_state.scan_results = run_command_scan("指令六", scan_scope, min_volume_filter)
        st.session_state.scan_mode = "cmd_6"
    with st.expander("📖 [戰術解密] 指令六"): st.write("單月營收呈現高成長(大於20%)的黑馬。")

    if st.button("⚡ [指令八] 昨日強勢延續", use_container_width=True):
        st.session_state.scan_results = run_command_scan("指令八", scan_scope, min_volume_filter)
        st.session_state.scan_mode = "cmd_8"
    with st.expander("📖 [戰術解密] 指令八"): st.write("前一交易日漲幅超過 5% 的強勢股。")

    if st.button("🎯 [指令九] 均線糾結突破", use_container_width=True):
        st.session_state.scan_results = run_command_scan("指令九", scan_scope, min_volume_filter)
        st.session_state.scan_mode = "cmd_9"
    with st.expander("📖 [戰術解密] 指令九"): st.write("5日、10日、20日均線黏合(差距小於3%)且今日放量突破。")

    if st.button("🤫 [指令十] 籌碼沉澱量縮", use_container_width=True):
        st.session_state.scan_results = run_command_scan("指令十", scan_scope, min_volume_filter)
        st.session_state.scan_mode = "cmd_10"
    with st.expander("📖 [戰術解密] 指令十"): st.write("成交量急縮至均量60%以下，且融資餘額減少。")
    st.markdown("</div>", unsafe_allow_html=True)
    
    st.markdown("<div class='scan-btn'>", unsafe_allow_html=True)
    if st.button("🔎 [常規掃描] 黃金起漲與魚身", use_container_width=True):
        st.session_state.scan_results = run_command_scan("常規", scan_scope, min_volume_filter)
        st.session_state.scan_mode = "golden"
    with st.expander("📖 [戰術解密] 常規掃描"): st.write("過濾掉破線與空頭的股票，保留所有安全的標的。")
    st.markdown("</div>", unsafe_allow_html=True)

if st.session_state.portfolio:
    st.markdown("<h2 style='color:#ff4d4d;'>💼 總指揮持倉 (模擬倉)</h2>", unsafe_allow_html=True)
    cols = st.columns(2)
    for i, (code, p_data) in enumerate(list(st.session_state.portfolio.items())):
        d = port_loaded_cards.get(code)
        if d:
            with cols[i % 2]:
                draw_card(d, f"port_{code}", is_portfolio=True, p_data=p_data)
                
                is_alert = d.get('is_crash_alert', False)
                with st.expander("🚨 [單檔崩跌戰損診斷報告]", expanded=is_alert):
                    st.markdown(f"### 標的 {code} 崩跌診斷報告")
                    st.markdown("**1. 籌碼元兇追蹤 (誰在賣)**")
                    st.write(f"當日外資淨買賣超: {d['f_buy']:,} 張")
                    st.write(f"當日投信淨買賣超: {d['t_buy']:,} 張")
                    st.write(f"當日融資增減: {d['margin_diff']:,} 張")
                    if d['f_buy'] < -500 or d['t_buy'] < -500: st.markdown("<span style='color:#00FF00;font-weight:bold;'>⚠️ [危險警告] 發現法人大宗出貨調頭，結構轉弱！</span>", unsafe_allow_html=True)
                    else: st.markdown("<span style='color:#ff4d4d;font-weight:bold;'>✅ [結構安全] 法人未出現叛逃性大倒貨。</span>", unsafe_allow_html=True)
                        
                    st.markdown("**2. 下方支撐韌性 (有沒有人接)**")
                    st.write(f"當日爆量比: {d['vol_ratio']:.2f}x")
                    st.write(f"下影線波幅佔比: {d['lower_shadow_pct']:.1f}%")
                    if d['lower_shadow_pct'] >= 40.0 and d['vol_ratio'] >= 1.2: st.markdown("<span style='color:#ff4d4d;font-weight:bold;'>💪 [支撐強悍] 下方留長下影線且爆量換手，有大戶承接！可留校察看。</span>", unsafe_allow_html=True)
                    else: st.markdown("<span style='color:#00FF00;font-weight:bold;'>💀 [缺乏防守] 實體長黑且低檔無承接量，下方放棄抵抗，建議依保險絲撤退。</span>", unsafe_allow_html=True)
                        
                    st.markdown("**3. 趨勢逆轉檢驗 (主力有沒有變臉)**")
                    st.write(f"外資連續買超天數: {d['f_consec']} 天")
                    st.write(f"投信連續買超天數: {d['t_consec']} 天")
                    if (d['f_consec'] == 0 and d['f_buy'] < -200) or (d['t_consec'] == 0 and d['t_buy'] < -200): st.markdown("<span style='color:#00FF00;font-weight:bold;'>📉 [大戶變臉] 趨勢已遭今日大賣反轉，主力正式變臉！</span>", unsafe_allow_html=True)
                    else: st.markdown("<span style='color:#ff4d4d;font-weight:bold;'>🛡️ [趨勢延續] 主力多方慣性未發生逆轉。</span>", unsafe_allow_html=True)

                if st.button("🗑️ [賣出平倉]", key=f"sell_{code}", use_container_width=True):
                    del st.session_state.portfolio[code]
                    save_db(); st.rerun()

if st.session_state.pinned_stocks:
    st.markdown("<h2 style='color:#f1c40f;'>🎯 觀測雷達</h2>", unsafe_allow_html=True)
    cols = st.columns(2)
    for i, code in enumerate(st.session_state.pinned_stocks.keys()):
        d = pin_loaded_cards.get(code)
        if d:
            with cols[i % 2]: 
                draw_card(d, f"pin_{code}")
                
                is_alert = d.get('is_crash_alert', False)
                with st.expander("🚨 [單檔崩跌戰損診斷報告]", expanded=is_alert):
                    st.markdown(f"### 標的 {code} 崩跌診斷報告")
                    st.markdown("**1. 籌碼元兇追蹤 (誰在賣)**")
                    st.write(f"當日外資淨買賣超: {d['f_buy']:,} 張")
                    st.write(f"當日投信淨買賣超: {d['t_buy']:,} 張")
                    st.write(f"當日融資增減: {d['margin_diff']:,} 張")
                    if d['f_buy'] < -500 or d['t_buy'] < -500: st.markdown("<span style='color:#00FF00;font-weight:bold;'>⚠️ [危險警告] 發現法人大宗出貨調頭，結構轉弱！</span>", unsafe_allow_html=True)
                    else: st.markdown("<span style='color:#ff4d4d;font-weight:bold;'>✅ [結構安全] 法人未出現叛逃性大倒貨。</span>", unsafe_allow_html=True)
                        
                    st.markdown("**2. 下方支撐韌性 (有沒有人接)**")
                    st.write(f"當日爆量比: {d['vol_ratio']:.2f}x")
                    st.write(f"下影線波幅佔比: {d['lower_shadow_pct']:.1f}%")
                    if d['lower_shadow_pct'] >= 40.0 and d['vol_ratio'] >= 1.2: st.markdown("<span style='color:#ff4d4d;font-weight:bold;'>💪 [支撐強悍] 下方留長下影線且爆量換手，有大戶承接！可留校察看。</span>", unsafe_allow_html=True)
                    else: st.markdown("<span style='color:#00FF00;font-weight:bold;'>💀 [缺乏防守] 實體長黑且低檔無承接量，下方放棄抵抗，建議依保險絲撤退。</span>", unsafe_allow_html=True)

                st.markdown("<div style='background:#10141d; padding:10px; border-radius:6px; margin-bottom:10px; border:1px solid #333;'>", unsafe_allow_html=True)
                c_ep, c_eq = st.columns(2)
                buy_p = c_ep.number_input("買進單價", value=float(d['price']), step=0.1, key=f"bp_{code}")
                buy_q = c_eq.number_input("買進張數", value=1, min_value=1, step=1, key=f"bq_{code}")
                st.markdown("</div>", unsafe_allow_html=True)
                
                c1, c2 = st.columns(2)
                if c1.button("📥 [建立部位]", key=f"buy_{code}", use_container_width=True):
                    st.session_state.portfolio[code] = {'entry_price': buy_p, 'qty': buy_q}
                    del st.session_state.pinned_stocks[code]
                    save_db(); st.rerun()
                if c2.button("🗑️ [刪除追蹤]", key=f"del_{code}", use_container_width=True):
                    del st.session_state.pinned_stocks[code]
                    save_db(); st.rerun()

if st.session_state.get('scan_mode'):
    st.markdown("<h2 style='color:#00d2ff;'>⚡ 初篩結果</h2>", unsafe_allow_html=True)
    st.markdown("<div style='background:#10141d; padding:10px; border-radius:6px; border:1px solid #333; margin-bottom:15px;'>", unsafe_allow_html=True)
    if st.button("➕ 將已勾選標的【批次加入】觀測雷達", type="primary", use_container_width=True):
        added_count = 0
        for d in st.session_state.scan_results:
            if d and st.session_state.get(f"chk_batch_{d['code']}", False):
                st.session_state.pinned_stocks[d['code']] = {}
                added_count += 1
        if added_count > 0:
            save_db()
            st.rerun()
    st.markdown("</div>", unsafe_allow_html=True)

    cols = st.columns(2)
    for i, d in enumerate(st.session_state.scan_results):
        if d and d['code'] not in st.session_state.portfolio and d['code'] not in st.session_state.pinned_stocks:
            with cols[i % 2]:
                st.checkbox(f"勾選追蹤 {d['code']} {d['name']}", key=f"chk_batch_{d['code']}")
                draw_card(d, f"scan_{i}")
