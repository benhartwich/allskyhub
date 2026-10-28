# pyright: reportPrivateUsage=false
"""Sightings by eye (stage 5): fireballs matched with the cameras, aurora with photos that
show only once approved, flags, alerts at a chosen place and their push messages."""

from __future__ import annotations

import asyncio
import datetime as dt
import io
import uuid
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from PIL import Image
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from allskyhub_server.devices import astro
from allskyhub_server.devices.tonight import ForecastCache
from allskyhub_server.mail.send import MemoryMailer
from allskyhub_server.models import AuroraReport, Device, EventRecord, PushToken, User
from allskyhub_server.push.notify import Notifier
from allskyhub_server.settings import Settings
from tests.helpers import PASSWORD, csrf_from, make_account
from tests.test_public_page import _public, _save
from tests.test_push import FakeSender
from tests.test_reports import _reporter, signup_on  # noqa: F401  # pyright: ignore[reportUnusedImport]

ALL_SECTIONS = {f"section_{s}": "on" for s in ("live", "loop", "archive", "events", "sky")}


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _jpeg_with_gps() -> bytes:
    img = Image.new("RGB", (3000, 2000), (20, 120, 60))
    exif = Image.Exif()
    exif[0x8825] = {2: (48, 7, 0)}  # GPSInfo: latitude
    exif[0x010F] = "PhoneMaker"
    exif[0x0112] = 6  # Orientation: the phone was held upright
    out = io.BytesIO()
    img.save(out, "JPEG", exif=exif)
    return out.getvalue()


async def test_fireball_matched_with_cameras_and_others(
    app: FastAPI,
    client: httpx.AsyncClient,
    new_client: httpx.AsyncClient,
    signup_on: MemoryMailer,  # noqa: F811
) -> None:
    device_id, slug = await _public(app, client, new_client)
    await _save(client, device_id, title="Sternwarte", show_coordinates="on", **ALL_SECTIONS)
    seen = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=30)
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        await db.execute(
            update(Device)
            .where(Device.id == device_id)
            .values(last_status={"settings": {"latitude": 48.2, "longitude": 14.5}})
        )
        db.add(EventRecord(device_id=device_id, event_id="m1", night_id="20261028",
                           kind="meteor", start=seen + dt.timedelta(seconds=40),
                           end=seen + dt.timedelta(seconds=42), confidence=0.9,
                           has_image=True, has_thumb=True, data={}))  # fmt: skip
        await db.commit()

    one = await _reporter(new_client, signup_on, "fb1@example.org")
    two = await _reporter(new_client, signup_on, "fb2@example.org")
    body = {"seen_at": seen.isoformat(), "lat": 48.31, "lon": 14.29, "brightness": "venus",
            "start": [40, 315], "end": [12, 250], "details": ["green", "nonsense"]}  # fmt: skip
    first = await new_client.post("/api/v1/sightings/fireball", json=body, headers=_auth(one))
    assert first.status_code == 200, first.text
    second = await new_client.post(
        "/api/v1/sightings/fireball",
        json={**body, "lat": 48.5, "lon": 13.4},
        headers=_auth(two),
    )
    out = second.json()
    [camera] = out["cameras"]
    assert camera["title"] == "Sternwarte"
    assert camera["image"] == f"/sky/{slug}/events/20261028/m1.jpg?v=1"
    assert out["others_km"] == [100.0]  # Linz – Passau, about 70 km, in 50 km steps
    again = await new_client.get(
        f"/api/v1/sightings/fireball/{first.json()['id']}", headers=_auth(two)
    )
    assert again.status_code == 404  # only for its reporter

    old = {**body, "seen_at": (seen - dt.timedelta(days=2)).isoformat()}
    r = await new_client.post("/api/v1/sightings/fireball", json=old, headers=_auth(one))
    assert r.status_code == 422
    assert (await new_client.post("/api/v1/sightings/fireball", json=body)).status_code == 401


async def test_aurora_photo_waits_for_approval(
    app: FastAPI,
    client: httpx.AsyncClient,
    new_client: httpx.AsyncClient,
    settings: Settings,
    signup_on: MemoryMailer,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = await make_account(client)
    monkeypatch.setattr(settings, "admin_emails", admin)
    app.state.notifier.admins = settings.admins
    token = await _reporter(new_client, signup_on, "au@example.org")
    form = {"lat": "48.31", "lon": "14.29", "naked_eye": "true", "details": "rays,green,x",
            "author": "  Ben   H. "}  # fmt: skip
    photo = {"photo": ("a.jpg", _jpeg_with_gps(), "image/jpeg")}
    no_licence = await new_client.post(
        "/api/v1/sightings/aurora", data=form, files=photo, headers=_auth(token)
    )
    assert no_licence.status_code == 422
    r = await new_client.post(
        "/api/v1/sightings/aurora",
        data={**form, "licence": "true"},
        files=photo,
        headers=_auth(token),
    )
    assert r.status_code == 200, r.text
    report_id = r.json()["id"]
    assert r.json()["photo_state"] == "pending"
    stored = settings.data_dir / "sightings" / f"{report_id}.jpg"
    with Image.open(stored) as img:
        assert img.size == (1365, 2048)  # turned upright by its EXIF note, then dropped
        assert not img.getexif()  # no GPS, no maker, no orientation
    too_soon = await new_client.post("/api/v1/sightings/aurora", data=form, headers=_auth(token))
    assert too_soon.status_code == 429

    listed = (
        await new_client.get("/api/v1/sightings/aurora", params={"lat": 48, "lon": 14})
    ).json()
    assert listed[0]["details"] == ["rays", "green"]
    assert listed[0]["photo"] is None  # not yet approved
    photo_url = f"/api/v1/sightings/aurora/{report_id}/photo.jpg"
    assert (await new_client.get(photo_url)).status_code == 404

    page = await client.get("/admin")
    assert f"/admin/aurora-photos/{report_id}.jpg" in page.text
    assert (await client.get(f"/admin/aurora-photos/{report_id}.jpg")).status_code == 200
    r = await client.post(
        f"/admin/aurora-photos/{report_id}",
        data={"csrf_token": csrf_from(page.text), "action": "approve"},
    )
    assert "Foto freigegeben" in r.text
    listed = (
        await new_client.get("/api/v1/sightings/aurora", params={"lat": 48, "lon": 14})
    ).json()
    assert listed[0]["photo"] == photo_url
    assert listed[0]["author"] == "Ben H."
    assert (await new_client.get(photo_url)).headers["content-type"] == "image/jpeg"
    assert (await new_client.get(photo_url, params={"thumb": 1})).status_code == 200

    # Three accounts flag it: back to the admins; one account counts once.
    for n in range(3):
        flagger = await _reporter(new_client, signup_on, f"flag{n}@example.org")
        for _ in range(2):
            await new_client.post(
                f"/api/v1/sightings/aurora/{report_id}/flag", headers=_auth(flagger)
            )
        state = (await new_client.get(photo_url)).status_code
        assert state == (404 if n == 2 else 200)


async def test_alerts_and_aurora_push(
    app: FastAPI,
    new_client: httpx.AsyncClient,
    signup_on: MemoryMailer,  # noqa: F811
) -> None:
    sender = FakeSender()
    notifier = Notifier(app.state.sessionmaker, sender)
    app.state.notifier = notifier
    watcher = await _reporter(new_client, signup_on, "watch@example.org")
    r = await new_client.put(
        "/api/v1/account/alerts",
        json={"lat": 48.234, "lon": 14.567, "radius_km": 300, "aurora": True},
        headers=_auth(watcher),
    )
    assert r.json() == {"lat": 48.2, "lon": 14.6, "radius_km": 300, "aurora": True,
                        "fireball": False, "clear_night": False, "tz": None}  # fmt: skip
    bad = await new_client.put(
        "/api/v1/account/alerts", json={"radius_km": 7}, headers=_auth(watcher)
    )
    assert bad.status_code == 422
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        user = await db.scalar(select(User).where(User.email == "watch@example.org"))
        assert user
        db.add(
            PushToken(
                user_id=user.id,
                token="phone-1",  # noqa: S106
                platform="android",
            )
        )
        await db.commit()

    seer = await _reporter(new_client, signup_on, "seer@example.org")
    form = {"lat": "48.9", "lon": "15.1", "naked_eye": "true"}
    report = await new_client.post("/api/v1/sightings/aurora", data=form, headers=_auth(seer))
    for _ in range(100):  # the hub tells the others in a task of its own
        if sender.sent:
            break
        await asyncio.sleep(0.01)
    [(tokens, message)] = sender.sent
    assert tokens == ["phone-1"]
    assert message.title == "Polarlicht gesichtet"
    assert "1 Meldung im Umkreis" in message.body
    assert "die nächste bis 100 km entfernt" in message.body  # about 80 km, 25 km steps
    assert "mit bloßem Auge" in message.body
    assert message.data["kind"] == "aurora_near"
    assert message.data["path"] == "/sightings/aurora"
    await notifier.aurora_near(uuid.UUID(report.json()["id"]))
    assert len(sender.sent) == 1  # quiet for two hours

    # Switching every alert off forgets the place.
    off = await new_client.put(
        "/api/v1/account/alerts", json={"aurora": False}, headers=_auth(watcher)
    )
    assert (off.json()["lat"], off.json()["lon"]) == (None, None)


async def test_clear_night_at_the_alert_place(
    app: FastAPI,
    new_client: httpx.AsyncClient,
    signup_on: MemoryMailer,  # noqa: F811
) -> None:
    sender = FakeSender()
    notifier = Notifier(app.state.sessionmaker, sender)
    notifier.forecast = app.state.forecast
    token = await _reporter(new_client, signup_on, "clear@example.org")
    r = await new_client.put(
        "/api/v1/account/alerts",
        json={"lat": 48.1, "lon": 14.4, "clear_night": True, "tz": "Europe/Vienna"},
        headers=_auth(token),
    )
    assert r.json()["tz"] == "Europe/Vienna"
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        user = await db.scalar(select(User).where(User.email == "clear@example.org"))
        assert user
        db.add(PushToken(user_id=user.id, token="phone-2", platform="ios"))  # noqa: S106
        await db.commit()
    # A clear forecast around the new moon of 9 November 2026: no moon in the way.
    today = dt.datetime(2026, 11, 9, 11, tzinfo=dt.UTC)

    def clear(request: httpx.Request) -> httpx.Response:
        start = int(dt.datetime(2026, 11, 9, tzinfo=dt.UTC).timestamp())
        times = [start + 3600 * i for i in range(72)]
        return httpx.Response(200, json={"hourly": {"time": times, "cloud_cover": [5] * 72}})

    notifier.forecast = ForecastCache(httpx.AsyncClient(transport=httpx.MockTransport(clear)))
    night = astro.night_after(today, 48.1, 14.4)
    assert night.sunset
    evening = night.sunset - dt.timedelta(minutes=20)
    await notifier.check_clear_here(evening)
    await notifier.check_clear_here(evening + dt.timedelta(minutes=5))
    [(tokens, message)] = sender.sent  # once per night
    assert tokens == ["phone-2"]
    assert message.title == "Heute Nacht klar bei dir"
    assert message.data["kind"] == "clear_night"


async def test_sightings_expire_with_their_photos(
    app: FastAPI,
    new_client: httpx.AsyncClient,
    settings: Settings,
    signup_on: MemoryMailer,  # noqa: F811
) -> None:
    from allskyhub_server import maintenance

    token = await _reporter(new_client, signup_on, "old@example.org")
    r = await new_client.post(
        "/api/v1/sightings/aurora",
        data={"lat": "48", "lon": "14", "naked_eye": "false", "licence": "true"},
        files={"photo": ("a.png", _png(), "image/png")},
        headers=_auth(token),
    )
    report_id = r.json()["id"]
    path = settings.data_dir / "sightings" / f"{report_id}.jpg"
    assert path.is_file()
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        later = dt.datetime.now(dt.UTC) + dt.timedelta(days=366)
        await maintenance.purge(db, app.state.images, settings, now=later)
        assert await db.scalar(select(AuroraReport)) is None
    assert not path.exists()


def _png() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (40, 30), (0, 200, 100)).save(out, "PNG")
    return out.getvalue()


async def test_deleting_the_account_deletes_its_photos(
    app: FastAPI,
    new_client: httpx.AsyncClient,
    settings: Settings,
    signup_on: MemoryMailer,  # noqa: F811
) -> None:
    token = await _reporter(new_client, signup_on, "gone@example.org")
    r = await new_client.post(
        "/api/v1/sightings/aurora",
        data={"lat": "48", "lon": "14", "naked_eye": "true", "licence": "true"},
        files={"photo": ("a.png", _png(), "image/png")},
        headers=_auth(token),
    )
    folder = settings.data_dir / "sightings"
    report_id = r.json()["id"]
    assert (folder / f"{report_id}.jpg").is_file()
    deleted = await new_client.post(
        "/api/v1/account/delete", json={"password": PASSWORD}, headers=_auth(token)
    )
    assert deleted.status_code < 300, deleted.text
    assert not (folder / f"{report_id}.jpg").exists()
    assert not (folder / f"{report_id}-thumb.jpg").exists()


async def test_no_huge_or_unknown_pictures(tmp_path: Path) -> None:
    from allskyhub_server.devices import sightings

    with pytest.raises(sightings.PhotoError):
        sightings.store_photo(tmp_path, uuid.uuid4(), b"GIF89a not a photo")
    big = io.BytesIO()
    Image.new("L", (8000, 7000)).save(big, "PNG")
    with pytest.raises(sightings.PhotoError):
        sightings.store_photo(tmp_path, uuid.uuid4(), big.getvalue())
