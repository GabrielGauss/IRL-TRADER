from __future__ import annotations

import asyncio
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from trading_bot.irl.client import IrlUnavailable
from trading_bot.irl.heartbeat import MacroPulseHeartbeatSource

_HEARTBEAT = {
    "sequence_id": 42,
    "timestamp_ms": 1_790_000_000_000,
    "regime_id": 2,
    "mta_ref": "e" * 64,
    "signature": "f" * 128,
}


def _fetch(status: int, payload: Any) -> tuple[Any, list[dict[str, str]]]:
    seen_headers: list[dict[str, str]] = []

    async def handler(request: web.Request) -> web.Response:
        seen_headers.append(dict(request.headers))
        return web.json_response(payload, status=status)

    async def scenario():
        app = web.Application()
        app.router.add_get("/v1/irl/heartbeat", handler)
        async with TestServer(app) as server:
            source = MacroPulseHeartbeatSource(str(server.make_url("/v1/irl/heartbeat")), "mp_key")
            try:
                return await source.fetch()
            finally:
                await source.close()

    return asyncio.run(scenario()), seen_headers


def test_fetch_returns_signed_heartbeat_and_sends_api_key():
    heartbeat, headers = _fetch(200, _HEARTBEAT)

    assert heartbeat == _HEARTBEAT
    assert headers[0]["X-MacroPulse-Key"] == "mp_key"


def test_fetch_drops_unexpected_fields():
    heartbeat, _ = _fetch(200, {**_HEARTBEAT, "extra": "ignored"})

    assert heartbeat == _HEARTBEAT


@pytest.mark.parametrize("status", [401, 403, 503])
def test_fetch_raises_unavailable_on_error_status(status):
    with pytest.raises(IrlUnavailable):
        _fetch(status, {"detail": "nope"})


def test_fetch_raises_unavailable_when_fields_missing():
    incomplete = {k: v for k, v in _HEARTBEAT.items() if k != "signature"}

    with pytest.raises(IrlUnavailable, match="signature"):
        _fetch(200, incomplete)


def test_fetch_raises_unavailable_when_unreachable():
    async def scenario():
        source = MacroPulseHeartbeatSource("http://127.0.0.1:9/v1/irl/heartbeat", "k", timeout=2)
        try:
            await source.fetch()
        finally:
            await source.close()

    with pytest.raises(IrlUnavailable):
        asyncio.run(scenario())


def test_source_rejects_blank_url_or_key():
    with pytest.raises(ValueError):
        MacroPulseHeartbeatSource("", "k")
    with pytest.raises(ValueError):
        MacroPulseHeartbeatSource("http://api:8000/v1/irl/heartbeat", "")
