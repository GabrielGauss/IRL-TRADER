from __future__ import annotations

import pytest

from trading_bot.risk.kill_switch import KillSwitch, KillSwitchLimits, KillSwitchTripped


def _switch(**overrides) -> KillSwitch:
    defaults = {"max_drawdown_pct": 10.0, "max_daily_loss_pct": 3.0, "max_consecutive_losses": None}
    defaults.update(overrides)
    return KillSwitch(KillSwitchLimits(**defaults))


def test_check_passes_silently_when_all_metrics_are_within_limits():
    switch = _switch()
    switch.check(drawdown_pct=5.0, daily_loss_pct=1.0)
    assert switch.tripped is False


def test_check_trips_and_raises_on_drawdown_breach():
    switch = _switch()
    with pytest.raises(KillSwitchTripped, match="drawdown"):
        switch.check(drawdown_pct=12.0, daily_loss_pct=0.0)
    assert switch.tripped is True


def test_check_trips_and_raises_on_daily_loss_breach():
    switch = _switch()
    with pytest.raises(KillSwitchTripped, match="daily loss"):
        switch.check(drawdown_pct=0.0, daily_loss_pct=5.0)
    assert switch.tripped is True


def test_check_trips_on_consecutive_losses_when_limit_configured():
    switch = _switch(max_consecutive_losses=3)
    with pytest.raises(KillSwitchTripped, match="consecutive losses"):
        switch.check(drawdown_pct=0.0, daily_loss_pct=0.0, consecutive_losses=3)


def test_consecutive_losses_ignored_when_limit_not_configured():
    switch = _switch(max_consecutive_losses=None)
    switch.check(drawdown_pct=0.0, daily_loss_pct=0.0, consecutive_losses=100)
    assert switch.tripped is False


def test_once_tripped_stays_tripped_on_subsequent_checks_even_if_metrics_recover():
    switch = _switch()
    with pytest.raises(KillSwitchTripped):
        switch.check(drawdown_pct=12.0, daily_loss_pct=0.0)

    with pytest.raises(KillSwitchTripped):
        switch.check(drawdown_pct=0.0, daily_loss_pct=0.0)


def test_reset_clears_the_tripped_state():
    switch = _switch()
    with pytest.raises(KillSwitchTripped):
        switch.check(drawdown_pct=12.0, daily_loss_pct=0.0)

    switch.reset()

    switch.check(drawdown_pct=0.0, daily_loss_pct=0.0)
    assert switch.tripped is False


def test_trip_reason_lists_all_breached_metrics():
    switch = _switch()
    with pytest.raises(KillSwitchTripped, match="drawdown.*daily loss|daily loss.*drawdown"):
        switch.check(drawdown_pct=20.0, daily_loss_pct=10.0)
