"""`trading-bot backtest ...` and `trading-bot run ...` entrypoints."""

from __future__ import annotations

import argparse
import logging
import sys
import time

from trading_bot.backtest.engine import run_backtest
from trading_bot.config import load_public_data_settings, load_settings
from trading_bot.exchange.binance_client import BinanceClient
from trading_bot.execution.engine import ExecutionEngine, RiskLimitBreached, RiskLimits
from trading_bot.persistence.repository import TradeRepository
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
