"""Time-series momentum, long only.

Hold while the return over the last `lookback` bars is above
`threshold_pct`; exit once it falls below `-threshold_pct`. A non-zero
threshold adds a dead band that cuts whipsaw trades around zero.
"""

from __future__ import annotations

import pandas as pd

from trading_bot.strategy.vectorized import VectorizedStrategy


class MomentumStrategy(VectorizedStrategy):
    def __init__(self, lookback: int = 200, threshold_pct: float = 0.0):
        if lookback < 1:
            raise ValueError("lookback must be at least 1")
        if threshold_pct < 0:
            raise ValueError("threshold_pct must be >= 0")
        self.lookback = lookback
        self.threshold_pct = threshold_pct

    @property
    def min_lookback(self) -> int:
        return self.lookback + 1

    def _rules(self, df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
        close = df["close"]
        momentum_pct = (close / close.shift(self.lookback) - 1) * 100
        return momentum_pct > self.threshold_pct, momentum_pct < -self.threshold_pct
