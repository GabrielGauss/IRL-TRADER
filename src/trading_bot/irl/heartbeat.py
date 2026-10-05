"""Signed Layer-2 heartbeats for IRL authorize calls.

With LAYER2_ENABLED=true (IRL's production default), every authorize must
carry a heartbeat signed by the MTA operator (MacroPulse). IRL rejects
heartbeats older than MAX_HEARTBEAT_DRIFT_MS (200 ms by default) or with a
non-increasing sequence_id, so a fresh one is fetched immediately before
each authorize and never reused or cached.

Fetch failures raise IrlUnavailable, which the gate turns into a blocked
order: no heartbeat, no authorization, no trade.
"""

from __future__ import annotations

from typing import Any

import aiohttp

from trading_bot.irl.client import IrlUnavailable

HEARTBEAT_FIELDS = ("sequence_id", "timestamp_ms", "regime_id", "mta_ref", "signature")


class MacroPulseHeartbeatSource:
    """GET /v1/irl/heartbeat on the MacroPulse API (needs an irl_sidecar key)."""

    def __init__(self, url: str, api_key: str, *, timeout: float = 5.0):
        if not url or not api_key:
            raise ValueError("heartbeat url and MacroPulse API key are required")
        self._url = url
        self._headers = {"X-MacroPulse-Key": api_key}
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._session: aiohttp.ClientSession | None = None

    async def fetch(self) -> dict[str, Any]:
        if self._session is None:
            self._session = aiohttp.ClientSession(headers=self._headers, timeout=self._timeout)
        try:
            async with self._session.get(self._url) as resp:
                status = resp.status
                body = await resp.json(content_type=None) if status == 200 else await resp.text()
        except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
            raise IrlUnavailable(0, "HEARTBEAT_FETCH", f"GET {self._url}: {exc!r}") from exc

        if status != 200:
            raise IrlUnavailable(status, "HEARTBEAT_FETCH", str(body)[:200])
        if not isinstance(body, dict):
            raise IrlUnavailable(status, "HEARTBEAT_FETCH", "response is not a JSON object")
        missing = [field for field in HEARTBEAT_FIELDS if field not in body]
        if missing:
            raise IrlUnavailable(status, "HEARTBEAT_FETCH", f"missing fields: {missing}")
        return {field: body[field] for field in HEARTBEAT_FIELDS}

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None
