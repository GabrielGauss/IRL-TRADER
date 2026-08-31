"""Deterministic portfolio state: cash, positions, and realized PnL.

Immutable by design: every state transition (`apply_fill`) returns a new
PortfolioState rather than mutating in place, so a historical state stays a
valid reference (e.g. for an audit trail or a drawdown monitor snapshot)
even as trading continues. Long-only, matching the rest of this codebase's
spot-trading assumptions -- selling more than is held is rejected rather
than opening a short.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Literal

OrderSideLiteral = Literal["BUY", "SELL"]


@dataclass(frozen=True)
class Position:
    symbol: str
    quantity: float
    average_entry_price: float


@dataclass(frozen=True)
class PortfolioState:
    cash: float
    positions: Mapping[str, Position] = field(default_factory=dict)
    realized_pnl: float = 0.0

    def position_quantity(self, symbol: str) -> float:
        position = self.positions.get(symbol)
        return position.quantity if position is not None else 0.0

    def market_value(self, mark_prices: Mapping[str, float]) -> float:
        total = 0.0
        for symbol, position in self.positions.items():
            if position.quantity == 0:
                continue
            price = self._require_price(mark_prices, symbol)
            total += position.quantity * price
        return total

    def equity(self, mark_prices: Mapping[str, float]) -> float:
        return self.cash + self.market_value(mark_prices)

    def unrealized_pnl(self, mark_prices: Mapping[str, float]) -> float:
        total = 0.0
        for symbol, position in self.positions.items():
            if position.quantity == 0:
                continue
            price = self._require_price(mark_prices, symbol)
            total += (price - position.average_entry_price) * position.quantity
        return total

    def apply_fill(
        self, symbol: str, side: OrderSideLiteral, quantity: float, price: float
    ) -> PortfolioState:
        if quantity <= 0:
            raise ValueError("quantity must be positive")
        if price <= 0:
            raise ValueError("price must be positive")

        existing = self.positions.get(symbol)
        new_positions = dict(self.positions)

        if side == "BUY":
            new_cash = self.cash - quantity * price
            if existing is None:
                new_positions[symbol] = Position(
                    symbol=symbol, quantity=quantity, average_entry_price=price
                )
            else:
                total_quantity = existing.quantity + quantity
                blended_price = (
                    existing.quantity * existing.average_entry_price + quantity * price
                ) / total_quantity
                new_positions[symbol] = Position(
                    symbol=symbol, quantity=total_quantity, average_entry_price=blended_price
                )
            return PortfolioState(
                cash=new_cash, positions=new_positions, realized_pnl=self.realized_pnl
            )

        held = existing.quantity if existing is not None else 0.0
        if existing is None or held < quantity:
            raise ValueError(f"Cannot sell {quantity} {symbol}, only holding {held}")

        new_cash = self.cash + quantity * price
        new_realized_pnl = self.realized_pnl + (price - existing.average_entry_price) * quantity
        remaining_quantity = existing.quantity - quantity
        if remaining_quantity == 0:
            del new_positions[symbol]
        else:
            new_positions[symbol] = replace(existing, quantity=remaining_quantity)

        return PortfolioState(cash=new_cash, positions=new_positions, realized_pnl=new_realized_pnl)

    @staticmethod
    def _require_price(mark_prices: Mapping[str, float], symbol: str) -> float:
        price = mark_prices.get(symbol)
        if price is None:
            raise KeyError(f"No mark price provided for open position {symbol!r}")
        return price
