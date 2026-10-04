from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from trading_bot.execution.broker import OrderSide, PaperBroker
from trading_bot.execution.router import OrderPlan
from trading_bot.irl.client import (
    AgentIdentity,
    AuthorizeResult,
    BindResult,
    IrlClient,
    IrlDenied,
    IrlUnavailable,
)
from trading_bot.irl.gate import IrlGate, OrderBlocked, PassthroughGate

_IDENTITY = AgentIdentity(
    agent_id="00000000-0000-0000-0000-000000000001",
    model_hash_hex="a" * 64,
    model_id="trading-bot",
    prompt_version="0.1.0",
    feature_schema_id="signal-payload-v1",
    hyperparameter_checksum="b" * 64,
)
_TRACE = "11111111-1111-1111-1111-111111111111"


async def _price(symbol: str) -> float:
    return 100.0


def _paper_broker() -> PaperBroker:
    return PaperBroker(_price, initial_balances={"USDT": 1000.0, "BTC": 2.0})


def _irl_client(**overrides) -> AsyncMock:
    client = AsyncMock(spec=IrlClient)
    client.authorize.return_value = overrides.get(
        "authorize",
        AuthorizeResult(
            trace_id=_TRACE, reasoning_hash="c" * 64, authorized=True, shadow_blocked=False
        ),
    )
    client.bind.return_value = overrides.get(
        "bind",
        BindResult(
            trace_id=_TRACE,
            final_proof="d" * 64,
            verification_status="Matched",
            divergence_reason=None,
        ),
    )
    return client


def _gate(client) -> IrlGate:
    return IrlGate(client, _IDENTITY, venue_id="BINANCE-PAPER", notional_currency="USDT")


_BUY = OrderPlan(symbol="BTC/USDT", side=OrderSide.BUY, quantity=1.0)
_SELL = OrderPlan(symbol="BTC/USDT", side=OrderSide.SELL, quantity=0.5)


def test_passthrough_gate_places_order_without_receipt():
    broker = _paper_broker()

    result = asyncio.run(PassthroughGate().execute(broker, _BUY, price=100.0))

    assert result.fill.side is OrderSide.BUY
    assert result.receipt is None


def test_irl_gate_authorizes_places_then_binds_with_shared_client_order_id():
    broker = _paper_broker()
    client = _irl_client()

    result = asyncio.run(_gate(client).execute(broker, _BUY, price=100.0))

    auth_kwargs = client.authorize.await_args.kwargs
    assert client.authorize.await_args.args[0] == _IDENTITY
    assert auth_kwargs["is_buy"] is True
    assert auth_kwargs["quantity"] == 1.0
    assert auth_kwargs["asset"] == "BTC/USDT"
    assert auth_kwargs["notional"] == pytest.approx(100.0)
    assert auth_kwargs["venue_id"] == "BINANCE-PAPER"
    client_order_id = auth_kwargs["client_order_id"]
    assert client_order_id.startswith("irl-") and len(client_order_id) <= 36

    # The exchange order carries the same id, linking it to the IRL trace.
    assert result.fill.order_id == client_order_id

    bind_args = client.bind.await_args
    assert bind_args.args[0] == _TRACE
    assert bind_args.kwargs["exchange_tx_id"] == client_order_id
    assert bind_args.kwargs["execution_status"] == "Filled"
    assert bind_args.kwargs["executed_side"] == "Long"
    assert bind_args.kwargs["executed_quantity"] == 1.0
    assert bind_args.kwargs["asset"] == "BTC/USDT"

    assert result.receipt.trace_id == _TRACE
    assert result.receipt.reasoning_hash == "c" * 64
    assert result.receipt.final_proof == "d" * 64
    assert result.receipt.verification_status == "Matched"
    assert result.receipt.bind_error is None


def test_irl_gate_binds_sell_as_short():
    client = _irl_client()

    asyncio.run(_gate(client).execute(_paper_broker(), _SELL, price=100.0))

    assert client.authorize.await_args.kwargs["is_buy"] is False
    assert client.bind.await_args.kwargs["executed_side"] == "Short"


@pytest.mark.parametrize(
    "error",
    [
        IrlDenied(403, "NOTIONAL_CAP", "over cap"),
        IrlUnavailable(503, "NOT_READY", "db down"),
    ],
)
def test_irl_gate_blocks_order_when_authorize_fails(error):
    broker = AsyncMock(wraps=_paper_broker())
    client = _irl_client()
    client.authorize.side_effect = error

    with pytest.raises(OrderBlocked) as exc_info:
        asyncio.run(_gate(client).execute(broker, _BUY, price=100.0))

    assert exc_info.value.policy_denied is isinstance(error, IrlDenied)
    broker.place_order.assert_not_awaited()
    client.bind.assert_not_awaited()


def test_irl_gate_blocks_order_when_not_authorized():
    broker = AsyncMock(wraps=_paper_broker())
    client = _irl_client(
        authorize=AuthorizeResult(
            trace_id=_TRACE, reasoning_hash="c", authorized=False, shadow_blocked=False
        )
    )

    with pytest.raises(OrderBlocked) as exc_info:
        asyncio.run(_gate(client).execute(broker, _BUY, price=100.0))

    assert exc_info.value.trace_id == _TRACE
    broker.place_order.assert_not_awaited()


def test_irl_gate_proceeds_when_only_shadow_blocked():
    client = _irl_client(
        authorize=AuthorizeResult(
            trace_id=_TRACE, reasoning_hash="c", authorized=True, shadow_blocked=True
        )
    )

    result = asyncio.run(_gate(client).execute(_paper_broker(), _BUY, price=100.0))

    assert result.receipt.shadow_blocked is True
    assert result.fill is not None


def test_irl_gate_binds_rejected_then_reraises_when_order_fails():
    broker = AsyncMock()
    broker.place_order.side_effect = RuntimeError("exchange down")
    client = _irl_client()

    with pytest.raises(RuntimeError, match="exchange down"):
        asyncio.run(_gate(client).execute(broker, _BUY, price=100.0))

    bind_kwargs = client.bind.await_args.kwargs
    assert bind_kwargs["execution_status"] == "Rejected"
    assert bind_kwargs["exchange_tx_id"].startswith("irl-")


def test_irl_gate_returns_fill_even_when_bind_fails():
    client = _irl_client()
    client.bind.side_effect = IrlUnavailable(503, "NOT_READY", "db down")

    result = asyncio.run(_gate(client).execute(_paper_broker(), _BUY, price=100.0))

    assert result.fill is not None
    assert result.receipt.final_proof is None
    assert "db down" in result.receipt.bind_error
