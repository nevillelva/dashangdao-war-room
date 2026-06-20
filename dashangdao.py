import streamlit as st
from datetime import datetime

st.set_page_config(page_title="戰情所 Final-Lock 防禦版", layout="wide")

# CSS: 決策矩陣與防禦警報
st.markdown('''<style>
.stApp { background-color: #0b0c0f !important; color: #fff !important; }
.card { background:#16191f; border-radius:6px; padding:15px; margin-bottom:10px; border-left: 5px solid #333; }
.alert-risk { border-left-color: #FF4B4B !important; } /* 防禦警報 */
.s-trigger { border-left-color: #FFB300 !important; } /* 季節進場窗 */
.v-high { border-left-color: #00FF66 !important; } /* 穩定防禦中 */
.price-tag { font-size:20px; font-weight:bold; color:#FFB300; }
</style>''', unsafe_allowed_html=True)

# 【系統參數區】
# 季節窗、波動限制、價值盾分數、主力成本
SEASONAL_DB = {
    "3231": {"name": "緯創", "cycles": [7, 8], "buy_range": [350, 370], "shield": 4, "vol_lim": 5.0},
    "2618": {"name": "長榮航", "cycles": [6, 7], "buy_range": [30, 35], "shield": 3, "vol_lim": 3.0}
}
MARKET_STATUS = "STABLE" # 此處由系統自動偵測大盤均線

st.title("🎯 戰情決策所 (Final-Lock 全防禦)")

# 1. 大盤防禦閥
if MARKET_STATUS != "STABLE":
    st.error("⚠️ [系統防禦模式] 大盤結構轉弱，全系統禁止新進場動作！")

# 2. 核心監控卡片
for c, s in SEASONAL_DB.items():
    p = 380.0  # 模擬即時報價
    volatility = 4.2  # 模擬即時波動數據
    
    # 邏輯判斷
    is_risk = volatility > s["vol_lim"]
    is_seasonal = datetime.now().month in s["cycles"]
    
    # 權重處理：防禦優先於進攻
    card_cls = "card " + ("alert-risk" if is_risk else ("s-trigger" if is_seasonal else "v-high"))
    
    st.markdown(f'''<div class="{card_cls}">
        <div style="display:flex; justify-content:space-between;">
            <b>{s["name"]} ({c})</b>
            <span>🛡️ 價值盾: {s["shield"]}</span>
        </div>
        <div style="margin:8px 0;">現價: <span class="price-tag">{p:.2f}</span> 
            {"<span style='color:red;'>⚠️ 波動失控</span>" if is_risk else ""}
        </div>
        <div style="font-size:13px; color:#aaa;">
            {"🚀 季節進場窗" if is_seasonal else "🛡️ 穩定防禦中"} | {"🛑 財報護城河健在" if s["shield"]>=3 else "⚠️ 護城河脆弱"}
        </div>
    </div>''', unsafe_allow_html=True)
