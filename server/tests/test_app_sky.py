# pyright: reportPrivateUsage=false
"""The sky at any place for the app without login, and a camera's measured sky series."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

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


def test_eclipses_and_planets_at_known_dates() -> None:
    from allskyhub_server.devices import sky_events

    u = dt.UTC
    # 12 Aug 2026: total in Spain, partial in Vienna, night in Sydney.
    new_moon = dt.datetime(2026, 8, 12, 17, 37, tzinfo=u)
    madrid = sky_events.solar_eclipse(new_moon, 40.4, -3.7)
    assert madrid is not None
    assert abs(madrid.at - dt.datetime(2026, 8, 12, 18, 30, tzinfo=u)) < dt.timedelta(minutes=20)
    assert sky_events.solar_eclipse(new_moon, 48.2, 16.4) is not None
    assert sky_events.solar_eclipse(new_moon, -33.9, 151.2) is None
    # A new moon without an eclipse.
    assert sky_events.solar_eclipse(dt.datetime(2026, 11, 9, 7, 2, tzinfo=u), 48.2, 16.4) is None
    # 3 Mar 2026: total lunar eclipse at 11:33 UTC, seen in America, not in Europe.
    full = dt.datetime(2026, 3, 3, 11, 38, tzinfo=u)
    la = sky_events.lunar_eclipse(full, 34.0, -118.2)
    assert la is not None
    assert la.depth == "umbral"
    assert sky_events.lunar_eclipse(full, 48.2, 16.4) is None
    assert sky_events.lunar_eclipse(dt.datetime(2026, 10, 26, 4, 12, tzinfo=u), 48.2, 16.4) is None
    # Late October 2026: Saturn in the evening sky, Jupiter in the morning.
    found = {
        w.key: w
        for w in sky_events.planets(
            dt.datetime(2026, 10, 28, 15, 50, tzinfo=u),
            dt.datetime(2026, 10, 29, 5, 40, tzinfo=u),
            48.1,
            14.4,
        )
    }
    assert found["saturn"].start.hour < 18
    assert found["jupiter"].start.hour < 3
    assert "venus" not in found or found["venus"].altitude < 30


async def test_sky_lists_tonights_planets(new_client: httpx.AsyncClient) -> None:
    sky = (await new_client.get("/api/v1/public/sky", params={"lat": 48.1, "lon": 14.4})).json()
    assert isinstance(sky["planets"], list)
    for p in sky["planets"]:
        assert p["key"] in ("mercury", "venus", "mars", "jupiter", "saturn")
        assert p["altitude"] >= 10


async def test_public_sky_view_and_object_info(
    app: FastAPI, new_client: httpx.AsyncClient, tmp_path: Path
) -> None:
    from allskyhub_server.devices.sky_photos import SkyPhotos

    asked: list[str] = []

    def commons(request: httpx.Request) -> httpx.Response:
        asked.append(str(request.url))
        return httpx.Response(200, content=b"\xff\xd8\xff\xe0jupiter")

    app.state.sky_photos = SkyPhotos(
        tmp_path, httpx.AsyncClient(transport=httpx.MockTransport(commons))
    )
    view = (
        await new_client.get("/api/v1/public/sky-view", params={"lat": 48.1, "lon": 14.4})
    ).json()
    keys = {o["key"] for o in view["objects"]}
    assert {"moon", "jupiter", "M31", "M42", "Ori", "UMa"} <= keys  # also below the horizon
    assert len([o for o in view["objects"] if o["kind"] == "constellation"]) == 88
    m31 = next(o for o in view["objects"] if o["key"] == "M31")
    assert (m31["label"], m31["kind"], m31["mag"]) == ("M31 Andromedagalaxie", "s", 3.4)
    assert view["meteors"] == []
    assert view["lines"]

    info = (await new_client.get("/api/v1/public/sky-objects/M31")).json()
    assert info["kind_label"] == "Spiralgalaxie"
    assert "2,5 Millionen Lichtjahre" in info["text"]
    photo = info["photo"]
    assert photo["url"] == "/api/v1/public/sky-photos/M31"
    assert photo["licence"] in ("Public domain", "CC0") or photo["licence"].startswith("CC BY")
    assert "SA" not in photo["licence"]
    assert photo["page"].startswith("https://commons.wikimedia.org/")

    en = await new_client.get("/api/v1/public/sky-objects/M2", headers={"Accept-Language": "en"})
    assert en.json()["kind_label"] == "Globular cluster"
    assert "binoculars" in en.json()["text"]
    ori = (await new_client.get("/api/v1/public/sky-objects/Ori")).json()
    assert ori["kind"] == "constellation"
    assert ori["photo"] is None
    assert (await new_client.get("/api/v1/public/sky-objects/M999")).status_code == 404
    assert (await new_client.get("/api/v1/public/sky-objects/..%2Fx")).status_code in (400, 404)

    first = await new_client.get("/api/v1/public/sky-photos/jupiter")
    assert first.content == b"\xff\xd8\xff\xe0jupiter"
    assert first.headers["content-type"] == "image/jpeg"
    await new_client.get("/api/v1/public/sky-photos/jupiter")
    assert len(asked) == 1  # kept on the hub's disk
    assert asked[0].startswith("https://thumb.wikimedia.org/")
    assert "?" not in asked[0]
    assert (await new_client.get("/api/v1/public/sky-photos/Ori")).status_code == 404


def test_every_photo_is_freely_licensed() -> None:
    """tools/sky_photos.py: public domain, CC0 or CC BY only, each with its credit."""
    from allskyhub_sky.describe import photos

    found = photos()
    assert len(found) > 100
    for key, p in found.items():
        licence = p["licence"].lower()
        assert licence in ("public domain", "cc0") or licence.startswith("cc by "), key
        assert not {"sa", "nc", "nd"} & set(licence.replace("-", " ").split()), key
        assert p["author"], key
        assert p["page"].startswith("https://commons.wikimedia.org/wiki/File:"), key
        assert "charles" not in p["file"].lower(), key


async def test_sky_photo_failures_are_remembered_and_capped(tmp_path: Path) -> None:
    from allskyhub_server.devices import sky_photos

    asked: list[str] = []

    def commons(request: httpx.Request) -> httpx.Response:
        asked.append(str(request.url))
        if "Saturn" in str(request.url):
            return httpx.Response(200, headers={"content-length": "lots"}, content=b"\xff\xd8")
        if "Jupiter" in str(request.url):
            return httpx.Response(200, content=b"\xff\xd8" + b"x" * (sky_photos.MAX_BYTES + 10))
        return httpx.Response(404)

    photos = sky_photos.SkyPhotos(
        tmp_path, httpx.AsyncClient(transport=httpx.MockTransport(commons))
    )
    assert await photos.get("jupiter") is None  # too big
    assert await photos.get("saturn") is None  # a broken Content-Length
    assert await photos.get("jupiter") is None
    assert await photos.get("saturn") is None
    assert len(asked) == 2  # each tried once within the hour
    assert not (tmp_path / "jupiter.img").exists()
