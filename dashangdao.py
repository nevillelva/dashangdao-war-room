import streamlit as st
import requests

# 銲死最高防禦級低調外殼
st.set_page_config(page_title="即時播報", layout="wide")

# 消除網頁頂部空白，讓第一檔股票直接置頂
st.markdown("""
    <style>
    .block-container { padding-top: 1rem; padding-bottom: 1rem; }
    </style>
""", unsafe_allow_html=True)

st.title("📊 即時播報")
st.write("---")

# 🎯 核心策略銲死區（網址永遠純淨，桌面捷徑永久有效）
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
    
    gemini_msg_list = []
    
    # ⚡ 徹底回歸直列式流暢排版（一行一檔，清爽無負擔）
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
                    
                    color = "#FF4B4B" if pct_change > 0 else "#00FF66" if pct_change < 0 else "#FFFFFF"
                    
                    # ⚡ 7.0 降噪卡片：橫向拉開，只留核心 Facts，絕無多餘文字
                    st.markdown(f"""
                    <div style="border: 1px solid #333; border-radius: 8px; padding: 12px 16px; background-color: #1a1a1a; margin-bottom: 10px; box-shadow: 0px 2px 4px rgba(0,0,0,0.1);">
                        <div style="display: flex; justify-content: space-between; align-items: center;">
                            <div>
                                <span style="font-size: 20px; font-weight: bold; color: #ffffff;">{name}</span>
                                <span style="font-size: 14px; color: #888888; margin-left: 6px;">({code})</span>
                            </div>
                            <div style="text-align: right;">
                                <span style="font-size: 22px; font-weight: bold; color: {color};">{price:.2f}</span>
                                <span style="font-size: 13px; color: {color}; margin-left: 6px; font-weight: bold;">({pct_change:+.2f}%)</span>
                            </div>
                        </div>
                        <div style="margin-top: 8px; border-top: 1px solid #2a2a2a; padding-top: 6px; font-size: 14px; color: #ff4b4b; font-weight: bold;">
                            🟢 參考區間：{zone}
                        </div>
                    </div>
                    """, unsafe_allow_html=True)
                    
                    gemini_msg_list.append(f"{code}={price:.2f}")
        except:
            pass
            
    # 下方一鍵複製區
    if gemini_msg_list:
        final_command = "今日10檔 " + " ".join(gemini_msg_list)
        st.write("---")
        st.write("### 📝 數據複製區")
        st.caption("💡 點擊下方框框右側圖示即可秒級複製：")
        st.code(final_command, language="text")
