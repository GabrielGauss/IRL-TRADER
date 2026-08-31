from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pandas as pd
import pytest

from trading_bot.exchange.binance_client import OrderResult, OrderSide
from trading_bot.execution.engine import ExecutionEngine, RiskLimitBreached, RiskLimits
from trading_bot.persistence.repository import TradeRepository
from trading_bot.strategy.base import Signal, Strategy


class FixedSignalStrategy(Strategy):
    def __init__(self, signal: Signal):
        self._signal = signal

    def generate_signal(self, df: pd.DataFrame) -> Signal:
        return self._signal


def _klines_df(price: float = 100.0) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open_time": pd.date_range("2024-01-01", periods=3, freq="1h"),
            "open": [price] * 3,
            "high": [price] * 3,
            "low": [price] * 3,
            "close": [price] * 3,
            "volume": [1.0] * 3,
        }
    )


@pytest.fixture
def repository(tmp_path) -> TradeRepository:
    return TradeRepository(str(tmp_path / "test.db"))


@pytest.fixture
def mock_client():
    client = MagicMock()
    client.get_klines.return_value = _klines_df(price=100.0)
    return client


def _engine(client, repository, signal: Signal, **overrides) -> ExecutionEngine:
    defaults = {
        "client": client,
        "strategy": FixedSignalStrategy(signal),
        "repository": repository,
        "risk_limits": RiskLimits(max_daily_loss_pct=3.0, max_drawdown_pct=10.0),
        "base_asset": "BTC",
        "quote_asset": "USDT",
        "position_size_fraction": 0.1,
    }
    defaults.update(overrides)
    return ExecutionEngine(**defaults)


def test_symbol_combines_base_and_quote_asset(mock_client, repository):
    engine = _engine(mock_client, repository, Signal.HOLD)
    assert engine.symbol == "BTCUSDT"


def test_run_once_buys_when_signal_is_buy_and_flat(mock_client, repository):
    # Arrange: flat (no BTC), 1000 USDT available
    mock_client.get_balance.side_effect = lambda asset: {"BTC": 0.0, "USDT": 1000.0}[asset]
    mock_client.place_market_order.return_value = OrderResult(
        order_id="1", symbol="BTCUSDT", side=OrderSide.BUY, quantity=1.0, status="FILLED"
    )
    engine = _engine(mock_client, repository, Signal.BUY)

    # Act
    trade = engine.run_once()

    # Assert
    mock_client.place_market_order.assert_called_once_with(
        "BTCUSDT", OrderSide.BUY, pytest.approx(1.0)
    )
    assert trade is not None
    assert trade.order_id == "1"
    assert repository.get_trades() == [trade]


def test_run_once_sells_entire_position_when_signal_is_sell_and_in_position(
    mock_client, repository
):
    # Arrange: holding 0.5 BTC
    mock_client.get_balance.side_effect = lambda asset: {"BTC": 0.5, "USDT": 50.0}[asset]
    mock_client.place_market_order.return_value = OrderResult(
        order_id="2", symbol="BTCUSDT", side=OrderSide.SELL, quantity=0.5, status="FILLED"
    )
    engine = _engine(mock_client, repository, Signal.SELL)

    # Act
    trade = engine.run_once()

    # Assert
    mock_client.place_market_order.assert_called_once_with("BTCUSDT", OrderSide.SELL, 0.5)
    assert trade is not None
    assert trade.quantity == pytest.approx(0.5)


def test_run_once_does_nothing_on_hold_signal(mock_client, repository):
    mock_client.get_balance.side_effect = lambda asset: {"BTC": 0.0, "USDT": 1000.0}[asset]
    engine = _engine(mock_client, repository, Signal.HOLD)

    trade = engine.run_once()

    mock_client.place_market_order.assert_not_called()
    assert trade is None


def test_run_once_skips_buy_signal_when_already_in_position(mock_client, repository):
    mock_client.get_balance.side_effect = lambda asset: {"BTC": 0.5, "USDT": 50.0}[asset]
    engine = _engine(mock_client, repository, Signal.BUY)

    trade = engine.run_once()

    mock_client.place_market_order.assert_not_called()
    assert trade is None


def test_run_once_skips_sell_signal_when_flat(mock_client, repository):
    mock_client.get_balance.side_effect = lambda asset: {"BTC": 0.0, "USDT": 1000.0}[asset]
    engine = _engine(mock_client, repository, Signal.SELL)

    trade = engine.run_once()

    mock_client.place_market_order.assert_not_called()
    assert trade is None


def test_run_once_records_an_equity_snapshot(mock_client, repository):
    mock_client.get_balance.side_effect = lambda asset: {"BTC": 0.0, "USDT": 1000.0}[asset]
    engine = _engine(mock_client, repository, Signal.HOLD)

    engine.run_once()

    snapshots = repository.get_equity_since(datetime.now(UTC) - timedelta(minutes=1))
    assert len(snapshots) == 1
    assert snapshots[0].equity == pytest.approx(1000.0)


def test_run_once_raises_when_daily_loss_limit_is_breached(mock_client, repository):
    # Arrange: equity was 1000 an hour ago, has since dropped to 900 (10% loss, limit is 3%)
    repository.record_equity(1000.0, recorded_at=datetime.now(UTC) - timedelta(hours=1))
    mock_client.get_balance.side_effect = lambda asset: {"BTC": 0.0, "USDT": 900.0}[asset]
    engine = _engine(mock_client, repository, Signal.BUY)

    # Act / Assert
    with pytest.raises(RiskLimitBreached, match="Daily loss"):
        engine.run_once()
    mock_client.place_market_order.assert_not_called()


def test_run_once_raises_when_drawdown_limit_is_breached(mock_client, repository):
    # Arrange: peak equity 1100 two hours ago, current equity 900 (~18% drawdown, limit is 10%)
    # but recent-enough day-start equity (950) keeps daily loss under its own 3% limit alone
    repository.record_equity(1100.0, recorded_at=datetime.now(UTC) - timedelta(hours=2))
    repository.record_equity(950.0, recorded_at=datetime.now(UTC) - timedelta(minutes=1))
    mock_client.get_balance.side_effect = lambda asset: {"BTC": 0.0, "USDT": 900.0}[asset]
    engine = _engine(
        mock_client,
        repository,
        Signal.BUY,
        risk_limits=RiskLimits(max_daily_loss_pct=50.0, max_drawdown_pct=10.0),
    )

    # Act / Assert
    with pytest.raises(RiskLimitBreached, match="Drawdown"):
        engine.run_once()
    mock_client.place_market_order.assert_not_called()


def test_run_once_allows_trading_when_no_prior_equity_history(mock_client, repository):
    mock_client.get_balance.side_effect = lambda asset: {"BTC": 0.0, "USDT": 1000.0}[asset]
    mock_client.place_market_order.return_value = OrderResult(
        order_id="1", symbol="BTCUSDT", side=OrderSide.BUY, quantity=1.0, status="FILLED"
    )
    engine = _engine(mock_client, repository, Signal.BUY)

    trade = engine.run_once()

    assert trade is not None
