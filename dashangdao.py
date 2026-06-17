import streamlit as st
import requests

# 銲死最高防禦級低調外殼
st.set_page_config(page_title="即時播報", layout="wide")

st.markdown("""
    <style>
    .block-container { padding-top: 1rem; padding-bottom: 1rem; }
    </style>
""", unsafe_allow_html=True)

st.title("📊 即時播報台")
st.write("---")

# 📡 核心對照表（擴充雷達庫：自動辨識股名）
STOCK_NAMES = {
    "2313": "華通", "3231": "緯創", "3036": "文曄", "2301": "光寶科", 
    "2449": "京元電", "2421": "建準", "2330": "台積電", "2454": "聯發科",
    "2382": "廣達", "2317": "鴻海", "2603": "長榮", "2609": "陽明",
    "2615": "萬海", "3711": "日月光", "2303": "聯電", "2408": "南亞科", 
    "2337": "旺宏", "5347": "世界", "2367": "燿華", "3017": "奇鋐", "3324": "雙鴻"
}

# 📡 接收由 AI 吐出的動態指令網址
url_params = st.query_params
stocks_param = url_params.get("stocks", "")
zones_param = url_params.get("zones", "")
types_param = url_params.get("types", "")  # C=核心, W=觀察

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

# 📡 側邊欄：盤中臨時自選功能
st.sidebar.markdown("### ➕ 盤中臨時追加")
temp_code = st.sidebar.text_input("臨時股票代碼", value="")
temp_name = st.sidebar.text_input("臨時股票名稱", value="自選黑馬")
temp_zone = st.sidebar.text_input("臨時參考區間", value="待精算")

headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
gemini_msg_list = []

def render_stock_card(item):
    code = item["code"]
    name = item["name"]
    zone = item["zone"]
    badge = item["badge"]
    symbol = f"{code}.TW"
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=1d"
    try:
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            data = response.json()
            if data and "chart" in data and "result" in data["chart"] and data["chart"]["result"]:
                result = data["chart"]["result"][0]
                meta = result.get("meta", {})
                price = meta.get("regularMarketPrice", 0.0)
                prev_close = meta.get("chartPreviousClose", price)
                pct_change = ((price - prev_close) /
