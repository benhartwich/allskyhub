"""Notifications by e-mail (roadmap #15) and the evening clear-night notice (C1)."""

from __future__ import annotations

import datetime as dt

import httpx
from fastapi import FastAPI
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from allskyhub_server.devices.tonight import Hour
from allskyhub_server.mail.send import MemoryMailer
from allskyhub_server.models import Device, NightStat, User
from allskyhub_server.push.notify import Notifier
from tests.fake_device import FakeDevice
from tests.helpers import csrf_from
from tests.test_alarms import _night  # pyright: ignore[reportPrivateUsage]
from tests.ws_helpers import paired_token

NIGHT = "20261012"


class FakeForecast:
    def __init__(self, clouds: int) -> None:
        self.clouds = clouds

    async def get(self, lat: float, lon: float) -> list[Hour]:
        start = dt.datetime(2026, 10, 12, 12, tzinfo=dt.UTC)
        return [
            Hour(start + dt.timedelta(hours=i), self.clouds, self.clouds, 0, 0, 0)
            for i in range(30)
        ]


async def _setup(
    app: FastAPI, client: httpx.AsyncClient, **user: bool
) -> tuple[str, MemoryMailer, Notifier]:
    """A camera near Nordheim whose owner has no phone: e-mail only."""
    dev = FakeDevice()
    await paired_token(client, dev)
    mailer = MemoryMailer()
    notifier = Notifier(app.state.sessionmaker, None)
    notifier.mailer = mailer
    notifier.base_url = "https://hub.example.org"
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        device = await db.get(Device, dev.device_id)
        assert device
        device.last_status = {
            "settings": {"latitude": 50.04, "longitude": 10.39, "timezone": "Europe/Vienna"}
        }
        await db.execute(update(User).where(User.id == device.owner_id).values(**user))
        await db.commit()
    return dev.device_id, mailer, notifier


async def test_morning_summary_by_mail_without_push(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    device_id, mailer, notifier = await _setup(
        app, client, notify_summary=True, notify_by_mail=True
    )
    await _night(app, device_id)
    await notifier.check_morning(dt.datetime(2026, 10, 13, 6, 30, tzinfo=dt.UTC))
    assert [m.subject for m in mailer.sent] == ["allskyhub: Die Nacht über Garten"]
    body = mailer.sent[0].body
    assert body.splitlines()[2].startswith("2 Meteore")
    assert f"https://hub.example.org/cameras/{device_id}/nights/{NIGHT}" in body
    assert "https://hub.example.org/account" in body


async def test_no_mail_unless_wanted(app: FastAPI, client: httpx.AsyncClient) -> None:
    device_id, mailer, notifier = await _setup(app, client, notify_summary=True)
    await _night(app, device_id)
    await notifier.check_morning(dt.datetime(2026, 10, 13, 6, 30, tzinfo=dt.UTC))
    assert mailer.sent == []


async def test_clear_night_notice_once_in_the_evening(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    _, mailer, notifier = await _setup(app, client, notify_tonight=True, notify_by_mail=True)
    notifier.forecast = FakeForecast(clouds=5)  # pyright: ignore[reportAttributeAccessIssue]
    await notifier.check_tonight(dt.datetime(2026, 10, 12, 12, tzinfo=dt.UTC))  # 14:00 local
    assert mailer.sent == []
    evening = dt.datetime(2026, 10, 12, 15, 45, tzinfo=dt.UTC)  # shortly before sunset
    await notifier.check_tonight(evening)
    await notifier.check_tonight(evening + dt.timedelta(minutes=5))
    assert [m.subject for m in mailer.sent] == ["allskyhub: Heute Nacht klar über Garten"]
    assert "Klar und dunkel von" in mailer.sent[0].body
    assert "Mond:" in mailer.sent[0].body


async def test_no_notice_for_a_cloudy_night(app: FastAPI, client: httpx.AsyncClient) -> None:
    device_id, mailer, notifier = await _setup(
        app, client, notify_tonight=True, notify_by_mail=True
    )
    notifier.forecast = FakeForecast(clouds=90)  # pyright: ignore[reportAttributeAccessIssue]
    await notifier.check_tonight(dt.datetime(2026, 10, 12, 15, 45, tzinfo=dt.UTC))
    assert mailer.sent == []
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        device = await db.get(Device, device_id)
        assert device
        assert device.tonight_night == NIGHT  # decided: no second look this night


async def test_account_form_without_push(app: FastAPI, client: httpx.AsyncClient) -> None:
    """Mail on, push off: the form shows the mail box and keeps the push-only setting."""
    dev = FakeDevice()
    await paired_token(client, dev)
    app.state.mailer = MemoryMailer()
    page = await client.get("/account")
    assert "Auch per E-Mail" in page.text
    assert "Bei neuen Ereignissen" not in page.text
    r = await client.post(
        "/account/notifications",
        data={
            "notify_summary": "true",
            "notify_tonight": "true",
            "notify_by_mail": "true",
            "csrf_token": csrf_from(page.text),
        },
    )
    assert r.status_code == 200
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        device = await db.get(Device, dev.device_id)
        assert device
        user = await db.get(User, device.owner_id)
        assert user
        assert (user.notify_summary, user.notify_tonight, user.notify_by_mail) == (True,) * 3
        assert user.notify_events is False


async def test_onboarding_mail_when_a_paired_camera_stays_silent(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    """2026-11-25: paired, nothing sent for two hours: one mail with what to do, in German
    and English (the account's language is German by default); a waiting camera that
    reports its camera error gets that error."""
    device_id, mailer, notifier = await _setup(app, client, notify_by_mail=False)
    notifier.reply_to = "support@example.org"
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        device = await db.get(Device, device_id)
        assert device
        device.last_status = None
        paired = device.paired_at
        assert paired is not None
        device.last_seen_at = paired + dt.timedelta(hours=2, minutes=30)  # online, waiting
        await db.commit()
    await notifier.check_onboarding(paired + dt.timedelta(hours=1))
    assert mailer.sent == []  # too early
    await notifier.check_onboarding(paired + dt.timedelta(hours=3))
    assert len(mailer.sent) == 1
    mail = mailer.sent[0]
    assert "ein Schritt fehlt noch" in mail.subject
    assert "one step left" in mail.subject
    assert "Standort" in mail.body
    assert "location" in mail.body
    assert mail.reply_to == "support@example.org"
    await notifier.check_onboarding(paired + dt.timedelta(hours=5))
    assert len(mailer.sent) == 1  # once

    # Waiting for a broken camera (SPEC §6.3 waiting), English account: English only.
    device_id, mailer, notifier = await _setup(app, client, notify_by_mail=False)
    async with maker() as db:
        device = await db.get(Device, device_id)
        assert device
        device.last_status = {
            "waiting": {"reason": "camera", "since": "x", "message": "no camera found"}
        }
        await db.execute(update(User).where(User.id == device.owner_id).values(lang="en"))
        paired = device.paired_at
        assert paired is not None
        await db.commit()
    await notifier.check_onboarding(paired + dt.timedelta(hours=3))
    assert len(mailer.sent) == 1
    assert "no camera found" in mailer.sent[0].body
    assert "Hallo" not in mailer.sent[0].body

    # A camera that reports without waiting (frames on their way) gets none.
    device_id, mailer, notifier = await _setup(app, client, notify_by_mail=False)
    async with maker() as db:
        device = await db.get(Device, device_id)
        assert device
        paired = device.paired_at
        assert paired is not None
    await notifier.check_onboarding(paired + dt.timedelta(hours=3))
    assert mailer.sent == []


async def test_checkin_mail_a_week_after_the_first_clear_night(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    """2026-11-25: a week after the first clear night, how it went and a request for
    feedback; replies go to the contact address."""
    device_id, mailer, notifier = await _setup(app, client, notify_by_mail=False)
    notifier.reply_to = "support@example.org"
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        db.add_all(
            [
                NightStat(device_id=device_id, night_id="20261012", clear_hours=0.5,
                          meteors=1, showers={}, aurora=0, storms=0),
                NightStat(device_id=device_id, night_id="20261013", clear_hours=6.0,
                          best_sqm=21.35, meteors=4, showers={}, aurora=0, storms=0),
            ]
        )  # fmt: skip
        await db.commit()
    await notifier.check_checkin(dt.datetime(2026, 10, 18, 12, tzinfo=dt.UTC))
    assert mailer.sent == []  # not a week yet
    await notifier.check_checkin(dt.datetime(2026, 10, 21, 12, tzinfo=dt.UTC))
    assert len(mailer.sent) == 1
    body = mailer.sent[0].body
    assert "2 Nächte, davon 1 klar, 5 Meteore, dunkelster Himmel 21,35" in body
    assert "2 nights, 1 of them clear, 5 meteors, darkest sky 21.35" in body
    assert f"https://hub.example.org/cameras/{device_id}" in body
    assert mailer.sent[0].reply_to == "support@example.org"
    await notifier.check_checkin(dt.datetime(2026, 10, 22, 12, tzinfo=dt.UTC))
    assert len(mailer.sent) == 1  # once
    # A camera whose first clear night is long past (running before these mails) gets none.
    device_id, mailer, notifier = await _setup(app, client, notify_by_mail=False)
    async with maker() as db:
        db.add(NightStat(device_id=device_id, night_id="20261013", clear_hours=6.0,
                         meteors=0, showers={}, aurora=0, storms=0))  # fmt: skip
        await db.commit()
    await notifier.check_checkin(dt.datetime(2026, 11, 25, 12, tzinfo=dt.UTC))
    assert mailer.sent == []


async def test_onboarding_mail_window_and_offline_text(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    """Review of #526: pairings older than two weeks get no greeting; a camera not seen
    since its pairing hears about power and Wi-Fi, not about its location."""
    device_id, mailer, notifier = await _setup(app, client, notify_by_mail=False)
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        device = await db.get(Device, device_id)
        assert device
        device.last_status = None
        paired = device.paired_at
        assert paired is not None
        device.last_seen_at = paired
        await db.commit()
    await notifier.check_onboarding(paired + dt.timedelta(days=20))
    assert mailer.sent == []  # given up long ago
    await notifier.check_onboarding(paired + dt.timedelta(hours=5))
    assert len(mailer.sent) == 1
    body = mailer.sent[0].body
    assert "WLAN" in body
    assert "Wi-Fi" in body
    assert "Sie ist verbunden" not in body
