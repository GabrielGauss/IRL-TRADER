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

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        """Signal for every bar, each computed only from bars up to and
        including it. The default replays generate_signal bar by bar (slow but
        trivially free of look-ahead); strategies may override with a
        vectorized version, which must match this output exactly."""
        return pd.Series(
            [self.generate_signal(df.iloc[: i + 1]) for i in range(len(df))],
            index=df.index,
            dtype=object,
        )
