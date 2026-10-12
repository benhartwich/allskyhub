"""Public sky page sections and the owner's profile (roadmap #9, astronomy.garden parity)."""

from __future__ import annotations

import datetime as dt
import re

import httpx
from fastapi import FastAPI
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from allskyhub_protocol import FrameVariant
from allskyhub_server.devices import space_weather
from allskyhub_server.devices.images import ImageStore
from allskyhub_server.models import Device, EventRecord, Frame
from tests.helpers import home_csrf
from tests.test_public import JPEG, _camera, _toggle  # pyright: ignore[reportPrivateUsage]


async def _public(
    app: FastAPI, client: httpx.AsyncClient, new_client: httpx.AsyncClient
) -> tuple[str, str]:
    device_id = await _camera(app, client, new_client)
    await _toggle(client, device_id, True)
    page = (await client.get(f"/cameras/{device_id}")).text
    match = re.search(r'href="/sky/([A-Za-z0-9_-]+)"', page)
    assert match
    return device_id, match[1]


async def _save(client: httpx.AsyncClient, device_id: str, **fields: str) -> httpx.Response:
    data = {"csrf_token": await home_csrf(client), **fields}
    return await client.post(f"/cameras/{device_id}/public/edit", data=data)


async def _add_archive(app: FastAPI, device_id: str) -> None:
    store: ImageStore = app.state.images
    now = dt.datetime.now(dt.UTC)
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        for i in range(3):
            name = f"image-{i}.jpg"
            path = store.frame_path(device_id, "20261012", name, FrameVariant.THUMB)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(JPEG)
            db.add(Frame(device_id=device_id, night_id="20261012", name=name,
                         captured_at=now - dt.timedelta(minutes=10 * i), mode="night",
                         exposure_us=1, gain=1, mean=1, sun_elevation=-20,
                         has_thumb=True))  # fmt: skip
        for event_id, kind, label in [
            ("meteor-1", "meteor", None),
            ("meteor-2", "meteor", "false_positive"),
            ("sky-1", "sky_quality", None),
        ]:
            path = store.event_path(device_id, "20261012", event_id, FrameVariant.THUMB)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(JPEG)
            db.add(EventRecord(device_id=device_id, event_id=event_id, night_id="20261012",
                               kind=kind, start=now, end=now, confidence=0.9, has_image=True,
                               has_thumb=True, label=label,
                               data={"shower": "Orionids", "shower_match": "radiant"}))  # fmt: skip
        await db.execute(
            update(Device)
            .where(Device.id == device_id)
            .values(last_status={"settings": {"latitude": 48.13601, "longitude": 14.38951}})
        )
        await db.commit()


async def test_profile_texts_and_coordinates(
    app: FastAPI, client: httpx.AsyncClient, new_client: httpx.AsyncClient
) -> None:
    device_id, slug = await _public(app, client, new_client)
    await _add_archive(app, device_id)
    assert "Seite gestalten" in (await client.get(f"/cameras/{device_id}")).text
    r = await _save(
        client, device_id, title="Allsky Lanzenberg", location_label="Hofkirchen",
        camera="ZWO ASI678MC", lens="2,5 mm", computer="Pi 4", owner="Ben",
        notes="<script>x</script>", section_live="on", section_aurora="on",
    )  # fmt: skip
    assert r.status_code == 303
    page = (await new_client.get(f"/sky/{slug}")).text
    for text in ("Allsky Lanzenberg", "Hofkirchen", "ZWO ASI678MC", "2,5 mm", "Pi 4", "Ben"):
        assert text in page
    assert "<script>x</script>" not in page  # escaped
    assert "48.1°" not in page  # coordinates only on request
    assert 'property="og:image"' in page

    await _save(client, device_id, show_coordinates="on")
    page = (await new_client.get(f"/sky/{slug}")).text
    assert "48.1° N, 14.4° O" in page
    assert "48.136" not in page

    too_long = await _save(client, device_id, title="x" * 101)
    assert too_long.status_code == 400


async def test_sections_and_public_files(
    app: FastAPI, client: httpx.AsyncClient, new_client: httpx.AsyncClient
) -> None:
    device_id, slug = await _public(app, client, new_client)
    await _add_archive(app, device_id)
    page = (await new_client.get(f"/sky/{slug}")).text  # defaults: everything on
    assert "Die letzte Stunde" in page
    assert f"/sky/{slug}/frames/20261012/image-0.jpg" in page
    assert "Polarlicht-Aussichten" in page
    assert "kp-high" in page  # 5.3 tomorrow
    assert "ab etwa Kp 8" in page  # 48° N
    assert f"/sky/{slug}/events/20261012/meteor-1.jpg" in page
    assert "meteor-2" not in page  # marked as false positive
    assert "sky-1" not in page  # measurements are not shown as events

    thumb = await new_client.get(f"/sky/{slug}/frames/20261012/image-0.jpg")
    assert thumb.content == JPEG
    assert (await new_client.get(f"/sky/{slug}/frames/20261012/nope.jpg")).status_code == 404
    assert (await new_client.get(f"/sky/{slug}/events/20261012/meteor-1.jpg")).content == JPEG
    for hidden in ("meteor-2", "sky-1"):
        r = await new_client.get(f"/sky/{slug}/events/20261012/{hidden}.jpg")
        assert r.status_code == 404

    night = (await new_client.get(f"/sky/{slug}/nights/20261012")).text
    assert "1 Meteor: 1 Orioniden" in night
    assert f"/sky/{slug}/frames/20261012/image-1.jpg" in night

    await _save(client, device_id)  # every section off
    page = (await new_client.get(f"/sky/{slug}")).text
    for gone in ("Die letzte Stunde", "Polarlicht-Aussichten", "Zuletzt am Himmel", "Archiv"):
        assert gone not in page
    assert (await new_client.get(f"/sky/{slug}/frames/20261012/image-0.jpg")).status_code == 404
    assert (await new_client.get(f"/sky/{slug}/events/20261012/meteor-1.jpg")).status_code == 404
    assert (await new_client.get(f"/sky/{slug}/nights/20261012")).status_code == 404


async def test_profile_needs_the_owner(
    app: FastAPI, client: httpx.AsyncClient, new_client: httpx.AsyncClient
) -> None:
    device_id, _ = await _public(app, client, new_client)
    from tests.helpers import make_account

    await make_account(new_client)
    assert (await new_client.get(f"/cameras/{device_id}/public/edit")).status_code == 404
    r = await new_client.post(
        f"/cameras/{device_id}/public/edit",
        data={"csrf_token": await home_csrf(new_client), "title": "fremd"},
    )
    assert r.status_code == 404


def test_kp_parse() -> None:
    today = dt.date(2026, 10, 12)
    rows = [
        {"time_tag": "2026-10-11T21:00:00", "kp": 6, "observed": "observed"},
        {"time_tag": "2026-10-12T00:00:00", "kp": 2.33, "observed": "observed"},
        {"time_tag": "2026-10-12T03:00:00", "kp": 3.67, "observed": "estimated"},
        {"time_tag": "2026-10-13T03:00:00", "kp": 4.0, "observed": "predicted"},
        {"time_tag": "bad", "kp": 9, "observed": "predicted"},
    ]
    forecast = space_weather.parse(rows, today)
    assert forecast.now == 3.67
    assert [(d.day.day, d.kp, d.predicted) for d in forecast.days] == [
        (12, 3.67, False),
        (13, 4.0, True),
    ]
    old = [
        ["time_tag", "kp", "observed", "noaa_scale"],
        ["2026-10-12 00:00:00", "2.00", "observed", None],
    ]
    assert space_weather.parse(old, today).now == 2.0
    assert space_weather.parse({"nope": 1}, today).days == []
    assert round(space_weather.needed_kp(48.1)) == 8
    assert space_weather.needed_kp(70) == 0
