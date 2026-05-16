/**
 * n8n Code node script: Professional 4h trend-pullback strategy.
 *
 * Code node mode: "Run Once for All Items"
 *
 * Expected input items from a Postgres node:
 * {
 *   symbol: "BTCUSDT",
 *   open_time: "2026-05-16 12:00:00",
 *   open: "78200.00",
 *   high: "78500.00",
 *   low: "77900.00",
 *   close: "78238.67",
 *   volume: "123.45"
 * }
 *
 * Recommended SQL before this node:
 *
 * SELECT symbol, open_time, open, high, low, close, volume
 * FROM public.market_candles
 * WHERE timeframe = '1m'
 *   AND open_time >= now() - interval '60 days'
 *   AND symbol IN (
 *     'ADAUSDT','AVAXUSDT','BNBUSDT','BTCUSDT','DOGEUSDT',
 *     'DOTUSDT','ETHUSDT','LINKUSDT','SOLUSDT','XRPUSDT'
 *   )
 * ORDER BY symbol, open_time;
 */

const STRATEGY = {
  name: "trend_pullback_4h_sl1.8_rr1.5_hold12_rsi35-65",
  timeframeHours: 4,
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

function toNumber(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function parseDbTime(value) {
  if (value instanceof Date) return value.getTime();
  const text = String(value).trim();
  const match = text.match(
    /^(\d{4})-(\d{2})-(\d{2})(?:[ T])(\d{2}):(\d{2})(?::(\d{2}))?/
  );
  if (!match) {
    const parsed = Date.parse(text);
    if (Number.isNaN(parsed)) throw new Error(`Invalid open_time: ${value}`);
    return parsed;
  }
  const [, year, month, day, hour, minute, second = "0"] = match;
  return Date.UTC(
    Number(year),
    Number(month) - 1,
    Number(day),
    Number(hour),
    Number(minute),
    Number(second)
  );
}

function isoNoMs(timestampMs) {
  return new Date(timestampMs).toISOString().replace(".000Z", "Z");
}

function barStart4h(timestampMs) {
  const d = new Date(timestampMs);
  const hour = d.getUTCHours();
  const startHour = Math.floor(hour / STRATEGY.timeframeHours) * STRATEGY.timeframeHours;
  return Date.UTC(
    d.getUTCFullYear(),
    d.getUTCMonth(),
    d.getUTCDate(),
    startHour,
    0,
    0
  );
}

function groupRowsBySymbol(inputItems) {
  const grouped = new Map();
  for (const item of inputItems) {
    const row = item.json || item;
    const symbol = String(row.symbol || "").toUpperCase();
    if (!symbol || !SYMBOLS.includes(symbol)) continue;

    const candle = {
      symbol,
      time: parseDbTime(row.open_time),
      open: toNumber(row.open),
      high: toNumber(row.high),
      low: toNumber(row.low),
      close: toNumber(row.close),
      volume: toNumber(row.volume),
    };

    if (
      candle.open === null ||
      candle.high === null ||
      candle.low === null ||
      candle.close === null ||
      candle.volume === null
    ) {
      continue;
    }

    if (!grouped.has(symbol)) grouped.set(symbol, []);
    grouped.get(symbol).push(candle);
  }

  for (const rows of grouped.values()) rows.sort((a, b) => a.time - b.time);
  return grouped;
}

function resampleTo4h(rows) {
  const barsByStart = new Map();

  for (const row of rows) {
    const start = barStart4h(row.time);
    let bar = barsByStart.get(start);
    if (!bar) {
      bar = {
        time: start,
        open: row.open,
        high: row.high,
        low: row.low,
        close: row.close,
        volume: row.volume,
        firstTime: row.time,
        lastTime: row.time,
      };
      barsByStart.set(start, bar);
      continue;
    }

    if (row.time < bar.firstTime) {
      bar.open = row.open;
      bar.firstTime = row.time;
    }
    if (row.time > bar.lastTime) {
      bar.close = row.close;
      bar.lastTime = row.time;
    }
    bar.high = Math.max(bar.high, row.high);
    bar.low = Math.min(bar.low, row.low);
    bar.volume += row.volume;
  }

  return [...barsByStart.values()].sort((a, b) => a.time - b.time);
}

function latestCompleteBarIndex(bars, latestOneMinuteOpen) {
  const timeframeMs = STRATEGY.timeframeHours * 60 * 60 * 1000;
  const dataKnownUntil = latestOneMinuteOpen + 60 * 1000;
  let idx = -1;

  for (let i = 0; i < bars.length; i += 1) {
    if (bars[i].time + timeframeMs <= dataKnownUntil) idx = i;
  }

  return idx;
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
    if (avgLoss[i] === 0) {
      out[i] = 100;
    } else {
      const rs = avgGain[i] / avgLoss[i];
      out[i] = 100 - 100 / (1 + rs);
    }
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

function buildSignal(symbol, rows) {
  const bars = resampleTo4h(rows);
  const latestOneMinuteOpen = rows[rows.length - 1]?.time;
  const idx = latestCompleteBarIndex(bars, latestOneMinuteOpen);

  if (idx < 1) {
    return {
      symbol,
      signal: "HOLD",
      reason: "Not enough complete 4h candles",
    };
  }

  const closes = bars.map((bar) => bar.close);
  const emaFast = ema(closes, STRATEGY.emaFast);
  const emaSlow = ema(closes, STRATEGY.emaSlow);
  const rsi14 = rsi(closes, STRATEGY.rsiPeriod);
  const atr14 = atr(bars, STRATEGY.atrPeriod);

  const bar = bars[idx];
  const prevRsi = rsi14[idx - 1];
  const currentRsi = rsi14[idx];
  const currentEmaFast = emaFast[idx];
  const currentEmaSlow = emaSlow[idx];
  const currentAtr = atr14[idx];

  if (
    currentRsi === null ||
    prevRsi === null ||
    currentEmaFast === null ||
    currentEmaSlow === null ||
    currentAtr === null ||
    currentAtr <= 0
  ) {
    return {
      symbol,
      strategy: STRATEGY.name,
      timeframe: "4h",
      bar_time: isoNoMs(bar.time),
      close_price: bar.close,
      signal: "HOLD",
      reason: "Indicators are still warming up",
    };
  }

  const longSignal =
    currentEmaFast > currentEmaSlow &&
    bar.close > currentEmaSlow &&
    prevRsi < STRATEGY.rsiLow &&
    currentRsi >= STRATEGY.rsiLow;

  const shortSignal =
    currentEmaFast < currentEmaSlow &&
    bar.close < currentEmaSlow &&
    prevRsi > STRATEGY.rsiHigh &&
    currentRsi <= STRATEGY.rsiHigh;

  let signal = "HOLD";
  let stopLoss = null;
  let takeProfit = null;
  let score = 0;

  if (longSignal) {
    signal = "BUY";
    const risk = currentAtr * STRATEGY.stopAtr;
    stopLoss = bar.close - risk;
    takeProfit = bar.close + risk * STRATEGY.rewardRisk;
    score = Math.abs(currentRsi - STRATEGY.rsiLow);
  }

  if (shortSignal) {
    signal = "SELL";
    const risk = currentAtr * STRATEGY.stopAtr;
    stopLoss = bar.close + risk;
    takeProfit = bar.close - risk * STRATEGY.rewardRisk;
    score = Math.abs(currentRsi - STRATEGY.rsiHigh);
  }

  return {
    symbol,
    strategy: STRATEGY.name,
    timeframe: "4h",
    bar_time: isoNoMs(bar.time),
    close_price: Number(bar.close.toFixed(8)),
    rsi: Number(currentRsi.toFixed(4)),
    ema50: Number(currentEmaFast.toFixed(8)),
    ema200: Number(currentEmaSlow.toFixed(8)),
    atr14: Number(currentAtr.toFixed(8)),
    signal,
    score: Number(score.toFixed(4)),
    stop_loss_price: stopLoss === null ? null : Number(stopLoss.toFixed(8)),
    take_profit_price: takeProfit === null ? null : Number(takeProfit.toFixed(8)),
    max_hold_bars: STRATEGY.maxHoldBars,
    max_hold_hours: STRATEGY.maxHoldBars * STRATEGY.timeframeHours,
    reason:
      signal === "HOLD"
        ? "No entry: waiting for EMA trend + RSI pullback cross"
        : `${signal}: EMA trend confirmed and RSI crossed pullback threshold`,
  };
}

const grouped = groupRowsBySymbol(items);
const output = [];

for (const symbol of SYMBOLS) {
  const rows = grouped.get(symbol) || [];
  if (rows.length === 0) {
    output.push({
      json: {
        symbol,
        strategy: STRATEGY.name,
        timeframe: "4h",
        signal: "HOLD",
        reason: "No candle rows received for symbol",
      },
    });
    continue;
  }

  output.push({ json: buildSignal(symbol, rows) });
}

return output;
