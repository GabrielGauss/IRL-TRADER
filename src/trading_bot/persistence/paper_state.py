"""Persist the paper account across restarts.

PaperBroker balances and the controller's PortfolioState live in memory, so
every deploy or Docker restart used to reset the simulated account to its
starting cash while the trade DB (and IRL) kept the trade history. Snapshotting
both after each fill keeps them exact -- replaying trades is not enough because
TradeRecord does not store fees.

A corrupt or incomplete file raises instead of silently starting fresh: a
quiet reset is exactly the drift this module exists to prevent.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from trading_bot.risk.portfolio import PortfolioState, Position

STATE_FILENAME = "paper_state.json"


@dataclass(frozen=True)
class PaperSnapshot:
    balances: dict[str, float]
    portfolio: PortfolioState


def save_paper_state(
    path: str | os.PathLike[str], balances: Mapping[str, float], portfolio: PortfolioState
) -> None:
    payload = {
        "balances": dict(balances),
        "portfolio": {
            "cash": portfolio.cash,
            "realized_pnl": portfolio.realized_pnl,
            "positions": [
                {
                    "symbol": pos.symbol,
                    "quantity": pos.quantity,
                    "average_entry_price": pos.average_entry_price,
                }
                for pos in portfolio.positions.values()
            ],
        },
    }
    target = Path(path)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    os.replace(tmp, target)


def load_paper_state(path: str | os.PathLike[str]) -> PaperSnapshot | None:
    target = Path(path)
    if not target.exists():
        return None
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        balances = {str(k): float(v) for k, v in data["balances"].items()}
        raw = data["portfolio"]
        positions = {
            p["symbol"]: Position(
                symbol=p["symbol"],
                quantity=float(p["quantity"]),
                average_entry_price=float(p["average_entry_price"]),
            )
            for p in raw["positions"]
        }
        portfolio = PortfolioState(
            cash=float(raw["cash"]),
            positions=positions,
            realized_pnl=float(raw["realized_pnl"]),
        )
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise ValueError(
            f"Unreadable {target.name} ({exc!r}); fix or delete it to start a fresh paper account"
        ) from exc
    return PaperSnapshot(balances=balances, portfolio=portfolio)
