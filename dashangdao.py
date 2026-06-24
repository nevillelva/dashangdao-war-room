import streamlit as st
import json
import os
import time
import re

# ==========================================
# 底層架構 (The Bone) - V7.1 實體硬碟與防爆
# ==========================================
DB_FILE = "54088_database.json"
MAX_CAPACITY = 40
CACHE_TTL = 86400

def load_database():
    if not os.path.exists(DB_FILE):
        return {"inventory": [], "last_scan": 0}
    try:
        with open(DB_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"inventory": [], "last_scan": 0}

def save_database(data):
    if len(data.get("inventory", [])) > MAX_CAPACITY:
        data["inventory"] = data["inventory"][:MAX_CAPACITY]
    temp_file = DB_FILE + ".tmp"
    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
    os.replace(temp_file, DB_FILE)

def violent_extractor(raw_text):
    clean_text = re.sub(r'\s+', '', raw_text)
    match = re.search(r'\d{4}', clean_text)
    return match.group(0) if match else clean_text

# ==========================================
# 戰術判定引擎 (涵蓋 13 項終極心法)
# ==========================================
def run_left_side_shield(stock):
    """左側價值波段單：動態目標價、重壓邏輯、總經護航"""
    
    # [新增維度 B & 國家級護盤] 總經絕望與融資斷頭探測雷達
    if stock.get('margin_call_extreme') or stock.get('national_team_enters'):
        return "buy", "大盤融資斷頭或國家級護盤啟動！極度恐慌中浮現超額價值，無視短線技術面，準備長線重壓！"
        
    # [核心風控 3] 核心事件消失條款
    if stock.get('core_event_vanished'):
        return "danger", "買進的本質事件/理由已消失，霸王逃命條款觸發，強制擊碎護盾全數撤退。"

    # [精華 3] 財報開獎的「預期值落差」強制平倉機制
    if stock.get('earnings_missed_expectation'):
        return "danger", "財報開獎不如預期(預期落差)，無視價位，開盤第一盤市價強制全數平倉！"

    # [精華 1 & 2 & 小股本] 魚頭探測儀 + 本質變化 + 底部爆量 + 財報防護網
    if stock.get('eps_turnaround') and stock.get('fundamental_healthy'):
        if stock.get('capital') < 30 and stock.get('volume') < 1000:
            return "wait", "財報轉虧為盈且為小股本，目前為無人知曉的『魚頭期』，準備無聲吃貨。"
        elif stock.get('bottom_huge_volume'):
            return "buy", "底部爆量築底確認，本質發生變化，大資金進場，啟動價值重壓。"

    # [動態目標價] 預估EPS x 合理本益比
    if stock.get('price') < stock.get('target_price'):
        return "observe", "股價低於精算目標價，左側護盾維持中，自動過濾短線洗盤。"
        
    return "observe", "價值區間內，持續觀察。"

def run_right_side_blade(stock, avg_cost):
    """右側技術動能單：期望值風控、雙時區、型態與連動防護"""
    price = stock.get('price')
    loss_pct = ((price - avg_cost) / avg_cost) * 100 if avg_cost > 0 else 0
    
    # [驗證 A & 期望值引擎] 10% 絕對停損結界
    if avg_cost > 0 and loss_pct <= -10.0:
        return "danger", f"觸發 10% 絕對停損結界 ({loss_pct:.2f}%)！為維持 11:1 期望值，請「一次全數殺出」。"

    # [精華 2] 板塊連動與領頭羊防護網
    if stock.get('sector_leader_crashed'):
        return "danger", "板塊領頭羊暴跌！跟風效應即將崩盤，請立刻撤退。"

    # [右側精華 2] 賣出三要件 + 執行節奏差異化
    if stock.get('break_5ma') and (stock.get('huge_volume') or stock.get('black_k')):
        if loss_pct > 0:
             return "danger", "觸發賣出三要件（爆量/破5MA）。由於已有獲利保護，請啟動「分批慢慢賣出」停利節奏。"
        else:
             return "danger", "觸發賣出三要件，短線轉空。請「一次全數殺出」果斷停損。"

    # [右側精華 2] KD/MACD/RSI 與雙時區週K 複合探測
    if stock.get('kd_over_80') and stock.get('rsi_divergence'):
        return "danger", "KD 進入 80 高檔區且 RSI 出現背離，高檔利多出盡陷阱，請準備獲利入袋。"
        
    if stock.get('break_5ma') and stock.get('weekly_k_bullish'):
        return "observe", "日線雖破 5MA，但「週 K 線」仍維持強勢多頭，降級為先觀察，提防主力洗盤假跌破。"

    # [右側精華 2] 型態型線與多頭基因
    if stock.get('ma10') > stock.get('ma20') > stock.get('ma60') or stock.get('w_bottom_breakout'):
        return "buy", "均線多頭排列或帶量突破 W 底，魚身啟動，技術動能強勁！"
        
    return "observe", "動能延續中，嚴守風控底線。"

# ==========================================
# 前端視覺與 UI 隔離
# ==========================================
def render_hud_signal(signal_type, message):
    if signal_type == "buy":
        st.markdown(f"### ✅【可以買進】\n{message}")
    elif signal_type == "danger":
        st.markdown(f"### ❌【極度危險】\n{message}")
    elif signal_type == "wait":
        st.markdown(f"### ⏳【等待時機】\n{message}")
    elif signal_type == "observe":
        st.markdown(f"### 🛡️【先觀察】\n{message}")

st.set_page_config(page_title="《作戰所 54088》V10.0 全知終端", layout="wide")
st.markdown("## 🦅 《作戰所 54088》V10.0 終極戰術雷達面板")
st.markdown("**13 項戰略心法全熔接：大賺小賠、期望值至上**")
st.markdown("---")

col_mode, col_input = st.columns([1, 2])
with col_mode:
    mode = st.radio("請嚴格選擇作戰維度：", ("左側：長線價值波段單", "右側：短線技術動能單"))
with col_input:
    raw_input = st.text_input("輸入情報代碼 (支援暴力萃取)：")
    avg_cost = st.number_input("持有成本 (計算期望值/停損/停利節奏)：", value=0.0)

if st.button("啟動系統判定"):
    db = load_database()
    stock_code = violent_extractor(raw_input)
    
    # 這裡為對接市場即時資料庫的參數槽
    mock_stock_data = {
        "price": 100.0, "target_price": 150.0, "capital": 15, "volume": 800,
        "margin_call_extreme": False, "national_team_enters": False,
        "eps_turnaround": True, "fundamental_healthy": True, "bottom_huge_volume": True,
        "earnings_missed_expectation": False, "core_event_vanished": False,
        "sector_leader_crashed": False, "break_5ma": False, "huge_volume": False,
        "black_k": False, "kd_over_80": False, "rsi_divergence": False,
        "weekly_k_bullish": True, "w_bottom_breakout": False,
        "ma10": 105, "ma20": 100, "ma60": 90
    }

    st.markdown("---")
    st.markdown(f"**目標鎖定：** `{stock_code}`")
    
    if "左側" in mode:
        sig, msg = run_left_side_shield(mock_stock_data)
        render_hud_signal(sig, msg)
    else:
        sig, msg = run_right_side_blade(mock_stock_data, avg_cost)
        render_hud_signal(sig, msg)
        
    save_database(db)
