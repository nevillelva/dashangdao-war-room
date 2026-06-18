import streamlit as st, requests as req, time
st.set_page_config(page_title="即時播報", layout="wide")

css = '''<style>
.block-container { padding-top: 1rem; }
div[data-testid="stButton"] button[kind="primary"] {
    position: fixed !important; bottom: 30px !important;
    right: 20px !important; z-index: 999999 !important;
    background-color: #FF4B4B !important; color: white !important;
    border: 2px solid #333 !important; border-radius: 50px !important;
    padding: 12px 24px !important; font-size: 16px !important;
    font-weight: bold !important;
    box-shadow: 0px 6px 12px rgba(0,0,0,0.5) !important;
}
</style>'''
st.markdown(css, unsafe_allow_html=True)

st.sidebar.markdown("### 🔔 戰情控制台")
mute_al = st.sidebar.checkbox("🔕 關閉進場警報", value=False)
auto_rf = st.sidebar.checkbox("🔄 定時自動刷新", value=True)
rf_min = st.sidebar.slider("⏱️ 頻率 (分鐘)", 1, 15, 3)
hide_op = st.sidebar.checkbox("🚫 自動隱藏高飛股", value=True)
max_pre = st.sidebar.slider("📈 允許最大溢價上限 (%)", 5, 100, 20)

st.title("📊 大商道戰情指揮所 v20.0")
if st.button("🔄 刷新最新報價", type="primary"):
    st.rerun()

if auto_rf:
    ms = rf_min * 60 * 1000
    js = f"<script>setTimeout(function(){{location.reload();}},{ms});</script>"
    st.components.v1.html(js, height=0)

st.write("---")
al_holder, act_al, res_list = st.empty(), [], []

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

st.sidebar.markdown("---")
q_in = st.sidebar.text_input("🔍 盤中臨時追加", value="")
if q_in.strip():
    m_items = []
    for k, n in DB.items():
        if q_in.strip().lower() in k or q_in.strip().lower() in n.lower():
            m_items.append(f"{k} | {n} | .TW")
    if m_items:
        sel = st.sidebar.selectbox("🎯 符合標的:", ["-- 請選擇 --"] + m_items)
        if sel != "-- 請選擇 --":
            p = sel.split(" | ")
            raw_items.append({
                "code": p[0].strip(), "name": p[1].strip(),
                "zone": "待精算", "type": "W",
                "badge": "🔥", "suf": p[2].strip()
            })

proc_list, hid_cnt = [], 0
for item in raw_items:
    c, sufs = item["code"], [item["suf"]] if item["suf"] else [".TW", ".TWO"]
    for suf in sufs:
        try:
            base_url = "https://query1.finance.yahoo.com/v8/finance/chart/"
            url = f"{base_url}{c}{suf}?interval=1d&range=1d"
            r = req.get(url, headers={'User-Agent':'Mozilla/5.0'}, timeout=2).json()
            res = r["chart"]["result"][0]
            m = res["meta"]
            p = m["regularMarketPrice"]
            if p > 0:
                pc = m.get("chartPreviousClose", p)
                pct = ((p - pc) / pc) * 100 if pc else 0.0
                try:
                    q = res["indicators"]["quote"][0]
                    op = q.get("open", [p])[0] or p
                    hi = q.get("high", [p])[0] or p
                    lo = q.get("low", [p])[0] or p
                    vl = q.get("volume", [0])[0] or 0
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
                            status_text = f"💎 超值折價: {((lb - p) / lb) * 100:.1f}%"
                        else:
                            status_text = "🎯 狙擊就位 (特權批發價)"
                except: pre_pct = 999.0
                
                item.update({
                    "price": p, "pct": pct, "open": op,
                    "high": hi, "low": lo, "vol": vl,
                    "pre_pct": pre_pct, "status": status_text
                })
                proc_list.append(item)
                break
        except: pass

f_INV, f_CORE, f_WATCH = [], [], []
for item in proc_list:
    p, z, n, c = item["price"], item["zone"], item["name"], item["code"]
    if "🎯" in item["status"]:
        act_al.append(f"🎯 **{n} ({c})** 現價 **{p:.2f}** 已踩入特權區 **{z}**！")
    res_list.append(f"{c}={p:.2f}")
    if item["type"] == "I": f_INV.append(item)
    elif hide_op and item["pre_pct"] > max_pre and item["pre_pct"] != 999.0: hid_cnt += 1
    else: (f_CORE if item["type"] == "C" else f_WATCH).append(item)

f_CORE.sort(key=lambda x: x["pre_pct"])
f_WATCH.sort(key=lambda x: x["pre_pct"])

def draw(item):
    clr = "#FF4B4B" if item["pct"] > 0 else "#00FF66" if item["pct"] < 0 else "#FFFFFF"
    is_hit = "🎯" in item["status"] or "💎" in item["status"]
    bd = "2px solid #FF4B4B" if is_hit else "1px solid #333"
    bg = "background:rgba(255,75,75,0.06);" if is_hit else "background:#1e1e1e;"
    
    html = f'<div style="{bg}border-radius:8px;padding:12px;margin-bottom:12px;'
    html += f'border:{bd};">'
    html += '<div style="display:flex;justify-content:space-between;color:#fff;'
    html += 'align-items:center;">'
    html += f'<div><span style="font-size:20px;font-weight:bold;">'
    html += f'{item["badge"]} {item["name"]}</span>'
    html += f'<span style="color:#88;margin-left:6px;">{item["code"]}</span></div>'
    html += f'<div><span style="font-size:24px;font-weight:bold;color:{clr};">'
    html += f'{item["price"]:.2f}</span><span style="font-size:13px;color:{clr};'
    html += f'margin-left:4px;">({item["pct"]:+.2f}%)</span></div></div>'
    html += '<div style="display:grid;grid-template-columns:repeat(4,1fr);'
    html += 'background:#111;padding:6px;border-radius:6px;text-align:center;'
    html += 'margin:8px 0;font-size:13px;color:#fff;">'
    html += f'<div>開盤<br/><b>{item["open"]:.2f}</b></div>'
    html += f'<div>最高<br/><span style="color:#ff4b4b;"><b>{item["high"]:.2f}</b>'
    html += '</span></div>'
    html += f'<div>最低<br/><span style="color:#00ff66;"><b>{item["low"]:.2f}</b>'
    html += '</span></div>'
    v_sz = item["vol"] // 1000 if item["vol"] else 0
    html += f'<div>總量<br/><span style="color:#ffeb3b;"><b>{v_sz}張</b></span></div></div>'
    html += '<div style="display:flex;justify-content:space-between;font-size:13px;'
    html += 'background:rgba(255,75,75,0.1);padding:6px 10px;border-radius:4px;'
    html += 'border:1px dashed rgba(255,75,75,0.3);color:#fff;">'
    html += f'<span>🎯 參考區間: {item["zone"]}</span>'
    html += f'<span style="color:#ff4b4b;font-weight:bold;">{item["status"]}'
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

if hid_cnt > 0:
    st.info(f"💡 大商道空間優化：已自動隱藏 **{hid_cnt}** 檔超限個股。")

if act_al and not mute_al:
    with al_holder:
        html = "<div style='background:rgba(255,75,75,0.2);border:2px solid #FF4B4B;"
        html += "padding:15px;border-radius:8px;margin-bottom:20px;'>"
        html += "<h3 style='color:#FF4B4B;margin-top:0;'>🚨 【大商道・進場特權警報】</h3>"
        for a in act_al: 
            html += f"<p style='color:#fff;font-size:16px;margin-bottom:8px;'>{a}</p>"
        st.markdown(html + "</div>", unsafe_allow_html=True)

if res_list:
    st.write("---")
    st.code("今日精選 " + " ".join(res_list), language="text")
