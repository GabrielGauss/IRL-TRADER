from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum

import pandas as pd


class Signal(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


class Strategy(ABC):
    """A strategy turns an OHLCV DataFrame into a single trading Signal for the latest bar."""

    @abstractmethod
    def generate_signal(self, df: pd.DataFrame) -> Signal:
        """df must contain at least ['open', 'high', 'low', 'close', 'volume'] columns,
        ordered oldest-to-newest, with enough history for the strategy's lookback."""
        raise NotImplementedError
