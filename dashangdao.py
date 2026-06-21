import streamlit as st
import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta

st.set_page_config(layout="wide", page_title="作戰所")

# ==========================================
# 系統記憶體：初始化 鎖定雷達 與 實戰庫存
# ==========================================
if 'pinned_stocks' not in st.session_state:
    st.session_state.pinned_stocks = {}
if 'portfolio' not in st.session_state:
    st.session_state.portfolio = {}

params = st.query_params
default_main = "2317:0:260:1:2,3231:0:158:0:1"
default_sub = "2881:0:73:1:1,2884:0:27:0:2,2603:0:185:1:1,2618:0:34:2:1,2609:0:70:0:1,2615:0:80:0:1,3481:0:14:0:1"
default_cycle = "2731:0:120:0:0:7"
default_topic = "1519:0:750:1:3"
default_yield = "2542:0:40:1:1:8:0,3005:0:115:1:2:7:1" 

main_raw = params.get("main", default_main).split(",")
sub_raw = params.get("sub", default_sub).split(",")
cycle_raw = params.get("cycle", default_cycle).split(",")
topic_raw = params.get("topic", default_topic).split(",")
yield_raw = params.get("yield", default_yield).split(",")

# [修復] 補齊所有股票代碼對照表
TW_STOCKS = {
    "2330": "台積電", "2317": "鴻海", "2382": "廣達", "3231": "緯創", "1519": "華城",
    "2881": "富邦金", "2884": "玉山金", "2603": "長榮", "2618": "長榮航", "2609": "陽明",
    "2615": "萬海", "2731": "雄獅", "3293": "鈊象", "2542": "興富發", "3005": "神基",
    "3481": "群創", "2454": "聯發科", "3008": "大立光", "8454": "富邦媒"
}

CHIP_MAP = {"1": "🐳 巨鯨進駐", "2": "🩸 外資提款", "0": "⚖️ 籌碼平穩"}
VAL_MAP = {"1": "🟢 便宜階", "2": "🟡 合理階", "3": "🔴 昂貴階", "0": "⚪ 未定階"}

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
        symbol = parts[0]
        
        override_shd_raw = int(parts[1]) if len(parts) > 1 else 0
        override_cost = float(parts[2]) if len(parts) > 2 and float(parts[2]) > 0 else None
        chip_code = parts[3] if len(parts) > 3 else "0"
        val_code = parts[4] if len(parts) > 4 else "0"
        extra_param = float(parts[5]) if len(parts) > 5 else 0
        is_double_dip = int(parts[6]) if len(parts) > 6 else 0 
        
        ticker = yf.Ticker(f"{symbol}.TW")
        hist = ticker.history(period="6mo")
        # 嚴格清洗數據
        hist = hist.dropna(subset=['Close', 'Open', 'High', 'Low', 'Volume'])
        if len(hist) < 60: return None

        current_price = float(hist['Close'].iloc[-1])
        prev_price = float(hist['Close'].iloc[-2])
        open_p = float(hist['Open'].iloc[-1])
        high_p = float(hist['High'].iloc[-1])
        low_p = float(hist['Low'].iloc[-1])
        
        gain = ((current_price - prev_price) / prev_price) * 100
        vol = int(hist['Volume'].iloc[-1] / 1000)
        vol_5d = hist['Volume'].iloc[-6:-1].mean() / 1000
        vol_5d = max(vol_5d, 0.01) # 防止除以零錯誤
        
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
        kdj_signal = "📈 低檔金叉" if kdj_golden_cross else ("📉 高檔死叉" if (k>70 and prev_k>prev_d and k<d) else "〰️ KDJ 震盪")

        main_cost = override_cost if override_cost else round(ma60, 1)
        buy_zone = f"{round(main_cost * 0.97, 1)} - {round(main_cost * 1.03, 1)}"
        diff_from_cost = ((current_price - main_cost) / main_cost) * 100
        diff_from_ma20 = ((current_price - ma20) / ma20) * 100

        # 動態計算價值盾
        if override_shd_raw > 0:
            shd_score = override_shd_raw
        else:
            shd_score = 2 if diff_from_cost <= -5.0 else (5 if diff_from_ma20 >= 5.0 else 4)

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
            extra_badge = f"<span class='info-badge' style='background:#1a4d2e; border:1px solid #2ecc71; color:#fff;' title='推估年度殖利率(>6%具備保護力)'>💰 預估殖利: {yield_pct}%</span>"
            cost_label = "殖利率保護底"
            if is_double_dip:
                extra_badge += " <span class='info-badge' style='background:#b8860b; color:#fff;' title='戰術：參與除息，並抱到完全填息賺取價差！'>🏅 填息雙賺</span>"
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
            "shd": shd_score, "chip": CHIP_MAP.get(chip_code, "⚖️"),
            "val": VAL_MAP.get(val_code, "⚪"), "kdj": kdj_signal, "signal": signal_text,
            "cycle": cycle_text, "color": color_border, "extra_badge": extra_badge,
            "exit_s": exit_s, "exit_price": exit_p, "exit_color": exit_c, "exit_bg": exit_bg, 
            "vol": vol, "open": open_p, "high": high_p, "low": low_p,
            "raw_data": symbol_data, "cat": category_type
        }
    except Exception as e: 
        return None

def calc_real_profit(cost, price, qty):
    if cost <= 0: return 0, 0
    buy_val = cost * qty * 1000
    profit = (price - cost) * qty * 1000
    return profit, (profit/(cost * qty * 1000))*100

st.markdown('''<style>
.stApp { background-color: #0b0c0f !important; color: #fff !important; }
div.stButton > button[kind="primary"] { background-color: #3498db !important; color: white !important; border: none; font-weight:bold; height: 45px; font-size: 16px;}
.buy-btn > button { background-color: #e74c3c !important; width: 100%; margin-top: 10px; }
.sell-btn > button { background-color: #2ecc71 !important; width: 100%; margin-top: 10px; }
.info-badge { background: #2b2b36; padding: 4px 8px; border-radius: 4px; font-size: 13px; color: #ccc; margin-right: 5px; border: 1px solid #444; display: inline-block; margin-bottom: 5px; cursor: help;}
</style>''', unsafe_allow_html=True)

st.markdown("<h1 style='color:#FFB300;'>🦅 作戰所</h1>", unsafe_allow_html=True)

if is_black_swan: 
    st.markdown(f"<div style='background:#3a1515; border:1px solid #e74c3c; color:#fff; padding:10px; border-radius:8px; margin-bottom:20px; font-weight:bold;' title='大盤單日跌幅超過1.5%，系統啟動保護機制。'>🚨 大盤暴跌 {market_change:.2f}%：防禦機制啟動，暫緩追高！</div>", unsafe_allow_html=True)

if st.button("🔄 刷新全域戰場", type="primary", use_container_width=True): 
    st.cache_data.clear()
    st.rerun()

# ==========================================
# UI 渲染函數：觀察區與建倉面板 (完全修復 Tooltip 與 財報位階)
# ==========================================
def render_stock_card(d, ui_key_prefix):
    html_card = f"""<div style="border: 2px solid {d['color']}; border-radius: 8px; padding: 15px; background-color: #16191f; margin-bottom: 5px;">
<div style="font-weight:bold; font-size:18px; margin-bottom:5px;" title="價值盾：5分為滿分。動態評估財報與股價防禦力。">{d['name']} ({d['code']}) | 🛡️ 價值盾: {d['shd']}分</div>
<div style="font-size:32px; font-weight:bold; margin-bottom: 10px;" title="即時報價與單日漲跌幅">{d['price']:.2f} <span style="font-size:18px; color:{'#ff4d4d' if d['gain']>0 else '#00FF00'};">{d['gain']:+.1f}%</span></div>
<div style="margin-bottom: 15px;">
<span class="info-badge" title="三大法人籌碼動向：判斷有無主力護航">{d['chip']}</span>
<span class="info-badge" title="財報狗位階：評估目前股價是否處於便宜區間">📊 {d['val']}</span>
<span class="info-badge" title="KDJ(9,3,3)指標：捕捉低檔轉折與高檔過熱">{d['kdj']}</span>
{d['extra_badge']}
</div>

<div style="background:#2b2b36; border-radius:5px; padding:10px; display:flex; justify-content:space-between; text-align:center; margin-bottom:10px;">
<div style="flex:1; color:#aaa; font-size:12px;" title="今日開盤價">開盤<br><span style="color:#fff; font-size:15px; font-weight:bold;">{d['open']:.1f}</span></div>
<div style="flex:1; color:#aaa; font-size:12px;" title="今日最高價">最高<br><span style="color:#fff; font-size:15px; font-weight:bold;">{d['high']:.1f}</span></div>
<div style="flex:1; color:#aaa; font-size:12px;" title="今日最低價">最低<br><span style="color:#fff; font-size:15px; font-weight:bold;">{d['low']:.1f}</span></div>
<div style="flex:1; color:#aaa; font-size:12px;" title="今日成交量 (若低於1000張將觸發流動性警報)">總量<br><span style="color:#fff; font-size:15px; font-weight:bold;">{d['vol']}張</span></div>
</div>

<div style="background:#2b2b36; border-radius:5px; padding:10px; margin-bottom:10px; text-align:center;">
<span style="color:#aaa;" title="由幕僚綜合各方數據與季線精算的底線">{d['cost_label']}:</span> <strong style="color:#fff; font-size:16px;">{d['cost']}</strong><br>
<span style="color:#e74c3c; font-weight:bold;" title="幕僚防守線的正負3%區間，跌入此區即為最佳佈局開火位置。">🎯 打擊區: [ {d['buy_zone']} ]</span>
</div>
<div style="background:{d['exit_bg']}; color:{d['exit_color']}; font-weight:bold; text-align:center; padding:8px; border-radius:5px; margin-bottom:10px; cursor:help;" title="系統根據獲利%數與大盤狀況，自動即時切換平倉或停損建議。">
{d['exit_s']} ({d['exit_price']})
</div>
<div style="font-size:13px; color:#ddd; margin-bottom:10px;">
📌 狀態: <strong style="color:{d['color']}" title="整合均線、MACD、KDJ的終極戰術判定">{d['signal']}</strong><br>
<span title="專屬戰區時程提醒">{d['cycle']}</span>
</div>
</div>"""
    st.markdown(html_card, unsafe_allow_html=True)
    
    is_pinned = d['code'] in st.session_state.pinned_stocks
    pin_action = st.checkbox("📌 鎖定追蹤 (置頂保護)", value=is_pinned, key=f"pin_{ui_key_prefix}_{d['code']}")
    if pin_action and not is_pinned:
        st.session_state.pinned_stocks[d['code']] = d
        st.rerun()
    elif not pin_action and is_pinned:
        del st.session_state.pinned_stocks[d['code']]
        st.rerun() 

    with st.expander(f"💼 實戰風控與建倉 ({d['name']})"):
        c1, c2 = st.columns(2)
        sim_cost = c1.number_input("預計進場價", value=float(d['cost']), key=f"c_{ui_key_prefix}_{d['code']}")
        sim_qty = c2.number_input("預計張數", value=1.0, key=f"q_{ui_key_prefix}_{d['code']}")
        
        st.markdown("<div class='buy-btn'>", unsafe_allow_html=True)
        if st.button(f"⚡ 確認建倉 (轉入庫存)", key=f"buy_{ui_key_prefix}_{d['code']}"):
            st.session_state.portfolio[d['code']] = {
                "entry_price": sim_cost,
                "qty": sim_qty,
                "raw_data": d['raw_data'],
                "cat": d['cat']
            }
            if d['code'] in st.session_state.pinned_stocks:
                del st.session_state.pinned_stocks[d['code']]
            st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)


# ==========================================
# UI 渲染函數：實戰庫存專屬面板
# ==========================================
def render_portfolio_card(code, p_data):
    d = calculate_tactical_signals(p_data['raw_data'], p_data['cat'])
    if not d: return 
    
    entry_price = p_data['entry_price']
    qty = p_data['qty']
    real_profit, real_roi = calc_real_profit(entry_price, d['price'], qty)
    p_color = '#ff4d4d' if real_profit > 0 else '#00FF00'
    
    p_html = f"""<div style="border: 3px solid {p_color}; border-radius: 8px; padding: 15px; background-color: #1a1a24; margin-bottom: 5px; box-shadow: 0 0 15px {p_color}40;">
<div style="display:flex; justify-content:space-between; align-items:center; border-bottom: 1px solid #444; padding-bottom:10px; margin-bottom:10px;">
<div style="font-weight:bold; font-size:20px;" title="已持倉作戰單位">{d['name']} ({code})</div>
<div style="font-size:20px; font-weight:bold; color:#fff;" title="市場即時報價">現價 {d['price']:.2f}</div>
</div>
<div style="display:flex; justify-content:space-between; margin-bottom: 15px;">
<div style="color:#aaa;">成本: <strong style="color:#fff;">{entry_price:.2f}</strong></div>
<div style="color:#aaa;">張數: <strong style="color:#fff;">{qty}</strong></div>
</div>
<div style="background:#000; padding:15px; border-radius:8px; text-align:center; margin-bottom:15px;" title="扣除手續費與稅金後的即時未實現淨利">
<div style="color:#aaa; font-size:14px; margin-bottom:5px;">💰 即時未實現損益</div>
<div style="font-size:36px; font-weight:bold; color:{p_color};">{real_profit:+,.0f} 元</div>
<div style="font-size:18px; color:{p_color};">({real_roi:+.2f}%)</div>
</div>
<div style="background:{d['exit_bg']}; color:{d['exit_color']}; font-weight:bold; text-align:center; padding:8px; border-radius:5px; cursor:help;" title="系統根據獲利%數與大盤狀態自動給出的平倉建議">
{d['exit_s']} ({d['exit_price']})
</div>
</div>"""
    st.markdown(p_html, unsafe_allow_html=True)

    st.markdown("<div class='sell-btn'>", unsafe_allow_html=True)
    if st.button(f"🚪 平倉了結 (賣出)", key=f"sell_{code}"):
        del st.session_state.portfolio[code]
        st.rerun()
    st.markdown("</div>", unsafe_allow_html=True)


# ==========================================
# 板塊渲染順序 (庫存 -> 雷達 -> 掃描區)
# ==========================================

if st.session_state.portfolio:
    st.markdown("<h2 style='color:#ff4d4d; margin-top:10px; border-bottom: 2px solid #ff4d4d; padding-bottom:5px;'>💼 總指揮實戰庫存 (持有中)</h2>", unsafe_allow_html=True)
    cols = st.columns(2)
    for i, (code, p_data) in enumerate(list(st.session_state.portfolio.items())):
        with cols[i % 2]:
            render_portfolio_card(code, p_data)

if st.session_state.pinned_stocks:
    st.markdown("<h2 style='color:#f1c40f; margin-top:20px; border-bottom: 2px solid #f1c40f; padding-bottom:5px;'>⭐ 總指揮專屬雷達 (觀察中)</h2>", unsafe_allow_html=True)
    cols = st.columns(2)
    for i, (code, d) in enumerate(list(st.session_state.pinned_stocks.items())):
        if code in st.session_state.portfolio: continue
        with cols[i % 2]:
            render_stock_card(d, ui_key_prefix="pinned")

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
        
        if d['code'] in st.session_state.portfolio or d['code'] in st.session_state.pinned_stocks: 
            continue 
        
        with cols[valid_count % 2]:
            render_stock_card(d, ui_key_prefix=cat_type)
        valid_count += 1
