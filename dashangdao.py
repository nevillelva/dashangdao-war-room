import streamlit as st
import requests
import time

# 銲死最高防禦級低調外殼
st.set_page_config(page_title="即時播報", layout="wide")

st.markdown("""
    <style>
    .block-container { padding-top: 1rem; padding-bottom: 1rem; }
    /* ⚡ 鋼鐵懸浮鈕：換成 window.location.reload() 徹底擊穿跨網域安全封鎖牆 */
    .float-rf-btn {
        position: fixed; bottom: 30px; right: 20px; z-index: 999999;
        background-color: #FF4B4B; color: white; border: 2px solid #333;
        border-radius: 50px; padding: 14px 22px; font-size: 16px;
        font-weight: bold; box-shadow: 0px 6px 12px rgba(0,0,0,0.5); cursor: pointer;
    }
    </style>
    <button class="float-rf-btn" onclick="window.location.reload();">🔄 刷新最新報價</button>
""", unsafe_allow_html=True)

hd = {'User-Agent': 'Mozilla/5.0'}
st.sidebar.markdown("### 🔔 戰情控制台")
mute_al = st.sidebar.checkbox("🔕 關閉進場警報", value=False)
auto_rf = st.sidebar.checkbox("🔄 定時自動刷新", value=True)
rf_min = st.sidebar.slider("⏱️ 頻率 (分鐘)", 1, 15, 3)
hide_op = st.sidebar.checkbox("🚫 自動隱藏高飛股", value=True)
max_pre = st.sidebar.slider("📈 允許最大溢價上限 (%)", 5, 100, 20)

st.title("📊 大商道戰情指揮所 v17.6")
if auto_rf:
    # ⚡ 同步優化自動刷新核心，確保雲端沙盒定時滿血重組
    st.components.v1.html(f"<script>setTimeout(function(){{window.location.reload();}},{rf_min*60*1000});</script>", height=0)
st.write("---")
al_holder, act_al, res_list = st.empty(), [], []

# 👑 離線安全字典分流，100%防截斷
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
    for suf in sufs:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{c}{suf}?interval=1d&range=1d&_={int(time.time())}"
        try:
            r = requests.get(url, headers=hd, timeout=2)
            if r.status_code == 200:
                res = r.json().get("chart", {}).get("result", [None])[0]
                if res:
                    m = res.get("meta", {})
                    p = m.get("regularMarketPrice", 0.0)
                    if p > 0:
                        pc = m.get("chartPreviousClose", p)
                        pct = ((p - pc) / pc) * 100 if pc > 0 else 0.0
                        try:
                            q = res.get("indicators", {}).get("quote", [{}])[0]
                            op, hi, lo, vl = q.get("open", [p])[0], q.get("high", [p])[0], q.get("low", [p])[0], q.get("volume", [0])[0]
                        except: op, hi, lo, vl = p, p, p, 0
                        
                        pre_pct, status_text = 0.0, "待精算"
                        try:
                            zp = item["zone"].split('-')
                            if len(zp) == 2:
                                lb, hb = float(zp[0].strip()), float(zp[1].strip())
                                if p > hb:
                                    pre_pct = ((p - hb) / hb) * 100
                                    status_text = f"📈 溢價: {pre_pct:.1f}%"
                                elif p < lb:
                                    pre_pct = 0.0
                                    status_text = f"💎 超值折價: {((lb - p) / lb) * 100:.1f}%"
                                else:
                                    pre_pct = 0.0
