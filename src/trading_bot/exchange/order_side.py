"""Shared BUY/SELL order side, used by both the legacy python-binance stack
(exchange/binance_client.py, execution/engine.py) and the async broker
abstraction (execution/broker.py) so the two stacks share one type instead
of two identically-named-but-incompatible enums.
"""

from __future__ import annotations

from enum import Enum


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
