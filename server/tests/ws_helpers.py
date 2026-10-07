"""Shared helpers for tests that talk to the device WebSocket (SPEC §6.1)."""

from __future__ import annotations

import asyncio
import datetime as dt

import httpx
import pytest
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed

from allskyhub_protocol import (
    Command,
    Envelope,
    FrameInfo,
    Mode,
    parse_envelope,
)
from allskyhub_protocol.models import Body
from tests.fake_device import FakeDevice
from tests.helpers import claim, make_account

TS = dt.datetime(2026, 10, 6, 21, 30, tzinfo=dt.UTC)
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64


def frame_info(name: str = "image-20261006213000.jpg") -> FrameInfo:
    return FrameInfo(
        captured_at=TS,
        night_id="20261006",
        name=name,
        mode=Mode.NIGHT,
        exposure_us=30_000_000,
        gain=120,
        mean=0.21,
        sun_elevation=-35.2,
        profile="zwo-asi678mc",
    )


async def paired_token(client: httpx.AsyncClient, dev: FakeDevice) -> str:
    reg = await dev.register(client)
    assert reg.pairing_code
    await make_account(client)
    await claim(client, reg.pairing_code)
    return await dev.token(client)


def device_ws(host: str, token: str) -> connect:
    return connect(
        f"ws://{host}/device/v1/ws", additional_headers={"Authorization": f"Bearer {token}"}
    )


async def send(ws: ClientConnection, body: Body) -> None:
    await ws.send(Envelope.wrap(body, ts=TS).model_dump_json())


async def next_command(ws: ClientConnection) -> Command:
    env = parse_envelope(await asyncio.wait_for(ws.recv(), 5))
    assert isinstance(env.body, Command)
    return env.body


async def closed_with(ws: ClientConnection) -> int | None:
    with pytest.raises(ConnectionClosed) as info:
        await asyncio.wait_for(ws.recv(), 5)
    return info.value.rcvd.code if info.value.rcvd else None
