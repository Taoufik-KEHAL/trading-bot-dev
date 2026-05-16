# Strategy Details

## Strategy Name

```text
trend_pullback_4h_sl1.8_rr1.5_hold12_rsi35-65
```

This is a 4-hour trend-pullback strategy with ATR-based exits and risk-managed paper sizing.

It is designed to trade only when the market is already trending, then wait for RSI to recover from a pullback before entering.

## Market Universe

The strategy scans ten Binance USDT pairs:

- ADAUSDT
- AVAXUSDT
- BNBUSDT
- BTCUSDT
- DOGEUSDT
- DOTUSDT
- ETHUSDT
- LINKUSDT
- SOLUSDT
- XRPUSDT

## Timeframe

Signal timeframe:

```text
4h
```

The workflow may run every 15 minutes, but the strategy only evaluates completed 4h candles. It ignores the current unfinished 4h candle.

## Indicators

### EMA50

Used as the faster trend average.

### EMA200

Used as the slower regime filter.

### RSI14

Used to detect pullback recovery.

### ATR14

Used to calculate stop-loss distance.

## Long Entry Rules

A LONG paper trade opens when all conditions are true on the latest completed 4h candle:

```text
EMA50 > EMA200
close > EMA200
previous RSI14 < 35
current RSI14 >= 35
```

Interpretation:

The pair is in an uptrend, price is still above the long-term average, RSI was recently weak, and RSI has just recovered above the pullback threshold.

## Short Entry Rules

A SHORT paper trade opens when all conditions are true on the latest completed 4h candle:

```text
EMA50 < EMA200
close < EMA200
previous RSI14 > 65
current RSI14 <= 65
```

Interpretation:

The pair is in a downtrend, price is below the long-term average, RSI was recently strong, and RSI has just rolled back below the pullback threshold.

## Exit Rules

Every open paper trade is checked each time the workflow runs.

### Stop Loss

Stop distance:

```text
1.8 * ATR14
```

For LONG:

```text
stop = entry price - 1.8 * ATR14
```

For SHORT:

```text
stop = entry price + 1.8 * ATR14
```

### Take Profit

Reward-to-risk:

```text
1.5R
```

For LONG:

```text
target = entry price + stop distance * 1.5
```

For SHORT:

```text
target = entry price - stop distance * 1.5
```

### Time Exit

If neither stop nor target is hit after 12 completed 4h bars, the paper trade exits at the latest close.

```text
12 bars * 4h = 48 hours
```

## Intrabar Assumption

For paper exits, the bot uses 4h candle high and low.

If stop and target are both touched inside the same 4h candle, the strategy assumes the stop was hit first. This is conservative.

For real trading, stops and targets should be monitored with live price or exchange orders, not only 4h candles.

## Money Management

Paper capital:

```text
100 USDT
```

Risk per trade:

```text
1% of equity
```

At 100 USDT equity, planned risk is about:

```text
1 USDT per trade
```

Position size is calculated from the stop distance:

```text
risk budget = equity * 0.01
stop distance % = abs(entry - stop) / entry
risk-sized notional = risk budget / stop distance %
```

Then the bot caps position size using exposure limits.

## Exposure Limits

Maximum notional per trade:

```text
25% of equity
```

At 100 USDT equity:

```text
max 25 USDT per trade
```

Maximum total open exposure:

```text
60% of equity
```

At 100 USDT equity:

```text
max 60 USDT open exposure
```

Maximum open positions:

```text
2
```

Minimum trade notional:

```text
10 USDT
```

If the risk-sized position is below 10 USDT, the entry is skipped.

## Loss Guards

Daily realized loss guard:

```text
3% of starting capital
```

At 100 USDT:

```text
stop opening new trades after -3 USDT realized daily loss
```

Account drawdown guard:

```text
10% of starting capital
```

At 100 USDT:

```text
stop opening new trades after -10 USDT account drawdown
```

These guards prevent new entries. Existing open paper positions can still be managed and closed.

## Fees

Paper fee model:

```text
0.05% per side
```

Round trip:

```text
0.10%
```

Fees are deducted from paper cash when trades open and close.

## Backtest Summary

The selected strategy came from a lower-frequency search over the one-year candle database.

The stronger result came from 4h trend pullbacks. Shorter 5m and 15m strategies were rejected because they did not survive the cost model well.

Selected 4h candidate:

- Train trades: 89
- Train profit factor: 2.70
- Train return model result: 16.33%
- Train max drawdown: -2.93%
- Test trades: 50
- Test profit factor: 6.66
- Test return model result: 15.46%
- Test max drawdown: -0.90%

These are research results, not a guarantee of future performance.

## Operational Behavior

Each workflow run:

1. Fetches recent Binance 4h candles for all symbols.
2. Ignores the unfinished current 4h candle.
3. Builds EMA50, EMA200, RSI14, and ATR14.
4. Checks existing open paper positions for stop, target, or time exit.
5. Checks for new entry signals.
6. Applies risk and exposure limits.
7. Updates paper state in n8n static workflow data.
8. Sends Telegram alerts only for opened or closed paper trades.

## Practical Notes

- Paper shorts are allowed in the simulation.
- Binance Spot cannot short without margin, so this should remain paper trading until execution venue is explicitly chosen.
- For live execution, add exchange-specific quantity filters, minimum notional filters, order-status reconciliation, slippage assumptions, and hard kill switches.
