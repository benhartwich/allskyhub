"""The hub logs what blocks its event loop (stall.py)."""

from __future__ import annotations

import asyncio
import logging
import time

import pytest

from allskyhub_server.stall import StallWatch


def _busy(seconds: float) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        pass


async def test_a_blocked_loop_is_logged_with_its_stack(caplog: pytest.LogCaptureFixture) -> None:
    watch = StallWatch(stall_s=0.3)
    task = asyncio.create_task(watch.run_forever())
    await asyncio.sleep(0.3)
    assert watch.stalls == 0
    with caplog.at_level(logging.WARNING, logger="allskyhub_server.stall"):
        _busy(0.8)
        await asyncio.sleep(0.3)
    task.cancel()
    assert watch.stalls == 1  # once per stall
    record = next(r for r in caplog.records if r.message == "event loop blocked")
    assert "_busy" in str(getattr(record, "stack", ""))
