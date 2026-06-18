import streamlit as st
import requests
import time

st.set_page_config(page_title="即時播報", layout="wide")

# ⚡ 逆向工程：利用 CSS 將 Streamlit 原生 Primary 按鈕強制改裝成右下角鋼鐵懸浮鈕
st.markdown("""
    <style>
    .block-container { padding-top: 1rem; padding-bottom: 1rem; }
    div[data-testid="stButton"] button[kind="primary"] {
        position: fixed !important; bottom: 30px !important; right: 20px !important;
        z-index: 999999 !important; background-color: #FF4B4B !important; color: white !important;
        border: 2px solid #333333 !important; border-radius: 50px !important;
        padding: 12px 24px !important; font-size: 16px !important; font-weight: bold !important;
        box-shadow: 0px 6px 12px rgba(0,0,0,0.5) !important;
    }
    </style>
""", unsafe_allow_html=True)

hd = {'User-Agent': 'Mozilla/5.0'}
st.sidebar.markdown("### 🔔 戰情控制台")
mute_al = st.sidebar.checkbox("🔕 關閉進場警報", value=False)
auto_rf = st.sidebar.checkbox("🔄 定時自動刷新", value=True)
rf_min = st.sidebar.slider("⏱️ 頻率 (分鐘)", 1, 15, 3)
hide_op = st.sidebar.checkbox("🚫 自動隱藏高飛股", value=True)
max_pre = st.sidebar.slider("📈 允許最大溢價上限 (%)", 5, 100, 20)

st.title("📊 大商道戰情指揮所 v18.0")
# ⚡ 懸浮秒刷核心：點擊此原生懸浮鈕，直接觸發 st.rerun()，零延遲更新數據！
if st.button("🔄 刷新最新報價", type="primary"):
    st.rerun()

if auto_rf:
    st.components.v1.html(f"<script>setTimeout(function(){{window.location.reload();}},{rf_min*60*1000});</script>", height=0)
st.write("---")
al_holder, act_al, res_list = st.empty(), [], []

DB = {}
DB.update({"3231": "緯創", "2317": "鴻海", "2301": "光寶科", "2603": "長榮"})
DB.update({"1513": "中興電", "2891": "中信金", "2356": "英業達", "2618": "長榮航"})
DB.update({"1101": "台泥", "2449": "京元電", "2313": "華通", "3036": "文曄"})
DB.update({"2421": "建準", "2337": "旺宏", "2367": "燿華", "5347": "世界"})
DB.update({"2412": "中華電", "2002": "中鋼", "1326": "台化", "2881": "富邦金"})
DB.update({"2882": "國泰金", "1519": "華城", "2353": "宏碁", "2409": "友達"})
DB.update({"2886": "兆豐金", "2884": "玉山金", "2892": "第一金", "2880": "華南金"})
DB.update({"2885": "元大金", "2890": "永豐金", "5880": "合庫金", "2883": "開發金"})

pm = st.query_params
sk_p, zn_p, tp_p = pm.get("stocks",""), pm.get("zones",""), pm.get("types","")
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
        n = DB.get(c) or "個股 "+c
        b = "📦" if tps[i]=="I" else (bg[c_id] if tps[i]=="C" else "🔍")
        if tps[i]=="C": c_id += 1
        raw_items.append({"code":c, "name":n, "zone":zns[i], "type":tps[i], "badge":b, "suf":""})

st.sidebar.markdown("---")
q_in = st.sidebar.text_input("🔍 盤中臨時追加", value="")
if q_in.strip():
    m_items = [f"{k} | {n} | .TW" for k, n in DB.items() if q_in.strip().lower() in k or q_in.strip().lower() in n.lower()]
    if m_items:
        sel = st.sidebar.selectbox("🎯 符合標的:", ["-- 請選擇 --"] + m_items)
        if sel != "-- 請選擇 --":
            p = sel.split(" | ")
            raw_items.append({"code":p[0].strip(), "name":p[1].strip(), "zone":"待精算", "type":"W", "badge":"🔥", "suf":p[2].strip()})

proc_list, hid_cnt = [], 0
for item in raw_items:
    c, sufs = item["code"], [item["suf"]] if item["suf"] else [".TW", ".TWO"]
    for
