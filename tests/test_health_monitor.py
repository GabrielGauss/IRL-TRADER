from __future__ import annotations

from trading_bot.runtime.health import HealthMonitor


def test_fresh_monitor_is_healthy_with_no_signals_processed():
    monitor = HealthMonitor()
    status = monitor.status()

    assert status.healthy is True
    assert status.signals_processed == 0
    assert status.last_signal_at is None
    assert status.last_error is None
    assert status.kill_switch_tripped is False
    assert status.uptime_seconds >= 0.0


def test_record_signal_processed_increments_count_and_sets_timestamp():
    monitor = HealthMonitor()
    monitor.record_signal_processed()
    monitor.record_signal_processed()
    status = monitor.status()

    assert status.signals_processed == 2
    assert status.last_signal_at is not None


def test_record_error_is_visible_but_does_not_flip_healthy():
    monitor = HealthMonitor()
    monitor.record_error("broker timeout")
    status = monitor.status()

    assert status.last_error == "broker timeout"
    assert status.healthy is True


def test_record_kill_switch_tripped_flips_healthy_to_false():
    monitor = HealthMonitor()
    monitor.record_kill_switch_tripped()
    status = monitor.status()

    assert status.kill_switch_tripped is True
    assert status.healthy is False
