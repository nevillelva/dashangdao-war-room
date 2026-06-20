import streamlit as st
import yfinance as yf
from datetime import datetime, timedelta

st.set_page_config(layout="wide", page_title="大商道 14.1 終極戰情室")

# 讀取超級網址參數 (代碼:價值盾:成本:籌碼:位階)
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
        override_shd = int(parts[1]) if len(parts) > 1 else None
        override_cost = float(parts[2]) if len(parts) > 2 else None
        chip_code = parts[3] if len(parts) > 3 else "0"
        val_code = parts[4] if len(parts) > 4 else "0"

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
        
        vol_5d = hist['Volume'].iloc[-6:-1].mean() / 1000
        vol_signal = "<span style='color:#e74c3c; font-weight:bold;'>⚡ 爆量點火</span>" if (vol_5d > 0 and vol > (vol_5d * 1.5)) else "<span style='color:#7f8c8d;'>穩健量能</span>"

        ma60 = hist['Close'].rolling(window=60).mean().iloc[-1]
        main_cost = override_cost if override_cost else round(ma60, 1)
        buy_zone = f"{round(main_cost * 0.97, 1)} - {round(main_cost * 1.03, 1)}"

        diff_from_cost = ((current_price - main_cost) / main_cost) * 100
        
        # 撤退導航儀
        is_expensive = (val_code == "3")
        if is_expensive:
            exit_strategy, exit_price, exit_color, exit_bg = "🔴 價值滿水：建議獲利了結", f"{current_price:.1f}", "#e74c3c", "#3a1515"
        elif diff_from_cost >= 15.0:
            exit_strategy, exit_price, exit_color, exit_bg = "🛡️ 階梯保本：鎖定利潤", f"{max(current_price * 0.92, main_cost):.1f}", "#3498db", "#152a3a"
        else:
            exit_strategy, exit_price, exit_color, exit_bg = "🚪 鐵血紀律：跌破季線5%撤退", f"{main_cost * 0.95:.1f}", "#8e44ad", "#2c153a"

        return {
            "name": stock_name, "code": symbol, "price": current_price, "gain": gain, 
            "open": open_p, "high": high_p, "low": low_p, "vol": vol,
            "cost": main_cost, "buy_zone": buy_zone, "cycle": "波段戰略定錨中", 
            "color": "#e74c3c" if diff_from_cost <= -5.0 else "#2ecc71",
            "shd": override_shd or 4, "chip": CHIP_MAP.get(chip_code, "⚖️"),
            "val": VAL_MAP.get(val_code, "⚪"), "vol_signal": vol_signal,
            "exit_strategy": exit_strategy, "exit_price": exit_price, "exit_color": exit_color, "exit_bg": exit_bg
        }
    except Exception: return None

def calc_real_profit(cost, price, qty):
    if cost <= 0: return 0, 0
    buy_val = cost * qty * 1000
    profit = (price - cost) * qty * 1000
    return profit, (profit/(cost * qty * 1000))*100

st.markdown("<h1 style='font-size:28px; font-weight:bold;'>🦅 大商道 14.1 波段戰鬥版</h1>", unsafe_allow_html=True)

if st.button("🔄 刷新戰場"): st.rerun()

cols = st.columns(2)
for i, symbol_data in enumerate(main_raw + sub_raw):
    d = calculate_tactical_signals(symbol_data)
    if not d: continue
    with cols[i % 2]:
        st.markdown(f"""<div style="border: 2px solid {d['color']}; padding: 10px; border-radius:8px; margin-bottom:10px;">
        <div style="font-weight:bold;">{d['name']} ({d['code']}) | 🛡️ 價值盾: {d['shd']}</div>
        <div style="font-size:24px;">{d['price']:.2f} <span style="color:red;">{d['gain']:+.1f}%</span></div>
        <div>{d['chip']} | {d['val']} | {d['vol_signal']}</div>
        <div style="background:{d['exit_bg']}; color:white; padding:5px; border-radius:5px; margin:5px 0;">🚪 撤退線: {d['exit_price']} | {d['exit_strategy']}</div>
        </div>""", unsafe_allow_html=True)
