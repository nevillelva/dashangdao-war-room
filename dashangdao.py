import streamlit as st
import requests
import time

# 銲死最高防禦級低調外殼
st.set_page_config(page_title="即時播報", layout="wide")

st.markdown("""
    <style>
    .block-container { padding-top: 1rem; padding-bottom: 1rem; }
    </style>
""", unsafe_allow_html=True)

# ⚡ 核心功能：標題與強制刷新按鈕
col1, col2 = st.columns([8, 2])
with col1:
    st.title("📊 即時播報台")
with col2:
    st.write("") 
    if st.button("🔄 刷新最新報價", use_container_width=True, type="primary"):
        st.rerun()

st.write("---")

# 📡 核心對照表（保底核心庫）
STOCK_NAMES = {
    "2313": "華通", "3231": "緯創", "3036": "文曄", "2301": "光寶科", 
    "2449": "京元電", "2421": "建準", "2330": "台積電", "2454": "聯發科",
    "2382": "廣達", "2317": "鴻海", "2603": "長榮", "2609": "陽明",
    "2615": "萬海", "3711": "日月光", "2303": "聯電", "2408": "南亞科", 
    "2337": "旺宏", "5347": "世界", "2367": "燿華", "3017": "奇鋐", "3324": "雙鴻"
}

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

# 📡 側邊欄：盤中臨時自選功能 (🚀 終極進化：雲端友善的 Yahoo 即時搜尋天網)
st.sidebar.markdown("### ➕ 盤中臨時追加")
search_query = st.sidebar.text_input("🔍 輸入關鍵字或代碼 (如: 中華 / 中 / 2412)", value="")

temp_code = ""
temp_name = "自選黑馬"
temp_suffix = ""

headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}

if search_query.strip():
    # 🏹 利用 Yahoo Finance 官方搜尋 API，無視伺服器防火牆封鎖，動態抓取全台股上市櫃
    search_url = f"https://query1.finance.yahoo.com/v1/finance/search?q={search_query}&quotesCount=10&newsCount=0&lang=zh-Hant-TW&region=TW"
    try:
        res = requests.get(search_url, headers=headers, timeout=3)
        if res.status_code == 200:
            search_data = res.json()
            quotes = search_data.get("quotes", [])
            matched_items = []
            for q in quotes:
                symbol = q.get("symbol", "")
                # 僅篩選台灣市場上市 (.TW) 或上櫃 (.TWO) 股票
                if symbol.endswith(".TW") or symbol.endswith(".TWO"):
                    code = symbol.split(".")[0]
                    suffix = "." + symbol.split(".")[1]
                    name = q.get("shortname") or q.get("longname") or "未知"
                    matched_items.append(f"{code} | {name} | {suffix}")
            
            if matched_items:
                selected_item = st.sidebar.selectbox(f"🎯 找到 {len(matched_items)} 檔符合標的:", ["-- 請點擊選擇目標 --"] + matched_items)
                if selected_item != "-- 請點擊選擇目標 --":
                    parts = selected_item.split(" | ")
                    temp_code = parts[0].strip()
                    temp_name = parts[1].strip()
                    temp_suffix = parts[2].strip()
            else:
                st.sidebar.warning("⚠️ 查無匹配股票，已切換完全手動輸入")
                temp_code = search_query
                temp_name = "自選黑馬"
        else:
            temp_code = search_query
            temp_name = "自選黑馬"
    except:
        st.sidebar.warning("⚠️ 連線超時，請直接輸入正確代碼")
        temp_code = search_query
        temp_name = "自選黑馬"
else:
    temp_code = st.sidebar.text_input("臨時股票代碼(選填)", value="")
    temp_name = st.sidebar.text_input("臨時股票名稱(選填)", value="自選黑馬")

temp_zone = st.sidebar.text_input("臨時參考區間", value="待精算")

gemini_msg_list = []

def render_stock_card(item):
    code = item["code"]
    name = item["name"]
    zone = item["zone"]
    badge = item["badge"]
    
    # ⚡ 雙彈頭智慧判定機制：如果已有搜尋到的明確尾碼就直接用；沒有的話（網址帶入的股票）依序盲測 .TW 與 .TWO
    suffixes_to_try = [item["suffix"]] if "suffix" in item and item["suffix"] else [".TW", ".TWO"]
    
    for suffix in suffixes_to_try:
        symbol = f"{code}{suffix}"
        timestamp = int(time.time())
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=1d&_={timestamp}"
        
        try:
            response = requests.get(url, headers=headers, timeout=2)
            if response.status_code == 200:
                data = response.json()
                if data and "chart" in data and "result" in data["chart"] and data["chart"]["result"]:
                    result = data["chart"]["result"][0]
                    meta = result.get("meta", {})
                    price = meta.get("regularMarketPrice", 0.0)
                    
                    # 判定是否抓到有效股價，抓到即中斷迴圈輸出卡片
                    if price is not None and price > 0:
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
                        st.markdown(card_html, unsafe_allow_html=True)
                        gemini_msg_list.append(f"{code}={price:.2f}")
                        break  # 成功抓到有效資料，直接中斷後續盲測
        except:
            pass

    if CORE_STOCKS:
        st.markdown("### 🦅 核心精選主將 (高勝率狙擊區)")
        for stock in CORE_STOCKS: render_stock_card(stock)

    if WATCH_STOCKS:
        st.markdown("---")
        st.markdown("### 📈 短中期轉折觀察區")
        for stock in WATCH_STOCKS: render_stock_card(stock)

    # ⚡ 盤中臨時自選區
    if temp_code.strip():
        st.markdown("---")
        st.markdown("### ⚡ 盤中臨時自選區")
        render_stock_card({"code": temp_code, "name": temp_name, "zone": temp_zone, "badge": "🔥", "suffix": temp_suffix})

    if gemini_msg_list:
        final_command = "今日精選 " + " ".join(gemini_msg_list)
        st.write("---")
        st.write("### 📝 數據複製區")
        st.code(final_command, language="text")
