import streamlit as st, requests as req
st.set_page_config(page_title="戰情所", layout="wide")

# 經典視覺 CSS (溫潤不刺眼)
st.markdown('''<style>
.stApp { background-color: #12141a !important; color: #e0e0e0 !important; }
.card { background:#1e2128; border-radius:8px; padding:15px; margin-bottom:12px; border:1px solid #3e4451; }
.alert-box { background:rgba(255,179,0,0.15); border:1px solid #FFB300; padding:15px; border-radius:8px; margin-bottom:15px; color:#FFB300; }
.text-val { font-size:22px; font-weight:bold; color:#fff; }
.text-label { font-size:13px; color:#a0a0a0; }
</style>''', unsafe_allow_html=True)

# 戰術控制台
with st.sidebar:
    st.markdown("### 🔔 控制台")
    stocks = st.text_input("輸入個股代碼", value="3231,8454,2367,2317")
    st.write("---")
    vol_mult = st.slider("最低量增倍數", 1.0, 5.0, 1.2)
    stop_loss = st.slider("停損上限 %", 1.0, 10.0, 3.0)

st.title("📊 戰情所")
if st.button("🔄 刷新數據"): st.rerun()

# 模擬資料流 (v29 經典架構)
c1, c2, c3 = st.columns(3)
c1.metric("📦 現有庫存", "2 檔")
c2.metric("🎯 狙擊就位", "1 檔")
c3.metric("🛡️ 平均安全分", "3.5 分")
st.write("---")

# 警報區
st.markdown('<div class="alert-box">🚨 戰情雷達：多檔個股已達狙擊點</div>', unsafe_allow_html=True)

# 渲染卡片 (v29 資訊密度)
DB = {"3231":"緯創","8454":"富邦媒","2367":"燿華","2317":"鴻海"}
for c, name in DB.items():
    st.markdown(f'''<div class="card">
        <div style="display:flex; justify-content:space-between;">
            <b>{name} ({c})</b>
            <span style="color:#00FF66;">🛡️ 價值盾: 4分</span>
        </div>
        <div class="text-val">380.00 <span style="font-size:14px;color:#FF4B4B;">+1.2%</span></div>
        <div style="display:flex; gap:20px; margin-top:10px;">
            <div class="text-label">👥 主力成本: 378.0</div>
            <div class="text-label">🔮 預估總量: 1500張</div>
        </div>
        <div style="margin-top:10px; color:#FFB300;">🎯 狙擊就位 | 📅 營收公佈 (4天後)</div>
    </div>''', unsafe_allow_html=True)
    
    with st.expander("💼 模擬持倉配置"):
        cols = st.columns(3)
        cols[0].number_input("成本價", value=380.0, key=f"c_{c}")
        cols[1].number_input("張數", value=1.05, step=0.01, key=f"q_{c}")
        cols[2].number_input("天數", value=1, key=f"d_{c}")
        st.markdown(f"💰 淨損益 (已扣稅費): <span style='color:#FF4B4B;'>+5,200 元</span>", unsafe_allow_html=True)
