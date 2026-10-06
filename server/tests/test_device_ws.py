"""Device WebSocket, upload_frame and the live view (SPEC §6.1, §6.3, §6.5)."""

from __future__ import annotations

import asyncio
import datetime as dt

import httpx
import pytest
from fastapi import FastAPI
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed

from allskyhub_protocol import (
    CloseCode,
    Command,
    CommandName,
    Envelope,
    FrameInfo,
    FrameVariant,
    Hello,
    Mode,
    Status,
    UploadFrameArgs,
    parse_envelope,
)
from tests.fake_device import FakeDevice
from tests.helpers import claim, home_csrf, make_account

TS = dt.datetime(2026, 10, 6, 21, 30, tzinfo=dt.UTC)
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64


def _frame(name: str = "image-20261006213000.jpg") -> FrameInfo:
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


async def _paired(client: httpx.AsyncClient, dev: FakeDevice) -> str:
    reg = await dev.register(client)
    assert reg.pairing_code
    await make_account(client)
    await claim(client, reg.pairing_code)
    return await dev.token(client)


def _ws(host: str, token: str) -> connect:
    return connect(
        f"ws://{host}/device/v1/ws", additional_headers={"Authorization": f"Bearer {token}"}
    )


async def _send(ws: ClientConnection, body: Hello | Status | FrameInfo) -> None:
    await ws.send(Envelope.wrap(body, ts=TS).model_dump_json())


async def _command(ws: ClientConnection) -> Command:
    env = parse_envelope(await asyncio.wait_for(ws.recv(), 5))
    assert isinstance(env.body, Command)
    return env.body


async def _closed_with(ws: ClientConnection) -> int | None:
    with pytest.raises(ConnectionClosed) as info:
        await asyncio.wait_for(ws.recv(), 5)
    return info.value.rcvd.code if info.value.rcvd else None


async def test_invalid_token_closes_with_reauth(live_app: str) -> None:
    async with _ws(live_app, "nope") as ws:
        assert await _closed_with(ws) == CloseCode.REAUTH


async def test_frame_triggers_upload_and_image_is_shown(
    app: FastAPI, client: httpx.AsyncClient, live_app: str
) -> None:
    dev = FakeDevice()
    token = await _paired(client, dev)
    async with _ws(live_app, token) as ws:
        await _send(ws, Hello(device_id=dev.device_id, profile=dev.profile, agent_version="0.2"))
        await _send(
            ws,
            Status(
                mode=Mode.NIGHT, exposure_us=30_000_000, gain=120, mean=0.2,
                sensor_temp_c=21.5, uptime_s=60, time_trusted=True,
            ),
        )  # fmt: skip
        await _send(ws, _frame())
        wanted = {
            UploadFrameArgs.model_validate((await _command(ws)).args).variant for _ in range(2)
        }
        assert wanted == {FrameVariant.FULL, FrameVariant.THUMB}

        async with httpx.AsyncClient(base_url=f"http://{live_app}") as dc:
            auth = {"Authorization": f"Bearer {token}"}
            url = "/device/v1/frames/20261006/image-20261006213000.jpg"
            r = await dc.put(url, content=JPEG, headers=auth)
            assert r.status_code == 204, r.text
            # Only once, and only what was asked for.
            assert (await dc.put(url, content=JPEG, headers=auth)).status_code == 404
            other = "/device/v1/frames/20261006/other.jpg"
            assert (await dc.put(other, content=JPEG, headers=auth)).status_code == 404
            bad = await dc.put(url + "?variant=thumb", content=b"GIF89a", headers=auth)
            assert bad.status_code == 400
            assert (await dc.put(url, content=JPEG)).status_code == 401

        # The latest image was just stored: the next frame asks for nothing.
        await _send(ws, _frame("image-20261006213100.jpg"))
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(ws.recv(), 0.5)

        page = await client.get(f"/cameras/{dev.device_id}")
        assert "online" in page.text
        assert "21.5 °C" in page.text
        img = await client.get(f"/cameras/{dev.device_id}/image/full.jpg")
        assert img.content == JPEG

        # Someone watches live: every frame is requested in full.
        await client.get(f"/cameras/{dev.device_id}/live")
        await _send(ws, _frame("image-20261006213200.jpg"))
        cmd = await _command(ws)
        assert cmd.name is CommandName.UPLOAD_FRAME
        assert UploadFrameArgs.model_validate(cmd.args).variant is FrameVariant.FULL


async def test_new_connection_replaces_old(client: httpx.AsyncClient, live_app: str) -> None:
    dev = FakeDevice()
    token = await _paired(client, dev)
    async with _ws(live_app, token) as first:
        await _send(first, Hello(device_id=dev.device_id, profile=dev.profile, agent_version="1"))
        async with _ws(live_app, token):
            assert await _closed_with(first) == CloseCode.REPLACED


async def test_remove_closes_with_unpaired(client: httpx.AsyncClient, live_app: str) -> None:
    dev = FakeDevice()
    token = await _paired(client, dev)
    async with _ws(live_app, token) as ws:
        await _send(ws, Hello(device_id=dev.device_id, profile=dev.profile, agent_version="1"))
        await asyncio.sleep(0.1)
        await client.post(
            f"/cameras/{dev.device_id}/remove", data={"csrf_token": await home_csrf(client)}
        )
        assert await _closed_with(ws) == CloseCode.UNPAIRED


async def test_hello_with_other_device_id_is_refused(
    client: httpx.AsyncClient, live_app: str
) -> None:
    dev = FakeDevice()
    token = await _paired(client, dev)
    async with _ws(live_app, token) as ws:
        await _send(ws, Hello(device_id="b" * 26, profile=dev.profile, agent_version="1"))
        assert await _closed_with(ws) == CloseCode.REAUTH
