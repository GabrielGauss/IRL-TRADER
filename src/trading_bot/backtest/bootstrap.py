"""How much of a walk-forward result could be luck? A paired block bootstrap.

The strategy and buy-and-hold return series are resampled on the same
indices, so their correlation is kept, in circular blocks, so volatility
clustering inside a block is kept. Each resample gives a Sharpe difference
and a max-drawdown ratio; the spread of those is the uncertainty.

It resamples days only. It does not capture the uncertainty of re-selecting
parameters on each fold, so the true uncertainty is larger than reported.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from trading_bot.backtest.weights import MAX_DRAWDOWN_RATIO

DEFAULT_SEED = 20261008


@dataclass(frozen=True)
class BootstrapResult:
    block: int
    n_boot: int
    sharpe_diff: float  # point estimate, strategy minus benchmark
    sharpe_diff_ci: tuple[float, float]  # 95% percentile interval
    p_sharpe_diff_le_zero: float
    dd_ratio: float  # point estimate, strategy max drawdown / benchmark's
    dd_ratio_ci: tuple[float, float]
    dd_bar: float  # the pre-registered drawdown ratio the verdict uses
    p_dd_ratio_above_bar: float


def paired_block_bootstrap(
    strategy_returns: Sequence[float] | np.ndarray,
    benchmark_returns: Sequence[float] | np.ndarray,
    *,
    block: int,
    periods_per_year: float,
    n_boot: int = 10_000,
    seed: int = DEFAULT_SEED,
    dd_bar: float = MAX_DRAWDOWN_RATIO,
) -> BootstrapResult:
    strat = np.asarray(strategy_returns, dtype=float)
    bench = np.asarray(benchmark_returns, dtype=float)
    _validate(strat, bench, block, n_boot, periods_per_year)
    ann = math.sqrt(periods_per_year)

    idx = _block_indices(len(strat), block, n_boot, np.random.default_rng(seed))
    diffs = _sharpe(strat[idx], ann) - _sharpe(bench[idx], ann)
    ratios = _max_dd(strat[idx]) / _max_dd(bench[idx])
    return BootstrapResult(
        block=block,
        n_boot=n_boot,
        sharpe_diff=float(_sharpe(strat, ann) - _sharpe(bench, ann)),
        sharpe_diff_ci=_ci(diffs),
        p_sharpe_diff_le_zero=float(np.mean(diffs <= 0)),
        dd_ratio=float(_max_dd(strat) / _max_dd(bench)),
        dd_ratio_ci=_ci(ratios),
        dd_bar=dd_bar,
        p_dd_ratio_above_bar=float(np.mean(ratios > dd_bar)),
    )


def _validate(
    strat: np.ndarray, bench: np.ndarray, block: int, n_boot: int, periods_per_year: float
) -> None:
    if strat.shape != bench.shape or strat.ndim != 1:
        raise ValueError(
            f"need two 1-D series of equal length, got {strat.shape} and {bench.shape}"
        )
    if not 1 <= block <= len(strat) // 2:
        raise ValueError(f"block must be in [1, {len(strat) // 2}], got {block}")
    if n_boot < 1:
        raise ValueError("n_boot must be positive")
    if periods_per_year <= 0:
        raise ValueError("periods_per_year must be positive")


def _block_indices(n: int, block: int, n_boot: int, rng: np.random.Generator) -> np.ndarray:
    n_blocks = math.ceil(n / block)
    starts = rng.integers(0, n, size=(n_boot, n_blocks))
    idx = (starts[:, :, None] + np.arange(block)[None, None, :]) % n
    return idx.reshape(n_boot, -1)[:, :n]


def _sharpe(r: np.ndarray, ann: float) -> np.ndarray:
    std = r.std(axis=-1, ddof=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(std > 0, r.mean(axis=-1) / std * ann, 0.0)


def _max_dd(r: np.ndarray) -> np.ndarray:
    equity = np.cumprod(1 + r, axis=-1)
    peak = np.maximum.accumulate(equity, axis=-1)
    return ((peak - equity) / peak).max(axis=-1)


def _ci(samples: np.ndarray) -> tuple[float, float]:
    low, high = np.percentile(samples, [2.5, 97.5])
    return float(low), float(high)
