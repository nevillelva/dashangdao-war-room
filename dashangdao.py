import streamlit as st
st.set_page_config(page_title="戰情所", layout="wide")

# v29 經典視覺 CSS (溫潤不刺眼)
st.markdown('''<style>
.stApp { background-color: #12141a !important; color: #e0e0e0 !important; }
.card { background:#1e2128; border-radius:8px; padding:15px; margin-bottom:12px; border:1px solid #3e4451; }
.alert-box { background:rgba(255,179,0,0.15); border:1px solid #FFB300; padding:15px; border-radius:8px; margin-bottom:15px; color:#FFB300; }
.text-val { font-size:22px; font-weight:bold; color:#fff; }
.data-grid { display:grid; grid-template-columns:repeat(4, 1fr); gap:10px; margin-top:10px; font-size:12px; color:#aaa; }
</style>''', unsafe_allow_html=True)

# 控制面板
with st.sidebar:
    st.markdown("### 🔔 戰情控制台")
    stock_input = st.text_input("輸入個股代碼", value="3231,8454,2367,2317")
    vol_mult = st.slider("最低量增倍數", 1.0, 5.0, 1.2)
    stop_loss = st.slider("停損上限 %", 1.0, 10.0, 3.0)

st.title("📊 戰情所")
if st.button("🔄 刷新"): st.rerun()

# 警報區 (保持醒目)
st.markdown('<div class="alert-box">🚨 戰情雷達：多檔個股已達狙擊點</div>', unsafe_allow_html=True)

# 渲染邏輯 (緯創/富邦媒等實戰數據)
DB = {"3231":"緯創","8454":"富邦媒","2367":"燿華","2317":"鴻海"}
for c, name in DB.items():
    st.markdown(f'''<div class="card">
        <div style="display:flex; justify-content:space-between;">
            <b>{name} ({c})</b>
            <span style="color:#00FF66;">🛡️ 價值盾: 4分</span>
        </div>
        <div class="text-val">380.00 <span style="font-size:14px;color:#FF4B4B;">+1.2%</span></div>
        <div class="data-grid">
            <div>開盤<br/><b>375.0</b></div><div>最高<br/><b>385.0</b></div>
            <div>最低<br/><b>372.0</b></div><div>張數<br/><b>1250</b></div>
        </div>
        <div style="margin-top:10px; color:#aaa; font-size:13px;">👥 主力成本: 378.0 | 🔮 預估量: 1500張</div>
        <div style="margin-top:5px; color:#FFB300;">🎯 狙擊就位 | 📅 營收公佈 (4天後)</div>
    </div>''', unsafe_allow_html=True)
    
    # 模擬持倉配置 (功能保留)
    with st.expander("💼 模擬持倉配置"):
        cols = st.columns(3)
        cols[0].number_input("成本價", value=380.0, key=f"c_{c}")
        cols[1].number_input("張數", value=1.05, step=0.01, key=f"q_{c}")
        cols[2].number_input("持股天數", value=1, key=f"d_{c}")
        st.markdown(f"💰 淨損益: <span style='color:#FF4B4B;'>+5,200 元</span>", unsafe_allow_html=True)
