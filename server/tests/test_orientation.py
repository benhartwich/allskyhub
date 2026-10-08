"""Calibrated orientation (SPEC §4.8 status.orientation) and directions on the sky."""

from __future__ import annotations

import datetime as dt

import httpx
from fastapi import FastAPI
from sqlalchemy import update

from allskyhub_server.devices.event_text import compass, event_rows, orientation_line
from allskyhub_server.models import Device, EventRecord
from tests.fake_device import FakeDevice
from tests.ws_helpers import paired_token

ORIENTATION = {
    "north_deg": 18.3,
    "mirrored": True,
    "solved_at": "2026-10-07T23:01:00+00:00",
    "stars": 34,
    "rms_deg": 0.3,
}


def test_compass_and_lines() -> None:
    assert [compass(a) for a in (0, 44, 46, 135, 180, 270, 337.6, 359)] == [
        "N", "NO", "NO", "SO", "S", "W", "N", "N",
    ]  # fmt: skip
    assert orientation_line({"orientation": ORIENTATION}) == (
        "Norden bei 18° im Bild · kalibriert auf 34 Sternen · ±0,3° · 7.10."
    )
    assert orientation_line({"orientation": None}) is None
    assert orientation_line(None) is None

    start = dt.datetime(2026, 10, 8, 21, 0, tzinfo=dt.UTC)
    event = EventRecord(
        kind="meteor", confidence=0.9, start=start, end=start,
        data={"length_px": 300, "direction_deg": 90.0, "azimuth_deg": 132.4,
              "altitude_deg": 41.7},
    )  # fmt: skip
    rows = dict(event_rows(event))
    assert rows["Himmelsrichtung"] == "132° (SO)"
    assert rows["Höhe über dem Horizont"] == "42°"
    assert "Richtung" in rows  # the image-relative axis stays
    plain = EventRecord(kind="meteor", confidence=0.9, start=start, end=start, data={})
    assert "Himmelsrichtung" not in dict(event_rows(plain))


async def test_camera_page_shows_orientation(app: FastAPI, client: httpx.AsyncClient) -> None:
    dev = FakeDevice()
    await paired_token(client, dev)
    page = (await client.get(f"/cameras/{dev.device_id}")).text
    assert "noch nicht kalibriert" in page
    async with app.state.sessionmaker() as db:
        await db.execute(
            update(Device)
            .where(Device.id == dev.device_id)
            .values(last_status={"orientation": ORIENTATION})
        )
        await db.commit()
    page = (await client.get(f"/cameras/{dev.device_id}")).text
    assert "Norden bei 18° im Bild · kalibriert auf 34 Sternen" in page


def test_update_lines() -> None:
    from allskyhub_server.devices.event_text import update_line

    assert update_line(None) is None
    assert update_line({"update": None}) is None
    assert update_line({"update": {"state": "up_to_date", "version": "0.1.0"}}) == (
        "aktuell",
        False,
    )
    waiting = update_line(
        {"update": {"state": "waiting", "version": "0.2.0", "at": "2026-10-09T01:00:00+00:00"}}
    )
    assert waiting == ("Update 0.2.0 geladen, wird tagsüber installiert, 9.10.", False)
    rolled = update_line(
        {"update": {"state": "rolled_back", "version": "0.2.0", "code": "unhealthy",
                    "at": "2026-10-09T11:00:00+00:00"}}
    )  # fmt: skip
    assert rolled == (
        "Update 0.2.0 zurückgerollt (die neue Version lief nicht sauber), 9.10.",
        True,
    )
    assert update_line({"update": {"state": "bogus"}}) is None


async def test_camera_page_shows_update(app: FastAPI, client: httpx.AsyncClient) -> None:
    dev = FakeDevice()
    await paired_token(client, dev)
    async with app.state.sessionmaker() as db:
        await db.execute(
            update(Device)
            .where(Device.id == dev.device_id)
            .values(last_status={"update": {"state": "failed", "version": "0.2.0",
                                            "code": "checksum"}})
        )  # fmt: skip
        await db.commit()
    page = (await client.get(f"/cameras/{dev.device_id}")).text
    assert '<span class="warn">Update 0.2.0 fehlgeschlagen (Prüfsumme falsch)</span>' in page
