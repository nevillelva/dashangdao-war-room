import streamlit as st, requests, time
st.set_page_config(page_title="即時播報", layout="wide")
st.markdown("<style>.block-container { padding-top: 1rem; padding-bottom: 1rem; }</style>", unsafe_allow_html=True)
hd = {'User-Agent': 'Mozilla/5.0'}

cl1, cl2 = st.columns([8, 2])
cl1.title("📊 即時播報台")
if cl2.button("🔄 刷新最新報價", use_container_width=True, type="primary"): st.rerun()
st.write("---")

# 👑 內建核心天網資料庫（帶*為上櫃股 .TWO，其餘為上市 .TW，徹底免疫雲端封鎖）
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
CORE, WATCH = [], []

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
        if tps[i] == "C":
            b = bg[c_id] if c_id < len(bg) else "🚀"
            CORE.append({"code":c, "name":n, "zone":zns[i], "badge":b, "suf":""})
            c_id += 1
        else:
            WATCH.append({"code":c, "name":n, "zone":zns[i], "badge":"🔍", "suf":""})

# 📡 側邊欄全台股即時本地搜尋
st.sidebar.markdown("### ➕ 盤中臨時追加")
q_in = st.sidebar.text_input("🔍 輸入關鍵字或代碼 (如: 中華 / 中 / 2412)", value="")
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
        sel = st.sidebar.selectbox(f"🎯 找到 {len(m_items)} 檔符合標的:", ["-- 請選擇 --"]+m_items)
        if sel != "-- 請選擇 --":
            p = sel.split(" | ")
            t_c, t_n, t_s = p[0].strip(), p[1].strip(), p[2].strip()
    else: st.sidebar.warning("⚠️ 庫內無匹配，請直接輸入4碼代碼")
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
                            op, hi, lo, vl = q.get("open", [p])[0], q.get("high", [p])[0], q.get("low", [p])[0], q.get("volume", [0])[0]
                            op, hi, lo = [x if x is not None else p for x in [op, hi, lo]]
                        except: op, hi, lo, vl = p, p, p, 0
                        clr = "#FF4B4B" if pct > 0 else "#00FF66" if pct < 0 else "#FFFFFF"
                        
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
                        <div><div style="font-size: 11px; color: #777777;">總量</div><div style="font-size: 14px; font-weight: bold; color: #ffeb3b;">{vl//1000 if vl else 0}張</div></div>
                        </div>
                        <div style="display: flex; justify-content: space-between; align-items: center; font-size: 13px; background-color: rgba(255, 75, 75, 0.1); padding: 6px 10px; border-radius: 4px; border: 1px dashed rgba(255, 75, 75, 0.3);">
                        <span style="color: #ffaaaa; font-weight: bold;">🎯 參考區間</span><span style="color: #ff4b4b; font-weight: bold; font-size: 16px;">{z}</span>
                        </div>
                        </div>
                        """
                        st.markdown(html, unsafe_allow_html=True)
                        res_list.append(f"{c}={p:.2f}")
                        break
        except: pass

if CORE:
    st.markdown("### 🦅 核心精選主將 (高勝率狙擊區)")
    for s in CORE: draw_card(s)
if WATCH:
    st.markdown("---")
    st.markdown("### 📈 短中期轉折觀察區")
    for s in WATCH: draw_card(s)
if t_c.strip():
    st.markdown("---")
    st.markdown("### ⚡ 盤中臨時自選區")
    draw_card({"code": t_c, "name": t_n, "zone": t_z, "badge": "🔥", "suf": t_s})
if res_list:
    st.write("---")
    st.write("### 📝 數據複製區")
    st.code("今日精選 " + " ".join(res_list), language="text")
