/**
 * n8n Code node: build Postgres persistence queries for paper trading.
 *
 * Code node mode: Run Once for All Items
 *
 * Place this after `binance_paper_trader_from_klines_n8n.js`.
 * Then connect to a Postgres node:
 *   Operation: Execute Query
 *   Query: ={{$json.query}}
 *
 * Run `sql/paper_trading_schema.sql` once before using this.
 */

const STORE_ACCOUNT_SNAPSHOTS = true;
const STORE_SIGNALS = false;
const STORE_OPEN_POSITIONS_EVERY_RUN = true;

function isPresent(value) {
  return value !== undefined && value !== null && value !== "";
}

function sqlText(value) {
  if (!isPresent(value)) return "NULL";
  return `'${String(value).replace(/'/g, "''")}'`;
}

function sqlNumber(value) {
  const number = Number(value);
  return Number.isFinite(number) ? String(number) : "NULL";
}

function sqlBool(value) {
  if (value === true) return "true";
  if (value === false) return "false";
  return "NULL";
}

function sqlTimestamp(value) {
  if (!isPresent(value)) return "NULL";
  return `${sqlText(String(value).replace("T", " ").replace("Z", ""))}::timestamp`;
}

function sqlJson(value) {
  return `${sqlText(JSON.stringify(value ?? {}))}::jsonb`;
}

function accountSnapshotQuery(row) {
  return `
INSERT INTO public.paper_account_snapshots (
  strategy, cash_usdt, equity_usdt, unrealized_pnl_usdt, closed_net_pnl_usdt,
  closed_trades, open_positions, open_position_symbols, capital_base_usdt,
  risk_per_trade_usdt, risk_per_trade_percent, total_open_notional_usdt,
  total_open_risk_usdt, max_total_exposure_usdt, daily_realized_pnl_usdt,
  daily_loss_limit_usdt
) VALUES (
  ${sqlText(row.strategy)},
  ${sqlNumber(row.cash_usdt)},
  ${sqlNumber(row.equity_usdt)},
  ${sqlNumber(row.unrealized_pnl_usdt)},
  ${sqlNumber(row.closed_net_pnl_usdt)},
  ${sqlNumber(row.closed_trades)},
  ${sqlNumber(row.open_positions)},
  ${sqlJson(row.open_position_symbols || [])},
  ${sqlNumber(row.capital_base_usdt)},
  ${sqlNumber(row.risk_per_trade_usdt)},
  ${sqlNumber(row.risk_per_trade_percent)},
  ${sqlNumber(row.total_open_notional_usdt)},
  ${sqlNumber(row.total_open_risk_usdt)},
  ${sqlNumber(row.max_total_exposure_usdt)},
  ${sqlNumber(row.daily_realized_pnl_usdt)},
  ${sqlNumber(row.daily_loss_limit_usdt)}
);`;
}

function signalQuery(row) {
  return `
INSERT INTO public.paper_signals (
  strategy, symbol, timeframe, bar_time, bar_close_time, close_price,
  high_price, low_price, rsi, ema50, ema200, atr14, signal, side,
  actionable, score, stop_loss_price, take_profit_price, paper_action,
  paper_reason, reason
) VALUES (
  ${sqlText(row.strategy)},
  ${sqlText(row.symbol)},
  ${sqlText(row.timeframe)},
  ${sqlTimestamp(row.bar_time)},
  ${sqlTimestamp(row.bar_close_time)},
  ${sqlNumber(row.close_price)},
  ${sqlNumber(row.high_price)},
  ${sqlNumber(row.low_price)},
  ${sqlNumber(row.rsi)},
  ${sqlNumber(row.ema50)},
  ${sqlNumber(row.ema200)},
  ${sqlNumber(row.atr14)},
  ${sqlText(row.signal)},
  ${sqlText(row.side)},
  ${sqlBool(row.actionable)},
  ${sqlNumber(row.score)},
  ${sqlNumber(row.stop_loss_price)},
  ${sqlNumber(row.take_profit_price)},
  ${sqlText(row.paper_action)},
  ${sqlText(row.paper_reason)},
  ${sqlText(row.reason)}
);`;
}

function tradeEventQuery(row) {
  return `
INSERT INTO public.paper_trade_events (
  event_id, entry_event_id, event_type, strategy, symbol, side, entry_time,
  exit_time, entry_price, exit_price, quantity, notional_usdt,
  planned_risk_usdt, risk_budget_usdt, stop_distance_percent,
  stop_loss_price, take_profit_price, open_fee_usdt, close_fee_usdt,
  gross_pnl_usdt, net_pnl_usdt, pnl_percent_on_notional, exit_reason, raw_event
) VALUES (
  ${sqlText(row.event_id)},
  ${sqlText(row.entry_event_id)},
  ${sqlText(row.type)},
  ${sqlText(row.strategy)},
  ${sqlText(row.symbol)},
  ${sqlText(row.side)},
  ${sqlTimestamp(row.entry_time)},
  ${sqlTimestamp(row.exit_time)},
  ${sqlNumber(row.entry_price)},
  ${sqlNumber(row.exit_price)},
  ${sqlNumber(row.quantity)},
  ${sqlNumber(row.notional_usdt)},
  ${sqlNumber(row.planned_risk_usdt)},
  ${sqlNumber(row.risk_budget_usdt)},
  ${sqlNumber(row.stop_distance_percent)},
  ${sqlNumber(row.stop_loss_price)},
  ${sqlNumber(row.take_profit_price)},
  ${sqlNumber(row.open_fee_usdt)},
  ${sqlNumber(row.close_fee_usdt)},
  ${sqlNumber(row.gross_pnl_usdt)},
  ${sqlNumber(row.net_pnl_usdt)},
  ${sqlNumber(row.pnl_percent_on_notional)},
  ${sqlText(row.exit_reason)},
  ${sqlJson(row)}
) ON CONFLICT (event_id) DO NOTHING;`;
}

function upsertOpenPositionQuery(row) {
  if (!row.event_id) return null;
  return `
INSERT INTO public.paper_positions (
  event_id, strategy, symbol, side, status, entry_time, entry_price, quantity,
  notional_usdt, planned_risk_usdt, risk_budget_usdt, stop_distance_percent,
  stop_loss_price, take_profit_price, open_fee_usdt, raw_open
) VALUES (
  ${sqlText(row.event_id)},
  ${sqlText(row.strategy)},
  ${sqlText(row.symbol)},
  ${sqlText(row.side)},
  'OPEN',
  ${sqlTimestamp(row.entry_time)},
  ${sqlNumber(row.entry_price)},
  ${sqlNumber(row.quantity)},
  ${sqlNumber(row.notional_usdt)},
  ${sqlNumber(row.planned_risk_usdt)},
  ${sqlNumber(row.risk_budget_usdt)},
  ${sqlNumber(row.stop_distance_percent)},
  ${sqlNumber(row.stop_loss_price)},
  ${sqlNumber(row.take_profit_price)},
  ${sqlNumber(row.open_fee_usdt)},
  ${sqlJson(row)}
) ON CONFLICT (event_id) DO UPDATE SET
  last_seen_at = now(),
  raw_open = COALESCE(public.paper_positions.raw_open, EXCLUDED.raw_open);`;
}

function closePositionQuery(row) {
  const eventIdMatch = row.entry_event_id
    ? `event_id = ${sqlText(row.entry_event_id)}`
    : `symbol = ${sqlText(row.symbol)} AND status = 'OPEN'`;

  return `
UPDATE public.paper_positions
SET
  status = 'CLOSED',
  exit_time = ${sqlTimestamp(row.exit_time)},
  exit_price = ${sqlNumber(row.exit_price)},
  close_fee_usdt = ${sqlNumber(row.close_fee_usdt)},
  gross_pnl_usdt = ${sqlNumber(row.gross_pnl_usdt)},
  net_pnl_usdt = ${sqlNumber(row.net_pnl_usdt)},
  pnl_percent_on_notional = ${sqlNumber(row.pnl_percent_on_notional)},
  exit_reason = ${sqlText(row.exit_reason)},
  closed_at = now(),
  last_seen_at = now(),
  raw_close = ${sqlJson(row)}
WHERE ${eventIdMatch};`;
}

const output = [];

for (const item of items) {
  const row = item.json || item;

  if (STORE_ACCOUNT_SNAPSHOTS && row.type === "paper_account") {
    output.push({ json: { db_action: "insert_account_snapshot", query: accountSnapshotQuery(row) } });
  }

  if (STORE_SIGNALS && row.type === "signal") {
    output.push({ json: { db_action: "insert_signal", query: signalQuery(row) } });
  }

  if (row.type === "trade_opened") {
    output.push({ json: { db_action: "insert_trade_opened_event", query: tradeEventQuery(row) } });
    output.push({ json: { db_action: "upsert_open_position", query: upsertOpenPositionQuery(row) } });
  }

  if (row.type === "trade_closed") {
    output.push({ json: { db_action: "insert_trade_closed_event", query: tradeEventQuery(row) } });
    output.push({ json: { db_action: "close_position", query: closePositionQuery(row) } });
  }

  if (STORE_OPEN_POSITIONS_EVERY_RUN && row.type === "open_position") {
    const query = upsertOpenPositionQuery(row);
    if (query) output.push({ json: { db_action: "refresh_open_position", query } });
  }
}

return output;
