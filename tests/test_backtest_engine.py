from __future__ import annotations

import pandas as pd
import pytest

from trading_bot.backtest.engine import BacktestResult, Trade, run_backtest
from trading_bot.strategy.base import Signal, Strategy


class StubStrategy(Strategy):
    """Returns a preset signal keyed by the length of the window it's given,
    so tests can dictate exactly which bar triggers which action."""

    def __init__(self, signal_by_window_length: dict[int, Signal], min_lookback: int = 0):
        self._signal_by_window_length = signal_by_window_length
        self.min_lookback = min_lookback

    def generate_signal(self, df: pd.DataFrame) -> Signal:
        return self._signal_by_window_length.get(len(df), Signal.HOLD)


def _make_df(closes: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open_time": pd.date_range("2024-01-01", periods=len(closes), freq="1min"),
            "open": closes,
            "high": closes,
            "low": closes,
            "close": closes,
            "volume": [1.0] * len(closes),
        }
    )


def test_run_backtest_with_no_signals_keeps_equity_flat():
    # Arrange
    df = _make_df([100.0, 101.0, 102.0, 103.0, 104.0])
    strategy = StubStrategy(signal_by_window_length={})

    # Act
    result = run_backtest(strategy, df, initial_balance=1000.0, position_size_fraction=0.1)

    # Assert
    assert result.num_trades == 0
    assert result.final_equity == pytest.approx(1000.0)
    assert result.total_return_pct == pytest.approx(0.0)


def test_run_backtest_executes_buy_then_sell_and_records_trade():
    # Arrange: BUY when window length is 2 (bar price 101), SELL at length 4 (bar price 103)
    df = _make_df([100.0, 101.0, 102.0, 103.0, 104.0])
    strategy = StubStrategy(signal_by_window_length={2: Signal.BUY, 4: Signal.SELL})

    # Act
    result = run_backtest(strategy, df, initial_balance=1000.0, position_size_fraction=0.1)

    # Assert
    assert result.num_trades == 1
    trade = result.trades[0]
    assert trade.entry_price == pytest.approx(101.0)
    assert trade.exit_price == pytest.approx(103.0)
    assert trade.quantity == pytest.approx(100.0 / 101.0)
    assert result.final_equity == pytest.approx(1001.9801, abs=1e-4)
    assert result.win_rate_pct == pytest.approx(100.0)


def test_run_backtest_marks_open_position_to_market_at_final_bar():
    # Arrange: BUY at length 2 (price 101), never sell
    df = _make_df([100.0, 101.0, 102.0, 103.0, 104.0])
    strategy = StubStrategy(signal_by_window_length={2: Signal.BUY})

    # Act
    result = run_backtest(strategy, df, initial_balance=1000.0, position_size_fraction=0.1)

    # Assert
    assert result.num_trades == 0
    expected_quantity = 100.0 / 101.0
    expected_equity = 900.0 + expected_quantity * 104.0
    assert result.final_equity == pytest.approx(expected_equity)


def test_run_backtest_ignores_repeated_buy_signal_while_already_in_position():
    # Arrange: BUY at length 2 and 3 (second should be a no-op), SELL at length 4
    df = _make_df([100.0, 101.0, 102.0, 103.0, 104.0])
    strategy = StubStrategy(signal_by_window_length={2: Signal.BUY, 3: Signal.BUY, 4: Signal.SELL})

    # Act
    result = run_backtest(strategy, df, initial_balance=1000.0, position_size_fraction=0.1)

    # Assert: quantity matches a single buy, not a doubled-up position
    assert result.num_trades == 1
    assert result.trades[0].quantity == pytest.approx(100.0 / 101.0)


def test_run_backtest_ignores_sell_signal_while_flat():
    # Arrange
    df = _make_df([100.0, 101.0, 102.0])
    strategy = StubStrategy(signal_by_window_length={2: Signal.SELL})

    # Act
    result = run_backtest(strategy, df, initial_balance=1000.0, position_size_fraction=0.1)

    # Assert
    assert result.num_trades == 0
    assert result.final_equity == pytest.approx(1000.0)


@pytest.mark.parametrize("initial_balance", [0.0, -100.0])
def test_run_backtest_raises_on_non_positive_initial_balance(initial_balance):
    df = _make_df([100.0, 101.0])
    strategy = StubStrategy(signal_by_window_length={})

    with pytest.raises(ValueError, match="initial_balance must be positive"):
        run_backtest(strategy, df, initial_balance=initial_balance)


@pytest.mark.parametrize("fraction", [0.0, -0.1, 1.1])
def test_run_backtest_raises_on_fraction_out_of_range(fraction):
    df = _make_df([100.0, 101.0])
    strategy = StubStrategy(signal_by_window_length={})

    with pytest.raises(ValueError, match=r"position_size_fraction must be in \(0, 1\]"):
        run_backtest(strategy, df, position_size_fraction=fraction)


def test_backtest_result_max_drawdown_pct_finds_largest_peak_to_trough_decline():
    # Arrange: peak 1100 -> trough 900 is the largest decline (~18.18%)
    result = BacktestResult(
        initial_balance=1000.0,
        equity_curve=(1000.0, 1100.0, 1050.0, 900.0, 950.0),
        trades=(),
    )

    # Act / Assert
    assert result.max_drawdown_pct == pytest.approx(18.1818, abs=1e-3)


def test_backtest_result_win_rate_pct_counts_only_profitable_trades():
    # Arrange: two winning trades, one losing trade
    trades = (
        Trade(entry_time=0, entry_price=100.0, exit_time=1, exit_price=110.0, quantity=1.0),
        Trade(entry_time=1, entry_price=100.0, exit_time=2, exit_price=90.0, quantity=1.0),
        Trade(entry_time=2, entry_price=100.0, exit_time=3, exit_price=105.0, quantity=1.0),
    )
    result = BacktestResult(initial_balance=1000.0, equity_curve=(1000.0,), trades=trades)

    # Act / Assert
    assert result.win_rate_pct == pytest.approx(200.0 / 3.0)


def test_backtest_result_win_rate_pct_is_zero_with_no_trades():
    result = BacktestResult(initial_balance=1000.0, equity_curve=(1000.0,), trades=())
    assert result.win_rate_pct == pytest.approx(0.0)
