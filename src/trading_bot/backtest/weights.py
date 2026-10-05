"""Backtesting for strategies that hold a fraction of equity, not all or nothing.

A weight strategy outputs a target weight in [0, 1] per bar, computed only
from bars up to and including it. The engine rebalances toward the previous
bar's target at the next bar's open (no trading on a price you only learn at
the close), paying fees and slippage on the traded notional. A rebalance
band skips trades smaller than ``rebalance_band`` of equity so the costs of
constant nudging don't eat the strategy.

Walk-forward here judges the stitched out-of-sample equity curve (all test
windows chained) against buy and hold over the same windows, so max drawdown
is measured across the whole period rather than averaged per fold.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol

import numpy as np
import pandas as pd

from trading_bot.backtest.grids import Params
from trading_bot.backtest.metrics import sharpe_ratio


class WeightStrategy(Protocol):
    @property
    def min_lookback(self) -> int: ...

    def target_weights(self, df: pd.DataFrame) -> pd.Series: ...


class WeightGrid(Protocol):
    def combinations(self) -> list[Params]: ...

    def build(self, params: Params) -> WeightStrategy: ...

    def describe(self, params: Params) -> str: ...


@dataclass(frozen=True)
class WeightResult:
    equity_curve: tuple[float, ...]
    weights_held: tuple[float, ...]
    rebalances: int
    fees_paid: float
    turnover: float  # traded notional / mean equity

    @property
    def total_return_pct(self) -> float:
        return (
            (self.equity_curve[-1] / self.equity_curve[0] - 1) * 100 if self.equity_curve else 0.0
        )


@dataclass(frozen=True)
class WeightSummary:
    total_return_pct: float
    sharpe: float
    max_drawdown_pct: float
    avg_exposure_pct: float
    rebalances: int
    fees_paid: float


def simulate_weights(
    df: pd.DataFrame,
    weights: pd.Series,
    *,
    start: int = 0,
    initial_balance: float = 1000.0,
    fee_bps: float = 10.0,
    slippage_bps: float = 5.0,
    rebalance_band: float = 0.05,
) -> WeightResult:
    """Hold ``weights`` (decided at each close) from bar ``start``, flat at start."""
    if initial_balance <= 0:
        raise ValueError("initial_balance must be positive")
    if not 0 <= rebalance_band < 1:
        raise ValueError("rebalance_band must be in [0, 1)")
    target = weights.to_numpy(dtype=float)
    if np.isnan(target).any() or (target < 0).any() or (target > 1).any():
        raise ValueError("weights must be in [0, 1] with no NaN")
    fee = fee_bps / 10_000
    slip = slippage_bps / 10_000
    opens = df["open"].to_numpy(dtype=float)
    closes = df["close"].to_numpy(dtype=float)

    cash, units = initial_balance, 0.0
    equity: list[float] = []
    held: list[float] = []
    rebalances, fees, traded = 0, 0.0, 0.0
    for i in range(start, len(df)):
        if i > start:
            want = target[i - 1]
            value = cash + units * opens[i]
            current = units * opens[i] / value if value > 0 else 0.0
            closing = want == 0.0 and units > 0
            if closing or abs(want - current) > rebalance_band:
                delta_value = want * value - units * opens[i]
                if delta_value > 0:  # buy
                    price = opens[i] * (1 + slip)
                    cost = delta_value * fee
                    units += (delta_value - cost) / price
                    cash -= delta_value
                else:  # sell
                    price = opens[i] * (1 - slip)
                    sold_units = units if closing else -delta_value / opens[i]
                    gross = sold_units * price
                    cost = gross * fee
                    units -= sold_units
                    cash += gross - cost
                rebalances += 1
                fees += cost
                traded += abs(delta_value)
        value = cash + units * closes[i]
        equity.append(value)
        held.append(units * closes[i] / value if value > 0 else 0.0)
    mean_equity = float(np.mean(equity)) if equity else initial_balance
    return WeightResult(
        equity_curve=tuple(equity),
        weights_held=tuple(held),
        rebalances=rebalances,
        fees_paid=fees,
        turnover=traded / mean_equity if mean_equity else 0.0,
    )


def max_drawdown_pct(equity: tuple[float, ...] | list[float]) -> float:
    peak, worst = -math.inf, 0.0
    for value in equity:
        peak = max(peak, value)
        if peak > 0:
            worst = max(worst, (peak - value) / peak * 100)
    return worst


def summarize_weights(result: WeightResult, interval: str) -> WeightSummary:
    held = result.weights_held
    return WeightSummary(
        total_return_pct=result.total_return_pct,
        sharpe=sharpe_ratio(result.equity_curve, interval),
        max_drawdown_pct=max_drawdown_pct(result.equity_curve),
        avg_exposure_pct=sum(held) / len(held) * 100 if held else 0.0,
        rebalances=result.rebalances,
        fees_paid=result.fees_paid,
    )


@dataclass(frozen=True)
class WeightFold:
    test_start: int
    test_end: int
    params: Params
    in_sample_sharpe: float
    oos: WeightSummary
    benchmark: WeightSummary


@dataclass(frozen=True)
class WeightWalkForwardReport:
    folds: tuple[WeightFold, ...]
    oos: WeightSummary  # stitched across all test windows
    benchmark: WeightSummary  # buy and hold over the same windows, same costs

    @property
    def mean_in_sample_sharpe(self) -> float:
        return sum(f.in_sample_sharpe for f in self.folds) / len(self.folds)


# Set before any run (2026-10-05): the volatility-targeted core must at least
# match buy and hold's risk-adjusted return and cut its worst drawdown by a
# quarter or more. Beating it on raw return is not the goal.
MAX_DRAWDOWN_RATIO = 0.75


def weight_verdict(oos: WeightSummary, benchmark: WeightSummary) -> str:
    sharpe_ok = oos.sharpe >= benchmark.sharpe
    dd_ok = oos.max_drawdown_pct <= benchmark.max_drawdown_pct * MAX_DRAWDOWN_RATIO
    if sharpe_ok and dd_ok:
        return (
            "PASSES: out-of-sample Sharpe at least buy & hold's and max drawdown "
            f"<= {MAX_DRAWDOWN_RATIO:.0%} of buy & hold's (after costs)."
        )
    reasons = []
    if not sharpe_ok:
        reasons.append(f"Sharpe {oos.sharpe:.2f} < buy & hold {benchmark.sharpe:.2f}")
    if not dd_ok:
        reasons.append(
            f"max drawdown {oos.max_drawdown_pct:.1f}% > {MAX_DRAWDOWN_RATIO:.0%} of "
            f"buy & hold's {benchmark.max_drawdown_pct:.1f}%"
        )
    return "FAILS: " + "; ".join(reasons) + "."


def walk_forward_weights(
    df: pd.DataFrame,
    grid: WeightGrid,
    *,
    interval: str,
    train_bars: int,
    test_bars: int,
    initial_balance: float = 1000.0,
    fee_bps: float = 10.0,
    slippage_bps: float = 5.0,
    rebalance_band: float = 0.05,
) -> WeightWalkForwardReport:
    n_folds = (len(df) - train_bars) // test_bars
    if train_bars <= 0 or test_bars <= 0 or n_folds < 1:
        raise ValueError(
            f"need at least train_bars + test_bars = {train_bars + test_bars} bars, got {len(df)}"
        )
    costs = {"fee_bps": fee_bps, "slippage_bps": slippage_bps, "rebalance_band": rebalance_band}
    folds: list[WeightFold] = []
    oos_returns: list[np.ndarray] = []
    bench_returns: list[np.ndarray] = []
    for k in range(n_folds):
        start = k * test_bars
        train_end = start + train_bars
        test_end = train_end + test_bars
        train = df.iloc[start:train_end].reset_index(drop=True)
        window = df.iloc[start:test_end].reset_index(drop=True)

        params, is_sharpe = _best_on_train(train, grid, interval, costs, initial_balance)
        strategy = grid.build(params)
        oos = simulate_weights(
            window,
            strategy.target_weights(window),
            start=train_bars,
            initial_balance=initial_balance,
            **costs,
        )
        bench = simulate_weights(
            window,
            pd.Series(1.0, index=window.index),
            start=train_bars,
            initial_balance=initial_balance,
            **costs,
        )
        folds.append(
            WeightFold(
                test_start=train_end,
                test_end=test_end,
                params=params,
                in_sample_sharpe=is_sharpe,
                oos=summarize_weights(oos, interval),
                benchmark=summarize_weights(bench, interval),
            )
        )
        oos_returns.append(_bar_returns(oos.equity_curve, initial_balance))
        bench_returns.append(_bar_returns(bench.equity_curve, initial_balance))

    return WeightWalkForwardReport(
        folds=tuple(folds),
        oos=_stitched(oos_returns, folds, interval, initial_balance, oos_side=True),
        benchmark=_stitched(bench_returns, folds, interval, initial_balance, oos_side=False),
    )


def _best_on_train(
    train: pd.DataFrame,
    grid: WeightGrid,
    interval: str,
    costs: dict[str, float],
    initial_balance: float,
) -> tuple[Params, float]:
    best: tuple[Params, float] | None = None
    for params in grid.combinations():
        strategy = grid.build(params)
        result = simulate_weights(
            train,
            strategy.target_weights(train),
            start=strategy.min_lookback,
            initial_balance=initial_balance,
            **costs,
        )
        sharpe = sharpe_ratio(result.equity_curve, interval)
        if best is None or sharpe > best[1]:
            best = (params, sharpe)
    if best is None:
        raise ValueError("the grid produced no parameter combinations")
    return best


def _bar_returns(equity: tuple[float, ...], initial_balance: float) -> np.ndarray:
    curve = np.asarray((initial_balance, *equity), dtype=float)
    return curve[1:] / curve[:-1] - 1


def _stitched(
    returns: list[np.ndarray],
    folds: list[WeightFold],
    interval: str,
    initial_balance: float,
    *,
    oos_side: bool,
) -> WeightSummary:
    chained = np.concatenate(returns)
    equity = tuple(float(v) for v in initial_balance * np.cumprod(1 + chained))
    summaries = [f.oos if oos_side else f.benchmark for f in folds]
    return WeightSummary(
        total_return_pct=(equity[-1] / initial_balance - 1) * 100,
        sharpe=sharpe_ratio((initial_balance, *equity), interval),
        max_drawdown_pct=max_drawdown_pct((initial_balance, *equity)),
        avg_exposure_pct=sum(s.avg_exposure_pct for s in summaries) / len(summaries),
        rebalances=sum(s.rebalances for s in summaries),
        fees_paid=sum(s.fees_paid for s in summaries),
    )
