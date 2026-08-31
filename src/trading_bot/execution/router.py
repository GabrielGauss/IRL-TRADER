"""Reconciles a TargetPosition against actual broker holdings by issuing at
most one delta order: buy the shortfall if under target, sell the excess if
over. Decoupled from any specific venue via the Broker interface, so the
same routing logic works against CcxtBroker or PaperBroker unchanged.
"""

from __future__ import annotations

from trading_bot.execution.broker import Broker, Fill, OrderSide
from trading_bot.risk.target_position import TargetPosition


async def route_to_target(
    broker: Broker,
    target: TargetPosition,
    symbol: str,
    base_asset: str,
    min_order_quantity: float = 1e-8,
) -> Fill | None:
    current_quantity = await broker.get_balance(base_asset)
    delta = target.target_quantity - current_quantity

    if abs(delta) < min_order_quantity:
        return None

    side = OrderSide.BUY if delta > 0 else OrderSide.SELL
    return await broker.place_order(symbol, side, abs(delta))
