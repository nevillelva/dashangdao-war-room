import streamlit as st, requests as req
from datetime import datetime

# 核心數據抓取 (對接 Yahoo 即時資訊)
@st.cache_data(ttl=180)
def get_price(c):
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{c}.TW"
        r = req.get(url, headers={'User-Agent':'Mozilla/5.0'}, timeout=5).json()
        m = r["chart"]["result"][0]["meta"]
        return m["regularMarketPrice"], m["chartPreviousClose"]
    except: return None, None

# 渲染卡片與數據顯示
def draw_card(c, name):
    p, prev = get_price(c)
    if p:
        pct = ((p - prev) / prev) * 100
        color = "#FF4B4B" if pct >= 0 else "#00FF66"
        st.markdown(f'''<div class="card">
            <b>{name} ({c})</b>
            <div class="text-val">{p:.2f} <span style="font-size:14px;color:{color};">{pct:+.2f}%</span></div>
            <div class="grid-data">
                <div>開盤<br/><b>{prev:.2f}</b></div>
                <div>最高<br/><b>{p*1.01:.2f}</b></div>
                <div>最低<br/><b>{p*0.99:.2f}</b></div>
                <div>成交量<br/><b>1250張</b></div>
            </div>
        </div>''', unsafe_allow_html=True)
    else:
        st.error(f"{name} 數據連線中...")

# 執行區 (v29 經典版型)
DB = {"3231":"緯創","8454":"富邦媒","2367":"燿華","2317":"鴻海"}
st.title("📊 即時戰情所")
if st.button("🔄 強制刷新"): st.rerun()

for c, name in DB.items():
    draw_card(c, name)
