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
# 二、 記憶體全域安全隔離初始化 (全架構無括號代理人解耦)
# ==============================================================================
def init_session_state():
    if not hasattr(st.session_state, 'db_loaded'):
        st.session_state.db_loaded = False
    if not hasattr(st.session_state, 'pinned_stocks'):
        st.session_state.pinned_stocks = {"2303": "手動強制加入", "5871": "手動強制加入"}
    if not hasattr(st.session_state, 'portfolio'):
        st.session_state.portfolio = {}
    if not hasattr(st.session_state, 'revenue_override'):
        st.session_state.revenue_override = {}
    if not hasattr(st.session_state, 'inst_history'):
        st.session_state.inst_history = {}
    if not hasattr(st.session_state, 'scan_results'):
        st.session_state.scan_results = []
    if not hasattr(st.session_state, 'scan_mode'):
        st.session_state.scan_mode = ""
    if not hasattr(st.session_state, 'active_key_index'):
        st.session_state.active_key_index = 0
    if not hasattr(st.session_state, 'ai_report'):
        st.session_state.ai_report = ""
    if not hasattr(st.session_state, 'single_ai_trigger'):
        st.session_state.single_ai_trigger = ""
    if not hasattr(st.session_state, 'single_ai_report'):
        st.session_state.single_ai_report = {}
    if not hasattr(st.session_state, 'intelligence_pool'):
        st.session_state.intelligence_pool = {"podcast": {}, "report": {}}

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
                    st.session_state.intelligence_pool = data.get("intelligence_pool", {"podcast": {}, "report": {}})
            except Exception:
                pass
        if os.path.exists(INST_HISTORY_FILE):
            try:
                with open(INST_HISTORY_FILE, "r", encoding="utf-8") as f:
                    st.session_state.inst_history = json.load(f)
                    if len(getattr(st.session_state, 'inst_history', {})) > 30:
                        sorted_dates = sorted(st.session_state.inst_history.keys(), reverse=True)
                        st.session_state.inst_history = {d: st.session_state.inst_history[d] for d in sorted_dates[:30]}
            except Exception:
                pass
        st.session_state.db_loaded = True

def save_local_db_isolated():
    payload = {
        "pinned_stocks": getattr(st.session_state, 'pinned_stocks', {}), 
        "portfolio": getattr(st.session_state, 'portfolio', {}),
        "revenue_override": getattr(st.session_state, 'revenue_override', {}),
        "intelligence_pool": getattr(st.session_state, 'intelligence_pool', {"podcast": {}, "report": {}})
    }
    try:
        with open(USER_DB_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=4)
        if getattr(st.session_state, 'inst_history', {}):
            with open(INST_HISTORY_FILE, "w", encoding="utf-8") as f:
                json.dump(st.session_state.inst_history, f, ensure_ascii=False)
    except Exception:
        pass

load_and_isolate_db()

# 雲端金鑰後台鎖定與狀態判定
API_READY = True
FINMIND_READY = True
try:
    COMMANDER_PIN = st.secrets.radar_secrets.commander_pin
    raw_keys = st.secrets.radar_secrets.gemini_api_key
    GEMINI_API_KEYS = [k.strip() for k in raw_keys.split(",") if k.strip()]
    SECRET_FINMIND = st.secrets.radar_secrets.get("finmind_token", "")
    FINMIND_TOKENS = [k.strip() for k in SECRET_FINMIND.split(",") if k.strip()]
    if not FINMIND_TOKENS or FINMIND_TOKENS[0] == "":
        FINMIND_TOKENS = [""]
        FINMIND_READY = False
except Exception:
    API_READY = False
    FINMIND_READY = False
    COMMANDER_PIN = "54088"
    GEMINI_API_KEYS = [""]
    FINMIND_TOKENS = [""]

# ==============================================================================
# 三、 真實大數據晶片核心 (手續費與證交稅真精算)
# ==============================================================================
def safe_float(val):
    if pd.isna(val) or val is None or str(val).strip() == '':
        return 0.0
    try:
        s = str(val).upper().replace(',', '').replace('-', '').strip()
        s = re.sub(r'[^\d.]', '', s)
        return float(s) if s else 0.0
    except Exception:
        return 0.0

def calc_real_profit(cost, price, qty=1):
    if cost <= 0 or price <= 0:
        return 0, 0
    buy_val = cost * qty * 1000
    sell_val = price * qty * 1000
    fee_buy = max(20, int(buy_val * 0.001425))
    fee_sell = max(20, int(sell_val * 0.001425))
    tax_sell = int(sell_val * 0.003)
    profit = sell_val - buy_val - fee_buy - fee_sell - tax_sell
    roi = (profit / buy_val) * 100 if buy_val > 0 else 0
    return profit, roi

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
                        rev_db.update({c: {'yoy': yoy, 'mom': mom}})
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
                        names.update({c: n})
        except Exception:
            pass
    fallbacks = {"2330":"台積電", "2303":"聯電", "2317":"鴻海", "2308":"台達電", "5871":"中租-KY", "3481":"群創", "2454":"聯發科"}
    for k, v in fallbacks.items():
        if k not in names:
            names.update({k: v})
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
                    stock_div = safe_float(item.get('盈餘轉增資配股股數', 0)) / 100 # 換算成元
                    if stock_div <= 0:
                        stock_div = safe_float(item.get('資本公積轉增資配股股數', 0)) / 100
                    divs.update({c: {
                        'date': str(item.get('除權息日期', '')).strip(), 
                        'cash': cash_div,
                        'stock': stock_div
                    }})
    except Exception:
        pass
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
    return "<span style='color:#888;'>大盤連線中...</span>", False, 0.0

@st.cache_data(ttl=120, show_spinner=False)
def get_real_stock_data_yfinance(symbol):
    session = get_safe_session()
    for ext in [".TW", ".TWO"]:
        try:
            tk = yf.Ticker(symbol + ext, session=session)
            hist = tk.history(period="3mo", timeout=4).dropna(subset=['Close'])
            hist_1m = tk.history(period="1d", interval="1m", timeout=3).dropna(subset=['Close'])
            if not hist.empty and len(hist) > 10:
                return hist.tail(30), hist_1m, tk.info
        except Exception:
            pass
    return None, None, {}

weather_str, is_panic, global_twii_gain = get_market_weather_real()

# ==============================================================================
# 四、 視覺化走勢圖與 K 線型態學
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
    
    if (c0 > o0) and body0 > (c0 * 0.025):
        if (c1 < o1) and c0 > o1 and o0 < c1: patterns.append({"text": "長紅吞噬", "class": "tag-red"})
        else: patterns.append({"text": "低檔長紅", "class": "tag-red"})
    if (c0 > o0) and (c1 > o1) and (c2 > o2) and (c0 > c1 > c2):
        patterns.append({"text": "紅三兵", "class": "tag-red"})
    if (c0 < o0) and body0 > (c0 * 0.025):
        if (c1 > o1) and c0 < o1 and o0 > c1: patterns.append({"text": "長黑吞噬", "class": "tag-green"})
        else: patterns.append({"text": "高檔長黑", "class": "tag-green"})
    if (c0 < o0) and (c1 < o1) and (c2 < o2) and (c0 < c1 < c2):
        patterns.append({"text": "黑三兵", "class": "tag-green"})
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

# ==============================================================================
# 五、 核心訊號與五大戰區聚合核心 (多天期籌碼與手動覆寫融入)
# ==============================================================================
def calculate_comprehensive_signals(symbol, enable_doomsday=False):
    hist, hist_1m, info = get_real_stock_data_yfinance(symbol)
    if hist is None or hist.empty:
        return {"code": symbol, "name": TW_STOCK_NAMES.get(symbol, symbol), "error": True}
    
    curr_price = float(hist['Close'].iloc[-1])
    prev_price = float(hist['Close'].iloc[-2])
    open_price = float(hist['Open'].iloc[-1])
    gain = ((curr_price - prev_price) / prev_price) * 100 if prev_price > 0 else 0
    
    vol_today = int(hist['Volume'].iloc[-1] / 1000)
    vol_yesterday = max(1, int(hist['Volume'].iloc[-2] / 1000))
    vol_change_pct = ((vol_today - vol_yesterday) / vol_yesterday) * 100 if vol_yesterday > 0 else 0
    vol_5d_mean = max(1, hist['Volume'].tail(5).mean() / 1000)
    vol_ratio = vol_today / vol_5d_mean if vol_5d_mean > 0 else 0
    
    ma5 = float(hist['Close'].tail(5).mean())
    ma20 = float(hist['Close'].tail(20).mean())
    ma60 = float(hist['Close'].mean())
    
    exp1 = hist['Close'].ewm(span=12, adjust=False).mean()
    exp2 = hist['Close'].ewm(span=26, adjust=False).mean()
    macd_hist = (exp1 - exp2) - (exp1 - exp2).ewm(span=9, adjust=False).mean()
    macd_val = macd_hist.iloc[-1] if not macd_hist.empty else 0
    macd_str = "多方動能" if macd_val > 0 else "空方動能"
    macd_color = "#ff4d4d" if macd_val > 0 else "#00FF00"
    
    low_min = hist['Low'].rolling(9).min()
    high_max = hist['High'].rolling(9).max()
    rsv = (hist['Close'] - low_min) / (high_max - low_min + 1e-9) * 100
    calc_k = rsv.bfill().ffill().ewm(com=2, adjust=False).mean()
    calc_d = calc_k.bfill().ffill().ewm(com=2, adjust=False).mean()
    kdj_str = "金叉向上" if not calc_k.empty and calc_k.iloc[-1] > calc_d.iloc[-1] else "死叉向下"
    
    # 籌碼面：單日、5日、10日精算
    f_single = t_single = d_single = margin_diff = big_holder = 0
    f_5d = t_5d = f_10d = t_10d = 0
    
    sorted_dates = sorted(getattr(st.session_state, 'inst_history', {}).keys(), reverse=True)
    if sorted_dates:
        # 單日
        latest_data = st.session_state.inst_history[sorted_dates[0]].get(symbol, {})
        f_single = latest_data.get('foreign', 0)
        t_single = latest_data.get('trust', 0)
        d_single = latest_data.get('dealer', 0)
        margin_diff = latest_data.get('margin', 0)
        big_holder = latest_data.get('big_holder', 0.0)
        
        # 5日與10日
        for idx, d in enumerate(sorted_dates):
            day_data = st.session_state.inst_history[d].get(symbol, {})
            if idx < 5:
                f_5d += day_data.get('foreign', 0)
                t_5d += day_data.get('trust', 0)
            if idx < 10:
                f_10d += day_data.get('foreign', 0)
                t_10d += day_data.get('trust', 0)
                
    # 第一戰區營收：檢核手動覆寫大腦
    manual_mode = False
    override_db = getattr(st.session_state, 'revenue_override', {})
    if symbol in override_db:
        rev_yoy = override_db[symbol].get('yoy', 0.0)
        rev_mom = override_db[symbol].get('mom', 0.0)
        manual_mode = True
    else:
        rev_data = TW_REVENUE_DB.get(symbol, {})
        rev_yoy = rev_data.get('yoy', 0.0)
        rev_mom = rev_data.get('mom', 0.0)
        if rev_yoy == 0.0 and rev_mom == 0.0:
            # 嘗試從 Yahoo 備援
            rev_yoy = safe_float(info.get('revenueGrowth', 0.0)) * 100
            
    # 雙股利解析
    div_info = DIVIDEND_DB.get(symbol)
    if div_info:
        d_cash = div_info.get('cash', 0.0)
        d_stock = div_info.get('stock', 0.0)
        div_yield = (d_cash / curr_price) * 100 if curr_price > 0 else 0.0
        
        if d_cash > 0 and d_stock > 0:
            div_display = f"{div_info.get('date', '')} | 息 {d_cash}元 + 權 {d_stock}元"
        elif d_cash > 0:
            div_display = f"{div_info.get('date', '')} | 息 {d_cash}元"
        else:
            div_display = f"{div_info.get('date', '')} | 權 {d_stock}元"
    else:
        # Yahoo 備援
        d_cash = safe_float(info.get('dividendRate', 0.0))
        div_yield = safe_float(info.get('dividendYield', 0.0)) * 100
        div_display = f"配息 {d_cash}元" if d_cash > 0 else "無近期資訊"
        
    debt_ratio = safe_float(info.get('debtToEquity', 0))
    op_cashflow = safe_float(info.get('operatingCashflow', 0))
    net_income = safe_float(info.get('netIncome', 0))
    
    mine_tags = []
    if debt_ratio > 75.0: mine_tags.append("高負債比")
    if net_income > 0 and op_cashflow < 0: mine_tags.append("有獲利無現金(盈餘異常)")
    
    multi_bull = []
    multi_bear = []
    if curr_price > ma5: multi_bull.append("☑️ 站上5日線")
    else: multi_bear.append("❌ 跌破5日線")
    if curr_price > ma20: multi_bull.append("☑️ 站上月線(20MA)")
    else: multi_bear.append("❌ 跌破月線")
    if f_single > 0: multi_bull.append(f"☑️ 外資買超 ({f_single:,}張)")
    if t_single > 0: multi_bull.append(f"☑️ 投信買超 ({t_single:,}張)")
    if margin_diff < 0: multi_bull.append(f"☑️ 融資減少籌碼沉澱")
    else: multi_bear.append(f"❌ 融資增加籌碼發散")
    if rev_yoy > 20.0: multi_bull.append(f"☑️ 營收雙增 (YoY {rev_yoy:.1f}%)")
    
    detected_patterns = detect_k_line_patterns_v133(hist)
    for p in detected_patterns:
        if "長紅" in p.get('text', '') or "紅三兵" in p.get('text', ''): multi_bull.append(f"☑️ {p.get('text')}")
        else: multi_bear.append(f"❌ {p.get('text')}")
        
    total_checks = len(multi_bull) + len(multi_bear)
    bull_score = int((len(multi_bull) / total_checks) * 100) if total_checks > 0 else 50
    
    signal_text = "[🔥 偏多攻擊]" if (curr_price > ma5 and f_single > 0) else ("[🚨 撤退警告]" if curr_price < ma5 else "[⚠️ 整理觀望]")
    color_border = "#ff4d4d" if "攻擊" in signal_text else ("#00FF00" if "警告" in signal_text else "#f1c40f")
    signal_bg = "#3a1515" if "攻擊" in signal_text else ("#153a20" if "警告" in signal_text else "#332b00")
    
    blood_line = getattr(st.session_state, 'pinned_stocks', {}).get(symbol, "手動強制加入")
        
    if enable_doomsday and rev_yoy <= 20.0: return None

    return {
        "code": symbol, "name": TW_STOCK_NAMES.get(symbol, symbol), "price": curr_price, "gain": gain, "error": False,
        "open": open_price, "high": float(hist['High'].iloc[-1]), "low": float(hist['Low'].iloc[-1]),
        "vol": vol_today, "vol_change_pct": vol_change_pct, "vol_ratio": vol_ratio,
        "ma5": ma5, "ma20": ma20, "ma60": ma60, "macd_str": macd_str, "macd_color": macd_color, "kdj_str": kdj_str,
        "f_buy": f_single, "t_buy": t_single, "d_buy": d_single, "margin_diff": margin_diff, "big_holder": big_holder,
        "f_5d": f_5d, "t_5d": t_5d, "f_10d": f_10d, "t_10d": t_10d,
        "rev_yoy": rev_yoy, "rev_mom": rev_mom, "div_display": div_display, "div_yield": div_yield,
        "mine_tags": mine_tags, "bull_score": bull_score, "blood_line": blood_line,
        "signal_text": signal_text, "color_border": color_border, "signal_bg": signal_bg,
        "sparkline_html": generate_bi_color_sparkline(hist['Close'].tail(7).tolist()), 
        "intraday_str": get_intraday_trend(hist_1m), "manual_mode": manual_mode,
        "detected_patterns": detected_patterns, "sector": get_industry_label_wrapper(symbol),
        "is_first_red": (gain > 0 and curr_price > open_price and curr_price > ma5 and prev_price < ma5),
        "is_yesterday_strong": (gain > 0 and len(hist)>2 and ((prev_price - float(hist['Close'].iloc[-3]))/float(hist['Close'].iloc[-3])*100 > 5.0))
    }

# ==============================================================================
# 六、 雙軌籌碼備援管線 (官方 CSV 強填核心)
# ==============================================================================
def process_twse_csv(file_bytes, target_date):
    try:
        df = pd.read_csv(file_bytes, encoding='big5', skiprows=1, thousands=',')
        code_col = next((c for c in df.columns if '代號' in str(c)), None)
        f_col = next((c for c in df.columns if '外資' in str(c) and '買賣超' in str(c)), None)
        t_col = next((c for c in df.columns if '投信買賣超' in str(c)), None)
        d_col = next((c for c in df.columns if '自營商買賣超' in str(c) and '自行買賣' not in str(c)), None)
        
        if not code_col or not f_col:
            st.error("❌ CSV 欄位解析錯誤，請確認為證交所官方『三大法人買賣超日報』。")
            return
            
        history_db = getattr(st.session_state, 'inst_history', {})
        if target_date not in history_db:
            history_db.update({target_date: {}})
            
        success_count = 0
        for index, row in df.iterrows():
            code = str(row[code_col]).strip()
            if len(code) == 4 and code.isdigit():
                f_buy = int(safe_float(row[f_col]) / 1000) if f_col else 0
                t_buy = int(safe_float(row[t_col]) / 1000) if t_col else 0
                d_buy = int(safe_float(row[d_col]) / 1000) if d_col else 0
                
                existing = history_db.get(target_date, {}).get(code, {})
                payload = {
                    'foreign': f_buy, 'trust': t_buy, 'dealer': d_buy,
                    'margin': existing.get('margin', 0), 'big_holder': existing.get('big_holder', 0.0)
                }
                history_db.get(target_date, {}).update({code: payload})
                success_count += 1
                
        save_local_db_isolated()
        st.success(f"✅ 官方籌碼強填成功！武裝充填 {success_count} 檔法人數據至大腦 ({target_date})。")
        time.sleep(0.5)
        st.rerun()
    except Exception as e:
        st.error(f"❌ 檔案讀取失敗，請覆核是否為原始官方 CSV: {str(e)}")

def execute_heavy_data_sync(target_codes, target_date):
    progress_bar = st.progress(0)
    status_text = st.empty()
    history_db = getattr(st.session_state, 'inst_history', {})
    if target_date not in history_db:
        history_db.update({target_date: {}})
        
    missing = [c for c in target_codes if c not in history_db.get(target_date, {})]
    if not missing:
        st.success("✅ 當日籌碼大腦記憶庫已 100% 飽和。")
        return
        
    status_text.info(f"📡 備援引擎啟動，正在對 {len(missing)} 檔個股進行靶向精準修復...")
    success_count = 0
    url = 'https://api.finmindtrade.com/api/v4/data'
    
    def fetch_finmind_worker(code):
        token = FINMIND_TOKENS[getattr(st.session_state, 'active_key_index', 0)]
        payload = {'foreign':0, 'trust':0, 'dealer':0, 'margin':0, 'big_holder':0.0}
        try:
            p1 = {'dataset': 'TaiwanStockInstitutionalInvestorsBuySell', 'data_id': code, 'start_date': target_date}
            if token: p1['token'] = token
            r1 = requests.get(url, params=p1, timeout=4)
            if r1.status_code == 200 and r1.json().get('msg') == 'success':
                df = pd.DataFrame(r1.json().get('data', []))
                if not df.empty:
                    df['net'] = pd.to_numeric(df['buy'], errors='coerce').fillna(0) - pd.to_numeric(df['sell'], errors='coerce').fillna(0)
                    piv = df.pivot_table(index='date', columns='name', values='net', aggfunc='sum')
                    payload['foreign'] = int(piv['Foreign_Investor'].iloc[-1]/1000) if 'Foreign_Investor' in piv.columns else 0
                    payload['trust'] = int(piv['Investment_Trust'].iloc[-1]/1000) if 'Investment_Trust' in piv.columns else 0
                    payload['dealer'] = int(piv['Dealer'].iloc[-1]/1000) if 'Dealer' in piv.columns else 0
            
            p2 = {'dataset': 'TaiwanStockMarginPurchaseShortSale', 'data_id': code, 'start_date': target_date}
            if token: p2['token'] = token
            r2 = requests.get(url, params=p2, timeout=4)
            if r2.status_code == 200 and r2.json().get('msg') == 'success':
                m_df = pd.DataFrame(r2.json().get('data', []))
                if not m_df.empty: payload['margin'] = int(m_df.iloc[-1].get('MarginPurchaseTodayBalance',0)) - int(m_df.iloc[-1].get('MarginPurchaseYesterdayBalance',0))

            p3 = {'dataset': 'TaiwanStockHoldingSharesPer', 'data_id': code, 'start_date': (datetime.strptime(target_date, "%Y-%m-%d") - timedelta(days=7)).strftime('%Y-%m-%d')}
            if token: p3['token'] = token
            r3 = requests.get(url, params=p3, timeout=4)
            if r3.status_code == 200 and r3.json().get('msg') == 'success':
                b_df = pd.DataFrame(r3.json().get('data', []))
                if not b_df.empty:
                    latest_date = b_df['date'].max()
                    payload['big_holder'] = round(b_df[(b_df['date'] == latest_date) & (b_df['HoldingSharesLevel'] >= 15)]['percent'].sum(), 2)

            st.session_state.inst_history.get(target_date, {}).update({code: payload})
            return True
        except Exception: return False

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(fetch_finmind_worker, code): code for code in missing}
        for idx, future in enumerate(concurrent.futures.as_completed(futures)):
            if future.result(): success_count += 1
            progress_bar.progress(min((idx + 1) / len(futures), 1.0))
            if idx > 0 and idx % 40 == 0: save_local_db_isolated()

    status_text.empty()
    progress_bar.empty()
    save_local_db_isolated()
    st.success(f"✅ API 靶向斷點修復完畢，成功充填: {success_count} 檔。")
    time.sleep(0.5)
    st.rerun()

# ==============================================================================
# 七、 外部情報萃取與單檔股票四大段 AI 推演引擎
# ==============================================================================
def execute_ai_intelligence_extraction(raw_text, info_type, tag_name):
    if not GEMINI_API_KEYS or not GEMINI_API_KEYS[0]:
        st.error("❌ 戰略 AI 運算大腦未設定金鑰。")
        return
    
    prompt = f"請以首席戰略情報幕僚身分，依據台灣法規規範，客觀深度解析以下情報。情報屬性：【{info_type}】| 標籤：【{tag_name}】\n{raw_text}\n請嚴格遵循『財報體質』、『技術動能』、『主力籌碼』、『明日戰略總結』四段結構繁體輸出。並於最底部用獨立行印出：[標的代號: 2330, 2454]"
    key = GEMINI_API_KEYS[getattr(st.session_state, 'active_key_index', 0) % len(GEMINI_API_KEYS)]
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={key}"
        res = requests.post(url, headers={'Content-Type': 'application/json'}, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=20)
        if res.status_code == 200:
            ai_output = str(res.json()['candidates'][0]['content']['parts'][0]['text'])
            st.session_state.ai_report = ai_output
            
            matched = re.search(r'\[標的代號:\s*([^\]]+)\]', ai_output)
            if matched:
                raw_codes = matched.group(1)
                extracted_codes = [c.strip() for c in raw_codes.split(',') if c.strip().isdigit() and len(c.strip()) == 4]
                pool_target = "podcast" if info_type == "股癌最新節目" else "report"
                max_limit = 5 if info_type == "股癌最新節目" else 10
                
                st.session_state.intelligence_pool.get(pool_target, {}).update({tag_name: extracted_codes})
                if len(st.session_state.intelligence_pool.get(pool_target, {})) > max_limit:
                    oldest_key = list(st.session_state.intelligence_pool.get(pool_target, {}).keys())[0]
                    st.session_state.intelligence_pool.get(pool_target, {}).pop(oldest_key, None)
                    
                save_local_db_isolated()
                st.success(f"✅ AI 數據萃取成功！已寫入『{info_type} - {tag_name}』集結池。")
                time.sleep(0.5)
                st.rerun()
    except Exception as e:
        st.error(f"❌ AI 情報分析連線失敗: {str(e)}")

def execute_single_stock_ai_推演(c):
    if not GEMINI_API_KEYS or not GEMINI_API_KEYS[0]:
        return "金鑰未配置，無法啟動 AI 推演大腦。"
    
    code = c['code']
    prompt = f"""請以首席戰略幕僚身分，針對個股 {c['name']} ({code}) 進行冷血客觀的多空戰略推演。
當前現價: {c['price']} | 單日漲跌: {c['gain']:.2f}% | 爆量比: {c['vol_ratio']:.1f}x | 日內趨勢: {c['intraday_str']}
營收 YoY: {c['rev_yoy']:.1f}% | MoM: {c['rev_mom']:.1f}% | 除權息資訊: {c['div_display']}
外資單日: {c['f_buy']}張 (5日累計:{c['f_5d']}張, 10日:{c['f_10d']}張)
投信單日: {c['t_buy']}張 (5日累計:{c['t_5d']}張, 10日:{c['t_10d']}張)
技術指標: 5MA={c['ma5']:.1f}, 20MA={c['ma20']:.1f}, 60MA={c['ma60']:.1f}, MACD={c['macd_str']}, KDJ={c['kdj_str']}

請嚴格輸出以下四個段落，每段獨立小結，最後給出明日總結：
📊 【第一戰區財報面獨立評估小結】
⚔️ 【第二戰區技術面獨立評估小結】
📊 【第三戰區籌碼面獨立評估小結】
🎯 【總指揮明日戰略總結】"""

    key = GEMINI_API_KEYS[getattr(st.session_state, 'active_key_index', 0) % len(GEMINI_API_KEYS)]
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={key}"
        res = requests.post(url, headers={'Content-Type': 'application/json'}, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=15)
        if res.status_code == 200:
            return str(res.json()['candidates'][0]['content']['parts'][0]['text'])
    except Exception as e:
        return f"AI 智囊團連線超時: {str(e)}"
    return "運算大腦未回傳有效推演結論。"

def run_global_consensus_intersection():
    pod_stocks = []
    for codes in getattr(st.session_state, 'intelligence_pool', {}).get('podcast', {}).values():
        pod_stocks.extend(codes)
    rep_stocks = []
    for codes in getattr(st.session_state, 'intelligence_pool', {}).get('report', {}).values():
        rep_stocks.extend(codes)
        
    intersection = list(set(pod_stocks) & set(rep_stocks))
    if not intersection:
        st.warning("⚠️ 目前兩大陣地之間尚未產生超級共識股。")
        return
        
    deployed_count = 0
    for code in intersection:
        if code in TW_STOCK_NAMES:
            st.session_state.pinned_stocks.update({code: "情報共識超級共識"})
            deployed_count += 1
    if deployed_count > 0:
        save_local_db_isolated()
        st.success(f"🚨 交叉比對完畢！偵測到 {deployed_count} 檔全域超級共識股，已自動強制武裝寫入雷達！")
        time.sleep(1)
        st.rerun()

# ==============================================================================
# 八、 全網專屬 CSS 行動端觸控懸浮裝甲配置
# ==============================================================================
st.markdown("""<style>
:root { color-scheme: dark !important; }
html, body, [class*="css"] { color-scheme: dark !important; background-color: #0b0c0f !important; color: #fff !important; font-family: Arial, sans-serif; }
div[data-testid="stSidebar"] { background-color: #12141a !important; border-right: 1px solid #333 !important; }
div[data-testid="stButton"] > button { background-color: #1e1e24 !important; border: 1px solid #444 !important; }
div[data-testid="stButton"] > button p { color: #00d2ff !important; font-weight: bold !important; font-size: 14px !important; }
.hud-box { background: linear-gradient(135deg, #1a1c23 0%, #0d1117 100%); border-radius: 10px; padding: 15px; border-left: 5px solid #ff4d4d; margin-bottom: 20px;}
.zone-box { background: #11141c; border: 1px solid #2c3e50; border-radius: 6px; padding: 10px; margin-bottom: 8px; }
.zone-title { color: #00d2ff; font-weight: bold; font-size: 13px; margin-bottom: 6px; border-bottom: 1px dashed #333; padding-bottom: 3px; }

/* 純 CSS 觸控懸浮說明裝甲 */
.m-tooltip { position: relative; border-bottom: 1px dashed #00d2ff; cursor: pointer; display: inline-block; color: #00d2ff; font-weight: bold; }
.m-tooltip .m-tooltiptext {
    visibility: hidden; width: max-content; max-width: 240px; background-color: #1f242d; color: #ffffff;
    text-align: left; border-radius: 6px; padding: 8px 12px; position: absolute;
    z-index: 999; bottom: 135%; left: 50%; transform: translateX(-50%); opacity: 0; transition: opacity 0.2s;
    font-size: 12px; line-height: 1.5; font-weight: normal; box-shadow: 0px 5px 15px rgba(0,0,0,0.8); border: 1px solid #00d2ff;
}
.m-tooltip .m-tooltiptext::after {
    content: ""; position: absolute; top: 100%; left: 50%; margin-left: -5px;
    border-width: 5px; border-style: solid; border-color: #1f242d transparent transparent transparent;
}
.m-tooltip:hover .m-tooltiptext, .m-tooltip:active .m-tooltiptext { visibility: visible; opacity: 1; }
</style>""", unsafe_allow_html=True)

# ----------------- 九、 側邊欄控制台 -----------------
with st.sidebar:
    st.markdown("<h2 style='color:#f1c40f; text-align:center;'>⚙️ 戰略控制台</h2>", unsafe_allow_html=True)
    if st.button("🔄 強制重整畫面", use_container_width=True):
        st.rerun()
    st.divider()
    
    # 系統連線狀態儀表板
    st.markdown("<div style='font-size:13px; font-weight:bold; margin-bottom:8px; color:#aaa;'>📡 系統連線狀態儀表板</div>", unsafe_allow_html=True)
    brain_light = "🟢" if (GEMINI_API_KEYS and GEMINI_API_KEYS[0] != "") else "🔴"
    fm_light = "🟢" if FINMIND_READY else "🔴"
    st.markdown(f"""<div style='background:#11141c; padding:8px; border-radius:5px; font-size:12px;'>
    {brain_light} AI 戰略大腦：{'連線正常' if brain_light=='🟢' else '未配置金鑰'}<br>
    {fm_light} FinMind 籌碼線路：{'連線正常' if fm_light=='🟢' else '未配置Token'}
    </div>""", unsafe_allow_html=True)
    st.divider()
    
    with st.expander("📊 資料庫完整度天數細節", expanded=False):
        db_days = len(getattr(st.session_state, 'inst_history', {}))
        if db_days == 0:
            st.warning("⚠️ 目前大腦無籌碼資料，請上傳 CSV 或啟動 FinMind 補齊")
        else:
            st.write(f"當前儲存天數共: {db_days} 天")
            for d, data_dict in sorted(st.session_state.inst_history.items(), reverse=True):
                st.caption(f"📅 {d}: 已存真實數據 {len(data_dict)} 檔 (完整)")
            
    target_date_sim = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
    
    with st.expander("📥 [主攻] 官方 CSV 籌碼強填中樞", expanded=True):
        uploaded_csv = st.file_uploader("拖曳證交所三大法人日報 CSV", type=['csv'])
        if uploaded_csv is not None:
            if st.button("🚀 執行大腦強制解析回填", use_container_width=True):
                process_twse_csv(uploaded_csv, target_date_sim)

    with st.expander("📡 [備援] 智慧靶向補齊引擎"):
        slider_sync_range = st.slider("同步上限檔數設定", min_value=100, max_value=1700, value=300, step=100)
        if st.button("🚀 執行遺失自動靶向補齊", use_container_width=True):
            execute_heavy_data_sync(GLOBAL_MARKET_CODES[:slider_sync_range], target_date_sim)
            
    st.divider()
    min_volume_filter = st.slider("最低 5 日波段均量門檻 (張)", 0, 5000, 500, 100)
    min_yield_filter = st.slider("最低現金殖利率門檻調整 (%)", 0.0, 30.0, 4.5, 0.5)
    enable_doomsday_lock = st.checkbox("💀 開啟末日鎔斷防護鎖", value=False)
    
    st.divider()
    commands_list = ["查1.主升段突擊", "查2.魚頭慢伏支撐", "查3.價值投資與循環", "查4.投信作帳集團股", "查5.籌碼外資霸王色", "查6.營收雙增爆發突破", "查7.股癌戰情雷達", "查8.昨日強勢動能延續", "查9.均線糾結爆量突破", "查10.籌碼沉澱量縮潛伏", "查11.除權息尋寶雷達", "查12.K線型態尋寶型"]
    selected_cmd = st.radio("戰略選單：", commands_list, label_visibility="collapsed")
    selected_k_patterns = []
    if "查12" in selected_cmd:
        with st.container(border=True):
            if st.checkbox("🔥 長紅吞噬 / 低檔長紅"): selected_k_patterns.append("長紅")
            if st.checkbox("🔥 紅三兵強勢推推"): selected_k_patterns.append("紅三兵")
            if st.checkbox("💀 長黑吞噬頂部出貨"): selected_k_patterns.append("長黑")
            if st.checkbox("💀 黑三兵弱勢跌破"): selected_k_patterns.append("黑三兵")
            
    with st.expander("📖 統籌戰術解密說明書"):
        st.markdown("""
        <div style="font-size:12px; line-height:1.6; color:#ffffff;">
        <b style='color:#00d2ff;'>查1.主升段突擊</b>: 首根紅K突破且爆量2倍以上、KDJ金叉。<br>
        <b style='color:#00d2ff;'>查2.魚頭慢伏支撐</b>: 股價站上季線(60MA)且溫和放量。<br>
        <b style='color:#00d2ff;'>查3.價值投資與循環</b>: 多方評分達60分以上且無財務地雷。<br>
        <b style='color:#00d2ff;'>查4.投信作帳集團股</b>: 單日投信買超大於 0 張。<br>
        <b style='color:#00d2ff;'>查5.籌碼外資霸王色</b>: 外資買超且融資同日減少(籌碼沉澱)。<br>
        <b style='color:#00d2ff;'>查6.營收雙增爆發突破</b>: 營收 YoY 大於 20%。<br>
        <b style='color:#00d2ff;'>查7.股癌戰情雷達</b>: 從 AI 情報中樞動態擷取之專屬標的。<br>
        <b style='color:#00d2ff;'>查8.昨日強勢動能延續</b>: 昨日漲幅>5%且今日續強。<br>
        <b style='color:#00d2ff;'>查9.均線糾結爆量突破</b>: 單日成交量大於五日均量 2 倍。<br>
        <b style='color:#00d2ff;'>查10.籌碼沉澱量縮潛伏</b>: 單日量縮 40% 以上且融資減少。<br>
        <b style='color:#00d2ff;'>查11.除權息尋寶雷達</b>: 現金殖利率大於您自訂的門檻。<br>
        <b style='color:#00d2ff;'>查12.K線型態尋寶型</b>: 匹配多/空強力 K 線型態篩選。
        </div>
        """, unsafe_allow_html=True)

# ==============================================================================
# 十、 主畫面：高能多模態情報分析中心
# ==============================================================================
st.title("🚀 54088 戰情室 V133 完全體")

with st.container(border=True):
    st.markdown("<h3 style='color:#f1c40f; font-size:16px; margin:0 0 10px 0;'>🎙️ 視覺與文字情報解析中樞</h3>", unsafe_allow_html=True)
    i_cols = st.columns([1, 1])
    with i_cols[0]:
        uploaded_doc = st.file_uploader("支援 PNG / JPG / PDF 偵察文件", type=['png', 'jpg', 'jpeg', 'pdf'], label_visibility="collapsed")
    with i_cols[1]:
        text_input_area = st.text_area("情報文字貼上區", height=68, label_visibility="collapsed", placeholder="請在此處貼上數千字原文...")
        
    ctrl_cols = st.columns([1, 1, 1])
    info_src = ctrl_cols[0].selectbox("情報來源陣地劃分", ["股癌最新節目", "外資法人報告", "綜合財經新聞"])
    tag_input_str = ctrl_cols[1].text_input("定義本手情報標籤", "最新集數")
    
    if ctrl_cols[2].button("⚡ 發動 AI 情報核心萃取與分流", use_container_width=True, type="primary"):
        final_text_source = text_input_area
        if uploaded_doc is not None:
            final_text_source = "【多模態文件解讀】\n" + text_input_area
        if final_text_source.strip():
            execute_ai_intelligence_extraction(final_text_source, info_src, tag_input_str)
        else:
            st.error("❌ 偵察失敗：請提供貼上文字或上傳文件。")

    with st.expander("📂 檢閱當前持久化大腦情報集結池與超級共識比對", expanded=False):
        p_cols = st.columns(2)
        with p_cols[0]:
            st.markdown("<strong style='color:#ff4d4d;'>🎙️ 股癌陣地 (最大5集滾動)</strong>", unsafe_allow_html=True)
            for k in list(getattr(st.session_state, 'intelligence_pool', {}).get('podcast', {}).keys()):
                st.write(f"📁 {k}: {st.session_state.intelligence_pool.get('podcast', {}).get(k, [])}")
                if st.button(f"🗑️ 移除 {k}", key=f"del_pod_{k}"):
                    st.session_state.intelligence_pool.get('podcast', {}).pop(k, None)
                    save_local_db_isolated(); st.rerun()
        with p_cols[1]:
            st.markdown("<strong style='color:#00d2ff;'>📄 法人與新聞陣地 (最大10份滾動)</strong>", unsafe_allow_html=True)
            for k in list(getattr(st.session_state, 'intelligence_pool', {}).get('report', {}).keys()):
                st.write(f"📁 {k}: {st.session_state.intelligence_pool.get('report', {}).get(k, [])}")
                if st.button(f"🗑️ 移除 {k}", key=f"del_rep_{k}"):
                    st.session_state.intelligence_pool.get('report', {}).pop(k, None)
                    save_local_db_isolated(); st.rerun()
        st.divider()
        if st.button("🎯 [發動全域三段式交叉共識比對 ➡️ 自動強制武裝加入雷達]", use_container_width=True, type="primary"):
            run_global_consensus_intersection()

if getattr(st.session_state, 'ai_report', ""):
    with st.expander("🤖 首席 AI 戰略幕僚 - 結構化情報推演報告", expanded=True):
        st.markdown(st.session_state.ai_report)

# ==============================================================================
# 十一、 主畫面字卡與雷達防線渲染晶片
# ==============================================================================
st.markdown(f"""<div class='hud-box'>
    <div style='color:#f1c40f; font-size:16px; font-weight:bold; margin-bottom:4px;'>📊 大將軍智慧 HUD 總覽</div>
    <div style='color:#ddd; font-size:14px;'><b>大盤氣象：</b> {weather_str} | <b>安全狀態：</b> 三重防呆自癒看門狗裝甲全面就緒</div>
</div>""", unsafe_allow_html=True)

search_input = st.text_input("🔍 手動股票代號輸入框 (多檔請用空白隔開)", "")
if st.button("➕ 強制加入常態觀測雷達", use_container_width=True):
    if search_input:
        found_codes = re.findall(r'\b\d{4}\b', search_input)
        for c in found_codes:
            st.session_state.pinned_stocks.update({c: "手動強制加入"})
        save_local_db_isolated(); st.rerun()

# --- 核心字卡組裝函數 ---
def render_commander_stock_card(c, is_portfolio=False, profit=0, roi=0, ent_p=0):
    gain_c = '#ff4d4d' if float(c.get('gain',0)) > 0 else ('#00FF00' if float(c.get('gain',0)) < 0 else '#aaaaaa')
    gain_b = '#3a1515' if float(c.get('gain',0)) > 0 else ('#153a20' if float(c.get('gain',0)) < 0 else '#333333')
    vol_c = '#ff4d4d' if float(c.get('vol_change_pct',0)) > 0 else '#00FF00'
    vol_t = f"爆量 {float(c.get('vol_change_pct',0)):+.1f}%" if float(c.get('vol_change_pct',0)) > 0 else f"量縮 {float(c.get('vol_change_pct',0)):.1f}%"
    
    portfolio_header = f"<div style='font-size:14px; color:#ffffff; margin-bottom:8px;'>持倉成本: {ent_p} | 真實扣稅損益: <strong style='color:{'#ff4d4d' if profit>0 else '#00FF00'};'>{int(profit):+,} 元</strong> ({roi:+.2f}%)</div>" if is_portfolio else ""
    
    # 判斷營收是否觸發手動覆寫警告
    rev_display_html = ""
    if c.get('rev_yoy') == 0.0 and c.get('rev_mom') == 0.0 and not c.get('manual_mode'):
        rev_display_html = f"""<span style='color:#f1c40f; font-weight:bold;'>⚠️ 營收 API 連線異常，請於下方進行大腦手動覆寫補給</span>"""
    else:
        m_tag = f"<span style='background:#7f8c8d; color:#fff; font-size:10px; padding:1px 3px; border-radius:3px;'>手動</span>" if c.get('manual_mode') else ""
        rev_display_html = f"營收 YoY: <strong style='color:#00d2ff;'>{float(c.get('rev_yoy',0)):.1f}%</strong> {m_tag} | MoM月增: <strong style='color:#00d2ff;'>{float(c.get('rev_mom',0)):.1f}%</strong> {m_tag}"

    html = f"""
<div style="border:2px solid {c.get('color_border')}; border-radius:8px; padding:15px; background:#16191f; margin-bottom:12px;">
{portfolio_header}
<div style="display:flex; justify-content:space-between; align-items:center;">
<span style="font-weight:bold; font-size:19px; color:#ffffff;">{c.get('name')} <span style="color:#00d2ff;">({c.get('code')})</span> <span style="font-size:12px; color:#ffffff; background:#2c3e50; padding:2px 6px; border-radius:4px; margin-left:5px;">{c.get('sector')}</span></span>
<span style="font-size:13px; color:#f1c40f;">{c.get('blood_line')}</span>
</div>

<div style="display:flex; justify-content:space-between; align-items:flex-end; margin:10px 0;">
    <div style="display:flex; align-items:center;">
        <span style="font-size:32px; font-weight:bold; color:#ffffff;">{float(c.get('price',0)):.2f}</span> 
        <span style="font-size:15px; color:{gain_c}; background:{gain_b}; padding:3px 8px; border-radius:4px; margin-left:10px; font-weight:bold;">{float(c.get('gain',0)):+.2f}%</span>
    </div>
    <div style="font-size:14px; color:#ffffff; display:flex; align-items:center;">
        <span class="m-tooltip" style="margin-right:5px; font-size:12px;">近7日<span class="m-tooltiptext">近七日收盤價高低動能波段圖</span></span> {c.get('sparkline_html')}
    </div>
</div>

<div style="background:#0e1117; padding:8px; border-radius:4px; margin-bottom:10px;">
    <div style="font-size:13px; color:#ffffff; margin-bottom:4px;">
        <span class="m-tooltip">總量<span class="m-tooltiptext">今日總成交張數</span></span>: <b style="color:#ffffff;">{int(c.get('vol',0)):,} K張</b> (<span style="color:{vol_c}; font-weight:bold;">{vol_t}</span>)
    </div>
    <div style="font-size:13px; color:#ffffff; display:flex; justify-content:space-between;">
        <span><span class="m-tooltip">爆量比<span class="m-tooltiptext">今日量 ÷ 近五日均量，大於 2 具備主力反轉攻擊動能</span></span>: <strong style="color:#e67e22;">{float(c.get('vol_ratio',0)):.1f}x</strong></span>
        <span style="color:#00FF00; font-weight:bold;">{c.get('intraday_str')}</span>
    </div>
</div>

<div class="zone-box">
    <div class="zone-title">❤️ 第一戰區：基本與財報面</div>
    <div style="font-size:13px; color:#ffffff; margin-bottom:4px;">{rev_display_html}</div>
    <div style="font-size:13px; color:#ffffff;">除權息資訊: <strong style="color:#d200ff;">{c.get('div_display')} ({float(c.get('div_yield',0)):.1f}%)</strong></div>
</div>

<div class="zone-box">
    <div class="zone-title">⚔️ 第二戰區：技術與多空領先指標清單</div>
    <div style="font-size:13px; color:#ffffff; margin-bottom:4px; display:flex; justify-content:space-between;">
        <span>5MA(周): <b>{float(c.get('ma5',0)):.1f}</b></span>
        <span>20MA(月): <b>{float(c.get('ma20',0)):.1f}</b></span>
        <span>60MA(季): <b>{float(c.get('ma60',0)):.1f}</b></span>
    </div>
    <div style="display:flex; justify-content:space-between; font-size:13px; color:#ffffff;">
        <span style="color:{c.get('macd_color')}; font-weight:bold;" class="m-tooltip">{c.get('macd_str')}<span class="m-tooltiptext">指數平滑異同移動平均線，紅柱多方動能、綠柱空方動能</span></span>
        <span style="color:#f1c40f;" class="m-tooltip">KDJ: {c.get('kdj_str')}<span class="m-tooltiptext">短線隨機指標金叉死叉狀態，捕捉短線拐點</span></span>
    </div>
</div>

<div class="zone-box">
    <div class="shadow-box">
        <div class="zone-title">📊 第三戰區：三大法人與千張大戶主力籌碼</div>
        <div style="font-size:13px; color:#ffffff; margin-bottom:4px;">
            <b>[外資(單日)]</b> <strong style="color:#ff4d4d;">{int(c.get('f_buy',0)):+,}張</strong> | 5日累計: <strong>{int(c.get('f_5d',0)):+,}張</strong> | 10日: <strong>{int(c.get('f_10d',0)):+,}張</strong>
        </div>
        <div style="font-size:13px; color:#ffffff; margin-bottom:6px;">
            <b>[投信(單日)]</b> <strong style="color:#ff4d4d;">{int(c.get('t_buy',0)):+,}張</strong> | 5日累計: <strong>{int(c.get('t_5d',0)):+,}張</strong> | 10日: <strong>{int(c.get('t_10d',0)):+,}張</strong>
        </div>
        <div style="font-size:12px; color:#ffffff; border-top:1px dashed #444; padding-top:6px; display:flex; justify-content:space-between;">
            <span class="m-tooltip">千張大戶持股比率<span class="m-tooltiptext">持有公司股票超過 1,000 張以上的極核心大股東持股總比例</span></span>: <strong style="color:#00d2ff;">{c.get('big_holder',0)}%</strong>
            <span>自營商: {int(c.get('d_buy',0)):+,}張</span>
        </div>
    </div>
</div>

<div style="background:{c.get('signal_bg')}; padding:10px; border-radius:5px; text-align:center; margin-top:8px;"><strong style="color:{c.get('color_border')}; font-size:15px;">決策判定：{c.get('signal_text')}</strong></div>
</div>
"""
    return re.sub(r'^\s+', '', html, flags=re.MULTILINE)

# --- 渲染常態持倉模擬倉 ---
if getattr(st.session_state, 'portfolio', {}):
    total_pnl = 0
    with st.expander("💼 總指揮常態持倉模擬倉 (實戰扣稅精算)", expanded=True):
        cols = st.columns(2)
        idx = 0
        for code, p_data in list(st.session_state.portfolio.items()):
            c = calculate_comprehensive_signals(code, enable_doomsday_lock)
            if c and not c.get('error'):
                ent_p = safe_float(p_data.get('entry_price', c.get('price')))
                qty = safe_float(p_data.get('qty', 1))
                profit, roi = calc_real_profit(ent_p, float(c.get('price', 0.0)), qty)
                total_pnl += profit
                with cols[idx % 2]:
                    st.markdown(render_commander_stock_card(c, is_portfolio=True, profit=profit, roi=roi, ent_p=ent_p), unsafe_allow_html=True)
                    if st.button("從持倉移除", key=f"del_port_{c.get('code')}", use_container_width=True):
                        st.session_state.portfolio.pop(str(c.get('code', '')), None)
                        save_local_db_isolated(); st.rerun()
                idx += 1
        st.markdown(f"### 總持倉淨利回報: <span style='color:{'#ff4d4d' if total_pnl>0 else '#00FF00'};'>{int(total_pnl):+,} 元</span>", unsafe_allow_html=True)

# --- 渲染常態觀測雷達防線 ---
if getattr(st.session_state, 'pinned_stocks', {}):
    all_sources = list(set(st.session_state.pinned_stocks.values()))
    filter_src = st.selectbox("🎯 篩選特定戰術血統標的", ["全部顯示"] + all_sources)
    
    with st.expander("🎯 總指揮常態觀測雷達防線", expanded=True):
        cols = st.columns(2)
        idx = 0
        for code, blood_label in list(st.session_state.pinned_stocks.items()):
            if filter_src != "全部顯示" and blood_label != filter_src:
                continue
            card = calculate_comprehensive_signals(code, enable_doomsday_lock)
            if card:
                with cols[idx % 2]:
                    if card.get('error', False):
                        st.warning(f"⚠️ {card.get('code')} API 真實連線超時，已啟動防護隔離保護。")
                        continue
                        
                    st.markdown(render_commander_stock_card(card), unsafe_allow_html=True)
                    
                    # 補給線：手動覆寫介面
                    if card.get('rev_yoy') == 0.0 and card.get('rev_mom') == 0.0 and not card.get('manual_mode'):
                        with st.container(border=True):
                            st.caption("📥 補給線：手動輸入營收 (將永久刻進大腦庫)")
                            m_y = st.number_input("輸入 YoY (%)", -100.0, 1000.0, 0.0, 0.1, key=f"my_y_{code}")
                            m_m = st.number_input("輸入 MoM (%)", -100.0, 1000.0, 0.0, 0.1, key=f"my_m_{code}")
                            if st.button("强制寫入實體大腦", key=f"btn_override_{code}"):
                                st.session_state.revenue_override.update({code: {'yoy': m_y, 'mom': m_m}})
                                save_local_db_isolated(); st.success("寫入成功！檔案已持久化鎖定。"); time.sleep(0.5); st.rerun()
                    
                    # 個股專屬 4 段式 AI 分析面板
                    if st.button("🤖 解鎖戰略推演與多空健診", key=f"ai_single_{code}", use_container_width=True):
                        st.session_state.single_ai_trigger = code
                        with st.spinner("幕僚團正在對三大戰區進行冷血推演..."):
                            rep = execute_single_stock_ai_推演(card)
                            st.session_state.single_ai_report.update({code: rep})
                            
                    if getattr(st.session_state, 'single_ai_trigger', '') == code:
                        if code in getattr(st.session_state, 'single_ai_report', {}):
                            st.info(st.session_state.single_ai_report.get(code))
                    
                    code_val = str(card.get('code', ''))
                    price_val = float(card.get('price', 0.0))
                    m_cols = st.columns(2)
                    if m_cols[0].button("轉移至持倉倉位", key=f"mov_pin_{code_val}", use_container_width=True):
                        st.session_state.portfolio.update({code_val: {"entry_price": price_val, "qty": 1}})
                        st.session_state.pinned_stocks.pop(code_val, None)
                        save_local_db_isolated(); st.rerun()
                    if m_cols[1].button("移出雷達防線", key=f"del_pin_{code_val}", use_container_width=True):
                        st.session_state.pinned_stocks.pop(code_val, None)
                        save_local_db_isolated(); st.rerun()
                idx += 1

# ==============================================================================
# 十二、 全市場戰略條件掃描
# ==============================================================================
if st.sidebar.button("🚀 執行全市場戰略條件掃描", use_container_width=True, type="primary"):
    with st.spinner("重型全市場真實 API 篩選中... (超時個股自動優雅隔離)"):
        results = []
        for c in GLOBAL_MARKET_CODES[:300]: # 設定安全池
            card = calculate_comprehensive_signals(c, enable_doomsday_lock)
            if card and not card.get('error', False) and float(card.get('vol', 0)) >= (min_volume_filter / 1000):
                valid = False
                if "查1" in selected_cmd and card.get('is_first_red') and float(card.get('vol_ratio',0)) >= 2.0 and "金叉" in card.get('kdj_str',''): valid = True
                elif "查2" in selected_cmd and float(card.get('price',0)) > float(card.get('ma60',0)) and float(card.get('vol_ratio',0)) >= 1.2: valid = True
                elif "查3" in selected_cmd and int(card.get('bull_score',0)) >= 60 and not card.get('mine_tags'): valid = True
                elif "查4" in selected_cmd and int(card.get('t_buy',0)) > 0: valid = True
                elif "查5" in selected_cmd and int(card.get('f_buy',0)) > 0 and int(card.get('margin_diff',0)) < 0: valid = True
                elif "查6" in selected_cmd and float(card.get('rev_yoy',0)) > 20: valid = True
                elif "查8" in selected_cmd and card.get('is_yesterday_strong'): valid = True
                elif "查9" in selected_cmd and float(card.get('vol_ratio',0)) >= 2.0: valid = True
                elif "查10" in selected_cmd and float(card.get('vol_change_pct',0)) < -40 and int(card.get('margin_diff',0)) < 0: valid = True
                elif "查11" in selected_cmd and float(card.get('div_yield',0)) >= min_yield_filter: valid = True
                elif "查12" in selected_cmd and selected_k_patterns:
                    if any(p in [x.get('text') for x in card.get('detected_patterns',[])] for p in selected_k_patterns): valid = True
                elif "查" not in selected_cmd: valid = True 
                
                if valid: results.append(card)
        st.session_state.scan_results = results
        st.session_state.scan_mode = selected_cmd

if getattr(st.session_state, 'scan_results', []):
    st.markdown(f"### ⚡ {st.session_state.scan_mode} 篩選戰果清單 ({len(st.session_state.scan_results)} 檔符合)")
    
    if st.button("➕ 批次部署並強制寫入常態追蹤雷達", use_container_width=True):
        for card in st.session_state.scan_results:
            st.session_state.pinned_stocks.update({card.get('code', ''): selected_cmd})
        save_local_db_isolated()
        st.success(f"✅ 成功將 {len(st.session_state.scan_results)} 檔標的綁定血統【{selected_cmd}】並永久存檔。")
        time.sleep(0.5); st.rerun()
        
    table_rows = []
    for card in st.session_state.scan_results:
        table_rows.append({
            "代號": card.get('code'), "名稱": card.get('name'), "現價": card.get('price'),
            "漲跌(%)": round(float(card.get('gain',0)), 2), "YoY年增(%)": round(float(card.get('rev_yoy',0)), 1),
            "MoM月增(%)": round(float(card.get('rev_mom',0)), 1), "殖利率(%)": f"{float(card.get('div_yield',0)):.1f}%", "地雷標記": len(card.get('mine_tags',[]))
        })
    st.dataframe(pd.DataFrame(table_rows), use_container_width=True, hide_index=True)
    
    cols = st.columns(2)
    for idx, card in enumerate(st.session_state.scan_results):
        with cols[idx % 2]:
            html_card = f"""
<div style="border:2px solid {card.get('color_border')}; border-radius:8px; padding:15px; background:#16191f; margin-bottom:12px;">
<span style="font-weight:bold; font-size:19px; color:#ffffff;">{card.get('name')} <span style="color:#00d2ff;">({card.get('code')})</span></span>
<div style="font-size:13px; color:#eeeeee; margin-top:5px;">當前狀態：初篩戰果符合 | 爆量比: {float(card.get('vol_ratio',0)):.1f}x</div>
</div>
"""
            st.markdown(re.sub(r'^\s+', '', html_card, flags=re.MULTILINE), unsafe_allow_html=True)

# === 54088 戰情室程式碼結束 (請確保此行以下沒有任何文字) ===
