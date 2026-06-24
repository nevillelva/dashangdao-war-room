import streamlit as st
import yfinance as yf
import pandas as pd
from datetime import datetime
import re
import math
import time
import json  
import os    
import requests  

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
.my-tooltip .my-tooltiptext { visibility: hidden; width: max-content; max-width: 280px; background-color: #ffcc00; color: #111; text-align: left; border-radius: 6px; padding: 10px 14px; position: absolute; z-index: 99999; bottom: 125%; left: 50%; transform: translateX(-50%); opacity: 0; transition: opacity 0.2s; font-size: 14px; font-weight: bold; line-height: 1.5; box-shadow: 0px 4px 15px rgba(0,0,0,0.6); pointer-events: none; white-space: normal; }
.my-tooltip .my-tooltiptext::after { content: ""; position: absolute; top: 100%; left: 50%; margin-left: -6px; border-width: 6px; border-style: solid; border-color: #ffcc00 transparent transparent transparent; }
.my-tooltip:hover .my-tooltiptext { visibility: visible; opacity: 1; }
</style>''', unsafe_allow_html=True)

# ==========================================
# 🛡️ 實體硬碟資料庫引擎
# ==========================================
COMMANDER_PIN = "0826"
MAX_CAPACITY = 40  
DB_FILE = "54088_database.json" 

def load_db():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r", encoding="utf-8") as f: return json.load(f)
        except: return {"pinned_stocks": {}, "portfolio": {}}
    return {"pinned_stocks": {}, "portfolio": {}}

def save_db():
    data = {"pinned_stocks": st.session_state.pinned_stocks, "portfolio": st.session_state.portfolio}
    try:
        with open(DB_FILE, "w", encoding="utf-8") as f: json.dump(data, f, ensure_ascii=False, indent=4)
    except: pass

def cb_login():
    if st.session_state.pwd_input == COMMANDER_PIN:
        st.session_state.authenticated = True
        st.query_params["auth"] = "54088"
    else: st.session_state.login_error = True

def cb_pin_stock(code, raw_data, cat):
    if len(st.session_state.pinned_stocks) >= MAX_CAPACITY: return
    st.session_state.pinned_stocks[code] = {'raw_data': raw_data, 'cat': cat}
    save_db()

def cb_unpin_stock(code):
    if code in st.session_state.pinned_stocks:
        del st.session_state.pinned_stocks[code]
        save_db()

def cb_buy_stock(code, raw_data, cat, ui_key_prefix):
    if len(st.session_state.portfolio) >= MAX_CAPACITY: return
    try:
        cost = float(st.session_state.get(f"c_{ui_key_prefix}_{code}", 0.0))
        qty = float(st.session_state.get(f"q_{ui_key_prefix}_{code}", 1.0))
        mode = st.session_state.get(f"mode_{ui_key_prefix}_{code}", "短線技術動能單")
        manual_target = float(st.session_state.get(f"tval_{ui_key_prefix}_{code}", 0.0))
        catalyst = st.session_state.get(f"cat_{ui_key_prefix}_{code}", "")
    except: 
        cost, qty, mode, manual_target, catalyst = 0.0, 1.0, "短線技術動能單", 0.0, ""
    st.session_state.portfolio[code] = {"entry_price": round(cost, 2), "qty": round(qty, 3), "raw_data": raw_data, "cat": cat, "mode": mode, "manual_target": manual_target, "catalyst": catalyst}
    if code in st.session_state.pinned_stocks: del st.session_state.pinned_stocks[code]
    save_db()

def cb_sell_stock(code):
    if code in st.session_state.portfolio:
        del st.session_state.portfolio[code]
        save_db()

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

if 'authenticated' not in st.session_state: st.session_state.authenticated = (st.query_params.get("auth") == "54088")

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

if 'manual_prices' not in st.session_state: st.session_state.manual_prices = {} 
if 'scan_results' not in st.session_state: st.session_state.scan_results = []
if 'scan_mode' not in st.session_state: st.session_state.scan_mode = ""

if 'db_loaded' not in st.session_state:
    db_data = load_db()
    st.session_state.pinned_stocks = db_data.get("pinned_stocks", {})
    st.session_state.portfolio = db_data.get("portfolio", {})
    st.session_state.db_loaded = True

# ==========================================
# 📡 系統參數庫 & 全市場極速快取連線引擎
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

@st.cache_data(ttl=86400) 
def get_full_market_codes():
    codes = []
    try:
        twse_url = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
        twse_res = requests.get(twse_url, timeout=10)
        if twse_res.status_code == 200:
            codes.extend([item['Code'] for item in twse_res.json() if len(item.get('Code', '')) >= 4])
            
        tpex_url = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes"
        tpex_res = requests.get(tpex_url, timeout=10)
        if tpex_res.status_code == 200:
            codes.extend([item['SecuritiesCompanyCode'] for item in tpex_res.json() if len(item.get('SecuritiesCompanyCode', '')) >= 4])
    except: pass
    
    clean_codes = [c for c in set(codes) if c.isdigit()]
    if not clean_codes: return list(TW_STOCKS.keys())
    return clean_codes

FULL_MARKET_CODES = get_full_market_codes()

YIELD_POOL = ["2881", "2882", "2891", "2886", "0050", "0056", "00878", "00919", "00929"]
CYCLICAL_POOL = ["1519", "1513", "1514", "1504", "2603", "2609", "2615", "2618", "2610", "2002", "1605", "1101", "2542", "2408", "3481", "2409", "2344", "2337"]

CHIP_MAP = {"1": "🐳 巨鯨進駐(籌碼面)", "2": "🩸 外資提款(籌碼面)", "0": "⚖️ 籌碼平穩(籌碼面)", "?": "❓ 籌碼待查(籌碼面)"}
VAL_MAP = {"1": "🟢 便宜(長線價值)", "2": "🟡 合理(長線價值)", "3": "🔴 昂貴(長線價值)", "0": "⚪ 未定(長線價值)", "?": "❓ 待定(長線價值)"}

def safe_int(val, default=0):
    try: return int(val) if val else default
    except: return default
def safe_float(val, default=None):
    try: return float(val) if val else default
    except: return default
def safe_parse_float(val, default=0.0):
    if isinstance(val, (int, float)): return float(val)
    try: return float(re.findall(r"[-+]?\d*\.\d+|\d+", str(val))[0])
    except: return default

def get_stock_name(symbol):
    return TW_STOCKS.get(symbol, f"個股 {symbol}")

@st.cache_data(ttl=300)
def get_market_weather():
    try:
        taiex = yf.Ticker("^TWII").history(period="1mo")
        if taiex.empty: return "未知", "#888", False, False
        current = taiex['Close'].iloc[-1]
        ma20 = taiex['Close'].rolling(window=20).mean().iloc[-1]
        gain = ((current - taiex['Close'].iloc[-2]) / taiex['Close'].iloc[-2]) * 100
        is_bull_market = current > ma20 or gain > 0
        is_panic = gain <= -2.0
        if is_panic: return f"🌩️ 系統性崩跌風險 (加權 {current:.0f})", "#e74c3c", is_bull_market, True
        elif current > ma20: return f"☀️ 多頭順風環境 (加權 {current:.0f})", "#2ecc71", is_bull_market, False
        else: return f"☁️ 空頭震盪環境 (加權 {current:.0f} 破月線)", "#f1c40f", is_bull_market, False
    except: return "📡 大盤資料獲取中...", "#888", False, False

# ==========================================
# 🧠 核心量化演算法
# ==========================================
def calculate_tactical_signals(symbol_data, category_type="main", mode="短線技術動能單", manual_target=0.0):
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

        if chip_code == "1": chip_desc = "【籌碼面】大戶跟法人正在偷偷倒錢買進，跟著主力大哥走比較安全！"
        elif chip_code == "2": chip_desc = "【籌碼面】外資或主力正在大量賣出倒貨，散戶容易被當韭菜割，快避開。"
        elif chip_code == "0": chip_desc = "【籌碼面】最近沒有大資金在裡面搞事，多空雙方都在觀望。"
        else: chip_desc = "【籌碼面】目前沒有資料，先不要盲目動作。"

        if val_code == "1": val_desc = "【基本面】這家公司的股價比它真實價值便宜很多，也就是「物超所值」，適合放長線慢慢賺。"
        elif val_code == "2": val_desc = "【基本面】股價跟公司的價值差不多，不貴也不便宜，想買的話要看短線有沒有人點火發動。"
        elif val_code == "3": val_desc = "【基本面】股價已經被炒得太高了，現在買等於幫別人抬轎，隨時會大跌，絕對不要追高！"
        else: val_desc = "【基本面】系統還在評估它的真實價值。"
        
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

        manual_override = st.session_state.manual_prices.get(symbol)
        is_overridden = False

        if hist.empty or ticker is None:
            current_price = float(manual_override) if (manual_override and manual_override > 0) else 0.0
            shd_display = "❓ 待查" if override_shd_raw == "?" else f"{override_shd_raw}分"
            return {
                "name": stock_name, "code": symbol, "price": current_price, "gain": 0.0,
                "cost": 0.0, "cost_label": "網路中斷(請手動更新)", "buy_zone": "0 - 0",
                "shd": shd_display, "chip_code": chip_code, "chip": CHIP_MAP.get(chip_code, "⚖️"),
                "val_code": val_code, "val": VAL_MAP.get(val_code, "⚪"),
                "kdj": "⚠️ 無法取得指標", "chip_desc": chip_desc, "val_desc": val_desc,
                "kdj_desc": "請檢查網路或手動校正", "downgrade_alert": "🚨 API 阻擋或網路延遲，強制降級為手動模式",
                "signal": "❌ 【API抓取失敗】請稍後重整，或手動輸入現價！", "color": "#888888", "signal_bg": "#111111",
                "extra_badge": "⚠️ 斷線盲區", "exit_s": "未知", "exit_price": "0", "exit_color": "#888", "exit_bg": "#333",
                "vol": 0, "open": 0, "high": 0, "low": 0, "raw_data": symbol_data, "cat": category_type,
                "spotter_html": "<div style='color:#e74c3c;'>無法取得 K 線資料，撤退雷達失效</div>",
                "buy_html": "<div style='color:#e74c3c;'>無法取得 K 線資料，起漲雷達失效</div>",
                "jail_html": "", "buy_cond_count": 0, "diff_from_cost": 0.0, "vol_ratio": 0.0,
                "sell_cond_count": 0, "is_overridden": (manual_override is not None), "auto_target": 0.0, "is_shield_active": False,
                "is_ma_bullish": False
            }

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

        is_ma_bullish = (current_price > ma10) and (ma10 > ma20) and (ma20 > ma60)
        
        auto_target_price = round(float(hist['High'].max()), 1)

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
        kdj_signal = "📈 低檔金叉(短線時機)" if kdj_golden_cross else ("📉 高檔死叉(短線時機)" if (k>70 and k<d) else "〰️ KDJ 震盪(短線時機)")

        if kdj_golden_cross: kdj_desc = "【短線時機】股價跌到底部準備反彈了！這是一個「短線轉強可以買」的黃金暗號。"
        elif (k > 70 and k < d): kdj_desc = "【短線時機】股價短線漲太多，動能開始衰退了，隨時可能往下殺，要提高警覺。"
        else: kdj_desc = "【短線時機】目前沒有特別的漲跌訊號，就是一般的上下震盪。"

        is_breakout = (gain > 2.0) and (vol > vol_5d * 1.5) and (current_price > ma20) 
        buy_cond_count = sum([kdj_golden_cross, is_macd_red, is_breakout])
        
        buy_status, buy_color, buy_bg = "⚪ 醞釀中 (無明顯起漲)", "#aaaaaa", "#1a1a24"
        if buy_cond_count == 3: buy_status, buy_color, buy_bg = "🔥 三火全亮，強勢起漲！", "#ff4d4d", "#3a1515"
        elif buy_cond_count == 2: buy_status, buy_color, buy_bg = "🚀 雙引擎發動，準備表態", "#f1c40f", "#3a3015"
        elif buy_cond_count == 1: buy_status, buy_color, buy_bg = "✨ 底部浮現單一火苗", "#3498db", "#152a3a"

        ma_bull_str = "<span>🔴 均線多頭(飆股基因)</span>" if is_ma_bullish else "<span>⚪ 均線未排列</span>"
        buy_html = f"<div class='my-tooltip' style='background:{buy_bg}; padding:10px 15px; border-radius:8px; margin-bottom:12px; border-left: 5px solid {buy_color}; display:block; width:100%;'><div style='font-size:12px; color:#ddd; margin-bottom:6px;'>🚀 起漲(買進)雷達：<strong style='color:{buy_color}; font-size:14px;'>{buy_status}</strong></div><div style='font-size:12px; color:#eee; display:flex; justify-content:space-between;'><span>{'🔴' if kdj_golden_cross else '⚪'} KDJ金叉</span><span>{'🔴' if is_macd_red else '⚪'} MACD翻紅</span><span>{'🔴' if is_breakout else '⚪'} 帶量上攻</span>{ma_bull_str}</div><span class='my-tooltiptext'>短線有沒有起漲的動能。如果出現「均線多頭」代表長線也在保護短線，是飆股的特徵。</span></div>"

        is_huge_vol = vol > (vol_5d * 2.0)               
        is_black_k = current_price < open_p and gain < 0 
        is_break_ma5 = current_price < ma5               
        
        sell_cond_count = sum([is_huge_vol, is_black_k, is_break_ma5])
        
        downgrade_alert = ""
        if override_cost:
            main_cost = override_cost
            cost_label = "自訂防線"
        else:
            if current_price >= ma60 * 0.96: 
                main_cost, cost_label = "MA60季線防禦"
            elif current_price >= ma120 * 0.96: 
                main_cost, cost_label = "MA120半年線退守"
                downgrade_alert = "⚠️ 系統自動降級：跌破季線，退到半年線防禦"
            else: 
                main_cost, cost_label = "MA240年線大底"
                downgrade_alert = "🚨 系統極限退守：跌破半年線，退到年線最後大底"

        main_cost = round(main_cost, 1)
        buy_low, buy_high = round(main_cost * 0.97, 1), round(main_cost * 1.03, 1)
        diff_from_cost = ((current_price - max(main_cost, 0.001)) / max(main_cost, 0.001)) * 100

        is_shield_active = False
        target_reference = manual_target if manual_target > 0 else auto_target_price
        if mode == "長線價值波段單" and val_code in ["1", "2", "?"] and current_price < target_reference:
            is_shield_active = True

        is_cyclical = symbol in CYCLICAL_POOL
        if is_cyclical and sell_cond_count >= 2:
            is_shield_active = False
            if downgrade_alert: downgrade_alert += " | "
            downgrade_alert += "⚠️ 循環股逃命條款：技術轉空，強制撤除護盾！"

        if is_shield_active:
            sell_cond_count = 0  
            spotter_status, spotter_color, spotter_bg = "🛡️ 左側波段護盾全開 (無視短線洗盤)", "#3498db", "#152a3a"
            spotter_html = f"<div class='my-tooltip' style='background:{spotter_bg}; padding:10px 15px; border-radius:8px; margin-bottom:12px; border-left: 5px solid {spotter_color}; display:block; width:100%;'><div style='font-size:12px; color:#ddd; margin-bottom:6px;'>🚨 撤退雷達狀態：<strong style='color:{spotter_color}; font-size:14px;'>{spotter_status}</strong></div><div style='font-size:11px; color:#bbb;'>因為您設定了長線波段，且股價還沒漲到目標價，系統自動幫您過濾掉短線忽上忽下的假動作，讓您安心抱單。</div></div>"
        else:
            spotter_status, spotter_color, spotter_bg = "🟢 目前安全，繼續抱著", "#2ecc71", "#153a20"
            if sell_cond_count == 3: spotter_status, spotter_color, spotter_bg = "🔴 三個危險訊號全亮，立刻賣出逃命！", "#e74c3c", "#3a1515"
            elif sell_cond_count == 2: spotter_status, spotter_color, spotter_bg = "🟡 有多個危險訊號，隨時準備賣出", "#f1c40f", "#3a3015"
            elif sell_cond_count == 1: spotter_status, spotter_color, spotter_bg = "🟡 出現一個異常跌勢，請提高警戒", "#f39c12", "#3a2515"
            spotter_html = f"<div class='my-tooltip' style='background:{spotter_bg}; padding:10px 15px; border-radius:8px; margin-bottom:12px; border-left: 5px solid {spotter_color}; display:block; width:100%;'><div style='font-size:12px; color:#ddd; margin-bottom:6px;'>🚨 撤退(賣出)雷達：<strong style='color:{spotter_color}; font-size:14px;'>{spotter_status}</strong></div><div style='font-size:12px; color:#eee; display:flex; justify-content:space-between;'><span>{'🔴' if is_huge_vol else '⚪'} 爆出大量</span><span>{'🔴' if is_black_k else '⚪'} 實體大跌(黑K)</span><span>{'🔴' if is_break_ma5 else '⚪'} 跌破5日線</span></div><span class='my-tooltiptext'>短線該不該賣掉逃命的判定。如果有兩個以上亮紅燈，代表趨勢已經往下，千萬別留戀。</span></div>"

        jail_html = ""
        if len(hist) >= 7:
            close_6d_ago = max(float(hist['Close'].iloc[-6]), 0.001)
            return_6d = ((current_price - close_6d_ago) / close_6d_ago) * 100
            prev_close = float(hist['Close'].iloc[-2])
            close_7d_ago = max(float(hist['Close'].iloc[-7]), 0.001)
            prev_return_6d = ((prev_close - close_7d_ago) / close_7d_ago) * 100
            
            jail_color, jail_bg, jail_status = "#2ecc71", "#153a20", f"沒問題 (最近漲了 {return_6d:.1f}%)"
            if return_6d >= 25.0 and prev_return_6d >= 25.0: jail_color, jail_bg, jail_status = "#9b59b6", "#2c153a", f"🛑 漲太多，準備被關禁閉(處置股)！"
            elif return_6d >= 25.0: jail_color, jail_bg, jail_status = "#e74c3c", "#3a1515", f"🔥 漲太瘋，觸發證交所警告！"
            elif return_6d >= 20.0: jail_color, jail_bg, jail_status = "#f39c12", "#3a3015", f"⚠️ 快要碰到證交所紅線了"
            jail_html = f"<div class='my-tooltip' style='background:{jail_bg}; padding:10px 15px; border-radius:8px; margin-bottom:12px; border-left: 5px solid {jail_color}; display:block; width:100%;'><div style='font-size:12px; color:#ddd; margin-bottom:4px;'>⚖️ 證交所警示：<strong style='color:{jail_color}; font-size:13px;'>{jail_status}</strong></div><span class='my-tooltiptext'>用來監看股票是不是短期漲太兇。如果漲太多變成「處置股」，股票流動性會變很差，要小心。</span></div>"

        ACTION_WAIT = "⏳ 【等待時機，先別買】"
        ACTION_NO   = "❌ 【極度危險，千萬別買】"
        ACTION_YES  = "✅ 【訊號確認，可以買進】"
        ACTION_HOLD = "🛡️ 【卡在中間，先觀察】"

        if val_code == "3": exit_s, exit_p, exit_c, exit_bg = "🔴 價值太貴該賣了", f"{current_price:.1f}", "#e74c3c", "#3a1515"
        elif diff_from_cost >= 15.0: exit_s, exit_p, exit_c, exit_bg = "🛡️ 跌破這條線就停利", f"{max(ma10, main_cost * 1.05):.1f}", "#e67e22", "#3a2515"
        else: exit_s, exit_p, exit_c, exit_bg = "🚪 跌破底線一定要逃", f"{main_cost * 0.95:.1f}", "#e74c3c", "#2c153a"

        is_in_buy_zone = (buy_low <= current_price <= buy_high)
        if is_shield_active and gain < -2.0:
            signal_text, color_border, signal_bg = f"🛡️ 【左側長線抱單】 股價雖然在跌，但因為便宜且還沒到目標價，請安心抱著不要被洗掉！", "#3498db", "#152a3a"
        elif vol_5d < 0.5: 
            signal_text, color_border, signal_bg = f"{ACTION_NO} 沒人在玩這檔股票 (流動性枯竭勿碰)", "#8e44ad", "#2c153a"
        elif is_in_buy_zone:
            if sell_cond_count >= 2: 
                signal_text, color_border, signal_bg = f"{ACTION_WAIT} 雖然跌到了便宜區，但目前還在一直跌 (等不跌了再買)", "#f39c12", "#3a3015"
            elif val_code == "3": 
                signal_text, color_border, signal_bg = f"{ACTION_NO} 跌到了便宜區，但這家公司現在被炒得太貴了 (有危險別碰)", "#e67e22", "#3a2515"
            elif buy_cond_count >= 2: 
                signal_text, color_border, signal_bg = f"{ACTION_YES} 完美的買點！(又便宜，主力又剛好在拉抬)", "#00FF00", "#153a20"
            elif buy_cond_count == 1: 
                signal_text, color_border, signal_bg = f"{ACTION_YES} 跌到了便宜區，可以準備進場了", "#2ecc71", "#153a20"
            else: 
                signal_text, color_border, signal_bg = f"{ACTION_WAIT} 跌到了便宜區，但主力還沒動作 (先等等)", "#27ae60", "#102a15"
        elif diff_from_cost < -5.0: 
            signal_text, color_border, signal_bg = f"{ACTION_NO} 股價破大底還在往下掉 (絕對不要伸手去接刀)", "#e74c3c", "#3a1515"
        elif diff_from_cost > 20.0:
            if val_code == "1": signal_text, color_border, signal_bg = f"{ACTION_NO} 長線雖然便宜，但最近幾天漲太瘋了 (乖離極大，等它跌下來再買)", "#ff0000", "#3a1010"
            elif val_code == "2": signal_text, color_border, signal_bg = f"{ACTION_NO} 長線價格合理，但最近幾天漲太瘋了 (乖離極大，等它跌下來再買)", "#ff0000", "#3a1010"
            else: signal_text, color_border, signal_bg = f"{ACTION_NO} 漲太高太危險了，隨時會大崩盤 (千萬別追高)", "#ff0000", "#3a1010"
        elif diff_from_cost > 10.0:
            if val_code == "3": 
                signal_text, color_border, signal_bg = f"{ACTION_NO} 股價太貴了 (已經漲到天花板，千萬別買)", "#e74c3c", "#3a1515"
            elif sell_cond_count >= 2: 
                signal_text, color_border, signal_bg = f"{ACTION_WAIT} 高檔有人在大量賣出，趨勢往下 (等它跌完再說)", "#e67e22", "#3a2515"
            elif buy_cond_count >= 2: 
                if val_code == "1" or val_code == "2":
                    signal_text, color_border, signal_bg = f"{ACTION_YES} 長線有保護，而且主力正在強勢拉抬 (可以順勢跟著買)", "#e67e22", "#3a2515"
                else:
                    signal_text, color_border, signal_bg = f"{ACTION_YES} 主力正在強勢拉抬 (適合短線跟風買進)", "#e67e22", "#3a2515"
            else: 
                signal_text, color_border, signal_bg = f"{ACTION_WAIT} 短線漲了一段，但沒有繼續往上的力氣 (等它拉回再買)", "#e67e22", "#3a2515"
        else: 
            signal_text, color_border, signal_bg = f"{ACTION_HOLD} 股價卡在中間不上不下 (先在旁邊看戲)", "#ccc", "#2b2b36"

        buy_zone = f"{buy_low} - {buy_high}"
        shd_display = "❓ 待查" if override_shd_raw == "?" else f"{override_shd_raw}分"
        
        extra_badge = ""
        if symbol in YIELD_POOL: extra_badge = "💰 高殖利防禦股"
        elif symbol in CYCLICAL_POOL: extra_badge = "🔄 景氣循環股 (要跑得快)"
        if is_ma_bullish: extra_badge += " 📈 飆股基因(均線多頭)"

        return {"name": stock_name, "code": symbol, "price": current_price, "gain": gain, "cost": main_cost, "cost_label": cost_label, "buy_zone": buy_zone, "shd": shd_display, "chip_code": chip_code, "chip": CHIP_MAP.get(chip_code, "⚖️"), "val_code": val_code, "val": VAL_MAP.get(val_code, "⚪"), "kdj": kdj_signal, "chip_desc": chip_desc, "val_desc": val_desc, "kdj_desc": kdj_desc, "downgrade_alert": downgrade_alert, "signal": signal_text, "color": color_border, "signal_bg": signal_bg, "extra_badge": extra_badge.strip(), "exit_s": exit_s, "exit_price": exit_p, "exit_color": exit_c, "exit_bg": exit_bg, "vol": vol, "open": open_p, "high": high_p, "low": low_p, "raw_data": symbol_data, "cat": category_type, "spotter_html": spotter_html, "buy_html": buy_html, "jail_html": jail_html, "buy_cond_count": buy_cond_count, "diff_from_cost": diff_from_cost, "vol_ratio": vol_ratio, "sell_cond_count": sell_cond_count, "is_overridden": is_overridden, "auto_target": auto_target_price, "is_shield_active": is_shield_active, "is_ma_bullish": is_ma_bullish}
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

weather_str, weather_color, is_bull_market, is_panic = get_market_weather()
st.markdown(f"<div style='text-align:right; color:#888; font-size:12px; margin-bottom:10px;'>大盤天候：<strong style='color:{weather_color};'>{weather_str}</strong> | 視覺除蟲完美渲染版 | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</div>", unsafe_allow_html=True)

port_count = len(st.session_state.portfolio)
pin_count = len(st.session_state.pinned_stocks)
total_unrealized = 0
action_needed = 0
golden_targets = 0

long_term_count = 0
for code, p_data in st.session_state.portfolio.items():
    if p_data.get('mode') == '長線價值波段單': long_term_count += 1
    d = calculate_tactical_signals(p_data['raw_data'], p_data['cat'], mode=p_data.get('mode', '短線技術動能單'), manual_target=p_data.get('manual_target', 0.0))
    if d:
        p, _ = calc_real_profit(p_data['entry_price'], d['price'], p_data['qty'])
        total_unrealized += p
        if (not d['is_shield_active']) and (d['sell_cond_count'] >= 2 or p < -10.0): action_needed += 1

for code, p_data in st.session_state.pinned_stocks.items():
    d = calculate_tactical_signals(p_data['raw_data'], p_data['cat'])
    if d and "✅" in d['signal']: golden_targets += 1

# 💥 大賺小賠：資金控管與大盤恐慌危機入市
if is_panic:
    market_suggestion = "🩸 【危機入市模式啟動】大盤恐慌崩跌！請握緊現金子彈，準備切換到「左側價值佈局」撿鑽石！"
    market_bg = "#3a1515"
    market_border = "#e74c3c"
elif is_bull_market:
    market_suggestion = "💡 大盤多頭健康 ➡️ 適合【🚀 右側動能狙擊】(買強勢突破)"
    market_bg = "#1e222b"
    market_border = "#2ecc71"
else:
    market_suggestion = "💡 大盤恐慌震盪 ➡️ 適合【🛡️ 左側價值佈局】(撿錯殺便宜好股)"
    market_bg = "#1e222b"
    market_border = "#f1c40f"

# 資金配比建議
long_ratio = (long_term_count / port_count * 100) if port_count > 0 else 0
ratio_color = "#2ecc71" if long_ratio >= 70 else "#e67e22"

# 💥 徹底移除 HUD 內縮排，封殺 Markdown Code Block 解析錯誤
st.markdown(f"""
<div class='hud-box'>
<div class='hud-title'>🌐 大將軍戰情總覽 (HUD)</div>
<div class='hud-metric'><span style='color:#aaa;'>現有庫存 / 鎖定雷達</span> <strong style='color:#fff;'>{port_count} 檔 / {pin_count} 檔</strong></div>
<div class='hud-metric'><span style='color:#aaa;'>總未實現損益估算</span> <strong style='color:{'#ff4d4d' if total_unrealized>0 else '#00FF00'}; font-size:18px;'>{total_unrealized:+,.0f} 元</strong></div>
<div class='health-bar-bg'><div class='{'health-bar-fill-green' if total_unrealized >= 0 else 'health-bar-fill-red'}' style='width: {max(0, min(100, 50 + (total_unrealized / 50000) * 50))}%;'></div></div>
<div class='hud-metric' style='margin-top:10px; padding-top:10px; border-top:1px dashed #333;'>
<span style='color:#aaa;'>⚖️ 庫存資金控管 (建議波段80%/短線20%)：</span>
<strong style='color:{ratio_color};'>波段單佔比 {long_ratio:.0f}%</strong>
</div>
<div class='hud-metric' style='margin-top:10px; padding-top:10px; border-top:1px dashed #333;'>
<span style='color:#2ecc71;'>🎯 雷達內可狙擊目標：<strong>{golden_targets} 檔</strong></span>
<span style='color:#e74c3c;'>🚨 庫存需警戒/撤退：<strong>{action_needed} 檔</strong></span>
</div>
<div style='margin-top:10px; padding:8px; background-color:{market_bg}; border-radius:5px; border-left:3px solid {market_border}; font-size:13px; color:#ddd; font-weight:bold;'>
{market_suggestion}
</div>
</div>
""", unsafe_allow_html=True)

st.markdown("<div style='background:#16191f; padding:15px; border-radius:8px; border: 1px solid #3498db; margin-bottom:10px;'>", unsafe_allow_html=True)
st.markdown("<h4 style='color:#3498db; margin-top:0px;'>📡 智能情報萃取器 (無腦貼上)</h4>", unsafe_allow_html=True)
with st.form(key='intel_form', clear_on_submit=True): 
    intel_input = st.text_area("請直接把密碼貼在這裡 (多按幾個空白或全形標點都沒關係)：", placeholder="例如：2610:?:?:1:?")
    submit_button = st.form_submit_button(label='📥 啟動萃取並直接加入雷達')
    
    if submit_button and intel_input:
        clean_input = intel_input.replace("INTEL:", "").replace("ＩＮＴＥＬ：", "").strip()
        clean_input = clean_input.replace("：", ":").replace("？", "?").replace("，", ",")
        potential_items = re.split(r'[,\s]+', clean_input)
        matches = [x.strip() for x in potential_items if x.count(':') >= 3]
        
        if matches:
            added_count = 0
            for symbol_data in matches:
                parts = symbol_data.split(":")
                code = parts[0].strip()
                if code and code not in st.session_state.portfolio and code not in st.session_state.pinned_stocks:
                    st.session_state.pinned_stocks[code] = {'raw_data': symbol_data, 'cat': 'intel'}
                    added_count += 1
            save_db()
            if added_count > 0:
                st.success(f"✅ 報告總指揮：成功攔截 {added_count} 檔主力情報，已全數為您鎖定在下方的【⭐ 觀測雷達】中！")
                time.sleep(1) 
                st.rerun()
            else: st.warning("💡 報告總指揮：您剛剛貼的這些標的，您之前就已經通通加進去了！")
        else: st.error("🚨 找不到情報代碼！請確認格式是否正確 (需要像這樣 2610:?:?:1:?)")
st.markdown("</div>", unsafe_allow_html=True)

col_scan1, col_scan2, col_scan3 = st.columns(3)
with col_scan1:
    st.markdown("<div class='scan-btn-golden'>", unsafe_allow_html=True)
    if st.button("🚀 黃金起漲掃描\n(帶量或KDJ金叉)", use_container_width=True):
        st.markdown("</div>", unsafe_allow_html=True)
        st.info(f"⚠️ 報告總指揮：準備掃描全市場 {len(FULL_MARKET_CODES)} 檔股票，大約需要幾分鐘，請稍候。")
        progress_bar = st.progress(0)
        status_text = st.empty()
        golden_stocks = []
        total_stocks = len(FULL_MARKET_CODES)
        
        for i, sym in enumerate(FULL_MARKET_CODES):
            clean_sym = sym.strip()
            if clean_sym in st.session_state.portfolio or clean_sym in st.session_state.pinned_stocks: continue
            status_text.markdown(f"📡 深潛雷達掃描中: **{clean_sym}** ({i+1}/{total_stocks})...")
            
            d = calculate_tactical_signals(f"{clean_sym}:?:?:?:?", "scan") 
            if d and d['buy_cond_count'] >= 1 and -5.0 <= d['diff_from_cost'] <= 10.0 and d['gain'] > -2.0:
                if not any(x in d['signal'] for x in ["❌", "⏳"]): golden_stocks.append(d)
            
            progress_bar.progress(min((i + 1) / total_stocks, 1.0))
            time.sleep(0.05) 
            
        st.session_state.scan_results = golden_stocks
        st.session_state.scan_mode = "golden"
        status_text.empty()
        progress_bar.empty()
        st.rerun()
    else: st.markdown("</div>", unsafe_allow_html=True)

with col_scan2:
    st.markdown("<div class='scan-btn-stealth'>", unsafe_allow_html=True)
    if st.button("🕵️‍♂️ 底部潛伏掃描\n(盤整＋1.2倍量)", use_container_width=True):
        st.markdown("</div>", unsafe_allow_html=True)
        st.info(f"⚠️ 報告總指揮：準備掃描全市場 {len(FULL_MARKET_CODES)} 檔股票，大約需要幾分鐘，請稍候。")
        progress_bar = st.progress(0)
        status_text = st.empty()
        stealth_stocks = []
        total_stocks = len(FULL_MARKET_CODES)
        
        for i, sym in enumerate(FULL_MARKET_CODES):
            clean_sym = sym.strip()
            if clean_sym in st.session_state.portfolio or clean_sym in st.session_state.pinned_stocks: continue
            status_text.markdown(f"📡 潛水探測中: **{clean_sym}** ({i+1}/{total_stocks})...")
            
            d = calculate_tactical_signals(f"{clean_sym}:?:?:?:?", "scan")
            if d and -5.0 <= d['diff_from_cost'] <= 8.0 and d['vol_ratio'] >= 1.2:
                if not any(x in d['signal'] for x in ["❌", "⏳"]): stealth_stocks.append(d)
                
            progress_bar.progress(min((i + 1) / total_stocks, 1.0))
            time.sleep(0.05) 
            
        st.session_state.scan_results = stealth_stocks
        st.session_state.scan_mode = "stealth"
        status_text.empty()
        progress_bar.empty()
        st.rerun()
    else: st.markdown("</div>", unsafe_allow_html=True)

with col_scan3:
    st.markdown("<div class='scan-btn-yield'>", unsafe_allow_html=True)
    if st.button("🛡️ 防禦與循環掃描\n(高殖利/季節循環)", use_container_width=True):
        st.markdown("</div>", unsafe_allow_html=True)
        with st.spinner("📡 正在搜尋安全防禦地帶..."):
            yield_stocks = []
            for sym in YIELD_POOL + CYCLICAL_POOL:
                clean_sym = sym.strip()
                if clean_sym in st.session_state.portfolio or clean_sym in st.session_state.pinned_stocks: continue
                d = calculate_tactical_signals(f"{clean_sym}:?:?:?:?", "scan")
                if d and d['diff_from_cost'] >= -6.0:
                    if not any(x in d['signal'] for x in ["❌", "⏳"]):
                        d['extra_badge'] = "💰 高殖利防禦股" if clean_sym in YIELD_POOL else "🔄 景氣循環股 (要跑得快)"
                        yield_stocks.append(d)
                time.sleep(0.05) 
            st.session_state.scan_results = yield_stocks
            st.session_state.scan_mode = "yield"
        st.rerun()
    else: st.markdown("</div>", unsafe_allow_html=True)

st.markdown("<h3 style='color:#f1c40f; margin-top:10px; border-bottom: 2px solid #f1c40f; padding-bottom:5px;'>🔍 手動探測雷達</h3>", unsafe_allow_html=True)
search_query = st.text_input("📝 輸入代號或名稱 [輸入後按 Enter]：", key="search_input")

def render_stock_card(d, ui_key_prefix, is_portfolio=False, p_data=None):
    strategy_html = f"""
<div style="background:#1a1c23; border-radius:6px; padding:12px; margin-bottom:12px; border: 1px solid #333; border-left: 4px solid #3498db;">
<div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;"><span style="color:#888; font-size:13px;">目前防守底線：{d['cost_label']}</span><strong style="color:#fff; font-size:14px;">{d['cost']}</strong></div>
<div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;"><span style="color:#888; font-size:13px;">🎯 最佳入場區</span><strong style="color:{d['color']}; font-size:15px;">[ {d['buy_zone']} ]</strong></div>
<div style="display:flex; justify-content:space-between; align-items:center;"><span style="color:#888; font-size:13px;">{d['exit_s'].split('：')[0] if '：' in d['exit_s'] else d['exit_s']}</span><strong style="color:{d['exit_color']}; font-size:15px;">{d['exit_price']}</strong></div>
</div>"""

    gain_color = '#ff4d4d' if d['gain']>0 else ('#00FF00' if d['gain']<0 else '#aaaaaa')
    gain_bg = '#3a1515' if d['gain']>0 else ('#153a20' if d['gain']<0 else '#333333')
    
    price_badge = ""
    if d['is_overridden']: price_badge += "<span style='font-size:14px; background-color:#8e44ad; color:white; padding:3px 8px; border-radius:4px; margin-left:10px;'>🔧 報價已強制校正</span>"
    
    extra_badge_html = f"<span class='special-badge'>{d['extra_badge']}</span>" if d.get('extra_badge') else ""
    downgrade_html = f"<div style='background-color:#3a2515; color:#f39c12; font-size:13px; font-weight:bold; padding:6px 12px; border-radius:5px; margin-bottom:10px; border:1px solid #f39c12;'>{d['downgrade_alert']}</div>" if d.get('downgrade_alert') else ""

    portfolio_header_html = ""
    if is_portfolio and p_data:
        mode_color = "#3498db" if p_data.get('mode') == "長線價值波段單" else "#e67e22"
        target_display = f"{p_data.get('manual_target'):.1f} 元" if p_data.get('manual_target', 0.0) > 0 else "未設定"
        catalyst_html = f"<div style='color:#e056fd; font-size:13px; font-weight:bold; margin-top:5px;'>🌟 為什麼買它 (備忘)：{p_data.get('catalyst')}</div>" if p_data.get('catalyst') else ""
        
        portfolio_header_html = f"""
        <div style='background:#10141d; padding:10px; border-radius:6px; margin-bottom:12px; border:1px solid #444;'>
            <div style='display:flex; justify-content:space-between; align-items:center;'>
                <span style='background-color:{mode_color}; color:#fff; font-size:12px; padding:2px 8px; border-radius:4px; font-weight:bold;'>🎮 {p_data.get('mode')}</span>
                <span style='color:#aaa; font-size:12px;'>🎯 目標價：[ 您的預期: <strong style='color:#f1c40f;'>{target_display}</strong> | 系統估值: <strong style='color:#00d2ff;'>{d['auto_target']:.1f} 元</strong> ]</span>
            </div>
            {catalyst_html}
        </div>
        """

    html_card = f"""
<div style="border: 2px solid {d['color']}; border-radius: 8px; padding: 15px; background-color: #16191f; margin-bottom: 5px;">
{downgrade_html}
{portfolio_header_html}
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
<div style="background:{d['signal_bg']}; padding:10px; border-radius:6px; text-align:center; margin-bottom:10px; border: 1px solid {d['color']}40;"><span style="color:#aaa; font-size:12px;">⚡ 總指揮該怎麼做：</span><br><strong style="color:{d['color']}; font-size:18px;">{d['signal']}</strong></div>
</div>"""
    st.markdown(html_card, unsafe_allow_html=True)
    
    is_unknown_intel = "?" in d['raw_data']
    is_pinned = d['code'] in st.session_state.pinned_stocks
    
    with st.expander(f"🔧 1. 手動更新現在股價 ({d['name']})"):
        st.markdown("<div style='background:#2c153a; padding:10px; border-radius:5px; border-left:3px solid #9b59b6; margin-bottom:5px;'>", unsafe_allow_html=True)
        oc1, oc2, oc3 = st.columns([2, 1, 1])
        oc1.number_input("輸入您在券商看到的最新價格", value=float(d['price']), step=0.5, key=f"override_input_{ui_key_prefix}_{d['code']}", label_visibility="collapsed")
        st.markdown("<div class='override-btn'>", unsafe_allow_html=True)
        oc2.button("⚡ 重新計算", key=f"btn_override_{ui_key_prefix}_{d['code']}", use_container_width=True, on_click=cb_override_price, args=(d['code'], ui_key_prefix))
        st.markdown("</div>", unsafe_allow_html=True)
        if d['is_overridden']: oc3.button("🔄 恢復自動抓取", key=f"btn_clear_ov_{ui_key_prefix}_{d['code']}", use_container_width=True, on_click=cb_clear_override, args=(d['code'],))
        st.markdown("</div>", unsafe_allow_html=True)

    with st.expander(f"📌 2. 情報參數與放入觀測雷達 ({d['name']})"):
        if is_unknown_intel: st.markdown("<div style='color:#f39c12; font-size:13px; font-weight:bold; margin-bottom:10px;'>⚠️ 系統不知道這檔的細節！請向 CEO 拿到情報密碼後設定。</div>", unsafe_allow_html=True)
        new_shd, new_chip, new_val = "4", "0", "0"
        if is_unknown_intel:
            ic1, ic2, ic3 = st.columns(3)
            new_shd = ic1.selectbox("防護盾分數", ["1", "2", "3", "4", "5", "?"], index=5, key=f"ishd_{ui_key_prefix}_{d['code']}")
            new_chip = ic2.selectbox("籌碼好壞", ["0", "1", "2", "?"], index=3, format_func=lambda x: CHIP_MAP[x][:5], key=f"ichip_{ui_key_prefix}_{d['code']}")
            new_val = ic3.selectbox("目前貴不貴", ["0", "1", "2", "3", "?"], index=4, format_func=lambda x: VAL_MAP[x][:5], key=f"ival_{ui_key_prefix}_{d['code']}")
        else:
            parts = d['raw_data'].split(":")
            new_shd = parts[1] if len(parts)>1 else "?"
            new_chip = parts[3] if len(parts)>3 else "?"
            new_val = parts[4] if len(parts)>4 else "?"

        compiled_raw_data = f"{d['code']}:{new_shd}:0:{new_chip}:{new_val}:0"
        if not is_pinned:
            st.markdown("<div class='pin-btn'>", unsafe_allow_html=True)
            st.button(f"📌 把這檔加進觀測雷達", key=f"pinbtn_{ui_key_prefix}_{d['code']}", use_container_width=True, on_click=cb_pin_stock, args=(d['code'], compiled_raw_data, d['cat']))
            st.markdown("</div>", unsafe_allow_html=True)
        else:
            st.markdown("<div class='unpin-btn'>", unsafe_allow_html=True)
            st.button(f"❌ 從雷達中刪除", key=f"unpinbtn_{ui_key_prefix}_{d['code']}", use_container_width=True, on_click=cb_unpin_stock, args=(d['code'],))
            st.markdown("</div>", unsafe_allow_html=True)

    with st.expander(f"💼 3. 算算看風險，準備買進 ({d['name']})"):
        c1, c2 = st.columns(2)
        sim_cost = c1.number_input("您預計買進的價格", value=float(d['price']), key=f"c_{ui_key_prefix}_{d['code']}")
        sim_qty = c2.number_input("打算買幾張", value=1.0, key=f"q_{ui_key_prefix}_{d['code']}")
        
        st.markdown("<div style='background:#10141d; padding:10px; border-radius:5px; margin-bottom:10px;'>", unsafe_allow_html=True)
        v_mode = st.selectbox("🎯 您想做短線還是長線？", ["短線技術動能單", "長線價值波段單"], key=f"mode_{ui_key_prefix}_{d['code']}")
        v_target = st.number_input("🎯 您心中的目標價是多少？ (如果不填，系統會自動幫您抓歷史高點)", value=0.0, step=1.0, key=f"tval_{ui_key_prefix}_{d['code']}")
        v_cat = st.text_input("🌟 為什麼買它？自己寫個備忘 (如：營收大爆發、吃到蘋果訂單)", key=f"cat_{ui_key_prefix}_{d['code']}")
        st.markdown(f"<span style='color:#00d2ff; font-size:12px;'>🤖 系統自動算出來的目標價為：<strong>{d['auto_target']:.1f} 元</strong></span></div>", unsafe_allow_html=True)

        exit_val = safe_parse_float(d['exit_price'], default=d['price'])
        risk_loss, risk_pct = calc_real_profit(sim_cost, exit_val, sim_qty)
        risk_bar_pct = min(100, max(0, abs(risk_pct) * 5))
        st.markdown(f"""
        <div style='background:#3a1515; padding:8px; border-radius:5px; border-left:3px solid #e74c3c; margin-top:5px; margin-bottom:10px; font-size:12px; color:#ddd;'>
            🛡️ 戰前警告：如果運氣不好，買完跌到了要逃命的價格({exit_val})，您最多大概會賠掉 <strong style='color:#e74c3c;'>{risk_loss:,.0f} 元 ({risk_pct:.1f}%)</strong>，請確認心臟能不能承受！
            <div class='health-bar-bg'><div class='health-bar-fill-red' style='width: {risk_bar_pct}%;'></div></div>
        </div>
        """, unsafe_allow_html=True)
        
        parts = d['raw_data'].split(":")
        new_shd = parts[1] if len(parts)>1 else "?"
        new_chip = parts[3] if len(parts)>3 else "?"
        new_val = parts[4] if len(parts)>4 else "?"
        compiled_raw_data = f"{d['code']}:{new_shd}:0:{new_chip}:{new_val}:0"
        
        st.markdown("<div class='buy-btn'>", unsafe_allow_html=True)
        st.button(f"⚡ 我決定買了！加入庫存", key=f"buy_{ui_key_prefix}_{d['code']}", use_container_width=True, on_click=cb_buy_stock, args=(d['code'], compiled_raw_data, d['cat'], ui_key_prefix))
        st.markdown("</div>", unsafe_allow_html=True)

if search_query:
    with st.spinner("📡 正在幫您查這檔股票..."):
        clean_code = re.split(r'[,\s、，]+', search_query)[0].replace('.TW', '').replace('.TWO', '')
        REV_TW_STOCKS = {v: k for k, v in TW_STOCKS.items()}
        code_to_scan = REV_TW_STOCKS.get(clean_code, clean_code)

        symbol_data = f"{code_to_scan}:?:?:?:?"
        d = calculate_tactical_signals(symbol_data, "search")
        if d:
            if d['code'] not in st.session_state.portfolio and d['code'] not in st.session_state.pinned_stocks:
                cols = st.columns(2)
                with cols[0]: render_stock_card(d, ui_key_prefix="search_res")
            else: st.warning(f"💡 提醒您：【{d['name']} ({d['code']})】已經在您的雷達或庫存裡面囉！")
        else: st.error(f"🚨 找不到這檔股票：【{code_to_scan}】。請確認代號是不是打錯了。")

def render_portfolio_card(code, p_data):
    d = calculate_tactical_signals(p_data['raw_data'], p_data['cat'], mode=p_data.get('mode', '短線技術動能單'), manual_target=p_data.get('manual_target', 0.0))
    if not d: return 
    entry_price = p_data['entry_price']
    qty = p_data['qty']
    real_profit, real_roi = calc_real_profit(entry_price, d['price'], qty)
    
    is_hard_stop = real_roi <= -10.0
    p_color = '#e74c3c' if is_hard_stop else ('#ff4d4d' if real_profit > 0 else '#00FF00')
    border_style = f"4px solid {p_color}" if is_hard_stop else f"3px solid {p_color}"
    bg_color = "#3a1515" if is_hard_stop else "#1a1a24"
    
    stop_warning = "<div class='my-tooltip' style='background:#e74c3c; color:#fff; font-weight:bold; text-align:center; padding:8px; border-radius:5px; margin-bottom:10px; display:block; width:100%;'>🚨 已經跌超過 10%，請嚴格執行停損逃命！🚨</div>" if is_hard_stop else ""
    
    p_html = f"""<div style="border: {border_style}; border-radius: 8px; padding: 15px; background-color: {bg_color}; margin-bottom: 5px; box-shadow: 0 0 15px {p_color}40;">
{stop_warning}
"""
    st.markdown(p_html, unsafe_allow_html=True)
    
    render_stock_card(d, ui_key_prefix=f"port_{code}", is_portfolio=True, p_data=p_data)
    
    p_footer_html = f"""
<div style="display:flex; justify-content:space-between; margin-bottom: 15px;"><div style="color:#aaa;">您買進的成本: <strong style="color:#fff;">{entry_price:.2f}</strong></div><div style="color:#aaa;">您買了幾張: <strong style="color:#fff;">{qty}</strong></div></div>
<div style="background:#000; padding:15px; border-radius:8px; text-align:center; margin-bottom:15px; display:block; width:100%;"><div style="color:#aaa; font-size:14px; margin-bottom:5px;">💰 帳面上目前賺賠</div><div style="font-size:36px; font-weight:bold; color:{p_color};">{real_profit:+,.0f} 元</div><div style="font-size:18px; color:{p_color};">({real_roi:+.2f}%)</div></div></div>"""
    st.markdown(p_footer_html, unsafe_allow_html=True)
    
    st.markdown("<div class='sell-btn'>", unsafe_allow_html=True)
    st.button(f"🚪 賣出清空 (從庫存移除)", key=f"sell_{code}", on_click=cb_sell_stock, args=(code,))
    st.markdown("</div>", unsafe_allow_html=True)

    with st.expander(f"🔧 手動更新這檔股價 ({code})"):
        st.markdown("<div style='background:#2c153a; padding:10px; border-radius:5px; border-left:3px solid #9b59b6; margin-bottom:15px;'>", unsafe_allow_html=True)
        oc1, oc2, oc3 = st.columns([2, 1, 1])
        oc1.number_input("輸入您在券商看到的最新價格", value=float(d['price']), step=0.5, key=f"override_input_port_{code}", label_visibility="collapsed")
        st.markdown("<div class='override-btn'>", unsafe_allow_html=True)
        oc2.button("⚡ 重新計算", key=f"btn_override_port_{code}", use_container_width=True, on_click=cb_override_price, args=(code, "port"))
        st.markdown("</div>", unsafe_allow_html=True)
        if d['is_overridden']: oc3.button("🔄 恢復自動抓取", key=f"btn_clear_ov_port_{code}", use_container_width=True, on_click=cb_clear_override, args=(code,))
        st.markdown("</div>", unsafe_allow_html=True)

if st.session_state.portfolio:
    st.markdown(f"<h2 style='color:#ff4d4d; margin-top:20px; border-bottom: 2px solid #ff4d4d; padding-bottom:5px;'>💼 總指揮的作戰庫存 ({len(st.session_state.portfolio)}/{MAX_CAPACITY})</h2>", unsafe_allow_html=True)
    cols = st.columns(2)
    for i, (code, p_data) in enumerate(list(st.session_state.portfolio.items())):
        with cols[i % 2]: render_portfolio_card(code, p_data)

if st.session_state.pinned_stocks:
    st.markdown(f"<h2 style='color:#f1c40f; margin-top:20px; border-bottom: 2px solid #f1c40f; padding-bottom:5px;'>⭐ 放在旁邊觀察的雷達 ({len(st.session_state.pinned_stocks)}/{MAX_CAPACITY})</h2>", unsafe_allow_html=True)
    cols = st.columns(2)
    for i, (code, p_data) in enumerate(list(st.session_state.pinned_stocks.items())):
        if code in st.session_state.portfolio: continue
        d = calculate_tactical_signals(p_data['raw_data'], p_data['cat'])
        if d:
            with cols[i % 2]: render_stock_card(d, ui_key_prefix="pinned")

if st.session_state.get('scan_results') is not None:
    visible_results = [d for d in st.session_state.scan_results if d['code'] not in st.session_state.portfolio and d['code'] not in st.session_state.pinned_stocks]
    
    if len(st.session_state.scan_results) > 0 and len(visible_results) == 0:
        st.markdown("<h4 style='color:#00FF00; margin-top:30px; text-align:center;'>📡 剛剛掃描出來的好股票，您都已經存起來囉！</h4>", unsafe_allow_html=True)
    elif len(st.session_state.scan_results) == 0:
        st.markdown("<h4 style='color:#f39c12; margin-top:30px; text-align:center;'>📡 報告總指揮：目前沒有符合條件的股票。</h4>", unsafe_allow_html=True)
    else:
        if st.session_state.get('scan_mode') == 'golden':
            st.markdown("<h2 style='color:#00FF00; margin-top:30px; border-bottom: 2px solid #00FF00; padding-bottom:5px;'>⚡ 準備往上衝的股票 (黃金起漲)</h2>", unsafe_allow_html=True)
        elif st.session_state.get('scan_mode') == 'stealth':
            st.markdown("<h2 style='color:#00d2ff; margin-top:30px; border-bottom: 2px solid #00d2ff; padding-bottom:5px;'>🕵️‍♂️ 剛睡醒準備發動的股票 (底部潛伏)</h2>", unsafe_allow_html=True)
        elif st.session_state.get('scan_mode') == 'yield':
            st.markdown("<h2 style='color:#e056fd; margin-top:30px; border-bottom: 2px solid #9b59b6; padding-bottom:5px;'>🛡️ 比較安全的防禦型股票 (高殖利/循環股)</h2>", unsafe_allow_html=True)
            
        cols = st.columns(2)
        for i, d in enumerate(visible_results):
            with cols[i % 2]: render_stock_card(d, ui_key_prefix="scan_res")

st.markdown("<h2 style='color:#3498db; margin-top:40px; border-bottom: 2px solid #3498db; padding-bottom:5px;'>📤 呼叫 CEO：幫我分析這些股票</h2>", unsafe_allow_html=True)
st.markdown("<p style='color:#ccc; font-size:14px;'>點一下下面代碼框右上角的「複製」按鈕，直接貼到聊天室給我，我來幫您做深度分析！</p>", unsafe_allow_html=True)

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
    st.info("📌 目前雷達或掃描區是空的，請先隨便找一檔股票加入雷達。")

st.markdown("---")
with st.expander("📘 給總指揮的小抄 (暗號本)"):
    st.markdown("""
    在聊天室直接輸入以下指令，我會立刻幫您處理：
    * **`指令1`**：每天收盤後，幫我全面掃描一次全市場
    * **`指令2`**：幫我找抗跌安全的高殖利率股票
    * **`指令3`**：幫我找大戶正在偷偷買進的股票
    * **`指令4 [代號]`**：幫我把這檔股票的祖宗十八代都查清楚
    * **`指令5`**：告訴我哪些股票漲太瘋快被關禁閉了
    """)
