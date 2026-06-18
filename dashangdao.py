import streamlit as st, requests as req, time
st.set_page_config(page_title="即時播報", layout="wide")
st.markdown("""<style>.block-container{padding-top:1rem;} div[data-testid="stButton"] button[kind="primary"]{position:fixed!important;bottom:30px!important;right:20px!important;z-index:999999!important;background-color:#FF4B4B!important;color:white!important;border:2px solid #333!important;border-radius:50px!important;padding:12px 24px!important;font-size:16px!important;font-weight:bold!important;box-shadow:0 6px 12px rgba(0,0,0,0.5)!important;}</style>""", unsafe_allow_html=True)
st.sidebar.markdown("### 🔔 戰情控制台")
mute_al = st.sidebar.checkbox("🔕 關閉進場警報", value=False)
auto_rf = st.sidebar.checkbox("🔄 定時自動刷新", value=True)
rf_min = st.sidebar.slider("⏱️ 頻率 (分鐘)", 1, 15, 3)
hide_op = st.sidebar.checkbox("🚫 自動隱藏高飛股", value=True)
max_pre = st.sidebar.slider("📈 允許最大溢價上限 (%)", 5, 100, 20)
st.title("📊 大商道戰情指揮所 v19.5")
if st.button("🔄 刷新最新報價", type="primary"): st.rerun()
if auto_rf: st.components.v1.html(f"<script>setTimeout(function(){{window.location.reload();}},{rf_min*60*1000});</script>", height=0)
st.write("---")
al_holder, act_al, res_list = st.empty(), [], []
DB = {"3231":"緯創","2317":"鴻海","2301":"光寶科","2603":"長榮","1513":"中興電","2891":"中信金","2356":"英業達","2618":"長榮航","1101":"台泥","2449":"京元電","2313":"華通","3036":"文曄","2421":"建準","2337":"旺宏","2367":"燿華","5347":"世界","2412":"中華電","2002":"中鋼","1326":"台化","2881":"富邦金","2882":"國泰金","1519":"華城","2353":"宏碁","2409":"友達","2886":"兆豐金","2884":"玉山金","2892":"第一金","2880":"華南金","2885":"元大金","2890":"永豐金","5880":"合庫金","2883":"開發金"}
pm = st.query_params
sk_p, zn_p, tp_p = pm.get("stocks",""), pm.get("zones",""), pm.get("types","")
raw_items = []
if sk_p:
    sk_c = [c.strip() for c in sk_p.split(",") if c.strip()]; zns = [z.strip() for z in zn_p.split(",")] if zn_p else []; tps = [t.strip() for t in tp_p.split(",")] if tp_p else []
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
            p = sel.split(" | "); raw_items.append({"code":p[0].strip(), "name":p[1].strip(), "zone":"待精算", "type":"W", "badge":"🔥", "suf":p[2].strip()})
proc_list, hid_cnt = [], 0
for item in raw_items:
    c, sufs = item["code"], [item["suf"]] if item["suf"] else [".TW", ".TWO"]
    for suf in sufs:
        try:
            r = req.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{c}{suf}?interval=1d&range=1d", headers={'User-Agent':'Mozilla/5.0'}, timeout=2).json()["chart"]["result"][0]; m = r["meta"]; p = m["regularMarketPrice"]
            if p > 0:
                pc = m.get("chartPreviousClose", p); pct = ((p - pc) / pc) * 100 if pc else 0.0
                try: q = r["indicators"]["quote"][0]; op, hi, lo, vl = q.get("open",[p])[0] or p, q.get("high",[p])[0] or p, q.get("low",[p])[0] or p, q.get("volume",[0])[0] or 0
                except: op, hi, lo, vl = p, p, p, 0
                pre_pct, status_text = 0.0, "待精算"
                try:
                    zp = item["zone"].split('-')
                    if len(zp) == 2:
                        lb, hb = float(zp[0].strip()), float(zp[1].strip())
                        if p > hb: pre_pct, status_text = ((p - hb) / hb) * 100, f"📈 溢價: {((p - hb) / hb) * 100:.1f}%"
                        elif p < lb: status
