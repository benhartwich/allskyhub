"""Fireballs sent later (SPEC §10.8): the star check and the owner's pages."""

from __future__ import annotations

import asyncio
import dataclasses
import datetime as dt
import io
import json
import math
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import pytest
from fastapi import FastAPI, HTTPException
from PIL import Image, ImageDraw
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from allskyhub_server.auth import ratelimit
from allskyhub_server.devices import (
    labels,
    late_fireballs,
    meteor_pairs,
    past_orientations,
    station_solve,
    stations,
)
from allskyhub_server.devices import triangulation as t
from allskyhub_server.devices.stations import SecretKey
from allskyhub_server.models import (
    Device,
    EventRecord,
    LateFireball,
    MeteorPair,
    PastOrientation,
    User,
)
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


async def _accepted(app: FastAPI, client: httpx.AsyncClient, device_id: str) -> LateFireball:
    r = await _send(client, device_id, _with_fireball(LINZ), AT)
    url = r.headers["location"]
    await _mark(client, url, _track(LINZ))
    upload = await _upload(app, url)
    assert upload.state == "accepted", upload.reason
    return upload


async def test_a_check_a_restart_ended_starts_again(
    app: FastAPI, client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """allsky's review: a deploy ends the task, the upload must not stay "checking"; one
    that never ends goes with the failed ones."""
    monkeypatch.setattr(late_fireballs, "sun_altitude", _sun(-30.0))
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    linz = await _station(app, await make_account(client), LINZ)
    r = await _send(client, linz, _with_fireball(LINZ), AT)
    upload_id = r.headers["location"].rsplit("/", 1)[1]
    async with maker() as db:
        upload = await db.get(LateFireball, upload_id)
        assert upload is not None
        upload.state, upload.track_px = "checking", _track(LINZ)  # as a restart left it
        await db.commit()
    assert await late_fireballs.resume(maker, app.state.images) == 1
    await _settle()
    async with maker() as db:
        upload = await db.get(LateFireball, upload_id)
        assert upload is not None
        assert upload.state == "accepted"
        stuck = LateFireball(
            device_id=linz, taken_at=AT, exposure_s=30.0, track_px=[], width=W, height=H,
            state="checking", created_at=AT,
        )  # fmt: skip
        db.add(stuck)
        await db.commit()
        await late_fireballs.purge(db, app.state.images, AT + late_fireballs.KEEP_FAILED * 2)
        await db.commit()
        assert await db.get(LateFireball, stuck.id) is None


async def test_paired_later_still_sent_later(
    app: FastAPI, client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """allsky's review: the pair a live camera makes with a fireball sent later is marked
    so and never alarms; the station's new orientation keeps its own directions; once
    hidden it pairs no more and its label stays."""
    monkeypatch.setattr(late_fireballs, "sun_altitude", _sun(-30.0))
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    email = await make_account(client)
    linz = await _station(app, email, LINZ)
    salzburg = await _station(app, email, SALZBURG)
    upload = await _accepted(app, client, linz)
    assert upload.event_id is not None

    sky = station_solve.directions(_true(), _track(SALZBURG))
    assert sky is not None
    async with maker() as db:
        device = await db.get(Device, salzburg)
        assert device is not None
        live = await stations.event_id(db, device, "meteor", AT)
        db.add(EventRecord(
            device_id=salzburg, event_id=live, night_id="20261007", kind="meteor", has_image=False,
            start=AT - dt.timedelta(seconds=15), end=AT + dt.timedelta(seconds=15),
            confidence=1.0, data={"track_px": _track(SALZBURG), "time_trusted": True,
                                  "peak": 0.95} | sky,
        ))  # fmt: skip
        await db.commit()
    alarms: list[Any] = []

    async def alarm(pair_id: Any) -> None:
        alarms.append(pair_id)

    assert await meteor_pairs.pair_event(maker, salzburg, live, on_fireball=alarm) == 1
    assert alarms == []
    async with maker() as db:
        pair = await db.scalar(select(MeteorPair))
        assert pair is not None
        assert (pair.late, pair.alerted) == (True, True)
        event = await db.scalar(
            select(EventRecord).where(
                EventRecord.device_id == linz, EventRecord.event_id == upload.event_id
            )
        )
        assert event is not None
        before = dict(event.data)
        device = await db.get(Device, linz)
        assert device is not None
        turned = dataclasses.replace(_true(), rot=_true().rot + 2.0)
        orientation = station_solve.to_orientation(turned, AT)
        device.last_status = {**(device.last_status or {}), "orientation": orientation}
        await db.commit()
    changed = await station_solve.apply(maker, linz, AT + dt.timedelta(hours=1))
    assert upload.event_id not in changed
    async with maker() as db:
        row = await db.get(LateFireball, upload.id)
        assert row is not None
        await late_fireballs.hide(db, row)
        await db.commit()
        event = await db.scalar(
            select(EventRecord).where(
                EventRecord.device_id == linz, EventRecord.event_id == upload.event_id
            )
        )
        assert event is not None
        assert {k: v for k, v in event.data.items() if k != "hidden"} == before
        assert (await db.scalars(select(MeteorPair))).all() == []
        with pytest.raises(HTTPException):
            await labels.set_on_hub(db, linz, event.night_id, event.event_id, None)
    assert await meteor_pairs.pair_event(maker, linz, upload.event_id) == 0
    assert await meteor_pairs.pair_event(maker, salzburg, live) == 0


async def test_deleting_frees_no_upload(
    app: FastAPI, client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """allsky's review: the day's uploads and checks are counted apart from the rows."""
    monkeypatch.setattr(late_fireballs, "UPLOADS_PER_DAY", (ratelimit.Limit(1, 86400),))
    linz = await _station(app, await make_account(client), LINZ)
    r = await _send(client, linz, _render(*LINZ), AT)
    url = r.headers["location"]
    page = await client.get(url)
    r = await client.post(f"{url}/loeschen", data={"csrf_token": csrf_from(page.text)})
    assert r.status_code == 303
    r = await _send(client, linz, _render(*LINZ), AT)
    assert r.status_code == 400
    assert "20" in r.text


def test_waiting_uploads_count_not_for_the_station(tmp_path: Path) -> None:
    """allsky's review: late/ is not the station's storage to cap."""
    (tmp_path / "dev" / "late").mkdir(parents=True)
    (tmp_path / "dev" / "late" / "a.jpg").write_bytes(b"x" * 1000)
    (tmp_path / "dev" / "n" / "original").mkdir(parents=True)
    (tmp_path / "dev" / "n" / "original" / "b.jpg").write_bytes(b"x" * 100)
    assert stations.cap_storage(tmp_path, "dev", limit=500) == 0
    assert (tmp_path / "dev" / "n" / "original" / "b.jpg").exists()


def test_a_moved_camera_is_told_apart() -> None:
    """SPEC §10.8: a new solution of the same pose is no move; a turn, another mirror or
    another size is."""
    at = station_solve.to_orientation(_true(), AT)
    again_at = AT + dt.timedelta(hours=1)
    again = station_solve.to_orientation(_true(), AT + dt.timedelta(days=1))
    turned = station_solve.to_orientation(
        dataclasses.replace(_true(), rot=_true().rot + 2.0), again_at
    )
    smaller = station_solve.to_orientation(dataclasses.replace(_true(), width=W // 2), again_at)
    assert not past_orientations.moved(at, again)
    assert not past_orientations.moved(at, at)  # the same report again
    assert past_orientations.moved(at, turned)
    assert past_orientations.moved(at, smaller)
    assert past_orientations.moved(at, None)  # dropped for a new size
    assert not past_orientations.moved(None, at)


async def test_a_fireball_from_before_the_camera_was_moved(
    app: FastAPI, client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The camera was turned by 2° since: the frame is checked against the orientation it
    had then, and only while it held (allsky's review). A pose kept already only holds
    longer; old ones go after a year, at most 20 are kept."""
    monkeypatch.setattr(late_fireballs, "sun_altitude", _sun(-30.0))
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    linz = await _station(app, await make_account(client), LINZ)
    later = AT + dt.timedelta(days=30)

    def pose(turn: float, at: dt.datetime) -> dict[str, Any]:
        return station_solve.to_orientation(
            dataclasses.replace(_true(), rot=_true().rot + turn), at
        )

    async with maker() as db:
        device = await db.get(Device, linz)
        assert device is not None
        before = (device.last_status or {})["orientation"]
        assert await past_orientations.keep(db, linz, before, pose(2.0, later), later)
        device.last_status = {**(device.last_status or {}), "orientation": pose(2.0, later)}
        await db.commit()
        # The same pose solved again (0.5° off) is no new one: it holds longer.
        assert await past_orientations.keep(db, linz, pose(0.5, AT), pose(2.0, later), later)
        await db.commit()
        assert len((await db.scalars(select(PastOrientation))).all()) == 1
        known = await past_orientations.known(db, device)
        assert [w for _, w in known] == [(later, None), (None, later)]
        # Back to an older pose and away again (A, B, A, C): A twice, each in its time.
        t2, t3 = later + dt.timedelta(days=10), later + dt.timedelta(days=20)
        assert await past_orientations.keep(db, linz, pose(2.0, later), pose(0.0, t2), t2)
        assert await past_orientations.keep(db, linz, pose(0.0, t2), pose(5.0, t3), t3)
        windows = [w for _, w in await past_orientations.known(db, device)]
        await db.rollback()
    assert windows[1:] == [(t2, t3), (later, t2), (None, later)]
    found = _true()
    assert late_fireballs.best_match(found, known, AT)[0] is None
    # Taken while the camera stood turned: the old pose does not count then.
    assert late_fireballs.best_match(found, known, later + dt.timedelta(days=5))[0] == "turned"

    upload = await _accepted(app, client, linz)
    assert upload.check["orientation_until"] is not None
    assert upload.check["offset_deg"] <= late_fireballs.MAX_OFF_DEG

    async with maker() as db:
        for k in range(25):
            await past_orientations.keep(
                db, linz, pose(3.0 + 2 * k, AT), pose(90.0, later), later + dt.timedelta(days=k)
            )
        await past_orientations.keep(
            db, linz, pose(-30.0, AT), pose(90.0, later), later - dt.timedelta(days=400)
        )
        await db.commit()
        await past_orientations.purge(db, later + dt.timedelta(days=30))
        await db.commit()
        rows = (await db.scalars(select(PastOrientation))).all()
    assert len(rows) == past_orientations.MAX_KEPT


async def test_a_new_frame_size_keeps_the_orientation(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    linz = await _station(app, await make_account(client), LINZ)
    async with maker() as db:
        device = await db.get(Device, linz)
        assert device is not None
        past_orientations.dropped(device, AT)
        await db.commit()
        assert len((await db.scalars(select(PastOrientation))).all()) == 1
        assert await late_fireballs.frame_sizes(db, device) == [(W, H)]


def _disc(im: Image.Image, alt: float, az: float, across: float) -> None:
    """A saturated disc ``across`` degrees wide at (alt, az): a moon with its glow."""
    x, y = project(np.array([alt]), np.array([az]), TRUE, FLIP)
    x2, y2 = project(np.array([alt + 1.0]), np.array([az]), TRUE, FLIP)
    r = across / 2 * math.hypot(float(x2[0] - x[0]), float(y2[0] - y[0]))
    cx, cy = float(x[0]), float(y[0])
    white = 255 if im.mode == "L" else (255, 255, 255)
    ImageDraw.Draw(im).ellipse((cx - r, cy - r, cx + r, cy + r), fill=white)


def _moon_frame(*discs: tuple[float, float, float]) -> np.ndarray[Any, np.dtype[np.uint8]]:
    im = Image.open(io.BytesIO(_render(*LINZ))).convert("L")
    for disc in discs:
        _disc(im, *disc)
    return np.asarray(im).copy()


def test_the_moon_checks_the_date() -> None:
    """SPEC §10.8 after allsky's review: the moon where it stood confirms the date, its glow
    however wide; a compact moon-sized patch elsewhere contradicts it; a missing moon, a
    small bright point, the fireball's own glow, a low or thin moon say nothing."""
    sol, track = _true(), _track(LINZ)
    here = (45.0, 100.0, 0.6)

    def check(
        *discs: tuple[float, float, float], moon: tuple[float, float, float] = here
    ) -> str | None:
        return late_fireballs.moon_check(_moon_frame(*discs), sol, moon, track)

    assert check((45.0, 100.0, 15.0)) == "confirmed"
    assert check((45.0, 100.0, 15.0), (50.0, 250.0, 3.0)) == "confirmed"  # a ghost besides
    assert check((50.0, 250.0, 3.0)) == "contradicted"
    assert check() is None  # clouds, or a lower exposure
    assert check((50.0, 250.0, 0.8)) is None  # Venus
    a, b = track
    mid = sol.position((a[0] + b[0]) / 2, (a[1] + b[1]) / 2, W, H)
    assert mid is not None
    assert check((mid[1], mid[0], 4.0)) is None  # the fireball's own glow
    assert check((50.0, 250.0, 3.0), moon=(5.0, 100.0, 0.9)) is None
    assert check((50.0, 250.0, 3.0), moon=(45.0, 100.0, 0.05)) is None
    # The real moon of that night is computed where it stood.
    alt, az, lit = late_fireballs.moon_place(AT, *LINZ)
    assert -90 <= alt <= 90
    assert 0 <= az < 360
    assert 0 <= lit <= 1


async def test_a_wrong_day_fails_on_the_moon(
    app: FastAPI, client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(late_fireballs, "sun_altitude", _sun(-30.0))

    def moon_then(*_: object) -> tuple[float, float, float]:
        return 45.0, 100.0, 0.9  # where it stood on the given day

    monkeypatch.setattr(late_fireballs, "moon_place", moon_then)
    linz = await _station(app, await make_account(client), LINZ)
    im = Image.open(io.BytesIO(_with_fireball(LINZ)))
    _disc(im, 50.0, 250.0, 3.0)  # the moon of another day
    out = io.BytesIO()
    im.save(out, "JPEG", quality=97)
    r = await _send(client, linz, out.getvalue(), AT)
    url = r.headers["location"]
    await _mark(client, url, _track(LINZ))
    upload = await _upload(app, url)
    assert (upload.state, upload.reason) == ("failed", "moon")
    assert "Mond" in (await client.get(url)).text
