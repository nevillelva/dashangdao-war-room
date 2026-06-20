import streamlit as st

st.set_page_config(layout="wide")

# 1. 確保數據持久化：建立一個黑名單機制
if 'deleted_stocks' not in st.session_state:
    st.session_state.deleted_stocks = []

# 初始標的庫 (這是系統底層)
ALL_STOCKS = {
    "3231": {"n": "緯創", "type": "科技硬體", "buy": "350-370", "shd": 4},
    "8454": {"n": "富邦媒", "type": "金融動能", "buy": "380-410", "shd": 5},
    "2618": {"n": "長榮航", "type": "航空旅遊", "buy": "30-35", "shd": 3},
    "2367": {"n": "燿華", "type": "科技硬體", "buy": "25-28", "shd": 2}
}

st.markdown('''<style>
.card { background:#16191f; border-radius:8px; padding:20px; margin-bottom:15px; border: 2px solid #333; }
.price-tag { font-size:24px; font-weight:bold; color:#FFB300; }
</style>''', unsafe_allow_html=True)

# 2. 控制台：加入產業篩選邏輯
with st.sidebar:
    st.markdown("### 🛠️ 指揮官控制台")
    selected_industry = st.selectbox("產業週期偵測", ["全部", "科技硬體", "金融動能", "航空旅遊"])
    
# 3. 核心邏輯：過濾標的 (扣除已刪除的，並過濾產業)
current_stocks = {k: v for k, v in ALL_STOCKS.items() if k not in st.session_state.deleted_stocks}
if selected_industry != "全部":
    current_stocks = {k: v for k, v in current_stocks.items() if v['type'] == selected_industry}

st.title("🎯 戰情決策所 (持久化旗艦版)")

# 4. 渲染卡片
cols = st.columns(2)
for i, (code, s) in enumerate(current_stocks.items()):
    with cols[i % 2]:
        st.markdown(f'''<div class="card">
            <b>{s['n']} ({code})</b> | 🛡️ 價值盾: {s['shd']}分
            <div class="price-tag">380.00 <span style="font-size:16px; color:#FF4B4B;">+1.2%</span></div>
            <div style="font-size:13px; color:#aaa;">開:375 | 高:385 | 低:372 | 量:1250張</div>
            <hr>
            <b>錨定區間: {s['buy']}</b>
        </div>''', unsafe_allow_html=True)
        
        # 刪除功能：加入黑名單
        if st.button(f"❌ 一鍵清空 {s['n']}", key=f"del_{code}"):
            st.session_state.deleted_stocks.append(code)
            st.rerun()
