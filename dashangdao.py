import streamlit as st
from datetime import datetime

# 畫面配置
st.set_page_config(page_title="戰情決策所 - 旗艦版", layout="wide")

# CSS: 建立旗艦級戰鬥視覺
st.markdown('''<style>
.stApp { background-color: #0b0c0f !important; color: #fff !important; }
.card { background:#16191f; border-radius:6px; padding:20px; margin-bottom:15px; border-left: 6px solid #333; }
.alert-banner { background:#4a2b0f; border:1px solid #FFB300; padding:15px; margin-bottom:20px; border-radius:6px; color:#fff; }
.price-tag { font-size:28px; font-weight:bold; color:#FFB300; }
.sidebar-content { background:#16191f; padding:20px; border-radius:10px; }
</style>''', unsafe_allow_html=True)

# 1. 側邊控制台渲染
with st.sidebar:
    st.markdown("### 🛠️ 1. 側面控制台")
    st.multiselect("戰術標的鎖定", ["緯創(3231)", "富邦媒(8454)", "長榮航(2618)"], default=["緯創(3231)", "富邦媒(8454)"])
    st.slider("自動定時刷新 (分)", 1, 3, 3)
    st.slider("價值盾篩選", 2, 4, 4)
    st.selectbox("週期慣性偵測", ["Q3 科技慣性", "Q4 金融動能"])
    st.checkbox("破底停損監控", True)
    st.checkbox("自動警報", True)

# 2. 警報區 (旗艦版核心)
st.markdown('<div class="alert-banner">🚨 戰情雷達：富邦媒 (8454) 觸發出清風控警報，請立即結算！</div>', unsafe_allow_html=True)

st.title("🎯 戰情決策所 (旗艦版)")
st.button("🔄 強制刷新最新報價 (v42.0)")

# 3. 核心卡片區 (左右並排)
col1, col2 = st.columns(2)

def render_card(c_name, c_code, p, gain, cost, alert=None):
    st.markdown(f'''<div class="card">
        <b>{c_name} ({c_code}) | 🛡️ 價值盾: 4分</b>
        <div class="price-tag">{p} <span style="font-size:16px; color:#FF4B4B;">{gain}</span></div>
        <div style="font-size:14px; color:#aaa;">數據儀表板：開盤 375.0 | 最高 385.0 | 成交量 1250張</div>
        <hr>
        <b>錨定進價區間:</b> <div style="background:#FFB300; color:#000; padding:5px;">建議進價區間: [ 350 - 370 ]</div>
        <div style="margin-top:10px; font-size:13px;">主力成本: {cost} | 💰 淨損益: +5,200元</div>
        {"<div style='color:#FFB300;'>🚀 季節進場窗已開啟！</div>" if alert else ""}
    </div>''', unsafe_allow_html=True)
    st.button("❌ 一鍵清空今日此標的", key=f"del_{c_code}")

with col1:
    render_card("緯創", "3231", "380.00", "+1.2%", "378.0", alert=True)
with col2:
    render_card("富邦媒", "8454", "380.00", "+1.2%", "390.0")
