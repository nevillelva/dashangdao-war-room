import streamlit as st

st.set_page_config(layout="wide", page_title="戰情決策所 - 指揮中心")

# CSS 樣式
st.markdown('''<style>
.stApp { background-color: #0b0c0f !important; color: #fff !important; }
.card { background:#16191f; border-radius:8px; padding:20px; margin-bottom:15px; border: 2px solid #333; }
.alert-banner { background:#4a2b0f; border:1px solid #FFB300; padding:15px; margin-bottom:20px; border-radius:6px; }
.price-tag { font-size:28px; font-weight:bold; color:#FFB300; }
.data-box { background:#262730; padding:10px; border-radius:5px; font-size:13px; margin:10px 0; border: 1px solid #444; }
div.stButton > button { background-color: #FF4B4B !important; color: white !important; font-weight: bold; width: 100%; border: none; }
</style>''', unsafe_allow_html=True)

# 狀態初始化
if 'deleted_codes' not in st.session_state: st.session_state.deleted_codes = []

# 完整定義產業資料庫 (補齊所有參數，防止 NameError)
INDUSTRY_DATA = {
    "科技硬體": [
        {"n": "緯創", "c": "3231", "buy": "350-370", "shd": 4, "open": 375, "high": 385, "low": 372, "vol": 1250, "cost": 378.0},
        {"n": "鴻海", "c": "2317", "buy": "180-195", "shd": 5, "open": 182, "high": 190, "low": 181, "vol": 3500, "cost": 175.0}
    ],
    "航空旅遊": [
        {"n": "長榮航", "c": "2618", "buy": "30-35", "shd": 3, "open": 32, "high": 34, "low": 31, "vol": 800, "cost": 32.0}
    ],
    "金融動能": [
        {"n": "富邦媒", "c": "8454", "buy": "380-410", "shd": 5, "open": 390, "high": 405, "low": 385, "vol": 450, "cost": 390.0}
    ]
}

# 控制台與邏輯渲染
with st.sidebar:
    industry = st.selectbox("產業週期偵測", list(INDUSTRY_DATA.keys()))

st.markdown('<div class="alert-banner">🚨 戰情雷達：多標的監控中</div>', unsafe_allow_html=True)
st.title("🎯 戰情決策所 (終極旗艦版)")

cols = st.columns(2)
stocks = [s for s in INDUSTRY_DATA[industry] if s['c'] not in st.session_state.deleted_codes]

for i, s in enumerate(stocks):
    with cols[i % 2]:
        st.markdown(f'''<div class="card">
            <b>{s['n']} ({s['c']})</b> | 🛡️ 價值盾: {s['shd']}分
            <div class="price-tag">380.00 <span style="font-size:16px; color:#FF4B4B;">+1.2%</span></div>
            <div class="data-box">開盤: {s['open']} | 最高: {s['high']} | 最低: {s['low']} | 量: {s['vol']}張</div>
            <b>錨定進價區間: [ {s['buy']} ]</b>
        </div>''', unsafe_allow_html=True)
        
        with st.expander("💼 實戰模擬持倉精算 (含稅費)"):
            cost = st.number_input(f"成本 {s['c']}", value=float(s['cost']), key=f"cost_{s['c']}")
            qty = st.number_input(f"張數 {s['c']}", value=1.0, key=f"qty_{s['c']}")
            
            # 計算邏輯 (含手續費與稅)
            buy_val = cost * qty * 1000
            sell_val = 380.0 * qty * 1000
            profit = sell_val - buy_val - max(20, buy_val*0.001425) - max(20, sell_val*0.001425) - (sell_val*0.003)
            st.write(f"💰 實戰淨利: {profit:,.0f} 元 | 📈 報酬率: {(profit/buy_val)*100:.2f}%")
        
        if st.button(f"❌ 一鍵清空今日標的", key=f"del_{s['c']}"):
            st.session_state.deleted_codes.append(s['c'])
            st.rerun()
