import streamlit as st

st.set_page_config(layout="wide", page_title="戰情決策所 - 指揮中心")

# CSS: 打造與圖片完全一致的旗艦風格
st.markdown('''<style>
.stApp { background-color: #0b0c0f !important; color: #fff !important; }
.card { background:#16191f; border-radius:8px; padding:20px; margin-bottom:15px; border: 2px solid #333; }
.alert-banner { background:#4a2b0f; border:1px solid #FFB300; padding:15px; margin-bottom:20px; border-radius:6px; }
.price-tag { font-size:28px; font-weight:bold; color:#FFB300; }
.data-table { background:#262730; padding:10px; border-radius:5px; font-size:14px; margin:10px 0; }
div.stButton > button { background-color: #FF4B4B !important; color: white !important; font-weight: bold; width: 100%; border: none; }
</style>''', unsafe_allow_html=True)

# 狀態初始化
if 'deleted' not in st.session_state: st.session_state.deleted = []

# 1. 側面控制台
with st.sidebar:
    st.markdown("### 🛠️ 1. 側面控制台")
    st.multiselect("戰術標的鎖定", ["3231 緯創", "8454 富邦媒"], default=["3231 緯創", "8454 富邦媒"])
    st.slider("自動定時刷新 (分)", 1, 3, 3)
    st.selectbox("週期慣性偵測", ["Q3 科技慣性", "Q4 金融動能"])
    st.checkbox("破底停損監控", True)
    st.checkbox("自動警報", True)

# 2. 警報區 & 標題
st.markdown('<div class="alert-banner">🚨 戰情雷達：富邦媒 (8454) 觸發出清風控警報，請立即結算！</div>', unsafe_allow_html=True)
st.title("🎯 戰情決策所 (旗艦版)")
if st.button("🔄 強制刷新最新報價 (v42.0)"): st.rerun()

# 3. 雙欄卡片邏輯
col1, col2 = st.columns(2)
stocks = [("緯創", "3231", col1), ("富邦媒", "8454", col2)]

for name, code, col in stocks:
    if code not in st.session_state.deleted:
        with col:
            st.markdown(f'''<div class="card">
                <b>{name} ({code})</b> | 🛡️ 價值盾: 4分
                <div class="price-tag">380.00 <span style="font-size:16px; color:#FF4B4B;">+1.2%</span></div>
                <div class="data-table">開盤: 375.0 | 最高: 385.0 | 最低: 372.0 | 量: 1250張</div>
                <b>錨定進價區間: [ 350 - 370 ]</b><br>
                主力成本: 378.0 | 💰 今日淨損益: +5,200元
            </div>''', unsafe_allow_html=True)
            if st.button(f"❌ 一鍵清空今日標的", key=f"del_{code}"):
                st.session_state.deleted.append(code)
                st.rerun()
