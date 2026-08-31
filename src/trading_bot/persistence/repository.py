"""SQLite-backed storage for executed trades and equity snapshots.

Uses the stdlib sqlite3 module directly rather than an ORM: the schema is
small and stable, and a repository class keeps the SQL in one place so the
rest of the app never touches it directly. Equity snapshots exist so the
execution engine can evaluate daily-loss and drawdown risk limits from
persisted history instead of in-memory state that wouldn't survive a
restart.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True)
class TradeRecord:
    order_id: str
    symbol: str
    side: str
    quantity: float
    price: float
    status: str
    executed_at: datetime


@dataclass(frozen=True)
class EquitySnapshot:
    equity: float
    recorded_at: datetime


class TradeRepository:
    def __init__(self, db_path: str):
        self._db_path = db_path
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    order_id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    quantity REAL NOT NULL,
                    price REAL NOT NULL,
                    status TEXT NOT NULL,
                    executed_at TEXT NOT NULL
                )
                """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS equity_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    equity REAL NOT NULL,
                    recorded_at TEXT NOT NULL
                )
                """)

    def save_trade(self, trade: TradeRecord) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute(
                """
                INSERT INTO trades (order_id, symbol, side, quantity, price, status, executed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(order_id) DO UPDATE SET
                    symbol = excluded.symbol,
                    side = excluded.side,
                    quantity = excluded.quantity,
                    price = excluded.price,
                    status = excluded.status,
                    executed_at = excluded.executed_at
                """,
                (
                    trade.order_id,
                    trade.symbol,
                    trade.side,
                    trade.quantity,
                    trade.price,
                    trade.status,
                    trade.executed_at.isoformat(),
                ),
            )

    def get_trades(self, symbol: str | None = None) -> list[TradeRecord]:
        query = "SELECT * FROM trades"
        params: tuple[str, ...] = ()
        if symbol is not None:
            query += " WHERE symbol = ?"
            params = (symbol,)
        query += " ORDER BY executed_at ASC"

        with closing(self._connect()) as conn:
            rows = conn.execute(query, params).fetchall()
        return [_row_to_trade(row) for row in rows]

    def record_equity(self, equity: float, recorded_at: datetime | None = None) -> None:
        recorded_at = recorded_at or datetime.now(UTC)
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "INSERT INTO equity_snapshots (equity, recorded_at) VALUES (?, ?)",
                (equity, recorded_at.isoformat()),
            )

    def get_equity_since(self, since: datetime) -> list[EquitySnapshot]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """
                SELECT equity, recorded_at FROM equity_snapshots
                WHERE recorded_at >= ?
                ORDER BY recorded_at ASC
                """,
                (since.isoformat(),),
            ).fetchall()
        return [
            EquitySnapshot(
                equity=row["equity"], recorded_at=datetime.fromisoformat(row["recorded_at"])
            )
            for row in rows
        ]


def _row_to_trade(row: sqlite3.Row) -> TradeRecord:
    return TradeRecord(
        order_id=row["order_id"],
        symbol=row["symbol"],
        side=row["side"],
        quantity=row["quantity"],
        price=row["price"],
        status=row["status"],
        executed_at=datetime.fromisoformat(row["executed_at"]),
    )
