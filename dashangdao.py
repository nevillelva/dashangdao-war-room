import streamlit as st, requests as req, time
st.set_page_config(page_title="即時播報", layout="wide")

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

pm = st.query_params
sk_p = pm.get("stocks","")
zn_p = pm.get("zones","")
tp_p = pm.get("types","")
sk_c = [c.strip() for c in sk_p.split(",") if c.strip()] if sk_p else []

st.sidebar.markdown("### 🔔 戰情控制台")
mute_al = st.sidebar.checkbox("🔕 關閉進場警報", value=False)
auto_rf = st.sidebar.checkbox("🔄 定時自動刷新", value=True)
rf_min = st.sidebar.slider("⏱️ 頻率 (分鐘)", 1, 15, 3)

st.sidebar.markdown("---")
st.sidebar.markdown("### 🔍 起漲智慧過濾")
min_vol_mult = st.sidebar.slider(
    "🔥 最低量增倍數 (相比近期均量)", 1.0, 5.0, 1.2, step=0.1
)

wl_p = pm.get("wl", "")
wl_init = [x.strip() for x in wl_p.split(",")] if wl_p else []
whitelist = st.sidebar.multiselect(
    "📌 豁免過濾個股名單", sk_c, default=wl_init
)
st.query_params["wl"] = ",".join(whitelist)

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

if "mock" not in st.session_state:
    st.session_state["mock"] = {}
    for c in sk_c:
        m_val = pm.get(f"m_{c}", "0.0_0_1")
        try:
            m_parts = m_val.split("_")
            d_val = int(m_parts[2]) if len(m_parts) > 2 else 1
            st.session_state["mock"][c] = {
                "cost": float(m_parts[0]),
                "qty": float(m_parts[1]),
                "days": d_val
            }
        except:
            st.session_state["mock"][c] = {
                "cost": 0.0, "qty": 0.0, "days": 1
            }

DB = {
    "3231":"緯創","2317":"鴻海","2301":"光寶科","2313":"華通",
    "2421":"建準","2367":"燿華","8454":"富邦媒","2882":"國泰金",
    "2891":"中信金","2886":"兆豐金","1513":"中興電","2356":"英業達",
    "2618":"長榮航","1101":"台泥","2449":"京元電","3036":"文曄",
    "2337":"旺宏","5347":"世界","2412":"中華電","2002":"中鋼",
    "1326":"台化","2881":"富邦金","1519":"華城","2409":"友達",
    "2884":"玉山金","2892":"第一金","2880":"華南金","2885":"元大金",
    "2890":"永豐金","5880":"合庫金","2883":"開發金","2603":"長榮"
}

BOMB = {
    "3231": "法說會 (2天後)", "2317": "營收揭露 (1天後)",
    "2301": "季報開牌 (4天後)", "2313": "法說會 (3天後)",
    "2421": "營收揭露 (2天後)", "2367": "股東會 (5天後)",
    "8454": "營收公佈 (4天後)"
}
DOG_SCORE = {
    "3231": 4, "2317": 5, "2301": 4, "2313": 3, "2421": 4, "2367": 2,
    "8454": 4
}

raw_items = []
if sk_p:
    zns = [z.strip() for z in zn_p.split(",")] if zn_p else []
    tps = [t.strip() for t in tp_p.split(",")] if tp_p else []
    while len(zns) < len(sk_c): zns.append("待精算")
    while len(tps) < len(sk_c): tps.append("W")
    bg = ["🥇","🥈","🥉","🚀"] + ["🔍"]*20
    c_id = 0
    for i, c in enumerate(sk_c):
        n = DB.get(c, f"個股 {c}")
        b = "📦" if tps[i]=="I" else (bg[c_id] if tps[i]=="C" else "🔍")
        if tps[i]=="C": c_id += 1
        raw_items.append({
            "code": c, "name": n, "zone": zns[i],
            "type": tps[i], "badge": b, "suf": ""
        })

proc_list, vol_filtered_cnt = [], 0
act_al, sell_al = [], []
for item in raw_items:
    c, sufs = item["code"], [item["suf"]] if item["suf"] else [".TW", ".TWO"]
    fetched = False
    for suf in sufs:
        try:
            base_url = "https://query1.finance.yahoo.com/v8/finance/chart/"
            url = f"{base_url}{c}{suf}?interval=1d&range=5d"
            hd = {'User-Agent': 'Mozilla/5.0'}
            r = req.get(url, headers=hd, timeout=2).json()
            res = r["chart"]["result"][0]
            m = res["meta"]
            p = m["regularMarketPrice"]
            if p > 0:
                pc = m.get("chartPreviousClose", p)
                pct = ((p - pc) / pc) * 100 if pc else 0.0
                try:
                    q = res["indicators"]["quote"][0]
                    ops = [x for x in q.get("open", []) if x is not None]
                    his = [x for x in q.get("high", []) if x is not None]
                    los = [x for x in q.get("low", []) if x is not None]
                    vls = [x for x in q.get("volume", []) if x is not None]
                    op, hi, lo, vl = ops[-1] if ops else p, his[-1] if his else p, los[-1] if los else p, vls[-1] if vls else 0
                    prev_avg = sum(vls[:-1]) / len(vls[:-1]) if len(vls) > 1 else 0
                    v_mult = vl / prev_avg if prev_avg > 0 else 1.0
                except: op, hi, lo, vl, v_mult = p, p, p, 0, 1.0
                st.session_state["cache"][c] = {"p": p, "pct": pct, "op": op, "hi": hi, "lo": lo, "vl": vl, "v_mult": v_mult, "stale": False}
                fetched = True; break
        except: pass
    if not fetched and c in st.session_state["cache"]:
        st.session_state["cache"][c]["stale"] = True; fetched = True
    if fetched:
        cd = st.session_state["cache"][c]; p, pct = cd["p"], cd["pct"]
        if item["type"] != "I" and cd["v_mult"] < min_vol_mult and c not in whitelist:
            vol_filtered_cnt += 1; continue
        zp = item["zone"].split('-')
        status_text = "🎯 狙擊就位"
        is_break = is_near = is_low_hit = is_sell = False
        if len(zp) == 2:
            lb, hb = float(zp[0].strip()), float(zp[1].strip())
            if p > hb: status_text = f"📈 溢價: {((p-hb)/hb)*100:.1f}%"
            elif p < lb:
                drop_pct = ((lb-p)/lb)*100
                if item["type"] == "I":
                    if drop_pct >= stop_loss_pct: status_text = f"💀 出清: {drop_pct:.1f}%"; is_sell = True
                    else: status_text = f"🚨 警報: 破底 {drop_pct:.1f}%"; is_break = True
                else: status_text = f"💎 折價: {drop_pct:.1f}%"
            elif p == cd["lo"]: status_text = "🔥 最低開火區！"; is_low_hit = True
            else: status_text = "🎯 狙擊就位"
        item.update({**cd, "status": status_text, "is_sell": is_sell, "is_break": is_break})
        proc_list.append(item)
        if "🎯" in status_text or "🔥" in status_text or "💎" in status_text: act_al.append(f"🎯 {item['name']} ({c}) {status_text}")
        if is_sell or is_break: sell_al.append(f"🚨 {item['name']} ({c}) {status_text}")

if (act_al or sell_al) and not mute_al:
    with st.expander("⚡ 大商道・戰情雷達警報", expanded=True):
        for s in sell_al: st.error(s)
        for a in act_al: st.warning(a)

f_INV = [i for i in proc_list if i["type"] == "I"]
f_CORE = [i for i in proc_list if i["type"] == "C"]
f_WATCH = [i for i in proc_list if i["type"] == "W"]
for title, lst in [("📦 現有庫存", f_INV), ("🦅 狙擊觀察", f_CORE), ("📈 監控區", f_WATCH)]:
    if lst: st.markdown(f"### {title}"); [draw(s) for s in lst]

def draw(item):
    p, pct, c = item["price"], item["pct"], item["code"]
    c_cls = "class='sell-card'" if item["is_sell"] else ""
    html = f'<div {c_cls} style="background:#1e1e1e;border-radius:6px;padding:8px 12px;margin-bottom:6px;">'
    html += f'<div><b>{item["name"]} ({c})</b> | 🛡️ {DOG_SCORE.get(c,3)}分</div>'
    html += f'<div style="font-size:20px;">{p:.2f} ({pct:+.2f}%)</div>'
    html += f'<div>🎯 {item["status"]} | 📅 {BOMB.get(c, "無事件")}</div></div>'
    st.markdown(html, unsafe_allow_html=True)
