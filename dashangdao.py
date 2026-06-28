import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime
import re
import time
import json
import os
import requests

# ==========================================
# 🛡️ 基礎配置與狀態初始化
# ==========================================
st.set_page_config(layout="wide", page_title="54088 - 戰情室 V42.0", initial_sidebar_state="expanded")

COMMANDER_PIN = "0826"
USER_DB_FILE = "54088_database.json" 

if 'scan_results' not in st.session_state: st.session_state.scan_results = []
if 'scan_mode' not in st.session_state: st.session_state.scan_mode = ""
if 'temp_intel' not in st.session_state: st.session_state.temp_intel = []
if 'portfolio' not in st.session_state: st.session_state.portfolio = {}
if 'pinned_stocks' not in st.session_state: st.session_state.pinned_stocks = {}

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
    try:
        with open(USER_DB_FILE, "w", encoding="utf-8") as f: 
            json.dump({"pinned_stocks": st.session_state.pinned_stocks, "portfolio": st.session_state.portfolio}, f, ensure_ascii=False, indent=4)
    except: pass

# ==========================================
# 🛡️ 身份驗證
# ==========================================
if 'authenticated' not in st.session_state: 
    st.session_state.authenticated = (st.query_params.get("auth") == "54088")

if not st.session_state.authenticated:
    st.markdown("<h2 style='text-align: center; color: #444; margin-top: 10vh; letter-spacing: 5px;'>SYSTEM LOCKED</h2>", unsafe_allow_html=True)
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        pwd = st.text_input("輸入授權密碼", type="password")
        if st.button("系統解鎖", use_container_width=True):
            if pwd == COMMANDER_PIN:
                st.session_state.authenticated = True
                st.rerun()
            else: st.error("❌ 密碼錯誤")
    st.stop()

# ==========================================
# 🎨 視覺與樣式定義 (台股色彩特化)
# ==========================================
st.markdown('''<style>
.stApp { background-color: #0b0c0f !important; color: #fff !important; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
div[data-testid="stSidebar"] { background-color: #12141a !important; border-right: 1px solid #333 !important; }
div[data-testid="stButton"] > button { background-color: #1e1e24 !important; border: 1px solid #444 !important; transition: all 0.2s ease-in-out; }
div[data-testid="stButton"] > button p { color: #ffffff !important; font-weight: bold !important; font-size: 15px !important; }
.scan-btn div[data-testid="stButton"] > button { background-color: #3a1515 !important; border: 2px solid #ff4d4d !important; margin-bottom: 5px;}
.scan-btn div[data-testid="stButton"] > button p { color: #ff4d4d !important; font-weight: bold !important; }
.hud-box { background: linear-gradient(135deg, #1a1c23 0%, #0d1117 100%); border-radius: 10px; padding: 15px; border-left: 5px solid #ff4d4d; box-shadow: 0 4px 15px rgba(0,0,0,0.5); margin-bottom: 20px;}
.hud-title { color: #f1c40f; font-size: 14px; font-weight: bold; margin-bottom: 10px; border-bottom: 1px solid #333; padding-bottom: 5px;}
.hud-metric { display: flex; justify-content: space-between; align-items: center; margin-bottom: 5px;}
.health-bar-bg { width: 100%; background-color: #333; border-radius: 5px; height: 8px; margin-top: 5px; overflow: hidden;}
.health-bar-fill-red { height: 100%; background-color: #ff4d4d; transition: width 0.5s ease;}
.health-bar-fill-green { height: 100%; background-color: #00FF00; transition: width 0.5s ease;}
.tag-red { background: #3a1515; padding: 4px 8px; border-radius: 4px; font-size: 13px; color: #ff4d4d; border: 1px solid #e74c3c; display: inline-block; margin: 0 5px 5px 0; font-weight: bold; }
.tag-green { background: #153a20; padding: 4px 8px; border-radius: 4px; font-size: 13px; color: #00FF00; border: 1px solid #2ecc71; display: inline-block; margin: 0 5px 5px 0; font-weight: bold; }
.tag-gray { background: #222; padding: 4px 8px; border-radius: 4px; font-size: 13px; color: #aaa; border: 1px solid #555; display: inline-block; margin: 0 5px 5px 0; font-weight: bold; }
.tactical-summary { background: #000; border-top: 1px dashed #444; margin-top: 10px; padding: 10px; font-size: 14px; color: #f1c40f; font-weight: bold; border-radius: 5px;}
.tactical-danger { background: #153a20; border-top: 1px dashed #2ecc71; margin-top: 10px; padding: 10px; font-size: 15px; color: #00FF00; font-weight: bold; border-radius: 5px;}
.metric-grid { display: flex; gap: 15px; flex-wrap: wrap; font-size: 13px; color: #ccc; margin-bottom: 10px; background: #10141d; padding: 12px; border-radius: 6px; border: 1px solid #333;}
</style>''', unsafe_allow_html=True)

# ==========================================
# 📡 雙引擎資料獲取 (抗 N/A 機制 + 大盤正名)
# ==========================================
@st.cache_resource
def get_safe_session():
    session = requests.Session()
    session.request = lambda *args, **kwargs: requests.Session.request(session, *args, **{**kwargs, 'timeout': 5.0})
    session.headers.update({"User-Agent": "Mozilla/5.0"})
    return session

@st.cache_data(ttl=86400, show_spinner=False)
def fetch_stock_names():
    api_names = {}
    try:
        res = requests.get("https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockInfo", timeout=5)
        if res.status_code == 200:
            for item in res.json().get('data', []):
                code = str(item.get('stock_id', '')).strip()
                if len(code) == 4 and code.isdigit(): api_names[code] = item.get('stock_name', code)
    except: pass
    fallbacks = {"2330":"台積電", "2317":"鴻海", "2454":"聯發科", "2382":"廣達", "2603":"長榮", "1519":"華城", "3017":"奇鋐", "3324":"雙鴻", "2313":"華通"}
    for k, v in fallbacks.items():
        if k not in api_names: api_names[k] = v
    return api_names

@st.cache_data(ttl=86400, show_spinner=False)
def fetch_fundamentals():
    db = {}
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        res = requests.get("https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL", timeout=5, headers=headers)
        if res.status_code == 200:
            for item in res.json():
                code = str(item.get('Code', '')).strip()
                if len(code) == 4 and code.isdigit():
                    db[code] = {
                        'PE': float(str(item.get('PeRatio', '0')).replace(',', '')) if str(item.get('PeRatio', '0')).replace('.','',1).replace(',','').isdigit() else 0.0,
                        'Yield': float(str(item.get('DividendYield', '0')).replace(',', '')) if str(item.get('DividendYield', '0')).replace('.','',1).replace(',','').isdigit() else 0.0,
                        'PB': float(str(item.get('PbRatio', '0')).replace(',', '')) if str(item.get('PbRatio', '0')).replace('.','',1).replace(',','').isdigit() else 0.0
                    }
    except: pass
    try:
        res2 = requests.get("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_peratio_analysis", timeout=5, headers=headers)
        if res2.status_code == 200:
            for item in res2.json():
                code = str(item.get('SecuritiesCompanyCode', '')).strip()
                if len(code) == 4 and code.isdigit():
                    db[code] = {
                        'PE': float(str(item.get('PERatio', '0')).replace(',', '')) if str(item.get('PERatio', '0')).replace('.','',1).replace(',','').isdigit() else 0.0,
                        'Yield': float(str(item.get('YieldRatio', '0')).replace(',', '')) if str(item.get('YieldRatio', '0')).replace('.','',1).replace(',','').isdigit() else 0.0,
                        'PB': float(str(item.get('PBRatio', '0')).replace(',', '')) if str(item.get('PBRatio', '0')).replace('.','',1).replace(',','').isdigit() else 0.0
                    }
    except: pass
    return db

TW_STOCK_NAMES = fetch_stock_names()
FUNDAMENTAL_DB = fetch_fundamentals()
GLOBAL_MARKET_CODES = list(TW_STOCK_NAMES.keys())

# 縮短快取時間，確保抓到最新大盤
@st.cache_data(ttl=120, show_spinner=False)
def get_market_weather():
    try:
        session = get_safe_session()
        twii = yf.Ticker("^TWII", session=session).history(period="3mo").dropna(subset=['Close'])
        if twii.empty: twii = yf.Ticker("0050.TW", session=session).history(period="3mo").dropna(subset=['Close'])
        if twii.empty: return "大盤連線異常", "#888", False, False
        
        c_idx = float(twii['Close'].iloc[-1])
        ma20 = float(twii['Close'].rolling(20).mean().iloc[-1])
        gain = ((c_idx - float(twii['Close'].iloc[-2])) / float(twii['Close'].iloc[-2])) * 100
        is_panic = (gain <= -3.0) or (c_idx < float(twii['Close'].rolling(60).mean().iloc[-1]) * 0.95)
        
        display_str = f"加權指數: {c_idx:,.0f} 點"

        if is_panic: return f"🌩️ 恐慌斷頭潮 ({display_str})", "#00FF00", c_idx > ma20, True
        elif c_idx > ma20: return f"☀️ 多頭順風環境 ({display_str})", "#ff4d4d", True, False
        else: return f"☁️ 空頭震盪環境 ({display_str})", "#f1c40f", False, False
    except: return "📡 大盤資料獲取中...", "#888", False, False

weather_str, weather_color, is_bull_market, is_panic = get_market_weather()

def get_stock_data(symbol):
    session = get_safe_session()
    for ext in [".TW", ".TWO"]:
        try:
            tk = yf.Ticker(symbol + ext, session=session)
            hist = tk.history(period="1y").dropna(subset=['Close'])
            if not hist.empty and len(hist) > 15:
                info = tk.info
                pe = info.get('trailingPE', 0.0)
                pb = info.get('priceToBook', 0.0)
                yld = info.get('dividendYield', 0.0) * 100 if info.get('dividendYield') else 0.0
                return hist, pe, pb, yld
        except: pass
    return None

# ==========================================
# 🧠 戰略演算法核心 (全數據實戰派)
# ==========================================
# 定義標籤說明字典
TAG_DEFINITIONS = {
    "✨ 起漲第一根": "👉 底部爆量且突破季線，為絕佳右側買點。",
    "🚨 撤退警報": "👉 出現假突破(長上影線)、死叉或破線，主力可能出貨。",
    "🔴 均線多頭": "👉 5/20/60均線向上排列，趨勢穩健偏多。",
    "⚪ 量縮整理": "👉 近期量能低迷，股價無明確方向。"
}

def calculate_signals(symbol, data_tuple, portfolio_data=None, is_panic_global=False):
    if not data_tuple: return None
    hist_df, pe_yf, pb_yf, yld_yf = data_tuple
    if hist_df is None or hist_df.empty or len(hist_df) < 26: return None
    
    fund_info = FUNDAMENTAL_DB.get(symbol, {})
    pe = fund_info.get('PE', 0.0) if fund_info.get('PE', 0.0) > 0 else pe_yf
    pb = fund_info.get('PB', 0.0) if fund_info.get('PB', 0.0) > 0 else pb_yf
    yld = fund_info.get('Yield', 0.0) if fund_info.get('Yield', 0.0) > 0 else yld_yf

    score = 50
    if 0 < pe < 15: score += 20
    elif pe > 25: score -= 15
    if 0 < pb < 1.5: score += 20
    elif pb > 3.0: score -= 15
    if yld >= 5.0: score += 10
    score = max(0, min(100, score))

    if pe == 0.0 and pb == 0.0: 
        val_shield = "⚪ 無基本面"
        score = 0
    elif score >= 70: val_shield = "🔴 價值低估 (買)"
    elif score <= 40: val_shield = "🟢 估值過高 (險)"
    else: val_shield = "⚪ 估值適中"
    
    if yld >= 5.0: val_shield += " | 💰 高息"

    curr = float(hist_df['Close'].iloc[-1])
    prev = max(float(hist_df['Close'].iloc[-2]), 0.001)
    open_p = float(hist_df['Open'].iloc[-1])
    high_p = float(hist_df['High'].iloc[-1])
    gain = ((curr - prev) / prev) * 100
    
    vol = int(hist_df['Volume'].iloc[-1] / 1000)
    vol_5d = max(hist_df['Volume'].iloc[-6:-1].mean() / 1000, 0.01)
    vol_ratio = vol / vol_5d
    
    calc_df = hist_df.copy()
    ma5 = calc_df['Close'].rolling(min(5, len(calc_df))).mean().iloc[-1]
    ma20 = calc_df['Close'].rolling(min(20, len(calc_df))).mean().iloc[-1]
    ma60 = calc_df['Close'].rolling(min(60, len(calc_df))).mean().iloc[-1] if len(calc_df) >= 60 else ma20
    ma240 = calc_df['Close'].rolling(min(240, len(calc_df))).mean().iloc[-1] if len(calc_df) >= 240 else ma60

    low_min = calc_df['Low'].rolling(min(9, len(calc_df))).min()
    high_max = calc_df['High'].rolling(min(9, len(calc_df))).max()
    rsv = (calc_df['Close'] - low_min) / (high_max - low_min + 1e-9) * 100
    calc_df['K'] = rsv.bfill().ffill().fillna(50).ewm(com=2, adjust=False).mean()
    calc_df['D'] = calc_df['K'].bfill().ffill().fillna(50).ewm(com=2, adjust=False).mean()
    k, d_val = calc_df['K'].iloc[-1], calc_df['D'].iloc[-1]
    
    is_kdj_golden = (k < 50) and (calc_df['K'].iloc[-2] <= calc_df['D'].iloc[-2]) and (k > d_val)
    is_kdj_dead = (k > 70) and (calc_df['K'].iloc[-2] >= calc_df['D'].iloc[-2]) and (k < d_val)
    kdj_str = "金叉" if is_kdj_golden else ("死叉" if is_kdj_dead else ("向上" if k > d_val else "向下"))

    exp1 = calc_df['Close'].ewm(span=12, adjust=False).mean()
    exp2 = calc_df['Close'].ewm(span=26, adjust=False).mean()
    macd = exp1 - exp2
    macd_signal = macd.ewm(span=9, adjust=False).mean()
    macd_hist = macd - macd_signal
    is_macd_golden = (macd_hist.iloc[-2] <= 0) and (macd_hist.iloc[-1] > 0)
    is_macd_dead = (macd_hist.iloc[-2] >= 0) and (macd_hist.iloc[-1] < 0)
    macd_str = "金叉" if is_macd_golden else ("死叉" if is_macd_dead else ("紅柱" if macd_hist.iloc[-1] > 0 else "綠柱"))

    is_ma_bullish = (curr > ma5) and (ma5 > ma20) and (ma20 > ma60)
    is_vol_breakout = vol_ratio >= 2.0 and gain >= 2.0
    is_stealth = (curr > ma60) and (gain < 2.0) and (curr < ma60 * 1.1) and (vol_ratio >= 1.2)
    is_yield_def = (curr > ma240) and (curr < ma60 * 1.05) and (yld >= 5.0)
    
    body = abs(curr - open_p)
    upper_shadow = high_p - max(open_p, curr)
    is_shooting_star = (upper_shadow > body * 1.5) and (high_p > ma5)
    is_fake_breakout = (vol_ratio >= 2.0) and is_shooting_star
    is_break_ma5 = curr < ma5

    start_signals = []
    if is_kdj_golden: start_signals.append("KDJ金叉")
    if is_macd_golden: start_signals.append("MACD金叉")
    if is_vol_breakout: start_signals.append("爆量上攻")
    
    retreat_signals = []
    if is_fake_breakout: retreat_signals.append("假突破(避雷針)")
    if is_kdj_dead or is_macd_dead: retreat_signals.append("高檔死叉")
    if is_break_ma5: retreat_signals.append("跌破5日線")

    entry_price = float(portfolio_data.get('entry_price', 0.0)) if portfolio_data else 0.0
    roi_pct = ((curr - entry_price) / entry_price) * 100 if entry_price > 0 else 0.0
    
    if is_ma_bullish or start_signals:
        main_cost = ma5
        cost_label = "5日線防守"
        buy_zone = f"{round(main_cost * 0.98, 1)} ~ {round(main_cost * 1.02, 1)}"
        exit_price = round(main_cost * 0.96, 1)
    else:
        main_cost = ma60
        cost_label = "季線防守"
        buy_zone = f"{round(main_cost * 0.97, 1)} ~ {round(main_cost * 1.03, 1)}"
        exit_price = round(main_cost * 0.95, 1)

    signal_text, color_border, signal_bg = "⏳ 【耐心觀望】", "#888", "#2b2b36"
    tactical_summary = f"區間震盪。若無庫存，建議於 {buy_zone} 區間佈局，跌破 {exit_price} 停損。"
    is_action_needed = False
    is_golden_signal = False
    
    if entry_price > 0 and roi_pct <= -10.0:
        signal_text, color_border, signal_bg = "❌ 🩸 觸發 10% 停損結界！", "#00FF00", "#153a20"
        is_action_needed = True; tactical_summary = "🩸 虧損已達 10% 底線，嚴格執行紀律，立刻停損保護本金！"
    elif retreat_signals:
        signal_text, color_border, signal_bg = f"🚨 撤退警告：{', '.join(retreat_signals)}", "#00FF00", "#153a20"
        is_action_needed = True; tactical_summary = "🟢 技術面已現敗象，請評估分批停利或停損撤退。"
    elif is_panic_global and curr <= ma60 * 1.05:
        signal_text, color_border, signal_bg = "✅ 🩸 斷頭潮！左側重壓！", "#ff4d4d", "#3a1515"
        is_golden_signal = True; tactical_summary = f"🔴 大盤恐慌崩跌，標的已超跌。大膽於 {buy_zone} 承接！"
    elif start_signals:
        signal_text, color_border, signal_bg = f"🚀 起漲點火！", "#ff4d4d", "#3a1515"
        is_golden_signal = True; tactical_summary = f"🔴 底部爆量或指標金叉！符合右側進場標準，建議進場區間 {buy_zone}。"
    elif is_ma_bullish:
        signal_text, color_border, signal_bg = "📈 多頭確立！", "#ff4d4d", "#3a1515"
        is_golden_signal = True; tactical_summary = f"🔴 均線多頭排列。符合短線切入標準，強勢進場區間為 {buy_zone}。"
        
    ai_tags = []
    if start_signals: ai_tags.append("✨ 起漲第一根")
    if retreat_signals: ai_tags.append("🚨 撤退警報")
    if is_ma_bullish: ai_tags.append("🔴 均線多頭")
    if not ai_tags: ai_tags.append("⚪ 量縮整理")

    return {
        "name": TW_STOCK_NAMES.get(symbol, symbol), "code": symbol, "price": curr, "gain": gain,
        "cost": round(main_cost, 1), "cost_label": cost_label, "signal": signal_text, "color": color_border, 
        "signal_bg": signal_bg, "ai_tags": ai_tags, "tactical_summary": tactical_summary,
        "buy_zone": buy_zone, "exit_price": exit_price, 
        "start_signals": "無" if not start_signals else ", ".join(start_signals),
        "retreat_signals": "無" if not retreat_signals else ", ".join(retreat_signals),
        "kdj_str": kdj_str, "macd_str": macd_str, "vol_ratio": vol_ratio, "val_score": score,
        "val_shield": val_shield, "pe": round(pe,1) if pe>0 else "N/A", "pb": round(pb,2) if pb>0 else "N/A", "yld": round(yld,1) if yld>0 else "N/A",
        "is_golden": is_golden_signal, "is_first_red": bool(start_signals), 
        "is_stealth": is_stealth, "is_yield": is_yield_def, "is_action_needed": is_action_needed
    }

def calc_real_profit(cost, price, qty):
    if cost <= 0: return 0, 0
    buy_val = cost * qty * 1000
    sell_val = price * qty * 1000
    profit = sell_val - buy_val - max(20, int(buy_val * 0.001425)) - max(20, int(sell_val * 0.001425)) - int(sell_val * 0.003)
    return profit, (profit/buy_val)*100 if buy_val > 0 else 0

# ==========================================
# 🖥️ 高階卡片渲染模組 (✅ AI 標籤詳細說明強制顯示)
# ==========================================
def draw_card(d, ui_key_prefix, is_portfolio=False, p_data=None):
    if not d: return
    gain_color = '#ff4d4d' if d['gain'] > 0 else ('#00FF00' if d['gain'] < 0 else '#aaaaaa')
    gain_bg = '#3a1515' if d['gain'] > 0 else ('#153a20' if d['gain'] < 0 else '#333333')
    
    tags_html = ""
    tag_explanations = []
    for tag in d.get('ai_tags', []):
        if '起漲' in tag or '多頭' in tag: 
            tags_html += f"<span class='tag-red'>{tag}</span>"
            tag_explanations.append(TAG_DEFINITIONS.get(tag, ""))
        elif '撤退' in tag or '警報' in tag: 
            tags_html += f"<span class='tag-green'>{tag}</span>"
            tag_explanations.append(TAG_DEFINITIONS.get(tag, ""))
        else: 
            tags_html += f"<span class='tag-gray'>{tag}</span>"
            tag_explanations.append(TAG_DEFINITIONS.get(tag, ""))
            
    # ✅ 將標籤說明組合成文字
    tag_exp_str = "<br>".join([x for x in tag_explanations if x])
    if tag_exp_str: tag_exp_str = f"<div style='margin-top:10px; font-size:13px; color:#aaa; border-top:1px dashed #444; padding-top:5px;'>{tag_exp_str}</div>"
    
    port_html = ""
    if is_portfolio and p_data:
        port_html = f"<div style='background:#10141d; padding:10px; border-radius:6px; margin-bottom:12px;'><span style='color:#aaa; font-size:13px;'>🎯 進場價：<strong style='color:#f1c40f;'>{p_data['entry_price']}</strong> | 📦 數量：{p_data['qty']} 張</span></div>"
    
    metric_grid = f"""
    <div class='metric-grid'>
        <div style="width:100%; margin-bottom:6px;">🛡️ 價值分數: <strong style="color:#00d2ff; font-size:15px;">{d['val_score']} 分</strong> <span style="color:#888;">({d['val_shield']} | PE:{d['pe']} PB:{d['pb']} 殖利率:{d['yld']}%)</span></div>
        <div style="width:100%; border-top: 1px dashed #444; margin-bottom:6px;"></div>
        <span>🎯 建議區間: <strong style="color:#f1c40f;">{d['buy_zone']}</strong></span>
        <span>🛑 停損防線: <strong style="color:#00FF00;">{d['exit_price']}</strong></span>
        <span>🚀 攻擊訊號: <strong style="color:#ff4d4d;">{d['start_signals']}</strong></span>
        <span>🚨 撤退風險: <strong style="color:#00FF00;">{d['retreat_signals']}</strong></span>
        <span>🌊 KDJ/MACD: <strong style="color:#00d2ff;">{d['kdj_str']} / {d['macd_str']}</strong></span>
        <span>📊 爆量比: <strong style="color:#e67e22;">{d['vol_ratio']:.1f}x</strong></span>
    </div>
    """
    
    summary_class = "tactical-danger" if d['is_action_needed'] else "tactical-summary"

    st.markdown(f"""
    <div style="border: 2px solid {d['color']}; border-radius: 8px; padding: 15px; background-color: #16191f; margin-bottom: 5px;">
    {port_html}
    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:5px;">
        <span style="font-weight:bold; font-size:18px;">{d['name']} ({d['code']})</span>
        <span style="color:#888; font-size:12px;">🛡️ {d['cost_label']}: {d['cost']}</span>
    </div>
    <div style="font-size:32px; font-weight:bold; margin-bottom: 10px; display:flex; gap:12px;">{d['price']:.2f} <span style="font-size:16px; color:{gain_color}; background-color:{gain_bg}; padding:4px 10px; border-radius:6px;">{d['gain']:+.1f}%</span></div>
    <div style="margin-bottom: 10px;">{tags_html}</div>
    {metric_grid}
    <div style="background:{d['signal_bg']}; padding:10px; border-radius:6px; text-align:center; margin-bottom:10px; border: 1px solid {d['color']}40;"><strong style="color:{d['color']}; font-size:16px;">{d['signal']}</strong></div>
    <div class="{summary_class}">
        📝 指揮官戰術：<br>{d['tactical_summary']}
        {tag_exp_str}
    </div>
    </div>""", unsafe_allow_html=True)

# ==========================================
# ⚙️ 側邊欄控制台
# ==========================================
with st.sidebar:
    st.markdown("<h2 style='color:#f1c40f; text-align:center;'>⚙️ 戰術控制台</h2>", unsafe_allow_html=True)
    
    if len(GLOBAL_MARKET_CODES) < 100:
        st.error("⚠️ 股市資料庫連線中，請稍等片刻。")

    st.markdown("<div style='background:#16191f; padding:10px; border-radius:8px; border: 1px solid #3498db; margin-bottom:10px;'><h4 style='color:#3498db; margin-top:0px; font-size:14px;'>📡 智能情報匯入</h4>", unsafe_allow_html=True)
    with st.form(key='intel_form', clear_on_submit=True): 
        intel_input = st.text_area("貼上密碼 (支援任何夾雜代碼的文字)：", placeholder="例如: 2313:?:?:1:?")
        # ✅ 修正指令匯入按鈕：確保在 form 內能正確觸發
        submit_intel = st.form_submit_button('📥 匯入預覽')
        if submit_intel and intel_input:
            # 暴力抓取任何 4 位數字
            found_codes = set(re.findall(r'\b\d{4}\b', intel_input))
            if found_codes:
                st.session_state.temp_intel = []
                for c in found_codes:
                    if c in TW_STOCK_NAMES: 
                        st.session_state.temp_intel.append({'code': c})
                st.rerun()
            else:
                st.error("未偵測到有效股票代碼")
    st.markdown("</div>", unsafe_allow_html=True)

    st.markdown("---")
    st.markdown("<h4 style='color:#ff4d4d;'>🚀 全境安全掃描雷達</h4>", unsafe_allow_html=True)
    scan_scope = st.selectbox("🎯 選擇掃描範圍", ["💻 電子/半導體/光電", "🌐 全市場 1700+ 檔", "🏗️ 傳產/機電/重電", "🚢 航運/觀光百貨", "🏦 金融/保險", "🧬 生技/醫療"])

    def get_scope_codes(scope):
        if "全市場" in scope: return GLOBAL_MARKET_CODES
        elif "電子" in scope: return [c for c in GLOBAL_MARKET_CODES if c.startswith(('23','24','30','31','32','33','34','35','36','49','52','53','54','61','62','64','80','81','82'))]
        elif "傳產" in scope: return [c for c in GLOBAL_MARKET_CODES if c.startswith(('11','12','13','14','15','16','17','18','19','20','21','22','99'))]
        elif "航運" in scope: return [c for c in GLOBAL_MARKET_CODES if c.startswith(('26','27'))]
        elif "金融" in scope: return [c for c in GLOBAL_MARKET_CODES if c.startswith(('28','58'))]
        elif "生技" in scope: return [c for c in GLOBAL_MARKET_CODES if c.startswith(('17','41','47','65'))]
        return GLOBAL_MARKET_CODES

    def run_safe_scan(mode, scope):
        results = []
        codes = get_scope_codes(scope)
        bar = st.progress(0)
        status = st.empty()
        for i, c in enumerate(codes):
            if i % 3 == 0: status.text(f"📡 過濾中... ({i}/{len(codes)})")
            d = calculate_signals(c, get_stock_data(c), is_panic_global=is_panic)
            if d and "❌" not in d['signal']:
                if mode == "golden" and d['is_golden']: results.append(d)
                elif mode == "first_red" and d['is_first_red']: results.append(d)
                elif mode == "stealth" and d['is_stealth']: results.append(d)
                elif mode == "yield" and d['is_yield']: results.append(d)
            bar.progress(min((i + 1) / len(codes), 1.0))
            time.sleep(0.01) # 絕對防卡死
        bar.empty(); status.empty()
        return results

    st.markdown("<div class='scan-btn'>", unsafe_allow_html=True)
    if st.button("🚀 黃金起漲與魚身", use_container_width=True):
        st.session_state.scan_results = run_safe_scan("golden", scan_scope)
        st.session_state.scan_mode = "golden"
    if st.button("✨ 破繭第一根專區", use_container_width=True):
        st.session_state.scan_results = run_safe_scan("first_red", scan_scope)
        st.session_state.scan_mode = "first_red"
    if st.button("🕵️‍♂️ 魚頭潛伏與轉機", use_container_width=True):
        st.session_state.scan_results = run_safe_scan("stealth", scan_scope)
        st.session_state.scan_mode = "stealth"
    if st.button("🛡️ 總經防禦高息池", use_container_width=True):
        st.session_state.scan_results = run_safe_scan("yield", scan_scope)
        st.session_state.scan_mode = "yield"
    st.markdown("</div>", unsafe_allow_html=True)

# ==========================================
# 🖥️ 主戰情室畫面渲染
# ==========================================
col_nav1, col_nav2, col_nav3 = st.columns([5, 1, 1])
with col_nav1: st.markdown("<h1 style='color:#FFB300; margin: 0;'>54088 戰情室 V42.0 (破壁實戰版)</h1>", unsafe_allow_html=True)
with col_nav2:
    if st.button("🔄 刷新", use_container_width=True): st.rerun()
with col_nav3:
    if st.button("🔒 鎖定", use_container_width=True):
        st.session_state.authenticated = False
        st.rerun()

# HUD 數值計算
port_loaded_cards, pin_loaded_cards = {}, {}
for code, p in st.session_state.portfolio.items():
    d = calculate_signals(code, get_stock_data(code), portfolio_data=p, is_panic_global=is_panic)
    if d: port_loaded_cards[code] = d
for code in st.session_state.pinned_stocks:
    d = calculate_signals(code, get_stock_data(code), portfolio_data=None, is_panic_global=is_panic)
    if d: pin_loaded_cards[code] = d

total_unrealized, action_needed, golden_targets = 0, 0, 0
for code, d in port_loaded_cards.items():
    p_profit, _ = calc_real_profit(st.session_state.portfolio[code]['entry_price'], d['price'], st.session_state.portfolio[code]['qty'])
    total_unrealized += p_profit
    if d.get('is_action_needed'): action_needed += 1
for code, d in pin_loaded_cards.items():
    if d.get('is_golden'): golden_targets += 1

market_suggestion = "🩸 【斷頭潮來臨】切換「左側價值」重壓便宜股！" if is_panic else ("💡 【多頭順風】大盤健康 ➡️ 適合右側動能狙擊" if is_bull_market else "💡 【空頭震盪】大盤不穩 ➡️ 適合左側防禦佈局")

st.markdown(f"""
<div class='hud-box'>
<div class='hud-title' style='display:flex; justify-content:space-between;'><span>🌐 大將軍戰情總覽 (HUD)</span><span style='color:{weather_color};'>{weather_str}</span></div>
<div style='background:#1a1c23; padding:10px; border-radius:5px; border-left:3px solid {weather_color}; margin-bottom:10px; font-size:14px; color:#ddd;'>
<strong>🌅 今日戰情速報：</strong> {market_suggestion}
</div>
<div class='hud-metric'><span style='color:#aaa;'>庫存 / 雷達數量</span> <strong style='color:#fff;'>{len(port_loaded_cards)} / {len(pin_loaded_cards)} 檔</strong></div>
<div class='hud-metric'><span style='color:#aaa;'>總未實現損益</span> <strong style='color:{'#ff4d4d' if total_unrealized>0 else '#00FF00'}; font-size:18px;'>{total_unrealized:+,.0f} 元</strong></div>
<div class='health-bar-bg'><div class='{'health-bar-fill-red' if total_unrealized >= 0 else 'health-bar-fill-green'}' style='width: {max(0, min(100, 50 + (total_unrealized / 50000) * 50))}%;'></div></div>
<div class='hud-metric' style='margin-top:10px; padding-top:10px; border-top:1px dashed #333;'><span style='color:#ff4d4d;'>🎯 雷達可狙擊：<strong>{golden_targets} 檔</strong></span><span style='color:#00FF00;'>🚨 庫存需撤退：<strong>{action_needed} 檔</strong></span></div>
</div>
""", unsafe_allow_html=True)

# 📝 手動搜尋標的
st.markdown("<h3 style='color:#3498db; margin-top:20px; border-bottom: 2px solid #3498db; padding-bottom:5px;'>🔍 手動搜尋雷達</h3>", unsafe_allow_html=True)
search_query = st.text_input("輸入股票代號 (如 '2330' 或 '台積電') ：")
if search_query:
    raw_input = search_query.strip()
    match_digits = re.search(r'\d{4,}', raw_input)
    clean_code = match_digits.group() if match_digits else None
    if not clean_code:
        for name, code in TW_STOCK_NAMES.items():
            if raw_input in name: clean_code = code; break
    if clean_code:
        d = calculate_signals(clean_code, get_stock_data(clean_code), portfolio_data=None, is_panic_global=is_panic)
        if d:
            draw_card(d, "search")
            if st.button("📌 加入觀測雷達", key="pin_search"):
                st.session_state.pinned_stocks[d['code']] = {}
                save_db(); st.rerun()
        else: st.error("❌ 查無報價。可能是下市股票或輸入錯誤。")

# 焦點戰役區
if st.session_state.temp_intel:
    st.markdown("<h3 style='color:#00d2ff; margin-top:20px; border-bottom: 2px solid #00d2ff; padding-bottom:5px;'>👁️ 情報觀測區</h3>", unsafe_allow_html=True)
    cols = st.columns(2)
    for i, item in enumerate(st.session_state.temp_intel):
        code = item['code']
        d = calculate_signals(code, get_stock_data(code), portfolio_data=None, is_panic_global=is_panic)
        if d:
            with cols[i % 2]: 
                draw_card(d, f"temp_{code}")
                if st.button("📌 鎖定至雷達", key=f"pin_temp_{code}"):
                    st.session_state.pinned_stocks[code] = {}
                    save_db(); st.rerun()

# 庫存區
if st.session_state.portfolio:
    st.markdown("<h2 style='color:#ff4d4d; margin-top:20px; border-bottom: 2px solid #ff4d4d; padding-bottom:5px;'>💼 總指揮持倉</h2>", unsafe_allow_html=True)
    cols = st.columns(2)
    for i, (code, p_data) in enumerate(list(st.session_state.portfolio.items())):
        d = port_loaded_cards.get(code)
        if d:
            with cols[i % 2]:
                p_profit, p_roi = calc_real_profit(p_data['entry_price'], d['price'], p_data['qty'])
                st.markdown(f"""<div style="border: 4px solid {'#00FF00' if d['is_action_needed'] else '#ff4d4d'}; border-radius: 8px; padding: 15px; background-color: #1a1a24; margin-bottom: 5px;"><div style="font-weight:bold; font-size:18px;">{d['name']}</div><div style="font-size:24px; font-weight:bold; color:{'#ff4d4d' if p_profit>0 else '#00FF00'};">{p_profit:+,.0f} 元 ({p_roi:+.1f}%)</div></div>""", unsafe_allow_html=True)
                draw_card(d, f"port_{code}", is_portfolio=True, p_data=p_data)
                if st.button("🚪 賣出平倉", key=f"sell_{code}", use_container_width=True):
                    del st.session_state.portfolio[code]
                    save_db(); st.rerun()

# 雷達區
if st.session_state.pinned_stocks:
    st.markdown("<h2 style='color:#f1c40f; margin-top:20px; border-bottom: 2px solid #f1c40f; padding-bottom:5px;'>⭐ 觀測雷達</h2>", unsafe_allow_html=True)
    cols = st.columns(2)
    for i, code in enumerate(list(st.session_state.pinned_stocks.keys())):
        d = pin_loaded_cards.get(code)
        if d:
            with cols[i % 2]:
                draw_card(d, f"pin_{code}")
                c1, c2 = st.columns(2)
                if c1.button("⚡ 買進", key=f"buy_{code}", use_container_width=True):
                    st.session_state.portfolio[code] = {'entry_price': d['price'], 'qty': 1}
                    del st.session_state.pinned_stocks[code]
                    save_db(); st.rerun()
                if c2.button("❌ 刪除", key=f"del_{code}", use_container_width=True):
                    del st.session_state.pinned_stocks[code]
                    save_db(); st.rerun()

# 掃描結果區
if scan_mode_current := st.session_state.get('scan_mode', ""):
    st.markdown("<h2 style='color:#ff4d4d; margin-top:30px; border-bottom: 2px solid #ff4d4d; padding-bottom:5px;'>⚡ 掃描結果</h2>", unsafe_allow_html=True)
    if not st.session_state.scan_results:
        st.warning("⚠️ 掃描完畢，目前無標的符合條件。")
    else:
        cols = st.columns(2)
        for i, d in enumerate([x for x in st.session_state.scan_results if x['code'] not in st.session_state.portfolio and x['code'] not in st.session_state.pinned_stocks]):
            with cols[i % 2]: 
                draw_card(d, f"scan_{i}")
                if st.button("📌 加入雷達", key=f"add_scan_{d['code']}", use_container_width=True):
                    st.session_state.pinned_stocks[d['code']] = {}
                    save_db(); st.rerun()
