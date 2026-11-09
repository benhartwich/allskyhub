"""SharedSky's pages, messages and mails in English too (Benjamin, 2026-11-05: "ja
zweisprachig!"): ?lang=en or the cookie (web/lang.py), User.lang for mails."""

from __future__ import annotations

import io
import re
from urllib.parse import unquote

import httpx
import pytest
from fastapi import FastAPI
from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from allskyhub_server.devices import image_url
from allskyhub_server.i18n import page_english
from allskyhub_server.mail.send import MemoryMailer
from allskyhub_server.models import User
from allskyhub_server.settings import Settings
from tests.helpers import PASSWORD, csrf_from, make_account


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


def english(page: httpx.Response, *sentences: str) -> None:
    assert '<html lang="en">' in page.text
    for sentence in sentences:
        assert sentence in page.text, sentence


def test_the_language_of_a_page() -> None:
    assert page_english("lang=en", None)
    assert not page_english("lang=de", "ash_lang=en")  # the query wins
    assert page_english("", "theme=red; ash_lang=en")
    assert not page_english("", None)


async def test_public_pages_in_english(
    client: httpx.AsyncClient, open_signup: MemoryMailer
) -> None:
    english(
        await client.get("/mitmachen", params={"lang": "en"}),
        "Your all-sky camera sees more when it isn't alone",
        "Create a free account",
        "Terms in short",
    )
    # The cookie keeps it from here on.
    english(await client.get("/entwickler/connect"), "Report the station", "Test mode")
    english(await client.get("/konto/anlegen"), "Create an account", "Email address")
    english(await client.get("/login"), "Forgot your password?", "Create one for free")
    english(await client.get("/password/forgot"), "Send me a link")
    english(await client.get("/password/reset", params={"token": "x" * 20}), "Ask for a new link")
    english(await client.get("/konto/bestaetigen", params={"token": "x" * 20}), "Link not valid")
    german = await client.get("/mitmachen", params={"lang": "de"})
    assert '<html lang="de">' in german.text
    assert "Deine Allsky-Kamera sieht mehr" in german.text


async def test_sign_up_in_english(
    app: FastAPI, client: httpx.AsyncClient, open_signup: MemoryMailer
) -> None:
    page = await client.get("/konto/anlegen", params={"lang": "en", "weiter": "sharedsky"})
    form = {"csrf_token": csrf_from(page.text), "email": "en@example.org", "password": "kurz",
            "terms": "true", "cf-turnstile-response": "ok", "weiter": "sharedsky"}  # fmt: skip
    short = await client.post("/konto/anlegen", data=form)
    assert "The password needs at least 10 characters." in short.text
    assert 'name="weiter" value="sharedsky"' in short.text  # kept for the next try
    done = await client.post("/konto/anlegen", data=form | {"password": PASSWORD})
    english(done, "Almost done")
    mail = open_signup.sent[-1]
    assert mail.subject == "allskyhub: your account"
    assert "this link confirms your allskyhub account" in mail.body
    link = re.search(r"/konto/bestaetigen\?token=([^&\s]+)\S*", mail.body)
    assert link
    assert "lang=en" in link[0]
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        user = await db.scalar(select(User).where(User.email == "en@example.org"))
    assert user is not None
    assert user.lang == "en"
    confirm = await client.get(link[0])
    english(confirm, "One more click")
    r = await client.post(
        "/konto/bestaetigen",
        data={"csrf_token": csrf_from(confirm.text), "token": unquote(link[1]),
              "weiter": "sharedsky"},
    )  # fmt: skip
    assert r.headers["location"] == "/start?kamera=ja"
    english(await client.get("/start?kamera=ja"), "Which software does it run?")


async def test_a_failed_try_keeps_the_way_to_sharedsky(
    client: httpx.AsyncClient, open_signup: MemoryMailer
) -> None:
    """allsky's review of #404: every way back to the form keeps ``weiter``."""
    page = await client.get("/konto/anlegen", params={"weiter": "sharedsky"})
    form = {"csrf_token": csrf_from(page.text), "email": "again@example.org",
            "password": PASSWORD, "cf-turnstile-response": "nope",
            "weiter": "sharedsky"}  # fmt: skip
    for broken in ({"terms": "true"}, {}):  # the bot check, then the terms
        failed = await client.post("/konto/anlegen", data=form | broken)
        assert failed.status_code == 400
        assert 'name="weiter" value="sharedsky"' in failed.text
    await client.post(
        "/konto/anlegen", data=form | {"terms": "true", "cf-turnstile-response": "ok"}
    )
    link = re.search(r"/konto/bestaetigen\?token=([^&\s]+)\S*", open_signup.sent[-1].body)
    assert link
    assert "weiter=sharedsky" in link[0]
    confirm = await client.get(link[0])
    r = await client.post(
        "/konto/bestaetigen",
        data={"csrf_token": csrf_from(confirm.text), "token": unquote(link[1]),
              "weiter": "sharedsky"},
    )  # fmt: skip
    assert r.headers["location"] == "/start?kamera=ja"


async def test_wizard_and_stations_in_english(
    app: FastAPI,
    client: httpx.AsyncClient,
    new_client: httpx.AsyncClient,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = await make_account(client)
    switched = await client.get("/sprache/en", params={"next": "/start"})
    assert switched.status_code == 303
    english(await client.get("/start"), "Do you already have an all-sky camera?")
    english(await client.get("/start?kamera=ja&software=allsky"), "Join SharedSky",
            "next to Allsky")  # fmt: skip
    english(await client.get("/start?kamera=nein"), "Getting started", "I need hardware")
    form = await client.get("/stationen/neu", params={"software": "image-url"})
    english(form, "Connect a camera", "Address of the latest image")
    data = {"csrf_token": csrf_from(form.text), "software": "image-url", "terms": "true",
            "name": "Hilltop", "url": "https://cam.example.org/allsky/image.jpg",
            "latitude": "48.3", "longitude": "4.29"}  # fmt: skip
    missing = await client.post("/stationen/neu", data=data)
    assert "Please give a proof" in missing.text
    bad = await client.post("/stationen/neu", data=data | {"url": "http://x.example:8080/a.jpg",
                                                            "proof_text": "x"})  # fmt: skip
    assert "port 80 or 443" in bad.text
    out = io.BytesIO()
    Image.new("RGB", (64, 48)).save(out, "JPEG")
    asked = await client.post(
        "/stationen/neu", data=data | {"proof_text": "My Allsky page"}, follow_redirects=True
    )
    english(asked, "waiting for a check", "Remove station")
    station_id = asked.url.path.rsplit("/", 1)[1]
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        user = await db.scalar(select(User).where(User.email == owner))
    assert user is not None
    assert user.lang == "en"  # the switch keeps it for mails

    # The admin's page stays German; the owner's mail follows the owner's language.
    admin = await make_account(new_client)
    monkeypatch.setattr(settings, "admin_emails", admin)

    async def resolve(host: str, port: int) -> list[str]:
        return ["93.184.216.34"]

    app.state.image_fetcher = image_url.Fetcher(
        resolve=resolve,
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, content=out.getvalue(),
                                     headers={"content-type": "image/jpeg"})
        ),
    )  # fmt: skip
    detail = await new_client.get(f"/admin/stationen/{station_id}")
    assert '<html lang="de">' in detail.text
    mailer: MemoryMailer = app.state.mailer
    sent = len(mailer.sent)
    await new_client.post(
        f"/admin/stationen/{station_id}/ablehnen",
        data={"csrf_token": csrf_from(detail.text), "password": PASSWORD,
              "reason": "Bitte einen Screenshot."},
    )  # fmt: skip
    mail = next(m for m in mailer.sent[sent:] if m.to == owner)
    assert mail.subject == "SharedSky: Hilltop declined"
    assert 'your station "Hilltop" was not approved.' in mail.body
    assert "?lang=en" in mail.body
