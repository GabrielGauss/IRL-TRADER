"""Phase 3 candidate strategies: no look-ahead, rule behaviour, grids, and
walk-forward over every registered strategy."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from trading_bot.backtest.grids import (
    GRIDS,
    DonchianGrid,
    MeanReversionGrid,
    MomentumGrid,
    ParamGrid,
)
from trading_bot.backtest.walkforward import walk_forward
from trading_bot.strategy.base import Signal, Strategy
from trading_bot.strategy.donchian import DonchianBreakoutStrategy
from trading_bot.strategy.mean_reversion import RsiMeanReversionStrategy
from trading_bot.strategy.momentum import MomentumStrategy


def _df(closes: list[float]) -> pd.DataFrame:
    closes_arr = np.asarray(closes, dtype=float)
    opens = np.concatenate([[closes_arr[0]], closes_arr[:-1]])
    return pd.DataFrame(
        {
            "open_time": pd.date_range("2024-01-01", periods=len(closes_arr), freq="1h"),
            "open": opens,
            "high": np.maximum(opens, closes_arr) * 1.001,
            "low": np.minimum(opens, closes_arr) * 0.999,
            "close": closes_arr,
            "volume": 1.0,
        }
    )


def _random_walk(n: int, seed: int = 11, drift: float = 0.0, vol: float = 0.01) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    return _df(list(100 * np.exp(np.cumsum(rng.normal(drift, vol, n)))))


# ---- no look-ahead: vectorized signals equal a bar-by-bar replay ----------

STRATEGIES = [
    DonchianBreakoutStrategy(entry_period=20, exit_period=10),
    DonchianBreakoutStrategy(entry_period=30, exit_period=10, trend_period=50),
    MomentumStrategy(lookback=24),
    MomentumStrategy(lookback=40, threshold_pct=1.0),
    RsiMeanReversionStrategy(rsi_period=2, oversold=10, overbought=70, trend_period=50),
    RsiMeanReversionStrategy(rsi_period=3, oversold=25, overbought=60, trend_period=30),
]


@pytest.mark.parametrize("strategy", STRATEGIES, ids=lambda s: type(s).__name__)
def test_vectorized_signals_match_bar_by_bar_replay(strategy: Strategy):
    df = _random_walk(260, vol=0.02)

    vectorized = strategy.generate_signals(df)
    replayed = Strategy.generate_signals(strategy, df)

    assert vectorized.tolist() == replayed.tolist()
    assert {Signal.BUY, Signal.SELL} <= set(vectorized), "fixture should exercise both sides"


@pytest.mark.parametrize("strategy", STRATEGIES, ids=lambda s: type(s).__name__)
def test_holds_until_enough_history(strategy: Strategy):
    df = _random_walk(strategy.min_lookback - 1)  # type: ignore[attr-defined]

    assert set(strategy.generate_signals(df)) <= {Signal.HOLD}
    assert strategy.generate_signal(df) is Signal.HOLD


# ---- Donchian ----------------------------------------------------------------


def test_donchian_buys_breakout_and_sells_breakdown():
    closes = [100.0] * 30 + [110.0] + [100.0] * 5 + [90.0]
    signals = DonchianBreakoutStrategy(entry_period=20, exit_period=5).generate_signals(_df(closes))

    assert signals.iloc[30] is Signal.BUY
    assert signals.iloc[-1] is Signal.SELL
    assert signals.iloc[25] is Signal.HOLD


def test_donchian_trend_filter_blocks_breakouts_below_the_average():
    # Long decline, then a pop that breaks the recent 5-bar high (~108) but is
    # still far below the 50-bar average (~140).
    closes = list(np.linspace(200, 100, 60)) + [115.0]
    df = _df(closes)

    unfiltered = DonchianBreakoutStrategy(entry_period=5, exit_period=3).generate_signals(df)
    filtered = DonchianBreakoutStrategy(
        entry_period=5, exit_period=3, trend_period=50
    ).generate_signals(df)

    assert unfiltered.iloc[-1] is Signal.BUY
    assert filtered.iloc[-1] is Signal.HOLD


@pytest.mark.parametrize("kwargs", [{"entry_period": 1}, {"exit_period": 1}, {"trend_period": -1}])
def test_donchian_rejects_bad_params(kwargs):
    with pytest.raises(ValueError):
        DonchianBreakoutStrategy(**kwargs)


# ---- momentum ----------------------------------------------------------------


def test_momentum_is_long_in_uptrend_and_exits_in_downtrend():
    up = _df(list(np.linspace(100, 150, 40)))
    down = _df(list(np.linspace(150, 100, 40)))
    strategy = MomentumStrategy(lookback=10)

    assert strategy.generate_signal(up) is Signal.BUY
    assert strategy.generate_signal(down) is Signal.SELL


def test_momentum_dead_band_holds_on_small_moves():
    flat = _df(list(np.linspace(100, 101, 40)))  # ~1% over the whole window

    assert MomentumStrategy(lookback=20, threshold_pct=2.0).generate_signal(flat) is Signal.HOLD
    assert MomentumStrategy(lookback=20).generate_signal(flat) is Signal.BUY


@pytest.mark.parametrize("kwargs", [{"lookback": 0}, {"threshold_pct": -1.0}])
def test_momentum_rejects_bad_params(kwargs):
    with pytest.raises(ValueError):
        MomentumStrategy(**kwargs)


# ---- mean reversion ----------------------------------------------------------


def test_mean_reversion_buys_dip_in_uptrend_and_sells_bounce():
    closes = list(np.linspace(100, 130, 60)) + [126.0, 123.0, 128.0, 131.0]
    signals = RsiMeanReversionStrategy(
        rsi_period=2, oversold=10, overbought=70, trend_period=50
    ).generate_signals(_df(closes))

    assert Signal.BUY in set(signals.iloc[60:62])
    assert Signal.BUY not in set(signals.iloc[:60])
    assert signals.iloc[-1] is Signal.SELL


def test_mean_reversion_ignores_dips_in_a_downtrend():
    closes = list(np.linspace(130, 100, 60)) + [96.0, 92.0]
    signals = RsiMeanReversionStrategy(rsi_period=2, trend_period=20).generate_signals(_df(closes))

    assert Signal.BUY not in set(signals)
    assert signals.iloc[-1] is Signal.SELL  # trend filter broken: flat is the only safe state


@pytest.mark.parametrize(
    "kwargs",
    [
        {"rsi_period": 1},
        {"oversold": 70.0, "overbought": 60.0},
        {"oversold": 0.0},
        {"trend_period": 1},
    ],
)
def test_mean_reversion_rejects_bad_params(kwargs):
    with pytest.raises(ValueError):
        RsiMeanReversionStrategy(**kwargs)


# ---- grids and walk-forward --------------------------------------------------


@pytest.mark.parametrize("name", sorted(GRIDS))
def test_every_registered_grid_builds_and_describes_each_combination(name):
    grid = GRIDS[name]()
    combos = grid.combinations()

    assert combos
    for params in combos:
        assert isinstance(grid.build(params), Strategy)
        assert grid.describe(params)


def test_donchian_grid_requires_exit_shorter_than_entry():
    combos = DonchianGrid(
        entry_period=(20,), exit_period=(10, 20, 50), trend_period=(0,)
    ).combinations()

    assert [c["exit_period"] for c in combos] == [10]


@pytest.mark.parametrize(
    "grid",
    [
        ParamGrid(fast_ema=(8,), slow_ema=(26,), rsi_oversold=(35.0,), rsi_overbought=(65.0,)),
        DonchianGrid(entry_period=(20,), exit_period=(10,), trend_period=(0,)),
        MomentumGrid(lookback=(24, 48), threshold_pct=(0.0,)),
        MeanReversionGrid(
            rsi_period=(2,), oversold=(10.0,), overbought=(70.0,), trend_period=(50,)
        ),
    ],
    ids=lambda g: type(g).__name__,
)
def test_walk_forward_runs_for_every_strategy(grid):
    df = _random_walk(900, drift=0.0003, vol=0.015)

    report = walk_forward(df, grid, interval="1h", train_bars=400, test_bars=250)

    assert len(report.folds) == 2
    for fold in report.folds:
        assert fold.params in grid.combinations()
        assert fold.test_start == fold.train_end
