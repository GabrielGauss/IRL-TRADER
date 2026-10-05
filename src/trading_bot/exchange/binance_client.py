"""Thin wrapper around python-binance, always testnet-routed unless explicitly overridden.

Only the operations this bot needs are exposed (get_klines, get_balance,
place_market_order) so the rest of the codebase never touches the raw
python-binance client directly and can be mocked with a single fake.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import pandas as pd
from binance.client import Client

from trading_bot.config import Settings
from trading_bot.exchange.order_side import OrderSide

__all__ = ["KLINE_COLUMNS", "BinanceClient", "OrderResult", "OrderSide"]

logger = logging.getLogger(__name__)

KLINE_COLUMNS = [
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time",
    "quote_asset_volume",
    "num_trades",
    "taker_buy_base",
    "taker_buy_quote",
    "ignore",
]


@dataclass(frozen=True)
class OrderResult:
    order_id: str
    symbol: str
    side: OrderSide
    quantity: float
    status: str


class BinanceClient:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._client = Client(
            api_key=settings.binance_api_key or None,
            api_secret=settings.binance_api_secret or None,
            testnet=settings.use_testnet,
        )
        logger.info("BinanceClient initialized (testnet=%s)", settings.use_testnet)

    def get_klines(self, symbol: str, interval: str, limit: int = 500) -> pd.DataFrame:
        raw = self._client.get_klines(symbol=symbol, interval=interval, limit=limit)
        return _klines_frame(raw)

    def get_historical_klines(
        self, symbol: str, interval: str, start: str, end: str | None = None
    ) -> pd.DataFrame:
        """Any length of history (python-binance paginates the 1000-bar limit)."""
        raw = self._client.get_historical_klines(symbol, interval, start, end)
        return _klines_frame(raw)

    def get_balance(self, asset: str) -> float:
        info = self._client.get_asset_balance(asset=asset)
        if info is None:
            raise RuntimeError(f"No balance info returned for asset {asset!r}")
        return float(info["free"])

    def place_market_order(self, symbol: str, side: OrderSide, quantity: float) -> OrderResult:
        logger.info("Placing %s market order: %s qty=%s", side.value, symbol, quantity)
        response = self._client.create_order(
            symbol=symbol,
            side=side.value,
            type="MARKET",
            quantity=quantity,
        )
        return OrderResult(
            order_id=str(response["orderId"]),
            symbol=response["symbol"],
            side=side,
            quantity=quantity,
            status=response["status"],
        )


def _klines_frame(raw: list[list[object]]) -> pd.DataFrame:
    df = pd.DataFrame(raw, columns=KLINE_COLUMNS)
    numeric_cols = ["open", "high", "low", "close", "volume"]
    df[numeric_cols] = df[numeric_cols].astype(float)
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
    return df[["open_time", "open", "high", "low", "close", "volume"]]
