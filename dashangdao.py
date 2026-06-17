import streamlit as st
import requests
import json

# 設定大商道最高國防級儀表板
st.set_page_config(page_title="大商道戰情室中繼站", layout="wide")
st.title("🦅 大商道 3.0 完全體：零阻力火線網址中繼站")
st.write("---")

# 📡 核心黑科技：自動讀取網址後方的股票參數 (例如 ?stocks=2313,3231,3036)
url_params = st.query_params
url_stocks = url_params.get("stocks", "")

st.sidebar.markdown("### ⚔️ 戰情室代碼控制台")
# 如果網址有參數就用網址的，沒有就用預設值
default_value = url_stocks.replace(",", " ") if url_stocks else "2313 3231 3036"
raw_input = st.sidebar.text_area(
    "當前雷達鎖定代碼：",
    value=default_value,
    help="代碼之間用空格或逗號隔開即可"
)

# 處理代碼，對齊奇摩股市後綴 .TW
stock_codes = [code.strip() for code in raw_input.replace(",", " ").split() if code.strip()]

if stock_codes:
    symbols = ",".join([f"{code}.TW" for code in stock_codes])
    url = f"https://tw.stock.yahoo.com/api/v1/getQuotes?symbols={symbols}"
    headers = {'User-Agent': 'Mozilla/5.0'}
    
    try:
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            data = response.json()
            
            if data and "content" in data and "quotes" in data["content"]:
                quotes = data["content"]["quotes"]
                
                st.subheader(f"📋 實時追蹤中（共 {len(quotes)} 檔）｜ 點貨時間：0秒延遲")
                
                cols = st.columns(3)
                gemini_msg_list = []
                
                for idx, q in enumerate(quotes):
                    symbol = q.get("symbol", "")
                    short_code = symbol.split(".")[0]
                    name = q.get("stockName", short_code)
                    
                    price = q.get("price", 0.0)
                    pct_change = q.get("changePercent", 0.0)
                    volume = q.get("volume", 0)
                    
                    # 抓取微觀盤口：最佳買進/賣出價
                    bid = q.get("bid", price)
                    ask = q.get("ask", price)
                    
                    color = "red" if pct_change > 0 else "green" if pct_change < 0 else "white"
                    
                    with cols[idx % 3]:
                        st.markdown(f"### {name} ({short_code})")
                        st.markdown(f"**最新實價**: <span style='color:{color};font-size:24px;font-weight:bold;'>{price:.2f}</span> ({pct_change:+.2f}%)", unsafe_allow_html=True)
                        st.write(f"總量: {volume} 張 ｜ 買進托底: {bid:.2f} ｜ 賣出壓盤: {ask:.2f}")
                        st.write("---")
                        
                    gemini_msg_list.append(f"{short_code}={price:.2f}[買{bid:.2f}/賣{ask:.2f}]")
                
                # 終極一鍵複製區
                final_command = "今日10檔 " + " ".join(gemini_msg_list)
                st.write("### ⚔️ 戰情室指令火線外包區")
                st.info("💡 提示：滑鼠移到下方文字框的右上方，會出現官方的「一鍵複製」圖示，點一下即可秒級複製！")
                st.text_area("當前純淨盤口密碼 (直接貼回給 Gemini):", value=final_command, height=100)
                
        else:
            st.error("無法連線至奇摩大數據接口。")
    except Exception as e:
        st.error(f"系統運行異常: {e}")
else:
    st.warning("👈 戰情室等待代碼輸入中...")
