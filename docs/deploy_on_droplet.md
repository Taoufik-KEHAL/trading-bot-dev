# Deploy the n8n Paper-Trading Bot on a Droplet

This deploys n8n, Postgres, and HTTPS on a Linux droplet using Docker Compose.

The bot is still paper trading only. It uses Binance public kline data and Telegram alerts.

## 1. Create the Droplet

Recommended baseline:

- Ubuntu LTS
- 1 vCPU / 1 GB RAM minimum
- 2 GB RAM is more comfortable for n8n
- Add an SSH key
- Point a domain/subdomain to the droplet IP, for example `n8n.example.com`

Open firewall ports:

```bash
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw enable
```

Do not expose Postgres to the internet.

## 2. Install Docker

SSH into the droplet:

```bash
ssh root@YOUR_DROPLET_IP
```

Install Docker:

```bash
apt update
apt install -y ca-certificates curl gnupg
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" > /etc/apt/sources.list.d/docker.list
apt update
apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
```

## 3. Upload the Deployment Files

From your local machine:

```bash
scp -r deploy/droplet root@YOUR_DROPLET_IP:/opt/trading-bot
scp -r workflows sql docs root@YOUR_DROPLET_IP:/opt/trading-bot/
```

On the droplet:

```bash
cd /opt/trading-bot/droplet
cp .env.example .env
cp docker-compose.example.yml docker-compose.yml
```

Edit `.env`:

```bash
nano .env
```

Set:

```text
POSTGRES_PASSWORD=long_random_password
N8N_HOST=n8n.your-domain.com
N8N_ENCRYPTION_KEY=long_random_32_plus_character_key
GENERIC_TIMEZONE=Africa/Casablanca
```

Generate an encryption key:

```bash
openssl rand -hex 32
```

Keep `N8N_ENCRYPTION_KEY` forever. If you lose it, existing n8n credentials cannot be decrypted.

## 4. Start n8n

```bash
docker compose up -d
docker compose logs -f caddy n8n
```

Open:

```text
https://n8n.your-domain.com
```

Create the first n8n owner account.

## 5. Create the Workflow

In n8n, create this workflow:

```text
Schedule Trigger
-> Code: Build Binance requests
-> HTTP Request: Binance klines
-> Code: Paper trade code
-> Code: Telegram alert formatter
-> Telegram: Send a text message
```

Use these files:

- `workflows/binance_make_kline_requests_n8n.js`
- `workflows/binance_paper_trader_from_klines_n8n.js`
- `workflows/telegram_alert_formatter_n8n.js`

HTTP Request node:

- Method: `GET`
- URL: `={{$json.url}}`
- Response Format: `JSON`

Telegram node:

- Resource: `Message`
- Operation: `Send Message`
- Chat ID: your chat id
- Text: `={{$json.telegram_text}}`
- Parse Mode: `HTML`

## 6. Schedule

Use one of these:

- Every 15 minutes
- Or every 4 hours, five minutes after candle close:

```text
00:05, 04:05, 08:05, 12:05, 16:05, 20:05 UTC
```

The paper trader deduplicates processed 4h bars, so every 15 minutes is fine.

## 7. Publish

Click **Publish** in n8n.

Run one manual execution and confirm:

- Paper trade code returns account and signal rows
- Telegram formatter returns rows only for trade events
- Telegram node sends a message when a trade opens or closes

## 8. Optional Postgres Tables

The workflow already stores paper state inside n8n static workflow data.

If you also want database tables for history:

```bash
cd /opt/trading-bot/droplet
docker compose exec -T postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" < ../sql/paper_trading_schema.sql
```

Then add Postgres insert nodes after the paper trader.

## 9. Backups

Manual backup:

```bash
cd /opt/trading-bot/droplet
set -a
. ./.env
set +a
./backup.sh
```

Daily backup cron:

```bash
crontab -e
```

Add:

```cron
15 2 * * * cd /opt/trading-bot/droplet && set -a && . ./.env && set +a && ./backup.sh >> /var/log/trading-bot-backup.log 2>&1
```

Download backups to your local machine sometimes:

```bash
scp -r root@YOUR_DROPLET_IP:/opt/trading-bot/droplet/backups ./droplet-backups
```

## 10. Upgrade

```bash
cd /opt/trading-bot/droplet
docker compose pull
docker compose up -d
docker compose logs -f n8n
```

Take a backup before upgrading.

## 11. Reset Paper State

In the paper trader Code node:

```js
const RESET_PAPER_STATE = true;
```

Run once, then set it back to:

```js
const RESET_PAPER_STATE = false;
```

## 12. Security Notes

- Keep Postgres private inside Docker.
- Do not put Telegram bot tokens in Code nodes.
- Store Telegram credentials in n8n credentials.
- Keep `N8N_ENCRYPTION_KEY` backed up.
- Use a domain with HTTPS before entering credentials.
- Do not add live Binance order execution until paper trading has proven stable.
