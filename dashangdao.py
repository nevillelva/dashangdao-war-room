import streamlit as st
import requests

st.set_page_config(layout="wide")

# 1. 完整的產業智能資料庫 (內建價值盾與指標)
DATA_BANK = {
    "科技硬體": {"3231": {"n": "緯創", "buy": "350-370", "shd": 4}, "2317": {"n": "鴻海", "buy": "180-195", "shd": 5}},
    "金融動能": {"8454": {"n": "富邦媒", "buy": "380-410", "shd": 5}},
    "航空旅遊": {"2618": {"n": "長榮航", "buy": "30-35", "shd": 3}}
}

# 2. 真實數據抓取 (即時 API)
@st.cache_data(ttl=300)
def get_live_price(code):
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{code}.TW"
        r = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}).json()
        meta = r["chart"]["result"][0]["meta"]
        return meta["regularMarketPrice"], meta["previousClose"], meta["chartPreviousClose"]
    except: return 0.0, 0.0, 0.0

# 3. 控制台與產業邏輯
with st.sidebar:
    st.markdown("### 🛠️ 指揮官控制台")
    selected_industry = st.selectbox("產業週期偵測", list(DATA_BANK.keys()))
    st.write(f"系統已載入 {selected_industry} 篩選邏輯")

st.title("🎯 戰情決策所 (實戰版)")

# 4. 渲染卡片 (自動帶入真實數據)
stocks = DATA_BANK[selected_industry]
cols = st.columns(2)

for i, (code, info) in enumerate(stocks.items()):
    price, prev, _ = get_live_price(code)
    gain = ((price - prev) / prev) * 100 if prev else 0
    
    with cols[i % 2]:
        st.markdown(f'''<div style="background:#16191f; padding:20px; border:2px solid #333; border-radius:8px;">
            <b>{info['n']} ({code})</b> | 🛡️ 價值盾: {info['shd']}
            <div style="font-size:28px; font-weight:bold; color:#FFB300;">{price:.2f} 
            <span style="font-size:16px; color:{'#FF4B4B' if gain>=0 else '#00FF00'};">{gain:+.2f}%</span></div>
            <div style="font-size:13px; color:#aaa;">錨定區間: {info['buy']}</div>
        </div>''', unsafe_allow_html=True)
        st.button(f"❌ 清除 {info['n']}", key=f"del_{code}")
