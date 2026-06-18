import streamlit as st, requests as req
st.set_page_config(page_title="戰情所", layout="wide")

# 戰術 CSS：冷血、高對比
st.markdown('''<style>
.stApp { background-color: #0b0c0f !important; }
.card { background:#15171e; border-radius:8px; padding:12px; margin-bottom:10px; border:1px solid #2d3139; }
.alert-box { background:rgba(255,75,75,0.1); border:1px solid #FF4B4B; padding:15px; border-radius:8px; margin-bottom:20px; }
.text-hit { color:#FF4B4B; font-weight:bold; }
.text-sell { color:#FFB300; font-weight:bold; }
</style>''', unsafe_allow_html=True)

# 核心 DB
DB = {"3231":"緯創","2317":"鴻海","8454":"富邦媒","2367":"燿華","2421":"建準"}

# 渲染函數 (模組化封裝)
def render_stock(c, data):
    n, p, pct = DB.get(c, c), data["p"], data["pct"]
    cls = "text-sell" if "出清" in data["status"] else "text-hit"
    st.markdown(f'''<div class="card">
        <div style="font-size:16px;"><b>{n} ({c})</b></div>
        <div style="font-size:24px;">{p:.2f} <span style="font-size:14px;color:{'#FF4B4B' if pct>0 else '#00FF66'}">{pct:+.2f}%</span></div>
        <div class="{cls}">🎯 {data["status"]}</div>
    </div>''', unsafe_allow_html=True)

# 戰場執行
pm = st.query_params
sk_c = pm.get("stocks", "").split(",")
if st.button("🔄 執行戰情刷新"): st.rerun()

results = []
for c in sk_c:
    # 這裡執行您的數據 fetch 邏輯...
    # 假設已獲取資料，直接封裝狀態
    data = {"p": 150.0, "pct": 1.2, "status": "狙擊就位"}
    results.append((c, data))

# 警報區 (全息綁定)
act_al = [f"🎯 {DB.get(c,c)} 現價{d['p']:.2f} 已達狙擊點" for c, d in results if "狙擊" in d["status"]]
if act_al:
    with st.expander("⚡ 戰情警報雷達", expanded=True):
        for al in act_al: st.markdown(f'<div class="alert-box">{al}</div>', unsafe_allow_html=True)

# 庫存顯示
for c, d in results: render_stock(c, d)
