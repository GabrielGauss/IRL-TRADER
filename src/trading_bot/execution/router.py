"""Reconciles a TargetPosition against actual broker holdings by issuing at
most one delta order: buy the shortfall if under target, sell the excess if
over. Decoupled from any specific venue via the Broker interface, so the
same routing logic works against CcxtBroker or PaperBroker unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass

from trading_bot.execution.broker import Broker, Fill, OrderSide
from trading_bot.risk.target_position import TargetPosition


@dataclass(frozen=True)
class OrderPlan:
    """The single delta order needed to reach a target, not yet placed."""

    symbol: str
    side: OrderSide
    quantity: float


async def plan_order(
    broker: Broker,
    target: TargetPosition,
    symbol: str,
    base_asset: str,
    min_order_quantity: float = 1e-8,
) -> OrderPlan | None:
    current_quantity = await broker.get_balance(base_asset)
    delta = target.target_quantity - current_quantity

    if abs(delta) < min_order_quantity:
        return None

    side = OrderSide.BUY if delta > 0 else OrderSide.SELL
    return OrderPlan(symbol=symbol, side=side, quantity=abs(delta))


async def route_to_target(
    broker: Broker,
    target: TargetPosition,
    symbol: str,
    base_asset: str,
    min_order_quantity: float = 1e-8,
) -> Fill | None:
    plan = await plan_order(broker, target, symbol, base_asset, min_order_quantity)
    if plan is None:
        return None
    return await broker.place_order(plan.symbol, plan.side, plan.quantity)
