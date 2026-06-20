import streamlit as st
import requests as req

# 系統配置
st.set_page_config(page_title="戰情決策所 - 旗艦指揮中心", layout="wide")

# CSS 視覺強化：對齊旗艦級視覺
st.markdown('''<style>
.stApp { background-color: #0b0c0f !important; color: #fff !important; }
.card { background:#16191f; border-radius:8px; padding:20px; margin-bottom:15px; border: 2px solid #333; }
.alert-banner { background:#4a2b0f; border:1px solid #FFB300; padding:15px; margin-bottom:20px; border-radius:6px; color:#fff; }
.price-tag { font-size:28px; font-weight:bold; color:#FFB300; }
</style>''', unsafe_allow_html=True)

# 1. 側面控制台 (SideBar)
with st.sidebar:
    st.markdown("### 🛠️ 1. 側面控制台")
    st.multiselect("戰術標的鎖定", ["3231 緯創", "8454 富邦媒", "2618 長榮航"], default=["3231 緯創", "8454 富邦媒"])
    st.slider("自動定時刷新 (分)", 1, 3, 3)
    st.slider("價值盾篩選", 2, 4, 4)
    st.selectbox("週期慣性偵測", ["Q3 科技慣性", "Q4 金融動能"])
    st.checkbox("破底停損監控", True)
    st.checkbox("自動警報", True)

# 2. 警報區
st.markdown('<div class="alert-banner">🚨 戰情雷達：富邦媒 (8454) 觸發出清風控警報，請立即結算！</div>', unsafe_allow_html=True)

# 3. 標題與刷新
st.title("🎯 戰情決策所 (旗艦版)")
if st.button("🔄 強制刷新最新報價 (v42.0)"): st.rerun()

# 4. 雙欄核心決策區
col1, col2 = st.columns(2)

def render_decision_card(col, name, code, cost, pnl):
    with col:
        st.markdown(f'''<div class="card">
            <b>{name} ({code})</b> | 🛡️ 價值盾: 4分
            <div class="price-tag">380.00 <span style="font-size:16px; color:#FF4B4B;">+1.2%</span></div>
            <div style="font-size:14px; color:#aaa; margin-top:10px;">數據儀表板: 開盤 375.0 | 最高 385.0 | 成交量 1250張</div>
            <hr>
            <b>錨定進價區間: [ 350 - 370 ]</b>
            <div style="margin-top:10px; font-size:13px;">
                主力成本: {cost} | 💰 今日淨損益: +{pnl}元
            </div>
        </div>''', unsafe_allow_html=True)
        st.button(f"❌ 一鍵清空 {name}", key=f"del_{code}")

render_decision_card(col1, "緯創", "3231", "378.0", "5,200")
render_decision_card(col2, "富邦媒", "8454", "390.0", "5,200")
