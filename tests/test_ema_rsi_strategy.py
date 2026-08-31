"""Indicator math (EMA/RSI) is delegated to pandas_ta and trusted; these tests
mock ta.ema/ta.rsi to isolate the strategy's own decision logic."""

from __future__ import annotations

import pandas as pd
import pytest

from trading_bot.strategy.base import Signal
from trading_bot.strategy.ema_rsi import EmaRsiStrategy


def _make_df(length: int) -> pd.DataFrame:
    return pd.DataFrame({"close": [float(i) for i in range(length)]})


@pytest.fixture
def strategy() -> EmaRsiStrategy:
    return EmaRsiStrategy(fast_ema=12, slow_ema=26, rsi_period=14)


def _mock_indicators(monkeypatch, fast, slow, rsi):
    monkeypatch.setattr(
        "trading_bot.strategy.ema_rsi.ta.ema", lambda close, length: fast if length == 12 else slow
    )
    monkeypatch.setattr("trading_bot.strategy.ema_rsi.ta.rsi", lambda close, length: rsi)


def test_generate_signal_returns_hold_when_insufficient_history(strategy):
    # Arrange
    df = _make_df(strategy.min_lookback - 1)

    # Act
    signal = strategy.generate_signal(df)

    # Assert
    assert signal == Signal.HOLD


def test_generate_signal_returns_buy_when_uptrend_and_rsi_recovers_from_oversold(
    strategy, monkeypatch
):
    # Arrange
    n = strategy.min_lookback
    df = _make_df(n)
    fast = pd.Series([110.0] * n)
    slow = pd.Series([100.0] * n)
    rsi = pd.Series([25.0] * (n - 2) + [28.0, 32.0])
    _mock_indicators(monkeypatch, fast, slow, rsi)

    # Act
    signal = strategy.generate_signal(df)

    # Assert
    assert signal == Signal.BUY


def test_generate_signal_returns_sell_when_rsi_overbought(strategy, monkeypatch):
    # Arrange
    n = strategy.min_lookback
    df = _make_df(n)
    fast = pd.Series([110.0] * n)
    slow = pd.Series([100.0] * n)
    rsi = pd.Series([65.0] * (n - 1) + [72.0])
    _mock_indicators(monkeypatch, fast, slow, rsi)

    # Act
    signal = strategy.generate_signal(df)

    # Assert
    assert signal == Signal.SELL


def test_generate_signal_returns_hold_when_no_clear_signal(strategy, monkeypatch):
    # Arrange
    n = strategy.min_lookback
    df = _make_df(n)
    fast = pd.Series([90.0] * n)
    slow = pd.Series([100.0] * n)
    rsi = pd.Series([50.0] * n)
    _mock_indicators(monkeypatch, fast, slow, rsi)

    # Act
    signal = strategy.generate_signal(df)

    # Assert
    assert signal == Signal.HOLD


def test_generate_signal_returns_hold_when_indicators_have_nan(strategy, monkeypatch):
    # Arrange
    n = strategy.min_lookback
    df = _make_df(n)
    fast = pd.Series([float("nan")] * n)
    slow = pd.Series([100.0] * n)
    rsi = pd.Series([50.0] * n)
    _mock_indicators(monkeypatch, fast, slow, rsi)

    # Act
    signal = strategy.generate_signal(df)

    # Assert
    assert signal == Signal.HOLD


def test_strategy_rejects_fast_ema_not_less_than_slow_ema():
    with pytest.raises(ValueError, match="fast_ema must be less than slow_ema"):
        EmaRsiStrategy(fast_ema=26, slow_ema=12)
