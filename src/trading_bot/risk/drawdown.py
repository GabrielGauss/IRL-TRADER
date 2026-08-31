"""Tracks a running equity peak and reports drawdown against it.

A stateful monitor (like TradeRepository/ExecutionEngine elsewhere in this
codebase) rather than a pure function, since "peak so far" is inherently a
running aggregate over a stream of equity readings. Each `update()` call
returns an immutable snapshot of the result.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DrawdownStatus:
    peak_equity: float
    current_equity: float
    drawdown_pct: float
    breached: bool


class DrawdownMonitor:
    def __init__(self, max_drawdown_pct: float):
        if max_drawdown_pct <= 0:
            raise ValueError("max_drawdown_pct must be positive")
        self._max_drawdown_pct = max_drawdown_pct
        self._peak_equity: float | None = None

    def update(self, equity: float) -> DrawdownStatus:
        if self._peak_equity is None or equity > self._peak_equity:
            self._peak_equity = equity

        drawdown_pct = 0.0
        if self._peak_equity > 0:
            drawdown_pct = (self._peak_equity - equity) / self._peak_equity * 100.0

        return DrawdownStatus(
            peak_equity=self._peak_equity,
            current_equity=equity,
            drawdown_pct=drawdown_pct,
            breached=drawdown_pct >= self._max_drawdown_pct,
        )

    def reset(self, equity: float | None = None) -> None:
        self._peak_equity = equity
