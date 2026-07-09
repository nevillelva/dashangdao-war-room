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
# 一、 系統最高安全防禦與法規合規宣告
# ==============================================================================
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
warnings.filterwarnings('ignore')

st.set_page_config(layout="wide", page_title="54088 戰情室 V133 完全體", initial_sidebar_state="expanded")

GOV_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*"
}

USER_DB_FILE = "54088_database.json" 
INST_HISTORY_FILE = "54088_inst_history_v30d.json"

# ==============================================================================
# 二、 記憶體全域安全隔離初始化 (徹底杜絕 AttributeError)
# ==============================================================================
def init_session_state():
    if 'db_loaded' not in st.session_state: st.session_state['db_loaded'] = False
    if 'pinned_stocks' not in st.session_state: st.session_state['pinned_stocks'] = {"2303": {}, "5871": {}}
    if 'portfolio' not in st.session_state: st.session_state['portfolio'] = {}
    if 'inst_history' not in st.session_state: st.session_state['inst_history'] = {}
    if 'scan_results' not in st.session_state: st.session_state['scan_results'] = []
    if 'scan_mode' not in st.session_state: st.session_state['scan_mode'] = ""
    if 'active_key_index' not in st.session_state: st.session_state['active_key_index'] = 0
    if 'ai_report' not in st.session_state: st.session_state['ai_report'] = ""

init_session_state()

def load_and_isolate_db():
    if not st.session_state.get('db_loaded', False):
        if os.path.exists(USER_DB_FILE):
            try:
                with open(USER_DB_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    st.session_state['pinned_stocks'] = data.get("pinned_stocks", {})
                    st.session_state['portfolio'] = data.get("portfolio", {})
            except Exception: pass
        if os.path.exists(INST_HISTORY_FILE):
            try:
                with open(INST_HISTORY_FILE, "r", encoding="utf-8") as f:
                    st.session_state['inst_history'] = json.load(f)
                    if len(st.session_state.get('inst_history', {})) > 30:
                        sorted_dates = sorted(st.session_state['inst_history'].keys(), reverse=True)
                        st.session_state['inst_history'] = {d: st.session_state['inst_history'][d] for d in sorted_dates[:30]}
            except Exception: pass
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
    except Exception: pass

load_and_isolate_db()

# 雲端金鑰讀取與防呆狀態
API_READY = True
try:
    COMMANDER_PIN = st.secrets["radar_secrets"]["commander_pin"]
    raw_keys = st.secrets["radar_secrets"]["gemini_api_key"]
    GEMINI_API_KEYS = [k.strip() for k in raw_keys.split(",") if k.strip()]
    SECRET_FINMIND = st.secrets["radar_secrets"].get("finmind_token", "")
    FINMIND_TOKENS = [k.strip() for k in SECRET_FINMIND.split(",") if k.strip()]
    if not FINMIND_TOKENS: FINMIND_TOKENS = [""]
except Exception:
    API_READY = False
    COMMANDER_PIN = "54088"
    GEMINI_API_KEYS = [""]
    FINMIND_TOKENS = [""]

# ==============================================================================
# 三、 基礎運算與真實大數據抓取管線 (Real API + 超時降級防護)
# ==============================================================================
def safe_float(val):
    if pd.isna(val) or val is None or str(val).strip() == '': return 0.0
    try:
        s = str(val).upper().replace(',', '').replace('-', '').strip()
        s = re.sub(r'[^\d.]', '', s)
        return float(s) if s else 0.0
    except Exception: return 0.0

def calc_real_profit(cost, price, qty=1):
    """精算包含台灣證交稅(0.3%)與手續費(0.1425%)的真實淨損益"""
    if cost <= 0 or price <= 0: return 0, 0
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
    for url in ["https://openapi.twse.com.tw/v1/opendata/t187ap05_L", "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap05_O"]:
        try:
            res = requests.get(url, headers=GOV_HEADERS, verify=False, timeout=5)
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
            res = requests.get(url, headers=GOV_HEADERS, verify=False, timeout=5)
            if res.status_code == 200:
                for item in res.json():
                    c = str(item.get('Code', item.get('SecuritiesCompanyCode', ''))).strip()
                    n = str(item.get('Name', item.get('CompanyName', ''))).strip()
                    if len(c) == 4 and c.isdigit() and n: names[c] = n
        except Exception: pass
    fallbacks = {"2330":"台積電", "2303":"聯電", "2317":"鴻海", "2308":"台達電", "5871":"中租-KY", "3481":"群創", "2454":"聯發科"}
    for k, v in fallbacks.items():
        if k not in names: names[k] = v
    return names

@st.cache_data(ttl=86400, show_spinner=False)
def fetch_twse_dividends():
    divs = {}
    try:
        res = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/TWT48U", headers=GOV_HEADERS, verify=False, timeout=5)
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
    except Exception: pass
    return "<span style='color:#888;'>大盤連線異常...</span>", False, 0.0

@st.cache_data(ttl=120, show_spinner=False)
def get_real_stock_data_yfinance(symbol):
    """若 YFinance 抓不到真實數據，絕不回傳假資料，直接回傳 None 引發優雅降級"""
    session = get_safe_session()
    for ext in [".TW", ".TWO"]:
        try:
            tk = yf.Ticker(symbol + ext, session=session)
            hist = tk.history(period="3mo", timeout=4).dropna(subset=['Close'])
            hist_1m = tk.history(period="1d", interval="1m", timeout=3).dropna(subset=['Close'])
            if not hist.empty and len(hist) > 10:
                return hist.tail(30), hist_1m, tk.info
        except Exception: pass
    return None, None, {}

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
        if i == 0: color = "#888888"
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
    if (c0 > o0) and (c1 > o1) and (c2 > o2) and (c0 > c1 > c2): patterns.append({"text": "紅三兵", "class": "tag-red"})
    if (c0 < o0) and body0 > (c0 * 0.025):
        if (c1 > o1) and c0 < o1 and o0 > c1: patterns.append({"text": "長黑吞噬", "class": "tag-green"})
        else: patterns.append({"text": "高檔長黑", "class": "tag-green"})
    if (c0 < o0) and (c1 < o1) and (c2 < o2) and (c0 < c1 < c2): patterns.append({"text": "黑三兵", "class": "tag-green"})
    return patterns

def get_intraday_trend(df_1m):
    if df_1m is None or df_1m.empty: return "▰▰▰▱▱ 無即時資料"
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
        # 優雅降級：若無數據回傳錯誤標記，防止字卡渲染崩潰
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
    ma10 = float(hist['Close'].tail(10).mean())
    ma20 = float(hist['Close'].tail(20).mean())
    ma60 = float(hist['Close'].mean())
    
    exp1 = hist['Close'].ewm(span=12, adjust=False).mean()
    exp2 = hist['Close'].ewm(span=26, adjust=False).mean()
    macd_hist = (exp1 - exp2) - (exp1 - exp2).ewm(span=9, adjust=False).mean()
    macd_val = macd_hist.iloc[-1] if not macd_hist.empty else 0
    macd_str = "📈 多方動能(紅柱)" if macd_val > 0 else "📉 空方動能(綠柱)"
    macd_color = "#ff4d4d" if macd_val > 0 else "#00FF00"
    
    low_min = hist['Low'].rolling(9).min()
    high_max = hist['High'].rolling(9).max()
    rsv = (hist['Close'] - low_min) / (high_max - low_min + 1e-9) * 100
    calc_k = rsv.bfill().ffill().ewm(com=2, adjust=False).mean()
    calc_d = calc_k.bfill().ffill().ewm(com=2, adjust=False).mean()
    kdj_str = "金叉" if not calc_k.empty and calc_k.iloc[-1] > calc_d.iloc[-1] else "死叉"
    
    f_buy = t_buy = d_buy = margin_diff = big_holder = 0
    sorted_dates = sorted(st.session_state.get('inst_history', {}).keys(), reverse=True)
    if sorted_dates and symbol in st.session_state['inst_history'][sorted_dates[0]]:
        mem = st.session_state['inst_history'][sorted_dates[0]][symbol]
        f_buy = mem.get('foreign', 0)
        t_buy = mem.get('trust', 0)
        d_buy = mem.get('dealer', 0)
        margin_diff = mem.get('margin', 0)
        big_holder = mem.get('big_holder', 0.0)
    
    rev_data = TW_REVENUE_DB.get(symbol, {'yoy': 0.0, 'mom': 0.0})
    rev_yoy = rev_data['yoy']
    rev_mom = rev_data['mom']
    
    div_info = DIVIDEND_DB.get(symbol)
    if div_info:
        div_display = f"{div_info['date']} | {div_info['cash']}元"
        div_yield = (div_info['cash'] / curr_price) * 100 if curr_price > 0 else 0.0
    else:
        div_display = "無近期資訊"; div_yield = 0.0
        
    debt_ratio = safe_float(info.get('debtToEquity', 0))
    op_cashflow = safe_float(info.get('operatingCashflow', 0))
    net_income = safe_float(info.get('netIncome', 0))
    consensus_target = safe_float(info.get('targetMeanPrice', curr_price))
    potential_roi = round(((consensus_target - curr_price) / curr_price) * 100, 1) if curr_price > 0 else 0.0
    
    mine_tags = []
    if debt_ratio > 75.0: mine_tags.append("高負債比")
    if net_income > 0 and op_cashflow < 0: mine_tags.append("有獲利無現金(盈餘異常)")
    
    multi_bull = []
    multi_bear = []
    if curr_price > ma5: multi_bull.append("☑️ 站上5日線")
    else: multi_bear.append("❌ 跌破5日線")
    if curr_price > ma20: multi_bull.append("☑️ 站上月線(20MA)")
    else: multi_bear.append("❌ 跌破月線")
    if f_buy > 0: multi_bull.append(f"☑️ 外資買超 ({f_buy:,}張)")
    if t_buy > 0: multi_bull.append(f"☑️ 投信買超 ({t_buy:,}張)")
    if margin_diff < 0: multi_bull.append(f"☑️ 融資減少籌碼沉澱")
    else: multi_bear.append(f"❌ 融資增加籌碼發散")
    if rev_yoy > 20.0: multi_bull.append(f"☑️ 營收雙增 (YoY {rev_yoy}%)")
    
    detected_patterns = detect_k_line_patterns_v133(hist)
    for p in detected_patterns:
        if "長紅" in p['text'] or "紅三兵" in p['text']: multi_bull.append(f"☑️ {p['text']}")
        else: multi_bear.append(f"❌ {p['text']}")
        
    total_checks = len(multi_bull) + len(multi_bear)
    bull_score = int((len(multi_bull) / total_checks) * 100) if total_checks > 0 else 50
    trend_label = "<span class='tag-red'>[短強]</span>" if curr_price > ma5 else "<span class='tag-green'>[短弱]</span>"
    trade_attr = "<span class='tag-base tag-purple'>[波段屬性]</span>" if curr_price > 120 else "<span class='tag-base tag-blue'>[短線屬性]</span>"
    
    if enable_doomsday and rev_yoy <= 20.0: return None
        
    signal_text = "[🔥 偏多攻擊]" if (curr_price > ma5 and f_buy > 0) else ("[🚨 撤退警告]" if curr_price < ma5 else "[⚠️ 整理觀望]")
    color_border = "#ff4d4d" if "攻擊" in signal_text else ("#00FF00" if "警告" in signal_text else "#f1c40f")
    signal_bg = "#3a1515" if "攻擊" in signal_text else ("#153a20" if "警告" in signal_text else "#332b00")
        
    return {
        "code": symbol, "name": TW_STOCK_NAMES.get(symbol, symbol), "price": curr_price, "gain": gain, "error": False,
        "open": open_price, "high": float(hist['High'].iloc[-1]), "low": float(hist['Low'].iloc[-1]),
        "vol": vol_today, "vol_change_pct": vol_change_pct, "vol_ratio": vol_ratio,
        "ma5": ma5, "ma10": ma10, "ma20": ma20, "ma60": ma60,
        "macd_str": macd_str, "macd_color": macd_color, "kdj_str": kdj_str,
        "f_buy": f_buy, "t_buy": t_buy, "d_buy": d_buy, "margin_diff": margin_diff, "big_holder": big_holder,
        "rev_yoy": rev_yoy, "rev_mom": rev_mom, "div_display": div_display, "div_yield": div_yield,
        "consensus_target": consensus_target, "potential_roi": potential_roi,
        "mine_tags": mine_tags, "bull_score": bull_score, "trend_label": trend_label, "trade_attr": trade_attr,
        "multi_bull": multi_bull, "multi_bear": multi_bear,
        "signal_text": signal_text, "color_border": color_border, "signal_bg": signal_bg,
        "sparkline_html": generate_bi_color_sparkline(hist['Close'].tail(7).tolist()), 
        "intraday_str": get_intraday_trend(hist_1m),
        "detected_patterns": detected_patterns, "sector": get_industry_label_wrapper(symbol),
        "is_first_red": (gain > 0 and curr_price > open_price and curr_price > ma5 and prev_price < ma5),
        "is_yesterday_strong": (gain > 0 and len(hist)>2 and ((prev_price - float(hist['Close'].iloc[-3]))/float(hist['Close'].iloc[-3])*100 > 5.0))
    }

# ==============================================================================
# 六、 官方 CSV 上傳強填大腦 (免 API、防超時主攻武器)
# ==============================================================================
def process_twse_csv(file_bytes, target_date):
    """手動解析台灣證交所下載的 CSV 檔案並強填入大腦"""
    try:
        df = pd.read_csv(file_bytes, encoding='big5', skiprows=1, thousands=',')
        code_col = next((c for c in df.columns if '代號' in str(c)), None)
        f_col = next((c for c in df.columns if '外資' in str(c) and '買賣超' in str(c)), None)
        t_col = next((c for c in df.columns if '投信買賣超' in str(c)), None)
        d_col = next((c for c in df.columns if '自營商買賣超' in str(c) and '自行買賣' not in str(c)), None)
        
        if not code_col or not f_col:
            st.error("❌ CSV 欄位異常，請確認為證交所『三大法人買賣超日報』原檔。")
            return
            
        if target_date not in st.session_state['inst_history']: 
            st.session_state['inst_history'][target_date] = {}
            
        success_count = 0
        for index, row in df.iterrows():
            code = str(row[code_col]).strip()
            if len(code) == 4 and code.isdigit():
                f_buy = int(safe_float(row[f_col]) / 1000) if f_col else 0
                t_buy = int(safe_float(row[t_col]) / 1000) if t_col else 0
                d_buy = int(safe_float(row[d_col]) / 1000) if d_col else 0
                
                existing = st.session_state['inst_history'][target_date].get(code, {})
                st.session_state['inst_history'][target_date][code] = {
                    'foreign': f_buy, 'trust': t_buy, 'dealer': d_buy,
                    'margin': existing.get('margin', 0), 'big_holder': existing.get('big_holder', 0.0)
                }
                success_count += 1
                
        save_local_db_isolated()
        st.success(f"✅ CSV 解析成功！強制填入 {success_count} 檔法人數據 ({target_date})。")
        time.sleep(1); st.rerun()
    except Exception as e:
        st.error(f"❌ 檔案讀取錯誤: {str(e)}")

# ==============================================================================
# 七、 FinMind 備援：多執行緒歷史回填 (斷點續傳)
# ==============================================================================
def execute_heavy_data_sync(target_codes, target_date):
    progress_bar = st.progress(0)
    status_text = st.empty()
    if target_date not in st.session_state['inst_history']: 
        st.session_state['inst_history'][target_date] = {}
        
    missing = [c for c in target_codes if c not in st.session_state['inst_history'][target_date]]
    if not missing:
        st.success("✅ 當日大腦記憶庫已滿，無需重複抓取。")
        return
        
    status_text.info(f"📡 啟動 FinMind 靶向補齊引擎，回填 {len(missing)} 檔...")
    success_count = 0
    url = 'https://api.finmindtrade.com/api/v4/data'
    
    def fetch_finmind_worker(code):
        token = FINMIND_TOKENS[st.session_state['active_key_index']]
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

            st.session_state['inst_history'][target_date][code] = payload
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
    st.success(f"✅ 真實數據大腦回填完畢！成功充填: {success_count} 檔。")
    time.sleep(0.5); st.rerun()

# ==============================================================================
# 八、 結構化 AI 幕僚生成引擎 (Gemini API)
# ==============================================================================
def generate_ai_report(command_name, candidates):
    if not GEMINI_API_KEYS or not GEMINI_API_KEYS[0]: return "⚠️ 未配置有效 AI 金鑰。"
    if not candidates: return "⚠️ 目前沒有符合條件的標的。"
    
    candidates = sorted(candidates, key=lambda x: x['vol_ratio'], reverse=True)[:5]
    lite_data = [{'代號': c['code'], '名稱': c['name'], '價格': c['price'], '漲幅': c['gain'], '外資買賣': c['f_buy'], '型態': [p['text'] for p in c['detected_patterns']]} for c in candidates]
    
    prompt = f"你是首席戰略幕僚。總指揮使用戰術：【{command_name}】。名單：\n{json.dumps(lite_data, ensure_ascii=False)}\n請以繁體中文針對這幾檔給出具體沙盤推演與防守價位。本報告僅供教育與學術研究，請客觀分析，不可有非法保證獲利之詞彙。"
    
    key = GEMINI_API_KEYS[st.session_state['active_key_index'] % len(GEMINI_API_KEYS)]
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={key}"
        res = requests.post(url, headers={'Content-Type': 'application/json'}, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=15)
        if res.status_code == 200: return f"**([AI 核心運算完成])**\n\n{res.json()['candidates'][0]['content']['parts'][0]['text']}"
    except Exception: pass
    return "❌ AI 連線失敗或超時。"

# ==============================================================================
# 九、 介面與 CSS 樣式
# ==============================================================================
st.markdown("""<style>
:root { color-scheme: dark !important; }
html, body, [class*="css"] { color-scheme: dark !important; background-color: #0b0c0f !important; color: #fff !important; font-family: Arial, sans-serif; }
div[data-testid="stSidebar"] { background-color: #12141a !important; border-right: 1px solid #333 !important; }
div[data-testid="stButton"] > button { background-color: #1e1e24 !important; border: 1px solid #444 !important; }
div[data-testid="stButton"] > button p { color: #00d2ff !important; font-weight: bold !important; font-size: 14px !important; }
.hud-box { background: linear-gradient(135deg, #1a1c23 0%, #0d1117 100%); border-radius: 10px; padding: 15px; border-left: 5px solid #ff4d4d; margin-bottom: 20px;}
.tag-base { display: inline-block; padding: 3px 6px; border-radius: 4px; font-size: 12px; font-weight: bold; margin: 0 4px 4px 0; }
.tag-red { background: #3a1515; color: #ff4d4d; border: 1px solid #e74c3c; }
.tag-green { background: #153a20; color: #00FF00; border: 1px solid #2ecc71; }
.tag-blue { background: #15203a; color: #00d2ff; border: 1px solid #3498db; }
.tag-purple { background: #2a153a; color: #d200ff; border: 1px solid #9b59b6; }
.zone-box { background: #11141c; border: 1px solid #2c3e50; border-radius: 6px; padding: 10px; margin-bottom: 8px; }
.zone-title { color: #00d2ff; font-weight: bold; font-size: 13px; margin-bottom: 6px; border-bottom: 1px dashed #333; padding-bottom: 3px; }
</style>""", unsafe_allow_html=True)

# ----------------- 側邊欄控制台 -----------------
with st.sidebar:
    st.markdown("<h2 style='color:#f1c40f; text-align:center;'>⚙️ 戰略控制台</h2>", unsafe_allow_html=True)
    if st.button("🔄 強制重整畫面", use_container_width=True): st.rerun()
    st.divider()
    
    if not API_READY:
        st.error("⚠️ 雲端保險箱讀取失敗：系統將以無金鑰受限模式運行。")
    else:
        st.success("✅ API 雲端金鑰串接成功")
        
    with st.expander("📊 資料庫完整度天數細節", expanded=False):
        db_days = max(1, len(st.session_state.get('inst_history', {})))
        st.write(f"總天數: {db_days} 天")
        for d, data in st.session_state.get('inst_history', {}).items():
            st.caption(f"📅 {d}: 已存 {len(data)} 檔")
    
    target_date_sim = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
    
    with st.expander("📥 [主攻] 官方 CSV 籌碼強填中樞", expanded=True):
        st.markdown("<span style='font-size:12px; color:#aaa;'>免 API 限制，請上傳證交所「三大法人日報」CSV 檔。</span>", unsafe_allow_html=True)
        uploaded_csv = st.file_uploader("上傳官方 CSV", type=['csv'])
        if uploaded_csv is not None:
            if st.button("🚀 執行大腦強制解析回填", use_container_width=True):
                process_twse_csv(uploaded_csv, target_date_sim)

    with st.expander("📡 [備援] FinMind 靶向補齊引擎"):
        slider_sync_range = st.slider("同步上限檔數", min_value=100, max_value=1750, value=300, step=100)
        if st.button("🚀 執行 FinMind 遺失補齊", use_container_width=True):
            execute_heavy_data_sync(GLOBAL_MARKET_CODES[:slider_sync_range], target_date_sim)
            
    st.divider()
    st.markdown("<h4 style='color:#00d2ff;'>💾 備份與還原智慧大腦</h4>", unsafe_allow_html=True)
    export_json = json.dumps({"pinned_stocks": st.session_state['pinned_stocks'], "portfolio": st.session_state['portfolio'], "inst_history": st.session_state['inst_history']}, ensure_ascii=False)
    st.download_button("💾 下載目前記憶體 JSON", data=export_json, file_name=f"54088_Backup_{datetime.now().strftime('%Y%m%d')}.json", mime="application/json", use_container_width=True)
    
    uploaded_file = st.file_uploader("📤 上傳還原大腦 JSON 檔", type=['json'])
    if uploaded_file is not None:
        if st.button("⚠️ 確認覆蓋並還原記憶體", use_container_width=True):
            try:
                imported_data = json.loads(uploaded_file.getvalue().decode("utf-8"))
                st.session_state['pinned_stocks'] = imported_data.get("pinned_stocks", {})
                st.session_state['portfolio'] = imported_data.get("portfolio", {})
                if "inst_history" in imported_data: st.session_state['inst_history'] = imported_data["inst_history"]
                save_local_db_isolated(); st.success("✅ 備份大腦還原成功！"); st.rerun()
            except Exception: st.error("檔案解析失敗")
            
    st.divider()
    enable_doomsday_lock = st.checkbox("💀 開啟末日鎔斷防護鎖", value=False)
    min_yield_filter = st.slider("最低現金殖利率門檻調整 (%)", 0.0, 30.0, 4.5, 0.5)
    min_volume_filter = st.slider("最低 5 日波段均量過濾 (張)", 0, 5000, 500, 100)
    
    st.divider()
    commands_list = ["查1.主升段突擊", "查2.魚頭潛伏支撐", "查3.價值投資與循環", "查4.投信作帳集團股", "查5.籌碼外資霸王色", "查6.營收雙增爆發突破", "查7.股癌戰情雷達", "查8.昨日強勢動能延續", "查9.均線糾結爆量突破", "查10.籌碼沉澱量縮潛伏", "查11.除權息尋寶雷達", "查12.K線型態尋寶型"]
    selected_cmd = st.radio("戰術指令：", commands_list, label_visibility="collapsed")
    selected_k_patterns = []
    if "查12" in selected_cmd:
        with st.container(border=True):
            if st.checkbox("🔥 長紅吞噬 / 低檔長紅"): selected_k_patterns.append("長紅")
            if st.checkbox("🔥 紅三兵強勢推升"): selected_k_patterns.append("紅三兵")
            if st.checkbox("💀 長黑吞噬頂部出貨"): selected_k_patterns.append("長黑")
            
    with st.expander("📖 統籌戰術解密說明書"):
        st.write("查1~10: 常規籌碼戰。查11: 高殖利率除息。查12: K線轉折識別。")

# ----------------- 五大戰區字卡渲染中樞 -----------------
def render_comprehensive_5_zone_card_v133(card, prefix_id):
    if card.get('error', False):
        st.warning(f"⚠️ {card['code']} {card['name']} 無真實連線數據，已優雅降級跳過。")
        return
        
    gain_c = '#ff4d4d' if card['gain'] > 0 else ('#00FF00' if card['gain'] < 0 else '#aaaaaa')
    gain_b = '#3a1515' if card['gain'] > 0 else ('#153a20' if card['gain'] < 0 else '#333333')
    vol_c = '#ff4d4d' if card['vol_change_pct'] > 0 else '#00FF00'
    vol_t = f"🔥 爆量 {card['vol_change_pct']:+.1f}%" if card['vol_change_pct'] > 0 else f"🧊 量縮 {card['vol_change_pct']:.1f}%"
    
    mines = "".join([f"<span class='tag-base tag-green' style='background:#2c3e50; color:#f1c40f; border:1px solid #f1c40f;'>🚨 財報地雷：{t}</span> " for t in card['mine_tags']])
    badges = "".join([f"<span class='tag-base {p['class']}'>{p['text']}</span> " for p in card['detected_patterns']])
    bulls = "<br>".join([f"<span style='color:#ff4d4d;'>{item}</span>" for item in card['multi_bull']])
    bears = "<br>".join([f"<span style='color:#00FF00;'>{item}</span>" for item in card['multi_bear']])
    if not bulls and not bears: bulls = "<span style='color:#aaa;'>☑️ 盤整結構無明顯訊號</span>"

    html_content = f"""
<div style="border:2px solid {card['color_border']}; border-radius:8px; padding:15px; background:#16191f; margin-bottom:12px;">
<div style="display:flex; justify-content:space-between; align-items:center;">
<span style="font-weight:bold; font-size:19px; color:#ffffff;">{card['name']} <span style="color:#00d2ff;">({card['code']})</span> <span style="font-size:12px; color:#fff; background:#2c3e50; padding:2px 6px; border-radius:4px; margin-left:5px;">{card['sector']}</span></span>
<span style="font-size:13px; color:#f1c40f;">外資共識價: <b>{card['consensus_target']:.1f}</b> (回報: <strong style="color:#ff4d4d;">+{card['potential_roi']}%</strong>)</span>
</div>
<div style="font-size:32px; font-weight:bold; margin:8px 0; display:flex; align-items:center;">
{card['price']:.2f} <span style="font-size:15px; color:{gain_c}; background:{gain_b}; padding:3px 8px; border-radius:4px; margin-left:10px;">{card['gain']:+.2f}%</span>
<span style="font-size:14px; color:#ccc; margin-left:15px;">近7日: {card['sparkline_html']}</span>
</div>
<div style="display:flex; justify-content:space-between; font-size:13px; color:#aaa; margin-bottom:10px; background:#0e1117; padding:8px; border-radius:4px;">
<span>總量: <b>{card['vol']:,} K張</b> (<span style="color:{vol_c}; font-weight:bold;">{vol_t}</span>)</span>
<span>爆量比: <strong style="color:#e67e22;">{card['vol_ratio']:.1f}x</strong></span>
<span>{card['intraday_str']}</span>
</div>
<div style="margin-bottom:10px;">{badges}{mines}</div>
<div class="zone-box"><div class="zone-title">❤️ 戰區一：基本與財報面</div><div style="font-size:13px; color:#ddd;">營收 YoY: <strong style="color:#00d2ff;">{card['rev_yoy']:.1f}%</strong> | MoM: <strong style="color:#00d2ff;">{card['rev_mom']:.1f}%</strong> | 除息: <strong style="color:#d200ff;">{card['div_display']} ({card['div_yield']:.1f}%)</strong></div></div>
<div class="zone-box"><div class="zone-title">⚔️ 戰區二：技術與多空領先指標清單</div><div style="display:flex; justify-content:space-between; font-size:13px; margin-bottom:4px;"><span style="color:#ccc;">20MA防守: {card['ma20']:.1f}</span><span style="color:{card['macd_color']};">{card['macd_str']}</span><span style="color:#f1c40f;">KDJ: {card['kdj_str']}</span></div><div style="font-size:13px; line-height:1.6; border-top:1px dashed #333; padding-top:4px;">{bulls}<br>{bears}</div></div>
<div class="zone-box"><div class="zone-title">📊 戰區三：三大法人與千張大戶籌碼</div><div style="font-size:13px; color:#ddd; margin-bottom:4px;">外: <strong style="color:#ff4d4d;">{card['f_buy']:,} 張</strong> | 投: <strong style="color:#ff4d4d;">{card['t_buy']:,} 張</strong> | 自: <strong style="color:#ff4d4d;">{card['d_buy']:,} 張</strong> | 融資: <strong style="color:#f1c40f;">{card['margin_diff']:,} 張</strong></div><div style="font-size:12px; color:#aaa; border-top:1px dashed #333; padding-top:4px;">千張大戶持股比率: <strong style="color:#00d2ff;">{card['big_holder']}%</strong></div></div>
<div style="background:{card['signal_bg']}; padding:10px; border-radius:5px; text-align:center; border:1px solid {card['color_border']}40; margin-bottom:8px;"><strong style="color:{card['color_border']}; font-size:15px;">決策判定：{card['signal_text']}</strong></div>
</div>
"""
    safe_html = re.sub(r'^\s+', '', html_content, flags=re.MULTILINE)
    st.markdown(safe_html, unsafe_allow_html=True)
    
    with st.expander("🤖 解鎖第四、五戰區：[AI 戰略推演與多空綜合健診]"):
        st.progress(card['bull_score'] / 100)
        st.caption(f"多方健診項目佔比: {card['bull_score']}% | 交易定位：{card['trade_attr']}")
        prompt = f"""請以首席 AI 戰略幕僚身分，依據台灣證券法規規範，針對以下標的提供「歷史數據沙盤推演」：
【標的】{card['name']} ({card['code']})
【第一戰區：財報體質】營收 YoY {card['rev_yoy']:.1f}% | MoM {card['rev_mom']:.1f}% | 地雷警示: {', '.join(card['mine_tags']) if card['mine_tags'] else '無異常'}
【第二戰區：技術動能】現價 {card['price']:.2f} | 爆量比: {card['vol_ratio']:.1f}x | KDJ: {card['kdj_str']} | 均線防守: {card['ma20']:.1f}
【第三戰區：主力籌碼】外資 {card['f_buy']} 張 | 投信 {card['t_buy']} 張 | 自營商 {card['d_buy']} 張 | 大戶持股 {card['big_holder']}%
請你務必依照上述三大戰區分別給出冷血客觀的點評，最後再給出「🎯 總指揮明日戰略總結」(具體的防守價位與進退建議)。
*(註：本報告僅供學術教育研究，不作絕對獲利保證)*"""
        st.code(prompt, language="markdown")
        
    with st.expander("⚙️ [管理面板] (單檔庫存與追蹤剔除)"):
        m_cols = st.columns(2)
        if m_cols[0].button("轉移至庫存 (模擬倉)", key=f"mov_{prefix_id}_{card['code']}", use_container_width=True):
            st.session_state'portfolio' = {"entry_price": card['price'], "qty": 1}
            st.session_state['pinned_stocks'].pop(card['code'], None)
            save_local_db_isolated(); st.rerun()
            
        if m_cols[1].button("從防線移除", key=f"del_{prefix_id}_{card['code']}", use_container_width=True):
            if prefix_id == "port_zone": st.session_state['portfolio'].pop(card['code'], None)
            else: st.session_state['pinned_stocks'].pop(card['code'], None)
            save_local_db_isolated(); st.rerun()

# ----------------- 主視窗渲染 -----------------
st.title("🚀 54088 戰情室 V133 終極滿血版")
st.markdown(f"""<div class='hud-box'>
    <div style='color:#f1c40f; font-size:16px; font-weight:bold; margin-bottom:8px;'>📊 大將軍戰情智慧總覽中樞 (HUD)</div>
    <div style='color:#ddd; font-size:14px; margin-bottom:4px;'><b>大盤風向：</b> {weather_str}</div>
</div>""", unsafe_allow_html=True)

search_input = st.text_input("🔍 手動輸入股票代號加入雷達 (如: 2330 2303)", "")
if st.button("➕ 手動強制加入", use_container_width=True):
    if search_input:
        found_codes = re.findall(r'\b\d{4}\b', search_input)
        for c in found_codes: st.session_state['pinned_stocks'][c] = {}
        save_local_db_isolated(); st.rerun()

# 模擬倉 (真實扣稅運算)
if st.session_state.get('portfolio'):
    total_pnl = 0
    with st.expander("💼 總指揮庫存模擬倉 (真實手續費扣除運算中)", expanded=True):
        cols = st.columns(2)
        idx = 0
        for code, p_data in list(st.session_state['portfolio'].items()):
            c = calculate_comprehensive_signals(code, enable_doomsday_lock)
            if c and not c.get('error'):
                ent_p = safe_float(p_data.get('entry_price', c['price']))
                qty = safe_float(p_data.get('qty', 1))
                profit, roi = calc_real_profit(ent_p, c['price'], qty)
                total_pnl += profit
                with cols[idx % 2]:
                    st.markdown(f"<div style='font-size:14px; color:#fff; margin-bottom:5px;'>成本: {ent_p} | 淨利: <strong style='color:{'#ff4d4d' if profit>0 else '#00FF00'};'>{int(profit):+,}</strong> ({roi:+.2f}%)</div>", unsafe_allow_html=True)
                    render_comprehensive_5_zone_card_v133(c, prefix_id="port_zone")
                idx += 1
        st.markdown(f"### 🎯 總未實現淨損益: <span style='color:{'#ff4d4d' if total_pnl>0 else '#00FF00'};'>{int(total_pnl):+,} 元</span>", unsafe_allow_html=True)

# 觀測雷達
pin_cards = {}
for code in list(st.session_state.get('pinned_stocks', {}).keys()):
    c = calculate_comprehensive_signals(code, enable_doomsday_lock)
    if c: pin_cards[code] = c

if st.session_state.get('pinned_stocks'):
    with st.expander("🎯 總指揮常態觀測雷達防線", expanded=True):
        cols = st.columns(2)
        idx = 0
        for code, card in pin_cards.items():
            with cols[idx % 2]: render_comprehensive_5_zone_card_v133(card, prefix_id="pin_zone")
            idx += 1

# ----------------- 初篩海選掃描 -----------------
def get_scope_codes():
    return GLOBAL_MARKET_CODES

scan_scope_name = "全市場"
if st.sidebar.button("🔎 [啟動全市場真實連線初篩掃描]", use_container_width=True, type="primary"):
    with st.spinner(f"重型 API 引擎運算中... (執行 1700 檔掃描，超時將自動優雅降級)..."):
        results = []
        codes = get_scope_codes()
        for c in codes:
            card = calculate_comprehensive_signals(c, enable_doomsday_lock)
            if card and not card.get('error') and card['vol'] >= (min_volume_filter / 1000):
                valid = False
                if "查1" in selected_cmd and card['is_first_red'] and card['vol_ratio'] >= 2.0 and "金叉" in card['kdj_str']: valid = True
                elif "查2" in selected_cmd and card['price'] > card['ma60'] and card['vol_ratio'] >= 1.2: valid = True
                elif "查3" in selected_cmd and card['bull_score'] >= 60 and not card['mine_tags']: valid = True
                elif "查4" in selected_cmd and card['t_buy'] > 0: valid = True
                elif "查5" in selected_cmd and card['f_buy'] > 0 and card['margin_diff'] < 0: valid = True
                elif "查6" in selected_cmd and card['rev_yoy'] > 20: valid = True
                elif "查8" in selected_cmd and card['is_yesterday_strong']: valid = True
                elif "查9" in selected_cmd and card['vol_ratio'] >= 2.0: valid = True
                elif "查10" in selected_cmd and card['vol_change_pct'] < -40 and card['margin_diff'] < 0: valid = True
                elif "查11" in selected_cmd and card['div_yield'] >= min_yield_filter: valid = True
                elif "查12" in selected_cmd and selected_k_patterns:
                    if any(p in [x['text'] for x in card['detected_patterns']] for p in selected_k_patterns): valid = True
                elif "查" not in selected_cmd: valid = True 
                
                if valid: results.append(card)
                
        st.session_state['scan_results'] = results
        st.session_state['scan_mode'] = selected_cmd

if st.session_state.get('scan_results'):
    st.markdown(f"### ⚡ {st.session_state['scan_mode']} 篩選戰果清單 ({len(st.session_state['scan_results'])} 檔符合)")
    
    if st.button("🤖 [AI 幕僚] 懶人戰術打包送交運算", type="primary", use_container_width=True):
        with st.spinner("戰略大腦推演中..."):
            st.session_state['ai_report'] = generate_ai_report(st.session_state['scan_mode'], st.session_state['scan_results'])
    if st.session_state.get('ai_report'):
        st.info(st.session_state['ai_report'])
        
    table_rows = []
    for card in st.session_state['scan_results']:
        table_rows.append({
            "代號": card['code'], "名稱": card['name'], "現價": card['price'],
            "漲跌(%)": round(card['gain'], 2), "YoY年增(%)": round(card['rev_yoy'], 1),
            "MoM月增(%)": round(card['rev_mom'], 1), "殖利率(%)": f"{card['div_yield']:.1f}%", "地雷標記": len(card['mine_tags'])
        })
    st.dataframe(pd.DataFrame(table_rows), use_container_width=True, hide_index=True)
    
    if st.button("➕ 批次加入常態追蹤雷達", use_container_width=True):
        for card in st.session_state['scan_results']:
            st.session_state'pinned_stocks' = {}
        save_local_db_isolated(); st.success("✅ 批次新增成功"); st.rerun()
        
    cols = st.columns(2)
    for idx, card in enumerate(st.session_state['scan_results']):
        with cols[idx % 2]: render_comprehensive_5_zone_card_v133(card, prefix_id=f"scan_res_{idx}")
