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
# 🛡️ 步驟一：絕對置頂的頁面設定 (保證 UI 秒開)
# ==========================================
st.set_page_config(layout="wide", page_title="54088 - 終極大腦 V28.2", initial_sidebar_state="expanded")

# 記憶體初始化：絕對乾淨，無任何背景網路請求
if 'manual_prices' not in st.session_state: st.session_state.manual_prices = {} 
if 'scan_results' not in st.session_state: st.session_state.scan_results = []
if 'scan_mode' not in st.session_state: st.session_state.scan_mode = ""
if 'temp_intel' not in st.session_state: st.session_state.temp_intel = []

COMMANDER_PIN = "0826"
USER_DB_FILE = "54088_database.json" 
MAX_CAPACITY = 40

if 'db_loaded' not in st.session_state:
    st.session_state.pinned_stocks = {}
    st.session_state.portfolio = {}
    if os.path.exists(USER_DB_FILE):
        try:
            with open(USER_DB_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                st.session_state.pinned_stocks = data.get("pinned_stocks", {})
                st.session_state.portfolio = data.get("portfolio", {})
        except: pass
    st.session_state.db_loaded = True

def save_user_db_action():
    try:
        with open(USER_DB_FILE, "w", encoding="utf-8") as f: 
            json.dump({"pinned_stocks": st.session_state.pinned_stocks, "portfolio": st.session_state.portfolio}, f, ensure_ascii=False, indent=4)
    except: pass

# ==========================================
# 🛡️ 步驟二：系統解鎖門禁
# ==========================================
if 'authenticated' not in st.session_state: 
    st.session_state.authenticated = (st.query_params.get("auth") == "54088")

def cb_login():
    if st.session_state.pwd_input == COMMANDER_PIN:
        st.session_state.authenticated = True
        st.query_params["auth"] = "54088"
    else: st.error("❌ 密碼錯誤")

if not st.session_state.authenticated:
    st.markdown("<h2 style='text-align: center; color: #444; margin-top: 10vh; font-family: monospace; letter-spacing: 5px;'>SYSTEM LOCKED</h2>", unsafe_allow_html=True)
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        st.text_input(" ", type="password", key="pwd_input", placeholder="請輸入指揮官授權密碼")
        st.button("系統解鎖", use_container_width=True, on_click=cb_login)
    st.stop() 

# ==========================================
# 🎨 視覺與樣式定義
# ==========================================
st.markdown('''<style>
.stApp { background-color: #0b0c0f !important; color: #fff !important; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
div[data-testid="stSidebar"] { background-color: #12141a !important; border-right: 1px solid #333 !important; }
div[data-testid="stButton"] > button { background-color: #1e1e24 !important; border: 1px solid #444 !important; transition: all 0.2s ease-in-out; }
div[data-testid="stButton"] > button p { color: #ffffff !important; font-weight: bold !important; font-size: 15px !important; }
.sync-btn div[data-testid="stButton"] > button { background-color: #f39c12 !important; border: 2px solid #e67e22 !important; }
.sync-btn div[data-testid="stButton"] > button p { color: #000000 !important; font-weight: 900 !important; }
.hud-box { background: linear-gradient(135deg, #1a1c23 0%, #0d1117 100%); border-radius: 10px; padding: 15px; border-left: 5px solid #f1c40f; box-shadow: 0 4px 15px rgba(0,0,0,0.5); margin-bottom: 20px;}
.special-badge { background: #1a2a3a; padding: 4px 8px; border-radius: 4px; font-size: 13px; color: #00d2ff; margin-right: 5px; border: 1px solid #3498db; display: inline-block; margin-bottom: 5px; font-weight: bold; }
.danger-badge { background: #3a1515; padding: 4px 8px; border-radius: 4px; font-size: 13px; color: #ff4d4d; margin-right: 5px; border: 1px solid #e74c3c; display: inline-block; margin-bottom: 5px; font-weight: bold; }
.tactical-summary { background: #000; border-top: 1px dashed #444; margin-top: 10px; padding: 10px; font-size: 14px; color: #f1c40f; font-weight: bold; border-radius: 5px;}
.tactical-danger { background: #1a0505; border-top: 1px dashed #e74c3c; margin-top: 10px; padding: 10px; font-size: 15px; color: #ff4d4d; font-weight: bold; border-radius: 5px;}
</style>''', unsafe_allow_html=True)

# ==========================================
# 📡 網路防護診斷與基礎名單
# ==========================================
def check_network_health():
    st.sidebar.markdown("### 📡 網路防護診斷報告")
    with st.sidebar.status("正在檢測 API 連線狀態...", expanded=True) as status:
        try:
            st.write("測試 Yahoo Finance 報價伺服器...")
            res = requests.get("https://query1.finance.yahoo.com/v8/finance/chart/2330.TW", timeout=3)
            if res.status_code == 200: st.write("✅ Yahoo API: 正常連線")
            else: st.write(f"❌ Yahoo API: 遭阻擋 (代碼 {res.status_code})")
        except: st.write("❌ Yahoo API: 徹底斷線 (伺服器 Timeout)")
        
        try:
            st.write("測試台灣證交所伺服器...")
            res = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL", timeout=3)
            if res.status_code == 200: st.write("✅ 證交所 API: 正常連線")
            else: st.write(f"❌ 證交所 API: 遭阻擋 (代碼 {res.status_code})")
        except: st.write("❌ 證交所 API: 徹底斷線 (伺服器 Timeout)")
        status.update(label="診斷完成", state="complete")

# 基礎名單，避免全域呼叫卡死雲端
TW_STOCK_NAMES = {"2330":"台積電", "2317":"鴻海", "2454":"聯發科", "2382":"廣達", "2603":"長榮", "1519":"華城", "3017":"奇鋐", "3324":"雙鴻"}
GLOBAL_MARKET_CODES = list(TW_STOCK_NAMES.keys())

# ==========================================
# 🧠 戰術演算法核心 (極度簡化以防卡死)
# ==========================================
def get_safe_yf_session():
    session = requests.Session()
    # 加入嚴格 3 秒超時設定，絕對不允許假死轉圈
    session.request = lambda *args, **kwargs: requests.Session.request(session, *args, **{**kwargs, 'timeout': 3.0})
    session.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    return session

def calculate_signals_v28(symbol):
    try:
        stock_name = TW_STOCK_NAMES.get(symbol, f"個股 {symbol}") 
        session = get_safe_yf_session()
        
        hist_df = pd.DataFrame()
        for ext in [".TW", ".TWO"]:
            try:
                tk = yf.Ticker(symbol + ext, session=session)
                temp = tk.history(period="1y").dropna(subset=['Close']) 
                if not temp.empty and len(temp) > 15:
                    hist_df = temp; break
            except: pass

        if hist_df.empty or len(hist_df) < 15: return None

        current_price = float(hist_df['Close'].iloc[-1])
        prev_price = max(float(hist_df['Close'].iloc[-2]), 0.001)
        gain = ((current_price - prev_price) / prev_price) * 100
        
        calc_df = hist_df.copy()
        ma5 = calc_df['Close'].rolling(min(5, len(calc_df))).mean().iloc[-1]
        ma20 = calc_df['Close'].rolling(min(20, len(calc_df))).mean().iloc[-1]
        ma60 = calc_df['Close'].rolling(min(60, len(calc_df))).mean().iloc[-1] if len(calc_df) >= 60 else ma20
        
        is_ma_bullish = (current_price > ma5) and (ma5 > ma20) and (ma20 > ma60)
        is_first_red = (gain >= 3.0) and (prev_price <= ma60) and (current_price > ma60)

        main_cost = ma60
        cost_label = "季線防守"

        signal_text, color_border, signal_bg = "⏳ 【耐心觀望】", "#888", "#2b2b36"
        tactical_summary = "區間震盪，在旁看戲即可。"
        is_golden_signal = False

        if is_first_red:
            signal_text, color_border, signal_bg = "✅ ✨ 破繭第一根！強勢起漲！", "#00FF00", "#153a20"; is_golden_signal = True
            tactical_summary = "✨ 底部爆量突破！起漲第一根，大膽切入並設好停損！"
        elif is_ma_bullish:
            signal_text, color_border, signal_bg = "✅ 突破或多頭確立！", "#00FF00", "#153a20"; is_golden_signal = True
            tactical_summary = "✅ 動能點火，符合右側進場標準！"

        ai_tags = []
        if is_first_red: ai_tags.append("✨ 起漲第一根")
        if is_ma_bullish: ai_tags.append("🔴 均線多頭")

        return {
            "name": stock_name, "code": symbol, "price": current_price, "gain": gain, "cost_label": cost_label,
            "signal": signal_text, "color": color_border, "signal_bg": signal_bg, "ai_tags": ai_tags, 
            "is_golden": is_golden_signal, "is_action_needed": False, "tactical_summary": tactical_summary,
            "is_first_red": is_first_red, "cost": round(main_cost,1)
        }
    except: return None

# ==========================================
# 🖥️ 側邊欄控制台
# ==========================================
with st.sidebar:
    st.markdown("<h2 style='color:#f1c40f; text-align:center;'>⚙️ 戰術控制台</h2>", unsafe_allow_html=True)
    
    st.markdown("---")
    if st.button("🚨 執行網路防護診斷 (必按)", use_container_width=True):
        check_network_health()
        
    st.markdown("---")
    st.markdown("<h4 style='color:#00FF00;'>🚀 雲端安全掃描雷達</h4>", unsafe_allow_html=True)
    st.markdown("<p style='color:#aaa; font-size:12px;'>⚠️ 採用純淨單執行緒防護，掃描時請耐心等候進度條跑完。</p>", unsafe_allow_html=True)
    
    def run_micro_scan(mode, target_codes):
        results = []
        progress_bar = st.progress(0)
        for idx, c in enumerate(target_codes):
            d = calculate_signals_v28(c)
            if d and "❌" not in d.get('signal', ''):
                if mode == "golden" and d.get('is_golden'): results.append(d)
                elif mode == "first_red" and d.get('is_first_red'): results.append(d)
            progress_bar.progress(min((idx + 1) / len(target_codes), 1.0))
        progress_bar.empty()
        return results

    if st.button("🧪 測試掃描 (僅掃台積電等 8 檔)", use_container_width=True):
        st.session_state.scan_results = run_micro_scan("golden", GLOBAL_MARKET_CODES)
        st.session_state.scan_mode = "golden"

# ==========================================
# 🖥️ 主戰情室畫面渲染
# ==========================================
col_nav1, col_nav2, col_nav3 = st.columns([5, 1, 1])
with col_nav1: st.markdown("<h1 style='color:#FFB300; margin: 0;'>54088 戰情室 V28.2 (純淨版)</h1>", unsafe_allow_html=True)
with col_nav2:
    st.markdown("<div class='sync-btn'>", unsafe_allow_html=True)
    if st.button("🔄 刷新畫面", use_container_width=True): st.rerun()
    st.markdown("</div>", unsafe_allow_html=True)
with col_nav3:
    st.markdown("<div class='lock-btn'>", unsafe_allow_html=True)
    if st.button("🔒 鎖定", use_container_width=True):
        st.session_state.authenticated = False
        st.rerun()
    st.markdown("</div>", unsafe_allow_html=True)

st.markdown(f"<div style='text-align:right; color:#888; font-size:12px; margin-bottom:10px;'>UI Rendered | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</div>", unsafe_allow_html=True)

def draw_v28_card(d):
    if not d: return
    gain_color, gain_bg = ('#ff4d4d', '#3a1515') if d.get('gain',0)>0 else (('#00FF00', '#153a20') if d.get('gain',0)<0 else ('#aaaaaa', '#333333'))
    ai_tags_html = "".join([f"<span class='special-badge'>{tag}</span>" for tag in d.get('ai_tags', [])])
    st.markdown(f"""
    <div style="border: 2px solid {d.get('color', '#444')}; border-radius: 8px; padding: 15px; background-color: #16191f; margin-bottom: 5px;">
    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:5px;">
        <span style="font-weight:bold; font-size:18px;">{d.get('name', '未知')} ({d.get('code', '未知')})</span>
        <span style="color:#888; font-size:12px;">🛡️ 防守: {d.get('cost_label', '')}</span>
    </div>
    <div style="font-size:32px; font-weight:bold; margin-bottom: 10px; display:flex; gap:12px;">{d.get('price', 0.0):.2f} <span style="font-size:16px; color:{gain_color}; background-color:{gain_bg}; padding:4px 10px; border-radius:6px;">{d.get('gain', 0.0):+.1f}%</span></div>
    <div style="margin-bottom: 5px;">{ai_tags_html}</div>
    <div style="background:{d.get('signal_bg', '#111')}; padding:10px; border-radius:6px; text-align:center; margin-bottom:10px; border: 1px solid {d.get('color', '#444')}40;"><strong style="color:{d.get('color', '#fff')}; font-size:18px;">{d.get('signal', '')}</strong></div>
    <div class="tactical-summary">📝 指揮官戰術小結：<br>{d.get('tactical_summary', '')}</div>
    </div>""", unsafe_allow_html=True)

if scan_mode_current := st.session_state.get('scan_mode', ""):
    st.markdown(f"<h2 style='color:#00FF00; margin-top:30px; border-bottom: 2px solid #00FF00; padding-bottom:5px;'>⚡ 掃描篩選結果</h2>", unsafe_allow_html=True)
    if not st.session_state.scan_results:
        st.warning("⚠️ 掃描完畢。目前沒有標的符合條件。如果連線診斷失敗，請 Reboot App 或改至本機端執行。")
    else:
        cols = st.columns(2)
        for i, d in enumerate(st.session_state.scan_results):
            with cols[i % 2]: draw_v28_card(d)
