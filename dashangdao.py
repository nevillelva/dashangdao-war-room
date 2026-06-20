import streamlit as st
import yfinance as yf
from datetime import datetime, timedelta

st.set_page_config(layout="wide", page_title="大商道 14.1 終極戰情室")

# 讀取超級網址 2.0 參數 (代碼:價值盾:成本:籌碼:位階)
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
        
        # 爆量偵測
        vol_5d = hist['Volume'].iloc[-6:-1].mean() / 1000
        if vol_5d > 0 and vol > (vol_5d * 1.5) and gain > 0:
            vol_signal = "<span style='color:#e74c3c; font-weight:bold;'>⚡ 爆量點火發動</span>"
        else:
            vol_signal = "<span style='color:#7f8c8d;'>穩健量能維持</span>"

        ma20 = hist['Close'].rolling(window=20).mean().iloc[-1]
        ma60 = hist['Close'].rolling(window=60).mean().iloc[-1]

        main_cost = override_cost if override_cost else round(ma60, 1)
        buy_low = round(ma20 * 0.98, 1)
        buy_high = round(ma20 * 1.02, 1)
        buy_zone = f"{buy_low} - {buy_high}"

        diff_from_cost = ((current_price - main_cost) / main_cost) * 100
        diff_from_ma20 = ((current_price - ma20) / ma20) * 100

        # ==========================================
        # 🛡️ 撤退導航儀邏輯 (三合一)
        # ==========================================
        is_expensive = (val_code == "3")
        
        if is_expensive:
            exit_strategy = "🔴 價值滿水：估價過高，建議分批獲利了結"
            exit_price = f"現價 {current_price:.1f}"
            exit_color = "#e74c3c"
            exit_bg = "#3a1515"
        elif diff_from_cost >= 10.0:
            trailing_stop = max(current_price * 0.95, ma20)
            exit_strategy = "🛡️ 階梯保本：跌破此線上移點出場，鎖定利潤"
            exit_price = f"{trailing_stop:.1f}"
            exit_color = "#3498db"
            exit_bg = "#152a3a"
        else:
            stop_loss = main_cost * 0.97
            exit_strategy = "🚪 鐵血紀律：跌破主力防守線3%無條件停損"
            exit_price = f"{stop_loss:.1f}"
            exit_color = "#8e44ad"
            exit_bg = "#2c153a"

        # 進場訊號燈
        if diff_from_cost <= -3.0:
            signal_text = f"破底邊緣 {abs(diff_from_cost):.1f}%！(警報🚨)"
            color_border = "#e74c3c"
        elif diff_from_ma20 >= 5.0:
            signal_text = "強勢多頭排列 🔥"
            color_border = "#e67e22"
        else:
            signal_text = "量縮築底區 (打擊區) 🛡️"
            color_border = "#2ecc71"

        shd_score = override_shd if override_shd else (2 if diff_from_cost <= -3.0 else (5 if diff_from_ma20 >= 5.0 else 4))
        chip_text = CHIP_MAP.get(chip_code, "⚖️ 籌碼平穩")
        val_text = VAL_MAP.get(val_code, "⚪ 未定階")

        today = datetime.now()
        target_date = today.replace(day=10) if today.day <= 10 else (today.replace(day=28) + timedelta(days=4)).replace(day=10)
        
        return {
            "name": stock_name, "code": symbol, "price": current_price, "gain": gain, 
            "open": open_p, "high": high_p, "low": low_p, "vol": vol,
            "cost": main_cost, "buy_zone": buy_zone, "signal": signal_text, 
            "cycle": f"營收公告倒數：{(target_date - today).days} 天", 
            "color": color_border, "shd": shd_score,
            "chip": chip_text, "val": val_text, "vol_signal": vol_signal,
            "exit_strategy": exit_strategy, "exit_price": exit_price, "exit_color": exit_color, "exit_bg": exit_bg
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

st.markdown('''<style>
.stApp { background-color: #0b0c0f !important; color: #fff !important; }
div.stButton > button[kind="primary"] { background-color: #3498db !important; color: white !important; border: none !important; font-weight:bold; height: 45px; font-size: 16px;}
.info-badge { background: #2b2b36; padding: 4px 8px; border-radius: 4px; font-size: 13px; color: #ccc; margin-right: 5px; border: 1px solid #444; }
</style>''', unsafe_allow_html=True)

st.markdown("<h1 style='font-size:28px; font-weight:bold;'>🦅 大商道 14.1 終極戰情室</h1>", unsafe_allow_html=True)

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
<div style="font-size:36px; font-weight:bold; color:#fff; margin-bottom: 10px;">{d['price']:.2f} <span style="font-size:18px; color:{gain_color};">{d['gain']:+.1f}%</span></div>

<div style="margin-bottom: 15px;">
    <span class="info-badge">{d['chip']}</span>
    <span class="info-badge">📊 {d['val']}</span>
    <span class="info-badge">{d['vol_signal']}</span>
</div>

<div style="background:#2b2b36; border-radius:5px; padding:10px; display:flex; justify-content:space-between; text-align:center; margin-bottom:10px;">
<div style="flex:1; color:#aaa; font-size:12px;">開盤<br><span style="color:#fff; font-size:15px; font-weight:bold;">{d['open']:.1f}</span></div>
<div style="flex:1; color:#aaa; font-size:12px;">最高<br><span style="color:#fff; font-size:15px; font-weight:bold;">{d['high']:.1f}</span></div>
<div style="flex:1; color:#aaa; font-size:12px;">最低<br><span style="color:#fff; font-size:15px; font-weight:bold;">{d['low']:.1f}</span></div>
<div style="flex:1; color:#aaa; font-size:12px;">總量<br><span style="color:#fff; font-size:15px; font-weight:bold;">{d['vol']}張</span></div>
</div>

<div style="background:#3a1515; color:#ff4d4d; font-size:16px; font-weight:bold; text-align:center; padding:8px; border-radius:5px; border:1px solid #ff4d4d; margin-bottom:10px;">🎯 參考區間: [ {d['buy_zone']} ]</div>

<div style="background:{d['exit_bg']}; color:{d['exit_color']}; font-size:14px; font-weight:bold; text-align:center; padding:8px; border-radius:5px; border:1px solid {d['exit_color']}; margin-bottom:15px;">
🚪 撤退線: {d['exit_price']} | {d['exit_strategy']}
</div>

<div style="font-size:13px; color:#ddd; line-height:1.8;">
👥 幕僚防守線: {d['cost']} | 🛡️ {d['signal']}<br>
🚀 {d['cycle']} ⏳<br>
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
