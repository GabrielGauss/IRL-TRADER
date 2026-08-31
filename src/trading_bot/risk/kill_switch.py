"""Portfolio-level circuit breaker.

Decoupled from any specific metric source (DrawdownMonitor, execution
engine, backtest) -- callers compute drawdown_pct/daily_loss_pct however
is appropriate for their context and pass the numbers in. Once tripped,
the switch latches: every subsequent check raises until `reset()` is
called explicitly, so a recovering metric can never silently un-trip it.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class KillSwitchLimits:
    max_drawdown_pct: float
    max_daily_loss_pct: float
    max_consecutive_losses: int | None = None


class KillSwitchTripped(Exception):
    """The kill switch has fired. Caller must halt all new trading until reset()."""


class KillSwitch:
    def __init__(self, limits: KillSwitchLimits):
        self._limits = limits
        self._tripped = False
        self._trip_reason: str | None = None

    @property
    def tripped(self) -> bool:
        return self._tripped

    def check(
        self, *, drawdown_pct: float, daily_loss_pct: float, consecutive_losses: int = 0
    ) -> None:
        if self._tripped:
            raise KillSwitchTripped(self._trip_reason or "kill switch previously tripped")

        reasons = []
        if drawdown_pct >= self._limits.max_drawdown_pct:
            reasons.append(
                f"drawdown {drawdown_pct:.2f}% >= limit {self._limits.max_drawdown_pct}%"
            )
        if daily_loss_pct >= self._limits.max_daily_loss_pct:
            reasons.append(
                f"daily loss {daily_loss_pct:.2f}% >= limit {self._limits.max_daily_loss_pct}%"
            )
        if (
            self._limits.max_consecutive_losses is not None
            and consecutive_losses >= self._limits.max_consecutive_losses
        ):
            reasons.append(
                f"{consecutive_losses} consecutive losses >= limit "
                f"{self._limits.max_consecutive_losses}"
            )

        if reasons:
            self._trip_reason = "; ".join(reasons)
            self._tripped = True
            raise KillSwitchTripped(self._trip_reason)

    def reset(self) -> None:
        self._tripped = False
        self._trip_reason = None
