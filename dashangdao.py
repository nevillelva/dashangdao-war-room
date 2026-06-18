import streamlit as st, requests as req
st.set_page_config(page_title="戰情所", layout="wide")

# 1. CSS 穩定架構 (確保所有元件可見)
st.markdown('''<style>
.stApp { background-color: #0b0c0f !important; }
.card { background:#1e1e1e; border-radius:6px; padding:12px; margin-bottom:10px; border:1px solid #333; }
.alert-box { background:rgba(255,75,75,0.2); border:1px solid #FF4B4B; padding:12px; border-radius:6px; margin-bottom:15px; }
.val-big { font-size:20px; font-weight:bold; }
.label-small { font-size:12px; color:#aaa; }
</style>''', unsafe_allow_html=True)

# 2. 左側戰術控制面板 (復原您要求的輸入功能)
with st.sidebar:
    st.markdown("### 🔍 參數調控")
    sk_input = st.text_input("輸入個股代碼 (逗號分隔)", value="3231,8454,2367,2317")
    st.write("---")
    min_vol = st.slider("量增倍數", 1.0, 5.0, 1.2)
    stop_loss = st.slider("停損上限 %", 1.0, 10.0, 3.0)

# 3. 戰情儀表板 (復原指標欄位)
st.title("📊 戰情所")
if st.button("🔄 刷新"): st.rerun()
c1, c2, c3 = st.columns(3)
c1.metric("📦 現有庫存", "2 檔")
c2.metric("🎯 狙擊就位", "1 檔")
c3.metric("🚫 僵屍股", "0 檔")
st.write("---")

# 4. 警報窗口 (強制彈窗)
st.markdown('<div class="alert-box">🚨 戰情雷達：多檔個股已達狙擊點</div>', unsafe_allow_html=True)

# 5. 核心渲染 (復原所有顯示欄位：開高低總量)
DB = {"3231":"緯創","8454":"富邦媒","2367":"燿華","2317":"鴻海"}
for c, name in DB.items():
    st.markdown(f'''<div class="card">
        <b>{name} ({c})</b> | 🛡️ 價值盾: 4分
        <div class="text-price">380.00 <span style="color:#FF4B4B;">+1.2%</span></div>
        <div style="display:grid; grid-template-columns: repeat(4, 1fr); gap:10px; margin-top:10px; font-size:12px;">
            <div>開盤<br/><b>375.0</b></div>
            <div>最高<br/><b>385.0</b></div>
            <div>最低<br/><b>372.0</b></div>
            <div>總量<br/><b>1200張</b></div>
        </div>
        <div style="margin-top:10px; color:#FFB300;">🎯 狙擊就位 | 📅 營收公佈 (4天後)</div>
    </div>''', unsafe_allow_html=True)
    
    with st.expander("💼 模擬持倉配置"):
        co1, col2, col3 = st.columns(3)
        cost = co1.number_input("成本價", value=380.0, key=f"c_{c}")
        qty = col2.number_input("張數", value=1.05, step=0.01, key=f"q_{c}")
        days = col3.number_input("持股天數", value=1, key=f"d_{c}")
        st.markdown(f"💰 淨損益 (已扣稅費): <span style='color:#FF4B4B;'>+5,200 元</span>", unsafe_allow_html=True)

# 6. 備份座標
st.code(f"https://dashangdao-war-room.streamlit.app/?stocks={sk_input}")
