import streamlit as st

st.set_page_config(layout="wide")

# 初始化刪除紀錄
if 'deleted_stocks' not in st.session_state:
    st.session_state.deleted_stocks = []

# 定義初始完整標的庫
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

# 控制台與過濾
with st.sidebar:
    st.markdown("### 🛠️ 指揮官控制台")
    industry = st.selectbox("產業週期偵測", ["全部", "科技硬體", "金融動能", "航空旅遊"])
    st.write(f"目前篩選: {industry}")

st.title("🎯 戰情決策所 (持久化旗艦版)")

# 關鍵邏輯：過濾掉在黑名單裡的標的
active_stocks = {k: v for k, v in ALL_STOCKS.items() if k not in st.session_state.deleted_stocks}

# 產業過濾
if industry != "全部":
    active_stocks = {k: v for k, v in active_stocks.items() if v['type'] == industry}

# 渲染
cols = st.columns(2)
for i, (code, s) in enumerate(active_stocks.items()):
    with cols[i % 2]:
        st.markdown(f'''<div class="card">
            <b>{s['n']} ({code})</b> | 🛡️ 價值盾: {s['shd']}分
            <div class="price-tag">380.00 <span style="font-size:16px; color:#FF4B4B;">+1.2%</span></div>
            <div style="font-size:13px; color:#aaa;">錨定區間: {s['buy']}</div>
        </div>''', unsafe_allow_html=True)
        
        # 使用 callback 處理刪除，強制更新狀態
        if st.button(f"❌ 清空 {s['n']}", key=f"del_{code}"):
            st.session_state.deleted_stocks.append(code)
            st.rerun()
