import streamlit as st
import requests
import time

# 銲死最高防禦級低調外殼
st.set_page_config(page_title="即時播報", layout="wide")

st.markdown("""
    <style>
    .block-container { padding-top: 1rem; padding-bottom: 1rem; }
    /* ⚡ 終極黑科技：右下角鋼鐵懸浮鈕，永遠置頂、無視滑動、大拇指極速秒刷 */
    .floating-refresh-btn {
        position: fixed;
        bottom: 30px;
        right: 20px;
        z-index: 999999;
        background-color: #FF4B4B;
        color: white;
        border: 2px solid #333333;
        border-radius: 50px;
        padding: 14px 22px;
        font-size: 16px;
        font-weight: bold;
        box-shadow: 0px 6px 12px rgba(0,0,0,0.5);
        cursor: pointer;
        transition: all 0.1s ease;
    }
    .floating-refresh-btn:hover { background-color: #FF3333; transform: scale(1.03); }
    .floating-refresh-btn:active { transform: scale(0.95); }
    </style>
    <button class="floating-refresh-btn" onclick="window.parent.location.reload();">
        🔄 刷新最新報價
    </button>
""", unsafe_allow_html=True)

hd = {'User-Agent': 'Mozilla/5.0'}

# 📡 側邊欄控制台
st.sidebar.markdown("### 🔔 戰情警報與自動刷新")
mute_alerts = st.sidebar.checkbox("🔕 暫時靜音/手動關閉進場警報", value=False)
auto_refresh = st.sidebar.checkbox("🔄 啟動盤中定時自動刷新", value=True)
refresh_min = st.sidebar.slider("⏱️ 設定刷新頻率 (分鐘)", min_value=1, max_value=15, value=3)

st.sidebar.markdown("---")
st.sidebar.markdown("### 🎯 視窗射程空間優化")
hide_overpriced = st.sidebar.checkbox("🚫 自動隱藏嚴重高飛股 (釋放空間)", value=True)
max_premium_pct = st.sidebar.slider("📈 允許最大溢價上限 (%)", min_value=5, max_value=100, value=20)

st.title("📊 大商道戰情指揮所 v17.1")

if auto_refresh:
    st.components.v1.html(f"""
        <script>
        setTimeout(function() {{ window.parent.location.reload(); }}, {refresh_min * 60 * 1000});
        </script>
    """, height=0)

st.write("---")
alert_holder = st.empty()
active_alerts = []

# 👑 智慧股名庫
DB = {
    "3231": "緯創", "2317": "鴻海", "2301": "光寶科", "2603": "長榮",
    "1513": "中興電", "2891": "中信金", "2356": "英業達", "2618": "長榮航",
    "1101": "台泥", "2449": "京元電", "2313": "華通", "3036": "文曄",
    "2421": "建準", "2337": "旺宏", "2367": "燿華", "5347": "世界",
    "2412": "中華電", "2002": "中鋼", "1326": "台化", "2881": "富邦金",
    "2882": "國泰金", "1519": "華城", "2353": "宏碁", "2409": "友達",
    "2886": "兆豐金", "2884": "玉山金", "2892": "第一金", "2880": "華南金",
    "2885": "元大金", "2890": "永豐金", "5880": "合庫金", "2883": "開發金",
    "2887": "台新金", "2888": "新光金", "3481": "群創", "2609": "陽明",
    "2615": "萬海", "2610": "華航", "1504": "東元", "1503": "士電",
    "1605": "華新", "2324": "仁寶", "2377": "微星", "2352": "佳世達",
    "3037": "欣興", "2344": "華邦電", "3711": "日月光", "3035": "智原",
    "2368": "金像電", "3044": "健鼎", "5388
