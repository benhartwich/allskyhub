"""Timelapse over a chosen period and keogram over nights (roadmap C8)."""

from __future__ import annotations

import asyncio
import datetime as dt
import shutil
import subprocess
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from allskyhub_protocol import FrameVariant
from allskyhub_server.devices import renders
from allskyhub_server.devices.images import ImageStore
from allskyhub_server.models import Device, Frame, Product, RenderJob
from tests.fake_device import FakeDevice
from tests.helpers import home_csrf
from tests.ws_helpers import paired_token

FFMPEG = shutil.which("ffmpeg")
needs_ffmpeg = pytest.mark.skipif(FFMPEG is None, reason="ffmpeg not installed")
START = dt.datetime(2026, 10, 12, 18, tzinfo=dt.UTC)


def _jpeg(path: Path, colour: str, size: str = "64x36") -> None:
    assert FFMPEG
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [FFMPEG, "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"color=c={colour}:s={size}",
         "-frames:v", "1", str(path)],
        check=True,
    )  # fmt: skip


async def _job(app: FastAPI, job_id: object) -> RenderJob:
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    for _ in range(200):
        async with maker() as db:
            job = await db.get(RenderJob, job_id)
            assert job
            if job.state in ("done", "failed"):
                return job
        await asyncio.sleep(0.05)
    raise AssertionError("render did not finish")


async def _camera(app: FastAPI, client: httpx.AsyncClient) -> str:
    dev = FakeDevice()
    await paired_token(client, dev)
    return dev.device_id


@needs_ffmpeg
async def test_timelapse(app: FastAPI, client: httpx.AsyncClient) -> None:
    device_id = await _camera(app, client)
    store: ImageStore = app.state.images
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        for i in range(30):
            name = f"image-{i:02d}.jpg"
            _jpeg(store.frame_path(device_id, "20261012", name, FrameVariant.THUMB),
                  "blue" if i % 2 else "black")  # fmt: skip
            db.add(Frame(device_id=device_id, night_id="20261012", name=name,
                         captured_at=START + dt.timedelta(minutes=i), mode="night",
                         exposure_us=1, gain=1, mean=0.1, sun_elevation=-20,
                         has_thumb=True))  # fmt: skip
        await db.commit()
    url = f"/cameras/{device_id}/renders"
    page = await client.get(url)
    assert "Zeitraffer erstellen" in page.text
    r = await client.post(url, data={"kind": "timelapse", "start": "2026-10-12T20:00",
                                     "end": "2026-10-12T20:20", "fps": "10",
                                     "csrf_token": await home_csrf(client)})  # fmt: skip
    assert r.status_code == 303
    async with maker() as db:
        job_id = (await db.scalars(select(RenderJob.id))).one()
    job = await _job(app, job_id)
    assert job.state == "done", job.error
    assert job.frames == 21  # 20:00 to 20:20 Vienna = 18:00 to 18:20 UTC
    video = await client.get(f"{url}/{job_id}/file")
    assert video.headers["content-type"] == "video/mp4"
    assert video.content[4:8] == b"ftyp"
    assert "fertig" in (await client.get(url)).text

    r = await client.post(f"{url}/{job_id}/delete", data={"csrf_token": await home_csrf(client)})
    assert r.status_code == 303
    assert (await client.get(f"{url}/{job_id}/file")).status_code == 404


@needs_ffmpeg
async def test_keogram_over_nights(app: FastAPI, client: httpx.AsyncClient) -> None:
    device_id = await _camera(app, client)
    store: ImageStore = app.state.images
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        for night in ("20261010", "20261011", "20261012"):
            _jpeg(store.product_path(device_id, night, "keogram.jpg", FrameVariant.THUMB),
                  "navy", "120x80")  # fmt: skip
            db.add(Product(device_id=device_id, night_id=night, kind="keogram",
                           name="keogram.jpg", content_type="image/jpeg", size=1,
                           thumbnail=True, has_thumb=True))  # fmt: skip
        device = await db.get(Device, device_id)
        assert device
        job = await renders.request(db, device, "keogram", START - dt.timedelta(days=3), START)
        await db.commit()
        job_id = job.id
    app.state.renderer.wake()
    done = await _job(app, job_id)
    assert done.state == "done", done.error
    assert done.frames == 3
    image = await client.get(f"/cameras/{device_id}/renders/{job_id}/file")
    assert image.content[:2] == b"\xff\xd8"


async def test_requests_are_checked(app: FastAPI, client: httpx.AsyncClient) -> None:
    device_id = await _camera(app, client)
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        device = await db.get(Device, device_id)
        assert device
        for start, end, fps, message in (
            (START, START, 25, "Das Ende muss nach dem Anfang liegen."),
            (START, START + dt.timedelta(days=40), 25, "Höchstens 31 Tage auf einmal."),
            (START, START + dt.timedelta(hours=1), 200, "Bilder pro Sekunde: 5 bis 60."),
        ):
            with pytest.raises(renders.RenderError, match=message):
                await renders.request(db, device, "timelapse", start, end, fps)
    url = f"/cameras/{device_id}/renders"
    bad = await client.post(url, data={"kind": "timelapse", "start": "gestern", "end": "",
                                       "csrf_token": await home_csrf(client)})  # fmt: skip
    assert bad.status_code == 400
    assert "Bitte Datum und Uhrzeit angeben." in bad.text


async def test_old_renders_are_removed(app: FastAPI, client: httpx.AsyncClient) -> None:
    device_id = await _camera(app, client)
    store: ImageStore = app.state.images
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        job = RenderJob(device_id=device_id, kind="keogram", start=START, end=START,
                        state="done", created_at=START - dt.timedelta(days=30))  # fmt: skip
        db.add(job)
        await db.commit()
        path = store.render_path(device_id, str(job.id), ".jpg")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
        await renders.purge(db, store, app.state.settings, dt.datetime.now(dt.UTC))
        await db.commit()
        assert (await db.scalars(select(RenderJob))).all() == []
    assert not path.exists()
