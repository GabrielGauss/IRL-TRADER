from __future__ import annotations

import asyncio

from trading_bot.signals.queue import SignalQueue
from trading_bot.signals.schema import SignalPayload


def _signal(symbol: str = "BTCUSDT", priority: int = 5, action: str = "BUY") -> SignalPayload:
    return SignalPayload(source="x", symbol=symbol, action=action, priority=priority)


def test_signal_queue_serves_lower_priority_number_first():
    async def scenario() -> tuple[int, int, int]:
        queue = SignalQueue()
        await queue.put(_signal(priority=5))
        await queue.put(_signal(priority=1))
        await queue.put(_signal(priority=3))
        first = await queue.get()
        second = await queue.get()
        third = await queue.get()
        return first.priority, second.priority, third.priority

    assert asyncio.run(scenario()) == (1, 3, 5)


def test_signal_queue_preserves_fifo_order_for_equal_priority():
    async def scenario() -> tuple[str, str]:
        queue = SignalQueue()
        await queue.put(_signal(symbol="AAA", priority=5))
        await queue.put(_signal(symbol="BBB", priority=5))
        first = await queue.get()
        second = await queue.get()
        return first.symbol, second.symbol

    assert asyncio.run(scenario()) == ("AAA", "BBB")


def test_signal_queue_qsize_and_empty_reflect_contents():
    async def scenario() -> tuple[int, bool, bool]:
        queue = SignalQueue()
        was_empty = queue.empty()
        await queue.put(_signal())
        return queue.qsize(), queue.empty(), was_empty

    qsize, empty_after, empty_before = asyncio.run(scenario())
    assert qsize == 1
    assert empty_after is False
    assert empty_before is True


def test_signal_queue_task_done_does_not_raise_after_get():
    async def scenario() -> None:
        queue = SignalQueue()
        await queue.put(_signal())
        await queue.get()
        queue.task_done()

    asyncio.run(scenario())
