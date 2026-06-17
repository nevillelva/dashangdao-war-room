import streamlit as st
import requests

# 設定大商道最高國防級儀表板
st.set_page_config(page_title="大商道戰情室中繼站", layout="wide")
st.title("🦅 大商道 3.0 完全體：零阻力火線網址中繼站")
st.write("---")

# 📡 自動讀取網址後方的股票參數
url_params = st.query_params
url_stocks = url_params.get("stocks", "")

st.sidebar.markdown("### ⚔️ 戰情室代碼控制台")
default_value = url_stocks.replace(",", " ") if url_stocks else "2313 3231 3036"
raw_input = st.sidebar.text_area(
    "當前雷達鎖定代碼：",
    value=default_value,
    help="代碼之間用空格或逗號隔開即可"
)

# 常用中文對照表，若不在表內則自動顯示全球英文簡稱或代碼
STOCK_NAMES = {
    "2313": "華通", "3231": "緯創", "3036": "文曄", "2301": "光寶科", 
    "2449": "京元電", "2421": "建準", "2330": "台積電", "2454": "聯發科",
    "2382": "廣達", "2317": "鴻海", "2603": "長榮", "2609": "陽明",
    "2615": "萬海", "3711": "日月光", "2303": "聯電", "2344": "華邦電",
    "2408": "南亞科", "2337": "旺宏", "5347": "世界", "6269": "台郡",
    "6153": "嘉聯益", "3044": "健鼎", "2367": "燿華", "3017": "奇鋐", "3324": "雙鴻"
}

stock_codes = [code.strip() for code in raw_input.replace(",", " ").split() if code.strip()]

if stock_codes:
    # ⚡ 更換為全球通用接口，徹底解決美國伺服器被台灣奇摩擋 IP 的問題
    symbols = ",".join([f"{code}.TW" for code in stock_codes])
    url = f"https://query1.finance.yahoo.com/v7/finance/quote?symbols={symbols}"
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }
    
    try:
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            data = response.json()
            
            if data and "quoteResponse" in data and "result" in data["quoteResponse"]:
                quotes = data["quoteResponse"]["result"]
                
                st.subheader(f"📋 實時追蹤中（共 {len(quotes)} 檔）｜ 點貨時間：0秒延遲")
                
                cols = st.columns(3)
                gemini_msg_list = []
                
                for idx, q in enumerate(quotes):
                    symbol = q.get("symbol", "")
                    short_code = symbol.split(".")[0]
                    name = STOCK_NAMES.get(short_code, q.get("shortName", short_code))
                    
                    price = q.get("regularMarketPrice", 0.0)
                    pct_change = q.get("regularMarketChangePercent", 0.0)
                    
                    # 全球接口交易量是「股」，自動換算為台灣習慣的「張」
                    raw_volume = q.get("regularMarketVolume", 0)
                    volume = raw_volume // 1000
                    
                    bid = q.get("bid", price)
                    ask = q.get("ask", price)
                    
                    color = "red" if pct_change > 0 else "green" if pct_change < 0 else "white"
                    
                    with cols[idx % 3]:
                        st.markdown(f"### {name} ({short_code})")
                        st.markdown(f"**最新實價**: <span style='color:{color};font-size:24px;font-weight:bold;'>{price:.2f}</span> ({pct_change:+.2f}%)", unsafe_allow_html=True)
                        st.write(f"總量: {volume} 張 ｜ 買進托底: {bid:.2f} ｜ 賣出壓盤: {ask:.2f}")
                        st.write("---")
                        
                    gemini_msg_list.append(f"{short_code}={price:.2f}[買{bid:.2f}/賣{ask:.2f}]")
                
                # 一鍵複製指令區
                final_command = "今日10檔 " + " ".join(gemini_msg_list)
                st.write("### ⚔️ 戰情室指令火線外包區")
                st.info("💡 提示：滑鼠移到下方文字框的右上方，會出現官方的「一鍵複製」圖示，點一下即可秒級複製！")
                st.text_area("當前純淨盤口密碼 (直接貼回給 Gemini):", value=final_command, height=100)
            else:
                st.error("數據解析失敗。")
        else:
            st.error(f"無法連線至全球金融接口，錯誤碼: {response.status_code}")
    except Exception as e:
        st.error(f"系統運行異常: {e}")
else:
    st.warning("👈 戰情室等待代碼輸入中...")
