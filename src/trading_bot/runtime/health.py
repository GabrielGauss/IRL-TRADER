"""Health state for the main loop, queried by the webhook's /health route and
usable from a CLI status check. A thin observation layer -- it records what
the controller tells it and reports a snapshot; it doesn't decide policy.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True)
class HealthStatus:
    healthy: bool
    started_at: datetime
    uptime_seconds: float
    signals_processed: int
    last_signal_at: datetime | None
    last_error: str | None
    kill_switch_tripped: bool


class HealthMonitor:
    def __init__(self) -> None:
        self._started_at = datetime.now(UTC)
        self._signals_processed = 0
        self._last_signal_at: datetime | None = None
        self._last_error: str | None = None
        self._kill_switch_tripped = False

    def record_signal_processed(self) -> None:
        self._signals_processed += 1
        self._last_signal_at = datetime.now(UTC)

    def record_error(self, error: str) -> None:
        self._last_error = error

    def record_kill_switch_tripped(self) -> None:
        self._kill_switch_tripped = True

    def status(self) -> HealthStatus:
        now = datetime.now(UTC)
        return HealthStatus(
            healthy=not self._kill_switch_tripped,
            started_at=self._started_at,
            uptime_seconds=(now - self._started_at).total_seconds(),
            signals_processed=self._signals_processed,
            last_signal_at=self._last_signal_at,
            last_error=self._last_error,
            kill_switch_tripped=self._kill_switch_tripped,
        )
