import streamlit as st, requests as req
st.set_page_config(page_title="戰情所", layout="wide")

# 戰術 CSS：冷血高對比
st.markdown('''<style>
.stApp { background-color: #0b0c0f !important; color: white !important; }
.card { background:#15171e; border-radius:8px; padding:12px; margin-bottom:10px; border:1px solid #2d3139; }
.alert-box { background:rgba(255,75,75,0.15); border:1px solid #FF4B4B; padding:12px; border-radius:8px; margin-bottom:10px; }
.text-hit { color:#FF4B4B; font-weight:bold; }
.text-sell { color:#FFB300; font-weight:bold; }
</style>''', unsafe_allow_html=True)

# 儀表板組件 (復原顯示庫存檔數)
def render_dashboard(total_inv, total_hit):
    col1, col2 = st.columns(2)
    col1.metric("📦 現有庫存", f"{total_inv} 檔")
    col2.metric("🎯 狙擊/警報", f"{total_hit} 檔")
    st.write("---")

# 核心渲染函數
def render_stock(c, data):
    n = "未知" # 簡化版 DB
    status = data.get("status", "")
    cls = "text-sell" if "出清" in status or "警報" in status else "text-hit"
    st.markdown(f'''<div class="card">
        <div><b>{n} ({c})</b></div>
        <div style="font-size:20px;">{data['p']:.2f} ({data['pct']:+.2f}%)</div>
        <div class="{cls}">{status}</div>
    </div>''', unsafe_allow_html=True)

# 主邏輯
pm = st.query_params
sk_c = pm.get("stocks", "").split(",") if pm.get("stocks") else []
if st.button("🔄 刷新戰情"): st.rerun()

# 模擬資料流
results = {c: {"p": 100.0, "pct": 1.2, "status": "狙擊就位"} for c in sk_c}
render_dashboard(len(sk_c), 1)

# 警報區 (復原彈窗)
act_al = [f"🎯 {c} 已入特權區" for c in sk_c]
if act_al:
    with st.container():
        st.markdown('<div class="alert-box">', unsafe_allow_html=True)
        for al in act_al: st.markdown(f"**{al}**")
        st.markdown('</div>', unsafe_allow_html=True)

# 列表渲染
for c, data in results.items(): render_stock(c, data)
