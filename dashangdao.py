import streamlit as st
import yfinance as yf
import pandas as pd
from datetime import datetime
import os
import json
import requests

# ==========================================
# 🛡️ 步驟一：基礎配置與狀態初始化
# ==========================================
st.set_page_config(layout="wide", page_title="54088 - 戰情室穩定版")

COMMANDER_PIN = "0826"
USER_DB_FILE = "54088_database.json"

if 'portfolio' not in st.session_state:
    st.session_state.portfolio = {}
    st.session_state.pinned_stocks = {}
    if os.path.exists(USER_DB_FILE):
        try:
            with open(USER_DB_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                st.session_state.portfolio = data.get("portfolio", {})
                st.session_state.pinned_stocks = data.get("pinned_stocks", {})
        except: pass

def save_db():
    with open(USER_DB_FILE, "w", encoding="utf-8") as f:
        json.dump({"portfolio": st.session_state.portfolio, "pinned_stocks": st.session_state.pinned_stocks}, f, ensure_ascii=False)

# ==========================================
# 📡 戰術演算法核心 (已修正順序)
# ==========================================
def get_hist(symbol):
    try:
        tk = yf.Ticker(f"{symbol}.TW" if not symbol.startswith('3') else f"{symbol}.TWO")
        hist = tk.history(period="1y", timeout=3)
        return hist.dropna(subset=['Close']) if not hist.empty else pd.DataFrame()
    except: return pd.DataFrame()

def calculate_signals(symbol, hist_df):
    if hist_df.empty: return None
    curr = float(hist_df['Close'].iloc[-1])
    ma60 = hist_df['Close'].rolling(min(60, len(hist_df))).mean().iloc[-1]
    gain = ((curr - hist_df['Close'].iloc[-2]) / hist_df['Close'].iloc[-2]) * 100
    is_first_red = (gain >= 3.0) and (curr > ma60)
    return {
        "name": symbol, "code": symbol, "price": curr, "gain": gain, "ma60": round(ma60, 1),
        "signal": "✅ 起漲訊號" if is_first_red else "⏳ 觀察中",
        "color": "#00FF00" if is_first_red else "#888"
    }

# ==========================================
# 🖥️ UI 渲染邏輯
# ==========================================
if 'authenticated' not in st.session_state: st.session_state.authenticated = False

if not st.session_state.authenticated:
    pwd = st.text_input("輸入密碼", type="password")
    if st.button("解鎖"):
        if pwd == COMMANDER_PIN:
            st.session_state.authenticated = True
            st.rerun()
    st.stop()

st.title("54088 戰情室 V31.0")

# 1. 庫存模組
st.header("💼 模擬倉管理")
for code, p in st.session_state.portfolio.items():
    st.write(f"代號: {code} | 進場: {p['entry_price']}")
    if st.button(f"賣出 {code}", key=f"sell_{code}"):
        del st.session_state.portfolio[code]
        save_db(); st.rerun()

# 2. 搜尋與雷達模組
st.header("🔎 搜尋與雷達")
search = st.text_input("輸入股票代號")
if search:
    d = calculate_signals(search, get_hist(search))
    if d:
        st.success(f"{d['code']} 現價: {d['price']}")
        if st.button("📌 加入雷達"):
            st.session_state.pinned_stocks[search] = {'cat': 'manual'}
            save_db(); st.rerun()

for code in st.session_state.pinned_stocks:
    d = calculate_signals(code, get_hist(code))
    if d:
        st.info(f"雷達: {code} | 訊號: {d['signal']}")
        if st.button(f"買進 {code}", key=f"buy_{code}"):
            st.session_state.portfolio[code] = {'entry_price': d['price'], 'qty': 1}
            save_db(); st.rerun()

# 3. 掃描模組
st.sidebar.header("⚙️ 戰術控制台")
if st.sidebar.button("🚀 執行起漲掃描"):
    target_codes = ["2330", "2317", "2454", "2603", "2303", "1519"]
    results = []
    for c in target_codes:
        d = calculate_signals(c, get_hist(c))
        if d and d['gain'] > 0: results.append(d)
    st.session_state.scan_results = results

if st.session_state.scan_results:
    st.header("⚡ 掃描結果")
    for res in st.session_state.scan_results:
        st.markdown(f"**{res['code']}** - 漲幅: {res['gain']:.2f}% - {res['signal']}")
