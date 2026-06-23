import streamlit as st
import yfinance as yf
import pandas as pd
from datetime import datetime
import re
import math
import time

st.set_page_config(layout="wide", page_title="54088")

# ==========================================
# 🛡️ 霸王級 CSS 與視覺化血條 
# ==========================================
st.markdown('''<style>
.stApp { background-color: #0b0c0f !important; color: #fff !important; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
div[data-testid="stButton"] > button { background-color: #1e1e24 !important; border: 1px solid #444 !important; transition: all 0.2s ease-in-out; }
div[data-testid="stButton"] > button p { color: #ffffff !important; font-weight: bold !important; font-size: 15px !important; }
div[data-testid="stButton"] > button:hover { border-color: #f1c40f !important; transform: translateY(-2px); box-shadow: 0 4px 10px rgba(241,196,15,0.2); }

[data-testid="stExpander"] details summary { background-color: #16191f !important; border: 1px solid #3498db !important; border-radius: 8px !important; margin-bottom: 5px !important; }
[data-testid="stExpander"] details summary:hover { background-color: #1e222b !important; border-color: #f39c12 !important; }
[data-testid="stExpander"] details summary p { color: #f1c40f !important; font-weight: 900 !important; font-size: 16px !important; }
[data-testid="stExpander"] details summary svg { fill: #f1c40f !important; }

.sync-btn div[data-testid="stButton"] > button { background-color: #f39c12 !important; border: 2px solid #e67e22 !important; }
.sync-btn div[data-testid="stButton"] > button p { color: #000000 !important; font-weight: 900 !important; }
.pin-btn div[data-testid="stButton"] > button { background-color: #2c3e50 !important; border: 1px solid #34495e !important; }
.unpin-btn div[data-testid="stButton"] > button { background-color: #7f8c8d !important; }
.buy-btn div[data-testid="stButton"] > button { background-color: #c0392b !important; border: 1px solid #e74c3c !important; }
.sell-btn div[data-testid="stButton"] > button { background-color: #27ae60 !important; border: 1px solid #2ecc71 !important; }
.lock-btn div[data-testid="stButton"] > button { background-color: #333333 !important; }
.lock-btn div[data-testid="stButton"] > button p { color: #aaaaaa !important; }

.override-btn div[data-testid="stButton"] > button { background-color: #8e44ad !important; border: 1px solid #9b59b6 !important; width: 100%; margin-top: 10px;}

.scan-btn-golden div[data-testid="stButton"] > button { background-color: #153a20 !important; border: 2px solid #00FF00 !important; margin-top:10px; margin-bottom: 10px; height: 75px;}
.scan-btn-golden div[data-testid="stButton"] > button p { color: #00FF00 !important; font-size: 16px !important; white-space: pre-wrap;}
.scan-btn-stealth div[data-testid="stButton"] > button { background-color: #0b2239 !important; border: 2px solid #00d2ff !important; margin-top:10px; margin-bottom: 10px; height: 75px;}
.scan-btn-stealth div[data-testid="stButton"] > button p { color: #00d2ff !important; font-size: 16px !important; white-space: pre-wrap;}
.scan-btn-yield div[data-testid="stButton"] > button { background-color: #2c153a !important; border: 2px solid #9b59b6 !important; margin-top:10px; margin-bottom: 10px; height: 75px;}
.scan-btn-yield div[data-testid="stButton"] > button p { color: #e056fd !important; font-size: 16px !important; white-space: pre-wrap;}

.hud-box { background: linear-gradient(135deg, #1a1c23 0%, #0d1117 100%); border-radius: 10px; padding: 15px; border-left: 5px solid #f1c40f; box-shadow: 0 4px 15px rgba(0,0,0,0.5); margin-bottom: 20px;}
.hud-title { color: #f1c40f; font-size: 14px; font-weight: bold; margin-bottom: 10px; border-bottom: 1px solid #333; padding-bottom: 5px;}
.hud-metric { display: flex; justify-content: space-between; align-items: center; margin-bottom: 5px;}

.health-bar-bg { width: 100%; background-color: #333; border-radius: 5px; height: 8px; margin-top: 5px; overflow: hidden;}
.health-bar-fill-green { height: 100%; background-color: #2ecc71; transition: width 0.5s ease;}
.health-bar-fill-red { height: 100%; background-color: #e74c3c; transition: width 0.5s ease;}

.info-badge { background: #2b2b36; padding: 4px 8px; border-radius: 4px; font-size: 13px; color: #ccc; margin-right: 5px; border: 1px solid #444; display: inline-block; margin-bottom: 5px; }
.special-badge { background: #2c153a; padding: 4px 8px; border-radius: 4px; font-size: 13px; color: #e056fd; margin-right: 5px; border: 1px solid #9b59b6; display: inline-block; margin-bottom: 5px; font-weight: bold; }
.my-tooltip { position: relative; display: inline-block; cursor: help; }
.my-tooltip .my-tooltiptext { visibility: hidden; width: max-content; max-width: 250px; background-color: #ffcc00; color: #111; text-align: center; border-radius: 6px; padding: 8px 12px; position: absolute; z-index: 99999; bottom: 125%; left: 50%; transform: translateX(-50%); opacity: 0; transition: opacity 0.2s; font-size: 13px; font-weight: bold; line-height: 1.4; box-shadow: 0px 4px 15px rgba(0,0,0,0.6); pointer-events: none; white-space: normal; }
.my-tooltip .my-tooltiptext::after { content: ""; position: absolute; top: 100%; left: 50%; margin-left: -6px; border-width: 6px; border-style: solid; border-color: #ffcc00 transparent transparent transparent; }
.my-tooltip:hover .my-tooltiptext { visibility: visible; opacity: 1; }
</style>''', unsafe_allow_html=True)

# ==========================================
# 🛡️ 記憶體與狀態復原引擎
# ==========================================
COMMANDER_PIN = "0826"
MAX_CAPACITY = 40  # 💥 擴充：由 20 升級至 40 檔滿載容量

params = st.query_params

def cb_login():
    if st.session_state.pwd_input == COMMANDER_PIN:
        st.session_state.authenticated = True
        st.query_params["auth"] = "54088"
    else: st.session_state.login_error = True

def sync_state_to_url():
    pin_list = [f"{k}@{v['raw_data']}@{v['cat']}" for k, v in st.session_state.pinned_stocks.items()]
    if pin_list: st.query_params["p_pin"] = ",".join(pin_list)
    elif "p_pin" in st.query_params: del st.query_params["p_pin"]
        
    port_list = [f"{k}@{round(v['entry_price'], 2)}@{round(v['qty'], 3)}@{v['raw_data']}@{v['cat']}" for k, v in st.session_state.portfolio.items()]
    if port_list: st.query_params["p_port"] = ",".join(port_list)
    elif "p_port" in st.query_params: del st.query_params["p_port"]

def cb_pin_stock(code, raw_data, cat):
    if len(st.session_state.pinned_stocks) >= MAX_CAPACITY: return
    st.session_state.pinned_stocks[code] = {'raw_data': raw_data, 'cat': cat}
    sync_state_to_url()

def cb_unpin_stock(code):
    if code in st.session_state.pinned_stocks:
        del st.session_state.pinned_stocks[code]
        sync_state_to_url()

def cb_buy_stock(code, raw_data, cat, ui_key_prefix):
    if len(st.session_state.portfolio) >= MAX_CAPACITY: return
    try:
        cost = float(st.session_state.get(f"c_{ui_key_prefix}_{code}", 0.0))
        qty = float(st.session_state.get(f"q_{ui_key_prefix}_{code}", 1.0))
    except: cost, qty = 0.0, 1.0
    st.session_state.portfolio[code] = {
        "entry_price": round(cost, 2), "qty": round(qty, 3), 
        "raw_data": raw_data, "cat": cat
    }
    if code in st.session_state.pinned_stocks: del st.session_state.pinned_stocks[code]
    sync_state_to_url()

def cb_sell_stock(code):
    if code in st.session_state.portfolio:
        del st.session_state.portfolio[code]
        sync_state_to_url()

def cb_logout():
    st.session_state.authenticated = False
    if "auth" in st.query_params: del st.query_params["auth"]

def cb_override_price(code, ui_key_prefix):
    try:
        manual_p = float(st.session_state.get(f"override_input_{ui_key_prefix}_{code}", 0.0))
        if manual_p > 0: st.session_state.manual_prices[code] = manual_p
    except: pass

def cb_clear_override(code):
    if code in st.session_state.manual_prices: del st.session_state.manual_prices[code]

if 'authenticated' not in st.session_state: st.session_state.authenticated = (params.get("auth") == "54088")

if not st.session_state.authenticated:
    st.markdown("<h1 style='text-align: center; color: #444; margin-top: 20vh; font-family: monospace; letter-spacing: 5px; font-size: 2rem;'>54088</h1>", unsafe_allow_html=True)
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        st.text_input(" ", type="password", key="pwd_input", placeholder="PIN")
        st.button("Enter", use_container_width=True, on_click=cb_login)
        if st.session_state.get("login_error"):
            st.error("Error")
            st.session_state.login_error = False
    st.stop()

if 'pinned_stocks' not in st.session_state: st.session_state.pinned_stocks = {}
if 'portfolio' not in st.session_state: st.session_state.portfolio = {}
if 'intel_mission' not in st.session_state: st.session_state.intel_mission = [] 
if 'scan_results' not in st.session_state: st.session_state.scan_results = []
if 'scan_mode' not in st.session_state: st.session_state.scan_mode = ""
if 'manual_prices' not in st.session_state: st.session_state.manual_prices = {} 

if 'url_loaded' not in st.session_state:
    if "p_pin" in params:
        for item in params.get("p_pin", "").split(","):
            if item:
                try:
                    parts = item.split("@")
                    if len(parts) >= 3: st.session_state.pinned_stocks[parts[0]] = {'raw_data': parts[1], 'cat': parts[2]}
                except: pass
    if "p_port" in params:
        for item in params.get("p_port", "").split(","):
            if item:
                try:
                    parts = item.split("@")
                    if len(parts) >= 5: st.session_state.portfolio[parts[0]] = {"entry_price": float(parts[1]), "qty": float(parts[2]), "raw_data": parts[3], "cat": parts[4]}
                except: pass
    st.session_state.url_loaded = True

# ==========================================
# 📡 系統參數庫
# ==========================================
TW_STOCKS = {
    "2330":"台積電", "2317":"鴻海", "2454":"聯發科", "2382":"廣達", "2303":"聯電",
    "3231":"緯創", "6669":"緯穎", "2356":"英業達", "2376":"技嘉", "3017":"奇鋐", "3324":"雙鴻", "2421":"建準",
    "3661":"世芯-KY", "3443":"創意", "3035":"智原", "6643":"M31", "3529":"力旺", "6533":"晶心科",
    "5347":"世界", "3707":"漢磊", "2481":"強茂", "8261":"富鼎", "3317":"尼克森", "5425":"台半", "8255":"朋程",
    "3711":"日月光投控", "3131":"弘塑", "3583":"辛耘", "6187":"萬潤", "1560":"中砂", "5443":"均豪",
    "3008":"大立光", "3034":"聯詠", "2379":"瑞昱", "3481":"群創", "2409":"友達", "2308":"台達電", "2345":"智邦", 
    "3189":"景碩", "2313":"華通", "2439":"美律", "6153":"嘉聯益", "2408":"南亞科", "2344":"華邦電", "2337":"旺宏",
    "1519":"華城", "1513":"中興電", "1514":"亞力", "1504":"東元", 
    "2603":"長榮", "2609":"陽明", "2615":"萬海", "2618":"長榮航", "2610":"華航",
    "2881":"富邦金", "2882":"國泰金", "2891":"中信金", "2886":"兆豐金",
    "2002":"中鋼", "1605":"華新", "1101":"台泥", "2542":"興富發", "3293":"鈊象",
    "0050":"元大台灣50", "0056":"元大高股息", "00878":"國泰永續高股息", "00919":"群益台灣精選高息", "00929":"復華台灣科技優息"
}

YIELD_POOL = ["2881", "2882", "2891", "2886", "0050", "0056", "00878", "00919", "00929"]
CYCLICAL_POOL = ["1519", "1513", "1514", "1504", "2603", "2609", "2615", "2618", "2610", "2002", "1605", "1101", "2542", "2408", "3481", "2409", "2344", "2337"]

CHIP_MAP = {"1": "🐳 巨鯨進駐(籌碼面)", "2": "🩸 外資提款(籌碼面)", "0": "⚖️ 籌碼平穩(籌碼面)", "?": "❓ 籌碼待查(籌碼面)"}
VAL_MAP = {"1": "🟢 便宜(長線基本面)", "2": "🟡 合理(長線基本面)", "3": "🔴 昂貴(長線基本面)", "0": "⚪ 未定(長線基本面)", "?": "❓ 待定(長線基本面)"}

def safe_int(val, default=0):
    try: return int(val) if val else default
    except: return default
def safe_float(val, default=None):
    try: return float(val) if val else default
    except: return default
def safe_parse_float(val, default=0.0):
    if isinstance(val, (int, float)): return float(val)
    try:
        nums = re.findall(r"[-+]?\d*\.\d+|\d+", str(val))
        return float(nums[0]) if nums else default
    except: return default

def get_stock_name(symbol):
    if symbol in TW_STOCKS: return TW_STOCKS[symbol]
    return f"個股 {symbol}"

@st.cache_data(ttl=300)
def get_market_weather():
    try:
        taiex = yf.Ticker("^TWII").history(period="1mo")
        if taiex.empty: return "未知", "#888"
        current = taiex['Close'].iloc[-1]
        ma20 = taiex['Close'].rolling(window=20).mean().iloc[-1]
        gain = ((current - taiex['Close'].iloc[-2]) / taiex['Close'].iloc[-2]) * 100
        if gain <= -2.0: return f"🌩️ 系統性崩跌風險 (加權 {current:.0f})", "#e74c3c"
        elif current > ma20: return f"☀️ 多頭順風環境 (加權 {current:.0f})", "#2ecc71"
        else: return f"☁️ 空頭震盪環境 (加權 {current:.0f} 破月線)", "#f1c40f"
    except: return "📡 大盤資料獲取中...", "#888"

# ==========================================
# 🧠 核心量化演算法 
# ==========================================
def calculate_tactical_signals(symbol_data, category_type="main"):
    try:
        parts = symbol_data.split(":")
        if not parts[0].strip(): return None
        symbol = parts[0].strip()
        stock_name = get_stock_name(symbol) 
        
        shd_str = parts[1].strip() if len(parts) > 1 else "?"
        override_shd_raw = shd_str if shd_str == "?" else safe_int(shd_str, 4)
        cost_str = parts[2].strip() if len(parts) > 2 else ""
        override_cost = None if cost_str == "?" else safe_float(cost_str, None)
        if override_cost and override_cost <= 0: override_cost = None
        chip_code = parts[3].strip() if len(parts) > 3 else "?"
        val_code = parts[4].strip() if len(parts) > 4 else "?"
        
        hist = pd.DataFrame()
        ticker = None
        try:
            temp_ticker = yf.Ticker(f"{symbol}.TW")
            temp_hist = temp_ticker.history(period="1y")
            if not temp_hist.empty and len(temp_hist) > 15:
                hist = temp_hist
                ticker = temp_ticker
        except: pass

        if hist.empty:
            try:
                temp_ticker = yf.Ticker(f"{symbol}.TWO")
                temp_hist = temp_ticker.history(period="1y")
                if not temp_hist.empty and len(temp_hist) > 15:
                    hist = temp_hist
                    ticker = temp_ticker
            except: pass

        if hist.empty or ticker is None: return None
        hist = hist.dropna(subset=['Close', 'Open', 'High', 'Low', 'Volume'])

        manual_override = st.session_state.manual_prices.get(symbol)
        is_overridden = False

        if manual_override and manual_override > 0:
            current_price = float(manual_override)
            is_overridden = True
        else:
            try:
                today_tick = ticker.history(period="1d", interval="1m")
                if not today_tick.empty: current_price = float(today_tick['Close'].iloc[-1])
                else: current_price = float(hist['Close'].iloc[-1])
            except: current_price = float(hist['Close'].iloc[-1])
            
        try:
            val_prev = ticker.fast_info.previous_close
            if math.isnan(val_prev) or val_prev <= 0: prev_price = max(float(hist['Close'].iloc[-2]), 0.001)
            else: prev_price = float(val_prev)
        except: prev_price = max(float(hist['Close'].iloc[-2]), 0.001)

        open_p = float(hist['Open'].iloc[-1])
        high_p = float(hist['High'].iloc[-1])
        low_p = float(hist['Low'].iloc[-1])
        
        raw_gain = ((current_price - prev_price) / prev_price) * 100
        gain = raw_gain if -50.0 <= raw_gain <= 50.0 else 0.0 

        vol = int(hist['Volume'].iloc[-1] / 1000)
        vol_5d = hist['Volume'].iloc[-6:-1].mean() / 1000 if len(hist) >= 6 else vol
        vol_5d = max(vol_5d, 0.01) 
        vol_ratio = vol / vol_5d 
        
        ma5 = hist['Close'].rolling(window=min(5, len(hist))).mean().iloc[-1]
        ma10 = hist['Close'].rolling(window=min(10, len(hist))).mean().iloc[-1]
        ma20 = hist['Close'].rolling(window=min(20, len(hist))).mean().iloc[-1]
        ma60 = hist['Close'].rolling(window=min(60, len(hist))).mean().iloc[-1]
        ma120 = hist['Close'].rolling(window=min(120, len(hist))).mean().iloc[-1]
        ma240 = hist['Close'].rolling(window=min(240, len(hist))).mean().iloc[-1]
        
        macd_line = hist['Close'].ewm(span=12, adjust=False).mean() - hist['Close'].ewm(span=26, adjust=False).mean()
        signal_line = macd_line.ewm(span=9, adjust=False).mean()
        macd_hist = macd_line - signal_line
        is_macd_red = (len(macd_hist) > 1) and (macd_hist.iloc[-1] > 0) and (macd_hist.iloc[-2] <= 0)
        
        low_min = hist['Low'].rolling(window=min(9, len(hist))).min()
        high_max = hist['High'].rolling(window=min(9, len(hist))).max()
        rsv = (hist['Close'] - low_min) / (high_max - low_min + 1e-9) * 100
        hist['K'] = rsv.fillna(50).ewm(com=2, adjust=False).mean()
        hist['D'] = hist['K'].fillna(50).ewm(com=2, adjust=False).mean()
        k, d = hist['K'].iloc[-1], hist['D'].iloc[-1]
        
        kdj_golden_cross = (k < 40) and (hist['K'].iloc[-2] < hist['D'].iloc[-2]) and (k > d) if len(hist) > 1 else False
        kdj_signal = "📈 低檔金叉(短線技術面)" if kdj_golden_cross else ("📉 高檔死叉(短線技術面)" if (k>70 and k<d) else "〰️ KDJ 震盪(短線技術面)")

        if chip_code == "1": chip_desc = "【資金籌碼面】大戶與法人近期籌碼高度集中，有主力資金點火進場跡象"
        elif chip_code == "2": chip_desc = "【資金籌碼面】外資或主力近期連續賣超提款，籌碼面呈現渙散狀態"
        elif chip_code == "0": chip_desc = "【資金籌碼面】近期籌碼無明顯集中或發散，多空資金動向平穩中立"
        else: chip_desc = "【資金籌碼面】目前無籌碼數據，請向 CEO 查詢最新情報"

        if val_code == "1": val_desc = "【長線基本面】股價處於歷史估值低位，長線具備極高的安全邊際與防禦力"
        elif val_code == "2": val_desc = "【長線基本面】股價處於合理估值區間，溢價不高，長線投資風險中立"
        elif val_code == "3": val_desc = "【長線基本面】估值已滿水或極度昂貴，長線潛在回撤風險巨大，嚴禁追價"
        else: val_desc = "【長線基本面】基於本益比/淨值比之長線估值水準待確認"

        if kdj_golden_cross: kdj_desc = "【短線技術面】K值從低檔向上突破D值，短線動能轉強，為潛在波段起漲訊號"
        elif (k > 70 and k < d): kdj_desc = "【短線技術面】K值在高檔向下死叉D值，短線多頭動能轉弱，需留意拉回風險"
        else: kdj_desc = "【短線技術面】目前指標處於常態盤整區間，無明顯超買超賣或強勢表態方向"

        is_breakout = (gain > 2.0) and (vol > vol_5d * 1.5) and (current_price > ma20) 
        buy_cond_count = sum([kdj_golden_cross, is_macd_red, is_breakout])
        
        buy_status, buy_color, buy_bg = "⚪ 醞釀中 (無明顯起漲)", "#aaaaaa", "#1a1a24"
        if buy_cond_count == 3: buy_status, buy_color, buy_bg = "🔥 三火全亮，強勢起漲！", "#ff4d4d", "#3a1515"
        elif buy_cond_count == 2: buy_status, buy_color, buy_bg = "🚀 雙引擎發動，準備表態", "#f1c40f", "#3a3015"
        elif buy_cond_count == 1: buy_status, buy_color, buy_bg = "✨ 底部浮現單一火苗", "#3498db", "#152a3a"

        buy_html = f"<div class='my-tooltip' style='background:{buy_bg}; padding:10px 15px; border-radius:8px; margin-bottom:12px; border-left: 5px solid {buy_color}; display:block; width:100%;'><div style='font-size:12px; color:#ddd; margin-bottom:6px;'>🚀 起漲(買進)雷達：<strong style='color:{buy_color}; font-size:14px;'>{buy_status}</strong></div><div style='font-size:12px; color:#eee; display:flex; justify-content:space-between;'><span>{'🔴' if kdj_golden_cross else '⚪'} KDJ金叉</span><span>{'🔴' if is_macd_red else '⚪'} MACD翻紅</span><span>{'🔴' if is_breakout else '⚪'} 帶量上攻</span></div><span class='my-tooltiptext'>短線起漲動能判定。三火全亮代表強勢表態。</span></div>"

        is_huge_vol = vol > (vol_5d * 2.0)               
        is_black_k = current_price < open_p and gain < 0 
        is_break_ma5 = current_price < ma5               
        
        sell_cond_count = sum([is_huge_vol, is_black_k, is_break_ma5])
        spotter_status, spotter_color, spotter_bg = "🟢 陣地安全，續抱", "#2ecc71", "#153a20"
        if sell_cond_count == 3: spotter_status, spotter_color, spotter_bg = "🔴 三要件確立，立即撤退！", "#e74c3c", "#3a1515"
        elif sell_cond_count == 2: spotter_status, spotter_color, spotter_bg = "🟡 多重警訊，提高警戒", "#f1c40f", "#3a3015"
        elif sell_cond_count == 1: spotter_status, spotter_color, spotter_bg = "🟡 注意單一異常訊號", "#f39c12", "#3a2515"

        spotter_html = f"<div class='my-tooltip' style='background:{spotter_bg}; padding:10px 15px; border-radius:8px; margin-bottom:12px; border-left: 5px solid {spotter_color}; display:block; width:100%;'><div style='font-size:12px; color:#ddd; margin-bottom:6px;'>🚨 撤退(賣出)雷達：<strong style='color:{spotter_color}; font-size:14px;'>{spotter_status}</strong></div><div style='font-size:12px; color:#eee; display:flex; justify-content:space-between;'><span>{'🔴' if is_huge_vol else '⚪'} 爆量</span><span>{'🔴' if is_black_k else '⚪'} 實體黑K</span><span>{'🔴' if is_break_ma5 else '⚪'} 破5MA</span></div><span class='my-tooltiptext'>短線波段撤退判定。三要件確立需立即拔檔。</span></div>"

        jail_html = ""
        if len(hist) >= 7:
            close_6d_ago = max(float(hist['Close'].iloc[-6]), 0.001)
            return_6d = ((current_price - close_6d_ago) / close_6d_ago) * 100
            prev_close = float(hist['Close'].iloc[-2])
            close_7d_ago = max(float(hist['Close'].iloc[-7]), 0.001)
            prev_return_6d = ((prev_close - close_7d_ago) / close_7d_ago) * 100
            
            jail_color, jail_bg, jail_status = "#2ecc71", "#153a20", f"安全 (累計漲幅 {return_6d:.1f}%)"
            if return_6d >= 25.0 and prev_return_6d >= 25.0: jail_color, jail_bg, jail_status = "#9b59b6", "#2c153a", f"🛑 高危險處置區！"
            elif return_6d >= 25.0: jail_color, jail_bg, jail_status = "#e74c3c", "#3a1515", f"🔥 觸發注意股紅線！"
            elif return_6d >= 20.0: jail_color, jail_bg, jail_status = "#f39c12", "#3a3015", f"⚠️ 漲幅過熱逼近紅線"
            jail_html = f"<div class='my-tooltip' style='background:{jail_bg}; padding:10px 15px; border-radius:8px; margin-bottom:12px; border-left: 5px solid {jail_color}; display:block; width:100%;'><div style='font-size:12px; color:#ddd; margin-bottom:4px;'>⚖️ 證交所警示：<strong style='color:{jail_color}; font-size:13px;'>{jail_status}</strong></div><span class='my-tooltiptext'>追蹤短線漲幅，避免觸發證交所處置股條件。</span></div>"

        downgrade_alert = ""
        if override_cost:
            main_cost = override_cost
            cost_label = "自訂防線"
        else:
            if current_price >= ma60 * 0.96: 
                main_cost, cost_label = ma60, "MA60季線防禦"
            elif current_price >= ma120 * 0.96: 
                main_cost, cost_label = ma120, "MA120半年線退守"
                downgrade_alert = "⚠️ 系統自動降級：破季線，啟動半年線防禦"
            else: 
                main_cost, cost_label = ma240, "MA240年線大底"
                downgrade_alert = "🚨 系統極限退守：破半年線，鎖定年線大底"

        main_cost = round(main_cost, 1)
        buy_low, buy_high = round(main_cost * 0.97, 1), round(main_cost * 1.03, 1)
        diff_from_cost = ((current_price - max(main_cost, 0.001)) / max(main_cost, 0.001)) * 100

        if val_code == "3": exit_s, exit_p, exit_c, exit_bg = "🔴 價值滿水了結", f"{current_price:.1f}", "#e74c3c", "#3a1515"
        elif diff_from_cost >= 15.0: exit_s, exit_p, exit_c, exit_bg = "🛡️ 階梯移動停利", f"{max(ma10, main_cost * 1.05):.1f}", "#e67e22", "#3a2515"
        else: exit_s, exit_p, exit_c, exit_bg = "🚪 破線底線撤退", f"{main_cost * 0.95:.1f}", "#e74c3c", "#2c153a"

        ACTION_WAIT = "⏳ 【禁止買進：等待】"
        ACTION_NO   = "❌ 【嚴禁買進：危險】"
        ACTION_YES  = "✅ 【允許買進：狙擊】"
        ACTION_HOLD = "🛡️ 【區間觀察：佈陣】"

        is_in_buy_zone = (buy_low <= current_price <= buy_high)
        if vol_5d < 0.5: 
            signal_text, color_border, signal_bg = f"{ACTION_NO} 流動性枯竭 (勿碰)", "#8e44ad", "#2c153a"
        elif is_in_buy_zone:
            if sell_cond_count >= 2: 
                signal_text, color_border, signal_bg = f"{ACTION_WAIT} 抵達支撐，但短線偏弱 (等待止跌)", "#f39c12", "#3a3015"
            elif val_code == "3": 
                signal_text, color_border, signal_bg = f"{ACTION_NO} 抵達支撐，但基本面估值滿水 (嚴控)", "#e67e22", "#3a2515"
            elif buy_cond_count >= 2: 
                signal_text, color_border, signal_bg = f"{ACTION_YES} 完美打擊區！(支撐＋技術面強勢起漲)", "#00FF00", "#153a20"
            elif buy_cond_count == 1: 
                signal_text, color_border, signal_bg = f"{ACTION_YES} 進入最佳入場區 (準備建倉)", "#2ecc71", "#153a20"
            else: 
                signal_text, color_border, signal_bg = f"{ACTION_WAIT} 進入最佳入場區 (等待表態)", "#27ae60", "#102a15"
        elif diff_from_cost < -5.0: 
            signal_text, color_border, signal_bg = f"{ACTION_NO} 破線尋找下檔支撐中 (勿接刀)", "#e74c3c", "#3a1515"
        elif diff_from_cost > 20.0:
            if val_code == "1": signal_text, color_border, signal_bg = f"{ACTION_NO} 長線基本面便宜，但短線技術面嚴重過熱 (乖離極大勿接刀)", "#ff0000", "#3a1010"
            elif val_code == "2": signal_text, color_border, signal_bg = f"{ACTION_NO} 長線基本面合理，但短線技術面嚴重過熱 (乖離極大等拉回)", "#ff0000", "#3a1010"
            else: signal_text, color_border, signal_bg = f"{ACTION_NO} 短線技術面嚴重過熱 (乖離極大，風險極高勿追)", "#ff0000", "#3a1010"
        elif diff_from_cost > 10.0:
            if val_code == "3": 
                signal_text, color_border, signal_bg = f"{ACTION_NO} 長線基本面滿水 (極度昂貴，技術面嚴禁追價)", "#e74c3c", "#3a1515"
            elif sell_cond_count >= 2: 
                signal_text, color_border, signal_bg = f"{ACTION_WAIT} 高檔面臨賣壓且跌勢確立 (嚴格等待拉回)", "#e67e22", "#3a2515"
            elif buy_cond_count >= 2: 
                if val_code == "1" or val_code == "2":
                    signal_text, color_border, signal_bg = f"{ACTION_YES} 長線保護短線，右側強勢發動中 (順勢追擊)", "#e67e22", "#3a2515"
                else:
                    signal_text, color_border, signal_bg = f"{ACTION_YES} 技術面右側強勢發動中 (短線順勢操作)", "#e67e22", "#3a2515"
            else: 
                signal_text, color_border, signal_bg = f"{ACTION_WAIT} 短線技術面乖離過大且無強勢表態 (等拉回再佈局)", "#e67e22", "#3a2515"
        else: 
            signal_text, color_border, signal_bg = f"{ACTION_HOLD} 區間震盪 (等待落點或表態)", "#ccc", "#2b2b36"

        buy_zone = f"{buy_low} - {buy_high}"
        shd_display = "❓ 待查" if override_shd_raw == "?" else f"{override_shd_raw}分"
        
        extra_badge = ""
        if symbol in YIELD_POOL: extra_badge = "💰 高殖利防禦"
        elif symbol in CYCLICAL_POOL: extra_badge = "🔄 季節循環"

        return {"name": stock_name, "code": symbol, "price": current_price, "gain": gain, "cost": main_cost, "cost_label": cost_label, "buy_zone": buy_zone, "shd": shd_display, "chip_code": chip_code, "chip": CHIP_MAP.get(chip_code, "⚖️"), "val_code": val_code, "val": VAL_MAP.get(val_code, "⚪"), "kdj": kdj_signal, "chip_desc": chip_desc, "val_desc": val_desc, "kdj_desc": kdj_desc, "downgrade_alert": downgrade_alert, "signal": signal_text, "color": color_border, "signal_bg": signal_bg, "extra_badge": extra_badge, "exit_s": exit_s, "exit_price": exit_p, "exit_color": exit_c, "exit_bg": exit_bg, "vol": vol, "open": open_p, "high": high_p, "low": low_p, "raw_data": symbol_data, "cat": category_type, "spotter_html": spotter_html, "buy_html": buy_html, "jail_html": jail_html, "buy_cond_count": buy_cond_count, "diff_from_cost": diff_from_cost, "vol_ratio": vol_ratio, "sell_cond_count": sell_cond_count, "is_overridden": is_overridden}
    except: return None

def calc_real_profit(cost, price, qty):
    if cost <= 0: return 0, 0
    buy_val = cost * qty * 1000
    sell_val = price * qty * 1000
    fee_buy = max(20, int(buy_val * 0.001425))
    fee_sell = max(20, int(sell_val * 0.001425))
    tax = int(sell_val * 0.003)
    profit = sell_val - buy_val - fee_buy - fee_sell - tax
    return profit, (profit/buy_val)*100 if buy_val > 0 else 0

# ==========================================
# 🖥️ 戰情室主要版面
# ==========================================
col_title, col_sync, col_logout = st.columns([4, 1, 1])
with col_title: st.markdown("<h1 style='color:#FFB300; margin: 0;'>54088</h1>", unsafe_allow_html=True)
with col_sync:
    st.markdown("<div class='sync-btn'>", unsafe_allow_html=True)
    st.button("🔄 同步更新即時報價", use_container_width=True) 
    st.markdown("</div>", unsafe_allow_html=True)
with col_logout:
    st.markdown("<div class='lock-btn'>", unsafe_allow_html=True)
    st.button("🔒 系統鎖定", use_container_width=True, on_click=cb_logout)
    st.markdown("</div>", unsafe_allow_html=True)

weather_str, weather_color = get_market_weather()
st.markdown(f"<div style='text-align:right; color:#888; font-size:12px; margin-bottom:10px;'>大盤天候：<strong style='color:{weather_color};'>{weather_str}</strong> | 40檔擴容滿載版 | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</div>", unsafe_allow_html=True)

port_count = len(st.session_state.portfolio)
pin_count = len(st.session_state.pinned_stocks)
total_unrealized = 0
action_needed = 0
golden_targets = 0

for code, p_data in st.session_state.portfolio.items():
    d = calculate_tactical_signals(p_data['raw_data'], p_data['cat'])
    if d:
        p, _ = calc_real_profit(p_data['entry_price'], d['price'], p_data['qty'])
        total_unrealized += p
        if d['sell_cond_count'] >= 2 or p < -10.0: action_needed += 1

for code, p_data in st.session_state.pinned_stocks.items():
    d = calculate_tactical_signals(p_data['raw_data'], p_data['cat'])
    if d and "✅" in d['signal']: golden_targets += 1

st.markdown(f"""
<div class='hud-box'>
    <div class='hud-title'>🌐 大將軍戰情總覽 (HUD)</div>
    <div class='hud-metric'><span style='color:#aaa;'>現有庫存 / 鎖定雷達</span> <strong style='color:#fff;'>{port_count} 檔 / {pin_count} 檔</strong></div>
    <div class='hud-metric'><span style='color:#aaa;'>總未實現損益估算</span> <strong style='color:{'#ff4d4d' if total_unrealized>0 else '#00FF00'}; font-size:18px;'>{total_unrealized:+,.0f} 元</strong></div>
    <div class='health-bar-bg'><div class='{'health-bar-fill-green' if total_unrealized >= 0 else 'health-bar-fill-red'}' style='width: {max(0, min(100, 50 + (total_unrealized / 50000) * 50))}%;'></div></div>
    <div class='hud-metric' style='margin-top:10px; padding-top:10px; border-top:1px dashed #333;'>
        <span style='color:#2ecc71;'>🎯 雷達內可狙擊目標：<strong>{golden_targets} 檔</strong></span>
        <span style='color:#e74c3c;'>🚨 庫存需警戒/撤退：<strong>{action_needed} 檔</strong></span>
    </div>
</div>
""", unsafe_allow_html=True)

st.markdown("<div style='background:#16191f; padding:15px; border-radius:8px; border: 1px solid #3498db; margin-bottom:10px;'>", unsafe_allow_html=True)
st.markdown("<h4 style='color:#3498db; margin-top:0px;'>📡 智能情報萃取器 (可貼整段對話)</h4>", unsafe_allow_html=True)
with st.form(key='intel_form', clear_on_submit=True): 
    intel_input = st.text_area("直接貼上 CEO 的整段報告或密碼 (系統會自動找出 INTEL 代碼)：", placeholder="請直接貼上對話...")
    submit_button = st.form_submit_button(label='📥 啟動萃取與注入')
    if submit_button and intel_input:
        matches = re.findall(r'INTEL:([0-9A-Za-z:,\s\?]+)', intel_input)
        if matches:
            raw_str = matches[0]
            st.session_state.intel_mission = [x.strip() for x in raw_str.split(",") if x.strip()]
st.markdown("</div>", unsafe_allow_html=True)

# 三大雷達引擎排版
col_scan1, col_scan2, col_scan3 = st.columns(3)
with col_scan1:
    st.markdown("<div class='scan-btn-golden'>", unsafe_allow_html=True)
    if st.button("🚀 黃金起漲掃描\n(帶量或KDJ金叉)", use_container_width=True):
        with st.spinner("📡 正在全域起漲過濾..."):
            golden_stocks = []
            for sym in TW_STOCKS.keys():
                clean_sym = sym.strip()
                if clean_sym in st.session_state.portfolio or clean_sym in st.session_state.pinned_stocks: continue
                d = calculate_tactical_signals(f"{clean_sym}:?:?:?:?", "scan") 
                if d and d['buy_cond_count'] >= 1 and -5.0 <= d['diff_from_cost'] <= 10.0 and d['gain'] > -2.0 and d['price'] <= 400:
                    if not any(x in d['signal'] for x in ["❌", "⏳"]):
                        golden_stocks.append(d)
            st.session_state.scan_results = golden_stocks
            st.session_state.scan_mode = "golden"
    st.markdown("</div>", unsafe_allow_html=True)

with col_scan2:
    st.markdown("<div class='scan-btn-stealth'>", unsafe_allow_html=True)
    if st.button("🕵️‍♂️ 底部潛伏掃描\n(盤整＋1.2倍量)", use_container_width=True):
        with st.spinner("📡 正在深潛探測..."):
            stealth_stocks = []
            for sym in TW_STOCKS.keys():
                clean_sym = sym.strip()
                if clean_sym in st.session_state.portfolio or clean_sym in st.session_state.pinned_stocks: continue
                d = calculate_tactical_signals(f"{clean_sym}:?:?:?:?", "scan")
                if d and -5.0 <= d['diff_from_cost'] <= 8.0 and d['vol_ratio'] >= 1.2 and d['price'] <= 400:
                    if not any(x in d['signal'] for x in ["❌", "⏳"]):
                        stealth_stocks.append(d)
            st.session_state.scan_results = stealth_stocks
            st.session_state.scan_mode = "stealth"
    st.markdown("</div>", unsafe_allow_html=True)

with col_scan3:
    st.markdown("<div class='scan-btn-yield'>", unsafe_allow_html=True)
    if st.button("🛡️ 防禦與循環掃描\n(高殖利/季節循環)", use_container_width=True):
        with st.spinner("📡 正在搜尋安全防禦地帶..."):
            yield_stocks = []
            for sym in YIELD_POOL + CYCLICAL_POOL:
                clean_sym = sym.strip()
                if clean_sym in st.session_state.portfolio or clean_sym in st.session_state.pinned_stocks: continue
                d = calculate_tactical_signals(f"{clean_sym}:?:?:?:?", "scan")
                if d and d['price'] <= 400 and d['diff_from_cost'] >= -6.0:
                    if not any(x in d['signal'] for x in ["❌", "⏳"]):
                        d['extra_badge'] = "💰 高殖利防禦" if clean_sym in YIELD_POOL else "🔄 季節循環"
                        yield_stocks.append(d)
            st.session_state.scan_results = yield_stocks
            st.session_state.scan_mode = "yield"
    st.markdown("</div>", unsafe_allow_html=True)

st.markdown("<h3 style='color:#f1c40f; margin-top:10px; border-bottom: 2px solid #f1c40f; padding-bottom:5px;'>🔍 手動探測雷達</h3>", unsafe_allow_html=True)
search_query = st.text_input("📝 輸入代號或名稱 (如：5347 或 智原) [輸入後按 Enter]：", key="search_input")

def render_stock_card(d, ui_key_prefix):
    strategy_html = f"""
<div style="background:#1a1c23; border-radius:6px; padding:12px; margin-bottom:12px; border: 1px solid #333; border-left: 4px solid #3498db;">
<div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;"><span style="color:#888; font-size:13px;">{d['cost_label']}</span><strong style="color:#fff; font-size:14px;">{d['cost']}</strong></div>
<div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;"><span style="color:#888; font-size:13px;">🎯 最佳入場區</span><strong style="color:{d['color']}; font-size:15px;">[ {d['buy_zone']} ]</strong></div>
<div style="display:flex; justify-content:space-between; align-items:center;"><span style="color:#888; font-size:13px;">{d['exit_s'].split('：')[0] if '：' in d['exit_s'] else d['exit_s']}</span><strong style="color:{d['exit_color']}; font-size:15px;">{d['exit_price']}</strong></div>
</div>"""

    gain_color = '#ff4d4d' if d['gain']>0 else ('#00FF00' if d['gain']<0 else '#aaaaaa')
    gain_bg = '#3a1515' if d['gain']>0 else ('#153a20' if d['gain']<0 else '#333333')
    
    price_badge = ""
    if d['is_overridden']: price_badge += "<span style='font-size:14px; background-color:#8e44ad; color:white; padding:3px 8px; border-radius:4px; margin-left:10px;'>🔧 報價已強制校正</span>"
    if d['price'] > 400: price_badge += "<span style='font-size:14px; background-color:#e74c3c; color:white; padding:3px 8px; border-radius:4px; margin-left:10px;'>⚠️ >400元 (高價警戒)</span>"
    
    extra_badge_html = f"<span class='special-badge'>{d['extra_badge']}</span>" if d.get('extra_badge') else ""
    downgrade_html = f"<div style='background-color:#3a2515; color:#f39c12; font-size:13px; font-weight:bold; padding:6px 12px; border-radius:5px; margin-bottom:10px; border:1px solid #f39c12;'>{d['downgrade_alert']}</div>" if d.get('downgrade_alert') else ""

    html_card = f"""
<div style="border: 2px solid {d['color']}; border-radius: 8px; padding: 15px; background-color: #16191f; margin-bottom: 5px;">
{downgrade_html}
<div class="my-tooltip" style="font-weight:bold; font-size:18px; margin-bottom:5px;">{d['name']} ({d['code']}) | 🛡️ {d['shd']}</div>
<div class="my-tooltip" style="font-size:32px; font-weight:bold; margin-bottom: 10px; display:flex; align-items:center; flex-wrap:wrap; gap:12px;">
{d['price']:.2f} {price_badge} <span style="font-size:16px; color:{gain_color}; background-color:{gain_bg}; padding:4px 10px; border-radius:6px; border: 1px solid {gain_color}40; line-height:1;">{d['gain']:+.1f}%</span></div>
<div style="margin-bottom: 15px;">{extra_badge_html}<span class="my-tooltip info-badge">{d['chip']}<span class="my-tooltiptext">{d['chip_desc']}</span></span><span class="my-tooltip info-badge">📊 {d['val']}<span class="my-tooltiptext">{d['val_desc']}</span></span><span class="my-tooltip info-badge">{d['kdj']}<span class="my-tooltiptext">{d['kdj_desc']}</span></span></div>
{d['buy_html']}{d['spotter_html']}{d['jail_html']}    
<div style="background:#2b2b36; border-radius:5px; padding:10px; display:flex; justify-content:space-between; text-align:center; margin-bottom:10px;">
<div style="flex:1; color:#aaa; font-size:12px;">開盤<br><span style="color:#fff; font-size:15px; font-weight:bold;">{d['open']:.1f}</span></div>
<div style="flex:1; color:#aaa; font-size:12px;">最高<br><span style="color:#fff; font-size:15px; font-weight:bold;">{d['high']:.1f}</span></div>
<div style="flex:1; color:#aaa; font-size:12px;">最低<br><span style="color:#fff; font-size:15px; font-weight:bold;">{d['low']:.1f}</span></div>
<div style="flex:1; color:#aaa; font-size:12px;">總量<br><span style="color:#fff; font-size:15px; font-weight:bold;">{d['vol']}張</span></div>
</div>
{strategy_html}
<div style="background:{d['signal_bg']}; padding:10px; border-radius:6px; text-align:center; margin-bottom:10px; border: 1px solid {d['color']}40;"><span style="color:#aaa; font-size:12px;">⚡ 系統量化戰術判定</span><br><strong style="color:{d['color']}; font-size:18px;">{d['signal']}</strong></div>
</div>"""
    st.markdown(html_card, unsafe_allow_html=True)
    
    is_unknown_intel = "?" in d['raw_data']
    is_pinned = d['code'] in st.session_state.pinned_stocks
    
    with st.expander(f"🔧 1. 強制校正報價 ({d['name']})"):
        st.markdown("<div style='background:#2c153a; padding:10px; border-radius:5px; border-left:3px solid #9b59b6; margin-bottom:5px;'>", unsafe_allow_html=True)
        oc1, oc2, oc3 = st.columns([2, 1, 1])
        oc1.number_input("手動輸入即時現價", value=float(d['price']), step=0.5, key=f"override_input_{ui_key_prefix}_{d['code']}", label_visibility="collapsed")
        st.markdown("<div class='override-btn'>", unsafe_allow_html=True)
        oc2.button("⚡ 校正測算", key=f"btn_override_{ui_key_prefix}_{d['code']}", use_container_width=True, on_click=cb_override_price, args=(d['code'], ui_key_prefix))
        st.markdown("</div>", unsafe_allow_html=True)
        if d['is_overridden']: oc3.button("🔄 恢復API", key=f"btn_clear_ov_{ui_key_prefix}_{d['code']}", use_container_width=True, on_click=cb_clear_override, args=(d['code'],))
        st.markdown("</div>", unsafe_allow_html=True)

    with st.expander(f"📌 2. 情報參數與雷達鎖定 ({d['name']})"):
        if is_unknown_intel: st.markdown("<div style='color:#f39c12; font-size:13px; font-weight:bold; margin-bottom:10px;'>⚠️ 偵測到未知情報！請向 CEO 獲取參數並設定。</div>", unsafe_allow_html=True)
        new_shd, new_chip, new_val = "4", "0", "0"
        if is_unknown_intel:
            ic1, ic2, ic3 = st.columns(3)
            new_shd = ic1.selectbox("盾", ["1", "2", "3", "4", "5", "?"], index=5, key=f"ishd_{ui_key_prefix}_{d['code']}")
            new_chip = ic2.selectbox("籌碼", ["0", "1", "2", "?"], index=3, format_func=lambda x: CHIP_MAP[x][:5], key=f"ichip_{ui_key_prefix}_{d['code']}")
            new_val = ic3.selectbox("位階", ["0", "1", "2", "3", "?"], index=4, format_func=lambda x: VAL_MAP[x][:5], key=f"ival_{ui_key_prefix}_{d['code']}")
        else:
            parts = d['raw_data'].split(":")
            new_shd = parts[1] if len(parts)>1 else "?"
            new_chip = parts[3] if len(parts)>3 else "?"
            new_val = parts[4] if len(parts)>4 else "?"

        compiled_raw_data = f"{d['code']}:{new_shd}:0:{new_chip}:{new_val}:0"
        if not is_pinned:
            st.markdown("<div class='pin-btn'>", unsafe_allow_html=True)
            st.button(f"📌 僅鎖定雷達", key=f"pinbtn_{ui_key_prefix}_{d['code']}", use_container_width=True, on_click=cb_pin_stock, args=(d['code'], compiled_raw_data, d['cat']))
            st.markdown("</div>", unsafe_allow_html=True)
        else:
            st.markdown("<div class='unpin-btn'>", unsafe_allow_html=True)
            st.button(f"❌ 移除鎖定", key=f"unpinbtn_{ui_key_prefix}_{d['code']}", use_container_width=True, on_click=cb_unpin_stock, args=(d['code'],))
            st.markdown("</div>", unsafe_allow_html=True)

    with st.expander(f"💼 3. 風險試算與建立陣地 ({d['name']})"):
        c1, c2 = st.columns(2)
        sim_cost = c1.number_input("進場成本", value=float(d['price']), key=f"c_{ui_key_prefix}_{d['code']}")
        sim_qty = c2.number_input("建倉張數", value=1.0, key=f"q_{ui_key_prefix}_{d['code']}")
        
        exit_val = safe_parse_float(d['exit_price'], default=d['price'])
        risk_loss, risk_pct = calc_real_profit(sim_cost, exit_val, sim_qty)
        risk_bar_pct = min(100, max(0, abs(risk_pct) * 5))
        st.markdown(f"""
        <div style='background:#3a1515; padding:8px; border-radius:5px; border-left:3px solid #e74c3c; margin-top:5px; margin-bottom:10px; font-size:12px; color:#ddd;'>
            🛡️ 戰前風控：若不幸跌至撤退點({exit_val})，預估最大風險為 <strong style='color:#e74c3c;'>{risk_loss:,.0f} 元 ({risk_pct:.1f}%)</strong>
            <div class='health-bar-bg'><div class='health-bar-fill-red' style='width: {risk_bar_pct}%;'></div></div>
        </div>
        """, unsafe_allow_html=True)
        
        parts = d['raw_data'].split(":")
        new_shd = parts[1] if len(parts)>1 else "?"
        new_chip = parts[3] if len(parts)>3 else "?"
        new_val = parts[4] if len(parts)>4 else "?"
        compiled_raw_data = f"{d['code']}:{new_shd}:0:{new_chip}:{new_val}:0"
        
        st.markdown("<div class='buy-btn'>", unsafe_allow_html=True)
        st.button(f"⚡ 轉入作戰庫存", key=f"buy_{ui_key_prefix}_{d['code']}", use_container_width=True, on_click=cb_buy_stock, args=(d['code'], compiled_raw_data, d['cat'], ui_key_prefix))
        st.markdown("</div>", unsafe_allow_html=True)

if search_query:
    with st.spinner("📡 連線抓取即時報價中..."):
        clean_code = re.split(r'[,\s、，]+', search_query)[0].replace('.TW', '').replace('.TWO', '')
        REV_TW_STOCKS = {v: k for k, v in TW_STOCKS.items()}
        code_to_scan = REV_TW_STOCKS.get(clean_code, clean_code)

        symbol_data = f"{code_to_scan}:?:?:?:?"
        d = calculate_tactical_signals(symbol_data, "search")
        if d:
            if d['code'] not in st.session_state.portfolio and d['code'] not in st.session_state.pinned_stocks:
                cols = st.columns(2)
                with cols[0]: render_stock_card(d, ui_key_prefix="search_res")
            else: st.warning(f"💡 觀測員提示：【{d['name']} ({d['code']})】已在您的雷達或庫存中。")
        else: st.error(f"🚨 查無情報：【{code_to_scan}】。請確認是否為有效台股代號。")

def render_portfolio_card(code, p_data):
    d = calculate_tactical_signals(p_data['raw_data'], p_data['cat'])
    if not d: return 
    entry_price = p_data['entry_price']
    qty = p_data['qty']
    real_profit, real_roi = calc_real_profit(entry_price, d['price'], qty)
    
    is_hard_stop = real_roi <= -10.0
    p_color = '#e74c3c' if is_hard_stop else ('#ff4d4d' if real_profit > 0 else '#00FF00')
    border_style = f"4px solid {p_color}" if is_hard_stop else f"3px solid {p_color}"
    bg_color = "#3a1515" if is_hard_stop else "#1a1a24"
    
    stop_warning = "<div class='my-tooltip' style='background:#e74c3c; color:#fff; font-weight:bold; text-align:center; padding:8px; border-radius:5px; margin-bottom:10px; display:block; width:100%;'>🚨 觸發 -10% 鐵血停損！🚨</div>" if is_hard_stop else ""
    
    strategy_html = f"""<div style="background:#1a1c23; border-radius:6px; padding:12px; margin-bottom:12px; border: 1px solid #333; border-left: 4px solid #3498db;">
<div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;"><span style="color:#888; font-size:13px;">{d['cost_label']}</span><strong style="color:#fff; font-size:14px;">{d['cost']}</strong></div>
<div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;"><span style="color:#888; font-size:13px;">🎯 最佳入場區</span><strong style="color:{d['color']}; font-size:15px;">[ {d['buy_zone']} ]</strong></div>
<div style="display:flex; justify-content:space-between; align-items:center;"><span style="color:#888; font-size:13px;">🛡️ 預估撤退/保本點</span><strong style="color:{d['exit_color']}; font-size:15px;">{d['exit_price']}</strong></div></div>"""

    gain_color = '#ff4d4d' if d['gain']>0 else ('#00FF00' if d['gain']<0 else '#aaaaaa')
    gain_bg = '#3a1515' if d['gain']>0 else ('#153a20' if d['gain']<0 else '#333333')
    
    price_badge = ""
    if d['is_overridden']: price_badge += "<span style='font-size:14px; background-color:#8e44ad; color:white; padding:3px 8px; border-radius:4px; margin-left:10px;'>🔧 報價已強制校正</span>"
    if d['price'] > 400: price_badge += "<span style='font-size:14px; background-color:#e74c3c; color:white; padding:3px 8px; border-radius:4px; margin-left:10px;'>⚠️ >400元 (高價警戒)</span>"

    extra_badge_html = f"<span class='special-badge'>{d['extra_badge']}</span>" if d.get('extra_badge') else ""
    downgrade_html = f"<div style='background-color:#3a2515; color:#f39c12; font-size:13px; font-weight:bold; padding:6px 12px; border-radius:5px; margin-bottom:10px; border:1px solid #f39c12;'>{d['downgrade_alert']}</div>" if d.get('downgrade_alert') else ""

    p_html = f"""<div style="border: {border_style}; border-radius: 8px; padding: 15px; background-color: {bg_color}; margin-bottom: 5px; box-shadow: 0 0 15px {p_color}40;">
{stop_warning}
{downgrade_html}
<div style="display:flex; justify-content:space-between; align-items:center; border-bottom: 1px solid #444; padding-bottom:10px; margin-bottom:10px;">
<div style="font-weight:bold; font-size:20px;">{d['name']} ({code})</div>
<div style="font-size:20px; font-weight:bold; color:#fff; display:flex; align-items:center; flex-wrap:wrap; gap:10px;">現價 {d['price']:.2f} {price_badge} <span style="font-size:14px; color:{gain_color}; background-color:{gain_bg}; padding:3px 8px; border-radius:4px; border: 1px solid {gain_color}40;">{d['gain']:+.1f}%</span></div></div>
<div style="margin-bottom: 15px;">
<span class="my-tooltip info-badge">{d['chip']}<span class="my-tooltiptext">{d['chip_desc']}</span></span>
<span class="my-tooltip info-badge">📊 {d['val']}<span class="my-tooltiptext">{d['val_desc']}</span></span>
<span class="my-tooltip info-badge">{d['kdj']}<span class="my-tooltiptext">{d['kdj_desc']}</span></span>
</div>
{d['buy_html']}{d['spotter_html']}{d['jail_html']}{strategy_html}
<div style="background:{d['signal_bg']}; padding:10px; border-radius:6px; text-align:center; margin-bottom:10px; border: 1px solid {d['color']}40;"><span style="color:#aaa; font-size:12px;">⚡ 系統量化戰術判定</span><br><strong style="color:{d['color']}; font-size:18px;">{d['signal']}</strong></div>
<div style="display:flex; justify-content:space-between; margin-bottom: 15px;"><div style="color:#aaa;">建倉成本: <strong style="color:#fff;">{entry_price:.2f}</strong></div><div style="color:#aaa;">庫存張數: <strong style="color:#fff;">{qty}</strong></div></div>
<div style="background:#000; padding:15px; border-radius:8px; text-align:center; margin-bottom:15px; display:block; width:100%;"><div style="color:#aaa; font-size:14px; margin-bottom:5px;">💰 即時未實現淨損益</div><div style="font-size:36px; font-weight:bold; color:{p_color};">{real_profit:+,.0f} 元</div><div style="font-size:18px; color:{p_color};">({real_roi:+.2f}%)</div></div></div>"""
    st.markdown(p_html, unsafe_allow_html=True)
    
    st.markdown("<div class='sell-btn'>", unsafe_allow_html=True)
    st.button(f"🚪 撤退清倉 (移除)", key=f"sell_{code}", on_click=cb_sell_stock, args=(code,))
    st.markdown("</div>", unsafe_allow_html=True)

    with st.expander(f"🔧 強制校正報價 ({code})"):
        st.markdown("<div style='background:#2c153a; padding:10px; border-radius:5px; border-left:3px solid #9b59b6; margin-bottom:15px;'>", unsafe_allow_html=True)
        oc1, oc2, oc3 = st.columns([2, 1, 1])
        oc1.number_input("手動輸入即時現價", value=float(d['price']), step=0.5, key=f"override_input_port_{code}", label_visibility="collapsed")
        st.markdown("<div class='override-btn'>", unsafe_allow_html=True)
        oc2.button("⚡ 校正", key=f"btn_override_port_{code}", use_container_width=True, on_click=cb_override_price, args=(code, "port"))
        st.markdown("</div>", unsafe_allow_html=True)
        if d['is_overridden']: oc3.button("🔄 恢復API", key=f"btn_clear_ov_port_{code}", use_container_width=True, on_click=cb_clear_override, args=(code,))
        st.markdown("</div>", unsafe_allow_html=True)

if st.session_state.portfolio:
    st.markdown(f"<h2 style='color:#ff4d4d; margin-top:20px; border-bottom: 2px solid #ff4d4d; padding-bottom:5px;'>💼 狙擊手作戰庫存 ({len(st.session_state.portfolio)}/{MAX_CAPACITY})</h2>", unsafe_allow_html=True)
    cols = st.columns(2)
    for i, (code, p_data) in enumerate(list(st.session_state.portfolio.items())):
        with cols[i % 2]: render_portfolio_card(code, p_data)

if st.session_state.pinned_stocks:
    st.markdown(f"<h2 style='color:#f1c40f; margin-top:20px; border-bottom: 2px solid #f1c40f; padding-bottom:5px;'>⭐ 觀測員警戒雷達 ({len(st.session_state.pinned_stocks)}/{MAX_CAPACITY})</h2>", unsafe_allow_html=True)
    cols = st.columns(2)
    for i, (code, p_data) in enumerate(list(st.session_state.pinned_stocks.items())):
        if code in st.session_state.portfolio: continue
        d = calculate_tactical_signals(p_data['raw_data'], p_data['cat'])
        if d:
            with cols[i % 2]: render_stock_card(d, ui_key_prefix="pinned")

if st.session_state.intel_mission:
    st.markdown("<h2 style='color:#9b59b6; margin-top:30px; border-bottom: 2px solid #9b59b6; padding-bottom:5px;'>📡 總部手動派發任務</h2>", unsafe_allow_html=True)
    cols = st.columns(2)
    valid_count = 0
    for symbol_data in st.session_state.intel_mission:
        d = calculate_tactical_signals(symbol_data, "intel")
        if not d: continue
        if d['code'] in st.session_state.portfolio or d['code'] in st.session_state.pinned_stocks: continue 
        with cols[valid_count % 2]: render_stock_card(d, ui_key_prefix="intel")
        valid_count += 1

if st.session_state.get('scan_results') is not None:
    visible_results = [d for d in st.session_state.scan_results if d['code'] not in st.session_state.portfolio and d['code'] not in st.session_state.pinned_stocks]
    
    if len(st.session_state.scan_results) > 0 and len(visible_results) == 0:
        st.markdown("<h4 style='color:#00FF00; margin-top:30px; text-align:center;'>📡 所有掃描目標已全數在庫存與觀測雷達中列陣。</h4>", unsafe_allow_html=True)
    elif len(st.session_state.scan_results) == 0:
        st.markdown("<h4 style='color:#f39c12; margin-top:30px; text-align:center;'>📡 報告總指揮：目前市場無完全符合條件之標的，建議持續觀測。</h4>", unsafe_allow_html=True)
    else:
        if st.session_state.get('scan_mode') == 'golden':
            st.markdown("<h2 style='color:#00FF00; margin-top:30px; border-bottom: 2px solid #00FF00; padding-bottom:5px;'>⚡ 黃金起漲目標 (實戰過濾淨化版)</h2>", unsafe_allow_html=True)
        elif st.session_state.get('scan_mode') == 'stealth':
            st.markdown("<h2 style='color:#00d2ff; margin-top:30px; border-bottom: 2px solid #00d2ff; padding-bottom:5px;'>🕵️‍♂️ 底部潛伏爆量目標 (1.2倍均量)</h2>", unsafe_allow_html=True)
        elif st.session_state.get('scan_mode') == 'yield':
            st.markdown("<h2 style='color:#e056fd; margin-top:30px; border-bottom: 2px solid #9b59b6; padding-bottom:5px;'>🛡️ 防禦與循環掃描 (高殖利/季節循環)</h2>", unsafe_allow_html=True)
            
        cols = st.columns(2)
        for i, d in enumerate(visible_results):
            with cols[i % 2]: render_stock_card(d, ui_key_prefix="scan_res")

st.markdown("<h2 style='color:#3498db; margin-top:40px; border-bottom: 2px solid #3498db; padding-bottom:5px;'>📤 呼叫 CEO：一鍵匯出情報分析</h2>", unsafe_allow_html=True)
st.markdown("<p style='color:#ccc; font-size:14px;'>點擊下方代碼框右上角的「複製」按鈕，直接貼到聊天室給 CEO 進行深度分析！</p>", unsafe_allow_html=True)

export_codes = set()
for c in st.session_state.pinned_stocks.keys(): export_codes.add(c)
if st.session_state.get('scan_results'):
    for d in st.session_state.scan_results: 
        if d['code'] not in st.session_state.portfolio and d['code'] not in st.session_state.pinned_stocks:
            export_codes.add(d['code'])

if export_codes:
    export_str = f"指令4 {', '.join(list(export_codes))}"
    st.code(export_str, language="text")
else:
    st.info("📌 目前警戒雷達或掃描區沒有標的，請先手動鎖定或啟動掃描功能。")

st.markdown("---")
with st.expander("📘 AI 幕僚通訊暗號本 (總指揮專用)"):
    st.markdown("""
    在聊天室直接輸入以下指令，獲取 **戰術密碼 (INTEL CODE)**：
    * **`指令1`**：每日盤後全域掃描
    * **`指令2`**：高殖利率防禦狙擊
    * **`指令3`**：巨鯨籌碼突擊掃描
    * **`指令4 [代號]`**：單檔深度情報掃描
    * **`指令5`**：處置股逃命預警
    """)
