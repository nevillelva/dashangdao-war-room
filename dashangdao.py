import streamlit as st

# ... (CSS 與上方保持一致，省略) ...

def calculate_real_profit(price, cost, qty):
    # 交易總額
    buy_amount = cost * qty * 1000
    sell_amount = price * qty * 1000
    
    # 手續費 (買賣各 0.1425%，不足 20 元以 20 元計)
    buy_fee = max(20, buy_amount * 0.001425)
    sell_fee = max(20, sell_amount * 0.001425)
    
    # 交易稅 (賣出 0.3%)
    tax = sell_amount * 0.003
    
    # 淨利潤
    profit = sell_amount - buy_amount - buy_fee - sell_fee - tax
    roi = (profit / buy_amount) * 100
    return profit, roi

# 在循環中的模擬倉區塊修正如下：
with st.expander("💼 實戰模擬持倉精算 (含稅費)"):
    cost = st.number_input(f"成本 {s['c']}", value=float(s.get('cost', 378.0)), key=f"cost_{s['c']}")
    qty = st.number_input(f"張數 {s['c']}", value=1.0, key=f"qty_{s['c']}")
    
    # 執行實戰損益計算
    profit, roi = calculate_real_profit(380.0, cost, qty)
    
    st.markdown(f"""
    <div style="background:#0b0c0f; padding:10px; border-radius:5px;">
    💰 <b>實戰淨利潤:</b> {profit:,.0f} 元<br>
    📈 <b>實際報酬率:</b> {roi:.2f}%
    </div>
    """, unsafe_allow_html=True)
