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
from openai import OpenAI  # V148 全新掛載 NVIDIA NIM (相容 OpenAI 協定)

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
INST_HISTORY_FILE = "54088_inst_history_v30d.json"

# ==============================================================================
# 二、 記憶體全域安全隔離初始化 (V148 擴建時光膠囊)
# ==============================================================================
def init_session_state():
    if not hasattr(st.session_state, 'db_loaded'): st.session_state.db_loaded = False
    if not hasattr(st.session_state, 'pinned_stocks'): st.session_state.pinned_stocks = {"2303": "手動強制加入", "5871": "手動強制加入"}
    if not hasattr(st.session_state, 'portfolio'): st.session_state.portfolio = {}
    if not hasattr(st.session_state, 'revenue_override'): st.session_state.revenue_override = {}
    if not hasattr(st.session_state, 'dividend_override'): st.session_state.dividend_override = {}
    if not hasattr(st.session_state, 'inst_history'): st.session_state.inst_history = {}
    if not hasattr(st.session_state, 'scan_results'): st.session_state.scan_results = []
    if not hasattr(st.session_state, 'scan_mode'): st.session_state.scan_mode = ""
    if not hasattr(st.session_state, 'active_key_index'): st.session_state.active_key_index = 0
    if not hasattr(st.session_state, 'single_ai_trigger'): st.session_state.single_ai_trigger = ""
    if not hasattr(st.session_state, 'single_ai_report'): st.session_state.single_ai_report = {}
    if not hasattr(st.session_state, 'intelligence_pool'): st.session_state.intelligence_pool = {}
    if not hasattr(st.session_state, 'analysis_history'): st.session_state.analysis_history = {} 
    if not hasattr(st.session_state, 'last_refresh'): st.session_state.last_refresh = time.time()

init_session_state()

def load_and_isolate_db():
    if not getattr(st.session_state, 'db_loaded', False):
        if os.path.exists(USER_DB_FILE):
            try:
                with open(USER_DB_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    st.session_state.pinned_stocks = data.get("pinned_stocks", {})
                    st.session_state.portfolio = data.get("portfolio", {})
                    st.session_state.revenue_override = data.get("revenue_override", {})
                    st.session_state.dividend_override = data.get("dividend_override", {})
                    st.session_state.intelligence_pool = data.get("intelligence_pool", {})
                    st.session_state.analysis_history = data.get("analysis_history", {})
            except Exception: pass
        if os.path.exists(INST_HISTORY_FILE):
            try:
                with open(INST_HISTORY_FILE, "r", encoding="utf-8") as f:
                    st.session_state.inst_history = json.load(f)
                    if len(getattr(st.session_state, 'inst_history', {})) > 30:
                        sorted_dates = sorted(st.session_state.inst_history.keys(), reverse=True)
                        st.session_state.inst_history = {d: st.session_state.inst_history[d] for d in sorted_dates[:30]}
            except Exception: pass
        st.session_state.db_loaded = True

def save_local_db_isolated():
    payload = {
        "pinned_stocks": getattr(st.session_state, 'pinned_stocks', {}), 
        "portfolio": getattr(st.session_state, 'portfolio', {}),
        "revenue_override": getattr(st.session_state, 'revenue_override', {}),
        "dividend_override": getattr(st.session_state, 'dividend_override', {}),
        "intelligence_pool": getattr(st.session_state, 'intelligence_pool', {}),
        "analysis_history": getattr(st.session_state, 'analysis_history', {})
    }
    try:
        with open(USER_DB_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=4)
        if getattr(st.session_state, 'inst_history', {}):
            with open(INST_HISTORY_FILE, "w", encoding="utf-8") as f:
                json.dump(st.session_state.inst_history, f, ensure_ascii=False)
    except Exception: pass

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
# 三、 真實大數據晶片核心 
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
    roi = (profit / buy_val) * 100 if buy_val > 0 else 0
    return profit, roi

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
            res = requests.get(url, headers=GOV_HEADERS, verify=False, timeout=5)
            if res.status_code == 200:
                for item in res.json():
                    c = str(item.get('公司代號', '')).strip()
                    if len(c) == 4:
                        m_str = str(item.get('資料年月', item.get('出表日期', ''))).strip()
                        month_match = re.search(r'(\d{2})$', m_str)
                        m_label = f"{month_match.group(1)}月" if month_match else "最新"
                        rev_db.update({c: {'yoy': safe_float(item.get('當月營收較去年當月增減百分比', 0)), 'mom': safe_float(item.get('上月比較增減(%)', 0)), 'month': m_label}})
        except: pass
    return rev_db

@st.cache_data(ttl=86400, show_spinner=False)
def fetch_stock_names():
    names = {}
    for url in ["https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL", "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_peratio_analysis"]:
        try:
            res = requests.get(url, headers=GOV_HEADERS, verify=False, timeout=5)
            if res.status_code == 200:
                for item in res.json():
                    c = str(item.get('Code', item.get('SecuritiesCompanyCode', ''))).strip()
                    n = str(item.get('Name', item.get('CompanyName', ''))).strip()
                    if len(c) == 4 and c.isdigit() and n: names.update({c: n})
        except: pass
    for k, v in {"2330":"台積電", "2303":"聯電", "2317":"鴻海", "2308":"台達電", "5871":"中租-KY", "3481":"群創", "2454":"聯發科"}.items():
        if k not in names: names.update({k: v})
    return names

@st.cache_data(ttl=86400, show_spinner=False)
def fetch_twse_dividends():
    divs = {}
    try:
        res = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/TWT48U", headers=GOV_HEADERS, verify=False, timeout=5)
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

TW_STOCK_NAMES = fetch_stock_names()
TW_REVENUE_DB = fetch_tw_revenue()
DIVIDEND_DB = fetch_twse_dividends()
GLOBAL_MARKET_CODES = list(TW_STOCK_NAMES.keys())

@st.cache_data(ttl=60, show_spinner=False)
def get_market_weather_real():
    try:
        tk = yf.Ticker("^TWII", session=get_safe_session())
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
    session = get_safe_session()
    for ext in [".TW", ".TWO"]:
        try:
            tk = yf.Ticker(symbol + ext, session=session)
            hist = tk.history(period="3mo", timeout=4).dropna(subset=['Close'])
            hist = hist[hist['Volume'] > 0]
            hist_1m = tk.history(period="1d", interval="1m", timeout=3).dropna(subset=['Close'])
            if not hist.empty and len(hist) > 10: return hist.tail(30), hist_1m, tk.info
        except: pass
    return None, None, {}

weather_str, is_panic, global_twii_gain = get_market_weather_real()

def generate_bi_color_sparkline(closes_list):
    if not closes_list: return "<span style='color:#888;'>▃</span>"
    while len(closes_list) < 7: closes_list.append(closes_list[-1])
    closes_list = closes_list[-7:] 
    
    bars, min_p, max_p = " ▂▃▄▅▆▇█", min(closes_list), max(closes_list)
    rng = max_p - min_p if max_p != min_p else 1e-9
    html_sparkline = ""
    for i in range(len(closes_list)):
        idx = max(0, min(7, int((closes_list[i] - min_p) / rng * 7)))
        color = "#888888" if i == 0 else ("#ff4d4d" if closes_list[i] > closes_list[i-1] else ("#00FF00" if closes_list[i] < closes_list[i-1] else "#aaaaaa"))
        html_sparkline += f"<span style='color:{color}; font-weight:bold;'>{bars[idx]}</span>"
    return html_sparkline

def detect_k_line_patterns_v133(df):
    patterns = []
    if len(df) < 5: return patterns
    c0, c1, c2 = float(df['Close'].iloc[-1]), float(df['Close'].iloc[-2]), float(df['Close'].iloc[-3])
    o0, o1, o2 = float(df['Open'].iloc[-1]), float(df['Open'].iloc[-2]), float(df['Open'].iloc[-3])
    body0 = abs(c0 - o0)
    if (c0 > o0) and body0 > (c0 * 0.025):
        if (c1 < o1) and c0 > o1 and o0 < c1: patterns.append({"text": "長紅吞噬", "class": "tag-red"})
        else: patterns.append({"text": "低檔長紅", "class": "tag-red"})
    if (c0 > o0) and (c1 > o1) and (c2 > o2) and (c0 > c1 > c2): patterns.append({"text": "紅三兵", "class": "tag-red"})
    if (c0 < o0) and body0 > (c0 * 0.025):
        if (c1 > o1) and c0 < o1 and o0 > c1: patterns.append({"text": "長黑吞噬", "class": "tag-green"})
        else: patterns.append({"text": "高檔長黑", "class": "tag-green"})
    if (c0 < o0) and (c1 < o1) and (c2 < o2) and (c0 < c1 < c2): patterns.append({"text": "黑三兵", "class": "tag-green"})
    return patterns

def get_intraday_trend(df_1m):
    if df_1m is None or df_1m.empty: return "無即時看盤資料"
    op = float(df_1m['Open'].iloc[0])
    cl = float(df_1m['Close'].iloc[-1])
    hi = float(df_1m['High'].max())
    lo = float(df_1m['Low'].min())
    if cl > op and cl >= hi * 0.99: return "開低走高·強勢收上"
    if cl < op and cl <= lo * 1.01: return "開高走低·弱勢收下"
    if cl > op: return "震盪走高"
    return "震盪偏弱"

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
# 五、 核心訊號與五大戰區聚合核心 
# ==============================================================================
def calculate_comprehensive_signals(symbol, enable_doomsday=False):
    manual_mode, manual_div_mode = False, False
    f_single = t_single = d_single = margin_diff = big_holder = 0
    f_5d = t_5d = f_10d = t_10d = 0
    f_pct = t_pct = f_5d_pct = t_5d_pct = f_10d_pct = t_10d_pct = 0.0
    
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
    
    sorted_dates = sorted(getattr(st.session_state, 'inst_history', {}).keys(), reverse=True)
    if sorted_dates:
        latest_data = st.session_state.inst_history[sorted_dates[0]].get(symbol, {})
        f_single, t_single, d_single, margin_diff, big_holder = latest_data.get('foreign', 0), latest_data.get('trust', 0), latest_data.get('dealer', 0), latest_data.get('margin', 0), latest_data.get('big_holder', 0.0)
        
        f_pct = (f_single / vol_today * 100) if vol_today > 0 else 0.0
        t_pct = (t_single / vol_today * 100) if vol_today > 0 else 0.0
        
        for idx, d in enumerate(sorted_dates):
            day_data = st.session_state.inst_history[d].get(symbol, {})
            if idx < 5: f_5d += day_data.get('foreign', 0); t_5d += day_data.get('trust', 0)
            if idx < 10: f_10d += day_data.get('foreign', 0); t_10d += day_data.get('trust', 0)
        
        f_5d_pct = (f_5d / vol_5d_sum * 100) if vol_5d_sum > 0 else 0.0
        t_5d_pct = (t_5d / vol_5d_sum * 100) if vol_5d_sum > 0 else 0.0
        f_10d_pct = (f_10d / vol_10d_sum * 100) if vol_10d_sum > 0 else 0.0
        t_10d_pct = (t_10d / vol_10d_sum * 100) if vol_10d_sum > 0 else 0.0
                
    override_db = getattr(st.session_state, 'revenue_override', {})
    if symbol in override_db:
        rev_yoy, rev_mom, rev_month, manual_mode = override_db[symbol].get('yoy', 0.0), override_db[symbol].get('mom', 0.0), override_db[symbol].get('month', "自訂"), True
    else:
        rev_data = TW_REVENUE_DB.get(symbol, {})
        rev_yoy, rev_mom, rev_month = rev_data.get('yoy', 0.0), rev_data.get('mom', 0.0), rev_data.get('month', "最新")
        if rev_yoy == 0.0 and rev_mom == 0.0: rev_yoy = safe_float(info.get('revenueGrowth', 0.0)) * 100
            
    override_div = getattr(st.session_state, 'dividend_override', {})
    if symbol in override_div:
        div_display, div_yield, manual_div_mode = override_div[symbol].get('display', "自訂資料"), override_div[symbol].get('yield', 0.0), True
    else:
        div_info = DIVIDEND_DB.get(symbol)
        div_date_str = ""
        if div_info:
            d_cash, d_stock, div_date_str = div_info.get('cash', 0.0), div_info.get('stock', 0.0), div_info.get('date', '')
            div_yield = (d_cash / curr_price) * 100 if curr_price > 0 else 0.0
            if d_cash > 0 and d_stock > 0: div_display = f"{div_date_str} | 息 {d_cash}元 + 權 {d_stock}元"
            elif d_cash > 0: div_display = f"{div_date_str} | 息 {d_cash}元"
            elif d_stock > 0: div_display = f"{div_date_str} | 權 {d_stock}元"
            else: div_display = "無近期資訊"
        else:
            d_cash = safe_float(info.get('dividendRate', 0.0))
            div_yield = (d_cash / curr_price) * 100 if curr_price > 0 else 0.0
            div_display = f"無日期 | 息 {d_cash}元" if d_cash > 0 else "無近期資訊"
        
        if "無" not in div_display and div_date_str and len(div_date_str) == 8:
            try:
                if datetime.strptime(div_date_str, "%Y%m%d") < datetime.now():
                    div_display = f"<span style='color:#888888;'>已於 {div_date_str[:4]}/{div_date_str[4:6]} 除息</span> (息{d_cash} 權{d_stock})"
            except: pass

    atk_zone, def_line = "", ""
    if curr_price >= ma5:
        atk_zone = f"{ma5:.1f} ~ 現價"
        def_line = f"跌破 {ma5:.1f}"
    elif curr_price >= ma20:
        atk_zone = f"{ma20:.1f} ~ 現價"
        def_line = f"跌破 {ma20:.1f}"
    else:
        atk_zone = "空手觀望 (破線)"
        def_line = "全面破線，嚴守紀律"

    multi_bull, multi_bear = [], []
    if curr_price > ma5: multi_bull.append("站上5日線")
    else: multi_bear.append("跌破5日線")
    if curr_price > ma20: multi_bull.append("站上月線")
    else: multi_bear.append("跌破月線")
    if f_single > 0: multi_bull.append("外資買超")
    else: multi_bear.append("外資無買超")
    if t_single > 0: multi_bull.append("投信買超")
    if margin_diff < 0: multi_bull.append("融資減少(沉澱)")
    else: multi_bear.append("融資增加(發散)")
    if rev_yoy > 20.0: multi_bull.append("營收雙增")
    if macd_val < 0: multi_bear.append("空方動能")
    
    detected_patterns = detect_k_line_patterns_v133(hist)
    for p in detected_patterns:
        if "紅" in p.get('text', ''): multi_bull.append(p.get('text'))
        else: multi_bear.append(p.get('text'))
        
    signal_text = "[🔥 偏多攻擊]" if (curr_price > ma5 and f_single > 0) else ("[🚨 撤退警告]" if curr_price < ma5 else "[⚠️ 整理觀望]")
    color_border = "#ff4d4d" if "攻擊" in signal_text else ("#00FF00" if "警告" in signal_text else "#f1c40f")
    signal_bg = "#3a1515" if "攻擊" in signal_text else ("#153a20" if "警告" in signal_text else "#332b00")
    
    return {
        "code": symbol, "name": TW_STOCK_NAMES.get(symbol, symbol), "price": curr_price, "gain": gain, "error": False,
        "open": open_price, "high": float(hist['High'].iloc[-1]), "low": float(hist['Low'].iloc[-1]),
        "vol": vol_today, "vol_change_pct": vol_change_pct, "vol_ratio": vol_ratio,
        "ma5": ma5, "ma20": ma20, "ma60": ma60, "macd_str": macd_str, "macd_color": macd_color, "kdj_str": kdj_str,
        "f_buy": f_single, "t_buy": t_single, "d_buy": d_single, "margin_diff": margin_diff, "big_holder": big_holder,
        "f_5d": f_5d, "t_5d": t_5d, "f_10d": f_10d, "t_10d": t_10d, "f_pct": f_pct, "t_pct": t_pct, 
        "f_5d_pct": f_5d_pct, "t_5d_pct": t_5d_pct, "f_10d_pct": f_10d_pct, "t_10d_pct": t_10d_pct,
        "atk_zone": atk_zone, "def_line": def_line,
        "rev_yoy": rev_yoy, "rev_mom": rev_mom, "rev_month": rev_month, 
        "div_display": div_display, "div_yield": div_yield, "manual_div_mode": manual_div_mode,
        "multi_bull": multi_bull, "multi_bear": multi_bear, "blood_line": getattr(st.session_state, 'pinned_stocks', {}).get(symbol, "手動強制加入"),
        "signal_text": signal_text, "color_border": color_border, "signal_bg": signal_bg,
        "sparkline_html": generate_bi_color_sparkline(hist['Close'].tail(7).tolist()), 
        "intraday_str": get_intraday_trend(hist_1m), "manual_mode": manual_mode, "sector": get_industry_label_wrapper(symbol),
        "is_first_red": (gain > 0 and curr_price > open_price and curr_price > ma5 and prev_price < ma5),
        "is_yesterday_strong": (gain > 0 and len(hist)>2 and ((prev_price - float(hist['Close'].iloc[-3]))/float(hist['Close'].iloc[-3])*100 > 5.0))
    }

# ==============================================================================
# 六、 雙軌籌碼備援管線 
# ==============================================================================
def process_twse_csv(uploaded_files):
    success_files = 0
    history_db = getattr(st.session_state, 'inst_history', {})
    
    for file_bytes in uploaded_files:
        raw_bytes = file_bytes.getvalue()
        decoded_content = None
        for enc in ['big5', 'utf-8', 'utf-8-sig', 'cp950']:
            try:
                decoded_content = raw_bytes.decode(enc)
                break
            except Exception: pass
            
        if not decoded_content:
            try:
                decoded_content = raw_bytes.decode('big5', errors='ignore')
            except:
                st.error(f"❌ {file_bytes.name} 編碼解析徹底失敗。")
                continue

        try:
            first_line = decoded_content.split('\n')[0]
            date_match = re.search(r'(\d+)年(\d+)月(\d+)日', first_line)
            if date_match:
                file_date = f"{int(date_match.group(1))+1911}-{date_match.group(2).zfill(2)}-{date_match.group(3).zfill(2)}"
            else:
                file_date = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
            
            import io
            df = pd.read_csv(io.StringIO(decoded_content), skiprows=1, thousands=',')
            
            code_col = next((c for c in df.columns if '代號' in str(c)), None)
            f_col = next((c for c in df.columns if '外資' in str(c) and '買賣超' in str(c)), None)
            t_col = next((c for c in df.columns if '投信買賣超' in str(c)), None)
            d_col = next((c for c in df.columns if '自營商' in str(c) and '自行買賣' in str(c)), None)
            d_hedge = next((c for c in df.columns if '自營商' in str(c) and '避險' in str(c)), None)
            
            if not code_col or not f_col:
                st.error(f"❌ {file_bytes.name} 找不到法人買賣超欄位。")
                continue
                
            if file_date not in history_db: history_db.update({file_date: {}})
            
            for index, row in df.iterrows():
                code = str(row[code_col]).strip()
                if len(code) == 4 and code.isdigit():
                    f_buy = int(safe_float(row[f_col]) / 1000) if f_col else 0
                    t_buy = int(safe_float(row[t_col]) / 1000) if t_col else 0
                    d1 = int(safe_float(row[d_col]) / 1000) if d_col else 0
                    d2 = int(safe_float(row[d_hedge]) / 1000) if d_hedge else 0
                    existing = history_db.get(file_date, {}).get(code, {})
                    payload = {'foreign': f_buy, 'trust': t_buy, 'dealer': (d1 + d2), 'margin': existing.get('margin', 0), 'big_holder': existing.get('big_holder', 0.0)}
                    history_db.get(file_date, {}).update({code: payload})
            success_files += 1
        except Exception as e:
            st.error(f"❌ {file_bytes.name} 讀取失敗: {str(e)}")
            
    if success_files > 0:
        save_local_db_isolated()
        st.success(f"✅ 成功強填 {success_files} 份日報至大腦！")
        time.sleep(1); st.rerun()

def execute_heavy_data_sync(target_codes, target_date):
    progress_bar = st.progress(0)
    status_text = st.empty()
    history_db = getattr(st.session_state, 'inst_history', {})
    if target_date not in history_db: history_db.update({target_date: {}})
        
    missing = []
    for c in target_codes:
        existing_data = history_db.get(target_date, {}).get(c, {})
        if not existing_data or existing_data.get('big_holder', 0.0) == 0.0:
            missing.append(c)

    if not missing:
        st.success("✅ 當日籌碼大腦記憶庫已 100% 飽和，無須修復。")
        return
        
    status_text.info(f"📡 備援引擎啟動，正在對 {len(missing)} 檔個股進行靶向精準修復...")
    success_count = 0
    url = 'https://api.finmindtrade.com/api/v4/data'
    
    # 🚀 V148.3 線程安全修復：主線程抓取 Token 與初始資料
    active_token = FINMIND_TOKENS[getattr(st.session_state, 'active_key_index', 0)]
    current_history_slice = history_db.get(target_date, {})
    
    def fetch_finmind_worker(code, token, init_payload):
        payload = init_payload.copy()
        try:
            p1 = {'dataset': 'TaiwanStockInstitutionalInvestorsBuySell', 'data_id': code, 'start_date': target_date}
            if token: p1['token'] = token
            r1 = requests.get(url, params=p1, timeout=4)
            if r1.status_code == 200 and r1.json().get('msg') == 'success':
                df = pd.DataFrame(r1.json().get('data', []))
                if not df.empty:
                    df['net'] = pd.to_numeric(df['buy'], errors='coerce').fillna(0) - pd.to_numeric(df['sell'], errors='coerce').fillna(0)
                    piv = df.pivot_table(index='date', columns='name', values='net', aggfunc='sum')
                    payload['foreign'] = int(piv['Foreign_Investor'].iloc[-1]/1000) if 'Foreign_Investor' in piv.columns else payload['foreign']
                    payload['trust'] = int(piv['Investment_Trust'].iloc[-1]/1000) if 'Investment_Trust' in piv.columns else payload['trust']
                    payload['dealer'] = int(piv['Dealer'].iloc[-1]/1000) if 'Dealer' in piv.columns else payload['dealer']
            
            p2 = {'dataset': 'TaiwanStockMarginPurchaseShortSale', 'data_id': code, 'start_date': target_date}
            if token: p2['token'] = token
            r2 = requests.get(url, params=p2, timeout=4)
            if r2.status_code == 200 and r2.json().get('msg') == 'success':
                m_df = pd.DataFrame(r2.json().get('data', []))
                if not m_df.empty: payload['margin'] = int(m_df.iloc[-1].get('MarginPurchaseTodayBalance',0)) - int(m_df.iloc[-1].get('MarginPurchaseYesterdayBalance',0))

            p3 = {'dataset': 'TaiwanStockHoldingSharesPer', 'data_id': code, 'start_date': (datetime.strptime(target_date, "%Y-%m-%d") - timedelta(days=20)).strftime('%Y-%m-%d')}
            if token: p3['token'] = token
            r3 = requests.get(url, params=p3, timeout=4)
            if r3.status_code == 200 and r3.json().get('msg') == 'success':
                b_df = pd.DataFrame(r3.json().get('data', []))
                if not b_df.empty:
                    latest_date = b_df['date'].max()
                    payload['big_holder'] = round(b_df[(b_df['date'] == latest_date) & (b_df['HoldingSharesLevel'] >= 15)]['percent'].sum(), 2)

            return True, code, payload
        except Exception: 
            return False, code, None

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = {}
        for code in missing:
            init_p = current_history_slice.get(code, {'foreign':0, 'trust':0, 'dealer':0, 'margin':0, 'big_holder':0.0})
            futures[executor.submit(fetch_finmind_worker, code, active_token, init_p)] = code
            
        for idx, future in enumerate(concurrent.futures.as_completed(futures)):
            success, r_code, r_payload = future.result()
            if success:
                success_count += 1
                # 🚀 V148.3 線程安全修復：在主線程將資料安全寫入 st.session_state
                st.session_state.inst_history[target_date][r_code] = r_payload
                
            progress_bar.progress(min((idx + 1) / len(futures), 1.0))
            if idx > 0 and idx % 40 == 0: save_local_db_isolated()

    status_text.empty()
    progress_bar.empty()
    save_local_db_isolated()
    st.success(f"✅ API 靶向斷點修復完畢，成功充填: {success_count} 檔。")
    time.sleep(0.5); st.rerun()

# ==============================================================================
# 七、 V148 NVIDIA NIM DeepSeek 引擎 (自動輪替備援)
# ==============================================================================
def _auto_fallback_nvidia_nim(prompt, is_json=False):
    if not NVIDIA_API_KEY: 
        return False, "未配置 NVIDIA API 金鑰"
        
    client = OpenAI(
        base_url="https://integrate.api.nvidia.com/v1",
        api_key=NVIDIA_API_KEY
    )
    
    models_to_try = [
        "deepseek-ai/deepseek-v4-pro",
        "deepseek-ai/deepseek-v4-flash",
        "nvidia/nemotron-3-ultra-550b-a55b",
        "minimax-m3-preview"
    ]
    
    last_error = ""
    for model_id in models_to_try:
        try:
            completion = client.chat.completions.create(
                model=model_id,
                messages=[
                    {"role": "system", "content": "你是一位冷血的台灣股市操盤幕僚。所有輸出嚴格使用繁體中文，並使用台灣金融專有名詞（如：融資斷頭、投信作帳、隔日沖等）。不說廢話，直擊核心，進行客觀冷血的籌碼與技術面推演。"},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.2,
                max_tokens=1024,
                timeout=15 
            )
            res_text = completion.choices[0].message.content
            
            if is_json:
                match = re.search(r'\{.*\}', res_text, re.DOTALL)
                if match: return True, json.loads(match.group(0))
                last_error = "回傳格式非 JSON"
                continue
                
            return True, f"【{model_id.split('/')[-1]} 提供分析】\n\n{res_text}"
            
        except Exception as e:
            last_error = str(e)
            continue
            
    return False, f"⚠️ NVIDIA API 全面癱瘓或限流，所有備援模型皆已耗盡。最後報錯：{last_error}"

def execute_ai_revenue_fetch(code, name):
    prompt = f"請根據你的知識庫估算或預測台灣股票「{name} ({code})」最近可能的單月營收年增率(YoY)與月增率(MoM)。嚴格只回傳 JSON 格式：{{\"month\": \"最新\", \"yoy\": 15.2, \"mom\": -2.1}}"
    return _auto_fallback_nvidia_nim(prompt, is_json=True)

def execute_ai_dividend_fetch(code, name, price):
    prompt = f"請根據你的知識庫估算台灣股票「{name} ({code})」今年最可能的除權息資訊。嚴格只回傳 JSON 格式：{{\"date\": \"2026/07/15\", \"cash\": 3.5, \"stock\": 0.0}}"
    success, result = _auto_fallback_nvidia_nim(prompt, is_json=True)
    if success:
        yld = (float(result.get('cash',0)) / float(price)) * 100 if float(price) > 0 else 0
        disp = f"{result.get('date','')} | 息 {result.get('cash',0)}元 + 權 {result.get('stock',0)}元"
        return True, {"display": disp, "yield": yld}
    return False, result

def execute_single_stock_ai_推演(c):
    prompt = f"""請以首席戰略幕僚身分，對 {c['name']} ({c['code']}) 進行冷血多空推演。現價:{c['price']} | 漲跌:{c['gain']:.2f}% | 營收YoY:{c['rev_yoy']:.1f}% | 外資5日:{c['f_5d']}張 | MACD:{c['macd_str']}。請分四段繁體輸出：【第一戰區財報面小結】、【第二戰區技術面小結】、【第三戰區籌碼面小結】、【總指揮明日戰略總結】"""
    success, result = _auto_fallback_nvidia_nim(prompt, is_json=False)
    return result if success else result

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
        st.session_state.last_refresh = time.time()
        st.rerun()
        
    st.divider()
    with st.expander("📥 [主攻] 官方 CSV 籌碼強填中樞", expanded=False):
        st.markdown("<a href='https://www.twse.com.tw/zh/trading/foreign/t86.html' target='_blank' style='color:#00d2ff; text-decoration:none;'>👉 點此前往證交所下載日報</a>", unsafe_allow_html=True)
        st.caption("支援一次拖曳多個 CSV 檔，系統會自動解碼日期")
        uploaded_csvs = st.file_uploader("拖曳證交所三大法人 CSV", type=['csv'], accept_multiple_files=True, label_visibility="collapsed")
        if uploaded_csvs:
            if st.button("🚀 批次強制解析回填", use_container_width=True):
                process_twse_csv(uploaded_csvs)
                
    with st.expander("📊 資料庫完整度與備份還原", expanded=False):
        db_days = len(getattr(st.session_state, 'inst_history', {}))
        if db_days == 0:
            st.warning("⚠️ 目前大腦無籌碼資料，請上傳 CSV")
        else:
            st.write(f"當前儲存天數共: {db_days} 天")
            with st.container(height=150):
                for d, data_dict in sorted(st.session_state.inst_history.items(), reverse=True):
                    st.caption(f"📅 {d}: 已存全市場 {len(data_dict)} 檔籌碼")
        
        st.divider()
        st.markdown("**💾 實體大腦 JSON 備份下載**")
        if os.path.exists(USER_DB_FILE):
            with open(USER_DB_FILE, "r", encoding="utf-8") as f:
                st.download_button("下載自訂參數/持倉 (DB)", f.read(), file_name=USER_DB_FILE, mime="application/json", use_container_width=True)
        if os.path.exists(INST_HISTORY_FILE):
            with open(INST_HISTORY_FILE, "r", encoding="utf-8") as f:
                st.download_button("下載籌碼歷史大腦", f.read(), file_name=INST_HISTORY_FILE, mime="application/json", use_container_width=True)
                
        st.divider()
        st.markdown("**📤 實體大腦 JSON 上傳還原包**")
        uploaded_json = st.file_uploader("拖曳 JSON 備份檔至此", type=['json'], label_visibility="collapsed")
        if uploaded_json is not None:
            if st.button("🚀 強制還原大腦記憶", use_container_width=True):
                try:
                    raw_data = json.load(uploaded_json)
                    if "portfolio" in raw_data or "pinned_stocks" in raw_data:
                        with open(USER_DB_FILE, "w", encoding="utf-8") as f: json.dump(raw_data, f, ensure_ascii=False, indent=4)
                        st.success("自訂參數 DB 還原成功！"); time.sleep(1); st.session_state.db_loaded = False; st.rerun()
                    elif "2330" in str(raw_data): 
                        with open(INST_HISTORY_FILE, "w", encoding="utf-8") as f: json.dump(raw_data, f, ensure_ascii=False)
                        st.success("籌碼歷史大腦還原成功！"); time.sleep(1); st.session_state.db_loaded = False; st.rerun()
                    else: st.error("JSON 格式不符。")
                except Exception as e: st.error(f"還原失敗: {str(e)}")

    with st.expander("📡 [備援] 智慧靶向補齊引擎", expanded=False):
        target_date_sim = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
        slider_sync_range = st.slider("同步上限檔數設定", min_value=100, max_value=1700, value=300, step=100)
        if st.button("🚀 執行遺失自動靶向補齊", use_container_width=True):
            execute_heavy_data_sync(GLOBAL_MARKET_CODES[:slider_sync_range], target_date_sim)
            
    st.divider()
    min_volume_filter = st.slider("最低 5 日波段均量門檻 (張)", 0, 5000, 500, 100)
    min_yield_filter = st.slider("最低現金殖利率門檻調整 (%)", 0.0, 30.0, 4.5, 0.5)
    enable_doomsday_lock = st.checkbox("💀 開啟末日鎔斷防護鎖", value=False)
    
    st.divider()
    
    # 🌟 V148: 動態組合雷達掃描條件 (查1~查12 加上 查13~查15)
    commands_list = ["查1.主升段突擊", "查2.魚頭慢伏支撐", "查3.價值投資與循環", "查4.投信作帳集團股", "查5.籌碼外資霸王色", "查6.營收雙增爆發突破", "查8.昨日強勢動能延續", "查9.均線糾結爆量突破", "查10.籌碼沉澱量縮潛伏", "查11.除權息尋寶雷達", "查12.K線型態尋寶型"]
    
    existing_sources = set()
    for info in getattr(st.session_state, 'intelligence_pool', {}).values():
        if isinstance(info, dict) and "sources" in info:
            for src in info["sources"]: existing_sources.add(src)
            
    base_idx = 13
    for src in sorted(list(existing_sources)):
        commands_list.append(f"查{base_idx}. 情報雷達：{src}")
        base_idx += 1
    if len(existing_sources) > 0:
        commands_list.append(f"查{base_idx}. 🏆 情報黃金交叉 (符合 2 種以上來源)")

    selected_cmds = st.multiselect("🎯 戰略掃描條件 (可複選交集)", commands_list, default=[])
    
    selected_k_patterns = []
    if any("查12" in cmd for cmd in selected_cmds):
        with st.container(border=True):
            if st.checkbox("🔥 長紅吞噬 / 低檔長紅"): selected_k_patterns.append("長紅")
            if st.checkbox("🔥 紅三兵強勢推推"): selected_k_patterns.append("紅三兵")
            if st.checkbox("💀 長黑吞噬頂部出貨"): selected_k_patterns.append("長黑")
            if st.checkbox("💀 黑三兵弱勢跌破"): selected_k_patterns.append("黑三兵")
            
    if st.button("🚀 執行全市場多維度交叉掃描", use_container_width=True, type="primary"):
        if not selected_cmds: st.warning("⚠️ 總指揮官，請先在上方選擇至少一項戰略條件。")
        else: st.session_state.trigger_scan = True

    with st.expander("📖 統籌戰術解密說明書", expanded=False):
        st.markdown("""
        <div style="font-size:12px; line-height:1.6; color:#eeeeee;">
        <b style='color:#00d2ff;'>查1~查12</b>: 原有技術面與籌碼面濾網。<br>
        <b style='color:#00d2ff;'>情報雷達</b>: V148 全新動態生成，抓出具有特定情報血統之標的。<br>
        <b style='color:#00d2ff;'>黃金交叉</b>: 僅顯示「同時被兩種以上情報來源提及」的最高共識標的。
        </div>
        """, unsafe_allow_html=True)
        
    st.divider()
    st.markdown("<div style='font-size:12px; font-weight:bold; margin-bottom:5px;'>📡 系統連線狀態</div>", unsafe_allow_html=True)
    b_light = "🟢" if NVIDIA_API_KEY else "🔴"
    f_light = "🟢" if FINMIND_READY else "🔴"
    st.markdown(f"<div style='font-size:11px;'>{b_light} NVIDIA NIM 自動火力網<br>{f_light} FinMind 線路</div>", unsafe_allow_html=True)

# ==============================================================================
# 十、 主畫面：V148 物理情報大腦注入中樞
# ==============================================================================
st.title("🚀 54088 戰情室 V148 終極物理拔管版")

with st.container(border=True):
    st.markdown("<h3 style='color:#f1c40f; font-size:16px; margin:0 0 10px 0;'>🎙️ 視覺與文字情報解析中樞 (V148 物理注入版)</h3>", unsafe_allow_html=True)
    with st.expander("展開情報注入面板 (貼上 Claude/Gemini 整理報告)", expanded=True):
        col1, col2 = st.columns(2)
        source_type = col1.selectbox("情報來源陣地劃分", ["股癌最新節目", "外資法人報告", "綜合財經新聞", "其他自訂"])
        source_tag = col2.text_input("定義本手情報標籤", "最新情報")
        
        manual_intel_text = st.text_area("請在此貼上由外部 AI 處理好的繁體中文戰略報告：", height=120, placeholder="報告中必須包含 [標的代號: 2330] 字樣以便系統綁定血統。")
        
        if st.button("💾 寫入大腦情報庫", use_container_width=True, type="primary"):
            if manual_intel_text.strip():
                tickers_found = re.findall(r"\[標的代號:\s*(\d{4})\]", manual_intel_text)
                if not tickers_found:
                    st.warning("⚠️ 警告：報告中未偵測到 [標的代號: XXXX] 格式，無法綁定血統。")
                else:
                    for ticker in tickers_found:
                        if ticker not in st.session_state.intelligence_pool:
                            st.session_state.intelligence_pool[ticker] = {"sources": [], "history": []}
                        if source_type not in st.session_state.intelligence_pool[ticker]["sources"]:
                            st.session_state.intelligence_pool[ticker]["sources"].append(source_type)
                        st.session_state.intelligence_pool[ticker]["history"].append({
                            "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
                            "tag": source_tag
                        })
                    save_local_db_isolated()
                    st.success(f"✅ 情報已成功寫入大腦！成功綁定 {len(tickers_found)} 檔標的 ({', '.join(tickers_found)})。請重新整理頁面以更新左側雷達清單！")
            else:
                st.error("請先貼上報告內容！")

# ==============================================================================
# 十一、 主畫面字卡與雷達防線渲染晶片
# ==============================================================================
st.markdown(f"""<div class='hud-box'>
    <div style='color:#f1c40f; font-size:16px; font-weight:bold; margin-bottom:4px;'>📊 大將軍智慧 HUD 總覽</div>
    <div style='color:#ddd; font-size:14px;'><b>大盤氣象：</b> {weather_str} | <b>安全狀態：</b> V148.3 線程安全防護版已掛載</div>
</div>""", unsafe_allow_html=True)

search_input = st.text_input("🔍 手動股票代號/名稱輸入框 (如: 2330 或 聯電)", "")
if st.button("➕ 強制加入常態觀測雷達", use_container_width=True):
    if search_input:
        found_codes = re.findall(r'\b\d{4}\b', search_input)
        for code, name in TW_STOCK_NAMES.items():
            if (name in search_input or search_input in name) and code not in found_codes: found_codes.append(code)
        if found_codes:
            for c in found_codes: st.session_state.pinned_stocks.update({c: "手動強制加入"})
            save_local_db_isolated()
            target_date_sim = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
            execute_heavy_data_sync(found_codes, target_date_sim) 
            st.rerun()
        else:
            st.error("⚠️ 找不到對應的股票代號或名稱，請重新輸入。")

def render_commander_stock_card(c, is_portfolio=False, profit=0, roi=0, ent_p=0):
    gain_c = '#ff4d4d' if float(c.get('gain',0)) > 0 else ('#00FF00' if float(c.get('gain',0)) < 0 else '#aaaaaa')
    gain_b = '#3a1515' if float(c.get('gain',0)) > 0 else ('#153a20' if float(c.get('gain',0)) < 0 else '#333333')
    vol_c = '#ff4d4d' if float(c.get('vol_change_pct',0)) > 0 else '#00FF00'
    vol_t = f"爆量 {float(c.get('vol_change_pct',0)):+.1f}%" if float(c.get('vol_change_pct',0)) > 0 else f"量縮 {float(c.get('vol_change_pct',0)):.1f}%"
    portfolio_header = f"<div style='font-size:14px; margin-bottom:8px; color:#eeeeee;'>持倉成本: {ent_p} | 損益: <strong style='color:{'#ff4d4d' if profit>0 else '#00FF00'};'>{int(profit):+,} 元</strong> ({roi:+.2f}%)</div>" if is_portfolio else ""
    
    yoy_val, mom_val = float(c.get('rev_yoy',0)), float(c.get('rev_mom',0))
    yoy_color = "#ff4d4d" if yoy_val > 0 else ("#00FF00" if yoy_val < 0 else "#00d2ff")
    mom_color = "#ff4d4d" if mom_val > 0 else ("#00FF00" if mom_val < 0 else "#00d2ff")

    m_tag = f"<span style='background:#7f8c8d; color:#fff; font-size:10px; padding:1px 3px; border-radius:3px;'>手動/AI</span>" if c.get('manual_mode') else ""
    rev_html = f"<span class='m-tooltip'>營收 年增<span class='m-tooltiptext'>當月營收較去年同期增減百分比</span></span> <strong style='color:#ffffff;'>({c.get('rev_month')})</strong>: <strong style='color:{yoy_color};'>{yoy_val:.1f}%</strong> {m_tag} | <span class='m-tooltip'>月增<span class='m-tooltiptext'>較上月增減</span></span>: <strong style='color:{mom_color};'>{mom_val:.1f}%</strong>"
    div_html = f"除權息資訊: <strong style='color:#d200ff;'>{c.get('div_display')} (殖利率: {float(c.get('div_yield',0)):.1f}%)</strong>"
    
    bloodline_str = c.get('blood_line', '')
    if c.get('code') in getattr(st.session_state, 'intelligence_pool', {}):
        srcs = st.session_state.intelligence_pool[c.get('code')].get('sources', [])
        if srcs: bloodline_str += f" | 🧬 血統: {', '.join(srcs)}"

    html = f"""
<div style="border:2px solid {c.get('color_border')}; border-radius:8px; padding:15px; background:#16191f; margin-bottom:12px; color:#eeeeee;">
{portfolio_header}
<div style="display:flex; justify-content:space-between; align-items:center;">
<span style="font-weight:bold; font-size:19px; color:#ffffff;">{c.get('name')} <span style="color:#00d2ff;">({c.get('code')})</span></span>
<span style="font-size:13px; color:#f1c40f;">{bloodline_str}</span>
</div>
<div style="display:flex; justify-content:space-between; align-items:flex-end; margin:10px 0;">
    <div style="display:flex; align-items:center;"><span style="font-size:32px; font-weight:bold; color:#ffffff;">{float(c.get('price',0)):.2f}</span><span style="font-size:15px; color:{gain_c}; background:{gain_b}; padding:3px 8px; border-radius:4px; margin-left:10px; font-weight:bold;">{float(c.get('gain',0)):+.2f}%</span></div>
    <div style="font-size:14px; display:flex; align-items:center; color:#ccc;">近7日: {c.get('sparkline_html')}</div>
</div>
<div style="background:#0e1117; padding:8px; border-radius:4px; margin-bottom:10px; color:#eeeeee;">
    <div style="font-size:13px; margin-bottom:4px;"><span class="m-tooltip">總量<span class="m-tooltiptext">今日總成交張數</span></span>: <b style="color:#ffffff;">{int(c.get('vol',0)):,} K張</b> (<span style="color:{vol_c}; font-weight:bold;">{vol_t}</span>)</div>
    <div style="font-size:13px; display:flex; justify-content:space-between;">
        <span><span class="m-tooltip">爆量比<span class="m-tooltiptext">今日量 ÷ 5日均量</span></span>: <strong style="color:#e67e22;">{float(c.get('vol_ratio',0)):.1f}x</strong></span>
        <span style="color:#00FF00; font-weight:bold;">{c.get('intraday_str')}</span>
    </div>
</div>
<div class="zone-box">
    <div class="zone-title">❤️ 第一戰區：基本與財報面</div>
    <div style="font-size:13px; margin-bottom:4px;">{rev_html}</div>
    <div style="font-size:13px;">{div_html}</div>
</div>
<div class="zone-box">
    <div class="zone-title">⚔️ 第二戰區：技術與多空領先指標清單</div>
    <div style="font-size:13px; margin-bottom:4px; display:flex; justify-content:space-between;">
        <span>5MA: <b style="color:#ffffff;">{float(c.get('ma5',0)):.1f}</b></span><span>20MA: <b style="color:#ffffff;">{float(c.get('ma20',0)):.1f}</b></span><span>60MA: <b style="color:#ffffff;">{float(c.get('ma60',0)):.1f}</b></span>
    </div>
    <div style="display:flex; justify-content:space-between; font-size:13px;">
        <span>MACD 動能: <strong style="color:{c.get('macd_color')};" class="m-tooltip">{c.get('macd_str')}<span class="m-tooltiptext">紅柱多方動能、綠柱空方動能</span></strong></span>
        <span>KDJ 指標: <strong style="color:#f1c40f;" class="m-tooltip">{c.get('kdj_str')}<span class="m-tooltiptext">隨機指標轉折點</span></strong></span>
    </div>
    <div style="font-size:12px; color:#aaa; margin-top:6px; border-top:1px dashed #444; padding-top:4px;">
        <span style="color:#ff4d4d;">🔥進攻參考區間:</span> {c.get('atk_zone', '無')}<br>
        <span style="color:#00FF00;">🧊防守停損線:</span> {c.get('def_line', '無')}
    </div>
</div>
<div class="zone-box">
    <div class="shadow-box">
        <div class="zone-title">📊 第三戰區：三大法人與主力籌碼</div>
        <div style="font-size:13px; margin-bottom:4px;"><b>[外資]</b> 單日: <strong style="color:#ff4d4d;">{int(c.get('f_buy',0)):+,}張 (佔 {float(c.get('f_pct',0)):.1f}%)</strong> | 5日: <strong>{int(c.get('f_5d',0)):+,}張 (佔 {float(c.get('f_5d_pct',0)):.1f}%)</strong> | 10日: <strong>{int(c.get('f_10d',0)):+,}張 (佔 {float(c.get('f_10d_pct',0)):.1f}%)</strong></div>
        <div style="font-size:13px; margin-bottom:6px;"><b>[投信]</b> 單日: <strong style="color:#ff4d4d;">{int(c.get('t_buy',0)):+,}張 (佔 {float(c.get('t_pct',0)):.1f}%)</strong> | 5日: <strong>{int(c.get('t_5d',0)):+,}張 (佔 {float(c.get('t_5d_pct',0)):.1f}%)</strong> | 10日: <strong>{int(c.get('t_10d',0)):+,}張 (佔 {float(c.get('t_10d_pct',0)):.1f}%)</strong></div>
        <div style="font-size:12px; border-top:1px dashed #444; padding-top:6px; display:flex; justify-content:space-between; color:#aaa;">
            <span class="m-tooltip">千張大戶持股比率<span class="m-tooltiptext">大股東持股總比例</span></span>: <strong style="color:#00d2ff;">{c.get('big_holder',0)}%</strong>
            <span>自營商: {int(c.get('d_buy',0)):+,}張</span>
        </div>
    </div>
</div>
<div style="background:{c.get('signal_bg')}; padding:10px; border-radius:5px; text-align:center; margin-top:8px;"><strong style="color:{c.get('color_border')}; font-size:15px;">決策判定：{c.get('signal_text')}</strong></div>
</div>
"""
    return re.sub(r'^\s+', '', html, flags=re.MULTILINE)

# ==============================================================================
# V148 全新介面：三方會審與時光膠囊功能模組
# ==============================================================================
def render_action_buttons(card, code, is_portfolio):
    if code not in st.session_state.analysis_history:
        st.session_state.analysis_history[code] = {'nv_history': [], 'gm_history': [], 'cl_history': []}
        
    with st.expander("⚙️ 啟動資料校正與 AI 補給線", expanded=False):
        
        st.markdown("<div style='font-size:13px; font-weight:bold; color:#00d2ff;'>✏️ 手動覆寫營收資料 (永久寫入大腦)</div>", unsafe_allow_html=True)
        m_cols = st.columns([1, 1, 1])
        m_month = m_cols[0].text_input("月份 (如:06月)", value="06月", key=f"my_mo_{code}")
        m_y = m_cols[1].number_input("年增 (%)", -100.0, 1000.0, float(card.get('rev_yoy', 0.0)), 0.1, key=f"my_y_{code}")
        m_m = m_cols[2].number_input("月增 (%)", -100.0, 1000.0, float(card.get('rev_mom', 0.0)), 0.1, key=f"my_m_{code}")
        
        btn_rev1, btn_rev2 = st.columns(2)
        if btn_rev1.button("✅ 寫入營收", key=f"btn_override_{code}", use_container_width=True):
            st.session_state.revenue_override.update({code: {'yoy': m_y, 'mom': m_m, 'month': m_month}})
            save_local_db_isolated(); st.success("營收覆寫成功！"); time.sleep(0.5); st.rerun()
        if btn_rev2.button("🗑️ 清除自訂(恢復)", key=f"btn_clear_rev_{code}", use_container_width=True):
            st.session_state.revenue_override.pop(code, None)
            save_local_db_isolated(); st.success("已解除鎖定，恢復系統自動抓取！"); time.sleep(0.5); st.rerun()
            
        st.markdown("<div style='font-size:13px; font-weight:bold; color:#d200ff; margin-top:10px;'>✏️ 手動覆寫除權息資訊</div>", unsafe_allow_html=True)
        d_cols = st.columns([1.5, 1, 1])
        d_date = d_cols[0].text_input("日期 (如:20260715)", value="", key=f"my_d_d_{code}")
        d_c = d_cols[1].number_input("息 (元)", 0.0, 100.0, 0.0, 0.1, key=f"my_d_c_{code}")
        d_s = d_cols[2].number_input("權 (元)", 0.0, 100.0, 0.0, 0.1, key=f"my_d_s_{code}")
        if st.button("✅ 寫入除權息", key=f"btn_div_override_{code}", use_container_width=True):
            yld = (d_c / card.get('price',1)) * 100 if card.get('price',0) > 0 else 0
            disp = f"{d_date} | 息 {d_c}元 + 權 {d_s}元" if d_date else f"無日期 | 息 {d_c}元 + 權 {d_s}元"
            st.session_state.dividend_override.update({code: {"display": disp, "yield": yld}})
            save_local_db_isolated(); st.success("除權息覆寫成功！"); time.sleep(0.5); st.rerun()

        st.divider()
        c1, c2 = st.columns(2)
        if c1.button("🤖 委派 NVIDIA 營收特搜", key=f"btn_ai_rev_{code}", use_container_width=True):
            with st.spinner("NVIDIA 輪替火力網特搜中..."):
                success, result = execute_ai_revenue_fetch(code, card.get('name'))
                if success:
                    st.session_state.revenue_override.update({code: {'yoy': result.get('yoy', 0.0), 'mom': result.get('mom', 0.0), 'month': result.get('month', '最新')}})
                    save_local_db_isolated(); st.success(f"✅ 成功寫入！"); time.sleep(1); st.rerun()
                else: st.error(f"⚠️ {result}")
        if c2.button("🤖 委派 NVIDIA 除息特搜", key=f"btn_ai_div_{code}", use_container_width=True):
            with st.spinner("NVIDIA 除權息特搜中..."):
                success, result = execute_ai_dividend_fetch(code, card.get('name'), card.get('price'))
                if success:
                    st.session_state.dividend_override.update({code: result})
                    save_local_db_isolated(); st.success("✅ 成功寫入！"); time.sleep(1); st.rerun()
                else: st.error(f"⚠️ {result}")

    # --- V148 第一波攻擊：NVIDIA 單發指令 ---
    btn_cols = st.columns(2)
    if btn_cols[0].button("🤖 解鎖 NVIDIA 戰略推演", key=f"ai_single_{code}", use_container_width=True):
        st.session_state.single_ai_trigger = code
        with st.spinner("NVIDIA 輪替陣列推演中... (等待時間視模型而定)"):
            rep = execute_single_stock_ai_推演(card)
            st.session_state.single_ai_report.update({code: rep})
            ts = datetime.now().strftime("%Y-%m-%d %H:%M")
            st.session_state.analysis_history[code]['nv_history'].append({"time": ts, "report": rep})
            if len(st.session_state.analysis_history[code]['nv_history']) > 100:
                st.session_state.analysis_history[code]['nv_history'].pop(0)
            save_local_db_isolated()
            
    if btn_cols[1].button("📋 一鍵複製純數據 (折疊)", key=f"copy_{code}", use_container_width=True):
        srcs = getattr(st.session_state, 'intelligence_pool', {}).get(code, {}).get("sources", ["無紀錄"])
        tactical_data = f"""【54088 戰情室客觀基礎數據】\n標的：{code} {card.get('name')}\n最新股價：{card.get('price')} (漲跌 {card.get('gain'):.2f}%)\n籌碼面：外資 5日 {card.get('f_5d')}張, 大戶持股 {card.get('big_holder')}%\n技術面：5MA {card.get('ma5'):.1f}, MACD {card.get('macd_str')}, 爆量比 {card.get('vol_ratio'):.1f}x"""
        st.success("✅ 數據已生成！請點選右上角複製，貼給網頁版 NVIDIA / Gemini")
        st.code(tactical_data, language="text")
            
    if getattr(st.session_state, 'single_ai_trigger', '') == code and code in getattr(st.session_state, 'single_ai_report', {}):
        st.info(st.session_state.single_ai_report.get(code))

    # --- V148 核心區：三方會審多框 UI ---
    st.markdown("---")
    with st.expander("📥 貼上外部網頁版情報與裁決 (三方會審區)", expanded=False):
        st.markdown("將 NVIDIA 網頁版或 Gemini 產出的報告貼入，進行一鍵打包或歷史歸檔。")
        c1, c2 = st.columns(2)
        nv_val = c1.text_area("📝 NVIDIA (DeepSeek) 網頁版報告", height=120, key=f"nv_txt_{code}")
        gm_val = c2.text_area("📝 Gemini 分析報告", height=120, key=f"gm_txt_{code}")
        cl_val = st.text_area("👑 Claude 總裁決報告 (將存入 100 筆歷史紀錄)", height=120, key=f"cl_txt_{code}")

        bc1, bc2 = st.columns(2)
        if bc1.button("🚀 一鍵打包送交 Claude", key=f"pack_{code}", use_container_width=True):
            mega_prompt = f"""【系統提示：請以最高階戰略總裁決身分，進行台股多空深度判定】

一、 戰情室完整客觀基礎數據（請務必嚴格依此數據進行推演）：
* 標的：{card.get('name')} ({code})
* 價格與動能：現價 {card.get('price')} | 漲跌幅 {card.get('gain'):.2f}% | 爆量比 {card.get('vol_ratio'):.1f}x
* 基本面：營收 YoY {card.get('rev_yoy')}% | 營收 MoM {card.get('rev_mom')}% | 現金殖利率 {card.get('div_yield'):.1f}%
* 技術面：5MA {card.get('ma5'):.1f} | 20MA {card.get('ma20'):.1f} | 60MA {card.get('ma60'):.1f} | MACD {card.get('macd_str')} | KDJ {card.get('kdj_str')}
* 籌碼面 (近5日)：外資 {card.get('f_5d')}張 | 投信 {card.get('t_5d')}張 | 大戶持股 {card.get('big_holder')}%

二、 NVIDIA 幕僚獨立推演視角：
{nv_val if nv_val else "(無)"}

三、 Gemini 幕僚獨立推演視角：
{gm_val if gm_val else "(無)"}

四、 總裁決終極任務：
1. 請先根據「第一段的客觀數據」，給出 Claude 您自己獨立的基本/技術/籌碼面分析與多空判定。
2. 接著，請綜合比對 NVIDIA 與 Gemini 的觀點，指出這兩派幕僚是否有盲點或分歧。
3. 最後，給出總指揮官明日開盤的「具體實戰操作建議 (進攻/防守/觀望)」。"""
            st.success("✅ Mega-Prompt 條列式數據整合完畢！請複製下方內容交給 Claude：")
            st.code(mega_prompt, language="text")

        if bc2.button("💾 儲存 Claude 裁決至時光膠囊", key=f"save_cl_{code}", use_container_width=True):
            if cl_val:
                ts = datetime.now().strftime("%Y-%m-%d %H:%M")
                
                env_tag = "[⏳ 沉澱盤整]"
                if card.get('price') > card.get('ma5') and card.get('vol_ratio') > 1.5:
                    env_tag = "[🔥 起漲點火 / 強勢大買]"
                elif card.get('price') < card.get('ma20') and card.get('macd_str') == "空方動能":
                    env_tag = "[💀 恐慌殺盤 / 惡劣環境]"

                hist_entry = {
                    "time": ts,
                    "env_tag": env_tag,
                    "report": cl_val,
                    "snapshot": f"收盤:{card.get('price')} | 外資:{card.get('f_5d')}張 | {card.get('macd_str')} | 爆量:{card.get('vol_ratio'):.1f}x"
                }
                
                st.session_state.analysis_history[code]['cl_history'].append(hist_entry)
                
                if len(st.session_state.analysis_history[code]['cl_history']) > 100:
                    removed = False
                    for i in range(len(st.session_state.analysis_history[code]['cl_history'])):
                        if "⏳" in st.session_state.analysis_history[code]['cl_history'][i]['env_tag']:
                            st.session_state.analysis_history[code]['cl_history'].pop(i)
                            removed = True
                            break
                    if not removed:
                        st.session_state.analysis_history[code]['cl_history'].pop(0)

                if gm_val:
                    st.session_state.analysis_history[code]['gm_history'].append({"time": ts, "report": gm_val})
                    if len(st.session_state.analysis_history[code]['gm_history']) > 100:
                        st.session_state.analysis_history[code]['gm_history'].pop(0)

                save_local_db_isolated()
                st.success("✅ 總裁決與數據快照已寫入時光膠囊！(具備黃金樣本篩選保護)")
                time.sleep(1); st.rerun()
            else:
                st.warning("⚠️ 請先輸入 Claude 裁決報告！")

    # --- V148 時光膠囊歷史覆盤區 ---
    has_nv = len(st.session_state.analysis_history.get(code, {}).get('nv_history', [])) > 0
    has_gm = len(st.session_state.analysis_history.get(code, {}).get('gm_history', [])) > 0
    has_cl = len(st.session_state.analysis_history.get(code, {}).get('cl_history', [])) > 0

    if has_nv or has_gm or has_cl:
        with st.expander("🗂️ 歷史時光膠囊覆盤區 (AI 裁決記憶體)", expanded=False):
            h1, h2, h3 = st.tabs(["NVIDIA 歷史 (近 10 筆)", "Gemini 歷史 (近 10 筆)", "Claude 總裁決 (近 20 筆)"])
            with h1:
                for h in reversed(st.session_state.analysis_history.get(code, {}).get('nv_history', [])[-10:]):
                    st.markdown(f"**🕒 {h['time']}**")
                    st.info(h['report'])
            with h2:
                for h in reversed(st.session_state.analysis_history.get(code, {}).get('gm_history', [])[-10:]):
                    st.markdown(f"**🕒 {h['time']}**")
                    st.info(h['report'])
            with h3:
                for h in reversed(st.session_state.analysis_history.get(code, {}).get('cl_history', [])[-20:]):
                    st.markdown(f"**🕒 {h['time']} <span style='color:#f1c40f;'>{h.get('env_tag', '')}</span>**", unsafe_allow_html=True)
                    st.caption(f"📊 當時數據快照：{h.get('snapshot', '無紀錄')}")
                    st.success(h['report'])

    m_cols = st.columns(2)
    if is_portfolio:
        if m_cols[0].button("從持倉移除", key=f"del_port_{code}", use_container_width=True):
            st.session_state.portfolio.pop(code, None); save_local_db_isolated(); st.rerun()
    else:
        if m_cols[0].button("轉移至持倉", key=f"mov_pin_{code}", use_container_width=True):
            st.session_state.portfolio.update({code: {"entry_price": card.get('price', 0.0), "qty": 1}})
            st.session_state.pinned_stocks.pop(code, None); save_local_db_isolated(); st.rerun()
        if m_cols[1].button("移出雷達", key=f"del_pin_{code}", use_container_width=True):
            st.session_state.pinned_stocks.pop(code, None); save_local_db_isolated(); st.rerun()

# 渲染模擬倉與觀察雷達區塊
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
    all_sources = list(set(st.session_state.pinned_stocks.values()))
    filter_src = st.selectbox("🎯 篩選特定戰術血統標的", ["全部顯示"] + all_sources)
    with st.expander("🎯 總指揮常態觀測雷達防線", expanded=True):
        cols, idx = st.columns(2), 0
        for code, blood_label in list(st.session_state.pinned_stocks.items()):
            if filter_src != "全部顯示" and blood_label != filter_src: continue
            c = calculate_comprehensive_signals(code, False)
            if c:
                with cols[idx % 2]:
                    if c.get('error', False):
                        st.warning(f"⚠️ {c.get('code')} 連線超時，隔離保護。")
                        continue
                    st.markdown(render_commander_stock_card(c), unsafe_allow_html=True)
                    render_action_buttons(c, code, False)
                idx += 1

# ==============================================================================
# 十二、 全市場戰略條件掃描 (絕對防禦 AND 邏輯)
# ==============================================================================
if getattr(st.session_state, 'trigger_scan', False):
    st.session_state.trigger_scan = False
    st.session_state.scan_results.clear()
    
    results = []
    target_pool = GLOBAL_MARKET_CODES[:300] 
    total_targets = len(target_pool)
    
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    for i, c in enumerate(target_pool):
        status_text.markdown(f"<div style='color:#00d2ff; font-size:13px; font-weight:bold;'>📡 掃描進度: {i+1}/{total_targets} ({int((i+1)/total_targets*100)}%) - 正在解析 {c}</div>", unsafe_allow_html=True)
        progress_bar.progress((i + 1) / total_targets)
        
        card = calculate_comprehensive_signals(c, enable_doomsday_lock)
        
        c_vol = float(card.get('vol', 0) or 0)
        c_price = float(card.get('price', 0) or 0)
        c_ma60 = float(card.get('ma60', 0) or 0)
        c_vol_ratio = float(card.get('vol_ratio', 0) or 0)
        c_vol_chg = float(card.get('vol_change_pct', 0) or 0)
        c_score = int(card.get('bull_score', 0) or 0)
        c_tbuy = int(card.get('t_buy', 0) or 0)
        c_fbuy = int(card.get('f_buy', 0) or 0)
        c_margin = int(card.get('margin_diff', 0) or 0)
        c_rev_yoy = float(card.get('rev_yoy', 0) or 0)
        c_div = float(card.get('div_yield', 0) or 0)
        c_kdj = str(card.get('kdj_str', ''))
        
        if card and not card.get('error', False) and c_vol >= (min_volume_filter / 1000):
            meets_all_conditions = True
            for cmd in selected_cmds:
                if "查1." in cmd:
                    if not (card.get('is_first_red') and c_vol_ratio >= 2.0 and "金叉" in c_kdj): meets_all_conditions = False
                elif "查2." in cmd:
                    if not (c_price > c_ma60 and c_vol_ratio >= 1.2): meets_all_conditions = False
                elif "查3." in cmd:
                    if not (c_score >= 60 and not card.get('mine_tags')): meets_all_conditions = False
                elif "查4." in cmd:
                    if not (c_tbuy > 0): meets_all_conditions = False
                elif "查5." in cmd:
                    if not (c_fbuy > 0 and c_margin < 0): meets_all_conditions = False
                elif "查6." in cmd:
                    if not (c_rev_yoy > 20): meets_all_conditions = False
                elif "查8." in cmd:
                    if not (card.get('is_yesterday_strong')): meets_all_conditions = False
                elif "查9." in cmd:
                    if not (c_vol_ratio >= 2.0): meets_all_conditions = False
                elif "查10." in cmd:
                    if not (c_vol_chg < -40 and c_margin < 0): meets_all_conditions = False
                elif "查11." in cmd:
                    if not (c_div >= min_yield_filter): meets_all_conditions = False
                elif "查12." in cmd:
                    if not selected_k_patterns or not any(p in [x.get('text') for x in card.get('detected_patterns',[])] for p in selected_k_patterns): meets_all_conditions = False
                
                elif "情報雷達：" in cmd:
                    src_name = cmd.split("情報雷達：")[-1]
                    if src_name not in getattr(st.session_state, 'intelligence_pool', {}).get(c, {}).get("sources", []):
                        meets_all_conditions = False
                elif "情報黃金交叉" in cmd:
                    if len(getattr(st.session_state, 'intelligence_pool', {}).get(c, {}).get("sources", [])) < 2:
                        meets_all_conditions = False
            
            if meets_all_conditions:
                results.append(card)
            
    progress_bar.empty()
    status_text.empty()
    st.session_state.scan_results = results
    st.session_state.scan_mode = " + ".join([cmd.split('.')[0] for cmd in selected_cmds])

if getattr(st.session_state, 'scan_results', []):
    st.markdown(f"### ⚡ 【{st.session_state.scan_mode}】交叉篩選戰果 ({len(st.session_state.scan_results)} 檔符合)")
    if st.button("➕ 批次部署並強制寫入常態追蹤雷達", use_container_width=True):
        for card in st.session_state.scan_results:
            st.session_state.pinned_stocks.update({card.get('code', ''): st.session_state.scan_mode})
        save_local_db_isolated()
        st.success(f"✅ 成功綁定血統並永久存檔。")
        time.sleep(0.5); st.rerun()
        
    table_rows = []
    for card in st.session_state.scan_results:
        table_rows.append({"代號": card.get('code'), "名稱": card.get('name'), "現價": card.get('price'), "漲跌(%)": round(float(card.get('gain',0)), 2), "年增(%)": round(float(card.get('rev_yoy',0)), 1), "月增(%)": round(float(card.get('rev_mom',0)), 1), "殖利率(%)": f"{float(card.get('div_yield',0)):.1f}%", "地雷標記": len(card.get('mine_tags',[]))})
    st.dataframe(pd.DataFrame(table_rows), use_container_width=True, hide_index=True)
    
    cols = st.columns(2)
    for idx, card in enumerate(st.session_state.scan_results):
        with cols[idx % 2]:
            st.markdown(re.sub(r'^\s+', '', f"""<div style="border:2px solid {card.get('color_border')}; border-radius:8px; padding:15px; background:#16191f; margin-bottom:12px; color:#eeeeee;"><span style="font-weight:bold; font-size:19px; color:#ffffff;">{card.get('name')} <span style="color:#00d2ff;">({card.get('code')})</span></span><div style="font-size:13px; margin-top:5px;">多重火力篩選符合 | 爆量比: {float(card.get('vol_ratio',0)):.1f}x</div></div>""", flags=re.MULTILINE), unsafe_allow_html=True)
