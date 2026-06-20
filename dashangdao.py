import streamlit as st

st.set_page_config(page_title="戰情決策所 - 旗艦指揮中心", layout="wide")

# CSS: 修正反白、強化按鈕對比
st.markdown('''<style>
.stApp { background-color: #0b0c0f !important; color: #fff !important; }
.card { background:#16191f; border-radius:8px; padding:20px; margin-bottom:15px; border: 2px solid #333; }
.alert-banner { background:#4a2b0f; border:1px solid #FFB300; padding:15px; margin-bottom:20px; border-radius:6px; color:#fff; }
.price-tag { font-size:24px; font-weight:bold; color:#FFB300; }
/* 修正清除按鈕視覺 */
div.stButton > button { background-color: #ffffff !important; color: #000000 !important; font-weight: bold; width: 100%; border: none; }
</style>''', unsafe_allow_html=True)

# 左側控制台：補全產業選項
with st.sidebar:
    st.markdown("### 🛠️ 指揮官控制台")
    st.multiselect("戰術標的鎖定", ["3231 緯創", "8454 富邦媒", "2618 長榮航", "2317 鴻海"], default=["3231 緯創", "8454 富邦媒"])
    st.slider("自動定時刷新 (分)", 1, 3, 3)
    # 新增完整產業慣性
    st.selectbox("產業週期偵測", ["科技硬體", "金融動能", "航空旅遊", "原物料傳產", "生技醫療"])
    st.checkbox("破底停損監控", True)
    st.checkbox("自動警報", True)

# 頂層警報
st.markdown('<div class="alert-banner">🚨 戰情雷達：多標的監控中 | 自動平衡系統已啟動</div>', unsafe_allow_html=True)

st.title("🎯 戰情決策所 (旗艦終極版)")
if st.button("🔄 強制刷新報價數據"): st.rerun()

# 核心雙欄佈局
col1, col2 = st.columns(2)

def draw_card(col, name, code, cost, pnl):
    with col:
        st.markdown(f'''<div class="card">
            <b>{name} ({code})</b> | 🛡️ 價值盾: 4分
            <div class="price-tag">380.00 <span style="font-size:14px; color:#FF4B4B;">+1.2%</span></div>
            <div style="font-size:14px; color:#aaa;">錨定進價區間: [ 350 - 370 ]</div>
            <div style="border-top: 1px solid #333; padding-top: 10px; font-size:13px; margin-top:10px;">
                主力成本: {cost} | 💰 今日淨損益: +{pnl}元
            </div>
        </div>''', unsafe_allow_html=True)
        
        # 隱藏式模擬倉
        with st.expander("💼 模擬持倉精算"):
            st.number_input(f"調整成本 {code}", value=float(cost), key=f"c_{code}")
            st.button(f"執行試算 {code}", key=f"calc_{code}")
            
        st.button(f"❌ 清除 {name}", key=f"del_{code}")

# 初始化卡片
draw_card(col1, "緯創", "3231", "378.0", "5,200")
draw_card(col2, "富邦媒", "8454", "390.0", "5,200")
