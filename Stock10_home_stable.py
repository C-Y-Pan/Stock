    # --- 進場邏輯 ---
        if position == 0:
            is_buy = False
            rsi_threshold_A = 60 if is_strict_bear else 55
            action_code = "Hold" # Initialize action_code here

            # 只有在「非禁買」狀態下才檢查策略
            if not is_squeeze_ban:
                # 策略 A
                if (trend[i]==1 and (i>0 and trend[i-1]==-1) and volume[i]>vol_ma20[i] and close[i]>ma60[i] and rsi[i]>rsi_threshold_A and obv[i]>obv_ma20[i]):
                    is_buy=True; trade_type=1; reason_str="動能突破"
                # 策略 B
                elif not is_strict_bear and trend[i]==1 and close[i]>ma60[i] and (df['Low'].iloc[i]<=ma20[i]*1.02) and close[i]>ma20[i] and volume[i]<vol_ma20[i] and rsi[i]>45:
                    is_buy=True; trade_type=1; reason_str="均線回測"
                # 策略 C
                elif use_chip_strategy and not is_strict_bear and close[i]>ma60[i] and obv[i]>obv_ma20[i] and volume[i]<vol_ma20[i] and (close[i]<ma20[i] or rsi[i]<55) and close[i]>bb_lower[i]:
                    is_buy=True; trade_type=3; reason_str="籌碼佈局"
                # 策略 D (超賣反彈也需避開極度壓縮後的崩盤)
                elif rsi[i]<rsi_buy_thresh and close[i]<bb_lower[i] and market_panic[i] and volume[i]>vol_ma20[i]*0.5:
                    is_buy=True; trade_type=2; reason_str="超賣反彈"

            if is_buy:
                signal=1; days_held=0; entry_price=close[i]; action_code="Buy"
                cum_div = 0.0

                base_score = 60
                if is_strict_bear: base_score -= 10
                if is_ma240_down and is_ma60_up: base_score += 5
                if volume[i] > vol_ma20[i] * 1.5: base_score += 15
                elif volume[i] > vol_ma20[i]: base_score += 8
                if i > 5 and ma60[i] > ma60[i-5] and close[i] > ma60[i]: base_score += 10
                if trade_type == 1 and 60 <= rsi[i] <= 75: base_score += 10
                elif trade_type == 2 and rsi[i] <= 25: base_score += 10
                if i > 3 and bb_width_vals[i-1] < 0.15: base_score += 5
                if close[i] > ma30[i] * 1.04: base_score += 5

                weekly_ratio = close[i] / close_lag5[i] if close_lag5[i] > 0 else 1.0
                if close[i] >= high_100d[i] and weekly_ratio < 1.27: base_score += 15

                conf_score = min(base_score, 99)

        # --- 出場邏輯 ---
        elif position == 1:
            days_held+=1
            if dividends[i] > 0: cum_div += dividends[i]
            adjusted_current_value = close[i] + cum_div
            drawdown = (adjusted_current_value - entry_price) / entry_price

            if trade_type==2 and trend[i]==1: trade_type=1; reason_str="反彈轉波段"
            if trade_type==3 and volume[i]>vol_ma20[i]*1.2: trade_type=1; reason_str="佈局完成發動"

            is_sell = False
            stop_loss_limit = -0.10 if is_strict_bear else -0.12
            action_code = "Hold" # Default to Hold unless a specific sell condition is met

            if drawdown < stop_loss_limit:
                is_sell=True; reason_str=f"觸發停損({stop_loss_limit*100:.0f}%)"; action_code="Sell"
            elif days_held <= (2 if is_strict_bear else 3):
                action_code="Hold"; reason_str="鎖倉觀察"
            else:
                if trade_type==1 and trend[i]==-1:
                    if close[i] < ma20[i]:
                        is_sell=True; reason_str="趨勢轉弱且破月線"
                    else:
                        action_code="Hold"; reason_str="轉弱(守月線)"
                elif use_strict_bear_exit and is_strict_bear and close[i] < ma20[i]:
                    is_sell=True; reason_str="長空破月線"
                elif trade_type==2 and days_held>10 and drawdown<0: is_sell=True; reason_str="逆勢操作超時"
                elif trade_type==3 and close[i]<bb_lower[i]: is_sell=True; reason_str="支撐確認失敗"

            if is_sell:
                signal=0; action_code="Sell"
                final_pnl_value = (close[i] + cum_div) - entry_price
                pnl = final_pnl_value / entry_price * 100
                sign = "+" if pnl > 0 else ""
                ret_label = f"{sign}{pnl:.1f}%"

        position=signal
        positions.append(signal); reasons.append(reason_str); actions.append(action_code)
        target_prices.append(this_target); return_labels.append(ret_label)
        confidences.append(conf_score if action_code == "Buy" else 0)
