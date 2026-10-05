"""aiohttp application exposing the real external signal-ingestion point
(POST /signals), plus GET /health and GET /metrics for the running
controller -- this is what makes signal ingestion actually "accept dynamic,
external, JSON-based payload streams" rather than only being reachable from
tests.

POST /signals does not validate against SignalPayload itself; it only checks
the body is JSON and an object, then relays it onto the raw queue. Schema
validation stays SignalIngestor's job (Stage 1) so there's exactly one place
that knows the signal schema -- this HTTP layer is a thin, fast relay, not a
second validator that could drift from the first.

When a signal secret is configured, POST /signals requires it either in the
X-Signal-Secret header or as a top-level "passphrase" field in the JSON body.
The body form exists because TradingView alerts can't set custom headers. If
the header is present it is authoritative. The passphrase field is always
stripped before queueing so the secret never reaches logs or the audit trail.
"""

from __future__ import annotations

import asyncio
import hmac
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiohttp import web

from trading_bot.runtime.health import HealthMonitor
from trading_bot.runtime.metrics import PerformanceSnapshot

logger = logging.getLogger(__name__)

# Async because a real snapshot needs a live price for unrealized PnL.
MetricsProvider = Callable[[], Awaitable[PerformanceSnapshot]]

SECRET_HEADER = "X-Signal-Secret"
PASSPHRASE_FIELD = "passphrase"


def _secret_matches(candidate: object, secret: str) -> bool:
    if not isinstance(candidate, str):
        return False
    return hmac.compare_digest(candidate.encode(), secret.encode())


def _is_authorized(request: web.Request, payload: Any, secret: str) -> bool:
    header_value = request.headers.get(SECRET_HEADER)
    if header_value is not None:
        return _secret_matches(header_value, secret)
    if isinstance(payload, dict):
        return _secret_matches(payload.get(PASSPHRASE_FIELD), secret)
    return False


def create_app(
    raw_signal_queue: asyncio.Queue[dict[str, Any]],
    health: HealthMonitor,
    metrics_provider: MetricsProvider,
    signal_secret: str | None = None,
) -> web.Application:
    app = web.Application()

    async def post_signals(request: web.Request) -> web.Response:
        try:
            payload = await request.json()
        except ValueError:
            return web.json_response({"error": "invalid JSON body"}, status=400)
        if signal_secret is not None and not _is_authorized(request, payload, signal_secret):
            logger.warning("Rejected unauthenticated signal from %s", request.remote)
            return web.json_response({"error": "unauthorized"}, status=401)
        if not isinstance(payload, dict):
            return web.json_response({"error": "expected a JSON object"}, status=400)

        signal = {k: v for k, v in payload.items() if k != PASSPHRASE_FIELD}
        await raw_signal_queue.put(signal)
        return web.json_response({"status": "accepted"}, status=202)

    async def get_health(request: web.Request) -> web.Response:
        status = health.status()
        return web.json_response(
            {
                "healthy": status.healthy,
                "started_at": status.started_at.isoformat(),
                "uptime_seconds": status.uptime_seconds,
                "signals_processed": status.signals_processed,
                "last_signal_at": (
                    status.last_signal_at.isoformat() if status.last_signal_at else None
                ),
                "last_error": status.last_error,
                "kill_switch_tripped": status.kill_switch_tripped,
            }
        )

    async def get_metrics(request: web.Request) -> web.Response:
        snapshot = await metrics_provider()
        return web.json_response(
            {
                "equity": snapshot.equity,
                "cash": snapshot.cash,
                "realized_pnl": snapshot.realized_pnl,
                "unrealized_pnl": snapshot.unrealized_pnl,
                "drawdown_pct": snapshot.drawdown_pct,
                "sharpe_ratio": snapshot.sharpe_ratio,
                "num_trades": snapshot.num_trades,
            }
        )

    app.router.add_post("/signals", post_signals)
    app.router.add_get("/health", get_health)
    app.router.add_get("/metrics", get_metrics)
    return app
