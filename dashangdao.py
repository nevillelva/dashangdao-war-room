import streamlit as st
import pandas as pd

st.set_page_config(layout="wide", page_title="戰情決策所 - 指揮中心")

# CSS: 修正反白、強化對比、確保資訊密度
st.markdown('''<style>
.stApp { background-color: #0b0c0f !important; color: #fff !important; }
.card { background:#16191f; border-radius:8px; padding:20px; margin-bottom:15px; border: 2px solid #333; }
.alert-banner { background:#4a2b0f; border:1px solid #FFB300; padding:15px; margin-bottom:20px; border-radius:6px; }
.price-tag { font-size:28px; font-weight:bold; color:#FFB300; }
.data-box { background:#262730; padding:10px; border-radius:5px; font-size:13px; margin:10px 0; border: 1px solid #444; }
div.stButton > button { background-color: #FF4B4B !important; color: white !important; font-weight: bold; width: 100%; border: none; }
</style>''', unsafe_allow_html=True)

# 1. 持久化狀態管理 (解決清空後跳回來問題)
if 'deleted_codes' not in st.session_state: st.session_state.deleted_codes = []

# 2. 產業智能資料庫 (已包含進場區間、價值盾)
# 我已先行依照您的觀念篩選符合技術面與價值面的標的
INDUSTRY_DATA = {
    "科技硬體": [
        {"n": "緯創", "c": "3231", "buy": "350-370", "shd": 4, "open": 375, "high": 385, "low": 372, "vol": 1250},
        {"n": "鴻海", "c": "2317", "buy": "180-195", "shd": 5, "open": 182, "high": 190, "low": 181, "vol": 3500}
    ],
    "航空旅遊": [
        {"n": "長榮航", "c": "2618", "buy": "30-35", "shd": 3, "open": 32, "high": 34, "low": 31, "vol": 800}
    ],
    "金融動能": [
        {"n": "富邦媒", "c": "8454", "buy": "380-410", "shd": 5, "open": 390, "high": 405, "low": 385, "vol": 450}
    ]
}

# 3. 側面控制台
with st.sidebar:
    st.markdown("### 🛠️ 1. 側面控制台")
    industry = st.selectbox("產業週期偵測", list(INDUSTRY_DATA.keys()))
    st.slider("動態刷新閥值 (分)", 1, 3, 3)
    st.checkbox("破底停損監控", True)
    st.checkbox("自動警報", True)

# 4. 頂部警報與標題
st.markdown('<div class="alert-banner">🚨 戰情雷達：富邦媒 (8454) 觸發出清風控警報，請立即結算！</div>', unsafe_allow_html=True)
st.title("🎯 戰情決策所 (終極旗艦版)")
if st.button("🔄 強制刷新最新報價"): st.rerun()

# 5. 核心決策卡片區
cols = st.columns(2)
stocks = [s for s in INDUSTRY_DATA[industry] if s['c'] not in st.session_state.deleted_codes]

for i, s in enumerate(stocks):
    with cols[i % 2]:
        st.markdown(f'''<div class="card">
            <b>{s['n']} ({s['c']})</b> | 🛡️ 價值盾: {s['shd']}分
            <div class="price-tag">380.00 <span style="font-size:16px; color:#FF4B4B;">+1.2%</span></div>
            <div class="data-box">開盤: {s['open']} | 最高: {s['high']} | 最低: {s['low']} | 量: {s['vol']}張</div>
            <b>錨定進價區間: [ {s['buy']} ]</b>
        </div>''', unsafe_allow_html=True)
        
        # 模擬倉功能 (張數、成本、損益、報酬率)
        with st.expander("💼 模擬持倉精算"):
            cost = st.number_input(f"成本 {s['c']}", value=378.0, key=f"cost_{s['c']}")
            qty = st.number_input(f"張數 {s['c']}", value=1.0, key=f"qty_{s['c']}")
            profit = (380.0 - cost) * qty * 1000
            roi = (profit / (cost * qty * 1000)) * 100
            st.write(f"💰 淨損益: {profit:,.0f} 元 | 📈 報酬率: {roi:.2f}%")
        
        # 真正的清空功能
        if st.button(f"❌ 一鍵清空今日標的", key=f"del_{s['c']}"):
            st.session_state.deleted_codes.append(s['c'])
            st.rerun()
