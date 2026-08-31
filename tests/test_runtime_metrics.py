from __future__ import annotations

import pytest

from trading_bot.risk.drawdown import DrawdownStatus
from trading_bot.risk.portfolio import PortfolioState
from trading_bot.runtime.metrics import (
    build_performance_snapshot,
    compute_returns_from_equity_curve,
    compute_sharpe_ratio,
)


def test_compute_returns_from_equity_curve_gives_pct_changes():
    returns = compute_returns_from_equity_curve([100.0, 110.0, 99.0])
    assert returns == pytest.approx([0.1, -0.1])


def test_compute_returns_from_equity_curve_needs_at_least_two_points():
    assert compute_returns_from_equity_curve([100.0]) == []
    assert compute_returns_from_equity_curve([]) == []


def test_compute_returns_from_equity_curve_skips_zero_denominator():
    returns = compute_returns_from_equity_curve([0.0, 100.0, 110.0])
    assert returns == pytest.approx([0.1])


def test_sharpe_ratio_is_zero_with_fewer_than_two_returns():
    assert compute_sharpe_ratio([]) == 0.0
    assert compute_sharpe_ratio([0.05]) == 0.0


def test_sharpe_ratio_is_zero_when_returns_have_no_variance():
    assert compute_sharpe_ratio([0.01, 0.01, 0.01]) == 0.0


def test_sharpe_ratio_is_positive_for_consistently_positive_excess_returns():
    sharpe = compute_sharpe_ratio([0.02, 0.01, 0.03, 0.015], periods_per_year=365.0)
    assert sharpe > 0.0


def test_sharpe_ratio_is_negative_for_consistently_negative_excess_returns():
    sharpe = compute_sharpe_ratio([-0.02, -0.01, -0.03, -0.015], periods_per_year=365.0)
    assert sharpe < 0.0


def test_sharpe_ratio_scales_with_sqrt_of_periods_per_year():
    returns = [0.02, 0.01, 0.03, -0.005]
    daily = compute_sharpe_ratio(returns, periods_per_year=365.0)
    annual_equivalent = compute_sharpe_ratio(returns, periods_per_year=365.0 * 4)
    assert annual_equivalent == pytest.approx(daily * (4**0.5))


def test_build_performance_snapshot_combines_portfolio_and_drawdown_and_returns():
    portfolio = PortfolioState(cash=500.0).apply_fill("BTC", "BUY", quantity=1.0, price=100.0)
    drawdown_status = DrawdownStatus(
        peak_equity=1000.0, current_equity=900.0, drawdown_pct=10.0, breached=False
    )

    snapshot = build_performance_snapshot(
        portfolio=portfolio,
        mark_prices={"BTC": 120.0},
        drawdown_status=drawdown_status,
        equity_curve=[1000.0, 950.0, 900.0],
        num_trades=3,
    )

    assert snapshot.equity == pytest.approx(400.0 + 120.0)
    assert snapshot.cash == pytest.approx(400.0)
    assert snapshot.unrealized_pnl == pytest.approx(20.0)
    assert snapshot.realized_pnl == pytest.approx(0.0)
    assert snapshot.drawdown_pct == pytest.approx(10.0)
    assert snapshot.num_trades == 3
    assert isinstance(snapshot.sharpe_ratio, float)
