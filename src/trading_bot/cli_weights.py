"""`trading-bot walkforward --strategy vol_target`: report for fractional-position
strategies, judged on the stitched out-of-sample curve against buy and hold."""

from __future__ import annotations

import argparse
import dataclasses

import pandas as pd

from trading_bot.backtest.bootstrap import BootstrapResult, paired_block_bootstrap
from trading_bot.backtest.metrics import annualization_factor
from trading_bot.backtest.weights import (
    WeightGrid,
    WeightSummary,
    walk_forward_weights,
    weight_verdict,
)


def run_weight_walkforward(args: argparse.Namespace, grid: WeightGrid, df: pd.DataFrame) -> int:
    if dataclasses.is_dataclass(grid) and hasattr(grid, "interval"):
        grid = dataclasses.replace(grid, interval=args.interval)  # type: ignore[type-var]
    report = walk_forward_weights(
        df,
        grid,
        interval=args.interval,
        train_bars=args.train_bars,
        test_bars=args.test_bars,
        fee_bps=args.fee_bps,
        slippage_bps=args.slippage_bps,
        rebalance_band=args.rebalance_band,
    )
    print(
        f"{args.symbol} {args.interval} {args.strategy}: {len(df)} bars, {len(report.folds)} folds "
        f"(train {args.train_bars} / test {args.test_bars}), {len(grid.combinations())} param sets, "
        f"fee {args.fee_bps} bps, slippage {args.slippage_bps} bps, band {args.rebalance_band:.0%}"
    )
    print(
        f"{'fold':>4}  {'test window':<25} {'params':<26} {'IS Sh':>6} {'OOS ret%':>9} "
        f"{'OOS DD%':>8} {'B&H ret%':>9} {'B&H DD%':>8} {'expo%':>6}"
    )
    for i, fold in enumerate(report.folds, start=1):
        window = (
            f"{df['open_time'].iloc[fold.test_start]:%Y-%m-%d}..."
            f"{df['open_time'].iloc[fold.test_end - 1]:%Y-%m-%d}"
        )
        print(
            f"{i:>4}  {window:<25} {grid.describe(fold.params):<26} {fold.in_sample_sharpe:>6.2f} "
            f"{fold.oos.total_return_pct:>9.2f} {fold.oos.max_drawdown_pct:>8.1f} "
            f"{fold.benchmark.total_return_pct:>9.2f} {fold.benchmark.max_drawdown_pct:>8.1f} "
            f"{fold.oos.avg_exposure_pct:>6.1f}"
        )
    print()
    print(f"In-sample Sharpe (mean of folds): {report.mean_in_sample_sharpe:.2f}")
    _print_pair(report.oos, report.benchmark)
    print("Verdict: " + weight_verdict(report.oos, report.benchmark))
    blocks = _parse_blocks(args.bootstrap_blocks)
    if blocks:
        _print_bootstrap(
            [
                paired_block_bootstrap(
                    report.oos_returns,
                    report.benchmark_returns,
                    block=block,
                    periods_per_year=annualization_factor(args.interval),
                )
                for block in blocks
            ]
        )
    return 0


def _parse_blocks(text: str) -> list[int]:
    try:
        return [int(part) for part in text.split(",") if part.strip()]
    except ValueError as exc:
        raise SystemExit(f"--bootstrap-blocks must be comma-separated integers: {exc}") from exc


def _print_bootstrap(results: list[BootstrapResult]) -> None:
    print()
    print(
        f"Paired block bootstrap ({results[0].n_boot} resamples; days only, "
        "not parameter re-selection):"
    )
    print(
        f"{'block':>5}  {'Sharpe diff':>11} {'95% CI':>16} {'P(<=0)':>7}  "
        f"{'DD ratio':>8} {'95% CI':>14} {'P(>bar)':>8}"
    )
    for r in results:
        print(
            f"{r.block:>5}  {r.sharpe_diff:>+11.2f} "
            f"{f'[{r.sharpe_diff_ci[0]:+.2f}, {r.sharpe_diff_ci[1]:+.2f}]':>16} "
            f"{r.p_sharpe_diff_le_zero:>7.1%}  {r.dd_ratio:>8.2f} "
            f"{f'[{r.dd_ratio_ci[0]:.2f}, {r.dd_ratio_ci[1]:.2f}]':>14} "
            f"{r.p_dd_ratio_above_bar:>8.1%}"
        )


def _print_pair(oos: WeightSummary, bench: WeightSummary) -> None:
    print("Stitched out-of-sample vs buy & hold (same windows, same costs):")
    print(
        f"{'':<14} {'Return%':>9} {'Sharpe':>7} {'MaxDD%':>7} {'Exposure%':>9} {'Rebal':>6} {'Fees':>8}"
    )
    for name, s in (("Strategy", oos), ("Buy & hold", bench)):
        print(
            f"{name:<14} {s.total_return_pct:>9.2f} {s.sharpe:>7.2f} {s.max_drawdown_pct:>7.1f} "
            f"{s.avg_exposure_pct:>9.1f} {s.rebalances:>6} {s.fees_paid:>8.2f}"
        )
