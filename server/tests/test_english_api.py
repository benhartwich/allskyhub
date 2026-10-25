"""The app API in English when the app asks for it (i18n.py, Benjamin, 2026-10-25)."""

from __future__ import annotations

import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from allskyhub_server.i18n import prefers_english
from allskyhub_server.models import Device
from tests.test_app_manage import _camera as _app_camera  # pyright: ignore[reportPrivateUsage]
from tests.test_app_overview import _camera  # pyright: ignore[reportPrivateUsage]

EN = {"Accept-Language": "en"}


def test_accept_language_is_read_loosely() -> None:
    assert prefers_english("en")
    assert prefers_english("en-GB,en;q=0.9")
    assert prefers_english("fr,en;q=0.5")
    assert not prefers_english(None)
    assert not prefers_english("de-AT,de;q=0.9,en;q=0.8")
    assert not prefers_english("*")
    assert not prefers_english("en;q=0.5,de;q=0.5")


async def test_overview_and_targets_in_english(app: FastAPI, client: httpx.AsyncClient) -> None:
    device_id, auth = await _camera(app, client)
    url = f"/api/v1/cameras/{device_id}/overview"
    german = (await client.get(url, headers=auth)).json()
    english = (await client.get(url, headers={**auth, **EN})).json()
    assert german["health"][0]["text"].startswith("Die Kamera ist offline")
    assert english["health"][0]["text"].startswith("The camera is offline")
    assert english["tonight"]["moon_phase"] != german["tonight"]["moon_phase"]

    targets = (await client.get("/api/v1/targets", headers={**auth, **EN})).json()
    labels = {t["key"]: t["label"] for t in targets}
    assert labels["M42"] == "M42 Orion Nebula"
    assert labels["mercury"] == "Mercury"
    # A request without the header stays German.
    targets = (await client.get("/api/v1/targets", headers=auth)).json()
    assert {t["key"]: t["label"] for t in targets}["M42"] == "M42 Orionnebel"


async def test_sky_view_in_english(app: FastAPI, client: httpx.AsyncClient) -> None:
    device_id, auth = await _app_camera(client)
    url = f"/api/v1/cameras/{device_id}/sky-view"
    r = await client.get(url, headers={**auth, **EN})
    assert r.status_code == 409
    assert "location" in r.json()["detail"]
    r = await client.get(url, headers={**auth, "Accept-Language": "de-AT,de;q=0.9,en;q=0.8"})
    assert "Standort" in r.json()["detail"]
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        device = await db.get(Device, device_id)
        assert device
        device.last_status = {"settings": {"latitude": 50.04, "longitude": 10.39}}
        await db.commit()
    english = (await client.get(url, headers={**auth, **EN})).json()
    german = (await client.get(url, headers=auth)).json()
    constellations = {o["label"] for o in english["objects"] if o["kind"] == "constellation"}
    assert constellations & {"Pegasus", "Andromeda", "Cassiopeia", "Ursa Major"}
    assert not constellations & {"Großer Bär", "Kassiopeia", "Fuhrmann"}
    assert "Moon" in {o["label"] for o in english["objects"]} or not any(
        o["kind"] == "moon" for o in german["objects"]
    )
