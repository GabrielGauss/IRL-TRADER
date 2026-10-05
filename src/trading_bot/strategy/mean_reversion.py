"""Short-period RSI mean reversion inside an uptrend (Connors-style), long only.

Buy a sharp dip (RSI below `oversold`) only while the close is above its
`trend_period` SMA; sell on the bounce (RSI above `overbought`) or when the
trend filter breaks, so a dip that turns into a downtrend is not held.
"""

from __future__ import annotations

import pandas as pd
import pandas_ta as ta

from trading_bot.strategy.vectorized import VectorizedStrategy


class RsiMeanReversionStrategy(VectorizedStrategy):
    def __init__(
        self,
        rsi_period: int = 2,
        oversold: float = 10.0,
        overbought: float = 70.0,
        trend_period: int = 200,
    ):
        if rsi_period < 2:
            raise ValueError("rsi_period must be at least 2")
        if not 0 < oversold < overbought < 100:
            raise ValueError("need 0 < oversold < overbought < 100")
        if trend_period < 2:
            raise ValueError("trend_period must be at least 2")
        self.rsi_period = rsi_period
        self.oversold = oversold
        self.overbought = overbought
        self.trend_period = trend_period

    @property
    def min_lookback(self) -> int:
        return max(self.rsi_period, self.trend_period) + 1

    def _rules(self, df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
        close = df["close"]
        rsi = ta.rsi(close, length=self.rsi_period)
        if rsi is None:
            rsi = pd.Series(float("nan"), index=df.index)
        uptrend = close > close.rolling(self.trend_period).mean()
        buy = uptrend & (rsi < self.oversold)
        sell = (rsi > self.overbought) | ~uptrend
        return buy, sell
