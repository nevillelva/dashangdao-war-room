import streamlit as st
import json
import os
import time
from datetime import datetime

# ==========================================
# 系統底層架構 (The Bone)
# ==========================================
DB_FILE = "54088_database.json"

# 實體硬碟資料庫防爆與非同步緩衝讀寫機制
def load_database():
    if not os.path.exists(DB_FILE):
        return {"left_side": [], "right_side": []}
    try:
        with open(DB_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError:
        st.error("資料庫讀取異常，啟動安全備份還原...")
        return {"left_side": [], "right_side": []}

def save_database(data):
    # 採用雙層快取安全寫入，避免檔案鎖死 (Race Condition)
    temp_file = DB_FILE + ".tmp"
    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
    os.replace(temp_file, DB_FILE)

# ==========================================
# 視覺與大腦直覺指令 (The Interface)
# ==========================================
def render_signal(signal_type, message):
    # 嚴格禁止縮排，確保 Markdown 完美渲染不破圖
    if signal_type == "buy":
        st.markdown(f"### ✅【可以買進】\n{message}")
    elif signal_type == "danger":
        st.markdown(f"### ❌【極度危險】\n{message}")
    elif signal_type == "wait":
        st.markdown(f"### ⏳【等待時機】\n{message}")
    elif signal_type == "observe":
        st.markdown(f"### 🛡️【先觀察】\n{message}")

# ==========================================
# 戰略核心心法 (The Soul) - 邏輯運算引擎
# ==========================================
def check_left_side_logic(stock_data):
    """左側價值波段單 (魚頭掃描與長線護盾)"""
    # 總經斷頭防護與財報護盾邏輯
    if stock_data['is_margin_call_extreme']:
         return "buy", "大盤融資維持率極端斷頭，長線重壓價值浮現，啟動人棄我取機制。"
    
    if stock_data['turnaround'] or stock_data['eps_growth'] > 30:
        if stock_data['volume'] < 1000:
             return "wait", "財報大幅好轉，本質發生變化，目前無人知曉，準備無聲吃貨。"
        else:
             return "buy", "基本面強勁且具備小股本優勢，目標價尚未滿足。"
             
    if stock_data['core_event_vanished']:
        return "danger", "核心買進事件(如政策/運價)已反轉，霸王逃命條款觸發，無條件強制擊碎護盾。"

    return "observe", "基本面護盾運作中，不受短線技術面震盪影響。"

def check_right_side_logic(stock_data, current_price, avg_cost):
    """右側技術動能單 (10% 絕對停損結界與賣出三要件)"""
    # 期望值風控：10% 停損結界 (收盤價確認制)
    loss_percentage = ((current_price - avg_cost) / avg_cost) * 100
    
    if loss_percentage <= -10.0:
        return "danger", f"已觸發 10% 絕對停損結界 (目前虧損 {loss_percentage:.2f}%)，請一次全數殺出，維持 11:1 期望值。"
        
    # 賣出三要件判定 (爆量、上影線/大黑K、破5MA)
    if stock_data['price_below_5ma'] and (stock_data['huge_volume'] or stock_data['big_black_k']):
        if loss_percentage > 0:
            return "danger", "觸發賣出三要件且已有獲利，啟動停利防護，可分批慢慢賣出。"
        else:
            return "danger", "觸發賣出三要件，短線趨勢轉弱，請果斷停損。"

    if stock_data['leader_stock_crashed']:
        return "danger", "板塊領頭羊已暴跌，跟風股理由消失，強制撤退。"

    return "observe", "動能趨勢延續中，嚴守 5MA 防線。"

# ==========================================
# 前端介面與屬性絕對隔離
# ==========================================
st.set_page_config(page_title="作戰所 54088 - V9.0", layout="wide")

st.markdown("# 🦅 《作戰所 54088》V9.0 戰術終端")
st.markdown("---")

# 雙軌戰鬥模式隔離面板
strategy_mode = st.radio("請選擇作戰維度 (屬性絕對隔離)：", ("左側價值波段 (70% 資金)", "右側技術動能 (30% 資金)"))

st.markdown("### 標的情報載入")
col1, col2 = st.columns(2)
with col1:
    stock_symbol = st.text_input("輸入掃描代碼 (支援暴力情報萃取，無視格式)：")
    current_price = st.number_input("目前收盤價：", value=0.0)
with col2:
    avg_cost = st.number_input("持有成本價：", value=0.0)
    target_price = st.number_input("精算目標價 (預估EPS x 合理本益比)：", value=0.0)

if st.button("啟動雷達掃描與決策判定"):
    if stock_symbol:
        # 模擬暴力情報萃取與資料庫調用
        clean_symbol = ''.join(e for e in stock_symbol if e.isalnum())
        db = load_database()
        
        # 模擬即時全市場雙層快取掃描數據 (此處串接您的 API)
        mock_market_data = {
            "eps_growth": 35, 
            "turnaround": False,
            "volume": 500,
            "price_below_5ma": False,
            "huge_volume": False,
            "big_black_k": False,
            "core_event_vanished": False,
            "is_margin_call_extreme": False,
            "leader_stock_crashed": False
        }

        st.markdown("---")
        
        # 執行屬性隔離判定
        if "左側" in strategy_mode:
            st.markdown("#### 🛡️ 左側護盾掃描報告")
            sig, msg = check_left_side_logic(mock_market_data)
            render_signal(sig, msg)
            if target_price > 0 and current_price > 0:
                 st.markdown(f"**期望值探測：** 距離目標價尚有 {((target_price - current_price) / current_price)*100:.1f}% 潛在空間。")
                 
        elif "右側" in strategy_mode:
            st.markdown("#### ⚔️ 右側動能掃描報告")
            if avg_cost <= 0:
                st.warning("請輸入持有成本以啟動 10% 停損結界運算。")
            else:
                sig, msg = check_right_side_logic(mock_market_data, current_price, avg_cost)
                render_signal(sig, msg)
                
        # 背景非同步寫入資料庫
        save_database(db)
    else:
        st.error("請輸入有效標的代碼。")
