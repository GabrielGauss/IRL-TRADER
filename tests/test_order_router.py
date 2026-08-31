from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from trading_bot.execution.broker import Fill, OrderSide
from trading_bot.execution.router import route_to_target
from trading_bot.risk.target_position import TargetPosition


def _fake_broker(current_balance: float) -> AsyncMock:
    broker = AsyncMock()
    broker.get_balance.return_value = current_balance
    return broker


def test_buys_the_shortfall_when_under_target():
    broker = _fake_broker(current_balance=0.3)
    broker.place_order.return_value = Fill(
        order_id="1",
        symbol="BTC/USDT",
        side=OrderSide.BUY,
        quantity=0.7,
        price=100.0,
        fee=0.0,
        fee_asset=None,
        status="FILLED",
    )

    fill = asyncio.run(route_to_target(broker, TargetPosition("BTC/USDT", 1.0), "BTC/USDT", "BTC"))

    broker.place_order.assert_awaited_once_with("BTC/USDT", OrderSide.BUY, pytest.approx(0.7))
    assert fill is not None
    assert fill.quantity == pytest.approx(0.7)


def test_sells_the_excess_when_over_target():
    broker = _fake_broker(current_balance=1.0)
    broker.place_order.return_value = Fill(
        order_id="1",
        symbol="BTC/USDT",
        side=OrderSide.SELL,
        quantity=0.4,
        price=100.0,
        fee=0.0,
        fee_asset=None,
        status="FILLED",
    )

    fill = asyncio.run(route_to_target(broker, TargetPosition("BTC/USDT", 0.6), "BTC/USDT", "BTC"))

    broker.place_order.assert_awaited_once_with("BTC/USDT", OrderSide.SELL, pytest.approx(0.4))
    assert fill is not None


def test_does_nothing_when_already_at_target():
    broker = _fake_broker(current_balance=1.0)

    fill = asyncio.run(route_to_target(broker, TargetPosition("BTC/USDT", 1.0), "BTC/USDT", "BTC"))

    broker.place_order.assert_not_awaited()
    assert fill is None


def test_ignores_dust_deltas_below_min_order_quantity():
    broker = _fake_broker(current_balance=1.0)

    fill = asyncio.run(
        route_to_target(
            broker,
            TargetPosition("BTC/USDT", 1.0 + 1e-10),
            "BTC/USDT",
            "BTC",
            min_order_quantity=1e-8,
        )
    )

    broker.place_order.assert_not_awaited()
    assert fill is None
