"""Star size in the hub (draft E, approved 2026-11-15; SPEC §6.3 `hfr`): stored, shown to
the camera's own viewers, and an earlier hint on a fogging dome and a shifted focus."""

from __future__ import annotations

import datetime as dt

import httpx
from fastapi import FastAPI
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from allskyhub_server.devices import health, star_size, stats
from allskyhub_server.models import Device, NightStat, SkySample
from tests.fake_device import FakeDevice
from tests.ws_helpers import paired_token

NOW = dt.datetime(2026, 10, 13, 23, 30, tzinfo=dt.UTC)
CLEAR = {dt.datetime(2026, 10, 13, h, tzinfo=dt.UTC): 5 for h in range(20, 24)}


def _blurred(
    hfr: float = 3.0, cloud: float | None = 0.1, n: int = 8
) -> list[tuple[dt.datetime, float | None, float | None]]:
    return [(NOW - dt.timedelta(minutes=5 * i), cloud, hfr) for i in range(n)]


def test_blur_hint() -> None:
    hint = health.blur_hint(_blurred(), 1.8, CLEAR, NOW)
    assert hint is not None
    assert hint.key == "dome_blur"
    assert hint.push is True
    assert "3,0 statt 1,8 px" in hint.text
    assert "Taupunkt" not in hint.text
    # Sharp as usual, one sharp frame, overcast, clouds forecast, no usual value, too few.
    assert health.blur_hint(_blurred(2.0), 1.8, CLEAR, NOW) is None
    assert health.blur_hint([*_blurred(), (NOW, 0.1, 1.9)], 1.8, CLEAR, NOW) is None
    assert health.blur_hint(_blurred(cloud=0.9), 1.8, CLEAR, NOW) is None
    assert health.blur_hint(_blurred(), 1.8, dict.fromkeys(CLEAR, 60), NOW) is None
    assert health.blur_hint(_blurred(), None, CLEAR, NOW) is None
    assert health.blur_hint(_blurred(n=5), 1.8, CLEAR, NOW) is None
    # Older measurements do not count; frames without stars do not either.
    old = [(t - dt.timedelta(hours=1), c, h) for t, c, h in _blurred()]
    assert health.blur_hint(old, 1.8, CLEAR, NOW) is None
    assert health.blur_hint([*_blurred(), (NOW, None, None)], 1.8, CLEAR, NOW) is not None


def test_blur_hint_names_the_dew() -> None:
    env = {"temperature_c": 3.1, "dewpoint_c": 2.3}
    hint = health.blur_hint(_blurred(), 1.8, CLEAR, NOW, env)
    assert hint is not None
    assert "nur 0,8 °C wärmer als der Taupunkt" in hint.text
    dry = health.blur_hint(_blurred(), 1.8, CLEAR, NOW, {"temperature_c": 9.0, "dewpoint_c": 2.0})
    assert dry is not None
    assert "Taupunkt" not in dry.text
    broken = {"temperature_c": 3.1, "dewpoint_c": 2.3, "error": "i2c"}
    sensor_broken = health.blur_hint(_blurred(), 1.8, CLEAR, NOW, broken)
    assert sensor_broken is not None
    assert "Taupunkt" not in sensor_broken.text


def test_usual_and_drift() -> None:
    assert star_size.usual_of([1.8, 1.9]) is None  # fewer than three clear nights
    assert star_size.usual_of([1.8, 2.0, 1.7]) == 1.8
    steady = [1.8] * 20
    assert star_size.drift(steady) is None
    shifted = [2.9] * 7 + [1.8] * 10
    assert star_size.drift(shifted) == (2.9, 1.8)
    assert star_size.drift([2.9] * 7 + [1.8] * 4) is None  # too little before
    assert star_size.drift([2.9] * 3 + [1.8] * 14) is None  # a few nights only
    hint = health.focus_hint(shifted)
    assert hint is not None
    assert hint.key == "focus"
    assert hint.push is False  # not urgent
    assert "2,9 statt 1,8 px" in hint.text


async def test_nights_live_tile_and_stats(app: FastAPI, client: httpx.AsyncClient) -> None:
    dev = FakeDevice()
    await paired_token(client, dev)
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        for day in range(1, 5):  # four clear nights in October, stars of 1.8 px
            night = f"202610{day:02d}"
            start = dt.datetime(2026, 10, day, 20, tzinfo=dt.UTC)
            for i in range(8):
                db.add(SkySample(device_id=dev.device_id, night_id=night,
                                 at=start + dt.timedelta(minutes=10 * i), cloud_cover=0.1,
                                 stars=200, hfr=1.8 if i else 9.0))  # fmt: skip
            # Overcast frames do not count for the night's star size.
            late = start + dt.timedelta(hours=3)
            db.add(SkySample(device_id=dev.device_id, night_id=night, at=late,
                             cloud_cover=0.9, hfr=7.0))  # fmt: skip
        await db.commit()
        await stats.refresh(db, dt.datetime(2026, 10, 13, 8, tzinfo=dt.UTC))
        sizes = [n.median_hfr for n in await db.scalars(select(NightStat))]
        assert sizes == [1.8] * 4
        assert await star_size.usual(db, dev.device_id, "20261005") == 1.8
        assert await star_size.usual(db, dev.device_id, "20261003") is None  # two before
        await db.execute(
            update(Device)
            .where(Device.id == dev.device_id)
            .values(
                last_status={
                    "sky": {
                        "at": "2026-10-05T21:00:00+00:00",
                        "night_id": "20261005",
                        "stars": 150,
                        "hfr": 2.94,
                    }
                },
                latest_image_at=dt.datetime.now(dt.UTC),
            )
        )
        await db.commit()
    live = (await client.get(f"/cameras/{dev.device_id}/live")).text
    assert "Sternschärfe" in live
    assert "2,9 px" in live
    assert "üblich 1,8 px" in live
    page = (await client.get(f"/cameras/{dev.device_id}/stats?year=2026")).text
    assert "Sternschärfe" in page
    assert "1,8 px" in page


async def test_statistics_without_star_sizes_have_no_column(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    dev = FakeDevice()
    await paired_token(client, dev)
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        db.add(NightStat(device_id=dev.device_id, night_id="20261001", clear_hours=4.0))
        await db.commit()
    page = (await client.get(f"/cameras/{dev.device_id}/stats?year=2026")).text
    assert "Statistik 2026" in page
    assert "Sternschärfe" not in page


class _Hour:
    def __init__(self, at: dt.datetime, clouds: int) -> None:
        self.at, self.clouds = at, clouds


class _Forecast:
    async def get(self, lat: float, lon: float) -> list[_Hour]:
        return [_Hour(at, clouds) for at, clouds in CLEAR.items()]


async def test_full_check_blur_and_focus(app: FastAPI, client: httpx.AsyncClient) -> None:
    dev = FakeDevice()
    await paired_token(client, dev)
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        # Seven blurred clear nights after ten sharp ones: the focus hint; and tonight the
        # stars grow to 3 px within the last 45 minutes: the dome hint.
        for day in range(1, 18):
            db.add(NightStat(device_id=dev.device_id, night_id=f"202609{day:02d}",
                             median_hfr=1.8 if day <= 10 else 2.9))  # fmt: skip
        for at, cloud, hfr in _blurred():
            db.add(SkySample(device_id=dev.device_id, night_id="20261013", at=at,
                             cloud_cover=cloud, hfr=hfr))  # fmt: skip
        await db.execute(
            update(Device)
            .where(Device.id == dev.device_id)
            .values(
                last_status={"settings": {"latitude": 50.04, "longitude": 10.39}},
                last_frame={"captured_at": NOW.isoformat(), "mode": "night"},
                last_seen_at=NOW,
            )
        )
        await db.commit()
        device = await db.get(Device, dev.device_id)
        assert device is not None
        found = await health.full_check(db, device, True, NOW, _Forecast())  # type: ignore[arg-type]
    by_key = {p.key: p for p in found}
    assert by_key["focus"].push is False
    # The usual value follows the last 14 clear nights (mostly the blurred ones): 2.9 px,
    # so 3 px tonight is not blurred against it.
    assert "dome_blur" not in by_key
    async with maker() as db:
        await db.execute(
            update(NightStat).where(NightStat.device_id == dev.device_id).values(median_hfr=1.8)
        )
        await db.commit()
        device = await db.get(Device, dev.device_id)
        assert device is not None
        found = await health.full_check(db, device, True, NOW, _Forecast())  # type: ignore[arg-type]
    keys = [p.key for p in found]
    assert "dome_blur" in keys
    assert "focus" not in keys
