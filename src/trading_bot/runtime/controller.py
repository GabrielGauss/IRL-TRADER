"""Main orchestration loop for the component stack (Stages 1-3): pulls a
validated signal off the queue, turns it into a target position, checks the
kill switch, routes the resulting order through a Broker, and persists the
fill plus an equity snapshot. One reconciled risk model (KillSwitch +
DrawdownMonitor, replacing the daily-loss math duplicated in the legacy
ExecutionEngine) and one persistence path (TradeRepository, shared with the
legacy stack) instead of the disconnected pieces that existed before this
module.

Any exception other than KillSwitchTripped is left to propagate and crash
the process rather than being retried here: SignalIngestor already owns
retry/backoff for *fetching* signals, and blindly retrying a failed
place_order call risks double-executing a real trade. Failing loudly is the
safer default for a system that places real orders.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from trading_bot.execution.broker import Broker, Fill
from trading_bot.execution.router import plan_order
from trading_bot.irl.gate import ExecutionResult, OrderBlocked, OrderGate, PassthroughGate
from trading_bot.persistence.repository import TradeRecord, TradeRepository
from trading_bot.risk.drawdown import DrawdownMonitor, DrawdownStatus
from trading_bot.risk.kill_switch import KillSwitch, KillSwitchTripped
from trading_bot.risk.portfolio import PortfolioState
from trading_bot.risk.target_position import signal_to_target_position
from trading_bot.runtime.health import HealthMonitor
from trading_bot.runtime.logging_config import AUDIT_LOGGER_NAME
from trading_bot.signals.queue import SignalQueue

logger = logging.getLogger(__name__)
audit_logger = logging.getLogger(AUDIT_LOGGER_NAME)


def _symbols_match(signal_symbol: str, configured_symbol: str) -> bool:
    """Signals (Stage 1) carry no enforced symbol format; brokers (Stage 3)
    require ccxt's 'BASE/QUOTE' form. Compare with slashes stripped so
    'BTCUSDT' and 'BTC/USDT' are recognized as the same instrument."""
    return signal_symbol.replace("/", "") == configured_symbol.replace("/", "")


class TradingController:
    def __init__(
        self,
        *,
        broker: Broker,
        queue: SignalQueue,
        repository: TradeRepository,
        kill_switch: KillSwitch,
        drawdown_monitor: DrawdownMonitor,
        health: HealthMonitor,
        symbol: str,
        base_asset: str,
        quote_asset: str,
        position_size_fraction: float,
        initial_cash: float = 0.0,
        gate: OrderGate | None = None,
    ):
        self._broker = broker
        self.queue = queue
        self._repository = repository
        self._kill_switch = kill_switch
        self._drawdown_monitor = drawdown_monitor
        self.health = health
        self._symbol = symbol
        self._base_asset = base_asset
        self._quote_asset = quote_asset
        self._position_size_fraction = position_size_fraction
        self._portfolio = PortfolioState(cash=initial_cash)
        self._last_drawdown_status: DrawdownStatus | None = None
        self._gate: OrderGate = gate or PassthroughGate()

    @property
    def portfolio(self) -> PortfolioState:
        return self._portfolio

    @property
    def last_drawdown_status(self) -> DrawdownStatus | None:
        """The DrawdownStatus from the most recent risk check, for read-only
        reporting (e.g. the /metrics endpoint). Not a live recomputation --
        callers that need one must go through process_next_signal, the only
        path allowed to advance DrawdownMonitor's peak."""
        return self._last_drawdown_status

    async def process_next_signal(self, timeout: float | None = None) -> Fill | None:
        try:
            signal = await asyncio.wait_for(self.queue.get(), timeout=timeout)
        except TimeoutError:
            return None

        if not _symbols_match(signal.symbol, self._symbol):
            logger.info("Ignoring signal for %s (configured for %s)", signal.symbol, self._symbol)
            self.health.record_signal_processed()
            return None

        try:
            price = await self._broker.get_price(self._symbol)
            current_quantity = await self._broker.get_balance(self._base_asset)
            quote_balance = await self._broker.get_balance(self._quote_asset)
            equity = quote_balance + current_quantity * price

            drawdown_status = self._drawdown_monitor.update(equity)
            self._last_drawdown_status = drawdown_status
            daily_loss_pct = await self._compute_daily_loss_pct(equity)
            self._kill_switch.check(
                drawdown_pct=drawdown_status.drawdown_pct, daily_loss_pct=daily_loss_pct
            )

            target = signal_to_target_position(
                signal,
                account_balance_quote=quote_balance,
                price=price,
                current_quantity=current_quantity,
                position_size_fraction=self._position_size_fraction,
            )
            plan = await plan_order(self._broker, target, self._symbol, self._base_asset)
            fill = None
            if plan is not None:
                try:
                    result = await self._gate.execute(self._broker, plan, price=price)
                except OrderBlocked as blocked:
                    self._record_blocked(blocked, signal.source)
                else:
                    fill = result.fill
                    self._record_fill(result, signal.source)
            self._repository.record_equity(equity)
            self.health.record_signal_processed()
            return fill
        except KillSwitchTripped:
            self.health.record_kill_switch_tripped()
            raise
        except Exception as exc:
            self.health.record_error(str(exc))
            raise

    def _record_fill(self, result: ExecutionResult, signal_source: str) -> None:
        fill = result.fill
        self._portfolio = self._portfolio.apply_fill(
            self._base_asset, fill.side.value, fill.quantity, fill.price
        )
        self._repository.save_trade(_fill_to_trade_record(fill))
        receipt = result.receipt
        audit_logger.info(
            "fill executed",
            extra={
                "order_id": fill.order_id,
                "symbol": fill.symbol,
                "side": fill.side.value,
                "quantity": fill.quantity,
                "price": fill.price,
                "fee": fill.fee,
                "status": fill.status,
                "signal_source": signal_source,
                "irl_trace_id": receipt.trace_id if receipt else None,
                "irl_reasoning_hash": receipt.reasoning_hash if receipt else None,
                "irl_final_proof": receipt.final_proof if receipt else None,
                "irl_verification_status": receipt.verification_status if receipt else None,
                "irl_bind_error": receipt.bind_error if receipt else None,
            },
        )

    def _record_blocked(self, blocked: OrderBlocked, signal_source: str) -> None:
        logger.warning("Order blocked by gate: %s", blocked.reason)
        self.health.record_error(blocked.reason)
        audit_logger.info(
            "order blocked",
            extra={
                "reason": blocked.reason,
                "policy_denied": blocked.policy_denied,
                "irl_trace_id": blocked.trace_id,
                "signal_source": signal_source,
                "symbol": self._symbol,
            },
        )

    async def _compute_daily_loss_pct(self, current_equity: float) -> float:
        since = datetime.now(UTC) - timedelta(days=1)
        history = self._repository.get_equity_since(since)
        if not history:
            return 0.0
        day_start_equity = history[0].equity
        if day_start_equity <= 0:
            return 0.0
        return (day_start_equity - current_equity) / day_start_equity * 100.0

    async def run_forever(self, *, stop_event: asyncio.Event, poll_timeout: float = 1.0) -> None:
        """Runs until `stop_event` is set (graceful shutdown) or the kill
        switch trips (halts trading but returns normally, so a caller's
        health/metrics server can keep reporting the tripped state)."""
        while not stop_event.is_set():
            try:
                await self.process_next_signal(timeout=poll_timeout)
            except KillSwitchTripped as exc:
                logger.error("Kill switch tripped, halting trading: %s", exc)
                return


def _fill_to_trade_record(fill: Fill) -> TradeRecord:
    return TradeRecord(
        order_id=fill.order_id,
        symbol=fill.symbol,
        side=fill.side.value,
        quantity=fill.quantity,
        price=fill.price,
        status=fill.status,
        executed_at=datetime.now(UTC),
    )
