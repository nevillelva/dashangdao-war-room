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
params = st.query_params

if 'authenticated' not in st.session_state:
    st.session_state.authenticated = (params.get("auth") == "54088")

# 恢復為極致低調的 54088 登入大門
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

if 'url_loaded' not in st.session_state:
    st.session_state.pinned_stocks = {}
    st.session_state.portfolio = {}
    
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
        
    port_list = [f"{k}@{v['entry_price']}@{v['qty']}@{v['raw_data']}@{v['cat']}" for k, v in st.session_state.portfolio.items()]
    if port_list: st.query_params["p_port"] = ",".join(port_list)
    elif "p_port" in st.query_params: del st.query_params["p_port"]

# ==========================================
# 📡 系統參數
# ==========================================
main_raw = params.get("main", "").split(",")
sub_raw = params.get("sub", "").split(",")
cycle_raw = params.get("cycle", "").split(",")
topic_raw = params.get("topic", "").split(",")
yield_raw = params.get("yield", "").split(",")

TW_STOCKS = {"2330":"台積電", "2317":"鴻海", "2382":"廣達", "3231":"緯創", "1519":"華城", "2603":"長榮", "2618":"長榮航", "2609":"陽明", "2615":"萬海", "2731":"雄獅", "3293":"鈊象", "2542":"興富發", "3005":"神基", "3481":"群創", "2454":"聯發科", "3008":"大立光", "8454":"富邦媒", "2303":"聯電"}
CHIP_MAP = {"1": "🐳 巨鯨進駐", "2": "🩸 外資提款", "0": "⚖️ 籌碼平穩"}
VAL_MAP = {"1": "🟢 便宜階", "2": "🟡 合理階", "3": "🔴 昂貴階", "0": "⚪ 未定階"}

def safe_int(val, default=0):
    try: return int(val) if val else default
    except: return default

def safe_float(val, default=None):
    try: return float(val) if val else default
    except: return default

@st.cache_data(ttl=300)
def get_market_weather():
    try:
        taiex = yf.Ticker("^TWII").history(period="1mo")
        taiex = taiex.dropna(subset=['Close'])
        if len(taiex) < 2: return False, False, 0.0
        current_idx = taiex['Close'].iloc[-1]
        ma20_idx = taiex['Close'].rolling(window=20).mean().iloc[-1]
        daily_change = ((current_idx - taiex['Close'].iloc[-2]) / taiex['Close'].iloc[-2]) * 100
        return current_idx < ma20_idx, daily_change <= -1.5, daily_change
    except: return False, False, 0.0

is_bear_market, is_black_swan, market_change = get_market_weather()

# ==========================================
# 🧠 核心量化演算法 (內建觀測員與處置防禦)
# ==========================================
@st.cache_data(ttl=10)
def calculate_tactical_signals(symbol_data, category_type="main"):
    try:
        parts = symbol_data.split(":")
        if not parts[0].strip(): return None
        symbol = parts[0].strip()
        override_shd_raw = safe_int(parts[1], 4) if len(parts) > 1 else 4
        override_cost = safe_float(parts[2], None) if len(parts) > 2 else None
        if override_cost and override_cost <= 0: override_cost = None
        chip_code = parts[3] if len(parts) > 3 else "0"
        val_code = parts[4] if len(parts) > 4 else "0"
        
        ticker = yf.Ticker(f"{symbol}.TW")
        hist = ticker.history(period="6mo")
        if hist.empty or 'Close' not in hist.columns:
            ticker = yf.Ticker(f"{symbol}.TWO")
            hist = ticker.history(period="6mo")
        if hist.empty or 'Close' not in hist.columns: return None
        hist = hist.dropna(subset=['Close', 'Open', 'High', 'Low', 'Volume'])
        if len(hist) < 15: return None 

        # 暴力壓力測試防呆：加入 NoneType 與型別異常過濾
        try:
            val_last = ticker.fast_info.last_price
            val_prev = ticker.fast_info.previous_close
            
            if val_last is None or math.isnan(val_last) or val_last <= 0:
                raise ValueError("Invalid last price")
            current_price = float(val_last)
            
            if val_prev is None or math.isnan(val_prev) or val_prev <= 0:
                prev_price = max(float(hist['Close'].iloc[-2]), 0.001)
            else:
                prev_price = float(val_prev)
        except Exception:
            current_price = float(hist['Close'].iloc[-1])
            prev_price = max(float(hist['Close'].iloc[-2]), 0.001)

        open_p = float(hist['Open'].iloc[-1])
        high_p = float(hist['High'].iloc[-1])
        low_p = float(hist['Low'].iloc[-1])
        
        gain = ((current_price - prev_price) / prev_price) * 100
        vol = int(hist['Volume'].iloc[-1] / 1000)
        vol_5d = hist['Volume'].iloc[-6:-1].mean() / 1000 if len(hist) >= 6 else vol
        vol_5d = max(vol_5d, 0.01) 
        
        ma5 = hist['Close'].rolling(window=min(5, len(hist))).mean().iloc[-1]
        ma60 = hist['Close'].rolling(window=min(60, len(hist))).mean().iloc[-1]
        
        macd_line = hist['Close'].ewm(span=12, adjust=False).mean() - hist['Close'].ewm(span=26, adjust=False).mean()
        signal_line = macd_line.ewm(span=9, adjust=False).mean()
        macd_hist = macd_line - signal_line
        macd_golden_cross = (macd_hist.iloc[-2] < 0) and (macd_hist.iloc[-1] > 0) if len(macd_hist) > 1 else False
        
        low_min = hist['Low'].rolling(window=min(9, len(hist))).min()
        high_max = hist['High'].rolling(window=min(9, len(hist))).max()
        rsv = (hist['Close'] - low_min) / (high_max - low_min + 1e-9) * 100
        hist['K'] = rsv.fillna(50).ewm(com=2, adjust=False).mean()
        hist['D'] = hist['K'].fillna(50).ewm(com=2, adjust=False).mean()
        k, d = hist['K'].iloc[-1], hist['D'].iloc[-1]
        kdj_golden_cross = (k < 40) and (hist['K'].iloc[-2] < hist['D'].iloc[-2]) and (k > d) if len(hist) > 1 else False
        kdj_signal = "📈 低檔金叉" if kdj_golden_cross else ("📉 高檔死叉" if (k>70 and k<d) else "〰️ KDJ 震盪")

        # ---------------------------------------------
        # 🚨 證交所處置預警系統 (6日漲幅25%防禦線)
        # ---------------------------------------------
        jail_html = ""
        if len(hist) >= 6:
            close_6d_ago = float(hist['Close'].iloc[-6])
            limit_price_6d = close_6d_ago * 1.25
            return_6d = ((current_price - close_6d_ago) / close_6d_ago) * 100
            
            jail_color = "#2ecc71"
            jail_status = f"安全 (累計漲幅 {return_6d:.1f}%)"
            
            if return_6d >= 20.0:
                jail_color = "#e74c3c"
                jail_status = f"🔥 極度危險！距處置紅線僅差 {(limit_price_6d - current_price):.1f} 元"
            elif return_6d >= 12.0:
                jail_color = "#f39c12"
                jail_status = f"⚠️ 漲幅過熱 (累計漲幅 {return_6d:.1f}%)"
                
            jail_html = f"<div style='background:#1a1a24; padding:6px 12px; border-radius:5px; margin-bottom:8px; border-left: 4px solid {jail_color};'><div style='font-size:11px; color:#aaa;'>⚖️ 處置與注意股雷達：<strong style='color:{jail_color}; font-size:12px;'>{jail_status}</strong></div><div style='font-size:11px; color:#888;'>【證交所 25% 緊閉紅線】：今日收盤若 ≥ <strong style='color:#e74c3c;'>{limit_price_6d:.1f}</strong> 元將觸發警報</div></div>"
        # ---------------------------------------------

        # ---------------------------------------------
        # 🎯 觀測員雷達：賣出三要件判定邏輯
        # ---------------------------------------------
        is_huge_vol = vol > (vol_5d * 2.0)               
        is_black_k = current_price < open_p and gain < 0 
        is_break_ma5 = current_price < ma5               
        
        sell_cond_count = sum([is_huge_vol, is_black_k, is_break_ma5])
        spotter_status, spotter_color = "🟢 陣地安全，續抱", "#2ecc71"
        if sell_cond_count == 3: spotter_status, spotter_color = "🔴 三要件確立，立即撤退！", "#e74c3c"
        elif sell_cond_count == 2: spotter_status, spotter_color = "🟡 多重警訊，提高警戒", "#f1c40f"
        elif sell_cond_count == 1: spotter_status, spotter_color = "🟡 注意單一異常訊號", "#f39c12"

        spotter_html = f"<div style='background:#1a1a24; padding:6px 12px; border-radius:5px; margin-bottom:12px; border-left: 4px solid {spotter_color}; box-shadow: 0 2px 5px rgba(0,0,0,0.5);'><div style='font-size:11px; color:#aaa; margin-bottom:4px;'>🎯 觀測員三要件：<strong style='color:{spotter_color}; font-size:13px;'>{spotter_status}</strong></div><div style='font-size:12px; color:#ccc; display:flex; justify-content:space-between;'><span>{'🔴' if is_huge_vol else '⚪'} 爆量(>{vol_5d*2:.0f}張)</span><span>{'🔴' if is_black_k else '⚪'} 實體黑K</span><span>{'🔴' if is_break_ma5 else '⚪'} 破5MA({ma5:.1f})</span></div></div>"
        # ---------------------------------------------

        main_cost = override_cost if override_cost else round(ma60, 1)
        buy_low, buy_high = round(main_cost * 0.97, 1), round(main_cost * 1.03, 1)
        diff_from_cost = ((current_price - max(main_cost, 0.001)) / max(main_cost, 0.001)) * 100

        if (buy_low <= current_price <= buy_high): signal_text, color_border = "🎯 進入打擊區 (可建倉)", "#2ecc71"
        elif diff_from_cost < -5.0: signal_text, color_border = "⚠️ 嚴重破線/破底 (勿入)", "#e74c3c"
        elif diff_from_cost > 5.0: signal_text, color_border = "🚀 高檔觀察 (太貴勿追)", "#e67e22"
        else: signal_text, color_border = "🛡️ 區間震盪 (等待落點)", "#ccc"

        if val_code == "3": exit_s, exit_p, exit_c, exit_bg = "🔴 價值滿水：分批了結", f"{current_price:.1f}", "#e74c3c", "#3a1515"
        elif diff_from_cost >= 15.0: exit_s, exit_p, exit_c, exit_bg = "🛡️ 階梯保本：鎖定波段", f"{max(current_price * 0.92, main_cost):.1f}", "#3498db", "#152a3a"
        else: exit_s, exit_p, exit_c, exit_bg = "🚪 波段底線：跌破季線撤退", f"{main_cost * 0.95:.1f}", "#8e44ad", "#2c153a"

        return {"name": TW_STOCKS.get(symbol, symbol), "code": symbol, "price": current_price, "gain": gain, "cost": main_cost, "cost_label": "長線季線(MA60)", "buy_zone": f"{buy_low} - {buy_high}", "shd": override_shd_raw, "chip": CHIP_MAP.get(chip_code, "⚖️"), "val": VAL_MAP.get(val_code, "⚪"), "kdj": kdj_signal, "signal": signal_text, "color": color_border, "extra_badge": "", "exit_s": exit_s, "exit_price": exit_p, "exit_color": exit_c, "exit_bg": exit_bg, "vol": vol, "open": open_p, "high": high_p, "low": low_p, "raw_data": symbol_data, "cat": category_type, "spotter_html": spotter_html, "jail_html": jail_html}
    except Exception as e: return None

@st.cache_data
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
.buy-btn > button { background-color: #e74c3c !important; width: 100%; margin-top: 10px; }
.sell-btn > button { background-color: #2ecc71 !important; width: 100%; margin-top: 10px; }
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
    if st.button("🔄 Sync"):
        st.cache_data.clear()
        st.rerun()
with col_logout:
    if st.button("🔒 Lock"):
        st.session_state.authenticated = False
        st.query_params.clear()
        st.rerun()

st.markdown(f"<div style='text-align:right; color:#888; font-size:12px; margin-bottom:10px;'>觀測雷達運作中 | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</div>", unsafe_allow_html=True)

st.markdown("<h3 style='color:#3498db; margin-top:10px; border-bottom: 2px solid #3498db; padding-bottom:5px;'>🔍 狙擊手探測雷達</h3>", unsafe_allow_html=True)
search_query = st.text_input("📝 代號 (如：2330 2603)：", key="search_input")

def render_stock_card(d, ui_key_prefix):
    html_card = f"""
<div style="border: 2px solid {d['color']}; border-radius: 8px; padding: 15px; background-color: #16191f; margin-bottom: 5px;">
<div class="my-tooltip" style="font-weight:bold; font-size:18px; margin-bottom:5px;">{d['name']} ({d['code']}) | 🛡️ 價值盾: {d['shd']}分<span class="my-tooltiptext">價值盾：5分為滿分。絕對尊重財報防禦力。</span></div>
<div class="my-tooltip" style="font-size:32px; font-weight:bold; margin-bottom: 10px; display:block;">{d['price']:.2f} <span style="font-size:18px; color:{'#ff4d4d' if d['gain']>0 else '#00FF00'};">{d['gain']:+.1f}%</span><span class="my-tooltiptext">市場即時報價與漲跌幅</span></div>
<div style="margin-bottom: 15px;">
<span class="info-badge my-tooltip">{d['chip']}<span class="my-tooltiptext">三大法人動向：判斷有無主力護航</span></span>
<span class="info-badge my-tooltip">📊 {d['val']}<span class="my-tooltiptext">財報狗位階：評估目前股價是否處於便宜區間</span></span>
<span class="info-badge my-tooltip">{d['kdj']}<span class="my-tooltiptext">KDJ(9,3,3)指標：捕捉低檔轉折</span></span>
{d['extra_badge']}
</div>

{d['spotter_html']} <!-- 插入觀測員雷達回報 -->
{d['jail_html']}    <!-- 插入處置與注意股雷達 -->

<div style="background:#2b2b36; border-radius:5px; padding:10px; display:flex; justify-content:space-between; text-align:center; margin-bottom:10px;">
<div class="my-tooltip" style="flex:1; color:#aaa; font-size:12px;">開盤<br><span style="color:#fff; font-size:15px; font-weight:bold;">{d['open']:.1f}</span><span class="my-tooltiptext">今日開盤價</span></div>
<div class="my-tooltip" style="flex:1; color:#aaa; font-size:12px;">最高<br><span style="color:#fff; font-size:15px; font-weight:bold;">{d['high']:.1f}</span><span class="my-tooltiptext">今日最高價</span></div>
<div class="my-tooltip" style="flex:1; color:#aaa; font-size:12px;">最低<br><span style="color:#fff; font-size:15px; font-weight:bold;">{d['low']:.1f}</span><span class="my-tooltiptext">今日最低價</span></div>
<div class="my-tooltip" style="flex:1; color:#aaa; font-size:12px;">總量<br><span style="color:#fff; font-size:15px; font-weight:bold;">{d['vol']}張</span><span class="my-tooltiptext">今日成交量</span></div>
</div>
<div class="my-tooltip" style="background:#2b2b36; border-radius:5px; padding:10px; margin-bottom:10px; text-align:center; display:block; width:100%;">
<span style="color:#aaa;">{d['cost_label']}: <strong style="color:#fff; font-size:16px;">{d['cost']}</strong></span><br>
<span style="color:{d['color']}; font-weight:bold;">🎯 打擊區: [ {d['buy_zone']} ]</span>
<span class="my-tooltiptext">防守線±3%的緩衝安全區間，跌入此區即為最佳開火位置。</span>
</div>
<div class="my-tooltip" style="background:{d['exit_bg']}; color:{d['exit_color']}; font-weight:bold; text-align:center; padding:8px; border-radius:5px; margin-bottom:10px; display:block; width:100%;">
{d['exit_s']} ({d['exit_price']})
<span class="my-tooltiptext">系統根據獲利%數與大盤狀況，自動切換撤退建議。</span>
</div>
<div style="font-size:13px; color:#ddd; margin-bottom:10px;">
📌 狀態: <strong style="color:{d['color']}">{d['signal']}</strong><br>
</div>
</div>
"""
    st.markdown(html_card, unsafe_allow_html=True)
    
    is_pinned = d['code'] in st.session_state.pinned_stocks
    pin_action = st.checkbox("📌 鎖定追蹤 (永久保存)", value=is_pinned, key=f"pin_{ui_key_prefix}_{d['code']}")
    if pin_action and not is_pinned:
        st.session_state.pinned_stocks[d['code']] = {'raw_data': d['raw_data'], 'cat': d['cat']}
        sync_state_to_url()
        st.rerun()
    elif not pin_action and is_pinned:
        del st.session_state.pinned_stocks[d['code']]
        sync_state_to_url()
        st.rerun() 

    with st.expander(f"💼 建立狙擊陣地 ({d['name']})"):
        st.markdown("<div style='color:#888; font-size:12px; margin-bottom:10px;'>建立真實成本，系統將啟動 -10% 強制停損機制。</div>", unsafe_allow_html=True)
        c1, c2 = st.columns(2)
        sim_cost = c1.number_input("進場成本價", value=float(d['price']), key=f"c_{ui_key_prefix}_{d['code']}")
        sim_qty = c2.number_input("建倉張數", value=1.0, key=f"q_{ui_key_prefix}_{d['code']}")
        
        st.markdown("<div class='buy-btn'>", unsafe_allow_html=True)
        if st.button(f"⚡ 轉入作戰庫存", key=f"buy_{ui_key_prefix}_{d['code']}"):
            st.session_state.portfolio[d['code']] = {
                "entry_price": round(sim_cost, 2),
                "qty": sim_qty,
                "raw_data": d['raw_data'],
                "cat": d['cat']
            }
            if d['code'] in st.session_state.pinned_stocks:
                del st.session_state.pinned_stocks[d['code']]
            sync_state_to_url()
            st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)

if search_query:
    codes = [c.strip() for c in re.split(r'[,\s、，]+', search_query) if c.strip()]
    cols = st.columns(2)
    valid_count = 0
    for code in codes:
        symbol_data = code if ":" in code else f"{code}:4:0:0:0"
        d = calculate_tactical_signals(symbol_data, "search")
        if d and d['code'] not in st.session_state.portfolio and d['code'] not in st.session_state.pinned_stocks:
            with cols[valid_count % 2]:
                render_stock_card(d, ui_key_prefix="search_res")
            valid_count += 1

def render_portfolio_card(code, p_data):
    d = calculate_tactical_signals(p_data['raw_data'], p_data['cat'])
    if not d: return 
    
    entry_price = p_data['entry_price']
    qty = p_data['qty']
    real_profit, real_roi = calc_real_profit(entry_price, d['price'], qty)
    
    # 💥 -10% 鐵血斷頭台判定
    is_hard_stop = real_roi <= -10.0
    p_color = '#e74c3c' if is_hard_stop else ('#ff4d4d' if real_profit > 0 else '#00FF00')
    border_style = f"4px solid {p_color}" if is_hard_stop else f"3px solid {p_color}"
    bg_color = "#3a1515" if is_hard_stop else "#1a1a24"
    
    stop_warning = ""
    if is_hard_stop:
        stop_warning = "<div style='background:#e74c3c; color:#fff; font-weight:bold; text-align:center; padding:8px; border-radius:5px; margin-bottom:10px;'>🚨 觸發 -10% 鐵血停損，立即清倉！🚨</div>"
    
    p_html = f"""
<div style="border: {border_style}; border-radius: 8px; padding: 15px; background-color: {bg_color}; margin-bottom: 5px; box-shadow: 0 0 15px {p_color}40;">
{stop_warning}
<div style="display:flex; justify-content:space-between; align-items:center; border-bottom: 1px solid #444; padding-bottom:10px; margin-bottom:10px;">
<div class="my-tooltip" style="font-weight:bold; font-size:20px;">{d['name']} ({code})</div>
<div class="my-tooltip" style="font-size:20px; font-weight:bold; color:#fff;">現價 {d['price']:.2f}</div>
</div>
{d['spotter_html']} <!-- 庫存區顯示觀測員三要件雷達 -->
{d['jail_html']}    <!-- 庫存區顯示處置預警雷達 -->
<div style="display:flex; justify-content:space-between; margin-bottom: 15px;">
<div style="color:#aaa;">建倉成本: <strong style="color:#fff;">{entry_price:.2f}</strong></div>
<div style="color:#aaa;">庫存張數: <strong style="color:#fff;">{qty}</strong></div>
</div>
<div class="my-tooltip" style="background:#000; padding:15px; border-radius:8px; text-align:center; margin-bottom:15px; display:block; width:100%;">
<div style="color:#aaa; font-size:14px; margin-bottom:5px;">💰 即時未實現淨損益</div>
<div style="font-size:36px; font-weight:bold; color:{p_color};">{real_profit:+,.0f} 元</div>
<div style="font-size:18px; color:{p_color};">({real_roi:+.2f}%)</div>
<span class="my-tooltiptext">已扣除雙邊手續費與證交稅的真實損益</span>
</div>
</div>
"""
    st.markdown(p_html, unsafe_allow_html=True)

    st.markdown("<div class='sell-btn'>", unsafe_allow_html=True)
    if st.button(f"🚪 撤退清倉 (移除)", key=f"sell_{code}"):
        del st.session_state.portfolio[code]
        sync_state_to_url()
        st.rerun()
    st.markdown("</div>", unsafe_allow_html=True)

if st.session_state.portfolio:
    st.markdown("<h2 style='color:#ff4d4d; margin-top:20px; border-bottom: 2px solid #ff4d4d; padding-bottom:5px;'>💼 狙擊手作戰庫存</h2>", unsafe_allow_html=True)
    cols = st.columns(2)
    for i, (code, p_data) in enumerate(list(st.session_state.portfolio.items())):
        with cols[i % 2]:
            render_portfolio_card(code, p_data)

if st.session_state.pinned_stocks:
    st.markdown("<h2 style='color:#f1c40f; margin-top:20px; border-bottom: 2px solid #f1c40f; padding-bottom:5px;'>⭐ 觀測員警戒雷達</h2>", unsafe_allow_html=True)
    cols = st.columns(2)
    for i, (code, p_data) in enumerate(list(st.session_state.pinned_stocks.items())):
        if code in st.session_state.portfolio: continue
        d = calculate_tactical_signals(p_data['raw_data'], p_data['cat'])
        if d:
            with cols[i % 2]:
                render_stock_card(d, ui_key_prefix="pinned")

if main_raw and main_raw[0]:
    st.markdown("<h2 style='color:#9b59b6; margin-top:30px; border-bottom: 2px solid #9b59b6; padding-bottom:5px;'>📡 AI 觀測員派發：戰略標的</h2>", unsafe_allow_html=True)
    SECTIONS = [
        ("🔥 核心精選", main_raw, "main"), 
        ("🎯 短中期轉折", sub_raw, "sub"), 
        ("🌪️ 題材強勢", topic_raw, "topic"), 
        ("🌊 週期戰略", cycle_raw, "cycle"),
        ("💰 財報高殖利", yield_raw, "yield")
    ]
    for category, raw_codes, cat_type in SECTIONS:
        if not raw_codes or not raw_codes[0]: continue
        st.markdown(f"<h4 style='color:#ccc; margin-top:15px;'>{category}</h4>", unsafe_allow_html=True)
        cols = st.columns(2)
        valid_count = 0
        for symbol_data in raw_codes:
            d = calculate_tactical_signals(symbol_data, cat_type)
            if not d: continue
            if d['code'] in st.session_state.portfolio or d['code'] in st.session_state.pinned_stocks: 
                continue 
            with cols[valid_count % 2]:
                render_stock_card(d, ui_key_prefix=cat_type)
            valid_count += 1
