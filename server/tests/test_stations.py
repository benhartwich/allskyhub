"""Connected cameras, "stations" (SharedSky, SPEC §10)."""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import io
import json
import math
import os
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from PIL import Image, ImageDraw
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from allskyhub_protocol import EventVariant
from allskyhub_server.api import connect
from allskyhub_server.auth.ratelimit import Limit
from allskyhub_server.devices import stations
from allskyhub_server.devices.stations import SecretKey, StationError
from allskyhub_server.models import Device, EventRecord, Frame, User
from tests.helpers import make_account

W, H = 1200, 900


def _jpeg(width: int = W, height: int = H, streak: bool = False, noise: bool = False) -> bytes:
    im = Image.new("RGB", (width, height), (12, 14, 20))
    if streak:
        ImageDraw.Draw(im).line((100, 100, 500, 300), fill=(250, 250, 250), width=3)
    if noise:  # a big file
        im = Image.effect_noise((width, height), 120).convert("RGB")
    out = io.BytesIO()
    im.save(out, "JPEG", quality=95)
    return out.getvalue()


async def _station(
    app: FastAPI, client: httpx.AsyncClient, software: str = "other"
) -> tuple[str, str]:
    email = await make_account(client)
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        user = await db.scalar(select(User).where(User.email == email))
        assert user is not None
        device, secret = await stations.create(
            db, user, "Sternwarte Musterberg", software, SecretKey(app.state.stores.vault)
        )
        await db.commit()
        return device.id, secret


def _auth(station_id: str, secret: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {station_id}:{secret}"}


def _station_body(**changes: Any) -> dict[str, Any]:
    return {
        "name": "Sternwarte Musterberg", "latitude": 48.3, "longitude": 14.29,
        "elevation_m": 300, "software": "MySky", "software_version": "1.2",
        "lens": {"focal_mm": 1.55, "f_ratio": 2.0, "projection": "equidistant"},
        "width": W, "height": H, "clock_synced": True, **changes,
    }  # fmt: skip


def _files(meta: dict[str, Any], image: bytes) -> dict[str, tuple[str, bytes, str]]:
    return {
        "metadata": ("metadata.json", json.dumps(meta).encode(), "application/json"),
        "image": ("frame.jpg", image, "image/jpeg"),
    }


async def test_create_and_rotate(app: FastAPI, client: httpx.AsyncClient) -> None:
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    station_id, secret = await _station(app, client, "indi-allsky")
    assert len(secret) == 64
    assert int(secret, 16) >= 0
    key = SecretKey(app.state.stores.vault)
    async with maker() as db:
        device = await db.get(Device, station_id)
        assert device is not None
        assert device.kind == "station"
        assert device.network  # consent through the terms (SPEC §10.6)
        assert device.station_secret_hash == stations.hash_secret(secret)
        assert device.station_secret_enc is not None
        assert secret.encode() not in device.station_secret_enc
        assert key.open(device.station_secret_enc) == secret
        assert (await stations.authenticate(db, station_id, secret)).id == station_id
        new = stations.rotate_secret(device, key)
        await db.commit()
        with pytest.raises(StationError):
            await stations.authenticate(db, station_id, secret)
        assert (await stations.authenticate(db, station_id, new)).id == station_id
    other_id, _ = await _station(app, client, "other")
    async with maker() as db:
        other = await db.get(Device, other_id)
        assert other is not None
        assert other.station_secret_enc is None  # connect/v1 keeps only the hash


async def test_open_api(
    app: FastAPI, client: httpx.AsyncClient, new_client: httpx.AsyncClient
) -> None:
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    station_id, secret = await _station(app, client)
    auth = _auth(station_id, secret)

    r = await new_client.put("/api/connect/v1/station", json=_station_body(),
                             headers=_auth(station_id, "0" * 64))  # fmt: skip
    assert (r.status_code, r.json()) == (401, {"error": "unauthorized"})
    r = await new_client.put("/api/connect/v1/station", json=_station_body(latitude=95),
                             headers=auth)  # fmt: skip
    assert (r.status_code, r.json()) == (400, {"error": "bad_request", "field": "latitude"})
    r = await new_client.put("/api/connect/v1/station", json=_station_body(), headers=auth)
    assert r.status_code == 200
    assert r.json() == {"orientation": None, "want_full_frame": False}
    async with maker() as db:
        device = await db.get(Device, station_id)
        assert device is not None
        assert device.last_status is not None
        assert device.last_status["settings"] == {"latitude": 48.3, "longitude": 14.29,
                                                  "elevation_m": 300}  # fmt: skip
        assert device.last_status["station"]["width"] == W

    # Frames: a thumbnail at most once a minute.
    now = dt.datetime.now(dt.UTC)
    meta = {"time": now.isoformat(), "exposure_s": 30, "gain": 200, "night": True,
            "clock_synced": True, "stars": 400}  # fmt: skip
    thumb = _jpeg(640, 480)
    r = await new_client.post("/api/connect/v1/frames", files=_files(meta, thumb), headers=auth)
    assert (r.status_code, r.json()) == (200, {"want_full_frame": False})
    r = await new_client.post("/api/connect/v1/frames", files=_files(meta, thumb), headers=auth)
    assert r.status_code == 200
    async with maker() as db:
        frames = (await db.scalars(select(Frame).where(Frame.device_id == station_id))).all()
        assert len(frames) == 1
        assert frames[0].has_thumb
        assert frames[0].mode == "night"
        await db.execute(update(Device).where(Device.id == station_id).values(latest_thumb_at=None))
        await db.commit()
    r = await new_client.post("/api/connect/v1/frames", files=_files(meta, _jpeg(noise=True)),
                              headers=auth)  # fmt: skip
    assert (r.status_code, r.json()) == (413, {"error": "too_large"})  # a full frame unasked
    r = await new_client.post("/api/connect/v1/frames", files=_files(meta, b"not a jpeg"),
                              headers=auth)  # fmt: skip
    assert (r.status_code, r.json()) == (400, {"error": "bad_image"})
    bad_time = _files({**meta, "time": "gestern"}, thumb)
    r = await new_client.post("/api/connect/v1/frames", files=bad_time, headers=auth)
    assert (r.status_code, r.json()) == (400, {"error": "bad_request", "field": "time"})

    # Meteors: in the full frame, as an event of §6.4; without peak the hub measures it.
    event = {"kind": "meteor", "start": now.isoformat(),
             "end": (now + dt.timedelta(seconds=30)).isoformat(),
             "track": [[100, 100], [300, 200], [500, 300]], "clock_synced": True}  # fmt: skip
    r = await new_client.post("/api/connect/v1/events", files=_files(event, thumb), headers=auth)
    assert (r.status_code, r.json()) == (400, {"error": "wrong_size"})
    r = await new_client.post("/api/connect/v1/test/events",
                              files=_files(event, _jpeg(streak=True)), headers=auth)  # fmt: skip
    assert r.status_code == 200
    assert r.json()["understood"]["image"] == [W, H]
    async with maker() as db:
        assert (
            await db.scalars(select(EventRecord).where(EventRecord.device_id == station_id))
        ).all() == []
    r = await new_client.post("/api/connect/v1/events", files=_files(event, _jpeg(streak=True)),
                              headers=auth)  # fmt: skip
    assert r.status_code == 200
    event_id = r.json()["id"]
    assert event_id == f"meteor-{now:%Y%m%dT%H%M%SZ}"
    r = await new_client.post("/api/connect/v1/events", files=_files(event, _jpeg(streak=True)),
                              headers=auth)  # fmt: skip
    assert r.json()["id"] == f"{event_id}-2"
    async with maker() as db:
        stored = await db.scalar(select(EventRecord).where(EventRecord.event_id == event_id))
        assert stored is not None
        assert stored.has_original
        assert (stored.original_w, stored.original_h) == (W, H)
        assert stored.data["track_px"][0] == [100.0, 100.0]
        assert stored.data["peak"] > 0.8  # the bright line
        assert stored.data["start_azimuth_deg"] is None  # no orientation yet
        assert stored.data["time_trusted"] is True
    store = app.state.images
    assert store.event_path(station_id, stored.night_id, event_id, EventVariant.ORIGINAL).exists()

    r = await new_client.put("/api/connect/v1/test/station", json=_station_body(), headers=auth)
    assert r.json()["ok"] is True


async def test_limits(
    app: FastAPI, client: httpx.AsyncClient, new_client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    station_id, secret = await _station(app, client)
    auth = _auth(station_id, secret)
    assert (await new_client.put("/api/connect/v1/station", json=_station_body(),
                                 headers=auth)).status_code == 200  # fmt: skip
    monkeypatch.setattr(stations, "EVENTS_PER_NIGHT", 1)
    now = dt.datetime.now(dt.UTC)
    event = {"kind": "meteor", "start": now.isoformat(), "end": now.isoformat(), "peak": 0.5,
             "track": [[10, 10], [20, 20]]}  # fmt: skip
    r = await new_client.post("/api/connect/v1/events", files=_files(event, _jpeg()), headers=auth)
    assert r.status_code == 200
    r = await new_client.post("/api/connect/v1/events", files=_files(event, _jpeg()), headers=auth)
    assert (r.status_code, r.json()) == (429, {"error": "night_full"})
    monkeypatch.setattr(connect, "PER_STATION", (Limit(4, 60),))
    body = _station_body()
    codes = [
        (await new_client.put("/api/connect/v1/test/station", json=body, headers=auth)).status_code
        for _ in range(3)
    ]
    assert codes[-1] == 429
    r = await new_client.put("/api/connect/v1/test/station", json=_station_body(), headers=auth)
    assert r.json() == {"error": "too_often"}
    assert int(r.headers["Retry-After"]) >= 1


def test_clock_check() -> None:
    """SPEC §10.1 Clock: three wrong frames make the clock untrusted, ten right ones trust it
    again; frames sent late after an outage do not judge it."""
    device = Device(clock={"trusted": True, "bad": 0, "good": 0, "recent": []})
    t0 = dt.datetime(2026, 11, 5, 21, 0, tzinfo=dt.UTC)
    for i in range(3):  # a clock an hour ahead
        now = t0 + dt.timedelta(minutes=i)
        stations.check_clock(device, now + dt.timedelta(hours=1), 30, now)
    assert not stations.clock_trusted(device, True)
    for i in range(10):
        now = t0 + dt.timedelta(minutes=10 + i)
        stations.check_clock(device, now - dt.timedelta(seconds=20), 30, now)
    assert stations.clock_trusted(device, True)
    assert not stations.clock_trusted(device, False)  # the station's own word counts too
    # After an outage: a burst of old frames, a second apart, judges nothing.
    late = t0 + dt.timedelta(hours=2)
    for i in range(5):
        stations.check_clock(
            device, t0 + dt.timedelta(minutes=30 + i), 30, late + dt.timedelta(seconds=i)
        )
    assert stations.clock_trusted(device, True)
    assert device.clock is not None
    assert device.clock["bad"] == 0
    # Sending live a minute apart but each frame ten minutes old: that is the clock (judged
    # once the last three frames show it is live).
    for i in range(5):
        at = late + dt.timedelta(minutes=10 + i)
        stations.check_clock(device, at - dt.timedelta(minutes=10), 30, at)
    assert not stations.clock_trusted(device, True)


def test_night_id_and_storage_cap(tmp_path: Path) -> None:
    taken = dt.datetime(2026, 11, 6, 2, 0, tzinfo=dt.UTC)
    assert stations.night_id(taken, 14.3) == "20261105"
    assert stations.night_id(dt.datetime(2026, 11, 6, 12, 0, tzinfo=dt.UTC), 14.3) == "20261106"
    folder = tmp_path / "dev"
    for i, part in enumerate(
        ("n/events/original/a.jpg", "n/events/original/b.jpg", "n/thumb/c.jpg")
    ):
        path = folder / part
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * 1000)
        mtime = time.time() - 100 + i
        os.utime(path, (mtime, mtime))
    assert stations.cap_storage(tmp_path, "dev", limit=2500) == 1
    assert not (folder / "n/events/original/a.jpg").exists()  # the oldest full frame
    assert (folder / "n/thumb/c.jpg").exists()  # never a thumbnail


def _indi_headers(station_id: str, secret: str, metadata: bytes, shift: int = 0) -> dict[str, str]:
    """indi-allsky's own client (requests_syncapi_v1.py)."""
    window = math.floor(time.time() / 300) + shift
    signed = hmac.new(secret.encode(), msg=str(window).encode() + metadata,
                      digestmod=hashlib.sha3_512).hexdigest()  # fmt: skip
    return {"Authorization": f"Bearer {station_id}:{signed}"}


def _indi_files(metadata: bytes, media: bytes) -> dict[str, tuple[str, bytes, str]]:
    return {
        "metadata": ("metadata.json", metadata, "application/json"),
        "media": ("image.jpg", media, "application/octet-stream"),
    }


async def test_indi_allsky(
    app: FastAPI, client: httpx.AsyncClient, new_client: httpx.AsyncClient
) -> None:
    """SPEC §10.3: indi-allsky's SyncAPI as it is, signed like its client signs."""
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    station_id, secret = await _station(app, client, "indi-allsky")
    camera = json.dumps({
        "type": 1, "uuid": "abc", "name": "ZWO ASI178MC", "latitude": 47.8, "longitude": 13.04,
        "elevation": 430, "lensFocalLength": 1.55, "lensFocalRatio": 2.0, "width": 3096,
        "height": 2080,
    }).encode()  # fmt: skip
    url = "/indi-allsky/sync/v1/camera"
    files = _indi_files(camera, b"")
    wrong = _indi_headers(station_id, "f" * 64, camera)
    assert (await new_client.post(url, files=files, headers=wrong)).status_code == 401
    old = _indi_headers(station_id, secret, camera, shift=-5)
    r = await new_client.post(url, files=files, headers=old)
    assert r.status_code == 401  # outside the windows
    slow = _indi_headers(station_id, secret, camera, shift=-2)
    r = await new_client.post(url, files=files, headers=slow)
    assert r.status_code == 200
    assert isinstance(r.json()["id"], int)
    async with maker() as db:
        device = await db.get(Device, station_id)
        assert device is not None
        assert device.name == "Sternwarte Musterberg"  # not the driver's name
        assert device.last_status is not None
        assert device.last_status["settings"]["latitude"] == 47.8

    start = int(time.time()) - 40
    image = json.dumps({"type": 2, "createDate": start, "exposure": 30.0, "gain": 250,
                        "night": True, "sqm": 1234.0, "stars": 350, "detections": 1,
                        "camera_uuid": "abc", "utc_offset": 3600}).encode()  # fmt: skip
    r = await new_client.post("/indi-allsky/sync/v1/image",
                              files=_indi_files(image, _jpeg(streak=True)),
                              headers=_indi_headers(station_id, secret, image))  # fmt: skip
    assert r.status_code == 200
    async with maker() as db:
        frames = (await db.scalars(select(Frame).where(Frame.device_id == station_id))).all()
        assert len(frames) == 1
        assert frames[0].captured_at == dt.datetime.fromtimestamp(start + 15, dt.UTC)
        found = (
            await db.scalars(select(EventRecord).where(EventRecord.device_id == station_id))
        ).all()
        assert len(found) == 1
        assert found[0].data["source"] == "indi-allsky"
        assert found[0].data["candidate"] is True
        device = await db.get(Device, station_id)
        assert device is not None
        assert device.last_status is not None
        assert device.last_status["station"]["width"] == W  # the saved image's size
    video = json.dumps({"type": 3}).encode()
    r = await new_client.post("/indi-allsky/sync/v1/video", files=_indi_files(video, b"x"),
                              headers=_indi_headers(station_id, secret, video))  # fmt: skip
    assert r.status_code == 200  # dropped


def test_forget() -> None:
    device = Device(
        last_status={"settings": {"latitude": 1}}, station_secret_hash=b"x" * 32,
        station_secret_enc=b"y", network=True,
    )  # fmt: skip
    stations.forget(device)
    assert device.last_status is None
    assert device.station_secret_hash is None
    assert device.station_secret_enc is None
