"""Fireballs sent later (SPEC §10.8): the star check and the owner's pages."""

from __future__ import annotations

import asyncio
import dataclasses
import datetime as dt
import io
import json
import math
from typing import Any

import httpx
import numpy as np
import pytest
from fastapi import FastAPI
from PIL import Image, ImageDraw
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from allskyhub_server.devices import late_fireballs, station_solve, stations
from allskyhub_server.devices import triangulation as t
from allskyhub_server.devices.stations import SecretKey
from allskyhub_server.models import Device, EventRecord, LateFireball, MeteorPair, User
from allskyhub_solve.solve import project
from tests.helpers import csrf_from, make_account
from tests.test_station_solve import (
    AT,
    FLIP,
    LINZ,
    SALZBURG,
    TRUE,
    H,
    W,
    _render,  # pyright: ignore[reportPrivateUsage]
    _true,  # pyright: ignore[reportPrivateUsage]
)

TOP, BOTTOM = t.ecef(47.95, 13.80, 95.0), t.ecef(47.90, 13.95, 50.0)


def _track(place: tuple[float, float]) -> list[list[float]]:
    points: list[list[float]] = []
    for p in (TOP, BOTTOM):
        az, alt = t.look(place[0], place[1], 0.3, p)
        x, y = project(np.array([alt]), np.array([az]), TRUE, FLIP)
        points.append([round(float(x[0]), 1), round(float(y[0]), 1)])
    return points


def _with_fireball(place: tuple[float, float]) -> bytes:
    """The night frame at ``place`` with a bright streak along the fireball's track."""
    im = Image.open(io.BytesIO(_render(*place))).convert("RGB")
    ImageDraw.Draw(im).line([tuple(p) for p in _track(place)], fill=(255, 255, 255), width=5)
    out = io.BytesIO()
    im.save(out, "JPEG", quality=97)
    return out.getvalue()


def _sun(altitude: float) -> Any:
    def at(when: dt.datetime, lat: float, lon: float) -> float:
        return altitude

    return at


async def _station(app: FastAPI, email: str, place: tuple[float, float]) -> str:
    """An oriented station of ``email`` at ``place`` (the lens of test_station_solve)."""
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        user = await db.scalar(select(User).where(User.email == email))
        assert user is not None
        device, _ = await stations.create(
            db, user, f"Station {place[0]}", "other", SecretKey(app.state.stores.vault)
        )
        device.last_status = {
            "settings": {"latitude": place[0], "longitude": place[1], "elevation_m": 300,
                         "timezone": "UTC"},
            "station": {"width": W, "height": H},
            "orientation": station_solve.to_orientation(_true(), AT),
        }  # fmt: skip
        await db.commit()
        return device.id


async def _settle() -> None:
    while late_fireballs.TASKS:
        await asyncio.gather(*late_fireballs.TASKS)


async def _send(
    client: httpx.AsyncClient, device_id: str, image: bytes, taken: dt.datetime
) -> httpx.Response:
    page = await client.get(f"/nachreichen/{device_id}")
    assert page.status_code == 200
    return await client.post(
        f"/nachreichen/{device_id}",
        data={
            "csrf_token": csrf_from(page.text),
            "taken": taken.strftime("%Y-%m-%dT%H:%M:%S"),
            "zone": "UTC",
            "exposure_s": "30",
        },
        files={"image": ("image-20261007210100.jpg", image, "image/jpeg")},
    )


async def _mark(client: httpx.AsyncClient, url: str, track: list[list[float]]) -> None:
    page = await client.get(url)
    r = await client.post(
        f"{url}/spur", data={"csrf_token": csrf_from(page.text), "track": json.dumps(track)}
    )
    assert r.status_code == 303
    await _settle()


async def _upload(app: FastAPI, url: str) -> LateFireball:
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        upload = await db.get(LateFireball, url.rsplit("/", 1)[1])
        assert upload is not None
        return upload


def test_the_sky_compared_as_directions() -> None:
    """A turn of the camera about the zenith shows as that angle times cos 30° (the lowest
    sky compared); the same solution as none."""
    known = _true()
    assert station_solve.offset_deg(known, known) == pytest.approx(0.0, abs=1e-3)
    turned = dataclasses.replace(known, rot=known.rot + 1.0)
    off = station_solve.offset_deg(turned, known)
    assert off is not None
    assert off == pytest.approx(math.cos(math.radians(30.0)), abs=0.02)
    assert late_fireballs.compare(turned, known)[0] == "turned"
    mirrored = dataclasses.replace(known, flip=-known.flip)
    assert late_fireballs.compare(mirrored, known)[0] == "mirrored"


async def test_a_fireball_sent_later_pairs(
    app: FastAPI, client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two stations of one owner send the same fireball later: each passes the star check,
    the second pairs with the first, and the pair is marked as sent later. A wrong time
    fails, and passes once corrected."""
    monkeypatch.setattr(late_fireballs, "sun_altitude", _sun(-30.0))
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    email = await make_account(client)
    linz = await _station(app, email, LINZ)
    salzburg = await _station(app, email, SALZBURG)

    r = await _send(client, linz, _with_fireball(LINZ), AT + dt.timedelta(minutes=10))
    assert r.status_code == 303
    url = r.headers["location"]
    await _mark(client, url, _track(LINZ))
    upload = await _upload(app, url)
    assert upload.state == "failed"
    assert upload.reason in ("turned", "no_match")

    page = await client.get(url)
    assert "nicht bestanden" in page.text or "not passed" in page.text
    r = await client.post(
        f"{url}/zeit",
        data={"csrf_token": csrf_from(page.text), "taken": AT.strftime("%Y-%m-%dT%H:%M:%S"),
              "zone": "UTC", "exposure_s": "30"},
    )  # fmt: skip
    assert r.status_code == 303
    await _settle()
    upload = await _upload(app, url)
    assert upload.state == "accepted", upload.reason
    assert upload.check["offset_deg"] <= late_fireballs.MAX_OFF_DEG
    assert upload.event_id is not None

    r = await _send(client, salzburg, _with_fireball(SALZBURG), AT)
    second = r.headers["location"]
    await _mark(client, second, _track(SALZBURG))
    assert (await _upload(app, second)).state == "accepted"
    async with maker() as db:
        event = await db.scalar(
            select(EventRecord).where(
                EventRecord.device_id == linz, EventRecord.event_id == upload.event_id
            )
        )
        assert event is not None
        assert event.data["imported"] is True
        assert event.data["source"] == "late"
        pairs = (await db.scalars(select(MeteorPair))).all()
    assert len(pairs) == 1
    assert pairs[0].late is True
    assert pairs[0].alerted is True  # never an alarm
    assert pairs[0].low_km == pytest.approx(50.0, abs=3.0)

    page = await client.get(f"/feuerkugeln/{pairs[0].id}")
    assert "nachgereicht" in page.text

    # Deleting one takes its pair along.
    page = await client.get(second)
    r = await client.post(f"{second}/loeschen", data={"csrf_token": csrf_from(page.text)})
    assert r.status_code == 303
    async with maker() as db:
        assert (await db.scalars(select(MeteorPair))).all() == []
        assert await db.get(LateFireball, second.rsplit("/", 1)[1]) is None
        device = await db.get(Device, salzburg)
        assert device is not None


async def test_uploads_refused_at_once(
    app: FastAPI, client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Another size, an old date, and another owner's camera."""
    email = await make_account(client)
    linz = await _station(app, email, LINZ)
    small = io.BytesIO()
    Image.new("RGB", (640, 360)).save(small, "JPEG")
    r = await _send(client, linz, small.getvalue(), AT)
    assert r.status_code == 400
    assert "Größe" in r.text or "size" in r.text
    r = await _send(client, linz, _render(*LINZ), dt.datetime.now(dt.UTC) - dt.timedelta(days=400))
    assert r.status_code == 400

    await make_account(client)  # signed in as someone else now
    assert (await client.get(f"/nachreichen/{linz}")).status_code == 404


async def test_a_faint_short_meteor_is_no_fireball(
    app: FastAPI, client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(late_fireballs, "sun_altitude", _sun(-30.0))
    email = await make_account(client)
    linz = await _station(app, email, LINZ)
    a, b = _track(LINZ)
    short = [a, [a[0] + (b[0] - a[0]) * 0.1, a[1] + (b[1] - a[1]) * 0.1]]
    r = await _send(client, linz, _render(*LINZ), AT)
    url = r.headers["location"]
    await _mark(client, url, short)
    upload = await _upload(app, url)
    assert (upload.state, upload.reason) == ("failed", "not_fireball")
