import streamlit as st

st.set_page_config(page_title="戰情決策所 - 旗艦指揮中心", layout="wide")

st.markdown('''<style>
.stApp { background-color: #0b0c0f !important; color: #fff !important; }
.card { background:#16191f; border-radius:8px; padding:20px; margin-bottom:15px; border: 2px solid #333; }
.price-tag { font-size:28px; font-weight:bold; color:#FFB300; }
div.stButton > button { background-color: #ffffff; color: #000; font-weight: bold; width: 100%; border: none; }
</style>''', unsafe_allow_html=True)

# 狀態管理：確保標的可以被真正移除
if 'active_stocks' not in st.session_state:
    st.session_state.active_stocks = ["3231", "8454"]

# 左側控制台
with st.sidebar:
    st.markdown("### 🛠️ 指揮官控制台")
    st.selectbox("產業週期偵測", ["科技硬體", "金融動能", "航空旅遊", "原物料傳產"])
    st.checkbox("破底停損監控", True)

st.title("🎯 戰情決策所 (旗艦終極版)")

# 決策卡片
cols = st.columns(2)
for i, code in enumerate(st.session_state.active_stocks):
    col = cols[i % 2]
    with col:
        st.markdown(f'''<div class="card">
            <b>緯創 ({code})</b> | 🛡️ 價值盾: 4分
            <div class="price-tag">380.00 <span style="font-size:16px; color:#FF4B4B;">+1.2%</span></div>
            <div style="font-size:13px; color:#aaa;">開盤: 375 | 最高: 385 | 最低: 372 | 量: 1250張</div>
            <hr>
            <b>錨定進價區間: [ 350 - 370 ]</b>
        </div>''', unsafe_allow_html=True)
        
        with st.expander("💼 模擬持倉精算 (成本/張數/報酬率)"):
            cost = st.number_input(f"持有成本 {code}", value=378.0, key=f"cost_{code}")
            qty = st.number_input(f"持有張數 {code}", value=1.0, key=f"qty_{code}")
            profit = (380.0 - cost) * qty * 1000
            roi = (profit / (cost * qty * 1000)) * 100
            st.markdown(f"💰 **淨損益:** {profit:,.0f} 元 | 📈 **報酬率:** {roi:.2f}%")
            
        if st.button(f"❌ 一鍵清空 {code}", key=f"del_{code}"):
            st.session_state.active_stocks.remove(code)
            st.rerun()
