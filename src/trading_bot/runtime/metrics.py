"""Performance metrics for the running controller: Sharpe ratio plus a
snapshot combining PortfolioState, the current DrawdownStatus, and trade
count -- the numbers a health/metrics endpoint or CLI status check reports.
"""

from __future__ import annotations

import itertools
import math
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from trading_bot.risk.drawdown import DrawdownStatus
from trading_bot.risk.portfolio import PortfolioState


def compute_returns_from_equity_curve(equity_curve: Sequence[float]) -> list[float]:
    """Period-over-period fractional returns. Points where the prior equity
    reading is zero are skipped (a return relative to zero is undefined)."""
    if len(equity_curve) < 2:
        return []
    returns = []
    for previous, current in itertools.pairwise(equity_curve):
        if previous == 0:
            continue
        returns.append((current - previous) / previous)
    return returns


def compute_sharpe_ratio(
    returns: Sequence[float],
    *,
    risk_free_rate: float = 0.0,
    periods_per_year: float = 365.0,
) -> float:
    """Annualized Sharpe ratio from a series of periodic returns. Needs at
    least two returns (sample stdev is undefined otherwise) and non-zero
    variance; both edge cases return 0.0 rather than raising, since "not
    enough data yet" is a normal, expected state for a freshly started
    controller, not an error."""
    if len(returns) < 2:
        return 0.0
    excess_returns = [r - risk_free_rate for r in returns]
    stdev = statistics.stdev(excess_returns)
    if stdev == 0:
        return 0.0
    return statistics.mean(excess_returns) / stdev * math.sqrt(periods_per_year)


@dataclass(frozen=True)
class PerformanceSnapshot:
    equity: float
    cash: float
    realized_pnl: float
    unrealized_pnl: float
    drawdown_pct: float
    sharpe_ratio: float
    num_trades: int


def build_performance_snapshot(
    *,
    portfolio: PortfolioState,
    mark_prices: Mapping[str, float],
    drawdown_status: DrawdownStatus,
    equity_curve: Sequence[float],
    num_trades: int,
) -> PerformanceSnapshot:
    returns = compute_returns_from_equity_curve(equity_curve)
    return PerformanceSnapshot(
        equity=portfolio.equity(mark_prices),
        cash=portfolio.cash,
        realized_pnl=portfolio.realized_pnl,
        unrealized_pnl=portfolio.unrealized_pnl(mark_prices),
        drawdown_pct=drawdown_status.drawdown_pct,
        sharpe_ratio=compute_sharpe_ratio(returns),
        num_trades=num_trades,
    )
