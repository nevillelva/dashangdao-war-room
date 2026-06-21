import streamlit as st
import yfinance as yf
import pandas as pd
import re
from datetime import datetime

st.set_page_config(layout="wide", page_title="作戰所")

# ==========================================
# 系統記憶體：初始化 鎖定雷達 與 實戰庫存
# ==========================================
params = st.query_params

if 'pinned_stocks' not in st.session_state:
    st.session_state.pinned_stocks = {}
    pinned_param = params.get("pinned", "")
    if pinned_param:
        for raw in pinned_param.split(","):
            if raw:
                # 只在開機第一次解碼
                from_url_code = raw.split(":")[0]
                st.session_state.pinned_stocks[from_url_code] = {"raw_data": raw}

if 'portfolio' not in st.session_state:
    st.session_state.portfolio = {}
    port_param = params.get("port", "")
    if port_param:
        for item in port_param.split(","):
            if item:
                try:
                    p_parts = item.split("_")
                    code = p_parts[0]
                    st.session_state.portfolio[code] = {
                        "entry_price": float(p_parts[1]),
                        "qty": float(p_parts[2]),
                        "raw_data": p_parts[3],
                        "cat": p_parts[4] if len(p_parts) > 4 else "search"
                    }
                except: pass

def update_url_state():
    if st.session_state.pinned_stocks:
        st.query_params["pinned"] = ",".join([v['raw_data'] for v in st.session_state.pinned_stocks.values() if 'raw_data' in v])
    else:
        if "pinned" in st.query_params: del st.query_params["pinned"]
        
    if st.session_state.portfolio:
        port_list = []
        for code, p in st.session_state.portfolio.items():
            # 壓力測試優化：強制限縮小數點位數，防止 URL 膨脹崩潰
            ep = round(float(p['entry_price']), 2)
            qt = round(float(p['qty']), 2)
            port_list.append(f"{code}_{ep}_{qt}_{p['raw_data']}_{p['cat']}")
        st.query_params["port"] = ",".join(port_list)
    else:
        if "port" in st.query_params: del st.query_params["port"]

main_raw = params.get("main", "").split(",")
sub_raw = params.get("sub", "").split(",")
cycle_raw = params.get("cycle", "").split(",")
topic_raw = params.get("topic", "").split(",")
yield_raw = params.get("yield", "").split(",")

TW_STOCKS = {
    "2330": "台積電", "2317": "鴻海", "2382": "廣達", "3231": "緯創", "1519": "華城",
    "2881": "富邦金", "2884": "玉山金", "2603": "長榮", "2618": "長榮航", "2609": "陽明",
    "2615": "萬海", "2731": "雄獅", "3293": "鈊象", "2542": "興富發", "3005": "神基",
    "3481": "群創", "2454": "聯發科", "3008": "大立光", "8454": "富邦媒", "2303": "聯電"
}

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

@st.cache_data(ttl=120)
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
        extra_param = safe_float(parts[5], 0.0) if len(parts) > 5 else 0.0
        is_double_dip = safe_int(parts[6], 0) if len(parts) > 6 else 0 
        
        ticker = yf.Ticker(f"{symbol}.TW")
        hist = ticker.history(period="6mo")
        if hist.empty or 'Close' not in hist.columns:
            ticker = yf.Ticker(f"{symbol}.TWO")
            hist = ticker.history(period="6mo")
            
        if hist.empty or 'Close' not in hist.columns: return None
        
        hist = hist.dropna(subset=['Close', 'Open', 'High', 'Low', 'Volume'])
        
        # 壓力測試優化：IPO 新股容錯。將 60 放寬至 15，避免新上市股票直接當機
        if len(hist) < 15: return None

        current_price = float(hist['Close'].iloc[-1])
        prev_price = max(float(hist['Close'].iloc[-2]), 0.001) 
        open_p = float(hist['Open'].iloc[-1])
        high_p = float(hist['High'].iloc[-1])
        low_p = float(hist['Low'].iloc[-1])
        
        gain = ((current_price - prev_price) / prev_price) * 100
        vol = int(hist['Volume'].iloc[-1] / 1000)
        
        # 避免上市未滿 6 天時的均量崩潰
        if len(hist) >= 6:
            vol_5d = hist['Volume'].iloc[-6:-1].mean() / 1000
        else:
            vol_5d = hist['Volume'].mean() / 1000
            
        vol_5d = max(vol_5d, 0.01) 
        
        # 壓力測試優化：min_periods=10 確保新股 MA 計算不回傳 NaN
        ma20 = hist['Close'].rolling(window=20, min_periods=10).mean().iloc[-1]
        ma60 = hist['Close'].rolling(window=60, min_periods=10).mean().iloc[-1]
        if pd.isna(ma60): ma60 = current_price  # 極端容錯
        
        macd_line = hist['Close'].ewm(span=12, adjust=False).mean() - hist['Close'].ewm(span=26, adjust=False).mean()
        signal_line = macd_line.ewm(span=9, adjust=False).mean()
        macd_hist = macd_line - signal_line
        macd_golden_cross = (macd_hist.iloc[-2] < 0) and (macd_hist.iloc[-1] > 0)
        
        low_min = hist['Low'].rolling(window=9, min_periods=1).min()
        high_max = hist['High'].rolling(window=9, min_periods=1).max()
        rsv = (hist['Close'] - low_min) / (high_max - low_min + 1e-9) * 100
        hist['K'] = rsv.fillna(50).ewm(com=2, adjust=False).mean()
        hist['D'] = hist['K'].fillna(50).ewm(com=2, adjust=False).mean()
        k, d = hist['K'].iloc[-1], hist['D'].iloc[-1]
        prev_k, prev_d = hist['K'].iloc[-2], hist['D'].iloc[-2]
        
        kdj_golden_cross = (k < 40) and (prev_k < prev_d) and (k > d)
        kdj_signal = "📈 低檔金叉" if kdj_golden_cross else ("📉 高檔死叉" if (k>70 and prev_k>prev_d and k<d) else "〰️ KDJ 震盪")

        main_cost = override_cost if override_cost else round(ma60, 1)
        buy_low = round(main_cost * 0.97, 1)
        buy_high = round(main_cost * 1.03, 1)
        buy_zone = f"{buy_low} - {buy_high}"
        diff_from_cost = ((current_price - main_cost) / main_cost) * 100
        shd_score = override_shd_raw

        in_strike_zone = (buy_low <= current_price <= buy_high)
        if in_strike_zone:
            signal_text, color_border = "🎯 進入打擊區 (可建倉)", "#2ecc71"
        elif diff_from_cost < -5.0:
            signal_text, color_border = "⚠️ 嚴重破線/破底 (勿入)", "#e74c3c"
        elif diff_from_cost > 5.0:
            signal_text, color_border = "🚀 高檔觀察 (太貴勿追)", "#e67e22"
        else:
            signal_text, color_border = "🛡️ 區間震盪 (等待落點)", "#ccc"

        anti_trap_warning = ""
        if vol_5d < 1.0:
            anti_trap_warning, color_border = "⚠️ 流動性陷阱：量能低迷！", "#f39c12"
        elif diff_from_cost < -5.0 and macd_hist.iloc[-1] < 0 and not kdj_golden_cross:
            anti_trap_warning, color_border = "🔪 嚴禁接刀：空方宣洩中！", "#e74c3c"
        elif val_code == "3" and vol > (vol_5d * 2) and gain < 0:
            anti_trap_warning, color_border = "🩸 高檔爆量收黑：主力出貨！", "#8e44ad"
            
        if anti_trap_warning:
            signal_text = anti_trap_warning

        if not anti_trap_warning and category_type not in ["cycle", "yield"]:
            if kdj_golden_cross and macd_golden_cross:
                signal_text, color_border = "✨ 雙重技術金叉共振 ✨", "#f1c40f"

        if val_code == "3":
            exit_s, exit_p, exit_c, exit_bg = "🔴 價值滿水：分批了結", f"{current_price:.1f}", "#e74c3c", "#3a1515"
        elif diff_from_cost >= 15.0:
            exit_s, exit_p, exit_c, exit_bg = "🛡️ 階梯保本：鎖定波段", f"{max(current_price * 0.92, main_cost):.1f}", "#3498db", "#152a3a"
        else:
            stop_loss = main_cost * 0.95
            exit_s, exit_p, exit_c, exit_bg = "🚪 鐵血紀律：跌破防守撤退", f"{stop_loss:.1f}", "#8e44ad", "#2c153a"

        today = datetime.now()
        cost_label, cycle_text, extra_badge = "幕僚防守線(MA60)", "等待戰略指示", ""
        
        return {
            "name": TW_STOCKS.get(symbol, symbol), "code": symbol, "price": current_price,
            "gain": gain, "cost": main_cost, "cost_label": cost_label, "buy_zone": buy_zone,
            "shd": shd_score, "chip": CHIP_MAP.get(chip_code, "⚖️"),
            "val": VAL_MAP.get(val_code, "⚪"), "kdj": kdj_signal, "signal": signal_text,
            "cycle": cycle_text, "color": color_border, "extra_badge": extra_badge,
            "exit_s": exit_s, "exit_price": exit_p, "exit_color": exit_c, "exit_bg": exit_bg, 
            "vol": vol, "open": open_p, "high": high_p, "low": low_p,
            "raw_data": symbol_data, "cat": category_type
        }
    except Exception as e: 
        return None

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

st.markdown("<h1 style='color:#FFB300;'>🦅 作戰所</h1>", unsafe_allow_html=True)

if is_black_swan: 
    st.markdown(f"<div class='my-tooltip' style='display:block; width:100%; background:#3a1515; border:1px solid #e74c3c; color:#fff; padding:10px; border-radius:8px; margin-bottom:20px; font-weight:bold;'>🚨 大盤暴跌 {market_change:.2f}%：防禦機制啟動，暫緩追高！<span class='my-tooltiptext'>大盤單日跌幅超過1.5%，系統啟動保護機制。</span></div>", unsafe_allow_html=True)

st.markdown(f"<div style='text-align:right; color:#888; font-size:12px; margin-bottom:10px;'>即時數據連線：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</div>", unsafe_allow_html=True)

# ==========================================
# 🆕 盤後動態掃描雷達 (支援模糊辨識)
# ==========================================
st.markdown("<h3 style='color:#3498db; margin-top:10px; border-bottom: 2px solid #3498db; padding-bottom:5px;'>🔍 盤後動態掃描雷達</h3>", unsafe_allow_html=True)
search_query = st.text_input("📝 輸入欲掃描之台股代號 (支援空白或逗號分隔，例如：2330 2603)", key="search_input")

# 如果一開機系統要載入 URL 中的 pinned，先幫它運算
for code, v_dict in list(st.session_state.pinned_stocks.items()):
    if 'name' not in v_dict:
        calc_d = calculate_tactical_signals(v_dict['raw_data'], "pinned")
        if calc_d: st.session_state.pinned_stocks[code] = calc_d

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
    
    # 釘選觸發：URL 狀態同步寫入
    is_pinned = d['code'] in st.session_state.pinned_stocks
    pin_action = st.checkbox("📌 鎖定追蹤 (寫入記憶體永不消失)", value=is_pinned, key=f"pin_{ui_key_prefix}_{d['code']}")
    if pin_action and not is_pinned:
        st.session_state.pinned_stocks[d['code']] = d
        update_url_state()
        st.rerun()
    elif not pin_action and is_pinned:
        del st.session_state.pinned_stocks[d['code']]
        update_url_state()
        st.rerun() 

    with st.expander(f"💼 戰術沙盤推演 ({d['name']})"):
        st.markdown("<div style='color:#888; font-size:12px; margin-bottom:10px;'>此區為兵推，實戰庫存請以券商APP為主。</div>", unsafe_allow_html=True)
        c1, c2 = st.columns(2)
        sim_cost = c1.number_input("模擬進場價", value=float(d['cost']), key=f"c_{ui_key_prefix}_{d['code']}")
        sim_qty = c2.number_input("模擬張數", value=1.0, key=f"q_{ui_key_prefix}_{d['code']}")
        
        st.markdown("<div class='buy-btn'>", unsafe_allow_html=True)
        if st.button(f"⚡ 轉入沙盤推演區", key=f"buy_{ui_key_prefix}_{d['code']}"):
            st.session_state.portfolio[d['code']] = {
                "entry_price": sim_cost,
                "qty": sim_qty,
                "raw_data": d['raw_data'],
                "cat": d['cat']
            }
            if d['code'] in st.session_state.pinned_stocks:
                del st.session_state.pinned_stocks[d['code']]
            update_url_state()
            st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)

if search_query:
    # 壓力測試優化：使用正則表達式，完美處理各種胖手指(全半形逗號、多重空白)
    codes = re.split(r'[,\s、，]+', search_query)
    cols = st.columns(2)
    valid_count = 0
    for code in codes:
        code = code.strip()
        if not code: continue
        
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
    p_color = '#ff4d4d' if real_profit > 0 else '#00FF00'
    
    p_html = f"""
<div style="border: 3px solid {p_color}; border-radius: 8px; padding: 15px; background-color: #1a1a24; margin-bottom: 5px; box-shadow: 0 0 15px {p_color}40;">
<div style="display:flex; justify-content:space-between; align-items:center; border-bottom: 1px solid #444; padding-bottom:10px; margin-bottom:10px;">
<div class="my-tooltip" style="font-weight:bold; font-size:20px;">{d['name']} ({code})</div>
<div class="my-tooltip" style="font-size:20px; font-weight:bold; color:#fff;">現價 {d['price']:.2f}</div>
</div>
<div style="display:flex; justify-content:space-between; margin-bottom: 15px;">
<div style="color:#aaa;">模擬成本: <strong style="color:#fff;">{entry_price:.2f}</strong></div>
<div style="color:#aaa;">模擬張數: <strong style="color:#fff;">{qty}</strong></div>
</div>
<div class="my-tooltip" style="background:#000; padding:15px; border-radius:8px; text-align:center; margin-bottom:15px; display:block; width:100%;">
<div style="color:#aaa; font-size:14px; margin-bottom:5px;">💰 模擬未實現淨損益</div>
<div style="font-size:36px; font-weight:bold; color:{p_color};">{real_profit:+,.0f} 元</div>
<div style="font-size:18px; color:{p_color};">({real_roi:+.2f}%)</div>
<span class="my-tooltiptext">已扣除雙邊手續費與證交稅。</span>
</div>
<div class="my-tooltip" style="background:{d['exit_bg']}; color:{d['exit_color']}; font-weight:bold; text-align:center; padding:8px; border-radius:5px; display:block; width:100%;">
{d['exit_s']} ({d['exit_price']})
</div>
</div>
"""
    st.markdown(p_html, unsafe_allow_html=True)

    st.markdown("<div class='sell-btn'>", unsafe_allow_html=True)
    if st.button(f"🚪 結束推演 (移除)", key=f"sell_{code}"):
        del st.session_state.portfolio[code]
        update_url_state()
        st.rerun()
    st.markdown("</div>", unsafe_allow_html=True)

if st.session_state.portfolio:
    st.markdown("<h2 style='color:#ff4d4d; margin-top:20px; border-bottom: 2px solid #ff4d4d; padding-bottom:5px;'>💼 戰術沙盤推演區 (記憶掛載)</h2>", unsafe_allow_html=True)
    cols = st.columns(2)
    for i, (code, p_data) in enumerate(list(st.session_state.portfolio.items())):
        with cols[i % 2]:
            render_portfolio_card(code, p_data)

if st.session_state.pinned_stocks:
    st.markdown("<h2 style='color:#f1c40f; margin-top:20px; border-bottom: 2px solid #f1c40f; padding-bottom:5px;'>⭐ 總指揮雷達 (記憶掛載)</h2>", unsafe_allow_html=True)
    cols = st.columns(2)
    for i, (code, d) in enumerate(list(st.session_state.pinned_stocks.items())):
        if code in st.session_state.portfolio: continue
        # 如果 d 只有 raw_data (剛從網址解碼但還沒算)，則算一下
        if 'name' not in d:
             d = calculate_tactical_signals(d['raw_data'], "pinned")
             st.session_state.pinned_stocks[code] = d
        if d:
            with cols[i % 2]:
                render_stock_card(d, ui_key_prefix="pinned")

# 執行：幕僚遙控器派發的戰略名單
if main_raw and main_raw[0]:
    st.markdown("<h2 style='color:#9b59b6; margin-top:30px; border-bottom: 2px solid #9b59b6; padding-bottom:5px;'>📡 幕僚派發：最新戰略標的</h2>", unsafe_allow_html=True)
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
