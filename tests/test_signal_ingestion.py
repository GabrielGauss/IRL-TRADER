from __future__ import annotations

import asyncio

import pytest

from trading_bot.signals.ingestion import SignalIngestionError, SignalIngestor
from trading_bot.signals.queue import SignalQueue


def _valid_raw(symbol: str = "BTCUSDT") -> dict:
    return {"source": "test", "symbol": symbol, "action": "BUY"}


def test_ingestor_validates_and_enqueues_valid_payloads():
    raws = [_valid_raw("BTCUSDT"), _valid_raw("ETHUSDT")]

    async def fetch() -> dict:
        return raws.pop(0)

    async def scenario() -> tuple[str, str]:
        queue = SignalQueue()
        ingestor = SignalIngestor(fetch, queue)
        await ingestor.run(stop_after=2)
        first = await queue.get()
        second = await queue.get()
        return first.symbol, second.symbol

    assert asyncio.run(scenario()) == ("BTCUSDT", "ETHUSDT")


def test_ingestor_routes_invalid_payloads_to_dead_letter_callback_and_continues():
    raws = [{"source": "x", "symbol": "", "action": "BUY"}, _valid_raw("ETHUSDT")]
    dead_letters: list[dict] = []

    async def fetch() -> dict:
        return raws.pop(0)

    async def scenario() -> int:
        queue = SignalQueue()
        ingestor = SignalIngestor(
            fetch, queue, on_invalid_payload=lambda raw, exc: dead_letters.append(raw)
        )
        await ingestor.run(stop_after=2)
        return queue.qsize()

    remaining = asyncio.run(scenario())
    assert len(dead_letters) == 1
    assert remaining == 1


def test_ingestor_backs_off_exponentially_and_retries_on_transient_fetch_errors(monkeypatch):
    attempts = {"n": 0}
    sleep_calls: list[float] = []

    async def fetch() -> dict:
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise ConnectionError("network drop")
        return _valid_raw()

    async def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    monkeypatch.setattr("trading_bot.signals.ingestion.asyncio.sleep", fake_sleep)

    async def scenario() -> None:
        queue = SignalQueue()
        ingestor = SignalIngestor(fetch, queue, base_backoff_seconds=0.01, max_backoff_seconds=1.0)
        await ingestor.run(stop_after=1)

    asyncio.run(scenario())

    assert attempts["n"] == 3
    assert len(sleep_calls) == 2
    assert sleep_calls[1] > sleep_calls[0]


def test_ingestor_caps_backoff_at_max_backoff_seconds(monkeypatch):
    sleep_calls: list[float] = []

    async def always_fails() -> dict:
        raise ConnectionError("down")

    async def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)
        if len(sleep_calls) >= 4:
            raise KeyboardInterrupt

    monkeypatch.setattr("trading_bot.signals.ingestion.asyncio.sleep", fake_sleep)

    async def scenario() -> None:
        queue = SignalQueue()
        ingestor = SignalIngestor(
            always_fails,
            queue,
            max_consecutive_errors=100,
            base_backoff_seconds=1.0,
            max_backoff_seconds=3.0,
        )
        await ingestor.run()

    with pytest.raises(KeyboardInterrupt):
        asyncio.run(scenario())

    assert sleep_calls[-1] <= 3.0
    assert max(sleep_calls) <= 3.0


def test_ingestor_raises_after_exceeding_max_consecutive_errors(monkeypatch):
    async def always_fails() -> dict:
        raise ConnectionError("down")

    async def fake_sleep(seconds: float) -> None:
        return None

    monkeypatch.setattr("trading_bot.signals.ingestion.asyncio.sleep", fake_sleep)

    async def scenario() -> None:
        queue = SignalQueue()
        ingestor = SignalIngestor(
            always_fails, queue, max_consecutive_errors=2, base_backoff_seconds=0.01
        )
        await ingestor.run()

    with pytest.raises(SignalIngestionError):
        asyncio.run(scenario())


def test_ingestor_resets_error_count_after_a_successful_fetch(monkeypatch):
    # Alternating error/success: with max_consecutive_errors=1 this would raise if
    # (and only if) the error counter failed to reset after each success.
    calls = {"n": 0}

    async def alternating() -> dict:
        calls["n"] += 1
        if calls["n"] % 2 == 1:
            raise ConnectionError("down")
        return _valid_raw()

    async def fake_sleep(seconds: float) -> None:
        return None

    monkeypatch.setattr("trading_bot.signals.ingestion.asyncio.sleep", fake_sleep)

    async def scenario() -> None:
        queue = SignalQueue()
        ingestor = SignalIngestor(
            alternating, queue, max_consecutive_errors=1, base_backoff_seconds=0.01
        )
        await ingestor.run(stop_after=2)

    asyncio.run(scenario())  # must not raise
    assert calls["n"] == 4
