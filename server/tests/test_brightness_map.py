"""Sky brightness per sky cell (SPEC §6.3 `brightness_map`, roadmap L37): stored per
measurement with its cloud map, the night map from clear moonless frames, the chart on the
camera and public pages and the app's layer."""

from __future__ import annotations

import datetime as dt

import httpx
from fastapi import FastAPI
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from allskyhub_protocol import Mode, SkyMetrics, Status
from allskyhub_server.devices import brightness, public, stats
from allskyhub_server.models import Device, NightStat, SkySample
from tests.fake_device import FakeDevice
from tests.helpers import owner_email
from tests.test_app_api import _app_login  # pyright: ignore[reportPrivateUsage]
from tests.test_sky_overlay import _status  # pyright: ignore[reportPrivateUsage]
from tests.ws_helpers import device_ws, paired_token, send, until

# 2026-11-08 at 50.04 N 10.39 E: the moon is far below the horizon all evening; on
# 2026-11-23 it stands high.
MOONLESS = dt.datetime(2026, 11, 8, 20, tzinfo=dt.UTC)
MOONLIT = dt.datetime(2026, 11, 23, 22, tzinfo=dt.UTC)
LAT, LON = 50.04, 10.39


def _map(value: float = 20.5) -> list[float | None]:
    """A map brighter in the low south-west cells (a town), with the zenith darkest."""
    out: list[float | None] = []
    for i in range(brightness.CELLS):
        band, sector = divmod(i, brightness.SECTORS)
        out.append(value + 0.5 * band - (1.5 if band == 0 and sector in (9, 10) else 0.0))
    return out


def test_cells_nest_in_the_cloud_map() -> None:
    """SPEC §6.3: sector k covers k·22.5°..(k+1)·22.5° and lies in cloud sector
    ((k+1)//2) mod 8 of the same band."""
    assert [brightness.cloud_cell(k) for k in range(16)] == [
        0, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 6, 7, 7, 0,
    ]  # fmt: skip
    assert brightness.cloud_cell(16) == 8
    assert brightness.cloud_cell(47) == 16
    assert brightness.cell_bounds(17) == (40.0, 65.0, 22.5, 45.0)


def _sample(at: dt.datetime, values: list[float | None] | None, clouds: list[float | None],
            flat: str | None = "geometry") -> SkySample:  # fmt: skip
    return SkySample(device_id="d", night_id="20261108", at=at, cloud_map=clouds,
                     brightness_map=values, brightness_map_flat=flat)  # fmt: skip


def test_night_map_uses_clear_moonless_frames() -> None:
    clear: list[float | None] = [0.0] * 24
    frames = [_sample(MOONLESS + dt.timedelta(minutes=10 * i), _map(), clear) for i in range(6)]
    # Clouds over the north (cloud sector 0) in one more frame: brighter, but not counted.
    cloudy = [0.9, *clear[1:]]
    frames.append(_sample(MOONLESS + dt.timedelta(hours=2), _map(17.0), cloudy))
    found = brightness.night_map(frames, LAT, LON)
    assert found is not None
    assert found.flat == "geometry"
    assert found.frames == 7  # the cloudy frame still gave its clear cells...
    assert found.values[0] == 20.5  # ...but not the cloudy north
    assert found.values[1] == 20.5  # NNE lies in cloud sector 1
    assert found.values[2] == 20.5  # seven values, one of them 17.0: the median holds
    assert found.values[47] == 21.5
    assert found.values[9] == 19.0  # the town
    # Too few frames: nothing.
    assert brightness.night_map(frames[:5], LAT, LON) is None
    # The moon up: nothing; frames without a map or cloud map are skipped.
    moonlit = [_sample(MOONLIT + dt.timedelta(minutes=i), _map(), clear) for i in range(8)]
    assert brightness.night_map(moonlit, LAT, LON) is None
    assert brightness.night_map([_sample(MOONLESS, None, clear)], LAT, LON) is None
    # Mixed corrections: the night's most common one only.
    twilight = [
        _sample(MOONLESS + dt.timedelta(hours=3, minutes=i), _map(18.0), clear, "twilight")
        for i in range(3)
    ]
    mixed = brightness.night_map(frames[:6] + twilight, LAT, LON)
    assert mixed is not None
    assert (mixed.flat, mixed.values[0]) == ("geometry", 20.5)


def test_chart_colours_and_labels() -> None:
    chart = brightness.chart("20261108", _map(), "geometry")
    assert chart is not None
    assert len(chart.cells) == brightness.CELLS
    assert chart.brightest.label == "SSW · 15–40°: 19,00"
    assert chart.darkest.value == 21.5
    assert brightness.color(16.0) == brightness.RAMP[0]  # bright sky: light
    assert brightness.color(23.0) == brightness.RAMP[-1]  # dark sky: dark
    assert brightness.color(19.5) == brightness.RAMP[3]
    assert brightness.color(19.6) not in brightness.RAMP  # between two steps
    holes = _map()
    holes[3] = None
    gappy = brightness.chart("20261108", holes, "twilight")
    assert gappy is not None
    assert (len(gappy.cells), len(gappy.empty)) == (brightness.CELLS - 1, 1)
    english = brightness.chart("20261108", _map(), "geometry", english=True)
    assert english is not None
    assert {c[2] for c in english.cardinals} == {"N", "E", "S", "W"}
    assert brightness.chart("20261108", [None] * brightness.CELLS, None) is None
    assert brightness.chart("20261108", [1.0], None) is None  # wrong length


CLEAR: list[float | None] = [0.0] * 24


def _status_msg(minute: int, sky_map: list[float | None] | None) -> Status:
    return Status(
        mode=Mode.NIGHT, exposure_us=20_000_000, gain=100, mean=0.2, uptime_s=60,
        time_trusted=True,
        sky=SkyMetrics(at=MOONLESS + dt.timedelta(minutes=minute), night_id="20261108",
                       cloud_cover=0.0, sqm_mag=20.9, cloud_map=CLEAR,
                       brightness_map=sky_map,
                       brightness_map_flat="twilight" if sky_map else None),
    )  # fmt: skip


async def test_stored_with_the_cloud_map_and_kept_per_night(
    app: FastAPI, client: httpx.AsyncClient, live_app: str
) -> None:
    dev = FakeDevice()
    token = await paired_token(client, dev)
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with device_ws(live_app, token) as ws:
        await send(ws, _status_msg(0, None))  # older agent or no map: no maps stored
        for minute in range(10, 80, 10):
            await send(ws, _status_msg(minute, _map()))

        async def stored() -> bool:
            async with maker() as db:
                return len(list(await db.scalars(select(SkySample)))) == 8

        await until(stored)
    async with maker() as db:
        rows = list(await db.scalars(select(SkySample).order_by(SkySample.at)))
        assert (rows[0].cloud_map, rows[0].brightness_map) == (None, None)
        assert rows[1].cloud_map == [0.0] * 24
        assert rows[1].brightness_map == _map()
        assert rows[1].brightness_map_flat == "twilight"
        # The night map needs the place (for the moon).
        await stats.refresh(db, dt.datetime(2026, 11, 9, 8, tzinfo=dt.UTC))
        night = await db.get(NightStat, (dev.device_id, "20261108"))
        assert night is not None
        assert night.brightness_map is None
        device = await db.get(Device, dev.device_id)
        assert device
        device.last_status = _status()
        await db.commit()
        await stats.refresh(db, dt.datetime(2026, 11, 9, 8, tzinfo=dt.UTC))
        await db.refresh(night)
        assert night.brightness_map == _map()
        assert night.brightness_map_flat == "twilight"
        best_sqm, clear_hours = night.best_sqm, night.clear_hours
        assert best_sqm == 20.9
        assert clear_hours is not None
        # The samples expire; a label change recomputes the night weeks later (review of
        # #500): what came from the samples stays.
        await db.execute(delete(SkySample))
        await stats.refresh_night(db, dev.device_id, "20261108")
        await db.commit()
        await db.refresh(night)
        assert night.brightness_map == _map()
        assert night.brightness_map_flat == "twilight"
        assert (night.best_sqm, night.clear_hours) == (best_sqm, clear_hours)


async def test_chart_on_the_pages_and_the_app_layer(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    dev = FakeDevice()
    await paired_token(client, dev)
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    night = (dt.datetime.now(dt.UTC).date() - dt.timedelta(days=2)).strftime("%Y%m%d")
    async with maker() as db:
        device = await db.get(Device, dev.device_id)
        assert device
        device.last_status = _status()
        device.last_frame = {"captured_at": MOONLESS.isoformat()}
        device.latest_image_at = MOONLESS
        public.enable(device)
        slug = device.public_slug
        await db.commit()

    camera = f"/cameras/{dev.device_id}"
    assert "Himmelshelligkeit nach Richtung" not in (await client.get(camera)).text
    auth = await _app_login(client, await owner_email(app, dev.device_id))
    overlay = f"/api/v1/cameras/{dev.device_id}/sky-overlay"
    assert (await client.get(overlay, headers=auth)).json()["brightness"] is None

    async with maker() as db:
        db.add(NightStat(device_id=dev.device_id, night_id=night, brightness_map=_map(),
                         brightness_map_flat="geometry"))  # fmt: skip
        await db.commit()
    page = (await client.get(camera)).text
    assert "Himmelshelligkeit nach Richtung" in page
    assert "SSW · 15–40°: 19,00" in page
    assert "(Vignettierung)" in page
    shown = (await client.get(f"/sky/{slug}")).text
    assert "Himmelshelligkeit nach Richtung" in shown

    layer = (await client.get(overlay, headers=auth)).json()["brightness"]
    assert layer["night_id"] == night
    assert layer["scale"] == list(brightness.SCALE)
    assert len(layer["cells"]) == brightness.CELLS
    first = layer["cells"][0]
    assert first["path"].startswith("M")
    assert first["path"].endswith("Z")
    assert first["color"].startswith("#")

    # Without the sky section the public page leaves it out.
    async with maker() as db:
        device = await db.get(Device, dev.device_id)
        assert device
        profile = public.profile(device)
        sections = profile.sections.model_copy(update={"sky": False})
        device.public_profile = profile.model_copy(update={"sections": sections}).model_dump()
        await db.commit()
    assert "Himmelshelligkeit nach Richtung" not in (await client.get(f"/sky/{slug}")).text
