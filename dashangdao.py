import streamlit as st, requests as req
st.set_page_config(page_title="戰情所", layout="wide")

# 鎖定CSS：確保卡片渲染穩定
st.markdown('''<style>
.stApp { background-color: #0b0c0f !important; }
.stock-card { background:#1e1e1e; border-radius:6px; padding:12px; margin-bottom:8px; border:1px solid #333; }
.alert-box { background:rgba(255,75,75,0.2); border:1px solid #FF4B4B; padding:12px; border-radius:6px; margin-bottom:12px; }
.text-price { font-size:20px; font-weight:bold; }
</style>''', unsafe_allow_html=True)

# 資料庫 (包含富邦媒/燿華在庫存區，緯創在觀察區)
DB = {"3231":"緯創","8454":"富邦媒","2367":"燿華","2317":"鴻海"}
TYPES = {"8454":"I", "2367":"I", "3231":"C", "2317":"C"}

st.title("📊 戰情所")
if st.button("🔄 刷新"): st.rerun()

# 模擬庫存 (載入您指定的 1.05 張)
if "mock" not in st.session_state:
    st.session_state["mock"] = {"8454": {"cost": 380.0, "qty": 1.05, "days": 1}}

# 模擬渲染 (確保卡片不隱形)
for c, name in DB.items():
    st.markdown(f'''<div class="stock-card">
        <div><b>{name} ({c})</b></div>
        <div class="text-price">100.00 <span style="font-size:14px;color:#FF4B4B;">+1.2%</span></div>
        <div>🎯 狙擊就位</div>
    </div>''', unsafe_allow_html=True)

# 統一警報
with st.container():
    st.markdown('<div class="alert-box">🚨 戰情雷達：多檔個股已達狙擊點</div>', unsafe_allow_html=True)
