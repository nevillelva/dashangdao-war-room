import streamlit as st, requests as req, time
st.set_page_config(page_title="即時播報", layout="wide")

# CSS 穿透注入：壓緊垂直間距、焊死暗黑高對比、加入「橘光雙向脈衝」逃命燈
css = '''<style>
.stApp, [data-testid="stAppViewContainer"], [data-testid="stHeader"] {
    background-color: #0b0c0f !important; color: #ffffff !important;
}
[data-testid="stSidebar"] { background-color: #12141a !important; }
h1, h2, h3, h4, h5, h6, p, label, .stMarkdown { color: #ffffff !important; }
div[data-testid="stMetric"] {
    background-color: #181b22 !important; padding: 8px 12px !important;
    border-radius: 6px !important; border: 1px solid #2d3139 !important;
}
div[data-testid="stMetricValue"] div {
    font-size: 22px !important; color: #fff !important;
}
div[data-testid="stMetricLabel"] p {
    font-size: 12px !important; color: #a1a8b8 !important;
}
.block-container {
    padding-top: 0.5rem !important; padding-bottom: 0.5rem !important;
}
div[data-testid="stButton"] button[kind="primary"] {
    position: fixed !important; bottom: 30px !important;
    right: 20px !important; z-index: 999999 !important;
    background-color: #FF4B4B !important; color: white !important;
    border: 2px solid #333 !important; border-radius: 50px !important;
    padding: 12px 24px !important; font-size: 16px !important;
    font-weight: bold !important;
    box-shadow: 0px 6px 12px rgba(0,0,0,0.5) !important;
}
@keyframes pulse-red {
    0% { border-color: #FF4B4B; box-shadow: 0 0 2px rgba(255,75,75,0.2); }
    50% { border-color: #ff1a1a; box-shadow: 0 0 8px rgba(255,75,75,0.5); }
    100% { border-color: #FF4B4B; box-shadow: 0 0 2px rgba(255,75,75,0.2); }
}
.hit-card {
    animation: pulse-red 2s infinite !important;
    border: 2px solid #FF4B4B !important;
}
/* ⚡ 橘色脈衝：庫存超限面臨「要賣、觸發停損」的鋼鐵風控視覺燈 */
@keyframes pulse-orange {
    0% { border-color: #FFB300; box-shadow: 0 0 2px rgba(255,179,0,0.2); }
    50% { border-color: #ff8000; box-shadow: 0 0 8px rgba(255,179,0,0.5); }
    100% { border-color: #FFB300; box-shadow: 0 0 2px rgba(255,179,0,0.2); }
}
.sell-card {
    animation: pulse-orange 1.5s infinite !important;
    border: 2px solid #FFB300 !important;
}
</style>'''
st.markdown(css, unsafe_allow_html=True)

st.sidebar.markdown("### 🔔 戰情控制台")
mute_al = st.sidebar.checkbox("🔕 關閉進場警報", value=False)
auto_rf = st.sidebar.checkbox("🔄 定時自動刷新", value=True)
rf_min = st.sidebar.slider("⏱️ 頻率 (分鐘)", 1, 15, 3)

st.sidebar.markdown("---")
st.sidebar.markdown("### 🔍 起漲智慧過濾")
min_vol_mult = st.sidebar.slider(
    "🔥 最低量增倍數 (相比近期均量)", 1.0, 5.0, 1.2, step=0.1
)

# 🧠 出清/要賣/跌幅上限的「百分比智慧停損風控」
st.sidebar.markdown("---")
st.sidebar.markdown("### 📉 庫存出清風控")
stop_loss_pct = st.sidebar.slider(
    "🚨 破底停損容忍上限 (%)", 1.0, 10.0, 3.0, step=0.5
)

st.title("📊 戰情所")
if st.button("🔄 刷新最新報報價", type="primary"):
    st.rerun()

if auto_rf:
    ms = rf_min * 60 * 1000
    js = f"<script>setTimeout(function(){{location.reload();}},{ms});</script>"
    st.components.v1.html(js, height=0)

st.write("---")
if "cache" not in st.session_state:
    st.session_state["cache"] = {}

DB = {
    "3231":"緯創","2317":"鴻海","2301":"光寶科","2603":"長榮",
    "1513":"中興電","2891":"中信金","2356":"英業達","2618":"長榮航",
    "1101":"台泥","2449":"京元電","2313":"華通","3036":"文曄",
    "2421":"建準","2337":"旺宏","2367":"燿華","5347":"世界",
    "2412":"中華電","2002":"中鋼","1326":"台化","2881":"富邦金",
    "2882":"國泰金","1519":"學城","2353":"宏碁","2409":"友達",
    "2886":"兆豐金","2884":"玉山金","2892":"第一金","2880":"華南金",
    "2885":"元大金","2890":"永豐金","5880":"合庫金","2883":"開發金"
}

pm = st.query_params
sk_p = pm.get("stocks","")
zn_p = pm.get("zones","")
tp_p = pm.get("types","")
raw_items = []

if sk_p:
    sk_c = [c.strip() for c in sk_p.split(",") if c.strip()]
    zns = [z.strip() for z in zn_p.split(",")] if zn_p else []
    tps = [t.strip() for t in tp_p.split(",")] if tp_p else []
    while len(zns) < len(sk_c): zns.append("待精算")
    while len(tps) < len(sk_c): tps.append("W")
    bg = ["🥇","🥈","🥉","🚀"] + ["🔍"]*20
    c_id = 0
    for i, c in enumerate(sk_c):
        n = DB.get(c, f"個股 {c}")
        b = "📦" if tps[i]=="I" else (bg[c_id] if tps[i]=="C" else "🔍")
        if tps[i]=="C": c_id +=
