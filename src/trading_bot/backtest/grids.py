"""Parameter grids for walk-forward: one per strategy, each able to build the
strategy for a parameter set and describe it in one short line.

Grids are deliberately small. Every extra combination is another chance to
fit noise in the training window, which walk-forward then exposes as an
in-sample / out-of-sample gap.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, fields
from typing import Protocol

from trading_bot.strategy.base import Strategy
from trading_bot.strategy.donchian import DonchianBreakoutStrategy
from trading_bot.strategy.ema_rsi import EmaRsiStrategy
from trading_bot.strategy.mean_reversion import RsiMeanReversionStrategy
from trading_bot.strategy.momentum import MomentumStrategy
from trading_bot.strategy.vol_target import VolTargetTrendStrategy

Params = dict[str, float]


class StrategyGrid(Protocol):
    def combinations(self) -> list[Params]: ...

    def build(self, params: Params) -> Strategy: ...

    def describe(self, params: Params) -> str: ...


def _product(grid: object) -> list[Params]:
    names = [f.name for f in fields(grid)]  # type: ignore[arg-type]
    return [
        dict(zip(names, values)) for values in itertools.product(*(getattr(grid, n) for n in names))
    ]


@dataclass(frozen=True)
class ParamGrid:
    """EMA crossover + RSI grid (the original strategy)."""

    fast_ema: tuple[int, ...] = (8, 12, 20)
    slow_ema: tuple[int, ...] = (26, 50)
    rsi_period: tuple[int, ...] = (14,)
    rsi_oversold: tuple[float, ...] = (25.0, 30.0, 35.0)
    rsi_overbought: tuple[float, ...] = (65.0, 70.0, 75.0)

    def combinations(self) -> list[Params]:
        return [c for c in _product(self) if c["fast_ema"] < c["slow_ema"]]

    def build(self, params: Params) -> Strategy:
        return EmaRsiStrategy(
            fast_ema=int(params["fast_ema"]),
            slow_ema=int(params["slow_ema"]),
            rsi_period=int(params["rsi_period"]),
            rsi_oversold=params["rsi_oversold"],
            rsi_overbought=params["rsi_overbought"],
        )

    def describe(self, params: Params) -> str:
        return (
            f"{int(params['fast_ema'])}/{int(params['slow_ema'])} "
            f"rsi {params['rsi_oversold']:g}-{params['rsi_overbought']:g}"
        )


@dataclass(frozen=True)
class DonchianGrid:
    entry_period: tuple[int, ...] = (20, 55, 100)
    exit_period: tuple[int, ...] = (10, 20, 50)
    trend_period: tuple[int, ...] = (0, 200)

    def combinations(self) -> list[Params]:
        return [c for c in _product(self) if c["exit_period"] < c["entry_period"]]

    def build(self, params: Params) -> Strategy:
        return DonchianBreakoutStrategy(
            entry_period=int(params["entry_period"]),
            exit_period=int(params["exit_period"]),
            trend_period=int(params["trend_period"]),
        )

    def describe(self, params: Params) -> str:
        trend = int(params["trend_period"])
        return f"in {int(params['entry_period'])} out {int(params['exit_period'])}" + (
            f" sma {trend}" if trend else ""
        )


@dataclass(frozen=True)
class MomentumGrid:
    lookback: tuple[int, ...] = (50, 100, 200, 400)
    threshold_pct: tuple[float, ...] = (0.0, 2.0, 5.0)

    def combinations(self) -> list[Params]:
        return _product(self)

    def build(self, params: Params) -> Strategy:
        return MomentumStrategy(
            lookback=int(params["lookback"]), threshold_pct=params["threshold_pct"]
        )

    def describe(self, params: Params) -> str:
        return f"lb {int(params['lookback'])} band {params['threshold_pct']:g}%"


@dataclass(frozen=True)
class MeanReversionGrid:
    rsi_period: tuple[int, ...] = (2, 3)
    oversold: tuple[float, ...] = (5.0, 10.0, 15.0)
    overbought: tuple[float, ...] = (60.0, 70.0, 80.0)
    trend_period: tuple[int, ...] = (100, 200)

    def combinations(self) -> list[Params]:
        return _product(self)

    def build(self, params: Params) -> Strategy:
        return RsiMeanReversionStrategy(
            rsi_period=int(params["rsi_period"]),
            oversold=params["oversold"],
            overbought=params["overbought"],
            trend_period=int(params["trend_period"]),
        )

    def describe(self, params: Params) -> str:
        return (
            f"rsi{int(params['rsi_period'])} {params['oversold']:g}-{params['overbought']:g} "
            f"sma {int(params['trend_period'])}"
        )


GRIDS: dict[str, type] = {
    "ema_rsi": ParamGrid,
    "donchian": DonchianGrid,
    "momentum": MomentumGrid,
    "mean_reversion": MeanReversionGrid,
}


@dataclass(frozen=True)
class VolTargetGrid:
    """Fractional-position grid (backtested by backtest.weights, not the signal engine)."""

    trend_period: tuple[int, ...] = (0, 50, 100, 200)
    vol_period: tuple[int, ...] = (20, 60)
    target_vol: tuple[float, ...] = (0.4, 0.6, 0.8)
    interval: str = "1d"

    def combinations(self) -> list[Params]:
        names = ("trend_period", "vol_period", "target_vol")
        return [
            dict(zip(names, values))
            for values in itertools.product(*(getattr(self, n) for n in names))
        ]

    def build(self, params: Params) -> VolTargetTrendStrategy:
        return VolTargetTrendStrategy(
            trend_period=int(params["trend_period"]),
            vol_period=int(params["vol_period"]),
            target_vol=params["target_vol"],
            interval=self.interval,
        )

    def describe(self, params: Params) -> str:
        trend = int(params["trend_period"])
        return (
            f"{'sma ' + str(trend) if trend else 'no trend'} vol{int(params['vol_period'])} "
            f"tgt {params['target_vol']:.0%}"
        )


WEIGHT_GRIDS: dict[str, type] = {"vol_target": VolTargetGrid}
