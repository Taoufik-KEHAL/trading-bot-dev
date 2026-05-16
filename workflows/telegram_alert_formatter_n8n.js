/**
 * n8n Code node: format paper-trading events for Telegram alerts.
 *
 * Code node mode: Run Once for All Items
 *
 * Place this after `binance_paper_trader_from_klines_n8n.js`.
 * Then connect it to a Telegram node:
 *   Resource: Message
 *   Operation: Send Message
 *   Chat ID: your Telegram chat id
 *   Text: ={{$json.telegram_text}}
 *   Additional Fields > Parse Mode: HTML
 */

const ALERT_ON_OPEN = true;
const ALERT_ON_CLOSE = true;
const ALERT_ON_ENTRY_SKIPPED = false;
const ALERT_ON_ACCOUNT_SUMMARY = false;
const DEDUPE_ALERTS = true;

function fmt(value, decimals = 2) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "-";
  return number.toFixed(decimals);
}

function htmlEscape(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function eventKey(row) {
  if (row.event_id) return String(row.event_id);
  return [
    row.type,
    row.symbol,
    row.side,
    row.entry_time,
    row.exit_time,
    row.entry_price,
    row.exit_price,
    row.exit_reason,
  ].join("|");
}

function tradeOpenedMessage(row) {
  return [
    `🟢 <b>Paper trade opened</b>`,
    `<b>${htmlEscape(row.symbol)}</b> ${htmlEscape(row.side)}`,
    `Entry: <code>${fmt(row.entry_price, 8)}</code>`,
    `Notional: <code>${fmt(row.notional_usdt, 2)} USDT</code>`,
    `Planned risk: <code>${fmt(row.planned_risk_usdt, 4)} USDT</code>`,
    `Stop distance: <code>${fmt(row.stop_distance_percent, 4)}%</code>`,
    `Qty: <code>${fmt(row.quantity, 8)}</code>`,
    `Stop: <code>${fmt(row.stop_loss_price, 8)}</code>`,
    `Target: <code>${fmt(row.take_profit_price, 8)}</code>`,
    `Fee: <code>${fmt(row.open_fee_usdt, 4)} USDT</code>`,
    `Strategy: <code>${htmlEscape(row.strategy)}</code>`,
  ].join("\n");
}

function tradeClosedMessage(row) {
  const pnl = Number(row.net_pnl_usdt);
  const icon = pnl >= 0 ? "✅" : "🔴";
  return [
    `${icon} <b>Paper trade closed</b>`,
    `<b>${htmlEscape(row.symbol)}</b> ${htmlEscape(row.side)}`,
    `Entry: <code>${fmt(row.entry_price, 8)}</code>`,
    `Exit: <code>${fmt(row.exit_price, 8)}</code>`,
    `Reason: <code>${htmlEscape(row.exit_reason)}</code>`,
    `Gross PnL: <code>${fmt(row.gross_pnl_usdt, 4)} USDT</code>`,
    `Net PnL: <code>${fmt(row.net_pnl_usdt, 4)} USDT</code>`,
    `Return: <code>${fmt(row.pnl_percent_on_notional, 4)}%</code>`,
    `Equity: <code>${fmt(row.equity_usdt, 2)} USDT</code>`,
  ].join("\n");
}

function entrySkippedMessage(row) {
  return [
    `⚠️ <b>Paper entry skipped</b>`,
    `<b>${htmlEscape(row.symbol)}</b> ${htmlEscape(row.signal || "")}`,
    `Reason: ${htmlEscape(row.reason || row.paper_reason || "-")}`,
  ].join("\n");
}

function accountSummaryMessage(row) {
  return [
    `📊 <b>Paper account</b>`,
    `Cash: <code>${fmt(row.cash_usdt, 2)} USDT</code>`,
    `Equity: <code>${fmt(row.equity_usdt, 2)} USDT</code>`,
    `Unrealized: <code>${fmt(row.unrealized_pnl_usdt, 2)} USDT</code>`,
    `Closed PnL: <code>${fmt(row.closed_net_pnl_usdt, 2)} USDT</code>`,
    `Closed trades: <code>${row.closed_trades ?? 0}</code>`,
    `Open positions: <code>${row.open_positions ?? 0}</code>`,
    `Risk/trade: <code>${fmt(row.risk_per_trade_usdt, 4)} USDT</code>`,
    `Open risk: <code>${fmt(row.total_open_risk_usdt, 4)} USDT</code>`,
  ].join("\n");
}

const staticData = $getWorkflowStaticData("global");
staticData.telegramAlertKeys ||= {};

const output = [];

for (const item of items) {
  const row = item.json || item;
  let telegramText = null;

  if (ALERT_ON_OPEN && row.type === "trade_opened") {
    telegramText = tradeOpenedMessage(row);
  } else if (ALERT_ON_CLOSE && row.type === "trade_closed") {
    telegramText = tradeClosedMessage(row);
  } else if (ALERT_ON_ENTRY_SKIPPED && row.type === "entry_skipped") {
    telegramText = entrySkippedMessage(row);
  } else if (ALERT_ON_ACCOUNT_SUMMARY && row.type === "paper_account") {
    telegramText = accountSummaryMessage(row);
  }

  if (!telegramText) continue;

  const key = eventKey(row);
  if (DEDUPE_ALERTS && staticData.telegramAlertKeys[key]) continue;
  staticData.telegramAlertKeys[key] = new Date().toISOString();

  output.push({
    json: {
      ...row,
      telegram_text: telegramText,
      telegram_parse_mode: "HTML",
    },
  });
}

return output;
