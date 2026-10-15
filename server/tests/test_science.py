"""Exports for science (roadmap C11)."""

from __future__ import annotations

import csv
import datetime as dt
import io

import httpx
from fastapi import FastAPI
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from allskyhub_server.devices import science
from allskyhub_server.models import Device, EventRecord, SkySample
from tests.fake_device import FakeDevice
from tests.helpers import make_account
from tests.ws_helpers import paired_token

T = dt.datetime(2026, 8, 12, 22, 15, 3, 250000, tzinfo=dt.UTC)


def _meteor(i: int, minutes: int, data: dict[str, object], label: str | None = None) -> EventRecord:
    start = T + dt.timedelta(minutes=minutes)
    return EventRecord(device_id="x", event_id=f"meteor-{i}", night_id="20260812", kind="meteor",
                       start=start, end=start + dt.timedelta(seconds=0.8), confidence=0.9,
                       has_image=True, has_thumb=True, data=data, label=label)  # fmt: skip


def _rows(text: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(text)))


def test_meteor_list_and_hourly_counts() -> None:
    meteors = [
        _meteor(1, 0, {"shower": "Perseids", "shower_match": "radiant", "azimuth_deg": 45.5,
                       "altitude_deg": 60.25, "peak": 0.95, "length_px": 300}),
        _meteor(2, 10, {"shower": None, "shower_match": "radiant"}),
        _meteor(3, 50, {"shower": "Perseids", "shower_match": "date"}),
        _meteor(4, 55, {"shower": "Perseids", "shower_match": "radiant"}, "false_positive"),
    ]  # fmt: skip
    rows = _rows(science.meteors_csv(meteors, 50.036, 10.39))
    assert rows[0]["time_utc"] == "2026-08-12T22:15:03.250+00:00"
    assert rows[0]["azimuth_deg"] == "45.5"
    assert rows[0]["altitude_deg"] == "60.25"
    assert rows[0]["shower"] == "Perseids"
    assert rows[0]["camera_latitude"] == "50.036"
    assert len(rows) == 3  # SPEC §6.4 Labels: a false positive counts nowhere, not here either
    hourly = _rows(science.hourly_counts_csv(meteors))
    assert hourly == [
        {"hour_utc": "2026-08-12T22:00:00+00:00", "meteors": "2", "Perseids": "1",
         "sporadic": "1", "unassigned": "0"},
        {"hour_utc": "2026-08-12T23:00:00+00:00", "meteors": "1", "Perseids": "0",
         "sporadic": "0", "unassigned": "1"},
    ]  # fmt: skip


def test_fireball_facts() -> None:
    bright = _meteor(1, 0, {"peak": 0.97, "azimuth_deg": 200, "altitude_deg": 30,
                            "shower": None, "shower_match": "radiant"})  # fmt: skip
    facts = dict(science.fireball_facts(bright) or [])
    assert facts["Zeit (UTC)"] == "12.08.2026 22:15:03"
    assert facts["Richtung (Mitte der Spur)"] == "Azimut 200°, Höhe 30°"
    assert facts["Strom"] == "sporadisch"
    assert science.fireball_facts(_meteor(2, 0, {"peak": 0.5})) is None


async def test_downloads(
    app: FastAPI, client: httpx.AsyncClient, new_client: httpx.AsyncClient
) -> None:
    dev = FakeDevice()
    await paired_token(client, dev)
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        await db.execute(
            update(Device)
            .where(Device.id == dev.device_id)
            .values(last_status={"settings": {"latitude": 50.036, "longitude": 10.39}})
        )
        for i, sqm in enumerate((20.1, 21.2, 20.7)):
            db.add(SkySample(device_id=dev.device_id, night_id="20260812",
                             at=T + dt.timedelta(hours=i), cloud_cover=0.1, sqm_mag=sqm,
                             stars=500))  # fmt: skip
        m = _meteor(1, 0, {"shower": "Perseids", "shower_match": "radiant", "peak": 0.95})
        m.device_id = dev.device_id
        m.event_id = "meteor-20260812T221503Z"  # a real id, as the event page checks it
        db.add(m)
        await db.commit()
    base = f"/cameras/{dev.device_id}/export"
    assert "Daten für die Wissenschaft" in (await client.get(base)).text
    sqm = await client.get(f"{base}/sqm.csv")
    assert sqm.headers["content-type"].startswith("text/csv")
    assert "attachment" in sqm.headers["content-disposition"]
    assert _rows(sqm.text) == [
        {"night": "20260812", "time_utc": "2026-08-12T23:15:03+00:00",
         "sqm_mag_arcsec2": "21.2", "cloud_cover": "0.1", "stars": "500",
         "latitude": "50.036", "longitude": "10.39"}
    ]  # fmt: skip
    assert len(_rows((await client.get(f"{base}/meteors.csv")).text)) == 1
    assert (await client.get(f"{base}/nope.csv")).status_code == 404
    page = f"/cameras/{dev.device_id}/events/20260812/meteor-20260812T221503Z"
    event = (await client.get(page)).text
    assert "Feuerkugel bei der IMO melden" in event

    await make_account(new_client)
    assert (await new_client.get(f"{base}/meteors.csv")).status_code == 404
