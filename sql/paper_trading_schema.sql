CREATE TABLE IF NOT EXISTS public.paper_account_snapshots (
  id bigserial PRIMARY KEY,
  strategy text NOT NULL,
  cash_usdt numeric NOT NULL,
  equity_usdt numeric NOT NULL,
  unrealized_pnl_usdt numeric NOT NULL,
  closed_net_pnl_usdt numeric NOT NULL,
  closed_trades integer NOT NULL,
  open_positions integer NOT NULL,
  open_position_symbols jsonb NOT NULL DEFAULT '[]'::jsonb,
  capital_base_usdt numeric,
  risk_per_trade_usdt numeric,
  risk_per_trade_percent numeric,
  total_open_notional_usdt numeric,
  total_open_risk_usdt numeric,
  max_total_exposure_usdt numeric,
  daily_realized_pnl_usdt numeric,
  daily_loss_limit_usdt numeric,
  created_at timestamp without time zone NOT NULL DEFAULT now()
);

ALTER TABLE public.paper_account_snapshots
  ADD COLUMN IF NOT EXISTS capital_base_usdt numeric,
  ADD COLUMN IF NOT EXISTS risk_per_trade_usdt numeric,
  ADD COLUMN IF NOT EXISTS risk_per_trade_percent numeric,
  ADD COLUMN IF NOT EXISTS total_open_notional_usdt numeric,
  ADD COLUMN IF NOT EXISTS total_open_risk_usdt numeric,
  ADD COLUMN IF NOT EXISTS max_total_exposure_usdt numeric,
  ADD COLUMN IF NOT EXISTS daily_realized_pnl_usdt numeric,
  ADD COLUMN IF NOT EXISTS daily_loss_limit_usdt numeric;

CREATE TABLE IF NOT EXISTS public.paper_positions (
  event_id text PRIMARY KEY,
  strategy text NOT NULL,
  symbol text NOT NULL,
  side text NOT NULL,
  status text NOT NULL DEFAULT 'OPEN',
  entry_time timestamp without time zone,
  exit_time timestamp without time zone,
  entry_price numeric,
  exit_price numeric,
  quantity numeric,
  notional_usdt numeric,
  planned_risk_usdt numeric,
  risk_budget_usdt numeric,
  stop_distance_percent numeric,
  stop_loss_price numeric,
  take_profit_price numeric,
  open_fee_usdt numeric,
  close_fee_usdt numeric,
  gross_pnl_usdt numeric,
  net_pnl_usdt numeric,
  pnl_percent_on_notional numeric,
  exit_reason text,
  opened_at timestamp without time zone NOT NULL DEFAULT now(),
  closed_at timestamp without time zone,
  last_seen_at timestamp without time zone NOT NULL DEFAULT now(),
  raw_open jsonb,
  raw_close jsonb
);

CREATE TABLE IF NOT EXISTS public.paper_signals (
  id bigserial PRIMARY KEY,
  strategy text NOT NULL,
  symbol text NOT NULL,
  timeframe text NOT NULL,
  bar_time timestamp without time zone,
  bar_close_time timestamp without time zone,
  close_price numeric,
  high_price numeric,
  low_price numeric,
  rsi numeric,
  ema50 numeric,
  ema200 numeric,
  atr14 numeric,
  signal text NOT NULL,
  side text,
  actionable boolean NOT NULL DEFAULT false,
  score numeric,
  stop_loss_price numeric,
  take_profit_price numeric,
  paper_action text,
  paper_reason text,
  reason text,
  created_at timestamp without time zone NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.paper_trade_events (
  id bigserial PRIMARY KEY,
  event_id text,
  entry_event_id text,
  event_type text NOT NULL,
  strategy text,
  symbol text,
  side text,
  entry_time timestamp without time zone,
  exit_time timestamp without time zone,
  entry_price numeric,
  exit_price numeric,
  quantity numeric,
  notional_usdt numeric,
  planned_risk_usdt numeric,
  risk_budget_usdt numeric,
  stop_distance_percent numeric,
  stop_loss_price numeric,
  take_profit_price numeric,
  open_fee_usdt numeric,
  close_fee_usdt numeric,
  gross_pnl_usdt numeric,
  net_pnl_usdt numeric,
  pnl_percent_on_notional numeric,
  exit_reason text,
  raw_event jsonb NOT NULL,
  created_at timestamp without time zone NOT NULL DEFAULT now()
);

ALTER TABLE public.paper_trade_events
  ADD COLUMN IF NOT EXISTS event_id text,
  ADD COLUMN IF NOT EXISTS entry_event_id text,
  ADD COLUMN IF NOT EXISTS planned_risk_usdt numeric,
  ADD COLUMN IF NOT EXISTS risk_budget_usdt numeric,
  ADD COLUMN IF NOT EXISTS stop_distance_percent numeric;

CREATE INDEX IF NOT EXISTS paper_signals_symbol_bar_idx
  ON public.paper_signals(symbol, bar_time DESC);

CREATE UNIQUE INDEX IF NOT EXISTS paper_trade_events_event_id_idx
  ON public.paper_trade_events(event_id);

CREATE INDEX IF NOT EXISTS paper_trade_events_symbol_created_idx
  ON public.paper_trade_events(symbol, created_at DESC);

CREATE INDEX IF NOT EXISTS paper_positions_status_symbol_idx
  ON public.paper_positions(status, symbol);

CREATE INDEX IF NOT EXISTS paper_positions_opened_idx
  ON public.paper_positions(opened_at DESC);
