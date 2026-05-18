# Telegram Message Templates

## 1) Trade Opened
🟢 *TRADE OPENED*  
Pair: `{{symbol}}`  
Side: `LONG (spot)`  
Entry: `{{entry_price}}`  
Stop: `{{stop_loss_price}}`  
TP: `{{take_profit_price}}`  
Notional: `${{notional_usdt}}`  
Risk: `${{planned_risk_usdt}}`  
Time: `{{entry_time}}`

## 2) Trade Closed
🔴 *TRADE CLOSED*  
Pair: `{{symbol}}`  
Exit: `{{exit_price}}`  
Reason: `{{exit_reason}}`  
PnL: `${{net_pnl_usdt}}`  
PnL %: `{{pnl_percent_on_notional}}%`  
Held: `{{held_minutes}} min`  
Time: `{{exit_time}}`

## 3) Entry Skipped / Guard
🛡️ *ENTRY SKIPPED*  
Pair: `{{symbol}}`  
Reason: `{{reason}}`  
Equity: `${{equity_usdt}}`

## 4) 6-Hour Account Summary
📊 *6H ACCOUNT SUMMARY*  
Cash: `${{cash_usdt}}`  
Equity: `${{equity_usdt}}`  
Open Positions: `{{open_positions}}`  
Closed Trades: `{{closed_trades}}`  
Closed Net PnL: `${{closed_net_pnl_usdt}}`  
Daily Realized PnL: `${{daily_realized_pnl_usdt}}`  
Max Drawdown Guard: `{{drawdown_guard_status}}`  
Updated: `{{updated_at}}`
