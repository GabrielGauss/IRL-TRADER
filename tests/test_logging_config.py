from __future__ import annotations

import json
import logging

import pytest

from trading_bot.runtime.logging_config import (
    AUDIT_LOGGER_NAME,
    JsonLogFormatter,
    configure_logging,
    get_audit_logger,
)


@pytest.fixture
def restore_root_logger():
    root = logging.getLogger()
    original_handlers = list(root.handlers)
    original_level = root.level
    yield
    root.handlers = original_handlers
    root.setLevel(original_level)


def test_json_log_formatter_produces_valid_json_with_core_fields():
    formatter = JsonLogFormatter()
    record = logging.LogRecord(
        "my.logger", logging.INFO, __file__, 10, "hello %s", ("world",), None
    )

    parsed = json.loads(formatter.format(record))

    assert parsed["level"] == "INFO"
    assert parsed["logger"] == "my.logger"
    assert parsed["message"] == "hello world"
    assert "timestamp" in parsed


def test_json_log_formatter_includes_extra_fields():
    formatter = JsonLogFormatter()
    record = logging.LogRecord("my.logger", logging.INFO, __file__, 10, "fill executed", None, None)
    record.order_id = "123"
    record.symbol = "BTC/USDT"

    parsed = json.loads(formatter.format(record))

    assert parsed["order_id"] == "123"
    assert parsed["symbol"] == "BTC/USDT"


def test_json_log_formatter_includes_exception_info():
    formatter = JsonLogFormatter()
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        record = logging.LogRecord(
            "my.logger", logging.ERROR, __file__, 10, "failed", None, sys.exc_info()
        )
    parsed = json.loads(formatter.format(record))
    assert "boom" in parsed["exc_info"]


def test_configure_logging_installs_json_formatter_by_default(restore_root_logger):
    configure_logging()
    root = logging.getLogger()

    assert len(root.handlers) == 1
    assert isinstance(root.handlers[0].formatter, JsonLogFormatter)


def test_configure_logging_can_use_plain_text_formatter(restore_root_logger):
    configure_logging(json_output=False)
    root = logging.getLogger()

    assert not isinstance(root.handlers[0].formatter, JsonLogFormatter)


def test_configure_logging_sets_root_level(restore_root_logger):
    configure_logging(level=logging.WARNING)
    assert logging.getLogger().level == logging.WARNING


def test_get_audit_logger_writes_json_lines_to_file(tmp_path):
    log_file = tmp_path / "audit.log"
    logger = get_audit_logger(str(log_file))

    logger.info("fill executed", extra={"order_id": "abc", "quantity": 1.0})
    for handler in logger.handlers:
        handler.flush()

    lines = [line for line in log_file.read_text().splitlines() if line.strip()]
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed["order_id"] == "abc"
    assert parsed["message"] == "fill executed"


def test_get_audit_logger_does_not_propagate_to_root(tmp_path):
    logger = get_audit_logger(str(tmp_path / "audit.log"))
    assert logger.propagate is False
    assert logger.name == AUDIT_LOGGER_NAME


def test_get_audit_logger_reconfigures_cleanly_on_repeated_calls(tmp_path):
    first_file = tmp_path / "first.log"
    second_file = tmp_path / "second.log"

    get_audit_logger(str(first_file))
    logger = get_audit_logger(str(second_file))
    logger.info("second file entry")
    for handler in logger.handlers:
        handler.flush()

    assert len(logger.handlers) == 1
    assert "second file entry" in second_file.read_text()
