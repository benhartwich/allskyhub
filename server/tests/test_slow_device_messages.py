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
    with caplog.at_level(logging.WARNING, logger="allskyhub_server.api.device"):
        async with device_ws(live_app, token) as ws:
            await send(ws, meteor())

            async def logged() -> bool:
                return any(r.message == "slow device message" for r in caplog.records)

            await until(logged)
            await asyncio.sleep(0)
    record = next(r for r in caplog.records if r.message == "slow device message")
    assert record.__dict__["type"] == "event"
    assert record.__dict__["device_id"] == dev.device_id
    assert "_handle" in record.__dict__["stack"]
