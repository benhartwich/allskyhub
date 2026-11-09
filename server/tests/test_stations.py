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
from allskyhub_server.devices import station_solve, stations
from allskyhub_server.devices.stations import SecretKey, StationError
from allskyhub_server.models import Device, EventRecord, Frame, SkySample, User
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
        "name": "Sternwarte Musterberg", "latitude": 48.3, "longitude": 4.29,
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
    app: FastAPI,
    client: httpx.AsyncClient,
    new_client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Daylight at the station whenever the test runs: no full frames for solving (§10.1).
    def daylight(*_: object) -> float:
        return 20.0

    monkeypatch.setattr(station_solve, "sun_altitude", daylight)
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
        assert device.last_status["settings"] == {"latitude": 48.3, "longitude": 4.29,
                                                  "elevation_m": 300}  # fmt: skip
        assert device.last_status["station"]["width"] == W

    # Frames: a thumbnail at most once a minute.
    now = dt.datetime.now(dt.UTC)
    meta = {"time": now.isoformat(), "exposure_s": 30, "gain": 200, "night": True,
            "clock_synced": True, "stars": 400, "sky_brightness": 21.05}  # fmt: skip
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
        # Its own sky measurement, every frame, like a camera's (§10.2).
        sky = (await db.scalars(select(SkySample).where(SkySample.device_id == station_id))).all()
        assert [(s.sqm_mag, s.stars) for s in sky] == [(21.05, 400)]
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
        assert stored.data["time_trusted"] is False  # a new station's clock (SPEC §10.1)
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
    # A window of its own (the requests above counted in the 60 s one) and so long that no
    # window ends during the test: four go through, the fifth is refused. With 60 s the
    # count began again whenever the test crossed a minute (allsky, #414's CI).
    monkeypatch.setattr(connect, "PER_STATION", (Limit(4, 10**9),))
    body = _station_body()
    codes = [
        (await new_client.put("/api/connect/v1/test/station", json=body, headers=auth)).status_code
        for _ in range(5)
    ]
    assert codes == [200, 200, 200, 200, 429]
    r = await new_client.put("/api/connect/v1/test/station", json=_station_body(), headers=auth)
    assert r.json() == {"error": "too_often"}
    assert int(r.headers["Retry-After"]) >= 1


def test_clock_check() -> None:
    """SPEC §10.1 Clock: a new station's clock is trusted after ten right frames; three
    wrong ones make it untrusted again; frames sent late after an outage judge nothing."""
    device = Device(clock={"trusted": False, "bad": 0, "good": 0, "recent": []})
    t0 = dt.datetime(2026, 11, 5, 21, 0, tzinfo=dt.UTC)
    for i in range(stations.CLOCK_GOOD_IN_A_ROW - 1):
        now = t0 + dt.timedelta(minutes=i)
        stations.check_clock(device, now - dt.timedelta(seconds=20), 30, now)
    assert not stations.clock_trusted(device, True)
    now = t0 + dt.timedelta(minutes=9)
    stations.check_clock(device, now - dt.timedelta(seconds=20), 30, now)
    assert stations.clock_trusted(device, True)
    assert not stations.clock_trusted(device, False)  # the station's own word counts too
    for i in range(3):  # a clock an hour ahead
        now = t0 + dt.timedelta(minutes=20 + i)
        stations.check_clock(device, now + dt.timedelta(hours=1), 30, now)
    assert not stations.clock_trusted(device, True)
    for i in range(10):
        now = t0 + dt.timedelta(minutes=30 + i)
        stations.check_clock(device, now - dt.timedelta(seconds=20), 30, now)
    assert stations.clock_trusted(device, True)
    # After an outage: a burst of old frames, a second apart, judges nothing.
    late = t0 + dt.timedelta(hours=2)
    for i in range(5):
        stations.check_clock(
            device, t0 + dt.timedelta(minutes=50 + i), 30, late + dt.timedelta(seconds=i)
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


def test_station_key_made_once(tmp_path: Path) -> None:
    """SPEC §10.1: two first uses at the same time make one key, not two."""
    from concurrent.futures import ThreadPoolExecutor

    from allskyhub_server.admin.stores import Vault

    vault = Vault(tmp_path / "vault")

    def use(_: int) -> bytes:
        key = SecretKey(Vault(tmp_path / "vault"))
        return key.open(key.seal("abc")).encode() + bytes(vault.secret(stations.KEY_KIND, "t"))

    with ThreadPoolExecutor(8) as pool:
        results = set(pool.map(use, range(16)))
    assert len(results) == 1
    assert vault.has(stations.KEY_KIND)
    assert len(list((tmp_path / "vault").glob(".*"))) == 0  # no leftovers


async def test_wrong_logins_and_big_frames(
    app: FastAPI, client: httpx.AsyncClient, new_client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    """Security review of #390: an address with too many wrong logins waits, also with the
    right secret, before any body is read; a frame's size is checked before decoding."""
    station_id, secret = await _station(app, client)
    auth = _auth(station_id, secret)
    assert (await new_client.put("/api/connect/v1/station", json=_station_body(),
                                 headers=auth)).status_code == 200  # fmt: skip
    monkeypatch.setattr(stations, "MAX_PIXELS", 1000)
    now = dt.datetime.now(dt.UTC)
    meta = {"time": now.isoformat(), "exposure_s": 30, "night": True}
    r = await new_client.post("/api/connect/v1/frames", files=_files(meta, _jpeg(640, 480)),
                              headers=auth)  # fmt: skip
    assert (r.status_code, r.json()) == (413, {"error": "too_large"})
    monkeypatch.setattr(stations, "MAX_PIXELS", 50_000_000)
    monkeypatch.setattr("allskyhub_server.auth.ratelimit.STATION_AUTH_PER_IP", (Limit(2, 900),))
    wrong = _auth(station_id, "0" * 64)
    body = _station_body()
    codes = [
        (await new_client.put("/api/connect/v1/station", json=body, headers=wrong)).status_code
        for _ in range(3)
    ]
    assert codes == [401, 401, 429]
    r = await new_client.put("/api/connect/v1/station", json=_station_body(), headers=auth)
    assert (r.status_code, r.json()) == (429, {"error": "too_often"})
    image = json.dumps({"createDate": int(time.time())}).encode()
    r = await new_client.post("/indi-allsky/sync/v1/image",
                              files=_indi_files(image, _jpeg()),
                              headers=_indi_headers(station_id, secret, image))  # fmt: skip
    assert r.status_code == 429  # checked before the body is parsed


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
        "type": 1, "uuid": "abc", "name": "ZWO ASI178MC", "latitude": 47.8, "longitude": 3.04,
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
        # SPEC §10.1: the clock is judged on indi-allsky's frames too, at mid-exposure.
        assert device.clock is not None
        assert device.clock["recent"][-1][0] == start + 15
        assert device.clock["good"] == 1
    video = json.dumps({"type": 3}).encode()
    r = await new_client.post("/indi-allsky/sync/v1/video", files=_indi_files(video, b"x"),
                              headers=_indi_headers(station_id, secret, video))  # fmt: skip
    assert r.status_code == 200  # dropped


async def test_indi_allsky_candidate_gets_its_track(
    app: FastAPI, client: httpx.AsyncClient, new_client: httpx.AsyncClient
) -> None:
    """indi-allsky saw a streak on the middle of three frames: the hub's own detection
    replaces its candidate with a trial meteor that has its track (SPEC §10.3)."""
    from tests.test_station_detect import frame

    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    station_id, secret = await _station(app, client, "indi-allsky")
    start = int(time.time()) - 200
    pictures = [frame(1600, 1200, seed=1), frame(1600, 1200, (300, 300, 1100, 700), seed=2),
                frame(1600, 1200, seed=3)]  # fmt: skip
    for i, picture in enumerate(pictures):
        image = json.dumps({"type": 2, "createDate": start + 60 * i, "exposure": 30.0,
                            "night": True, "detections": 1 if i == 1 else 0,
                            "camera_uuid": "abc", "utc_offset": 0}).encode()  # fmt: skip
        r = await new_client.post("/indi-allsky/sync/v1/image",
                                  files=_indi_files(image, picture),
                                  headers=_indi_headers(station_id, secret, image))  # fmt: skip
        assert r.status_code == 200
    async with maker() as db:
        found = (
            await db.scalars(select(EventRecord).where(EventRecord.device_id == station_id))
        ).all()
    assert len(found) == 1
    data = found[0].data or {}
    assert (data["source"], data["trial"]) == ("indi-allsky", True)
    assert "candidate" not in data
    assert len(data["track_px"]) >= 2
    assert found[0].start == dt.datetime.fromtimestamp(start + 60, dt.UTC)


async def test_remove_station(app: FastAPI, client: httpx.AsyncClient) -> None:
    """SPEC §10.1: removing a station deletes its place and secret at once; its meteors stay
    for joint results (§10.6)."""
    from allskyhub_server.devices import pairing, queries

    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    station_id, _ = await _station(app, client)
    async with maker() as db:
        device = await db.get(Device, station_id)
        assert device is not None
        assert device.owner_id is not None
        assert await queries.owned(db, device.owner_id) == []  # no camera pages for it
        device.last_status = {"settings": {"latitude": 48.3, "longitude": 4.29}}
        db.add(EventRecord(device_id=station_id, event_id="meteor-20261105T210000Z",
                           night_id="20261105", kind="meteor", start=dt.datetime.now(dt.UTC),
                           end=dt.datetime.now(dt.UTC), confidence=1.0, has_image=True,
                           has_original=True, data={}))  # fmt: skip
        await db.commit()
        await pairing.unpair(db, device)
        await db.commit()
    async with maker() as db:
        device = await db.get(Device, station_id)
        assert device is not None
        assert device.owner_id is None
        assert device.last_status is None
        assert device.station_secret_hash is None
        event = await db.scalar(select(EventRecord).where(EventRecord.device_id == station_id))
        assert event is not None
        assert not event.has_original


def test_forget() -> None:
    device = Device(
        last_status={"settings": {"latitude": 1}}, station_secret_hash=b"x" * 32,
        station_secret_enc=b"y", network=True,
    )  # fmt: skip
    stations.forget(device)
    assert device.last_status is None
    assert device.station_secret_hash is None
    assert device.station_secret_enc is None
