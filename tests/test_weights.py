"""Fractional-position backtesting and the volatility-targeted trend core."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from trading_bot.backtest.grids import VolTargetGrid
from trading_bot.backtest.weights import (
    WeightSummary,
    max_drawdown_pct,
    simulate_weights,
    walk_forward_weights,
    weight_verdict,
)
from trading_bot.strategy.vol_target import VolTargetTrendStrategy


def _df(opens: list[float], closes: list[float] | None = None) -> pd.DataFrame:
    closes = closes if closes is not None else opens
    return pd.DataFrame(
        {
            "open_time": pd.date_range("2022-01-01", periods=len(opens), freq="1D"),
            "open": opens,
            "high": np.maximum(opens, closes),
            "low": np.minimum(opens, closes),
            "close": closes,
            "volume": 1.0,
        }
    )


def _walk(n: int, seed: int = 3, drift: float = 0.0005, vol: float = 0.03) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    closes = 100 * np.exp(np.cumsum(rng.normal(drift, vol, n)))
    opens = np.concatenate([[100.0], closes[:-1]])
    return _df(list(opens), list(closes))


def _w(values: list[float]) -> pd.Series:
    return pd.Series(values, dtype=float)


# ---- engine ------------------------------------------------------------------


def test_weight_decided_at_a_close_trades_at_the_next_open():
    # Gap up from 100 to 200 at bar 2's open. A weight of 1 decided at bar 1's
    # close buys at bar 2's open (200), so the gap is not captured.
    df = _df(opens=[100, 100, 200, 200], closes=[100, 100, 200, 220])
    result = simulate_weights(df, _w([0, 1, 1, 1]), fee_bps=0, slippage_bps=0)

    assert result.equity_curve == pytest.approx((1000, 1000, 1000, 1100))
    assert result.rebalances == 1


def test_zero_weight_stays_in_cash():
    df = _walk(50)
    result = simulate_weights(df, _w([0.0] * 50))

    assert set(result.equity_curve) == {1000.0}
    assert result.rebalances == 0 and result.fees_paid == 0


def test_half_weight_captures_half_the_move_without_costs():
    df = _df(opens=[100, 100, 100], closes=[100, 100, 150])
    result = simulate_weights(df, _w([0.5, 0.5, 0.5]), fee_bps=0, slippage_bps=0)

    assert result.equity_curve[-1] == pytest.approx(1250)
    assert result.weights_held[-1] == pytest.approx(750 / 1250)


def test_costs_are_charged_on_traded_notional():
    df = _df(opens=[100] * 4)
    result = simulate_weights(df, _w([1, 1, 0, 0]), fee_bps=10, slippage_bps=0)

    # buy 1000 (fee 1.0), sell ~999 back (fee ~0.999)
    assert result.fees_paid == pytest.approx(1.0 + 0.999, rel=1e-3)
    assert result.equity_curve[-1] == pytest.approx(1000 - result.fees_paid, rel=1e-6)
    assert result.weights_held[-1] == 0.0


def test_band_skips_small_rebalances_but_always_closes_fully():
    df = _df(opens=[100] * 5)
    result = simulate_weights(df, _w([0.5, 0.53, 0.47, 0.0, 0.0]), rebalance_band=0.05)

    assert result.rebalances == 2  # open at 0.5, then close to 0
    assert result.weights_held[-1] == 0.0


def test_start_ignores_earlier_bars():
    df = _df(opens=[100, 100, 100, 100], closes=[100, 300, 100, 110])
    result = simulate_weights(df, _w([1, 1, 1, 1]), start=2, fee_bps=0, slippage_bps=0)

    assert len(result.equity_curve) == 2
    assert result.equity_curve[0] == 1000  # flat on the first evaluated bar
    assert result.equity_curve[1] == pytest.approx(1100)


@pytest.mark.parametrize("bad", [[1.2, 0], [-0.1, 0], [float("nan"), 0]])
def test_invalid_weights_raise(bad):
    with pytest.raises(ValueError):
        simulate_weights(_df([100, 100]), _w(bad))


def test_max_drawdown():
    assert max_drawdown_pct([100, 120, 60, 130, 65]) == pytest.approx(50.0)
    assert max_drawdown_pct([100, 101, 102]) == 0.0


# ---- strategy ------------------------------------------------------------------


@pytest.mark.parametrize(
    "strategy",
    [
        VolTargetTrendStrategy(trend_period=50, vol_period=20, target_vol=0.5),
        VolTargetTrendStrategy(trend_period=0, vol_period=10, target_vol=0.3),
    ],
)
def test_weights_use_no_future_data(strategy):
    df = _walk(160)

    full = strategy.target_weights(df)
    prefix = [strategy.target_weights(df.iloc[: i + 1]).iloc[-1] for i in range(len(df))]

    assert full.tolist() == pytest.approx(prefix)
    assert (full[: strategy.min_lookback - 1] == 0).all()
    assert full.between(0, 1).all()


def test_weight_is_zero_below_trend_and_shrinks_as_volatility_rises():
    falling = _df(list(np.linspace(200, 100, 120)))
    calm = _walk(200, seed=1, drift=0.003, vol=0.01)
    wild = _walk(200, seed=1, drift=0.003, vol=0.06)
    strategy = VolTargetTrendStrategy(trend_period=50, vol_period=20, target_vol=0.4)

    assert strategy.target_weights(falling).iloc[-1] == 0.0
    assert strategy.target_weights(wild).iloc[-1] < strategy.target_weights(calm).iloc[-1]


def test_components_explain_the_weight():
    comp = VolTargetTrendStrategy(trend_period=20, vol_period=10).components(_walk(60))

    assert set(comp.columns) == {"close", "sma", "trend_on", "realized_vol", "weight"}


@pytest.mark.parametrize(
    "kwargs",
    [{"trend_period": 1}, {"vol_period": 1}, {"target_vol": 0}, {"max_weight": 1.5}],
)
def test_strategy_rejects_bad_params(kwargs):
    with pytest.raises(ValueError):
        VolTargetTrendStrategy(**kwargs)


# ---- walk-forward and verdict ----------------------------------------------------


def test_walk_forward_stitches_every_test_window():
    df = _walk(700)
    grid = VolTargetGrid(trend_period=(0, 50), vol_period=(20,), target_vol=(0.5,))

    report = walk_forward_weights(df, grid, interval="1d", train_bars=300, test_bars=100)

    assert len(report.folds) == 4
    assert all(f.params in grid.combinations() for f in report.folds)
    assert report.benchmark.avg_exposure_pct > 90
    assert report.oos.max_drawdown_pct >= 0


def test_walk_forward_needs_enough_bars():
    with pytest.raises(ValueError):
        walk_forward_weights(
            _walk(100), VolTargetGrid(), interval="1d", train_bars=90, test_bars=20
        )


def _summary(sharpe: float, dd: float) -> WeightSummary:
    return WeightSummary(0.0, sharpe, dd, 50.0, 10, 1.0)


def test_verdict_requires_both_sharpe_and_drawdown():
    bench = _summary(1.0, 80.0)

    assert weight_verdict(_summary(1.1, 50.0), bench).startswith("PASSES")
    assert "Sharpe" in weight_verdict(_summary(0.9, 50.0), bench)
    assert "drawdown" in weight_verdict(_summary(1.2, 70.0), bench)
