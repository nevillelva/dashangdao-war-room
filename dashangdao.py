import streamlit as st
import streamlit.components.v1 as components
import yfinance as yf
import pandas as pd
from datetime import datetime
import re
import math
import time
import json
import os
import requests
import concurrent.futures
import random

st.set_page_config(layout="wide", page_title="54088 - 終極大腦 V16.0", initial_sidebar_state="expanded")

# ==========================================
# 🛡️ 霸王級 CSS 與視覺化血條
# ==========================================
st.markdown('''<style>
.stApp { background-color: #0b0c0f !important; color: #fff !important; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
div[data-testid="stSidebar"] { background-color: #12141a !important; border-right: 1px solid #333 !important; }
div[data-testid="stButton"] > button { background-color: #1e1e24 !important; border: 1px solid #444 !important; transition: all 0.2s ease-in-out; }
div[data-testid="stButton"] > button p { color: #ffffff !important; font-weight: bold !important; font-size: 15px !important; }
div[data-testid="stButton"] > button:hover { border-color: #f1c40f !important; transform: translateY(-2px); box-shadow: 0 4px 10px rgba(241,196,15,0.2); }
[data-testid="stExpander"] details summary { background-color: #16191f !important; border: 1px solid #3498db !important; border-radius: 8px !important; margin-bottom: 5px !important; }
[data-testid="stExpander"] details summary:hover { background-color: #1e222b !important; border-color: #f39c12 !important; }
[data-testid="stExpander"] details summary p { color: #f1c40f !important; font-weight: 900 !important; font-size: 16px !important; }
[data-testid="stExpander"] details summary svg { fill: #f1c40f !important; }
.sync-btn div[data-testid="stButton"] > button { background-color: #f39c12 !important; border: 2px solid #e67e22 !important; }
.sync-btn div[data-testid="stButton"] > button p { color: #000000 !important; font-weight: 900 !important; }
.scan-btn-golden div[data-testid="stButton"] > button { background-color: #153a20 !important; border: 2px solid #00FF00 !important; margin-top:5px; margin-bottom: 5px; height: 60px;}
.scan-btn-golden div[data-testid="stButton"] > button p { color: #00FF00 !important; font-size: 14px !important; white-space: pre-wrap;}
.scan-btn-stealth div[data-testid="stButton"] > button { background-color: #0b2239 !important; border: 2px solid #00d2ff !important; margin-top:5px; margin-bottom: 5px; height: 60px;}
.scan-btn-stealth div[data-testid="stButton"] > button p { color: #00d2ff !important; font-size: 14px !important; white-space: pre-wrap;}
.scan-btn-yield div[data-testid="stButton"] > button { background-color: #2c153a !important; border: 2px solid #9b59b6 !important; margin-top:5px; margin-bottom: 5px; height: 60px;}
.scan-btn-yield div[data-testid="stButton"] > button p { color: #e056fd !important; font-size: 14px !important; white-space: pre-wrap;}
.hud-box { background: linear-gradient(135deg, #1a1c23 0%, #0d1117 100%); border-radius: 10px; padding: 15px; border-left: 5px solid #f1c40f; box-shadow: 0 4px 15px rgba(0,0,0,0.5); margin-bottom: 20px;}
.hud-title { color: #f1c40f; font-size: 14px; font-weight: bold; margin-bottom: 10px; border-bottom: 1px solid #333; padding-bottom: 5px;}
.hud-metric { display: flex; justify-content: space-between; align-items: center; margin-bottom: 5px;}
.health-bar-bg { width: 100%; background-color: #333; border-radius: 5px; height: 8px; margin-top: 5px; overflow: hidden;}
.health-bar-fill-green { height: 100%; background-color: #2ecc71; transition: width 0.5s ease;}
.health-bar-fill-red { height: 100%; background-color: #e74c3c; transition: width 0.5s ease;}
.info-badge { background: #2b2b36; padding: 4px 8px; border-radius: 4px; font-size: 13px; color: #ccc; margin-right: 5px; border: 1px solid #444; display: inline-block; margin-bottom: 5px; }
.special-badge { background: #1a2a3a; padding: 4px 8px; border-radius: 4px; font-size: 13px; color: #00d2ff; margin-right: 5px; border: 1px solid #3498db; display: inline-block; margin-bottom: 5px; font-weight: bold; }
.danger-badge { background: #3a1515; padding: 4px 8px; border-radius: 4px; font-size: 13px; color: #ff4d4d; margin-right: 5px; border: 1px solid #e74c3c; display: inline-block; margin-bottom: 5px; font-weight: bold; }
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

def cb_sync(): st.session_state.temp_intel = []

def cb_pin_stock(code, raw_data, cat):
    if len(st.session_state.pinned_stocks) >= MAX_CAPACITY: return
    st.session_state.pinned_stocks[code] = {'raw_data': raw_data, 'cat': cat}
    st.session_state.temp_intel = [x for x in st.session_state.temp_intel if x['code'] != code]
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
        eps_val = float(st.session_state.get(f"eps_{ui_key_prefix}_{code}", 0.0))
        pe_val = float(st.session_state.get(f"pe_{ui_key_prefix}_{code}", 0.0))
        manual_target = eps_val * pe_val if (eps_val > 0 and pe_val > 0) else float(st.session_state.get(f"tval_{ui_key_prefix}_{code}", 0.0))
        catalyst = st.session_state.get(f"cat_{ui_key_prefix}_{code}", "")
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
    st.session_state.temp_intel = [x for x in st.session_state.temp_intel if x['code'] != code]
    save_db()

def cb_sell_stock(code):
    if code in st.session_state.portfolio:
        del st.session_state.portfolio[code]
        save_db()

def cb_logout():
    st.session_state.authenticated = False
    if "auth" in st.query_params: del st.query_params["auth"]

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
if 'temp_intel' not in st.session_state: st.session_state.temp_intel = []
if 'sentinel_active' not in st.session_state: st.session_state.sentinel_active = False

if 'db_loaded' not in st.session_state:
    db_data = load_db()
    st.session_state.pinned_stocks = db_data.get("pinned_stocks", {})
    st.session_state.portfolio = db_data.get("portfolio", {})
    st.session_state.db_loaded = True

# ==========================================
# 📡 系統參數庫 & 官方 OpenAPI
# ==========================================
TW_STOCKS = {
    "2330":"台積電", "2317":"鴻海", "2454":"聯發科", "2382":"廣達", "2303":"聯電",
    "2881":"富邦金", "2882":"國泰金", "2891":"中信金", "2886":"兆豐金",
    "2603":"長榮", "2609":"陽明", "2615":"萬海", "1519":"華城", "1513":"中興電",
    "2408":"南亞科"
}

@st.cache_data(ttl=86400)
def fetch_official_fundamentals():
    dynamic_data = {}
    market_codes = []
    api_names = {}
    try:
        twse_url = "https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL"
        twse_res = requests.get(twse_url, timeout=10)
        if twse_res.status_code == 200:
            for item in twse_res.json():
                code = item.get('Code', '').strip()
                if len(code) >= 4 and code.isdigit():
                    market_codes.append(code)
                    api_names[code] = item.get('Name', code)
                    pe = float(item.get('PeRatio', 0)) if item.get('PeRatio', '-').replace('.', '', 1).isdigit() else 999.0
                    pb = float(item.get('PbRatio', 0)) if item.get('PbRatio', '-').replace('.', '', 1).isdigit() else 999.0
                    yld = float(item.get('DividendYield', 0)) if item.get('DividendYield', '-').replace('.', '', 1).isdigit() else 0.0
                    dynamic_data[code] = {'PE': pe, 'PB': pb, 'Yield': yld}
    except: pass
    try:
        tpex_url = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_peratio_analysis"
        tpex_res = requests.get(tpex_url, timeout=10)
        if tpex_res.status_code == 200:
            for item in tpex_res.json():
                code = item.get('SecuritiesCompanyCode', '').strip()
                if len(code) >= 4 and code.isdigit():
                    market_codes.append(code)
                    api_names[code] = item.get('CompanyName', code)
                    pe = float(item.get('PERatio', 0)) if item.get('PERatio', '-').replace('.', '', 1).isdigit() else 999.0
                    pb = float(item.get('PBRatio', 0)) if item.get('PBRatio', '-').replace('.', '', 1).isdigit() else 999.0
                    yld = float(item.get('YieldRatio', 0)) if item.get('YieldRatio', '-').replace('.', '', 1).isdigit() else 0.0
                    dynamic_data[code] = {'PE': pe, 'PB': pb, 'Yield': yld}
    except: pass
    if not market_codes: market_codes = list(TW_STOCKS.keys())
    return list(set(market_codes)), dynamic_data, api_names

FULL_MARKET_CODES, FUNDAMENTAL_DB, API_NAMES = fetch_official_fundamentals()
TW_STOCKS.update(API_NAMES)

CHIP_MAP = {"1": "🐳 巨鯨進駐(籌碼面)", "2": "🩸 外資提款(籌碼面)", "0": "⚖️ 籌碼平穩(籌碼面)", "?": "❓ 籌碼待查(籌碼面)"}
VAL_MAP = {"1": "🟢 便宜(長線價值)", "2": "🟡 合理(長線價值)", "3": "🔴 昂貴(長線價值)", "0": "⚪ 未定(長線價值)", "?": "❓ 待定(長線價值)"}

def safe_int(val, default=0):
    try: return int(val) if val else default
    except: return default
def safe_float(val, default=None):
    try: return float(val) if val else default
    except: return default
def get_stock_name(symbol): return TW_STOCKS.get(symbol, f"個股 {symbol}")

@st.cache_data(ttl=14400)
def fetch_macro_margin_signals():
    try:
        url = "https://openapi.twse.com.tw/v1/exchangeReport/MI_MARGN"
        res = requests.get(url, timeout=3)
        if res.status_code == 200:
            data = res.json()
            total_margin_sell = sum(float(item.get('Sell', 0)) for item in data[:50]) 
            if total_margin_sell > 500000: return True, "大數據顯示融資斷頭天量殺出"
    except: pass
    return False, ""

@st.cache_data(ttl=300)
def get_market_weather():
    try:
        tw50 = yf.Ticker("0050.TW").history(period="3mo").dropna(subset=['Close'])
        try:
            twii = yf.Ticker("^TWII").history(period="1d").dropna(subset=['Close'])
            twii_price = float(twii['Close'].iloc[-1])
            twii_str = f"加權指數: {twii_price:,.0f} 點"
        except: twii_str = ""

        if tw50.empty: return "未知", "#888", False, False
        
        current_50 = float(tw50['Close'].iloc[-1])
        ma20_50 = float(tw50['Close'].rolling(window=20).mean().iloc[-1])
        ma60_50 = float(tw50['Close'].rolling(window=60).mean().iloc[-1])
        
        gain = ((current_50 - float(tw50['Close'].iloc[-2])) / float(tw50['Close'].iloc[-2])) * 100
        drop_5d = ((current_50 - float(tw50['Close'].iloc[-6])) / float(tw50['Close'].iloc[-6])) * 100 if len(tw50) >= 6 else 0.0
        is_bull_market = current_50 > ma20_50 or gain > 0
        
        api_panic, api_reason = fetch_macro_margin_signals()
        is_epic_crash = gain <= -4.0                  
        is_continuous_slaughter = drop_5d <= -7.0     
        is_deep_deviation = current_50 < (ma60_50 * 0.95)   
        price_panic = is_epic_crash or is_continuous_slaughter or is_deep_deviation
        is_panic = api_panic or price_panic
        
        display_idx = twii_str if twii_str else f"0050: {current_50:.1f}"

        if is_panic: 
            reason_str = api_reason if api_panic else "大盤極端破線與連跌"
            return f"🌩️ 恐慌斷頭潮來臨 ({reason_str} | {display_idx})", "#e74c3c", is_bull_market, True
        elif current_50 > ma20_50: 
            return f"☀️ 多頭順風環境 ({display_idx})", "#2ecc71", is_bull_market, False
        else: 
            return f"☁️ 空頭震盪環境 ({display_idx} / 0050破月線)", "#f1c40f", is_bull_market, False
    except: 
        return "📡 大盤資料獲取中...", "#888", False, False

def calculate_tactical_signals(symbol_data, category_type="main", mode="短線技術動能單", manual_target=0.0, portfolio_data=None, manual_prices_dict=None, is_macro_panic_global=False):
    try:
        if manual_prices_dict is None: manual_prices_dict = {}
        
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

        if chip_code == "1": chip_desc = "大戶跟法人正在偷偷倒錢買進，跟著主力走比較安全！"
        elif chip_code == "2": chip_desc = "外資或主力正在大量賣出倒貨，散戶容易被當韭菜割，快避開。"
        elif chip_code == "0": chip_desc = "最近沒有大資金在裡面搞事，多空雙方都在觀望。"
        else: chip_desc = "目前沒有籌碼資料，先不要盲目動作。"

        fund_info = FUNDAMENTAL_DB.get(symbol, {})
        dynamic_pe = fund_info.get('PE', 999.0)
        dynamic_pb = fund_info.get('PB', 999.0)
        
        if val_code == "?":
            if dynamic_pe < 12.0 or dynamic_pb < 1.2: val_code = "1"
            elif dynamic_pe > 25.0 or dynamic_pb > 3.0: val_code = "3"
            else: val_code = "2"

        if val_code == "1": val_desc = f"動態估算：便宜 (PE:{dynamic_pe if dynamic_pe!=999.0 else '-'}, PB:{dynamic_pb if dynamic_pb!=999.0 else '-'})"
        elif val_code == "2": val_desc = "動態估算：合理不貴也不便宜。"
        elif val_code == "3": val_desc = f"動態估算：昂貴，隨時會大跌！(PE:{dynamic_pe if dynamic_pe!=999.0 else '-'})"
        else: val_desc = "系統還在評估它的真實價值。"
        
        hist = pd.DataFrame()
        ticker = None
        for attempt in range(3):
            try:
                temp_ticker = yf.Ticker(f"{symbol}.TW")
                temp_hist = temp_ticker.history(period="2y").dropna(subset=['Close'])
                if not temp_hist.empty and len(temp_hist) > 15: 
                    hist = temp_hist; ticker = temp_ticker; break
            except: pass
            if hist.empty:
                try:
                    temp_ticker = yf.Ticker(f"{symbol}.TWO")
                    temp_hist = temp_ticker.history(period="2y").dropna(subset=['Close'])
                    if not temp_hist.empty and len(temp_hist) > 15: 
                        hist = temp_hist; ticker = temp_ticker; break
                except: pass
            if not hist.empty: break
            time.sleep(random.uniform(0.5, 1.5)) 

        manual_override = manual_prices_dict.get(symbol)
        is_overridden = False

        if hist.empty or ticker is None:
            current_price = float(manual_override) if (manual_override and manual_override > 0) else 0.0
            shd_display = "❓ 待查" if override_shd_raw == "?" else f"{override_shd_raw}分"
            return {
                "name": stock_name, "code": symbol, "price": current_price, "gain": 0.0,
                "cost": 0.0, "cost_label": "網路中斷", "buy_zone": "0 - 0",
                "shd": shd_display, "chip_code": chip_code, "chip": "⚖️", "val_code": val_code, "val": VAL_MAP.get(val_code, "⚪"),
                "kdj": "⚠️ 無法取得指標", "chip_desc": chip_desc, "val_desc": val_desc, "kdj_desc": "斷線", 
                "downgrade_alert": "🚨 API 阻擋，強制降級手動模式", "signal": "❌ 【API抓取失敗】請手動輸入現價！", 
                "color": "#888888", "signal_bg": "#111111", "ai_tags": ["⚠️ 斷線盲區"], "exit_s": "未知", 
                "exit_price": "0", "exit_color": "#888", "exit_bg": "#333", "vol": 0, "open": 0, "high": 0, "low": 0, 
                "raw_data": symbol_data, "cat": category_type, "spotter_html": "", "buy_html": "", "jail_html": "", 
                "buy_cond_count": 0, "diff_from_cost": 0.0, "vol_ratio": 0.0, "sell_cond_count": 0, 
                "is_overridden": (manual_override is not None), "auto_target": 0.0, "is_shield_active": False,
                "is_ma_bullish": False, "roi_pct": 0.0, "w_bottom_breakout": False, "is_action_needed": False, "is_golden": False
            }

        if manual_override and manual_override > 0:
            current_price = float(manual_override); is_overridden = True
        else:
            try:
                today_tick = ticker.history(period="1d", interval="1m").dropna(subset=['Close'])
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
        vol_5d = max(hist['Volume'].iloc[-6:-1].mean() / 1000, 0.01) 
        vol_20d = max(hist['Volume'].iloc[-21:-1].mean() / 1000, 0.01) if len(hist) > 20 else vol_5d
        vol_ratio = vol / vol_5d 
        
        ma5 = hist['Close'].rolling(window=min(5, len(hist))).mean().iloc[-1]
        ma10 = hist['Close'].rolling(window=min(10, len(hist))).mean().iloc[-1]
        ma20 = hist['Close'].rolling(window=min(20, len(hist))).mean().iloc[-1]
        ma60 = hist['Close'].rolling(window=min(60, len(hist))).mean().iloc[-1]
        ma120 = hist['Close'].rolling(window=min(120, len(hist))).mean().iloc[-1]
        ma240 = hist['Close'].rolling(window=min(240, len(hist))).mean().iloc[-1]

        is_ma_bullish = (current_price > ma10) and (ma10 > ma20) and (ma20 > ma60)
        auto_target_price = round(float(hist['High'].max() * 1.1), 1)

        ma_max = max(ma10, ma20, ma60)
        ma_min = min(ma10, ma20, ma60)
        ma_squeeze = (ma_max - ma_min) / max(ma_min, 0.01) < 0.05 
        w_bottom_breakout = ma_squeeze and (current_price > ma_max) and (vol_ratio >= 1.5)

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
        
        body = abs(current_price - open_p)
        upper_shadow = high_p - max(open_p, current_price)
        is_shooting_star = (upper_shadow > (body * 1.5)) and (high_p > ma5)
        
        is_attack_vol = vol_ratio >= 4.0
        is_cold_to_hot = (vol_20d <= 1.0) and is_attack_vol

        is_huge_vol = vol > (vol_5d * 2.0)                
        is_black_k = current_price < open_p and gain < 0 
        is_break_ma5 = current_price < ma5                
        sell_cond_count = sum([is_huge_vol, is_shooting_star, is_break_ma5])

        kdj_danger_trap = (k > 80) and is_black_k and is_huge_vol
        
        if kdj_golden_cross: kdj_signal, kdj_desc = "📈 低檔金叉", "跌到底部準備反彈！「短線轉強可以買」的暗號。"
        elif kdj_danger_trap: kdj_signal, kdj_desc = "💀 利多出盡", "高檔爆出天量且收黑，主力絕對在倒貨，快逃！"
        elif (k > 70 and k < d_val): kdj_signal, kdj_desc = "📉 高檔死叉", "進入 80 以上高檔動能衰退，提防主力出貨。"
        else: kdj_signal, kdj_desc = "〰️ KDJ 震盪", "目前無特別訊號。"

        buy_cond_count = sum([kdj_golden_cross, is_macd_red, is_attack_vol, w_bottom_breakout])
        
        buy_status, buy_color, buy_bg = "⚪ 醞釀中 (無明顯起漲)", "#aaaaaa", "#1a1a24"
        if buy_cond_count >= 3 or is_cold_to_hot: buy_status, buy_color, buy_bg = "🔥 訊號全亮，強勢起漲！", "#ff4d4d", "#3a1515"
        elif buy_cond_count == 2: buy_status, buy_color, buy_bg = "🚀 雙引擎發動，準備表態", "#f1c40f", "#3a3015"
        elif buy_cond_count == 1: buy_status, buy_color, buy_bg = "✨ 底部浮現單一火苗", "#3498db", "#152a3a"

        ma_bull_str = "<span>🔴 均線多頭</span>" if is_ma_bullish else ("<span>🔴 W底突破</span>" if w_bottom_breakout else "<span>⚪ 均線未排列</span>")
        buy_html = f"<div class='my-tooltip' style='background:{buy_bg}; padding:10px 15px; border-radius:8px; margin-bottom:12px; border-left: 5px solid {buy_color}; display:block; width:100%;'><div style='font-size:12px; color:#ddd; margin-bottom:6px;'>🚀 起漲(買進)雷達：<strong style='color:{buy_color}; font-size:14px;'>{buy_status}</strong></div><div style='font-size:12px; color:#eee; display:flex; justify-content:space-between;'><span>{'🔴' if kdj_golden_cross else '⚪'} KDJ金叉</span><span>{'🔴' if is_attack_vol else '⚪'} 攻擊量</span><span>{'🔴' if is_cold_to_hot else '⚪'} 冷門甦醒</span>{ma_bull_str}</div><span class='my-tooltiptext'>短線起漲動能判定。</span></div>"
        
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

        is_high_yield = fund_info.get('Yield', 0.0) >= 5.0
        is_cyclical = (fund_info.get('PB', 999.0) < 1.2) or (fund_info.get('PE', 999.0) < 12.0)
        
        if is_cyclical and sell_cond_count >= 2:
            is_shield_active = False
            downgrade_alert += " | ⚠️ 循環股逃命：強制撤除左側護盾！"

        ACTION_WAIT, ACTION_NO, ACTION_YES, ACTION_HOLD = "⏳ 【等待時機】", "❌ 【極度危險】", "✅ 【可以買進】", "🛡️ 【先觀察】"
        signal_text, color_border, signal_bg = "", "", ""
        
        opt_event_vanish = portfolio_data.get('opt_event_vanish', False) if portfolio_data else False
        opt_earnings_miss = portfolio_data.get('opt_earnings_miss', False) if portfolio_data else False
        opt_leader_crash = portfolio_data.get('opt_leader_crash', False) if portfolio_data else False
        opt_margin_call = portfolio_data.get('opt_margin_call', False) if portfolio_data else False

        f_margin = portfolio_data.get('f_margin', False) if portfolio_data else False
        f_cashflow = portfolio_data.get('f_cashflow', False) if portfolio_data else False
        f_cashlevel = portfolio_data.get('f_cashlevel', False) if portfolio_data else False
        f_fail = (mode == "長線價值波段單") and not (f_margin and f_cashflow and f_cashlevel)

        is_bottom_volume = (current_price <= ma120 * 1.05) and (vol_ratio >= 2.0)
        is_fish_head = is_bottom_volume
        is_macro_panic = opt_margin_call or is_macro_panic_global

        is_action_needed = False
        is_golden = False

        if opt_event_vanish: signal_text, color_border, signal_bg = f"{ACTION_NO} 買進核心理由消失，強制撤退。", "#e74c3c", "#3a1515"; is_shield_active = False; is_action_needed = True
        elif opt_earnings_miss: signal_text, color_border, signal_bg = f"{ACTION_NO} 財報落差，強制全數平倉！", "#e74c3c", "#3a1515"; is_shield_active = False; is_action_needed = True
        elif opt_leader_crash: signal_text, color_border, signal_bg = f"{ACTION_NO} 領頭羊暴跌！跟風股直接逃命。", "#e74c3c", "#3a1515"; is_shield_active = False; is_action_needed = True
        elif kdj_danger_trap: signal_text, color_border, signal_bg = f"{ACTION_NO} 高檔割韭菜，無條件快逃！", "#e74c3c", "#3a1515"; is_shield_active = False; is_action_needed = True
        elif entry_price > 0 and roi_pct <= -10.0: signal_text, color_border, signal_bg = f"{ACTION_NO} 觸發 10% 絕對停損！", "#e74c3c", "#3a1515"; is_shield_active = False; is_action_needed = True
        elif sell_cond_count >= 2 and roi_pct > 0: signal_text, color_border, signal_bg = f"{ACTION_HOLD} 觸發賣出三要件，分批慢慢賣出。", "#f1c40f", "#3a3015"; is_action_needed = True
        elif sell_cond_count >= 2 and roi_pct <= 0: signal_text, color_border, signal_bg = f"{ACTION_NO} 觸發賣出三要件，短線轉空。", "#e74c3c", "#3a1515"; is_action_needed = True
        elif is_macro_panic: 
            if current_price <= buy_high: signal_text, color_border, signal_bg = f"{ACTION_YES} 融資斷頭潮來臨！長線重壓！", "#00FF00", "#153a20"; is_golden = True
            else: signal_text, color_border, signal_bg = f"{ACTION_WAIT} 等斷頭賣壓打下來再撿！", "#f39c12", "#3a3015"
        elif is_break_ma5 and weekly_k_bullish: signal_text, color_border, signal_bg = f"{ACTION_HOLD} 日線破5MA，但週K保護中。", "#f39c12", "#3a2515"
        else:
            if is_shield_active and gain < -2.0: 
                signal_text, color_border, signal_bg = f"🛡️ 【長線護盾】 自動過濾假跌破，安心抱單！", "#3498db", "#152a3a"
            elif val_code == "3": 
                signal_text, color_border, signal_bg = f"{ACTION_NO} 股價太貴已達天花板，千萬別買！", "#e74c3c", "#3a1515"
            elif mode == "長線價值波段單" and manual_target > 0 and ((manual_target - current_price) / max(current_price, 0.001) * 100) < 30.0:
                signal_text, color_border, signal_bg = f"{ACTION_WAIT} 潛在報酬小於30%，不值得冒險！", "#f39c12", "#3a3015"
            elif (is_ma_bullish or buy_cond_count >= 1) and current_price > (buy_high * 1.03):
                signal_text, color_border, signal_bg = f"{ACTION_WAIT} 已嚴重偏離防守區，請等拉回！", "#f39c12", "#3a3015"
            elif is_cold_to_hot:
                signal_text, color_border, signal_bg = f"{ACTION_YES} 🌟 冷門轉機股甦醒！具備妖股潛力！", "#e056fd", "#2c153a"; is_golden = True
            elif (buy_cond_count >= 1 or is_ma_bullish or w_bottom_breakout) and not f_fail: 
                signal_text, color_border, signal_bg = f"{ACTION_YES} 突破或均線多頭確立！", "#00FF00", "#153a20"; is_golden = True
            elif is_fish_head and not f_fail: 
                signal_text, color_border, signal_bg = f"{ACTION_WAIT} 發現魚頭特徵！準備無聲吃貨。", "#3498db", "#152a3a"
            elif f_fail and (buy_cond_count >= 1 or is_fish_head): 
                signal_text, color_border, signal_bg = f"{ACTION_NO} 三大財報防彈網未過！拒絕許可。", "#e74c3c", "#3a1515"
            else: 
                signal_text, color_border, signal_bg = f"{ACTION_HOLD} 股價卡在區間，在旁邊看戲。", "#ccc", "#2b2b36"

        # [V16.0 核心升級] AI 焦點速讀標籤生成
        ai_tags = []
        if chip_code == "1": ai_tags.append("🔴 大戶進駐")
        elif chip_code == "2": ai_tags.append("🟢 外資提款")
        if is_break_ma5: ai_tags.append("🟢 破 5MA")
        if current_price < ma20: ai_tags.append("🟢 破月線")
        if is_attack_vol: ai_tags.append("🔴 爆量攻擊")
        if w_bottom_breakout: ai_tags.append("🔴 W底突破")
        if is_ma_bullish: ai_tags.append("🔴 均線多頭")
        if is_shooting_star: ai_tags.append("🟢 避雷針")
        if not ai_tags: ai_tags.append("⚪ 量縮整理")

        if is_shield_active:
            spotter_status, spotter_color, spotter_bg = "🛡️ 左側波段護盾全開", "#3498db", "#152a3a"
            spotter_html = f"<div class='my-tooltip' style='background:{spotter_bg}; padding:10px; border-radius:8px; margin-bottom:12px; border-left: 5px solid {spotter_color}; display:block; width:100%;'><div style='font-size:12px; color:#ddd;'>🚨 撤退雷達：<strong style='color:{spotter_color}; font-size:14px;'>{spotter_status}</strong></div></div>"
        else:
            spotter_status, spotter_color, spotter_bg = "🟢 目前安全", "#2ecc71", "#153a20"
            if sell_cond_count >= 3: spotter_status, spotter_color, spotter_bg = "🔴 三要件全亮，逃命！", "#e74c3c", "#3a1515"
            elif sell_cond_count == 2: spotter_status, spotter_color, spotter_bg = "🟡 多危險訊號準備賣出", "#f1c40f", "#3a3015"
            elif sell_cond_count == 1: spotter_status, spotter_color, spotter_bg = "🟡 異常跌勢提高警戒", "#f39c12", "#3a2515"
            spotter_html = f"<div class='my-tooltip' style='background:{spotter_bg}; padding:10px; border-radius:8px; margin-bottom:12px; border-left: 5px solid {spotter_color}; display:block; width:100%;'><div style='font-size:12px; color:#ddd; margin-bottom:6px;'>🚨 撤退雷達：<strong style='color:{spotter_color}; font-size:14px;'>{spotter_status}</strong></div><div style='font-size:12px; color:#eee; display:flex; justify-content:space-between;'><span>{'🔴' if is_huge_vol else '⚪'} 爆量</span><span>{'🔴' if is_shooting_star else '⚪'} 避雷針(上影線)</span><span>{'🔴' if is_break_ma5 else '⚪'} 破5MA</span></div></div>"

        jail_html = ""
        if len(hist) >= 7:
            return_6d = ((current_price - max(float(hist['Close'].iloc[-6]), 0.001)) / max(float(hist['Close'].iloc[-6]), 0.001)) * 100
            if return_6d >= 20.0:
                jail_html = f"<div class='my-tooltip' style='background:#3a3015; padding:10px; border-radius:8px; margin-bottom:12px; border-left: 5px solid #f39c12; display:block; width:100%;'><div style='font-size:12px; color:#ddd;'>⚖️ 處置警示：<strong style='color:#f39c12;'>⚠️ 逼近紅線</strong></div></div>"

        if val_code == "3": exit_s, exit_p, exit_c, exit_bg = "🔴 估值昂貴強制定利線", f"{current_price:.1f}", "#e74c3c", "#3a1515"
        elif roi_pct > 15.0: exit_s, exit_p, exit_c, exit_bg = "🛡️ 跌破五日線就停利", f"{ma5:.1f}", "#e67e22", "#3a2515"
        else: exit_s, exit_p, exit_c, exit_bg = "🚪 10% 風控底線", f"{entry_price * 0.9 if entry_price > 0 else main_cost * 0.95:.1f}", "#e74c3c", "#2c153a"

        buy_zone = f"{buy_low} - {buy_high}"
        shd_display = "❓ 待查" if override_shd_raw == "?" else f"{override_shd_raw}分"
        
        extra_badge = ""
        if is_high_yield: extra_badge += "💰 高殖利防禦 "
        if is_cyclical: extra_badge += "🔄 深度循環 "
        if w_bottom_breakout: extra_badge += "🎯 W底突破 "
        if is_cold_to_hot: extra_badge += "🌟 妖股甦醒 "

        return {
            "name": stock_name, "code": symbol, "price": current_price, "gain": gain, "cost": main_cost, 
            "cost_label": cost_label, "buy_zone": buy_zone, "shd": shd_display, "chip_code": chip_code, 
            "chip": CHIP_MAP.get(chip_code, "⚖️"), "val_code": val_code, "val": VAL_MAP.get(val_code, "⚪"), 
            "kdj": kdj_signal, "chip_desc": chip_desc, "val_desc": val_desc, "kdj_desc": kdj_desc, 
            "downgrade_alert": downgrade_alert, "signal": signal_text, "color": color_border, 
            "signal_bg": signal_bg, "ai_tags": ai_tags, "extra_badge": extra_badge.strip(), "exit_s": exit_s, "exit_price": exit_p, 
            "exit_color": exit_c, "exit_bg": exit_bg, "vol": vol, "open": open_p, "high": high_p, "low": low_p, 
            "raw_data": symbol_data, "cat": category_type, "spotter_html": spotter_html, "buy_html": buy_html, 
            "jail_html": jail_html, "buy_cond_count": buy_cond_count, "diff_from_cost": diff_from_cost, 
            "vol_ratio": vol_ratio, "sell_cond_count": sell_cond_count, "is_overridden": is_overridden, 
            "auto_target": auto_target_price, "is_shield_active": is_shield_active, "is_ma_bullish": is_ma_bullish,
            "roi_pct": roi_pct, "eps_val": portfolio_data.get('eps', 0.0) if portfolio_data else 0.0,
            "pe_val": portfolio_data.get('pe', 0.0) if portfolio_data else 0.0, "w_bottom_breakout": w_bottom_breakout,
            "is_high_yield": is_high_yield, "is_cyclical": is_cyclical,
            "is_action_needed": is_action_needed, "is_golden": is_golden
        }
    except Exception as e: return None

def calc_real_profit(cost, price, qty):
    if cost <= 0: return 0, 0
    buy_val = cost * qty * 1000
    sell_val = price * qty * 1000
    profit = sell_val - buy_val - max(20, int(buy_val * 0.001425)) - max(20, int(sell_val * 0.001425)) - int(sell_val * 0.003)
    return profit, (profit/buy_val)*100 if buy_val > 0 else 0

# ==========================================
# 🖥️ 側邊欄控制台 (V16.0)
# ==========================================
with st.sidebar:
    st.markdown("<h2 style='color:#f1c40f; text-align:center;'>⚙️ 戰術控制台</h2>", unsafe_allow_html=True)
    
    st.markdown("<div style='background:#16191f; padding:10px; border-radius:8px; border: 1px solid #3498db; margin-bottom:10px;'><h4 style='color:#3498db; margin-top:0px; font-size:14px;'>📡 智能情報萃取器</h4>", unsafe_allow_html=True)
    with st.form(key='intel_form', clear_on_submit=True): 
        intel_input = st.text_area("貼上密碼 (支援全半形)：", placeholder="2313:?:?:1:?")
        if st.form_submit_button('📥 匯入預覽'):
            matches = [x.strip() for x in re.split(r'[,\s]+', intel_input.replace("INTEL:", "").replace("ＩＮＴＥＬ：", "").replace("：", ":").replace("？", "?").replace("，", ",")) if x.count(':') >= 3]
            st.session_state.temp_intel = [] 
            if matches:
                for s in matches:
                    c = s.split(":")[0].strip()
                    if c and c not in st.session_state.portfolio and c not in st.session_state.pinned_stocks: 
                        st.session_state.temp_intel.append({'code': c, 'raw_data': s, 'cat': 'intel'})
                st.rerun()
    st.markdown("</div>", unsafe_allow_html=True)

    st.markdown("---")
    st.markdown("<h4 style='color:#00FF00;'>🚀 全市場深度掃描</h4>", unsafe_allow_html=True)
    
    def run_parallel_scan(target_codes, mode):
        results = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            future_to_code = {executor.submit(calculate_tactical_signals, f"{c}:?:?:?:?", "scan", "短線技術動能單", 0.0, None, {}, is_panic): c for c in target_codes}
            for future in concurrent.futures.as_completed(future_to_code):
                try: d = future.result()
                except: d = None
                if d and "❌" not in d['signal']:
                    if mode == "golden" and (d['is_ma_bullish'] or d['w_bottom_breakout']) and d['buy_cond_count'] >= 1: results.append(d)
                    elif mode == "stealth" and d['vol_ratio'] >= 2.0 and d['diff_from_cost'] <= 15.0: results.append(d)
                    elif mode == "yield" and d['sell_cond_count'] < 2 and d['vol'] > 300 and (d['is_high_yield'] or d['is_cyclical']): results.append(d)
        return results

    if st.button("🚀 黃金起漲與魚身", use_container_width=True):
        with st.spinner("掃描中..."):
            st.session_state.scan_results = run_parallel_scan(FULL_MARKET_CODES, "golden")
            st.session_state.scan_mode = "golden"; st.rerun()
    if st.button("🕵️‍♂️ 魚頭潛伏與轉機", use_container_width=True):
        with st.spinner("掃描中..."):
            st.session_state.scan_results = run_parallel_scan(FULL_MARKET_CODES, "stealth")
            st.session_state.scan_mode = "stealth"; st.rerun()
    if st.button("🛡️ 總經防禦高息池", use_container_width=True):
        with st.spinner("掃描中..."):
            st.session_state.scan_results = run_parallel_scan([c for c, data in FUNDAMENTAL_DB.items() if data['Yield'] >= 5.0 or data['PE'] < 12.0], "yield")
            st.session_state.scan_mode = "yield"; st.rerun()
            
    st.markdown("---")
    sentinel_label = "🔕 關閉哨兵模式" if st.session_state.sentinel_active else "🔔 啟動哨兵模式"
    if st.button(sentinel_label, use_container_width=True):
        st.session_state.sentinel_active = not st.session_state.sentinel_active
        st.rerun()

# ==========================================
# 🖥️ 主戰情室版面
# ==========================================
col_title, col_sync, col_logout = st.columns([5, 1, 1])
with col_title: st.markdown("<h1 style='color:#FFB300; margin: 0;'>54088</h1>", unsafe_allow_html=True)
with col_sync:
    st.markdown("<div class='sync-btn'>", unsafe_allow_html=True)
    st.button("🔄 同步", use_container_width=True, on_click=cb_sync) 
    st.markdown("</div>", unsafe_allow_html=True)
with col_logout:
    st.markdown("<div class='lock-btn'>", unsafe_allow_html=True)
    st.button("🔒 鎖定", use_container_width=True, on_click=cb_logout)
    st.markdown("</div>", unsafe_allow_html=True)

weather_str, weather_color, is_bull_market, is_panic = get_market_weather()
st.markdown(f"<div style='text-align:right; color:#888; font-size:12px; margin-bottom:10px;'>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</div>", unsafe_allow_html=True)

port_count, pin_count, total_unrealized, action_needed, golden_targets, long_term_count = len(st.session_state.portfolio), len(st.session_state.pinned_stocks), 0, 0, 0, 0
current_manual_prices = st.session_state.get('manual_prices', {})

# Data processing loop
for code, p_data in st.session_state.portfolio.items():
    if p_data.get('mode') == '長線價值波段單': long_term_count += 1
    d = calculate_tactical_signals(p_data['raw_data'], p_data['cat'], mode=p_data.get('mode', '短線技術動能單'), manual_target=p_data.get('manual_target', 0.0), portfolio_data=p_data, manual_prices_dict=current_manual_prices, is_macro_panic_global=is_panic)
    if d:
        p, _ = calc_real_profit(p_data['entry_price'], d['price'], p_data['qty'])
        total_unrealized += p
        if d['is_action_needed']: action_needed += 1

golden_list_marketing = []
for code, p_data in st.session_state.pinned_stocks.items():
    d = calculate_tactical_signals(p_data['raw_data'], p_data['cat'], manual_prices_dict=current_manual_prices, is_macro_panic_global=is_panic)
    if d and d['is_golden']: 
        golden_targets += 1
        golden_list_marketing.append(d)

market_suggestion = "🩸 【斷頭潮啟動】大盤恐慌崩跌！切換「左側價值」重壓便宜股！" if is_panic else ("💡 大盤多頭健康 ➡️ 適合【🚀 右側動能狙擊】" if is_bull_market else "💡 大盤恐慌震盪 ➡️ 適合【🛡️ 左側價值佈局】")
market_bg, market_border = ("#3a1515", "#e74c3c") if is_panic else ("#1e222b", "#2ecc71" if is_bull_market else "#f1c40f")
long_ratio = (long_term_count / port_count * 100) if port_count > 0 else 0

# [V16.0 核心升級] 盤前/盤後總結速報與熱圖模組
sector_mock_html = ""
if is_bull_market: sector_mock_html = "<span style='background:#153a20; color:#00FF00; padding:2px 6px; border-radius:4px; font-size:12px;'>🔥 半導體/光通訊 流入</span>"
else: sector_mock_html = "<span style='background:#3a1515; color:#ff4d4d; padding:2px 6px; border-radius:4px; font-size:12px;'>❄️ 消費性電子/代工 流出</span>"

st.markdown(f"""
<div class='hud-box'>
<div class='hud-title' style='display:flex; justify-content:space-between;'><span>🌐 大將軍戰情總覽 (HUD)</span><span style='color:{weather_color};'>{weather_str}</span></div>
<div style='background:#1a1c23; padding:10px; border-radius:5px; border-left:3px solid {weather_color}; margin-bottom:10px; font-size:13px; color:#ddd;'>
<strong>🌅 每日戰情報告：</strong>大盤目前判定為 {weather_str.split(' ')[1]}。 {market_suggestion} {sector_mock_html}
</div>
<div class='hud-metric'><span style='color:#aaa;'>庫存 / 雷達</span> <strong style='color:#fff;'>{port_count} / {pin_count} 檔</strong></div>
<div class='hud-metric'><span style='color:#aaa;'>總未實現損益</span> <strong style='color:{'#ff4d4d' if total_unrealized>0 else '#00FF00'}; font-size:18px;'>{total_unrealized:+,.0f} 元</strong></div>
<div class='health-bar-bg'><div class='{'health-bar-fill-green' if total_unrealized >= 0 else 'health-bar-fill-red'}' style='width: {max(0, min(100, 50 + (total_unrealized / 50000) * 50))}%;'></div></div>
<div class='hud-metric' style='margin-top:10px; padding-top:10px; border-top:1px dashed #333;'><span style='color:#2ecc71;'>🎯 雷達可狙擊：<strong>{golden_targets} 檔</strong></span><span style='color:#e74c3c;'>🚨 庫存需撤退：<strong>{action_needed} 檔</strong></span></div>
</div>
""", unsafe_allow_html=True)

# [V16.0 核心升級] 戰情室哨兵模式警報 (JS Notification & Audio)
if st.session_state.sentinel_active:
    st.info("🔔 哨兵模式運作中 (自動背景輪詢監視)")
    if action_needed > 0 or golden_targets > 0:
        alert_msg = f"注意！發現 {action_needed} 檔庫存需撤退，{golden_targets} 檔雷達發出買進訊號！"
        st.markdown(f"<div style='background:#e74c3c; color:#fff; font-weight:bold; text-align:center; padding:10px; border-radius:5px; margin-bottom:15px; animation: blinker 2s linear infinite;'>🚨 哨兵警報：{alert_msg}</div>", unsafe_allow_html=True)
        components.html(f"""
            <script>
                var audio = new Audio('https://actions.google.com/sounds/v1/alarms/beep_short.ogg');
                audio.play().catch(function(error) {{ console.log("Audio play blocked by browser."); }});
                if (Notification.permission === 'granted') {{
                    new Notification('🚨 54088 哨兵警報', {{body: '{alert_msg}'}});
                }} else if (Notification.permission !== 'denied') {{
                    Notification.requestPermission().then(permission => {{
                        if (permission === 'granted') {{ new Notification('🚨 54088 哨兵警報', {{body: '{alert_msg}'}}); }}
                    }});
                }}
            </script>
        """, height=0)
    # Auto refresh every 30 seconds
    time.sleep(30)
    st.rerun()

# [V16.0 核心升級] 合規行銷文案引擎
if golden_list_marketing:
    with st.expander("📝 產生合規行銷文案引擎 (支援社群擴散)"):
        st.markdown("<div style='background:#1e222b; padding:10px; border-radius:8px; border-left:4px solid #f1c40f;'>", unsafe_allow_html=True)
        st.markdown("<p style='color:#fff; font-weight:bold; margin-bottom:5px;'>本日精選起漲盲盒：</p>", unsafe_allow_html=True)
        for idx, d in enumerate(golden_list_marketing):
            char_label = chr(65 + idx) # A, B, C...
            masked_code = d['code'][:2] + "X" + d['code'][3:] if len(d['code']) >= 4 else "XXX"
            reason = "帶量突破均線，底部起漲" if d['is_ma_bullish'] else "出現罕見攻擊量，主力進駐"
            st.markdown(f"<p style='color:#00FF00; margin:0;'>{char_label}. ({masked_code}) {reason}，極具波段潛力！</p>", unsafe_allow_html=True)
        st.markdown("<p style='color:#aaa; font-size:11px; margin-top:10px;'>免責聲明：本資訊僅供盤勢觀測與量化教學使用，完全符合台灣證券交易法規，任何行銷文字均無推薦買賣之意。投資人應獨立判斷，自負盈虧。</p>", unsafe_allow_html=True)
        st.markdown("</div>", unsafe_allow_html=True)

st.markdown("<h3 style='color:#f1c40f; margin-top:10px; border-bottom: 2px solid #f1c40f; padding-bottom:5px;'>🔍 手動探測雷達</h3>", unsafe_allow_html=True)
search_query = st.text_input("📝 手動搜尋標的 (可直接輸入代號 '2313' 或名稱 '華通') [輸入後按 Enter]：", key="search_input")

def render_stock_card(d, ui_key_prefix, is_portfolio=False, p_data=None):
    gain_color, gain_bg = ('#ff4d4d', '#3a1515') if d['gain']>0 else (('#00FF00', '#153a20') if d['gain']<0 else ('#aaaaaa', '#333333'))
    downgrade_html = f"<div style='background-color:#3a2515; color:#f39c12; font-size:13px; font-weight:bold; padding:6px 12px; border-radius:5px; margin-bottom:10px;'>{d['downgrade_alert']}</div>" if d.get('downgrade_alert') else ""
    extra_badge_html = f"<span class='special-badge'>{d['extra_badge']}</span>" if d.get('extra_badge') else ""
    
    # [V16.0 核心升級] AI 焦點標籤 HTML 渲染
    ai_tags_html = "".join([f"<span class='{'danger-badge' if '🔴' in tag else 'special-badge'}'>{tag}</span>" for tag in d['ai_tags']])

    port_html = ""
    if is_portfolio and p_data:
        port_html = f"<div style='background:#10141d; padding:10px; border-radius:6px; margin-bottom:12px;'><div style='display:flex; justify-content:space-between; align-items:center;'><span style='background-color:{'#3498db' if p_data.get('mode') == '長線價值波段單' else '#e67e22'}; color:#fff; font-size:12px; padding:2px 8px; border-radius:4px;'>🎮 {p_data.get('mode')}</span><span style='color:#aaa; font-size:12px;'>🎯 目標價：[ 您設定: <strong style='color:#f1c40f;'>{p_data.get('manual_target', 0.0):.1f}</strong> | 系統估值: <strong style='color:#00d2ff;'>{d['auto_target']:.1f}</strong> ]</span></div><div style='color:#e056fd; font-size:13px; margin-top:5px;'>🌟 買進核心理由：{p_data.get('catalyst')}</div></div>"

    st.markdown(f"""
<div style="border: 2px solid {d['color']}; border-radius: 8px; padding: 15px; background-color: #16191f; margin-bottom: 5px;">
{downgrade_html}{port_html}
<div style="font-weight:bold; font-size:18px; margin-bottom:5px;">{d['name']} ({d['code']}) | 🛡️ {d['shd']}</div>
<div style="font-size:32px; font-weight:bold; margin-bottom: 10px; display:flex; gap:12px;">{d['price']:.2f} <span style="font-size:16px; color:{gain_color}; background-color:{gain_bg}; padding:4px 10px; border-radius:6px;">{d['gain']:+.1f}%</span></div>
<div style="margin-bottom: 5px;">{ai_tags_html}</div>
<div style="margin-bottom: 15px;">{extra_badge_html}<span class="my-tooltip info-badge">{d['chip']}<span class="my-tooltiptext">{d['chip_desc']}</span></span><span class="my-tooltip info-badge">📊 {d['val']}<span class="my-tooltiptext">{d['val_desc']}</span></span><span class="my-tooltip info-badge">{d['kdj']}<span class="my-tooltiptext">{d['kdj_desc']}</span></span></div>
{d['buy_html']}{d['spotter_html']}{d['jail_html']}
<div style="background:#1a1c23; border-radius:6px; padding:12px; margin-bottom:12px; border-left: 4px solid #3498db;"><div style="display:flex; justify-content:space-between;"><span style="color:#888;">目前防守底線：{d['cost_label']}</span><strong style="color:#fff;">{d['cost']}</strong></div><div style="display:flex; justify-content:space-between;"><span style="color:#888;">🎯 最佳入場區</span><strong style="color:{d['color']};">[ {d['buy_zone']} ]</strong></div><div style="display:flex; justify-content:space-between;"><span style="color:#888;">{d['exit_s'].split('：')[0]}</span><strong style="color:{d['exit_color']};">{d['exit_price']}</strong></div></div>
<div style="background:{d['signal_bg']}; padding:10px; border-radius:6px; text-align:center; margin-bottom:10px; border: 1px solid {d['color']}40;"><span style="color:#aaa; font-size:12px;">⚡ 總指揮決策指令：</span><br><strong style="color:{d['color']}; font-size:18px;">{d['signal']}</strong></div></div>""", unsafe_allow_html=True)
    
    with st.expander(f"📌 參數設定與雷達 ({d['code']})"):
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

    with st.expander(f"💼 風險試算與買進設定 ({d['code']})"):
        c1, c2 = st.columns(2)
        c1.number_input("買進價格", value=float(d['price']), key=f"c_{ui_key_prefix}_{d['code']}")
        c2.number_input("張數", value=1.0, key=f"q_{ui_key_prefix}_{d['code']}")
        
        v_mode = st.selectbox("🎯 作戰屬性", ["短線技術動能單", "長線價值波段單"], key=f"mode_{ui_key_prefix}_{d['code']}")
        if v_mode == "長線價值波段單":
            st.markdown("<div style='background:#10141d; padding:10px; border-radius:5px;'>", unsafe_allow_html=True)
            e1, e2 = st.columns(2)
            e1.number_input("預估 EPS", value=0.0, step=0.1, key=f"eps_{ui_key_prefix}_{d['code']}")
            e2.number_input("合理 PE", value=0.0, step=0.5, key=f"pe_{ui_key_prefix}_{d['code']}")
            st.checkbox("✅ 本業賺錢 (營益率為正)", key=f"f_margin_{ui_key_prefix}_{d['code']}")
            st.checkbox("✅ 自由現金流為正", key=f"f_cashflow_{ui_key_prefix}_{d['code']}")
            st.checkbox("✅ 現金水位 > 2個月", key=f"f_cashlevel_{ui_key_prefix}_{d['code']}")
            st.markdown("</div>", unsafe_allow_html=True)
        else:
            st.number_input("🎯 手動目標價", value=0.0, step=1.0, key=f"tval_{ui_key_prefix}_{d['code']}")

        st.text_input("🌟 買進核心理由 (消失即砍)", key=f"cat_{ui_key_prefix}_{d['code']}")
        st.button(f"⚡ 買進庫存！", key=f"buy_{ui_key_prefix}_{d['code']}", use_container_width=True, on_click=cb_buy_stock, args=(d['code'], f"{d['code']}:{ns}:0:{nc}:{nv}:0", d['cat'], ui_key_prefix))

if search_query:
    raw_input = search_query.strip().replace('.TW', '').replace('.TWO', '')
    match_digits = re.search(r'\d{4,}', raw_input)
    clean_code = match_digits.group() if match_digits else None
    if not clean_code:
        reverse_dict = {v: k for k, v in TW_STOCKS.items()}
        for name, code in reverse_dict.items():
            if raw_input in name: clean_code = code; break
                
    if clean_code:
        d = calculate_tactical_signals(f"{clean_code}:?:?:?:?", "search", manual_prices_dict=current_manual_prices, is_macro_panic_global=is_panic)
        if d: render_stock_card(d, ui_key_prefix="search_res")
    else: st.error(f"❌ 找不到「{raw_input}」的對應代號。")

if st.session_state.temp_intel:
    st.markdown("<h3 style='color:#00d2ff; margin-top:20px; border-bottom: 2px solid #00d2ff; padding-bottom:5px;'>👁️ 暫存情報觀測區</h3>", unsafe_allow_html=True)
    cols = st.columns(2)
    for i, item in enumerate(st.session_state.temp_intel):
        with cols[i % 2]:
            d = calculate_tactical_signals(item['raw_data'], item['cat'], manual_prices_dict=current_manual_prices, is_macro_panic_global=is_panic)
            if d: render_stock_card(d, ui_key_prefix=f"temp_{item['code']}")

def render_portfolio_card(code, p_data):
    d = calculate_tactical_signals(p_data['raw_data'], p_data['cat'], mode=p_data.get('mode', '短線技術動能單'), manual_target=p_data.get('manual_target', 0.0), portfolio_data=p_data, manual_prices_dict=current_manual_prices, is_macro_panic_global=is_panic)
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
            with cols[i % 2]: render_stock_card(calculate_tactical_signals(p_data['raw_data'], p_data['cat'], manual_prices_dict=current_manual_prices, is_macro_panic_global=is_panic), ui_key_prefix="pinned")

if scan_mode_current := st.session_state.get('scan_mode', ""):
    st.markdown(f"<h2 style='color:#00FF00; margin-top:30px; border-bottom: 2px solid #00FF00; padding-bottom:5px;'>⚡ 掃描結果</h2>", unsafe_allow_html=True)
    if not st.session_state.scan_results:
        st.warning("⚠️ 掃描完畢，未發現符合極限條件的標的！")
    else:
        cols = st.columns(2)
        for i, d in enumerate([x for x in st.session_state.scan_results if x['code'] not in st.session_state.portfolio and x['code'] not in st.session_state.pinned_stocks]):
            with cols[i % 2]: render_stock_card(d, ui_key_prefix="scan_res")
