import streamlit as st
import requests
import time

# 銲死最高防禦級低調外殼
st.set_page_config(page_title="即時播報", layout="wide")

st.markdown("""
    <style>
    .block-container { padding-top: 1rem; padding-bottom: 1rem; }
    </style>
""", unsafe_allow_html=True)

hd = {'User-Agent': 'Mozilla/5.0'}

# ⚡ 頂部強制刷新戰術鈕
cl1, cl2 = st.columns([8, 2])
with cl1:
    st.title("📊 大商道戰情指揮所")
with cl2:
    st.write("")
    if st.button("🔄 刷新最新報價", use_container_width=True, type="primary"):
        st.rerun()

st.write("---")

# 👑 內建 0-300 元全台股精選轉折打底庫（徹底離線化，防封鎖）
DB = {
    "3231": "緯創", "2317": "鴻海", "2301": "光寶科", "2603": "長榮",
    "1513": "中興電", "2891": "中信金", "2356": "英業達", "2618": "長榮航",
    "1101": "台泥", "2449": "京元電", "2313": "華通", "3036": "文曄",
    "2421": "建準", "2337": "旺宏", "2367": "燿華", "5347*": "世界",
    "2412": "中華電", "2002": "中鋼", "1326": "台化", "2881": "富邦金",
    "2882": "國泰金", "1519": "華城", "2353": "宏碁", "2409": "友達"
}

pm = st.query_params
sk_p = pm.get("stocks", "")
zn_p = pm.get("zones", "")
tp_p = pm.get("types", "")

INV = []
CORE = []
WATCH = []

if sk_p:
    sk_c = [c.strip() for c in sk_p.split(",") if c.strip()]
    zns = [z.strip() for z in zn_p.split(",")] if zn_p else []
    tps = [t.strip() for t in tp_p.split(",")] if tp_p else []
    
    while len(zns) < len(sk_c):
        zns.append("待精算")
    while len(tps) < len(sk_c):
        tps.append("W")
        
    bg = ["🥇", "🥈", "🥉", "🚀"] + ["🔍"] * 20
    c_id = 0
    
    for i, c in enumerate(sk_c):
        n = DB.get(c) or DB.get(c + "*") or "自選股"
        item = {"code": c, "name": n, "zone": zns[i], "badge": "🔍", "suf": ""}
        
        if tps[i] == "I":
            item["badge"] = "📦"
            INV.append(item)
        elif tps[i] == "C":
            item["badge"] = bg[c_id] if c_id < len(bg) else "🚀"
            CORE.append(item)
            c_id += 1
        else:
            WATCH.append(item)

# 📡 側邊欄本地智慧天網
st.sidebar.markdown("### ➕ 盤中臨時追加")
q_in = st.sidebar.text_input("🔍 輸入關鍵字或代碼 (如: 中華 / 2412)", value="")
t_c, t_n, t_s = "", "自選黑馬", ""

if q_in.strip():
    m_items = []
    q_low = q_in.strip().lower()
    for k, n in DB.items():
        c = k.replace("*", "")
        if q_low in c or q_low in n.lower():
            suf = ".TWO" if "*" in k else ".TW"
            m_items.append(f"{c} | {n} | {suf}")
            
    if not m_items and q_in.strip().isdigit() and len(q_in.strip()) >= 4:
        m_items.append(f"{q_in.strip()} | 自選股 | .TW")
        m_items.append(f"{q_in.strip()} | 自選股 | .TWO")
        
    if m_items:
        sel = st.sidebar.selectbox(f"🎯 符合標的:", ["-- 請選擇 --"] + m_items)
        if sel != "-- 請選擇 --":
            p = sel.split(" | ")
            t_c = p[0].strip()
            t_n = p[1].strip()
            t_s = p[2].strip()
    else:
        st.sidebar.warning("⚠️ 庫內無匹配")
else:
    t_c = st.sidebar.text_input("臨時股票代碼(選填)", value="")
    t_n = st.sidebar.text_input("臨時股票名稱(選填)", value="自選黑馬")

t_z = st.sidebar.text_input("臨時參考區間", value="待精算")
res_list = []

# ⚡ 重新編排：徹底打碎長代碼，防範任何換行切斷
def draw_card(item):
    c = item["code"]
    n = item["name"]
    z = item["zone"]
    b = item["badge"]
    sufs = [item["suf"]] if "suf" in item and item["suf"] else [".TW", ".TWO"]
    
    for suf in sufs:
        ts = int(time.time())
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{c}{suf}?interval=1d&range=1d&_={ts}"
        try:
            r = requests.get(url, headers=hd, timeout=2)
            if r.status_code == 200:
                res = r.json().get("chart", {}).get("result", [None])[0]
                if res:
                    m = res.get("meta", {})
                    p = m.get("regularMarketPrice", 0.0)
                    if p and p > 0:
                        pc = m.get("chartPreviousClose", p)
                        pct = ((p - pc) / pc) * 100 if pc > 0 else 0.0
                        
                        try:
                            q = res.get("indicators", {}).get("quote", [{}])[0]
                            op = q.get("open", [p])[0]
                            hi = q.get("high", [p])[0]
                            lo = q.get("low", [p])[0]
                            vl = q.get("volume", [0])[0]
                            op = op if op is not None else p
                            hi = hi if hi is not None else p
                            lo = lo if lo is not None else p
                        except:
                            op, hi, lo, vl = p, p, p, 0
                            
                        clr = "#FF4B4B" if pct > 0 else "#00FF66" if pct < 0 else "#FFFFFF"
                        v_k = vl // 1000 if vl else 0
                        
                        html = f"""
                        <div style="background-color: #1e1e1e; border-radius: 8px; padding: 12px; margin-bottom: 12px; border: 1px solid #333;">
                        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
                        <div><span style="font-size: 20px; font-weight: bold; color: #ffffff;">{b} {n}</span><span style="font-size: 13px; color: #888888; margin-left: 6px;">{c}</span></div>
                        <div style="text-align: right;"><span style="font-size: 24px; font-weight: bold; color: {clr};">{p:.2f}</span><span style="font-size: 13px; font-weight: bold; color: {clr}; margin-left: 4px;">({pct:+.2f}%)</span></div>
                        </div>
                        <div style="display: grid; grid-template-columns: repeat(4, 1fr); gap: 4px; background-color: #111111; padding: 8px 4px; border-radius: 6px; text-align: center; margin-bottom: 8px;">
                        <div><div style="font-size: 11px; color: #777777;">開盤</div><div style="font-size: 14px; font-weight: bold; color: #ffffff;">{op:.2f}</div></div>
                        <div><div style="font-size: 11px; color: #777777;">最高</div><div style="font-size: 14px; font-weight: bold; color: #ff4b4b;">{hi:.2f}</div></div>
                        <div><div style="font-size: 11px; color: #777777;">最低</div><div style="font-size: 14px; font-weight: bold; color: #00ff66;">{lo:.2f}</div></div>
                        <div><div style="font-size: 11px; color: #777777;">總量</div><div style="font-size: 14px; font-weight: bold; color: #ffeb3b;">{v_k}張</div></div>
                        </div>
                        <div style="display: flex; justify-content: space-between; align-items: center; font-size: 13px; background-color: rgba(255, 75, 75, 0.1); padding: 6px 10px; border-radius: 4px; border: 1px dashed rgba(255, 75, 75, 0.3);">
                        <span style="color: #ffaaaa; font-weight: bold;">🎯 參考區間</span><span style="color: #ff4b4b; font-weight: bold; font-size: 16px;">{z}</span>
                        </div>
                        </div>
                        """
                        st.markdown(html, unsafe_allow_html=True)
                        res_list.append(f"{c}={p:.2f}")
                        break
        except:
            pass

# 👑 三級分流渲染（強行獨立於最外層，絕不縮排）
if INV:
    st.markdown("### 📦 我們的現有庫存")
    for s in INV:
        draw_card(s)

if CORE:
    st.markdown("### 🦅 主要戰略觀察 (高勝率狙擊區)")
    for s in CORE:
        draw_card(s)

if WATCH:
    st.markdown("---")
    st.markdown("### 📈 次要量能監控區")
    for s in WATCH:
        draw_card(s)

if t_c.strip():
    st.markdown("---")
    st.markdown("### ⚡ 盤中臨時自選區")
    draw_card({"code": t_c, "name": t_n, "zone": t_z, "badge": "🔥", "suf": t_s})

if res_list:
    st.write("---")
    st.code("今日精選 " + " ".join(res_list), language="text")
