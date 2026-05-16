# Professional Trading Bot Setup

## Data Source

- Source table: `public.market_candles`
- Data used: ten USDT crypto pairs, 1-minute candles
- Range tested: `2025-05-16 17:12:00` to `2026-05-16 18:18:00`
- Data quality check: no duplicate minute keys and no missing 1-minute gaps per symbol

## Selected Strategy

Config file: `strategies/pro_best_strategy.json`

Selected candidate:

- Family: `trend_pullback`
- Signal timeframe: `4h`
- Trend filter:
  - Long regime: EMA50 above EMA200 and close above EMA200
  - Short regime: EMA50 below EMA200 and close below EMA200
- Entry:
  - Long: RSI14 crosses back above `35`
  - Short: RSI14 crosses back below `65`
- Entry price in backtest: next 4h candle open
- Stop: `1.8 * ATR14`
- Take profit: `1.5R`
- Time exit: 12 bars, or 48 hours
- Cost model: 10 bps round trip per trade
- Position model in reports: 10% capital fraction per trade

## Validation Result

Train period: `2025-05-16 17:12:00` to `2026-01-27 05:58:00`

- Trades: 89
- Win rate: 67.42%
- Profit factor: 2.70
- Return model result: 16.33%
- Max drawdown: -2.93%
- Daily Sharpe: 2.73

Test period: `2026-01-27 05:58:00` to `2026-05-16 18:18:00`

- Trades: 50
- Win rate: 76.00%
- Profit factor: 6.66
- Return model result: 15.46%
- Max drawdown: -0.90%
- Daily Sharpe: 4.30

This is a research result, not permission to go live. Paper trade first, and only connect exchange order execution after limits, key permissions, and kill-switch behavior are explicit.

## Files Created

- `scripts/pro_strategy_research.py`: loads candles from Postgres, searches strategy candidates, writes reports and selected JSON.
- `scripts/pro_signal_bot.py`: reads the selected JSON, computes current signals, and optionally writes to `public.strategy_signals`.
- `reports/pro_strategy_candidates.csv`: all candidates from the last optimization run.
- `reports/pro_strategy_top_candidates.csv`: top candidates by in-sample score.
- `reports/pro_strategy_best_summary.csv`: selected candidate summary.
- `reports/pro_strategy_best_trades.csv`: trade ledger for selected candidate.
- `strategies/pro_best_strategy.json`: bot-ready strategy configuration.

## Commands

Run the current lower-frequency research pass:

```bash
python3 scripts/pro_strategy_research.py --timeframes 1h,4h --min-train-trades 30 --min-test-trades 10
```

Dry-run latest signals:

```bash
python3 scripts/pro_signal_bot.py
```

Write latest signals into `public.strategy_signals`:

```bash
python3 scripts/pro_signal_bot.py --write-db
```

For automation, schedule the signal bot shortly after each 4h candle closes using the same timestamp convention as the database.
