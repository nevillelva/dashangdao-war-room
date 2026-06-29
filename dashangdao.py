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
# 基礎配置與狀態初始化
# ==========================================
st.set_page_config(layout="wide", page_title="54088 - 戰情室 V46.0", initial_sidebar_state="expanded")

try:
    COMMANDER_PIN = st.secrets["radar_secrets"]["commander_pin"]
    raw_keys = st.secrets["radar_secrets"]["gemini_api_key"]
    GEMINI_API_KEYS = [k.strip() for k in raw_keys.split(",") if k.strip()]
except KeyError:
    st.error("[致命錯誤] 雲端保險箱 (Secrets) 未設定！")
    st.stop()

# 初始化 AI 等級選擇
if 'ai_mode' not in st.session_state: st.session_state.ai_mode = "快速 (Flash)"

# (其餘狀態初始化維持原樣)
if 'scan_results' not in st.session_state: st.session_state.scan_results = []
if 'scan_mode' not in st.session_state: st.session_state.scan_mode = ""
if 'ai_report' not in st.session_state: st.session_state.ai_report = ""
if 'temp_intel' not in st.session_state: st.session_state.temp_intel = []
if 'portfolio' not in st.session_state: st.session_state.portfolio = {}
if 'pinned_stocks' not in st.session_state: st.session_state.pinned_stocks = {}
if 'active_key_index' not in st.session_state: st.session_state.active_key_index = 0

# (省略重複的 save_db 與身份驗證，邏輯與 V45.1 相同，請保留原有結構)
# [請確保這裡保留原本 save_db(), 身份驗證, fetch 等函數]
# 為節省篇幅，以下直接進入核心功能修改部分

# ==========================================
# 🤖 AI 神經元生成引擎 (V46.0 智核切換版)
# ==========================================
@st.cache_data(ttl=300, show_spinner=False)
def get_best_model(key, preferred_mode):
    """自動探測該金鑰下符合模式的模型"""
    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={key}"
    res = requests.get(url, timeout=5)
    if res.status_code == 200:
        models = res.json().get('models', [])
        # 根據模式決定優先權
        target = "flash" if "快速" in preferred_mode else "pro"
        for m in models:
            name = m.get('name', '').replace('models/', '')
            if target in name.lower() and 'generateContent' in m.get('supportedGenerationMethods', []):
                return name
    return "gemini-pro" # 預設妥協

def generate_ai_report(command_name, candidates):
    if not GEMINI_API_KEYS or not GEMINI_API_KEYS[0]: return "[系統提示] 雲端保險箱未配置有效的 API 金鑰。"
    
    lite_data = [{ '代號': c['code'], '名稱': c['name'], '價格': c['price'], '漲幅': c['gain'], '特徵': c['ai_tags'], 'KDJ': c['kdj_str'] } for c in candidates[:15]]
    prompt = f"""
    你是首席戰略幕僚。總指揮下達戰術：【{command_name}】。
    分析以下標的清單：{json.dumps(lite_data, ensure_ascii=False)}
    請挑選最精銳的 3 檔股票。回報格式：
    [AI 戰術報告：{command_name}]
    1. [代號 名稱] - 理由、觀測重點
    """
    
    # 嘗試策略：優先使用使用者選定模式，若耗盡則自動降級
    modes_to_try = [st.session_state.ai_mode, "快速 (Flash)"] if "快速" not in st.session_state.ai_mode else ["快速 (Flash)"]
    
    for mode in modes_to_try:
        for idx in range(len(GEMINI_API_KEYS)):
            key = GEMINI_API_KEYS[idx]
            model = get_best_model(key, mode)
            try:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
                res = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=30)
                if res.status_code == 200:
                    text = res.json()['candidates'][0]['content']['parts'][0]['text']
                    return f"🟢 使用智核 [{model}]:\n\n{text}"
            except: continue
                
    return "[後勤告急] 所有金鑰額度耗盡，請補充火力。"

# (以下渲染邏輯請銜接原本的 側邊欄與主畫面渲染)
# 記得在側邊欄加入：
# st.session_state.ai_mode = st.radio("AI 智核火力等級:", ["快速 (Flash)", "深度 (Pro)"])
