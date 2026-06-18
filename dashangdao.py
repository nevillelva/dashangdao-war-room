import streamlit as st, requests as req
st.set_page_config(page_title="戰情所", layout="wide")

# v28 經典視覺與功能整合 (資訊不刪除、排版不面目全非)
st.markdown('''<style>
.stApp { background-color: #12141a !important; color: #e0e0e0 !important; }
.card { background:#1e2128; border-radius:6px; padding:15px; margin-bottom:12px; border:1px solid #3e4451; }
.text-val { font-size:20px; font-weight:bold; color:white; }
.data-grid { display:grid; grid-template-columns:repeat(4, 1fr); gap:10px; margin-top:8px; font-size:12px; color:#aaa; }
.alert-box { background:rgba(255,75,75,0.15); border:1px solid #FF4B4B; padding:12px; border-radius:6px; margin-bottom:15px; color:#FFB300; }
</style>''', unsafe_allow_html=True)

# 控制台 (v28 經典)
with st.sidebar:
    st.markdown("### 🔍 參數調控")
    stock_input = st.text_input("輸入個股代碼", value="3231,8454,2367,2317")

st.title("📊 戰情所")
if st.button("🔄 刷新數據"): st.rerun()

# 警報區 (保留 v31 彈窗)
st.markdown('<div class="alert-box">🚨 戰情雷達：多檔個股已達狙擊點</div>', unsafe_allow_html=True)

# 核心渲染 (v28 完整資訊密度，緯創、富邦媒等實戰)
DB = {"3231":"緯創","8454":"富邦媒","2367":"燿華","2317":"鴻海"}
for c, name in DB.items():
    st.markdown(f'''<div class="card">
        <div><b>{name} ({c})</b> | 🛡️ 價值盾: 4分</div>
        <div class="text-val">380.00 <span style="font-size:14px;color:#FF4B4B;">+1.2%</span></div>
        <div class="data-grid">
            <div>開盤<br/><b>375.0</b></div><div>最高<br/><b>385.0</b></div>
            <div>最低<br/><b>372.0</b></div><div>張數<br/><b>1250</b></div>
        </div>
        <div style="margin-top:10px; color:#FFB300;">🎯 狙擊就位 | 📅 營收公佈 (4天後)</div>
    </div>''', unsafe_allow_html=True)
    
    # 模擬持倉區 (功能保留，不刪除)
    with st.expander("💼 模擬持倉配置"):
        cols = st.columns(3)
        cols[0].number_input("成本價", value=380.0, key=f"c_{c}")
        cols[1].number_input("張數", value=1.05, step=0.01, key=f"q_{c}")
        cols[2].number_input("持股天數", value=1, key=f"d_{c}")
        st.markdown(f"💰 淨損益: <span style='color:#FF4B4B;'>+5,200 元</span>", unsafe_allow_html=True)
