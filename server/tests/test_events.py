"""Detections (SPEC §6.4): upsert by id, pictures via upload_event, app API, retention."""

from __future__ import annotations

import asyncio
import datetime as dt

import httpx
import pytest
from fastapi import FastAPI
from websockets.asyncio.client import ClientConnection

from allskyhub_protocol import (
    CommandName,
    Event,
    EventKind,
    FrameVariant,
    UploadEventArgs,
    event_id,
)
from allskyhub_server import maintenance
from allskyhub_server.devices.images import ImageStore
from allskyhub_server.settings import Settings
from tests.fake_device import FakeDevice
from tests.helpers import PASSWORD, home_csrf, owner_email
from tests.ws_helpers import JPEG, device_ws, next_command, paired_token, send

NIGHT = "20261008"
START = dt.datetime(2026, 10, 8, 21, 45, 12, tzinfo=dt.UTC)
METEOR = event_id(EventKind.METEOR, START)


def meteor(confidence: float = 0.8, has_image: bool = True) -> Event:
    return Event(
        id=METEOR,
        night_id=NIGHT,
        kind=EventKind.METEOR,
        start=START,
        end=START + dt.timedelta(seconds=1),
        confidence=confidence,
        has_image=has_image,
        data={"length_px": 412, "peak": 0.93, "frames": 3, "direction_deg": 135.0, "shower": None},
    )


async def put(host: str, token: str, variant: FrameVariant, body: bytes) -> int:
    async with httpx.AsyncClient(base_url=f"http://{host}") as dc:
        r = await dc.put(
            f"/device/v1/events/{NIGHT}/{METEOR}",
            params={"variant": variant.value},
            content=body,
            headers={"Authorization": f"Bearer {token}"},
        )
        return r.status_code


async def expect_request(ws: ClientConnection) -> UploadEventArgs:
    cmd = await next_command(ws)
    assert cmd.name is CommandName.UPLOAD_EVENT
    return UploadEventArgs.model_validate(cmd.args)


async def test_events_flow(
    app: FastAPI, settings: Settings, client: httpx.AsyncClient, live_app: str
) -> None:
    dev = FakeDevice()
    token = await paired_token(client, dev)
    r = await client.post(
        "/api/v1/auth/login",
        json={"email": await owner_email(app, dev.device_id), "password": PASSWORD},
    )
    auth = {"Authorization": f"Bearer {r.json()['access_token']}"}
    api = f"/api/v1/cameras/{dev.device_id}"

    async with device_ws(live_app, token) as ws:
        await send(ws, meteor())
        args = await expect_request(ws)
        assert (args.night_id, args.event_id, args.variant) == (NIGHT, METEOR, FrameVariant.THUMB)
        assert await put(live_app, token, FrameVariant.THUMB, b"GIF89a") == 400
        assert await put(live_app, token, FrameVariant.THUMB, JPEG) == 204  # retry works
        assert await put(live_app, token, FrameVariant.THUMB, JPEG) == 404  # only once

        # Resent after a reconnect: upserted, no second picture request.
        await send(ws, meteor(confidence=0.9))
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(ws.recv(), 0.3)

        listed = (await client.get(f"{api}/events", headers=auth)).json()
        assert len(listed) == 1
        assert listed[0]["id"] == METEOR
        assert listed[0]["confidence"] == 0.9
        assert listed[0]["data"]["length_px"] == 412
        assert listed[0]["has_thumb"] is True
        assert listed[0]["has_full"] is False
        night = (await client.get(f"{api}/nights/{NIGHT}/events", headers=auth)).json()
        assert [e["id"] for e in night] == [METEOR]
        assert (await client.get(f"{api}/nights/20261001/events", headers=auth)).json() == []

        image = f"{api}/events/{NIGHT}/{METEOR}/image"
        thumb = await client.get(image, params={"variant": "thumb"}, headers=auth)
        assert thumb.content == JPEG
        # The full picture is fetched when someone opens it.
        assert (await client.get(image, headers=auth)).status_code == 202
        full = await expect_request(ws)
        assert full.variant is FrameVariant.FULL
        assert (await client.get(image, headers=auth)).status_code == 202  # no second command
        assert await put(live_app, token, FrameVariant.FULL, JPEG) == 204
        assert (await client.get(image, headers=auth)).content == JPEG

        # An event without a picture is listed but never requested.
        later = START + dt.timedelta(minutes=5)
        plain = Event(
            id=event_id(EventKind.METEOR, later), night_id=NIGHT, kind=EventKind.METEOR,
            start=later, end=later, confidence=0.5, has_image=False, data={},
        )  # fmt: skip
        await send(ws, plain)
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(ws.recv(), 0.3)
        assert len((await client.get(f"{api}/events", headers=auth)).json()) == 2
        no_image = await client.get(f"{api}/events/{NIGHT}/{plain.id}/image", headers=auth)
        assert no_image.status_code == 404
        paged = await client.get(
            f"{api}/events", params={"before": later.isoformat(), "limit": 1}, headers=auth
        )
        assert [e["id"] for e in paged.json()] == [METEOR]

    # Retention like frames: the full picture after 7 days, everything after 30.
    store: ImageStore = app.state.images
    now = dt.datetime.now(dt.UTC)
    async with app.state.sessionmaker() as db:
        await maintenance.purge(db, store, settings, now=now + dt.timedelta(days=8))
    assert not store.event_path(dev.device_id, NIGHT, METEOR, FrameVariant.FULL).exists()
    assert store.event_path(dev.device_id, NIGHT, METEOR, FrameVariant.THUMB).exists()
    async with app.state.sessionmaker() as db:
        await maintenance.purge(db, store, settings, now=now + dt.timedelta(days=31))
    assert (await client.get(f"{api}/events", headers=auth)).json() == []


async def test_unpairing_removes_events(
    app: FastAPI, client: httpx.AsyncClient, live_app: str
) -> None:
    dev = FakeDevice()
    token = await paired_token(client, dev)
    async with device_ws(live_app, token) as ws:
        await send(ws, meteor())
        await expect_request(ws)
        assert await put(live_app, token, FrameVariant.THUMB, JPEG) == 204
    store: ImageStore = app.state.images
    assert store.event_path(dev.device_id, NIGHT, METEOR, FrameVariant.THUMB).exists()
    await client.post(
        f"/cameras/{dev.device_id}/remove", data={"csrf_token": await home_csrf(client)}
    )
    assert not (store.root / dev.device_id).exists()
    async with app.state.sessionmaker() as db:
        from sqlalchemy import func, select

        from allskyhub_server.models import EventRecord

        assert await db.scalar(select(func.count()).select_from(EventRecord)) == 0
