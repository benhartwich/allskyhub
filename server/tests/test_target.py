"""„Ziel frei?“ (roadmap H4)."""

from __future__ import annotations

import datetime as dt
from typing import Any

import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from allskyhub_server.devices import target
from allskyhub_server.devices.tonight import Hour
from allskyhub_server.models import Device
from tests.test_app_manage import _camera  # pyright: ignore[reportPrivateUsage]

EVENING = dt.datetime(2026, 10, 15, 20, 10, tzinfo=dt.UTC)  # 22:10 in Vienna, moon nearly new


def _status(cloud: float | None = 0.05, at: dt.datetime = EVENING) -> dict[str, Any]:
    return {
        "settings": {"latitude": 50.04, "longitude": 10.39, "timezone": "Europe/Vienna"},
        "sky": {"at": at.isoformat(), "night_id": "20261015", "cloud_cover": cloud},
    }


def _forecast(cloudy_from: int = 99) -> list[Hour]:
    start = EVENING.replace(minute=0)
    return [
        Hour(start + dt.timedelta(hours=i), 90 if i >= cloudy_from else 10, 0, 0, 0, None)
        for i in range(24)
    ]


def test_clear_now_and_the_best_hours() -> None:
    m31 = target.find("m31")
    assert m31 is not None
    found = target.plan(m31, _status(), _forecast(cloudy_from=4), EVENING)
    assert found is not None
    assert found.now.verdict == "clear"
    assert found.now.alt > 60  # high in an October evening
    assert found.summary() == "M31 Andromedagalaxie ist jetzt frei."
    assert len(found.hours) == target.HOURS
    assert [h.verdict for h in found.hours[:4]] == ["clear", "clear", "clear", "cloudy"]
    best = found.best
    assert best is not None
    assert found.clock(best[0]) == "23:00"
    assert found.clock(best[1]) == "02:00"


def test_the_camera_sees_clouds() -> None:
    m31 = target.find("M31")
    assert m31 is not None
    found = target.plan(m31, _status(cloud=0.9), _forecast(), EVENING)
    assert found is not None
    assert found.now.verdict == "cloudy"
    assert found.summary().startswith("M31 Andromedagalaxie: am besten von 23:00 bis ")
    # An old camera value does not count; the forecast stands in.
    stale = target.plan(m31, _status(0.9, EVENING - dt.timedelta(hours=1)), _forecast(), EVENING)
    assert stale is not None
    assert stale.now.cloud_cover is None
    assert stale.now.verdict == "clear"


def test_cloud_map_cells() -> None:
    """SPEC §6.3: index = 8·band + sector; on a limit the higher sector or band."""
    cells: list[float | None] = [i / 100 for i in range(24)]
    assert target.map_cell(cells, 20, 0) == 0.0  # band 0, north
    assert target.map_cell(cells, 20, 359) == 0.0
    assert target.map_cell(cells, 20, 22.5) == 0.01  # on the limit: the next sector
    assert target.map_cell(cells, 40, 90) == 0.10  # band 1, east
    assert target.map_cell(cells, 89, 180) == 0.20  # band 2, south
    assert target.map_cell(cells, 14.9, 0) is None  # below 15°
    cells[4] = None
    assert target.map_cell(cells, 30, 180) is None  # not judgeable
    assert target.map_cell(cells[:23], 30, 0) is None
    assert target.map_cell(None, 30, 0) is None


def test_the_cell_in_the_objects_direction() -> None:
    m31 = target.find("M31")
    assert m31 is not None
    clear = target.plan(m31, _status(cloud=0.05), _forecast(), EVENING)
    assert clear is not None
    status = _status(cloud=0.05)
    cloud_map: list[float | None] = [0.0] * 24
    band = 2 if clear.now.alt >= 65 else 1 if clear.now.alt >= 40 else 0
    cloud_map[8 * band + round(clear.now.az / 45) % 8] = 0.9
    status["sky"]["cloud_map"] = cloud_map
    found = target.plan(m31, status, _forecast(), EVENING)
    assert found is not None
    assert (found.now.cloud_cover, found.now.source) == (0.9, "in Richtung des Objekts")
    assert found.now.verdict == "cloudy"  # the whole sky would say clear


def test_low_and_bright() -> None:
    m42 = target.find("M42")
    jupiter = target.find("jupiter")
    assert m42 is not None
    assert jupiter is not None
    early = EVENING.replace(hour=17)  # 19:00: Orion still below the horizon
    found = target.plan(m42, _status(at=early), _forecast(), early)
    assert found is not None
    assert found.now.verdict == "low"
    noon = EVENING.replace(hour=10)
    day = target.plan(m42, _status(at=noon), [], noon)
    assert day is not None
    assert {h.verdict for h in day.hours[:3]} <= {"low", "bright"}
    assert target.plan(jupiter, {"settings": {}}, [], EVENING) is None


async def test_page_and_app_api(app: FastAPI, client: httpx.AsyncClient) -> None:
    device_id, auth = await _camera(client)
    page = (await client.get(f"/cameras/{device_id}/ziel")).text
    assert "M31 Andromedagalaxie" in page
    assert "Jupiter" in page
    r = await client.get(f"/cameras/{device_id}/ziel", params={"objekt": "M31"})
    assert "Standort fehlt" in r.text
    r = await client.get(f"/api/v1/cameras/{device_id}/target?key=M31", headers=auth)
    assert r.status_code == 409

    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        device = await db.get(Device, device_id)
        assert device is not None
        device.last_status = _status(at=dt.datetime.now(dt.UTC))
        await db.commit()
    r = await client.get(f"/cameras/{device_id}/ziel", params={"objekt": "M31"})
    assert "Die nächsten Stunden" in r.text
    assert (await client.get(f"/cameras/{device_id}/ziel?objekt=X99")).text.count(
        "Dieses Objekt kenne ich nicht."
    ) == 1

    r = await client.get(f"/api/v1/cameras/{device_id}/target?key=m31", headers=auth)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["label"] == "M31 Andromedagalaxie"
    assert len(body["hours"]) == target.HOURS
    assert body["verdict_text"] in target.VERDICT_TEXT.values()
    r = await client.get(f"/api/v1/cameras/{device_id}/target?key=X99", headers=auth)
    assert r.status_code == 404
    listed = (await client.get("/api/v1/targets", headers=auth)).json()
    assert listed[0]["kind"] == "planet"
    assert len(listed) == 117
