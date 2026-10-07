"""Device WebSocket, upload_frame and the live view (SPEC §6.1, §6.3, §6.5)."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from fastapi import FastAPI

from allskyhub_protocol import (
    CloseCode,
    CommandName,
    FrameVariant,
    Hello,
    Mode,
    Status,
    UploadFrameArgs,
)
from tests.fake_device import FakeDevice
from tests.helpers import home_csrf
from tests.ws_helpers import (
    JPEG,
    closed_with,
    device_ws,
    frame_info,
    next_command,
    paired_token,
    send,
)


async def test_invalid_token_closes_with_reauth(live_app: str) -> None:
    async with device_ws(live_app, "nope") as ws:
        assert await closed_with(ws) == CloseCode.REAUTH


async def test_frame_triggers_upload_and_image_is_shown(
    app: FastAPI, client: httpx.AsyncClient, live_app: str
) -> None:
    dev = FakeDevice()
    token = await paired_token(client, dev)
    async with device_ws(live_app, token) as ws:
        await send(ws, Hello(device_id=dev.device_id, profile=dev.profile, agent_version="0.2"))
        await send(
            ws,
            Status(
                mode=Mode.NIGHT, exposure_us=30_000_000, gain=120, mean=0.2,
                sensor_temp_c=21.5, uptime_s=60, time_trusted=True,
            ),
        )  # fmt: skip
        await send(ws, frame_info())
        wanted = {
            UploadFrameArgs.model_validate((await next_command(ws)).args).variant for _ in range(2)
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
            # A rejected upload can be retried.
            thumb = await dc.put(url + "?variant=thumb", content=JPEG, headers=auth)
            assert thumb.status_code == 204
            assert (await dc.put(url, content=JPEG)).status_code == 401

        # The latest image was just stored: the next frame asks for nothing.
        await send(ws, frame_info("image-20261006213100.jpg"))
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(ws.recv(), 0.5)

        page = await client.get(f"/cameras/{dev.device_id}")
        assert "online" in page.text
        assert "21.5 °C" in page.text
        img = await client.get(f"/cameras/{dev.device_id}/image/full.jpg")
        assert img.content == JPEG

        # Someone watches live: every frame is requested in full.
        await client.get(f"/cameras/{dev.device_id}/live")
        await send(ws, frame_info("image-20261006213200.jpg"))
        cmd = await next_command(ws)
        assert cmd.name is CommandName.UPLOAD_FRAME
        assert UploadFrameArgs.model_validate(cmd.args).variant is FrameVariant.FULL


async def test_new_connection_replaces_old(client: httpx.AsyncClient, live_app: str) -> None:
    dev = FakeDevice()
    token = await paired_token(client, dev)
    async with device_ws(live_app, token) as first:
        await send(first, Hello(device_id=dev.device_id, profile=dev.profile, agent_version="1"))
        async with device_ws(live_app, token):
            assert await closed_with(first) == CloseCode.REPLACED


async def test_remove_closes_with_unpaired(client: httpx.AsyncClient, live_app: str) -> None:
    dev = FakeDevice()
    token = await paired_token(client, dev)
    async with device_ws(live_app, token) as ws:
        await send(ws, Hello(device_id=dev.device_id, profile=dev.profile, agent_version="1"))
        await asyncio.sleep(0.1)
        await client.post(
            f"/cameras/{dev.device_id}/remove", data={"csrf_token": await home_csrf(client)}
        )
        assert await closed_with(ws) == CloseCode.UNPAIRED


async def test_hello_with_other_device_id_is_refused(
    client: httpx.AsyncClient, live_app: str
) -> None:
    dev = FakeDevice()
    token = await paired_token(client, dev)
    async with device_ws(live_app, token) as ws:
        await send(ws, Hello(device_id="b" * 26, profile=dev.profile, agent_version="1"))
        assert await closed_with(ws) == CloseCode.REAUTH
