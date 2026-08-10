"""An injectable clock.

Nothing in the orchestrator calls ``datetime.now`` or ``time.monotonic`` directly.
Everything — wall-clock budgets, run/stage timestamps, and Auditor's freshness
``as_of`` date — goes through a :class:`Clock`, so a test can move time forward by hand
instead of sleeping for real, and a replay stays deterministic.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...

    def monotonic(self) -> float: ...


class SystemClock:
    """The real clock, for production wiring."""

    def now(self) -> datetime:
        return datetime.now(UTC)

    def monotonic(self) -> float:
        return time.monotonic()


class FakeClock:
    """A clock a test advances by hand. Never touches the real wall clock."""

    def __init__(self, *, start: datetime | None = None, start_monotonic: float = 0.0) -> None:
        self._now = start or datetime(2026, 1, 1, tzinfo=UTC)
        self._monotonic = start_monotonic

    def now(self) -> datetime:
        return self._now

    def monotonic(self) -> float:
        return self._monotonic

    def advance(self, seconds: float) -> None:
        if seconds < 0:
            raise ValueError("a clock cannot go backwards")
        self._now += timedelta(seconds=seconds)
        self._monotonic += seconds
