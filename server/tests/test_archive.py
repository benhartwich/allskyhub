"""Frame archive: request cadence, gallery API, unpairing and retention (SPEC §6.5)."""

from __future__ import annotations

import asyncio
import datetime as dt

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from websockets.asyncio.client import ClientConnection

from allskyhub_protocol import FrameVariant, UploadFrameArgs
from allskyhub_server import maintenance
from allskyhub_server.devices.images import ImageStore
from allskyhub_server.models import Device, Frame
from allskyhub_server.settings import Settings
from tests.fake_device import FakeDevice
from tests.helpers import home_csrf
from tests.ws_helpers import JPEG, device_ws, frame_info, next_command, paired_token, send


async def _app_auth(client: httpx.AsyncClient, email: str) -> dict[str, str]:
    from tests.helpers import PASSWORD

    r = await client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _upload_requested(
    host: str, token: str, ws: ClientConnection, count: int
) -> list[UploadFrameArgs]:
    done: list[UploadFrameArgs] = []
    async with httpx.AsyncClient(base_url=f"http://{host}") as dc:
        for _ in range(count):
            args = UploadFrameArgs.model_validate((await next_command(ws)).args)
            r = await dc.put(
                f"/device/v1/frames/{args.night_id}/{args.name}",
                params={"variant": args.variant.value},
                content=JPEG,
                headers={"Authorization": f"Bearer {token}"},
            )
            assert r.status_code == 204
            done.append(args)
    return done


async def test_gallery_lists_archived_frames_and_thumbs_come_more_often(
    app: FastAPI, client: httpx.AsyncClient, live_app: str
) -> None:
    dev = FakeDevice()
    token = await paired_token(client, dev)
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with device_ws(live_app, token) as ws:
        await send(ws, frame_info("image-1.jpg"))
        first = await _upload_requested(live_app, token, ws, 2)
        assert {a.variant for a in first} == {FrameVariant.FULL, FrameVariant.THUMB}

        # A minute later: only a thumbnail is due, the full image not yet.
        async with maker() as db:
            await db.execute(
                update(Device)
                .where(Device.id == dev.device_id)
                .values(latest_thumb_at=dt.datetime.now(dt.UTC) - dt.timedelta(seconds=61))
            )
            await db.commit()
        await send(ws, frame_info("image-2.jpg"))
        second = await _upload_requested(live_app, token, ws, 1)
        assert second[0].variant is FrameVariant.THUMB
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(ws.recv(), 0.3)

    # The app's gallery: one night, two frames, the first with its full image.
    auth = await _app_auth(client, (await _email_of(app, dev.device_id)))
    base = f"/api/v1/cameras/{dev.device_id}"
    nights = (await client.get(f"{base}/nights", headers=auth)).json()
    assert [(n["night_id"], n["frames"]) for n in nights] == [("20261006", 2)]
    frames = (await client.get(f"{base}/nights/20261006/frames", headers=auth)).json()
    assert [(f["name"], f["has_full"]) for f in frames] == [
        ("image-1.jpg", True),
        ("image-2.jpg", False),
    ]
    img = await client.get(f"{base}/frames/20261006/image-2.jpg/thumb.jpg", headers=auth)
    assert img.content == JPEG
    missing = await client.get(f"{base}/frames/20261006/image-2.jpg/full.jpg", headers=auth)
    assert missing.status_code == 404
    bad = await client.get(f"{base}/frames/2026/..%2Fx/thumb.jpg", headers=auth)
    assert bad.status_code in (400, 404)

    # Removing the camera deletes the archive at once.
    store: ImageStore = app.state.images
    assert store.frame_path(dev.device_id, "20261006", "image-1.jpg", FrameVariant.FULL).exists()
    await client.post(
        f"/cameras/{dev.device_id}/remove", data={"csrf_token": await home_csrf(client)}
    )
    assert not (store.root / dev.device_id).exists()
    async with maker() as db:
        from sqlalchemy import func, select

        assert await db.scalar(select(func.count()).select_from(Frame)) == 0


async def _email_of(app: FastAPI, device_id: str) -> str:
    from sqlalchemy import select

    from allskyhub_server.models import User

    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        email = await db.scalar(
            select(User.email)
            .join(Device, Device.owner_id == User.id)
            .where(Device.id == device_id)
        )
    assert email
    return email


async def test_retention_drops_full_images_first_then_frames(
    app: FastAPI, settings: Settings, client: httpx.AsyncClient, live_app: str
) -> None:
    dev = FakeDevice()
    token = await paired_token(client, dev)
    async with device_ws(live_app, token) as ws:
        await send(ws, frame_info("old.jpg"))
        await _upload_requested(live_app, token, ws, 2)
    store: ImageStore = app.state.images
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    full = store.frame_path(dev.device_id, "20261006", "old.jpg", FrameVariant.FULL)
    thumb = store.frame_path(dev.device_id, "20261006", "old.jpg", FrameVariant.THUMB)

    now = dt.datetime.now(dt.UTC)
    async with maker() as db:
        await maintenance.purge(db, store, settings, now=now + dt.timedelta(days=8))
    assert not full.exists()
    assert thumb.exists()
    async with maker() as db:
        await maintenance.purge(db, store, settings, now=now + dt.timedelta(days=31))
    assert not thumb.exists()
    assert not (store.root / dev.device_id / "20261006").exists()
    # The latest image (a hard link) survives the archive clean-up.
    assert store.path(dev.device_id, FrameVariant.THUMB).exists()
