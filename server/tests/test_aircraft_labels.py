"""Meteors that are aircraft (roadmap H3, SPEC §6.4 Labels, §7.8): automatic labels."""

from __future__ import annotations

import asyncio
import datetime as dt

import httpx
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from allskyhub_protocol import Aircraft, AircraftPosition, EventLabel
from allskyhub_server.devices import aircraft, labels
from allskyhub_server.models import Device, EventRecord
from tests.fake_device import FakeDevice
from tests.helpers import PASSWORD, owner_email
from tests.test_web_events import EVENT, NIGHT, record
from tests.ws_helpers import device_ws, paired_token, send

LAT, LON = 48.31, 14.29


async def _camera(
    app: FastAPI, client: httpx.AsyncClient
) -> tuple[FakeDevice, str, dict[str, str]]:
    dev = FakeDevice()
    token = await paired_token(client, dev)
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        device = await db.get(Device, dev.device_id)
        assert device
        device.last_status = {"settings": {"latitude": LAT, "longitude": LON}}
        await db.commit()
    r = await client.post(
        "/api/v1/auth/login",
        json={"email": await owner_email(app, dev.device_id), "password": PASSWORD},
    )
    return dev, token, {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _event(app: FastAPI, device_id: str, event_id: str = EVENT) -> EventRecord:
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        event = await db.scalar(
            select(EventRecord).where(
                EventRecord.device_id == device_id, EventRecord.event_id == event_id
            )
        )
    assert event is not None
    return event


async def test_automatic_labels_give_way(app: FastAPI, client: httpx.AsyncClient) -> None:
    """EventLabel.replaces on the hub: an automatic label never replaces a label, an
    owner's label always replaces an automatic one, whatever its time."""
    dev, _, _ = await _camera(app, client)
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        db.add(record(dev.device_id))
        await db.commit()
    t1 = dt.datetime(2026, 10, 13, 21, tzinfo=dt.UTC)
    auto = EventLabel(night_id=NIGHT, event_id=EVENT, label="false_positive",
                      reason="aircraft", at=t1, auto=True, detail="DLH4AB 3c6444")  # fmt: skip
    async with maker() as db:
        assert await labels.from_device(db, dev.device_id, auto)
    event = await _event(app, dev.device_id)
    assert (event.label_auto, event.label_detail) == (True, "DLH4AB 3c6444")
    owner = EventLabel(night_id=NIGHT, event_id=EVENT, label="confirmed",
                       at=t1 - dt.timedelta(hours=1))  # fmt: skip
    async with maker() as db:
        assert await labels.from_device(db, dev.device_id, owner)  # older, still wins
        assert not await labels.from_device(
            db, dev.device_id, auto.model_copy(update={"at": t1 + dt.timedelta(hours=1)})
        )
    event = await _event(app, dev.device_id)
    assert (event.label, event.label_auto, event.label_detail) == ("confirmed", False, None)
    async with maker() as db:
        stored = await db.get(EventRecord, event.id)
        assert stored is not None
        assert not await labels.set_auto(db, stored, "aircraft", "X", t1)  # never over one


def _flyby(tracks: aircraft.Tracks, around: dt.datetime) -> None:
    for s in range(-90, 91, 10):
        fix = aircraft.Fix(
            around + dt.timedelta(seconds=s), LAT + 0.36, LON + s * 0.003, 10_000.0, 90.0,
            450.0, "DLH4AB",
        )  # fmt: skip
        tracks.add("adsb.lol", "3c6444", fix)


async def test_the_hub_marks_a_meteor_that_is_an_aircraft(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    dev, _, auth = await _camera(app, client)
    watch: aircraft.Watch = app.state.aircraft
    now = dt.datetime.now(dt.UTC)
    start = now - dt.timedelta(minutes=2)
    _flyby(watch.tracks, start)
    for s in range(-90, 91, 10):
        watch.tracks.covered(dev.device_id, start + dt.timedelta(seconds=s))
    a = next(p for p in watch.tracks.planes(start - dt.timedelta(seconds=5), LAT, LON))
    b = next(p for p in watch.tracks.planes(start + dt.timedelta(seconds=5), LAT, LON))
    track = {"start_altitude_deg": a.elevation, "start_azimuth_deg": a.azimuth,
             "end_altitude_deg": b.elevation, "end_azimuth_deg": b.azimuth}  # fmt: skip
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        for event_id, extra in (
            ("meteor-20261013T200000Z", {}),
            ("meteor-20261013T200000Z-2", {"imported": True}),
            ("meteor-20261013T200000Z-3", {"start_altitude_deg": 80.0, "end_altitude_deg": 75.0}),
        ):
            row = record(dev.device_id)
            row.event_id = event_id
            row.start, row.end = start, start + dt.timedelta(seconds=1)
            row.data = {**row.data, **track, **extra}
            db.add(row)
        await db.commit()

    assert await watch.check_meteors(now) == {dev.device_id}
    plane = await _event(app, dev.device_id, "meteor-20261013T200000Z")
    assert (plane.label, plane.label_reason, plane.label_auto) == (
        "false_positive",
        "aircraft",
        True,
    )
    assert plane.label_detail == "DLH4AB 3c6444"
    assert plane.label_pending  # on its way to the camera
    assert (await _event(app, dev.device_id, "meteor-20261013T200000Z-2")).label is None
    assert (await _event(app, dev.device_id, "meteor-20261013T200000Z-3")).label is None
    assert await watch.check_meteors(now) == set()  # each meteor once

    listed = (await client.get(f"/api/v1/cameras/{dev.device_id}/events", headers=auth)).json()
    item = next(e for e in listed if e["id"] == "meteor-20261013T200000Z")
    assert (item["label_auto"], item["label_detail"]) == (True, "DLH4AB 3c6444")
    page = (
        await client.get(f"/cameras/{dev.device_id}/events/{NIGHT}/meteor-20261013T200000Z")
    ).text
    assert "Automatisch als „kein Meteor“ markiert: Flugzeug DLH4AB 3c6444." in page


async def test_a_meteor_without_positions_stays_unchecked(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    dev, _, _ = await _camera(app, client)
    watch: aircraft.Watch = app.state.aircraft
    now = dt.datetime.now(dt.UTC)
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        row = record(dev.device_id)
        row.start, row.end = now - dt.timedelta(minutes=2), now - dt.timedelta(minutes=2)
        row.data = {**row.data, "start_altitude_deg": 20.0, "start_azimuth_deg": 0.0,
                    "end_altitude_deg": 22.0, "end_azimuth_deg": 5.0}  # fmt: skip
        db.add(row)
        await db.commit()
    assert await watch.check_meteors(now) == set()
    assert (await _event(app, dev.device_id)).label is None


async def test_the_cameras_receiver_feeds_the_hub(
    app: FastAPI, client: httpx.AsyncClient, live_app: str
) -> None:
    dev, token, _ = await _camera(app, client)
    watch: aircraft.Watch = app.state.aircraft
    at = dt.datetime.now(dt.UTC)
    body = Aircraft(at=at, aircraft=[AircraftPosition(
        hex="abc123", flight="AUA12", lat=LAT + 0.2, lon=LON, alt_m=9000.0, track_deg=90.0,
        speed_kn=420.0, seen_s=2.0,
    )])  # fmt: skip
    async with device_ws(live_app, token) as ws:
        await send(ws, body)
        for _ in range(100):
            if watch.tracks.at(at).get("abc123"):
                break
            await asyncio.sleep(0.05)
    fix = watch.tracks.at(at - dt.timedelta(seconds=2))["abc123"]
    assert (fix.flight, fix.alt_m) == ("AUA12", 9000.0)
    assert watch.tracks.covers(dev.device_id, at, at)
