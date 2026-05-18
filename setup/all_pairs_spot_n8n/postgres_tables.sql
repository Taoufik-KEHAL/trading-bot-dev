-- Core account state (single-row current state)
CREATE TABLE IF NOT EXISTS bot_account_state (
  id SMALLINT PRIMARY KEY DEFAULT 1,
  strategy_name TEXT NOT NULL,
  cash_usdt NUMERIC(18,8) NOT NULL,
  equity_usdt NUMERIC(18,8) NOT NULL,
  closed_net_pnl_usdt NUMERIC(18,8) NOT NULL DEFAULT 0,
  daily_realized_pnl_usdt NUMERIC(18,8) NOT NULL DEFAULT 0,
  trades_opened_today INTEGER NOT NULL DEFAULT 0,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Open and historical positions (spot only => side should be LONG)
CREATE TABLE IF NOT EXISTS bot_positions (
  position_id TEXT PRIMARY KEY,
  symbol TEXT NOT NULL,
  side TEXT NOT NULL CHECK (side IN ('LONG')),
  status TEXT NOT NULL CHECK (status IN ('OPEN','CLOSED')),
  entry_time TIMESTAMPTZ NOT NULL,
  exit_time TIMESTAMPTZ,
  entry_price NUMERIC(18,8) NOT NULL,
  exit_price NUMERIC(18,8),
  quantity NUMERIC(30,12) NOT NULL,
  notional_usdt NUMERIC(18,8) NOT NULL,
  stop_loss_price NUMERIC(18,8) NOT NULL,
  take_profit_price NUMERIC(18,8) NOT NULL,
  trailing_active BOOLEAN NOT NULL DEFAULT FALSE,
  trailing_stop_price NUMERIC(18,8),
  planned_risk_usdt NUMERIC(18,8) NOT NULL,
  open_fee_usdt NUMERIC(18,8) NOT NULL,
  close_fee_usdt NUMERIC(18,8),
  net_pnl_usdt NUMERIC(18,8),
  exit_reason TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_bot_positions_status ON bot_positions(status);
CREATE INDEX IF NOT EXISTS idx_bot_positions_symbol_status ON bot_positions(symbol, status);

-- Trade/event journal for telegram notifications and auditing
CREATE TABLE IF NOT EXISTS bot_trade_events (
  event_id TEXT PRIMARY KEY,
  event_type TEXT NOT NULL CHECK (event_type IN ('trade_opened','trade_closed','entry_skipped','risk_guard','summary')),
  symbol TEXT,
  side TEXT,
  status TEXT,
  message TEXT NOT NULL,
  payload JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_bot_trade_events_created_at ON bot_trade_events(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_bot_trade_events_type ON bot_trade_events(event_type);

-- Idempotency table for once-only telegram sends
CREATE TABLE IF NOT EXISTS bot_telegram_outbox (
  outbox_key TEXT PRIMARY KEY,
  channel TEXT NOT NULL DEFAULT 'telegram',
  message TEXT NOT NULL,
  sent_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
