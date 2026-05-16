# Tech Stack

## Purpose

This system runs a Binance market scanner and paper-trading bot in n8n. It fetches live public Binance candle data, evaluates the selected 4h trend-pullback strategy, tracks a paper account, and sends Telegram alerts when paper trades open or close.

No real Binance orders are placed.

## Runtime Architecture

Production deployment on the droplet:

```text
DigitalOcean Droplet / Ubuntu
-> Docker Compose
-> Caddy reverse proxy with HTTPS
-> n8n workflow engine
-> Postgres database
-> Binance public market-data API
-> Telegram bot alerts
```

Main workflow:

```text
Schedule Trigger
-> Build Binance requests
-> HTTP Request
-> Paper trade code
-> Telegram alert formatter code
-> Telegram Send Message
```

## Components

### n8n

n8n is the automation engine. It schedules the bot, calls Binance, runs the strategy code, keeps paper-trading state, and sends Telegram messages.

Important n8n files:

- `workflows/binance_make_kline_requests_n8n.js`
- `workflows/binance_paper_trader_from_klines_n8n.js`
- `workflows/telegram_alert_formatter_n8n.js`
- `workflows/market_scanner_dev_latest_4h_100usdt_risk_managed.workflow.json`

### Binance Market Data

The workflow uses Binance public kline data:

```text
GET https://data-api.binance.vision/api/v3/klines
```

Current request settings:

- Symbols: ADAUSDT, AVAXUSDT, BNBUSDT, BTCUSDT, DOGEUSDT, DOTUSDT, ETHUSDT, LINKUSDT, SOLUSDT, XRPUSDT
- Interval: `4h`
- Limit: `300`

No Binance API key is needed for paper trading.

### Paper Trading State

Paper-trading state is stored in n8n workflow static data:

```text
paperTradingBinanceV1
```

It stores:

- Paper cash
- Open positions
- Closed trades
- Event history
- Last processed 4h bar per symbol
- Daily risk state

This prevents duplicate entries on the same 4h candle even though the workflow runs every 15 minutes.

### Postgres

Postgres is used by n8n as its main database in production. Optional paper-trading history tables are defined in:

```text
sql/paper_trading_schema.sql
```

The workflow can also persist opened trades, closed trades, open positions, account snapshots, and raw trade events. The persistence helper is:

```text
workflows/postgres_persistence_queries_n8n.js
```

Full instructions and inspection SQL are in:

```text
docs/postgres_trade_journal.md
```

### Telegram

Telegram alerts are generated only for important events:

- `trade_opened`
- `trade_closed`

Normal HOLD signals do not send Telegram messages.

Telegram message formatting is handled by:

```text
workflows/telegram_alert_formatter_n8n.js
```

## Deployment Stack

Production deployment files:

- `deploy/droplet/docker-compose.yml`
- `deploy/droplet/Caddyfile`
- `deploy/droplet/.env.example`
- `deploy/droplet/backup.sh`
- `docs/deploy_on_droplet.md`

Docker services:

- `postgres`: n8n database
- `n8n`: workflow runtime
- `caddy`: HTTPS reverse proxy

Required public ports:

- `80/tcp`
- `443/tcp`

Postgres is private inside Docker and should not be exposed to the internet.

## Schedule

The workflow is currently intended to run every 15 minutes.

The strategy itself uses completed 4h candles only. Running every 15 minutes gives timely monitoring while dedupe logic prevents repeated processing of the same completed candle.

## Security Notes

- Keep `N8N_ENCRYPTION_KEY` backed up permanently.
- Store Telegram credentials in n8n credentials, not in code.
- Do not expose Postgres publicly.
- Keep the workflow as paper trading until live execution is designed separately.
- For live trading, add exchange-key permissions, order safety checks, exchange-specific sizing filters, and emergency kill switches before placing any real orders.

## Operations

Manual backup on the droplet:

```bash
cd /opt/trading-bot/droplet
set -a
. ./.env
set +a
./backup.sh
```

Upgrade:

```bash
cd /opt/trading-bot/droplet
docker compose pull
docker compose up -d
```

Reset paper account:

In the paper-trader Code node, set:

```js
const RESET_PAPER_STATE = true;
```

Run once, then set it back:

```js
const RESET_PAPER_STATE = false;
```
