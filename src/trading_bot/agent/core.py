"""The agent's decision: what weight of BTC to hold, and why, in words.

Uses exactly the procedure that passed walk-forward
(docs/research/2026-10-05-vol-target-core.md): pick the volatility-target
parameters by Sharpe on the trailing ``train_bars`` completed daily bars,
then take the target weight at the latest completed bar. A bar that is
still open is never used.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta

import pandas as pd

from trading_bot.backtest.grids import Params, VolTargetGrid
from trading_bot.backtest.metrics import sharpe_ratio
from trading_bot.backtest.weights import simulate_weights

TRAIN_BARS = 730
BAR = timedelta(days=1)


@dataclass(frozen=True)
class Decision:
    as_of: datetime  # open time of the latest completed daily bar
    params: Params
    train_sharpe: float
    close: float
    sma: float | None
    trend_on: bool
    realized_vol: float
    target_weight: float

    def describe(self, grid: VolTargetGrid) -> str:
        trend = int(self.params["trend_period"])
        trend_text = (
            f"close {self.close:,.2f} vs SMA{trend} {self.sma:,.2f}: trend "
            f"{'ON' if self.trend_on else 'OFF'}"
            if trend and self.sma is not None
            else "no trend filter selected"
        )
        return (
            f"Daily volatility-target core, decided on the daily bar of {self.as_of:%Y-%m-%d}. "
            f"Parameters re-selected by Sharpe ({self.train_sharpe:.2f}) on the trailing "
            f"{TRAIN_BARS} completed days: {grid.describe(self.params)}. {trend_text}. "
            f"Realized volatility {self.realized_vol:.1%} vs target "
            f"{float(self.params['target_vol']):.0%}, so target weight "
            f"{self.target_weight:.2f} of equity."
        )


def completed_bars(df: pd.DataFrame, now: datetime) -> pd.DataFrame:
    """Drop the trailing daily bar if it has not closed yet."""
    open_times = pd.to_datetime(df["open_time"])
    done = open_times + BAR <= pd.Timestamp(now).tz_localize(None)
    return df.loc[done.to_numpy()].reset_index(drop=True)


def decide(df: pd.DataFrame, now: datetime, grid: VolTargetGrid | None = None) -> Decision:
    grid = grid or VolTargetGrid()
    bars = completed_bars(df, now)
    if len(bars) < TRAIN_BARS:
        raise ValueError(f"need {TRAIN_BARS} completed daily bars, got {len(bars)}")
    train = bars.iloc[-TRAIN_BARS:].reset_index(drop=True)

    best: tuple[Params, float] | None = None
    for params in grid.combinations():
        strategy = grid.build(params)
        result = simulate_weights(
            train, strategy.target_weights(train), start=strategy.min_lookback
        )
        sharpe = sharpe_ratio(result.equity_curve, grid.interval)
        if best is None or sharpe > best[1]:
            best = (params, sharpe)
    if best is None:
        raise ValueError("the grid produced no parameter combinations")
    params, train_sharpe = best

    latest = grid.build(params).components(train).iloc[-1]
    sma = None if pd.isna(latest["sma"]) else float(latest["sma"])
    return Decision(
        as_of=pd.Timestamp(train["open_time"].iloc[-1]).to_pydatetime(),
        params=params,
        train_sharpe=train_sharpe,
        close=float(latest["close"]),
        sma=sma,
        trend_on=bool(latest["trend_on"] == 1.0),
        realized_vol=float(latest["realized_vol"]),
        target_weight=_clean(float(latest["weight"])),
    )


def _clean(weight: float) -> float:
    if math.isnan(weight):
        return 0.0
    return round(min(max(weight, 0.0), 1.0), 4)
