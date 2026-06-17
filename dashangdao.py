import streamlit as st
import requests
import streamlit.components.v1 as components

# 設定大商道最高防禦級手機排版儀表板
st.set_page_config(page_title="大商道戰情室中繼站", layout="wide")
st.title("🦅 大商道 4.0 完全體：零摩擦手機火線中繼站")
st.write("---")

# 📡 核心黑科技：同時讀取股票代碼 (stocks) 與 AI 精算區間 (zones)
url_params = st.query_params
url_stocks = url_params.get("stocks", "")
url_zones = url_params.get("zones", "")

# 處理動態進場區間參數
zone_map = {}
if url_stocks and url_zones:
    stock_list = url_stocks.split(",")
    zone_list = url_zones.split(",")
    # 將代碼與精算區間一一對齊
    for i in range(min(len(stock_list), len(zone_list))):
        zone_map[stock_list[i].strip()] = zone_list[i].strip()

st.sidebar.markdown("### ⚔️ 戰情室代碼控制台")
default_value = url_stocks.replace(",", " ") if url_stocks else "2313 3231 3036"
raw_input = st.sidebar.text_area(
    "當前雷達鎖定代碼：",
    value=default_value,
    help="代碼之間用空格或逗號隔開即可"
)

# 常用中文對照表
STOCK_NAMES = {
    "2313": "華通", "3231": "緯創", "3036": "文曄", "2301": "光寶科", 
    "2449": "京元電", "2421": "建準", "2330": "台積電", "2454": "聯發科",
    "2382": "廣達", "2317": "鴻海", "2603": "長榮", "2609": "陽明",
    "2615": "萬海", "3711": "日月光", "2303": "聯電", "2408": "南亞科", 
    "2337": "旺宏", "5347": "世界", "2367": "燿華", "3017": "奇鋐", "3324": "雙鴻"
}

stock_codes = [code.strip() for code in raw_input.replace(",", " ").split() if code.strip()]

if stock_codes:
    st.subheader(f"📋 實時追蹤中（共 {len(stock_codes)} 檔）｜ 點貨時間：0秒延遲")
    cols = st.columns(3)
    gemini_msg_list = []
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }
    
    for idx, code in enumerate(stock_codes):
        symbol = f"{code}.TW"
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=1d"
        
        # 讀取網址中由 AI 灌進來的動態策略區間，沒有的話就顯示未設定
        ai_suggestion = zone_map.get(code, "待戰情室精算")
        
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
                        # 🎯 核心升級：直接把 AI 的進場區間顯示在每檔股票卡片的最醒目位置
                        st.markdown(f"🟢 **黃金批發進貨區**: <span style='color:#FF4B4B;font-weight:bold;font-size:18px;'>{ai_suggestion}</span>", unsafe_allow_html=True)
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
        st.write("### ⚔️ 戰情室指令火線外包區")
        
        # ⚡ 終極黑科技：利用 HTML5 注入原生的「手機專用一鍵複製按鈕」，100% 支援 iPhone Safari/Chrome
        html_button_code = f"""
        <script>
        function mobileCopyToClipboard() {{
            const textToCopy = `{final_command}`;
            navigator.clipboard.writeText(textToCopy).then(function() {{
                const btn = document.getElementById('copyBtn');
                btn.innerText = '✅ 密碼已成功咬入剪貼簿！';
                btn.style.backgroundColor = '#24G149';
                setTimeout(() => {{
                    btn.innerText = '⚡ 點我一鍵複製戰情密碼 ⚡';
                    btn.style.backgroundColor = '#FF4B4B';
                }}, 2000);
            }}, function(err) {{
                // iOS 舊版瀏覽器相容性備用方案
                var textArea = document.createElement("textarea");
                textArea.value = textToCopy;
                document.body.appendChild(textArea);
                textArea.select();
                document.execCommand('copy');
                document.body.removeChild(textArea);
                alert('密碼已成功複製！');
            }});
        }}
        </script>
        <button id="copyBtn" onclick="mobileCopyToClipboard()" style="width:100%; height:60px; background-color:#FF4B4B; color:white; font-size:20px; font-weight:bold; border:none; border-radius:10px; cursor:pointer; box-shadow: 0px 4px 10px rgba(0,0,0,0.2); -webkit-appearance: none;">
            ⚡ 點我一鍵複製戰情密碼 ⚡
        </button>
        """
        components.html(html_button_code, height=80)
        st.caption("點擊上方大紅按鈕後，即可直接回到對話框貼上回傳！")
else:
    st.warning("👈 戰情室等待代碼輸入中...")
