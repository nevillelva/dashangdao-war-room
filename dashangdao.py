import streamlit as st

st.set_page_config(layout="wide", page_title="戰情決策所 - 旗艦指揮中心")

# CSS: 打造高密度資訊戰情室
st.markdown('''<style>
.stApp { background-color: #0b0c0f !important; color: #fff !important; }
.card { background:#16191f; border-radius:8px; padding:20px; margin-bottom:15px; border: 2px solid #333; }
.alert-banner { background:#4a2b0f; border:1px solid #FFB300; padding:15px; margin-bottom:20px; border-radius:6px; color:#fff; }
.price-tag { font-size:28px; font-weight:bold; color:#FFB300; }
.data-box { background:#262730; padding:10px; border-radius:5px; font-size:13px; margin:10px 0; border: 1px solid #444; }
div.stButton > button { background-color: #FF4B4B !important; color: white !important; font-weight: bold; width: 100%; }
</style>''', unsafe_allow_html=True)

# 狀態管理
if 'deleted' not in st.session_state: st.session_state.deleted = []

# 1. 側面控制台 (完全對照圖示)
with st.sidebar:
    st.markdown("### 1. 側面控制台")
    st.multiselect("戰術標的鎖定", ["3231 緯創", "8454 富邦媒", "2618 長榮航"], default=["3231 緯創", "8454 富邦媒"])
    st.slider("自動定時刷新", 1, 3, 3)
    st.slider("價值盾篩選", 2, 4, 4)
    st.selectbox("週期慣性偵測", ["Q3 科技慣性", "Q4 金融動能"])
    st.checkbox("破底停損監控", True)
    st.checkbox("自動警報", True, key="auto_alert")

# 2. 頂部警報與標題
st.markdown('<div class="alert-banner">🚨 戰情雷達：富邦媒 (8454) 觸發出清風控警報，請立即結算！</div>', unsafe_allow_html=True)
st.title("🎯 戰情決策所 (旗艦版)")
if st.button("🔄 強制刷新最新報價 (v42.0)"): st.rerun()

# 3. 雙欄決策卡片區
col1, col2 = st.columns(2)
stocks = [
    {"n": "緯創", "c": "3231", "buy": "350-370", "shd": 4, "open": 375, "high": 385, "low": 372, "vol": 1250, "cost": 378.0},
    {"n": "富邦媒", "c": "8454", "buy": "即將公佈營收", "shd": 5, "open": 390, "high": 405, "low": 385, "vol": 450, "cost": 390.0}
]

for i, s in enumerate(stocks):
    if s['c'] not in st.session_state.deleted:
        with (col1 if i == 0 else col2):
            st.markdown(f'''<div class="card">
                <b>{s['n']} ({s['c']})</b> | 🛡️ 價值盾:{s['shd']}分
                <div class="price-tag">380.00 <span style="font-size:16px; color:#FF4B4B;">+1.2%</span></div>
                <div class="data-box">開盤:{s['open']} | 最高:{s['high']} | 最低:{s['low']} | 成交量:{s['vol']}張</div>
                <b>錨定進價區間 (v44)</b><br>
                <div style="background:#FFD700; color:#000; padding:5px; font-weight:bold;">建議進價區間: [ {s['buy']} ]</div>
                <div style="margin-top:10px; font-size:13px;">
                    主力與週期訊號<br>
                    👥 主力成本:{s['cost']} | 🛡️ 抗股強勢股 (抗 Beta)<br>
                    🚀 季節進場富已開啟！(Q3 科技慣性)<br>
                    💰 今日淨損益: +5,200元
                </div>
            </div>''', unsafe_allow_html=True)
            if st.button(f"❌ 一鍵清空今日此標的", key=f"del_{s['c']}"):
                st.session_state.deleted.append(s['c'])
                st.rerun()
