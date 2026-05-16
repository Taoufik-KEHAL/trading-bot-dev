# Trading Bot Development

Production-ready research and n8n automation for a Binance crypto paper-trading bot. The current deployed workflow scans ten USDT pairs on 4-hour candles, evaluates a risk-managed trend-pullback strategy, stores paper-trading state, optionally journals trades to Postgres, and sends Telegram alerts when paper trades open or close.

This project is paper trading only. It does not place real Binance orders.

## Current Strategy

Selected strategy:

```text
trend_pullback_4h_sl1.8_rr1.5_hold12_rsi35-65
```

Summary:

- Timeframe: completed `4h` Binance klines
- Trend filter: EMA50 versus EMA200
- Pullback trigger: RSI14 cross back above `35` for longs, below `65` for shorts
- Stop loss: `1.8 * ATR14`
- Take profit: `1.5R`
- Time exit: `12` 4h candles, or `48` hours
- Paper capital: `100 USDT`
- Risk per trade: `1%` of equity
- Max open positions: `2`
- Max total exposure: `60%` of equity

Full strategy documentation: [docs/strategy_details.md](docs/strategy_details.md)

## Tech Stack

- n8n for scheduling, workflow orchestration, paper-state handling, and notifications
- Binance public market-data API for 4h klines
- Postgres for n8n storage and optional paper trade journaling
- Telegram Bot API through n8n credentials for alerts
- Docker Compose for local and droplet deployment
- Caddy for HTTPS reverse proxy on the droplet
- Python research scripts for database backtesting and strategy selection

Full architecture documentation: [docs/tech_stack.md](docs/tech_stack.md)

## Repository Layout

```text
deploy/droplet/   Production Docker Compose, Caddy, and backup scripts
docs/             Deployment, strategy, stack, and trade-journal docs
scripts/          Python research, history download, and backtest scripts
sql/              Postgres schema for paper trade journaling
strategies/       Selected strategy configuration
workflows/        n8n Code node scripts and importable workflow templates
```

## Environment Variables

Secrets are centralized in `.env`, which is intentionally ignored by Git.

Start from:

```bash
cp .env.example .env
```

Required values:

```env
POSTGRES_HOST=127.0.0.1
POSTGRES_PORT=5433
POSTGRES_DB=trading_bot
POSTGRES_USER=trading_bot
POSTGRES_PASSWORD=replace_with_a_long_random_password
N8N_ENCRYPTION_KEY=replace_with_32_plus_random_chars
TELEGRAM_CHAT_ID=replace_with_your_chat_id
```

Never commit `.env` or exported n8n credentials.

## n8n Workflow

Latest recommended workflow template:

```text
workflows/market_scanner_dev_latest_4h_postgres_journal.workflow.json
```

Workflow shape:

```text
Schedule Trigger
-> Build Binance requests
-> HTTP Request
-> Paper trade code
   -> Telegram alert formatter code
      -> Send Telegram alert
   -> Build Postgres persistence queries
      -> Persist paper state to Postgres
```

The workflow templates in this repository are sanitized. After importing into n8n, select your Telegram and Postgres credentials in the UI.

## Local Development

Start local n8n and Postgres:

```bash
cp docker-compose.example.yml docker-compose.yml
docker compose up -d
```

Run the latest lower-frequency strategy research:

```bash
python3 scripts/pro_strategy_research.py \
  --timeframes 1h,4h \
  --min-train-trades 30 \
  --min-test-trades 10
```

Dry-run current signals from local Postgres candles:

```bash
python3 scripts/pro_signal_bot.py
```

## Postgres Trade Journal

Run the schema once:

```bash
psql "$DATABASE_URL" < sql/paper_trading_schema.sql
```

Or on the droplet, follow:

[docs/postgres_trade_journal.md](docs/postgres_trade_journal.md)

Useful open-trade query:

```sql
SELECT symbol, side, entry_time, entry_price, notional_usdt,
       stop_loss_price, take_profit_price, last_seen_at
FROM public.paper_positions
WHERE status = 'OPEN'
ORDER BY entry_time DESC;
```

## Droplet Deployment

Production deployment guide:

[docs/deploy_on_droplet.md](docs/deploy_on_droplet.md)

Deployment stack:

```text
Ubuntu droplet
Docker Compose
Postgres
n8n
Caddy HTTPS reverse proxy
```

The live `docker-compose.yml` files are ignored by Git. Start from the tracked
`docker-compose.example.yml` templates, then edit the local copies only on the
machine where they run.

## Security Checklist Before GitHub

- `.env` is ignored by `.gitignore`
- Python scripts read Postgres credentials from environment variables
- n8n workflow templates are sanitized and do not include credential IDs
- Telegram bot tokens are stored only in n8n credentials
- Postgres is not exposed publicly in production compose
- Generated reports and charts are ignored

## Important Risk Notice

This repository is for research and paper trading. Backtest performance is not a guarantee of future returns. Before any real execution is added, implement exchange-specific order filters, live stop management, reconciliation, slippage controls, API-key permission restrictions, and emergency kill switches.
