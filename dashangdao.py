import streamlit as st
import yfinance as yf
from datetime import datetime, timedelta

st.set_page_config(layout="wide", page_title="大商道 14.1 戰情室")

# 讀取超級網址參數 (格式: 代碼:價值盾:主力成本)
params = st.query_params
main_raw = params.get("main", "2330:5:850,2317:4:170,2382:5:280,3231:4:145,1519:5:800").split(",")
sub_raw = params.get("sub", "2881:4:70,2884:4:28,2603:3:180,2618:4:35,3481:2:13").split(",")

# 專屬台股中文翻譯庫
TW_STOCKS = {
    "2330": "台積電", "2317": "鴻海", "2382": "廣達", "3231": "緯創", "1519": "華城",
    "2881": "富邦金", "2884": "玉山金", "2603": "長榮", "2618": "長榮航", "3481": "群創",
    "8454": "富邦媒", "2454": "聯發科", "3008": "大立光", "2609": "陽明", "2615": "萬海"
}

@st.cache_data(ttl=120)
def calculate_tactical_signals(symbol_data):
    try:
        # 解析分析師(AI)給的參數
        parts = symbol_data.split(":")
        symbol = parts[0]
        # 如果有指定價值盾與成本就用指定的，沒有就預設 None 讓系統自己算
        override_shd = int(parts[1]) if len(parts) > 1 else None
        override_cost = float(parts[2]) if len(parts) > 2 else None

        ticker = yf.Ticker(f"{symbol}.TW")
        hist = ticker.history(period="6mo")
        if hist.empty: return None

        stock_name = TW_STOCKS.get(symbol, f"代號 {symbol}")
        current_price = hist['Close'].iloc[-1]
        prev_close = hist['Close'].iloc[-2]
        gain = ((current_price - prev_close) / prev_close) * 100
        
        open_p = hist['Open'].iloc[-1]
        high_p = hist['High'].iloc[-1]
        low_p = hist['Low'].iloc[-1]
        vol = int(hist['Volume'].iloc[-1] / 1000)

        ma20 = hist['Close'].rolling(window=20).mean().iloc[-1]
        ma60 = hist['Close'].rolling(window=60).mean().iloc[-1]

        # 優先使用分析師給的主力成本，若無則用季線
        main_cost = override_cost if override_cost else round(ma60, 1)
        buy_low = round(ma20 * 0.98, 1)
        buy_high = round(ma20 * 1.02, 1)
        buy_zone = f"{buy_low} - {buy_high}"

        diff_from_cost = ((current_price - main_cost) / main_cost) * 100
        diff_from_ma20 = ((current_price - ma20) / ma20) * 100

        # 訊號判斷 (基於分析師給的成本)
        if diff_from_cost <= -3.0:
            signal_text = f"破底邊緣 {abs(diff_from_cost):.1f}%！(警報🚨)"
            color_border = "#e74c3c"
        elif diff_from_ma20 >= 5.0:
            signal_text = "強勢多頭排列 🔥"
            color_border = "#e67e22"
        else:
            signal_text = "量縮築底區 (打擊區) 🛡️"
            color_border = "#2ecc71"

        # 優先使用分析師給的價值盾，若無則用技術面評分
        shd_score = override_shd if override_shd else (2 if diff_from_cost <= -3.0 else (5 if diff_from_ma20 >= 5.0 else 4))

        today = datetime.now()
        target_date = today.replace(day=10) if today.day <= 10 else (today.replace(day=28) + timedelta(days=4)).replace(day=10)
        days_left = (target_date - today).days

        return {
            "name": stock_name, "code": symbol, "price": current_price, "gain": gain, 
            "open": open_p, "high": high_p, "low": low_p, "vol": vol,
            "cost": main_cost, "buy_zone": buy_zone, "signal": signal_text, 
            "cycle": f"營收公告倒數：{days_left} 天 ⏳", "color": color_border, "shd": shd_score
        }
    except Exception:
        return None

def calc_real_profit(cost, price, qty):
    if cost <= 0: return 0, 0
    buy_val = cost * qty * 1000
    sell_val = price * qty * 1000
    fee_buy = max(20, buy_val * 0.001425)
    fee_sell = max(20, sell_val * 0.001425)
    tax = sell_val * 0.003
    profit = sell_val - buy_val - fee_buy - fee_sell - tax
    return profit, (profit/buy_val)*100

# ==========================================
# 介面渲染
# ==========================================
st.markdown('''<style>
.stApp { background-color: #0b0c0f !important; color: #fff !important; }
div.stButton > button[kind="primary"] { background-color: #3498db !important; color: white !important; border: none !important; font-weight:bold; height: 45px; font-size: 16px;}
</style>''', unsafe_allow_html=True)

st.markdown("<h1 style='font-size:28px; font-weight:bold;'>🦅 大商道 14.1 戰情室</h1>", unsafe_allow_html=True)

if st.button("🔄 閃電刷新真實數據", type="primary", use_container_width=True): 
    st.cache_data.clear()
    st.rerun()

INDUSTRY_DB = {
    "🔥 核心精選主將": [c.strip() for c in main_raw if c.strip()],
    "🎯 短中期轉折觀察": [c.strip() for c in sub_raw if c.strip()]
}

for category, raw_codes in INDUSTRY_DB.items():
    if not raw_codes: continue
    st.markdown(f"<h3 style='color:#FFB300; margin-top:30px;'>{category}</h3>", unsafe_allow_html=True)
    cols = st.columns(2)
    
    for i, symbol_data in enumerate(raw_codes):
        d = calculate_tactical_signals(symbol_data)
        if not d: continue
        
        gain_color = "#ff4d4d" if d['gain'] > 0 else "#00FF00"
        
        with cols[i % 2]:
            html_card = f"""<div style="border: 2px solid {d['color']}; border-radius: 8px; padding: 15px; background-color: #16191f; margin-bottom: 5px;">
<div style="font-size:16px; font-weight:bold; margin-bottom:10px;">{d['name']} ({d['code']}) | 🛡️ 價值盾: {d['shd']}分</div>
<div style="font-size:36px; font-weight:bold; color:#fff;">{d['price']:.2f} <span style="font-size:18px; color:{gain_color};">{d['gain']:+.1f}%</span></div>
<div style="background:#2b2b36; border-radius:5px; padding:10px; display:flex; justify-content:space-between; text-align:center; margin-top:15px; margin-bottom:15px;">
<div style="flex:1; color:#aaa; font-size:12px;">開盤<br><span style="color:#fff; font-size:15px; font-weight:bold;">{d['open']:.1f}</span></div>
<div style="flex:1; color:#aaa; font-size:12px;">最高<br><span style="color:#fff; font-size:15px; font-weight:bold;">{d['high']:.1f}</span></div>
<div style="flex:1; color:#aaa; font-size:12px;">最低<br><span style="color:#fff; font-size:15px; font-weight:bold;">{d['low']:.1f}</span></div>
<div style="flex:1; color:#aaa; font-size:12px;">總量<br><span style="color:#fff; font-size:15px; font-weight:bold;">{d['vol']}張</span></div>
</div>
<div style="background:#3a1515; color:#ff4d4d; font-size:18px; font-weight:bold; text-align:center; padding:10px; border-radius:5px; border:1px solid #ff4d4d; margin-bottom:15px;">🎯 參考區間: [ {d['buy_zone']} ]</div>
<div style="font-size:13px; color:#ddd; line-height:1.8;">
👥 幕僚精算主力: {d['cost']} | 🛡️ {d['signal']}<br>
🚀 {d['cycle']}<br>
</div>
</div>"""
            st.markdown(html_card, unsafe_allow_html=True)
            
            with st.expander(f"💼 砸密碼：實戰風控 ({d['name']})"):
                c1, c2 = st.columns(2)
                sim_cost = c1.number_input("進場價", value=float(d['cost']), key=f"c_{d['code']}")
                sim_qty = c2.number_input("張數", value=1.0, key=f"q_{d['code']}")
                sim_profit, sim_roi = calc_real_profit(sim_cost, d['price'], sim_qty)
                p_color = '#ff4d4d' if sim_profit > 0 else '#00FF00'
                st.markdown(f"<div style='background:#000; padding:10px; border-radius:5px;'>💰 實戰盈虧: <strong style='color:{p_color}; font-size:16px;'>{sim_profit:+,.0f} 元 ({sim_roi:+.2f}%)</strong></div>", unsafe_allow_html=True)
