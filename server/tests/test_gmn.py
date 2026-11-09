"""Meteors the Global Meteor Network measured too (fireball package D, devices/gmn.py)."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from allskyhub_server.devices import gmn, meteor_pairs
from allskyhub_server.devices import triangulation as t
from allskyhub_server.models import Device, EventRecord, GmnMeteor
from tests.fake_device import FakeDevice
from tests.helpers import claim, make_account

# GMN's three header lines as in traj_summary_yesterday.txt of 2026-11-04.
# GMN ends its lines with "\n\r"; parse reads any line ending.
HEADER = "\n".join(
    line
    for line in (Path(__file__).parent / "data" / "gmn_summary_header.txt").read_text().splitlines()
    if line.strip()
)
NORDHEIM = (48.30, 4.29)
SUEDTAL = (47.80, 3.04)
BEG, END = (47.95, 3.80, 95.0), (47.80, 3.95, 30.0)
AT = dt.datetime(2026, 11, 5, 21, 0, 12, 345000, tzinfo=dt.UTC)


def _summary(rows: list[dict[str, str]]) -> str:
    """A summary file with GMN's header; every cell "0" unless given."""
    names = [c.strip() for c in HEADER.splitlines()[0].lstrip("#").split(";")]
    seen: dict[str, int] = {}
    keys: list[str] = []
    for name in names:
        keys.append(f"{name}#{seen.get(name, 0)}")
        seen[name] = seen.get(name, 0) + 1
    lines = ["# Summary generated for the test", *HEADER.splitlines()]
    for row in rows:
        lines.append("; ".join(row.get(k, "0") for k in keys))
    return "\n".join(lines) + "\n"


def _row(when: dt.datetime = AT, cells: dict[str, str] | None = None) -> dict[str, str]:
    return {
        "Unique trajectory#0": "20261105210012_abcde",
        "Beginning#1": when.strftime("%Y-%m-%d %H:%M:%S.%f"),
        "IAU#1": "STA",
        "LatBeg#0": str(BEG[0]), "LonBeg#0": str(BEG[1]), "HtBeg#0": str(BEG[2]),
        "LatEnd#0": str(END[0]), "LonEnd#0": str(END[1]), "HtEnd#0": str(END[2]),
        "Vinit#0": "28.4", "Vavg#0": "27.9", "Duration#0": "2.1", "Peak#0": "-6.2",
        "a#0": "2.11", "e#0": "0.83", "i#0": "5.2", "q#0": "0.36", "Num#0": "5",
        **(cells or {}),
    }  # fmt: skip


def test_parse_reads_the_columns_by_name() -> None:
    found = gmn.parse(_summary([_row(), _row(cells={"IAU#1": "...", "Vinit#0": "nan"}), _row()]))
    assert len(found) == 2  # the one without a speed is skipped
    first = found[0]
    assert first.at == AT
    assert first.shower == "STA"
    assert first.v_init_kms == pytest.approx(28.4)
    assert first.beg == pytest.approx(BEG)
    assert first.end == pytest.approx(END)
    assert first.stations == 5
    assert (first.a_au, first.e, first.i_deg) == pytest.approx((2.11, 0.83, 5.2))
    assert gmn.parse(_summary([_row(cells={"IAU#1": "..."})]))[0].shower is None
    assert gmn.parse("no header\n1;2;3\n") == []


def _seen(place: tuple[float, float], top: t.Vec, bottom: t.Vec) -> t.Sighting:
    return t.Sighting(place[0], place[1], 0.3, t.look(place[0], place[1], 0.3, top),
                      t.look(place[0], place[1], 0.3, bottom))  # fmt: skip


def test_offset_against_the_path_seen_from_the_camera() -> None:
    trajectory = gmn.parse(_summary([_row()]))[0]
    top, bottom = t.ecef(*BEG), t.ecef(*END)
    exact = gmn.offset_deg(_seen(NORDHEIM, top, bottom), trajectory)
    assert exact is not None
    assert exact < 0.01
    # Our camera saw only the middle part: still the same meteor.
    middle = t.ecef(47.875, 3.875, 62.0)
    part = gmn.offset_deg(_seen(NORDHEIM, top, middle), trajectory)
    assert part is not None
    assert part < 0.5
    # Another meteor elsewhere in the sky.
    other = gmn.offset_deg(
        _seen(NORDHEIM, t.ecef(48.9, 5.5, 90.0), t.ecef(49.0, 5.8, 70.0)), trajectory
    )
    assert other is None or other > gmn.MATCH_DEG


async def _camera(
    client: httpx.AsyncClient, maker: async_sessionmaker[AsyncSession], place: tuple[float, float]
) -> str:
    dev = FakeDevice()
    reg = await dev.register(client)
    assert reg.pairing_code
    await claim(client, reg.pairing_code, f"Kamera {place[0]}")
    (az1, alt1), (az2, alt2) = (t.look(place[0], place[1], 0.3, t.ecef(*p)) for p in (BEG, END))
    async with maker() as db:
        await db.execute(
            update(Device)
            .where(Device.id == dev.device_id)
            .values(last_status={"settings": {"latitude": place[0], "longitude": place[1],
                                              "elevation_m": 300}})
        )  # fmt: skip
        db.add(EventRecord(device_id=dev.device_id, event_id="meteor-20261105T210000Z",
                           night_id="20261105", kind="meteor",
                           start=dt.datetime(2026, 11, 5, 21, 0, tzinfo=dt.UTC),
                           end=dt.datetime(2026, 11, 5, 21, 0, 30, tzinfo=dt.UTC),
                           confidence=0.9, has_image=False,
                           data={"start_azimuth_deg": az1, "start_altitude_deg": alt1,
                                 "end_azimuth_deg": az2, "end_altitude_deg": alt2,
                                 "track_ordered": False, "peak": 1.0}))  # fmt: skip
        await db.commit()
    return dev.device_id


async def test_matched_and_shown(app: FastAPI, client: httpx.AsyncClient) -> None:
    """Our meteor within GMN's time and path: kept, shown on the event page and on the
    fireball page, with GMN's credit."""
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    await make_account(client)
    nordheim = await _camera(client, maker, NORDHEIM)
    salzburg = await _camera(client, maker, SUEDTAL)
    later = AT + dt.timedelta(minutes=5)  # outside both exposures
    trajectories = gmn.parse(_summary([_row(), _row(later, {"Unique trajectory#0": "x"})]))
    async with maker() as db:
        assert await gmn.match(db, trajectories) == 2
        assert await gmn.match(db, trajectories) == 2  # again: updated, not doubled
        rows = (await db.scalars(select(GmnMeteor))).all()
    assert len(rows) == 2
    assert {r.gmn_id for r in rows} == {"20261105210012_abcde"}

    page = (await client.get(f"/cameras/{nordheim}/events/20261105/meteor-20261105T210000Z")).text
    assert "Vom Global Meteor Network vermessen" in page
    assert "28,4 km/s" in page
    assert "Südliche Tauriden" in page
    assert "CC BY 4.0" in page

    assert await meteor_pairs.pair_event(maker, salzburg, "meteor-20261105T210000Z") == 1
    page = (await client.get(f"/cameras/{nordheim}/events/20261105/meteor-20261105T210000Z")).text
    pair_id = page.split('href="/feuerkugeln/')[1].split('"')[0]
    fireball_page = (await client.get(f"/feuerkugeln/{pair_id}")).text
    assert "Vom Global Meteor Network vermessen" in fireball_page
    assert "28,4 km/s" in fireball_page
