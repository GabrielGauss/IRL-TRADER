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

## 6. IRL gate (authorize → place → bind)

With `--irl`, every order is authorized by the IRL Engine before it is placed,
and the fill is bound back to the IRL trace afterwards. The order's client id
(`irl-…`) is sealed in the trace and sent to the exchange, linking the two.
If IRL denies the intent, is unreachable, or errors, the order is **not**
placed: the signal is skipped, logged as `order blocked` in the audit trail,
and `/health` shows the reason in `last_error`. A failed bind after a real
fill never stops the bot; it is logged with `irl_bind_error` and the trace
shows up under IRL's `/irl/pending` for reconciliation.

1. Issue a client token with IRL's admin endpoint (owner token required):
   `POST /irl/admin/tokens` with `{"client_name": "trading-bot"}`. The token
   is shown once.
2. Add to `.env`: `IRL_BASE_URL=http://irl-engine:4000` (the shared Docker
   network) and `IRL_API_TOKEN=<token>`.
   If IRL runs with `LAYER2_ENABLED=true` (its default), every authorize must
   carry a signed heartbeat from MacroPulse, fetched just before the call
   (IRL rejects heartbeats older than 200 ms). Also add
   `IRL_HEARTBEAT_URL=http://api:8000/v1/irl/heartbeat` and
   `MACROPULSE_API_KEY=<irl_sidecar-tier key>`; without them authorize fails
   with `HEARTBEAT_MISSING` and every order is blocked.
3. Register the agent with the **same** strategy/risk flags `serve` uses. The
   cap is per order, in quote currency, and IRL scales it by the current
   market regime:
   ```bash
   docker compose -f deploy/docker-compose.prod.yml run --rm trading-bot \
     irl-register --max-notional 200
   ```
   Put the printed `IRL_AGENT_ID=…` line in `.env`.
4. Start with the IRL overlay:
   ```bash
   docker compose -f deploy/docker-compose.prod.yml -f deploy/docker-compose.irl.yml up -d
   docker logs trading-bot | grep "IRL gate enabled"
   ```

Changing the version or any strategy/risk flag changes the model hash, and IRL
then rejects intents until you register again (step 3) and update
`IRL_AGENT_ID`.

## 7. Going live (Binance testnet first)

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

(`name: trading-bot` in the compose file keeps this volume name stable.)
