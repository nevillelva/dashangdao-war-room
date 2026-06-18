Import streamlit as st, requests as req, time

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

"qty": int(m_parts[1]),

"days": d_val

}

except:

st.session_state["mock"][c] = {

"cost": 0.0, "qty": 0, "days": 1

}



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

lo, vl, stale = cd["lo"], cd["vl"], cd["stale"]

v_mult = cd["v_mult"]


if item["type"] != "I" and v_mult < min_vol_mult and c not in whitelist:

vol_filtered_cnt += 1

continue


pre_pct, status_text = 0.0, "待精算"

is_break, is_near, is_low_hit, is_sell = False, False, False, False

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

drop_pct = ((lb - p) / lb) * 100

if item["type"] == "I":

if drop_pct >= stop_loss_pct:

status_text = f"💀 絕殺出清：破底達 {drop_pct:.1f}%"

is_sell = True

else:

status_text = f"🚨 警報：已摜破防線 ({drop_pct:.1f}%)"

is_break = True

else:

status_text = f"💎 超值折價: {drop_pct:.1f}%"

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

"is_near": is_near, "is_low_hit": is_low_hit, "is_sell": is_sell

})

proc_list.append(item)



f_INV, f_CORE, f_WATCH = [], [], []

act_al, res_list, sell_al = [], [], []

c_inv_total, c_hit_total = 0, 0



for item in proc_list:

p, z, n, c = item["price"], item["zone"], item["name"], item["code"]

if "🎯" in item["status"] or "🔥" in item["status"]:

act_al.append(f"🎯 **{n} ({c})** 現價 **{p:.2f}** 已踩入特權區 **{z}**！")

if item["is_sell"]:

sell_al.append(f"🚨 **{n} ({c})** 破底幅度超標，請立即執行出清風控！")


is_t = "🎯" in item["status"] or "💎" in item["status"]

if is_t or item["is_break"] or item["is_low_hit"] or item["is_sell"]:

c_hit_total += 1

res_list.append(f"{c}={p:.2f}")


if item["type"] == "I":

f_INV.append(item)

c_inv_total += 1

else: (f_CORE if item["type"] == "C" else f_WATCH).append(item)



f_CORE.sort(key=lambda x: x["pre_pct"])

f_WATCH.sort(key=lambda x: x["pre_pct"])



m1, m2, m3 = st.columns(3)

m1.metric("📦 持有庫存檔數", f"{c_inv_total} 檔")

m2.metric("🎯 狙擊就位/風控", f"{c_hit_total} 檔")

m3.metric("🚫 已屏障無量股", f"{vol_filtered_cnt} 檔")

st.write("---")

al_holder = st.empty()



def draw(item):

p, pct, c = item["price"], item["pct"], item["code"]

clr = "#FF4B4B" if pct > 0 else "#00FF66" if pct < 0 else "#FFFFFF"

is_hit = "🎯" in item["status"] or "💎" in item["status"] or item["is_low_hit"]


if item["is_sell"]:

c_cls, bd, bg = "class='sell-card'", "", "background:rgba(255,179,0,0.06);"

elif item["is_break"]:

c_cls, bd, bg = "", "2px dashed #FFB300", "background:rgba(255,179,0,0.04);"

elif is_hit:

c_cls, bd, bg = "class='hit-card'", "", "background:rgba(255,75,75,0.06);"

elif item["is_near"]:

c_cls, bd, bg = "", "2px dotted #FFeb3b", "background:rgba(255,235,59,0.04);"

else:

c_cls, bd, bg = "", "1px solid #333", "background:#1e1e1e;"


stale_mark = "⏳" if item["stale"] else ""


html = f'<div {c_cls} style="{bg}border-radius:6px;"'

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


op_p, hi_p, lo_p = item["open"], item["high"], item["low"]

html += f'<div>開盤<br/><b>{op_p:.2f}</b></div>'

html += f'<div>最高<br/><span style="color:#ff4b4b;">'

html += f'<b>{hi_p:.2f}</b></span></div>'

html += f'<div>最低<br/><span style="color:#00ff66;">'

html += f'<b>{lo_p:.2f}</b></span></div>'

v_sz = item["vol"] // 1000 if item["vol"] else 0

html += f'<div>總量<br/><span style="color:#ffeb3b;">'

html += f'<b>{v_sz}張 ({item["v_mult"]:.1f}x)</b></span></div></div>'

html += '<div style="display:flex;justify-content:space-between;font-size:12px;'

html += 'background:rgba(255,75,75,0.08);padding:4px 8px;border-radius:4px;'


st_clr = "#FFB300" if item["is_break"] or item["is_sell"] else "#ff4b4b"

if item["is_near"]: st_clr = "#FFeb3b"

if item["is_low_hit"]: st_clr = "#ff1a1a"

html += f'border:1px dashed rgba(255,75,75,0.2);color:#fff;">'

html += f'<span>🎯 區間: {item["zone"]}</span>'

html += f'<span style="color:{st_clr};font-weight:bold;">{item["status"]}'

html += '</span></div></div>'

st.markdown(html, unsafe_allow_html=True)


# 💼 模擬持倉配置：優化物理切除同步機制

with st.expander("💼 模擬持倉配置"):

co1, col2, col3 = st.columns(3)

saved = st.session_state["mock"].get(

c, {"cost": 0.0, "qty": 0, "days": 1}

)

cost = co1.number_input("成本價", value=saved["cost"], key=f"c_{c}")

qty = col2.number_input(

"張數", min_value=0, value=saved["qty"], key=f"q_{c}"

)

days = col3.number_input(

"持股天數", min_value=1,

value=saved.get("days", 1), key=f"d_{c}"

)


if cost > 0 and qty > 0:

st.session_state["mock"][c] = {

"cost": cost, "qty": qty, "days": days

}

st.query_params[f"m_{c}"] = f"{cost}_{qty}_{days}"


gross = (p - cost) * qty * 1000

b_fee = cost * qty * 1000 * 0.001425

s_fee = p * qty * 1000 * 0.001425

tax = p * qty * 1000 * 0.003

pnl = gross - (b_fee + s_fee + tax)

roi = (pnl / (cost * qty * 1000)) * 100

daily = pnl / days if days > 0 else pnl

p_clr = "#FF4B4B" if pnl > 0 else "#00FF66" if pnl < 0 else "#FFF"

txt = f"💰 淨損益 (已扣稅費): <span style='color:{p_clr};font-weight:bold;'>"

txt += f"{pnl:+,.0f} 元 ({roi:+.2f}%)</span><br/>"

txt += f"⏱️ 淨日均利潤: <span style='color:{p_clr};font-weight:bold;'>"

txt += f"{daily:+,.0f} 元 / 天</span>"

st.markdown(txt, unsafe_allow_html=True)


# ⚡ v28.0 核心升級：實施一鍵主動刪除特權

if st.button("❌ 一鍵清空此模擬持倉", key=f"clr_{c}"):

if f"m_{c}" in st.query_params:

del st.query_params[f"m_{c}"]

st.session_state["mock"][c] = {

"cost": 0.0, "qty": 0, "days": 1

}

st.rerun()

else:

if f"m_{c}" in st.query_params:

del st.query_params[f"m_{c}"]

st.session_state["mock"][c] = {

"cost": 0.0, "qty": 0, "days": 1

}



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



if (act_al or sell_al) and not mute_al:

with al_holder:

html = "<div style='background:rgba(255,75,75,0.2);border:2px solid #FF4B4B;"

html += "padding:12px;border-radius:6px;margin-bottom:12px;'>"

if sell_al:

html += "<h3 style='color:#FFB300;margin-top:0;'>⚠️ 【出清風控警報】</h3>"

for s in sell_al:

p_tag = f"<p style='color:#fff;font-size:15px;'"

p_tag += f"margin-bottom:4px;'>{s}</p>"

html += p_tag

if act_al:

html += "<h3 style='color:#FF4B4B;margin-top:0;'>🚨 【進場特權警報】</h3>"

for a in act_al:

p_tag = f"<p style='color:#fff;font-size:15px;'"

p_tag += f"margin-bottom:4px;'>{a}</p>"

html += p_tag

st.markdown(html + "</div>", unsafe_allow_html=True)



if res_list:

st.write("---")

st.markdown("### 🔗 戰情所完全體備份座標 (含當前持倉與白名單)")

u = "https://dashangdao-war-room-n9soppujuzqzhute5j9uzz.streamlit.app/?"

u += f"stocks={sk_p}&zones={zn_p}&types={tp_p}"

if whitelist: u += f"&wl={','.join(whitelist)}"

for k, v in st.session_state["mock"].items():

if v["cost"] > 0 and v["qty"] > 0:

u += f"&m_{k}={v['cost']}_{v['qty']}_{v.get('days',1)}"

st.code(u, language="text")
