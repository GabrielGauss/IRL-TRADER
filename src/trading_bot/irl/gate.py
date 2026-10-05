"""Order gates: the single path through which the controller places orders.

PassthroughGate places orders directly. IrlGate wraps each order in IRL's
authorize -> place -> bind chain:

  1. authorize the intent with IRL; a denial, an unreachable IRL, or an
     unauthorized result blocks the order (fail closed) via OrderBlocked,
  2. place the order with the same client_order_id that was sealed in the
     trace, so the exchange order is linked to the IRL trace,
  3. bind the outcome: Filled after a fill, Rejected if placing raised (the
     error is then re-raised, keeping the controller's crash-on-unexpected
     behavior).

A bind failure after a real fill never raises: the trade already happened,
so the fill is returned with bind_error set for manual reconciliation (IRL
lists unbound traces under /irl/pending and /irl/orphans).
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any, Protocol

from trading_bot.execution.broker import Broker, Fill, OrderSide
from trading_bot.execution.router import OrderPlan
from trading_bot.irl.client import AgentIdentity, IrlClient, IrlDenied, IrlError

logger = logging.getLogger(__name__)

# Binance caps client order ids at 36 chars of [A-Za-z0-9-_].
_CLIENT_ORDER_ID_PREFIX = "irl-"


class OrderBlocked(Exception):
    """The gate refused to place an order. No order was sent."""

    def __init__(self, reason: str, *, policy_denied: bool, trace_id: str | None = None):
        super().__init__(reason)
        self.reason = reason
        self.policy_denied = policy_denied
        self.trace_id = trace_id


@dataclass(frozen=True)
class IrlReceipt:
    trace_id: str
    reasoning_hash: str
    shadow_blocked: bool
    final_proof: str | None
    verification_status: str | None
    bind_error: str | None


@dataclass(frozen=True)
class ExecutionResult:
    fill: Fill
    receipt: IrlReceipt | None


class HeartbeatSource(Protocol):
    async def fetch(self) -> dict[str, Any]: ...


class OrderGate(Protocol):
    async def execute(
        self, broker: Broker, plan: OrderPlan, *, price: float
    ) -> ExecutionResult: ...


class PassthroughGate:
    async def execute(self, broker: Broker, plan: OrderPlan, *, price: float) -> ExecutionResult:
        fill = await broker.place_order(plan.symbol, plan.side, plan.quantity)
        return ExecutionResult(fill=fill, receipt=None)


class IrlGate:
    def __init__(
        self,
        client: IrlClient,
        identity: AgentIdentity,
        *,
        venue_id: str,
        notional_currency: str,
        heartbeat_source: HeartbeatSource | None = None,
        use_regime_ref: bool = False,
    ):
        self._client = client
        self._identity = identity
        self._venue_id = venue_id
        self._notional_currency = notional_currency
        self._heartbeat_source = heartbeat_source
        # Layer 2 v2: bind to IRL's own verified regime ref instead of a
        # MacroPulse-signed heartbeat (no 200 ms window, no second credential).
        self._use_regime_ref = use_regime_ref

    async def execute(self, broker: Broker, plan: OrderPlan, *, price: float) -> ExecutionResult:
        client_order_id = _CLIENT_ORDER_ID_PREFIX + uuid.uuid4().hex
        is_buy = plan.side is OrderSide.BUY
        try:
            # Fetched right before authorize: IRL rejects heartbeats older
            # than its drift window (200 ms by default).
            mta_ref = await self._client.get_regime() if self._use_regime_ref else None
            heartbeat = (
                await self._heartbeat_source.fetch()
                if self._heartbeat_source and not self._use_regime_ref
                else None
            )
            auth = await self._client.authorize(
                self._identity,
                is_buy=is_buy,
                quantity=plan.quantity,
                asset=plan.symbol,
                notional=plan.quantity * price,
                notional_currency=self._notional_currency,
                venue_id=self._venue_id,
                client_order_id=client_order_id,
                heartbeat=heartbeat,
                mta_ref=mta_ref,
            )
        except IrlDenied as exc:
            raise OrderBlocked(f"IRL denied: {exc}", policy_denied=True) from exc
        except IrlError as exc:
            raise OrderBlocked(f"IRL request failed: {exc}", policy_denied=False) from exc

        if not auth.authorized:
            raise OrderBlocked(
                "IRL did not authorize the intent", policy_denied=True, trace_id=auth.trace_id
            )
        if auth.shadow_blocked:
            logger.warning("IRL shadow mode would have blocked trace %s; proceeding", auth.trace_id)

        try:
            fill = await broker.place_order(
                plan.symbol, plan.side, plan.quantity, client_order_id=client_order_id
            )
        except Exception:
            await self._bind(auth.trace_id, exchange_tx_id=client_order_id, status="Rejected")
            raise

        final_proof, verification_status, bind_error = await self._bind(
            auth.trace_id,
            exchange_tx_id=fill.order_id or client_order_id,
            status="Filled",
            fill=fill,
        )
        return ExecutionResult(
            fill=fill,
            receipt=IrlReceipt(
                trace_id=auth.trace_id,
                reasoning_hash=auth.reasoning_hash,
                shadow_blocked=auth.shadow_blocked,
                final_proof=final_proof,
                verification_status=verification_status,
                bind_error=bind_error,
            ),
        )

    async def _bind(
        self, trace_id: str, *, exchange_tx_id: str, status: str, fill: Fill | None = None
    ) -> tuple[str | None, str | None, str | None]:
        """Returns (final_proof, verification_status, bind_error); never raises."""
        try:
            result = await self._client.bind(
                trace_id,
                exchange_tx_id=exchange_tx_id,
                execution_status=status,
                execution_price=fill.price if fill else None,
                executed_quantity=fill.quantity if fill else None,
                executed_side=_irl_side(fill.side) if fill else None,
                asset=fill.symbol if fill else None,
                execution_time_ms=int(time.time() * 1000),
            )
        except IrlError as exc:
            logger.error(
                "IRL bind failed for trace %s (exchange tx %s); reconcile via /irl/pending: %s",
                trace_id,
                exchange_tx_id,
                exc,
            )
            return None, None, str(exc)
        if result.verification_status not in ("Matched", "MATCHED"):
            logger.warning(
                "IRL trace %s bound as %s: %s",
                trace_id,
                result.verification_status,
                result.divergence_reason,
            )
        return result.final_proof, result.verification_status, None


def _irl_side(side: OrderSide) -> str:
    return "Long" if side is OrderSide.BUY else "Short"
