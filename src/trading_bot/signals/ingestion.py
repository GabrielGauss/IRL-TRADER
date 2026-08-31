"""Async ingestion pipeline: pulls raw provider payloads, validates them against
SignalPayload, and enqueues the valid ones onto a SignalQueue.

Decoupled from any specific transport (websocket, webhook, polling REST) via
the `fetch` callable, so retry/backoff and validation logic stays transport-
agnostic and independently testable without a real network source.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import ValidationError

from trading_bot.signals.queue import SignalQueue
from trading_bot.signals.schema import SignalPayload

logger = logging.getLogger(__name__)

FetchSignal = Callable[[], Awaitable[dict[str, Any]]]
InvalidPayloadHandler = Callable[[dict[str, Any], ValidationError], None]


class SignalIngestionError(Exception):
    """Raised when the signal source fails repeatedly with no successful fetch."""


class SignalIngestor:
    def __init__(
        self,
        fetch: FetchSignal,
        queue: SignalQueue,
        *,
        max_consecutive_errors: int = 5,
        base_backoff_seconds: float = 1.0,
        max_backoff_seconds: float = 30.0,
        on_invalid_payload: InvalidPayloadHandler | None = None,
    ):
        self._fetch = fetch
        self._queue = queue
        self._max_consecutive_errors = max_consecutive_errors
        self._base_backoff_seconds = base_backoff_seconds
        self._max_backoff_seconds = max_backoff_seconds
        self._on_invalid_payload = on_invalid_payload

    async def run(self, stop_after: int | None = None) -> None:
        """Consume from `fetch` until `stop_after` payloads have been processed
        (validated-and-enqueued or discarded as invalid), or forever if None.

        Raises SignalIngestionError if `fetch` fails more than
        `max_consecutive_errors` times in a row; a single success resets the
        counter.
        """
        processed = 0
        consecutive_errors = 0

        while stop_after is None or processed < stop_after:
            try:
                raw = await self._fetch()
            except Exception as exc:
                consecutive_errors += 1
                if consecutive_errors > self._max_consecutive_errors:
                    raise SignalIngestionError(
                        f"Signal source failed {consecutive_errors} times in a row"
                    ) from exc
                backoff = min(
                    self._base_backoff_seconds * (2 ** (consecutive_errors - 1)),
                    self._max_backoff_seconds,
                )
                logger.warning("Signal fetch failed (%s), retrying in %.2fs", exc, backoff)
                await asyncio.sleep(backoff)
                continue

            consecutive_errors = 0
            try:
                signal = SignalPayload.model_validate(raw)
            except ValidationError as exc:
                logger.warning("Discarding invalid signal payload: %s", exc)
                if self._on_invalid_payload is not None:
                    self._on_invalid_payload(raw, exc)
                processed += 1
                continue

            await self._queue.put(signal)
            processed += 1
