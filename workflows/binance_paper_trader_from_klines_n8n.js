/**
 * n8n Code node: strategy + paper trading from Binance 4h klines.
 *
 * Code node mode: Run Once for All Items
 *
 * Expected input from previous HTTP Request node:
 * {
 *   symbol: "BTCUSDT",
 *   interval: "4h",
 *   klines: [
 *     [
 *       1499040000000, "0.01634790", "0.80000000", "0.01575800",
 *       "0.01577100", "148976.11427815", 1499644799999, ...
 *     ]
 *   ]
 * }
 *
 * This is paper trading only. It does not call Binance order endpoints.
 */

const RESET_PAPER_STATE = false;

const STRATEGY = {
  name: "trend_pullback_4h_sl1.8_rr1.5_hold12_rsi35-65",
  timeframe: "4h",
  timeframeMs: 4 * 60 * 60 * 1000,
  emaFast: 50,
  emaSlow: 200,
  rsiPeriod: 14,
  atrPeriod: 14,
  rsiLow: 35,
  rsiHigh: 65,
  stopAtr: 1.8,
  rewardRisk: 1.5,
  maxHoldBars: 12,
};

const PAPER = {
  initialCashUsdt: 100,
  riskPerTradeFraction: 0.01,
  maxTradeNotionalFraction: 0.25,
  maxTotalExposureFraction: 0.60,
  maxOpenPositions: 2,
  maxDailyLossFraction: 0.03,
  maxAccountDrawdownFraction: 0.10,
  minTradeNotionalUsdt: 10,
  feeRatePerSide: 0.0005,
  allowPaperShorts: true,
};

const SYMBOLS = [
  "ADAUSDT",
  "AVAXUSDT",
  "BNBUSDT",
  "BTCUSDT",
  "DOGEUSDT",
  "DOTUSDT",
  "ETHUSDT",
  "LINKUSDT",
  "SOLUSDT",
  "XRPUSDT",
];

const EXPECTED_KLINES_PER_SYMBOL = 300;

function safeNumber(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function round(value, decimals = 8) {
  if (value === null || value === undefined || !Number.isFinite(value)) return null;
  return Number(value.toFixed(decimals));
}

function iso(timestampMs) {
  return new Date(timestampMs).toISOString().replace(".000Z", "Z");
}

function getKlinesFromItem(item) {
  const json = item.json || item;
  const symbol = String(json.symbol || "").toUpperCase();
  let klines = json.klines ?? json.body ?? json.data ?? json.response;

  if (typeof klines === "string") {
    klines = JSON.parse(klines);
  }

  if (!Array.isArray(klines)) {
    throw new Error(`No Binance kline array found for ${symbol || "unknown symbol"}`);
  }

  return { symbol, klines };
}

function isSplitKlineRow(json) {
  return (
    json &&
    safeNumber(json[0]) !== null &&
    safeNumber(json[1]) !== null &&
    safeNumber(json[2]) !== null &&
    safeNumber(json[3]) !== null &&
    safeNumber(json[4]) !== null &&
    safeNumber(json[5]) !== null &&
    safeNumber(json[6]) !== null
  );
}

function splitKlineJsonToArray(json) {
  return [
    json[0],
    json[1],
    json[2],
    json[3],
    json[4],
    json[5],
    json[6],
    json[7],
    json[8],
    json[9],
    json[10],
    json[11],
  ];
}

function normalizeInputItems(inputItems) {
  const grouped = [];
  const splitRows = [];

  for (const item of inputItems) {
    const json = item.json || item;
    const symbol = String(json.symbol || "").toUpperCase();
    const maybeKlines = json.klines ?? json.body ?? json.data ?? json.response;

    if (symbol && Array.isArray(maybeKlines)) {
      grouped.push({ symbol, klines: maybeKlines });
      continue;
    }

    if (isSplitKlineRow(json)) {
      splitRows.push(splitKlineJsonToArray(json));
    }
  }

  if (grouped.length > 0) return grouped;
  if (splitRows.length === 0) return [];

  // Fallback for an HTTP Request node that split Binance's array response into
  // 3000 separate items. This assumes the requests were generated in SYMBOLS
  // order and each symbol returned EXPECTED_KLINES_PER_SYMBOL rows.
  const fallbackGroups = [];
  for (let i = 0; i < SYMBOLS.length; i += 1) {
    const start = i * EXPECTED_KLINES_PER_SYMBOL;
    const end = start + EXPECTED_KLINES_PER_SYMBOL;
    const klines = splitRows.slice(start, end);
    if (klines.length > 0) {
      fallbackGroups.push({ symbol: SYMBOLS[i], klines });
    }
  }

  return fallbackGroups;
}

function parseBinanceKlines(rawKlines) {
  return rawKlines
    .map((kline) => ({
      openTime: safeNumber(kline[0]),
      open: safeNumber(kline[1]),
      high: safeNumber(kline[2]),
      low: safeNumber(kline[3]),
      close: safeNumber(kline[4]),
      volume: safeNumber(kline[5]),
      closeTime: safeNumber(kline[6]),
    }))
    .filter(
      (bar) =>
        bar.openTime !== null &&
        bar.open !== null &&
        bar.high !== null &&
        bar.low !== null &&
        bar.close !== null &&
        bar.volume !== null &&
        bar.closeTime !== null
    )
    .sort((a, b) => a.openTime - b.openTime);
}

function completeBarsOnly(bars) {
  const now = Date.now();
  return bars.filter((bar) => bar.closeTime < now);
}

function ema(values, span) {
  const out = Array(values.length).fill(null);
  const alpha = 2 / (span + 1);
  let current = null;

  for (let i = 0; i < values.length; i += 1) {
    const value = values[i];
    current = current === null ? value : alpha * value + (1 - alpha) * current;
    if (i >= span - 1) out[i] = current;
  }

  return out;
}

function wilderAverage(values, period) {
  const out = Array(values.length).fill(null);
  let current = null;

  for (let i = 0; i < values.length; i += 1) {
    const value = values[i];
    if (!Number.isFinite(value)) continue;
    current = current === null ? value : (current * (period - 1) + value) / period;
    if (i >= period - 1) out[i] = current;
  }

  return out;
}

function rsi(closes, period) {
  const gains = Array(closes.length).fill(null);
  const losses = Array(closes.length).fill(null);

  for (let i = 1; i < closes.length; i += 1) {
    const diff = closes[i] - closes[i - 1];
    gains[i] = Math.max(diff, 0);
    losses[i] = Math.max(-diff, 0);
  }

  const avgGain = wilderAverage(gains, period);
  const avgLoss = wilderAverage(losses, period);
  const out = Array(closes.length).fill(null);

  for (let i = 0; i < closes.length; i += 1) {
    if (avgGain[i] === null || avgLoss[i] === null) continue;
    out[i] = avgLoss[i] === 0 ? 100 : 100 - 100 / (1 + avgGain[i] / avgLoss[i]);
  }

  return out;
}

function atr(bars, period) {
  const trueRanges = Array(bars.length).fill(null);

  for (let i = 0; i < bars.length; i += 1) {
    if (i === 0) {
      trueRanges[i] = bars[i].high - bars[i].low;
      continue;
    }

    const prevClose = bars[i - 1].close;
    trueRanges[i] = Math.max(
      bars[i].high - bars[i].low,
      Math.abs(bars[i].high - prevClose),
      Math.abs(bars[i].low - prevClose)
    );
  }

  return wilderAverage(trueRanges, period);
}

function buildSignal(symbol, bars) {
  const completed = completeBarsOnly(bars);
  if (completed.length < STRATEGY.emaSlow + 2) {
    return {
      symbol,
      strategy: STRATEGY.name,
      timeframe: STRATEGY.timeframe,
      signal: "HOLD",
      actionable: false,
      reason: `Need at least ${STRATEGY.emaSlow + 2} complete 4h bars`,
    };
  }

  const closes = completed.map((bar) => bar.close);
  const emaFast = ema(closes, STRATEGY.emaFast);
  const emaSlow = ema(closes, STRATEGY.emaSlow);
  const rsi14 = rsi(closes, STRATEGY.rsiPeriod);
  const atr14 = atr(completed, STRATEGY.atrPeriod);
  const idx = completed.length - 1;
  const bar = completed[idx];
  const previousRsi = rsi14[idx - 1];
  const currentRsi = rsi14[idx];
  const currentEmaFast = emaFast[idx];
  const currentEmaSlow = emaSlow[idx];
  const currentAtr = atr14[idx];

  if (
    previousRsi === null ||
    currentRsi === null ||
    currentEmaFast === null ||
    currentEmaSlow === null ||
    currentAtr === null ||
    currentAtr <= 0
  ) {
    return {
      symbol,
      strategy: STRATEGY.name,
      timeframe: STRATEGY.timeframe,
      bar_time: iso(bar.openTime),
      close_price: round(bar.close),
      signal: "HOLD",
      actionable: false,
      reason: "Indicators are still warming up",
    };
  }

  const longSignal =
    currentEmaFast > currentEmaSlow &&
    bar.close > currentEmaSlow &&
    previousRsi < STRATEGY.rsiLow &&
    currentRsi >= STRATEGY.rsiLow;

  const shortSignal =
    currentEmaFast < currentEmaSlow &&
    bar.close < currentEmaSlow &&
    previousRsi > STRATEGY.rsiHigh &&
    currentRsi <= STRATEGY.rsiHigh;

  let signal = "HOLD";
  let side = null;
  let stopLoss = null;
  let takeProfit = null;
  let score = 0;

  if (longSignal) {
    signal = "BUY";
    side = "LONG";
    const risk = currentAtr * STRATEGY.stopAtr;
    stopLoss = bar.close - risk;
    takeProfit = bar.close + risk * STRATEGY.rewardRisk;
    score = Math.abs(currentRsi - STRATEGY.rsiLow);
  }

  if (shortSignal) {
    signal = "SELL";
    side = "SHORT";
    const risk = currentAtr * STRATEGY.stopAtr;
    stopLoss = bar.close + risk;
    takeProfit = bar.close - risk * STRATEGY.rewardRisk;
    score = Math.abs(currentRsi - STRATEGY.rsiHigh);
  }

  return {
    symbol,
    strategy: STRATEGY.name,
    timeframe: STRATEGY.timeframe,
    bar_time: iso(bar.openTime),
    bar_open_time_ms: bar.openTime,
    bar_close_time: iso(bar.closeTime),
    close_price: round(bar.close),
    high_price: round(bar.high),
    low_price: round(bar.low),
    rsi: round(currentRsi, 4),
    ema50: round(currentEmaFast),
    ema200: round(currentEmaSlow),
    atr14: round(currentAtr),
    signal,
    side,
    actionable: signal !== "HOLD",
    score: round(score, 4),
    stop_loss_price: round(stopLoss),
    take_profit_price: round(takeProfit),
    max_hold_bars: STRATEGY.maxHoldBars,
    max_hold_hours: STRATEGY.maxHoldBars * 4,
    reason:
      signal === "HOLD"
        ? "No entry: waiting for EMA trend + RSI pullback cross"
        : `${signal}: EMA trend confirmed and RSI crossed pullback threshold`,
  };
}

function initState(staticData) {
  if (RESET_PAPER_STATE || !staticData.paperTradingBinanceV1) {
    staticData.paperTradingBinanceV1 = {
      version: 1,
      strategy: STRATEGY.name,
      cashUsdt: PAPER.initialCashUsdt,
      positions: {},
      trades: [],
      events: [],
      lastProcessedBarBySymbol: {},
      openedTradeKeys: {},
      createdAt: new Date().toISOString(),
    };
  }

  const state = staticData.paperTradingBinanceV1;
  state.positions ||= {};
  state.trades ||= [];
  state.events ||= [];
  state.lastProcessedBarBySymbol ||= {};
  state.openedTradeKeys ||= {};
  return state;
}

function positionPnlUsdt(position, markPrice) {
  if (!position || !Number.isFinite(markPrice) || markPrice <= 0) return 0;
  const direction = position.side === "LONG" ? 1 : -1;
  return direction * ((markPrice / position.entry_price) - 1) * position.notional_usdt;
}

function todayKey() {
  return new Date().toISOString().slice(0, 10);
}

function ensureRiskState(state) {
  state.risk ||= {};
  if (state.risk.date !== todayKey()) {
    state.risk = {
      date: todayKey(),
      realizedPnlTodayUsdt: 0,
      tradesOpenedToday: 0,
    };
  }
  state.risk.realizedPnlTodayUsdt ||= 0;
  state.risk.tradesOpenedToday ||= 0;
  return state.risk;
}

function totalOpenNotional(state) {
  return Object.values(state.positions).reduce(
    (sum, position) => sum + Number(position.notional_usdt || 0),
    0
  );
}

function totalOpenRiskUsdt(state) {
  return Object.values(state.positions).reduce(
    (sum, position) => sum + Number(position.planned_risk_usdt || 0),
    0
  );
}

function openTradeKey(signal) {
  return [
    STRATEGY.name,
    signal.symbol,
    signal.side,
    signal.bar_open_time_ms,
    signal.close_price,
  ].join("|");
}

function closeTradeKey(position, exitTime, exitReason) {
  return [
    STRATEGY.name,
    position.symbol,
    position.side,
    position.entry_bar_open_time_ms,
    exitTime,
    exitReason,
  ].join("|");
}

function closePosition(state, position, exitPrice, exitTime, exitReason) {
  const event_id = closeTradeKey(position, exitTime, exitReason);
  const grossPnlUsdt = positionPnlUsdt(position, exitPrice);
  const closeFeeUsdt = Math.abs(position.quantity * exitPrice) * PAPER.feeRatePerSide;
  const netPnlUsdt = grossPnlUsdt - closeFeeUsdt;
  state.cashUsdt += netPnlUsdt;
  ensureRiskState(state).realizedPnlTodayUsdt += netPnlUsdt;

  const trade = {
    type: "trade_closed",
    event_id,
    entry_event_id: position.event_id,
    symbol: position.symbol,
    strategy: position.strategy,
    side: position.side,
    entry_time: position.entry_time,
    exit_time: exitTime,
    entry_price: round(position.entry_price),
    exit_price: round(exitPrice),
    quantity: round(position.quantity),
    notional_usdt: round(position.notional_usdt, 2),
    planned_risk_usdt: round(position.planned_risk_usdt, 4),
    stop_distance_percent: round(position.stop_distance_percent, 4),
    open_fee_usdt: round(position.open_fee_usdt, 4),
    close_fee_usdt: round(closeFeeUsdt, 4),
    gross_pnl_usdt: round(grossPnlUsdt, 4),
    net_pnl_usdt: round(netPnlUsdt, 4),
    pnl_percent_on_notional: round((netPnlUsdt / position.notional_usdt) * 100, 4),
    exit_reason: exitReason,
  };

  delete state.positions[position.symbol];
  state.trades.push(trade);
  state.events.push({ ...trade, event_time: new Date().toISOString() });
  return trade;
}

function openPosition(state, signal, equityUsdt) {
  const riskState = ensureRiskState(state);
  const event_id = openTradeKey(signal);
  if (state.openedTradeKeys[event_id]) {
    return {
      type: "entry_skipped",
      event_id,
      symbol: signal.symbol,
      signal: signal.signal,
      reason: "Duplicate open event already emitted for this symbol and 4h candle",
    };
  }

  if (state.positions[signal.symbol]) {
    return {
      type: "entry_skipped",
      event_id,
      symbol: signal.symbol,
      signal: signal.signal,
      reason: "Position already open for this symbol",
    };
  }

  const openPositions = Object.values(state.positions);
  if (openPositions.length >= PAPER.maxOpenPositions) {
    return {
      type: "entry_skipped",
      symbol: signal.symbol,
      signal: signal.signal,
      reason: `Max open positions reached: ${PAPER.maxOpenPositions}`,
    };
  }

  if (signal.side === "SHORT" && !PAPER.allowPaperShorts) {
    return {
      type: "entry_skipped",
      symbol: signal.symbol,
      signal: signal.signal,
      reason: "Paper shorts disabled",
    };
  }

  const accountDrawdownUsdt = PAPER.initialCashUsdt - equityUsdt;
  const maxAccountDrawdownUsdt = PAPER.initialCashUsdt * PAPER.maxAccountDrawdownFraction;
  if (accountDrawdownUsdt >= maxAccountDrawdownUsdt) {
    return {
      type: "entry_skipped",
      symbol: signal.symbol,
      signal: signal.signal,
      reason: `Account drawdown guard active: ${round(accountDrawdownUsdt, 2)} USDT`,
    };
  }

  const maxDailyLossUsdt = PAPER.initialCashUsdt * PAPER.maxDailyLossFraction;
  if (riskState.realizedPnlTodayUsdt <= -maxDailyLossUsdt) {
    return {
      type: "entry_skipped",
      symbol: signal.symbol,
      signal: signal.signal,
      reason: `Daily loss guard active: ${round(riskState.realizedPnlTodayUsdt, 2)} USDT`,
    };
  }

  const stopDistanceUsdt = Math.abs(signal.close_price - signal.stop_loss_price);
  const stopDistancePercent = stopDistanceUsdt / signal.close_price;
  if (!Number.isFinite(stopDistancePercent) || stopDistancePercent <= 0) {
    return {
      type: "entry_skipped",
      symbol: signal.symbol,
      signal: signal.signal,
      reason: "Invalid stop distance for risk-based sizing",
    };
  }

  const riskBudgetUsdt = equityUsdt * PAPER.riskPerTradeFraction;
  const riskBasedNotionalUsdt = riskBudgetUsdt / stopDistancePercent;
  const maxTradeNotionalUsdt = equityUsdt * PAPER.maxTradeNotionalFraction;
  const maxTotalExposureUsdt = equityUsdt * PAPER.maxTotalExposureFraction;
  const usedNotional = totalOpenNotional(state);
  const remainingExposureUsdt = Math.max(maxTotalExposureUsdt - usedNotional, 0);
  const notionalUsdt = Math.min(
    riskBasedNotionalUsdt,
    maxTradeNotionalUsdt,
    remainingExposureUsdt
  );

  if (notionalUsdt < PAPER.minTradeNotionalUsdt) {
    return {
      type: "entry_skipped",
      symbol: signal.symbol,
      signal: signal.signal,
      reason: `Risk-sized notional ${round(notionalUsdt, 2)} USDT is below minimum ${PAPER.minTradeNotionalUsdt} USDT`,
    };
  }

  const quantity = notionalUsdt / signal.close_price;
  const openFeeUsdt = notionalUsdt * PAPER.feeRatePerSide;
  const plannedRiskUsdt = notionalUsdt * stopDistancePercent;
  state.cashUsdt -= openFeeUsdt;
  riskState.tradesOpenedToday += 1;

  const position = {
    event_id,
    symbol: signal.symbol,
    strategy: signal.strategy,
    side: signal.side,
    entry_time: signal.bar_close_time,
    entry_bar_open_time_ms: signal.bar_open_time_ms,
    entry_price: signal.close_price,
    quantity,
    notional_usdt: notionalUsdt,
    planned_risk_usdt: plannedRiskUsdt,
    risk_budget_usdt: riskBudgetUsdt,
    stop_distance_percent: stopDistancePercent * 100,
    stop_loss_price: signal.stop_loss_price,
    take_profit_price: signal.take_profit_price,
    max_hold_bars: signal.max_hold_bars,
    open_fee_usdt: openFeeUsdt,
    opened_at: new Date().toISOString(),
  };

  state.positions[signal.symbol] = position;

  const event = {
    type: "trade_opened",
    event_id,
    symbol: signal.symbol,
    strategy: signal.strategy,
    side: signal.side,
    entry_time: position.entry_time,
    entry_price: round(position.entry_price),
    quantity: round(position.quantity),
    notional_usdt: round(position.notional_usdt, 2),
    planned_risk_usdt: round(position.planned_risk_usdt, 4),
    risk_budget_usdt: round(position.risk_budget_usdt, 4),
    stop_distance_percent: round(position.stop_distance_percent, 4),
    stop_loss_price: round(position.stop_loss_price),
    take_profit_price: round(position.take_profit_price),
    open_fee_usdt: round(openFeeUsdt, 4),
  };

  state.openedTradeKeys[event_id] = new Date().toISOString();
  state.events.push({ ...event, event_time: new Date().toISOString() });
  return event;
}

function maybeExitPosition(state, signal) {
  const position = state.positions[signal.symbol];
  if (!position) return null;

  let exitPrice = null;
  let exitReason = null;

  if (position.side === "LONG") {
    if (signal.low_price <= position.stop_loss_price) {
      exitPrice = position.stop_loss_price;
      exitReason = "stop_loss";
    } else if (signal.high_price >= position.take_profit_price) {
      exitPrice = position.take_profit_price;
      exitReason = "take_profit";
    }
  } else {
    if (signal.high_price >= position.stop_loss_price) {
      exitPrice = position.stop_loss_price;
      exitReason = "stop_loss";
    } else if (signal.low_price <= position.take_profit_price) {
      exitPrice = position.take_profit_price;
      exitReason = "take_profit";
    }
  }

  const heldBars = Math.floor(
    (signal.bar_open_time_ms - position.entry_bar_open_time_ms) / STRATEGY.timeframeMs
  );

  if (!exitReason && heldBars >= position.max_hold_bars) {
    exitPrice = signal.close_price;
    exitReason = "time_exit";
  }

  if (!exitReason) return null;
  return closePosition(state, position, exitPrice, signal.bar_close_time, exitReason);
}

function marksFromSignals(signals) {
  const marks = {};
  for (const signal of signals) {
    if (signal.symbol && Number.isFinite(signal.close_price)) {
      marks[signal.symbol] = signal.close_price;
    }
  }
  return marks;
}

function accountSummary(state, signals) {
  const riskState = ensureRiskState(state);
  const marks = marksFromSignals(signals);
  const positions = Object.values(state.positions);
  const unrealizedPnlUsdt = positions.reduce(
    (sum, position) => sum + positionPnlUsdt(position, marks[position.symbol] || position.entry_price),
    0
  );
  const equityUsdt = state.cashUsdt + unrealizedPnlUsdt;
  const closedNetPnlUsdt = state.trades.reduce((sum, trade) => sum + trade.net_pnl_usdt, 0);

  return {
    type: "paper_account",
    strategy: STRATEGY.name,
    cash_usdt: round(state.cashUsdt, 2),
    equity_usdt: round(equityUsdt, 2),
    unrealized_pnl_usdt: round(unrealizedPnlUsdt, 2),
    closed_net_pnl_usdt: round(closedNetPnlUsdt, 2),
    closed_trades: state.trades.length,
    open_positions: positions.length,
    open_position_symbols: positions.map((position) => position.symbol),
    capital_base_usdt: PAPER.initialCashUsdt,
    risk_per_trade_usdt: round(equityUsdt * PAPER.riskPerTradeFraction, 4),
    risk_per_trade_percent: round(PAPER.riskPerTradeFraction * 100, 2),
    total_open_notional_usdt: round(totalOpenNotional(state), 2),
    total_open_risk_usdt: round(totalOpenRiskUsdt(state), 4),
    max_total_exposure_usdt: round(equityUsdt * PAPER.maxTotalExposureFraction, 2),
    daily_realized_pnl_usdt: round(riskState.realizedPnlTodayUsdt, 4),
    trades_opened_today: riskState.tradesOpenedToday,
    daily_loss_limit_usdt: round(PAPER.initialCashUsdt * PAPER.maxDailyLossFraction, 2),
    account_drawdown_limit_usdt: round(PAPER.initialCashUsdt * PAPER.maxAccountDrawdownFraction, 2),
    updated_at: new Date().toISOString(),
  };
}

const staticData = $getWorkflowStaticData("global");
const state = initState(staticData);

const signals = [];
const parseErrors = [];
const groupedKlines = normalizeInputItems(items);

for (const group of groupedKlines) {
  try {
    const { symbol, klines } = group.symbol ? group : getKlinesFromItem(group);
    const bars = parseBinanceKlines(klines);
    signals.push(buildSignal(symbol, bars));
  } catch (error) {
    parseErrors.push({
      type: "error",
      message: error.message,
      item: group,
    });
  }
}

if (groupedKlines.length === 0) {
  parseErrors.push({
    type: "error",
    message:
      "No Binance klines found. In the HTTP Request node, set Options > Put Response in Field to 'klines'.",
  });
}

const events = [];
const freshSignals = [];

for (const signal of signals) {
  if (!signal.bar_open_time_ms) {
    freshSignals.push(signal);
    continue;
  }

  const lastProcessed = state.lastProcessedBarBySymbol[signal.symbol];
  if (lastProcessed === signal.bar_open_time_ms) {
    freshSignals.push({
      ...signal,
      already_processed: true,
      paper_action: "none",
      paper_reason: "This 4h bar was already processed",
    });
    continue;
  }

  const exitEvent = maybeExitPosition(state, signal);
  if (exitEvent) events.push(exitEvent);

  const summaryBeforeEntry = accountSummary(state, signals);
  if (signal.actionable && !state.positions[signal.symbol]) {
    const entryEvent = openPosition(state, signal, summaryBeforeEntry.equity_usdt);
    events.push(entryEvent);
    freshSignals.push({
      ...signal,
      paper_action: entryEvent.type,
      paper_reason: entryEvent.reason || null,
    });
  } else {
    freshSignals.push({
      ...signal,
      paper_action: exitEvent ? exitEvent.type : "none",
      paper_reason: signal.actionable ? "Position already open or exit handled first" : null,
    });
  }

  state.lastProcessedBarBySymbol[signal.symbol] = signal.bar_open_time_ms;
}

const summary = accountSummary(state, freshSignals);
const openPositions = Object.values(state.positions).map((position) => ({
  type: "open_position",
  ...position,
  entry_price: round(position.entry_price),
  quantity: round(position.quantity),
  notional_usdt: round(position.notional_usdt, 2),
  planned_risk_usdt: round(position.planned_risk_usdt, 4),
  risk_budget_usdt: round(position.risk_budget_usdt, 4),
  stop_distance_percent: round(position.stop_distance_percent, 4),
  stop_loss_price: round(position.stop_loss_price),
  take_profit_price: round(position.take_profit_price),
  open_fee_usdt: round(position.open_fee_usdt, 4),
}));

function withAccountColumns(row) {
  return {
    cash_usdt: summary.cash_usdt,
    equity_usdt: summary.equity_usdt,
    unrealized_pnl_usdt: summary.unrealized_pnl_usdt,
    closed_net_pnl_usdt: summary.closed_net_pnl_usdt,
    closed_trades: summary.closed_trades,
    open_positions: summary.open_positions,
    capital_base_usdt: summary.capital_base_usdt,
    risk_per_trade_usdt: summary.risk_per_trade_usdt,
    risk_per_trade_percent: summary.risk_per_trade_percent,
    total_open_notional_usdt: summary.total_open_notional_usdt,
    total_open_risk_usdt: summary.total_open_risk_usdt,
    max_total_exposure_usdt: summary.max_total_exposure_usdt,
    daily_realized_pnl_usdt: summary.daily_realized_pnl_usdt,
    daily_loss_limit_usdt: summary.daily_loss_limit_usdt,
    ...row,
  };
}

return [
  { json: summary },
  ...freshSignals.map((signal) => ({ json: withAccountColumns({ type: "signal", ...signal }) })),
  ...events.map((event) => ({ json: withAccountColumns(event) })),
  ...openPositions.map((position) => ({ json: withAccountColumns(position) })),
  ...parseErrors.map((error) => ({ json: withAccountColumns(error) })),
];
