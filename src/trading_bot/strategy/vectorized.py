"""Base for strategies defined by a vectorized rule over the whole series.

`generate_signal` is the last value of `generate_signals` on the prefix seen
so far, so the live bot and the backtester run the same code. Every rule must
use only trailing windows (rolling, shift, recursive indicators): the tests
replay each strategy bar by bar and assert that prefix signals equal the
full-series ones, which is exactly the no-look-ahead property.
"""

from __future__ import annotations

from abc import abstractmethod

import pandas as pd

from trading_bot.strategy.base import Signal, Strategy


class VectorizedStrategy(Strategy):
    @property
    @abstractmethod
    def min_lookback(self) -> int:
        """Bars needed before the first non-HOLD signal can be emitted."""

    @abstractmethod
    def _rules(self, df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
        """Boolean (buy, sell) masks; NaN comparisons must evaluate to False."""

    def generate_signal(self, df: pd.DataFrame) -> Signal:
        if len(df) < self.min_lookback:
            return Signal.HOLD
        return Signal(self.generate_signals(df).iloc[-1])

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        signals = pd.Series(Signal.HOLD, index=df.index, dtype=object)
        if len(df) < self.min_lookback:
            return signals
        buy, sell = self._rules(df)
        warm = pd.Series(range(len(df)), index=df.index) >= self.min_lookback - 1
        buy = buy.fillna(False).astype(bool) & warm
        sell = sell.fillna(False).astype(bool) & warm & ~buy
        signals[sell] = Signal.SELL
        signals[buy] = Signal.BUY
        return signals
