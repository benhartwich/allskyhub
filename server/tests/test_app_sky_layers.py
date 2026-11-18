"""The app's sky overlay and satellites (drafts 1 and 5 for the app, api/app.py)."""

from __future__ import annotations

import datetime as dt

import httpx
from fastapi import FastAPI

from allskyhub_server.devices import radiosondes, satellite_match
from allskyhub_server.models import Device
from tests.conftest import ISS_TLE
from tests.fake_device import FakeDevice
from tests.helpers import owner_email
from tests.test_app_api import _app_login  # pyright: ignore[reportPrivateUsage]
from tests.test_radiosondes import ANSWER, LAT, LON
from tests.test_sky_overlay import AT, LENS, _status  # pyright: ignore[reportPrivateUsage]
from tests.ws_helpers import paired_token

ISS_TOP = dt.datetime(2026, 10, 19, 4, 33, 8, tzinfo=dt.UTC)


async def _camera(app: FastAPI, client: httpx.AsyncClient, at: dt.datetime | None) -> str:
    dev = FakeDevice()
    await paired_token(client, dev)
    async with app.state.sessionmaker() as db:
        device = await db.get(Device, dev.device_id)
        assert device
        device.last_status = _status()
        if at is not None:
            device.last_frame = {"captured_at": at.isoformat()}
            device.latest_image_at = at
        await db.commit()
    return dev.device_id


async def test_sky_overlay(app: FastAPI, client: httpx.AsyncClient) -> None:
    device_id = await _camera(app, client, AT)
    auth = await _app_login(client, await owner_email(app, device_id))
    url = f"/api/v1/cameras/{device_id}/sky-overlay"
    assert (await client.get(url)).status_code == 401
    d = (await client.get(url, headers=auth)).json()
    assert (d["width"], d["height"]) == (LENS.width, LENS.height)
    assert d["at"] == AT.isoformat()
    assert d["lines"]
    assert "Vega" in {m["text"] for m in d["stars"]}
    assert [m["text"] for m in d["planets"]] == ["Saturn"]
    assert len(d["grid"]) == 10
    assert d["aircraft"] == []
    assert (await client.get("/api/v1/cameras/nope/sky-overlay", headers=auth)).status_code == 404


async def test_sky_overlay_without_an_image(app: FastAPI, client: httpx.AsyncClient) -> None:
    device_id = await _camera(app, client, None)
    auth = await _app_login(client, await owner_email(app, device_id))
    assert (await client.get(f"/api/v1/cameras/{device_id}/sky-overlay", headers=auth)).json() == {}


async def test_satellites(app: FastAPI, client: httpx.AsyncClient) -> None:
    device_id = await _camera(app, client, ISS_TOP)
    auth = await _app_login(client, await owner_email(app, device_id))
    url = f"/api/v1/cameras/{device_id}/satellites"
    # The test hub fetches no elements (satellites off) and has none on disk.
    empty = (await client.get(url, headers=auth)).json()
    assert empty == {"located": True, "loaded": False, "now": [], "passes": []}
    assert (await client.get(url, params={"f": "morgen"}, headers=auth)).status_code == 400

    class Loaded:
        async def get(self) -> satellite_match.Catalogue:
            return satellite_match.build(
                satellite_match.parse("ISS (ZARYA)\n" + "\n".join(ISS_TLE))
            )

        def loaded(self) -> satellite_match.Catalogue:
            return satellite_match.build(
                satellite_match.parse("ISS (ZARYA)\n" + "\n".join(ISS_TLE))
            )

    app.state.bright_satellites = Loaded()
    for f in ("jetzt", "oben", "nacht"):
        answer = (await client.get(url, params={"f": f}, headers=auth)).json()
        assert answer["located"] is True
        assert answer["loaded"] is True
        assert isinstance(answer["now"], list)
        assert isinstance(answer["passes"], list)
    # The overlay draws the ISS at the top of its pass (the image's time).
    d = (await client.get(f"/api/v1/cameras/{device_id}/sky-overlay", headers=auth)).json()
    assert [m["text"] for m in d["satellites"]] == ["Raumstation ISS"]


async def test_balloons_hfr_usual_and_globe_for_app_150(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    """App 1.5.0: weather balloons in the overlay, the usual star size with the camera and
    the satellite globe; all only for the camera's own viewers."""
    device_id = await _camera(app, client, AT)
    auth = await _app_login(client, await owner_email(app, device_id))
    url = f"/api/v1/cameras/{device_id}/sky-overlay"
    d = (await client.get(url, headers=auth)).json()
    assert d["balloons"] == []
    assert d["balloons_credit"] is None
    app.state.radiosondes.put(LAT, LON, radiosondes.parse(ANSWER))
    d = (await client.get(url, headers=auth)).json()
    [balloon] = d["balloons"]
    assert balloon["text"].startswith("Wetterballon · 30,1 km")
    assert balloon["trail"].startswith("M")
    assert d["balloons_credit"] == radiosondes.CREDIT

    camera = (await client.get(f"/api/v1/cameras/{device_id}", headers=auth)).json()
    assert camera["hfr_usual"] is None  # no star sizes yet

    globe = f"/api/v1/cameras/{device_id}/satellites/globe"
    assert (await client.get(globe)).status_code == 401
    answer = await client.get(globe, headers=auth)
    assert answer.headers["cache-control"] == "no-store"
    data = answer.json()
    assert data["loaded"] is False  # no elements in the tests
    assert data["satellites"] == []
    assert len(data["camera"]) == 3
    assert len(data["sun"]) == 3
    nope = await client.get("/api/v1/cameras/nope/satellites/globe", headers=auth)
    assert nope.status_code == 404
