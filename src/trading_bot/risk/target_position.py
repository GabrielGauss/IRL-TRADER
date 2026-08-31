"""Translates a (non-deterministic-origin) SignalPayload into a deterministic
target position: given the same signal and account state, always the same
target quantity. Long-only, matching the rest of this codebase.
"""

from __future__ import annotations

from dataclasses import dataclass

from trading_bot.risk.position_sizing import fixed_fraction_quantity
from trading_bot.signals.schema import SignalAction, SignalPayload


@dataclass(frozen=True)
class TargetPosition:
    symbol: str
    target_quantity: float


def signal_to_target_position(
    signal: SignalPayload,
    *,
    account_balance_quote: float,
    price: float,
    current_quantity: float,
    position_size_fraction: float,
) -> TargetPosition:
    """BUY sizes a new position if flat, otherwise holds what's already open.
    SELL/CLOSE targets flat. HOLD (or any other action) leaves the current
    quantity unchanged."""
    if signal.action == SignalAction.BUY:
        if current_quantity > 0:
            return TargetPosition(symbol=signal.symbol, target_quantity=current_quantity)
        sized = fixed_fraction_quantity(account_balance_quote, price, position_size_fraction)
        return TargetPosition(symbol=signal.symbol, target_quantity=sized)

    if signal.action in (SignalAction.SELL, SignalAction.CLOSE):
        return TargetPosition(symbol=signal.symbol, target_quantity=0.0)

    return TargetPosition(symbol=signal.symbol, target_quantity=current_quantity)
