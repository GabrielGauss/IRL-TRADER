"""Volatility-targeted trend core, long only, fractional position.

Weight = trend_on x min(max_weight, target_vol / realized_vol), where

- trend_on is 1 while the close is above its ``trend_period`` SMA (always 1
  when ``trend_period`` is 0, which gives pure volatility targeting),
- realized_vol is the annualized standard deviation of log returns over the
  last ``vol_period`` bars.

The aim is buy and hold's upside with less of its drawdown: hold less when
the market is wild, nothing while it trends down.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from trading_bot.backtest.metrics import annualization_factor


class VolTargetTrendStrategy:
    def __init__(
        self,
        trend_period: int = 200,
        vol_period: int = 30,
        target_vol: float = 0.5,
        max_weight: float = 1.0,
        interval: str = "1d",
    ):
        if trend_period < 0 or trend_period == 1:
            raise ValueError("trend_period must be 0 (off) or >= 2")
        if vol_period < 2:
            raise ValueError("vol_period must be >= 2")
        if target_vol <= 0:
            raise ValueError("target_vol must be positive")
        if not 0 < max_weight <= 1:
            raise ValueError("max_weight must be in (0, 1] (spot, no leverage)")
        self.trend_period = trend_period
        self.vol_period = vol_period
        self.target_vol = target_vol
        self.max_weight = max_weight
        self._annualize = math.sqrt(annualization_factor(interval))

    @property
    def min_lookback(self) -> int:
        return max(self.trend_period, self.vol_period + 1)

    def components(self, df: pd.DataFrame) -> pd.DataFrame:
        """Per-bar inputs behind the weight (also used to explain live trades)."""
        close = df["close"].astype(float)
        log_ret = np.log(close).diff()
        realized = log_ret.rolling(self.vol_period).std(ddof=1) * self._annualize
        if self.trend_period:
            sma = close.rolling(self.trend_period).mean()
            trend_on = (close > sma).astype(float).where(sma.notna())
        else:
            sma = pd.Series(np.nan, index=df.index)
            trend_on = pd.Series(1.0, index=df.index)
        vol_weight = (self.target_vol / realized).clip(upper=self.max_weight)
        weight = (trend_on * vol_weight).fillna(0.0).clip(lower=0.0, upper=self.max_weight)
        warm = pd.Series(np.arange(len(df)) >= self.min_lookback - 1, index=df.index)
        return pd.DataFrame(
            {
                "close": close,
                "sma": sma,
                "trend_on": trend_on,
                "realized_vol": realized,
                "weight": weight.where(warm, 0.0),
            }
        )

    def target_weights(self, df: pd.DataFrame) -> pd.Series:
        return self.components(df)["weight"]
