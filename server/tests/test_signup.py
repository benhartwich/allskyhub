"""Owner sign-up on /konto/anlegen and the warning about many failed sign-ins
(Benjamin, 2026-11-05: "dann B, mit passwort vergessen und brute force detection")."""

from __future__ import annotations

import asyncio
import re
from urllib.parse import unquote

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from allskyhub_server.mail.send import MemoryMailer
from allskyhub_server.models import User
from allskyhub_server.settings import Settings
from tests.helpers import PASSWORD, csrf_from


class _Turnstile:
    enabled = True
    site_key = "site"

    async def verify(self, token: str, ip: str) -> bool:
        return token == "ok"  # noqa: S105


@pytest.fixture
def open_signup(app: FastAPI, settings: Settings, monkeypatch: pytest.MonkeyPatch) -> MemoryMailer:
    monkeypatch.setattr(settings, "open_signup", True)
    app.state.turnstile = _Turnstile()
    mailer: MemoryMailer = app.state.mailer
    return mailer


async def _sign_up(
    client: httpx.AsyncClient, email: str, turnstile: str = "ok", password: str = PASSWORD
) -> httpx.Response:
    page = await client.get("/konto/anlegen")
    return await client.post(
        "/konto/anlegen",
        data={"csrf_token": csrf_from(page.text), "email": email, "password": password,
              "terms": "true", "cf-turnstile-response": turnstile},
    )  # fmt: skip


async def _confirm(client: httpx.AsyncClient, mailer: MemoryMailer) -> httpx.Response:
    link = re.search(r"/konto/bestaetigen\?token=([^&\s]+)\S*", mailer.sent[-1].body)
    assert link
    page = await client.get(link[0])
    assert page.status_code == 200
    data = {"csrf_token": csrf_from(page.text), "token": unquote(link[1])}
    if weiter := re.search(r'name="weiter" value="([^"]+)"', page.text):
        data["weiter"] = weiter[1]
    return await client.post("/konto/bestaetigen", data=data)


async def test_off_until_switched_on(app: FastAPI, client: httpx.AsyncClient) -> None:
    assert (await client.get("/konto/anlegen")).status_code == 404
    login = await client.get("/login")
    assert "/zugang" in login.text
    assert "/konto/anlegen" not in login.text


async def test_sign_up_confirm_and_sign_in(
    app: FastAPI, client: httpx.AsyncClient, new_client: httpx.AsyncClient,
    open_signup: MemoryMailer,
) -> None:  # fmt: skip
    assert "/konto/anlegen" in (await client.get("/login")).text
    assert (await _sign_up(client, "neu@example.org", turnstile="nope")).status_code == 400
    assert not open_signup.sent
    short = await _sign_up(client, "neu@example.org", password="kurz")  # noqa: S106
    assert "mindestens" in short.text
    assert (await _sign_up(client, "Neu@Example.org")).status_code == 200
    assert "E-Mail mit einem Link ist unterwegs" in (await _sign_up(client, "neu@example.org")).text
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        user = await db.scalar(select(User).where(User.email == "neu@example.org"))
    assert user is not None
    assert (user.kind, user.is_active) == ("owner", False)
    # Not before the link.
    r = await new_client.post(
        "/api/v1/auth/login", json={"email": "neu@example.org", "password": PASSWORD}
    )
    assert r.status_code == 401
    done = await _confirm(client, open_signup)
    assert done.status_code == 303
    assert done.headers["location"] == "/start"
    r = await new_client.post(
        "/api/v1/auth/login", json={"email": "neu@example.org", "password": PASSWORD}
    )
    assert r.status_code == 200
    # Again: the page says the same, the mail says there is an account.
    assert (await _sign_up(new_client, "neu@example.org")).status_code == 200
    assert "schon ein allskyhub-Konto" in open_signup.sent[-1].body
    # A used link is gone.
    link = re.search(r"/konto/bestaetigen\?token=\S+", open_signup.sent[-3].body)
    assert link
    assert (await client.get(link[0])).status_code == 404


async def test_many_wrong_passwords_warn_the_owner(
    app: FastAPI, client: httpx.AsyncClient, new_client: httpx.AsyncClient,
    open_signup: MemoryMailer, settings: Settings, monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    monkeypatch.setattr(settings, "trust_proxy_headers", True)
    await _sign_up(client, "ziel@example.org")
    await _confirm(client, open_signup)
    sent = len(open_signup.sent)

    async def attempt(ip: str, email: str = "ziel@example.org") -> int:
        r = await new_client.post(
            "/api/v1/auth/login", json={"email": email, "password": "falsch-falsch"},
            headers={"X-Real-IP": ip},
        )  # fmt: skip
        return r.status_code

    # Typos from one place: blocked there for a while, but no alarm.
    codes = [await attempt("198.51.100.7") for _ in range(11)]
    assert codes == [401] * 10 + [429]
    await asyncio.sleep(0.1)
    assert len(open_signup.sent) == sent
    # Guessing from many places: the account's own limit trips and its owner hears of it.
    last = 0
    for i in range(10):
        for _ in range(10):
            last = await attempt(f"203.0.113.{i}")
    assert last == 429
    for _ in range(50):
        if len(open_signup.sent) > sent:
            break
        await asyncio.sleep(0.02)
    for i in range(5):
        await attempt(f"192.0.2.{i}")
    await asyncio.sleep(0.1)
    warned = [m for m in open_signup.sent[sent:] if "viele falsche Anmeldungen" in m.subject]
    assert len(warned) == 1  # once, not for every blocked try
    assert warned[0].to == "ziel@example.org"
    assert "/password/forgot" in warned[0].body
    # An address without an account: the same 429, and no mail.
    sent = len(open_signup.sent)
    for i in range(10):
        for _ in range(10):
            last = await attempt(f"203.0.113.{100 + i}", "niemand@example.org")
    assert await attempt("192.0.2.200", "niemand@example.org") == 429
    await asyncio.sleep(0.1)
    assert len(open_signup.sent) == sent


async def test_forms_need_their_csrf_token(
    client: httpx.AsyncClient, open_signup: MemoryMailer
) -> None:
    """Else a page could confirm an attacker's account in someone's browser and sign them
    in to it (allsky's review)."""
    data = {"email": "a@example.org", "password": PASSWORD, "terms": "true",
            "cf-turnstile-response": "ok"}  # fmt: skip
    assert (await client.post("/konto/anlegen", data=data)).status_code == 403
    r = await client.post("/konto/bestaetigen", data={"token": "x" * 40})
    assert r.status_code == 403
    assert not open_signup.sent


async def test_one_address_gets_few_mails(
    client: httpx.AsyncClient, open_signup: MemoryMailer, settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    monkeypatch.setattr(settings, "trust_proxy_headers", True)
    for i in range(5):
        page = await client.get("/konto/anlegen")
        r = await client.post(
            "/konto/anlegen",
            data={"csrf_token": csrf_from(page.text), "email": "opfer@example.org",
                  "password": PASSWORD, "terms": "true", "cf-turnstile-response": "ok"},
            headers={"X-Real-IP": f"203.0.113.{i}"},
        )  # fmt: skip
        assert "E-Mail mit einem Link ist unterwegs" in r.text  # the page always says so
    assert len([m for m in open_signup.sent if m.to == "opfer@example.org"]) == 3


async def test_from_mitmachen_to_kamera_verbinden(
    client: httpx.AsyncClient, open_signup: MemoryMailer
) -> None:
    """Benjamin, 2026-11-05: who signs up to join SharedSky lands on the question of the
    software (start_choose.html), not on the first steps of building a camera."""
    assert "/konto/anlegen?weiter=sharedsky" in (await client.get("/mitmachen")).text
    page = await client.get("/konto/anlegen", params={"weiter": "sharedsky"})
    r = await client.post(
        "/konto/anlegen",
        data={"csrf_token": csrf_from(page.text), "email": "station@example.org",
              "password": PASSWORD, "terms": "true", "cf-turnstile-response": "ok",
              "weiter": "sharedsky"},
    )  # fmt: skip
    assert r.status_code == 200
    assert "weiter=sharedsky" in open_signup.sent[-1].body
    done = await _confirm(client, open_signup)
    assert done.headers["location"] == "/start?kamera=ja"
    # With open sign-up, the app's "Zugang anfragen" (older versions) leads here too.
    r = await client.get("/zugang")
    assert r.headers["location"] == "/konto/anlegen"


async def test_an_unknown_target_leads_to_the_first_steps(
    client: httpx.AsyncClient, open_signup: MemoryMailer
) -> None:
    page = await client.get("/konto/anlegen", params={"weiter": "https://evil.example"})
    assert "evil" not in page.text
    await client.post(
        "/konto/anlegen",
        data={"csrf_token": csrf_from(page.text), "email": "x@example.org",
              "password": PASSWORD, "terms": "true", "cf-turnstile-response": "ok",
              "weiter": "https://evil.example"},
    )  # fmt: skip
    assert "evil" not in open_signup.sent[-1].body
    done = await _confirm(client, open_signup)
    assert done.headers["location"] == "/start"
