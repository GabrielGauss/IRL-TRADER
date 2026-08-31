from __future__ import annotations

import pytest

from trading_bot.risk.drawdown import DrawdownMonitor


def test_first_update_establishes_the_peak_with_zero_drawdown():
    monitor = DrawdownMonitor(max_drawdown_pct=10.0)
    status = monitor.update(1000.0)

    assert status.peak_equity == pytest.approx(1000.0)
    assert status.drawdown_pct == pytest.approx(0.0)
    assert status.breached is False


def test_new_high_raises_the_peak():
    monitor = DrawdownMonitor(max_drawdown_pct=10.0)
    monitor.update(1000.0)
    status = monitor.update(1100.0)

    assert status.peak_equity == pytest.approx(1100.0)
    assert status.drawdown_pct == pytest.approx(0.0)


def test_drawdown_pct_computed_against_peak_not_previous_reading():
    monitor = DrawdownMonitor(max_drawdown_pct=50.0)
    monitor.update(1000.0)
    monitor.update(1100.0)
    status = monitor.update(990.0)

    assert status.peak_equity == pytest.approx(1100.0)
    assert status.drawdown_pct == pytest.approx(10.0)
    assert status.breached is False


def test_breached_is_true_once_drawdown_reaches_the_limit():
    monitor = DrawdownMonitor(max_drawdown_pct=10.0)
    monitor.update(1000.0)
    status = monitor.update(900.0)

    assert status.drawdown_pct == pytest.approx(10.0)
    assert status.breached is True


def test_reset_clears_the_peak_so_the_next_update_establishes_a_new_one():
    monitor = DrawdownMonitor(max_drawdown_pct=10.0)
    monitor.update(1000.0)
    monitor.update(500.0)  # breached

    monitor.reset()
    status = monitor.update(600.0)

    assert status.peak_equity == pytest.approx(600.0)
    assert status.breached is False


def test_reset_can_seed_an_explicit_peak():
    monitor = DrawdownMonitor(max_drawdown_pct=10.0)
    monitor.reset(equity=2000.0)
    status = monitor.update(1900.0)

    assert status.peak_equity == pytest.approx(2000.0)
    assert status.drawdown_pct == pytest.approx(5.0)


def test_rejects_non_positive_max_drawdown_pct():
    with pytest.raises(ValueError, match="must be positive"):
        DrawdownMonitor(max_drawdown_pct=0.0)
