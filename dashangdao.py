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

# 📡 側邊欄控制台：定時刷新與【實戰視窗極致優化引擎】
st.sidebar.markdown("### 🔔 戰情警報與自動刷新")
mute_alerts = st.sidebar.checkbox("🔕 暫時靜音/手動關閉進場警報", value=False)
auto_refresh = st.sidebar.checkbox("🔄 啟動盤中定時自動刷新", value=True)
refresh_min = st.sidebar.slider("⏱️ 設定刷新頻率 (分鐘)", min_value=1, max_value=15, value=3)

st.sidebar.markdown("---")
st.sidebar.markdown("### 🎯 視窗射程空間優化")
hide_overpriced = st.sidebar.checkbox("🚫 自動隱藏嚴重高飛股 (釋放空間)", value=True)
max_premium_pct = st.sidebar.slider("📈 允許最大溢價上限 (%)", min_value=5, max_value=100, value=20)

# 👑 擴充版全台股 0-300 元科技、金融、傳產、航運打底爆量智慧庫
DB = {
    "3231": "緯創", "2317": "鴻海", "2301": "光寶科", "2603": "長榮",
    "1513": "中興電", "2891": "中信金", "2356": "英業達", "2618": "長榮航",
    "1101": "台泥", "2449": "京元電", "2313": "華通", "3036": "文曄",
    "2421": "建準", "2337": "旺宏", "2367": "燿華", "5347": "世界",
    "2412": "中華電", "2002": "中鋼", "1326": "台化", "2881": "富邦金",
    "2882": "國泰金", "1519": "華城", "2353": "宏碁", "2409": "友達",
    "2886": "兆豐金", "2884": "玉山金", "2892": "第一金", "2880": "華南金",
    "2885": "元大金", "2890": "永豐金", "5880": "合庫金", "2883": "開發金",
    "2887": "台新金", "2888": "新光金", "3481": "群創", "2609": "陽明",
    "2615": "萬海", "2610": "華航", "1504": "東元", "1503": "士電",
    "1605": "華新", "2324": "仁寶", "2377": "微星", "2352": "佳世達",
    "3037": "欣興", "2344": "華邦電", "3711": "日月光", "3035": "智原",
    "2368": "金像電", "3044": "健鼎", "5388": "中磊", "6285": "啟碁",
    "2201": "裕隆", "2359": "所羅門", "1802": "台玻", "2614": "東森"
}

# ⚡ 頂部標題與手動刷新
cl1, cl2 = st.columns([8, 2])
with cl1:
    st.title("📊 大商道戰情指揮所 v17.0")
with col2 if 'col2' in locals() else cl2:
    st.write("")
    if st.button("🔄 刷新最新報價", use_container_width=True, type="primary"):
        st.rerun()

if auto_refresh:
    st.components.v1.html(f"""
        <script>
        setTimeout(function() {{ window.parent.location.reload(); }}, {refresh_min * 60 * 1000});
        </script>
    """, height=0)

st.write("---")
alert_holder = st.empty()
active_alerts = []

pm = st.query_params
sk_p = pm.get("stocks", "")
zn_p = pm.get("zones", "")
tp_p = pm.get("types", "")

raw_items = []
if sk_p:
    sk_c = [c.strip() for c in sk_p.split(",") if c.strip()]
    zns = [z.strip() for z in zn_p.split(",")] if zn_p else []
    tps = [t.strip() for t in tp_p.split(",")] if tp_p else []
    while len(zns) < len(sk_c): zns.append("待精算")
    while len(tps) < len(sk_c): tps.append("W")
    
    bg = ["🥇", "🥈", "🥉", "🚀"] + ["🔍"] * 20
    c_id = 0
    for i, c in enumerate(sk_c):
        n = DB.get(c) or "個股 " + c
        badge = "🔍"
        if tps[i] == "I": badge = "📦"
        elif tps[i] == "C":
            badge = bg[c_id] if c_id < len(bg) else "🚀"
            c_id += 1
        raw_items.append({"code": c, "name": n, "zone": zns[i], "type": tps[i], "badge": badge, "suf": ""})

# 📡 側邊欄追加功能
st.sidebar.markdown("---")
st.sidebar.markdown("### ➕ 盤中臨時追加")
q_in = st.sidebar.text_input("🔍 輸入關鍵字或代碼 (如: 兆豐 / 2886)", value="")
if q_in.strip():
    m_items = []
    for k, n in DB.items():
        if q_in.strip().lower() in k or q_in.strip().lower() in n.lower():
            m_items.append(f"{k} | {n} | .TW")
    if m_items:
        sel = st.sidebar.selectbox(f"🎯 符合標的:", ["-- 請選擇 --"] + m_items)
        if sel != "-- 請選擇 --":
            p = sel.split(" | ")
            raw_items.append({"code": p[0].strip(), "name": p[1].strip(), "zone": st.sidebar.text_input("臨時參考區間", value="待精算"), "type": "W", "badge": "🔥", "suf": p[2].strip()})

# ⚡ 數據清洗與 API 抓取核心（分離渲染，改為先計算、後過濾排序機制）
processed_list = []
hidden_count = 0

for item in raw_items:
    c = item["code"]
    sufs = [item["suf"]] if item["suf"] else [".TW", ".TWO"]
    fetched = False
    
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
                        except:
                            op, hi, lo, vl = p, p, p, 0
                        
                        # 精算溢價幅度
                        premium_pct = 0.0
                        try:
                            z_parts = item["zone"].split('-')
                            if len(z_parts) == 2:
                                h_bound = float(z_parts[1].strip())
                                if p > h_bound: premium_pct = ((p - h_bound) / h_bound) * 100
                        except:
                            premium_pct = 999.0
                        
                        item.update({"price": p, "pct": pct, "open": op, "high": hi, "low": lo, "vol": vl, "premium_pct": premium_pct})
                        processed_list.append(item)
                        fetched = True
                        break
        except:
            pass

# ⚔️ 執行大商道核心過濾與排序鐵律
final_INV = []
final_CORE = []
final_WATCH = []
res_list = []

for item in processed_list:
    p = item["price"]
    z = item["zone"]
    n = item["name"]
    c = item["code"]
    
    # 進場特權警報觸發判定
    try:
        z_p = z.split('-')
        if len(z_p) == 2 and float(z_p[0].strip()) <= p <= float(z_p[1].strip()):
            active_alerts.append(f"🎯 **{n} ({c})** 現價 **{p:.2f}** 已踩入特權批發區 **{z}**！")
    except:
        pass

    res_list.append(f"{c}={p:.2f}")
    
    # 庫存永久保留，觀察股若超過溢價上限則無情隱藏
    if item["type"] == "I":
        final_INV.append(item)
    else:
        if hide_overpriced and item["premium_pct"] > max_premium_pct and item["premium_pct"] != 999.0:
            hidden_count += 1
        else:
            if item["type"] == "C": final_CORE.append(item)
            else: final_WATCH.append(item)

# 🔝 依據與捕獸夾的接近距離進行升序排序（越接近防線、越接近0%的排越前面！）
final_CORE.sort(key=lambda x: x["premium_pct"])
final_WATCH.sort(key=lambda x: x["premium_pct"])

# 🎨 渲染引擎輸出
def render_card(item):
    clr = "#FF4B4B" if item["pct"] > 0 else "#00FF66" if item["pct"] < 0 else "#FFFFFF"
    v_k = item["vol"] // 1000 if item["vol"] else 0
    html = f"""
    <div style="background-color: #1e1e1e; border-radius: 8px; padding: 12px; margin-bottom: 12px; border: 1px solid #333;">
    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
    <div><span style="font-size: 20px; font-weight: bold; color: #ffffff;">{item["badge"]} {item["name"]}</span><span style="font-size: 13px; color: #888888; margin-left: 6px;">{item["code"]}</span></div>
    <div style="text-align: right;"><span style="font-size: 24px; font-weight: bold; color: {clr};">{item["price"]:.2f}</span><span style="font-size: 13px; font-weight: bold; color: {clr}; margin-left: 4px;">({item["pct"]:+.2f}%)</span></div>
    </div>
    <div style="display: grid; grid-template-columns: repeat(4, 1fr); gap: 4px; background-color: #111111; padding: 8px 4px; border-radius: 6px; text-align: center; margin-bottom: 8px;">
    <div><div style="font-size: 11px; color: #777777;">開盤</div><div style="font-size: 14px; font-weight: bold; color: #ffffff;">{item["open"]:.2f}</div></div>
    <div><div style="font-size: 11px; color: #777777;">最高</div><div style="font-size: 14px; font-weight: bold; color: #ff4b4b;">{item["high"]:.2f}</div></div>
    <div><div style="font-size: 11px; color: #777777;">最低</div><div style="font-size: 14px; font-weight: bold; color: #00ff66;">{item["low"]:.2f}</div></div>
    <div><div style="font-size: 11px; color: #777777;">總量</div><div style="font-size: 14px; font-weight: bold; color: #ffeb3b;">{v_k}張</div></div>
    </div>
    <div style="display: flex; justify-content: space-between; align-items: center; font-size: 13px; background-color: rgba(255, 75, 75, 0.1); padding: 6px 10px; border-radius: 4px; border: 1px dashed rgba(255, 75, 75, 0.3);">
    <span style="color: #ffaaaa; font-weight: bold;">🎯 參考區間: {item["zone"]}</span>
    <span style="color: #ff4b4b; font-weight: bold; font-size: 12px;">當前溢價: {item["premium_pct"]:.1f}%</span>
    </div>
    </div>
    """
    st.markdown(html, unsafe_allow_html=True)

if final_INV:
    st.markdown("### 📦 我們的現有庫存")
    for s in final_INV: render_card(s)

if final_CORE:
    st.markdown("### 🦅 主要戰略觀察 (高勝率狙擊區)")
    for s in final_CORE: render_card(s)

if final_WATCH:
    st.markdown("---")
    st.markdown("### 📈 次要量能監控區")
    for s in final_WATCH: render_card(s)

if hidden_count > 0:
    st.info(f"💡 大商道空間優化：已自動隱藏 **{hidden_count}** 檔溢價超過 {max_premium_pct}% 的高飛無效標的，釋放作戰視窗。")

if active_alerts and not mute_alerts:
    with alert_holder:
        alert_html = "<div style='background-color: rgba(255,75,75,0.2); border: 2px solid #FF4B4B; padding: 15px; border-radius: 8px; margin-bottom: 20px;'>"
        alert_html += "<h3 style='color: #FF4B4B; margin-top:0;'>🚨 【大商道・進場特權警報】</h3>"
        for alert in active_alerts: alert_html += f"<p style='color: #ffffff; font-size: 16px; margin-bottom:8px;'>{alert}</p>"
        alert_html += "</div>"
        st.markdown(alert_html, unsafe_allow_html=True)

if res_list:
    st.write("---")
    st.code("今日精選 " + " ".join(res_list), language="text")
