import streamlit as st
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

st.set_page_config(layout="wide", page_title="54088 - 終極大腦 V15")

# ==========================================
# 🛡️ 霸王級 CSS (保留完美深色戰情室)
# ==========================================
st.markdown('''<style>
.stApp { background-color: #0b0c0f !important; color: #fff !important; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
div[data-testid="stButton"] > button { background-color: #1e1e24 !important; border: 1px solid #444 !important; transition: all 0.2s ease-in-out; }
div[data-testid="stButton"] > button p { color: #ffffff !important; font-weight: bold !important; font-size: 15px !important; }
div[data-testid="stButton"] > button:hover { border-color: #f1c40f !important; transform: translateY(-2px); box-shadow: 0 4px 10px rgba(241,196,15,0.2); }
[data-testid="stExpander"] details summary { background-color: #16191f !important; border: 1px solid #3498db !important; border-radius: 8px !important; margin-bottom: 5px !important; }
[data-testid="stExpander"] details summary p { color: #f1c40f !important; font-weight: 900 !important; font-size: 16px !important; }
.my-tooltip { position: relative; display: inline-block; cursor: help; }
.my-tooltip .my-tooltiptext { visibility: hidden; width: max-content; max-width: 280px; background-color: #ffcc00; color: #111; text-align: left; border-radius: 6px; padding: 10px 14px; position: absolute; z-index: 99999; bottom: 125%; left: 50%; transform: translateX(-50%); opacity: 0; transition: opacity 0.2s; font-size: 14px; font-weight: bold; line-height: 1.5; box-shadow: 0px 4px 15px rgba(0,0,0,0.6); pointer-events: none; white-space: normal; }
.my-tooltip .my-tooltiptext::after { content: ""; position: absolute; top: 100%; left: 50%; margin-left: -6px; border-width: 6px; border-style: solid; border-color: #ffcc00 transparent transparent transparent; }
.my-tooltip:hover .my-tooltiptext { visibility: visible; opacity: 1; }
</style>''', unsafe_allow_html=True)

# ==========================================
# 📡 系統資料庫 & 官方 OpenAPI
# ==========================================
TW_STOCKS = {
    "2330":"台積電", "2317":"鴻海", "2454":"聯發科", "2382":"廣達", "2303":"聯電",
    "2881":"富邦金", "2882":"國泰金", "2891":"中信金", "2886":"兆豐金",
    "2603":"長榮", "2609":"陽明", "2615":"萬海", "1519":"華城", "1513":"中興電",
    "2408":"南亞科" # 新增避險白名單
}

@st.cache_data(ttl=86400)
def fetch_official_fundamentals():
    dynamic_data = {}
    market_codes = []
    # 模擬抓取證交所API (實戰維持現有連線)
    try:
        twse_url = "https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL"
        twse_res = requests.get(twse_url, timeout=10)
        if twse_res.status_code == 200:
            for item in twse_res.json():
                code = item.get('Code', '').strip()
                if len(code) >= 4 and code.isdigit():
                    market_codes.append(code)
                    TW_STOCKS[code] = item.get('Name', code)
                    pe = float(item.get('PeRatio', 0)) if item.get('PeRatio', '-').replace('.', '', 1).isdigit() else 999.0
                    pb = float(item.get('PbRatio', 0)) if item.get('PbRatio', '-').replace('.', '', 1).isdigit() else 999.0
                    yld = float(item.get('DividendYield', 0)) if item.get('DividendYield', '-').replace('.', '', 1).isdigit() else 0.0
                    dynamic_data[code] = {'PE': pe, 'PB': pb, 'Yield': yld}
    except: pass
    if not market_codes: market_codes = list(TW_STOCKS.keys())
    return list(set(market_codes)), dynamic_data

FULL_MARKET_CODES, FUNDAMENTAL_DB = fetch_official_fundamentals()

# ==========================================
# 🧠 核心量化演算法 (V15 整合第二層思考與防禦)
# ==========================================
def calculate_tactical_signals(symbol_data, category_type="main", mode="短線技術動能單", manual_target=0.0, portfolio_data=None):
    try:
        parts = symbol_data.split(":")
        symbol = parts[0].strip()
        stock_name = TW_STOCKS.get(symbol, f"個股 {symbol}") 
        
        fund_info = FUNDAMENTAL_DB.get(symbol, {})
        dynamic_pe = fund_info.get('PE', 999.0)
        dynamic_pb = fund_info.get('PB', 999.0)
        
        val_code = "2"
        if dynamic_pe < 12.0 or dynamic_pb < 1.2: val_code = "1"
        elif dynamic_pe > 25.0 or dynamic_pb > 3.0: val_code = "3"

        # [V15 重試機制] 指數退避防封鎖
        hist = pd.DataFrame()
        ticker = None
        for attempt in range(3):
            try:
                temp_ticker = yf.Ticker(f"{symbol}.TW")
                temp_hist = temp_ticker.history(period="2y")
                if not temp_hist.empty and len(temp_hist) > 15: 
                    hist = temp_hist; ticker = temp_ticker
                    break
            except:
                time.sleep(random.uniform(1.0, 3.0)) # 隱蔽等待
                
        if hist.empty: return None

        current_price = float(hist['Close'].iloc[-1])
        prev_price = max(float(hist['Close'].iloc[-2]), 0.001)
        open_p, high_p, low_p = float(hist['Open'].iloc[-1]), float(hist['High'].iloc[-1]), float(hist['Low'].iloc[-1])
        gain = ((current_price - prev_price) / prev_price) * 100

        vol = int(hist['Volume'].iloc[-1] / 1000)
        vol_5d = max(hist['Volume'].iloc[-6:-1].mean() / 1000, 0.01) 
        vol_20d = max(hist['Volume'].iloc[-21:-1].mean() / 1000, 0.01) if len(hist) > 20 else vol_5d
        vol_ratio = vol / vol_5d 
        
        ma5 = hist['Close'].rolling(window=5).mean().iloc[-1]
        ma10 = hist['Close'].rolling(window=10).mean().iloc[-1]
        ma20 = hist['Close'].rolling(window=20).mean().iloc[-1]
        ma60 = hist['Close'].rolling(window=60).mean().iloc[-1]

        is_ma_bullish = (current_price > ma10) and (ma10 > ma20) and (ma20 > ma60)
        
        # [V15 賣出三要件升級] 避雷針(上影線)判定
        body = abs(current_price - open_p)
        upper_shadow = high_p - max(open_p, current_price)
        is_shooting_star = upper_shadow > (body * 1.5) and high_p > ma5
        
        is_huge_vol = vol > (vol_5d * 2.0)                
        is_black_k = current_price < open_p and gain < 0 
        is_break_ma5 = current_price < ma5                
        sell_cond_count = sum([is_huge_vol, is_shooting_star, is_break_ma5])

        # [V15 買進升級] 攻擊量與冷門甦醒判定
        is_attack_vol = vol_ratio >= 4.0
        is_cold_to_hot = (vol_20d <= 1.0) and is_attack_vol # 過去無人問津，今天突然爆量
        buy_cond_count = sum([is_attack_vol, is_cold_to_hot, current_price > ma20])

        main_cost = ma60 if current_price >= ma60 * 0.96 else ma240
        buy_high = round(main_cost * 1.03, 1)

        ACTION_WAIT, ACTION_NO, ACTION_YES, ACTION_HOLD = "⏳ 【等待時機】", "❌ 【極度危險】", "✅ 【可以買進】", "🛡️ 【先觀察】"
        signal_text, color_border, signal_bg = "", "", ""

        target_reference = manual_target if manual_target > 0 else (current_price * 1.1)
        expected_roi = ((target_reference - current_price) / current_price) * 100 if target_reference > 0 else 0

        # [V15 決策樹重構：絕對攔截與期望值濾網]
        if val_code == "3": 
            signal_text, color_border, signal_bg = f"{ACTION_NO} 股價太貴已達天花板，買進期望值極低，千萬別買！", "#e74c3c", "#3a1515"
        elif expected_roi < 30.0 and mode == "長線價值波段單" and manual_target > 0:
            signal_text, color_border, signal_bg = f"{ACTION_WAIT} 距離目標價潛在報酬僅 {expected_roi:.1f}% (<30%)，肉太少不值得冒險！", "#f39c12", "#3a3015"
        elif (is_ma_bullish or buy_cond_count >= 1) and current_price > (buy_high * 1.03):
            signal_text, color_border, signal_bg = f"{ACTION_WAIT} 雖具備右側動能，但已嚴重偏離防守區，請等拉回再買！", "#f39c12", "#3a3015"
        elif is_cold_to_hot:
            signal_text, color_border, signal_bg = f"{ACTION_YES} 🌟 冷門轉機股甦醒！出現罕見攻擊量，具備妖股潛力！", "#e056fd", "#2c153a"
        elif buy_cond_count >= 1 or is_ma_bullish: 
            signal_text, color_border, signal_bg = f"{ACTION_YES} 突破或均線多頭確立！(右側極速狙擊)", "#00FF00", "#153a20"
        else: 
            signal_text, color_border, signal_bg = f"{ACTION_HOLD} 股價卡在區間，在旁邊看戲。", "#ccc", "#2b2b36"

        return {
            "name": stock_name, "code": symbol, "price": current_price, "gain": gain, 
            "val_code": val_code, "signal": signal_text, "color": color_border, "signal_bg": signal_bg,
            "is_ma_bullish": is_ma_bullish, "buy_cond_count": buy_cond_count, "sell_cond_count": sell_cond_count,
            "expected_roi": expected_roi, "is_shooting_star": is_shooting_star, "is_attack_vol": is_attack_vol
        }
    except Exception as e: return None

# ==========================================
# 🖥️ 戰情室主要版面 & 多執行緒掃描
# ==========================================
st.markdown("<h1 style='color:#FFB300;'>54088 終極大腦 V15</h1>", unsafe_allow_html=True)

# [V15 資金避風港推薦機制]
def suggest_safe_havens():
    st.markdown("<div style='background:#1e222b; padding:10px; border-left:4px solid #f1c40f; margin-bottom:15px;'><strong>💡 第二層思考：資金避風港 (低基期防禦名單)</strong><br><span style='font-size:13px; color:#aaa;'>當熱門股過熱時，資金可能轉移至以下本業賺錢且低估值的標的：</span></div>", unsafe_allow_html=True)
    safe_stocks = [c for c, d in FUNDAMENTAL_DB.items() if d['PE'] > 0 and d['PE'] < 12 and d['Yield'] >= 5.0][:3]
    cols = st.columns(3)
    for i, scode in enumerate(safe_stocks):
        with cols[i]:
            st.info(f"{TW_STOCKS.get(scode, scode)} ({scode})\nPE: {FUNDAMENTAL_DB[scode]['PE']:.1f}")

search_query = st.text_input("📝 輸入代號或名稱手動探測：")
if search_query:
    clean_code = re.split(r'[,\s、，]+', search_query)[0]
    with st.spinner("情報解算中..."):
        d = calculate_tactical_signals(f"{clean_code}:?:?:?:?")
        if d:
            st.markdown(f"""
            <div style="border: 2px solid {d['color']}; border-radius: 8px; padding: 15px; background-color: #16191f;">
            <div style="font-weight:bold; font-size:18px;">{d['name']} ({d['code']})</div>
            <div style="font-size:32px; font-weight:bold;">{d['price']:.2f} <span style="font-size:16px;">{d['gain']:+.1f}%</span></div>
            <div style="background:{d['signal_bg']}; padding:10px; margin-top:10px; border-radius:6px; text-align:center;">
            <strong style="color:{d['color']}; font-size:16px;">{d['signal']}</strong></div></div>
            """, unsafe_allow_html=True)
            
            # 若昂貴，觸發同族群/低位階避風港
            if d['val_code'] == "3":
                st.warning("⚠️ 警告：該標的已達天花板，建議採用第二層思考進行資金輪動！")
                suggest_safe_havens()

st.markdown("### ⚡ 全市場多執行緒雷達引擎")
def run_parallel_scan(target_codes):
    results = []
    # 使用 ThreadPoolExecutor 並發抓取，加入適當線程數防封鎖
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        future_to_code = {executor.submit(calculate_tactical_signals, f"{c}:?:?:?:?"): c for c in target_codes}
        progress_bar = st.progress(0)
        for i, future in enumerate(concurrent.futures.as_completed(future_to_code)):
            d = future.result()
            if d and d['gain'] > -2.0 and d['sell_cond_count'] == 0:
                results.append(d)
            progress_bar.progress(min((i + 1) / len(target_codes), 1.0))
        progress_bar.empty()
    return results

if st.button("🚀 啟動 500 檔冷門與熱門交集掃描", use_container_width=True):
    with st.spinner("📡 系統正以多執行緒陣列迴避封鎖，進行全市場大掃描..."):
        scan_pool = FULL_MARKET_CODES[:500] 
        res = run_parallel_scan(scan_pool)
        for d in res[:10]: # 顯示前10檔精華
            st.success(f"{d['name']} ({d['code']}) - 現價: {d['price']} | 狀態: {d['signal']}")
