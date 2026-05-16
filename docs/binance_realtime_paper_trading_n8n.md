# Binance Realtime Paper Trading in n8n

This workflow runs the selected 4h strategy from live Binance market data and keeps a paper account in n8n workflow static data.

It does not place real Binance orders.

## Nodes

1. **Schedule Trigger**
   - Run every 15 minutes, or every 4 hours around `00:05`, `04:05`, `08:05`, `12:05`, `16:05`, `20:05` UTC.
   - The paper trader only processes each completed 4h candle once, so running every 15 minutes is safe.

2. **Code: Build Binance Requests**
   - Mode: `Run Once for All Items`
   - Paste: `workflows/binance_make_kline_requests_n8n.js`

3. **HTTP Request: Binance Klines**
   - Method: `GET`
   - URL: `={{$json.url}}`
   - Response format: `JSON`
   - Expected output in your n8n version: `3000 items`, because Binance returns 300 kline rows for each of 10 symbols and n8n splits the top-level arrays.
   - This is okay. The next Code node regroups those 3000 rows into the 10 original symbols.
   - Continue on fail: optional, but useful during paper trading.

4. **Code: Strategy + Paper Trader**
   - Mode: `Run Once for All Items`
   - Paste: `workflows/binance_paper_trader_from_klines_n8n.js`

## Optional Postgres Persistence

Run once:

```sql
\i sql/paper_trading_schema.sql
```

Then add IF/Postgres nodes after the paper trader:

- `type = paper_account`: insert into `paper_account_snapshots`
- `type = signal`: insert into `paper_signals`
- `type = trade_opened`, `trade_closed`, or `entry_skipped`: insert into `paper_trade_events`

For quick deployment, the Code node already persists paper cash, positions, and trade history in n8n static workflow data under:

```text
paperTradingBinanceV1
```

To reset the paper account, change this line in `binance_paper_trader_from_klines_n8n.js`, run once, then change it back:

```js
const RESET_PAPER_STATE = true;
```

## Paper Trading Assumptions

- Starting cash: `10000 USDT`
- Position size: `10%` of paper equity, capped at `1000 USDT`
- Max open positions: `3`
- Fee model: `0.05%` per side, or `10 bps` round trip
- Entry price: close of the completed 4h candle, used as a practical paper approximation
- Exit rules:
  - Stop loss first if both stop and target are touched in the same candle
  - Take profit second
  - Time exit after `12` 4h bars, or `48` hours

## Important

This strategy can produce paper shorts. Binance Spot cannot short without margin. Keep it as paper trading unless you intentionally rebuild execution for Spot-only longs, Margin, or Futures.
