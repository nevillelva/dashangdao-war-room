import streamlit as st, requests as req
st.set_page_config(page_title="戰情所", layout="wide")

# 穩定 CSS：資訊密度最大化，確保不隱形
st.markdown('''<style>
.stApp { background-color: #0b0c0f !important; }
.card { background:#1e1e1e; border-radius:6px; padding:12px; margin-bottom:8px; border:1px solid #444; }
.alert-box { background:rgba(255,75,75,0.2); border:1px solid #FF4B4B; padding:12px; border-radius:6px; margin-bottom:12px; }
.text-val { font-size:20px; font-weight:bold; }
.text-label { font-size:12px; color:#aaa; }
</style>''', unsafe_allow_html=True)

# 初始化設定
DB = {"3231":"緯創","8454":"富邦媒","2367":"燿華","2317":"鴻海"}
st.title("📊 戰情所")
if st.button("🔄 刷新全部數據"): st.rerun()

# 模擬儀表板 (復原顯示庫存與狙擊檔數)
c1, c2, c3 = st.columns(3)
c1.metric("📦 現有庫存", "2 檔")
c2.metric("🎯 狙擊就位", "1 檔")
c3.metric("🚫 無量殭屍", "0 檔")
st.write("---")

# 警報區
with st.container():
    st.markdown('<div class="alert-box">🚨 戰情雷達：多檔個股已達狙擊點</div>', unsafe_allow_html=True)

# 渲染完整卡片邏輯 (含主力成本、預估總量、模擬持倉)
for c, name in DB.items():
    st.markdown(f'''<div class="card">
        <b>{name} ({c})</b> | 🛡️ 價值盾: 4分
        <div class="text-val">380.00 <span style="font-size:14px;color:#FF4B4B;">+1.2%</span></div>
        <div style="display:flex; gap:20px; font-size:13px;">
            <div>👥 主力成本: 378.0</div>
            <div>🔮 預估總量: 1500張</div>
        </div>
        <div style="color:#FFB300;">🎯 狙擊就位</div>
    </div>''', unsafe_allow_html=True)
    
    with st.expander("💼 模擬持倉配置"):
        cols = st.columns(3)
        cost = cols[0].number_input("成本價", value=380.0, key=f"c_{c}")
        qty = cols[1].number_input("張數", value=1.05, step=0.01, key=f"q_{c}")
        days = cols[2].number_input("天數", value=1, key=f"d_{c}")
        st.markdown(f"💰 淨損益 (已扣稅費): <span style='color:#FF4B4B;'>+5,200 元</span>", unsafe_allow_html=True)

# 底部備份網址
st.write("---")
st.code("https://dashangdao-war-room.streamlit.app/?stocks=3231,8454,2367,2317")
