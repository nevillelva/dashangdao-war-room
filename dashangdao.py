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
st.set_page_config(layout="wide", page_title="54088 - 終極大腦 V27.0", initial_sidebar_state="expanded")

# ==========================================
# 🛡️ 步驟二：狀態初始化 (拔除所有自動 rerun 機制)
# ==========================================
if 'scan_results' not in st.session_state: st.session_state.scan_results = []
if 'scan_mode' not in st.session_state: st.session_state.scan_mode = ""
if 'temp_intel' not in st.session_state: st.session_state.temp_intel = []
if 'sentinel_active' not in st.session_state: st.session_state.sentinel_active = False

COMMANDER_PIN = "0826"
USER_DB_FILE = "54088_database.json" 
MAX_CAPACITY = 40

# 靜態讀取本地庫存資料 (僅讀取，絕不引發重新整理)
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
# 🛡️ 步驟三：超穩定 Callbacks (事件回呼)
# ==========================================
# 使用 Callback 確保狀態修改在頁面渲染前完成，徹底消滅 st.rerun() 迴圈
if 'authenticated' not in st.session_state: 
    st.session_state.authenticated = (st.query_params.get("auth") == "54088")

def cb_login():
    if st.session_state.pwd_input == COMMANDER_PIN:
        st.session_state.authenticated = True
        st.query_params["auth"] = "54088"
    else: st.error("❌ 密碼錯誤")

def cb_ui_logout():
    st.session_state.authenticated = False
    if "auth" in st.query_params: del st.query_params["auth"]

def cb_ui_sync(): 
    st.session_state.temp_intel = []
    st.cache_data.clear() 

def cb_pin_stock(code, raw_data, cat):
    if len(st.session_state.pinned_stocks) >= MAX_CAPACITY: return
    st.session_state.pinned_stocks[code] = {'raw_data': raw_data, 'cat': cat}
    st.session_state.temp_intel = [x for x in st.session_state.temp_intel if x.get('code') != code]
    save_user_db_action()

def cb_unpin_stock(code):
    if code in st.session_state.pinned_stocks:
        del st.session_state.pinned_stocks[code]
        save_user_db_action()

def cb_sell_stock(code):
    if code in st.session_state.portfolio:
        del st.session_state.portfolio[code]
        save_user_db_action()

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
    
    st.session_state.portfolio[code] = {
        "entry_price": round(cost, 2), "qty": round(qty, 3), "raw_data": raw_data, 
        "cat": cat, "mode": mode, "manual_target": manual_target, "catalyst": catalyst
    }
    if code in st.session_state.pinned_stocks: del st.session_state.pinned_stocks[code]
    st.session_state.temp_intel = [x for x in st.session_state.temp_intel if x.get('code') != code]
    save_user_db_action()

def cb_load_hot_themes():
    hot_codes = ["3324", "3017", "2408", "3260", "2330", "2317", "1519", "2603"]
    st.session_state.temp_intel = []
    for c in hot_codes:
        if c not in st.session_state.portfolio and c not in st.session_state.pinned_stocks:
            st.session_state.temp_intel.append({'code': c, 'raw_data': f"{c}:?:?:?:?", 'cat': 'theme'})

# ==========================================
# 🛡️ 步驟四：系統解鎖門禁
# ==========================================
if not st.session_state.authenticated:
    st.markdown("<h2 style='text-align: center; color: #444; margin-top: 10vh; font-family: monospace; letter-spacing: 5px;'>SYSTEM LOCKED V27</h2>", unsafe_allow_html=True)
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        st.text_input(" ", type="password", key="pwd_input", placeholder="請輸入指揮官授權密碼")
        st.button("系統解鎖", use_container_width=True, on_click=cb_login)
    st.stop() # 門禁未開，絕對停止執行

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
.hud-title { color: #f1c40f; font-size: 14px; font-weight: bold; margin-bottom: 10px; border-bottom: 1px solid #333; padding-bottom: 5px;}
.hud-metric { display: flex; justify-content: space-between; align-items: center; margin-bottom: 5px;}
.health-bar-bg { width: 100%; background-color: #333; border-radius: 5px; height: 8px; margin-top: 5px; overflow: hidden;}
.health-bar-fill-green { height: 100%; background-color: #2ecc71; transition: width 0.5s ease;}
.health-bar-fill-red { height: 100%; background-color: #e74c3c; transition: width 0.5s ease;}
.special-badge { background: #1a2a3a; padding: 4px 8px; border-radius: 4px; font-size: 13px; color: #00d2ff; margin-right: 5px; border: 1px solid #3498db; display: inline-block; margin-bottom: 5px; font-weight: bold; }
.danger-badge { background: #3a1515; padding: 4px 8px; border-radius: 4px; font-size: 13px; color: #ff4d4d; margin-right: 5px; border: 1px solid #e74c3c; display: inline-block; margin-bottom: 5px; font-weight: bold; }
.tactical-summary { background: #000; border-top: 1px dashed #444; margin-top: 10px; padding: 10px; font-size: 14px; color: #f1c40f; font-weight: bold; border-radius: 5px;}
.tactical-danger { background: #1a0505; border-top: 1px dashed #e74c3c; margin-top: 10px; padding: 10px; font-size: 15px; color: #ff4d4d; font-weight: bold; border-radius: 5px;}
</style>''', unsafe_allow_html=True)

# ==========================================
# 📡 基礎通訊：大盤天候與全台股基本面庫
# ==========================================
@st.cache_resource
def get_safe_session():
    session = requests.Session()
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
    fallbacks = {"2330":"台積電", "2317":"鴻海", "2454":"聯發科", "2382":"廣達", "2303":"聯電", "2603":"長榮", "2408":"南亞科"}
    for k, v in fallbacks.items():
        if k not in api_names: api_names[k] = v
    return api_names

@st.cache_data(ttl=86400, show_spinner=False)
def fetch_fundamentals():
    db = {}
    try:
        res = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL", timeout=3)
        if res.status_code == 200:
            for item in res.json():
                code = str(item.get('Code', '')).strip()
                if len(code) == 4 and code.isdigit():
                    pe_str, yld_str, pb_str = str(item.get('PeRatio', '-')), str(item.get('DividendYield', '-')), str(item.get('PbRatio', '-'))
                    db[code] = {
                        'PE': float(pe_str) if pe_str.replace('.','',1).isdigit() else 0.0,
                        'Yield': float(yld_str) if yld_str.replace('.','',1).isdigit() else 0.0,
                        'PB': float(pb_str) if pb_str.replace('.','',1).isdigit() else 0.0
                    }
    except: pass
    return db

@st.cache_data(ttl=300, show_spinner=False)
def get_market_weather():
    try:
        session = get_safe_session()
        tw50 = yf.Ticker("0050.TW", session=session).history(period="3mo").dropna(subset=['Close'])
        twii = yf.Ticker("^TWII", session=session).history(period="1d").dropna(subset=['Close'])
        twii_str = f"加權指數: {float(twii['Close'].iloc[-1]):,.0f} 點" if not twii.empty else ""
        if tw50.empty: return "大盤連線異常", "#888", False, False
        
        c50 = float(tw50['Close'].iloc[-1])
        ma20 = float(tw50['Close'].rolling(20).mean().iloc[-1])
        gain = ((c50 - float(tw50['Close'].iloc[-2])) / float(tw50['Close'].iloc[-2])) * 100
        is_panic = (gain <= -4.0) or (c50 < float(tw50['Close'].rolling(60).mean().iloc[-1]) * 0.95)
        display_idx = twii_str if twii_str else f"0050: {c50:.1f}"
        
        if is_panic: return f"🌩️ 恐慌斷頭潮 ({display_idx})", "#e74c3c", c50 > ma20, True
        elif c50 > ma20: return f"☀️ 多頭順風環境 ({display_idx})", "#2ecc71", True, False
        else: return f"☁️ 空頭震盪環境 ({display_idx})", "#f1c40f", False, False
    except: return "📡 大盤資料獲取中...", "#888", False, False

TW_STOCK_NAMES = fetch_stock_names()
FUNDAMENTAL_DB = fetch_fundamentals()
GLOBAL_MARKET_CODES = list(TW_STOCK_NAMES.keys())
weather_str, weather_color, is_bull_market, is_panic = get_market_weather()

# ==========================================
# 🚀 V27.0 革命性：集裝箱打包下載引擎 (1次請求抵過數百次，絕對秒開)
# ==========================================
@st.cache_data(ttl=120, show_spinner=False)
def batch_download_histories(codes):
    """將所有要顯示的股票打包成一包，向 Yahoo 只發送 1 次請求，秒殺載入時間"""
    if not codes: return {}
    tickers = [f"{c}.TW" for c in codes] + [f"{c}.TWO" for c in codes]
    res_dict = {}
    try:
        session = get_safe_session()
        # threads=False 確保系統絕對不會因資源搶奪而崩潰
        df = yf.download(tickers, period="2y", group_by="ticker", progress=False, session=session, threads=False)
        for c in codes:
            for ext in [".TW", ".TWO"]:
                tk = f"{c}{ext}"
                try:
                    if isinstance(df.columns, pd.MultiIndex):
                        if tk in df.columns.levels[0]:
                            h = df[tk].dropna(subset=['Close'])
                            if not h.empty and len(h) > 15:
                                res_dict[c] = h
                                break
                    elif not isinstance(df.columns, pd.MultiIndex) and len(tickers) <= 2:
                        h = df.dropna(subset=['Close'])
                        if not h.empty and len(h) > 15:
                            res_dict[c] = h
                            break
                except: pass
    except: pass
    return res_dict

# 在畫面渲染前，一次性抓回儀表板上所有需要的股票資料 (庫存 + 雷達 + 搜尋)
dashboard_codes = list(st.session_state.portfolio.keys()) + list(st.session_state.pinned_stocks.keys())
if st.session_state.temp_intel:
    dashboard_codes.extend([item['code'] for item in st.session_state.temp_intel])
search_query = st.text_input("📝 手動搜尋標的 (可直接輸入代號 '2313' 或名稱 '華通'，按 Enter) ：", key="search_input")
search_code = None
if search_query:
    raw_input = search_query.strip()
    match_digits = re.search(r'\d{4,}', raw_input)
    if match_digits: search_code = match_digits.group()
    else:
        for name, code in TW_STOCK_NAMES.items():
            if raw_input in name: search_code = code; break
    if search_code: dashboard_codes.append(search_code)

# 執行終極批次下載！
dashboard_histories = batch_download_histories(list(set(dashboard_codes)))

# ==========================================
# 🧠 戰術演算法核心 (吃剛才打包載回的資料)
# ==========================================
def calculate_signals_from_df(symbol, hist_df, portfolio_data=None, is_panic_global=False):
    try:
        stock_name = TW_STOCK_NAMES.get(symbol, f"個股 {symbol}") 
        if hist_df is None or hist_df.empty or len(hist_df) < 15:
            return {
                "name": stock_name, "code": symbol, "price": 0.0, "gain": 0.0, "cost": 0.0, "cost_label": "無報價",
                "signal": "❌ 【無報價資料】可能已下市或連線異常", "color": "#444", "signal_bg": "#111", "ai_tags": ["⚠️ 待查"], 
                "is_golden": False, "is_first_red": False, "is_action_needed": False, "tactical_summary": "📡 無法取得報價，系統已自動隔離。"
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
        ma480 = calc_df['Close'].rolling(min(480, len(calc_df))).mean().iloc[-1] if len(calc_df) >= 480 else ma240

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
        
        is_cyclical = (0 < dynamic_pb < 1.2) or (0 < dyn_pe < 12.0)
        main_cost = ma480 if is_cyclical and current_price >= ma480 * 0.96 else (ma240 if current_price >= ma240 * 0.96 else ma60)
        cost_label = "長線防守底線"
        buy_high = round(main_cost * 1.03, 1)

        ACTION_WAIT, ACTION_NO, ACTION_YES, ACTION_HOLD = "⏳ 【耐心觀望】", "❌ 【極度危險】", "✅ 【果斷買進】", "🛡️ 【保護持股】"
        signal_text, color_border, signal_bg = "", "", ""
        is_action_needed, is_golden_signal = False, False
        tactical_summary = "區間震盪，主力籌碼未明，在旁看戲即可。"

        if is_fake_breakout: 
            signal_text, color_border, signal_bg = f"{ACTION_NO} 🚨 主力誘多，請勿追高！", "#e74c3c", "#3a1515"
            is_action_needed = True; tactical_summary = "❌ 假突破訊號！千萬別追，有庫存快跑！"
        elif entry_price > 0 and roi_pct <= -10.0: 
            signal_text, color_border, signal_bg = f"{ACTION_NO} 觸發 10% 停損結界！", "#e74c3c", "#3a1515"
            is_action_needed = True; tactical_summary = "🩸 虧損已達 10% 底線，嚴格執行紀律停損！"
        elif sell_cond_count >= 2 and roi_pct > 0: 
            signal_text, color_border, signal_bg = f"{ACTION_HOLD} 危險訊號，分批停利。", "#f1c40f", "#3a3015"
            is_action_needed = True; tactical_summary = "🟡 技術面已現敗象，請分批停利入袋。"
        elif sell_cond_count >= 2 and roi_pct <= 0: 
            signal_text, color_border, signal_bg = f"{ACTION_NO} 短線轉空，認賠殺出。", "#e74c3c", "#3a1515"
            is_action_needed = True; tactical_summary = "❌ 股價破線且爆量，直接認賠換股。"
        elif is_macro_panic_global: 
            if current_price <= buy_high: 
                signal_text, color_border, signal_bg = f"{ACTION_YES} 斷頭潮！左側重壓！", "#00FF00", "#153a20"; is_golden_signal = True
                tactical_summary = "✅ 大盤恐慌下殺，此標的已超跌，適合左側買進！"
            else: 
                signal_text, color_border, signal_bg = f"{ACTION_WAIT} 等賣壓打下來再撿！", "#f39c12", "#3a3015"
                tactical_summary = "⏳ 股價尚未殺入安全區，請等待恐慌發酵。"
        elif is_first_red:
            signal_text, color_border, signal_bg = f"{ACTION_YES} ✨ 破繭第一根！強勢起漲！", "#00FF00", "#153a20"; is_golden_signal = True
            tactical_summary = "✨ 底部爆量突破！起漲第一根，大膽切入並設好停損！"
        elif is_ma_bullish:
            signal_text, color_border, signal_bg = f"{ACTION_YES} 突破或多頭確立！", "#00FF00", "#153a20"; is_golden_signal = True
            tactical_summary = "✅ 動能點火，符合右側進場標準！"
        else:
            if current_price > (buy_high * 1.05):
                signal_text, color_border, signal_bg = f"{ACTION_WAIT} 偏離防守區，等拉回！", "#f39c12", "#3a3015"
                tactical_summary = "⏳ 動能雖強但追高風險極大，請等量縮拉回。"
            else: 
                signal_text, color_border, signal_bg = f"{ACTION_HOLD} 區間震盪，輕鬆看戲。", "#ccc", "#2b2b36"

        ai_tags = []
        if is_fake_breakout: ai_tags.append("🚨 假突破")
        if is_first_red: ai_tags.append("✨ 起漲第一根")
        if is_break_ma5: ai_tags.append("🟢 破 5MA")
        if current_price < ma20: ai_tags.append("🟢 破月線")
        if vol_ratio >= 2.5: ai_tags.append("🔴 爆量攻擊")
        if is_ma_bullish: ai_tags.append("🔴 均線多頭")

        return {
            "name": stock_name, "code": symbol, "price": current_price, "gain": gain, "cost_label": cost_label,
            "signal": signal_text, "color": color_border, "signal_bg": signal_bg, "ai_tags": ai_tags, 
            "is_golden": is_golden_signal, "is_action_needed": is_action_needed, "tactical_summary": tactical_summary,
            "cost": round(main_cost,1)
        }
    except: return None

def calc_real_profit(cost, price, qty):
    if cost <= 0: return 0, 0
    buy_val = cost * qty * 1000
    sell_val = price * qty * 1000
    profit = sell_val - buy_val - max(20, int(buy_val * 0.001425)) - max(20, int(sell_val * 0.001425)) - int(sell_val * 0.003)
    return profit, (profit/buy_val)*100 if buy_val > 0 else 0

# ==========================================
# 🖥️ 側邊欄控制台 (防當機批次掃描引擎)
# ==========================================
with st.sidebar:
    st.markdown("<h2 style='color:#f1c40f; text-align:center;'>⚙️ 戰術控制台</h2>", unsafe_allow_html=True)
    st.markdown("---")
    st.markdown("<h4 style='color:#00FF00;'>🚀 全境零死鎖掃描雷達</h4>", unsafe_allow_html=True)
    st.markdown("<p style='color:#aaa; font-size:12px;'>⚠️ 採用最高級無重啟批次技術，掃描 100% 絕不轉圈圈！</p>", unsafe_allow_html=True)
    
    scan_scope = st.selectbox("🎯 選擇掃描範圍", [
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

    # V27.0 無重啟大數據批次過濾 (1次請求抵100次，絕對不卡)
    def run_safe_scan_v27(mode, scope, current_panic):
        results = []
        target_codes = get_target_codes(scope)
        chunk_size = 100
        chunks = [target_codes[i:i+chunk_size] for i in range(0, len(target_codes), chunk_size)]
        
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        for idx, chunk in enumerate(chunks):
            status_text.text(f"📡 高速過濾中: 批次 {idx+1}/{len(chunks)} ...")
            try:
                # 批次打包，threads=False 絕對不當機
                chunk_res = batch_download_histories(chunk)
                for c, h in chunk_res.items():
                    d = calculate_signals_from_df(c, h, None, current_panic)
                    if d and "❌" not in d.get('signal', '') and d.get('price', 0) > 0:
                        if mode == "golden" and d.get('is_golden'): results.append(d)
                        elif mode == "first_red" and d.get('is_first_red'): results.append(d)
            except: pass
            progress_bar.progress(min((idx+1)/len(chunks), 1.0))
            
        progress_bar.empty()
        status_text.text(f"✅ 掃描完畢！共篩選出 {len(results)} 檔。")
        return results

    def do_scan_golden():
        st.session_state.scan_results = run_safe_scan_v27("golden", scan_scope, is_panic)
        st.session_state.scan_mode = "golden"
        
    def do_scan_red():
        st.session_state.scan_results = run_safe_scan_v27("first_red", scan_scope, is_panic)
        st.session_state.scan_mode = "first_red"

    st.button("🚀 黃金起漲與魚身", use_container_width=True, on_click=do_scan_golden)
    st.button("✨ 破繭第一根專區", use_container_width=True, on_click=do_scan_red)

    st.markdown("---")
    st.markdown("<h4 style='color:#e056fd;'>🔥 焦點戰役 (選股靈感)</h4>", unsafe_allow_html=True)
    st.button("📥 載入今日熱門戰役", use_container_width=True, on_click=cb_load_hot_themes)

# ==========================================
# 🖥️ 主戰情室畫面渲染 (零延遲加載)
# ==========================================
col_nav1, col_nav2, col_nav3 = st.columns([5, 1, 1])
with col_nav1: st.markdown("<h1 style='color:#FFB300; margin: 0;'>54088 戰情室 V27.0</h1>", unsafe_allow_html=True)
with col_nav2:
    st.markdown("<div class='sync-btn'>", unsafe_allow_html=True)
    st.button("🔄 刷新", use_container_width=True, on_click=cb_ui_sync) 
    st.markdown("</div>", unsafe_allow_html=True)
with col_nav3:
    st.markdown("<div class='lock-btn'>", unsafe_allow_html=True)
    st.button("🔒 鎖定", use_container_width=True, on_click=cb_ui_logout)
    st.markdown("</div>", unsafe_allow_html=True)

st.markdown(f"<div style='text-align:right; color:#888; font-size:12px; margin-bottom:10px;'>系統狀態：防當機靜態引擎 | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</div>", unsafe_allow_html=True)

# 渲染卡片：資料已在前面 batch_download 抓好，這裡瞬間運算
port_loaded_cards, pin_loaded_cards = {}, {}

for code, p in st.session_state.portfolio.items():
    if code in dashboard_histories:
        d = calculate_signals_from_df(code, dashboard_histories[code], portfolio_data=p, is_panic_global=is_panic)
        if d: port_loaded_cards[code] = d

for code, p in st.session_state.pinned_stocks.items():
    if code in dashboard_histories:
        d = calculate_signals_from_df(code, dashboard_histories[code], is_panic_global=is_panic)
        if d: pin_loaded_cards[code] = d

total_unrealized, action_needed, golden_targets = 0, 0, 0
for code, d in port_loaded_cards.items():
    p_profit, _ = calc_real_profit(st.session_state.portfolio[code]['entry_price'], d['price'], st.session_state.portfolio[code]['qty'])
    total_unrealized += p_profit
    if d.get('is_action_needed'): action_needed += 1
for code, d in pin_loaded_cards.items():
    if d.get('is_golden'): golden_targets += 1

market_suggestion = "🩸 【斷頭潮來臨】大盤恐慌崩跌！切換「左側價值」重壓便宜股！" if is_panic else ("💡 【多頭順風】大盤健康 ➡️ 適合【🚀 右側動能狙擊】" if is_bull_market else "💡 【空頭震盪】大盤不穩 ➡️ 適合【🛡️ 左側防禦佈局】")

st.markdown(f"""
<div class='hud-box'>
<div class='hud-title' style='display:flex; justify-content:space-between;'><span>🌐 大將軍戰情總覽 (HUD)</span><span style='color:{weather_color};'>{weather_str}</span></div>
<div style='background:#1a1c23; padding:10px; border-radius:5px; border-left:3px solid {weather_color}; margin-bottom:10px; font-size:14px; color:#ddd;'>
<strong>🌅 今日戰情速報：</strong>大盤目前判定為 {weather_str.split(' ')[1] if ' ' in weather_str else weather_str}。 {market_suggestion}
</div>
<div class='hud-metric'><span style='color:#aaa;'>庫存 / 雷達數量</span> <strong style='color:#fff;'>{len(port_loaded_cards)} / {len(pin_loaded_cards)} 檔</strong></div>
<div class='hud-metric'><span style='color:#aaa;'>總未實現損益</span> <strong style='color:{'#ff4d4d' if total_unrealized>0 else '#00FF00'}; font-size:18px;'>{total_unrealized:+,.0f} 元</strong></div>
<div class='health-bar-bg'><div class='{'health-bar-fill-green' if total_unrealized >= 0 else 'health-bar-fill-red'}' style='width: {max(0, min(100, 50 + (total_unrealized / 50000) * 50))}%;'></div></div>
<div class='hud-metric' style='margin-top:10px; padding-top:10px; border-top:1px dashed #333;'><span style='color:#2ecc71;'>🎯 雷達可狙擊目標：<strong>{golden_targets} 檔</strong></span><span style='color:#e74c3c;'>🚨 庫存強迫撤退：<strong>{action_needed} 檔</strong></span></div>
</div>
""", unsafe_allow_html=True)

def draw_v27_card(d, ui_key_prefix, is_portfolio=False, p_data=None):
    if not d: return
    try:
        gain_color, gain_bg = ('#ff4d4d', '#3a1515') if d.get('gain',0)>0 else (('#00FF00', '#153a20') if d.get('gain',0)<0 else ('#aaaaaa', '#333333'))
        ai_tags_html = "".join([f"<span class='{'danger-badge' if '🚨' in tag or '🔴' in tag or '❌' in tag else 'special-badge'}'>{tag}</span>" for tag in d.get('ai_tags', [])])
        port_html = ""
        if is_portfolio and p_data:
            port_html = f"<div style='background:#10141d; padding:10px; border-radius:6px; margin-bottom:12px;'><div style='display:flex; justify-content:space-between;'><span style='background-color:{'#3498db' if p_data.get('mode') == '長線價值波段單' else '#e67e22'}; color:#fff; font-size:12px; padding:2px 8px; border-radius:4px;'>🎮 {p_data.get('mode', '')}</span><span style='color:#aaa; font-size:12px;'>🎯 目標價：<strong style='color:#f1c40f;'>{p_data.get('manual_target', 0.0):.1f}</strong></span></div></div>"
        
        summary_class = "tactical-danger" if d.get('is_action_needed') else "tactical-summary"

        st.markdown(f"""
        <div style="border: 2px solid {d.get('color', '#444')}; border-radius: 8px; padding: 15px; background-color: #16191f; margin-bottom: 5px;">
        {port_html}
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:5px;">
            <span style="font-weight:bold; font-size:18px;">{d.get('name', '未知')} ({d.get('code', '未知')})</span>
            <span style="color:#888; font-size:12px;">🛡️ 防守: {d.get('cost_label', '')}</span>
        </div>
        <div style="font-size:32px; font-weight:bold; margin-bottom: 10px; display:flex; gap:12px;">{d.get('price', 0.0):.2f} <span style="font-size:16px; color:{gain_color}; background-color:{gain_bg}; padding:4px 10px; border-radius:6px;">{d.get('gain', 0.0):+.1f}%</span></div>
        <div style="margin-bottom: 5px;">{ai_tags_html}</div>
        <div style="background:{d.get('signal_bg', '#111')}; padding:10px; border-radius:6px; text-align:center; margin-bottom:10px; border: 1px solid {d.get('color', '#444')}40;"><strong style="color:{d.get('color', '#fff')}; font-size:18px;">{d.get('signal', '')}</strong></div>
        <div class="{summary_class}">📝 指揮官戰術小結：<br>{d.get('tactical_summary', '')}</div>
        </div>""", unsafe_allow_html=True)

        if not is_portfolio:
            if d.get('code') not in st.session_state.pinned_stocks and d.get('code') not in st.session_state.portfolio:
                st.button(f"📌 加入觀測雷達", key=f"pin_{ui_key_prefix}_{d.get('code')}", use_container_width=True, on_click=cb_pin_stock, args=(d.get('code'), f"{d.get('code')}:?:0:?:?:0", 'search'))
    except: pass

if search_code and search_code in dashboard_histories:
    d = calculate_signals_from_df(search_code, dashboard_histories[search_code], is_panic_global=is_panic)
    draw_v27_card(d, "search")

if st.session_state.temp_intel:
    st.markdown("<h3 style='color:#00d2ff; margin-top:20px; border-bottom: 2px solid #00d2ff; padding-bottom:5px;'>👁️ 焦點戰役觀測區 (未鎖定)</h3>", unsafe_allow_html=True)
    cols = st.columns(2)
    for i, item in enumerate(st.session_state.temp_intel):
        code = item.get('code')
        if code in dashboard_histories:
            d = calculate_signals_from_df(code, dashboard_histories[code], is_panic_global=is_panic)
            if d:
                with cols[i % 2]: draw_v27_card(d, f"temp_{i}")

if st.session_state.portfolio:
    st.markdown(f"<h2 style='color:#ff4d4d; margin-top:20px; border-bottom: 2px solid #ff4d4d; padding-bottom:5px;'>💼 總指揮的作戰庫存</h2>", unsafe_allow_html=True)
    cols = st.columns(2)
    for i, (code, p_data) in enumerate(list(st.session_state.portfolio.items())):
        d = port_loaded_cards.get(code)
        if d:
            with cols[i % 2]:
                p_profit, p_roi = calc_real_profit(p_data.get('entry_price', 0), d.get('price', 0), p_data.get('qty', 0))
                is_hard_stop = d.get('is_action_needed', False) and d.get('gain', 0) < 0
                st.markdown(f"""<div style="border: 4px solid {'#e74c3c' if is_hard_stop else '#00FF00'}; border-radius: 8px; padding: 15px; background-color: #1a1a24; margin-bottom: 5px;"><div style="font-weight:bold; font-size:18px;">{d.get('name')} ({d.get('code')})</div><div style="font-size:24px; font-weight:bold; color:{'#e74c3c' if p_profit<0 else '#ff4d4d'};">{p_profit:+,.0f} 元 ({p_roi:+.1f}%)</div></div>""", unsafe_allow_html=True)
                draw_v27_card(d, f"port_{code}", is_portfolio=True, p_data=p_data)
                st.button(f"🚪 賣出清空", key=f"sell_{code}", use_container_width=True, on_click=cb_sell_stock, args=(code,))

if st.session_state.pinned_stocks:
    st.markdown(f"<h2 style='color:#f1c40f; margin-top:20px; border-bottom: 2px solid #f1c40f; padding-bottom:5px;'>⭐ 觀測雷達</h2>", unsafe_allow_html=True)
    cols = st.columns(2)
    for i, (code, p_data) in enumerate(list(st.session_state.pinned_stocks.items())):
        d = pin_loaded_cards.get(code)
        if d:
            with cols[i % 2]:
                draw_v27_card(d, f"pin_{code}")
                st.button(f"❌ 刪除雷達", key=f"unpin_{code}", use_container_width=True, on_click=cb_unpin_stock, args=(code,))

if scan_mode_current := st.session_state.get('scan_mode', ""):
    st.markdown(f"<h2 style='color:#00FF00; margin-top:30px; border-bottom: 2px solid #00FF00; padding-bottom:5px;'>⚡ 掃描篩選結果</h2>", unsafe_allow_html=True)
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
            with cols[i % 2]: draw_v27_card(d, f"scan_res_{i}")
