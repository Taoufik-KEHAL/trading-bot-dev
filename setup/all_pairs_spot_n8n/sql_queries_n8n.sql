-- 1) Upsert account state (from strategy/account summary output)
-- Bind parameters in n8n as needed.
INSERT INTO bot_account_state (
  id, strategy_name, cash_usdt, equity_usdt, closed_net_pnl_usdt,
  daily_realized_pnl_usdt, trades_opened_today, updated_at
)
VALUES (
  1, :strategy_name, :cash_usdt, :equity_usdt, :closed_net_pnl_usdt,
  :daily_realized_pnl_usdt, :trades_opened_today, NOW()
)
ON CONFLICT (id)
DO UPDATE SET
  strategy_name = EXCLUDED.strategy_name,
  cash_usdt = EXCLUDED.cash_usdt,
  equity_usdt = EXCLUDED.equity_usdt,
  closed_net_pnl_usdt = EXCLUDED.closed_net_pnl_usdt,
  daily_realized_pnl_usdt = EXCLUDED.daily_realized_pnl_usdt,
  trades_opened_today = EXCLUDED.trades_opened_today,
  updated_at = NOW();

-- 2) Insert trade event (idempotent by event_id)
INSERT INTO bot_trade_events (event_id, event_type, symbol, side, status, message, payload)
VALUES (:event_id, :event_type, :symbol, :side, :status, :message, :payload::jsonb)
ON CONFLICT (event_id) DO NOTHING;

-- 3) Open position insert
INSERT INTO bot_positions (
  position_id, symbol, side, status, entry_time, entry_price, quantity,
  notional_usdt, stop_loss_price, take_profit_price, planned_risk_usdt,
  open_fee_usdt, created_at, updated_at
)
VALUES (
  :position_id, :symbol, 'LONG', 'OPEN', :entry_time, :entry_price, :quantity,
  :notional_usdt, :stop_loss_price, :take_profit_price, :planned_risk_usdt,
  :open_fee_usdt, NOW(), NOW()
)
ON CONFLICT (position_id) DO NOTHING;

-- 4) Close position update
UPDATE bot_positions
SET
  status = 'CLOSED',
  exit_time = :exit_time,
  exit_price = :exit_price,
  close_fee_usdt = :close_fee_usdt,
  net_pnl_usdt = :net_pnl_usdt,
  exit_reason = :exit_reason,
  updated_at = NOW()
WHERE position_id = :position_id AND status = 'OPEN';

-- 5) Open positions snapshot (used by 1m exit workflow)
SELECT * FROM bot_positions WHERE status = 'OPEN' ORDER BY entry_time ASC;

-- 6) Telegram outbox dedupe check+insert
INSERT INTO bot_telegram_outbox (outbox_key, message)
VALUES (:outbox_key, :message)
ON CONFLICT (outbox_key) DO NOTHING;
