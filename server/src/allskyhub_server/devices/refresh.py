"""Data from outside services without making a page wait for them.

A value past its age is served at once and fetched anew in the background
(stale-while-revalidate). Only when nothing is known yet (after a start) does the caller
wait, and at most ``wait_s``; the fetch goes on and the next page has the answer. One fetch
per key at a time; a failed fetch keeps the old value and is tried again after ``max_age_s``.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable, Hashable
from typing import Generic, TypeVar

log = logging.getLogger(__name__)

WAIT_S = 3.0

K = TypeVar("K", bound=Hashable)
V = TypeVar("V")


class Refreshing(Generic[K, V]):
    def __init__(
        self,
        fetch: Callable[[K], Awaitable[V | None]],
        max_age_s: float,
        wait_s: float = WAIT_S,
    ) -> None:
        self._fetch = fetch
        self._max_age_s = max_age_s
        self._wait_s = wait_s
        self._data: dict[K, tuple[float, V | None]] = {}
        self._tasks: dict[K, asyncio.Task[None]] = {}

    async def get(self, key: K) -> V | None:
        cached = self._data.get(key)
        if cached and time.monotonic() - cached[0] < self._max_age_s:
            return cached[1]
        task = self._tasks.get(key)
        if task is None:
            task = asyncio.create_task(self._refresh(key))
            self._tasks[key] = task
        if cached:
            return cached[1]
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(asyncio.shield(task), self._wait_s)
        found = self._data.get(key)
        return found[1] if found else None

    async def _refresh(self, key: K) -> None:
        try:
            value = await self._fetch(key)
        except Exception:  # a broken answer must not stop the next try
            log.exception("refresh failed")
            value = None
        finally:
            self._tasks.pop(key, None)
        if value is None and (old := self._data.get(key)):
            value = old[1]
        self._data[key] = (time.monotonic(), value)
