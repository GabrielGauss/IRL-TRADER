from __future__ import annotations

import pytest

from trading_bot.risk.target_position import signal_to_target_position
from trading_bot.signals.schema import SignalPayload


def _signal(action: str, symbol: str = "BTCUSDT") -> SignalPayload:
    return SignalPayload(source="x", symbol=symbol, action=action)


def test_buy_signal_while_flat_sizes_a_new_target_position():
    target = signal_to_target_position(
        _signal("BUY"),
        account_balance_quote=1000.0,
        price=100.0,
        current_quantity=0.0,
        position_size_fraction=0.1,
    )
    assert target.symbol == "BTCUSDT"
    assert target.target_quantity == pytest.approx(1.0)


def test_buy_signal_while_already_in_position_holds_current_quantity():
    target = signal_to_target_position(
        _signal("BUY"),
        account_balance_quote=1000.0,
        price=100.0,
        current_quantity=0.5,
        position_size_fraction=0.1,
    )
    assert target.target_quantity == pytest.approx(0.5)


@pytest.mark.parametrize("action", ["SELL", "CLOSE"])
def test_sell_or_close_signal_targets_flat(action):
    target = signal_to_target_position(
        _signal(action),
        account_balance_quote=1000.0,
        price=100.0,
        current_quantity=0.5,
        position_size_fraction=0.1,
    )
    assert target.target_quantity == 0.0


def test_hold_signal_leaves_current_quantity_unchanged():
    target = signal_to_target_position(
        _signal("HOLD"),
        account_balance_quote=1000.0,
        price=100.0,
        current_quantity=0.3,
        position_size_fraction=0.1,
    )
    assert target.target_quantity == pytest.approx(0.3)


def test_target_position_symbol_matches_signal_symbol():
    target = signal_to_target_position(
        _signal("HOLD", symbol="ETHUSDT"),
        account_balance_quote=1000.0,
        price=100.0,
        current_quantity=0.0,
        position_size_fraction=0.1,
    )
    assert target.symbol == "ETHUSDT"
