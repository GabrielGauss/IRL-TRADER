# Deploying `serve` to a VPS

This runs the signal-driven stack (`trading-bot serve`) as a Docker container
behind an existing nginx reverse proxy that already terminates HTTPS on
80/443. TradingView only delivers webhooks to ports 80 and 443, so the bot
publishes no port of its own: nginx forwards `POST /signals` to it over a
shared Docker network.

```
TradingView --HTTPS--> nginx (443) --docker network--> trading-bot:8080
                                                      /data volume: SQLite DB,
                                                      audit log, kill-switch latch
```

Paths and names below match a host where the proxy is MacroPulse's
`macropulse-nginx` container on the `macropulse_default` network. Adjust if
yours differ (`PROXY_NETWORK` overrides the network name).

## 0. Prerequisites

- A DNS `A` record for your subdomain (e.g. `bot.example.com`) pointing at the
  VPS IP. Check: `dig +short bot.example.com`.
- Free disk: the image is roughly 1 GB (numpy, pandas, numba, ccxt). Check
  `df -h /` and `docker system df`; `docker image prune -a` removes images no
  container uses (review `docker images` first).

## 1. Code and secrets

```bash
git clone https://github.com/GabrielGauss/IRL-TRADER.git /opt/trading-bot
cd /opt/trading-bot
cp .env.example .env && chmod 600 .env
python3 -c "import secrets; print(secrets.token_urlsafe(32))"   # -> SIGNAL_WEBHOOK_SECRET
nano .env
```

Paper mode only needs `SIGNAL_WEBHOOK_SECRET`. Leave the Binance fields empty
until you move to `--live` (section 6).

## 2. Build and start

```bash
docker compose -f deploy/docker-compose.prod.yml up -d --build
docker ps --filter name=trading-bot          # STATUS should become (healthy)
docker logs -f trading-bot
docker exec trading-bot trading-bot status   # health + metrics, from inside
```

## 3. TLS certificate

Add only the port-80 `server` block from `deploy/nginx-bot.conf` first (with
your subdomain), reload nginx, then issue the certificate through the existing
certbot container's webroot:

```bash
docker exec macropulse-nginx nginx -t && docker exec macropulse-nginx nginx -s reload
docker run --rm \
  -v macropulse_certbot_www:/var/www/certbot \
  -v macropulse_certbot_conf:/etc/letsencrypt \
  certbot/certbot certonly --webroot -w /var/www/certbot \
  -d bot.example.com --email you@example.com --agree-tos --no-eff-email
```

The running `macropulse-certbot` container renews it automatically.

## 4. nginx route

Back up the config, append the rest of `deploy/nginx-bot.conf`, then test
before reloading. A failed `nginx -t` means nothing changed for the other
sites; restore the backup and investigate.

```bash
cp /opt/macropulse/nginx/nginx.conf /opt/macropulse/nginx/nginx.conf.bak-$(date +%F)
nano /opt/macropulse/nginx/nginx.conf
docker exec macropulse-nginx nginx -t && docker exec macropulse-nginx nginx -s reload
```

Only `POST /signals` is exposed. `/health` and `/metrics` return 404 publicly;
use `docker exec trading-bot trading-bot status` instead.

## 5. Smoke test, then TradingView

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://bot.example.com/health        # 404
curl -s -w ' %{http_code}\n' -X POST https://bot.example.com/signals -d '{}'   # 401
curl -s -w ' %{http_code}\n' -X POST https://bot.example.com/signals \
  -H "X-Signal-Secret: $SECRET" \
  -d '{"source":"smoke","symbol":"BTC/USDT","action":"HOLD"}'                   # 202
```

In TradingView, set the alert's webhook URL to `https://bot.example.com/signals`
and the message to:

```json
{"source": "tradingview", "symbol": "{{ticker}}", "action": "{{strategy.order.action}}", "passphrase": "<SIGNAL_WEBHOOK_SECRET>"}
```

Signals for a symbol other than the configured `--symbol` (default `BTC/USDT`)
are ignored and logged.

## 6. Going live (Binance testnet first)

1. Create an API key with **withdrawals disabled** and **restricted to the VPS
   IP**. A leaked key can then trade but cannot move funds out.
2. Fill `BINANCE_API_KEY` / `BINANCE_API_SECRET` in `.env`; keep
   `USE_TESTNET=true` for the first run.
3. Override the command in a `deploy/docker-compose.override.yml` (or edit the
   compose file) to replace `--paper` with `--live`, then
   `docker compose -f deploy/docker-compose.prod.yml up -d`.
4. Mainnet additionally requires `USE_TESTNET=false` **and**
   `I_UNDERSTAND_LIVE_TRADING_RISK=true`.

## Operations

| Task | Command |
| --- | --- |
| Logs | `docker logs --tail 200 trading-bot` |
| Status | `docker exec trading-bot trading-bot status` |
| Audit trail | `docker exec trading-bot tail -n 50 /data/trade_audit.log` |
| Update | `git pull && docker compose -f deploy/docker-compose.prod.yml up -d --build` |
| Stop | `docker compose -f deploy/docker-compose.prod.yml down` (data volume is kept) |

### Kill switch

When the drawdown or daily-loss limit trips, `serve` stops trading, exits, and
writes `/data/kill_switch.tripped` (timestamp and reason). While that file
exists the container refuses to start (exit code 3), so Docker's restart policy
cannot quietly resume trading. After reviewing what happened:

```bash
docker run --rm -v trading-bot_trading_bot_data:/data alpine cat /data/kill_switch.tripped
docker run --rm -v trading-bot_trading_bot_data:/data alpine rm /data/kill_switch.tripped
docker restart trading-bot
```

(`docker volume ls | grep trading_bot` shows the exact volume name.)
