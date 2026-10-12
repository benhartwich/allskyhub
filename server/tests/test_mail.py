"""E-mail (roadmap #15): "Passwort vergessen", long outages, the message itself."""

from __future__ import annotations

import datetime as dt
import re

import httpx
from fastapi import FastAPI
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from allskyhub_server.mail.send import Mail, MemoryMailer, SmtpMailer
from allskyhub_server.models import Device, User
from allskyhub_server.push.notify import Notifier
from allskyhub_server.settings import Settings
from tests.fake_device import FakeDevice
from tests.helpers import PASSWORD, csrf_from, make_account
from tests.ws_helpers import paired_token

NEW_PASSWORD = "ein-ganz-neues-passwort"  # noqa: S105 - a test value


async def _ask(c: httpx.AsyncClient, email: str) -> httpx.Response:
    page = await c.get("/password/forgot")
    return await c.post(
        "/password/forgot", data={"email": email, "csrf_token": csrf_from(page.text)}
    )


async def test_password_reset(
    app: FastAPI, client: httpx.AsyncClient, new_client: httpx.AsyncClient
) -> None:
    mailer: MemoryMailer = app.state.mailer
    email = await make_account(client)  # client stays signed in
    assert "Passwort vergessen?" in (await new_client.get("/login")).text

    unknown = await _ask(new_client, "nobody@example.org")
    known = await _ask(new_client, email.upper())
    assert (
        unknown.text.count("ist jetzt eine E-Mail")
        == known.text.count("ist jetzt eine E-Mail")
        == 1
    )
    assert len(mailer.sent) == 1
    mail = mailer.sent[0]
    assert mail.to == email
    link = re.search(r"http://testserver(/password/reset\?token=\S+)", mail.body)
    assert link

    form = await new_client.get(link[1])
    assert form.status_code == 200
    token = re.search(r'name="token" value="([^"]+)"', form.text)
    assert token
    csrf = csrf_from(form.text)
    form_data = {"token": token[1], "password": NEW_PASSWORD, "csrf_token": csrf}
    bad = await new_client.post("/password/reset", data={**form_data, "password2": "anders"})
    assert bad.status_code == 400
    ok = await new_client.post("/password/reset", data={**form_data, "password2": NEW_PASSWORD})
    assert ok.status_code == 303
    assert (await client.get("/account")).status_code == 303  # the old session is gone
    assert (await new_client.get(link[1])).status_code == 404  # single use
    login = await client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 401


async def test_reset_is_rate_limited(app: FastAPI, new_client: httpx.AsyncClient) -> None:
    for _ in range(3):
        assert (await _ask(new_client, "flood@example.org")).status_code == 200
    assert (await _ask(new_client, "flood@example.org")).status_code == 429


async def test_off_without_smtp(app: FastAPI, client: httpx.AsyncClient) -> None:
    app.state.mailer = SmtpMailer(Settings(database_url="postgresql://x@y/z"))
    assert (await client.get("/password/forgot")).status_code == 404
    assert "Passwort vergessen?" not in (await client.get("/login")).text


async def test_long_outage_by_mail(app: FastAPI, client: httpx.AsyncClient) -> None:
    dev = FakeDevice()
    await paired_token(client, dev)
    mailer = MemoryMailer()
    notifier = Notifier(app.state.sessionmaker, None)
    notifier.mailer = mailer
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        device = await db.get(Device, dev.device_id)
        assert device
        await db.execute(update(User).where(User.id == device.owner_id).values(notify_offline=True))
        await db.execute(update(Device).where(Device.id == dev.device_id).values(
            last_seen_at=dt.datetime.now(dt.UTC) - dt.timedelta(hours=7)))  # fmt: skip
        await db.commit()
    await notifier.check_offline_mail(app.state.connections)
    await notifier.check_offline_mail(app.state.connections)
    assert [m.subject for m in mailer.sent] == ["allskyhub: Garten ist seit 7 Stunden offline"]


def test_message_headers() -> None:
    settings = Settings(database_url="postgresql://x@y/z", smtp_host="mail.example.org",
                        mail_from="allskyhub <noreply@allskyhub.org>")  # fmt: skip
    msg = SmtpMailer(settings)._message(Mail("a@example.org", "Betreff", "Text"))  # pyright: ignore[reportPrivateUsage]
    assert msg["From"] == "allskyhub <noreply@allskyhub.org>"
    assert msg["Auto-Submitted"] == "auto-generated"
    assert msg["Message-ID"].endswith("@allskyhub.org>")
