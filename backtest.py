import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime

# ==============================================================================
# 1. 戰略指標與訊號判定核心 (無未來函數)
# ==============================================================================
def calculate_atr(df, period=14):
    high = df['High']
    low = df['Low']
    prev_close = df['Close'].shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return true_range.rolling(window=period).mean()

def determine_signal(current_price, ma5, ma20, vol_ratio, is_open_high_close_low, buffer_pct):
    score = 0
    if current_price > ma5 > ma20: score += 2
    elif current_price > ma5: score += 1
    elif current_price < ma5: score -= 2
    
    if vol_ratio < 0.6: score -= 1
    elif vol_ratio > 2.0: score += 1
    
    if is_open_high_close_low: score -= 2
    if buffer_pct < 1.0: score -= 1
    
    if score >= 3: return "🔥 偏多攻擊"
    elif score >= 1: return "🟡 觀察偏多"
    elif score <= -3: return "🔵 偏空防守"
    elif score <= -1: return "⚠️ 轉弱謹慎"
    else: return "⚖️ 中立震盪"

# ==============================================================================
# 2. 批次回測引擎與數據聚合
# ==============================================================================
def run_batch_backtest(stock_list, atr_multiplier=0.5, years=2):
    all_results = []
    
    print(f"\n系統啟動：開始批次抓取 {len(stock_list)} 檔標的歷史數據 (區間: {years} 年)...")
    
    for stock_code in stock_list:
        try:
            ticker = yf.Ticker(f"{stock_code}.TW")
            df = ticker.history(period=f"{years}y")
            
            if df.empty:
                ticker = yf.Ticker(f"{stock_code}.TWO")
                df = ticker.history(period=f"{years}y")
                
            if df.empty:
                print(f"略過 {stock_code}：查無資料。")
                continue
                
        except Exception as e:
            print(f"略過 {stock_code}：連線錯誤 ({str(e)})")
            continue

        df['MA5'] = df['Close'].rolling(window=5).mean()
        df['MA20'] = df['Close'].rolling(window=20).mean()
        df['Vol_5MA'] = df['Volume'].rolling(window=5).mean()
        df['ATR'] = calculate_atr(df, 14)
        
        for i in range(20, len(df) - 10): 
            curr_price = df['Close'].iloc[i]
            open_price = df['Open'].iloc[i]
            ma5 = df['MA5'].iloc[i]
            ma20 = df['MA20'].iloc[i]
            vol_today = df['Volume'].iloc[i]
            vol_5ma = df['Vol_5MA'].iloc[i]
            atr = df['ATR'].iloc[i]
            
            vol_ratio = (vol_today / vol_5ma) if vol_5ma > 0 else 0
            is_open_high_close_low = curr_price < open_price
            
            def_line = ma5 - (atr * atr_multiplier)
            buffer_pct = ((curr_price - def_line) / curr_price) * 100 if curr_price > 0 else 0
            
            signal = determine_signal(curr_price, ma5, ma20, vol_ratio, is_open_high_close_low, buffer_pct)
            
            # 計算未來報酬率 (無未來函數，僅評估訊號產生後的實際漲跌)
            future_3d_ret = (df['Close'].iloc[i+3] - curr_price) / curr_price * 100
            future_10d_ret = (df['Close'].iloc[i+10] - curr_price) / curr_price * 100
            
            # 追蹤 10 日內防守線擊穿狀況
            future_10d_window = df.iloc[i+1 : i+11]
            is_breached = any(future_10d_window['Low'] < def_line)
            
            all_results.append({
                'Stock': stock_code,
                'Date': df.index[i].strftime('%Y-%m-%d'),
                'Signal': signal,
                'Future_3D_Ret': future_3d_ret,
                'Future_10D_Ret': future_10d_ret,
                'Is_Breached': is_breached
            })

    if not all_results:
        print("未產出任何有效測試結果，請檢查股票代號或網路連線。")
        return

    res_df = pd.DataFrame(all_results)
    
    # ==============================================================================
    # 3. 輸出大樣本統計報表
    # ==============================================================================
    print("\n" + "="*85)
    print(f"📊 量化回測總結報表 (樣本數: {len(stock_list)} 檔 | ATR 緩衝倍數: {atr_multiplier})")
    print("="*85)
    print(f"{'決策訊號':<10} | {'樣本數':<5} | {'3日勝率':<6} | {'3日平均報酬':<8} | {'10日平均報酬':<9} | {'10日防守擊穿率'}")
    print("-" * 85)
    
    signals = ["🔥 偏多攻擊", "🟡 觀察偏多", "⚖️ 中立震盪", "⚠️ 轉弱謹慎", "🔵 偏空防守"]
    
    for sig in signals:
        subset = res_df[res_df['Signal'] == sig]
        count = len(subset)
        if count > 0:
            win_rate_3d = (len(subset[subset['Future_3D_Ret'] > 0]) / count) * 100
            avg_ret_3d = subset['Future_3D_Ret'].mean()
            avg_ret_10d = subset['Future_10D_Ret'].mean()
            breach_rate = (len(subset[subset['Is_Breached'] == True]) / count) * 100
            
            print(f"{sig:<10} | {count:<8} | {win_rate_3d:>5.1f}% | {avg_ret_3d:>8.2f}%   | {avg_ret_10d:>9.2f}%    | {breach_rate:>8.1f}%")
        else:
            print(f"{sig:<10} | {count:<8} | {'--':>6} | {'--':>9}   | {'--':>10}    | {'--':>9}")
            
    print("="*85)
    print("戰略判讀提示：")
    print("1. 期望值檢驗：若勝率低於 50%，但平均報酬為正，代表該訊號具有大賺小賠的特性。")
    print("2. 擊穿率檢驗：若偏多訊號的防守擊穿率大於 50%，強烈建議重新執行腳本，調高 ATR 倍數進行比對。")

if __name__ == "__main__":
    print("\n" + "="*50)
    print("54088 戰情室 - 獨立量化兵棋推演系統")
    print("="*50)
    
    stock_input = input("請輸入多檔股票代號 (以逗號分隔，例如 2330,2303,2317): ").strip()
    if stock_input:
        stock_list = [s.strip() for s in stock_input.split(',')]
        
        try:
            atr_input = input("請設定防守停損的 ATR 倍數 (預設 0.5，輸入 1.0 測試寬鬆防守): ").strip()
            atr_mult = float(atr_input) if atr_input else 0.5
        except ValueError:
            atr_mult = 0.5
            print("輸入無效，採用預設值 0.5")
            
        run_batch_backtest(stock_list, atr_multiplier=atr_mult, years=2)
