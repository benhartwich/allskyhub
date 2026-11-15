"""Satellites 3D (draft D, devices/sat_globe.py, web/routes_cameras.satellites_3d)."""

from __future__ import annotations

import math

import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from allskyhub_server.devices import sat_globe
from allskyhub_server.models import Device
from tests.fake_device import FakeDevice
from tests.helpers import make_account
from tests.test_sky_layers import ISS_TOP, _iss  # pyright: ignore[reportPrivateUsage]
from tests.test_sky_overlay import _status  # pyright: ignore[reportPrivateUsage]
from tests.ws_helpers import paired_token

LAT, LON = 50.04, 10.39


def _norm(v: list[float]) -> float:
    return math.sqrt(sum(c * c for c in v))


def test_globe_with_the_iss_overhead() -> None:
    data = sat_globe.globe(_iss(), LAT, LON, ISS_TOP)
    assert abs(_norm(data["camera"]) - 1) < 1e-3
    assert abs(_norm(data["sun"]) - 1) < 1e-2
    [iss] = data["satellites"]
    assert iss["name"] == "Raumstation ISS"
    assert iss["up"] is True
    assert iss["sunlit"] is True
    assert 58.5 < iss["altitude"] < 59.5  # ISS_TOP: about 59° up
    assert 400 < iss["height_km"] < 440
    assert 1.05 < _norm(iss["now"]) < 1.08  # about 420 km up, in Earth radii
    # Its point lies over the camera's side of the Earth, within the circle it sees.
    dot = sum(a * b for a, b in zip(iss["now"], data["camera"], strict=True)) / _norm(iss["now"])
    assert math.degrees(math.acos(dot)) < 10
    assert len(iss["track"]) == 31  # ±45 min in 3 min steps


def test_globe_without_elements() -> None:
    data = sat_globe.globe(None, LAT, LON, ISS_TOP)
    assert data["satellites"] == []
    assert len(data["camera"]) == 3


async def test_page_and_data_for_the_cameras_viewers(
    app: FastAPI, client: httpx.AsyncClient, new_client: httpx.AsyncClient
) -> None:
    dev = FakeDevice()
    await paired_token(client, dev)
    page = await client.get(f"/cameras/{dev.device_id}/satelliten/3d")
    assert page.status_code == 200
    assert "kennt ihren Standort noch nicht" in page.text
    assert (await client.get(f"/cameras/{dev.device_id}/satelliten/3d.json")).status_code == 404
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        device = await db.get(Device, dev.device_id)
        assert device
        device.last_status = _status()
        await db.commit()
    page = await client.get(f"/cameras/{dev.device_id}/satelliten/3d")
    assert 'id="globe"' in page.text
    assert "sat_globe.js" in page.text
    data = (await client.get(f"/cameras/{dev.device_id}/satelliten/3d.json")).json()
    assert data["satellites"] == []  # no elements loaded in the tests
    assert len(data["camera"]) == 3
    await make_account(new_client)
    assert (await new_client.get(f"/cameras/{dev.device_id}/satelliten/3d")).status_code == 404
    assert (await new_client.get(f"/cameras/{dev.device_id}/satelliten/3d.json")).status_code == 404
