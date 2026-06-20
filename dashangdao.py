import streamlit as st
from datetime import datetime

# 系統封裝版 v1.0 - 戰情決策所 (十檔核心監控)
st.set_page_config(page_title="戰情決策所 - Final-Lock", layout="wide")

#  CSS 防禦矩陣
st.markdown('''<style>
.stApp { background-color: #0b0c0f !important; color: #fff !important; }
.card { background:#16191f; border-radius:6px; padding:15px; margin-bottom:10px; border-left: 5px solid #333; }
.alert-risk { border-left-color: #FF4B4B !important; }
.s-trigger { border-left-color: #FFB300 !important; }
.v-high { border-left-color: #00FF66 !important; }
</style>''', unsafe_allow_html=True)

# 【戰術庫】最新十檔標的配置
STOCKS = {
    "3231": {"n": "緯創", "cyc": [7, 8], "shd": 4, "v": 5.0},
    "2317": {"n": "鴻海", "cyc": [10, 11], "shd": 5, "v": 3.0},
    "2618": {"n": "長榮航", "cyc": [6, 7], "shd": 3, "v": 4.0},
    "2367": {"n": "燿華", "cyc": [10, 11], "shd": 2, "v": 6.0},
    "8454": {"n": "富邦媒", "cyc": [11, 12], "shd": 5, "v": 3.5},
    "2449": {"n": "京元電", "cyc": [5, 6], "shd": 4, "v": 4.5},
    "2330": {"n": "台積電", "cyc": [1, 12], "shd": 5, "v": 2.5},
    "2303": {"n": "聯電", "cyc": [9, 10], "shd": 3, "v": 4.0},
    "2382": {"n": "廣達", "cyc": [7, 8], "shd": 4, "v": 5.0},
    "3017": {"n": "奇鋐", "cyc": [8, 9], "shd": 4, "v": 6.0}
}

st.title("🎯 戰情決策所 (Final-Lock)")

for c, s in STOCKS.items():
    # 模擬數值 (實際連線後將自動變動)
    p, vol, risk = 380.0, 4.2, False
    is_sea = datetime.now().month in s["cyc"]
    
    cls = "card " + ("alert-risk" if risk else ("s-trigger" if is_sea else "v-high"))
    
    st.markdown(f'''<div class="{cls}">
        <b>{s["n"]} ({c})</b> | 🛡️價值盾: {s["shd"]}
        <br>{"🚀 季節進場窗" if is_sea else "🛡️ 穩定防禦中"} | {"🛑 護城河健在" if s["shd"]>=3 else "⚠️ 護城河脆弱"}
    </div>''', unsafe_allow_html=True)
