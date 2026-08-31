"""EMA crossover trend filter + RSI entry/exit.

Long bias only (spot-friendly): buy when the fast EMA is above the slow EMA
(uptrend) and RSI recovers out of oversold; sell when RSI pushes into
overbought. Otherwise hold.
"""

from __future__ import annotations

import pandas as pd
import pandas_ta as ta

from trading_bot.strategy.base import Signal, Strategy


class EmaRsiStrategy(Strategy):
    def __init__(
        self,
        fast_ema: int = 12,
        slow_ema: int = 26,
        rsi_period: int = 14,
        rsi_oversold: float = 30.0,
        rsi_overbought: float = 70.0,
    ):
        if fast_ema >= slow_ema:
            raise ValueError("fast_ema must be less than slow_ema")
        self.fast_ema = fast_ema
        self.slow_ema = slow_ema
        self.rsi_period = rsi_period
        self.rsi_oversold = rsi_oversold
        self.rsi_overbought = rsi_overbought

    @property
    def min_lookback(self) -> int:
        return max(self.slow_ema, self.rsi_period) + 1

    def generate_signal(self, df: pd.DataFrame) -> Signal:
        if len(df) < self.min_lookback:
            return Signal.HOLD

        close = df["close"]
        fast = ta.ema(close, length=self.fast_ema)
        slow = ta.ema(close, length=self.slow_ema)
        rsi = ta.rsi(close, length=self.rsi_period)

        if fast is None or slow is None or rsi is None:
            return Signal.HOLD

        fast_now, slow_now, rsi_now = fast.iloc[-1], slow.iloc[-1], rsi.iloc[-1]
        rsi_prev = rsi.iloc[-2]

        if pd.isna(fast_now) or pd.isna(slow_now) or pd.isna(rsi_now) or pd.isna(rsi_prev):
            return Signal.HOLD

        uptrend = fast_now > slow_now
        rsi_recovering_from_oversold = rsi_prev <= self.rsi_oversold < rsi_now

        if uptrend and rsi_recovering_from_oversold:
            return Signal.BUY
        if rsi_now >= self.rsi_overbought:
            return Signal.SELL
        return Signal.HOLD
