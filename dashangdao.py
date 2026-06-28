import streamlit as st
import streamlit.components.v1 as components
import yfinance as yf
import pandas as pd
from datetime import datetime
import re
import time
import json
import os
import requests
import concurrent.futures

# ==========================================
# 🛡️ 步驟一：絕對置頂的頁面與狀態初始化
# ==========================================
st.set_page_config(layout="wide", page_title="54088 - 終極大腦 V19.1 (5年深庫版)", initial_sidebar_state="expanded")

if 'manual_prices' not in st.session_state: st.session_state.manual_prices = {} 
if 'scan_results' not in st.session_state: st.session_state.scan_results = []
if 'scan_mode' not in st.session_state: st.session_state.scan_mode = ""
if 'temp_intel' not in st.session_state: st.session_state.temp_intel = []
if 'sentinel_active' not in st.session_state: st.session_state.sentinel_active = False
if 'login_error' not in st.session_state: st.session_state.login_error = False

# V19.0 核心：完全依賴記憶體，不寫入歷史檔案
if 'MEMORY_DB' not in st.session_state: st.session_state.MEMORY_DB = {}

COMMANDER_PIN = "0826"
USER_DB_FILE = "54088_database.json" 
MAX_CAPACITY = 40

if 'authenticated' not in st.session_state: 
    st.session_state.authenticated = (st.query_params.get("auth") == "54088")

def load_user_db():
    if os.path.exists(USER_DB_FILE):
        try:
            with open(USER_DB_FILE, "r", encoding="utf-8") as f: return json.load(f)
        except: pass
    return {"pinned_stocks": {}, "portfolio": {}}

def save_user_db():
    try:
        with open(USER_DB_FILE, "w", encoding="utf-8") as f: 
            json.dump({"pinned_stocks": st.session_state.pinned_stocks, "portfolio": st.session_state.portfolio}, f, ensure_ascii=False, indent=4)
    except: pass

if 'db_loaded' not in st.session_state:
    db_data = load_user_db()
    st.session_state.pinned_stocks = db_data.get("pinned_stocks", {})
    st.session_state.portfolio = db_data.get("portfolio", {})
    st.session_state.db_loaded = True

# ==========================================
# 🛡️ 步驟二：系統解鎖驗證 (保證畫面秒開)
# ==========================================
def cb_login():
    if st.session_state.pwd_input == COMMANDER_PIN:
        st.session_state.authenticated = True
        st.query_params["auth"] = "54088"
    else: st.session_state.login_error = True

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
.sync-btn div[data-testid="stButton"] > button { background-color: #f39c12 !important; border: 2px solid #e67e22 !important; }
.sync-btn div[data-testid="stButton"] > button p { color: #000000 !important; font-weight: 900 !important; }
.db-btn div[data-testid="stButton"] > button { background-color: #153a20 !important; border: 2px dashed #00FF00 !important; margin-top:20px;}
.db-btn div[data-testid="stButton"] > button p { color: #00FF00 !important;}
.hud-box { background: linear-gradient(135deg, #1a1c23 0%, #0d1117 100%); border-radius: 10px; padding: 15px; border-left: 5px solid #f1c40f; box-shadow: 0 4px 15px rgba(0,0,0,0.5); margin-bottom: 20px;}
.special-badge { background: #1a2a3a; padding: 4px 8px; border-radius: 4px; font-size: 13px; color: #00d2ff; margin-right: 5px; border: 1px solid #3498db; display: inline-block; margin-bottom: 5px; font-weight: bold; }
.danger-badge { background: #3a1515; padding: 4px 8px; border-radius: 4px; font-size: 13px; color: #ff4d4d; margin-right: 5px; border: 1px solid #e74c3c; display: inline-block; margin-bottom: 5px; font-weight: bold; }
.tactical-summary { background: #000; border-top: 1px dashed #444; margin-top: 10px; padding: 10px; font-size: 14px; color: #f1c40f; font-weight: bold; border-radius: 5px;}
.tactical-danger { background: #1a0505; border-top: 1px dashed #e74c3c; margin-top: 10px; padding: 10px; font-size: 15px; color: #ff4d4d; font-weight: bold; border-radius: 5px;}
</style>''', unsafe_allow_html=True)

# ==========================================
# 📡 基礎通訊：大盤天候與全台股名錄
# ==========================================
@st.cache_data(ttl=86400, show_spinner=False)
def fetch_all_taiwan_stock_names():
    api_names = {}
    try:
        res = requests.get("https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockInfo", timeout=5)
        if res.status_code == 200:
            for item in res.json().get('data', []):
                code = str(item.get('stock_id', '')).strip()
                if len(code) == 4 and code.isdigit(): api_names[code] = item.get('stock_name', code)
    except: pass
    fallbacks = {"2330":"台積電", "2317":"鴻海", "2454":"聯發科", "2382":"廣達", "2603":"長榮", "2408":"南亞科"}
    for k, v in fallbacks.items():
        if k not in api_names: api_names[k] = v
    return api_names

TW_STOCK_NAMES = fetch_all_taiwan_stock_names()
GLOBAL_MARKET_CODES = list(TW_STOCK_NAMES.keys())

@st.cache_data(ttl=86400, show_spinner=False)
def fetch_official_fundamentals():
    dynamic_data = {}
    for url in ["https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL", "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_peratio_analysis"]:
        try:
            res = requests.get(url, timeout=5)
            if res.status_code == 200:
                for item in res.json():
                    code = str(item.get('Code', item.get('SecuritiesCompanyCode', ''))).strip()
                    if len(code) == 4 and code.isdigit():
                        pe_str, yld_str, pb_str = str(item.get('PeRatio', item.get('PERatio', '-'))), str(item.get('DividendYield', item.get('YieldRatio', '-'))), str(item.get('PbRatio', item.get('PBRatio', '-')))
                        dynamic_data[code] = {
                            'PE': float(pe_str) if pe_str.replace('.','',1).isdigit() else 0.0,
                            'Yield': float(yld_str) if yld_str.replace('.','',1).isdigit() else 0.0,
                            'PB': float(pb_str) if pb_str.replace('.','',1).isdigit() else 0.0
                        }
        except: pass
    return dynamic_data

FUNDAMENTAL_DB = fetch_official_fundamentals()

@st.cache_data(ttl=600, show_spinner=False)
def get_market_weather():
    try:
        tw50 = yf.Ticker("0050.TW").history(period="3mo").dropna(subset=['Close'])
        twii = yf.Ticker("^TWII").history(period="1d").dropna(subset=['Close'])
        twii_str = f"加權指數: {float(twii['Close'].iloc[-1]):,.0f} 點" if not twii.empty else ""
        if tw50.empty: return "資料建立中", "#888", False, False
        c50 = float(tw50['Close'].iloc[-1])
        ma20 = float(tw50['Close'].rolling(20).mean().iloc[-1])
        gain = ((c50 - float(tw50['Close'].iloc[-2])) / float(tw50['Close'].iloc[-2])) * 100
        is_panic = (gain <= -4.0) or (c50 < float(tw50['Close'].rolling(60).mean().iloc[-1]) * 0.95)
        display_idx = twii_str if twii_str else f"0050: {c50:.1f}"
        if is_panic: return f"🌩️ 恐慌斷頭潮 ({display_idx})", "#e74c3c", c50 > ma20, True
        elif c50 > ma20: return f"☀️ 多頭順風環境 ({display_idx})", "#2ecc71", True, False
        else: return f"☁️ 空頭震盪環境 ({display_idx})", "#f1c40f", False, False
    except: return "📡 獲取中...", "#888", False, False

weather_str, weather_color, is_bull_market, is_panic = get_market_weather()

# ==========================================
# 🚀 V19.1 霸王週期大數據引擎 (RAM Memory 寫入)
# ==========================================
def build_memory_db():
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0"})
    tickers_list = [f"{c}.TW" for c in GLOBAL_MARKET_CODES] + [f"{c}.TWO" for c in GLOBAL_MARKET_CODES]
    chunk_size = 150 
    chunks = [tickers_list[i:i + chunk_size] for i in range(0, len(tickers_list), chunk_size)]
    
    progress_bar = st.progress(0)
    status_text = st.empty()
    total_chunks = len(chunks)
    new_db = {}
    
    for idx, chunk in enumerate(chunks):
        status_text.text(f"📥 正在將 5 年期大數據載入雲端記憶體... 批次 {idx+1}/{total_chunks} (約需 4~6 分鐘)")
        try:
            # 【V19.1 升級】擴張為 5 年期歷史資料 (5y)
            df = yf.download(chunk, period="5y", group_by="ticker", progress=False, session=session, threads=True)
            for tk in chunk:
                try:
                    if isinstance(df.columns, pd.MultiIndex):
                        if tk in df.columns.levels[0]: stock_df = df[tk].dropna(subset=['Close'])
                        else: continue
                    else:
                        stock_df = df.dropna(subset=['Close'])

                    if not stock_df.empty and len(stock_df) > 15:
                        symbol = tk.split(".")[0]
                        # 壓縮存入 dict 節省 RAM
                        new_db[symbol] = {
                            'Close': stock_df['Close'].tolist(),
                            'Open': stock_df['Open'].tolist(),
                            'High': stock_df['High'].tolist(),
                            'Low': stock_df['Low'].tolist(),
                            'Volume': stock_df['Volume'].tolist()
                        }
                except: pass
        except: pass
        progress_bar.progress((idx + 1) / total_chunks)
        time.sleep(0.5)
        
    st.session_state.MEMORY_DB = new_db
    status_text.text(f"✅ 5 年期雲端記憶體庫建置完成！共 {len(new_db)} 檔。此狀態將保持到伺服器休眠。")
    progress_bar.empty()

def get_single_stock_history(symbol):
    if symbol in st.session_state.MEMORY_DB:
        return pd.DataFrame(st.session_state.MEMORY_DB[symbol])
    
    session = requests.Session()
    for ext in [".TW", ".TWO"]:
        try:
            tk = yf.Ticker(symbol + ext, session=session)
            temp_hist = tk.history(period="5y").dropna(subset=['Close'])
            if not temp_hist.empty and len(temp_hist) > 15: return temp_hist
        except: pass
    return pd.DataFrame()

# ==========================================
# 🧠 戰術演算法核心 (支援 5 年均線與循環防線)
# ==========================================
def calculate_signals_v19(symbol, category_type="main", mode="短線技術動能單", manual_target=0.0, portfolio_data=None, is_panic_global=False):
    try:
        stock_name = TW_STOCK_NAMES.get(symbol, f"個股 {symbol}") 
        hist_df = get_single_stock_history(symbol)

        if hist_df.empty or len(hist_df) < 10:
            return {
                "name": stock_name, "code": symbol, "price": 0.0, "gain": 0.0, "cost": 0.0, "cost_label": "資料庫缺漏",
                "signal": "❌ 【無報價資料】請確認是否下市", "color": "#444", "signal_bg": "#111", "ai_tags": ["⚠️ 待查"], 
                "raw_data": f"{symbol}:?:?:?:?", "cat": category_type, "is_golden": False, "is_first_red": False,
                "is_action_needed": False, "tactical_summary": "📡 無法取得 5 年報價，建議稍候再試。",
                "is_high_yield": False, "is_cyclical": False, "vol_ratio": 0.0, "diff_from_cost": 0.0
            }

        fund_info = FUNDAMENTAL_DB.get(symbol, {})
        dyn_pe, dynamic_pb = fund_info.get('PE', 0.0), fund_info.get('PB', 0.0)

        current_price = float(hist_df['Close'].iloc[-1])
        prev_price = max(float(hist_df['Close'].iloc[-2]), 0.001)
        open_p = float(hist_df['Open'].iloc[-1])
        high_p = float(hist_df['High'].iloc[-1])
        
        gain = ((current_price - prev_price) / prev_price) * 100
        vol = int(hist_df['Volume'].iloc[-1] / 1000)
        vol_5d = max(hist_df['Volume'].iloc[-6:-1].mean() / 1000, 0.01) 
        vol_ratio = vol / vol_5d 
        
        calc_df = hist_df.copy()
        ma5 = calc_df['Close'].rolling(min(5, len(calc_df))).mean().iloc[-1]
        ma20 = calc_df['Close'].rolling(min(20, len(calc_df))).mean().iloc[-1]
        ma60 = calc_df['Close'].rolling(min(60, len(calc_df))).mean().iloc[-1] if len(calc_df) >= 60 else ma20
        ma120 = calc_df['Close'].rolling(min(120, len(calc_df))).mean().iloc[-1] if len(calc_df) >= 120 else ma60
        ma240 = calc_df['Close'].rolling(min(240, len(calc_df))).mean().iloc[-1] if len(calc_df) >= 240 else ma120
        
        # 【V19.1 循環股殺手鐧】計算 2 年線 (480MA) 與 5 年線 (1200MA)
        ma480 = calc_df['Close'].rolling(min(480, len(calc_df))).mean().iloc[-1] if len(calc_df) >= 480 else ma240
        ma1200 = calc_df['Close'].rolling(min(1200, len(calc_df))).mean().iloc[-1] if len(calc_df) >= 1200 else ma480

        is_ma_bullish = (current_price > ma5) and (ma5 > ma20) and (ma20 > ma60)
        ma_squeeze = (max(ma5, ma20, ma60) - min(ma5, ma20, ma60)) / max(min(ma5, ma20, ma60), 0.01) < 0.05 
        w_bottom_breakout = ma_squeeze and (current_price > max(ma5, ma20, ma60)) and (vol_ratio >= 1.5)
        is_first_red = (gain >= 3.0) and (vol_ratio >= 2.0) and (prev_price <= ma60 or prev_price <= ma20) and (current_price > ma60)

        body = abs(current_price - open_p)
        upper_shadow = high_p - max(open_p, current_price)
        is_shooting_star = (upper_shadow > (body * 1.5)) and (high_p > ma5)
        is_fake_breakout = (vol_ratio >= 2.0) and is_shooting_star
        is_huge_vol = vol > (vol_5d * 2.0)                
        is_break_ma5 = current_price < ma5                
        sell_cond_count = sum([is_huge_vol, is_shooting_star, is_break_ma5])

        entry_price = float(portfolio_data.get('entry_price', 0.0)) if portfolio_data else 0.0
        roi_pct = ((current_price - entry_price) / entry_price) * 100 if entry_price > 0 else 0.0
        
        # 【V19.1 核心防禦切換】
        is_high_yield = fund_info.get('Yield', 0.0) >= 5.0
        is_cyclical = (0 < dynamic_pb < 1.2) or (0 < dyn_pe < 12.0)

        if is_cyclical:
            # 景氣循環股防線：看 5 年大底 (1200MA) 或 2 年大底 (480MA)
            main_cost = ma480 if current_price >= ma480 * 0.96 else (ma1200 if current_price >= ma1200 * 0.96 else ma240)
            cost_label = "2~5年循環大底"
        else:
            # 成長股防線：看年線 (240MA) 或半年線 (120MA)
            main_cost = ma240 if current_price >= ma240 * 0.96 else (ma120 if current_price >= ma120 * 0.96 else ma60)
            cost_label = "半年/年線防守"

        buy_high = round(main_cost * 1.03, 1)

        ACTION_WAIT, ACTION_NO, ACTION_YES, ACTION_HOLD = "⏳ 【耐心觀望】", "❌ 【極度危險】", "✅ 【果斷買進】", "🛡️ 【保護持股】"
        signal_text, color_border, signal_bg = "", "", ""
        is_action_needed, is_golden_signal = False, False
        tactical_summary = "區間震盪，主力籌碼未明，在旁看戲即可。"

        if is_fake_breakout: 
            signal_text, color_border, signal_bg = f"{ACTION_NO} 🚨 主力誘多，請勿追高！", "#e74c3c", "#3a1515"
            is_action_needed = True
            tactical_summary = "❌ 【主力誘多】高檔爆量留長上影線，假突破訊號！有庫存快跑！"
        elif entry_price > 0 and roi_pct <= -10.0: 
            signal_text, color_border, signal_bg = f"{ACTION_NO} 觸發 10% 停損結界！", "#e74c3c", "#3a1515"
            is_action_needed = True
            tactical_summary = "🩸 【斷尾求生】虧損已達 10% 底線，嚴格執行紀律，立刻停損！"
        elif sell_cond_count >= 2 and roi_pct > 0: 
            signal_text, color_border, signal_bg = f"{ACTION_HOLD} 危險訊號，分批停利。", "#f1c40f", "#3a3015"
            is_action_needed = True
            tactical_summary = "🟡 【見好就收】技術面已現敗象，請分批停利入袋。"
        elif sell_cond_count >= 2 and roi_pct <= 0: 
            signal_text, color_border, signal_bg = f"{ACTION_NO} 短線轉空，認賠殺出。", "#e74c3c", "#3a1515"
            is_action_needed = True
            tactical_summary = "❌ 【放棄幻想】股價破線且爆量，趨勢已死，直接認賠換股。"
        elif is_macro_panic_global: 
            if current_price <= buy_high: 
                signal_text, color_border, signal_bg = f"{ACTION_YES} 斷頭潮！左側重壓！", "#00FF00", "#153a20"; is_golden_signal = True
                tactical_summary = "✅ 【危機入市】大盤恐慌下殺，此標的已超跌，適合勇敢左側買進！"
            else: 
                signal_text, color_border, signal_bg = f"{ACTION_WAIT} 等賣壓打下來再撿！", "#f39c12", "#3a3015"
                tactical_summary = "⏳ 【耐心等待】股價尚未殺入安全區，請等待恐慌發酵。"
        elif is_first_red:
            signal_text, color_border, signal_bg = f"{ACTION_YES} ✨ 破繭第一根！強勢起漲！", "#00FF00", "#153a20"; is_golden_signal = True
            tactical_summary = "✨ 【絕佳買點】底部爆量突破！起漲第一根，請大膽切入並設好停損！"
        elif is_ma_bullish:
            signal_text, color_border, signal_bg = f"{ACTION_YES} 突破或多頭確立！", "#00FF00", "#153a20"; is_golden_signal = True
            tactical_summary = "✅ 【果斷切入】動能點火，符合右側進場標準！"
        else:
            signal_text, color_border, signal_bg = f"{ACTION_HOLD} 區間震盪，輕鬆看戲。", "#ccc", "#2b2b36"

        ai_tags = []
        if is_fake_breakout: ai_tags.append("🚨 假突破")
        if is_first_red: ai_tags.append("✨ 起漲第一根")
        if is_break_ma5: ai_tags.append("🟢 破 5MA")
        if current_price < ma20: ai_tags.append("🟢 破月線")
        if vol_ratio >= 2.5: ai_tags.append("🔴 爆量攻擊")
        if is_ma_bullish: ai_tags.append("🔴 均線多頭")
        if not ai_tags: ai_tags.append("⚪ 量縮整理")

        return {
            "name": stock_name, "code": symbol, "price": current_price, "gain": gain, "cost_label": cost_label,
            "signal": signal_text, "color": color_border, "signal_bg": signal_bg, "ai_tags": ai_tags, 
            "raw_data": f"{symbol}:?:?:?:?", "cat": category_type, 
            "is_golden": is_golden_signal, "is_action_needed": is_action_needed, "tactical_summary": tactical_summary,
            "is_high_yield": is_high_yield, "is_cyclical": is_cyclical, "is_first_red": is_first_red, 
            "vol_ratio": vol_ratio, "cost": main_cost, "diff_from_cost": ((current_price - main_cost)/main_cost)*100
        }
    except Exception as e: return None

def calc_real_profit(cost, price, qty):
    if cost <= 0: return 0, 0
    buy_val = cost * qty * 1000
    sell_val = price * qty * 1000
    profit = sell_val - buy_val - max(20, int(buy_val * 0.001425)) - max(20, int(sell_val * 0.001425)) - int(sell_val * 0.003)
    return profit, (profit/buy_val)*100 if buy_val > 0 else 0

# ==========================================
# 🖥️ 側邊欄控制台
# ==========================================
def cb_ui_logout(): st.session_state.authenticated = False
def cb_ui_sync(): pass
def cb_pin_stock(code, raw_data, cat):
    st.session_state.pinned_stocks[code] = {'raw_data': raw_data, 'cat': cat}
    save_user_db()
def cb_unpin_stock(code):
    if code in st.session_state.pinned_stocks: del st.session_state.pinned_stocks[code]
    save_user_db()
def cb_sell_stock(code):
    if code in st.session_state.portfolio: del st.session_state.portfolio[code]
    save_user_db()

with st.sidebar:
    st.markdown("<h2 style='color:#f1c40f; text-align:center;'>⚙️ 戰術控制台</h2>", unsafe_allow_html=True)
    
    st.markdown("---")
    st.markdown("<h4 style='color:#00FF00;'>🚀 雲端零延遲極速掃描</h4>", unsafe_allow_html=True)
    
    if not st.session_state.MEMORY_DB:
        st.error("⚠️ 雲端記憶體尚未建立！請先點擊最下方『啟動 5 年期數據引擎』載入大循環數據。")
    else:
        st.success(f"🗄️ 雲端記憶體狀態：已載入 {len(st.session_state.MEMORY_DB)} 檔標的 (含 5 年線資料)")
        
        scan_scope = st.selectbox("🎯 選擇掃描範圍", [
            "🌐 全市場 1700+ 檔",
            "💻 電子/半導體/光電",
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

        def run_v19_lightspeed_scan(mode, scope):
            results = []
            target_codes = get_target_codes(scope)
            for c in target_codes:
                d = calculate_signals_v19(c, "scan", "短線技術動能單", 0.0, None, is_panic)
                if d and "❌" not in d.get('signal', '') and d.get('price', 0) > 0:
                    if mode == "golden" and d.get('is_golden'): results.append(d)
                    elif mode == "first_red" and d.get('is_first_red'): results.append(d)
                    elif mode == "stealth" and d.get('vol_ratio', 0) >= 1.5 and d.get('diff_from_cost', 99) <= 15.0: results.append(d)
                    elif mode == "yield" and (d.get('is_high_yield') or d.get('is_cyclical')): results.append(d)
            return results

        if st.button("🚀 黃金起漲與魚身 (秒殺)", use_container_width=True):
            st.session_state.scan_results = run_v19_lightspeed_scan("golden", scan_scope)
            st.session_state.scan_mode = "golden"; st.rerun()
        if st.button("✨ 破繭第一根專區 (秒殺)", use_container_width=True):
            st.session_state.scan_results = run_v19_lightspeed_scan("first_red", scan_scope)
            st.session_state.scan_mode = "first_red"; st.rerun()
        if st.button("🕵️‍♂️ 魚頭潛伏與轉機 (秒殺)", use_container_width=True):
            st.session_state.scan_results = run_v19_lightspeed_scan("stealth", scan_scope)
            st.session_state.scan_mode = "stealth"; st.rerun()
        if st.button("🛡️ 總經防禦與深度循環股 (秒殺)", use_container_width=True):
            st.session_state.scan_results = run_v19_lightspeed_scan("yield", scan_scope)
            st.session_state.scan_mode = "yield"; st.rerun()

    st.markdown("---")
    sentinel_label = "🔕 關閉哨兵模式" if st.session_state.sentinel_active else "🔔 啟動哨兵模式"
    if st.button(sentinel_label, use_container_width=True):
        st.session_state.sentinel_active = not st.session_state.sentinel_active
        st.rerun()
        
    st.markdown("---")
    st.markdown("<div class='db-btn'>", unsafe_allow_html=True)
    if st.button("📥 啟動 5 年期雲端數據引擎 (盤前必點)", use_container_width=True):
        build_memory_db()
        st.rerun()
    st.markdown("</div>", unsafe_allow_html=True)

# ==========================================
# 🖥️ 主戰情室畫面渲染
# ==========================================
col_navbar1, col_navbar2, col_navbar3 = st.columns([5, 1, 1])
with col_navbar1: st.markdown("<h1 style='color:#FFB300; margin: 0;'>54088 戰情室 V19.1 (5年深庫版)</h1>", unsafe_allow_html=True)
with col_navbar2:
    st.markdown("<div class='sync-btn'>", unsafe_allow_html=True)
    st.button("🔄 刷新", use_container_width=True, on_click=cb_ui_sync) 
    st.markdown("</div>", unsafe_allow_html=True)
with col_navbar3:
    st.markdown("<div class='lock-btn'>", unsafe_allow_html=True)
    st.button("🔒 鎖定", use_container_width=True, on_click=cb_ui_logout)
    st.markdown("</div>", unsafe_allow_html=True)

st.markdown(f"<div style='text-align:right; color:#888; font-size:12px; margin-bottom:10px;'>系統狀態：正常連線中 | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</div>", unsafe_allow_html=True)

port_loaded, pin_loaded = {}, {}
for code, p in st.session_state.portfolio.items():
    d = calculate_signals_v19(code, p.get('cat', 'main'), p.get('mode', '短線技術動能單'), p.get('manual_target', 0.0), p, is_panic)
    if d: port_loaded[code] = d
for code, p in st.session_state.pinned_stocks.items():
    d = calculate_signals_v19(code, p.get('cat', 'main'), is_macro_panic_global=is_panic)
    if d: pin_loaded[code] = d

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
<strong>🌅 今日戰情速報：</strong>大盤目前判定為 {weather_str.split(' ')[1] if ' ' in weather_str else weather_str}。 {market_suggestion}
</div>
<div class='hud-metric'><span style='color:#aaa;'>庫存 / 雷達數量</span> <strong style='color:#fff;'>{len(port_loaded)} / {len(pin_loaded)} 檔</strong></div>
<div class='hud-metric'><span style='color:#aaa;'>總未實現損益</span> <strong style='color:{'#ff4d4d' if total_unrealized>0 else '#00FF00'}; font-size:18px;'>{total_unrealized:+,.0f} 元</strong></div>
<div class='health-bar-bg'><div class='{'health-bar-fill-green' if total_unrealized >= 0 else 'health-bar-fill-red'}' style='width: {max(0, min(100, 50 + (total_unrealized / 50000) * 50))}%;'></div></div>
<div class='hud-metric' style='margin-top:10px; padding-top:10px; border-top:1px dashed #333;'><span style='color:#2ecc71;'>🎯 雷達可狙擊目標：<strong>{golden_targets} 檔</strong></span><span style='color:#e74c3c;'>🚨 庫存強迫撤退：<strong>{action_needed} 檔</strong></span></div>
</div>
""", unsafe_allow_html=True)

search_query = st.text_input("📝 搜尋標的 (輸入代號 '2313' 或名稱 '華通'，按 Enter) ：", key="search_input")

def draw_v19_card(d, ui_key_prefix, is_portfolio=False, p_data=None):
    if not d: return
    gain_color, gain_bg = ('#ff4d4d', '#3a1515') if d.get('gain',0)>0 else (('#00FF00', '#153a20') if d.get('gain',0)<0 else ('#aaaaaa', '#333333'))
    ai_tags_html = "".join([f"<span class='{'danger-badge' if '🚨' in tag or '🔴' in tag or '❌' in tag else 'special-badge'}'>{tag}</span>" for tag in d.get('ai_tags', [])])
    summary_class = "tactical-danger" if d.get('is_action_needed') else "tactical-summary"

    st.markdown(f"""
    <div style="border: 2px solid {d.get('color', '#444')}; border-radius: 8px; padding: 15px; background-color: #16191f; margin-bottom: 5px;">
    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:5px;">
        <span style="font-weight:bold; font-size:18px;">{d.get('name', '未知')} ({d.get('code', '未知')})</span>
        <span style="color:#888; font-size:12px;">🛡️ 防守: {d.get('cost_label', '')}</span>
    </div>
    <div style="font-size:32px; font-weight:bold; margin-bottom: 10px; display:flex; gap:12px;">{d.get('price', 0.0):.2f} <span style="font-size:16px; color:{gain_color}; background-color:{gain_bg}; padding:4px 10px; border-radius:6px;">{d.get('gain', 0.0):+.1f}%</span></div>
    <div style="margin-bottom: 5px;">{ai_tags_html}</div>
    <div style="background:{d.get('signal_bg', '#111')}; padding:10px; border-radius:6px; text-align:center; margin-bottom:10px; border: 1px solid {d.get('color', '#444')}40;"><strong style="color:{d.get('color', '#fff')}; font-size:18px;">{d.get('signal', '')}</strong></div>
    <div class="{summary_class}">📝 指揮官戰術小結：<br>{d.get('tactical_summary', '')}</div>
    </div>""", unsafe_allow_html=True)
    if not is_portfolio and d.get('code') not in st.session_state.pinned_stocks:
        st.button(f"📌 加入觀測雷達", key=f"pin_{ui_key_prefix}_{d.get('code')}", use_container_width=True, on_click=cb_pin_stock, args=(d.get('code'), f"{d.get('code')}:?:0:?:?:0", 'search'))

if search_query:
    raw_input = search_query.strip().replace('.TW', '').replace('.TWO', '')
    match_digits = re.search(r'\d{4,}', raw_input)
    clean_code = match_digits.group() if match_digits else None
    if not clean_code:
        for name, code in {v: k for k, v in TW_STOCK_NAMES.items()}.items():
            if raw_input in name: clean_code = code; break
    if clean_code:
        d = calculate_signals_v19(clean_code, "search", is_macro_panic_global=is_panic)
        draw_v19_card(d, "search")

if st.session_state.temp_intel:
    st.markdown("<h3 style='color:#00d2ff; margin-top:20px; border-bottom: 2px solid #00d2ff; padding-bottom:5px;'>👁️ 焦點戰役觀測區 (未鎖定)</h3>", unsafe_allow_html=True)
    cols = st.columns(2)
    for i, item in enumerate(st.session_state.temp_intel):
        d = calculate_signals_v19(item.get('code'), item.get('cat'), is_macro_panic_global=is_panic)
        if d:
            with cols[i % 2]: draw_v19_card(d, f"temp_{i}")

if st.session_state.portfolio:
    st.markdown(f"<h2 style='color:#ff4d4d; margin-top:20px; border-bottom: 2px solid #ff4d4d; padding-bottom:5px;'>💼 總指揮的作戰庫存</h2>", unsafe_allow_html=True)
    cols = st.columns(2)
    for i, (code, p_data) in enumerate(list(st.session_state.portfolio.items())):
        d = port_loaded.get(code)
        if d:
            with cols[i % 2]:
                p_profit, p_roi = calc_real_profit(p_data.get('entry_price', 0), d.get('price', 0), p_data.get('qty', 0))
                st.markdown(f"""<div style="border: 4px solid {'#e74c3c' if d.get('is_action_needed') else '#00FF00'}; border-radius: 8px; padding: 15px; background-color: #1a1a24; margin-bottom: 5px;"><div style="font-weight:bold; font-size:18px;">{d.get('name')} ({d.get('code')})</div><div style="font-size:24px; font-weight:bold; color:{'#e74c3c' if p_profit<0 else '#ff4d4d'};">{p_profit:+,.0f} 元 ({p_roi:+.1f}%)</div></div>""", unsafe_allow_html=True)
                draw_v19_card(d, f"port_{code}", is_portfolio=True, p_data=p_data)
                st.button(f"🚪 賣出清空", key=f"sell_{code}", use_container_width=True, on_click=cb_sell_stock, args=(code,))

if st.session_state.pinned_stocks:
    st.markdown(f"<h2 style='color:#f1c40f; margin-top:20px; border-bottom: 2px solid #f1c40f; padding-bottom:5px;'>⭐ 觀測雷達</h2>", unsafe_allow_html=True)
    cols = st.columns(2)
    for i, (code, p_data) in enumerate(list(st.session_state.pinned_stocks.items())):
        d = pin_loaded.get(code)
        if d:
            with cols[i % 2]:
                draw_v19_card(d, f"pin_{code}")
                st.button(f"❌ 刪除雷達", key=f"unpin_{code}", use_container_width=True, on_click=cb_unpin_stock, args=(code,))

if scan_mode_current := st.session_state.get('scan_mode', ""):
    st.markdown(f"<h2 style='color:#00FF00; margin-top:30px; border-bottom: 2px solid #00FF00; padding-bottom:5px;'>⚡ 雲端光速掃描結果</h2>", unsafe_allow_html=True)
    if not st.session_state.scan_results:
        st.warning("⚠️ 報告指揮官，掃描完畢。目前沒有任何標的符合條件。代表資金全數觀望或已過熱，建議保留現金，切勿硬買！")
    else:
        cols = st.columns(2)
        alpha_list = ""
        for idx, d in enumerate(st.session_state.scan_results):
            char_label = chr(65 + (idx % 26))
            alpha_list += f"{char_label}. {d.get('code')} {d.get('name')}\n"
        st.markdown("### 📋 一鍵複製名單")
        st.code(alpha_list, language="text")

        for i, d in enumerate([x for x in st.session_state.scan_results if x.get('code') not in st.session_state.portfolio and x.get('code') not in st.session_state.pinned_stocks]):
            with cols[i % 2]: draw_v19_card(d, f"scan_res_{i}")
