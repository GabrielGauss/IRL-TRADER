"""Phase 2: an honest backtester -- realistic fills, costs, a benchmark, and
out-of-sample evaluation."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from trading_bot.backtest.engine import run_backtest
from trading_bot.backtest.metrics import (
    annualization_factor,
    buy_and_hold,
    sharpe_ratio,
    summarize,
)
from trading_bot.backtest.walkforward import ParamGrid, walk_forward
from trading_bot.strategy.base import Signal, Strategy
from trading_bot.strategy.ema_rsi import EmaRsiStrategy


class StubStrategy(Strategy):
    def __init__(self, signal_by_window_length: dict[int, Signal]):
        self._signals = signal_by_window_length
        self.min_lookback = 0

    def generate_signal(self, df: pd.DataFrame) -> Signal:
        return self._signals.get(len(df), Signal.HOLD)


def _df(opens: list[float], closes: list[float] | None = None) -> pd.DataFrame:
    closes = closes if closes is not None else opens
    return pd.DataFrame(
        {
            "open_time": pd.date_range("2024-01-01", periods=len(opens), freq="1h"),
            "open": opens,
            "high": [max(o, c) for o, c in zip(opens, closes)],
            "low": [min(o, c) for o, c in zip(opens, closes)],
            "close": closes,
            "volume": [1.0] * len(opens),
        }
    )


def _random_walk(n: int, seed: int = 7, drift: float = 0.0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    closes = 100 * np.exp(np.cumsum(rng.normal(drift, 0.01, n)))
    opens = np.concatenate([[100.0], closes[:-1]])
    return _df(list(opens), list(closes))


# ---- realistic fills and costs --------------------------------------------


def test_next_open_fill_executes_on_the_following_bar_not_the_signal_bar():
    # Signal on bar index 1 (window length 2); next bar opens at 110.
    df = _df(opens=[100, 100, 110, 120], closes=[100, 105, 115, 120])
    strategy = StubStrategy({2: Signal.BUY, 3: Signal.SELL})

    result = run_backtest(strategy, df, position_size_fraction=1.0, fill="next_open")

    trade = result.trades[0]
    assert trade.entry_price == pytest.approx(110.0)  # open of bar 2, not close 105
    assert trade.exit_price == pytest.approx(120.0)  # open of bar 3


def test_signal_on_last_bar_is_not_filled_with_next_open():
    df = _df(opens=[100, 100, 100])
    strategy = StubStrategy({3: Signal.BUY})

    result = run_backtest(strategy, df, fill="next_open")

    assert result.trades == ()
    assert result.final_equity == pytest.approx(1000.0)


def test_fees_and_slippage_reduce_a_round_trip():
    df = _df(opens=[100, 100, 100, 100])
    strategy = StubStrategy({1: Signal.BUY, 2: Signal.SELL})

    free = run_backtest(strategy, df, position_size_fraction=1.0, fill="next_open")
    costly = run_backtest(
        strategy, df, position_size_fraction=1.0, fill="next_open", fee_bps=10, slippage_bps=5
    )

    assert free.final_equity == pytest.approx(1000.0)
    # Buy at 100.05, sell at 99.95, 10 bps fee on each side.
    expected = 1000 / 100.05 * (1 - 0.001) * 99.95 * (1 - 0.001)
    assert costly.final_equity == pytest.approx(expected, rel=1e-9)
    assert costly.trades[0].fees > 0


def test_legacy_defaults_are_unchanged():
    df = _df(opens=[1, 1, 1], closes=[100, 105, 110])
    strategy = StubStrategy({1: Signal.BUY, 3: Signal.SELL})

    result = run_backtest(strategy, df, position_size_fraction=1.0)

    assert result.trades[0].entry_price == 100 and result.trades[0].exit_price == 110


def test_rejects_unknown_fill_mode():
    with pytest.raises(ValueError, match="fill"):
        run_backtest(StubStrategy({}), _df([1, 1]), fill="whenever")


# ---- vectorized signals == bar-by-bar signals (no look-ahead) --------------


def test_ema_rsi_vectorized_signals_match_bar_by_bar():
    df = _random_walk(400, seed=2, drift=0.0005)  # chosen so both BUY and SELL occur
    strategy = EmaRsiStrategy(
        fast_ema=5, slow_ema=20, rsi_period=7, rsi_oversold=40, rsi_overbought=60
    )

    vectorized = strategy.generate_signals(df)
    looped = [strategy.generate_signal(df.iloc[: i + 1]) for i in range(len(df))]

    assert list(vectorized) == looped
    assert {Signal.BUY, Signal.SELL} <= set(looped)  # the test actually exercises both


def test_default_generate_signals_falls_back_to_bar_by_bar():
    strategy = StubStrategy({2: Signal.BUY})

    signals = strategy.generate_signals(_df([1, 1, 1]))

    assert list(signals) == [Signal.HOLD, Signal.BUY, Signal.HOLD]


# ---- metrics and benchmark --------------------------------------------------


def test_annualization_factor_by_interval():
    assert annualization_factor("1h") == pytest.approx(24 * 365)
    assert annualization_factor("1d") == pytest.approx(365)
    with pytest.raises(ValueError):
        annualization_factor("7x")


def test_sharpe_ratio_zero_for_flat_and_sign_follows_drift():
    assert sharpe_ratio([100.0] * 10, "1h") == 0.0
    assert sharpe_ratio([100, 101, 102, 101.5, 103], "1h") > 0
    assert sharpe_ratio([100, 99, 98, 98.5, 97], "1h") < 0


def test_buy_and_hold_benchmark_pays_the_same_costs_and_size():
    df = _df(opens=[100, 100, 120], closes=[100, 110, 120])

    bench = buy_and_hold(
        df, initial_balance=1000, position_size_fraction=0.5, fee_bps=10, slippage_bps=0
    )

    # Buys half the balance at the first open (fee 10 bps), marked at the last close, then sold with fee.
    units = 500 * (1 - 0.001) / 100
    assert bench.final_equity == pytest.approx(500 + units * 120 * (1 - 0.001))


def test_summarize_reports_exposure_and_profit_factor():
    df = _df(opens=[100, 100, 110, 110, 100, 100])
    strategy = StubStrategy({1: Signal.BUY, 2: Signal.SELL, 3: Signal.BUY, 4: Signal.SELL})

    result = run_backtest(strategy, df, position_size_fraction=1.0, fill="next_open")
    summary = summarize(result, "1h")

    assert summary.num_trades == 2
    assert 0 < summary.exposure_pct < 100
    assert summary.profit_factor == pytest.approx(1.0, rel=0.2)  # +10% then -9.09%


# ---- walk-forward -----------------------------------------------------------


def test_walk_forward_chooses_on_train_and_scores_only_on_unseen_test():
    df = _random_walk(1200, seed=3)
    grid = ParamGrid(
        fast_ema=(5, 8),
        slow_ema=(20,),
        rsi_period=(7,),
        rsi_oversold=(35, 40),
        rsi_overbought=(60,),
    )

    report = walk_forward(
        df, grid, interval="1h", train_bars=400, test_bars=200, fee_bps=10, slippage_bps=5
    )

    assert len(report.folds) == 4  # (1200 - 400) // 200
    for fold in report.folds:
        assert fold.test_start == fold.train_end  # test window starts where training ended
        assert fold.params in grid.combinations()
    assert report.oos_return_pct == pytest.approx(
        (np.prod([1 + f.oos.total_return_pct / 100 for f in report.folds]) - 1) * 100
    )
    assert report.benchmark_return_pct is not None


def test_walk_forward_requires_enough_data():
    grid = ParamGrid(
        fast_ema=(5,), slow_ema=(20,), rsi_period=(7,), rsi_oversold=(30,), rsi_overbought=(70,)
    )

    with pytest.raises(ValueError, match="bars"):
        walk_forward(_random_walk(300), grid, interval="1h", train_bars=400, test_bars=200)


def test_walk_forward_report_aggregates_trades_and_exposure():
    df = _random_walk(1200, seed=3)
    grid = ParamGrid(
        fast_ema=(5,), slow_ema=(20,), rsi_period=(7,), rsi_oversold=(40,), rsi_overbought=(60,)
    )

    report = walk_forward(df, grid, interval="1h", train_bars=400, test_bars=200)

    assert report.oos_trades == sum(f.oos.num_trades for f in report.folds)
    assert report.mean_oos_exposure_pct == pytest.approx(
        sum(f.oos.exposure_pct for f in report.folds) / len(report.folds)
    )
    assert report.mean_benchmark_sharpe == pytest.approx(
        sum(f.benchmark.sharpe for f in report.folds) / len(report.folds)
    )


def test_verdict_refuses_to_call_a_winner_on_too_few_trades():
    from trading_bot.backtest.walkforward import verdict

    assert verdict(oos_return=8.0, benchmark_return=-20.0, oos_trades=12).startswith("INCONCLUSIVE")
    assert verdict(oos_return=8.0, benchmark_return=-20.0, oos_trades=60).startswith("BEATS")
    assert verdict(oos_return=-1.0, benchmark_return=5.0, oos_trades=60).startswith("DOES NOT BEAT")
