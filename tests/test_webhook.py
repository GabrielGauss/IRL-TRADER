from __future__ import annotations

import asyncio

import pytest
from aiohttp.test_utils import TestClient, TestServer

from trading_bot.runtime.health import HealthMonitor
from trading_bot.runtime.metrics import PerformanceSnapshot
from trading_bot.runtime.webhook import create_app

_ZERO_SNAPSHOT = PerformanceSnapshot(
    equity=0.0,
    cash=0.0,
    realized_pnl=0.0,
    unrealized_pnl=0.0,
    drawdown_pct=0.0,
    sharpe_ratio=0.0,
    num_trades=0,
)


def _provider(snapshot: PerformanceSnapshot):
    async def provide() -> PerformanceSnapshot:
        return snapshot

    return provide


def test_post_signals_accepts_valid_json_and_enqueues_it():
    async def scenario():
        raw_queue: asyncio.Queue = asyncio.Queue()
        app = create_app(raw_queue, HealthMonitor(), _provider(_ZERO_SNAPSHOT))

        async with TestClient(TestServer(app)) as client:
            resp = await client.post(
                "/signals", json={"source": "tv", "symbol": "BTCUSDT", "action": "BUY"}
            )
            assert resp.status == 202
            body = await resp.json()
            assert body["status"] == "accepted"

        queued = raw_queue.get_nowait()
        assert queued["symbol"] == "BTCUSDT"

    asyncio.run(scenario())


def test_post_signals_rejects_non_json_body():
    async def scenario():
        raw_queue: asyncio.Queue = asyncio.Queue()
        app = create_app(raw_queue, HealthMonitor(), _provider(_ZERO_SNAPSHOT))

        async with TestClient(TestServer(app)) as client:
            resp = await client.post(
                "/signals", data="not json", headers={"Content-Type": "application/json"}
            )
            assert resp.status == 400
        assert raw_queue.empty()

    asyncio.run(scenario())


def test_post_signals_rejects_non_object_json():
    async def scenario():
        raw_queue: asyncio.Queue = asyncio.Queue()
        app = create_app(raw_queue, HealthMonitor(), _provider(_ZERO_SNAPSHOT))

        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/signals", json=[1, 2, 3])
            assert resp.status == 400
        assert raw_queue.empty()

    asyncio.run(scenario())


def test_get_health_reports_current_status():
    async def scenario():
        health = HealthMonitor()
        health.record_signal_processed()
        app = create_app(asyncio.Queue(), health, _provider(_ZERO_SNAPSHOT))

        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/health")
            assert resp.status == 200
            body = await resp.json()

        assert body["healthy"] is True
        assert body["signals_processed"] == 1
        assert body["kill_switch_tripped"] is False

    asyncio.run(scenario())


def test_get_health_reflects_kill_switch_tripped():
    async def scenario():
        health = HealthMonitor()
        health.record_kill_switch_tripped()
        app = create_app(asyncio.Queue(), health, _provider(_ZERO_SNAPSHOT))

        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/health")
            body = await resp.json()

        assert body["healthy"] is False
        assert body["kill_switch_tripped"] is True

    asyncio.run(scenario())


def test_get_metrics_reports_the_current_snapshot():
    async def scenario():
        snapshot = PerformanceSnapshot(
            equity=1234.5,
            cash=500.0,
            realized_pnl=10.0,
            unrealized_pnl=5.0,
            drawdown_pct=2.5,
            sharpe_ratio=1.1,
            num_trades=3,
        )
        app = create_app(asyncio.Queue(), HealthMonitor(), _provider(snapshot))

        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/metrics")
            body = await resp.json()

        assert body["equity"] == pytest.approx(1234.5)
        assert body["num_trades"] == 3
        assert body["sharpe_ratio"] == pytest.approx(1.1)

    asyncio.run(scenario())


_SECRET = "s3cret-token-for-tests-0123456789"
_SIGNAL = {"source": "tv", "symbol": "BTCUSDT", "action": "BUY"}


def _post_with_secret(json_body, headers=None):
    """POST /signals to an app that requires _SECRET; returns (status, raw_queue)."""

    async def scenario():
        raw_queue: asyncio.Queue = asyncio.Queue()
        app = create_app(
            raw_queue, HealthMonitor(), _provider(_ZERO_SNAPSHOT), signal_secret=_SECRET
        )
        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/signals", json=json_body, headers=headers or {})
            return resp.status, raw_queue

    return asyncio.run(scenario())


def test_post_signals_accepts_correct_secret_header():
    status, raw_queue = _post_with_secret(_SIGNAL, headers={"X-Signal-Secret": _SECRET})

    assert status == 202
    assert raw_queue.get_nowait()["symbol"] == "BTCUSDT"


def test_post_signals_accepts_correct_body_passphrase_and_strips_it():
    status, raw_queue = _post_with_secret({**_SIGNAL, "passphrase": _SECRET})

    assert status == 202
    queued = raw_queue.get_nowait()
    assert queued["symbol"] == "BTCUSDT"
    assert "passphrase" not in queued


def test_post_signals_rejects_missing_secret():
    status, raw_queue = _post_with_secret(_SIGNAL)

    assert status == 401
    assert raw_queue.empty()


def test_post_signals_rejects_wrong_secret_header():
    status, raw_queue = _post_with_secret(_SIGNAL, headers={"X-Signal-Secret": "wrong"})

    assert status == 401
    assert raw_queue.empty()


def test_post_signals_rejects_wrong_body_passphrase():
    status, raw_queue = _post_with_secret({**_SIGNAL, "passphrase": "wrong"})

    assert status == 401
    assert raw_queue.empty()


def test_post_signals_rejects_non_string_passphrase():
    status, raw_queue = _post_with_secret({**_SIGNAL, "passphrase": 12345})

    assert status == 401
    assert raw_queue.empty()


def test_post_signals_header_takes_precedence_over_body_passphrase():
    status, raw_queue = _post_with_secret(
        {**_SIGNAL, "passphrase": _SECRET}, headers={"X-Signal-Secret": "wrong"}
    )

    assert status == 401
    assert raw_queue.empty()


def test_post_signals_rejects_non_object_json_without_secret_as_unauthorized():
    status, raw_queue = _post_with_secret([1, 2, 3])

    assert status == 401
    assert raw_queue.empty()


def test_post_signals_strips_passphrase_even_when_auth_is_disabled():
    async def scenario():
        raw_queue: asyncio.Queue = asyncio.Queue()
        app = create_app(raw_queue, HealthMonitor(), _provider(_ZERO_SNAPSHOT))
        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/signals", json={**_SIGNAL, "passphrase": "x"})
            assert resp.status == 202
        return raw_queue.get_nowait()

    assert "passphrase" not in asyncio.run(scenario())


def test_health_stays_open_when_secret_is_configured():
    async def scenario():
        app = create_app(
            asyncio.Queue(), HealthMonitor(), _provider(_ZERO_SNAPSHOT), signal_secret=_SECRET
        )
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/health")
            return resp.status

    assert asyncio.run(scenario()) == 200
