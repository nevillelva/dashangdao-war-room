import streamlit as st

st.set_page_config(page_title="戰情決策所 - 旗艦版", layout="wide")

# CSS: 打造資訊一目了然的旗艦佈局
st.markdown('''<style>
.stApp { background-color: #0b0c0f !important; color: #fff !important; }
.card { background:#16191f; border-radius:8px; padding:20px; margin-bottom:15px; border: 1px solid #333; }
.price-tag { font-size:28px; font-weight:bold; color:#FFB300; }
</style>''', unsafe_allow_html=True)

# 系統自帶的戰術資料庫 (備用)
DEFAULT_DB = {
    "3231": {"n": "緯創", "buy": "350-370", "shd": 4, "cost": 378.0},
    "2317": {"n": "鴻海", "buy": "180-195", "shd": 5, "cost": 175.0},
    "2618": {"n": "長榮航", "buy": "30-35", "shd": 3, "cost": 32.0},
    "2367": {"n": "燿華", "buy": "25-28", "shd": 2, "cost": 26.5},
    "8454": {"n": "富邦媒", "buy": "380-410", "shd": 5, "cost": 390.0},
    "2449": {"n": "京元電", "buy": "120-130", "shd": 4, "cost": 125.0},
    "2330": {"n": "台積電", "buy": "850-900", "shd": 5, "cost": 880.0},
    "2303": {"n": "聯電", "buy": "50-55", "shd": 3, "cost": 52.0},
    "2382": {"n": "廣達", "buy": "280-300", "shd": 4, "cost": 290.0},
    "3017": {"n": "奇鋐", "buy": "600-650", "shd": 4, "cost": 620.0}
}

# 抓取網址中的標的參數
params = st.query_params
# 若網址沒帶參數，預設顯示前 10 檔
target_stocks = params.get("stocks", "3231,2317,2618,2367,8454,2449,2330,2303,2382,3017").split(",")

st.title("🎯 戰情決策所 (動態更新版)")

for code in target_stocks:
    s = DEFAULT_DB.get(code)
    if s:
        st.markdown(f'''<div class="card">
            <div style="font-size:16px;"><b>{s['n']} ({code})</b> | 🛡️ 價值盾: {s['shd']}分</div>
            <div class="price-tag">380.00 <span style="font-size:14px;color:#FF4B4B;">+1.2%</span></div>
            <div style="font-size:14px; color:#aaa; margin-bottom:10px;">建議進價區間: {s['buy']}</div>
            <div style="border-top: 1px solid #333; padding-top: 10px; font-size:14px;">
                主力成本: {s['cost']} | 💰 <b>淨損益: +5,200元</b>
            </div>
        </div>''', unsafe_allow_html=True)
        if st.button(f"❌ 清除 {s['n']}", key=f"del_{code}", use_container_width=True):
            st.rerun()
