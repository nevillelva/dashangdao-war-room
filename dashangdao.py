import streamlit as st, requests, time
st.set_page_config(page_title="即時播報", layout="wide")
st.markdown("<style>.block-container { padding-top: 1rem; padding-bottom: 1rem; }</style>", unsafe_allow_html=True)
hd = {'User-Agent': 'Mozilla/5.0'}

cl1, cl2 = st.columns([8, 2])
cl1.title("📊 大商道戰情指揮所")
if cl2.button("🔄 刷新最新報價", use_container_width=True, type="primary"): st.rerun()
st.write("---")

DB = {
    "2330":"台積電","2317":"鴻海","2454":"聯發科","2308":"台達電","2382":"廣達",
    "3231":"緯創","2313":"華通","3036":"文曄","2301":"光寶科","2449":"京元電",
    "2421":"建準","2337":"旺宏","3017":"奇鋐","3324":"雙鴻","2367":"燿華",
    "5347*":"世界","2412":"中華電","2002":"中鋼","1326":"台化","1101":"台泥",
    "2881":"富邦金","2882":"國泰金","2891":"中信金","2886":"兆豐金","2884":"玉山金",
    "2892":"第一金","2880":"華南金","2885":"元大金","2890":"永豐金","5880":"合庫金",
    "2603":"長榮","2609":"陽明","2615":"萬海","2618":"長榮航","2610":"華航",
    "1513":"中興電","1519":"華城","1504":"東元","1503":"士電","1605":"華新",
    "2357":"華碩","2324":"仁寶","2353":"宏碁","2377":"微星","2352":"佳世達",
    "2395":"研華","2345":"智邦","2379":"瑞昱","3008":"大立光","3045":"台灣大",
    "4904":"遠傳","2912":"統一超","1216":"統一","2105":"正新","9904":"寶成",
    "3037":"欣興","3189":"景碩","8046":"南電","2347":"聯強","2344":"華邦電",
    "2409":"友達","3481":"群創","3711":"日月光","6488*":"環球晶","5483*":"中美晶",
    "8069*":"元太","3105*":"穩懋","6274*":"台燿","6213*":"聯茂","3035":"智原",
    "3661":"世芯-KY","3443":"創意","2368":"金像電","2383":"台光電","3044":"健鼎",
    "3596":"智易","5388":"中磊","6285":"啟碁","2356":"英業達","2408":"南亞科",
    "2883":"開發金","2887":"台新金","8299*":"群聯","3260*":"威剛","8081*":"致新",
    "6182*":"合晶","5425*":"台半","2455":"全新","2481":"強茂","2605":"新興",
    "2606":"裕民","2201":"裕隆","2359":"所羅門","1802":"台玻","2614":"東森"
}

pm = st.query_params
sk_p, zn_p, tp_p = pm.get("stocks",""), pm.get("zones",""), pm.get("types","")
INV, CORE, WATCH = [], [], []

if sk_p:
    sk_c = [c.strip() for c in sk_p.split(",") if c.strip()]
    zns = [z.strip() for z in zn_p.split(",")] if zn_p else []
    tps = [t.strip() for t in tp_p.split(",")] if tp_p else []
    while len(zns) < len(sk_c): zns.append("待精算")
    while len(tps) < len(sk_c): tps.append("W")
    bg = ["🥇","🥈","🥉","🚀"] + ["🔍"]*20
    c_id = 0
    for i, c in enumerate(sk_c):
        n = DB.get(c) or DB.get(c+"*") or "自選股"
        item = {"code":c, "name":n, "zone":zns[i], "badge":"🔍", "suf":""}
        if tps[i] == "I":
            item["badge"] = "📦"
            INV.append(item)
        elif tps[i] == "C":
            item["badge"] = bg[c_id] if c_id < len(bg) else "🚀"
            CORE.append(item)
            c_id += 1
        else:
            WATCH.append(item)

st.sidebar.markdown("### ➕ 盤中臨時追加")
q_in = st.sidebar.text_input("🔍 輸入關鍵字或代碼 (如: 中華 / 2412)", value="")
t_c, t_n, t_s = "", "自選黑馬", ""

if q_in.strip():
    m_items = []
    q_low = q_in.strip().lower()
    for k, n in DB.items():
        c = k.replace("*", "")
        if q_low in c or q_low in n.lower():
            m_items.append(f"{c} | {n} | {'.TWO' if '*' in k else '.TW'}")
    if not m_items and q_in.strip().isdigit() and len(q_in.strip()) >= 4:
        m_items.append(f"{q_in.strip()} | 自選股 | .TW")
        m_items.append(f"{q_in.strip()} | 自選股 | .TWO")
    if m_items:
        sel = st.sidebar.selectbox(f"🎯 符合標的:", ["-- 請選擇 --"]+m_items)
        if sel != "-- 請選擇 --":
            p = sel.split(" | ")
            t_c, t_n, t_s = p[0].strip(), p[1].strip(), p[2].strip()
    else: st.sidebar.warning("⚠️ 庫內無匹配")
else:
    t_c = st.sidebar.text_input("臨時股票代碼(選填)", value="")
    t_n = st.sidebar.text_input("臨時股票名稱(選填)", value="自選黑馬")

t_z = st.sidebar.text_input("臨時參考區間", value="待精算")
res_list = []

def draw_card(item):
    c, n, z, b = item["code"], item["name"], item["zone"], item["badge"]
    sufs = [item["suf"]] if "suf" in item and item["suf"] else [".TW", ".TWO"]
    for suf in sufs:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{c}{suf}?interval=1d&range=1d&_={int(time.time())}"
        try:
            r = requests
