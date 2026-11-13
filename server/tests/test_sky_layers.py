"""More layers over the live image (draft 1, devices/sky_layers.py): stars, grid, moon and
planets, satellites, each with its switch."""

from __future__ import annotations

import datetime as dt
from typing import Any

import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from allskyhub_server.devices import satellite_match, sky_layers
from allskyhub_server.models import Device
from tests.conftest import ISS_TLE
from tests.fake_device import FakeDevice
from tests.test_sky_overlay import AT, LENS, _status  # pyright: ignore[reportPrivateUsage]
from tests.ws_helpers import paired_token

ISS_TOP = dt.datetime(2026, 10, 19, 4, 33, 8, tzinfo=dt.UTC)  # 59° up, sunlit


def _iss() -> satellite_match.Catalogue:
    return satellite_match.build(satellite_match.parse("ISS (ZARYA)\n" + "\n".join(ISS_TLE)))


def test_layers_need_a_lens_and_a_place() -> None:
    assert sky_layers.build(_status(None), AT) is None
    no_place: dict[str, Any] = {**_status(), "settings": {}}
    assert sky_layers.build(no_place, AT) is None


def test_stars_grid_and_planets() -> None:
    lay = sky_layers.build(_status(), AT)
    assert lay is not None
    stars = {m.text: m for m in lay.stars}
    assert {"Vega", "Capella", "Deneb", "Altair"} <= set(stars)  # an October evening
    assert "Sirius" not in stars  # not risen yet
    # North up and mirrored, like a camera looking up: east (Capella) left, west (Vega) right.
    assert stars["Capella"].x < LENS.cx < stars["Vega"].x
    assert [m.text for m in lay.planets] == ["Saturn"]  # opposition early in October 2026
    assert len(lay.grid) == 2 + 8  # two altitude rings, eight spokes
    assert [m.text for m in lay.grid_labels] == ["30°", "60°"]
    assert [m.text for m in lay.cardinals] == ["N", "O", "S", "W"]
    assert lay.satellites == []


def test_satellites_with_their_last_minute() -> None:
    lay = sky_layers.build(_status(), ISS_TOP, _iss())
    assert lay is not None
    [iss] = lay.satellites
    assert iss.text == "Raumstation ISS"
    assert iss.trail.startswith("M")
    assert iss.trail.count("L") >= 5
    assert iss.y > LENS.cy  # south of the zenith
    # Below the horizon at the evening of AT.
    evening = sky_layers.build(_status(), AT, _iss())
    assert evening is not None
    assert evening.satellites == []


async def test_live_and_public_pages_have_the_switches(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    dev = FakeDevice()
    await paired_token(client, dev)
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        device = await db.get(Device, dev.device_id)
        assert device
        device.last_status = _status()
        device.last_frame = {"captured_at": AT.isoformat()}
        device.latest_image_at = AT
        await db.commit()
    live = (await client.get(f"/cameras/{dev.device_id}/live")).text
    assert 'class="layer-overlay"' in live
    for kind in ("stars", "grid", "planets", "sats"):
        assert f'data-overlay-toggle="{kind}"' in live
    assert ">Vega<" in live
    assert ">Saturn<" in live
