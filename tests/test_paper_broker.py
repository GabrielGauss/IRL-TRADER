from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import pytest

from trading_bot.execution.broker import OrderSide, PaperBroker


def _price_source(price: float) -> Callable[[str], Awaitable[float]]:
    async def fetch(symbol: str) -> float:
        return price

    return fetch


def test_buy_debits_quote_and_credits_base():
    broker = PaperBroker(_price_source(100.0), initial_balances={"USDT": 1000.0})

    fill = asyncio.run(broker.place_order("BTC/USDT", OrderSide.BUY, 1.0))

    assert fill.price == pytest.approx(100.0)
    assert asyncio.run(broker.get_balance("USDT")) == pytest.approx(900.0)
    assert asyncio.run(broker.get_balance("BTC")) == pytest.approx(1.0)


def test_sell_credits_quote_and_debits_base():
    broker = PaperBroker(_price_source(100.0), initial_balances={"BTC": 1.0})

    asyncio.run(broker.place_order("BTC/USDT", OrderSide.SELL, 0.5))

    assert asyncio.run(broker.get_balance("BTC")) == pytest.approx(0.5)
    assert asyncio.run(broker.get_balance("USDT")) == pytest.approx(50.0)


def test_slippage_costs_the_buyer_a_worse_price():
    broker = PaperBroker(_price_source(100.0), initial_balances={"USDT": 1000.0}, slippage_bps=50.0)

    fill = asyncio.run(broker.place_order("BTC/USDT", OrderSide.BUY, 1.0))
    assert fill.price == pytest.approx(100.5)


def test_slippage_costs_the_seller_a_worse_price():
    broker = PaperBroker(_price_source(100.0), initial_balances={"BTC": 1.0}, slippage_bps=50.0)

    fill = asyncio.run(broker.place_order("BTC/USDT", OrderSide.SELL, 1.0))
    assert fill.price == pytest.approx(99.5)


def test_fee_is_deducted_from_quote_proceeds_on_sell():
    broker = PaperBroker(_price_source(100.0), initial_balances={"BTC": 1.0}, fee_bps=10.0)

    fill = asyncio.run(broker.place_order("BTC/USDT", OrderSide.SELL, 1.0))

    assert fill.fee == pytest.approx(0.1)
    assert asyncio.run(broker.get_balance("USDT")) == pytest.approx(99.9)


def test_fee_is_deducted_from_quote_balance_on_buy():
    broker = PaperBroker(_price_source(100.0), initial_balances={"USDT": 1000.0}, fee_bps=10.0)

    asyncio.run(broker.place_order("BTC/USDT", OrderSide.BUY, 1.0))

    assert asyncio.run(broker.get_balance("USDT")) == pytest.approx(1000.0 - 100.0 - 0.1)


def test_raises_on_insufficient_quote_balance_for_buy():
    broker = PaperBroker(_price_source(100.0), initial_balances={"USDT": 10.0})
    with pytest.raises(ValueError, match="Insufficient"):
        asyncio.run(broker.place_order("BTC/USDT", OrderSide.BUY, 1.0))


def test_raises_on_insufficient_base_balance_for_sell():
    broker = PaperBroker(_price_source(100.0), initial_balances={"BTC": 0.1})
    with pytest.raises(ValueError, match="Insufficient"):
        asyncio.run(broker.place_order("BTC/USDT", OrderSide.SELL, 1.0))


def test_rejects_non_positive_quantity():
    broker = PaperBroker(_price_source(100.0), initial_balances={})
    with pytest.raises(ValueError, match="quantity must be positive"):
        asyncio.run(broker.place_order("BTC/USDT", OrderSide.BUY, 0.0))


def test_rejects_symbol_without_a_slash():
    broker = PaperBroker(_price_source(100.0), initial_balances={"USDT": 1000.0})
    with pytest.raises(ValueError, match="BASE/QUOTE"):
        asyncio.run(broker.place_order("BTCUSDT", OrderSide.BUY, 1.0))


def test_get_price_delegates_to_the_injected_price_source():
    broker = PaperBroker(_price_source(12345.0), initial_balances={})
    assert asyncio.run(broker.get_price("BTC/USDT")) == pytest.approx(12345.0)


def test_close_is_a_safe_noop():
    broker = PaperBroker(_price_source(100.0), initial_balances={})
    asyncio.run(broker.close())


def test_order_ids_are_unique_across_fills():
    broker = PaperBroker(_price_source(100.0), initial_balances={"USDT": 1000.0})

    first = asyncio.run(broker.place_order("BTC/USDT", OrderSide.BUY, 0.01))
    second = asyncio.run(broker.place_order("BTC/USDT", OrderSide.BUY, 0.01))

    assert first.order_id != second.order_id


def test_unconfigured_asset_defaults_to_zero_balance():
    broker = PaperBroker(_price_source(100.0), initial_balances={"USDT": 1000.0})
    assert asyncio.run(broker.get_balance("ETH")) == 0.0


def test_client_order_id_becomes_the_paper_order_id():
    broker = PaperBroker(_price_source(100.0), initial_balances={"USDT": 1000.0})

    fill = asyncio.run(
        broker.place_order("BTC/USDT", OrderSide.BUY, 0.01, client_order_id="irl-abc")
    )

    assert fill.order_id == "irl-abc"
