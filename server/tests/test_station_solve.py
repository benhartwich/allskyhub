"""The hub orients connected cameras itself (SharedSky, SPEC §10.1, §4.8)."""

from __future__ import annotations

import asyncio
import datetime as dt
import io
import json
import math
from typing import Any

import cv2
import httpx
import numpy as np
import pytest
from fastapi import FastAPI
from PIL import Image
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from allskyhub_server.devices import station_solve, stations
from allskyhub_server.devices import triangulation as t
from allskyhub_server.devices.stations import SecretKey
from allskyhub_server.models import Device, EventRecord, MeteorPair, User
from allskyhub_solve import Solution
from allskyhub_solve.solve import catalogue, project
from tests.helpers import make_account

H, W = 1080, 1920
AT = dt.datetime(2026, 10, 7, 21, 1, tzinfo=dt.UTC)
NORDHEIM = (48.30, 4.29)
SUEDTAL = (47.80, 3.04)
TRUE = [985.0, 532.0, 1005.0, -130.0, 18.2, 0.0, 0.0]
FLIP = -1.0


def _render(lat: float, lon: float, seed: int = 1) -> bytes:
    """A night frame like packages/solve's test: the catalogue's bright stars where the lens
    puts them, fainter random ones and noise; as a JPEG."""
    r = np.random.default_rng(seed)
    img = np.zeros((H, W), dtype=np.float32)
    cv2.circle(img, (int(TRUE[0]), int(TRUE[1])), int(TRUE[2] + TRUE[3]), 40.0, -1)
    img += r.normal(0, 2.0, (H, W)).astype(np.float32)
    cat = catalogue(AT, lat, lon, min_alt=0.0)
    x, y = project(cat.alt, cat.az, TRUE, FLIP)
    for xx, yy, m in zip(x.tolist(), y.tolist(), cat.mag.tolist(), strict=True):
        if 0 <= xx < W and 0 <= yy < H:
            cv2.circle(img, (round(xx), round(yy)), 3 if m < 1.5 else 2, 230.0 - 25 * m, -1)
    for xx, yy in zip(r.integers(0, W, 400).tolist(), r.integers(0, H, 400).tolist(), strict=True):
        if math.hypot(xx - TRUE[0], yy - TRUE[1]) < TRUE[2] + TRUE[3]:
            img[yy, xx] = 90.0
    img = cv2.GaussianBlur(img, (0, 0), 1.0)
    gray = np.minimum(np.maximum(img, 0), 255).astype(np.uint8)
    out = io.BytesIO()
    Image.fromarray(gray).convert("RGB").save(out, "JPEG", quality=97)
    return out.getvalue()


def _blank() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (W, H), (30, 30, 30)).save(out, "JPEG")
    return out.getvalue()


def _true(width: int = W, height: int = H) -> Solution:
    return Solution(cx=TRUE[0], cy=TRUE[1], a1=TRUE[2], a3=TRUE[3], rot=TRUE[4], flip=FLIP,
                    tilt_east=0.0, tilt_north=0.0, stars=40, rms_px=1.0, rms_deg=0.1,
                    width=width, height=height)  # fmt: skip


def _sun(altitude: float) -> Any:
    def at(when: dt.datetime, lat: float, lon: float) -> float:
        return altitude

    return at


async def _station(
    app: FastAPI, client: httpx.AsyncClient, place: tuple[float, float]
) -> tuple[str, dict[str, str]]:
    email = await make_account(client)
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        user = await db.scalar(select(User).where(User.email == email))
        assert user is not None
        device, secret = await stations.create(db, user, f"Station {place[0]}", "other",
                                               SecretKey(app.state.stores.vault))  # fmt: skip
        await db.commit()
    auth = {"Authorization": f"Bearer {device.id}:{secret}"}
    body = {"name": f"Station {place[0]}", "latitude": place[0], "longitude": place[1],
            "elevation_m": 300, "width": W, "height": H, "clock_synced": True}  # fmt: skip
    r = await client.put("/api/connect/v1/station", json=body, headers=auth)
    assert r.status_code == 200
    return device.id, auth


def _files(meta: dict[str, Any], image: bytes) -> dict[str, tuple[str, bytes, str]]:
    return {
        "metadata": ("metadata.json", json.dumps(meta).encode(), "application/json"),
        "image": ("frame.jpg", image, "image/jpeg"),
    }


async def _settle() -> None:
    while station_solve.TASKS:
        await asyncio.gather(*station_solve.TASKS)


async def test_station_is_oriented(
    app: FastAPI, client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A dark sky and no orientation: the hub asks for a full frame, solves it in the
    background, and then asks no more; at most three tries a night."""
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    monkeypatch.setattr(station_solve, "sun_altitude", _sun(10.0))  # daylight
    station_id, auth = await _station(app, client, NORDHEIM)
    body = {"name": "S", "latitude": NORDHEIM[0], "longitude": NORDHEIM[1], "width": W, "height": H}
    r = await client.put("/api/connect/v1/station", json=body, headers=auth)
    assert r.json()["want_full_frame"] is False
    monkeypatch.setattr(station_solve, "sun_altitude", _sun(-30.0))  # night
    r = await client.put("/api/connect/v1/station", json=body, headers=auth)
    assert r.json() == {"orientation": None, "want_full_frame": True}

    meta = {"time": AT.isoformat(), "exposure_s": 30, "night": True, "clock_synced": True}
    r = await client.post("/api/connect/v1/frames", files=_files(meta, _render(*NORDHEIM)),
                          headers=auth)  # fmt: skip
    assert r.status_code == 200
    await _settle()
    async with maker() as db:
        device = await db.get(Device, station_id)
        assert device is not None
        assert device.last_status is not None
        found = device.last_status["orientation"]
    assert found is not None
    assert found["mirrored"] is True
    assert found["north_deg"] == pytest.approx(18.2, abs=0.5)
    assert found["stars"] >= 12
    assert found["lens"]["width"] == W
    meta["time"] = (AT + dt.timedelta(minutes=2)).isoformat()
    r = await client.post("/api/connect/v1/frames", files=_files(meta, _jpeg_thumb()),
                          headers=auth)  # fmt: skip
    assert r.json() == {"want_full_frame": False}  # oriented: no more full frames

    # A sky the solver cannot use: three tries tonight, then no more asking.
    other_id, other = await _station(app, client, SUEDTAL)
    answers: list[bool] = []
    for i in range(4):
        meta["time"] = (AT + dt.timedelta(minutes=10 + i)).isoformat()
        r = await client.post("/api/connect/v1/frames", files=_files(meta, _blank()),
                              headers=other)  # fmt: skip
        answers.append(r.json().get("want_full_frame"))
        await _settle()
    assert answers[:2] == [True, True]
    assert answers[2] is False  # the third try was the last tonight
    async with maker() as db:
        device = await db.get(Device, other_id)
        assert device is not None
        assert device.last_status is not None
        assert device.last_status["orientation"] is None
        assert device.last_status["solve"]["tries"] == 3


def _jpeg_thumb() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (640, 360), (10, 10, 10)).save(out, "JPEG")
    return out.getvalue()


def test_track_to_directions() -> None:
    sol = _true()
    x, y = project(np.array([30.0, 50.0]), np.array([100.0, 140.0]), TRUE, FLIP)
    sky = station_solve.directions(sol, [[float(x[0]), float(y[0])], [float(x[1]), float(y[1])]])
    assert sky is not None
    assert (sky["start_azimuth_deg"], sky["start_altitude_deg"]) == (100.0, 30.0)
    assert (sky["end_azimuth_deg"], sky["end_altitude_deg"]) == (140.0, 50.0)
    assert sky["track"] == [[100.0, 30.0], [140.0, 50.0]]
    again = station_solve.from_orientation(station_solve.to_orientation(sol, AT))
    assert again is not None
    assert again.params == pytest.approx(sol.params)


async def test_two_stations_pair(app: FastAPI, client: httpx.AsyncClient) -> None:
    """Oriented stations with trusted clocks: a meteor seen by both is triangulated; one sent
    before its station was oriented gets its directions afterwards."""
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    top, bottom = t.ecef(47.95, 3.80, 95.0), t.ecef(47.80, 3.95, 75.0)
    now = dt.datetime.now(dt.UTC)
    ids: list[str] = []
    auths: list[dict[str, str]] = []
    for place in (NORDHEIM, SUEDTAL):
        station_id, auth = await _station(app, client, place)
        ids.append(station_id)
        auths.append(auth)
        async with maker() as db:
            await db.execute(update(Device).where(Device.id == station_id).values(
                clock={"trusted": True, "bad": 0, "good": 10, "recent": []}))  # fmt: skip
            await db.commit()

    def track(place: tuple[float, float]) -> list[list[float]]:
        points: list[list[float]] = []
        for p in (top, bottom):
            az, alt = t.look(place[0], place[1], 0.3, p)
            x, y = project(np.array([alt]), np.array([az]), TRUE, FLIP)
            points.append([float(x[0]), float(y[0])])
        return points

    def event(place: tuple[float, float], second: int) -> dict[str, Any]:
        start = now + dt.timedelta(seconds=second)
        return {"kind": "meteor", "start": start.isoformat(),
                "end": (start + dt.timedelta(seconds=30)).isoformat(), "peak": 0.9,
                "track": track(place), "clock_synced": True}  # fmt: skip

    # Nordheim is oriented first; Südtal's meteor arrives before Südtal is.
    async with maker() as db:
        nordheim = await db.get(Device, ids[0])
        assert nordheim is not None
        nordheim.last_status = {**(nordheim.last_status or {}),
                            "orientation": station_solve.to_orientation(_true(), now)}  # fmt: skip
        await db.commit()
    r = await client.post("/api/connect/v1/events", files=_files(event(NORDHEIM, 0), _blank()),
                          headers=auths[0])  # fmt: skip
    assert r.status_code == 200
    later = _files(event(SUEDTAL, 10), _blank())
    r = await client.post("/api/connect/v1/events", files=later, headers=auths[1])
    assert r.status_code == 200
    async with maker() as db:
        assert (await db.scalars(select(MeteorPair))).all() == []
        salzburg = await db.get(Device, ids[1])
        assert salzburg is not None
        oriented = station_solve.to_orientation(_true(), now)
        salzburg.last_status = {**(salzburg.last_status or {}), "orientation": oriented}
        await db.commit()
    changed = await station_solve.apply(maker, ids[1], dt.datetime.now(dt.UTC))
    assert len(changed) == 1
    async with maker() as db:
        stored = await db.scalar(select(EventRecord).where(EventRecord.device_id == ids[1]))
        assert stored is not None
        assert stored.data["start_altitude_deg"] is not None
        assert len(stored.data["track"]) == 2
        pairs = (await db.scalars(select(MeteorPair))).all()
    assert len(pairs) == 1
    assert pairs[0].high_km == pytest.approx(95.0, abs=2.0)
    assert pairs[0].low_km == pytest.approx(75.0, abs=2.0)
