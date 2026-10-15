"""Access requests, the parts list and the first steps (web/routes_access.py)."""

from __future__ import annotations

import datetime as dt
import re

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from allskyhub_server.auth import access_requests
from allskyhub_server.mail.send import MemoryMailer
from allskyhub_server.models import AccessRequest
from allskyhub_server.settings import Settings
from tests.helpers import PASSWORD, csrf_from, make_account
from tests.test_admin import FakeGitHub


async def _ask(client: httpx.AsyncClient, **fields: str) -> httpx.Response:
    page = await client.get("/zugang")
    assert page.status_code == 200
    data = {
        "name": "Erika Muster",
        "email": "erika@example.org",
        "stage": "building",
        "place": "Mühlviertel",
        "message": "Gehäuse ist bestellt.",
        "privacy": "true",
        "csrf_token": csrf_from(page.text),
    }
    data.update(fields)
    return await client.post("/zugang", data=data)


async def _requests(app: FastAPI) -> list[AccessRequest]:
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        return list(await db.scalars(select(AccessRequest)))


async def test_a_request_reaches_the_admins(
    app: FastAPI, client: httpx.AsyncClient, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "admin_emails", "ben@example.org")
    r = await _ask(client)
    assert r.status_code == 200
    assert "Danke!" in r.text
    [saved] = await _requests(app)
    assert (saved.name, saved.stage, saved.place) == ("Erika Muster", "building", "Mühlviertel")
    mailer: MemoryMailer = app.state.mailer
    [mail] = mailer.sent
    assert mail.to == "ben@example.org"
    assert "erika@example.org" in mail.body
    assert "Ich baue gerade eine" in mail.body

    # Asking again, or for an address with an account, looks the same and stores nothing.
    again = await _ask(client)
    assert "Danke!" in again.text
    assert len(await _requests(app)) == 1
    assert len(mailer.sent) == 1


async def test_existing_accounts_cannot_be_looked_up(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    email = await make_account(client)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as anon:
        r = await _ask(anon, email=email)
    assert r.status_code == 200
    assert "Danke!" in r.text
    assert await _requests(app) == []


async def test_bots_and_missing_consent(app: FastAPI, client: httpx.AsyncClient) -> None:
    assert (await _ask(client, privacy="")).status_code == 400
    assert "Danke!" in (await _ask(client, website="http://spam.example")).text
    assert (await _ask(client, stage="other")).status_code == 400
    assert await _requests(app) == []


async def test_requests_are_rate_limited(app: FastAPI, client: httpx.AsyncClient) -> None:
    for i in range(3):
        assert (await _ask(client, email=f"a{i}@example.org")).status_code == 200
    assert (await _ask(client, email="a9@example.org")).status_code == 429


async def test_admin_invites_and_the_link_works(
    app: FastAPI, client: httpx.AsyncClient, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _ask(client, email="new@example.org")
    await _ask(client, email="no@example.org")
    admin = await make_account(client)
    monkeypatch.setattr(settings, "admin_emails", admin)
    monkeypatch.setattr(app.state, "github", FakeGitHub())
    page = await client.get("/admin")
    assert "new@example.org" in page.text
    assert "Ich baue gerade eine" in page.text
    by_email = {r.email: r for r in await _requests(app)}

    mailer: MemoryMailer = app.state.mailer
    mailer.sent.clear()
    r = await client.post(
        f"/admin/access/{by_email['new@example.org'].id}/invite",
        data={"csrf_token": csrf_from(page.text)},
    )
    assert "Einladung an new@example.org ist raus." in r.text
    [mail] = mailer.sent
    assert mail.to == "new@example.org"
    assert "/start" in mail.body
    link = re.search(r"http\S+/invite\?token=\S+", mail.body)
    assert link is not None

    r = await client.post(
        f"/admin/access/{by_email['no@example.org'].id}/decline",
        data={"csrf_token": csrf_from(page.text)},
    )
    assert "abgelehnt" in r.text
    assert len(mailer.sent) == 1
    assert "no@example.org" not in (await client.get("/admin")).text
    # Twice is refused.
    r = await client.post(
        f"/admin/access/{by_email['no@example.org'].id}/decline",
        data={"csrf_token": csrf_from(page.text)},
    )
    assert r.status_code == 400

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as invited:
        token = link.group(0).split("token=", 1)[1]
        form = await invited.get("/invite", params={"token": token})
        assert form.status_code == 200
        r = await invited.post(
            "/invite",
            data={
                "token": token,
                "password": PASSWORD,
                "password2": PASSWORD,
                "csrf_token": csrf_from(form.text),
            },
        )
        assert r.status_code == 303
        home = await invited.get("/")
        assert 'href="/start"' in home.text


async def test_only_admins_decide(app: FastAPI, client: httpx.AsyncClient) -> None:
    await _ask(client)
    [saved] = await _requests(app)
    await make_account(client)
    page = await client.get("/")
    r = await client.post(
        f"/admin/access/{saved.id}/invite", data={"csrf_token": csrf_from(page.text)}
    )
    assert r.status_code == 404


async def test_old_requests_are_deleted(app: FastAPI, client: httpx.AsyncClient) -> None:
    await _ask(client)
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    now = dt.datetime.now(dt.UTC)
    async with maker() as db:
        await access_requests.purge(db, now + dt.timedelta(days=179))
        await db.commit()
    assert len(await _requests(app)) == 1
    async with maker() as db:
        await access_requests.purge(db, now + dt.timedelta(days=181))
        await db.commit()
    assert await _requests(app) == []


async def test_hardware_and_first_steps(app: FastAPI, client: httpx.AsyncClient) -> None:
    page = await client.get("/hardware")
    assert page.status_code == 200
    assert "liquidart-shop.at/de/Allsky-Kamera-Body.html" in page.text
    assert "B08CVN1RFQ" in page.text  # the pole Daniel recommends
    assert "tag=" not in page.text  # plain links, no affiliate tags
    assert (await client.get("/start")).status_code in (303, 307)
    await make_account(client)
    for step in ("hardware", "zusammenbau", "image", "koppeln"):
        r = await client.get("/start", params={"schritt": step})
        assert r.status_code == 200
        assert 'aria-current="step"' in r.text
    assert (await client.get("/start", params={"schritt": "x"})).status_code == 404


async def test_guide_for_existing_allsky_cameras(client: httpx.AsyncClient) -> None:
    de = await client.get("/allsky")
    assert de.status_code == 200
    assert '<html lang="de">' in de.text
    assert "allskyhub neben Allsky" in de.text
    assert "import-allsky" in de.text
    en = await client.get("/allsky", params={"lang": "en"})
    assert '<html lang="en">' in en.text
    assert "allskyhub next to Allsky" in en.text
    assert "Conflicts=allsky.service" in en.text
    assert 'href="/allsky"' in (await client.get("/hardware")).text


async def test_voucher_and_image_download(app: FastAPI, client: httpx.AsyncClient) -> None:
    page = (await client.get("/hardware")).text
    assert "Daniel Nimmervoll" in page
    assert "<code>allsky</code>" in page
    r = await client.get("/download/image", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"].endswith("/agent-v0.1.3/allskyhub-0.1.3.img.xz")
    await make_account(client)
    step = (await client.get("/start", params={"schritt": "image"})).text
    assert "Image 0.1.3 herunterladen (884 MB)" in step
    assert "<code>allsky</code>" in (await client.get("/start")).text


async def test_without_github_the_download_goes_to_the_releases() -> None:
    from allskyhub_server.web.image_download import LatestImage

    down = LatestImage(
        "benhartwich/allskyhub",
        httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(503))),
    )
    assert await down.get() is None
    assert down.releases_url == "https://github.com/benhartwich/allskyhub/releases/latest"
