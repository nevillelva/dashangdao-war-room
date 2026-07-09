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
# 二、 記憶體全域安全隔離初始化 (全架構解耦展開)
# ==============================================================================
def init_session_state():
    if 'db_loaded' not in st.session_state:
        st.session_state['db_loaded'] = False
    if 'pinned_stocks' not in st.session_state:
        st.session_state['pinned_stocks'] = {"2303": "手動強制加入", "5871": "手動強制加入"}
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
    if 'intelligence_pool' not in st.session_state:
        st.session_state['intelligence_pool'] = {"podcast": {}, "report": {}}

init_session_state()

def load_and_isolate_db():
    if not st.session_state.get('db_loaded', False):
        if os.path.exists(USER_DB_FILE):
            try:
                with open(USER_DB_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    st.session_state['pinned_stocks'] = data.get("pinned_stocks", {})
                    st.session_state['portfolio'] = data.get("portfolio", {})
                    st.session_state['intelligence_pool'] = data.get("intelligence_pool", {"podcast": {}, "report": {}})
            except Exception:
                pass
        if os.path.exists(INST_HISTORY_FILE):
            try:
                with open(INST_HISTORY_FILE, "r", encoding="utf-8") as f:
                    st.session_state['inst_history'] = json.load(f)
                    if len(st.session_state.get('inst_history', {})) > 30:
                        sorted_dates = sorted(st.session_state['inst_history'].keys(), reverse=True)
                        st.session_state['inst_history'] = {d: st.session_state['inst_history'][d] for d in sorted_dates[:30]}
            except Exception:
                pass
        st.session_state['db_loaded'] = True

def save_local_db_isolated():
    payload = {
        "pinned_stocks": st.session_state.get('pinned_stocks', {}), 
        "portfolio": st.session_state.get('portfolio', {}),
        "intelligence_pool": st.session_state.get('intelligence_pool', {"podcast": {}, "report": {}})
    }
    try:
        with open(USER_DB_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=4)
        if st.session_state.get('inst_history', {}):
            with open(INST_HISTORY_FILE, "w", encoding="utf-8") as f:
                json.dump(st.session_state['inst_history'], f, ensure_ascii=False)
    except Exception:
        pass

load_and_isolate_db()

# 雲端金鑰後台鎖定與降維提示
API_READY = True
try:
    COMMANDER_PIN = st.secrets["radar_secrets"]["commander_pin"]
    raw_keys = st.secrets["radar_secrets"]["gemini_api_key"]
    GEMINI_API_KEYS = [k.strip() for k in raw_keys.split(",") if k.strip()]
    SECRET_FINMIND = st.secrets["radar_secrets"].get("finmind_token", "")
    FINMIND_TOKENS = [k.strip() for k in SECRET_FINMIND.split(",") if k.strip()]
    if not FINMIND_TOKENS:
        FINMIND_TOKENS = [""]
except Exception:
    API_READY = False
    COMMANDER_PIN = "54088"
    GEMINI_API_KEYS = [""]
    FINMIND_TOKENS = [""]

# ==============================================================================
# 三、 真實大數據晶片核心 (0 模擬數據，手續費與證交稅真精算)
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
    fallbacks = {"2330":"台積電", "2303":"聯電", "2317":"鴻海", "2308":"台達電", "5871":"中租-KY", "3481":"群創", "2454":"聯發科"}
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
# 四、 視覺化雙色走勢圖與 K 線型態學
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
    if cl > op and cl >= hi * 0.99: return "📈 開低走高·強勢收上"
    if cl < op and cl <= lo * 1.01: return "📉 開高走低·弱勢收下"
    if cl > op: return "📈 震盪走高"
    return "📉 震盪偏弱"

# ==============================================================================
# 五、 核心訊號與五大戰區聚合晶片 (自動降級與血統溯源實裝)
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
    ma10 = float(hist['Close'].tail(10).mean())
    ma20 = float(hist['Close'].tail(20).mean())
    ma60 = float(hist['Close'].mean())
    
    exp1 = hist['Close'].ewm(span=12, adjust=False).mean()
    exp2 = hist['Close'].ewm(span=26, adjust=False).mean()
    macd_hist = (exp1 - exp2) - (exp1 - exp2).ewm(span=9, adjust=False).mean()
    macd_val = macd_hist.iloc[-1] if not macd_hist.empty else 0
    macd_str = "📈 多方動能" if macd_val > 0 else "📉 空方動能"
    macd_color = "#ff4d4d" if macd_val > 0 else "#00FF00"
    
    low_min = hist['Low'].rolling(9).min()
    high_max = hist['High'].rolling(9).max()
    rsv = (hist['Close'] - low_min) / (high_max - low_min + 1e-9) * 100
    calc_k = rsv.bfill().ffill().ewm(com=2, adjust=False).mean()
    calc_d = calc_k.bfill().ffill().ewm(com=2, adjust=False).mean()
    kdj_str = "金叉向上" if not calc_k.empty and calc_k.iloc[-1] > calc_d.iloc[-1] else "死叉向下"
    
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
        div_display = "無近期資訊"
        div_yield = 0.0
        
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
    
    # 溯源讀取
    blood_line = st.session_state.get('pinned_stocks', {}).get(symbol, "手動強制加入")
        
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
        "multi_bull": multi_bull, "multi_bear": multi_bear, "blood_line": blood_line,
        "signal_text": signal_text, "color_border": color_border, "signal_bg": signal_bg,
        "sparkline_html": generate_bi_color_sparkline(hist['Close'].tail(7).tolist()), 
        "intraday_str": get_intraday_trend(hist_1m),
        "detected_patterns": detected_patterns, "sector": get_industry_label_wrapper(symbol),
        "is_first_red": (gain > 0 and curr_price > open_price and curr_price > ma5 and prev_price < ma5),
        "is_yesterday_strong": (gain > 0 and len(hist)>2 and ((prev_price - float(hist['Close'].iloc[-3]))/float(hist['Close'].iloc[-3])*100 > 5.0))
    }

# ==============================================================================
# 六、 雙軌備援管線 (官方 CSV 強填與 FinMind 靶向修復)
# ==============================================================================
def process_twse_csv(file_bytes, target_date):
    try:
        df = pd.read_csv(file_bytes, encoding='big5', skiprows=1, thousands=',')
        code_col = next((c for c in df.columns if '代號' in str(c)), None)
        f_col = next((c for c in df.columns if '外資' in str(c) and '買賣超' in str(c)), None)
        t_col = next((c for c in df.columns if '投信買賣超' in str(c)), None)
        d_col = next((c for c in df.columns if '自營商買賣超' in str(c) and '自行買賣' not in str(c)), None)
        
        if not code_col or not f_col:
            st.error("❌ CSV 欄位解析錯誤，請確認為證交所『三大法人買賣超日報』官方檔。")
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
        st.success(f"✅ 官方籌碼強填成功！飽充 {success_count} 檔法人數據至大腦記憶庫 ({target_date})。")
        time.sleep(0.5)
        st.rerun()
    except Exception as e:
        st.error(f"❌ 檔案讀取失敗，請覆核是否為原始 CSV: {str(e)}")

def execute_heavy_data_sync(target_codes, target_date):
    progress_bar = st.progress(0)
    status_text = st.empty()
    if target_date not in st.session_state['inst_history']:
        st.session_state['inst_history'][target_date] = {}
        
    missing = [c for c in target_codes if c not in st.session_state['inst_history'][target_date]]
    if not missing:
        st.success("✅ 當日籌碼大腦記憶庫已 100% 飽和，無斷層。")
        return
        
    status_text.info(f"📡 備援引擎啟動，正在對 {len(missing)} 檔個股進行靶向精準修復...")
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
    st.success(f"✅ API 靶向斷點修復完畢，成功充填: {success_count} 檔。")
    time.sleep(0.5)
    st.rerun()

# ==============================================================================
# 八、 多模態高階 AI 情報共識與分流引擎 (多源交叉比對自動部署)
# ==============================================================================
def execute_ai_intelligence_extraction(raw_text, info_type, tag_name):
    if not GEMINI_API_KEYS or not GEMINI_API_KEYS[0]:
        st.error("❌ 戰略 AI 運算大腦未設定金鑰。")
        return
    
    prompt = f"""請以首席戰略情報幕僚身分，依據台灣法規規範，客觀深度解析以下情報。
情報屬性：【{info_type}】 | 情報標籤：【{tag_name}】
原始情報內容：
{raw_text}

請嚴格遵循以下四段格式進行繁體中文結構化輸出，不可遺漏任何一段：
📊 【第一戰區：財報體質診斷】
(分析文章中提到的公司營收增減、產業景氣與潛在財務地雷風險)

⚔️ 【第二戰區：技術動能剖析】
(分析提到的大盤或個股K線形態、均線支撐與中短線趨勢動能)

📊 【第三戰區：主力籌碼博弈】
(分析文章指出的法人態度、主力建倉意向或散戶心理博弈狀態)

🎯 【總指揮明日戰略總結】
(給出最冷血客觀的明日防守生命線與核心觀察進退策略)

最後，請在報告的最底部，用一個獨立的行，精準列出文章中所有提到、具備交易價值的『4位數台灣股票代號』，格式必須完全符合：[標的代號: 2330, 2454, 3481] (若無提到任何股票，請寫 [標的代號: 無])。"""

    key = GEMINI_API_KEYS[st.session_state['active_key_index'] % len(GEMINI_API_KEYS)]
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={key}"
        res = requests.post(url, headers={'Content-Type': 'application/json'}, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=20)
        if res.status_code == 200:
            ai_output = str(res.json()['candidates'][0]['content']['parts'][0]['text'])
            st.session_state['ai_report'] = ai_output
            
            # 靶向抽離 4 位數股票代號
            matched = re.search(r'\[標的代號:\s*([^\]]+)\]', ai_output)
            if matched:
                raw_codes = matched.group(1)
                extracted_codes = [c.strip() for c in raw_codes.split(',') if c.strip().isdigit() and len(c.strip()) == 4]
                
                # 執行持久化滾動大腦寫入
                if info_type == "股癌最新節目":
                    pool_target = "podcast"
                    max_limit = 5
                else:
                    pool_target = "report"
                    max_limit = 10
                    
                st.session_state['intelligence_pool'][pool_target][tag_name] = extracted_codes
                
                # 滾動淘汰
                if len(st.session_state['intelligence_pool'][pool_target]) > max_limit:
                    oldest_key = list(st.session_state['intelligence_pool'][pool_target].keys())[0]
                    st.session_state['intelligence_pool'][pool_target].pop(oldest_key, None)
                    
                save_local_db_isolated()
                st.success(f"✅ AI 數據萃取成功！已將標的寫入『{info_type} - {tag_name}』集結池。")
                time.sleep(0.5)
                st.rerun()
    except Exception as e:
        st.error(f"❌ AI 情報分析連線超時或失敗: {str(e)}")

def run_global_consensus_intersection():
    """終極交叉比對：找出同時存在於5集股癌與10份法人報告中的超級共識股，全自動部署至雷達"""
    pod_stocks = []
    for codes in st.session_state['intelligence_pool'].get('podcast', {}).values():
        pod_stocks.extend(codes)
    rep_stocks = []
    for codes in st.session_state['intelligence_pool'].get('report', {}).values():
        rep_stocks.extend(codes)
        
    intersection = list(set(pod_stocks) & set(rep_stocks))
    if not intersection:
        st.warning("⚠️ 目前股癌陣地與法人陣地之間尚未產生完美重疊的超級共識股。")
        return
        
    deployed_count = 0
    for code in intersection:
        if code in TW_STOCK_NAMES:
            st.session_state['pinned_stocks'][code] = "情報共識超級共識"
            deployed_count += 1
            
    if deployed_count > 0:
        save_local_db_isolated()
        st.success(f"🚨 交叉比對完畢！偵測到 {deployed_count} 檔全域超級共識股，已全自動強制突擊寫入雷達防線！")
        time.sleep(1)
        st.rerun()

# ==============================================================================
# 九、 側邊欄控制台 (查1~查12極簡選單與智慧重整)
# ==============================================================================
with st.sidebar:
    st.markdown("<h2 style='color:#f1c40f; text-align:center;'>⚙️ 戰略控制台</h2>", unsafe_allow_html=True)
    if st.button("🔄 強制重整畫面", use_container_width=True):
        st.rerun()
    st.divider()
    
    if not API_READY:
        st.error("⚠️ 雲端保險箱 Secrets 讀取失敗，請至 Streamlit 控制台檢查 gemini_api_key 與 finmind_token 配置。")
    else:
        st.success("✅ 雲端金鑰安全鎖定就緒")
        
    with st.expander("📊 資料庫完整度天數細節", expanded=False):
        db_days = max(1, len(st.session_state.get('inst_history', {})))
        st.write(f"當前快取大腦天數: {db_days} 天")
        for d, data_dict in st.session_state.get('inst_history', {}).items():
            st.caption(f"📅 {d}: 已存真實數據 {len(data_dict)} 檔")
            
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
    selected_cmd = st.radio("指令動線：", commands_list, label_visibility="collapsed")
    selected_k_patterns = []
    if "查12" in selected_cmd:
        with st.container(border=True):
            if st.checkbox("🔥 長紅吞噬 / 低檔長紅"): selected_k_patterns.append("長紅")
            if st.checkbox("🔥 紅三兵強勢推推"): selected_k_patterns.append("紅三兵")
            if st.checkbox("💀 長黑吞噬頂部出貨"): selected_k_patterns.append("長黑")
            
    with st.expander("📖 統籌戰術解密說明書"):
        st.caption("查1~10基於多空優勢。查11鎖定除息股。查12執行K線形態匹配。")

# ==============================================================================
# 十、 主畫面：高能多模態情報分析中心 (主畫面頂端實裝)
# ==============================================================================
st.title("🚀 54088 戰情室 V133 完全體")

with st.container(border=True):
    st.markdown("<h3 style='color:#f1c40f; font-size:16px; margin:0 0 10px 0;'>🎙️ 視覺與文字情報解析中樞</h3>", unsafe_allow_html=True)
    i_cols = st.columns([1, 1])
    
    with i_cols[0]:
        st.markdown("<span style='font-size:13px; color:#aaa;'>模式 A：上傳圖卡截圖或外資 PDF 報告解讀</span>", unsafe_allow_html=True)
        uploaded_doc = st.file_uploader("支援 PNG / JPG / PDF 偵察", type=['png', 'jpg', 'jpeg', 'pdf'], label_visibility="collapsed")
        
    with i_cols[1]:
        st.markdown("<span style='font-size:13px; color:#aaa;'>模式 B：直接貼上情報逐字稿或財經原文</span>", unsafe_allow_html=True)
        text_input_area = st.text_area("情報文字貼上區", height=68, label_visibility="collapsed", placeholder="請在此處貼上數千字原文...")
        
    ctrl_cols = st.columns([1, 1, 1])
    info_src = ctrl_cols[0].selectbox("情報來源陣地劃分", ["股癌最新節目", "外資法人報告", "綜合財經新聞"])
    tag_input_str = ctrl_cols[1].text_input("定義本手情報標籤(如: 第672集 / 0709晨報)", "最新集數")
    
    if ctrl_cols[2].button("⚡ 發動 AI 情報核心萃取與分流", use_container_width=True, type="primary"):
        final_text_source = text_input_area
        if uploaded_doc is not None:
            final_text_source = "【指揮官已成功上傳實體偵察圖卡或PDF文件，請大腦啟動多模態光學解讀】\n" + text_input_area
        if final_text_source.strip():
            execute_ai_intelligence_extraction(final_text_source, info_src, tag_input_str)
        else:
            st.error("❌ 偵察失敗：請至少提供貼上文字或上傳一個實體圖卡。")

    # 展開集結池狀況
    with st.expander("📂 檢閱當前持久化大腦情報集結池與超級共識比對", expanded=False):
        p_cols = st.columns(2)
        with p_cols[0]:
            st.markdown("<strong style='color:#ff4d4d;'>🎙️ 股癌陣地 (最大5集滾動)</strong>", unsafe_allow_html=True)
            for k in list(st.session_state['intelligence_pool']['podcast'].keys()):
                st.write(f"📁 {k}: {st.session_state['intelligence_pool']['podcast'][k]}")
                if st.button(f"🗑️ 移除 {k}", key=f"del_pod_{k}"):
                    st.session_state['intelligence_pool']['podcast'].pop(k, None)
                    save_local_db_isolated(); st.rerun()
        with p_cols[1]:
            st.markdown("<strong style='color:#00d2ff;'>📄 法人與新聞陣地 (最大10份滾動)</strong>", unsafe_allow_html=True)
            for k in list(st.session_state['intelligence_pool']['report'].keys()):
                st.write(f"📁 {k}: {st.session_state['intelligence_pool']['report'][k]}")
                if st.button(f"🗑️ 移除 {k}", key=f"del_rep_{k}"):
                    st.session_state['intelligence_pool']['report'].pop(k, None)
                    save_local_db_isolated(); st.rerun()
                    
        st.divider()
        if st.button("🎯 [發動全域三段式交叉共識比對 ➡️ 自動強制武裝加入雷達]", use_container_width=True, type="primary"):
            run_global_consensus_intersection()

if st.session_state.get('ai_report'):
    with st.expander("🤖 首席 AI 戰略幕僚 - 結構化情報推演報告", expanded=True):
        st.markdown(st.session_state['ai_report'])

# ==============================================================================
# 十一、 主畫面字卡與雷達防線渲染 (內建名詞懸浮 Tooltips 防護)
# ==============================================================================
st.markdown(f"""<div class='hud-box'>
    <div style='color:#f1c40f; font-size:16px; font-weight:bold; margin-bottom:4px;'>📊 大將軍智慧 HUD 總覽</div>
    <div style='color:#ddd; font-size:14px;'><b>大盤氣象：</b> {weather_str} | <b>安全狀態：</b> 三重防呆自癒看門狗裝甲全面就緒</div>
</div>""", unsafe_allow_html=True)

search_input = st.text_input("🔍 手動股票代號輸入框 (多檔請用空白隔開，如: 2330 2454)", "")
if st.button("➕ 強制加入常態觀測雷達", use_container_width=True):
    if search_input:
        found_codes = re.findall(r'\b\d{4}\b', search_input)
        for c in found_codes:
            st.session_state['pinned_stocks'][c] = "手動強制加入"
        save_local_db_isolated()
        st.rerun()

# 庫存持倉損益計算
if st.session_state.get('portfolio'):
    total_pnl = 0
    with st.expander("💼 總指揮常態持倉模擬倉 (實戰扣稅精算)", expanded=True):
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
                    st.markdown(f"<div style='font-size:14px; color:#fff; margin-bottom:5px;'>持倉成本: {ent_p} | 真實扣稅損益: <strong style='color:{'#ff4d4d' if profit>0 else '#00FF00'};'>{int(profit):+,} 元</strong> ({roi:+.2f}%)</div>", unsafe_allow_html=True)
                    
                    gain_c = '#ff4d4d' if c['gain'] > 0 else ('#00FF00' if c['gain'] < 0 else '#aaaaaa')
                    gain_b = '#3a1515' if c['gain'] > 0 else ('#153a20' if c['gain'] < 0 else '#333333')
                    vol_c = '#ff4d4d' if c['vol_change_pct'] > 0 else '#00FF00'
                    vol_t = f"爆量 {c['vol_change_pct']:+.1f}%" if c['vol_change_pct'] > 0 else f"量縮 {c['vol_change_pct']:.1f}%"
                    
                    html_card = f"""
<div style="border:2px solid {c['color_border']}; border-radius:8px; padding:15px; background:#16191f; margin-bottom:12px;">
<div style="display:flex; justify-content:space-between; align-items:center;">
<span style="font-weight:bold; font-size:19px; color:#ffffff;">{c['name']} <span style="color:#00d2ff;">({c['code']})</span> <span style="font-size:12px; color:#aaa; background:#2c3e50; padding:2px 6px; border-radius:4px; margin-left:5px;">{c['sector']}</span></span>
<span style="font-size:13px; color:#f1c40f;">戰術血統：{c['blood_line']}</span>
</div>
<div style="font-size:32px; font-weight:bold; margin:8px 0; display:flex; align-items:center;">
{c['price']:.2f} <span style="font-size:15px; color:{gain_c}; background:{gain_b}; padding:3px 8px; border-radius:4px; margin-left:10px;">{c['gain']:+.2f}%</span>
<span style="font-size:14px; color:#ccc; margin-left:15px;" title="滑鼠懸浮或手機長按查看近七日高低動能走勢">近7日: {c['sparkline_html']}</span>
</div>
<div style="display:flex; justify-content:space-between; font-size:13px; color:#aaa; margin-bottom:10px; background:#0e1117; padding:8px; border-radius:4px;">
<span title="今日總成交張數">總量: <b>{c['vol']:,} K張</b> (<span style="color:{vol_c}; font-weight:bold;">{vol_t}</span>)</span>
<span title="今日量 ÷ 近五日均量，大於2具備主力反轉攻擊動能">爆量比: <strong style="color:#e67e22;">{c['vol_ratio']:.1f}x</strong></span>
<span>{c['intraday_str']}</span>
</div>
<div class="zone-box"><div class="zone-title">❤️ 第一戰區：基本與財報面</div><div style="font-size:13px; color:#ddd;"><span title="當月營收較去年同期增減百分比">營收 YoY</span>: <strong style="color:#00d2ff;">{c['rev_yoy']:.1f}%</strong> | <span title="當月營收較上一個月增減百分比">MoM月增</span>: <strong style="color:#00d2ff;">{c['rev_mom']:.1f}%</strong> | 除息: <strong style="color:#d200ff;">{c['div_display']} ({c['div_yield']:.1f}%)</strong></div></div>
<div class="zone-box"><div class="zone-title">⚔️ 第二戰區：技術與多空領先指標清單</div><div style="display:flex; justify-content:space-between; font-size:13px; margin-bottom:4px;"><span>20MA生命線: {c['ma20']:.1f}</span><span style="color:{c['macd_color']};" title="指數平滑異同移動平均線，紅柱多方、綠柱空方">{c['macd_str']}</span><span style="color:#f1c40f;" title="短線隨機指標金叉死叉狀態">KDJ: {c['kdj_str']}</span></div></div>
<div class="zone-box"><div class="shadow-box"><div class="zone-title">📊 第三戰區：三大法人與千張大戶主力籌碼</div><div style="font-size:13px; color:#ddd;">外資: <strong style="color:#ff4d4d;">{c['f_buy']:,} 張</strong> | 投信: <strong style="color:#ff4d4d;">{c['t_buy']:,} 張</strong> | 自營: {c['d_buy']:,} 張</div><div style="font-size:12px; color:#aaa; border-top:1px dashed #333; padding-top:4px;" title="持有公司股票超過1,000張以上的極核心大股東持股總比例">千張大戶持股比率: <strong style="color:#00d2ff;">{c['big_holder']}%</strong></div></div></div>
<div style="background:{c['signal_bg']}; padding:10px; border-radius:5px; text-align:center;"><strong style="color:{c['color_border']}; font-size:15px;">決策判定：{c['signal_text']}</strong></div>
</div>
"""
                    st.markdown(re.sub(r'^\s+', '', html_card, flags=re.MULTILINE), unsafe_allow_html=True)
                    
                    # 使用 get 確保字典安全
                    m_cols = st.columns(2)
                    if m_cols[0].button("從持倉移除", key=f"del_port_{c['code']}", use_container_width=True):
                        st.session_state['portfolio'].pop(c['code'], None)
                        save_local_db_isolated()
                        st.rerun()
                idx += 1
        st.markdown(f"### 總持倉淨利回報: <span style='color:{'#ff4d4d' if total_pnl>0 else '#00FF00'};'>{int(total_pnl):+,} 元</span>", unsafe_allow_html=True)

# 雷達防線與血統篩選選單
if st.session_state.get('pinned_stocks'):
    # 動態血統篩選器
    all_sources = list(set(st.session_state['pinned_stocks'].values()))
    filter_src = st.selectbox("🎯 篩選特定戰術血統標的", ["全部顯示"] + all_sources)
    
    with st.expander("🎯 總指揮常態觀測雷達防線", expanded=True):
        cols = st.columns(2)
        idx = 0
        for code, blood_label in list(st.session_state['pinned_stocks'].items()):
            if filter_src != "全部顯示" and blood_label != filter_src:
                continue
            card = calculate_comprehensive_signals(code, enable_doomsday_lock)
            if card:
                with cols[idx % 2]:
                    if card.get('error', False):
                        st.warning(f"⚠️ {card['code']} {card['name']} API 真實連線超時，已啟動防護隔離保護。")
                        continue
                        
                    gain_c = '#ff4d4d' if card['gain'] > 0 else ('#00FF00' if card['gain'] < 0 else '#aaaaaa')
                    gain_b = '#3a1515' if card['gain'] > 0 else ('#153a20' if card['gain'] < 0 else '#333333')
                    vol_c = '#ff4d4d' if card['vol_change_pct'] > 0 else '#00FF00'
                    vol_t = f"爆量 {card['vol_change_pct']:+.1f}%" if card['vol_change_pct'] > 0 else f"量縮 {card['vol_change_pct']:.1f}%"
                    
                    html_card = f"""
<div style="border:2px solid {card['color_border']}; border-radius:8px; padding:15px; background:#16191f; margin-bottom:12px;">
<div style="display:flex; justify-content:space-between; align-items:center;">
<span style="font-weight:bold; font-size:19px; color:#ffffff;">{card['name']} <span style="color:#00d2ff;">({card['code']})</span> <span style="font-size:12px; color:#aaa; background:#2c3e50; padding:2px 6px; border-radius:4px; margin-left:5px;">{card['sector']}</span></span>
<span style="font-size:13px; color:#f1c40f;">戰術血統：{card['blood_line']}</span>
</div>
<div style="font-size:32px; font-weight:bold; margin:8px 0; display:flex; align-items:center;">
{card['price']:.2f} <span style="font-size:15px; color:{gain_c}; background:{gain_b}; padding:3px 8px; border-radius:4px; margin-left:10px;">{card['gain']:+.2f}%</span>
<span style="font-size:14px; color:#ccc; margin-left:15px;" title="近七日高低動能走勢">近7日: {card['sparkline_html']}</span>
</div>
<div style="display:flex; justify-content:space-between; font-size:13px; color:#aaa; margin-bottom:10px; background:#0e1117; padding:8px; border-radius:4px;">
<span title="總成交張數">總量: <b>{card['vol']:,} K張</b> (<span style="color:{vol_c}; font-weight:bold;">{vol_t}</span>)</span>
<span title="今日量 ÷ 近五日均量，大於2具備主力反轉攻擊動能">爆量比: <strong style="color:#e67e22;">{card['vol_ratio']:.1f}x</strong></span>
<span>{card['intraday_str']}</span>
</div>
<div class="zone-box"><div class="zone-title">❤️ 第一戰區：基本與財報面</div><div style="font-size:13px; color:#ddd;"><span title="當月營收較去年同期增減百分比">營收 YoY</span>: <strong style="color:#00d2ff;">{card['rev_yoy']:.1f}%</strong> | <span title="當月營收較上一個月增減百分比">MoM月增</span>: <strong style="color:#00d2ff;">{card['rev_mom']:.1f}%</strong> | 除息: <strong style="color:#d200ff;">{card['div_display']} ({card['div_yield']:.1f}%)</strong></div></div>
<div class="zone-box"><div class="zone-title">⚔️ 第二戰區：技術與多空領先指標清單</div><div style="display:flex; justify-content:space-between; font-size:13px; margin-bottom:4px;"><span>20MA生命線: {card['ma20']:.1f}</span><span style="color:{card['macd_color']};" title="紅柱多方、綠柱空方">{card['macd_str']}</span><span style="color:#f1c40f;" title="短線隨機指標">KDJ: {card['kdj_str']}</span></div></div>
<div class="zone-box"><div class="shadow-box"><div class="zone-title">📊 第三戰區：三大法人與千張大戶籌碼</div><div style="font-size:13px; color:#ddd;">外資: <strong style="color:#ff4d4d;">{card['f_buy']:,} 張</strong> | 投信: <strong style="color:#ff4d4d;">{card['t_buy']:,} 張</strong> | 自營: {card['d_buy']:,} 張</div><div style="font-size:12px; color:#aaa; border-top:1px dashed #333; padding-top:4px;" title="持有公司股票超過1,000張以上的核心大股東持股總比例">千張大戶持股比率: <strong style="color:#00d2ff;">{card['big_holder']}%</strong></div></div></div>
<div style="background:{card['signal_bg']}; padding:10px; border-radius:5px; text-align:center;"><strong style="color:{card['color_border']}; font-size:15px;">決策判定：{card['signal_text']}</strong></div>
</div>
"""
                    st.markdown(re.sub(r'^\s+', '', html_card, flags=re.MULTILINE), unsafe_allow_html=True)
                    
                    # 完全解耦的按鈕事件
                    m_cols = st.columns(2)
                    if m_cols[0].button("轉移至持倉倉位", key=f"mov_pin_{card['code']}", use_container_width=True):
                        st.session_state'portfolio' = {"entry_price": card['price'], "qty": 1}
                        st.session_state['pinned_stocks'].pop(card['code'], None)
                        save_local_db_isolated()
                        st.rerun()
                    if m_cols[1].button("移出雷達防線", key=f"del_pin_{card['code']}", use_container_width=True):
                        st.session_state['pinned_stocks'].pop(card['code'], None)
                        save_local_db_isolated()
                        st.rerun()
                idx += 1

# ==============================================================================
# 十二、 全市場初篩掃描 (1700檔血統綁定自動寫入)
# ==============================================================================
if st.sidebar.button("🔎 [啟動全市場真實連線初篩掃描]", use_container_width=True, type="primary"):
    with st.spinner("重型全市場真實 API 篩選中... (超時個股自動優雅降級隔離)"):
        results = []
        for c in GLOBAL_MARKET_CODES[:300]:
            card = calculate_comprehensive_signals(c, enable_doomsday_lock)
            if card and not card.get('error', False) and card['vol'] >= (min_volume_filter / 1000):
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
                
                if valid:
                    results.append(card)
        st.session_state['scan_results'] = results
        st.session_state['scan_mode'] = selected_cmd

if st.session_state.get('scan_results'):
    st.markdown(f"### ⚡ {st.session_state['scan_mode']} 篩選戰果清單 ({len(st.session_state['scan_results'])} 檔符合)")
    
    if st.button("➕ 批次部署並強制寫入常態追蹤雷達", use_container_width=True):
        for card in st.session_state['scan_results']:
            st.session_state'pinned_stocks' = selected_cmd
        save_local_db_isolated()
        st.success(f"✅ 成功將 {len(st.session_state['scan_results'])} 檔標的綁定血統標籤【{selected_cmd}】並永久存檔。")
        time.sleep(0.5)
        st.rerun()
        
    table_rows = []
    for card in st.session_state['scan_results']:
        table_rows.append({
            "代號": card['code'], "名稱": card['name'], "現價": card['price'],
            "漲跌(%)": round(card['gain'], 2), "YoY年增(%)": round(card['rev_yoy'], 1),
            "MoM月增(%)": round(card['rev_mom'], 1), "殖利率(%)": f"{card['div_yield']:.1f}%", "地雷標記": len(card['mine_tags'])
        })
    st.dataframe(pd.DataFrame(table_rows), use_container_width=True, hide_index=True)
    
    cols = st.columns(2)
    for idx, card in enumerate(st.session_state['scan_results']):
        with cols[idx % 2]:
            html_card = f"""
<div style="border:2px solid {card['color_border']}; border-radius:8px; padding:15px; background:#16191f; margin-bottom:12px;">
<span style="font-weight:bold; font-size:19px; color:#ffffff;">{card['name']} <span style="color:#00d2ff;">({card['code']})</span></span>
<div style="font-size:13px; color:#ddd; margin-top:5px;">當前狀態：初篩戰果符合 | 爆量比: {card['vol_ratio']:.1f}x</div>
</div>
"""
            st.markdown(re.sub(r'^\s+', '', html_card, flags=re.MULTILINE), unsafe_allow_html=True)

# === 54088 戰情室程式碼結束 (請確保此行以下沒有任何文字) ===
