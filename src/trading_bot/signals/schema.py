"""Unified schema for inbound trading signals, decoupled from any specific provider.

Uses Pydantic (already a project dependency, see config.py) rather than plain
dataclasses so a malformed provider payload is rejected with a structured
ValidationError the ingestion pipeline can catch and route to a dead-letter
handler, instead of failing deep inside strategy or execution code.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SignalAction(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    CLOSE = "CLOSE"
    HOLD = "HOLD"


class SignalPayload(BaseModel):
    """A single directional signal from an arbitrary upstream source.

    `priority` follows asyncio.PriorityQueue convention: 0 is highest priority,
    9 is lowest. `received_at` must be timezone-aware so ordering and staleness
    checks downstream are never ambiguous about which clock/offset applies.
    """

    model_config = ConfigDict(extra="ignore", frozen=True)

    source: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    action: SignalAction
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    priority: int = Field(default=5, ge=0, le=9)
    strategy_id: str | None = Field(default=None)
    received_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("symbol")
    @classmethod
    def _normalize_symbol(cls, value: str) -> str:
        normalized = value.strip().upper()
        if not normalized:
            raise ValueError("symbol must not be blank")
        return normalized

    @field_validator("received_at")
    @classmethod
    def _require_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("received_at must be timezone-aware")
        return value
