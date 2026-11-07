"""Audit L5: knowing an address must not be enough to lock its owner out."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from allskyhub_server.auth import ratelimit


async def test_guesses_from_one_place_do_not_lock_out_another(engine: AsyncEngine) -> None:
    for _ in range(10):
        await ratelimit.login(engine, "ben@example.org", "203.0.113.9")
    with pytest.raises(ratelimit.RateLimitedError):
        await ratelimit.login(engine, "ben@example.org", "203.0.113.9")
    # The owner at home still gets in.
    await ratelimit.login(engine, "ben@example.org", "198.51.100.7")


async def test_many_places_together_are_capped(engine: AsyncEngine) -> None:
    for i in range(100):
        await ratelimit.login(engine, "ben@example.org", f"10.0.{i // 5}.{i % 5}")
    with pytest.raises(ratelimit.RateLimitedError):
        await ratelimit.login(engine, "ben@example.org", "198.51.100.7")


async def test_the_database_clock_outside_tests(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Production has no clock of its own: the database's now() counts the window.
    monkeypatch.setattr(ratelimit, "clock", None)
    limit = (ratelimit.Limit(1, 10**9),)
    await ratelimit.hit(engine, "probe", limit)
    assert await ratelimit.blocked(engine, "probe", limit) > 0
    with pytest.raises(ratelimit.RateLimitedError) as caught:
        await ratelimit.hit(engine, "probe", limit)
    assert caught.value.retry_after > 0
