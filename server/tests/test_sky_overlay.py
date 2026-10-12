"""Constellations over the image (roadmap H1, SPEC §4.8 lens mapping)."""

from __future__ import annotations

import datetime as dt
from typing import Any

import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from allskyhub_protocol import Lens
from allskyhub_server.devices import sky_overlay as so
from allskyhub_server.models import Device
from tests.fake_device import FakeDevice
from tests.ws_helpers import paired_token

AT = dt.datetime(2026, 10, 15, 21, tzinfo=dt.UTC)
LENS = Lens(cx=600, cy=600, a1=560, a3=-30, tilt_east_deg=0, tilt_north_deg=0,
            width=1200, height=1200)  # fmt: skip


def _status(lens: Lens | None = LENS) -> dict[str, Any]:
    orientation: dict[str, Any] = {
        "north_deg": 0.0, "mirrored": True, "solved_at": AT.isoformat(), "stars": 40,
        "rms_deg": 0.2,
    }  # fmt: skip
    if lens is not None:
        orientation["lens"] = lens.model_dump()
    return {"settings": {"latitude": 48.14, "longitude": 14.39}, "orientation": orientation}


def test_lens_mapping() -> None:
    assert so.project(90, 0, LENS, 0, True) == (600, 600)  # zenith in the centre
    x, y = so.project(0, 0, LENS, 0, True)  # north at the horizon: straight up
    assert (round(x, 6), round(y, 6)) == (600, 600 - (560 - 30))
    x, _ = so.project(0, 90, LENS, 0, True)  # mirrored: east to the left
    assert x < 600
    x, _ = so.project(0, 90, LENS, 0, False)
    assert x > 600


def test_tilt_moves_the_zenith_toward_its_lean() -> None:
    alt, _ = so.tilt(90, 0, 2.0, 0.0)
    assert abs(alt - 88.0) < 1e-9


def test_polaris_stands_at_the_latitude() -> None:
    lst = so.sidereal_deg(AT, 14.39)
    alt, az = so.alt_az(*so.precess(37.95, 89.26, AT), lst, 48.14)
    assert abs(alt - 48.14) < 1.0
    assert az < 2 or az > 358


def test_overlay_needs_a_usable_lens() -> None:
    assert so.build(_status(None), AT) is None
    bad = LENS.model_copy(update={"width": 0})
    assert so.build(_status(bad), AT) is None
    overlay = so.build(_status(), AT)
    assert overlay is not None
    assert overlay.paths
    assert {label.text for label in overlay.cardinals} == {"N", "O", "S", "W"}
    assert "Pegasus" in {label.text for label in overlay.names}  # high in an October night


async def test_public_page_draws_constellations(app: FastAPI, client: httpx.AsyncClient) -> None:
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
    page = (await client.get(f"/cameras/{dev.device_id}")).text
    assert 'class="sky-overlay"' in page
    assert "data-overlay-toggle" in page
