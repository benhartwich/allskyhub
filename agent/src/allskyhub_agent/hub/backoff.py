"""Reconnect delays (SPEC §6.1): 1 s doubling to 5 min, with jitter."""

from __future__ import annotations

import random

BASE_S = 1.0
MAX_S = 300.0


class Backoff:
    def __init__(self, rng: random.Random | None = None) -> None:
        self._rng = rng or random.Random()
        self._attempt = 0

    def next(self) -> float:
        """Delay before the next attempt; full jitter between half and the full value."""
        delay = min(MAX_S, BASE_S * 2**self._attempt)
        self._attempt += 1
        return delay * self._rng.uniform(0.5, 1.0)

    def reset(self) -> None:
        self._attempt = 0
