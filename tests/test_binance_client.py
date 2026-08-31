from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from trading_bot.config import load_public_data_settings
from trading_bot.exchange.binance_client import BinanceClient, OrderSide

RAW_KLINE = [
    1700000000000,
    "50000.0",
    "50100.0",
    "49900.0",
    "50050.0",
    "10.5",
    1700000059999,
    "525000.0",
    100,
    "5.0",
    "250000.0",
    "0",
]


@pytest.fixture
def mock_binance_sdk_client(monkeypatch):
    fake_client = MagicMock()
    monkeypatch.setattr("trading_bot.exchange.binance_client.Client", lambda **kwargs: fake_client)
    return fake_client


@pytest.fixture
def binance_client(mock_binance_sdk_client) -> BinanceClient:
    return BinanceClient(load_public_data_settings())


def test_get_klines_returns_dataframe_with_expected_columns_and_types(
    binance_client, mock_binance_sdk_client
):
    # Arrange
    mock_binance_sdk_client.get_klines.return_value = [RAW_KLINE]

    # Act
    df = binance_client.get_klines("BTCUSDT", "1m", limit=1)

    # Assert
    assert list(df.columns) == ["open_time", "open", "high", "low", "close", "volume"]
    assert len(df) == 1
    assert df["close"].iloc[0] == pytest.approx(50050.0)
    assert df["open_time"].dtype.kind == "M"


def test_get_balance_returns_free_amount_as_float(binance_client, mock_binance_sdk_client):
    # Arrange
    mock_binance_sdk_client.get_asset_balance.return_value = {
        "asset": "USDT",
        "free": "123.45",
        "locked": "0.0",
    }

    # Act
    balance = binance_client.get_balance("USDT")

    # Assert
    assert balance == pytest.approx(123.45)


def test_get_balance_raises_when_asset_info_missing(binance_client, mock_binance_sdk_client):
    # Arrange
    mock_binance_sdk_client.get_asset_balance.return_value = None

    # Act / Assert
    with pytest.raises(RuntimeError, match="No balance info"):
        binance_client.get_balance("USDT")


def test_place_market_order_returns_order_result(binance_client, mock_binance_sdk_client):
    # Arrange
    mock_binance_sdk_client.create_order.return_value = {
        "orderId": 12345,
        "symbol": "BTCUSDT",
        "status": "FILLED",
    }

    # Act
    result = binance_client.place_market_order("BTCUSDT", OrderSide.BUY, 0.01)

    # Assert
    assert result.order_id == "12345"
    assert result.symbol == "BTCUSDT"
    assert result.side == OrderSide.BUY
    assert result.quantity == pytest.approx(0.01)
    assert result.status == "FILLED"
    mock_binance_sdk_client.create_order.assert_called_once_with(
        symbol="BTCUSDT", side="BUY", type="MARKET", quantity=0.01
    )
