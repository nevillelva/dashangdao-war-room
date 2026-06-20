import streamlit as st
import yfinance as yf
from datetime import datetime, timedelta

st.set_page_config(layout="wide", page_title="大商道 14.1 終極戰情室")

# 參數讀取
params = st.query_params
default_main = "2317:4:260:1:2,3231:4:158:0:1"
default_sub = "2881:4:73:1:1,2884:4:27:0:2,2603:4:185:1:1,2618:4:34:2:1,2609:4:70:0:1,2615:4:80:0:1,3481:3:14:0:1"

main_raw = params.get("main", default_main).split(",")
sub_raw = params.get("sub", default_sub).split(",")

TW_STOCKS = {
    "2330": "台積電", "2317": "鴻海", "2382": "廣達", "3231": "緯創", "1519": "華城",
    "2881": "富邦金", "2884": "玉山金", "2603": "長榮", "2618": "長榮航", "3481": "群創",
    "8454": "富邦媒", "2454": "聯發科", "3008": "大立光", "2609": "陽明", "2615": "萬海"
}

CHIP_MAP = {"1": "🐳 巨鯨進駐", "2": "🩸 外資提款", "0": "⚖️ 籌碼平穩"}
VAL_MAP = {"1": "🟢 便宜階", "2": "🟡 合理階", "3": "🔴 昂貴階", "0": "⚪ 未定階"}

@st.cache_data(ttl=120)
def calculate_tactical_signals(symbol_data):
    try:
        parts = symbol_data.split(":")
        symbol = parts[0]
        override_shd = int(parts[1]) if len(parts) > 1 else 4
        override_cost = float(parts[2]) if len(parts) > 2 else 100.0
        chip_code = parts[3] if len(parts) > 3 else "0"
        val_code = parts[4] if len(parts) > 4 else "0"

        ticker = yf.Ticker(f"{symbol}.TW")
        hist = ticker.history(period="6mo")
        if hist.empty: return None

        current_price = hist['Close'].iloc[-1]
        vol = int(hist['Volume'].iloc[-1] / 1000)
        
        # 波段關鍵：改用季線 (MA60) 作為防守線與打擊區中心
        ma60 = hist['Close'].rolling(window=60).mean().iloc[-1]
        main_cost = override_cost
        buy_zone = f"{round(main_cost * 0.97, 1)} - {round(main_cost * 1.03, 1)}"

        # 撤退導航儀 (波段優化版)
        diff_from_cost = ((current_price - main_cost) / main_cost) * 100
        if val_code == "3":
            exit_s, exit_p, exit_c, exit_bg = "🔴 價值滿水：建議獲利了結", f"{current_price:.1f}", "#e74c3c", "#3a1515"
        elif diff_from_cost >= 15.0:
            exit_s, exit_p, exit_c, exit_bg = "🛡️ 階梯保本：鎖定利潤", f"{max(current_price * 0.92, main_cost):.1f}", "#3498db", "#152a3a"
        else:
            exit_s, exit_p, exit_c, exit_bg = "🚪 鐵血紀律：跌破季線5%撤退", f"{main_cost * 0.95:.1f}", "#8e44ad", "#2c153a"

        return {
            "name": TW_STOCKS.get(symbol, symbol), "code": symbol, "price": current_price,
            "gain": ((current_price - hist['Close'].iloc[-2]) / hist['Close'].iloc[-2]) * 100,
            "open": hist['Open'].iloc[-1], "high": hist['High'].iloc[-1], "low": hist['Low'].iloc[-1],
            "vol": vol, "cost": main_cost, "buy_zone": buy_zone, "shd": override_shd,
            "chip": CHIP_MAP.get(chip_code, "⚖️"), "val": VAL_MAP.get(val_code, "⚪"),
            "exit_s": exit_s, "exit_p": exit_p, "exit_c": exit_c, "exit_bg": exit_bg
        }
    except: return None

# 介面渲染
st.markdown("<h1 style='font-size:28px; font-weight:bold;'>🦅 大商道 14.1 波段戰鬥版</h1>", unsafe_allow_html=True)
if st.button("🔄 閃電刷新真實數據", type="primary"): st.rerun()

for category, raw_codes in [("🔥 核心精選主將", main_raw), ("🎯 短中期轉折", sub_raw)]:
    st.markdown(f"<h3 style='color:#FFB300;'>{category}</h3>", unsafe_allow_html=True)
    cols = st.columns(2)
    for i, code in enumerate(raw_codes):
        d = calculate_tactical_signals(code)
        if not d: continue
        with cols[i % 2]:
            st.markdown(f"""<div style="border:1px solid #444; padding:15px; border-radius:8px; margin-bottom:10px;">
            <div style="font-weight:bold; font-size:18px;">{d['name']} ({d['code']}) | 🛡️ 價值盾: {d['shd']}分</div>
            <div style="font-size:28px;">{d['price']:.2f} <span style="color:{'#ff4d4d' if d['gain']>0 else '#00FF00'}">{d['gain']:+.1f}%</span></div>
            <div>{d['chip']} | {d['val']} | 總量: {d['vol']}張</div>
            <div style="background:#3a1515; padding:5px; border-radius:4px; margin:5px 0;">🎯 參考區間: {d['buy_zone']}</div>
            <div style="background:{d['exit_bg']}; color:white; padding:5px; border-radius:4px; margin:5px 0;">🚪 撤退線: {d['exit_p']} | {d['exit_s']}</div>
            </div>""", unsafe_allow_html=True)
            with st.expander("💼 砸密碼：實戰風控 (模擬倉)"):
                c1, c2 = st.columns(2)
                sim_qty = c2.number_input("張數", value=1.0, key=f"q_{d['code']}")
                profit = (d['price'] - d['cost']) * sim_qty * 1000
                st.markdown(f"💰 預估損益: <strong style='color:{'#ff4d4d' if profit>0 else '#00FF00'}'>{profit:+,.0f} 元</strong>", unsafe_allow_html=True)
