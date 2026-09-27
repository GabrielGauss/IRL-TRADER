from __future__ import annotations

from datetime import datetime

import pytest
from pydantic import ValidationError

from trading_bot.signals.schema import SignalAction, SignalPayload


def test_signal_payload_accepts_minimal_valid_payload():
    signal = SignalPayload(source="tradingview", symbol="btcusdt", action="BUY")

    assert signal.symbol == "BTCUSDT"
    assert signal.action == SignalAction.BUY
    assert signal.priority == 5
    assert signal.received_at.tzinfo is not None


def test_signal_payload_normalizes_symbol_case_and_whitespace():
    signal = SignalPayload(source="x", symbol=" ethusdt ", action="SELL")
    assert signal.symbol == "ETHUSDT"


@pytest.mark.parametrize("confidence", [-0.1, 1.1])
def test_signal_payload_rejects_confidence_out_of_range(confidence):
    with pytest.raises(ValidationError):
        SignalPayload(source="x", symbol="BTCUSDT", action="BUY", confidence=confidence)


def test_signal_payload_rejects_unknown_action():
    with pytest.raises(ValidationError):
        SignalPayload(source="x", symbol="BTCUSDT", action="YOLO")


def test_signal_payload_rejects_empty_symbol():
    with pytest.raises(ValidationError):
        SignalPayload(source="x", symbol="", action="BUY")


def test_signal_payload_rejects_timezone_naive_received_at():
    with pytest.raises(ValidationError):
        SignalPayload(
            source="x",
            symbol="BTCUSDT",
            action="BUY",
            received_at=datetime(2024, 1, 1),  # noqa: DTZ001
        )


def test_signal_payload_accepts_arbitrary_metadata():
    signal = SignalPayload(
        source="x", symbol="BTCUSDT", action="BUY", metadata={"model": "gpt", "score": 0.8}
    )
    assert signal.metadata["model"] == "gpt"


def test_signal_payload_is_immutable():
    signal = SignalPayload(source="x", symbol="BTCUSDT", action="BUY")
    with pytest.raises(ValidationError):
        signal.symbol = "ETHUSDT"


def test_signal_payload_from_raw_dict_via_model_validate():
    raw = {"source": "webhook", "symbol": "BTCUSDT", "action": "SELL", "priority": 1}
    signal = SignalPayload.model_validate(raw)
    assert signal.priority == 1
    assert signal.action == SignalAction.SELL


@pytest.mark.parametrize("priority", [-1, 10])
def test_signal_payload_rejects_priority_out_of_range(priority):
    with pytest.raises(ValidationError):
        SignalPayload(source="x", symbol="BTCUSDT", action="BUY", priority=priority)


@pytest.mark.parametrize("raw_action", ["buy", "Sell", " close ", "hold\n"])
def test_signal_payload_normalizes_action_case_and_whitespace(raw_action):
    # TradingView's {{strategy.order.action}} placeholder renders lowercase "buy"/"sell".
    signal = SignalPayload(source="tradingview", symbol="BTCUSDT", action=raw_action)

    assert signal.action is SignalAction(raw_action.strip().upper())


def test_signal_payload_still_rejects_unknown_action():
    with pytest.raises(ValidationError):
        SignalPayload(source="tradingview", symbol="BTCUSDT", action="long")
