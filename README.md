# trading-bot

A Binance spot trading bot with a tested backtest/live-execution path, plus a
newer signal-driven, exchange-agnostic async stack (`serve`) with its own
webhook ingestion, risk engine, order routing, and health/metrics reporting.

It is also the reference client for the **IRL Engine** (Immutable Reasoning
Log): with `serve --irl`, every order is authorized by IRL before it is placed
and bound back to its sealed reasoning trace afterwards. How the project got
here, including what wiring it end-to-end uncovered in IRL, is told in
[docs/JOURNEY.md](docs/JOURNEY.md).

## Two stacks

This repo contains two parallel implementations, now both runnable from the
CLI. They share persistence (`TradeRepository`) and the `OrderSide` enum but
otherwise don't share code.

### 1. The proven stack (`config` -> `backtest`/`run`)

Sync, python-binance-based, verified live against real Binance market data
and a real testnet-style order flow. Single strategy (EMA/RSI), single
exchange (Binance).

```
config.Settings -> exchange.BinanceClient -> strategy.EmaRsiStrategy
  -> execution.ExecutionEngine (own RiskLimits/RiskLimitBreached)
  -> persistence.TradeRepository (SQLite)
```

```bash
trading-bot backtest --symbol BTCUSDT --interval 1h --limit 500
trading-bot run --once      # single testnet iteration
trading-bot run              # live poll loop (Ctrl+C to stop)
```

`trading-bot run` against real funds requires two explicit opt-ins in
`Settings` (`USE_TESTNET=false` and `I_UNDERSTAND_LIVE_TRADING_RISK=true`) --
a single misconfigured variable can't route real orders.

### 2. The component stack (`signals/`, `risk/`, `execution/broker.py`, `runtime/` -> `serve`)

Async, exchange-agnostic (via `ccxt`), signal-driven: external systems POST
JSON signals over HTTP rather than the bot computing its own from OHLCV.

```
POST /signals -> signals.SignalIngestor -> signals.SignalQueue
  -> runtime.TradingController:
       risk.signal_to_target_position (sizing)
       risk.KillSwitch + risk.DrawdownMonitor (one reconciled risk check)
       execution.plan_order -> irl.gate (PassthroughGate | IrlGate)
         -> execution.CcxtBroker | PaperBroker
       persistence.TradeRepository (same store as the proven stack)
       runtime.logging_config's audit logger (JSON trade audit trail)
  -> GET /health, GET /metrics (runtime.HealthMonitor, runtime.metrics)
```

```bash
trading-bot serve                          # paper mode (default), Binance market data, no real orders
trading-bot serve --live                   # real orders, Binance only, same credential/opt-in gate as `run`
trading-bot status                         # query a running `serve` instance's /health + /metrics
trading-bot serve --irl                    # every order through IRL authorize -> place -> bind
trading-bot irl-register --max-notional 200  # register this strategy/risk config as an IRL agent
trading-bot irl-canary                     # end-to-end IRL check; authorizes, binds as Rejected, never trades
curl -X POST localhost:8080/signals \
  -H "Content-Type: application/json" \
  -d '{"source": "my-strategy", "symbol": "BTC/USDT", "action": "BUY"}'
```

Symbols in this stack use ccxt's unified `BASE/QUOTE` form (`"BTC/USDT"`),
not Binance's native `"BTCUSDT"` form used by the proven stack; `serve`
accepts either in an incoming signal and normalizes internally. Live trading
via `serve` is currently wired for `--exchange-id binance` only -- paper mode
works against any ccxt exchange id since it only needs public price data.

The kill switch latches: once tripped (drawdown or daily-loss limit
breached), `serve` stops placing new orders and exits, writing a
`kill_switch.tripped` file next to the trade DB. While that file exists,
`serve` refuses to start (exit code 3), so a supervisor such as Docker's
restart policy can't silently resume trading; delete it after reviewing the
trip.

In paper mode the simulated account (balances and portfolio) is saved to
`paper_state.json` next to the trade DB after every fill and restored at
startup, so deploys and restarts don't reset it. A corrupt file stops startup
instead of silently starting a fresh account; delete it to reset on purpose.

With `--irl`, a denied or failed authorization never places the order: the
signal is skipped, audited as `order blocked`, and the bot keeps running.
Fill audit records carry the IRL trace id, reasoning hash, final proof and
verification status.

Deploying behind an existing nginx on a VPS (Docker, TLS, TradingView setup,
going live): see [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

## Setup

```bash
python -m venv .venv
.venv/Scripts/pip install -e ".[dev]"   # Windows
# .venv/bin/pip install -e ".[dev]"     # macOS/Linux
cp .env.example .env                     # BINANCE_API_KEY/SECRET for `run` / `serve --live`,
                                         # SIGNAL_WEBHOOK_SECRET for `serve`
```

## Testing

```bash
pytest --cov=src --cov-report=term-missing
ruff check src tests
black src tests
isort src tests
mypy src/trading_bot
```

301 tests, 96% coverage as of the last commit. `serve`'s async server
lifecycle (signal handlers, task orchestration) is intentionally left out of
the automated suite and verified with a real running instance instead -- see
the commit history for the smoke-test transcript.

## Roadmap

- [x] Strategy, exchange client, risk sizing, config
- [x] Backtest engine
- [x] SQLite trade/equity persistence
- [x] Execution engine (signal -> risk-sized live order)
- [x] CLI (`backtest`, `run`)
- [x] Signal ingestion (schema, priority queue, backoff)
- [x] Portfolio state, drawdown monitor, kill switch, volatility sizing
- [x] Async broker abstraction (Binance + ccxt) and order router
- [x] Main event loop (`TradingController`), webhook signal ingestion,
      structured JSON logging + trade audit trail, Sharpe/PnL/drawdown
      metrics, health checks, CLI (`serve`, `status`)
- [x] CI (GitHub Actions: ruff, black, isort, mypy, and pytest with a 90%
      coverage floor on Ubuntu + Windows, Python 3.12/3.13)
- [x] IRL Engine gate (`serve --irl`): every order authorized, placed with
      the sealed client id, and bound back to its IRL trace; fails closed
- [x] Docker deployment behind a shared nginx, webhook auth, persistent
      kill-switch latch, paper account persisted across restarts
- [x] `irl-canary`: scheduled end-to-end IRL check (via the VPS monitoring)
- [ ] Honest backtester: fees, slippage, out-of-sample / walk-forward,
      buy-and-hold benchmark
- [ ] Internal strategy as a signal source + signal combiner (with
      per-source webhook credentials)
- [ ] Weeks of paper trading on the VPS -> Binance testnet -> small live
      (< $500, spot, tight kill switch)
- [ ] Live trading via `serve` for exchanges other than Binance (needs a
      credential story beyond the Binance-shaped `Settings` fields)
