import streamlit as st
import requests as req

st.set_page_config(page_title="戰情決策所 - Final-Lock 旗艦版", layout="wide")

# CSS: 精準還原旗艦版視覺
st.markdown('''<style>
.stApp { background-color: #0b0c0f !important; color: #fff !important; }
.card { background:#16191f; border-radius:8px; padding:20px; margin-bottom:15px; border: 1px solid #333; }
.price-tag { font-size:28px; font-weight:bold; color:#FFB300; }
.gain-tag { font-size:14px; color:#FF4B4B; }
</style>''', unsafe_allow_html=True)

# 抓取即時數據函數
@st.cache_data(ttl=60)
def get_price(code):
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{code}.TW"
        r = req.get(url, headers={'User-Agent':'Mozilla/5.0'}).json()
        meta = r["chart"]["result"][0]["meta"]
        price = meta["regularMarketPrice"]
        prev = meta["previousClose"]
        gain = ((price - prev) / prev) * 100
        return f"{price:.2f}", f"{gain:+.2f}%"
    except: return "0.00", "0.00%"

# 戰術資料庫
STOCKS = [
    {"n": "緯創", "c": "3231", "buy": "350-370", "shd": 4, "cost": 378.0},
    {"n": "鴻海", "c": "2317", "buy": "180-195", "shd": 5, "cost": 175.0},
    {"n": "長榮航", "c": "2618", "buy": "30-35", "shd": 3, "cost": 32.0},
    {"n": "燿華", "c": "2367", "buy": "25-28", "shd": 2, "cost": 26.5},
    {"n": "富邦媒", "c": "8454", "buy": "380-410", "shd": 5, "cost": 390.0}
]

st.title("🎯 戰情決策所 (Final-Lock 旗艦版)")

# 核心顯示邏輯
for s in STOCKS:
    price, gain = get_price(s['c'])
    st.markdown(f'''<div class="card">
        <div style="font-size:16px;"><b>{s['n']} ({s['c']})</b> | 🛡️ 價值盾: {s['shd']}分</div>
        <div class="price-tag">{price} <span class="gain-tag">{gain}</span></div>
        <div style="font-size:14px; color:#aaa; margin-bottom:10px;">建議進價區間: {s['buy']}</div>
        <div style="border-top: 1px solid #333; padding-top: 10px; font-size:14px;">
            主力成本: {s['cost']} | 💰 <b>淨損益: +5,200元</b>
        </div>
    </div>''', unsafe_allow_html=True)
    
    if st.button(f"❌ 清除 {s['n']}", key=f"del_{s['c']}", use_container_width=True):
        st.rerun()
