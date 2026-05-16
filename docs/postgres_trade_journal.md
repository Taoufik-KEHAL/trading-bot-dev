# Postgres Trade Journal

The paper trader can persist opened and closed trades in Postgres so you can inspect state outside n8n static workflow data.

## Files

- Schema: `sql/paper_trading_schema.sql`
- Query builder Code node: `workflows/postgres_persistence_queries_n8n.js`
- Importable workflow with Postgres branch: `workflows/market_scanner_dev_latest_4h_postgres_journal.workflow.json`

## n8n Shape

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

## Run Schema Once

On the droplet:

```bash
cd /opt/trading-bot/droplet
docker compose exec -T postgres psql \
  -U "$POSTGRES_USER" \
  -d "$POSTGRES_DB" \
  < ../sql/paper_trading_schema.sql
```

If the `POSTGRES_USER` variables are not loaded:

```bash
set -a
. ./.env
set +a
```

Then run the schema command again.

## Add Nodes Manually

If you do not re-import the full workflow, add two nodes after `Paper trade code`.

### Code Node

Name:

```text
Build Postgres persistence queries
```

Mode:

```text
Run Once for All Items
```

Paste:

```text
workflows/postgres_persistence_queries_n8n.js
```

### Postgres Node

Name:

```text
Persist paper state to Postgres
```

Operation:

```text
Execute Query
```

Query:

```text
={{$json.query}}
```

Use your existing Postgres credential.

## What Gets Stored

### `paper_positions`

Current and historical position state.

Important columns:

- `event_id`
- `strategy`
- `symbol`
- `side`
- `status`: `OPEN` or `CLOSED`
- `entry_time`
- `exit_time`
- `entry_price`
- `exit_price`
- `quantity`
- `notional_usdt`
- `planned_risk_usdt`
- `stop_loss_price`
- `take_profit_price`
- `net_pnl_usdt`
- `exit_reason`

### `paper_trade_events`

Raw immutable event journal for `trade_opened` and `trade_closed`.

Uses `event_id` as a unique key, so duplicate n8n runs do not insert duplicate trade events.

### `paper_account_snapshots`

One account snapshot per workflow execution.

Useful for equity history and risk monitoring.

## Useful Queries

Open trades:

```sql
SELECT
  symbol,
  side,
  entry_time,
  entry_price,
  quantity,
  notional_usdt,
  planned_risk_usdt,
  stop_loss_price,
  take_profit_price,
  last_seen_at
FROM public.paper_positions
WHERE status = 'OPEN'
ORDER BY entry_time DESC;
```

Closed trades:

```sql
SELECT
  symbol,
  side,
  entry_time,
  exit_time,
  entry_price,
  exit_price,
  net_pnl_usdt,
  pnl_percent_on_notional,
  exit_reason
FROM public.paper_positions
WHERE status = 'CLOSED'
ORDER BY exit_time DESC;
```

Latest trade events:

```sql
SELECT
  created_at,
  event_type,
  symbol,
  side,
  entry_price,
  exit_price,
  net_pnl_usdt,
  exit_reason
FROM public.paper_trade_events
ORDER BY created_at DESC
LIMIT 50;
```

Latest account state:

```sql
SELECT
  created_at,
  cash_usdt,
  equity_usdt,
  unrealized_pnl_usdt,
  closed_net_pnl_usdt,
  open_positions,
  total_open_notional_usdt,
  total_open_risk_usdt
FROM public.paper_account_snapshots
ORDER BY created_at DESC
LIMIT 20;
```

Total closed PnL by symbol:

```sql
SELECT
  symbol,
  count(*) AS trades,
  round(sum(net_pnl_usdt), 4) AS net_pnl_usdt,
  round(avg(pnl_percent_on_notional), 4) AS avg_return_percent
FROM public.paper_positions
WHERE status = 'CLOSED'
GROUP BY symbol
ORDER BY net_pnl_usdt DESC;
```

## Importable Workflow

A full workflow export with the Postgres journal branch is available here:

```text
workflows/market_scanner_dev_latest_4h_postgres_journal.workflow.json
```

Local copy for import:

```text
/home/taoufikkehal/Downloads/Market Scanner DEV - Latest 4h Paper Strategy - Postgres Journal.json
```
