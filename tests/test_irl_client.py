from __future__ import annotations

import asyncio
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from trading_bot.irl.client import (
    AgentIdentity,
    IrlClient,
    IrlDenied,
    IrlError,
    IrlUnavailable,
    model_hash,
)

_TOKEN = "irl-test-token"
_IDENTITY = AgentIdentity(
    agent_id="00000000-0000-0000-0000-000000000001",
    model_hash_hex="a" * 64,
    model_id="trading-bot",
    prompt_version="0.1.0",
    feature_schema_id="signal-payload-v1",
    hyperparameter_checksum="b" * 64,
)
_TRACE_ID = "11111111-1111-1111-1111-111111111111"


class _FakeIrl:
    """Records requests and replies with canned responses per path."""

    def __init__(self, responses: dict[str, tuple[int, Any]]):
        self.responses = responses
        self.requests: list[tuple[str, dict[str, str], Any]] = []

    def app(self) -> web.Application:
        app = web.Application()

        async def handle(request: web.Request) -> web.Response:
            body = await request.json() if request.can_read_body else None
            self.requests.append((request.path, dict(request.headers), body))
            status, payload = self.responses[request.path]
            return web.json_response(payload, status=status)

        app.router.add_route("*", "/{tail:.*}", handle)
        return app


def _run(fake: _FakeIrl, call):
    async def scenario():
        async with TestServer(fake.app()) as server:
            client = IrlClient(str(server.make_url("")), _TOKEN)
            try:
                return await call(client)
            finally:
                await client.close()

    return asyncio.run(scenario())


def _authorize(client: IrlClient, **overrides):
    kwargs = {
        "is_buy": True,
        "quantity": 0.01,
        "asset": "BTC/USDT",
        "notional": 600.0,
        "notional_currency": "USDT",
        "venue_id": "BINANCE-PAPER",
        "client_order_id": "irl-abc123",
    }
    kwargs.update(overrides)
    return client.authorize(_IDENTITY, **kwargs)


def test_authorize_sends_spec_fields_and_bearer_token():
    fake = _FakeIrl(
        {
            "/irl/authorize": (
                200,
                {
                    "trace_id": _TRACE_ID,
                    "reasoning_hash": "c" * 64,
                    "authorized": True,
                    "shadow_blocked": False,
                },
            )
        }
    )

    result = _run(fake, _authorize)

    _, headers, body = fake.requests[0]
    assert headers["Authorization"] == f"Bearer {_TOKEN}"
    assert body["agent_id"] == _IDENTITY.agent_id
    assert body["model_hash_hex"] == _IDENTITY.model_hash_hex
    assert body["model_id"] == "trading-bot"
    assert body["prompt_version"] == "0.1.0"
    assert body["feature_schema_id"] == "signal-payload-v1"
    assert body["hyperparameter_checksum"] == "b" * 64
    assert body["action"] == {"Long": 0.01}
    assert body["quantity"] == 0.01
    assert body["asset"] == "BTC/USDT"
    assert body["notional"] == 600.0
    assert body["notional_currency"] == "USDT"
    assert body["order_type"] == "MARKET"
    assert body["venue_id"] == "BINANCE-PAPER"
    assert body["client_order_id"] == "irl-abc123"
    assert body["reduce_only"] is False
    assert isinstance(body["agent_valid_time"], int)
    assert result.trace_id == _TRACE_ID
    assert result.reasoning_hash == "c" * 64
    assert result.authorized is True
    assert result.shadow_blocked is False


def test_authorize_encodes_sell_as_reduce_only_short():
    fake = _FakeIrl(
        {
            "/irl/authorize": (
                200,
                {"trace_id": _TRACE_ID, "reasoning_hash": "c", "authorized": True},
            )
        }
    )

    _run(fake, lambda client: _authorize(client, is_buy=False, quantity=0.5))

    body = fake.requests[0][2]
    assert body["action"] == {"Short": 0.5}
    assert body["reduce_only"] is True


def test_authorize_policy_violation_raises_irl_denied():
    fake = _FakeIrl(
        {"/irl/authorize": (403, {"error": "NOTIONAL_CAP", "message": "notional over cap"})}
    )

    with pytest.raises(IrlDenied) as exc_info:
        _run(fake, _authorize)

    assert exc_info.value.status == 403
    assert exc_info.value.code == "NOTIONAL_CAP"
    assert "notional over cap" in str(exc_info.value)


def test_server_error_raises_irl_unavailable():
    fake = _FakeIrl({"/irl/authorize": (503, {"error": "NOT_READY", "message": "db down"})})

    with pytest.raises(IrlUnavailable):
        _run(fake, _authorize)


def test_validation_error_raises_plain_irl_error():
    fake = _FakeIrl({"/irl/authorize": (422, {"error": "VALIDATION", "message": "bad venue"})})

    with pytest.raises(IrlError) as exc_info:
        _run(fake, _authorize)

    assert not isinstance(exc_info.value, (IrlDenied, IrlUnavailable))
    assert exc_info.value.status == 422


def test_unreachable_server_raises_irl_unavailable():
    async def scenario():
        client = IrlClient("http://127.0.0.1:9", _TOKEN, timeout=2)
        try:
            await _authorize(client)
        finally:
            await client.close()

    with pytest.raises(IrlUnavailable):
        asyncio.run(scenario())


def test_bind_sends_execution_time_ms_per_spec():
    fake = _FakeIrl(
        {
            "/irl/bind-execution": (
                200,
                {
                    "trace_id": _TRACE_ID,
                    "final_proof": "d" * 64,
                    "verification_status": "Matched",
                    "execution_status": "FILLED",
                    "execution_time": "2026-10-04T00:00:00Z",
                },
            )
        }
    )

    result = _run(
        fake,
        lambda client: client.bind(
            _TRACE_ID,
            exchange_tx_id="EX-1",
            execution_status="Filled",
            execution_price=60000.0,
            executed_quantity=0.01,
            executed_side="Long",
            asset="BTC/USDT",
            execution_time_ms=1_790_000_000_000,
        ),
    )

    body = fake.requests[0][2]
    assert body == {
        "trace_id": _TRACE_ID,
        "exchange_tx_id": "EX-1",
        "execution_status": "Filled",
        "execution_price": 60000.0,
        "executed_quantity": 0.01,
        "executed_side": "Long",
        "asset": "BTC/USDT",
        "execution_time_ms": 1_790_000_000_000,
    }
    assert result.final_proof == "d" * 64
    assert result.verification_status == "Matched"
    assert result.divergence_reason is None


def test_bind_omits_unset_optional_fields():
    fake = _FakeIrl(
        {
            "/irl/bind-execution": (
                200,
                {"trace_id": _TRACE_ID, "final_proof": "d", "verification_status": "Matched"},
            )
        }
    )

    _run(
        fake,
        lambda client: client.bind(
            _TRACE_ID, exchange_tx_id="irl-abc", execution_status="Rejected"
        ),
    )

    assert fake.requests[0][2] == {
        "trace_id": _TRACE_ID,
        "exchange_tx_id": "irl-abc",
        "execution_status": "Rejected",
    }


def test_register_agent_returns_new_agent_id():
    # Shape returned by IRL v1.3.0's register_agent handler (its SDK wrongly reads "id").
    fake = _FakeIrl({"/irl/agents": (201, {"agent_id": "agent-uuid", "name": "bot"})})

    agent_id = _run(
        fake,
        lambda client: client.register_agent(
            name="bot", model_hash_hex="a" * 64, max_notional=500.0
        ),
    )

    assert agent_id == "agent-uuid"
    assert fake.requests[0][2] == {
        "name": "bot",
        "model_hash_hex": "a" * 64,
        "max_notional": 500.0,
    }


def test_health_returns_true_when_engine_reports_ok():
    fake = _FakeIrl({"/irl/health": (200, {"status": "ok", "db_ok": True})})

    assert _run(fake, lambda client: client.health()) is True


def test_health_returns_false_when_database_is_down():
    fake = _FakeIrl({"/irl/health": (200, {"status": "degraded", "db_ok": False})})

    assert _run(fake, lambda client: client.health()) is False


def test_model_hash_is_canonical_and_key_order_independent():
    first = model_hash({"b": 1, "a": [1, 2], "c": {"y": 1.5, "x": "s"}})
    second = model_hash({"c": {"x": "s", "y": 1.5}, "a": [1, 2], "b": 1})

    assert first == second
    assert len(first) == 64
    assert first != model_hash({"b": 2, "a": [1, 2], "c": {"y": 1.5, "x": "s"}})


def test_client_rejects_blank_base_url_or_token():
    with pytest.raises(ValueError):
        IrlClient("", _TOKEN)
    with pytest.raises(ValueError):
        IrlClient("http://irl", "")


def test_health_accepts_status_only_response_seen_on_real_engine():
    # IRL v1.3.0's unauthenticated /irl/health returns only {"status": "ok"},
    # despite db_ok being marked required in its OpenAPI spec.
    fake = _FakeIrl({"/irl/health": (200, {"status": "ok"})})

    assert _run(fake, lambda client: client.health()) is True


def test_authorize_includes_heartbeat_when_given():
    heartbeat = {
        "sequence_id": 7,
        "timestamp_ms": 1,
        "regime_id": 0,
        "mta_ref": "e",
        "signature": "f",
    }
    fake = _FakeIrl(
        {
            "/irl/authorize": (
                200,
                {"trace_id": _TRACE_ID, "reasoning_hash": "c", "authorized": True},
            )
        }
    )

    _run(fake, lambda client: _authorize(client, heartbeat=heartbeat))

    assert fake.requests[0][2]["heartbeat"] == heartbeat


def test_authorize_omits_heartbeat_by_default():
    fake = _FakeIrl(
        {
            "/irl/authorize": (
                200,
                {"trace_id": _TRACE_ID, "reasoning_hash": "c", "authorized": True},
            )
        }
    )

    _run(fake, _authorize)

    assert "heartbeat" not in fake.requests[0][2]
