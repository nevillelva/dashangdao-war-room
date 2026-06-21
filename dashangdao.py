import streamlit as st
import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta

st.set_page_config(layout="wide", page_title="大商道 14.6 終極完全體")

# ==========================================
# 系統記憶體：初始化總指揮的鎖定追蹤庫
# ==========================================
if 'pinned_stocks' not in st.session_state:
    st.session_state.pinned_stocks = {}

params = st.query_params
default_main = "2317:4:260:1:2,3231:4:158:0:1"
default_sub = "2881:4:73:1:1,2603:4:185:1:1"
default_cycle = "2731:3:120:0:0:7"
default_topic = "1519:5:750:1:3"
default_yield = "2542:4:40:1:1:8:0,3005:4:115:1:2:7:1" 

main_raw = params.get("main", default_main).split(",")
sub_raw = params.get("sub", default_sub).split(",")
cycle_raw = params.get("cycle", default_cycle).split(",")
topic_raw = params.get("topic", default_topic).split(",")
yield_raw = params.get("yield", default_yield).split(",")

TW_STOCKS = {
    "2330": "台積電", "2317": "鴻海", "2382": "廣達", "3231": "緯創", "1519": "華城",
    "2881": "富邦金", "2603": "長榮", "2618": "長榮航", "2731": "雄獅", "3293": "鈊象",
    "2542": "興富發", "3005": "神基"
}

CHIP_MAP = {"1": "🐳 巨鯨進駐", "2": "🩸 外資提款", "0": "⚖️ 籌碼平穩"}
VAL_MAP = {"1": "🟢 便宜階", "2": "🟡 合理階", "3": "🔴 昂貴階", "0": "⚪ 未定階"}

@st.cache_data(ttl=300)
def get_market_weather():
    try:
        taiex = yf.Ticker("^TWII").history(period="1mo")
        current_idx = taiex['Close'].iloc[-1]
        ma20_idx = taiex['Close'].rolling(window=20).mean().iloc[-1]
        daily_change = ((current_idx - taiex['Close'].iloc[-2]) / taiex['Close'].iloc[-2]) * 100
        return current_idx < ma20_idx, daily_change <= -1.5, daily_change
    except: return False, False, 0.0

is_bear_market, is_black_swan, market_change = get_market_weather()

@st.cache_data(ttl=120)
def calculate_tactical_signals(symbol_data, category_type="main"):
    try:
        parts = symbol_data.split(":")
        if not parts[0].strip(): return None
        symbol = parts[0]
        override_shd = int(parts[1]) if len(parts) > 1 else 4
        override_cost = float(parts[2]) if len(parts) > 2 else 100.0
        chip_code = parts[3] if len(parts) > 3 else "0"
        val_code = parts[4] if len(parts) > 4 else "0"
        extra_param = float(parts[5]) if len(parts) > 5 else 0
        is_double_dip = int(parts[6]) if len(parts) > 6 else 0 
        
        ticker = yf.Ticker(f"{symbol}.TW")
        hist = ticker.history(period="6mo")
        if hist.empty: return None

        current_price = hist['Close'].iloc[-1]
        prev_price = hist['Close'].iloc[-2]
        gain = ((current_price - prev_price) / prev_price) * 100
        vol = int(hist['Volume'].iloc[-1] / 1000)
        vol_5d = hist['Volume'].iloc[-6:-1].mean() / 1000
        
        ma20 = hist['Close'].rolling(window=20).mean().iloc[-1]
        ma60 = hist['Close'].rolling(window=60).mean().iloc[-1]
        
        ema12 = hist['Close'].ewm(span=12, adjust=False).mean()
        ema26 = hist['Close'].ewm(span=26, adjust=False).mean()
        macd = (ema12 - ema26).iloc[-1]
        prev_macd = (ema12 - ema26).iloc[-2]

        low_min = hist['Low'].rolling(window=9).min()
        high_max = hist['High'].rolling(window=9).max()
        rsv = (hist['Close'] - low_min) / (high_max - low_min) * 100
        hist['K'] = rsv.ewm(com=2, adjust=False).mean()
        hist['D'] = hist['K'].ewm(com=2, adjust=False).mean()
        k, d = hist['K'].iloc[-1], hist['D'].iloc[-1]
        prev_k, prev_d = hist['K'].iloc[-2], hist['D'].iloc[-2]
        
        kdj_golden_cross = (k < 40) and (prev_k < prev_d) and (k > d)
        macd_golden_cross = (prev_macd < 0) and (macd > 0)
        kdj_signal = "📈 低檔轉折金叉" if kdj_golden_cross else ("📉 高檔死叉" if (k>70 and prev_k>prev_d and k<d) else "〰️ KDJ 震盪")

        main_cost = override_cost if override_cost else round(ma60, 1)
        buy_zone = f"{round(main_cost * 0.97, 1)} - {round(main_cost * 1.03, 1)}"
        diff_from_cost = ((current_price - main_cost) / main_cost) * 100

        anti_trap_warning, trap_color = "", ""
        if vol_5d < 1.0:
            anti_trap_warning, trap_color = "⚠️ 流動性陷阱：量能低迷，嚴防滑價！", "#f39c12"
        elif diff_from_cost < -5.0 and macd < 0 and not kdj_golden_cross:
            anti_trap_warning, trap_color = "🔪 嚴禁接刀：空方宣洩，尚未見底！", "#e74c3c"
        elif val_code == "3" and vol > (vol_5d * 2) and gain < 0:
            anti_trap_warning, trap_color = "🩸 爆量收黑：高檔爆量，主力疑出貨！", "#8e44ad"

        if val_code == "3":
            exit_s, exit_p, exit_c, exit_bg = "🔴 價值滿水：分批了結", f"{current_price:.1f}", "#e74c3c", "#3a1515"
        elif diff_from_cost >= 15.0:
            exit_s, exit_p, exit_c, exit_bg = "🛡️ 階梯保本：鎖定波段", f"{max(current_price * 0.92, main_cost):.1f}", "#3498db", "#152a3a"
        else:
            stop_loss = main_cost * 0.95
            exit_s, exit_p, exit_c, exit_bg = "🚪 鐵血紀律：跌破防守撤退", f"{stop_loss:.1f}", "#8e44ad", "#2c153a"

        signal_text, color_border = "量縮打擊區 🛡️", "#2ecc71"
        today = datetime.now()
        cost_label, cycle_text, extra_badge = "幕僚防守線", "波段戰略定錨中", ""

        if category_type == "cycle":
            months_to_peak = (int(extra_param) - today.month) % 12
            cycle_text, cost_label = f"⏳ 距離旺季發酵：約 {months_to_peak} 個月", "歷年淡季鐵底"
            if months_to_peak <= 3 and abs(diff_from_cost) <= 5 and kdj_golden_cross:
                signal_text, color_border = "🌊 週期底部轉折 (提前佈局)", "#3498db"
                
        elif category_type == "yield":
            yield_pct = extra_param
            extra_badge = f"<span class='info-badge' style='background:#1a4d2e; border:1px solid #2ecc71; color:#fff;' title='推估的年度殖利率'>💰 預估殖利率: {yield_pct}%</span>"
            cost_label = "殖利率保護底"
            if is_double_dip:
                extra_badge += " <span class='info-badge' style='background:#b8860b; color:#fff;' title='參與除息，並抱到完全填息賺取價差！'>🏅 填息雙賺</span>"
                cycle_text = "🗓️ 狙擊目標：抱緊參與除息，等待完全填息"
                exit_s, exit_p, exit_c, exit_bg = "🛡️ 填息防守：基本面護航，抱緊待填息", f"成本 {main_cost:.1f}", "#f1c40f", "#332b00"
            else:
                cycle_text = "🗓️ 狙擊目標：趁除息前利多發酵，逢高獲利了結"
            if yield_pct >= 6.0 and kdj_golden_cross:
                signal_text, color_border = "✨ 殖利保護 & 轉折共振 ✨", "#2ecc71"
            if diff_from_cost > 20.0:
                anti_trap_warning, trap_color = "⚠️ 利多已反映：漲幅過大，嚴禁追高！", "#e67e22"

        if anti_trap_warning:
            signal_text, color_border = anti_trap_warning, trap_color
        elif category_type not in ["cycle", "yield"]:
            if kdj_golden_cross and macd_golden_cross:
                signal_text, color_border = "✨ 雙重技術金叉共振 (極高勝率) ✨", "#f1c40f"
            elif current_price > ma20 * 1.05 and not is_bear_market:
                signal_text, color_border = "強勢多頭突破 🔥", "#e67e22"

        return {
            "name": TW_STOCKS.get(symbol, symbol), "code": symbol, "price": current_price,
            "gain": gain, "cost": main_cost, "cost_label": cost_label, "buy_zone": buy_zone,
            "shd": override_shd, "chip": CHIP_MAP.get(chip_code, "⚖️"),
            "val": VAL_MAP.get(val_code, "⚪"), "kdj": kdj_signal, "signal": signal_text,
            "cycle": cycle_text, "color": color_border, "extra_badge": extra_badge,
            "exit_s": exit_s, "exit_p": exit_p, "exit_c": exit_c, "exit_bg": exit_bg, "vol": vol,
            "raw_data": symbol_data, "cat": category_type
        }
    except: return None

def calc_real_profit(cost, price, qty):
    if cost <= 0: return 0, 0
    buy_val = cost * qty * 1000
    profit = (price - cost) * qty * 1000
    return profit, (profit/(cost * qty * 1000))*100

st.markdown('''<style>
.stApp { background-color: #0b0c0f !important; color: #fff !important; }
div.stButton > button[kind="primary"] { background-color: #3498db !important; color: white !important; border: none; font-weight:bold; height: 45px; font-size: 16px;}
.info-badge { background: #2b2b36; padding: 4px 8px; border-radius: 4px; font-size: 13px; color: #ccc; margin-right: 5px; border: 1px solid #444; display: inline-block; margin-bottom: 5px; cursor: help;}
</style>''', unsafe_allow_html=True)

st.markdown("<h1 style='color:#FFB300;'>🦅 大商道 14.6 終極完全體</h1>", unsafe_allow_html=True)

if is_black_swan: 
    st.markdown(f"<div style='background:#3a1515; border:1px solid #e74c3c; color:#fff; padding:10px; border-radius:8px; margin-bottom:20px; font-weight:bold;' title='大盤單日跌幅超過1.5%，系統啟動保護機制。'>🚨 大盤暴跌 {market_change:.2f}%：防禦機制啟動，暫緩追高！</div>", unsafe_allow_html=True)

if st.button("🔄 刷新全域戰場", type="primary", use_container_width=True): 
    st.cache_data.clear()
    st.rerun()

# ==========================================
# UI 渲染函數 (含鎖定追蹤打勾功能)
# ==========================================
def render_stock_card(d, ui_key_prefix):
    st.markdown(f"""<div style="border: 2px solid {d['color']}; border-radius: 8px; padding: 15px; background-color: #16191f; margin-bottom: 5px;">
<div style="font-weight:bold; font-size:18px; margin-bottom:5px;" title="財報狗價值評估：5分為滿分。">{d['name']} ({d['code']}) | 🛡️ 價值盾: {d['shd']}分</div>
<div style="font-size:32px; font-weight:bold; margin-bottom: 10px;">{d['price']:.2f} <span style="font-size:18px; color:{'#ff4d4d' if d['gain']>0 else '#00FF00'};">{d['gain']:+.1f}%</span></div>
<div style="margin-bottom: 15px;">
    <span class="info-badge" title="追蹤三大法人與主力近期動向">{d['chip']}</span>
    <span class="info-badge" title="KDJ(9,3,3)指標">{d['kdj']}</span>
    <span class="info-badge">成交 {d['vol']}張</span>
    {d['extra_badge']}
</div>
<div style="background:#2b2b36; border-radius:5px; padding:10px; margin-bottom:10px; text-align:center;">
<span style="color:#aaa;">{d['cost_label']}:</span> <strong style="color:#fff; font-size:16px;">{d['cost']}</strong><br>
<span style="color:#e74c3c; font-weight:bold;" title="幕僚防守線的正負3%區間，跌入此區即為最佳開火位置。">🎯 打擊區: [ {d['buy_zone']} ]</span>
</div>
<div style="background:{d['exit_bg']}; color:{d['exit_color']}; font-weight:bold; text-align:center; padding:8px; border-radius:5px; margin-bottom:10px; cursor:help;" title="系統根據獲利%數與大盤狀況，自動切換撤退建議。">
{d['exit_s']} ({d['exit_price']})
</div>
<div style="font-size:13px; color:#ddd; margin-bottom:10px;">
📌 狀態: <strong style="color:{d['color']}">{d['signal']}</strong><br>
{d['cycle']}
</div>
</div>""", unsafe_allow_html=True)
    
    # 📌 鎖定追蹤核取方塊
    is_pinned = d['code'] in st.session_state.pinned_stocks
    pin_action = st.checkbox("📌 鎖定追蹤 (置頂保護)", value=is_pinned, key=f"pin_{ui_key_prefix}_{d['code']}")
    if pin_action:
        st.session_state.pinned_stocks[d['code']] = d
    elif not pin_action and is_pinned:
        del st.session_state.pinned_stocks[d['code']]
        st.rerun() # 取消鎖定時立刻刷新畫面

    with st.expander(f"💼 實戰風控 ({d['name']})"):
        c1, c2 = st.columns(2)
        sim_cost = c1.number_input("進場價", value=float(d['cost']), key=f"c_{ui_key_prefix}_{d['code']}")
        sim_qty = c2.number_input("張數", value=1.0, key=f"q_{ui_key_prefix}_{d['code']}")
        sim_profit, sim_roi = calc_real_profit(sim_cost, d['price'], sim_qty)
        p_color = '#ff4d4d' if sim_profit > 0 else '#00FF00'
        st.markdown(f"<div style='background:#000; padding:10px; border-radius:5px;'>預估損益: <strong style='color:{p_color}; font-size:16px;'>{sim_profit:+,.0f} 元 ({sim_roi:+.2f}%)</strong></div>", unsafe_allow_html=True)


# ==========================================
# 介面板塊渲染
# ==========================================

# 1. 優先渲染「總指揮鎖定雷達」
if st.session_state.pinned_stocks:
    st.markdown("<h2 style='color:#f1c40f; margin-top:20px; border-bottom: 2px solid #f1c40f; padding-bottom:5px;'>⭐ 總指揮專屬雷達 (已鎖定)</h2>", unsafe_allow_html=True)
    cols = st.columns(2)
    for i, (code, d) in enumerate(list(st.session_state.pinned_stocks.items())):
        with cols[i % 2]:
            render_stock_card(d, ui_key_prefix="pinned")

# 2. 渲染其他戰區
SECTIONS = [
    ("🔥 核心精選主將", main_raw, "main", "#FFB300"), 
    ("🎯 短中期轉折", sub_raw, "sub", "#ccc"), 
    ("🌪️ 題材強勢突擊", topic_raw, "topic", "#e67e22"), 
    ("🌊 週期戰略部隊", cycle_raw, "cycle", "#3498db"),
    ("💰 財報與高殖利狙擊", yield_raw, "yield", "#2ecc71")
]

for category, raw_codes, cat_type, title_color in SECTIONS:
    if not raw_codes or not raw_codes[0]: continue
    st.markdown(f"<h3 style='color:{title_color}; margin-top:30px;'>{category}</h3>", unsafe_allow_html=True)
    cols = st.columns(2)
    valid_count = 0
    for symbol_data in raw_codes:
        d = calculate_tactical_signals(symbol_data, cat_type)
        if not d: continue
        # 如果已經在鎖定區，就不在原本的區域重複顯示，保持畫面乾淨
        if d['code'] in st.session_state.pinned_stocks: continue 
        
        with cols[valid_count % 2]:
            render_stock_card(d, ui_key_prefix=cat_type)
        valid_count += 1
