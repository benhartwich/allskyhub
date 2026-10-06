"""Injected time source (architecture rule 3)."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...

    def sleep(self, seconds: float) -> None: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)

    def sleep(self, seconds: float) -> None:
        if seconds > 0:
            time.sleep(seconds)


class SimClock:
    """Clock for simulations and tests: sleeping only advances the time."""

    def __init__(self, start: datetime) -> None:
        if start.tzinfo is None:
            raise ValueError("start must be timezone-aware")
        self._t = start

    def now(self) -> datetime:
        return self._t

    def sleep(self, seconds: float) -> None:
        self._t += timedelta(seconds=max(0.0, seconds))
