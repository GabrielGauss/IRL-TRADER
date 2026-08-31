# trading-bot

A Binance spot trading bot with a tested backtest/live-execution path, plus a
newer set of exchange-agnostic building blocks (signal ingestion, portfolio
risk, async multi-exchange order routing) that aren't wired into a runnable
entrypoint yet.

## Two stacks

This repo currently contains two parallel implementations. They don't share
code except a common `OrderSide` enum, and nothing routes between them yet.

### 1. The proven stack (`config` -> CLI)

Sync, python-binance-based, verified live against real Binance market data
and a real testnet-style order flow.

```
config.Settings -> exchange.BinanceClient -> strategy.EmaRsiStrategy
  -> execution.ExecutionEngine (own RiskLimits/RiskLimitBreached)
  -> persistence.TradeRepository (SQLite)
```

Reachable today via the CLI:

```bash
trading-bot backtest --symbol BTCUSDT --interval 1h --limit 500
trading-bot run --once      # single testnet iteration
trading-bot run              # live poll loop (Ctrl+C to stop)
```

`trading-bot run` against real funds requires two explicit opt-ins in
`Settings` (`USE_TESTNET=false` and `I_UNDERSTAND_LIVE_TRADING_RISK=true`) --
a single misconfigured variable can't route real orders.

### 2. The component stack (`signals/`, additions to `risk/`, `execution/broker.py`)

Async, exchange-agnostic (via `ccxt`), built to eventually replace or
front the proven stack with a signal-driven, multi-venue design. Each piece
is unit-tested and has been smoke-tested standalone, but **there is no main
loop or CLI command wiring these together yet** -- that's the next piece of
work (see Roadmap).

```
signals.SignalPayload / SignalQueue / SignalIngestor
  -> risk.signal_to_target_position (-> risk.PortfolioState, DrawdownMonitor, KillSwitch)
  -> execution.route_to_target -> execution.CcxtBroker | PaperBroker
```

Symbols in this stack use ccxt's unified `BASE/QUOTE` form (`"BTC/USDT"`),
not Binance's native `"BTCUSDT"` form used by the proven stack -- another
reason the two don't share code directly.

## Setup

```bash
python -m venv .venv
.venv/Scripts/pip install -e ".[dev]"   # Windows
# .venv/bin/pip install -e ".[dev]"     # macOS/Linux
cp .env.example .env                     # fill in BINANCE_API_KEY/SECRET for `run`
```

## Testing

```bash
pytest --cov=src --cov-report=term-missing
ruff check src tests
black src tests
mypy src/trading_bot
```

156 tests, 98% coverage as of the last commit.

## Roadmap

- [x] Strategy, exchange client, risk sizing, config
- [x] Backtest engine
- [x] SQLite trade/equity persistence
- [x] Execution engine (signal -> risk-sized live order)
- [x] CLI (`backtest`, `run`)
- [x] Signal ingestion (schema, priority queue, backoff)
- [x] Portfolio state, drawdown monitor, kill switch, volatility sizing
- [x] Async broker abstraction (Binance + ccxt) and order router
- [ ] Main event loop wiring the component stack together end-to-end
      (signal -> target -> route -> fill -> persist, with one reconciled
      risk-limit model instead of the two that exist today), structured
      logging, trade audit trail, and performance metrics (Sharpe, etc.)
- [ ] CI (GitHub Actions running the test suite on push)
