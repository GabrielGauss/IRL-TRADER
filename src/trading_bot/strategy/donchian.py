"""Donchian channel breakout (turtle-style trend following), long only.

Buy when the close breaks above the highest high of the previous
`entry_period` bars; sell when it breaks below the lowest low of the previous
`exit_period` bars. With `trend_period > 0`, entries also require the close to
be above its `trend_period` simple moving average.
"""

from __future__ import annotations

import pandas as pd

from trading_bot.strategy.vectorized import VectorizedStrategy


class DonchianBreakoutStrategy(VectorizedStrategy):
    def __init__(self, entry_period: int = 55, exit_period: int = 20, trend_period: int = 0):
        if entry_period < 2 or exit_period < 2:
            raise ValueError("entry_period and exit_period must be at least 2")
        if trend_period < 0:
            raise ValueError("trend_period must be >= 0 (0 disables the filter)")
        self.entry_period = entry_period
        self.exit_period = exit_period
        self.trend_period = trend_period

    @property
    def min_lookback(self) -> int:
        return max(self.entry_period, self.exit_period, self.trend_period) + 1

    def _rules(self, df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
        close = df["close"]
        upper = df["high"].rolling(self.entry_period).max().shift(1)
        lower = df["low"].rolling(self.exit_period).min().shift(1)
        buy = close > upper
        if self.trend_period:
            buy &= close > close.rolling(self.trend_period).mean()
        sell = close < lower
        return buy, sell
