import streamlit as st
import yfinance as yf
import pandas as pd
from datetime import datetime
import re
import math

st.set_page_config(layout="wide", page_title="54088")

# ==========================================
# 🛡️ 記憶體與狀態復原引擎
# ==========================================
COMMANDER_PIN = "0826"
MAX_CAPACITY = 15  

params = st.query_params

if 'authenticated' not in st.session_state:
    st.session_state.authenticated = (params.get("auth") == "54088")

if not st.session_state.authenticated:
    st.markdown('''<style>
    .stApp { background-color: #0b0c0f !important; color: #fff !important; }
    div.stButton > button { background-color: #222 !important; color: #888 !important; border: 1px solid #444 !important; font-weight:normal; }
    div.stButton > button:hover { color: #fff !important; border-color: #666 !important; }
    </style>''', unsafe_allow_html=True)
    
    st.markdown("<h1 style='text-align: center; color: #444; margin-top: 20vh; font-family: monospace; letter-spacing: 5px; font-size: 2rem;'>54088</h1>", unsafe_allow_html=True)
    
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        pwd_input = st.text_input(" ", type="password", key="pwd_input", placeholder="PIN")
        if st.button("Enter", use_container_width=True):
            if pwd_input == COMMANDER_PIN:
                st.session_state.authenticated = True
                st.query_params["auth"] = "54088"
                st.rerun()
            else:
                st.error("Error")
    st.stop()

# 狀態初始化
if 'pinned_stocks' not in st.session_state: st.session_state.pinned_stocks = {}
if 'portfolio' not in st.session_state: st.session_state.portfolio = {}
if 'intel_mission' not in st.session_state: st.session_state.intel_mission = [] 

if 'url_loaded' not in st.session_state:
    if "p_pin" in params:
        for item in params.get("p_pin", "").split(","):
            if item:
                try:
                    parts = item.split("@")
                    if len(parts) >= 3:
                        st.session_state.pinned_stocks[parts[0]] = {'raw_data': parts[1], 'cat': parts[2]}
                except: pass
    if "p_port" in params:
        for item in params.get("p_port", "").split(","):
            if item:
                try:
                    parts = item.split("@")
                    if len(parts) >= 5:
                        st.session_state.portfolio[parts[0]] = {
                            "entry_price": float(parts[1]), "qty": float(parts[2]), 
                            "raw_data": parts[3], "cat": parts[4]
                        }
                except: pass
    st.session_state.url_loaded = True

def sync_state_to_url():
    pin_list = [f"{k}@{v['raw_data']}@{v['cat']}" for k, v in st.session_state.pinned_stocks.items()]
    if pin_list: st.query_params["p_pin"] = ",".join(pin_list)
    elif "p_pin" in st.query_params: del st.query_params["p_pin"]
        
    port_list = [f"{k}@{round(v['entry_price'], 2)}@{round(v['qty'], 3)}@{v['raw_data']}@{v['cat']}" for k, v in st.session_state.portfolio.items()]
    if port_list: st.query_params["p_port"] = ",".join(port_list)
    elif "p_port" in st.query_params: del st.query_params["p_port"]

# ==========================================
# 📡 系統參數與大擴充 ETF / 飆股字典
# ==========================================
TW_STOCKS = {
    "2330":"台積電", "2317":"鴻海", "2454":"聯發科", "2382":"廣達", "2303":"聯電",
    "0050":"元大台灣50", "0056":"元大高股息", "00878":"國泰永續高股息", "00919":"群益台灣精選高息",
    "00929":"復華台灣科技優息", "00631L":"元大台灣50正2", "00632R":"元大台灣50反1",
    "3231":"緯創", "6669":"緯穎", "2356":"英業達", "2376":"技嘉", "3017":"奇鋐", "3324":"雙鴻", "2421":"建準",
    "3661":"世芯-KY", "3443":"創意", "3035":"智原", "6643":"M31", "3529":"力旺", "6533":"晶心科",
    "5347":"世界", "3707":"漢磊", "2481":"強茂", "8261":"富鼎", "3317":"尼克森", "5425":"台半", "8255":"朋程",
    "3711":"日月光投控", "3131":"弘塑", "3583":"辛耘", "6187":"萬潤", "1560":"中砂", "5443":"均豪",
    "1519":"華城", "1513":"中興電", "1514":"亞力", "1504":"東元", "2603":"長榮", "2609":"陽明", "2615":"萬海", "2618":"長榮航",
    "3008":"大立光", "3034":"聯詠", "2379":"瑞昱", "3481":"群創", "2409":"友達", "2308":"台達電", "2345":"智邦", "3189":"景碩"
}

CHIP_MAP = {"1": "🐳 巨鯨進駐", "2": "🩸 外資提款", "0": "⚖️ 籌碼平穩", "?": "❓待查"}
VAL_MAP = {"1": "🟢 便宜階", "2": "🟡 合理階", "3": "🔴 昂貴階", "0": "⚪ 未定階", "?": "❓待查"}

def safe_int(val, default=0):
    try: return int(val) if val else default
    except: return default

def safe_float(val, default=None):
    try: return float(val) if val else default
    except: return default

def get_stock_name(symbol):
    if symbol in TW_STOCKS: return TW_STOCKS[symbol]
    try:
        info = yf.Ticker(f"{symbol}.TW").fast_info
        return symbol 
    except: pass
    return symbol

# ==========================================
# 🧠 核心量化演算法 (極速即時連線 & 400元觀測版)
# ==========================================
def calculate_tactical_signals(symbol_data, category_type="main"):
    try:
        parts = symbol_data.split(":")
        if not parts[0].strip(): return None
        symbol = parts[0].strip()
        stock_name = get_stock_name(symbol) 
        
        shd_str = parts[1].strip() if len(parts) > 1 else "4"
        override_shd_raw = shd_str if shd_str == "?" else safe_int(shd_str, 4)
        
        cost_str = parts[2].strip() if len(parts) > 2 else ""
        override_cost = None if cost_str == "?" else safe_float(cost_str, None)
        if override_cost and override_cost <= 0: override_cost = None
        
        chip_code = parts[3].strip() if len(parts) > 3 else "0"
        val_code = parts[4].strip() if len(parts) > 4 else "0"
        
        ticker = yf.Ticker(f"{symbol}.TW")
        hist = ticker.history(period="6mo")
        if hist.empty or 'Close' not in hist.columns:
            ticker = yf.Ticker(f"{symbol}.TWO")
            hist = ticker.history(period="6mo")
        if hist.empty or 'Close' not in hist.columns: return None
        hist = hist.dropna(subset=['Close', 'Open', 'High', 'Low', 'Volume'])
        if len(hist) < 15: return None 

        try:
            val_last = ticker.fast_info.last_price
            if val_last is None or math.isnan(val_last):
                current_price = float(hist['Close'].iloc[-1])
            else:
                current_price = float(val_last)
        except:
            current_price = float(hist['Close'].iloc[-1])
            
        try:
            val_prev = ticker.fast_info.previous_close
            if math.isnan(val_prev) or val_prev <= 0: prev_price = max(float(hist['Close'].iloc[-2]), 0.001)
            else: prev_price = float(val_prev)
        except:
            prev_price = max(float(hist['Close'].iloc[-2]), 0.001)

        open_p = float(hist['Open'].iloc[-1])
        high_p = float(hist['High'].iloc[-1])
        low_p = float(hist['Low'].iloc[-1])
        
        raw_gain = ((current_price - prev_price) / prev_price) * 100
        gain = raw_gain if -50.0 <= raw_gain <= 50.0 else 0.0 

        vol = int(hist['Volume'].iloc[-1] / 1000)
        vol_5d = hist['Volume'].iloc[-6:-1].mean() / 1000 if len(hist) >= 6 else vol
        vol_5d = max(vol_5d, 0.01) 
        
        ma5 = hist['Close'].rolling(window=min(5, len(hist))).mean().iloc[-1]
        ma20 = hist['Close'].rolling(window=min(20, len(hist))).mean().iloc[-1]
        ma60 = hist['Close'].rolling(window=min(60, len(hist))).mean().iloc[-1]
        
        macd_line = hist['Close'].ewm(span=12, adjust=False).mean() - hist['Close'].ewm(span=26, adjust=False).mean()
        signal_line = macd_line.ewm(span=9, adjust=False).mean()
        macd_hist = macd_line - signal_line
        is_macd_red = (len(macd_hist) > 1) and (macd_hist.iloc[-1] > 0) and (macd_hist.iloc[-2] <= 0)
        
        low_min = hist['Low'].rolling(window=min(9, len(hist))).min()
        high_max = hist['High'].rolling(window=min(9, len(hist))).max()
        rsv = (hist['Close'] - low_min) / (high_max - low_min + 1e-9) * 100
        hist['K'] = rsv.fillna(50).ewm(com=2, adjust=False).mean()
        hist['D'] = hist['K'].fillna(50).ewm(com=2, adjust=False).mean()
        k, d = hist['K'].iloc[-1], hist['D'].iloc[-1]
        kdj_golden_cross = (k < 40) and (hist['K'].iloc[-2] < hist['D'].iloc[-2]) and (k > d) if len(hist) > 1 else False
        kdj_signal = "📈 低檔金叉" if kdj_golden_cross else ("📉 高檔死叉" if (k>70 and k<d) else "〰️ KDJ 震盪")

        is_breakout = (gain > 2.0) and (vol > vol_5d * 1.5) and (current_price > ma20) 
        buy_cond_count = sum([kdj_golden_cross, is_macd_red, is_breakout])
        
        buy_status, buy_color, buy_bg = "⚪ 醞釀中 (無明顯起漲)", "#aaaaaa", "#1a1a24"
        if buy_cond_count == 3: buy_status, buy_color, buy_bg = "🔥 三火全亮，強勢起漲！", "#ff4d4d", "#3a1515"
        elif buy_cond_count == 2: buy_status, buy_color, buy_bg = "🚀 雙引擎發動，準備表態", "#f1c40f", "#3a3015"
        elif buy_cond_count == 1: buy_status, buy_color, buy_bg = "✨ 底部浮現單一火苗", "#3498db", "#152a3a"

        buy_html = f"<div class='my-tooltip' style='background:{buy_bg}; padding:10px 15px; border-radius:8px; margin-bottom:12px; border-left: 5px solid {buy_color}; box-shadow: 0 4px 8px rgba(0,0,0,0.4); display:block; width:100%;'><div style='font-size:12px; color:#ddd; margin-bottom:6px;'>🚀 起漲(買進)雷達：<strong style='color:{buy_color}; font-size:14px;'>{buy_status}</strong></div><div style='font-size:12px; color:#eee; display:flex; justify-content:space-between;'><span>{'🔴' if kdj_golden_cross else '⚪'} KDJ金叉</span><span>{'🔴' if is_macd_red else '⚪'} MACD翻紅</span><span>{'🔴' if is_breakout else '⚪'} 帶量上攻</span></div><span class='my-tooltiptext'>短線起漲動能判定。</span></div>"

        is_huge_vol = vol > (vol_5d * 2.0)               
        is_black_k = current_price < open_p and gain < 0 
        is_break_ma5 = current_price < ma5               
        
        sell_cond_count = sum([is_huge_vol, is_black_k, is_break_ma5])
        spotter_status, spotter_color, spotter_bg = "🟢 陣地安全，續抱", "#2ecc71", "#153a20"
        if sell_cond_count == 3: spotter_status, spotter_color, spotter_bg = "🔴 三要件確立，立即撤退！", "#e74c3c", "#3a1515"
        elif sell_cond_count == 2: spotter_status, spotter_color, spotter_bg = "🟡 多重警訊，提高警戒", "#f1c40f", "#3a3015"
        elif sell_cond_count == 1: spotter_status, spotter_color, spotter_bg = "🟡 注意單一異常訊號", "#f39c12", "#3a2515"

        spotter_html = f"<div class='my-tooltip' style='background:{spotter_bg}; padding:10px 15px; border-radius:8px; margin-bottom:12px; border-left: 5px solid {spotter_color}; box-shadow: 0 4px 8px rgba(0,0,0,0.4); display:block; width:100%;'><div style='font-size:12px; color:#ddd; margin-bottom:6px;'>🚨 撤退(賣出)雷達：<strong style='color:{spotter_color}; font-size:14px;'>{spotter_status}</strong></div><div style='font-size:12px; color:#eee; display:flex; justify-content:space-between;'><span>{'🔴' if is_huge_vol else '⚪'} 爆量</span><span>{'🔴' if is_black_k else '⚪'} 實體黑K</span><span>{'🔴' if is_break_ma5 else '⚪'} 破5MA</span></div><span class='my-tooltiptext'>短線波段撤退判定。</span></div>"

        jail_html = ""
        if len(hist) >= 7:
            close_6d_ago = max(float(hist['Close'].iloc[-6]), 0.001)
            return_6d = ((current_price - close_6d_ago) / close_6d_ago) * 100
            prev_close = float(hist['Close'].iloc[-2])
            close_7d_ago = max(float(hist['Close'].iloc[-7]), 0.001)
            prev_return_6d = ((prev_close - close_7d_ago) / close_7d_ago) * 100
            
            jail_color, jail_bg = "#2ecc71", "#153a20"
            jail_status = f"安全 (累計漲幅 {return_6d:.1f}%)"
            
            if return_6d >= 25.0 and prev_return_6d >= 25.0:
                jail_color, jail_bg = "#9b59b6", "#2c153a"
                jail_status = f"🛑 高危險處置區！(已連續觸發注意股)"
            elif return_6d >= 25.0:
                jail_color, jail_bg = "#e74c3c", "#3a1515"
                jail_status = f"🔥 觸發注意股紅線！"
            elif return_6d >= 20.0:
                jail_color, jail_bg = "#f39c12", "#3a3015"
                jail_status = f"⚠️ 漲幅過熱逼近紅線"
                
            jail_html = f"<div class='my-tooltip' style='background:{jail_bg}; padding:10px 15px; border-radius:8px; margin-bottom:12px; border-left: 5px solid {jail_color}; box-shadow: 0 4px 8px rgba(0,0,0,0.4); display:block; width:100%;'><div style='font-size:12px; color:#ddd; margin-bottom:4px;'>⚖️ 證交所警示：<strong style='color:{jail_color}; font-size:13px;'>{jail_status}</strong></div></div>"

        main_cost = override_cost if override_cost else round(ma60, 1)
        buy_low, buy_high = round(main_cost * 0.97, 1), round(main_cost * 1.03, 1)
        diff_from_cost = ((current_price - max(main_cost, 0.001)) / max(main_cost, 0.001)) * 100

        is_in_buy_zone = (buy_low <= current_price <= buy_high)
        if vol_5d < 0.5: signal_text, color_border, signal_bg = "⚠️ 流動性枯竭 (勿碰)", "#8e44ad", "#2c153a"
        elif is_in_buy_zone:
            if sell_cond_count >= 2: signal_text, color_border, signal_bg = "⚠️ 抵達支撐區，短線偏弱 (勿接刀)", "#f39c12", "#3a3015"
            elif val_code == "3": signal_text, color_border, signal_bg = "⚠️ 抵達支撐區，估值滿水 (嚴控資金)", "#e67e22", "#3a2515"
            elif buy_cond_count >= 1: signal_text, color_border, signal_bg = "🎯 完美打擊區！(支撐有守＋起漲)", "#00FF00", "#153a20"
            else: signal_text, color_border, signal_bg = "🎯 進入最佳入場區 (可分批建倉)", "#2ecc71", "#153a20"
        elif diff_from_cost < -5.0: signal_text, color_border, signal_bg = "⚠️ 嚴重破線/破底 (勿入)", "#e74c3c", "#3a1515"
        elif diff_from_cost > 5.0:
            if val_code == "3": signal_text, color_border, signal_bg = "🔥 估值滿水 (極度昂貴，嚴禁追價)", "#e74c3c", "#3a1515"
            elif buy_cond_count >= 2: signal_text, color_border, signal_bg = "🚀 右側強勢發動中 (順勢操作)", "#e67e22", "#3a2515"
            else: signal_text, color_border, signal_bg = "🚀 高檔觀察 (太貴勿追)", "#e67e22", "#3a2515"
        else: signal_text, color_border, signal_bg = "🛡️ 區間震盪 (等待落點)", "#ccc", "#2b2b36"

        if val_code == "3": exit_s, exit_p, exit_c, exit_bg = "🔴 價值滿水了結", f"{current_price:.1f}", "#e74c3c", "#3a1515"
        elif diff_from_cost >= 15.0: exit_s, exit_p, exit_c, exit_bg = "🛡️ 鎖定波段底線", f"{max(current_price * 0.92, main_cost):.1f}", "#e74c3c", "#152a3a"
        else: exit_s, exit_p, exit_c, exit_bg = "🚪 破線撤退點", f"{main_cost * 0.95:.1f}", "#e74c3c", "#2c153a"

        buy_zone = f"{buy_low} - {buy_high}"
        shd_display = "❓ 待查" if override_shd_raw == "?" else f"{override_shd_raw}分"

        return {"name": stock_name, "code": symbol, "price": current_price, "gain": gain, "cost": main_cost, "cost_label": "長線季線", "buy_zone": buy_zone, "shd": shd_display, "chip_code": chip_code, "chip": CHIP_MAP.get(chip_code, "⚖️"), "val_code": val_code, "val": VAL_MAP.get(val_code, "⚪"), "kdj": kdj_signal, "signal": signal_text, "color": color_border, "signal_bg": signal_bg, "extra_badge": "", "exit_s": exit_s, "exit_price": exit_p, "exit_color": exit_c, "exit_bg": exit_bg, "vol": vol, "open": open_p, "high": high_p, "low": low_p, "raw_data": symbol_data, "cat": category_type, "spotter_html": spotter_html, "buy_html": buy_html, "jail_html": jail_html}
    except Exception as e: return None

def calc_real_profit(cost, price, qty):
    if cost <= 0: return 0, 0
    buy_val = cost * qty * 1000
    sell_val = price * qty * 1000
    fee_buy = max(20, int(buy_val * 0.001425))
    fee_sell = max(20, int(sell_val * 0.001425))
    tax = int(sell_val * 0.003)
    profit = sell_val - buy_val - fee_buy - fee_sell - tax
    return profit, (profit/buy_val)*100 if buy_val > 0 else 0

st.markdown('''<style>
.stApp { background-color: #0b0c0f !important; color: #fff !important; }
div.stButton > button[kind="primary"] { background-color: #3498db !important; color: white !important; border: none; font-weight:bold; height: 45px; font-size: 16px;}
.sync-btn > button { background-color: #f1c40f !important; color: #000 !important; font-weight: 900 !important; border: 2px solid #f39c12 !important; border-radius: 8px !important; }
.sync-btn > button:hover { background-color: #f39c12 !important; color: #fff !important; }
.lock-btn > button { background-color: #333 !important; color: #888 !important; border-radius: 8px !important; }
.buy-btn > button { background-color: #e74c3c !important; width: 100%; margin-top: 10px; font-weight: bold; }
.pin-btn > button { background-color: #2c3e50 !important; color: #fff !important; width: 100%; font-weight: bold; margin-bottom: 5px; border: 1px solid #555 !important; }
.pin-btn > button:hover { background-color: #34495e !important; border-color: #f1c40f !important; color: #f1c40f !important; }
.unpin-btn > button { background-color: #7f8c8d !important; color: #fff !important; width: 100%; font-weight: bold; margin-bottom: 5px; }
.sell-btn > button { background-color: #2ecc71 !important; width: 100%; margin-top: 10px; font-weight: bold; }
.info-badge { background: #2b2b36; padding: 4px 8px; border-radius: 4px; font-size: 13px; color: #ccc; margin-right: 5px; border: 1px solid #444; display: inline-block; margin-bottom: 5px; }
.my-tooltip { position: relative; display: inline-block; cursor: help; }
.my-tooltip .my-tooltiptext { visibility: hidden; width: max-content; max-width: 250px; background-color: #ffcc00; color: #111; text-align: center; border-radius: 6px; padding: 8px 12px; position: absolute; z-index: 99999; bottom: 125%; left: 50%; transform: translateX(-50%); opacity: 0; transition: opacity 0.2s; font-size: 13px; font-weight: bold; line-height: 1.4; box-shadow: 0px 4px 15px rgba(0,0,0,0.6); pointer-events: none; white-space: normal; }
.my-tooltip .my-tooltiptext::after { content: ""; position: absolute; top: 100%; left: 50%; margin-left: -6px; border-width: 6px; border-style: solid; border-color: #ffcc00 transparent transparent transparent; }
.my-tooltip:hover .my-tooltiptext { visibility: visible; opacity: 1; }
</style>''', unsafe_allow_html=True)

# ==========================================
# 🖥️ 戰情室主要版面
# ==========================================
col_title, col_sync, col_logout = st.columns([4, 1, 1])
with col_title: st.markdown("<h1 style='color:#FFB300; margin: 0;'>54088</h1>", unsafe_allow_html=True)
with col_sync:
    st.markdown("<div class='sync-btn'>", unsafe_allow_html=True)
    if st.button("🔄 同步更新即時報價", use_container_width=True):
        st.rerun() 
    st.markdown("</div>", unsafe_allow_html=True)
with col_logout:
    st.markdown("<div class='lock-btn'>", unsafe_allow_html=True)
    if st.button("🔒 系統鎖定", use_container_width=True):
        st.session_state.authenticated = False
        # 💥 終極修復：只刪除登入授權碼，保留雷達與庫存記憶！
        if "auth" in st.query_params:
            del st.query_params["auth"]
        st.rerun()
    st.markdown("</div>", unsafe_allow_html=True)

st.markdown(f"<div style='text-align:right; color:#888; font-size:12px; margin-bottom:10px;'>即時報價連線中 | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</div>", unsafe_allow_html=True)

st.markdown("<div style='background:#16191f; padding:15px; border-radius:8px; border: 1px solid #3498db; margin-bottom:20px;'>", unsafe_allow_html=True)
st.markdown("<h4 style='color:#3498db; margin-top:0px;'>📡 總部情報接收器</h4>", unsafe_allow_html=True)
with st.form(key='intel_form', clear_on_submit=True): 
    intel_input = st.text_input("輸入 CEO 派發的戰術密碼 (例如 INTEL:2303:4:1:1...)：", placeholder="貼上密碼後點擊右方按鈕注入")
    submit_button = st.form_submit_button(label='📥 注入情報')
    if submit_button and intel_input.startswith("INTEL:"):
        raw_str = intel_input.replace("INTEL:", "").strip()
        st.session_state.intel_mission = [x.strip() for x in raw_str.split(",") if x.strip()]
st.markdown("</div>", unsafe_allow_html=True)

st.markdown("<h3 style='color:#f1c40f; margin-top:10px; border-bottom: 2px solid #f1c40f; padding-bottom:5px;'>🔍 手動探測雷達</h3>", unsafe_allow_html=True)
search_query = st.text_input("📝 輸入代號或名稱 (如：3035 或 智原) [輸入後按 Enter]：", key="search_input")

def render_stock_card(d, ui_key_prefix):
    strategy_html = f"""
<div style="background:#1a1c23; border-radius:6px; padding:12px; margin-bottom:12px; border: 1px solid #333; border-left: 4px solid #3498db;">
<div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">
<span style="color:#888; font-size:13px;">基準防線 (MA60季線)</span>
<strong style="color:#fff; font-size:14px;">{d['cost']}</strong>
</div>
<div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">
<span style="color:#888; font-size:13px;">🎯 最佳入場區</span>
<strong style="color:{d['color']}; font-size:15px;">[ {d['buy_zone']} ]</strong>
</div>
<div style="display:flex; justify-content:space-between; align-items:center;">
<span style="color:#888; font-size:13px;">🛡️ 預估撤退/保本點</span>
<strong style="color:{d['exit_color']}; font-size:15px;">{d['exit_price']} <span style="font-size:12px; color:#aaa;">({d['exit_s'].split('：')[0] if '：' in d['exit_s'] else ''})</span></strong>
</div>
</div>
"""
    gain_color = '#ff4d4d' if d['gain']>0 else ('#00FF00' if d['gain']<0 else '#aaaaaa')
    gain_bg = '#3a1515' if d['gain']>0 else ('#153a20' if d['gain']<0 else '#333333')
    
    price_badge = ""
    # 取消強制遮蔽，上調警戒線至 400，給予觀測自由
    if d['price'] > 400:
        price_badge = "<span style='font-size:14px; background-color:#e74c3c; color:white; padding:3px 8px; border-radius:4px; margin-left:10px;'>⚠️ >400元 (高價警戒)</span>"

    html_card = f"""
<div style="border: 2px solid {d['color']}; border-radius: 8px; padding: 15px; background-color: #16191f; margin-bottom: 5px;">
<div class="my-tooltip" style="font-weight:bold; font-size:18px; margin-bottom:5px;">{d['name']} ({d['code']}) | 🛡️ {d['shd']}</div>
<div class="my-tooltip" style="font-size:32px; font-weight:bold; margin-bottom: 10px; display:flex; align-items:center; gap:12px;">
{d['price']:.2f} {price_badge}
<span style="font-size:16px; color:{gain_color}; background-color:{gain_bg}; padding:4px 10px; border-radius:6px; border: 1px solid {gain_color}40; line-height:1;">{d['gain']:+.1f}%</span>
</div>
<div style="margin-bottom: 15px;">
<span class="info-badge">{d['chip']}</span>
<span class="info-badge">📊 {d['val']}</span>
<span class="info-badge">{d['kdj']}</span>
</div>
{d['buy_html']}     
{d['spotter_html']} 
{d['jail_html']}    
<div style="background:#2b2b36; border-radius:5px; padding:10px; display:flex; justify-content:space-between; text-align:center; margin-bottom:10px;">
<div style="flex:1; color:#aaa; font-size:12px;">開盤<br><span style="color:#fff; font-size:15px; font-weight:bold;">{d['open']:.1f}</span></div>
<div style="flex:1; color:#aaa; font-size:12px;">最高<br><span style="color:#fff; font-size:15px; font-weight:bold;">{d['high']:.1f}</span></div>
<div style="flex:1; color:#aaa; font-size:12px;">最低<br><span style="color:#fff; font-size:15px; font-weight:bold;">{d['low']:.1f}</span></div>
<div style="flex:1; color:#aaa; font-size:12px;">總量<br><span style="color:#fff; font-size:15px; font-weight:bold;">{d['vol']}張</span></div>
</div>
{strategy_html}
<div style="background:{d['signal_bg']}; padding:10px; border-radius:6px; text-align:center; margin-bottom:10px; border: 1px solid {d['color']}40;">
<span style="color:#aaa; font-size:12px;">總部指令判定</span><br>
<strong style="color:{d['color']}; font-size:18px;">{d['signal']}</strong>
</div>
</div>
"""
    st.markdown(html_card, unsafe_allow_html=True)
    
    is_unknown_intel = "?" in d['raw_data']
    if is_unknown_intel: st.markdown("<div style='color:#f39c12; font-size:13px; font-weight:bold; margin-bottom:10px;'>⚠️ 偵測到未知情報！請向 CEO 獲取參數並於下方設定。</div>", unsafe_allow_html=True)

    is_pinned = d['code'] in st.session_state.pinned_stocks
    
    with st.expander(f"💼 建立陣地 或 📌鎖定追蹤 ({d['name']})"):
        c1, c2 = st.columns(2)
        sim_cost = c1.number_input("進場成本", value=float(d['price']), key=f"c_{ui_key_prefix}_{d['code']}")
        sim_qty = c2.number_input("建倉張數", value=1.0, key=f"q_{ui_key_prefix}_{d['code']}")
        
        new_shd, new_chip, new_val = "4", "0", "0"
        if is_unknown_intel:
            ic1, ic2, ic3 = st.columns(3)
            new_shd = ic1.selectbox("盾", ["1", "2", "3", "4", "5"], index=3, key=f"ishd_{ui_key_prefix}_{d['code']}")
            new_chip = ic2.selectbox("籌碼", ["0", "1", "2"], index=0, format_func=lambda x: CHIP_MAP[x][:5], key=f"ichip_{ui_key_prefix}_{d['code']}")
            new_val = ic3.selectbox("位階", ["0", "1", "2", "3"], index=0, format_func=lambda x: VAL_MAP[x][:5], key=f"ival_{ui_key_prefix}_{d['code']}")
        else:
            parts = d['raw_data'].split(":")
            new_shd = parts[1] if len(parts)>1 else "4"
            new_chip = parts[3] if len(parts)>3 else "0"
            new_val = parts[4] if len(parts)>4 else "0"

        compiled_raw_data = f"{d['code']}:{new_shd}:0:{new_chip}:{new_val}:0"
        
        bc1, bc2 = st.columns(2)
        with bc1:
            if not is_pinned:
                st.markdown("<div class='pin-btn'>", unsafe_allow_html=True)
                if st.button(f"📌 僅鎖定雷達 (存檔)", key=f"pinbtn_{ui_key_prefix}_{d['code']}", use_container_width=True):
                    if len(st.session_state.pinned_stocks) >= MAX_CAPACITY: st.error(f"🚨 雷達滿載！")
                    else:
                        st.session_state.pinned_stocks[d['code']] = {'raw_data': compiled_raw_data, 'cat': d['cat']}
                        sync_state_to_url()
                        st.rerun()
                st.markdown("</div>", unsafe_allow_html=True)
            else:
                st.markdown("<div class='unpin-btn'>", unsafe_allow_html=True)
                if st.button(f"❌ 移除鎖定", key=f"unpinbtn_{ui_key_prefix}_{d['code']}", use_container_width=True):
                    del st.session_state.pinned_stocks[d['code']]
                    sync_state_to_url()
                    st.rerun()
                st.markdown("</div>", unsafe_allow_html=True)
            
        with bc2:
            st.markdown("<div class='buy-btn'>", unsafe_allow_html=True)
            if st.button(f"⚡ 轉入作戰庫存", key=f"buy_{ui_key_prefix}_{d['code']}", use_container_width=True):
                if len(st.session_state.portfolio) >= MAX_CAPACITY: st.error(f"🚨 庫存滿載！")
                else:
                    st.session_state.portfolio[d['code']] = {
                        "entry_price": round(sim_cost, 2), "qty": round(sim_qty, 3),
                        "raw_data": compiled_raw_data, "cat": d['cat']
                    }
                    if d['code'] in st.session_state.pinned_stocks: del st.session_state.pinned_stocks[d['code']]
                    sync_state_to_url()
                    st.rerun()
            st.markdown("</div>", unsafe_allow_html=True)
    st.markdown("</div>", unsafe_allow_html=True)

if search_query:
    with st.spinner("📡 連線抓取即時報價中..."):
        clean_code = re.split(r'[,\s、，]+', search_query)[0].replace('.TW', '').replace('.TWO', '')
        REV_TW_STOCKS = {v: k for k, v in TW_STOCKS.items()}
        code_to_scan = REV_TW_STOCKS.get(clean_code, clean_code)

        symbol_data = f"{code_to_scan}:?:?:?:?"
        d = calculate_tactical_signals(symbol_data, "search")
        if d:
            if d['code'] not in st.session_state.portfolio and d['code'] not in st.session_state.pinned_stocks:
                cols = st.columns(2)
                with cols[0]: render_stock_card(d, ui_key_prefix="search_res")
            else: st.warning(f"💡 觀測員提示：【{d['name']} ({d['code']})】已在您的雷達或庫存中。")
        else: st.error(f"🚨 查無情報：【{code_to_scan}】。請確認是否為有效台股代號。")

def render_portfolio_card(code, p_data):
    d = calculate_tactical_signals(p_data['raw_data'], p_data['cat'])
    if not d: return 
    
    entry_price = p_data['entry_price']
    qty = p_data['qty']
    real_profit, real_roi = calc_real_profit(entry_price, d['price'], qty)
    
    is_hard_stop = real_roi <= -10.0
    p_color = '#e74c3c' if is_hard_stop else ('#ff4d4d' if real_profit > 0 else '#00FF00')
    border_style = f"4px solid {p_color}" if is_hard_stop else f"3px solid {p_color}"
    bg_color = "#3a1515" if is_hard_stop else "#1a1a24"
    
    stop_warning = ""
    if is_hard_stop: stop_warning = "<div class='my-tooltip' style='background:#e74c3c; color:#fff; font-weight:bold; text-align:center; padding:8px; border-radius:5px; margin-bottom:10px; display:block; width:100%;'>🚨 觸發 -10% 鐵血停損，立即清倉！🚨</div>"
    
    strategy_html = f"""
<div style="background:#1a1c23; border-radius:6px; padding:12px; margin-bottom:12px; border: 1px solid #333; border-left: 4px solid #3498db;">
<div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">
<span style="color:#888; font-size:13px;">基準防線 (MA60季線)</span><strong style="color:#fff; font-size:14px;">{d['cost']}</strong>
</div>
<div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">
<span style="color:#888; font-size:13px;">🎯 最佳入場區</span><strong style="color:{d['color']}; font-size:15px;">[ {d['buy_zone']} ]</strong>
</div>
<div style="display:flex; justify-content:space-between; align-items:center;">
<span style="color:#888; font-size:13px;">🛡️ 預估撤退/保本點</span><strong style="color:{d['exit_color']}; font-size:15px;">{d['exit_price']}</strong>
</div></div>"""

    gain_color = '#ff4d4d' if d['gain']>0 else ('#00FF00' if d['gain']<0 else '#aaaaaa')
    gain_bg = '#3a1515' if d['gain']>0 else ('#153a20' if d['gain']<0 else '#333333')
    
    price_badge = ""
    if d['price'] > 400:
        price_badge = "<span style='font-size:14px; background-color:#e74c3c; color:white; padding:3px 8px; border-radius:4px; margin-left:10px;'>⚠️ >400元 (高價警戒)</span>"

    p_html = f"""
<div style="border: {border_style}; border-radius: 8px; padding: 15px; background-color: {bg_color}; margin-bottom: 5px; box-shadow: 0 0 15px {p_color}40;">
{stop_warning}
<div style="display:flex; justify-content:space-between; align-items:center; border-bottom: 1px solid #444; padding-bottom:10px; margin-bottom:10px;">
<div style="font-weight:bold; font-size:20px;">{d['name']} ({code})</div>
<div style="font-size:20px; font-weight:bold; color:#fff; display:flex; align-items:center; gap:10px;">
現價 {d['price']:.2f} {price_badge} <span style="font-size:14px; color:{gain_color}; background-color:{gain_bg}; padding:3px 8px; border-radius:4px; border: 1px solid {gain_color}40;">{d['gain']:+.1f}%</span>
</div></div>
{d['buy_html']}{d['spotter_html']}{d['jail_html']}
{strategy_html}
<div style="background:{d['signal_bg']}; padding:10px; border-radius:6px; text-align:center; margin-bottom:10px; border: 1px solid {d['color']}40;">
<span style="color:#aaa; font-size:12px;">總部指令判定</span><br>
<strong style="color:{d['color']}; font-size:18px;">{d['signal']}</strong>
</div>
<div style="display:flex; justify-content:space-between; margin-bottom: 15px;">
<div style="color:#aaa;">建倉成本: <strong style="color:#fff;">{entry_price:.2f}</strong></div>
<div style="color:#aaa;">庫存張數: <strong style="color:#fff;">{qty}</strong></div>
</div>
<div style="background:#000; padding:15px; border-radius:8px; text-align:center; margin-bottom:15px; display:block; width:100%;">
<div style="color:#aaa; font-size:14px; margin-bottom:5px;">💰 即時未實現淨損益</div>
<div style="font-size:36px; font-weight:bold; color:{p_color};">{real_profit:+,.0f} 元</div>
<div style="font-size:18px; color:{p_color};">({real_roi:+.2f}%)</div>
</div></div>"""
    st.markdown(p_html, unsafe_allow_html=True)
    st.markdown("<div class='sell-btn'>", unsafe_allow_html=True)
    if st.button(f"🚪 撤退清倉 (移除)", key=f"sell_{code}"):
        del st.session_state.portfolio[code]
        sync_state_to_url()
        st.rerun()
    st.markdown("</div>", unsafe_allow_html=True)

if st.session_state.portfolio:
    st.markdown(f"<h2 style='color:#ff4d4d; margin-top:20px; border-bottom: 2px solid #ff4d4d; padding-bottom:5px;'>💼 狙擊手作戰庫存 ({len(st.session_state.portfolio)}/{MAX_CAPACITY})</h2>", unsafe_allow_html=True)
    cols = st.columns(2)
    for i, (code, p_data) in enumerate(list(st.session_state.portfolio.items())):
        with cols[i % 2]: render_portfolio_card(code, p_data)

if st.session_state.pinned_stocks:
    st.markdown(f"<h2 style='color:#f1c40f; margin-top:20px; border-bottom: 2px solid #f1c40f; padding-bottom:5px;'>⭐ 觀測員警戒雷達 ({len(st.session_state.pinned_stocks)}/{MAX_CAPACITY})</h2>", unsafe_allow_html=True)
    cols = st.columns(2)
    for i, (code, p_data) in enumerate(list(st.session_state.pinned_stocks.items())):
        if code in st.session_state.portfolio: continue
        d = calculate_tactical_signals(p_data['raw_data'], p_data['cat'])
        if d:
            with cols[i % 2]: render_stock_card(d, ui_key_prefix="pinned")

if st.session_state.intel_mission:
    st.markdown("<h2 style='color:#9b59b6; margin-top:30px; border-bottom: 2px solid #9b59b6; padding-bottom:5px;'>📡 總部最新派發任務</h2>", unsafe_allow_html=True)
    with st.spinner("📡 總部連線解析中..."):
        cols = st.columns(2)
        valid_count = 0
        for symbol_data in st.session_state.intel_mission:
            d = calculate_tactical_signals(symbol_data, "intel")
            if not d: continue
            if d['code'] in st.session_state.portfolio or d['code'] in st.session_state.pinned_stocks: continue 
            with cols[valid_count % 2]:
                render_stock_card(d, ui_key_prefix="intel")
            valid_count += 1

st.markdown("---")
with st.expander("📘 AI 幕僚通訊暗號本 (總指揮專用)"):
    st.markdown("""
    在聊天室直接輸入以下指令，獲取 **戰術密碼 (INTEL CODE)**：
    * **`指令1`**：每日盤後全域掃描
    * **`指令2`**：高殖利率防禦狙擊
    * **`指令3`**：巨鯨籌碼突擊掃描
    * **`指令4 [代號]`**：單檔深度情報掃描
    * **`指令5`**：處置股逃命預警
    """)
