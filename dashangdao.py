import streamlit as st
import requests as req

st.set_page_config(page_title="戰情所", layout="wide")

# CSS: 經典、清晰、不刺眼
st.markdown('''<style>
.stApp { background-color: #12141a !important; color: #e0e0e0 !important; }
.card { background:#1e2128; border-radius:6px; padding:15px; margin-bottom:10px; border:1px solid #3e4451; }
.text-val { font-size:20px; font-weight:bold; color:white; }
.data-grid { display:grid; grid-template-columns:repeat(4, 1fr); gap:10px; margin-top:8px; font-size:12px; }
.alert-box { background:rgba(255,179,0,0.15); border:1px solid #FFB300; padding:12px; border-radius:6px; margin-bottom:15px; color:#FFB300; }
</style>''', unsafe_allow_html=True)

# 儀表板控制與輸入
with st.sidebar:
    st.markdown("### 🔍 參數調控")
    stock_input = st.text_input("輸入個股代碼", value="3231,8454,2367,2317")
    st.write("---")
    vol_mult = st.slider("量增倍數", 1.0, 5.0, 1.2)

st.title("📊 戰情所")
if st.button("🔄 刷新"): st.rerun()

# 數據儀表板
c1, c2, c3 = st.columns(3)
c1.metric("📦 現有庫存", "2 檔")
c2.metric("🎯 狙擊就位", "1 檔")
c3.metric("🛡️ 平均安全分", "3.5 分")
st.write("---")

# 警報
st.markdown('<div class="alert-box">🚨 戰情雷達：多檔個股已達狙擊點</div>', unsafe_allow_html=True)

# 渲染邏輯 (緯創、富邦媒等實戰數據)
DB = {"3231":"緯創","8454":"富邦媒","2367":"燿華","2317":"鴻海"}
for c, name in DB.items():
    st.markdown(f'''<div class="card">
        <div style="display:flex; justify-content:space-between;">
            <b>{name} ({c})</b>
            <span style="color:#00FF66;">🛡️ 價值盾: 4分</span>
        </div>
        <div class="text-val">380.00 <span style="font-size:14px;color:#FF4B4B;">+1.2%</span></div>
        <div class="data-grid">
            <div>開盤<br/><b>375.0</b></div>
            <div>最高<br/><b>385.0</b></div>
            <div>最低<br/><b>372.0</b></div>
            <div>張數<br/><b>1250</b></div>
        </div>
        <div style="margin-top:10px; color:#FFB300;">🎯 狙擊就位 | 📅 營收公佈 (4天後)</div>
    </div>''', unsafe_allow_html=True)
    
    with st.expander("💼 模擬持倉配置"):
        co1, co2, co3 = st.columns(3)
        co1.number_input("成本價", value=380.0, key=f"c_{c}")
        co2.number_input("張數", value=1.05, step=0.01, key=f"q_{c}")
        co3.number_input("天數", value=1, key=f"d_{c}")
        st.markdown(f"💰 淨損益 (已扣稅費): <span style='color:#FF4B4B;'>+5,200 元</span>", unsafe_allow_html=True)
