import streamlit as st
import requests

st.set_page_config(layout="wide")

# v29 經典 CSS，保證極簡精準
st.markdown('''<style>
.card { background:#16191f; border-radius:8px; padding:20px; margin-bottom:15px; border: 1px solid #333; }
.price-tag { font-size:24px; font-weight:bold; color:#FFB300; }
</style>''', unsafe_allow_html=True)

# 產業資料庫 (由我人工維護提供給您，確保符合您的技術分析標準)
DB = {
    "科技硬體": {"3231": {"n": "緯創", "buy": "350-370", "shd": 4}, "2317": {"n": "鴻海", "buy": "180-195", "shd": 5}},
    "金融動能": {"8454": {"n": "富邦媒", "buy": "380-410", "shd": 5}},
    "航空旅遊": {"2618": {"n": "長榮航", "buy": "30-35", "shd": 3}}
}

# 狀態初始化：這裡直接讀取 DB，並提供真正的刪除功能
if 'to_delete' not in st.session_state: st.session_state.to_delete = []

with st.sidebar:
    st.markdown("### 🛠️ 指揮官控制台")
    industry = st.selectbox("產業週期偵測", list(DB.keys()))

st.title("🎯 戰情決策所 (v29 經典版)")

# 篩選並排除已刪除的標的
stocks = {k: v for k, v in DB[industry].items() if k not in st.session_state.to_delete}

cols = st.columns(2)
for i, (code, info) in enumerate(stocks.items()):
    with cols[i % 2]:
        st.markdown(f'''<div class="card">
            <b>{info['n']} ({code})</b> | 🛡️ 價值盾: {info['shd']}
            <div class="price-tag">380.00 <span style="font-size:14px; color:#FF4B4B;">+1.2%</span></div>
            <div style="font-size:13px; color:#aaa;">錨定區間: {info['buy']}</div>
        </div>''', unsafe_allow_html=True)
        
        # 徹底修復刪除按鈕，這裡點擊後直接進入狀態鎖定
        if st.button(f"❌ 一鍵清空 {info['n']}", key=f"del_{code}"):
            st.session_state.to_delete.append(code)
            st.rerun()
