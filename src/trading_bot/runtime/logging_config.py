"""Structured JSON logging plus a dedicated trade-audit-trail logger.

The audit logger is separate from the root logger and never propagates to
it, so trade fills land in their own JSON-lines file regardless of what the
root logger's level or handlers are doing elsewhere -- a durable audit trail
shouldn't depend on nobody having turned down verbosity.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from typing import Any

AUDIT_LOGGER_NAME = "trading_bot.audit"

_RESERVED_LOG_RECORD_KEYS = frozenset(
    logging.LogRecord("", 0, "", 0, "", None, None).__dict__.keys()
)


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        extras = {
            key: value
            for key, value in record.__dict__.items()
            if key not in _RESERVED_LOG_RECORD_KEYS
        }
        payload.update(extras)
        return json.dumps(payload, default=str)


def configure_logging(*, level: int = logging.INFO, json_output: bool = True) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(
        JsonLogFormatter()
        if json_output
        else logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    )
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)


def get_audit_logger(log_file: str) -> logging.Logger:
    """(Re)configure the audit logger to write JSON lines to `log_file`.
    Safe to call repeatedly (e.g. at each CLI startup) -- prior handlers are
    closed and replaced rather than accumulating."""
    logger = logging.getLogger(AUDIT_LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    handler = RotatingFileHandler(log_file, maxBytes=10_000_000, backupCount=5)
    handler.setFormatter(JsonLogFormatter())
    logger.addHandler(handler)
    return logger
