"""Async broker abstraction so order routing can target Binance, any other
ccxt-supported exchange, or a paper-trading simulator through one interface.

CcxtBroker wraps ccxt.async_support rather than reimplementing per-exchange
order/balance normalization -- ccxt already does that for ~100 venues,
Binance included, so a single class covers both. Symbols here use ccxt's
unified 'BASE/QUOTE' format (e.g. 'BTC/USDT'), which is why this is a
separate abstraction from the legacy python-binance-based BinanceClient /
ExecutionEngine in this package, which use Binance's native 'BTCUSDT' form.
"""

from __future__ import annotations

import itertools
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import ccxt.async_support as ccxt_async

from trading_bot.exchange.order_side import OrderSide

__all__ = ["Broker", "CcxtBroker", "Fill", "OrderSide", "PaperBroker"]


@dataclass(frozen=True)
class Fill:
    order_id: str
    symbol: str
    side: OrderSide
    quantity: float
    price: float
    fee: float
    fee_asset: str | None
    status: str


class Broker(ABC):
    """Minimal async surface the order router needs from any venue."""

    @abstractmethod
    async def get_balance(self, asset: str) -> float: ...

    @abstractmethod
    async def get_price(self, symbol: str) -> float: ...

    @abstractmethod
    async def place_order(self, symbol: str, side: OrderSide, quantity: float) -> Fill: ...

    @abstractmethod
    async def close(self) -> None:
        """Release underlying network resources (HTTP session, etc.)."""


class CcxtBroker(Broker):
    """Live (or exchange-sandbox) broker backed by a ccxt.async_support exchange.

    Only market orders are supported, matching this project's existing
    execution model (no limit-order lifecycle to reconcile).
    """

    def __init__(
        self,
        exchange_id: str = "",
        api_key: str = "",
        api_secret: str = "",
        *,
        testnet: bool = True,
        exchange: Any | None = None,
    ):
        if exchange is not None:
            self._exchange = exchange
            return
        if not exchange_id:
            raise ValueError("exchange_id is required when exchange is not provided")
        exchange_class = getattr(ccxt_async, exchange_id)
        self._exchange = exchange_class(
            {"apiKey": api_key, "secret": api_secret, "enableRateLimit": True}
        )
        if testnet:
            self._exchange.set_sandbox_mode(True)

    @property
    def exchange_id(self) -> str:
        return str(self._exchange.id)

    async def get_balance(self, asset: str) -> float:
        balance = await self._exchange.fetch_balance()
        return float(balance.get("free", {}).get(asset, 0.0))

    async def get_price(self, symbol: str) -> float:
        ticker = await self._exchange.fetch_ticker(symbol)
        return float(ticker["last"])

    async def place_order(self, symbol: str, side: OrderSide, quantity: float) -> Fill:
        order = await self._exchange.create_order(symbol, "market", side.value.lower(), quantity)
        price = order.get("average") or order.get("price") or 0.0
        fee = order.get("fee") or {}
        return Fill(
            order_id=str(order.get("id", "")),
            symbol=symbol,
            side=side,
            quantity=float(order.get("filled") or quantity),
            price=float(price),
            fee=float(fee.get("cost", 0.0)),
            fee_asset=fee.get("currency"),
            status=str(order.get("status", "")),
        )

    async def close(self) -> None:
        await self._exchange.close()


PriceSource = Callable[[str], Awaitable[float]]


class PaperBroker(Broker):
    """Simulates fills against an injected price source and an in-memory
    balance sheet -- no real orders are ever placed. Symbols use the same
    'BASE/QUOTE' convention as CcxtBroker (e.g. 'BTC/USDT')."""

    def __init__(
        self,
        price_source: PriceSource,
        initial_balances: dict[str, float] | None = None,
        *,
        slippage_bps: float = 0.0,
        fee_bps: float = 0.0,
    ):
        self._price_source = price_source
        self._balances: dict[str, float] = dict(initial_balances or {})
        self._slippage_bps = slippage_bps
        self._fee_bps = fee_bps
        self._order_ids = itertools.count(1)

    async def get_balance(self, asset: str) -> float:
        return self._balances.get(asset, 0.0)

    async def get_price(self, symbol: str) -> float:
        return await self._price_source(symbol)

    async def place_order(self, symbol: str, side: OrderSide, quantity: float) -> Fill:
        if quantity <= 0:
            raise ValueError("quantity must be positive")
        base_asset, quote_asset = _split_symbol(symbol)

        mid_price = await self.get_price(symbol)
        slippage_direction = 1 if side == OrderSide.BUY else -1
        fill_price = mid_price * (1 + (self._slippage_bps / 10_000) * slippage_direction)
        notional = quantity * fill_price
        fee = notional * (self._fee_bps / 10_000)

        if side == OrderSide.BUY:
            available_quote = self._balances.get(quote_asset, 0.0)
            total_cost = notional + fee
            if total_cost > available_quote:
                raise ValueError(
                    f"Insufficient {quote_asset} balance: need {total_cost}, have {available_quote}"
                )
            self._balances[quote_asset] = available_quote - total_cost
            self._balances[base_asset] = self._balances.get(base_asset, 0.0) + quantity
        else:
            available_base = self._balances.get(base_asset, 0.0)
            if quantity > available_base:
                raise ValueError(
                    f"Insufficient {base_asset} balance: need {quantity}, have {available_base}"
                )
            self._balances[base_asset] = available_base - quantity
            self._balances[quote_asset] = self._balances.get(quote_asset, 0.0) + notional - fee

        return Fill(
            order_id=str(next(self._order_ids)),
            symbol=symbol,
            side=side,
            quantity=quantity,
            price=fill_price,
            fee=fee,
            fee_asset=quote_asset,
            status="FILLED",
        )

    async def close(self) -> None:
        return None


def _split_symbol(symbol: str) -> tuple[str, str]:
    if "/" not in symbol:
        raise ValueError(
            f"Expected a unified 'BASE/QUOTE' symbol (ccxt convention), got {symbol!r}"
        )
    base, quote = symbol.split("/", 1)
    return base, quote
