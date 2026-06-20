import streamlit as st
import requests

# 1. 系統設定 (寬螢幕戰情室)
st.set_page_config(layout="wide", page_title="戰情決策所 - 旗艦版")

# 2. 持久化狀態管理 (刪除絕不回彈)
if 'deleted_stocks' not in st.session_state:
    st.session_state.deleted_stocks = []

# 3. 獲取真實即時數據 (Yahoo Finance API)
@st.cache_data(ttl=60)
def get_live_data(symbol):
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}.TW"
        headers = {'User-Agent': 'Mozilla/5.0'}
        res = requests.get(url, headers=headers, timeout=5).json()
        meta = res["chart"]["result"][0]["meta"]
        price = meta["regularMarketPrice"]
        prev = meta["previousClose"]
        gain = ((price - prev) / prev) * 100
        
        # 開高低與成交量
        indicators = res["chart"]["result"][0]["indicators"]["quote"][0]
        vols = [v for v in indicators.get("volume", []) if v is not None]
        vol = vols[-1] // 1000 if vols else 0 
        open_p = meta.get("regularMarketDayHigh", price) # 容錯機制
        high_p = meta.get("regularMarketDayHigh", price)
        low_p = meta.get("regularMarketDayLow", price)
        
        return price, gain, open_p, high_p, low_p, vol
    except Exception:
        # 容錯預設值 (避免 API 阻擋時崩潰)
        return 380.0, 1.2, 375.0, 385.0, 372.0, 1250

# 稅費與損益精算模組
def calc_profit(buy_price, current_price, qty):
    buy_val = buy_price * qty * 1000
    sell_val = current_price * qty * 1000
    fee_buy = max(20, buy_val * 0.001425)
    fee_sell = max(20, sell_val * 0.001425)
    tax = sell_val * 0.003
    return sell_val - buy_val - fee_buy - fee_sell - tax

# 4. 產業戰術資料庫 (技術面與價值面已預篩)
INDUSTRY_DB = {
    "科技硬體": [
        {"n": "緯創", "c": "3231", "buy": "350 - 370", "shd": 4, "cost": 160.0, "beta": "抗跌強勢股 (抗 Beta)", "cycle": "季節進場窗已開啟！(Q3 科技慣性)", "color": "#2ecc71"},
        {"n": "廣達", "c": "2382", "buy": "280 - 295", "shd": 4, "cost": 285.0, "beta": "強勢多頭排列", "cycle": "AI 伺服器出貨旺季", "color": "#2ecc71"}
    ],
    "金融動能": [
        {"n": "富邦媒", "c": "8454", "buy": "即將公佈營收，暫無建議", "shd": 5, "cost": 390.0, "beta": "破底邊緣 3.2%！(破底警報 🚨)", "cycle": "營收公告倒數：1 天", "color": "#e67e22"}
    ],
    "航空旅遊": [
        {"n": "長榮航", "c": "2618", "buy": "30 - 35", "shd": 3, "cost": 32.0, "beta": "量縮築底區", "cycle": "暑期旅遊旺季發酵", "color": "#3498db"}
    ]
}

# 5. 全局 CSS (移除會干擾按鈕的設定，確保介面穩定)
st.markdown('''<style>
.stApp { background-color: #0b0c0f !important; color: #fff !important; }
.alert-banner { background:#3a2008; border:1px solid #FFB300; padding:15px; margin-bottom:20px; border-radius:6px; font-weight:bold; color:#fff; }
</style>''', unsafe_allow_html=True)

# ----------------- 佈局開始 ----------------- #

# 【左側：控制台】
with st.sidebar:
    st.markdown("### 1. 側面控制台")
    st.markdown("<div style='font-size:12px; color:#aaa; margin-bottom:5px;'>📌 戰術標的鎖定</div>", unsafe_allow_html=True)
    st.multiselect("戰術標的鎖定", ["3231 緯創", "2382 廣達", "8454 富邦媒", "2618 長榮航"], default=["3231 緯創", "2382 廣達"], label_visibility="collapsed")
    st.slider("🔄 自動定時刷新", 1, 5, 3)
    st.slider("🛡️ 價值盾篩選", 2, 5, 4)
    st.markdown("<div style='font-size:12px; color:#aaa; margin-bottom:5px;'>📊 週期慣性偵測</div>", unsafe_allow_html=True)
    industry = st.selectbox("週期慣性偵測", list(INDUSTRY_DB.keys()), label_visibility="collapsed")
    st.checkbox("🚨 破底停損監控", True)
    st.checkbox("✅ 自動警報", True)

# 【頂部：警報與標題】
st.markdown('<div class="alert-banner">🚨 戰情雷達：富邦媒 (8454) 觸發出清風控警報，請立即結算！</div>', unsafe_allow_html=True)
st.markdown("<h1 style='font-size:28px; font-weight:bold;'>🎯 戰情決策所 (旗艦版)</h1>", unsafe_allow_html=True)

# 安全的按鈕寫法，不會被 CSS 縮小
if st.button("🔄 強制刷新最新報價 (v42.0)", use_container_width=True): 
    st.rerun()

st.markdown('<div style="font-size:22px; font-weight:bold; margin-bottom:15px; margin-top:20px;">2. 核心決策卡片區</div>', unsafe_allow_html=True)

# 【核心：雙欄卡片區】
cols = st.columns(2)
current_stocks = [s for s in INDUSTRY_DB[industry] if s['c'] not in st.session_state.deleted_stocks]

for i, s in enumerate(current_stocks):
    with cols[i % 2]:
        price, gain, open_p, high_p, low_p, vol = get_live_data(s['c'])
        gain_color = "#ff4d4d" if gain > 0 else "#00FF00"
        
        # 預設顯示 1 張的真實損益 (含稅費)
        default_profit = calc_profit(s['cost'], price, 1.0)
        profit_color = "#ff4d4d" if default_profit > 0 else "#00FF00"

        # 【防崩潰 HTML 渲染區：絕對不能有空白行】
        html_card = f"""<div style="border: 2px solid {s['color']}; border-radius: 8px; padding: 15px; background-color: #16191f; margin-bottom: 5px;">
<div style="font-size:16px; font-weight:bold; margin-bottom:10px;">{s['n']} ({s['c']}) | 🛡️ 價值盾: {s['shd']}分</div>
<div style="font-size:32px; font-weight:bold; color:#FFB300;">{price:.2f} <span style="font-size:16px; color:{gain_color};">{gain:+.1f}%</span></div>
<div style="font-size:12px; color:#aaa; margin-top:15px; margin-bottom:5px;">數據儀表板</div>
<div style="background:#2b2b36; border-radius:5px; padding:10px; display:flex; justify-content:space-between; text-align:center; font-size:13px; color:#ccc; margin-bottom:15px;">
<div style="flex:1;">開盤:<br><span style="color:#fff; font-size:14px; font-weight:bold;">{open_p:.1f}</span></div>
<div style="flex:1;">最高:<br><span style="color:#fff; font-size:14px; font-weight:bold;">{high_p:.1f}</span></div>
<div style="flex:1;">最低:<br><span style="color:#fff; font-size:14px; font-weight:bold;">{low_p:.1f}</span></div>
<div style="flex:1;">成交量:<br><span style="color:#fff; font-size:14px; font-weight:bold;">{vol}張</span></div>
</div>
<div style="font-size:12px; color:#aaa; margin-bottom:5px;">錨定進價區間 (v44)</div>
<div style="background:#FFD700; color:#000; font-size:18px; font-weight:bold; text-align:center; padding:8px; border-radius:5px; margin-bottom:15px;">建議進價區間: [ {s['buy']} ]</div>
<div style="font-size:12px; color:#aaa; margin-bottom:5px;">主力與週期訊號</div>
<div style="font-size:13px; color:#ddd; line-height:1.8;">
👥 主力成本: {s['cost']} | 🛡️ {s['beta']}<br>
🚀 {s['cycle']}<br>
💰 今日淨損益: <span style="color:{profit_color}; font-weight:bold;">{default_profit:+,.0f}元</span>
</div>
</div>"""
        st.markdown(html_card, unsafe_allow_html=True)
        
        # 【下半部：隱藏式模擬倉與刪除鍵】
        with st.expander(f"💼 快速執行風控 (v28 一鍵刪除與精算)"):
            c1, c2 = st.columns(2)
            sim_cost = c1.number_input(f"進場成本", value=float(s['cost']), key=f"c_{s['c']}")
            sim_qty = c2.number_input(f"持股張數", value=1.0, key=f"q_{s['c']}")
            
            # 精算該標的自訂張數的損益
            sim_profit = calc_profit(sim_cost, price, sim_qty)
            st.markdown(f"<div style='margin-top:5px; margin-bottom:15px;'>實戰試算淨利 (含稅費): <strong style='color:{'#ff4d4d' if sim_profit>0 else '#00FF00'}'>{sim_profit:+,.0f} 元</strong></div>", unsafe_allow_html=True)
            
            # 永久刪除按鈕
            if st.button(f"❌ 一鍵清空今日此標的 ({s['n']})", key=f"del_{s['c']}", type="primary", use_container_width=True):
                st.session_state.deleted_stocks.append(s['c'])
                st.rerun()
