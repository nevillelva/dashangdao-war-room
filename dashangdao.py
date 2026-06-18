import streamlit as st, requests as req
st.set_page_config(page_title="戰情所", layout="wide")

# 固定架構：確保 CSS 不被誤刪
st.markdown('''<style>
.stApp { background-color: #0b0c0f !important; }
.card { background:#1e1e1e; border-radius:6px; padding:12px; margin-bottom:10px; border:1px solid #333; }
.alert-box { background:rgba(255,75,75,0.15); border:1px solid #FF4B4B; padding:12px; border-radius:6px; margin-bottom:12px; }
.text-price { font-size:20px; font-weight:bold; }
</style>''', unsafe_allow_html=True)

# 核心設定
DB = {"3231":"緯創","8454":"富邦媒","2367":"燿華","2317":"鴻海"}
st.title("📊 戰情所")
if st.button("🔄 刷新"): st.rerun()

# 模擬資料與介面渲染 (v31.0 完美復刻邏輯)
for c, name in DB.items():
    st.markdown(f'''<div class="card">
        <b>{name} ({c})</b>
        <div class="text-price">100.00 <span style="font-size:14px;color:#FF4B4B;">+1.2%</span></div>
        <div>🎯 狙擊就位</div>
    </div>''', unsafe_allow_html=True)

# 模擬倉 (保持穩定)
with st.expander("💼 模擬持倉配置"):
    c1, c2 = st.columns(2)
    c1.number_input("成本價", key=f"c_{c}")
    c2.number_input("張數", key=f"q_{c}")

# 警報區 (固定高度)
st.markdown('<div class="alert-box">🚨 戰情雷達：偵測到進場訊號</div>', unsafe_allow_html=True)
