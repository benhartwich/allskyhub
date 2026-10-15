"""Weather sensor, dew heater and lightning sensor on the hub (SPEC §7.11, §7.12; roadmap
L30, L31)."""

from __future__ import annotations

import asyncio
import datetime as dt

import httpx
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from allskyhub_protocol import Mode, Status
from allskyhub_server.devices import health, labels, lightning_li
from allskyhub_server.devices.event_text import event_rows
from allskyhub_server.models import Device, EventRecord, StatusSample
from tests.fake_device import FakeDevice
from tests.test_aircraft_labels import _camera  # pyright: ignore[reportPrivateUsage]
from tests.test_lightning_li import NOW, READ, FakeStore
from tests.test_lightning_li import _flash as flash  # pyright: ignore[reportPrivateUsage]
from tests.ws_helpers import device_ws, paired_token, send


def _status(**parts: object) -> Status:
    return Status.model_validate({
        "mode": Mode.NIGHT, "exposure_us": 20_000_000, "gain": 250, "mean": 0.2,
        "uptime_s": 60, "time_trusted": True, **parts,
    })  # fmt: skip


ENV = {"at": "2026-10-15T00:00:00Z", "temperature_c": 6.4, "humidity_pct": 93.0,
       "pressure_hpa": 1012.5, "dewpoint_c": 5.4, "error": None}  # fmt: skip
HEATER = {"on": True, "reason": "dew"}
SENSOR = {"at": "2026-10-15T00:00:00Z", "strikes_30min": 3, "nearest_km": 12.0,
          "last_strike_at": "2026-10-14T23:50:00Z", "error": None}  # fmt: skip


async def test_status_shows_and_charts_the_sensors(
    app: FastAPI, client: httpx.AsyncClient, live_app: str
) -> None:
    dev = FakeDevice()
    token = await paired_token(client, dev)
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with device_ws(live_app, token) as ws:
        await send(ws, _status(env=ENV, heater=HEATER, lightning_sensor=SENSOR))
        for _ in range(100):
            async with maker() as db:
                if await db.scalar(select(func.count()).select_from(StatusSample)):
                    break
            await asyncio.sleep(0.05)
        await asyncio.sleep(0.2)
    async with maker() as db:
        [row] = list(await db.scalars(select(StatusSample)))
    assert (row.air_temp_c, row.humidity_pct, row.dewpoint_c, row.pressure_hpa) == (
        6.4, 93.0, 5.4, 1012.5,
    )  # fmt: skip
    assert row.heater_on is True

    page = (await client.get(f"/cameras/{dev.device_id}")).text
    assert "6.4 °C, 93 %, Taupunkt 5.4 °C" in page
    assert "an: Taugefahr" in page
    assert "3 Blitze in 30 Min., nächster 12 km" in page
    data = (await client.get(f"/cameras/{dev.device_id}/charts/data", params={"hours": 24})).json()
    assert data["status"]["dewpoint"][-1] == 5.4
    assert data["status"]["heater"][-1] == 1.0


async def test_a_failing_sensor_is_a_problem_and_has_no_values(
    app: FastAPI, client: httpx.AsyncClient, live_app: str
) -> None:
    dev = FakeDevice()
    token = await paired_token(client, dev)
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    broken = {**ENV, "temperature_c": None, "error": "not_found"}
    deaf = {**SENSOR, "error": "no_interrupt"}
    async with device_ws(live_app, token) as ws:
        await send(ws, _status(env=broken, lightning_sensor=deaf))
        for _ in range(100):
            async with maker() as db:
                if await db.scalar(select(func.count()).select_from(StatusSample)):
                    break
            await asyncio.sleep(0.05)
        await asyncio.sleep(0.2)
        async with maker() as db:
            [row] = list(await db.scalars(select(StatusSample)))
            device = await db.get(Device, dev.device_id)
            assert device is not None
            problems = {p.key: p.text for p in health.check(device, True, dt.datetime.now(dt.UTC))}
    assert row.air_temp_c is None
    assert row.heater_on is None
    assert problems["env"].startswith("Klimasensor: nicht gefunden")
    assert "Interrupt" in problems["lightning_sensor"]


def test_sensor_km_on_lightning_events() -> None:
    heard = flash("x", NOW, "storm-A", sensor_km=7.6)
    silent = flash("x", NOW, "storm-A", sensor_km=None)
    without = flash("x", NOW, "storm-A")
    assert dict(event_rows(heard))["Blitzsensor"] == "Blitz in 8 km"
    assert dict(event_rows(silent))["Blitzsensor"] == "kein Blitz gehört"
    assert "Blitzsensor" not in dict(event_rows(without))


async def test_the_satellite_check_leaves_storms_the_sensor_decided(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    dev, _, _ = await _camera(app, client)
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    t0 = dt.datetime(2026, 10, 14, 22, 0, tzinfo=dt.UTC)
    store = FakeStore()
    for i in range(6):  # covered, and LI saw nothing anywhere
        store.add(t0 + dt.timedelta(minutes=10 * i), [])
    async with maker() as db:
        by_camera = flash(dev.device_id, t0 + dt.timedelta(minutes=5), "storm-A", sensor_km=None)
        heard = flash(dev.device_id, t0 + dt.timedelta(minutes=20), "storm-B", sensor_km=9.0)
        nothing = flash(dev.device_id, t0 + dt.timedelta(minutes=40), "storm-C")
        db.add_all([by_camera, heard, nothing])
        await db.commit()
        # The camera's own label from its sensor (SPEC §7.12), sent as event_label.
        await labels.set_auto(db, by_camera, "other", "kein Blitz am Sensor", t0)
        await db.commit()
    async with maker() as db:
        await lightning_li.ingest(db, store, READ)
        await lightning_li.check(db, NOW)
    async with maker() as db:
        rows = {e.data["storm"]: e for e in await db.scalars(select(EventRecord))}
    assert rows["storm-A"].label_detail == "kein Blitz am Sensor"  # left as the camera said
    assert rows["storm-B"].label is None  # the sensor heard it close by
    assert rows["storm-C"].label_detail == lightning_li.DETAIL  # no sensor: the satellite
