"""`trading-bot backtest|run|serve|status ...` entrypoints."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import signal
import sys
import time
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from importlib.metadata import version as package_version
from pathlib import Path

from aiohttp import web

from trading_bot.backtest.engine import run_backtest
from trading_bot.config import (
    IrlSettings,
    load_irl_settings,
    load_public_data_settings,
    load_settings,
    load_webhook_settings,
)
from trading_bot.exchange.binance_client import BinanceClient
from trading_bot.execution.broker import Broker, CcxtBroker, PaperBroker
from trading_bot.execution.engine import ExecutionEngine, RiskLimitBreached, RiskLimits
from trading_bot.irl.client import AgentIdentity, IrlClient, IrlError, model_hash
from trading_bot.irl.gate import IrlGate, OrderGate, PassthroughGate
from trading_bot.irl.heartbeat import MacroPulseHeartbeatSource
from trading_bot.persistence.repository import TradeRepository
from trading_bot.risk.drawdown import DrawdownMonitor, DrawdownStatus
from trading_bot.risk.kill_switch import KillSwitch, KillSwitchLimits
from trading_bot.runtime.controller import TradingController
from trading_bot.runtime.health import HealthMonitor
from trading_bot.runtime.logging_config import configure_logging, get_audit_logger
from trading_bot.runtime.metrics import PerformanceSnapshot, build_performance_snapshot
from trading_bot.runtime.webhook import create_app
from trading_bot.signals.ingestion import SignalIngestor
from trading_bot.signals.queue import SignalQueue
from trading_bot.strategy.ema_rsi import EmaRsiStrategy

logger = logging.getLogger(__name__)


def _add_strategy_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--fast-ema", type=int, default=12)
    parser.add_argument("--slow-ema", type=int, default=26)
    parser.add_argument("--rsi-period", type=int, default=14)
    parser.add_argument("--rsi-oversold", type=float, default=30.0)
    parser.add_argument("--rsi-overbought", type=float, default=70.0)


def _build_strategy(args: argparse.Namespace) -> EmaRsiStrategy:
    return EmaRsiStrategy(
        fast_ema=args.fast_ema,
        slow_ema=args.slow_ema,
        rsi_period=args.rsi_period,
        rsi_oversold=args.rsi_oversold,
        rsi_overbought=args.rsi_overbought,
    )


def cmd_backtest(args: argparse.Namespace) -> int:
    settings = load_public_data_settings(use_testnet=args.testnet)
    client = BinanceClient(settings)
    strategy = _build_strategy(args)

    logger.info(
        "Fetching %s %s klines (limit=%s, testnet=%s)",
        args.symbol,
        args.interval,
        args.limit,
        args.testnet,
    )
    df = client.get_klines(args.symbol, args.interval, limit=args.limit)

    result = run_backtest(
        strategy,
        df,
        initial_balance=args.initial_balance,
        position_size_fraction=args.position_size_fraction,
    )

    print(f"Symbol:           {args.symbol}")
    print(f"Interval:         {args.interval}")
    print(f"Bars:             {len(df)}")
    print(f"Initial balance:  {result.initial_balance:.2f}")
    print(f"Final equity:     {result.final_equity:.2f}")
    print(f"Total return:     {result.total_return_pct:+.2f}%")
    print(f"Trades:           {result.num_trades}")
    print(f"Win rate:         {result.win_rate_pct:.2f}%")
    print(f"Max drawdown:     {result.max_drawdown_pct:.2f}%")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    settings = load_settings()
    client = BinanceClient(settings)
    strategy = _build_strategy(args)
    repository = TradeRepository(settings.db_path)
    risk_limits = RiskLimits(
        max_daily_loss_pct=settings.max_daily_loss_pct,
        max_drawdown_pct=settings.max_drawdown_pct,
    )
    engine = ExecutionEngine(
        client=client,
        strategy=strategy,
        repository=repository,
        risk_limits=risk_limits,
        base_asset=args.base_asset,
        quote_asset=args.quote_asset,
        position_size_fraction=settings.position_size_fraction,
    )

    logger.info(
        "Starting %s%s loop: interval=%s testnet=%s",
        args.base_asset,
        args.quote_asset,
        args.interval,
        settings.use_testnet,
    )

    while True:
        try:
            trade = engine.run_once(interval=args.interval, limit=args.limit)
        except RiskLimitBreached as exc:
            logger.error("Risk limit breached, halting: %s", exc)
            return 1

        if trade is not None:
            logger.info("Trade executed: %s", trade)

        if args.once:
            return 0
        time.sleep(args.poll_seconds)


@dataclass
class ServeRuntime:
    controller: TradingController
    ingestor: SignalIngestor
    app: web.Application
    broker: Broker
    price_broker: Broker | None
    gate: OrderGate = field(default_factory=PassthroughGate)
    irl_client: IrlClient | None = None
    heartbeat_source: MacroPulseHeartbeatSource | None = None


IRL_MODEL_ID = "trading-bot/signal-executor"
IRL_FEATURE_SCHEMA_ID = "signal-payload-v1"


def _agent_identity(args: argparse.Namespace, agent_id: str) -> AgentIdentity:
    """The identity IRL seals into every trace. The model hash covers the
    package version and every parameter that changes trading behavior, so
    changing any of them requires re-registering (`trading-bot irl-register`)
    -- IRL then rejects intents from the old, unregistered configuration."""
    hyperparameters = {
        "symbol": args.symbol,
        "exchange_id": args.exchange_id,
        "position_size_fraction": args.position_size_fraction,
        "max_drawdown_pct": args.max_drawdown_pct,
        "max_daily_loss_pct": args.max_daily_loss_pct,
    }
    hyperparameter_checksum = model_hash(hyperparameters)
    prompt_version = package_version("trading-bot")
    return AgentIdentity(
        agent_id=agent_id,
        model_hash_hex=model_hash(
            {
                "model_id": IRL_MODEL_ID,
                "version": prompt_version,
                "feature_schema_id": IRL_FEATURE_SCHEMA_ID,
                "hyperparameter_checksum": hyperparameter_checksum,
            }
        ),
        model_id=IRL_MODEL_ID,
        prompt_version=prompt_version,
        feature_schema_id=IRL_FEATURE_SCHEMA_ID,
        hyperparameter_checksum=hyperparameter_checksum,
    )


def _irl_connection(settings: IrlSettings, *, need_agent: bool) -> tuple[str, str, str]:
    missing = [
        name
        for name, present in (
            ("IRL_BASE_URL", bool(settings.irl_base_url)),
            ("IRL_API_TOKEN", settings.irl_api_token is not None),
            ("IRL_AGENT_ID", bool(settings.irl_agent_id) or not need_agent),
        )
        if not present
    ]
    if missing or settings.irl_api_token is None:
        raise ValueError(f"IRL is enabled but {', '.join(missing)} not set (in .env or env).")
    return (
        settings.irl_base_url,
        settings.irl_api_token.get_secret_value(),
        settings.irl_agent_id,
    )


def _heartbeat_source(settings: IrlSettings) -> MacroPulseHeartbeatSource | None:
    if not settings.irl_heartbeat_url:
        return None
    if settings.macropulse_api_key is None:
        raise ValueError("IRL_HEARTBEAT_URL is set but MACROPULSE_API_KEY is not.")
    return MacroPulseHeartbeatSource(
        settings.irl_heartbeat_url, settings.macropulse_api_key.get_secret_value()
    )


def _build_irl_gate(
    args: argparse.Namespace,
) -> tuple[IrlGate, IrlClient, MacroPulseHeartbeatSource | None]:
    settings = load_irl_settings()
    base_url, token, agent_id = _irl_connection(settings, need_agent=True)
    heartbeat_source = _heartbeat_source(settings)
    identity = _agent_identity(args, agent_id)
    client = IrlClient(base_url, token)
    venue_id = args.exchange_id.upper() + ("-PAPER" if args.paper else "")
    logger.info(
        "IRL gate enabled: agent %s, model hash %s, venue %s, L2 heartbeat %s",
        agent_id,
        identity.model_hash_hex,
        venue_id,
        "on" if heartbeat_source else "off",
    )
    gate = IrlGate(
        client,
        identity,
        venue_id=venue_id,
        notional_currency=args.quote_asset,
        heartbeat_source=heartbeat_source,
    )
    return gate, client, heartbeat_source


def _resolve_signal_secret(args: argparse.Namespace) -> str | None:
    """Fail closed: POST /signals must be authenticated unless the operator
    explicitly opts out, and that opt-out is never allowed with real orders.
    Binding to loopback isn't treated as safe, since tunnels (ngrok etc.)
    commonly expose a localhost port to the internet."""
    if args.no_auth:
        if not args.paper:
            raise ValueError("--no-auth is not allowed with --live; set SIGNAL_WEBHOOK_SECRET.")
        logger.warning("POST /signals is UNAUTHENTICATED (--no-auth); paper mode only.")
        return None
    secret = load_webhook_settings().signal_webhook_secret
    if secret is None:
        raise ValueError(
            "Refusing to start: set SIGNAL_WEBHOOK_SECRET (in .env or the environment) "
            "to authenticate POST /signals, or pass --no-auth for local paper testing."
        )
    return secret.get_secret_value()


def _build_serve_runtime(args: argparse.Namespace) -> ServeRuntime:
    """Pure(ish) construction, no network binding -- kept separate from
    _run_serve_runtime so it's unit-testable without starting a real server."""
    signal_secret = _resolve_signal_secret(args)
    repository = TradeRepository(args.db_path)
    kill_switch = KillSwitch(
        KillSwitchLimits(
            max_drawdown_pct=args.max_drawdown_pct, max_daily_loss_pct=args.max_daily_loss_pct
        )
    )
    drawdown_monitor = DrawdownMonitor(max_drawdown_pct=args.max_drawdown_pct)
    health = HealthMonitor()
    signal_queue = SignalQueue()
    raw_queue: asyncio.Queue = asyncio.Queue()

    price_broker: Broker | None = None
    if args.paper:
        price_broker = CcxtBroker(args.exchange_id, testnet=False)
        broker: Broker = PaperBroker(
            price_broker.get_price,
            initial_balances={args.quote_asset: args.paper_quote_balance, args.base_asset: 0.0},
            slippage_bps=args.paper_slippage_bps,
            fee_bps=args.paper_fee_bps,
        )
        initial_cash = args.paper_quote_balance
    else:
        if args.exchange_id != "binance":
            raise ValueError(
                "Live trading is only wired for --exchange-id binance right now "
                "(see README); use --paper to try other exchanges."
            )
        settings = load_settings()
        broker = CcxtBroker(
            "binance",
            settings.binance_api_key,
            settings.binance_api_secret,
            testnet=settings.use_testnet,
        )
        initial_cash = 0.0

    gate: OrderGate = PassthroughGate()
    irl_client: IrlClient | None = None
    heartbeat_source: MacroPulseHeartbeatSource | None = None
    if args.irl:
        gate, irl_client, heartbeat_source = _build_irl_gate(args)

    controller = TradingController(
        broker=broker,
        queue=signal_queue,
        repository=repository,
        kill_switch=kill_switch,
        drawdown_monitor=drawdown_monitor,
        health=health,
        symbol=args.symbol,
        base_asset=args.base_asset,
        quote_asset=args.quote_asset,
        position_size_fraction=args.position_size_fraction,
        initial_cash=initial_cash,
        gate=gate,
    )
    ingestor = SignalIngestor(fetch=raw_queue.get, queue=signal_queue)

    async def metrics_provider() -> PerformanceSnapshot:
        price = await broker.get_price(args.symbol)
        mark_prices = {args.base_asset: price}
        since = datetime.now(UTC) - timedelta(days=7)
        equity_curve = [snap.equity for snap in repository.get_equity_since(since)]
        drawdown_status = controller.last_drawdown_status
        if drawdown_status is None:
            equity_now = controller.portfolio.equity(mark_prices)
            drawdown_status = DrawdownStatus(
                peak_equity=equity_now, current_equity=equity_now, drawdown_pct=0.0, breached=False
            )
        return build_performance_snapshot(
            portfolio=controller.portfolio,
            mark_prices=mark_prices,
            drawdown_status=drawdown_status,
            equity_curve=equity_curve,
            num_trades=len(repository.get_trades()),
        )

    app = create_app(raw_queue, health, metrics_provider, signal_secret=signal_secret)
    return ServeRuntime(
        controller=controller,
        ingestor=ingestor,
        app=app,
        broker=broker,
        price_broker=price_broker,
        gate=gate,
        irl_client=irl_client,
        heartbeat_source=heartbeat_source,
    )


async def _run_serve_runtime(runtime: ServeRuntime, args: argparse.Namespace) -> int:
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signal_name in ("SIGINT", "SIGTERM"):
        os_signal = getattr(signal, signal_name, None)
        if os_signal is not None:
            with contextlib.suppress(NotImplementedError):
                loop.add_signal_handler(os_signal, stop_event.set)

    runner = web.AppRunner(runtime.app)
    await runner.setup()
    site = web.TCPSite(runner, args.host, args.port)
    await site.start()
    logger.info(
        "serve listening on http://%s:%s (POST /signals, GET /health, GET /metrics)",
        args.host,
        args.port,
    )

    async def stop_after_timeout() -> None:
        if args.max_runtime_seconds is not None:
            await asyncio.sleep(args.max_runtime_seconds)
            stop_event.set()

    ingest_task = asyncio.create_task(runtime.ingestor.run())
    timeout_task = asyncio.create_task(stop_after_timeout())
    try:
        await runtime.controller.run_forever(stop_event=stop_event, poll_timeout=args.poll_timeout)
    finally:
        stop_event.set()
        ingest_task.cancel()
        timeout_task.cancel()
        for task in (ingest_task, timeout_task):
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await runner.cleanup()
        await runtime.broker.close()
        if runtime.price_broker is not None:
            await runtime.price_broker.close()
        if runtime.irl_client is not None:
            await runtime.irl_client.close()
        if runtime.heartbeat_source is not None:
            await runtime.heartbeat_source.close()

    return 1 if runtime.controller.health.status().kill_switch_tripped else 0


KILL_SWITCH_LATCH_FILENAME = "kill_switch.tripped"
EXIT_KILL_SWITCH_LATCHED = 3


def _kill_switch_latch_path(args: argparse.Namespace) -> Path:
    """Lives next to the trade DB so it shares the same persistent volume."""
    return Path(args.db_path).resolve().parent / KILL_SWITCH_LATCH_FILENAME


def _write_kill_switch_latch(latch: Path, last_error: str | None) -> None:
    latch.write_text(
        json.dumps({"tripped_at": datetime.now(UTC).isoformat(), "last_error": last_error})
    )
    logger.error("Kill switch tripped; latched at %s. Delete it to allow trading again.", latch)


def cmd_serve(args: argparse.Namespace) -> int:
    configure_logging(json_output=not args.plain_logs)
    # The in-process kill switch resets on restart, so a supervisor (Docker
    # restart policy, systemd) would silently resume trading after a trip.
    # Persist the trip and refuse to start until a human removes the latch.
    latch = _kill_switch_latch_path(args)
    if latch.exists():
        logger.error(
            "Refusing to start: kill switch latched (%s). Review, then delete %s to resume.",
            latch.read_text().strip(),
            latch,
        )
        return EXIT_KILL_SWITCH_LATCHED
    try:
        runtime = _build_serve_runtime(args)
    except ValueError as exc:  # includes pydantic ValidationError from settings
        logger.error("serve configuration error: %s", exc)
        return 2
    # Only create the audit log once config is known-good, so a refused start
    # doesn't leave an empty log file behind.
    get_audit_logger(args.audit_log)
    exit_code = asyncio.run(_run_serve_runtime(runtime, args))
    status = runtime.controller.health.status()
    if status.kill_switch_tripped:
        _write_kill_switch_latch(latch, status.last_error)
    return exit_code


def cmd_irl_register(args: argparse.Namespace) -> int:
    try:
        base_url, token, _ = _irl_connection(load_irl_settings(), need_agent=False)
    except ValueError as exc:
        logger.error("irl-register configuration error: %s", exc)
        return 2
    identity = _agent_identity(args, agent_id="")

    async def register() -> str:
        client = IrlClient(base_url, token)
        try:
            return await client.register_agent(
                name=args.name,
                model_hash_hex=identity.model_hash_hex,
                max_notional=args.max_notional,
            )
        finally:
            await client.close()

    try:
        agent_id = asyncio.run(register())
    except IrlError as exc:
        logger.error("IRL agent registration failed: %s", exc)
        return 1
    print(f"Registered IRL agent {args.name!r}")
    print(f"Model hash:  {identity.model_hash_hex}")
    print("Add to .env, then run `serve --irl` with the same strategy/risk flags:")
    print(f"IRL_AGENT_ID={agent_id}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    base_url = f"http://{args.host}:{args.port}"
    try:
        with urllib.request.urlopen(f"{base_url}/health", timeout=args.timeout) as resp:
            health = json.loads(resp.read())
        with urllib.request.urlopen(f"{base_url}/metrics", timeout=args.timeout) as resp:
            metrics = json.loads(resp.read())
    except OSError as exc:
        print(f"Could not reach {base_url}: {exc}", file=sys.stderr)
        return 1

    print(f"Healthy:           {health['healthy']}")
    print(f"Uptime (s):        {health['uptime_seconds']:.1f}")
    print(f"Signals processed: {health['signals_processed']}")
    print(f"Kill switch:       {'TRIPPED' if health['kill_switch_tripped'] else 'ok'}")
    if health["last_error"]:
        print(f"Last error:        {health['last_error']}")
    print()
    print(f"Equity:            {metrics['equity']:.2f}")
    print(f"Cash:              {metrics['cash']:.2f}")
    print(f"Realized PnL:      {metrics['realized_pnl']:.2f}")
    print(f"Unrealized PnL:    {metrics['unrealized_pnl']:.2f}")
    print(f"Drawdown:          {metrics['drawdown_pct']:.2f}%")
    print(f"Sharpe ratio:      {metrics['sharpe_ratio']:.3f}")
    print(f"Trades:            {metrics['num_trades']}")
    return 0


def _add_identity_args(parser: argparse.ArgumentParser) -> None:
    """Flags that define trading behavior, shared by `serve` and
    `irl-register` so both compute the same IRL model hash."""
    parser.add_argument("--symbol", default="BTC/USDT", help="ccxt unified symbol")
    parser.add_argument("--exchange-id", default="binance")
    parser.add_argument("--position-size-fraction", type=float, default=0.1)
    parser.add_argument("--max-drawdown-pct", type=float, default=10.0)
    parser.add_argument("--max-daily-loss-pct", type=float, default=3.0)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="trading-bot")
    subparsers = parser.add_subparsers(dest="command", required=True)

    backtest_parser = subparsers.add_parser(
        "backtest", help="Backtest the EMA/RSI strategy over historical klines"
    )
    backtest_parser.add_argument("--symbol", default="BTCUSDT")
    backtest_parser.add_argument("--interval", default="1h")
    backtest_parser.add_argument("--limit", type=int, default=500)
    backtest_parser.add_argument("--initial-balance", type=float, default=1000.0)
    backtest_parser.add_argument("--position-size-fraction", type=float, default=0.1)
    backtest_parser.add_argument(
        "--testnet",
        action="store_true",
        help="Fetch klines from Binance testnet instead of mainnet public data (default: mainnet)",
    )
    _add_strategy_arguments(backtest_parser)
    backtest_parser.set_defaults(func=cmd_backtest)

    run_parser = subparsers.add_parser("run", help="Run the live/testnet trading loop")
    run_parser.add_argument("--base-asset", default="BTC")
    run_parser.add_argument("--quote-asset", default="USDT")
    run_parser.add_argument("--interval", default="1h")
    run_parser.add_argument("--limit", type=int, default=200)
    run_parser.add_argument("--poll-seconds", type=float, default=60.0)
    run_parser.add_argument("--once", action="store_true", help="Run a single iteration and exit")
    _add_strategy_arguments(run_parser)
    run_parser.set_defaults(func=cmd_run)

    serve_parser = subparsers.add_parser(
        "serve",
        help="Run the signal-driven async controller (webhook + health/metrics HTTP server)",
    )
    _add_identity_args(serve_parser)
    serve_parser.add_argument("--base-asset", default="BTC")
    serve_parser.add_argument("--quote-asset", default="USDT")
    serve_parser.add_argument(
        "--paper",
        action="store_true",
        default=True,
        help="Paper-trade against real market prices with simulated fills (default)",
    )
    serve_parser.add_argument(
        "--live",
        dest="paper",
        action="store_false",
        help="Place real orders (Binance only; requires the same credentials/opt-in as `run`)",
    )
    serve_parser.add_argument("--paper-quote-balance", type=float, default=1000.0)
    serve_parser.add_argument("--paper-slippage-bps", type=float, default=5.0)
    serve_parser.add_argument("--paper-fee-bps", type=float, default=10.0)
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8080)
    serve_parser.add_argument("--poll-timeout", type=float, default=1.0)
    serve_parser.add_argument(
        "--max-runtime-seconds",
        type=float,
        default=None,
        help="Stop automatically after N seconds (mainly for bounded/test runs)",
    )
    serve_parser.add_argument("--db-path", default="trading_bot.db")
    serve_parser.add_argument("--audit-log", default="trade_audit.log")
    serve_parser.add_argument("--plain-logs", action="store_true")
    serve_parser.add_argument(
        "--no-auth",
        action="store_true",
        help="Accept unauthenticated POST /signals (paper mode only; for local testing)",
    )
    serve_parser.add_argument(
        "--irl",
        action="store_true",
        help="Route every order through IRL authorize/bind (needs IRL_* settings)",
    )
    serve_parser.set_defaults(func=cmd_serve)

    register_parser = subparsers.add_parser(
        "irl-register",
        help="Register this bot's strategy/risk configuration as an IRL agent",
    )
    _add_identity_args(register_parser)
    register_parser.add_argument("--name", default="trading-bot")
    register_parser.add_argument(
        "--max-notional",
        type=float,
        required=True,
        help="IRL notional cap per order, in quote currency",
    )
    register_parser.set_defaults(func=cmd_irl_register)

    status_parser = subparsers.add_parser(
        "status", help="Query a running `serve` instance's health and metrics"
    )
    status_parser.add_argument("--host", default="127.0.0.1")
    status_parser.add_argument("--port", type=int, default=8080)
    status_parser.add_argument("--timeout", type=float, default=5.0)
    status_parser.set_defaults(func=cmd_status)

    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        logger.info("Interrupted, shutting down.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
