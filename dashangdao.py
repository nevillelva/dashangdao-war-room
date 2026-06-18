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
</style>'''
st.markdown(css, unsafe_allow_html=True)

st.sidebar.markdown("### 🔔 戰情控制台")
mute_al = st.sidebar.checkbox("🔕 關閉進場警報", value=False)
auto_rf = st.sidebar.checkbox("🔄 定時自動刷新", value=True)
rf_min = st.sidebar.slider("⏱️ 頻率 (分鐘)", 1, 15, 3)
hide_op = st.sidebar.checkbox("🚫 自動隱藏高飛股", value=True)
max_pre = st.sidebar.slider("📈 允許最大溢價上限 (%)", 5, 100, 20)

st.sidebar.markdown("---")
st.sidebar.markdown("### 🔍 起漲智慧過濾")
min_vol_mult = st.sidebar.slider(
    "🔥 最低量增倍數 (相比近期均量)", 1.0, 5.0, 1.2, step=0.1
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
        n = DB.get(c, f"個股 {c}")
        b = "📦" if tps[i]=="I" else (bg[c_id] if tps[i]=="C" else "🔍")
        if tps[i]=="C": c_id += 1
        raw_items.append({
            "code": c, "name": n, "zone": zns[i],
            "type": tps[i], "badge": b, "suf": ""
        })

proc_list, hid_cnt, vol_filtered_cnt = [], 0, 0
for item in raw_items:
    c, sufs = item["code"], [item["suf"]] if item["suf"] else [".TW", ".TWO"]
    fetched = False
    for suf in sufs:
        try:
            base_url = "https://query1.finance.yahoo.com/v8/finance/chart/"
            url = f"{base_url}{c}{suf}?interval=1d&range=5d"
            r = req.get(url, headers={'User-Agent':'Mozilla/5.0'}, timeout=2).json()
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
                    
                    op = ops[-1] if ops else p
                    hi = his[-1] if his else p
                    lo = los[-1] if los else p
                    vl = vls[-1] if vls else 0
                    
                    if len(vls) > 1:
                        prev_avg = sum(vls[:-1]) / len(vls[:-1])
                        v_mult = vl / prev_avg if prev_avg > 0 else 1.0
                    else:
                        v_mult = 1.0
                except: op, hi, lo, vl, v_mult = p, p, p, 0, 1.0
                
                st.session_state["cache"][c] = {
                    "p": p, "pct": pct, "op": op, "hi": hi, "lo": lo, 
                    "vl": vl, "v_mult": v_mult, "stale": False
                }
                fetched = True
                break
        except: pass
        
    if not fetched and c in st.session_state["cache"]:
        st.session_state["cache"][c]["stale"] = True
        fetched = True
        
    if fetched:
        cd = st.session_state["cache"][c]
        p, pct, op, hi = cd["p"], cd["pct"], cd["op"], cd["hi"]
        lo, vl, v_mult, stale = cd["lo"], cd["vl"], cd["v_mult"], cd["stale"]
        
        if item["type"] != "I" and v_mult < min_vol_mult:
            vol_filtered_cnt += 1
            continue
            
        pre_pct, status_text = 0.0, "待精算"
        is_break, is_near, is_low_hit = False, False, False
        try:
            zp = item["zone"].split('-')
            if len(zp) == 2:
                lb, hb = float(zp[0].strip()), float(zp[1].strip())
                if p > hb:
                    pre_pct = ((p - hb) / hb) * 100
                    if pre_pct <= 2.0:
                        status_text = f"⏳ 逼近中 (僅差 {pre_pct:.1f}%)"
                        is_near = True
                    else:
                        status_text = f"📈 溢價: {pre_pct:.1f}%"
                elif p < lb:
                    status_text = f"💎 超值折價: {((lb - p) / lb) * 100:.1f}%"
                    if item["type"] == "I":
                        status_text = "🚨 警報：已摜破戰略防線！"
                        is_break = True
                else:
                    if p == lo:
                        status_text = "🔥 最低點開火伏擊區！"
                        is_low_hit = True
                    else:
                        status_text = "🎯 狙擊就位 (特權批發價)"
        except: pre_pct = 999.0
        
        item.update({
            "price": p, "pct": pct, "open": op, "high": hi, "low": lo,
            "vol": vl, "v_mult": v_mult, "pre_pct": pre_pct, 
            "status": status_text, "stale": stale, "is_break": is_break, 
            "is_near": is_near, "is_low_hit": is_low_hit
        })
        proc_list.append(item)

f_INV, f_CORE, f_WATCH = [], [], []
act_al, res_list = [], []
c_inv_total, c_hit_total = 0, 0

for item in proc_list:
    p, z, n, c = item["price"], item["zone"], item["name"], item["code"]
    is_ok = "🎯" in item["status"] or "🔥" in item["status"]
    if is_ok:
        act_al.append(f"🎯 **{n} ({c})** 現價 **{p:.2f}** 已踩入特權區 **{z}**！")
        
    is_trig = "🎯" in item["status"] or "💎" in item["status"]
    if is_trig or item["is_break"] or item["is_low_hit"]:
        c_hit_total += 1
    res_list.append(f"{c}={p:.2f}")
    
    if item["type"] == "I":
        f_INV.append(item)
        c_inv_total += 1
    elif hide_op and item["pre_pct"] > max_pre and item["pre_pct"] != 999.0:
        hid_cnt += 1
    else: (f_CORE if item["type"] == "C" else f_WATCH).append(item)

f_CORE.sort(key=lambda x: x["pre_pct"])
f_WATCH.sort(key=lambda x: x["pre_pct"])

m1, m2, m3 = st.columns(3)
m1.metric("📦 持有庫存檔數", f"{c_inv_total} 檔")
m2.metric("🎯 狙擊就位/折價", f"{c_hit_total} 檔")
m3.metric("🚫 已屏障高飛股", f"{hid_cnt + vol_filtered_cnt} 檔")
st.write("---")
al_holder = st.empty()

def draw(item):
    p, pct = item["price"], item["pct"]
    clr = "#FF4B4B" if pct > 0 else "#00FF66" if pct < 0 else "#FFFFFF"
    is_hit = "🎯" in item["status"] or "💎" in item["status"] or item["is_low_hit"]
    
    if item["is_break"]:
        c_cls, bd, bg = "", "2px dashed #FFB300", "background:rgba(255,179,0,0.06);"
    elif is_hit:
        c_cls, bd, bg = "class='hit-card'", "", "background:rgba(255,75,75,0.06);"
    elif item["is_near"]:
        c_cls, bd, bg = "", "2px dotted #FFeb3b", "background:rgba(255,235,59,0.04);"
    else:
        c_cls, bd, bg = "", "1px solid #333", "background:#1e1e1e;"
        
    stale_mark = "⏳" if item["stale"] else ""
    
    html = f'<div {c_cls} style="{bg}border-radius:6px;'
    html += 'padding:8px 12px;margin-bottom:6px;'
    if bd: html += f'border:{bd};'
    html += '">'
    html += '<div style="display:flex;justify-content:space-between;color:#fff;'
    html += 'align-items:center;">'
    html += f'<div><span style="font-size:18px;font-weight:bold;">'
    html += f'{item["badge"]} {item["name"]} {stale_mark}</span>'
    html += f'<span style="color:#88;margin-left:4px;font-size:12px;">'
    html += f'{item["code"]}</span>'
    html += '</div><div>'
    html += f'<span style="font-size:22px;font-weight:bold;color:{clr};">'
    html += f'{p:.2f}</span><span style="font-size:12px;color:{clr};'
    html += f'margin-left:4px;">({pct:+.2f}%)</span></div></div>'
    html += '<div style="display:grid;grid-template-columns:repeat(4,1fr);'
    html += 'background:#111;padding:4px;border-radius:4px;text-align:center;'
    html += 'margin:4px 0;font-size:12px;color:#fff;">'
    html += f'<div>開盤<br/><b>{item["open"]:.2f}</b></div>'
    html += f'<div>最高<br/><span style="color:#ff4b4b;"><b>{item["high"]:.2f}</b>'
    html += '</span></div>'
    html += f'<div>最低<br/><span style="color:#00ff66;"><b>{item["low"]:.2f}</b>'
    html += '</span></div>'
    v_sz = item["vol"] // 1000 if item["vol"] else 0
    html += f'<div>總量<br/><span style="color:#ffeb3b;">'
    html += f'<b>{v_sz}張 ({item["v_mult"]:.1f}x)</b></span></div></div>'
    html += '<div style="display:flex;justify-content:space-between;font-size:12px;'
    html += 'background:rgba(255,75,75,0.08);padding:4px 8px;border-radius:4px;'
    
    st_clr = "#FFB300" if item["is_break"] else "#ff4b4b"
    if item["is_near"]: st_clr = "#FFeb3b"
    if item["is_low_hit"]: st_clr = "#ff1a1a"
    html += f'border:1px dashed rgba(255,75,75,0.2);color:#fff;">'
    html += f'<span>🎯 區間: {item["zone"]}</span>'
    html += f'<span style="color:{st_clr};font-weight:bold;">{item["status"]}'
    html += '</span></div></div>'
    st.markdown(html, unsafe_allow_html=True)

if f_INV:
    st.markdown("### 📦 我們的現有庫存")
    for s in f_INV: draw(s)
if f_CORE:
    st.markdown("### 🦅 主要戰略觀察 (高勝率狙擊區)")
    for s in f_CORE: draw(s)
if f_WATCH:
    st.markdown("---")
    st.markdown("### 📈 次要量能監控區")
    for s in f_WATCH: draw(s)

if hid_cnt > 0 or vol_filtered_cnt > 0:
    st.info(f"💡 空間優化：已屏障 {hid_cnt} 檔高飛股 / {vol_filtered_cnt} 檔無量殭屍股。")

if act_al and not mute_al:
    with al_holder:
        html = "<div style='background:rgba(255,75,75,0.2);border:2px solid #FF4B4B;"
        html += "padding:12px;border-radius:6px;margin-bottom:12px;'>"
        html += "<h3 style='color:#FF4B4B;margin-top:0;'>🚨 【進場特權警報】</h3>"
        for a in act_al: 
            p_tag = f"<p style='color:#fff;font-size:15px;'"
            p_tag += f"margin-bottom:8px;'>{a}</p>"
            html += p_tag
        st.markdown(html + "</div>", unsafe_allow_html=True)

if res_list:
    st.write("---")
    st.code("今日精選 " + " ".join(res_list), language="text")
