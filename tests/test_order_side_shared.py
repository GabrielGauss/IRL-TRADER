"""Guards against the legacy (python-binance) and async (ccxt) stacks drifting
back into two incompatible OrderSide enums with identical member names."""

from __future__ import annotations

from trading_bot.exchange.binance_client import OrderSide as LegacyOrderSide
from trading_bot.exchange.order_side import OrderSide as SharedOrderSide
from trading_bot.execution.broker import OrderSide as BrokerOrderSide


def test_all_stacks_reference_the_same_order_side_type():
    assert LegacyOrderSide is SharedOrderSide
    assert BrokerOrderSide is SharedOrderSide
