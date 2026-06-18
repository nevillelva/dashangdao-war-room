import streamlit as st, requests as req
st.set_page_config(page_title="戰情所", layout="wide")

# 戰術 CSS：恢復 v29.0 完美高對比視覺與資訊密度
st.markdown('''<style>
.stApp { background-color: #0b0c0f !important; color: white !important; }
.card { background:#1e1e1e; border-radius:8px; padding:15px; margin-bottom:12px; border:1px solid #444; }
.alert-box { background:rgba(255,75,75,0.15); border:1px solid #FF4B4B; padding:15px; border-radius:8px; margin-bottom:15px; }
.text-val { font-size:22px; font-weight:bold; }
.text-label { font-size:13px; color:#aaa; }
</style>''', unsafe_allow_html=True)

# 核心設定
DB = {"3231":"緯創","8454":"富邦媒","2367":"燿華","2317":"鴻海"}
BOMB = {"8454": "營收公佈 (4天後)", "2367": "股東會 (5天後)"}
DOG_SCORE = {"8454": 4, "2367": 2}

st.title("📊 戰情所")
if st.button("🔄 刷新數據"): st.rerun()

# 模擬儀表板復原
c1, c2, c3 = st.columns(3)
c1.metric("📦 現有庫存", "2 檔")
c2.metric("🎯 狙擊就位", "1 檔")
c3.metric("🛡️ 財報防禦平均", "3.5 分")
st.write("---")

# 警報區 (全息綁定)
st.markdown('<div class="alert-box">🚨 戰情雷達：多檔個股已達狙擊點</div>', unsafe_allow_html=True)

# 渲染完整卡片邏輯 (v29.0 核心)
for c, name in DB.items():
    dog_score = DOG_SCORE.get(c, 3)
    dog_clr = "#00FF66" if dog_score >= 4 else "#FF4B4B"
    st.markdown(f'''<div class="card">
        <div style="display:flex; justify-content:space-between;">
            <b>{name} ({c})</b>
            <span style="color:{dog_clr};">🛡️ 價值盾: {dog_score}分</span>
        </div>
        <div class="text-val">380.00 <span style="font-size:14px;color:#FF4B4B;">+1.2%</span></div>
        <div style="display:flex; gap:20px; margin-top:10px;">
            <div class="text-label">👥 主力成本: 378.0</div>
            <div class="text-label">🔮 預估總量: 1500張</div>
        </div>
        <div style="margin-top:10px; color:#FFB300;">🎯 狙擊就位 | 📅 {BOMB.get(c, "無事件")}</div>
    </div>''', unsafe_allow_html=True)
    
    with st.expander("💼 模擬持倉配置"):
        cols = st.columns(3)
        cols[0].number_input("成本價", value=380.0, key=f"c_{c}")
        cols[1].number_input("張數", value=1.05, step=0.01, key=f"q_{c}")
        cols[2].number_input("天數", value=1, key=f"d_{c}")
        st.markdown(f"💰 淨損益 (已扣稅費): <span style='color:#FF4B4B;'>+5,200 元</span>", unsafe_allow_html=True)
