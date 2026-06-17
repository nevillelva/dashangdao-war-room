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

# =================【戰略核心部隊配置區】=================
# 老大，以後你篩選完，直接在這裡調整。想留幾檔就留幾檔，完全由你定生死！

# 🦅 1. 核心精選主將（3-5檔最高概率標的）
CORE_STOCKS = [
    {"code": "3231", "name": "緯創", "zone": "160-161.5", "type": "🥇 核心主將"},
    {"code": "2313", "name": "華通", "zone": "240-246", "type": "🥈 核心主將"},
    {"code": "3036", "name": "文曄", "zone": "215-220", "type": "🥉 核心主將"}
]

# 📈 2. 短中期轉折觀察區（波段、潛在轉折標的）
WATCH_STOCKS = [
    {"code": "2301", "name": "光寶科", "zone": "195-202", "type": "🔄 轉折觀察"},
    {"code": "2449", "name": "京元電", "zone": "270-275", "type": "🔄 轉折觀察"},
    {"code": "3017", "name": "奇鋐", "zone": "2250-2300", "type": "🔄 轉折觀察"}
]
# =======================================================

# 📡 側邊欄：盤中臨時突發想法插隊區
st.sidebar.markdown("### ➕ 盤中臨時追加")
temp_code = st.sidebar.text_input("臨時股票代碼 (如: 2330)", value="")
temp_name = st.sidebar.text_input("臨時股票名稱", value="突發黑馬")
temp_zone = st.sidebar.text_input("臨時參考區間", value="待精算")

# 整合三大模組
final_list = CORE_STOCKS.copy() + WATCH_STOCKS.copy()
if temp_code.strip():
    final_list.append({
        "code": temp_code.strip(),
        "name": temp_name,
        "zone": temp_zone,
        "type": "🔥 臨時雷達"
    })

if final_list:
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }
    
    gemini_msg_list = []
    current_type = ""
    
    for item in final_list:
        code = item["code"]
        name = item["name"]
        zone = item["zone"]
        stock_type = item["type"]
        
        # 視覺分區線：當遇到不同的模組，自動切換大標題
        if stock_type != current_type:
            current_type = stock_type
            if "核心" in current_type:
                st.markdown("### 🦅 核心精選主將 (高勝率狙擊區)")
            elif "轉折" in current_type:
                st.markdown("---")
                st.markdown("### 📈 短中期轉折觀察區")
            elif "臨時" in current_type:
                st.markdown("---")
                st.markdown("### ⚡ 盤中臨時自選區")
        
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
                    
                    # 9.1 工整板塊排版銲死
                    card_html = (
                        f'<div style="background-color: #1e1e1e; border-radius: 8px; padding: 12px; margin-bottom: 12px; border: 1px solid #333; box-shadow: 0px 4px 6px rgba(0,0,0,0.3);">'
                        f'<div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">'
                        f'<div>'
                        f'<span style="background-color: #333333; color: #ffeb3b; font-size: 11px; padding: 2px 6px; border-radius: 4px; font-weight: bold; margin-right: 8px;">{stock_type}</span>'
                        f'<span style="font-size: 20px; font-weight: bold; color: #ffffff;">{name}</span>'
                        f'<span style="font-size: 13px; color: #888888; margin-left: 4px;">{code}</span>'
                        f'</div>'
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
        except:
            pass
            
    if gemini_msg_list:
        final_command = "今日精選 " + " ".join(gemini_msg_list)
        st.write("---")
        st.write("### 📝 數據複製區")
        st.code(final_command, language="text")
