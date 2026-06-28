import streamlit as st
import yfinance as yf
import pandas as pd
from datetime import datetime
import re
import time
import json
import os
import requests

# ==========================================
# 🛡️ 步驟一：基礎配置
# ==========================================
st.set_page_config(layout="wide", page_title="54088 - 戰情室完全體 V30", initial_sidebar_state="expanded")

COMMANDER_PIN = "0826"
USER_DB_FILE = "54088_database.json" 
MAX_CAPACITY = 40

# 初始化狀態
if 'portfolio' not in st.session_state:
    st.session_state.portfolio = {}
    st.session_state.pinned_stocks = {}
    if os.path.exists(USER_DB_FILE):
        try:
            with open(USER_DB_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                st.session_state.pinned_stocks = data.get("pinned_stocks", {})
                st.session_state.portfolio = data.get("portfolio", {})
        except: pass

def save_db():
    try:
        with open(USER_DB_FILE, "w", encoding="utf-8") as f: 
            json.dump({"pinned_stocks": st.session_state.pinned_stocks, "portfolio": st.session_state.portfolio}, f, ensure_ascii=False, indent=4)
    except: pass

# ==========================================
# 🛡️ 步驟二：解鎖機制
# ==========================================
if 'authenticated' not in st.session_state: st.session_state.authenticated = (st.query_params.get("auth") == "54088")

if not st.session_state.authenticated:
    pwd = st.text_input("輸入授權密碼", type="password")
    if st.button("系統解鎖"):
        if pwd == COMMANDER_PIN:
            st.session_state.authenticated = True
            st.rerun()
        else: st.error("❌ 密碼錯誤")
    st.stop()

# ==========================================
# 📡 資料獲取核心 (單軌防斷線)
# ==========================================
TW_STOCK_NAMES = {"2330":"台積電", "2317":"鴻海", "2454":"聯發科", "2382":"廣達", "2603":"長榮", "1519":"華城", "3017":"奇鋐", "3324":"雙鴻"}
GLOBAL_MARKET_CODES = list(TW_STOCK_NAMES.keys())

def get_hist(symbol):
    try:
        session = requests.Session()
        session.headers.update({"User-Agent": "Mozilla/5.0"})
        tk = yf.Ticker(f"{symbol}.TW" if not symbol.startswith('3') else f"{symbol}.TWO", session=session)
        hist = tk.history(period="1y", timeout=3)
        return hist.dropna(subset=['Close']) if not hist.empty else pd.DataFrame()
    except: return pd.DataFrame()

# ==========================================
# 🧠 戰術運算 (完全復刻您的核心邏輯)
# ==========================================
def calc_signals(symbol, hist_df):
    if hist_df.empty: return None
    curr = float(hist_df['Close'].iloc[-1])
    ma60 = hist_df['Close'].rolling(60).mean().iloc[-1]
    gain = ((curr - hist_df['Close'].iloc[-2]) / hist_df['Close'].iloc[-2]) * 100
    
    is_first_red = (gain >= 3.0) and (curr > ma60)
    
    return {
        "name": TW_STOCK_NAMES.get(symbol, symbol),
        "code": symbol,
        "price": curr,
        "gain": gain,
        "cost": round(ma60, 1),
        "signal": "✅ 起漲點" if is_first_red else "⏳ 觀察中",
        "color": "#00FF00" if is_first_red else "#888",
        "is_golden": is_first_red,
        "ai_tags": ["✨ 起漲"] if is_first_red else ["⚪ 震盪"]
    }

# ==========================================
# 🖥️ 主戰情室畫面
# ==========================================
st.title("54088 戰情室 V30.0")

# 1. 模擬倉/持倉顯示
st.header("💼 總指揮持倉")
if st.session_state.portfolio:
    for code, p in st.session_state.portfolio.items():
        st.write(f"持股: {code} | 進場: {p['entry_price']} | 數量: {p['qty']}")
        if st.button(f"賣出 {code}"):
            del st.session_state.portfolio[code]
            save_db(); st.rerun()

# 2. 搜尋雷達
st.header("🔎 標的查詢與雷達")
search = st.text_input("輸入代號搜尋")
if search:
    d = calculate_signals(search, get_hist(search))
    if d:
        st.success(f"{d['name']} ({d['code']}) 目前價格: {d['price']}")
        if st.button("📌 加入雷達"):
            st.session_state.pinned_stocks[d['code']] = {'cat': 'manual'}
            save_db(); st.rerun()

# 3. 雷達列表
if st.session_state.pinned_stocks:
    for code in st.session_state.pinned_stocks:
        d = calculate_signals(code, get_hist(code))
        if d:
            st.info(f"雷達: {d['name']} | 訊號: {d['signal']}")
            if st.button(f"加入模擬倉 {code}"):
                st.session_state.portfolio[code] = {'entry_price': d['price'], 'qty': 1}
                save_user_db_action()
                st.rerun()

# 4. 全境掃描
st.sidebar.header("⚙️ 戰術控制台")
if st.sidebar.button("🚀 黃金起漲全市場掃描"):
    results = []
    for c in GLOBAL_MARKET_CODES:
        d = calculate_signals(c, get_hist(c))
        if d and d['is_golden']: results.append(d)
    st.session_state.scan_results = results

if st.session_state.scan_results:
    st.header("⚡ 掃描結果")
    cols = st.columns(3)
    for i, res in enumerate(st.session_state.scan_results):
        with cols[i % 3]:
            st.metric(res['name'], res['price'], f"{res['gain']:.1f}%")
