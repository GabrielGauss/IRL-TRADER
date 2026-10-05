from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from trading_bot.execution.broker import Fill, OrderSide
from trading_bot.persistence.repository import TradeRepository
from trading_bot.risk.drawdown import DrawdownMonitor
from trading_bot.risk.kill_switch import KillSwitch, KillSwitchLimits, KillSwitchTripped
from trading_bot.runtime.controller import TradingController
from trading_bot.runtime.health import HealthMonitor
from trading_bot.signals.queue import SignalQueue
from trading_bot.signals.schema import SignalPayload


def _signal(action: str = "BUY", symbol: str = "BTC/USDT") -> SignalPayload:
    return SignalPayload(source="test", symbol=symbol, action=action)


@pytest.fixture
def repository(tmp_path) -> TradeRepository:
    return TradeRepository(str(tmp_path / "test.db"))


def _broker(balances: dict, price: float = 100.0) -> AsyncMock:
    broker = AsyncMock()
    broker.get_balance.side_effect = lambda asset: balances.get(asset, 0.0)
    broker.get_price.return_value = price
    return broker


def _controller(broker, repository, **overrides) -> TradingController:
    defaults = {
        "broker": broker,
        "queue": SignalQueue(),
        "repository": repository,
        "kill_switch": KillSwitch(KillSwitchLimits(max_drawdown_pct=10.0, max_daily_loss_pct=3.0)),
        "drawdown_monitor": DrawdownMonitor(max_drawdown_pct=10.0),
        "health": HealthMonitor(),
        "symbol": "BTC/USDT",
        "base_asset": "BTC",
        "quote_asset": "USDT",
        "position_size_fraction": 0.1,
    }
    defaults.update(overrides)
    return TradingController(**defaults)


def test_process_next_signal_returns_none_when_queue_is_empty(repository):
    broker = _broker({})
    controller = _controller(broker, repository)

    fill = asyncio.run(controller.process_next_signal(timeout=0.01))

    assert fill is None
    broker.place_order.assert_not_awaited()


def test_process_next_signal_buys_routes_and_persists_on_buy_signal(repository):
    broker = _broker({"BTC": 0.0, "USDT": 1000.0}, price=100.0)
    broker.place_order.return_value = Fill(
        order_id="1",
        symbol="BTC/USDT",
        side=OrderSide.BUY,
        quantity=1.0,
        price=100.0,
        fee=0.0,
        fee_asset=None,
        status="FILLED",
    )
    controller = _controller(broker, repository)

    async def scenario():
        await controller.queue.put(_signal("BUY"))
        return await controller.process_next_signal(timeout=1.0)

    fill = asyncio.run(scenario())

    assert fill is not None
    assert fill.order_id == "1"
    broker.place_order.assert_awaited_once_with("BTC/USDT", OrderSide.BUY, pytest.approx(1.0))
    assert controller.portfolio.position_quantity("BTC") == pytest.approx(1.0)
    assert len(repository.get_trades()) == 1
    assert repository.get_trades()[0].order_id == "1"


def test_process_next_signal_ignores_signal_for_a_different_symbol(repository):
    broker = _broker({"BTC": 0.0, "USDT": 1000.0}, price=100.0)
    controller = _controller(broker, repository)

    async def scenario():
        await controller.queue.put(_signal("BUY", symbol="ETHUSDT"))
        return await controller.process_next_signal(timeout=1.0)

    fill = asyncio.run(scenario())

    assert fill is None
    broker.place_order.assert_not_awaited()


def test_process_next_signal_treats_native_and_unified_symbol_forms_as_equivalent(repository):
    broker = _broker({"BTC": 0.0, "USDT": 1000.0}, price=100.0)
    broker.place_order.return_value = Fill(
        order_id="1",
        symbol="BTC/USDT",
        side=OrderSide.BUY,
        quantity=1.0,
        price=100.0,
        fee=0.0,
        fee_asset=None,
        status="FILLED",
    )
    controller = _controller(broker, repository)

    async def scenario():
        await controller.queue.put(_signal("BUY", symbol="BTCUSDT"))
        return await controller.process_next_signal(timeout=1.0)

    fill = asyncio.run(scenario())
    assert fill is not None


def test_process_next_signal_records_equity_snapshot_even_on_hold(repository):
    broker = _broker({"BTC": 0.0, "USDT": 1000.0}, price=100.0)
    controller = _controller(broker, repository)

    async def scenario():
        await controller.queue.put(_signal("HOLD"))
        await controller.process_next_signal(timeout=1.0)

    asyncio.run(scenario())

    snapshots = repository.get_equity_since(datetime.now(UTC) - timedelta(minutes=1))
    assert len(snapshots) == 1
    assert snapshots[0].equity == pytest.approx(1000.0)


def test_process_next_signal_raises_and_records_health_when_kill_switch_trips(repository):
    repository.record_equity(1000.0, recorded_at=datetime.now(UTC) - timedelta(hours=1))
    broker = _broker({"BTC": 0.0, "USDT": 900.0}, price=100.0)  # 10% daily loss, limit is 3%
    controller = _controller(broker, repository)

    async def scenario():
        await controller.queue.put(_signal("BUY"))
        await controller.process_next_signal(timeout=1.0)

    with pytest.raises(KillSwitchTripped):
        asyncio.run(scenario())

    broker.place_order.assert_not_awaited()
    assert controller.health.status().kill_switch_tripped is True


def test_process_next_signal_records_health_error_on_unexpected_failure(repository):
    broker = _broker({"BTC": 0.0, "USDT": 1000.0}, price=100.0)
    broker.get_price.side_effect = ConnectionError("network drop")
    controller = _controller(broker, repository)

    async def scenario():
        await controller.queue.put(_signal("BUY"))
        await controller.process_next_signal(timeout=1.0)

    with pytest.raises(ConnectionError):
        asyncio.run(scenario())

    assert controller.health.status().last_error is not None


def test_run_forever_stops_gracefully_when_kill_switch_trips(repository):
    repository.record_equity(1000.0, recorded_at=datetime.now(UTC) - timedelta(hours=1))
    broker = _broker({"BTC": 0.0, "USDT": 900.0}, price=100.0)
    controller = _controller(broker, repository)

    async def scenario():
        await controller.queue.put(_signal("BUY"))
        stop_event = asyncio.Event()
        await controller.run_forever(stop_event=stop_event, poll_timeout=0.05)

    asyncio.run(scenario())  # must return normally, not raise
    assert controller.health.status().kill_switch_tripped is True


def test_process_next_signal_logs_fill_to_the_dedicated_audit_logger(repository):
    # Attaches directly to the audit logger's own handlers rather than relying on
    # root-logger propagation, since another test module may have already called
    # get_audit_logger() and flipped propagate=False on this process-wide singleton.
    import logging as logging_module

    from trading_bot.runtime.logging_config import AUDIT_LOGGER_NAME

    records: list[logging_module.LogRecord] = []

    class _CollectingHandler(logging_module.Handler):
        def emit(self, record: logging_module.LogRecord) -> None:
            records.append(record)

    audit_logger = logging_module.getLogger(AUDIT_LOGGER_NAME)
    original_level = audit_logger.level
    audit_logger.setLevel(logging_module.INFO)
    handler = _CollectingHandler()
    audit_logger.addHandler(handler)

    broker = _broker({"BTC": 0.0, "USDT": 1000.0}, price=100.0)
    broker.place_order.return_value = Fill(
        order_id="1",
        symbol="BTC/USDT",
        side=OrderSide.BUY,
        quantity=1.0,
        price=100.0,
        fee=0.1,
        fee_asset="USDT",
        status="FILLED",
    )
    controller = _controller(broker, repository)

    async def scenario():
        await controller.queue.put(_signal("BUY"))
        await controller.process_next_signal(timeout=1.0)

    try:
        asyncio.run(scenario())
    finally:
        audit_logger.removeHandler(handler)
        audit_logger.setLevel(original_level)

    assert len(records) == 1
    assert records[0].order_id == "1"  # type: ignore[attr-defined]
    assert records[0].symbol == "BTC/USDT"  # type: ignore[attr-defined]


def test_last_drawdown_status_is_none_before_any_signal_is_processed(repository):
    controller = _controller(_broker({}), repository)
    assert controller.last_drawdown_status is None


def test_last_drawdown_status_reflects_the_most_recent_risk_check(repository):
    broker = _broker({"BTC": 0.0, "USDT": 1000.0}, price=100.0)
    controller = _controller(broker, repository)

    async def scenario():
        await controller.queue.put(_signal("HOLD"))
        await controller.process_next_signal(timeout=1.0)

    asyncio.run(scenario())

    status = controller.last_drawdown_status
    assert status is not None
    assert status.current_equity == pytest.approx(1000.0)


def test_run_forever_stops_when_stop_event_is_set(repository):
    broker = _broker({"BTC": 0.0, "USDT": 1000.0}, price=100.0)
    controller = _controller(broker, repository)

    async def scenario():
        stop_event = asyncio.Event()

        async def stop_soon():
            await asyncio.sleep(0.05)
            stop_event.set()

        await asyncio.gather(
            controller.run_forever(stop_event=stop_event, poll_timeout=0.01), stop_soon()
        )

    asyncio.run(scenario())  # must return, not hang
    broker.place_order.assert_not_awaited()


def _collect_audit_records():
    import logging as logging_module

    from trading_bot.runtime.logging_config import AUDIT_LOGGER_NAME

    records: list[logging_module.LogRecord] = []

    class _CollectingHandler(logging_module.Handler):
        def emit(self, record: logging_module.LogRecord) -> None:
            records.append(record)

    audit_logger = logging_module.getLogger(AUDIT_LOGGER_NAME)
    handler = _CollectingHandler()
    original_level = audit_logger.level
    audit_logger.setLevel(logging_module.INFO)
    audit_logger.addHandler(handler)

    def restore():
        audit_logger.removeHandler(handler)
        audit_logger.setLevel(original_level)

    return records, restore


def _buy_fill() -> Fill:
    return Fill(
        order_id="irl-1",
        symbol="BTC/USDT",
        side=OrderSide.BUY,
        quantity=1.0,
        price=100.0,
        fee=0.0,
        fee_asset="USDT",
        status="FILLED",
    )


def test_process_next_signal_routes_orders_through_the_gate_and_audits_receipt(repository):
    from trading_bot.execution.router import OrderPlan
    from trading_bot.irl.gate import ExecutionResult, IrlReceipt

    broker = _broker({"BTC": 0.0, "USDT": 1000.0}, price=100.0)
    gate = AsyncMock()
    receipt = IrlReceipt(
        trace_id="t-1",
        reasoning_hash="r" * 64,
        shadow_blocked=False,
        final_proof="p" * 64,
        verification_status="Matched",
        bind_error=None,
    )
    gate.execute.return_value = ExecutionResult(fill=_buy_fill(), receipt=receipt)
    controller = _controller(broker, repository, gate=gate)
    records, restore = _collect_audit_records()

    async def scenario():
        await controller.queue.put(_signal("BUY"))
        return await controller.process_next_signal(timeout=1.0)

    try:
        fill = asyncio.run(scenario())
    finally:
        restore()

    plan = gate.execute.await_args.args[1]
    assert isinstance(plan, OrderPlan) and plan.side is OrderSide.BUY
    assert gate.execute.await_args.kwargs["price"] == 100.0
    broker.place_order.assert_not_awaited()  # only the gate places orders
    assert fill is not None and fill.order_id == "irl-1"
    assert len(repository.get_trades()) == 1
    fill_record = records[-1]
    assert fill_record.irl_trace_id == "t-1"  # type: ignore[attr-defined]
    assert fill_record.irl_final_proof == "p" * 64  # type: ignore[attr-defined]
    assert fill_record.irl_verification_status == "Matched"  # type: ignore[attr-defined]
    assert fill_record.signal_source == "test"  # type: ignore[attr-defined]


def test_process_next_signal_skips_trade_and_keeps_running_when_gate_blocks(repository):
    from trading_bot.irl.gate import OrderBlocked

    broker = _broker({"BTC": 0.0, "USDT": 1000.0}, price=100.0)
    gate = AsyncMock()
    gate.execute.side_effect = OrderBlocked("IRL denied: NOTIONAL_CAP", policy_denied=True)
    controller = _controller(broker, repository, gate=gate)
    records, restore = _collect_audit_records()

    async def scenario():
        await controller.queue.put(_signal("BUY"))
        return await controller.process_next_signal(timeout=1.0)

    try:
        fill = asyncio.run(scenario())
    finally:
        restore()

    assert fill is None
    assert repository.get_trades() == []
    status = controller.health.status()
    assert status.signals_processed == 1
    assert "NOTIONAL_CAP" in status.last_error
    assert records[-1].getMessage() == "order blocked"
    assert records[-1].policy_denied is True  # type: ignore[attr-defined]


def test_controller_starts_from_restored_portfolio(repository):
    from trading_bot.risk.portfolio import PortfolioState

    restored = PortfolioState(cash=123.0, realized_pnl=4.5)
    controller = _controller(_broker({}), repository, initial_portfolio=restored)

    assert controller.portfolio == restored


def test_after_fill_callback_runs_once_per_fill_only(repository):
    from trading_bot.irl.gate import ExecutionResult

    broker = _broker({"BTC": 0.0, "USDT": 1000.0}, price=100.0)
    gate = AsyncMock()
    gate.execute.return_value = ExecutionResult(fill=_buy_fill(), receipt=None)
    calls: list[float] = []
    controller = _controller(
        broker, repository, gate=gate, after_fill=lambda: calls.append(controller.portfolio.cash)
    )

    async def scenario():
        await controller.queue.put(_signal("BUY"))
        await controller.process_next_signal(timeout=1.0)
        await controller.queue.put(_signal("HOLD"))
        await controller.process_next_signal(timeout=1.0)

    asyncio.run(scenario())

    assert len(calls) == 1
    assert calls[0] == controller.portfolio.cash  # portfolio already includes the fill
