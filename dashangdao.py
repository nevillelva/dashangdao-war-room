import streamlit as st
import requests
import streamlit.components.v1 as components

# 設定低調外殼手機排版儀表板
st.set_page_config(page_title="即時播報", layout="wide")
st.title("📊 即時播報")
st.write("---")

# 📡 讀取股票代碼 (stocks) 與 參考區間 (zones)
url_params = st.query_params
url_stocks = url_params.get("stocks", "")
url_zones = url_params.get("zones", "")

zone_map = {}
if url_stocks and url_zones:
    stock_list = url_stocks.split(",")
    zone_list = url_zones.split(",")
    for i in range(min(len(stock_list), len(zone_list))):
        zone_map[stock_list[i].strip()] = zone_list[i].strip()

st.sidebar.markdown("### ⚙️ 設定控制台")
default_value = url_stocks.replace(",", " ") if url_stocks else "2313 3231 3036"
raw_input = st.sidebar.text_area(
    "鎖定代碼：",
    value=default_value,
    help="代碼之間用空格隔開"
)

STOCK_NAMES = {
    "2313": "華通", "3231": "緯創", "3036": "文曄", "2301": "光寶科", 
    "2449": "京元電", "2421": "建準", "2330": "台積電", "2454": "聯發科",
    "2382": "廣達", "2317": "鴻海", "2603": "長榮", "2609": "陽明",
    "2615": "萬海", "3711": "日月光", "2303": "聯電", "2408": "南亞科", 
    "2337": "旺宏", "5347": "世界", "2367": "燿華", "3017": "奇鋐", "3324": "雙鴻"
}

stock_codes = [code.strip() for code in raw_input.replace(",", " ").split() if code.strip()]

if stock_codes:
    st.subheader(f"📋 數據清單 (共 {len(stock_codes)} 檔)")
    cols = st.columns(3)
    gemini_msg_list = []
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }
    
    for idx, code in enumerate(stock_codes):
        symbol = f"{code}.TW"
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=1d"
        ai_suggestion = zone_map.get(code, "待計算")
        
        try:
            response = requests.get(url, headers=headers)
            if response.status_code == 200:
                data = response.json()
                if data and "chart" in data and "result" in data["chart"] and data["chart"]["result"]:
                    result = data["chart"]["result"][0]
                    meta = result.get("meta", {})
                    
                    price = meta.get("regularMarketPrice", 0.0)
                    prev_close = meta.get("chartPreviousClose", price)
                    
                    if prev_close > 0:
                        pct_change = ((price - prev_close) / prev_close) * 100
                    else:
                        pct_change = 0.0
                        
                    volume = 0
                    try:
                        volumes = result.get("indicators", {}).get("quote", [{}])[0].get("volume", [])
                        if volumes and volumes[0] is not None:
                            volume = volumes[0] // 1000
                    except:
                        pass
                    
                    name = STOCK_NAMES.get(code, code)
                    color = "red" if pct_change > 0 else "green" if pct_change < 0 else "white"
                    
                    with cols[idx % 3]:
                        st.markdown(f"### {name} ({code})")
                        st.markdown(f"**最新實價**: <span style='color:{color};font-size:24px;font-weight:bold;'>{price:.2f}</span> ({pct_change:+.2f}%)", unsafe_allow_html=True)
                        st.write(f"總量: {volume} 張")
                        # 低調化：更改字眼為「參考區間」
                        st.markdown(f"🟢 **參考區間**: <span style='color:#FF4B4B;font-weight:bold;font-size:18px;'>{ai_suggestion}</span>", unsafe_allow_html=True)
                        st.write("---")
                        
                    gemini_msg_list.append(f"{code}={price:.2f}")
            else:
                with cols[idx % 3]:
                    st.error(f"代碼 {code} 通道受阻")
        except Exception as e:
            with cols[idx % 3]:
                st.error(f"代碼 {code} 異常: {e}")
                
    if gemini_msg_list:
        final_command = "今日10檔 " + " ".join(gemini_msg_list)
        st.write("---")
        st.write("### 📝 數據複製區")
        
        html_button_code = f"""
        <script>
        function mobileCopyToClipboard() {{
            const textToCopy = `{final_command}`;
            navigator.clipboard.writeText(textToCopy).then(function() {{
                const btn = document.getElementById('copyBtn');
                btn.innerText = '✅ 複製成功！';
                btn.style.backgroundColor = '#24C149';
                setTimeout(() => {{
                    btn.innerText = '🚀 點我一鍵複製數據 🚀';
                    btn.style.backgroundColor = '#FF4B4B';
                }}, 2000);
            }}, function(err) {{
                var textArea = document.createElement("textarea");
                textArea.value = textToCopy;
                document.body.appendChild(textArea);
                textArea.select();
                document.execCommand('copy');
                document.body.removeChild(textArea);
                alert('複製成功！');
            }});
        }}
        </script>
        <button id="copyBtn" onclick="mobileCopyToClipboard()" style="width:100%; height:60px; background-color:#FF4B4B; color:white; font-size:20px; font-weight:bold; border:none; border-radius:10px; cursor:pointer; box-shadow: 0px 4px 10px rgba(0,0,0,0.2); -webkit-appearance: none;">
            🚀 點我一鍵複製數據 🚀
        </button>
        """
        components.html(html_button_code, height=80)
else:
    st.warning("👈 等待代碼輸入中...")
