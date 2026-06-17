import streamlit as st
import requests

# 銲死最高國防級低調外殼
st.set_page_config(page_title="即時播報", layout="wide")

# 注入自定義 CSS：消除網頁上方空白、緊湊邊距
st.markdown("""
    <style>
    .block-container { padding-top: 1rem; padding-bottom: 1rem; }
    iframe { max-height: 90px; }
    </style>
""", unsafe_allow_html=True)

st.title("📊 即時播報")
st.write("---")

# 🎯 核心黑科技：數據與策略直接銲死在代碼內！網址永遠乾淨，主畫面捷徑永久有效！
# 老大，以後要換股票或改區間，我直接扔給你這盤代碼，更新後網址完全不變！
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
    
    # ⚡ 建立原生手機雙列網格外殼
    grid_html = """
    <div style="display: grid; grid-template-columns: repeat(2, 1fr); gap: 8px; margin-bottom: 15px;">
    """
    
    gemini_msg_list = []
    
    for code in stock_codes:
        symbol = f"{code}.TW"
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=1d"
        
        name = STRATEGY[code]["name"]
        zone = STRATEGY[code]["zone"]
        
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
                    
                    volume = 0
                    try:
                        volumes = result.get("indicators", {}).get("quote", [{}])[0].get("volume", [])
                        if volumes and volumes[0] is not None:
                            volume = volumes[0] // 1000
                    except:
                        pass
                    
                    color = "#FF4B4B" if pct_change > 0 else "#00FF66" if pct_change < 0 else "#FFFFFF"
                    
                    # ⚡ 注入極緊湊卡片：字體縮小、間距鎖死，一屏直接看 4-6 檔
                    grid_html += f"""
                    <div style="border: 1px solid #333; border-radius: 6px; padding: 6px; background-color: #1a1a1a; min-height: 85px;">
                        <div style="font-size: 14px; font-weight: bold; color: #cccccc;">{name} ({code})</div>
                        <div style="font-size: 18px; font-weight: bold; color: {color}; margin: 2px 0;">{price:.2f} <span style="font-size: 11px;">({pct_change:+.2f}%)</span></div>
                        <div style="font-size: 11px; color: #888888;">量:{volume}張</div>
                        <div style="font-size: 12px; color: #FF4B4B; font-weight: bold; margin-top: 2px; border-top: 1px dashed #222; padding-top: 2px;">區間:{zone}</div>
                    </div>
                    """
                    gemini_msg_list.append(f"{code}={price:.2f}")
            else:
                grid_html += f"<div style='border:1px solid red; padding:6px; font-size:11px;'>{code}受阻</div>"
        except:
            grid_html += f"<div style='border:1px solid red; padding:6px; font-size:11px;'>{code}異常</div>"
            
    grid_html += "</div>"
    
    # 渲染終極網格
    st.markdown(grid_html, unsafe_allow_html=True)
    
    if gemini_msg_list:
        final_command = "今日10檔 " + " ".join(gemini_msg_list)
        st.write("---")
        st.write("### 📝 數據複製區")
        
        # 🛡️ 雙保險手機一鍵複製：同時放大按鈕，且下方直接提供官方原生點擊框
        st.code(final_command, language="text")
