"""A device message that holds up the WebSocket is logged with what it awaits
(k44i7rl6, 2026-11-20: unanswered pings, a reconnect every minute)."""

from __future__ import annotations

import asyncio
import logging

import httpx
import pytest

from allskyhub_server.api import device
from tests.fake_device import FakeDevice
from tests.test_events import meteor
from tests.ws_helpers import device_ws, paired_token, send, until


async def test_a_slow_message_is_logged_with_its_stack(
    client: httpx.AsyncClient,
    live_app: str,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(device, "SLOW_MESSAGE_S", 0.0)
    dev = FakeDevice()
    token = await paired_token(client, dev)
    with caplog.at_level(logging.INFO, logger="allskyhub_server.api.device"):
        async with device_ws(live_app, token) as ws:
            await send(ws, meteor())

            async def logged() -> bool:
                return any(r.message == "slow device message" for r in caplog.records)

            await until(logged)
            await asyncio.sleep(0)

        async def ended() -> bool:
            return any(r.message == "device disconnected" for r in caplog.records)

        await until(ended)
    record = next(r for r in caplog.records if r.message == "slow device message")
    assert record.__dict__["type"] == "event"
    assert record.__dict__["device_id"] == dev.device_id
    assert "_handle" in record.__dict__["stack"]
    gone = next(r for r in caplog.records if r.message == "device disconnected")
    assert gone.__dict__["messages"] == 1
    assert gone.__dict__["busy_s"] >= 0


async def test_pings_are_answered_while_messages_wait(
    client: httpx.AsyncClient,
    live_app: str,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reader goes on reading while the bucket slows a backlog down: before, it stopped
    with the bucket and the device's pings went unanswered (k44i7rl6, 2026-11-20: about
    4000 messages a minute, 1011 every minute)."""
    monkeypatch.setattr(device, "BURST", 1)
    monkeypatch.setattr(device, "PER_SECOND", 2.0)
    dev = FakeDevice()
    token = await paired_token(client, dev)
    with caplog.at_level(logging.INFO, logger="allskyhub_server.api.device"):
        async with device_ws(live_app, token) as ws:
            for _ in range(8):  # 3.5 s in the bucket
                await send(ws, meteor())
            # Later, as on the road: a ping in the same read as the messages is answered
            # with them.
            await asyncio.sleep(0.3)
            pong = await ws.ping()
            await asyncio.wait_for(pong, 1.5)

        async def ended() -> bool:
            return any(r.message == "device disconnected" for r in caplog.records)

        await until(ended)
    gone = next(r for r in caplog.records if r.message == "device disconnected")
    assert gone.__dict__["most_waiting"] > 1
    assert set(gone.__dict__["types"]) == {"event"}
