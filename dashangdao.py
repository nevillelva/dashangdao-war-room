import streamlit as st
import requests
import time

# 銲死最高防禦級低調外殼
st.set_page_config(page_title="即時播報", layout="wide")

st.markdown("""
    <style>
    .block-container { padding-top: 1rem; padding-bottom: 1rem; }
    /* ⚡ 鋼鐵懸浮鈕：無視滑動、死釘右下角、大拇指極速秒刷 */
    .float-refresh-btn {
        position: fixed; bottom: 30px; right: 20px; z-index: 999999;
        background-color: #FF4B4B; color: white; border: 2px solid #333;
        border-radius: 50px; padding: 14px 22px; font-size: 16px;
        font-weight: bold; box-shadow: 0px 6px 12px rgba(0,0,0,0.5); cursor: pointer;
    }
    </style>
    <button class="float-refresh-btn" onclick="window.parent.location.reload();">🔄 刷新最新報價</button>
""", unsafe_allow_html=True)

hd = {'User-Agent': 'Mozilla/5.0'}
st.sidebar.markdown("### 🔔 戰情控制台")
mute_al = st.sidebar.checkbox("🔕 關閉進場警報", value=False)
auto_rf = st.sidebar.checkbox("🔄 定時自動刷新", value=True)
rf_min = st.sidebar.slider("⏱️ 頻率 (分鐘)", 1, 15, 3)
hide_op = st.sidebar.checkbox("🚫 自動隱藏高飛股", value=True)
max_pre = st.sidebar.slider("📈 允許最大溢價上限 (%)", 5, 100, 20)

st.title("📊 大商道戰情指揮所 v17.3")
if auto_rf:
    st.components.v1.html(f"<script>setTimeout(function(){{window.parent.location.reload();}},{rf_min*60*1000});</script>", height=0)
st.write("---")
al_holder, act_al, res_list = st.empty(), [], []

# 👑 全台股 0-300 元科技、金融、傳產、航運打底爆量智慧庫
DB = {
    "3231":"緯創","2317":"鴻海","2301":"光寶科","2603":"長榮","1513":"中興電","2891":"中信金","2356":"英業達","2618":"長榮航",
    "1101":"台泥","2449":"京元電","2313":"華通","3036":"文曄","2421":"建準","2337":"旺宏","2367":"燿華","5347":"世界",
    "2412":"中華電","2002":"中鋼","1326":"台化","2881":"富邦金","2882":"國泰金","1519":"華城","2353":"宏碁","2409":"友達",
    "2886":"兆豐金","2884":"玉山金","2892":"第一金","2880":"華南金","2885":"元大金","2890":"永豐金","5880":"合庫金","2883":"開發金",
    "2887":"台新金","2888":"新光金","3481":"群創","2609":"陽明","2615":"萬海","2610":"華航","1504":"東元","1503":"士電"
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
                        pre_pct = 0.0
                        try:
                            zp = item["zone"].split('-')
                            if len(zp) == 2 and p > float(zp[1].strip()): pre_pct = ((p - float(zp[1].strip())) / float(zp[1].strip())) * 100
                        except: pre_pct = 999.0
                        item.update({"price":p, "pct":pct, "open":op, "high":hi, "low":lo, "vol":vl, "pre_pct":pre_pct})
                        proc_list.append(item)
                        break
        except: pass

f_INV, f_CORE, f_WATCH = [], [], []
for item in proc_list:
    p, z, n, c = item["price"], item["zone"], item["name"], item["code"]
    try:
        zp = z.split('-')
        if len(zp) == 2 and float(zp[0].strip()) <= p <= float(zp[1].strip()):
            act_al.append(f"🎯 **{n} ({c})** 現價 **{p:.2f}** 已踩入特權區 **{z}**！")
    except: pass
    res_list.append(f"{c}={p:.2f}")
    if item["type"] == "I": f_INV.append(item)
    elif hide_op and item["pre_pct"] > max_pre and item["pre_pct"] != 999.0: hid_cnt += 1
    else:
        if item["type"] == "C": f_CORE.append(item)
        else: f_WATCH.append(item)

f_CORE.sort(key=lambda x: x["pre_pct"])
f_WATCH.sort(key=lambda x: x["pre_pct"])

def draw(item):
    clr = "#FF4B4B" if item["pct"] > 0 else "#00FF66" if item["pct"] < 0 else "#FFFFFF"
    st.markdown(f"""
    <div style="background:#1e1e1e;border-radius:8px;padding:12px;margin-bottom:12px;border:1px solid #333;">
    <div style="display:flex;justify-content:space-between;color:#fff;align-items:center;">
    <div><span style="font-size:20px;font-weight:bold;">{item['badge']} {item['name']}</span><span style="color:#888;margin-left:6px;">{item['code']}</span></div>
    <div><span style="font-size:24px;font-weight:bold;color:{clr};">{item['price']:.2f}</span><span style="font-size:13px;color:{clr};margin-left:4px;">({item['pct']:+.2f}%)</span></div>
    </div>
    <div style="display:grid;grid-template-columns:repeat(4,1fr);background:#111;padding:6px;border-radius:6px;text-align:center;margin:8px 0;font-size:13px;color:#fff;">
    <div>開盤<br/><b>{item['open']:.2f}</b></div><div>最高<br/><span style="color:#ff4b4b;"><b>{item['high']:.2f}</b></span></div>
    <div>最低<br/><span style="color:#00ff66;"><b>{item['low']:.2f}</b></span></div><div>總量<br/><span style="color:#ffeb3b;"><b>{item['vol']//1000 if item['vol'] else 0}張</b></span></div>
    </div>
    <div style="display:flex;justify-content:space-between;font-size:13px;background:rgba(255,75,75,0.1);padding:6px 10px;border-radius:4px;border:1px dashed rgba(255,75,75,0.3);">
    <span style="color:#ffaaaa;font-weight:bold;">🎯 參考區間: {item['zone']}</span><span style="color:#ff4b4b;font-weight:bold;">溢價: {item['pre_pct']:.1f}%</span>
    </div>
    </div>
    """, unsafe_allow_html=True)

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
if hid_cnt > 0: st.info(f"💡 大商道空間優化：已自動隱藏 **{hid_cnt}** 檔超限個股。")
if act_al and not mute_al:
    with al_holder:
        html = "<div style='background:rgba(255,75,75,0.2);border:2px solid #FF4B4B;padding:15px;border-radius:8px;margin-bottom:20px;'>"
        html += "<h3 style='color:#FF4B4B;margin-top:0;'>🚨 【大商道・進場特權警報】</h3>"
        for a in act_al: html += f"<p style='color:#fff;font-size:16px;margin-bottom:8px;'>{a}</p>"
        st.markdown(html + "</div>", unsafe_allow_html=True)
if res_list:
    st.write("---
