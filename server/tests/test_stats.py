"""Statistics per month and year (roadmap C7)."""

from __future__ import annotations

import datetime as dt

import httpx
from fastapi import FastAPI
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from allskyhub_server.devices import stats
from allskyhub_server.models import EventRecord, NightStat, SkySample
from tests.fake_device import FakeDevice
from tests.helpers import home_csrf, make_account
from tests.ws_helpers import paired_token

NOW = dt.datetime(2026, 10, 13, 8, tzinfo=dt.UTC)


async def _data(app: FastAPI, device_id: str) -> None:
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        nights = (
            ("20260912", dt.datetime(2026, 9, 12, 20, tzinfo=dt.UTC), 18, 21.0),
            ("20261012", dt.datetime(2026, 10, 12, 20, tzinfo=dt.UTC), 6, 20.5),
        )
        for night, start, clear_until, sqm in nights:
            for i in range(19):  # 3 h, every 10 min
                db.add(
                    SkySample(
                        device_id=device_id,
                        night_id=night,
                        at=start + dt.timedelta(minutes=10 * i),
                        cloud_cover=0.1 if i < clear_until else 0.9,
                        sqm_mag=sqm,
                        stars=1,
                    )
                )
        for i, data in enumerate([{"shower": "Orionids", "shower_match": "radiant"},
                                  {"shower": None, "shower_match": "radiant"}, {}]):  # fmt: skip
            t = dt.datetime(2026, 10, 12, 22, i, tzinfo=dt.UTC)
            db.add(EventRecord(device_id=device_id, event_id=f"meteor-{i}", night_id="20261012",
                               kind="meteor", start=t, end=t, confidence=0.9, has_image=False,
                               data=data))  # fmt: skip
        await db.commit()


async def test_nights_are_kept_in_numbers(app: FastAPI, client: httpx.AsyncClient) -> None:
    dev = FakeDevice()
    await paired_token(client, dev)
    await _data(app, dev.device_id)
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        assert await stats.refresh(db, NOW) == 2
        assert await stats.refresh(db, NOW) == 1  # only the recent night again
        # The samples and events expire; the numbers stay.
        await db.execute(delete(SkySample))
        await db.execute(delete(EventRecord))
        await db.commit()
        assert await stats.refresh(db, NOW) == 0
        rows = {n.night_id: n for n in await db.scalars(select(NightStat))}
        assert rows["20260912"].clear_hours == 3.0
        assert rows["20261012"].clear_hours == 1.0
        assert rows["20261012"].meteors == 3
        assert rows["20261012"].showers == {"Orioniden": 1, "sporadisch": 1}
        year = await stats.year(db, dev.device_id, 2026)
    assert year.total.nights == 2
    assert year.total.clear_nights == 1  # only September had 2 h or more
    assert year.total.best_sqm == 21.0
    assert year.months[9].meteors == 3
    assert year.months[8].label == "Sep"
    assert year.showers == {"Orioniden": 1, "sporadisch": 1}
    assert year.years == [2026]

    page = (await client.get(f"/cameras/{dev.device_id}/stats?year=2026")).text
    assert "Statistik 2026" in page
    assert "Orioniden" in page
    assert "21,00" in page
    assert "Statistik" in (await client.get(f"/cameras/{dev.device_id}")).text

    other = httpx.AsyncClient(transport=client._transport, base_url="http://testserver")  # pyright: ignore[reportPrivateUsage]
    async with other:
        await make_account(other)
        assert (await other.get(f"/cameras/{dev.device_id}/stats")).status_code == 404

    await client.post(
        f"/cameras/{dev.device_id}/remove", data={"csrf_token": await home_csrf(client)}
    )
    async with maker() as db:
        assert list(await db.scalars(select(NightStat))) == []


async def test_empty_statistics(client: httpx.AsyncClient) -> None:
    dev = FakeDevice()
    await paired_token(client, dev)
    page = (await client.get(f"/cameras/{dev.device_id}/stats?year=1800")).text
    assert "Noch keine Nächte" in page
