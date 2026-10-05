from __future__ import annotations

import asyncio
import json

import pytest

from trading_bot.execution.broker import OrderSide, PaperBroker
from trading_bot.persistence.paper_state import load_paper_state, save_paper_state
from trading_bot.risk.portfolio import PortfolioState, Position


def _portfolio() -> PortfolioState:
    return PortfolioState(
        cash=899.9,
        positions={"BTC": Position(symbol="BTC", quantity=0.00116, average_entry_price=86000.1)},
        realized_pnl=-0.42,
    )


def test_round_trip_preserves_balances_and_portfolio(tmp_path):
    path = tmp_path / "paper_state.json"
    balances = {"USDT": 899.9, "BTC": 0.00116}

    save_paper_state(path, balances, _portfolio())
    snapshot = load_paper_state(path)

    assert snapshot is not None
    assert snapshot.balances == balances
    assert snapshot.portfolio == _portfolio()


def test_load_returns_none_when_no_state_saved(tmp_path):
    assert load_paper_state(tmp_path / "missing.json") is None


def test_load_refuses_corrupt_state_instead_of_resetting(tmp_path):
    path = tmp_path / "paper_state.json"
    path.write_text("{not json")

    with pytest.raises(ValueError, match="paper_state"):
        load_paper_state(path)


def test_load_refuses_state_missing_fields(tmp_path):
    path = tmp_path / "paper_state.json"
    path.write_text(json.dumps({"balances": {"USDT": 1.0}}))

    with pytest.raises(ValueError, match="paper_state"):
        load_paper_state(path)


def test_save_is_atomic_and_leaves_no_temp_file(tmp_path):
    path = tmp_path / "paper_state.json"

    save_paper_state(path, {"USDT": 1.0}, PortfolioState(cash=1.0))

    assert [p.name for p in tmp_path.iterdir()] == ["paper_state.json"]


def test_paper_broker_balances_is_a_copy():
    async def price(symbol: str) -> float:
        return 100.0

    broker = PaperBroker(price, initial_balances={"USDT": 1000.0})
    asyncio.run(broker.place_order("BTC/USDT", OrderSide.BUY, 1.0))

    snapshot = broker.balances
    snapshot["USDT"] = -1.0

    assert broker.balances["USDT"] == pytest.approx(900.0)
    assert broker.balances["BTC"] == pytest.approx(1.0)
