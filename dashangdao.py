import streamlit as st
from datetime import datetime

st.set_page_config(page_title="戰情決策所 - 旗艦版", layout="wide")

# CSS: 打造高密度數據視覺
st.markdown('''<style>
.stApp { background-color: #0b0c0f !important; color: #fff !important; }
.card { background:#16191f; border-radius:8px; padding:20px; margin-bottom:15px; border-left: 6px solid #333; border: 1px solid #333; }
.alert-banner { background:#4a2b0f; border:1px solid #FFB300; padding:15px; margin-bottom:20px; border-radius:6px; }
.price-tag { font-size:28px; font-weight:bold; color:#FFB300; }
</style>''', unsafe_allow_html=True)

STOCKS = [
    {"n": "緯創", "c": "3231", "buy": "350-370", "shd": 4, "cost": 378.0},
    {"n": "鴻海", "c": "2317", "buy": "180-195", "shd": 5, "cost": 175.0},
    {"n": "長榮航", "c": "2618", "buy": "30-35", "shd": 3, "cost": 32.0},
    {"n": "燿華", "c": "2367", "buy": "25-28", "shd": 2, "cost": 26.5}
]

st.title("🎯 戰情決策所 (旗艦版)")
st.markdown('<div class="alert-banner">📊 系統狀態：即時監控中 | 核心數據全展開</div>', unsafe_allow_html=True)

# 兩欄式佈局
cols = st.columns(2)
for i, s in enumerate(STOCKS):
    with cols[i % 2]:
        st.markdown(f'''<div class="card">
            <b>{s['n']} ({s['c']})</b> | 🛡️ 價值盾: {s['shd']}分
            <div class="price-tag">380.00 <span style="font-size:14px;color:#FF4B4B;">+1.2%</span></div>
            <div style="font-size:14px; color:#aaa;">建議進價區間: {s['buy']}</div>
            <hr>
            <div style="font-size:14px; color:#fff;">主力成本: {s['cost']} | 💰 淨損益: +5,200元</div>
        </div>''', unsafe_allow_html=True)
        # 移除選單，改為直接可用的操作列
        st.button("❌ 一鍵清空今日此標的", key=f"del_{s['c']}")
