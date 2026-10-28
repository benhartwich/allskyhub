# pyright: reportPrivateUsage=false
"""The sky at any place for the app without login, and a camera's measured sky series."""

from __future__ import annotations

import datetime as dt

import httpx
from fastapi import FastAPI
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from allskyhub_server.api import app_sky
from allskyhub_server.models import Device, SkySample
from tests.test_public_page import _add_archive, _public, _save

ALL_SECTIONS = {f"section_{s}": "on" for s in ("live", "loop", "archive", "events", "sky")}


async def test_three_nights_aurora_and_rounding(new_client: httpx.AsyncClient) -> None:
    r = await new_client.get(
        "/api/v1/public/sky", params={"lat": 48.1234, "lon": 14.4567, "tz": "Europe/Vienna"}
    )
    assert r.status_code == 200, r.text
    sky = r.json()
    assert (sky["lat"], sky["lon"], sky["tz"]) == (48.1, 14.5, "Europe/Vienna")
    assert len(sky["nights"]) == 3
    evenings = [dt.date.fromisoformat(n["evening"]) for n in sky["nights"]]
    assert evenings[1] - evenings[0] == dt.timedelta(days=1)
    tonight = sky["nights"][0]
    assert tonight["verdict"] in ("good", "fair", "cloudy", "unknown", "bright")
    assert tonight["dusk"]
    assert tonight["hours"]
    assert {"low", "mid", "high", "rain", "wind_kmh", "moon_up", "good"} <= tonight["hours"][
        0
    ].keys()
    assert sky["aurora"]["needed"] == 7.7  # (65 - 48.1) / 2.2
    assert sky["nearby"] is None
    bad = await new_client.get("/api/v1/public/sky", params={"lat": 91, "lon": 0})
    assert bad.status_code == 400  # the hub answers validation errors with 400


async def test_nearby_camera_measures_the_sky(
    app: FastAPI, client: httpx.AsyncClient, new_client: httpx.AsyncClient
) -> None:
    device_id, slug = await _public(app, client, new_client)
    await _add_archive(app, device_id)  # at 50.036, 10.39
    await _save(client, device_id, title="Sternwarte", show_coordinates="on", **ALL_SECTIONS)
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        await db.execute(
            update(Device)
            .where(Device.id == device_id)
            .values(
                latest_image_at=dt.datetime.now(dt.UTC),
                last_status={
                    "settings": {"latitude": 50.036, "longitude": 10.39},
                    "sky": {"cloud_cover": 0.2, "sqm_mag": 21.3, "stars": 300},
                },
            )
        )
        now = dt.datetime.now(dt.UTC)
        for i in range(5):
            db.add(SkySample(device_id=device_id, night_id="20261027",
                             at=now - dt.timedelta(minutes=10 * i), cloud_cover=0.1 * i,
                             sqm_mag=21.0, stars=100))  # fmt: skip
        await db.commit()

    sky = (await new_client.get("/api/v1/public/sky", params={"lat": 50.2, "lon": 10.5})).json()
    near = sky["nearby"]
    assert (near["slug"], near["title"], near["cloud_cover"], near["sqm_mag"]) == (
        slug, "Sternwarte", 0.2, 21.3,
    )  # fmt: skip
    assert 10 < near["distance_km"] < 30
    far = (await new_client.get("/api/v1/public/sky", params={"lat": 52.5, "lon": 13.4})).json()
    assert far["nearby"] is None

    series = (await new_client.get(f"/api/v1/public/cameras/{slug}/sky-series")).json()
    assert series["hours"] == 24
    assert len(series["points"]) == 5
    assert series["points"][0]["at"] < series["points"][-1]["at"]  # oldest first
    assert set(series["method"]) == {"cloud_cover", "sqm_mag", "stars"}
    r = await new_client.get(f"/api/v1/public/cameras/{slug}/sky-series", params={"hours": 5})
    assert r.status_code == 422  # our own check

    await _save(client, device_id, title="Sternwarte", show_coordinates="on", section_live="on")
    assert (await new_client.get(f"/api/v1/public/cameras/{slug}/sky-series")).status_code == 404
    sky = (await new_client.get("/api/v1/public/sky", params={"lat": 50.2, "lon": 10.5})).json()
    assert sky["nearby"] is None  # sky values not public


async def test_calendar_lists_what_comes(new_client: httpx.AsyncClient) -> None:
    r = await new_client.get("/api/v1/public/calendar", params={"lat": 48.1, "lon": 14.4})
    items = r.json()["items"]
    kinds = {i["kind"] for i in items}
    assert {"new_moon", "full_moon"} <= kinds  # two months always hold both
    assert [i["at"] for i in items] == sorted(i["at"] for i in items)


def test_moon_phases_hit_known_dates() -> None:
    # Full moon 2026-11-24 14:53 UTC, new moon 2026-11-09 07:02 UTC.
    found = dict(app_sky.moon_phases(dt.datetime(2026, 11, 1, tzinfo=dt.UTC), 30))
    assert abs(
        found["full_moon"] - dt.datetime(2026, 11, 24, 14, 53, tzinfo=dt.UTC)
    ) < dt.timedelta(hours=3)
    assert abs(found["new_moon"] - dt.datetime(2026, 11, 9, 7, 2, tzinfo=dt.UTC)) < dt.timedelta(
        hours=3
    )


def test_distance() -> None:
    assert round(app_sky.distance_km((48.2, 16.4), (48.3, 14.3))) == 156  # Vienna – Linz


async def test_privacy_policy_names_the_sky_tab(new_client: httpx.AsyncClient) -> None:
    page = (await new_client.get("/datenschutz")).text
    assert "Himmel in der App" in page
    assert "Open-Meteo" in page
