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

# ==========================================
# 🛡️ 步驟一：絕對置頂的頁面與記憶體初始化
# ==========================================
st.set_page_config(layout="wide", page_title="54088 - 終極大腦 V16.8", initial_sidebar_state="expanded")

if 'manual_prices' not in st.session_state: st.session_state.manual_prices = {} 
if 'scan_results' not in st.session_state: st.session_state.scan_results = []
if 'scan_mode' not in st.session_state: st.session_state.scan_mode = ""
if 'temp_intel' not in st.session_state: st.session_state.temp_intel = []
if 'sentinel_active' not in st.session_state: st.session_state.sentinel_active = False
if 'pinned_stocks' not in st.session_state: st.session_state.pinned_stocks = {}
if 'portfolio' not in st.session_state: st.session_state.portfolio = {}
if 'login_error' not in st.session_state: st.session_state.login_error = False
if 'api_cache' not in st.session_state: st.session_state.api_cache = {}

COMMANDER_PIN = "0826"
DB_FILE = "54088_database.json"
MAX_CAPACITY = 40

if 'authenticated' not in st.session_state: 
    st.session_state.authenticated = (st.query_params.get("auth") == "54088")

def load_db():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r", encoding="utf-8") as f: return json.load(f)
        except: pass
    return {"pinned_stocks": {}, "portfolio": {}}

def save_db():
    try:
        with open(DB_FILE, "w", encoding="utf-8") as f: 
            json.dump({"pinned_stocks": st.session_state.pinned_stocks, "portfolio": st.session_state.portfolio}, f, ensure_ascii=False, indent=4)
    except: pass

if 'db_loaded' not in st.session_state:
    db_data = load_db()
    st.session_state.pinned_stocks = db_data.get("pinned_stocks", {})
    st.session_state.portfolio = db_data.get("portfolio", {})
    st.session_state.db_loaded = True

# ==========================================
# 🛡️ 步驟二：所有按鈕動作指令 (Callbacks)
# ==========================================
def cb_login():
    if st.session_state.pwd_input == COMMANDER_PIN:
        st.session_state.authenticated = True
        st.query_params["auth"] = "54088"
    else: st.session_state.login_error = True

def cb_ui_logout():
    st.session_state.authenticated = False
    if "auth" in st.query_params: del st.query_params["auth"]

def cb_ui_sync(): 
    st.session_state.temp_intel = []
    st.session_state.api_cache = {}

def cb_load_hot_themes():
    hot_codes = ["3324", "3017", "2408", "3260", "2330", "2317", "1519", "2603"]
    st.session_state.temp_intel = []
    for c in hot_codes:
        if c not in st.session_state.portfolio and c not in st.session_state.pinned_stocks:
            st.session_state.temp_intel.append({'code': c, 'raw_data': f"{c}:?:?:?:?", 'cat': 'theme'})

def cb_pin_stock(code, raw_data, cat):
    if len(st.session_state.pinned_stocks) >= MAX_CAPACITY: return
    st.session_state.pinned_stocks[code] = {'raw_data': raw_data, 'cat': cat}
    st.session_state.temp_intel = [x for x in st.session_state.temp_intel if x.get('code') != code]
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
    except: 
        cost, qty, mode, manual_target, catalyst = 0.0, 1.0, "短線技術動能單", 0.0, ""
    
    st.session_state.portfolio[code] = {
        "entry_price": round(cost, 2), "qty": round(qty, 3), "raw_data": raw_data, 
        "cat": cat, "mode": mode, "manual_target": manual_target, "catalyst": catalyst,
        "opt_event_vanish": False, "opt_earnings_miss": False, "opt_leader_crash": False
    }
    if code in st.session_state.pinned_stocks: del st.session_state.pinned_stocks[code]
    st.session_state.temp_intel = [x for x in st.session_state.temp_intel if x.get('code') != code]
    save_db()

def cb_sell_stock(code):
    if code in st.session_state.portfolio:
        del st.session_state.portfolio[code]
        save_db()

# ==========================================
# 🛡️ 步驟三：系統解鎖驗證
# ==========================================
if not st.session_state.authenticated:
    st.markdown("<h1 style='text-align: center; color: #444; margin-top: 20vh; font-family: monospace; letter-spacing: 5px; font-size: 2rem;'>54088</h1>", unsafe_allow_html=True)
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        st.text_input(" ", type="password", key="pwd_input", placeholder="請輸入指揮官授權密碼")
        st.button("系統解鎖", use_container_width=True, on_click=cb_login)
        if st.session_state.get("login_error"):
            st.error("❌ 密碼錯誤，拒絕存取。")
            st.session_state.login_error = False
    st.stop()

# ==========================================
# 🎨 視覺與樣式定義
# ==========================================
st.markdown('''<style>
.stApp { background-color: #0b0c0f !important; color: #fff !important; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
div[data-testid="stSidebar"] { background-color: #12141a !important; border-right: 1px solid #333 !important; }
div[data-testid="stButton"] > button { background-color: #1e1e24 !important; border: 1px solid #444 !important; transition: all 0.2s ease-in-out; }
div[data-testid="stButton"] > button p { color: #ffffff !important; font-weight: bold !important; font-size: 15px !important; }
div[data-testid="stButton"] > button:hover { border-color: #f1c40f !important; transform: translateY(-2px); box-shadow: 0 4px 10px rgba(241,196,15,0.2); }
[data-testid="stExpander"] details summary { background-color: #16191f !important; border: 1px solid #3498db !important; border-radius: 8px !important; margin-bottom: 5px !important; }
[data-testid="stExpander"] details summary p { color: #f1c40f !important; font-weight: 900 !important; font-size: 16px !important; }
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
.tactical-summary { background: #000; border-top: 1px dashed #444; margin-top: 10px; padding: 10px; font-size: 14px; color: #f1c40f; font-weight: bold; border-radius: 5px;}
.tactical-danger { background: #1a0505; border-top: 1px dashed #e74c3c; margin-top: 10px; padding: 10px; font-size: 15px; color: #ff4d4d; font-weight: bold; border-radius: 5px;}
</style>''', unsafe_allow_html=True)

# ==========================================
# 📡 數據快取與大盤天候引擎 (全市場 100% 收錄)
# ==========================================
@st.cache_data(ttl=86400, show_spinner=False)
def fetch_official_fundamentals():
    dynamic_data, api_names = {}, {}
    # 讀取上市
    try:
        res = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL", timeout=5)
        if res.status_code == 200:
            for item in res.json():
                code = item.get('Code', '').strip()
                if code.isdigit():
                    api_names[code] = item.get('Name', code)
                    dynamic_data[code] = {
                        'PE': float(item.get('PeRatio', 0)) if item.get('PeRatio', '-').replace('.','',1).isdigit() else 999.0,
                        'Yield': float(item.get('DividendYield', 0)) if item.get('DividendYield', '-').replace('.','',1).isdigit() else 0.0,
                        'PB': float(item.get('PbRatio', 0)) if item.get('PbRatio', '-').replace('.','',1).isdigit() else 999.0
                    }
    except: pass
    # 讀取上櫃
    try:
        tpex_res = requests.get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_peratio_analysis", timeout=5)
        if tpex_res.status_code == 200:
            for item in tpex_res.json():
                code = item.get('SecuritiesCompanyCode', '').strip()
                if code.isdigit():
                    api_names[code] = item.get('CompanyName', code)
                    dynamic_data[code] = {
                        'PE': float(item.get('PERatio', 0)) if item.get('PERatio', '-').replace('.','',1).isdigit() else 999.0,
                        'Yield': float(item.get('YieldRatio', 0)) if item.get('YieldRatio', '-').replace('.','',1).isdigit() else 0.0,
                        'PB': float(item.get('PBRatio', 0)) if item.get('PBRatio', '-').replace('.','',1).isdigit() else 999.0
                    }
    except: pass
    return dynamic_data, api_names

FUNDAMENTAL_DB, API_NAMES = fetch_official_fundamentals()
# 確保全市場代碼 100% 收錄，絕無漏網之魚
FULL_MARKET_CODES = list(API_NAMES.keys()) if API_NAMES else ["2330", "2317", "2454", "2603", "2408"]

TW_STOCKS = {"2330":"台積電", "2317":"鴻海", "2454":"聯發科", "2382":"廣達", "2303":"聯電", "2603":"長榮", "2408":"南亞科"}
TW_STOCKS.update(API_NAMES)

@st.cache_data(ttl=300, show_spinner=False)
def get_market_weather_cached():
    try:
        tw50 = yf.Ticker("0050.TW").history(period="3mo").dropna(subset=['Close'])
        try:
            twii = yf.Ticker("^TWII").history(period="1d").dropna(subset=['Close'])
            twii_str = f"加權指數: {float(twii['Close'].iloc[-1]):,.0f} 點"
        except: twii_str = ""
        if tw50.empty: return "資料連線中", "#888", False, False
        c50 = float(tw50['Close'].iloc[-1])
        ma20 = float(tw50['Close'].rolling(20).mean().iloc[-1])
        gain = ((c50 - float(tw50['Close'].iloc[-2])) / float(tw50['Close'].iloc[-2])) * 100
        is_bull = c50 > ma20 or gain > 0
        display_idx = twii_str if twii_str else f"0050: {c50:.1f}"
        is_panic = (gain <= -4.0) or (c50 < float(tw50['Close'].rolling(60).mean().iloc[-1]) * 0.95)
        if is_panic: return f"🌩️ 恐慌斷頭潮 ({display_idx})", "#e74c3c", is_bull, True
        elif c50 > ma20: return f"☀️ 多頭順風環境 ({display_idx})", "#2ecc71", is_bull, False
        else: return f"☁️ 空頭震盪環境 ({display_idx} / 破月線)", "#f1c40f", is_bull, False
    except: return "📡 大盤資料獲取中...", "#888", False, False

weather_str, weather_color, is_bull_market, is_panic = get_market_weather_cached()
current_manual_prices = st.session_state.get('manual_prices', {})

# V16.8 降頻防禦快取：徹底解決 Yahoo API 阻擋
def get_stock_history_safe(symbol, is_slow_scan=False):
    cache_key = f"hist_{symbol}"
    if cache_key in st.session_state.api_cache:
        cached_time, data = st.session_state.api_cache[cache_key]
        if (datetime.now() - cached_time).seconds < 600: # 快取拉長到 10 分鐘，防禦更佳
            return data
            
    hist = pd.DataFrame()
    for ext in [".TW", ".TWO"]:
        try:
            # 加入擬真人類的延遲，避免密集轟炸
            if is_slow_scan: time.sleep(random.uniform(0.3, 0.8))
            tk = yf.Ticker(symbol + ext)
            temp_hist = tk.history(period="6mo").dropna(subset=['Close'])
            if not temp_hist.empty and len(temp_hist) > 15:
                hist = temp_hist; break
        except: pass
        
    if not hist.empty:
        cp = float(hist['Close'].iloc[-1])
        prev_p = max(float(hist['Close'].iloc[-2]), 0.001)
        res = (hist, cp, prev_p)
        st.session_state.api_cache[cache_key] = (datetime.now(), res)
        return res
        
    return pd.DataFrame(), 0.0, 0.0

# ==========================================
# 🧠 戰術演算法核心 (全極簡白話文 + 絕對容錯)
# ==========================================
def calculate_tactical_signals(symbol_data, category_type="main", mode="短線技術動能單", manual_target=0.0, portfolio_data=None, manual_prices_dict=None, is_macro_panic_global=False, is_slow_scan=False):
    try:
        parts = symbol_data.split(":")
        symbol = parts[0].strip()
        if not symbol: return None
        stock_name = TW_STOCKS.get(symbol, f"個股 {symbol}") 
        
        chip_code = parts[3].strip() if len(parts) > 3 else "?"
        chip_desc = {"1":"大戶/法人進駐中", "2":"主力大量倒貨", "0":"多空籌碼觀望"}.get(chip_code, "無籌碼資料")
        
        fund_info = FUNDAMENTAL_DB.get(symbol, {})
        dyn_pe, dynamic_pb = fund_info.get('PE', 999.0), fund_info.get('PB', 999.0)
        val_code = "1" if dyn_pe < 12.0 else ("3" if dyn_pe > 25.0 else "2")
        val_desc = f"PE:{dyn_pe if dyn_pe!=999.0 else '-'}, PB:{dynamic_pb if dynamic_pb!=999.0 else '-'}"
        
        hist, current_price, prev_price = get_stock_history_safe(symbol, is_slow_scan)
        
        # [防禦網] 若斷線或無資料，溫和提示
        if hist.empty or current_price <= 0:
            return {
                "name": stock_name, "code": symbol, "price": 0.0, "gain": 0.0, "cost": 0.0, "cost_label": "資料建檔中", "buy_zone": "0-0",
                "shd": "?", "chip_code": chip_code, "chip": "⚖️", "val_code": val_code, "val": "⚪",
                "kdj": "⚠️ 無法判斷", "chip_desc": "無資料", "val_desc": "無資料", "kdj_desc": "暫無資料", 
                "downgrade_alert": "", "signal": "❌ 【無最新報價】系統已暫停此檔監視", 
                "color": "#444", "signal_bg": "#111", "ai_tags": ["⚠️ 待查"], "exit_s": "未知", "exit_price": "0", "exit_color": "#888", "exit_bg": "#333", 
                "raw_data": symbol_data, "cat": category_type, "spotter_html": "", "buy_html": "", "jail_html": "", 
                "auto_target": 0.0, "is_shield_active": False, "roi_pct": 0.0, "is_action_needed": False, "is_golden": False, "is_first_red": False, 
                "tactical_summary": "📡 目前無法取得此標的之最新報價，建議稍後再試。", "is_high_yield": False, "is_cyclical": False, "vol_ratio": 0.0, "diff_from_cost": 0.0, "vol": 0
            }

        open_p, high_p, low_p = float(hist['Open'].iloc[-1]), float(hist['High'].iloc[-1]), float(hist['Low'].iloc[-1])
        gain = ((current_price - prev_price) / prev_price) * 100 if prev_price > 0 else 0.0
        vol = int(hist['Volume'].iloc[-1] / 1000)
        vol_5d = max(hist['Volume'].iloc[-6:-1].mean() / 1000, 0.01) 
        vol_ratio = vol / vol_5d 
        
        hist['Close'] = hist['Close'].bfill()
        ma5 = hist['Close'].rolling(min(5, len(hist))).mean().iloc[-1]
        ma20 = hist['Close'].rolling(min(20, len(hist))).mean().iloc[-1]
        ma60 = hist['Close'].rolling(min(60, len(hist))).mean().iloc[-1] if len(hist) >= 60 else ma20
        ma120 = hist['Close'].rolling(min(120, len(hist))).mean().iloc[-1] if len(hist) >= 120 else ma60

        is_ma_bullish = (current_price > ma5) and (ma5 > ma20) and (ma20 > ma60)
        ma_squeeze = (max(ma5, ma20, ma60) - min(ma5, ma20, ma60)) / max(min(ma5, ma20, ma60), 0.01) < 0.05 
        w_bottom_breakout = ma_squeeze and (current_price > max(ma5, ma20, ma60)) and (vol_ratio >= 1.5)
        is_first_red = (gain >= 3.0) and (vol_ratio >= 2.0) and (prev_price <= ma60 or prev_price <= ma20) and (current_price > ma60)

        low_min = hist['Low'].rolling(min(9, len(hist))).min()
        high_max = hist['High'].rolling(min(9, len(hist))).max()
        rsv = (hist['Close'] - low_min) / (high_max - low_min + 1e-9) * 100
        hist['K'] = rsv.bfill().ffill().fillna(50).ewm(com=2, adjust=False).mean()
        hist['D'] = hist['K'].bfill().ffill().fillna(50).ewm(com=2, adjust=False).mean()
        k, d_val = hist['K'].iloc[-1], hist['D'].iloc[-1]
        
        body = abs(current_price - open_p)
        upper_shadow = high_p - max(open_p, current_price)
        is_shooting_star = (upper_shadow > (body * 1.5)) and (high_p > ma5)
        is_fake_breakout = (vol_ratio >= 2.0) and is_shooting_star
        is_huge_vol = vol > (vol_5d * 2.0)                
        is_break_ma5 = current_price < ma5                
        sell_cond_count = sum([is_huge_vol, is_shooting_star, is_break_ma5])
        
        kdj_golden_cross = (k < 40) and (hist['K'].iloc[-2] < hist['D'].iloc[-2]) and (k > d_val) if len(hist) > 1 else False
        buy_cond_count = sum([kdj_golden_cross, is_first_red, vol_ratio >= 3.0, w_bottom_breakout])

        if kdj_golden_cross: kdj_signal, kdj_desc = "📈 買進訊號", "跌深反彈，金叉確立"
        elif k > 80 and current_price < open_p: kdj_signal, kdj_desc = "💀 逃命訊號", "高檔倒貨，死叉向下"
        elif k > 70 and k < d_val: kdj_signal, kdj_desc = "📉 動能衰退", "上漲無力"
        else: kdj_signal, kdj_desc = "〰️ 盤整中", "無明顯方向"

        entry_price = float(portfolio_data.get('entry_price', 0.0)) if portfolio_data else 0.0
        roi_pct = ((current_price - entry_price) / entry_price) * 100 if entry_price > 0 else 0.0
        main_cost = ma60 if current_price >= ma60 * 0.96 else ma120
        buy_low, buy_high = round(main_cost * 0.97, 1), round(main_cost * 1.03, 1)
        diff_from_cost = ((current_price - max(main_cost, 0.001)) / max(main_cost, 0.001)) * 100

        is_shield_active = False
        if mode == "長線價值波段單" and current_price < (manual_target if manual_target > 0 else (hist['High'].max()*1.1)):
            is_shield_active = True

        is_high_yield = fund_info.get('Yield', 0.0) >= 5.0
        is_cyclical = (fund_info.get('PB', 999.0) < 1.2) or (fund_info.get('PE', 999.0) < 12.0)

        opt_event_vanish = portfolio_data.get('opt_event_vanish', False) if portfolio_data else False
        opt_earnings_miss = portfolio_data.get('opt_earnings_miss', False) if portfolio_data else False
        opt_leader_crash = portfolio_data.get('opt_leader_crash', False) if portfolio_data else False

        ACTION_WAIT, ACTION_NO, ACTION_YES, ACTION_HOLD = "⏳ 【耐心觀望】", "❌ 【極度危險】", "✅ 【果斷買進】", "🛡️ 【保護持股】"
        signal_text, color_border, signal_bg = "", "", ""
        is_action_needed, is_golden = False, False
        tactical_summary = "目前走勢進入混沌期，多空交戰中，建議保留現金觀望。"

        if is_fake_breakout: 
            signal_text, color_border, signal_bg = f"{ACTION_NO} 🚨 主力誘多，請勿追高！", "#e74c3c", "#3a1515"
            is_shield_active = False; is_action_needed = True
            tactical_summary = "❌ 【主力誘多】高檔爆量留長上影線，這是標準的假突破！千萬別追，有庫存快跑！"
        elif opt_event_vanish or opt_earnings_miss or opt_leader_crash: 
            signal_text, color_border, signal_bg = f"{ACTION_NO} 買進理由消失，立刻撤退。", "#e74c3c", "#3a1515"
            is_shield_active = False; is_action_needed = True
            tactical_summary = "❌ 【紀律停損】當初的買進理由已消失，不要留戀，立刻市價砍單！"
        elif entry_price > 0 and roi_pct <= -10.0: 
            signal_text, color_border, signal_bg = f"{ACTION_NO} 觸發 10% 絕對停損結界！", "#e74c3c", "#3a1515"
            is_shield_active = False; is_action_needed = True
            tactical_summary = "🩸 【斷尾求生】虧損已達 10% 底線，嚴格執行紀律，立刻停損保護本金！"
        elif sell_cond_count >= 2 and roi_pct > 0: 
            signal_text, color_border, signal_bg = f"{ACTION_HOLD} 觸發危險訊號，分批停利。", "#f1c40f", "#3a3015"
            is_action_needed = True
            tactical_summary = "🟡 【見好就收】帳面雖獲利，但技術面已現敗象，請分批停利，將現金真實入袋。"
        elif sell_cond_count >= 2 and roi_pct <= 0: 
            signal_text, color_border, signal_bg = f"{ACTION_NO} 短線已經轉空，認賠殺出。", "#e74c3c", "#3a1515"
            is_action_needed = True
            tactical_summary = "❌ 【放棄幻想】股價破線且爆出大量，趨勢已死，不要再期待反彈，直接認賠換股。"
        elif is_macro_panic_global: 
            if current_price <= buy_high: 
                signal_text, color_border, signal_bg = f"{ACTION_YES} 斷頭潮來臨！左側準備重壓！", "#00FF00", "#153a20"; is_golden = True
                tactical_summary = "✅ 【危機入市】大盤恐慌下殺，此標的已殺入極便宜的超跌區，適合勇敢左側買進！"
            else: 
                signal_text, color_border, signal_bg = f"{ACTION_WAIT} 等待恐慌斷頭賣壓打下來再撿！", "#f39c12", "#3a3015"
                tactical_summary = "⏳ 【耐心等待】股價尚未殺入足夠便宜的安全區，請等待市場恐慌情緒發酵。"
        elif is_first_red:
            signal_text, color_border, signal_bg = f"{ACTION_YES} ✨ 破繭第一根！爆量強勢起漲！", "#00FF00", "#153a20"; is_golden = True
            tactical_summary = "✨ 【絕佳買點】底部爆量突破！這就是起漲第一根，請大膽切入並設好停損！"
        else:
            if is_shield_active and gain < -2.0: 
                signal_text, color_border, signal_bg = f"🛡️ 【長線護盾】 系統已過濾假跌破，安心抱單！", "#3498db", "#152a3a"
                tactical_summary = "🛡️ 【安心抱牢】長線距離目標價尚遠，系統已幫您屏蔽短線下殺雜音，請抱牢。"
            elif val_code == "3": 
                signal_text, color_border, signal_bg = f"{ACTION_NO} 估值太貴已撞天花板，絕對別買！", "#e74c3c", "#3a1515"
                tactical_summary = "❌ 【避開雷區】系統精算目前價格嚴重偏高，買進期望值極低，請完全避開！"
            elif (is_ma_bullish or buy_cond_count >= 1) and current_price > (buy_high * 1.05):
                signal_text, color_border, signal_bg = f"{ACTION_WAIT} 偏離防守區太遠，等拉回再買！", "#f39c12", "#3a3015"
                tactical_summary = "⏳ 【切勿追高】動能雖強但追高風險極大，停損會拉得很遠，請等量縮拉回再買。"
            elif (buy_cond_count >= 1 or is_ma_bullish or w_bottom_breakout): 
                signal_text, color_border, signal_bg = f"{ACTION_YES} 突破或多頭確立！(右側極速狙擊)", "#00FF00", "#153a20"; is_golden = True
                tactical_summary = "✅ 【果斷切入】攻擊動能已經點火，完全符合右側進場標準！設好停損線後果斷買進。"
            else: 
                signal_text, color_border, signal_bg = f"{ACTION_HOLD} 卡在區間震盪，在旁邊輕鬆看戲。", "#ccc", "#2b2b36"

        ai_tags = []
        if chip_code == "1": ai_tags.append("🔴 大戶進駐")
        elif chip_code == "2": ai_tags.append("🟢 外資提款")
        if is_fake_breakout: ai_tags.append("🚨 假突破")
        if is_first_red: ai_tags.append("✨ 第一根起漲")
        if is_break_ma5: ai_tags.append("🟢 跌破 5MA")
        if current_price < ma20: ai_tags.append("🟢 跌破月線")
        if vol_ratio >= 3.0: ai_tags.append("🔴 爆量攻擊")
        if is_ma_bullish: ai_tags.append("🔴 均線多頭")
        if is_shooting_star: ai_tags.append("🟢 上影線避雷針")
        if not ai_tags: ai_tags.append("⚪ 量縮整理中")

        buy_status, buy_color, buy_bg = "⚪ 尚在醞釀", "#aaaaaa", "#1a1a24"
        if buy_cond_count >= 2 or is_first_red: buy_status, buy_color, buy_bg = "🔥 強勢起漲！", "#ff4d4d", "#3a1515"
        elif buy_cond_count == 1: buy_status, buy_color, buy_bg = "🚀 準備表態", "#f1c40f", "#3a3015"

        return {
            "name": stock_name, "code": symbol, "price": current_price, "gain": gain, "cost": round(main_cost,1), 
            "cost_label": "長線季線防守", "buy_zone": f"{buy_low}-{buy_high}", "shd": "🛡️", "chip_code": chip_code, 
            "chip": {"1":"🐳","2":"🩸","0":"⚖️"}.get(chip_code, "❓"), "val_code": val_code, "val": VAL_MAP.get(val_code, "⚪"), 
            "kdj": kdj_signal, "chip_desc": chip_desc, "val_desc": val_desc, "kdj_desc": kdj_desc, 
            "downgrade_alert": "", "signal": signal_text, "color": color_border, 
            "signal_bg": signal_bg, "ai_tags": ai_tags, "extra_badge": "💰 高息防禦" if is_high_yield else "", 
            "exit_s": "10% 停損底線", "exit_price": round(entry_price*0.9 if entry_price>0 else main_cost*0.95,1), 
            "exit_color": "#e74c3c", "exit_bg": "#2c153a", "vol": vol, "raw_data": symbol_data, "cat": category_type, 
            "buy_status": buy_status, "buy_color": buy_color, "buy_bg": buy_bg,
            "auto_target": round(hist['High'].max()*1.1,1), "is_shield_active": is_shield_active, "is_ma_bullish": is_ma_bullish,
            "roi_pct": roi_pct, "is_golden": is_golden, "is_action_needed": is_action_needed, "tactical_summary": tactical_summary,
            "sell_cond_count": sell_cond_count, "is_fake_breakout": is_fake_breakout,
            "is_high_yield": is_high_yield, "is_cyclical": is_cyclical, "is_first_red": is_first_red, "vol_ratio": vol_ratio, "diff_from_cost": diff_from_cost
        }
    except Exception as e: return None

def calc_real_profit(cost, price, qty):
    if cost <= 0: return 0, 0
    buy_val = cost * qty * 1000
    sell_val = price * qty * 1000
    profit = sell_val - buy_val - max(20, int(buy_val * 0.001425)) - max(20, int(sell_val * 0.001425)) - int(sell_val * 0.003)
    return profit, (profit/buy_val)*100 if buy_val > 0 else 0

def parallel_load_tactical_data(items_dict, manual_prices, is_panic_global):
    res = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(calculate_tactical_signals, p.get('raw_data', f"{code}:?:?:?:?"), p.get('cat', 'main'), p.get('mode', '短線技術動能單'), p.get('manual_target', 0.0), p, manual_prices, is_panic_global, False): code for code, p in items_dict.items()}
        for f in concurrent.futures.as_completed(futures):
            code = futures[f]
            try: res[code] = f.result()
            except: res[code] = None
    return {k: v for k, v in res.items() if v is not None}

# ==========================================
# 🖥️ 側邊欄控制台 (V16.8 慢速安全掃描模組)
# ==========================================
with st.sidebar:
    st.markdown("<h2 style='color:#f1c40f; text-align:center;'>⚙️ 戰術控制台</h2>", unsafe_allow_html=True)
    
    st.markdown("<div style='background:#16191f; padding:10px; border-radius:8px; border: 1px solid #3498db; margin-bottom:10px;'><h4 style='color:#3498db; margin-top:0px; font-size:14px;'>📡 智能情報萃取器</h4>", unsafe_allow_html=True)
    with st.form(key='intel_form', clear_on_submit=True): 
        intel_input = st.text_area("貼上密碼 (支援全半形)：", placeholder="2313:?:?:1:?")
        if st.form_submit_button('📥 匯入預覽'):
            matches = [x.strip() for x in re.split(r'[,\s]+', intel_input.replace("INTEL:", "").replace("ＩＮＴＥＬ：", "").replace("：", ":").replace("？", "?").replace("，", ",")) if x.count(':') >= 3]
            st.session_state.temp_intel = [] 
            for s in matches:
                c = s.split(":")[0].strip()
                if c and c not in st.session_state.portfolio and c not in st.session_state.pinned_stocks: 
                    st.session_state.temp_intel.append({'code': c, 'raw_data': s, 'cat': 'intel'})
            st.rerun()
    st.markdown("</div>", unsafe_allow_html=True)

    st.markdown("---")
    st.markdown("<h4 style='color:#00FF00;'>🚀 全市場深度掃描 (100% 收錄)</h4>", unsafe_allow_html=True)
    st.markdown("<p style='color:#aaa; font-size:12px;'>⚠️ 安全慢速模式啟動：系統將以擬真人類速率讀取全市場 1700+ 檔標的，確保不被防毒機制封鎖，約需等候 3~5 分鐘。</p>", unsafe_allow_html=True)
    
    # [V16.8 核心重構] 降頻防禦的慢速安全掃描引擎
    def run_safe_slow_scan(target_codes, mode, current_panic_state):
        results = []
        progress_bar = st.progress(0)
        status_text = st.empty()
        total = len(target_codes)
        
        # 將線程降至 2，確保速度安全，完全繞過 Yahoo 防火牆
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            future_to_code = {executor.submit(calculate_tactical_signals, f"{c}:?:?:?:?", "scan", "短線技術動能單", 0.0, None, {}, current_panic_state, True): c for c in target_codes}
            completed = 0
            for future in concurrent.futures.as_completed(future_to_code):
                completed += 1
                try: d = future.result()
                except: d = None
                
                if d and "❌" not in d.get('signal', '') and d.get('price', 0) > 0:
                    if mode == "golden" and d.get('is_golden'): results.append(d)
                    elif mode == "first_red" and d.get('is_first_red'): results.append(d)
                    elif mode == "stealth" and d.get('vol_ratio', 0) >= 1.5 and d.get('diff_from_cost', 99) <= 15.0: results.append(d)
                    elif mode == "yield" and (d.get('is_high_yield') or d.get('is_cyclical')): results.append(d)
                
                if completed % 5 == 0 or completed == total:
                    progress_bar.progress(min(completed / total, 1.0))
                    status_text.text(f"📡 安全深度掃描中: {completed}/{total} 檔 (已過濾出 {len(results)} 檔)... 請耐心等候")
                
                # 額外的主執行緒微小喘息時間
                time.sleep(0.05)
                    
        progress_bar.empty()
        status_text.empty()
        return results

    if st.button("🚀 黃金起漲與魚身 (約 3-5 分鐘)", use_container_width=True):
        st.session_state.scan_results = run_safe_slow_scan(FULL_MARKET_CODES, "golden", is_panic)
        st.session_state.scan_mode = "golden"; st.rerun()
    if st.button("✨ 破繭第一根專區 (約 3-5 分鐘)", use_container_width=True):
        st.session_state.scan_results = run_safe_slow_scan(FULL_MARKET_CODES, "first_red", is_panic)
        st.session_state.scan_mode = "first_red"; st.rerun()
    if st.button("🕵️‍♂️ 魚頭潛伏與轉機 (約 3-5 分鐘)", use_container_width=True):
        st.session_state.scan_results = run_safe_slow_scan(FULL_MARKET_CODES, "stealth", is_panic)
        st.session_state.scan_mode = "stealth"; st.rerun()
    if st.button("🛡️ 總經防禦高息池", use_container_width=True):
        with st.spinner(f"過濾全市場高息防護標的..."):
            yield_pool = [c for c, data in FUNDAMENTAL_DB.items() if data['Yield'] >= 5.0 or data['PE'] < 12.0]
            st.session_state.scan_results = run_safe_slow_scan(yield_pool, "yield", is_panic)
            st.session_state.scan_mode = "yield"; st.rerun()
            
    st.markdown("---")
    st.markdown("<h4 style='color:#e056fd;'>🔥 焦點戰役 (選股靈感)</h4>", unsafe_allow_html=True)
    st.button("📥 載入今日熱門戰役", use_container_width=True, on_click=cb_load_hot_themes)

    st.markdown("---")
    sentinel_label = "🔕 關閉哨兵模式" if st.session_state.sentinel_active else "🔔 啟動哨兵模式"
    if st.button(sentinel_label, use_container_width=True):
        st.session_state.sentinel_active = not st.session_state.sentinel_active
        st.rerun()

# ==========================================
# 🖥️ 主戰情室畫面渲染
# ==========================================
col_navbar1, col_navbar2, col_navbar3 = st.columns([5, 1, 1])
with col_navbar1: st.markdown("<h1 style='color:#FFB300; margin: 0;'>54088 戰情室</h1>", unsafe_allow_html=True)
with col_navbar2:
    st.markdown("<div class='sync-btn'>", unsafe_allow_html=True)
    st.button("🔄 刷新", use_container_width=True, on_click=cb_ui_sync) 
    st.markdown("</div>", unsafe_allow_html=True)
with col_navbar3:
    st.markdown("<div class='lock-btn'>", unsafe_allow_html=True)
    st.button("🔒 鎖定", use_container_width=True, on_click=cb_ui_logout)
    st.markdown("</div>", unsafe_allow_html=True)

st.markdown(f"<div style='text-align:right; color:#888; font-size:12px; margin-bottom:10px;'>系統狀態：正常連線中 | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</div>", unsafe_allow_html=True)

port_loaded = parallel_load_tactical_data(st.session_state.portfolio, current_manual_prices, is_panic)
pin_loaded = parallel_load_tactical_data(st.session_state.pinned_stocks, current_manual_prices, is_panic)

total_unrealized, action_needed, golden_targets = 0, 0, 0
for code, d in port_loaded.items():
    p_profit, _ = calc_real_profit(st.session_state.portfolio[code]['entry_price'], d['price'], st.session_state.portfolio[code]['qty'])
    total_unrealized += p_profit
    if d.get('is_action_needed'): action_needed += 1
for code, d in pin_loaded.items():
    if d.get('is_golden'): golden_targets += 1

market_suggestion = "🩸 【斷頭潮來臨】大盤恐慌崩跌！切換「左側價值」重壓便宜股！" if is_panic else ("💡 【多頭順風】大盤健康 ➡️ 適合【🚀 右側動能狙擊】" if is_bull_market else "💡 【空頭震盪】大盤不穩 ➡️ 適合【🛡️ 左側防禦佈局】")

st.markdown(f"""
<div class='hud-box'>
<div class='hud-title' style='display:flex; justify-content:space-between;'><span>🌐 大將軍戰情總覽 (HUD)</span><span style='color:{weather_color};'>{weather_str}</span></div>
<div style='background:#1a1c23; padding:10px; border-radius:5px; border-left:3px solid {weather_color}; margin-bottom:10px; font-size:14px; color:#ddd;'>
<strong>🌅 今日戰情速報：</strong>大盤目前判定為 {weather_str.split(' ')[1]}。 {market_suggestion}
</div>
<div class='hud-metric'><span style='color:#aaa;'>庫存 / 雷達數量</span> <strong style='color:#fff;'>{len(port_loaded)} / {len(pin_loaded)} 檔</strong></div>
<div class='hud-metric'><span style='color:#aaa;'>總未實現損益</span> <strong style='color:{'#ff4d4d' if total_unrealized>0 else '#00FF00'}; font-size:18px;'>{total_unrealized:+,.0f} 元</strong></div>
<div class='health-bar-bg'><div class='{'health-bar-fill-green' if total_unrealized >= 0 else 'health-bar-fill-red'}' style='width: {max(0, min(100, 50 + (total_unrealized / 50000) * 50))}%;'></div></div>
<div class='hud-metric' style='margin-top:10px; padding-top:10px; border-top:1px dashed #333;'><span style='color:#2ecc71;'>🎯 雷達可狙擊目標：<strong>{golden_targets} 檔</strong></span><span style='color:#e74c3c;'>🚨 庫存強迫撤退：<strong>{action_needed} 檔</strong></span></div>
</div>
""", unsafe_allow_html=True)

if st.session_state.sentinel_active:
    st.info("🔔 哨兵模式運作中 (背景自動輪詢監視)")
    if action_needed > 0 or golden_targets > 0:
        alert_msg = f"指揮官注意！發現 {action_needed} 檔庫存需立刻撤退，{golden_targets} 檔雷達發出黃金買進訊號！"
        st.markdown(f"<div style='background:#e74c3c; color:#fff; font-weight:bold; text-align:center; padding:10px; border-radius:5px; margin-bottom:15px; animation: blinker 2s linear infinite;'>🚨 哨兵極限警報：{alert_msg}</div>", unsafe_allow_html=True)
    components.html("""<script>setTimeout(function(){ window.parent.location.reload(); }, 60000);</script>""", height=0)

search_query = st.text_input("📝 搜尋標的 (輸入代號 '2313' 或名稱 '華通'，按 Enter) ：", key="search_input")

def draw_stock_card(d, ui_key_prefix, is_portfolio=False, p_data=None):
    if not d: return
    try:
        gain_color, gain_bg = ('#ff4d4d', '#3a1515') if d.get('gain',0)>0 else (('#00FF00', '#153a20') if d.get('gain',0)<0 else ('#aaaaaa', '#333333'))
        ai_tags_html = "".join([f"<span class='{'danger-badge' if '🚨' in tag or '🔴' in tag else 'special-badge'}'>{tag}</span>" for tag in d.get('ai_tags', [])])
        port_html = ""
        if is_portfolio and p_data:
            port_html = f"<div style='background:#10141d; padding:10px; border-radius:6px; margin-bottom:12px;'><div style='display:flex; justify-content:space-between;'><span style='background-color:{'#3498db' if p_data.get('mode') == '長線價值波段單' else '#e67e22'}; color:#fff; font-size:12px; padding:2px 8px; border-radius:4px;'>🎮 {p_data.get('mode', '')}</span><span style='color:#aaa; font-size:12px;'>🎯 目標價：<strong style='color:#f1c40f;'>{p_data.get('manual_target', 0.0):.1f}</strong></span></div><div style='color:#e056fd; font-size:13px; margin-top:5px;'>🌟 買進核心理由：{p_data.get('catalyst', '')}</div></div>"
        
        summary_class = "tactical-danger" if d.get('is_action_needed') or d.get('is_fake_breakout') else "tactical-summary"

        st.markdown(f"""
        <div style="border: 2px solid {d.get('color', '#444')}; border-radius: 8px; padding: 15px; background-color: #16191f; margin-bottom: 5px;">
        {port_html}
        <div style="font-weight:bold; font-size:18px; margin-bottom:5px;">{d.get('name', '未知')} ({d.get('code', '未知')})</div>
        <div style="font-size:32px; font-weight:bold; margin-bottom: 10px; display:flex; gap:12px;">{d.get('price', 0.0):.2f} <span style="font-size:16px; color:{gain_color}; background-color:{gain_bg}; padding:4px 10px; border-radius:6px;">{d.get('gain', 0.0):+.1f}%</span></div>
        <div style="margin-bottom: 5px;">{ai_tags_html}</div>
        <div style="background:{d.get('signal_bg', '#111')}; padding:10px; border-radius:6px; text-align:center; margin-bottom:10px; border: 1px solid {d.get('color', '#444')}40;"><strong style="color:{d.get('color', '#fff')}; font-size:18px;">{d.get('signal', '')}</strong></div>
        <div class="{summary_class}">📝 指揮官戰術小結：<br>{d.get('tactical_summary', '')}</div>
        </div>""", unsafe_allow_html=True)

        if not is_portfolio:
            if d.get('code') not in st.session_state.pinned_stocks and d.get('code') not in st.session_state.portfolio:
                st.button(f"📌 加入觀測雷達", key=f"pin_{ui_key_prefix}_{d.get('code')}", use_container_width=True, on_click=cb_pin_stock, args=(d.get('code'), f"{d.get('code')}:?:0:?:?:0", 'search'))
    except Exception as e: pass

if search_query:
    raw_input = search_query.strip().replace('.TW', '').replace('.TWO', '')
    match_digits = re.search(r'\d{4,}', raw_input)
    clean_code = match_digits.group() if match_digits else None
    if not clean_code:
        for name, code in {v: k for k, v in TW_STOCKS.items()}.items():
            if raw_input in name: clean_code = code; break
    if clean_code:
        d = calculate_tactical_signals(f"{clean_code}:?:?:?:?", "search", manual_prices_dict=current_manual_prices, is_macro_panic_global=is_panic)
        draw_stock_card(d, "search")

if st.session_state.temp_intel:
    st.markdown("<h3 style='color:#00d2ff; margin-top:20px; border-bottom: 2px solid #00d2ff; padding-bottom:5px;'>👁️ 焦點戰役觀測區 (未鎖定)</h3>", unsafe_allow_html=True)
    cols = st.columns(2)
    for i, item in enumerate(st.session_state.temp_intel):
        try:
            d = calculate_tactical_signals(item.get('raw_data'), item.get('cat'), manual_prices_dict=current_manual_prices, is_macro_panic_global=is_panic)
            if d:
                with cols[i % 2]: draw_stock_card(d, f"temp_{i}")
        except: pass

if st.session_state.portfolio:
    st.markdown(f"<h2 style='color:#ff4d4d; margin-top:20px; border-bottom: 2px solid #ff4d4d; padding-bottom:5px;'>💼 總指揮的作戰庫存</h2>", unsafe_allow_html=True)
    cols = st.columns(2)
    for i, (code, p_data) in enumerate(list(st.session_state.portfolio.items())):
        d = port_loaded.get(code)
        if d:
            with cols[i % 2]:
                p_profit, p_roi = calc_real_profit(p_data.get('entry_price', 0), d.get('price', 0), p_data.get('qty', 0))
                is_hard_stop = d.get('is_action_needed', False) and d.get('gain', 0) < 0
                st.markdown(f"""<div style="border: 4px solid {'#e74c3c' if is_hard_stop else '#00FF00'}; border-radius: 8px; padding: 15px; background-color: #1a1a24; margin-bottom: 5px;"><div style="font-weight:bold; font-size:18px;">{d.get('name')} ({d.get('code')})</div><div style="font-size:24px; font-weight:bold; color:{'#e74c3c' if p_profit<0 else '#ff4d4d'};">{p_profit:+,.0f} 元 ({p_roi:+.1f}%)</div></div>""", unsafe_allow_html=True)
                draw_stock_card(d, f"port_{code}", is_portfolio=True, p_data=p_data)
                st.button(f"🚪 賣出清空", key=f"sell_{code}", use_container_width=True, on_click=cb_sell_stock, args=(code,))

if st.session_state.pinned_stocks:
    st.markdown(f"<h2 style='color:#f1c40f; margin-top:20px; border-bottom: 2px solid #f1c40f; padding-bottom:5px;'>⭐ 觀測雷達</h2>", unsafe_allow_html=True)
    cols = st.columns(2)
    for i, (code, p_data) in enumerate(list(st.session_state.pinned_stocks.items())):
        d = pin_loaded.get(code)
        if d:
            with cols[i % 2]:
                draw_stock_card(d, f"pin_{code}")
                c1, c2 = st.columns(2)
                c1.button(f"⚡ 買進", key=f"buy_pin_{code}", use_container_width=True, on_click=cb_buy_stock, args=(code, p_data.get('raw_data'), p_data.get('cat'), "pin"))
                c2.button(f"❌ 刪除", key=f"unpin_{code}", use_container_width=True, on_click=cb_unpin_stock, args=(code,))

if scan_mode_current := st.session_state.get('scan_mode', ""):
    st.markdown(f"<h2 style='color:#00FF00; margin-top:30px; border-bottom: 2px solid #00FF00; padding-bottom:5px;'>⚡ 全市場掃描結果</h2>", unsafe_allow_html=True)
    if not st.session_state.scan_results:
        st.warning("⚠️ 報告指揮官，全市場掃描完畢，目前沒有任何標的符合此嚴苛條件。代表資金正在觀望，建議您保留現金，切勿硬買！")
    else:
        cols = st.columns(2)
        for i, d in enumerate([x for x in st.session_state.scan_results if x.get('code') not in st.session_state.portfolio and x.get('code') not in st.session_state.pinned_stocks]):
            with cols[i % 2]: draw_stock_card(d, f"scan_res_{i}")
