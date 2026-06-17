import streamlit as st
import requests

# 銲死最高國防級低調外殼
st.set_page_config(page_title="即時播報", layout="wide")

# 調整網頁邊距
st.markdown("""
    <style>
    .block-container { padding-top: 1rem; padding-bottom: 1rem; }
    </style>
""", unsafe_allow_html=True)

st.title("📊 即時播報台")
st.write("---")

# 🎯 核心策略銲死區
STRATEGY = {
    "2313": {"name": "華通", "zone": "240-246"},
    "3231": {"name": "緯創", "zone": "160-161.5"},
    "3036": {"name": "文曄", "zone": "215-220"},
    "2301": {"name": "光寶科", "zone": "195-202"},
    "2449": {"name": "京元電", "zone": "270-275"},
    "2421": {"name": "建準", "zone": "130-135"},
    "2367": {"name": "燿華", "zone": "58-60"},
    "2408": {"name": "南亞科", "zone": "410-425"},
    "2337": {"name": "旺宏", "zone": "150-155"},
    "3017": {"name": "奇鋐", "zone": "2250-2300"}
}

stock_codes = list(STRATEGY.keys())

if stock_codes:
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }
    
    # 手機雙列網格外殼
    grid_html = """
    <div style="display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px; margin-bottom: 15px;">
    """
    
    gemini_msg_list = []
    
    for code in stock_codes:
        symbol = f"{code}.TW"
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=1d"
        
        name = STRATEGY[code]["name"]
        zone_str = STRATEGY[code]["zone"]
        
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
                    
                    # ⚡ 核心黑科技：Python 自動幫老大進行紅綠燈多空判定
                    try:
                        low_bound, high_bound = map(float, zone_str.split("-"))
                        if price < low_bound:
                            status_badge = "🔵 已超跌"
                            bg_color = "#1a2436"      # 沉穩暗藍
                            border_color = "#3b82f6"  # 藍邊
                            text_color = "#93c5fd"
                        elif low_bound <= price <= high_bound:
                            status_badge = "🟢 獲取特權"
                            bg_color = "#143a22"      # 發光軍綠
                            border_color = "#22c55e"  # 亮綠邊
                            text_color = "#4ade80"
                        else:
                            status_badge = "❌ 溢價勿追"
                            bg_color = "#241e1e"      # 熄燈暗焦
                            border_color = "#443333"  # 暗紅邊
                            text_color = "#888888"    # 數字變灰，不干擾視線
                    except:
                        status_badge = "🔍 待檢查"
                        bg_color = "#1f1f1f"
                        border_color = "#333333"
                        text_color = "#ffffff"
                    
                    # 漲跌幅顏色
                    change_color = "#FF4B4B" if pct_change > 0 else "#00FF66" if pct_change < 0 else "#FFFFFF"
                    
                    # ⚡ 6.0 極簡視覺卡片排版：突出重點，拒絕字海
                    grid_html += f"""
                    <div style="border: 2px solid {border_color}; border-radius: 8px; padding: 10px; background-color: {bg_color}; box-shadow: 0 2px 5px rgba(0,0,0,0.2);">
                        <div style="font-size: 16px; font-weight: bold; color: #ffffff; display: flex; justify-content: space-between;">
                            <span>{name}</span>
                            <span style="font-size: 12px; color: #aaaaaa; font-weight: normal;">{code}</span>
                        </div>
                        
                        <div style="font-size: 24px; font-weight: bold; color: {text_color}; margin: 5px 0;">
                            {price:.2f}
                        </div>
                        
                        <div style="display: flex; justify-content: space-between; align-items: center; margin-top: 5px; border-top: 1px solid rgba(255,255,255,0.05); padding-top: 5px;">
                            <span style="font-size: 13px; font-weight: bold; color: {text_color};">{status_badge}</span>
                            <span style="font-size: 12px; color: {change_color}; font-weight: bold;">{pct_change:+.2f}%</span>
                        </div>
                    </div>
                    """
                    gemini_msg_list.append(f"{code}={price:.2f}")
            else:
                grid_html += f"<div style='border:1px solid red; padding:10px;'>{code}阻斷</div>"
        except:
            grid_html += f"<div style='border:1px solid red; padding:10px;'>{code}異常</div>"
            
    grid_html += "</div>"
    st.markdown(grid_html, unsafe_allow_html=True)
    
    # 下方一鍵複製保持最純淨
    if gemini_msg_list:
        final_command = "今日10檔 " + " ".join(gemini_msg_list)
        st.write("---")
        st.write("### 📝 數據複製區")
        st.code(final_command, language="text")
