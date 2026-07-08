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
# 一、 系統安全防禦與法規合規宣告 (Regulatory & Safety Mandates)
# ==============================================================================
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
warnings.filterwarnings('ignore')

st.set_page_config(layout="wide", page_title="54088 戰情室 V133", initial_sidebar_state="expanded")

GOV_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*"
}

USER_DB_FILE = "54088_database.json" 
INST_HISTORY_FILE = "54088_inst_history_v30d.json"

# ==============================================================================
# 二、 記憶體快取隔離技術與全域初始化 (Memory Cache & Initialization)
# ==============================================================================
# 徹底修復 AttributeError：強制在最頂層初始化所有 session_state
for key in ['db_loaded', 'pinned_stocks', 'portfolio', 'inst_history', 'scan_results', 'scan_mode', 'active_key_index', 'ai_report']:
    if key not in st.session_state:
        if key in ['pinned_stocks', 'portfolio', 'inst_history']: st.session_state[key] = {}
        elif key in ['scan_results']: st.session_state[key] = []
        elif key == 'active_key_index': st.session_state[key] = 0
        else: st.session_state[key] = ""

def load_and_isolate_db():
    if not st.session_state.db_loaded:
        if os.path.exists(USER_DB_FILE):
            try:
                with open(USER_DB_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    st.session_state.pinned_stocks = data.get("pinned_stocks", {})
                    st.session_state.portfolio = data.get("portfolio", {})
            except Exception: pass
        if os.path.exists(INST_HISTORY_FILE):
            try:
                with open(INST_HISTORY_FILE, "r", encoding="utf-8") as f:
                    st.session_state.inst_history = json.load(f)
                    if len(st.session_state.inst_history) > 30:
                        sorted_dates = sorted(st.session_state.inst_history.keys(), reverse=True)
                        st.session_state.inst_history = {d: st.session_state.inst_history[d] for d in sorted_dates[:30]}
            except Exception: pass
        st.session_state.db_loaded = True

def save_local_db_isolated():
    payload = {"pinned_stocks": st.session_state.pinned_stocks, "portfolio": st.session_state.portfolio}
    try:
        with open(USER_DB_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=4)
        if st.session_state.inst_history:
            with open(INST_HISTORY_FILE, "w", encoding="utf-8") as f:
                json.dump(st.session_state.inst_history, f, ensure_ascii=False)
    except Exception: pass

load_and_isolate_db()

try:
    COMMANDER_PIN = st.secrets["radar_secrets"]["commander_pin"]
    raw_keys = st.secrets["radar_secrets"]["gemini_api_key"]
    GEMINI_API_KEYS = [k.strip() for k in raw_keys.split(",") if k.strip()]
    SECRET_FINMIND = st.secrets["radar_secrets"].get("finmind_token", "")
    FINMIND_TOKENS = [k.strip() for k in SECRET_FINMIND.split(",") if k.strip()]
    if not FINMIND_TOKENS: FINMIND_TOKENS = [""]
except KeyError:
    st.error("❌ 雲端保險箱 (Secrets) 未設定！")
    st.stop()

def safe_float(val):
    if pd.isna(val) or val is None or str(val).strip() == '': return 0.0
    try:
        s = str(val).upper().replace(',', '').replace('-', '').strip()
        s = re.sub(r'[^\d.]', '', s)
        return float(s) if s else 0.0
    except Exception: return 0.0

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
    return "綜合類股"

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
    for url in ["https://openapi.twse.com.tw/v1/opendata/t187ap05_L", "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap05_O"]:
        try:
            res = requests.get(url, headers=GOV_HEADERS, verify=False, timeout=10)
            if res.status_code == 200:
                for item in res.json():
                    c = str(item.get('公司代號', '')).strip()
                    if len(c) == 4: rev_db[c] = safe_float(item.get('當月營收較去年當月增減百分比', 0))
        except: pass
    return rev_db

@st.cache_data(ttl=86400, show_spinner=False)
def fetch_stock_names():
    names = {}
    for url in ["https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL", "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_peratio_analysis"]:
        try:
            res = requests.get(url, headers=GOV_HEADERS, verify=False, timeout=10)
            if res.status_code == 200:
                for item in res.json():
                    c = str(item.get('Code', item.get('SecuritiesCompanyCode', ''))).strip()
                    n = str(item.get('Name', item.get('CompanyName', ''))).strip()
                    if len(c) == 4 and c.isdigit() and n: names[c] = n
        except: pass
    fallbacks = {"2330":"台積電", "2303":"聯電", "2317":"鴻海", "2308":"台達電", "5871":"中租-KY", "6146":"耕興", "2015":"豐興"}
    for k, v in fallbacks.items():
        if k not in names: names[k] = v
    return names

@st.cache_data(ttl=86400, show_spinner=False)
def fetch_twse_dividends():
    divs = {}
    try:
        res = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/TWT48U", headers=GOV_HEADERS, verify=False, timeout=10)
        if res.status_code == 200:
            for item in res.json():
                c = str(item.get('股票代號', '')).strip()
                if len(c) == 4: divs[c] = {'date': str(item.get('除權息日期', '')).strip(), 'cash': safe_float(item.get('現金股利', 0))}
    except: pass
    return divs

@st.cache_data(ttl=300, show_spinner=False)
def get_real_stock_data_yfinance(symbol):
    """真實串接全球 YFinance 獲取 30 天歷史 K 線與基本面財報數據"""
    session = get_safe_session()
    for ext in [".TW", ".TWO"]:
        try:
            tk = yf.Ticker(symbol + ext, session=session)
            hist = tk.history(period="3mo").dropna(subset=['Close'])
            if not hist.empty and len(hist) > 10:
                info = tk.info
                return hist.tail(30), info
        except Exception: pass
    return None, {}

TW_STOCK_NAMES = fetch_stock_names()
TW_REVENUE_DB = fetch_tw_revenue()
DIVIDEND_DB = fetch_twse_dividends()
GLOBAL_MARKET_CODES = list(TW_STOCK_NAMES.keys())

# ==============================================================================
# 四、 視覺化雙色走勢與 K 線型態演算法核心 (Visual & K-Line Engine)
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
        color = "#888888" if i == 0 else ("#ff4d4d" if closes_list[i] > closes_list[i-1] else ("#00FF00" if closes_list[i] < closes_list[i-1] else "#aaaaaa"))
        html_sparkline += f"<span style='color:{color}; font-weight:bold;'>{bars[idx]}</span>"
    return html_sparkline

def detect_k_line_patterns_v133(df):
    patterns = []
    if len(df) < 5: return patterns
    c0, c1, c2 = float(df['Close'].iloc[-1]), float(df['Close'].iloc[-2]), float(df['Close'].iloc[-3])
    o0, o1, o2 = float(df['Open'].iloc[-1]), float(df['Open'].iloc[-2]), float(df['Open'].iloc[-3])
    body0 = abs(c0 - o0)
    
    if (c0 > o0) and body0 > (c0 * 0.035):
        if (c1 < o1) and c0 > o1 and o0 < c1: patterns.append({"text": "長紅吞噬", "class": "tag-red", "type": "多方", "desc": "☑️ 長紅吞噬：實體紅K吞沒昨日黑K，多方強勢奪回短線主控權。"})
        else: patterns.append({"text": "低檔長紅", "class": "tag-red", "type": "多方", "desc": "☑️ 低檔長紅：實體大紅棒點火爆發，主力大舉進場築底。"})
    if (c0 > o0) and (c1 > o1) and (c2 > o2) and (c0 > c1 > c2): patterns.append({"text": "紅三兵", "class": "tag-red", "type": "多方", "desc": "☑️ 紅三兵：連續三根每日收盤遞增的實體紅K，多頭動能結構紮實。"})
    if (c0 < o0) and body0 > (c0 * 0.035):
        if (c1 > o1) and c0 < o1 and o0 > c1: patterns.append({"text": "長黑吞噬", "class": "tag-green", "type": "空方", "desc": "❌ 長黑吞噬：高檔長黑吞沒昨日紅K實體，提防主力大舉出貨。"})
        else: patterns.append({"text": "高檔長黑", "class": "tag-green", "type": "空方", "desc": "❌ 高檔長黑：高檔爆量收實體大黑棒，上方潛在解套賣壓沈重。"})
    if (c0 < o0) and (c1 < o1) and (c2 < o2) and (c0 < c1 < c2): patterns.append({"text": "黑三兵", "class": "tag-green", "type": "空方", "desc": "❌ 黑三兵：連續三根收盤遞減之實體黑K，空方動能湧現趨勢轉弱。"})
    return patterns

# ==============================================================================
# 五、 核心運算晶片與「五大戰區」數據聚合 (Core Signal Processing)
# ==============================================================================
def calculate_comprehensive_signals(symbol, enable_doomsday=False):
    # 1. 抓取真實 YFinance 歷史與財報數據
    hist, info = get_real_stock_data_yfinance(symbol)
    if hist is None or hist.empty: return None
    
    curr_price = float(hist['Close'].iloc[-1])
    prev_price = float(hist['Close'].iloc[-2])
    gain = ((curr_price - prev_price) / prev_price) * 100
    
    vol_today = int(hist['Volume'].iloc[-1] / 1000)
    vol_yesterday = max(1, int(hist['Volume'].iloc[-2] / 1000))
    vol_change_pct = ((vol_today - vol_yesterday) / vol_yesterday) * 100
    vol_ratio = vol_today / max(1, hist['Volume'].tail(5).mean() / 1000)
    
    ma5 = float(hist['Close'].tail(5).mean())
    ma20 = float(hist['Close'].tail(20).mean())
    
    # 2. 抓取真實 FinMind 大腦記憶庫籌碼
    f_buy = t_buy = d_buy = margin_diff = 0
    sorted_dates = sorted(st.session_state.get('inst_history', {}).keys(), reverse=True)
    if sorted_dates and symbol in st.session_state['inst_history'][sorted_dates[0]]:
        mem = st.session_state['inst_history'][sorted_dates[0]][symbol]
        f_buy = mem.get('foreign', 0)
        t_buy = mem.get('trust', 0)
        d_buy = mem.get('dealer', 0)
        margin_diff = mem.get('margin', 0)
        
    rev_yoy = TW_REVENUE_DB.get(symbol, 0.0)
    
    div_info = DIVIDEND_DB.get(symbol)
    if div_info:
        div_display = f"除息日:{div_info['date']} | 現金:{div_info['cash']}元"
        div_yield = (div_info['cash'] / curr_price) * 100 if curr_price > 0 else 0.0
    else:
        div_display = "無近期除息資訊"; div_yield = 0.0
        
    # 3. 財報掃雷 (真實 Yahoo Finance 數據)
    debt_to_equity = info.get('debtToEquity', 0)
    op_cashflow = info.get('operatingCashflow', 0)
    net_income = info.get('netIncome', 0)
    consensus_target = info.get('targetMeanPrice', curr_price)
    potential_roi = round(((consensus_target - curr_price) / curr_price) * 100, 1) if curr_price > 0 else 0.0
    
    mine_tags = []
    if debt_to_equity > 75.0: mine_tags.append("高負債比警告")
    if net_income > 0 and op_cashflow < 0: mine_tags.append("盈餘品質異常-有獲利無現金")
    
    multi_bull_items = []
    multi_bear_items = []
    if curr_price > ma5: multi_bull_items.append("☑️ 股價站上5日線")
    else: multi_bear_items.append("❌ 股價跌破5日線")
    if curr_price > ma20: multi_bull_items.append("☑️ 股價站上月線(20MA)")
    else: multi_bear_items.append("❌ 股價跌破月線(20MA)")
    if f_buy > 0 and t_buy > 0: multi_bull_items.append("☑️ 土洋巨頭同步買盤")
    if margin_diff < 0: multi_bull_items.append("☑️ 融資散戶退場，籌碼沉澱")
    if rev_yoy > 20.0: multi_bull_items.append("☑️ 營收雙增盾牌 (YoY > 20%)")
    
    detected_patterns = detect_k_line_patterns_v133(hist)
    for p_pat in detected_patterns:
        if p_pat["type"] == "多方": multi_bull_items.append(p_pat["desc"])
        else: multi_bear_items.append(p_pat["desc"])
        
    total_checklist_count = len(multi_bull_items) + len(multi_bear_items)
    bull_score = int((len(multi_bull_items) / total_checklist_count) * 100) if total_checklist_count > 0 else 50
    
    if curr_price > ma5 and ma5 > ma20: trend_label = "<span class='tag-base tag-red'>[短強]</span><span class='tag-base tag-red'>[中強]</span>"
    else: trend_label = "<span class='tag-base tag-green'>[短弱]</span><span class='tag-base tag-blue'>[中橫盤]</span>"
    trade_attr_tag = "<span class='tag-base tag-purple'>[適合波段佈局]</span>" if curr_price > 100 else "<span class='tag-base tag-blue'>[適合短線價差]</span>"
    
    if enable_doomsday and rev_yoy <= 20.0: return None
        
    if curr_price < ma5 or "長黑吞噬" in [x["text"] for x in detected_patterns]: signal_text, color_border, signal_bg = "[🚨 撤退警告/高檔震盪]", "#00FF00", "#153a20"
    elif curr_price > ma5 and f_buy > 0: signal_text, color_border, signal_bg = "[🔥 偏多攻擊]", "#ff4d4d", "#3a1515"
    else: signal_text, color_border, signal_bg = "[⚠️ 區間拉回整理]", "#f1c40f", "#332b00"
        
    return {
        "code": symbol, "name": TW_STOCK_NAMES.get(symbol, symbol), "price": curr_price, "gain": gain,
        "vol": vol_today, "vol_change_pct": vol_change_pct, "vol_ratio": vol_ratio,
        "f_buy": f_buy, "t_buy": t_buy, "d_buy": d_buy, "margin_diff": margin_diff,
        "rev_yoy": rev_yoy, "div_display": div_display, "div_yield": div_yield,
        "consensus_target": consensus_target, "potential_roi": potential_roi,
        "mine_tags": mine_tags, "bull_score": bull_score, "trend_label": trend_label, "trade_attr_tag": trade_attr_tag,
        "multi_bull_items": multi_bull_items, "multi_bear_items": multi_bear_items,
        "signal_text": signal_text, "color_border": color_border, "signal_bg": signal_bg,
        "sparkline_html": generate_bi_color_sparkline(hist['Close'].tail(7).tolist()), "detected_patterns": detected_patterns,
        "sector": get_industry_label_wrapper(symbol)
    }

# ==============================================================================
# 六、 FinMind 真實多執行緒歷史回填引擎 (Real Multi-threaded Sync)
# ==============================================================================
def execute_heavy_data_sync(target_codes, target_date):
    """真實串接 FinMind API 進行多線程補齊"""
    progress_bar = st.progress(0)
    status_text = st.empty()
    if target_date not in st.session_state.inst_history:
        st.session_state.inst_history[target_date] = {}
        
    missing = [c for c in target_codes if c not in st.session_state.inst_history[target_date]]
    if not missing:
        st.success("✅ 記憶庫已 100% 飽和，無需重複抓取。")
        return
        
    status_text.info(f"📡 啟動 FinMind 重型引擎，真實回填 {len(missing)} 檔標的...")
    success_count = 0
    url = 'https://api.finmindtrade.com/api/v4/data'
    
    def fetch_finmind_worker(code):
        token = FINMIND_TOKENS[st.session_state.active_key_index]
        params = {'dataset': 'TaiwanStockInstitutionalInvestorsBuySell', 'data_id': code, 'start_date': target_date}
        if token: params['token'] = token
        try:
            res = requests.get(url, params=params, timeout=5)
            if res.status_code == 200 and res.json().get('msg') == 'success':
                df = pd.DataFrame(res.json().get('data', []))
                if not df.empty:
                    df['net'] = pd.to_numeric(df['buy'], errors='coerce').fillna(0) - pd.to_numeric(df['sell'], errors='coerce').fillna(0)
                    pivoted = df.pivot_table(index='date', columns='name', values='net', aggfunc='sum').sort_index(ascending=False)
                    f_val = pivoted['Foreign_Investor'].iloc[0]/1000 if 'Foreign_Investor' in pivoted.columns else 0
                    t_val = pivoted['Investment_Trust'].iloc[0]/1000 if 'Investment_Trust' in pivoted.columns else 0
                    d_val = pivoted['Dealer'].iloc[0]/1000 if 'Dealer' in pivoted.columns else 0
                    st.session_state.inst_history[target_date][code] = {'foreign': int(f_val), 'trust': int(t_val), 'dealer': int(d_val), 'margin': 0}
                    return True
        except Exception: pass
        return False

    # 為防止免費 API 被 Ban，限制 5 線程
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(fetch_finmind_worker, code): code for code in missing[:150]} # 一次最多補150檔防超時
        for idx, future in enumerate(concurrent.futures.as_completed(futures)):
            if future.result(): success_count += 1
            progress_bar.progress(min((idx + 1) / len(futures), 1.0))
            if idx > 0 and idx % 50 == 0: save_local_db_isolated()

    status_text.empty()
    progress_bar.empty()
    save_local_db_isolated()
    st.success(f"✅ 真實同步完畢！成功充填: {success_count} 檔。")
    time.sleep(1); st.rerun()

# ==============================================================================
# 七、 介面渲染與 CSS 樣式 (UI & CSS)
# ==============================================================================
st.markdown("""<style>
:root { color-scheme: dark !important; }
html, body, [class*="css"] { color-scheme: dark !important; background-color: #0b0c0f !important; color: #fff !important; font-family: Arial, sans-serif; }
div[data-testid="stSidebar"] { background-color: #12141a !important; border-right: 1px solid #333 !important; }
div[data-testid="stButton"] > button { background-color: #1e1e24 !important; border: 1px solid #444 !important; }
div[data-testid="stButton"] > button p { color: #00d2ff !important; font-weight: bold !important; font-size: 14px !important; }
.hud-box { background: linear-gradient(135deg, #1a1c23 0%, #0d1117 100%); border-radius: 10px; padding: 15px; border-left: 5px solid #ff4d4d; margin-bottom: 20px;}
.hud-title { color: #f1c40f; font-size: 14px; font-weight: bold; margin-bottom: 8px; border-bottom: 1px solid #333;}
.tag-base { display: inline-block; padding: 3px 6px; border-radius: 4px; font-size: 12px; font-weight: bold; margin: 0 4px 4px 0; }
.tag-red { background: #3a1515; color: #ff4d4d; border: 1px solid #e74c3c; }
.tag-green { background: #153a20; color: #00FF00; border: 1px solid #2ecc71; }
.tag-blue { background: #15203a; color: #00d2ff; border: 1px solid #3498db; }
.tag-purple { background: #2a153a; color: #d200ff; border: 1px solid #9b59b6; }
.zone-box { background: #11141c; border: 1px solid #2c3e50; border-radius: 6px; padding: 10px; margin-bottom: 8px; }
.zone-title { color: #00d2ff; font-weight: bold; font-size: 13px; margin-bottom: 5px; border-bottom: 1px dashed #333; }
</style>""", unsafe_allow_html=True)

# ----------------- 側邊欄控制台 -----------------
with st.sidebar:
    st.markdown("<h2 style='color:#f1c40f; text-align:center; margin-bottom:0;'>⚙️ 戰略控制台</h2>", unsafe_allow_html=True)
    st.markdown("<p style='text-align:center; color:#666; font-size:12px;'>真實滿血修復版 V133</p>", unsafe_allow_html=True)
    st.divider()
    
    db_days = max(1, len(st.session_state.get('inst_history', {})))
    st.markdown(f"#### 📊 資料庫完整度天數: {db_days} 天")
    
    target_date_sim = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
    if st.button("🚀 [執行真實 FinMind 遺失補齊]", use_container_width=True, type="primary"):
        execute_heavy_data_sync(GLOBAL_MARKET_CODES[:150], target_date_sim)
        
    st.divider()
    enable_doomsday_lock = st.checkbox("💀 開啟末日鎔斷防護鎖", value=False)
    min_yield_filter = st.slider("最低現金殖利率門檻 (%)", 0.0, 30.0, 4.5, 0.5)
    
    st.divider()
    commands_list = ["【指令一】 主升段突擊", "【指令二】 魚頭潛伏支撐", "【指令三】 價值投資循環", "【指令十一】 除權息尋寶", "【指令十二】 K線型態尋寶"]
    selected_cmd = st.radio("戰略指令動線：", commands_list, label_visibility="collapsed")
    selected_k_patterns = []
    if "指令十二" in selected_cmd:
        with st.container(border=True):
            if st.checkbox("🔥 長紅吞噬 / 低檔長紅"): selected_k_patterns.append("長紅吞噬")
            if st.checkbox("🔥 紅三兵強勢推升"): selected_k_patterns.append("紅三兵")
            if st.checkbox("💀 長黑吞噬頂部出貨"): selected_k_patterns.append("長黑吞噬")

# ----------------- 主畫面渲染 -----------------
st.title("🚀 54088 戰情室 V133 真實滿血版")
st.markdown(f"""<div class='hud-box'>
    <div class='hud-title'>📊 大將軍戰情智慧總覽中樞 (HUD)</div>
    <div style='background:#1a1c23; padding:8px; border-radius:5px; margin-bottom:5px; font-size:13px; color:#ddd;'><b>系統狀態：</b> 真實 API 串接完畢 | YFinance 歷史回溯引擎啟動中</div>
</div>""", unsafe_allow_html=True)

def render_comprehensive_5_zone_card_v133(card, prefix_id):
    gain_color = '#ff4d4d' if card['gain'] > 0 else ('#00FF00' if card['gain'] < 0 else '#aaaaaa')
    gain_bg = '#3a1515' if card['gain'] > 0 else ('#153a20' if card['gain'] < 0 else '#333333')
    vol_color = '#ff4d4d' if card['vol_change_pct'] > 0 else '#00FF00'
    vol_text = f"🔥 爆量 {card['vol_change_pct']:+.1f}%" if card['vol_change_pct'] > 0 else f"🧊 量縮 {card['vol_change_pct']:.1f}%"
    
    mine_html = "".join([f"<span class='tag-base tag-green' style='background:#2c3e50; color:#f1c40f; border:1px solid #f1c40f;'>🚨 財報地雷警示：{t}</span> " for t in card['mine_tags']])
    pattern_badges = "".join([f"<span class='tag-base {p['class']}'>{p['text']}</span> " for p in card['detected_patterns']])

    html_template = f"""
    <div style='border: 2px solid {card['color_border']}; border-radius: 8px; padding: 15px; background-color: #16191f; margin-bottom: 12px;'>
        <div style='display:flex; justify-content:space-between; align-items:center;'>
            <span style='font-weight:bold; font-size:19px; color:#fff;'>{card['name']} ({card['code']}) <span style='font-size:12px; color:#aaa; background:#2c3e50; padding:2px 6px; border-radius:4px;'>{card['sector']}</span></span>
            <span style='font-size:13px; color:#f1c40f;'>外資共識價: <b>{card['consensus_target']:.1f}</b> (潛在回報: <strong style='color:#ff4d4d;'>+{card['potential_roi']}%</strong>)</span>
        </div>
        <div style='font-size:32px; font-weight:bold; margin: 8px 0; display:flex; gap:15px; align-items:center;'>
            {card['price']:.2f} <span style='font-size:15px; color:{gain_color}; background-color:{gain_bg}; padding:3px 8px; border-radius:4px;'>{card['gain']:+.2f}%</span>
            <span style='font-size:14px; color:#ccc; margin-left:10px;'>近7日走勢： {card['sparkline_html']}</span>
        </div>
        <div style='display:flex; justify-content:space-between; font-size:13px; color:#aaa; margin-bottom:10px; background:#0e1117; padding:6px; border-radius:4px;'>
            <span>總量: <b>{card['vol']:,} K</b> (<span style='color:{vol_color}; font-weight:bold;'>{vol_text}</span>)</span>
            <span>爆量比: <strong style='color:#e67e22;'>{card['vol_ratio']:.1f}x</strong></span>
        </div>
        <div style='margin-bottom:8px;'>{pattern_badges}{mine_html}</div>
        
        <div class='zone-box'>
            <div class='zone-title'>❤️ 第一戰區：基本與財報面</div>
            <div style='font-size:12px; color:#ddd;'>營收年增(YoY): <strong style='color:#00d2ff;'>{card['rev_yoy']:.1f}%</strong> | 除權息: <strong style='color:#d200ff;'>{card['div_display']}</strong></div>
        </div>
        
        <div class='zone-box'>
            <div class='zone-title'>⚔️ 第二戰區：技術與型態面</div>
            <div style='font-size:12px; color:#bbb; line-height:1.5;'>{"<br>".join(card['multi_bull_items'][:2]) if card['multi_bull_items'] else "☑️ 盤整"}</div>
        </div>
        
        <div class='zone-box'>
            <div class='zone-title'>📊 第三戰區：籌碼主力動向</div>
            <div style='font-size:12px; color:#ddd;'>外資: <strong style='color:#ff4d4d;'>{card['f_buy']:,} 張</strong> | 投信: <strong style='color:#ff4d4d;'>{card['t_buy']:,} 張</strong></div>
        </div>
        
        <div style='background:{card['signal_bg']}; padding:8px; border-radius:5px; text-align:center; border: 1px solid {card['color_border']}40; margin-bottom:8px;'>
            <strong style='color:{card['color_border']}; font-size:14px;'>系統當前判定：{card['signal_text']}</strong>
        </div>
    </div>
    """
    st.markdown(html_template, unsafe_allow_html=True)
    with st.expander("⚙️ [管理面板] (單檔倉位剔除控制)"):
        if st.button("刪除此檔", key=f"del_{prefix_id}_{card['code']}", use_container_width=True):
            if card['code'] in st.session_state.pinned_stocks: del st.session_state.pinned_stocks[card['code']]
            save_local_db_isolated(); st.rerun()

port_cards, pin_cards = {}, {}
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

if st.sidebar.button("🔎 [啟動真實連線初篩掃描 (取前50檔示範)]", use_container_width=True, type="primary"):
    with st.spinner("真實 API 管線連線中... (速度取決於 YFinance 伺服器)"):
        results = []
        for c in GLOBAL_MARKET_CODES[:50]: # 實戰防護：示範取前 50 檔防超時
            card = calculate_comprehensive_signals(c, enable_doomsday_lock)
            if card:
                if "指令十一" in selected_cmd and card['div_yield'] < min_yield_filter: continue
                if "指令十二" in selected_cmd and selected_k_patterns:
                    if not any(p in [x['text'] for x in card['detected_patterns']] for p in selected_k_patterns): continue
                results.append(card)
        st.session_state.scan_results = results
        st.session_state.scan_mode = selected_cmd

if st.session_state.get('scan_results'):
    st.markdown(f"### ⚡ {st.session_state.scan_mode} 掃描戰果")
    cols = st.columns(2)
    for idx, card in enumerate(st.session_state.scan_results):
        with cols[idx % 2]:
            st.checkbox(f"追蹤 {card['code']}", key=f"chk_scan_{card['code']}")
            render_comprehensive_5_zone_card_v133(card, prefix_id=f"scan_res_{idx}")
