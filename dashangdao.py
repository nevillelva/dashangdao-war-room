import streamlit as st
import requests
import time
import re

# 銲死最高防禦級低調外殼
st.set_page_config(page_title="即時播報", layout="wide")

st.markdown("""
    <style>
    .block-container { padding-top: 1rem; padding-bottom: 1rem; }
    </style>
""", unsafe_allow_html=True)

# 📡 全台股天網連線庫（自動下載上市櫃所有股票，每天自動更新一次，0延遲）
@st.cache_data(ttl=86400)
def load_all_taiwan_market_universe():
    # 基礎保底庫
    stocks_db = {
        "2313": "華通", "3231": "緯創", "3036": "文曄", "2301": "光寶科", 
        "2449": "京元電", "2421": "建準", "2330": "台積電", "2454": "聯發科",
        "2382": "廣達", "2317": "鴻海", "2603": "長榮", "2609": "陽明",
        "2615": "萬海", "3711": "日月光", "2303": "聯電", "2408": "南亞科", 
        "2337": "旺宏", "5347": "世界", "2367": "燿華", "3017": "奇鋐", "3324": "雙鴻"
    }
    otc_market_set = set(["5347"]) # 用來記錄哪些是上櫃股(.TWO)
    
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    
    # 🏹 獵取證交所所有「上市」股票 (Mode 2)
    try:
        r2 = requests.get("https://isin.twse.com.tw/isin/C_public.jsp?strMode=2", headers=headers, timeout=10)
        r2.encoding = 'big5'
        if r2.status_code == 200:
            matches2 = re.findall(r'(\d{4,6})[\s\u3000]+([^<\s\u3000]+)', r2.text)
            for code, name in matches2:
                if len(code) == 4: # 鎖定正規個股
                    stocks_db[code] = name
    except:
        pass
        
    # 🏹 獵取櫃買中心所有「上櫃」股票 (Mode 4)
    try:
        r4 = requests.get("https://isin.twse.com.tw/isin/C_public.jsp?strMode=4", headers=headers, timeout=10)
        r4.encoding = 'big5'
        if r4.status_code == 200:
            matches4 = re.findall(r'(\d{4,6})[\s\u3000]+([^<\s\u3000]+)', r4.text)
            for code, name in matches4:
                if len(code) == 4:
                    stocks_db[code] = name
                    otc_market_set.add(code)
    except:
        pass
        
    return stocks_db, list(otc_market_set)

# 啟動動態資料庫
STOCK_NAMES, OTC_LIST = load_all_taiwan_market_universe()
OTC_CODES = set(OTC_LIST)

# ⚡ 核心功能：標題與強制刷新按鈕
col1, col2 = st.columns([8, 2])
with col1:
    st.title("📊 即時播報台")
with col2:
    st.write("") 
    if st.button("🔄 刷新最新報報", use_container_width=True, type="primary"):
        st.rerun()

st.write("---")

# 📡 接收由 AI 吐出的動態指令網址參數
url_params = st.query_params
stocks_param = url_params.get("stocks", "")
zones_param = url_params.get("zones", "")
types_param = url_params.get("types", "") 

CORE_STOCKS = []
WATCH_STOCKS = []

if stocks_param:
    stock_codes = [c.strip() for c in stocks_param.split(",") if c.strip()]
    zones = [z.strip() for z in zones_param.split(",")] if zones_param else []
    types = [t.strip() for t in types_param.split(",")] if types_param else []
    
    while len(zones) < len(stock_codes): zones.append("待精算")
    while len(types) < len(stock_codes): types.append("W")
    
    badges = ["🥇", "🥈", "🥉", "🚀"] + ["🔍"] * 20
    core_idx = 0
    
    for i, code in enumerate(stock_codes):
        name = STOCK_NAMES.get(code, "自選股")
        zone = zones[i]
        stype = types[i]
        
        if stype == "C":
            badge = badges[core_idx] if core_idx < len(badges) else "🚀"
            CORE_STOCKS.append({"code": code, "name": name, "zone": zone, "badge": badge})
            core_idx += 1
        else:
            WATCH_STOCKS.append({"code": code, "name": name, "zone": zone, "badge": "🔍"})

# 📡 側邊欄：盤中臨時自選功能 (🚀 升級：全台股模糊比對搜尋)
st.sidebar.markdown("### ➕ 盤中臨時追加")
search_query = st.sidebar.text_input("🔍 輸入關鍵字或代碼 (如: 中華 / 中 / 2412)", value="")

temp_code = ""
temp_name = "自選黑馬"

if search_query.strip():
    # 🎯 全島掃描比對
    matched_items = [
        f"{code} | {name}"
        for code, name in STOCK_NAMES.items()
        if search_query.strip().lower() in code or search_query.strip().lower() in name
    ]
    
    if matched_items:
        # 依代碼排序，方便老大選取
        matched_items = sorted(matched_items)
        selected_item = st.sidebar.selectbox(f"🎯 找到 {len(matched_items)} 檔符合標的:", ["-- 請點擊選擇目標 --"] + matched_items)
        if selected_item != "-- 請點擊選擇目標 --":
            temp_code = selected_item.split(" | ")[0].strip()
            temp_name = selected_item.split(" | ")[1].strip()
    else:
        st.sidebar.warning("⚠️ 查無此股票，已切換完全手動輸入")
        temp_code = st.sidebar.text_input("手動輸入代碼", value=search_query)
        temp_name = st.sidebar.text_input("手動輸入名稱", value="自選黑馬")
else:
    temp_code = st.sidebar.text_input("臨時股票代碼(選填)", value="")
    temp_name = st.sidebar.text_input("臨時股票名稱(選填)", value="自選黑馬")

temp_zone = st.sidebar.text_input("臨時參考區間", value="待精算")

headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
gemini_msg_list = []

def render_stock_card(item):
    code = item["code"]
    name = item["name"]
    zone = item["zone"]
    badge = item["badge"]
    
    # ⚡ 智慧市場尾碼判定：上市為 .TW，上櫃為 .TWO，打擊市場盲區！
    market_suffix = ".TWO" if code in OTC_CODES else ".TW"
    symbol = f"{code}{market_suffix}"
    
    timestamp = int(time.time())
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=1d&_={timestamp}"
    
    try:
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            data = response.json()
            if data and "chart" in data and "result" in data["chart"] and data["chart"]["result"]:
                result = data["chart"]["result"][0]
                meta = result.get("meta", {})
                price = meta.get("regularMarketPrice", 0.0)
                prev_close = meta.get("chartPreviousClose", price)
                pct_change = ((price - prev_close) / prev_close) * 100 if prev_close > 0 else 0.0
                try:
                    quote = result.get("indicators", {}).get("quote", [{}])[0]
                    open_p = quote.get("open", [price])[0]
                    high_p = quote.get("high", [price])[0]
                    low_p = quote.get("low", [price])[0]
                    volume_shares = quote.get("volume", [0])[0]
                    volume = volume_shares // 1000 if volume_shares else 0
                    if open_p is None: open_p = price
                    if high_p is None: high_p = price
                    if low_p is None: low_p = price
                except:
                    open_p, high_p, low_p, volume = price, price, price, 0
                color = "#FF4B4B" if pct_change > 0 else "#00FF66" if pct_change < 0 else "#FFFFFF"
                card_html = (
                    f'<div style="background-color: #1e1e1e; border-radius: 8px; padding: 12px; margin-bottom: 12px; border: 1px solid #333; box-shadow: 0px 4px 6px rgba(0,0,0,0.3);">'
                    f'<div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">'
                    f'<div><span style="font-size: 20px; font-weight: bold; color: #ffffff;">{badge} {name}</span><span style="font-size: 13px; color: #888888; margin-left: 6px;">{code}</span></div>'
                    f'<div style="text-align: right;"><span style="font-size: 24px; font-weight: bold; color: {color};">{price:.2f}</span><span style="font-size: 13px; font-weight: bold; color: {color}; margin-left: 4px;">({pct_change:+.2f}%)</span></div>'
                    f'</div>'
                    f'<div style="display: grid; grid-template-columns: repeat(4, 1fr); gap: 4px; background-color: #111111; padding: 8px 4px; border-radius: 6px; text-align: center; margin-bottom: 8px;">'
                    f'<div><div style="font-size: 11px; color: #777777; margin-bottom: 2px;">開盤</div><div style="font-size: 14px; font-weight: bold; color: #ffffff;">{open_p:.2f}</div></div>'
                    f'<div><div style="font-size: 11px; color: #777777; margin-bottom: 2px;">最高</div><div style="font-size: 14px; font-weight: bold; color: #ff4b4b;">{high_p:.2f}</div></div>'
                    f'<div><div style="font-size: 11px; color: #777777; margin-bottom: 2px;">最低</div><div style="font-size: 14px; font-weight: bold; color: #00ff66;">{low_p:.2f}</div></div>'
                    f'<div><div style="font-size: 11px; color: #777777; margin-bottom: 2px;">總量</div><div style="font-size: 14px; font-weight: bold; color: #ffeb3b;">{volume}張</div></div>'
                    f'</div>'
                    f'<div style="display: flex; justify-content: space-between; align-items: center; font-size: 13px; background-color: rgba(255, 75, 75, 0.1); padding: 6px 10px; border-radius: 4px; border: 1px dashed rgba(255, 75, 75, 0.3);">'
                    f'<span style="color: #ffaaaa; font-weight: bold;">🎯 參考區間</span><span style="color: #ff4b4b; font-weight: bold; font-size: 16px;">{zone}</span>'
                    f'</div>'
                    f'</div>'
                )
                st.markdown(card
