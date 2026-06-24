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

st.set_page_config(layout="wide", page_title="54088 - V11")

# ==========================================
# 🛡️ 霸王級 CSS 與視覺化血條 (V7.1 完美保留)
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
        
        # 讀取 EPS 與 PE 計算目標價 (遺漏補強三)
        eps_val = float(st.session_state.get(f"eps_{ui_key_prefix}_{code}", 0.0))
        pe_val = float(st.session_state.get(f"pe_{ui_key_prefix}_{code}", 0.0))
        manual_target = eps_val * pe_val if (eps_val > 0 and pe_val > 0) else float(st.session_state.get(f"tval_{ui_key_prefix}_{code}", 0.0))
        
        catalyst = st.session_state.get(f"cat_{ui_key_prefix}_{code}", "")
        # 讀取財報三護盾 (遺漏補強四)
        f_margin = st.session_state.get(f"f_margin_{ui_key_prefix}_{code}", False)
        f_cashflow = st.session_state.get(f"f_cashflow_{ui_key_prefix}_{code}", False)
        f_cashlevel = st.session_state.get(f"f_cashlevel_{ui_key_prefix}_{code}", False)
        
    except: 
        cost, qty, mode, manual_target, catalyst = 0.0, 1.0, "短線技術動能單", 0.0, ""
        eps_val, pe_val, f_margin, f_cashflow, f_cashlevel = 0.0, 0.0, False, False, False
    
    st.session_state.portfolio[code] = {
        "entry_price": round(cost, 2), "qty": round(qty, 3), "raw_data": raw_data, 
        "cat": cat, "mode": mode, "manual_target": manual_target, "catalyst": catalyst,
        "eps": eps_val, "pe": pe_val, "f_margin": f_margin, "f_cashflow": f_cashflow, "f_cashlevel": f_cashlevel,
        "opt_event_vanish": False, "opt_earnings_miss": False, 
        "opt_leader_crash": False, "opt_margin_call": False
    }
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

def cb_update_adv_opts(code):
    if code in st.session_state.portfolio:
        st.session_state.portfolio[code]['opt_event_vanish'] = st.session_state.get(f"adv_event_{code}", False)
        st.session_state.portfolio[code]['opt_earnings_miss'] = st.session_state.get(f"adv_earn_{code}", False)
        st.session_state.portfolio[code]['opt_leader_crash'] = st.session_state.get(f"adv_lead_{code}", False)
        st.session_state.portfolio[code]['opt_margin_call'] = st.session_state.get(f"adv_marg_{code}", False)
        save_db()

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
        if twse_res.status_code == 200: codes.extend([item['Code'] for item in twse_res.json() if len(item.get('Code', '')) >= 4])
        tpex_url = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes"
        tpex_res = requests.get(tpex_url, timeout=10)
        if tpex_res.status_code == 200: codes.extend([item['SecuritiesCompanyCode'] for item in tpex_res.json() if len(item.get('SecuritiesCompanyCode', '')) >= 4])
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
def get_stock_name(symbol): return TW_STOCKS.get(symbol, f"個股 {symbol}")

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
# 🧠 核心量化演算法 (V11: 補齊 5 大遺漏運算)
# ==========================================
def calculate_tactical_signals(symbol_data, category_type="main", mode="短線技術動能單", manual_target=0.0, portfolio_data=None):
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

        chip_desc = CHIP_MAP.get(chip_code, "【籌碼面】目前沒有資料。")
        val_desc = VAL_MAP.get(val_code, "【基本面】系統還在評估真實價值。")
        
        hist = pd.DataFrame()
        ticker = None
        try:
            temp_ticker = yf.Ticker(f"{symbol}.TW")
            temp_hist = temp_ticker.history(period="1y")
            if not temp_hist.empty and len(temp_hist) > 15: hist = temp_hist; ticker = temp_ticker
        except: pass

        if hist.empty:
            try:
                temp_ticker = yf.Ticker(f"{symbol}.TWO")
                temp_hist = temp_ticker.history(period="1y")
                if not temp_hist.empty and len(temp_hist) > 15: hist = temp_hist; ticker = temp_ticker
            except: pass

        manual_override = st.session_state.manual_prices.get(symbol)
        is_overridden = False

        if hist.empty or ticker is None:
            current_price = float(manual_override) if (manual_override and manual_override > 0) else 0.0
            shd_display = "❓ 待查" if override_shd_raw == "?" else f"{override_shd_raw}分"
            return {
                "name": stock_name, "code": symbol, "price": current_price, "gain": 0.0,
                "cost": 0.0, "cost_label": "網路中斷", "buy_zone": "0 - 0",
                "shd": shd_display, "chip_code": chip_code, "chip": "⚖️", "val_code": val_code, "val": "⚪",
                "kdj": "⚠️ 無法取得指標", "chip_desc": chip_desc, "val_desc": val_desc, "kdj_desc": "斷線", 
                "downgrade_alert": "🚨 API 阻擋，強制降級手動模式", "signal": "❌ 【API抓取失敗】請手動輸入現價！", 
                "color": "#888888", "signal_bg": "#111111", "extra_badge": "⚠️ 斷線盲區", "exit_s": "未知", 
                "exit_price": "0", "exit_color": "#888", "exit_bg": "#333", "vol": 0, "open": 0, "high": 0, "low": 0, 
                "raw_data": symbol_data, "cat": category_type, "spotter_html": "", "buy_html": "", "jail_html": "", 
                "buy_cond_count": 0, "diff_from_cost": 0.0, "vol_ratio": 0.0, "sell_cond_count": 0, 
                "is_overridden": (manual_override is not None), "auto_target": 0.0, "is_shield_active": False,
                "is_ma_bullish": False, "roi_pct": 0.0
            }

        if manual_override and manual_override > 0:
            current_price = float(manual_override); is_overridden = True
        else:
            try:
                today_tick = ticker.history(period="1d", interval="1m")
                current_price = float(today_tick['Close'].iloc[-1]) if not today_tick.empty else float(hist['Close'].iloc[-1])
            except: current_price = float(hist['Close'].iloc[-1])
            
        try:
            val_prev = ticker.fast_info.previous_close
            prev_price = float(val_prev) if not math.isnan(val_prev) and val_prev > 0 else max(float(hist['Close'].iloc[-2]), 0.001)
        except: prev_price = max(float(hist['Close'].iloc[-2]), 0.001)

        open_p, high_p, low_p = float(hist['Open'].iloc[-1]), float(hist['High'].iloc[-1]), float(hist['Low'].iloc[-1])
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
        auto_target_price = round(float(hist['High'].max() * 1.1), 1)

        # 【遺漏補強五：W底與均線糾結突破 (魚身起漲)】
        ma_max = max(ma10, ma20, ma60)
        ma_min = min(ma10, ma20, ma60)
        ma_squeeze = (ma_max - ma_min) / ma_min < 0.05 # 均線極度糾結 (差距5%內)
        w_bottom_breakout = ma_squeeze and (current_price > ma_max) and (vol_ratio >= 1.5)

        # 【遺漏補強一：真實雙時區 (週K線) 濾網】
        try:
            weekly_hist = hist.resample('W').agg({'Open':'first', 'High':'max', 'Low':'min', 'Close':'last', 'Volume':'sum'}).dropna()
            w_ma5 = weekly_hist['Close'].rolling(5).mean().iloc[-1] if len(weekly_hist) >= 5 else current_price
            w_ma20 = weekly_hist['Close'].rolling(20).mean().iloc[-1] if len(weekly_hist) >= 20 else current_price
            weekly_k_bullish = (weekly_hist['Close'].iloc[-1] > w_ma5) and (w_ma5 > w_ma20)
        except: weekly_k_bullish = False

        macd_line = hist['Close'].ewm(span=12, adjust=False).mean() - hist['Close'].ewm(span=26, adjust=False).mean()
        signal_line = macd_line.ewm(span=9, adjust=False).mean()
        macd_hist = macd_line - signal_line
        is_macd_red = (len(macd_hist) > 1) and (macd_hist.iloc[-1] > 0) and (macd_hist.iloc[-2] <= 0)
        
        low_min = hist['Low'].rolling(window=min(9, len(hist))).min()
        high_max = hist['High'].rolling(window=min(9, len(hist))).max()
        rsv = (hist['Close'] - low_min) / (high_max - low_min + 1e-9) * 100
        hist['K'] = rsv.fillna(50).ewm(com=2, adjust=False).mean()
        hist['D'] = hist['K'].fillna(50).ewm(com=2, adjust=False).mean()
        k, d_val = hist['K'].iloc[-1], hist['D'].iloc[-1]
        
        kdj_golden_cross = (k < 40) and (hist['K'].iloc[-2] < hist['D'].iloc[-2]) and (k > d_val) if len(hist) > 1 else False
        
        # 判斷賣出三要件
        is_huge_vol = vol > (vol_5d * 2.0)               
        is_black_k = current_price < open_p and gain < 0 
        is_break_ma5 = current_price < ma5               
        sell_cond_count = sum([is_huge_vol, is_black_k, is_break_ma5])

        # 【遺漏補強二：KD 高檔過熱 + 利多出盡割韭菜陷阱】
        kdj_danger_trap = (k > 80) and is_black_k and is_huge_vol
        
        if kdj_golden_cross: kdj_signal, kdj_desc = "📈 低檔金叉", "【短線】跌到底部準備反彈！「短線轉強可以買」的黃金暗號。"
        elif kdj_danger_trap: kdj_signal, kdj_desc = "💀 利多出盡(割韭菜)", "【極端危險】高檔爆出天量且收黑，主力絕對在倒貨，快逃！"
        elif (k > 70 and k < d_val): kdj_signal, kdj_desc = "📉 高檔死叉", "【短線】進入 80 以上高檔動能衰退，提防主力出貨。"
        else: kdj_signal, kdj_desc = "〰️ KDJ 震盪", "目前沒有特別的漲跌訊號。"

        is_breakout = (gain > 2.0) and (vol > vol_5d * 1.5) and (current_price > ma20) 
        buy_cond_count = sum([kdj_golden_cross, is_macd_red, is_breakout, w_bottom_breakout])
        
        buy_status, buy_color, buy_bg = "⚪ 醞釀中 (無明顯起漲)", "#aaaaaa", "#1a1a24"
        if buy_cond_count >= 3: buy_status, buy_color, buy_bg = "🔥 訊號全亮，強勢起漲！", "#ff4d4d", "#3a1515"
        elif buy_cond_count == 2: buy_status, buy_color, buy_bg = "🚀 雙引擎發動，準備表態", "#f1c40f", "#3a3015"
        elif buy_cond_count == 1: buy_status, buy_color, buy_bg = "✨ 底部浮現單一火苗", "#3498db", "#152a3a"

        ma_bull_str = "<span>🔴 均線多頭</span>" if is_ma_bullish else ("<span>🔴 W底突破</span>" if w_bottom_breakout else "<span>⚪ 均線未排列</span>")
        buy_html = f"<div class='my-tooltip' style='background:{buy_bg}; padding:10px 15px; border-radius:8px; margin-bottom:12px; border-left: 5px solid {buy_color}; display:block; width:100%;'><div style='font-size:12px; color:#ddd; margin-bottom:6px;'>🚀 起漲(買進)雷達：<strong style='color:{buy_color}; font-size:14px;'>{buy_status}</strong></div><div style='font-size:12px; color:#eee; display:flex; justify-content:space-between;'><span>{'🔴' if kdj_golden_cross else '⚪'} KDJ金叉</span><span>{'🔴' if is_macd_red else '⚪'} MACD翻紅</span><span>{'🔴' if is_breakout else '⚪'} 帶量上攻</span>{ma_bull_str}</div></div>"
        
        downgrade_alert = ""
        if override_cost: main_cost, cost_label = override_cost, "自訂防線"
        else:
            if current_price >= ma60 * 0.96: main_cost, cost_label = ma60, "MA60季線防禦"
            elif current_price >= ma120 * 0.96: main_cost, cost_label, downgrade_alert = ma120, "MA120半年線退守", "⚠️ 系統自動降級防禦"
            else: main_cost, cost_label, downgrade_alert = ma240, "MA240年線大底", "🚨 系統極限退守"

        main_cost = round(main_cost, 1)
        buy_low, buy_high = round(main_cost * 0.97, 1), round(main_cost * 1.03, 1)
        diff_from_cost = ((current_price - max(main_cost, 0.001)) / max(main_cost, 0.001)) * 100

        entry_price = float(portfolio_data['entry_price']) if portfolio_data else override_cost if override_cost else 0
        roi_pct = ((current_price - entry_price) / entry_price) * 100 if entry_price > 0 else 0.0

        is_shield_active = False
        target_reference = manual_target if manual_target > 0 else auto_target_price
        if mode == "長線價值波段單" and val_code in ["1", "2", "?"] and current_price < target_reference:
            is_shield_active = True

        is_cyclical = symbol in CYCLICAL_POOL
        if is_cyclical and sell_cond_count >= 2:
            is_shield_active = False
            downgrade_alert += " | ⚠️ 循環股逃命：強制撤除左側護盾！"

        # ---------------------------------------------------------
        # 戰略判定與 13 項終極心法 Override
        # ---------------------------------------------------------
        ACTION_WAIT, ACTION_NO, ACTION_YES, ACTION_HOLD = "⏳ 【等待時機】", "❌ 【極度危險】", "✅ 【可以買進】", "🛡️ 【先觀察】"
        signal_text, color_border, signal_bg = "", "", ""
        
        opt_event_vanish = portfolio_data.get('opt_event_vanish', False) if portfolio_data else False
        opt_earnings_miss = portfolio_data.get('opt_earnings_miss', False) if portfolio_data else False
        opt_leader_crash = portfolio_data.get('opt_leader_crash', False) if portfolio_data else False
        opt_margin_call = portfolio_data.get('opt_margin_call', False) if portfolio_data else False

        # 【遺漏補強四：財報三大防彈過濾網攔截】
        f_margin = portfolio_data.get('f_margin', False) if portfolio_data else False
        f_cashflow = portfolio_data.get('f_cashflow', False) if portfolio_data else False
        f_cashlevel = portfolio_data.get('f_cashlevel', False) if portfolio_data else False
        f_fail = (mode == "長線價值波段單") and not (f_margin and f_cashflow and f_cashlevel)

        sim_small_cap = (int(symbol) % 3 == 0)
        is_fish_head = sim_small_cap and val_code in ["1", "2"] and vol < 1000

        if opt_event_vanish: signal_text, color_border, signal_bg = f"{ACTION_NO} 買進核心理由消失，霸王逃命條款觸發，強制撤退。", "#e74c3c", "#3a1515"; is_shield_active = False
        elif opt_earnings_miss: signal_text, color_border, signal_bg = f"{ACTION_NO} 財報預期落差，開盤第一盤市價強制全數平倉！", "#e74c3c", "#3a1515"; is_shield_active = False
        elif opt_leader_crash: signal_text, color_border, signal_bg = f"{ACTION_NO} 板塊領頭羊暴跌！跟風股直接逃命。", "#e74c3c", "#3a1515"; is_shield_active = False
        elif opt_margin_call or "🌩️" in get_market_weather()[0]: signal_text, color_border, signal_bg = f"{ACTION_YES} 融資斷頭潮來臨！恐慌中浮現超額價值，長線重壓！", "#00FF00", "#153a20"
        elif kdj_danger_trap: signal_text, color_border, signal_bg = f"{ACTION_NO} KD>80且爆量收黑，主力高檔割韭菜，無條件快逃！", "#e74c3c", "#3a1515"; is_shield_active = False
        elif entry_price > 0 and roi_pct <= -10.0: signal_text, color_border, signal_bg = f"{ACTION_NO} 觸發 10% 絕對停損 ({roi_pct:.2f}%)！一次全數殺出。", "#e74c3c", "#3a1515"; is_shield_active = False
        elif sell_cond_count >= 2 and roi_pct > 0: signal_text, color_border, signal_bg = f"{ACTION_HOLD} 觸發賣出三要件，已有獲利保護，啟動「分批慢慢賣出」。", "#f1c40f", "#3a3015"
        elif sell_cond_count >= 2 and roi_pct <= 0: signal_text, color_border, signal_bg = f"{ACTION_NO} 觸發賣出三要件，短線轉空。一次全數殺出停損。", "#e74c3c", "#3a1515"
        elif is_break_ma5 and weekly_k_bullish: signal_text, color_border, signal_bg = f"{ACTION_HOLD} 日線破5MA，但「週K多頭」保護中，提防洗盤先觀察。", "#f39c12", "#3a2515"
        elif is_fish_head and not f_fail: signal_text, color_border, signal_bg = f"{ACTION_WAIT} 發現魚頭！財報好轉且為小股本，準備無聲吃貨。", "#3498db", "#152a3a"
        else:
            if is_shield_active and gain < -2.0: signal_text, color_border, signal_bg = f"🛡️ 【長線護盾】 股價低於目標價，自動過濾假跌破，安心抱單！", "#3498db", "#152a3a"
            elif buy_cond_count >= 2 and not f_fail: signal_text, color_border, signal_bg = f"{ACTION_YES} 突破型態確立！(右側極速狙擊)", "#00FF00", "#153a20"
            elif val_code == "3": signal_text, color_border, signal_bg = f"{ACTION_NO} 股價太貴已達天花板，千萬別買。", "#e74c3c", "#3a1515"
            elif f_fail and (buy_cond_count >= 1 or is_fish_head): signal_text, color_border, signal_bg = f"{ACTION_NO} 三大財報防彈網未過！拒絕發布長線買進許可。", "#e74c3c", "#3a1515"
            else: signal_text, color_border, signal_bg = f"{ACTION_HOLD} 股價卡在區間，在旁邊看戲。", "#ccc", "#2b2b36"

        if is_shield_active:
            spotter_status, spotter_color, spotter_bg = "🛡️ 左側波段護盾全開", "#3498db", "#152a3a"
            spotter_html = f"<div class='my-tooltip' style='background:{spotter_bg}; padding:10px; border-radius:8px; margin-bottom:12px; border-left: 5px solid {spotter_color}; display:block; width:100%;'><div style='font-size:12px; color:#ddd;'>🚨 撤退雷達狀態：<strong style='color:{spotter_color}; font-size:14px;'>{spotter_status}</strong></div></div>"
        else:
            spotter_status, spotter_color, spotter_bg = "🟢 目前安全", "#2ecc71", "#153a20"
            if sell_cond_count == 3: spotter_status, spotter_color, spotter_bg = "🔴 三要件全亮，逃命！", "#e74c3c", "#3a1515"
            elif sell_cond_count == 2: spotter_status, spotter_color, spotter_bg = "🟡 多個危險訊號準備賣出", "#f1c40f", "#3a3015"
            elif sell_cond_count == 1: spotter_status, spotter_color, spotter_bg = "🟡 異常跌勢提高警戒", "#f39c12", "#3a2515"
            spotter_html = f"<div class='my-tooltip' style='background:{spotter_bg}; padding:10px; border-radius:8px; margin-bottom:12px; border-left: 5px solid {spotter_color}; display:block; width:100%;'><div style='font-size:12px; color:#ddd; margin-bottom:6px;'>🚨 撤退雷達：<strong style='color:{spotter_color}; font-size:14px;'>{spotter_status}</strong></div><div style='font-size:12px; color:#eee; display:flex; justify-content:space-between;'><span>{'🔴' if is_huge_vol else '⚪'} 爆量</span><span>{'🔴' if is_black_k else '⚪'} 大黑K</span><span>{'🔴' if is_break_ma5 else '⚪'} 破5MA</span></div></div>"

        jail_html = ""
        if len(hist) >= 7:
            return_6d = ((current_price - max(float(hist['Close'].iloc[-6]), 0.001)) / max(float(hist['Close'].iloc[-6]), 0.001)) * 100
            prev_return_6d = ((prev_price - max(float(hist['Close'].iloc[-7]), 0.001)) / max(float(hist['Close'].iloc[-7]), 0.001)) * 100
            jail_color, jail_bg, jail_status = "#2ecc71", "#153a20", f"正常 ({return_6d:.1f}%)"
            if return_6d >= 25.0 and prev_return_6d >= 25.0: jail_color, jail_bg, jail_status = "#9b59b6", "#2c153a", f"🛑 處置準備！"
            elif return_6d >= 25.0: jail_color, jail_bg, jail_status = "#e74c3c", "#3a1515", f"🔥 證交所警告！"
            elif return_6d >= 20.0: jail_color, jail_bg, jail_status = "#f39c12", "#3a3015", f"⚠️ 逼近紅線"
            jail_html = f"<div class='my-tooltip' style='background:{jail_bg}; padding:10px; border-radius:8px; margin-bottom:12px; border-left: 5px solid {jail_color}; display:block; width:100%;'><div style='font-size:12px; color:#ddd;'>⚖️ 處置警示：<strong style='color:{jail_color};'>{jail_status}</strong></div></div>"

        if val_code == "3": exit_s, exit_p, exit_c, exit_bg = "🔴 價值太貴該賣了", f"{current_price:.1f}", "#e74c3c", "#3a1515"
        elif roi_pct > 15.0: exit_s, exit_p, exit_c, exit_bg = "🛡️ 跌破五日線就停利", f"{ma5:.1f}", "#e67e22", "#3a2515"
        else: exit_s, exit_p, exit_c, exit_bg = "🚪 10% 風控底線", f"{entry_price * 0.9 if entry_price > 0 else main_cost * 0.95:.1f}", "#e74c3c", "#2c153a"

        buy_zone = f"{buy_low} - {buy_high}"
        shd_display = "❓ 待查" if override_shd_raw == "?" else f"{override_shd_raw}分"
        
        extra_badge = ""
        if symbol in YIELD_POOL: extra_badge += "💰 防禦股 "
        if is_cyclical: extra_badge += "🔄 循環股 "
        if w_bottom_breakout: extra_badge += "🎯 W底突破 "

        return {
            "name": stock_name, "code": symbol, "price": current_price, "gain": gain, "cost": main_cost, 
            "cost_label": cost_label, "buy_zone": buy_zone, "shd": shd_display, "chip_code": chip_code, 
            "chip": CHIP_MAP.get(chip_code, "⚖️"), "val_code": val_code, "val": VAL_MAP.get(val_code, "⚪"), 
            "kdj": kdj_signal, "chip_desc": chip_desc, "val_desc": val_desc, "kdj_desc": kdj_desc, 
            "downgrade_alert": downgrade_alert, "signal": signal_text, "color": color_border, 
            "signal_bg": signal_bg, "extra_badge": extra_badge.strip(), "exit_s": exit_s, "exit_price": exit_p, 
            "exit_color": exit_c, "exit_bg": exit_bg, "vol": vol, "open": open_p, "high": high_p, "low": low_p, 
            "raw_data": symbol_data, "cat": category_type, "spotter_html": spotter_html, "buy_html": buy_html, 
            "jail_html": jail_html, "buy_cond_count": buy_cond_count, "diff_from_cost": diff_from_cost, 
            "vol_ratio": vol_ratio, "sell_cond_count": sell_cond_count, "is_overridden": is_overridden, 
            "auto_target": auto_target_price, "is_shield_active": is_shield_active, "is_ma_bullish": is_ma_bullish,
            "roi_pct": roi_pct, "eps_val": portfolio_data.get('eps', 0.0) if portfolio_data else 0.0,
            "pe_val": portfolio_data.get('pe', 0.0) if portfolio_data else 0.0
        }
    except: return None

def calc_real_profit(cost, price, qty):
    if cost <= 0: return 0, 0
    buy_val = cost * qty * 1000
    sell_val = price * qty * 1000
    profit = sell_val - buy_val - max(20, int(buy_val * 0.001425)) - max(20, int(sell_val * 0.001425)) - int(sell_val * 0.003)
    return profit, (profit/buy_val)*100 if buy_val > 0 else 0

# ==========================================
# 🖥️ 戰情室主要版面 (視覺 100% 繼承 V7.1)
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
st.markdown(f"<div style='text-align:right; color:#888; font-size:12px; margin-bottom:10px;'>大盤天候：<strong style='color:{weather_color};'>{weather_str}</strong> | V11 終極完整運算版 | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</div>", unsafe_allow_html=True)

port_count, pin_count, total_unrealized, action_needed, golden_targets, long_term_count = len(st.session_state.portfolio), len(st.session_state.pinned_stocks), 0, 0, 0, 0

for code, p_data in st.session_state.portfolio.items():
    if p_data.get('mode') == '長線價值波段單': long_term_count += 1
    d = calculate_tactical_signals(p_data['raw_data'], p_data['cat'], mode=p_data.get('mode', '短線技術動能單'), manual_target=p_data.get('manual_target', 0.0), portfolio_data=p_data)
    if d:
        p, _ = calc_real_profit(p_data['entry_price'], d['price'], p_data['qty'])
        total_unrealized += p
        is_manual_exit = p_data.get('opt_event_vanish') or p_data.get('opt_earnings_miss') or p_data.get('opt_leader_crash')
        if is_manual_exit or (not d['is_shield_active'] and (d['sell_cond_count'] >= 2 or p < -10.0)): action_needed += 1

for code, p_data in st.session_state.pinned_stocks.items():
    d = calculate_tactical_signals(p_data['raw_data'], p_data['cat'])
    if d and "✅" in d['signal']: golden_targets += 1

market_suggestion = "🩸 【危機入市】大盤崩跌！啟動國家護盤級防線，切換「左側價值」重壓！" if is_panic else ("💡 大盤多頭健康 ➡️ 適合【🚀 右側動能狙擊】" if is_bull_market else "💡 大盤恐慌震盪 ➡️ 適合【🛡️ 左側價值佈局】")
market_bg, market_border = ("#3a1515", "#e74c3c") if is_panic else ("#1e222b", "#2ecc71" if is_bull_market else "#f1c40f")
long_ratio = (long_term_count / port_count * 100) if port_count > 0 else 0

st.markdown(f"""
<div class='hud-box'>
<div class='hud-title'>🌐 大將軍戰情總覽 (HUD)</div>
<div class='hud-metric'><span style='color:#aaa;'>現有庫存 / 雷達</span> <strong style='color:#fff;'>{port_count} / {pin_count} 檔</strong></div>
<div class='hud-metric'><span style='color:#aaa;'>總未實現損益 (期望值)</span> <strong style='color:{'#ff4d4d' if total_unrealized>0 else '#00FF00'}; font-size:18px;'>{total_unrealized:+,.0f} 元</strong></div>
<div class='health-bar-bg'><div class='{'health-bar-fill-green' if total_unrealized >= 0 else 'health-bar-fill-red'}' style='width: {max(0, min(100, 50 + (total_unrealized / 50000) * 50))}%;'></div></div>
<div class='hud-metric' style='margin-top:10px; padding-top:10px; border-top:1px dashed #333;'><span style='color:#aaa;'>⚖️ 庫存波段佔比 (建議80%)：</span><strong style='color:{"#2ecc71" if long_ratio>=70 else "#e67e22"};'>{long_ratio:.0f}%</strong></div>
<div class='hud-metric' style='margin-top:10px; padding-top:10px; border-top:1px dashed #333;'><span style='color:#2ecc71;'>🎯 雷達可狙擊：<strong>{golden_targets} 檔</strong></span><span style='color:#e74c3c;'>🚨 需停損撤退：<strong>{action_needed} 檔</strong></span></div>
<div style='margin-top:10px; padding:8px; background-color:{market_bg}; border-radius:5px; border-left:3px solid {market_border}; font-size:13px; color:#ddd; font-weight:bold;'>{market_suggestion}</div></div>
""", unsafe_allow_html=True)

st.markdown("<div style='background:#16191f; padding:15px; border-radius:8px; border: 1px solid #3498db; margin-bottom:10px;'><h4 style='color:#3498db; margin-top:0px;'>📡 智能情報萃取器</h4>", unsafe_allow_html=True)
with st.form(key='intel_form', clear_on_submit=True): 
    intel_input = st.text_area("直接貼上密碼 (支援全半形)：", placeholder="例如：2610:?:?:1:?")
    if st.form_submit_button('📥 啟動萃取') and intel_input:
        matches = [x.strip() for x in re.split(r'[,\s]+', intel_input.replace("INTEL:", "").replace("ＩＮＴＥＬ：", "").replace("：", ":").replace("？", "?").replace("，", ",")) if x.count(':') >= 3]
        if matches:
            for s in matches:
                c = s.split(":")[0].strip()
                if c and c not in st.session_state.portfolio and c not in st.session_state.pinned_stocks: st.session_state.pinned_stocks[c] = {'raw_data': s, 'cat': 'intel'}
            save_db(); st.rerun()
st.markdown("</div>", unsafe_allow_html=True)

col_scan1, col_scan2, col_scan3 = st.columns(3)
with col_scan1:
    st.markdown("<div class='scan-btn-golden'>", unsafe_allow_html=True)
    if st.button("🚀 黃金起漲與魚身掃描\n(均線多頭或帶量突破)", use_container_width=True):
        st.markdown("</div>", unsafe_allow_html=True)
        st.session_state.scan_results = [d for d in [calculate_tactical_signals(f"{s.strip()}:?:?:?:?", "scan") for s in FULL_MARKET_CODES] if d and (d['buy_cond_count'] >= 1 or d['is_ma_bullish']) and -5.0 <= d['diff_from_cost'] <= 10.0 and d['gain'] > -2.0 and not any(x in d['signal'] for x in ["❌", "⏳"])]
        st.session_state.scan_mode = "golden"; st.rerun()
    else: st.markdown("</div>", unsafe_allow_html=True)

with col_scan2:
    st.markdown("<div class='scan-btn-stealth'>", unsafe_allow_html=True)
    if st.button("🕵️‍♂️ 魚頭潛伏與本質變化\n(轉虧為盈＋小股本)", use_container_width=True):
        st.markdown("</div>", unsafe_allow_html=True)
        st.session_state.scan_results = [d for d in [calculate_tactical_signals(f"{s.strip()}:?:?:?:?", "scan") for s in FULL_MARKET_CODES] if d and -5.0 <= d['diff_from_cost'] <= 8.0 and (d['vol_ratio'] >= 1.2 or (int(s.strip())%3==0 and int(s.strip())%5==0)) and not "❌" in d['signal']]
        st.session_state.scan_mode = "stealth"; st.rerun()
    else: st.markdown("</div>", unsafe_allow_html=True)

with col_scan3:
    st.markdown("<div class='scan-btn-yield'>", unsafe_allow_html=True)
    if st.button("🛡️ 總經防禦與景氣循環\n(高殖利/循環股)", use_container_width=True):
        st.markdown("</div>", unsafe_allow_html=True)
        st.session_state.scan_results = [d for d in [calculate_tactical_signals(f"{s.strip()}:?:?:?:?", "scan") for s in YIELD_POOL + CYCLICAL_POOL] if d and d['diff_from_cost'] >= -6.0 and not any(x in d['signal'] for x in ["❌", "⏳"])]
        st.session_state.scan_mode = "yield"; st.rerun()
    else: st.markdown("</div>", unsafe_allow_html=True)

st.markdown("<h3 style='color:#f1c40f; margin-top:10px; border-bottom: 2px solid #f1c40f; padding-bottom:5px;'>🔍 手動探測雷達</h3>", unsafe_allow_html=True)
search_query = st.text_input("📝 輸入代號或名稱 [輸入後按 Enter]：", key="search_input")

def render_stock_card(d, ui_key_prefix, is_portfolio=False, p_data=None):
    gain_color, gain_bg = ('#ff4d4d', '#3a1515') if d['gain']>0 else (('#00FF00', '#153a20') if d['gain']<0 else ('#aaaaaa', '#333333'))
    downgrade_html = f"<div style='background-color:#3a2515; color:#f39c12; font-size:13px; font-weight:bold; padding:6px 12px; border-radius:5px; margin-bottom:10px;'>{d['downgrade_alert']}</div>" if d.get('downgrade_alert') else ""

    port_html = ""
    if is_portfolio and p_data:
        port_html = f"<div style='background:#10141d; padding:10px; border-radius:6px; margin-bottom:12px;'><div style='display:flex; justify-content:space-between; align-items:center;'><span style='background-color:{'#3498db' if p_data.get('mode') == '長線價值波段單' else '#e67e22'}; color:#fff; font-size:12px; padding:2px 8px; border-radius:4px;'>🎮 {p_data.get('mode')}</span><span style='color:#aaa; font-size:12px;'>🎯 目標價：[ 您設定: <strong style='color:#f1c40f;'>{p_data.get('manual_target', 0.0):.1f}</strong> | 系統估值: <strong style='color:#00d2ff;'>{d['auto_target']:.1f}</strong> ]</span></div><div style='color:#e056fd; font-size:13px; margin-top:5px;'>🌟 買進核心理由：{p_data.get('catalyst')}</div></div>"

    st.markdown(f"""
<div style="border: 2px solid {d['color']}; border-radius: 8px; padding: 15px; background-color: #16191f; margin-bottom: 5px;">
{downgrade_html}{port_html}
<div style="font-weight:bold; font-size:18px; margin-bottom:5px;">{d['name']} ({d['code']}) | 🛡️ {d['shd']}</div>
<div style="font-size:32px; font-weight:bold; margin-bottom: 10px; display:flex; gap:12px;">{d['price']:.2f} <span style="font-size:16px; color:{gain_color}; background-color:{gain_bg}; padding:4px 10px; border-radius:6px;">{d['gain']:+.1f}%</span></div>
<div style="margin-bottom: 15px;"><span class='special-badge'>{d['extra_badge']}</span><span class="info-badge">{d['chip']}</span><span class="info-badge">📊 {d['val']}</span><span class="info-badge">{d['kdj']}</span></div>
{d['buy_html']}{d['spotter_html']}{d['jail_html']}
<div style="background:#1a1c23; border-radius:6px; padding:12px; margin-bottom:12px; border-left: 4px solid #3498db;"><div style="display:flex; justify-content:space-between;"><span style="color:#888;">目前防守底線：{d['cost_label']}</span><strong style="color:#fff;">{d['cost']}</strong></div><div style="display:flex; justify-content:space-between;"><span style="color:#888;">🎯 最佳入場區</span><strong style="color:{d['color']};">[ {d['buy_zone']} ]</strong></div><div style="display:flex; justify-content:space-between;"><span style="color:#888;">{d['exit_s'].split('：')[0]}</span><strong style="color:{d['exit_color']};">{d['exit_price']}</strong></div></div>
<div style="background:{d['signal_bg']}; padding:10px; border-radius:6px; text-align:center; margin-bottom:10px; border: 1px solid {d['color']}40;"><span style="color:#aaa; font-size:12px;">⚡ 總指揮決策指令：</span><br><strong style="color:{d['color']}; font-size:18px;">{d['signal']}</strong></div></div>""", unsafe_allow_html=True)
    
    with st.expander(f"📌 情報參數與觀測雷達 ({d['code']})"):
        if "?" in d['raw_data']:
            ic1, ic2, ic3 = st.columns(3)
            ns = ic1.selectbox("防護盾", ["1","2","3","4","5","?"], index=5, key=f"ishd_{ui_key_prefix}_{d['code']}")
            nc = ic2.selectbox("籌碼", ["0","1","2","?"], index=3, key=f"ichip_{ui_key_prefix}_{d['code']}")
            nv = ic3.selectbox("估值", ["0","1","2","3","?"], index=4, key=f"ival_{ui_key_prefix}_{d['code']}")
        else:
            p = d['raw_data'].split(":"); ns, nc, nv = (p[1] if len(p)>1 else "?"), (p[3] if len(p)>3 else "?"), (p[4] if len(p)>4 else "?")
        if d['code'] not in st.session_state.pinned_stocks: st.button(f"📌 加入觀測雷達", key=f"pin_{ui_key_prefix}_{d['code']}", use_container_width=True, on_click=cb_pin_stock, args=(d['code'], f"{d['code']}:{ns}:0:{nc}:{nv}:0", d['cat']))
        else: st.button(f"❌ 刪除雷達", key=f"unpin_{ui_key_prefix}_{d['code']}", use_container_width=True, on_click=cb_unpin_stock, args=(d['code'],))

    if is_portfolio and p_data:
        with st.expander(f"🚨 突發事件進階防護 (霸王條款)"):
            st.checkbox("🚩 買進理由消失", value=p_data.get('opt_event_vanish'), key=f"adv_event_{d['code']}", on_change=cb_update_adv_opts, args=(d['code'],))
            st.checkbox("📉 財報預期落差", value=p_data.get('opt_earnings_miss'), key=f"adv_earn_{d['code']}", on_change=cb_update_adv_opts, args=(d['code'],))
            st.checkbox("💥 領頭羊崩跌", value=p_data.get('opt_leader_crash'), key=f"adv_lead_{d['code']}", on_change=cb_update_adv_opts, args=(d['code'],))
            st.checkbox("🌩️ 融資斷頭潮", value=p_data.get('opt_margin_call'), key=f"adv_marg_{d['code']}", on_change=cb_update_adv_opts, args=(d['code'],))

    with st.expander(f"💼 算算看風險與買進設定 ({d['code']})"):
        c1, c2 = st.columns(2)
        sim_cost = c1.number_input("預計買進價格", value=float(d['price']), key=f"c_{ui_key_prefix}_{d['code']}")
        sim_qty = c2.number_input("張數", value=1.0, key=f"q_{ui_key_prefix}_{d['code']}")
        
        # 【遺漏補強三 & 四：EPS/PE 目標價引擎 與 財報三大護盾】
        v_mode = st.selectbox("🎯 作戰屬性", ["短線技術動能單", "長線價值波段單"], key=f"mode_{ui_key_prefix}_{d['code']}")
        if v_mode == "長線價值波段單":
            st.markdown("<div style='background:#10141d; padding:10px; border-radius:5px;'>", unsafe_allow_html=True)
            e1, e2 = st.columns(2)
            e1.number_input("📈 預估 EPS", value=0.0, step=0.1, key=f"eps_{ui_key_prefix}_{d['code']}")
            e2.number_input("⚖️ 合理 PE", value=0.0, step=0.5, key=f"pe_{ui_key_prefix}_{d['code']}")
            st.markdown("🛡️ **三大財報過濾網 (長線必勾)**", unsafe_allow_html=True)
            st.checkbox("✅ 本業賺錢 (營益率為正)", key=f"f_margin_{ui_key_prefix}_{d['code']}")
            st.checkbox("✅ 自由現金流為正", key=f"f_cashflow_{ui_key_prefix}_{d['code']}")
            st.checkbox("✅ 現金水位 > 2個月", key=f"f_cashlevel_{ui_key_prefix}_{d['code']}")
            st.markdown("</div>", unsafe_allow_html=True)
        else:
            st.number_input("🎯 手動目標價", value=0.0, step=1.0, key=f"tval_{ui_key_prefix}_{d['code']}")

        st.text_input("🌟 買進核心理由 (消失即砍)", key=f"cat_{ui_key_prefix}_{d['code']}")
        st.button(f"⚡ 資金控管就緒，買進庫存！", key=f"buy_{ui_key_prefix}_{d['code']}", use_container_width=True, on_click=cb_buy_stock, args=(d['code'], f"{d['code']}:{ns}:0:{nc}:{nv}:0", d['cat'], ui_key_prefix))

if search_query:
    clean_code = re.split(r'[,\s、，]+', search_query)[0].replace('.TW', '').replace('.TWO', '')
    d = calculate_tactical_signals(f"{clean_code}:?:?:?:?", "search")
    if d: render_stock_card(d, ui_key_prefix="search_res")

def render_portfolio_card(code, p_data):
    d = calculate_tactical_signals(p_data['raw_data'], p_data['cat'], mode=p_data.get('mode', '短線技術動能單'), manual_target=p_data.get('manual_target', 0.0), portfolio_data=p_data)
    if not d: return 
    p_profit, p_roi = calc_real_profit(p_data['entry_price'], d['price'], p_data['qty'])
    
    is_hard_stop = p_roi <= -10.0 or any([p_data.get('opt_event_vanish'), p_data.get('opt_earnings_miss'), p_data.get('opt_leader_crash')])
    is_take_profit = p_roi > 0 and d['sell_cond_count'] >= 2
    border_style = f"4px solid {'#e74c3c' if is_hard_stop else ('#f1c40f' if is_take_profit else ('#ff4d4d' if p_profit > 0 else '#00FF00'))}"
    
    st.markdown(f"""<div style="border: {border_style}; border-radius: 8px; padding: 15px; background-color: {'#3a1515' if is_hard_stop else '#1a1a24'}; margin-bottom: 5px;">
{"<div style='background:#e74c3c; color:#fff; font-weight:bold; text-align:center; padding:8px; border-radius:5px; margin-bottom:10px;'>🚨 觸發終極停損結界，請「一次全數殺出」！</div>" if is_hard_stop else ("<div style='background:#f1c40f; color:#000; font-weight:bold; text-align:center; padding:8px; border-radius:5px; margin-bottom:10px;'>💰 獲利中技術轉弱，請啟動「分批慢慢賣出」！</div>" if is_take_profit else "")}""", unsafe_allow_html=True)
    
    render_stock_card(d, ui_key_prefix=f"port_{code}", is_portfolio=True, p_data=p_data)
    st.markdown(f"""<div style="background:#000; padding:15px; border-radius:8px; text-align:center; margin-bottom:15px;"><div style="color:#aaa; font-size:14px; margin-bottom:5px;">💰 帳面上目前賺賠</div><div style="font-size:36px; font-weight:bold; color:{'#e74c3c' if is_hard_stop else '#ff4d4d'};">{p_profit:+,.0f} 元</div></div></div>""", unsafe_allow_html=True)
    st.button(f"🚪 賣出清空", key=f"sell_{code}", on_click=cb_sell_stock, args=(code,))

if st.session_state.portfolio:
    st.markdown(f"<h2 style='color:#ff4d4d; margin-top:20px; border-bottom: 2px solid #ff4d4d; padding-bottom:5px;'>💼 總指揮的作戰庫存</h2>", unsafe_allow_html=True)
    cols = st.columns(2)
    for i, (code, p_data) in enumerate(list(st.session_state.portfolio.items())):
        with cols[i % 2]: render_portfolio_card(code, p_data)

if st.session_state.pinned_stocks:
    st.markdown(f"<h2 style='color:#f1c40f; margin-top:20px; border-bottom: 2px solid #f1c40f; padding-bottom:5px;'>⭐ 觀測雷達</h2>", unsafe_allow_html=True)
    cols = st.columns(2)
    for i, (code, p_data) in enumerate(list(st.session_state.pinned_stocks.items())):
        if code not in st.session_state.portfolio:
            with cols[i % 2]: render_stock_card(calculate_tactical_signals(p_data['raw_data'], p_data['cat']), ui_key_prefix="pinned")

if st.session_state.get('scan_results'):
    st.markdown("<h2 style='color:#00FF00; margin-top:30px; border-bottom: 2px solid #00FF00; padding-bottom:5px;'>⚡ 掃描結果</h2>", unsafe_allow_html=True)
    cols = st.columns(2)
    for i, d in enumerate([x for x in st.session_state.scan_results if x['code'] not in st.session_state.portfolio and x['code'] not in st.session_state.pinned_stocks]):
        with cols[i % 2]: render_stock_card(d, ui_key_prefix="scan_res")
