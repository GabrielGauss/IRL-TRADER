"""`trading-bot backtest` and `trading-bot walkforward`.

Realistic by default: fills at the next bar's open, 10 bps taker fee and
5 bps slippage per side, and every result is printed next to a buy-and-hold
benchmark paying the same costs and position size.
"""

from __future__ import annotations

import argparse
import logging
from collections.abc import Callable

import pandas as pd

from trading_bot.backtest.data import load_history
from trading_bot.backtest.engine import run_backtest
from trading_bot.backtest.metrics import Summary, buy_and_hold, summarize
from trading_bot.backtest.walkforward import ParamGrid, verdict, walk_forward
from trading_bot.config import load_public_data_settings
from trading_bot.exchange.binance_client import BinanceClient
from trading_bot.strategy.ema_rsi import EmaRsiStrategy

logger = logging.getLogger(__name__)

DEFAULT_FEE_BPS = 10.0  # Binance spot taker fee
DEFAULT_SLIPPAGE_BPS = 5.0


def add_backtest_commands(
    subparsers: argparse._SubParsersAction,
    add_strategy_arguments: Callable[[argparse.ArgumentParser], None],
) -> None:
    backtest = subparsers.add_parser(
        "backtest", help="Backtest the EMA/RSI strategy with realistic fills and costs"
    )
    _add_data_args(backtest)
    backtest.add_argument("--limit", type=int, default=500, help="bars when --start is not given")
    backtest.add_argument("--initial-balance", type=float, default=1000.0)
    backtest.add_argument("--position-size-fraction", type=float, default=0.1)
    _add_cost_args(backtest)
    backtest.add_argument(
        "--fill",
        choices=["next_open", "close"],
        default="next_open",
        help="next_open (realistic, default) or close (optimistic legacy behaviour)",
    )
    add_strategy_arguments(backtest)
    backtest.set_defaults(func=cmd_backtest)

    wf = subparsers.add_parser(
        "walkforward",
        help="Choose EMA/RSI params on a training window, score them on the next unseen window",
    )
    _add_data_args(wf)
    wf.add_argument("--train-bars", type=int, default=2000)
    wf.add_argument("--test-bars", type=int, default=500)
    wf.add_argument("--position-size-fraction", type=float, default=1.0)
    _add_cost_args(wf)
    defaults = ParamGrid()
    for name in ("fast_ema", "slow_ema", "rsi_period", "rsi_oversold", "rsi_overbought"):
        wf.add_argument(
            f"--{name.replace('_', '-')}-grid",
            default=",".join(str(v) for v in getattr(defaults, name)),
            help=f"comma-separated values (default {getattr(defaults, name)})",
        )
    wf.set_defaults(func=cmd_walkforward)


def _add_data_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--interval", default="1h")
    parser.add_argument("--start", help="history start, e.g. 2024-01-01 (paginated past 1000 bars)")
    parser.add_argument("--end", help="history end (default: now; closed ranges are cached)")
    parser.add_argument(
        "--testnet",
        action="store_true",
        help="Fetch klines from Binance testnet instead of mainnet public data (default: mainnet)",
    )


def _add_cost_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--fee-bps", type=float, default=DEFAULT_FEE_BPS)
    parser.add_argument("--slippage-bps", type=float, default=DEFAULT_SLIPPAGE_BPS)


def _load(args: argparse.Namespace, limit: int | None = None) -> pd.DataFrame:
    client = BinanceClient(load_public_data_settings(use_testnet=args.testnet))
    if args.start:
        logger.info(
            "Fetching %s %s history %s..%s",
            args.symbol,
            args.interval,
            args.start,
            args.end or "now",
        )
        return load_history(client, args.symbol, args.interval, args.start, args.end)
    logger.info("Fetching last %s %s %s klines", limit, args.symbol, args.interval)
    return client.get_klines(args.symbol, args.interval, limit=limit or 500)


def cmd_backtest(args: argparse.Namespace) -> int:
    df = _load(args, args.limit)
    strategy = EmaRsiStrategy(
        fast_ema=args.fast_ema,
        slow_ema=args.slow_ema,
        rsi_period=args.rsi_period,
        rsi_oversold=args.rsi_oversold,
        rsi_overbought=args.rsi_overbought,
    )
    result = run_backtest(
        strategy,
        df,
        initial_balance=args.initial_balance,
        position_size_fraction=args.position_size_fraction,
        fee_bps=args.fee_bps,
        slippage_bps=args.slippage_bps,
        fill=args.fill,
    )
    tradable = df.iloc[getattr(strategy, "min_lookback", 0) :].reset_index(drop=True)
    bench = buy_and_hold(
        tradable,
        initial_balance=args.initial_balance,
        position_size_fraction=args.position_size_fraction,
        fee_bps=args.fee_bps,
        slippage_bps=args.slippage_bps,
    )
    print(f"Symbol:           {args.symbol}")
    print(f"Interval:         {args.interval}")
    print(f"Bars:             {len(df)}")
    print(
        f"Costs:            fee {args.fee_bps} bps, slippage {args.slippage_bps} bps, fill={args.fill}"
    )
    print(f"Initial balance:  {result.initial_balance:.2f}")
    print(f"Final equity:     {result.final_equity:.2f}")
    _print_summary_table(
        [
            ("Strategy", summarize(result, args.interval)),
            ("Buy & hold", summarize(bench, args.interval)),
        ]
    )
    return 0


def cmd_walkforward(args: argparse.Namespace) -> int:
    df = _load(args)
    grid = ParamGrid(
        fast_ema=_ints(args.fast_ema_grid),
        slow_ema=_ints(args.slow_ema_grid),
        rsi_period=_ints(args.rsi_period_grid),
        rsi_oversold=_floats(args.rsi_oversold_grid),
        rsi_overbought=_floats(args.rsi_overbought_grid),
    )
    report = walk_forward(
        df,
        grid,
        interval=args.interval,
        train_bars=args.train_bars,
        test_bars=args.test_bars,
        fee_bps=args.fee_bps,
        slippage_bps=args.slippage_bps,
        position_size_fraction=args.position_size_fraction,
    )
    print(
        f"{args.symbol} {args.interval}: {len(df)} bars, {len(report.folds)} folds "
        f"(train {args.train_bars} / test {args.test_bars}), {len(grid.combinations())} param sets, "
        f"fee {args.fee_bps} bps, slippage {args.slippage_bps} bps"
    )
    print(
        f"{'fold':>4}  {'test window':<33} {'params':<22} {'IS Sharpe':>9} {'OOS ret%':>9} {'OOS Sharpe':>10} {'B&H ret%':>9}"
    )
    for i, fold in enumerate(report.folds, start=1):
        p = fold.params
        window = f"{df['open_time'].iloc[fold.test_start]:%Y-%m-%d} -> {df['open_time'].iloc[fold.test_end - 1]:%Y-%m-%d}"
        params = f"{int(p['fast_ema'])}/{int(p['slow_ema'])} rsi {p['rsi_oversold']:g}-{p['rsi_overbought']:g}"
        print(
            f"{i:>4}  {window:<33} {params:<22} {fold.in_sample.sharpe:>9.2f} "
            f"{fold.oos.total_return_pct:>9.2f} {fold.oos.sharpe:>10.2f} {fold.benchmark.total_return_pct:>9.2f}"
        )
    print()
    print(f"In-sample Sharpe (mean):      {report.mean_in_sample_sharpe:.2f}")
    print(f"Out-of-sample Sharpe (mean):  {report.mean_oos_sharpe:.2f}")
    print(f"Out-of-sample return:         {report.oos_return_pct:+.2f}%")
    print(f"Buy & hold return (same windows): {report.benchmark_return_pct:+.2f}%")
    print(f"Out-of-sample trades:         {report.oos_trades}")
    print(
        f"Out-of-sample exposure (mean): {report.mean_oos_exposure_pct:.1f}% of bars in a position"
    )
    print(
        "Verdict: " + verdict(report.oos_return_pct, report.benchmark_return_pct, report.oos_trades)
    )
    return 0


def _print_summary_table(rows: list[tuple[str, Summary]]) -> None:
    print(
        f"{'':<12} {'Return%':>9} {'Sharpe':>7} {'MaxDD%':>7} {'Trades':>6} {'Win%':>6} {'Exposure%':>9} {'PF':>6} {'Fees':>8}"
    )
    for name, s in rows:
        pf = "inf" if s.profit_factor == float("inf") else f"{s.profit_factor:.2f}"
        print(
            f"{name:<12} {s.total_return_pct:>9.2f} {s.sharpe:>7.2f} {s.max_drawdown_pct:>7.2f} "
            f"{s.num_trades:>6} {s.win_rate_pct:>6.1f} {s.exposure_pct:>9.1f} {pf:>6} {s.fees_paid:>8.2f}"
        )


def _ints(text: str) -> tuple[int, ...]:
    return tuple(int(float(v)) for v in text.split(",") if v.strip())


def _floats(text: str) -> tuple[float, ...]:
    return tuple(float(v) for v in text.split(",") if v.strip())
