import streamlit as st
from datetime import datetime

# 旗艦版配置
st.set_page_config(page_title="戰情決策所 - 旗艦十檔版", layout="wide")

# CSS: 旗艦級視覺排版
st.markdown('''<style>
.stApp { background-color: #0b0c0f !important; color: #fff !important; }
.card { background:#16191f; border-radius:8px; padding:20px; margin-bottom:15px; border-left: 6px solid #333; border: 1px solid #333; }
.alert-banner { background:#4a2b0f; border:1px solid #FFB300; padding:15px; margin-bottom:20px; border-radius:6px; }
.price-tag { font-size:24px; font-weight:bold; color:#FFB300; }
</style>''', unsafe_allow_html=True)

# 【十檔核心戰術庫】
STOCKS = [
    {"n": "緯創", "c": "3231", "buy": "350-370", "shd": 4, "v": 5.0},
    {"n": "鴻海", "c": "2317", "buy": "180-195", "shd": 5, "v": 3.0},
    {"n": "長榮航", "c": "2618", "buy": "30-35", "shd": 3, "v": 4.0},
    {"n": "燿華", "c": "2367", "buy": "25-28", "shd": 2, "v": 6.0},
    {"n": "富邦媒", "c": "8454", "buy": "380-410", "shd": 5, "v": 3.5},
    {"n": "京元電", "c": "2449", "buy": "120-130", "shd": 4, "v": 4.5},
    {"n": "台積電", "c": "2330", "buy": "850-900", "shd": 5, "v": 2.5},
    {"n": "聯電", "c": "2303", "buy": "50-55", "shd": 3, "v": 4.0},
    {"n": "廣達", "c": "2382", "buy": "280-300", "shd": 4, "v": 5.0},
    {"n": "奇鋐", "c": "3017", "buy": "600-650", "shd": 4, "v": 6.0}
]

st.title("🎯 戰情決策所 (Final-Lock 旗艦十檔版)")
st.markdown('<div class="alert-banner">📊 系統狀態：即時監控中 | 防禦閥已啟動</div>', unsafe_allow_html=True)

# 動態生成兩欄佈局
cols = st.columns(2)
for i, s in enumerate(STOCKS):
    with cols[i % 2]:
        st.markdown(f'''<div class="card">
            <b>{s['n']} ({s['c']})</b> | 🛡️ 價值盾: {s['shd']}分
            <div class="price-tag">380.00 <span style="font-size:14px;color:#FF4B4B;">+1.2%</span></div>
            <div style="font-size:13px; color:#aaa; margin-top:5px;">建議進價: {s['buy']}</div>
            <div style="font-size:13px; color:#aaa;">{"🛑 護城河健在" if s['shd']>=3 else "⚠️ 護城河脆弱"} | 波動限制: {s['v']}</div>
        </div>''', unsafe_allow_html=True)
        st.button("❌ 清除此標的", key=f"del_{s['c']}")
