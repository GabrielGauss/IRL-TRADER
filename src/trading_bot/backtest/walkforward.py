"""Walk-forward evaluation: choose parameters on a training window, then judge
them only on the following, unseen test window, and roll forward.

In-sample results are optimistic by construction (the parameters were picked
because they did well there). The out-of-sample numbers stitched across folds
are the honest estimate, and they are compared with buy-and-hold over the
same test windows at the same costs and position size.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd

from trading_bot.backtest.engine import simulate
from trading_bot.backtest.grids import ParamGrid, Params, StrategyGrid
from trading_bot.backtest.metrics import Summary, buy_and_hold, summarize

__all__ = ["Fold", "ParamGrid", "WalkForwardReport", "verdict", "walk_forward"]


@dataclass(frozen=True)
class Fold:
    train_start: int
    train_end: int
    test_start: int
    test_end: int
    params: Params
    in_sample: Summary
    oos: Summary
    benchmark: Summary


@dataclass(frozen=True)
class WalkForwardReport:
    folds: tuple[Fold, ...]

    @property
    def oos_return_pct(self) -> float:
        return _compound(f.oos.total_return_pct for f in self.folds)

    @property
    def benchmark_return_pct(self) -> float:
        return _compound(f.benchmark.total_return_pct for f in self.folds)

    @property
    def mean_in_sample_sharpe(self) -> float:
        return sum(f.in_sample.sharpe for f in self.folds) / len(self.folds)

    @property
    def mean_oos_sharpe(self) -> float:
        return sum(f.oos.sharpe for f in self.folds) / len(self.folds)

    @property
    def mean_benchmark_sharpe(self) -> float:
        return sum(f.benchmark.sharpe for f in self.folds) / len(self.folds)

    @property
    def oos_trades(self) -> int:
        return sum(f.oos.num_trades for f in self.folds)

    @property
    def mean_oos_exposure_pct(self) -> float:
        return sum(f.oos.exposure_pct for f in self.folds) / len(self.folds)


# Below this many out-of-sample round trips, a return difference says more
# about luck and time-in-market than about edge.
MIN_OOS_TRADES_FOR_VERDICT = 30


def verdict(oos_return: float, benchmark_return: float, oos_trades: int) -> str:
    if oos_trades < MIN_OOS_TRADES_FOR_VERDICT:
        return (
            f"INCONCLUSIVE: only {oos_trades} out-of-sample trades "
            f"(< {MIN_OOS_TRADES_FOR_VERDICT}); a difference vs buy & hold mostly reflects "
            "time spent in cash, not edge."
        )
    if oos_return > benchmark_return:
        return "BEATS buy & hold out of sample (after costs)."
    return "DOES NOT BEAT buy & hold out of sample (after costs)."


def _compound(returns_pct) -> float:
    return (math.prod(1 + r / 100 for r in returns_pct) - 1) * 100


def walk_forward(
    df: pd.DataFrame,
    grid: StrategyGrid,
    *,
    interval: str,
    train_bars: int,
    test_bars: int,
    fee_bps: float = 10.0,
    slippage_bps: float = 5.0,
    initial_balance: float = 1000.0,
    position_size_fraction: float = 1.0,
) -> WalkForwardReport:
    n_folds = (len(df) - train_bars) // test_bars
    if train_bars <= 0 or test_bars <= 0 or n_folds < 1:
        raise ValueError(
            f"need at least train_bars + test_bars = {train_bars + test_bars} bars, got {len(df)}"
        )
    costs = {
        "initial_balance": initial_balance,
        "position_size_fraction": position_size_fraction,
        "fee_bps": fee_bps,
        "slippage_bps": slippage_bps,
    }
    folds = []
    for k in range(n_folds):
        start = k * test_bars
        train_end = start + train_bars
        test_end = train_end + test_bars
        train = df.iloc[start:train_end].reset_index(drop=True)
        window = df.iloc[start:test_end].reset_index(drop=True)

        best_params, best_summary = _best_on_train(train, grid, interval, costs)
        strategy = grid.build(best_params)
        # Indicators see the training bars as history; trading starts flat at the test window.
        oos = simulate(
            window, strategy.generate_signals(window), start=train_bars, fill="next_open", **costs
        )
        bench = buy_and_hold(df.iloc[train_end:test_end].reset_index(drop=True), **costs)
        folds.append(
            Fold(
                train_start=start,
                train_end=train_end,
                test_start=train_end,
                test_end=test_end,
                params=best_params,
                in_sample=best_summary,
                oos=summarize(oos, interval),
                benchmark=summarize(bench, interval),
            )
        )
    return WalkForwardReport(folds=tuple(folds))


def _best_on_train(
    train: pd.DataFrame, grid: StrategyGrid, interval: str, costs: dict[str, float]
) -> tuple[Params, Summary]:
    best: tuple[Params, Summary] | None = None
    for params in grid.combinations():
        strategy = grid.build(params)
        result = simulate(
            train,
            strategy.generate_signals(train),
            start=getattr(strategy, "min_lookback", 0),
            fill="next_open",
            **costs,
        )
        summary = summarize(result, interval)
        if best is None or summary.sharpe > best[1].sharpe:
            best = (params, summary)
    assert best is not None, "ParamGrid produced no valid combinations"
    return best
