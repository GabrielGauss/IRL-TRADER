"""Paired block bootstrap of the stitched out-of-sample curves."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from trading_bot.backtest.bootstrap import BootstrapResult, paired_block_bootstrap
from trading_bot.backtest.grids import VolTargetGrid
from trading_bot.backtest.metrics import sharpe_ratio
from trading_bot.backtest.weights import walk_forward_weights


def _walk(n: int, seed: int = 3) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    closes = 100 * np.exp(np.cumsum(rng.normal(0.0005, 0.03, n)))
    opens = np.concatenate([[100.0], closes[:-1]])
    return pd.DataFrame(
        {
            "open_time": pd.date_range("2022-01-01", periods=n, freq="1D"),
            "open": opens,
            "high": np.maximum(opens, closes),
            "low": np.minimum(opens, closes),
            "close": closes,
            "volume": 1.0,
        }
    )


def _returns(n: int, mean: float, vol: float, seed: int) -> np.ndarray:
    return np.random.default_rng(seed).normal(mean, vol, n)


def test_report_exposes_stitched_returns_matching_its_summary():
    df = _walk(700)
    grid = VolTargetGrid(trend_period=(0, 50), vol_period=(20,), target_vol=(0.5,))

    report = walk_forward_weights(df, grid, interval="1d", train_bars=300, test_bars=100)

    assert len(report.oos_returns) == len(report.benchmark_returns) == 400
    equity = 1000.0 * np.cumprod(1 + np.asarray(report.oos_returns))
    assert sharpe_ratio((1000.0, *equity), "1d") == pytest.approx(report.oos.sharpe)


def test_identical_series_give_a_zero_difference():
    r = _returns(500, 0.001, 0.02, seed=1)

    result = paired_block_bootstrap(r, r, block=10, n_boot=500, periods_per_year=365, seed=0)

    assert result.sharpe_diff == pytest.approx(0.0)
    assert result.sharpe_diff_ci == pytest.approx((0.0, 0.0))
    assert result.dd_ratio_ci == pytest.approx((1.0, 1.0))


def test_a_clearly_better_series_has_a_ci_above_zero():
    bench = _returns(2000, 0.0, 0.03, seed=2)
    strat = bench * 0.5 + 0.002  # half the risk plus a large, steady edge

    result = paired_block_bootstrap(strat, bench, block=20, n_boot=1000, periods_per_year=365)

    assert result.sharpe_diff_ci[0] > 0
    assert result.p_sharpe_diff_le_zero < 0.01


def test_noise_gives_a_ci_straddling_zero():
    bench = _returns(500, 0.0005, 0.03, seed=3)
    strat = bench + _returns(500, 0.0, 0.01, seed=4)

    result = paired_block_bootstrap(strat, bench, block=20, n_boot=1000, periods_per_year=365)

    low, high = result.sharpe_diff_ci
    assert low < 0 < high


def test_point_estimate_matches_the_annualized_sharpe():
    strat = _returns(300, 0.001, 0.02, seed=5)
    bench = _returns(300, 0.0005, 0.03, seed=6)

    result = paired_block_bootstrap(strat, bench, block=5, n_boot=100, periods_per_year=365)

    def sharpe(r: np.ndarray) -> float:
        return float(r.mean() / r.std(ddof=1) * math.sqrt(365))

    assert result.sharpe_diff == pytest.approx(sharpe(strat) - sharpe(bench))
    assert isinstance(result, BootstrapResult)


def test_same_seed_is_reproducible():
    strat = _returns(300, 0.001, 0.02, seed=7)
    bench = _returns(300, 0.0005, 0.03, seed=8)
    kwargs = {"block": 10, "n_boot": 200, "periods_per_year": 365, "seed": 42}

    assert paired_block_bootstrap(strat, bench, **kwargs) == paired_block_bootstrap(
        strat, bench, **kwargs
    )


@pytest.mark.parametrize(
    "kwargs",
    [{"block": 0}, {"block": 400}, {"n_boot": 0}, {"periods_per_year": 0}],
)
def test_rejects_bad_params(kwargs):
    r = _returns(300, 0.0, 0.02, seed=9)
    params = {"block": 10, "n_boot": 100, "periods_per_year": 365, **kwargs}

    with pytest.raises(ValueError):
        paired_block_bootstrap(r, r, **params)


def test_rejects_mismatched_lengths():
    with pytest.raises(ValueError):
        paired_block_bootstrap(
            _returns(300, 0, 0.02, 1), _returns(299, 0, 0.02, 2), block=10, periods_per_year=365
        )


def test_cli_parses_bootstrap_blocks():
    from trading_bot.cli_weights import _parse_blocks

    assert _parse_blocks("") == []
    assert _parse_blocks("5, 21,63") == [5, 21, 63]
    with pytest.raises(SystemExit):
        _parse_blocks("5,x")


def test_cli_prints_one_row_per_block(capsys):
    from trading_bot.cli_weights import _print_bootstrap

    r = _returns(300, 0.001, 0.02, seed=10)
    b = _returns(300, 0.0005, 0.03, seed=11)
    results = [
        paired_block_bootstrap(r, b, block=k, n_boot=50, periods_per_year=365) for k in (5, 21)
    ]

    _print_bootstrap(results)

    lines = capsys.readouterr().out.strip().splitlines()
    assert "50 resamples" in lines[0]
    assert [line.split()[0] for line in lines[2:]] == ["5", "21"]
