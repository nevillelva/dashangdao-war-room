import streamlit as st
import requests as req

st.set_page_config(page_title="戰情決策所 - Flagship v29", layout="wide")

# v29 經典戰鬥視覺 CSS
st.markdown('''<style>
.stApp { background-color: #0b0c0f !important; color: #fff !important; }
.card { background:#16191f; border-radius:6px; padding:15px; margin-bottom:10px; border-left: 5px solid #333; }
.s-trigger { border-left-color: #FFB300 !important; }
.price-tag { font-size:20px; font-weight:bold; color:#FFB300; }
.alert-text { color:#FF4B4B; font-weight:bold; }
</style>''', unsafe_allow_html=True)

@st.cache_data(ttl=60)
def get_live_data(c):
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{c}.TW"
        r = req.get(url, headers={'User-Agent':'Mozilla/5.0'}, timeout=5).json()
        meta = r["chart"]["result"][0]["meta"]
        price = meta["regularMarketPrice"]
        prev = meta["previousClose"]
        gain = ((price - prev) / prev) * 100
        return price, gain
    except: return 0.0, 0.0

# 戰術資料庫
STOCKS = [
    {"n": "緯創", "c": "3231", "buy": "350-370", "shd": 4, "cost": 378.0},
    {"n": "鴻海", "c": "2317", "buy": "180-195", "shd": 5, "cost": 175.0},
    {"n": "長榮航", "c": "2618", "buy": "30-35", "shd": 3, "cost": 32.0},
    {"n": "燿華", "c": "2367", "buy": "25-28", "shd": 2, "cost": 26.5},
    {"n": "富邦媒", "c": "8454", "buy": "380-410", "shd": 5, "cost": 390.0}
]

st.title("🎯 戰情決策所 (v29 旗艦版)")

for s in STOCKS:
    price, gain = get_live_data(s['c'])
    pnl = (price - s['cost']) * 1000 # 假設單位為張
    
    st.markdown(f'''<div class="card s-trigger">
        <div style="display:flex; justify-content:space-between;">
            <b>{s['n']} ({s['c']})</b> <span>🛡️ 價值盾: {s['shd']}</span>
        </div>
        <div style="margin:8px 0;">現價: <span class="price-tag">{price:.2f}</span> ({gain:+.2f}%)</div>
        <div style="font-size:13px; color:#aaa;">
            主力成本: {s['cost']} | 💰 淨損益: <span class="{'alert-text' if pnl < 0 else ''}">{pnl:,.0f}元</span>
        </div>
        <div style="font-size:13px; color:#aaa;">錨定區間: {s['buy']}</div>
    </div>''', unsafe_allow_html=True)
    if st.button(f"❌ 一鍵清空 {s['n']}", key=f"del_{s['c']}"): st.rerun()
