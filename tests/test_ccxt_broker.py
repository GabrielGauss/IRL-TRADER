from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from trading_bot.execution.broker import CcxtBroker, OrderSide


def _fake_exchange() -> AsyncMock:
    return AsyncMock()


def test_get_balance_reads_free_amount_from_unified_balance():
    exchange = _fake_exchange()
    exchange.fetch_balance.return_value = {"free": {"USDT": 1000.0}}
    broker = CcxtBroker(exchange=exchange)

    balance = asyncio.run(broker.get_balance("USDT"))
    assert balance == pytest.approx(1000.0)


def test_get_balance_defaults_to_zero_for_unknown_asset():
    exchange = _fake_exchange()
    exchange.fetch_balance.return_value = {"free": {}}
    broker = CcxtBroker(exchange=exchange)

    assert asyncio.run(broker.get_balance("BTC")) == 0.0


def test_get_price_reads_last_price_from_ticker():
    exchange = _fake_exchange()
    exchange.fetch_ticker.return_value = {"last": 65000.5}
    broker = CcxtBroker(exchange=exchange)

    price = asyncio.run(broker.get_price("BTC/USDT"))

    assert price == pytest.approx(65000.5)
    exchange.fetch_ticker.assert_awaited_once_with("BTC/USDT")


def test_place_order_maps_ccxt_order_to_fill():
    exchange = _fake_exchange()
    exchange.create_order.return_value = {
        "id": "123",
        "average": 65000.0,
        "filled": 0.01,
        "status": "closed",
        "fee": {"cost": 0.65, "currency": "USDT"},
    }
    broker = CcxtBroker(exchange=exchange)

    fill = asyncio.run(broker.place_order("BTC/USDT", OrderSide.BUY, 0.01))

    exchange.create_order.assert_awaited_once_with("BTC/USDT", "market", "buy", 0.01)
    assert fill.order_id == "123"
    assert fill.price == pytest.approx(65000.0)
    assert fill.quantity == pytest.approx(0.01)
    assert fill.fee == pytest.approx(0.65)
    assert fill.fee_asset == "USDT"
    assert fill.status == "closed"


def test_place_order_falls_back_to_price_field_when_average_missing():
    exchange = _fake_exchange()
    exchange.create_order.return_value = {"id": "1", "price": 100.0, "status": "closed"}
    broker = CcxtBroker(exchange=exchange)

    fill = asyncio.run(broker.place_order("BTC/USDT", OrderSide.SELL, 1.0))

    assert fill.price == pytest.approx(100.0)
    assert fill.fee == 0.0
    assert fill.fee_asset is None


def test_place_order_defaults_filled_quantity_to_requested_when_absent():
    exchange = _fake_exchange()
    exchange.create_order.return_value = {"id": "1", "price": 100.0, "status": "closed"}
    broker = CcxtBroker(exchange=exchange)

    fill = asyncio.run(broker.place_order("BTC/USDT", OrderSide.BUY, 2.5))
    assert fill.quantity == pytest.approx(2.5)


def test_close_closes_the_underlying_exchange_session():
    exchange = _fake_exchange()
    broker = CcxtBroker(exchange=exchange)

    asyncio.run(broker.close())
    exchange.close.assert_awaited_once()


def test_requires_either_exchange_id_or_injected_exchange():
    with pytest.raises(ValueError, match="exchange_id"):
        CcxtBroker()


def test_constructs_a_real_ccxt_exchange_when_exchange_id_given():
    broker = CcxtBroker("binance", testnet=False)
    assert broker.exchange_id == "binance"
    asyncio.run(broker.close())
