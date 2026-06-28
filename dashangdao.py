import streamlit as st
import yfinance as yf
import pandas as pd
import json
import os
import requests

# ==========================================
# 🛡️ 步驟一：基礎配置與狀態初始化 (修復 AttributeError)
# ==========================================
st.set_page_config(layout="wide", page_title="54088 - 戰情室 V31.1")

COMMANDER_PIN = "0826"
USER_DB_FILE = "54088_database.json" 

# 【關鍵修復】：確保所有變數都在第一時間宣告，絕不報錯
if 'scan_results' not in st.session_state: st.session_state.scan_results = []
if 'portfolio' not in st.session_state: st.session_state.portfolio = {}
if 'pinned_stocks' not in st.session_state: st.session_state.pinned_stocks = {}

# 讀取本地資料庫
if 'db_loaded' not in st.session_state:
    if os.path.exists(USER_DB_FILE):
        try:
            with open(USER_DB_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                st.session_state.pinned_stocks = data.get("pinned_stocks", {})
                st.session_state.portfolio = data.get("portfolio", {})
        except: pass
    st.session_state.db_loaded = True

def save_db():
    with open(USER_DB_FILE, "w", encoding="utf-8") as f: 
        json.dump({"pinned_stocks": st.session_state.pinned_stocks, "portfolio": st.session_state.portfolio}, f, ensure_ascii=False, indent=4)

# ==========================================
# 🛡️ 步驟二：身份驗證
# ==========================================
if 'authenticated' not in st.session_state: 
    st.session_state.authenticated = (st.query_params.get("auth") == "54088")

if not st.session_state.authenticated:
    pwd = st.text_input("輸入授權密碼", type="password")
    if st.button("系統解鎖"):
        if pwd == COMMANDER_PIN:
            st.session_state.authenticated = True
            st.rerun()
        else: st.error("❌ 密碼錯誤")
    st.stop()

# ==========================================
# 🎨 視覺與樣式定義
# ==========================================
st.markdown('''<style>
.stApp { background-color: #0b0c0f !important; color: #fff !important; }
div[data-testid="stSidebar"] { background-color: #12141a !important; border-right: 1px solid #333 !important; }
div[data-testid="stButton"] > button { background-color: #1e1e24 !important; border: 1px solid #444 !important; }
div[data-testid="stButton"] > button p { color: #ffffff !important; font-weight: bold !important; font-size: 15px !important; }
.danger-badge { background: #3a1515; padding: 4px 8px; border-radius: 4px; font-size: 13px; color: #ff4d4d; border: 1px solid #e74c3c; }
</style>''', unsafe_allow_html=True)

# ==========================================
# 📡 資料獲取與運算核心
# ==========================================
TW_STOCK_NAMES = {"2330":"台積電", "2317":"鴻海", "2454":"聯發科", "2382":"廣達", "2603":"長榮", "1519":"華城", "3017":"奇鋐", "3324":"雙鴻"}
GLOBAL_MARKET_CODES = list(TW_STOCK_NAMES.keys())

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
    gain = ((curr - float(hist_df['Close'].iloc[-2])) / float(hist_df['Close'].iloc[-2])) * 100
    is_first_red = (gain >= 3.0) and (curr > ma60)
    
    return {
        "name": TW_STOCK_NAMES.get(symbol, symbol), 
        "code": symbol, 
        "price": curr,
        "gain": gain, 
        "cost": round(ma60, 1),
        "signal": "✅ 起漲點" if is_first_red else "⏳ 觀察中",
        "color": "#00FF00" if is_first_red else "#888",
        "is_golden": is_first_red
    }

def draw_card(d, ui_key_prefix):
    gain_color = '#ff4d4d' if d['gain'] > 0 else '#00FF00'
    st.markdown(f"""
    <div style="border: 2px solid {d['color']}; border-radius: 8px; padding: 15px; background-color: #16191f; margin-bottom: 5px;">
        <div style="font-weight:bold; font-size:18px; margin-bottom:5px;">{d['name']} ({d['code']})</div>
        <div style="font-size:28px; font-weight:bold; display:flex; gap:12px;">{d['price']:.2f} <span style="font-size:16px; color:{gain_color};">{d['gain']:+.1f}%</span></div>
        <div style="margin-top:10px; color:{d['color']}; font-weight:bold;">{d['signal']}</div>
    </div>""", unsafe_allow_html=True)

# ==========================================
# 🖥️ 介面渲染
# ==========================================
st.title("54088 戰情室 V31.1")

# 1. 模擬倉/持倉顯示
st.header("💼 模擬倉管理")
if st.session_state.portfolio:
    cols = st.columns(3)
    for i, (code, p) in enumerate(list(st.session_state.portfolio.items())):
        with cols[i % 3]:
            st.markdown(f"**{code}** | 進場: {p['entry_price']} | 數量: {p['qty']}")
            if st.button(f"賣出平倉", key=f"sell_{code}"):
                del st.session_state.portfolio[code]
                save_db()
                st.rerun()
else:
    st.info("目前模擬倉無部位")

# 2. 搜尋雷達
st.header("🔎 搜尋與雷達")
search = st.text_input("輸入股票代號 (如 2330)")
if search:
    d = calculate_signals(search, get_hist(search))
    if d:
        draw_card(d, "search")
        if st.button("📌 加入雷達", key=f"pin_search"):
            st.session_state.pinned_stocks[d['code']] = {}
            save_db()
            st.rerun()
    else: st.error("查無資料或連線逾時")

# 3. 雷達列表
if st.session_state.pinned_stocks:
    st.subheader("⭐ 觀測清單")
    cols = st.columns(3)
    for i, code in enumerate(list(st.session_state.pinned_stocks.keys())):
        d = calculate_signals(code, get_hist(code))
        if d:
            with cols[i % 3]:
                draw_card(d, f"pin_{code}")
                c1, c2 = st.columns(2)
                if c1.button("買進", key=f"add_{code}"):
                    st.session_state.portfolio[code] = {'entry_price': d['price'], 'qty': 1}
                    if code in st.session_state.pinned_stocks: del st.session_state.pinned_stocks[code]
                    save_db()
                    st.rerun()
                if c2.button("刪除", key=f"del_{code}"):
                    del st.session_state.pinned_stocks[code]
                    save_db()
                    st.rerun()

# 4. 全境掃描
st.sidebar.header("⚙️ 戰術控制台")
if st.sidebar.button("🚀 執行起漲掃描"):
    res = []
    with st.spinner("掃描市場中..."):
        for c in GLOBAL_MARKET_CODES:
            d = calculate_signals(c, get_hist(c))
            if d and d['is_golden']: res.append(d)
    st.session_state.scan_results = res

if st.session_state.scan_results:
    st.header("⚡ 掃描結果")
    cols = st.columns(3)
    for i, res in enumerate(st.session_state.scan_results):
        with cols[i % 3]:
            draw_card(res, f"scan_{i}")
            if st.button("加入雷達", key=f"scan_add_{res['code']}"):
                st.session_state.pinned_stocks[res['code']] = {}
                save_db()
                st.rerun()
