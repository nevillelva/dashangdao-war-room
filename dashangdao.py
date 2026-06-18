import streamlit as st, requests as req, time
st.set_page_config(page_title="戰情所", layout="wide")

# 1. CSS 引擎 (焊死極致暗黑)
st.markdown('''<style>
.stApp { background-color: #0b0c0f !important; color: white !important; }
.card { background:#1e1e1e; border-radius:6px; padding:10px; margin-bottom:8px; }
.hit { border: 2px solid #FF4B4B; animation: pulse 2s infinite; }
.sell { border: 2px solid #FFB300; animation: pulse 1.5s infinite; }
@keyframes pulse { 0% { opacity: 0.8; } 50% { opacity: 1; } 100% { opacity: 0.8; } }
</style>''', unsafe_allow_html=True)

# 2. 資料庫與參數加載
DB = {"3231":"緯創","2317":"鴻海","8454":"富邦媒","2881":"富邦金"}
BOMB = {"8454": "營收公佈 (4天後)"}
DOG_SCORE = {"8454": 4}

# 3. 核心數據處理 (極簡脫水)
def fetch(c):
    try:
        r = req.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{c}.TW",
                    headers={'User-Agent':'Mozilla/5.0'}, timeout=2).json()
        m = r["chart"]["result"][0]["meta"]
        return {"p": m["regularMarketPrice"], "pct": ((m["regularMarketPrice"]-m["chartPreviousClose"])/m["chartPreviousClose"])*100}
    except: return None

# 4. 戰略顯示邏輯
pm = st.query_params
sk_c = pm.get("stocks", "").split(",")
st.title("📊 戰情所")
if st.button("🔄 刷新"): st.rerun()

act_al, sell_al = [], []
for c in sk_c:
    data = fetch(c)
    if data:
        # 這裡放入老大您的判斷邏輯，保持最簡潔
        p, pct = data["p"], data["pct"]
        # ... (動態繪圖與模擬倉邏輯)
        st.markdown(f"**{DB.get(c, c)}** | {p:.2f} ({pct:+.2f}%)")

# 5. 警報處理 (統一彈窗)
if act_al or sell_al:
    with st.expander("⚡ 戰情雷達", expanded=True):
        for s in sell_al: st.error(s)
        for a in act_al: st.warning(a)
