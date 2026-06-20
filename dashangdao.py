# ... (前面代碼保持不變，重點修改 calculate_tactical_signals 函式中的均線邏輯)

        # 改用季線 (MA60) 作為核心基準
        ma60 = hist_6m['Close'].rolling(window=60).mean().iloc[-1]
        
        # 波段打擊區調整：以季線為中心，正負 3% 作為打擊範圍
        # 這樣設定能讓打擊區更具波段保護力
        main_cost = override_cost if override_cost else round(ma60, 1)
        buy_low = round(main_cost * 0.97, 1)
        buy_high = round(main_cost * 1.03, 1)
        buy_zone = f"{buy_low} - {buy_high}"

        # 撤退導航儀邏輯同步更新
        diff_from_cost = ((current_price - main_cost) / main_cost) * 100
        
        # 波段撤退條件優化：以季線防守為主
        if is_expensive:
            exit_strategy = "🔴 價值滿水：建議分批獲利了結"
            exit_price = f"現價 {current_price:.1f}"
            exit_color = "#e74c3c"
            exit_bg = "#3a1515"
        elif diff_from_cost >= 15.0: # 波段獲利超過15%啟動階梯保本
            trailing_stop = max(current_price * 0.92, main_cost)
            exit_strategy = "🛡️ 階梯保本：跌破此線出場，鎖定波段利潤"
            exit_price = f"{trailing_stop:.1f}"
            exit_color = "#3498db"
            exit_bg = "#152a3a"
        else:
            stop_loss = main_cost * 0.95 # 波段停損放寬至5%，給予主力洗盤空間
            exit_strategy = "🚪 鐵血紀律：跌破季線支撐5%無條件撤退"
            exit_price = f"{stop_loss:.1f}"
            exit_color = "#8e44ad"
            exit_bg = "#2c153a"

# ... (其餘程式碼不變)
