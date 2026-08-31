"""Priority-ordered async queue for validated SignalPayloads.

Lower `priority` values are served first (0 = highest), matching
asyncio.PriorityQueue's min-heap semantics. A monotonic tie-breaking counter
lets equal-priority signals fall back to FIFO order without requiring
SignalPayload itself to be orderable.
"""

from __future__ import annotations

import asyncio
import itertools

from trading_bot.signals.schema import SignalPayload

_QueueItem = tuple[int, int, SignalPayload]


class SignalQueue:
    def __init__(self, maxsize: int = 0):
        self._queue: asyncio.PriorityQueue[_QueueItem] = asyncio.PriorityQueue(maxsize=maxsize)
        self._counter = itertools.count()

    async def put(self, signal: SignalPayload) -> None:
        await self._queue.put((signal.priority, next(self._counter), signal))

    async def get(self) -> SignalPayload:
        _, _, signal = await self._queue.get()
        return signal

    def task_done(self) -> None:
        self._queue.task_done()

    def qsize(self) -> int:
        return self._queue.qsize()

    def empty(self) -> bool:
        return self._queue.empty()
