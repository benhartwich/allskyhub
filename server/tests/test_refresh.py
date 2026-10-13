"""Pages do not wait for outside services (devices/refresh.py)."""

from __future__ import annotations

import asyncio
import time

import pytest

from allskyhub_server.devices.refresh import Refreshing


class _Service:
    def __init__(self) -> None:
        self.calls = 0
        self.delay = 0.0
        self.answer: str | None = "a"
        self.fail = False

    async def fetch(self, key: str) -> str | None:
        self.calls += 1
        await asyncio.sleep(self.delay)
        if self.fail:
            raise ValueError("broken")
        return None if self.answer is None else f"{key}:{self.answer}"


async def test_cold_waits_then_serves_from_memory() -> None:
    service = _Service()
    cache = Refreshing(service.fetch, max_age_s=60)
    assert await cache.get("x") == "x:a"
    assert await cache.get("x") == "x:a"
    assert service.calls == 1


async def test_cold_waits_at_most_wait_s() -> None:
    service = _Service()
    service.delay = 0.5
    cache = Refreshing(service.fetch, max_age_s=60, wait_s=0.05)
    start = time.monotonic()
    assert await cache.get("x") is None
    assert time.monotonic() - start < 0.3
    await asyncio.sleep(0.6)  # the fetch went on
    assert await cache.get("x") == "x:a"
    assert service.calls == 1


async def test_stale_is_served_at_once_and_refreshed_behind() -> None:
    service = _Service()
    cache = Refreshing(service.fetch, max_age_s=0.05)
    assert await cache.get("x") == "x:a"
    await asyncio.sleep(0.06)
    service.answer, service.delay = "b", 0.5
    start = time.monotonic()
    assert await cache.get("x") == "x:a"  # the old value, without waiting
    assert await cache.get("x") == "x:a"  # one fetch at a time
    assert time.monotonic() - start < 0.1
    await asyncio.sleep(0.6)
    assert await cache.get("x") == "x:b"
    assert service.calls == 2


@pytest.mark.parametrize("fail", [True, False])
async def test_a_failure_keeps_the_old_value(fail: bool) -> None:
    service = _Service()
    cache = Refreshing(service.fetch, max_age_s=0.05)
    assert await cache.get("x") == "x:a"
    await asyncio.sleep(0.06)
    service.fail, service.answer = fail, None
    await cache.get("x")
    await asyncio.sleep(0.02)
    assert await cache.get("x") == "x:a"
    assert service.calls == 2  # and not again before max_age_s
