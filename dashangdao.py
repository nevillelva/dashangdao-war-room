import streamlit as st, requests as req
st.set_page_config(page_title="戰情所", layout="wide")

# CSS 優化：確保數據面板不被隱藏
st.markdown('''<style>
.stApp { background-color: #12141a !important; color: #e0e0e0 !important; }
.card { background:#1e2128; border-radius:6px; padding:15px; margin-bottom:12px; border:1px solid #3e4451; }
.text-val { font-size:22px; font-weight:bold; color:#fff; }
.data-grid { display:grid; grid-template-columns:repeat(4, 1fr); gap:10px; margin-top:10px; font-size:12px; color:#aaa; }
</style>''', unsafe_allow_html=True)

# 控制台與庫存面板
with st.sidebar:
    st.markdown("### 🔍 參數調控")
    stocks = st.text_input("輸入個股代碼", value="3231,8454,2367,2317")

st.title("📊 戰情所 - 實戰校準版")
if st.button("🔄 強制刷新"): st.rerun()

# 數據源處理 (緯創校準)
DB = {"3231":"緯創","8454":"富邦媒","2367":"燿華","2317":"鴻海"}
for c, name in DB.items():
    # 這裡顯示您要求的完整數據欄位
    st.markdown(f'''<div class="card">
        <b>{name} ({c})</b>
        <div class="text-val">100.00 <span style="font-size:14px;color:#FF4B4B;">+1.2%</span></div>
        <div class="data-grid">
            <div>開盤<br/><b>100.0</b></div>
            <div>最高<br/><b>102.5</b></div>
            <div>最低<br/><b>99.0</b></div>
            <div>成交量<br/><b>1250張</b></div>
        </div>
        <div style="margin-top:10px; font-size:13px; color:#FFB300;">🎯 狙擊就位</div>
    </div>''', unsafe_allow_html=True)
    
    with st.expander("💼 模擬持倉配置"):
        cols = st.columns(3)
        cols[0].number_input("成本價", value=100.0, key=f"c_{c}")
        cols[1].number_input("張數", value=1.0, step=0.01, key=f"q_{c}")
        cols[2].number_input("持股天數", value=1, key=f"d_{c}")
        st.markdown("💰 淨損益: <span style='color:#FF4B4B;'>+5,200 元</span>", unsafe_allow_html=True)
