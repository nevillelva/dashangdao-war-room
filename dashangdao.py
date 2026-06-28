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
st.set_page_config(layout="wide", page_title="54088 - 終極大腦 V29.0", initial_sidebar_state="expanded")

# 狀態初始化：無任何背景網路請求
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
    st.markdown("<h2 style='text-align: center; color: #444; margin-top: 10vh; font-family: monospace; letter-spacing: 5px;'>SYSTEM LOCKED V29</h2>", unsafe_allow_html=True)
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        pwd = st.text_input(" ", type="password", placeholder="請輸入指揮官授權密碼")
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
.scan-btn div[data-testid="stButton"] > button { background-color: #153a20 !important; border: 2px solid #00FF00 !important; margin-bottom: 5px;}
.scan-btn div[data-testid="stButton"] > button p { color: #00FF00 !important; }
.hud-box { background: linear-gradient(135deg, #1a1c23 0%, #0d1117 100%); border-radius: 10px; padding: 15px; border-left: 5px solid #f1c40f; box-shadow: 0 4px 15px rgba(0,0,0,0.5); margin-bottom: 20px;}
.special-badge { background: #1a2a3a; padding: 4px 8px; border-radius: 4px; font-size: 13px; color: #00d2ff; margin-right: 5px; border: 1px solid #3498db; display: inline-block; margin-bottom: 5px; font-weight: bold; }
.danger-badge { background: #3a1515; padding: 4px 8px; border-radius: 4px; font-size: 13px; color: #ff4d4d; margin-right: 5px; border: 1px solid #e74c3c; display: inline-block; margin-bottom: 5px; font-weight: bold; }
.tactical-summary { background: #000; border-top: 1px dashed #444; margin-top: 10px; padding: 10px; font-size: 14px; color: #f1c40f; font-weight: bold; border-radius: 5px;}
</style>''', unsafe_allow_html=True)

# ==========================================
# 📡 延遲加載資料庫 (不卡死 UI)
# ==========================================
@st.cache_resource
def get_safe_session():
    session = requests.Session()
    session.request = lambda *args, **kwargs: requests.Session.request(session, *args, **{**kwargs, 'timeout': 3.0})
    session.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    return session

@st.cache_data(ttl=86400, show_spinner=False)
def fetch_stock_names():
    api_names = {}
    try:
        res = requests.get("https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockInfo", timeout=3)
        if res.status_code == 200:
            for item in res.json().get('data', []):
                code = str(item.get('stock_id', '')).strip()
                if len(code) == 4 and code.isdigit(): api_names[code] = item.get('stock_name', code)
    except: pass
    fallbacks = {"2330":"台積電", "2317":"鴻海", "2454":"聯發科", "2382":"廣達", "2603":"長榮", "1519":"華城", "3017":"奇鋐", "3324":"雙鴻"}
    for k, v in fallbacks.items():
        if k not in api_names: api_names[k] = v
    return api_names

TW_STOCK_NAMES = fetch_stock_names()
GLOBAL_MARKET_CODES = list(TW_STOCK_NAMES.keys())

# ==========================================
# 🧠 戰術演算法核心
# ==========================================
def calculate_signals_v29(symbol):
    try:
        stock_name = TW_STOCK_NAMES.get(symbol, f"個股 {symbol}") 
        session = get_safe_session()
        
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
        ma240 = calc_df['Close'].rolling(min(240, len(calc_df))).mean().iloc[-1] if len(calc_df) >= 240 else ma60
        
        is_ma_bullish = (current_price > ma5) and (ma5 > ma20) and (ma20 > ma60)
        is_first_red = (gain >= 3.0) and (prev_price <= ma60) and (current_price > ma60)
        is_stealth = (current_price > ma60) and (gain < 2.0) and (current_price < ma60 * 1.1)
        
        # 簡單判定高息或循環底部的替代邏輯(因拔除容易當機的證交所API)
        is_yield_def = (current_price > ma240) and (current_price < ma60 * 1.05)

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
            "is_first_red": is_first_red, "is_stealth": is_stealth, "is_yield": is_yield_def, "cost": round(main_cost,1)
        }
    except: return None

# ==========================================
# 🖥️ 側邊欄控制台 (火力全開歸位)
# ==========================================
with st.sidebar:
    st.markdown("<h2 style='color:#f1c40f; text-align:center;'>⚙️ 戰術控制台</h2>", unsafe_allow_html=True)
    
    # 1. 焦點戰役情報匯入
    st.markdown("<div style='background:#16191f; padding:10px; border-radius:8px; border: 1px solid #3498db; margin-bottom:10px;'><h4 style='color:#3498db; margin-top:0px; font-size:14px;'>📡 智能情報匯入</h4>", unsafe_allow_html=True)
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

    # 2. 掃描範圍與四大雷達
    st.markdown("---")
    st.markdown("<h4 style='color:#00FF00;'>🚀 全境安全掃描雷達</h4>", unsafe_allow_html=True)
    st.markdown("<p style='color:#aaa; font-size:12px;'>⚠️ 單向安全過濾，請耐心等候進度條跑完。</p>", unsafe_allow_html=True)
    
    scan_scope = st.selectbox("🎯 選擇掃描範圍 (支援全市場)", [
        "💻 電子/半導體/光電",
        "🌐 全市場 1700+ 檔",
        "🏗️ 傳產/機電/重電",
        "🚢 航運/觀光百貨",
        "🏦 金融/保險",
        "🧬 生技/醫療"
    ])

    def get_target_codes(scope):
        if "全市場" in scope: return GLOBAL_MARKET_CODES
        elif "電子" in scope: return [c for c in GLOBAL_MARKET_CODES if c.startswith(('23','24','30','31','32','33','34','35','36','49','52','53','54','61','62','64','80','81','82'))]
        elif "傳產" in scope: return [c for c in GLOBAL_MARKET_CODES if c.startswith(('11','12','13','14','15','16','17','18','19','20','21','22','99'))]
        elif "航運" in scope: return [c for c in GLOBAL_MARKET_CODES if c.startswith(('26','27'))]
        elif "金融" in scope: return [c for c in GLOBAL_MARKET_CODES if c.startswith(('28','58'))]
        elif "生技" in scope: return [c for c in GLOBAL_MARKET_CODES if c.startswith(('17','41','47','65'))]
        return GLOBAL_MARKET_CODES

    def run_radar_scan(mode, scope):
        results = []
        target_codes = get_target_codes(scope)
        progress_bar = st.progress(0)
        status_text = st.empty()
        total = len(target_codes)
        
        for idx, c in enumerate(target_codes):
            if idx % 5 == 0 or idx == total - 1:
                status_text.text(f"📡 過濾中: 第 {idx+1}/{total} 檔 ({c}) ...")
            
            d = calculate_signals_v29(c)
            if d and "❌" not in d.get('signal', '') and d.get('price', 0) > 0:
                if mode == "golden" and d.get('is_golden'): results.append(d)
                elif mode == "first_red" and d.get('is_first_red'): results.append(d)
                elif mode == "stealth" and d.get('is_stealth'): results.append(d)
                elif mode == "yield" and d.get('is_yield'): results.append(d)
                
            progress_bar.progress(min((idx + 1) / total, 1.0))
            time.sleep(0.01) # 讓 UI 喘息防當機
            
        progress_bar.empty()
        status_text.text(f"✅ 掃描完畢！共篩選出 {len(results)} 檔。")
        return results

    st.markdown("<div class='scan-btn'>", unsafe_allow_html=True)
    if st.button("🚀 黃金起漲與魚身", use_container_width=True):
        st.session_state.scan_results = run_radar_scan("golden", scan_scope)
        st.session_state.scan_mode = "golden"
    if st.button("✨ 破繭第一根專區", use_container_width=True):
        st.session_state.scan_results = run_radar_scan("first_red", scan_scope)
        st.session_state.scan_mode = "first_red"
    if st.button("🕵️‍♂️ 魚頭潛伏與轉機", use_container_width=True):
        st.session_state.scan_results = run_radar_scan("stealth", scan_scope)
        st.session_state.scan_mode = "stealth"
    if st.button("🛡️ 總經防禦高息池", use_container_width=True):
        st.session_state.scan_results = run_radar_scan("yield", scan_scope)
        st.session_state.scan_mode = "yield"
    st.markdown("</div>", unsafe_allow_html=True)

    # 3. 焦點戰役
    st.markdown("---")
    st.markdown("<h4 style='color:#e056fd;'>🔥 焦點戰役 (選股靈感)</h4>", unsafe_allow_html=True)
    def load_hot_themes():
        hot_codes = ["3324", "3017", "2408", "3260", "2330", "2317", "1519", "2603"]
        st.session_state.temp_intel = []
        for c in hot_codes:
            if c not in st.session_state.portfolio and c not in st.session_state.pinned_stocks:
                st.session_state.temp_intel.append({'code': c, 'raw_data': f"{c}:?:?:?:?", 'cat': 'theme'})
    st.button("📥 載入今日熱門戰役", use_container_width=True, on_click=load_hot_themes)

# ==========================================
# 🖥️ 主戰情室畫面渲染
# ==========================================
col_nav1, col_nav2, col_nav3 = st.columns([5, 1, 1])
with col_nav1: st.markdown("<h1 style='color:#FFB300; margin: 0;'>54088 戰情室 V29.0 (全武裝歸位)</h1>", unsafe_allow_html=True)
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

def draw_v29_card(d, ui_key_prefix, is_portfolio=False, p_data=None):
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
    
    if not is_portfolio and d.get('code') not in st.session_state.pinned_stocks and d.get('code') not in st.session_state.portfolio:
        if st.button("📌 加入觀測雷達", key=f"pin_{ui_key_prefix}_{d.get('code')}", use_container_width=True):
            st.session_state.pinned_stocks[d.get('code')] = {'raw_data': f"{d.get('code')}:?:0:?:?:0", 'cat': 'search'}
            save_user_db_action()
            st.rerun()

# 📝 手動搜尋標的
search_query = st.text_input("📝 手動搜尋標的 (可直接輸入代號 '2313' 或名稱 '華通'，按 Enter) ：", key="search_input")
if search_query:
    raw_input = search_query.strip()
    match_digits = re.search(r'\d{4,}', raw_input)
    clean_code = match_digits.group() if match_digits else None
    if not clean_code:
        for name, code in TW_STOCK_NAMES.items():
            if raw_input in name: clean_code = code; break
    if clean_code:
        with st.spinner("獲取報價中..."):
            d = calculate_signals_v29(clean_code)
            if d: draw_v29_card(d, "search")
            else: st.error("❌ 查無此標的報價，可能已下市或網路阻擋。")

# 👁️ 焦點戰役觀測區
if st.session_state.temp_intel:
    st.markdown("<h3 style='color:#00d2ff; margin-top:20px; border-bottom: 2px solid #00d2ff; padding-bottom:5px;'>👁️ 焦點戰役觀測區 (未鎖定)</h3>", unsafe_allow_html=True)
    cols = st.columns(2)
    for i, item in enumerate(st.session_state.temp_intel):
        d = calculate_signals_v29(item.get('code'))
        if d:
            with cols[i % 2]: draw_v29_card(d, f"temp_{i}")

# 💼 總指揮的作戰庫存
if st.session_state.portfolio:
    st.markdown(f"<h2 style='color:#ff4d4d; margin-top:20px; border-bottom: 2px solid #ff4d4d; padding-bottom:5px;'>💼 總指揮的作戰庫存</h2>", unsafe_allow_html=True)
    cols = st.columns(2)
    for i, (code, p_data) in enumerate(list(st.session_state.portfolio.items())):
        d = calculate_signals_v29(code)
        if d:
            with cols[i % 2]:
                draw_v29_card(d, f"port_{code}", is_portfolio=True, p_data=p_data)
                if st.button("🚪 賣出清空", key=f"sell_{code}", use_container_width=True):
                    del st.session_state.portfolio[code]
                    save_user_db_action()
                    st.rerun()

# ⭐ 觀測雷達
if st.session_state.pinned_stocks:
    st.markdown(f"<h2 style='color:#f1c40f; margin-top:20px; border-bottom: 2px solid #f1c40f; padding-bottom:5px;'>⭐ 觀測雷達</h2>", unsafe_allow_html=True)
    cols = st.columns(2)
    for i, (code, p_data) in enumerate(list(st.session_state.pinned_stocks.items())):
        d = calculate_signals_v29(code)
        if d:
            with cols[i % 2]:
                draw_v29_card(d, f"pin_{code}")
                if st.button("❌ 刪除雷達", key=f"unpin_{code}", use_container_width=True):
                    del st.session_state.pinned_stocks[code]
                    save_user_db_action()
                    st.rerun()

# ⚡ 掃描結果
if scan_mode_current := st.session_state.get('scan_mode', ""):
    st.markdown(f"<h2 style='color:#00FF00; margin-top:30px; border-bottom: 2px solid #00FF00; padding-bottom:5px;'>⚡ 掃描篩選結果</h2>", unsafe_allow_html=True)
    if not st.session_state.scan_results:
        st.warning("⚠️ 掃描完畢。目前沒有標的符合條件。如果連線診斷失敗，請確認網路或改至本機端執行。")
    else:
        cols = st.columns(2)
        for i, d in enumerate(st.session_state.scan_results):
            with cols[i % 2]: draw_v29_card(d, f"scan_res_{i}")
