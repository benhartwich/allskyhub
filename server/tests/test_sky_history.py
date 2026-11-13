"""The sky over a night's saved frames (draft 6, devices/sky_history.py)."""

from __future__ import annotations

import datetime as dt
import itertools
from typing import Any

import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from allskyhub_server.devices import sky_history
from allskyhub_server.models import Device, EventRecord, Frame, PastOrientation
from tests.fake_device import FakeDevice
from tests.test_sky_overlay import AT, LENS, _status  # pyright: ignore[reportPrivateUsage]
from tests.ws_helpers import paired_token

NIGHT = "20261015"


def _turned(north: float) -> dict[str, Any]:
    o: dict[str, Any] = dict(_status()["orientation"])
    o["north_deg"] = north
    return o


def _meteor(device_id: str, event_id: str, at: dt.datetime) -> EventRecord:
    return EventRecord(
        device_id=device_id,
        event_id=event_id, night_id=NIGHT, kind="meteor", start=at, end=at, confidence=0.9,
        has_image=False,
        data={"start_altitude_deg": 50.0, "start_azimuth_deg": 120.0,
              "end_altitude_deg": 35.0, "end_azimuth_deg": 150.0},
    )  # fmt: skip


async def _setup(app: FastAPI, client: httpx.AsyncClient) -> str:
    dev = FakeDevice()
    await paired_token(client, dev)
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        device = await db.get(Device, dev.device_id)
        assert device
        device.last_status = _status()  # now: north at 0°
        for i, minutes in enumerate((0, 60)):
            db.add(
                Frame(device_id=device.id, night_id=NIGHT, name=f"f{i}.jpg",
                      captured_at=AT + dt.timedelta(minutes=minutes), mode="night",
                      exposure_us=30_000_000, gain=100, mean=0.2, sun_elevation=-30,
                      has_full=True, has_thumb=True)
            )  # fmt: skip
        # Before AT + 30 min the camera was turned by 90°.
        db.add(
            PastOrientation(
                device_id=device.id, orientation=_turned(90.0),
                until=AT + dt.timedelta(minutes=30),
            )
        )  # fmt: skip
        db.add(_meteor(device.id, "m-near", AT + dt.timedelta(minutes=1)))
        db.add(_meteor(device.id, "m-far", AT + dt.timedelta(minutes=20)))
        await db.commit()
    return dev.device_id


async def test_orientation_at_the_frame_time(app: FastAPI, client: httpx.AsyncClient) -> None:
    device_id = await _setup(app, client)
    async with app.state.sessionmaker() as db:
        device = await db.get(Device, device_id)
        assert device
        before = await sky_history.orientation_at(db, device, AT)
        after = await sky_history.orientation_at(db, device, AT + dt.timedelta(minutes=31))
    assert before is not None
    assert before["north_deg"] == 90.0
    assert after is not None
    assert after["north_deg"] == 0.0


async def test_page_and_frame_json(app: FastAPI, client: httpx.AsyncClient) -> None:
    device_id = await _setup(app, client)
    page = (await client.get(f"/cameras/{device_id}/himmel")).text
    assert "data-sky-history" in page
    assert 'id="sky-frames"' in page
    assert "f0.jpg" in page
    assert "f1.jpg" in page
    assert (await client.get(f"/cameras/{device_id}/himmel?nacht=bad")).status_code == 422

    first = (await client.get(f"/cameras/{device_id}/himmel/{NIGHT}/f0.jpg.json")).json()
    second = (await client.get(f"/cameras/{device_id}/himmel/{NIGHT}/f1.jpg.json")).json()
    assert (first["width"], first["height"]) == (LENS.width, LENS.height)
    assert first["lines"]
    assert {c["text"] for c in first["cardinals"]} == {"N", "O", "S", "W"}
    # The first frame was taken while north was at 90° in the image: N on the right.
    n_first = next(c for c in first["cardinals"] if c["text"] == "N")
    n_second = next(c for c in second["cardinals"] if c["text"] == "N")
    assert n_first["x"] > LENS.cx + 300
    assert n_second["y"] < LENS.cy - 300
    # Only the meteor within 3 minutes of the frame.
    assert [t["kind"] for t in first["tracks"]] == ["meteor"]
    assert second["tracks"] == []
    assert (
        await client.get(f"/cameras/{device_id}/himmel/{NIGHT}/nope.jpg.json")
    ).status_code == 404


async def test_empty_and_closed(app: FastAPI, client: httpx.AsyncClient) -> None:
    dev = FakeDevice()
    await paired_token(client, dev)
    assert "keine Bilder" in (await client.get(f"/cameras/{dev.device_id}/himmel")).text
    async with httpx.AsyncClient(
        transport=client._transport,  # pyright: ignore[reportPrivateUsage]
        base_url=str(client.base_url),
    ) as other:
        answer = await other.get(f"/cameras/{dev.device_id}/himmel")
    assert answer.status_code in (303, 401, 404)


def test_night_window_and_planetarium_times() -> None:
    lat, lon = 50.04, 10.39
    window = sky_history.night_window(NIGHT, lat, lon, dt.UTC)
    assert window is not None
    sunset, sunrise = window
    assert sunset.date() == dt.date(2026, 10, 15)
    assert sunrise.date() == dt.date(2026, 10, 16)
    assert 15 <= sunset.hour <= 17
    times = sky_history.planetarium_times(window)
    assert times[0] >= sunset
    assert times[-1] <= sunrise
    assert times[0].minute % 10 == 0
    assert all(b - a == sky_history.PLANETARIUM_STEP for a, b in itertools.pairwise(times))
    # No sunset at all in the polar night or the midnight sun.
    assert sky_history.night_window("20260621", 78.2, 15.6, dt.UTC) is None


async def test_planetarium_json_only_inside_the_night(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    device_id = await _setup(app, client)
    url = f"/cameras/{device_id}/himmel/{NIGHT}/zeit"
    inside = (await client.get(url, params={"at": "2026-10-15T23:00:00+00:00"})).json()
    assert inside["lines"]
    assert {c["text"] for c in inside["cardinals"]} == {"N", "O", "S", "W"}
    noon = await client.get(url, params={"at": "2026-10-15T11:00:00+00:00"})
    assert noon.status_code == 422
    page = (await client.get(f"/cameras/{device_id}/himmel?nacht={NIGHT}")).text
    assert "data-sky-mode" in page  # frames and the planetarium
    assert 'id="sky-times"' in page


async def test_tonight_without_frames_is_a_planetarium(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    dev = FakeDevice()
    await paired_token(client, dev)
    async with app.state.sessionmaker() as db:
        device = await db.get(Device, dev.device_id)
        assert device
        device.last_status = _status()
        await db.commit()
    page = (await client.get(f"/cameras/{dev.device_id}/himmel")).text
    assert "sky-history planetarium" in page
    assert "Heute Nacht" in page
    assert "data-sky-mode" not in page  # no frames to switch to
