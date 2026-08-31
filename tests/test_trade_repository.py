from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from trading_bot.persistence.repository import TradeRecord, TradeRepository


@pytest.fixture
def repository(tmp_path) -> TradeRepository:
    return TradeRepository(str(tmp_path / "test.db"))


def _trade(
    order_id: str = "1", symbol: str = "BTCUSDT", executed_at: datetime | None = None
) -> TradeRecord:
    return TradeRecord(
        order_id=order_id,
        symbol=symbol,
        side="BUY",
        quantity=0.01,
        price=50000.0,
        status="FILLED",
        executed_at=executed_at or datetime(2024, 1, 1, tzinfo=UTC),
    )


def test_save_and_get_trades_round_trips(repository):
    # Arrange
    trade = _trade()

    # Act
    repository.save_trade(trade)
    trades = repository.get_trades()

    # Assert
    assert trades == [trade]


def test_get_trades_filters_by_symbol(repository):
    # Arrange
    repository.save_trade(_trade(order_id="1", symbol="BTCUSDT"))
    repository.save_trade(_trade(order_id="2", symbol="ETHUSDT"))

    # Act
    btc_trades = repository.get_trades(symbol="BTCUSDT")

    # Assert
    assert [t.order_id for t in btc_trades] == ["1"]


def test_get_trades_orders_by_executed_at_ascending(repository):
    # Arrange
    later = datetime(2024, 1, 2, tzinfo=UTC)
    earlier = datetime(2024, 1, 1, tzinfo=UTC)
    repository.save_trade(_trade(order_id="later", executed_at=later))
    repository.save_trade(_trade(order_id="earlier", executed_at=earlier))

    # Act
    trades = repository.get_trades()

    # Assert
    assert [t.order_id for t in trades] == ["earlier", "later"]


def test_save_trade_upserts_on_duplicate_order_id(repository):
    # Arrange
    repository.save_trade(_trade(order_id="1", symbol="BTCUSDT"))

    # Act: same order_id, different symbol -> should replace, not duplicate
    repository.save_trade(_trade(order_id="1", symbol="ETHUSDT"))
    trades = repository.get_trades()

    # Assert
    assert len(trades) == 1
    assert trades[0].symbol == "ETHUSDT"


def test_get_trades_returns_empty_list_when_no_trades_saved(repository):
    assert repository.get_trades() == []


def test_record_and_query_equity_since(repository):
    # Arrange
    now = datetime.now(UTC)
    two_days_ago = now - timedelta(days=2)
    one_hour_ago = now - timedelta(hours=1)
    repository.record_equity(900.0, recorded_at=two_days_ago)
    repository.record_equity(1000.0, recorded_at=one_hour_ago)

    # Act
    recent = repository.get_equity_since(now - timedelta(days=1))

    # Assert
    assert len(recent) == 1
    assert recent[0].equity == pytest.approx(1000.0)


def test_get_equity_since_returns_snapshots_in_chronological_order(repository):
    # Arrange
    base = datetime(2024, 1, 1, tzinfo=UTC)
    repository.record_equity(1100.0, recorded_at=base + timedelta(hours=2))
    repository.record_equity(1000.0, recorded_at=base)
    repository.record_equity(1050.0, recorded_at=base + timedelta(hours=1))

    # Act
    snapshots = repository.get_equity_since(base)

    # Assert
    assert [s.equity for s in snapshots] == [1000.0, 1050.0, 1100.0]


def test_record_equity_defaults_recorded_at_to_now(repository):
    # Act
    before = datetime.now(UTC)
    repository.record_equity(500.0)
    after = datetime.now(UTC)

    # Assert
    snapshots = repository.get_equity_since(before - timedelta(seconds=1))
    assert len(snapshots) == 1
    assert before <= snapshots[0].recorded_at <= after


def test_repository_is_reusable_across_instances_pointed_at_same_file(tmp_path):
    # Arrange
    db_path = str(tmp_path / "shared.db")
    first = TradeRepository(db_path)
    first.save_trade(_trade())

    # Act: a fresh instance against the same file should see prior data
    second = TradeRepository(db_path)
    trades = second.get_trades()

    # Assert
    assert len(trades) == 1
