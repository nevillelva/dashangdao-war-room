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
# 一、 系統安全防禦與法規合規宣告
# ==============================================================================
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
warnings.filterwarnings('ignore')

st.set_page_config(layout="wide", page_title="54088 戰情室 V133 絕對防護版", initial_sidebar_state="expanded")

GOV_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*"
}

USER_DB_FILE = "54088_database.json" 
INST_HISTORY_FILE = "54088_inst_history_v30d.json"

# ==============================================================================
# 二、 記憶體快取隔離與全域初始化 (嚴格多行安全寫法，徹底解決崩潰)
# ==============================================================================
def init_session_state():
    if 'db_loaded' not in st.session_state:
        st.session_state['db_loaded'] = False
    if 'pinned_stocks' not in st.session_state:
        st.session_state['pinned_stocks'] = {}
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

init_session_state()

def load_and_isolate_db():
    if not st.session_state['db_loaded']:
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
        "pinned_stocks": st.session_state['pinned_stocks'], 
        "portfolio": st.session_state['portfolio']
    }
    try:
        with open(USER_DB_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=4)
        if st.session_state['inst_history']:
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
    if not FINMIND_TOKENS:
        FINMIND_TOKENS = [""]
except KeyError:
    GEMINI_API_KEYS = [""]
    FINMIND_TOKENS = [""]

def safe_float(val):
    if pd.isna(val) or val is None or str(val).strip() == '':
        return 0.0
    try:
        s = str(val).upper().replace(',', '').replace('-', '').strip()
        s = re.sub(r'[^\d.]', '', s)
        return float(s) if s else 0.0
    except Exception:
        return 0.0

def get_industry_label_wrapper(code):
    c = str(code)
    if c.startswith('11'): return "水泥"
    elif c.startswith('12'): return "食品"
    elif c.startswith('13'): return "塑膠"
    elif c.startswith('14'): return "紡織"
    elif c.startswith('15'): return "電機"
    elif c.startswith('16'): return "電纜"
    elif c.startswith(('17', '41', '47', '65')): return "生技"
    elif c.startswith('20'): return "鋼鐵"
    elif c.startswith('22'): return "汽車"
    elif c.startswith(('23', '24', '30', '31', '35', '80', '64')): return "電子/半導體"
    elif c.startswith('25'): return "營造"
    elif c.startswith('26'): return "航運"
    elif c.startswith(('28', '58')): return "金融"
    return "綜合"

# ==============================================================================
# 三、 真實大數據抓取管線 (Real API Pipelines)
# ==============================================================================
@st.cache_resource
def get_safe_session():
    session = requests.Session()
    session.headers.update(GOV_HEADERS)
    return session

@st.cache_data(ttl=86400, show_spinner=False)
def fetch_tw_revenue():
    rev_db = {}
    urls = ["https://openapi.twse.com.tw/v1/opendata/t187ap05_L", "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap05_O"]
    for url in urls:
        try:
            res = requests.get(url, headers=GOV_HEADERS, verify=False, timeout=5)
            if res.status_code == 200:
                for item in res.json():
                    c = str(item.get('公司代號', '')).strip()
                    if len(c) == 4:
                        yoy = safe_float(item.get('當月營收較去年當月增減百分比', 0))
                        mom = safe_float(item.get('上月比較增減(%)', 0))
                        rev_db[c] = {'yoy': yoy, 'mom': mom}
        except Exception:
            pass
    return rev_db

@st.cache_data(ttl=86400, show_spinner=False)
def fetch_stock_names():
    names = {}
    urls = ["https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL", "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_peratio_analysis"]
    for url in urls:
        try:
            res = requests.get(url, headers=GOV_HEADERS, verify=False, timeout=5)
            if res.status_code == 200:
                for item in res.json():
                    c = str(item.get('Code', item.get('SecuritiesCompanyCode', ''))).strip()
                    n = str(item.get('Name', item.get('CompanyName', ''))).strip()
                    if len(c) == 4 and c.isdigit() and n:
                        names[c] = n
        except Exception:
            pass
    fallbacks = {"2330":"台積電", "2303":"聯電", "2317":"鴻海", "2308":"台達電", "5871":"中租-KY", "3481":"群創", "2454":"聯發科", "1101":"台泥"}
    for k, v in fallbacks.items():
        if k not in names:
            names[k] = v
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
                    divs[c] = {'date': str(item.get('除權息日期', '')).strip(), 'cash': safe_float(item.get('現金股利', 0))}
    except Exception:
        pass
    return divs

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
    except Exception:
        pass
    return "API連線中...", False, 0.0

@st.cache_data(ttl=120, show_spinner=False)
def get_real_stock_data_yfinance(symbol):
    session = get_safe_session()
    for ext in [".TW", ".TWO"]:
        try:
            tk = yf.Ticker(symbol + ext, session=session)
            hist = tk.history(period="3mo").dropna(subset=['Close'])
            hist_1m = tk.history(period="1d", interval="1m").dropna(subset=['Close'])
            if not hist.empty and len(hist) > 10:
                info = tk.info
                return hist.tail(30), hist_1m, info
        except Exception:
            pass
    return None, None, {}

TW_STOCK_NAMES = fetch_stock_names()
TW_REVENUE_DB = fetch_tw_revenue()
DIVIDEND_DB = fetch_twse_dividends()
GLOBAL_MARKET_CODES = list(TW_STOCK_NAMES.keys())
weather_str, is_panic, global_twii_gain = get_market_weather_real()

# ==============================================================================
# 四、 視覺與型態學引擎 (Visual & Pattern Engine)
# ==============================================================================
def generate_bi_color_sparkline(closes_list):
    if not closes_list or len(closes_list) < 2:
        return "<span style='color:#888;'>▃</span>"
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
    
    if (c0 > o0) and body0 > (c0 * 0.03):
        if (c1 < o1) and c0 > o1 and o0 < c1:
            patterns.append({"text": "長紅吞噬", "class": "tag-red"})
        else:
            patterns.append({"text": "低檔長紅", "class": "tag-red"})
    if (c0 > o0) and (c1 > o1) and (c2 > o2) and (c0 > c1 > c2):
        patterns.append({"text": "紅三兵", "class": "tag-red"})
        
    if (c0 < o0) and body0 > (c0 * 0.03):
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
    
    curr_price = float(hist['Close'].iloc[-1])
    prev_price = float(hist['Close'].iloc[-2])
    open_price = float(hist['Open'].iloc[-1])
    gain = ((curr_price - prev_price) / prev_price) * 100 if prev_price > 0 else 0
    
    vol_today = int(hist['Volume'].iloc[-1] / 1000)
    vol_yesterday = max(1, int(hist['Volume'].iloc[-2] / 1000))
    vol_change_pct = ((vol_today - vol_yesterday) / vol_yesterday) * 100
    vol_5d_mean = max(1, hist['Volume'].tail(5).mean() / 1000)
    vol_ratio = vol_today / vol_5d_mean
    
    # 完美保留 V132 的技術指標 (均線、MACD、KDJ)
    ma5 = float(hist['Close'].tail(5).mean())
    ma10 = float(hist['Close'].tail(10).mean())
    ma20 = float(hist['Close'].tail(20).mean())
    ma60 = float(hist['Close'].mean()) 
    
    exp1 = hist['Close'].ewm(span=12, adjust=False).mean()
    exp2 = hist['Close'].ewm(span=26, adjust=False).mean()
    macd = exp1 - exp2
    macd_hist = macd - macd.ewm(span=9, adjust=False).mean()
    macd_val = macd_hist.iloc[-1] if not macd_hist.empty else 0
    macd_str = "📈 動能增強(紅柱)" if macd_val > 0 else "📉 空方增強(綠柱)"
    macd_color = "#ff4d4d" if macd_val > 0 else "#00FF00"
    
    low_min = hist['Low'].rolling(9).min()
    high_max = hist['High'].rolling(9).max()
    rsv = (hist['Close'] - low_min) / (high_max - low_min + 1e-9) * 100
    calc_k = rsv.bfill().ffill().ewm(com=2, adjust=False).mean()
    calc_d = calc_k.bfill().ffill().ewm(com=2, adjust=False).mean()
    kdj_str = "金叉向上" if calc_k.iloc[-1] > calc_d.iloc[-1] else "死叉向下"
    
    # 從 inst_history 取出大數據
    f_buy = t_buy = d_buy = margin_diff = big_holder = retail_holder = 0
    sorted_dates = sorted(st.session_state['inst_history'].keys(), reverse=True)
    if sorted_dates and symbol in st.session_state['inst_history'][sorted_dates[0]]:
        mem = st.session_state['inst_history'][sorted_dates[0]][symbol]
        f_buy = mem.get('foreign', 0)
        t_buy = mem.get('trust', 0)
        d_buy = mem.get('dealer', 0)
        margin_diff = mem.get('margin', 0)
        big_holder = mem.get('big_holder', 0.0)
    
    # 財報基本面
    rev_data = TW_REVENUE_DB.get(symbol, {'yoy': 0.0, 'mom': 0.0})
    rev_yoy = rev_data['yoy']
    rev_mom = rev_data['mom']
    
    div_info = DIVIDEND_DB.get(symbol)
    if div_info:
        div_display = f"{div_info['date']} | {div_info['cash']}元"
        div_yield = (div_info['cash'] / curr_price) * 100 if curr_price > 0 else 0.0
    else:
        div_display = "無近期除權息"; div_yield = 0.0
        
    debt_ratio = info.get('debtToEquity', 0)
    op_cashflow = info.get('operatingCashflow', 0)
    net_income = info.get('netIncome', 0)
    consensus_target = info.get('targetMeanPrice', curr_price)
    potential_roi = round(((consensus_target - curr_price) / curr_price) * 100, 1) if curr_price > 0 else 0.0
    
    mine_tags = []
    if debt_ratio > 75.0: mine_tags.append("高負債比")
    if net_income > 0 and op_cashflow < 0: mine_tags.append("有獲利無現金(盈餘品質異常)")
    
    # 多空優勢清單 (V132 + V133)
    multi_bull = []
    multi_bear = []
    if curr_price > ma5: multi_bull.append("☑️ 站上5日線")
    else: multi_bear.append("❌ 跌破5日線")
    if curr_price > ma20: multi_bull.append("☑️ 站上月線(20MA)")
    else: multi_bear.append("❌ 跌破月線")
    if f_buy > 0: multi_bull.append("☑️ 外資買超")
    if t_buy > 0: multi_bull.append("☑️ 投信買超")
    if margin_diff < 0: multi_bull.append("☑️ 融資減少(籌碼沉澱)")
    else: multi_bear.append("❌ 融資增加")
    if rev_yoy > 20.0: multi_bull.append("☑️ 營收雙增(YoY>20%)")
    if "金叉" in kdj_str: multi_bull.append("☑️ KDJ 金叉")
    
    detected_patterns = detect_k_line_patterns_v133(hist)
    for p in detected_patterns:
        if "長紅" in p['text'] or "紅三兵" in p['text']: multi_bull.append(f"☑️ {p['text']}")
        else: multi_bear.append(f"❌ {p['text']}")
        
    total_checks = len(multi_bull) + len(multi_bear)
    bull_score = int((len(multi_bull) / total_checks) * 100) if total_checks > 0 else 50
    trend_label = "<span class='tag-red'>[短強]</span>" if curr_price > ma5 else "<span class='tag-green'>[短弱]</span>"
    
    if enable_doomsday and rev_yoy <= 20.0:
        return None
        
    signal_text = "[🔥 偏多攻擊]" if (curr_price > ma5 and f_buy > 0) else ("[🚨 撤退警告]" if curr_price < ma5 else "[⚠️ 整理觀望]")
    color_border = "#ff4d4d" if "攻擊" in signal_text else ("#00FF00" if "警告" in signal_text else "#f1c40f")
    signal_bg = "#3a1515" if "攻擊" in signal_text else ("#153a20" if "警告" in signal_text else "#332b00")
        
    return {
        "code": symbol, "name": TW_STOCK_NAMES.get(symbol, symbol), "price": curr_price, "gain": gain,
        "open": open_price, "high": float(hist['High'].iloc[-1]), "low": float(hist['Low'].iloc[-1]),
        "vol": vol_today, "vol_change_pct": vol_change_pct, "vol_ratio": vol_ratio,
        "ma5": ma5, "ma10": ma10, "ma20": ma20, "ma60": ma60,
        "macd_str": macd_str, "macd_color": macd_color, "kdj_str": kdj_str,
        "f_buy": f_buy, "t_buy": t_buy, "d_buy": d_buy, "margin_diff": margin_diff, "big_holder": big_holder,
        "rev_yoy": rev_yoy, "rev_mom": rev_mom, "div_display": div_display, "div_yield": div_yield,
        "consensus_target": consensus_target, "potential_roi": potential_roi,
        "mine_tags": mine_tags, "bull_score": bull_score, "trend_label": trend_label,
        "multi_bull": multi_bull, "multi_bear": multi_bear,
        "signal_text": signal_text, "color_border": color_border, "signal_bg": signal_bg,
        "sparkline_html": generate_bi_color_sparkline(hist['Close'].tail(7).tolist()), 
        "intraday_str": get_intraday_trend(hist_1m),
        "detected_patterns": detected_patterns, "sector": get_industry_label_wrapper(symbol),
        "is_first_red": (gain > 0 and curr_price > open_price and curr_price > ma5 and prev_price < ma5),
        "is_yesterday_strong": (gain > 0 and len(hist)>2 and ((prev_price - float(hist['Close'].iloc[-3]))/float(hist['Close'].iloc[-3])*100 > 5.0))
    }

# ==============================================================================
# 六、 FinMind 多執行緒歷史回填 (斷點續傳)
# ==============================================================================
def execute_heavy_data_sync(target_codes, target_date):
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    if target_date not in st.session_state['inst_history']: 
        st.session_state['inst_history'][target_date] = {}
        
    missing = [c for c in target_codes if c not in st.session_state['inst_history'][target_date]]
    if not missing:
        st.success("✅
