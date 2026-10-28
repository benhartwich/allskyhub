"""Reporter accounts and sky-brightness reports by eye (app "Himmelshelligkeit melden")."""

from __future__ import annotations

import datetime as dt
import re
from urllib.parse import unquote

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from allskyhub_server.api import app_reports
from allskyhub_server.mail.send import MemoryMailer
from allskyhub_server.models import SkyReport, User
from allskyhub_server.settings import Settings
from allskyhub_sky import report
from tests.helpers import PASSWORD, csrf_from

NIGHT = dt.datetime(2026, 10, 28, 21, tzinfo=dt.UTC)  # Cygnus about 50° up at 48.1° N
DAY = dt.datetime(2026, 10, 28, 11, tzinfo=dt.UTC)


class _Turnstile:
    enabled = True
    site_key = "site"

    async def verify(self, token: str, ip: str) -> bool:
        return token == "ok"  # noqa: S105


@pytest.fixture
def signup_on(app: FastAPI, settings: Settings, monkeypatch: pytest.MonkeyPatch) -> MemoryMailer:
    monkeypatch.setattr(settings, "turnstile_site_key", "site")
    monkeypatch.setattr(settings, "turnstile_secret", "secret")
    app.state.turnstile = _Turnstile()
    mailer: MemoryMailer = app.state.mailer
    return mailer


async def _sign_up(client: httpx.AsyncClient, email: str, turnstile: str = "ok") -> httpx.Response:
    page = await client.get("/melden/konto")
    return await client.post(
        "/melden/konto",
        data={"csrf_token": csrf_from(page.text), "email": email, "password": PASSWORD,
              "privacy": "true", "cf-turnstile-response": turnstile},
    )  # fmt: skip


async def _app_login(client: httpx.AsyncClient, email: str) -> httpx.Response:
    return await client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})


async def _confirm(client: httpx.AsyncClient, mailer: MemoryMailer) -> httpx.Response:
    """Open the newest confirmation link and press its button."""
    link = re.search(r"/melden/bestaetigen\?token=(\S+)", mailer.sent[-1].body)
    assert link
    page = await client.get(link[0])
    assert page.status_code == 200
    assert "Konto bestätigen" in page.text
    return await client.post(
        "/melden/bestaetigen",
        data={"csrf_token": csrf_from(page.text), "token": unquote(link[1])},
    )


async def _reporter(client: httpx.AsyncClient, mailer: MemoryMailer, email: str) -> str:
    """Sign up and confirm; the app token."""
    assert (await _sign_up(client, email)).status_code == 200
    assert (await _confirm(client, mailer)).status_code == 200
    return (await _app_login(client, email)).json()["access_token"]


async def test_signup_is_off_without_turnstile_keys(client: httpx.AsyncClient) -> None:
    assert (await client.get("/melden/konto")).status_code == 404


async def test_signup_confirm_and_no_cameras(
    client: httpx.AsyncClient, signup_on: MemoryMailer
) -> None:
    page = await client.get("/melden/konto")
    assert "challenges.cloudflare.com" in page.headers["content-security-policy"]
    assert 'data-sitekey="site"' in page.text
    bot = await _sign_up(client, "a@example.org", turnstile="nope")
    assert bot.status_code == 400
    assert not signup_on.sent

    r = await _sign_up(client, "a@example.org")
    assert "E-Mail mit einem Link ist unterwegs" in r.text
    assert (await _app_login(client, "a@example.org")).status_code == 401  # not confirmed
    # The mail got lost: signing up again sends a fresh link, the first password stays.
    first = signup_on.sent[-1].body
    assert (await _sign_up(client, "a@example.org")).status_code == 200
    assert signup_on.sent[-1].body != first
    assert "/melden/bestaetigen" in signup_on.sent[-1].body
    old = re.search(r"/melden/bestaetigen\?token=\S+", first)
    assert old
    assert (await client.get(old[0])).status_code == 404  # replaced
    # A scanner fetching the link does not confirm; the button does, once.
    link = re.search(r"/melden/bestaetigen\?token=\S+", signup_on.sent[-1].body)
    assert link
    await client.get(link[0])
    assert (await _app_login(client, "a@example.org")).status_code == 401
    assert "Konto bestätigt" in (await _confirm(client, signup_on)).text
    assert (await client.get(link[0])).status_code == 404  # single use
    token = (await _app_login(client, "a@example.org")).json()["access_token"]
    auth = {"Authorization": f"Bearer {token}"}
    settings = (await client.get("/api/v1/account/settings", headers=auth)).json()
    assert settings["kind"] == "reporter"
    claim = await client.post("/api/v1/cameras/claim", json={"code": "ABCDEF"}, headers=auth)
    assert claim.status_code == 400
    assert "Melder-Konto" in claim.text

    # Signing up again with the address says the same and mails a hint instead.
    again = await _sign_up(client, "a@example.org")
    assert "unterwegs" in again.text
    assert "schon ein allskyhub-Konto" in signup_on.sent[-1].body


async def test_report_at_night_once_an_hour(
    app: FastAPI,
    client: httpx.AsyncClient,
    signup_on: MemoryMailer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = await _reporter(client, signup_on, "b@example.org")
    auth = {"Authorization": f"Bearer {token}"}
    where = {"lat": 48.1234, "lon": 14.4321}

    monkeypatch.setattr(app_reports, "_now", lambda: DAY)
    day = (await client.get("/api/v1/reports/chart", params=where, headers=auth)).json()
    assert day["ready"] is False
    assert "nicht dunkel genug" in day["reason"]

    monkeypatch.setattr(app_reports, "_now", lambda: NIGHT)
    chart = (await client.get("/api/v1/reports/chart", params=where, headers=auth)).json()
    assert chart["ready"] is True
    assert (chart["key"], chart["name"]) == ("Cyg", "Schwan")
    assert len(chart["stars"]) > 100
    assert chart["limits"] == list(report.CHART_LIMITS)

    body = {**where, "constellation": "Cyg", "limit_mag": 4.5, "clouds": False}
    r = await client.post("/api/v1/reports", json=body, headers=auth)
    assert r.status_code == 200, r.text
    assert r.json()["sqm"] == report.sqm_from_limit(4.5)
    assert r.json()["label"] == "Stadthimmel"
    again = await client.post("/api/v1/reports", json=body, headers=auth)
    assert again.status_code == 429
    mine = (await client.get("/api/v1/reports/mine", headers=auth)).json()
    assert mine["count"] == 1
    assert mine["next_at"] is not None

    # On the light-pollution map only from three reports by two accounts, without names.
    assert '"lat": 48.1, "lon": 14.4' not in (await client.get("/karte?layer=sqm")).text
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        owner = await db.scalar(select(User).where(User.email == "b@example.org"))
        assert owner
        other = User(email="z@example.org", password_hash="-", kind="reporter")  # noqa: S106
        db.add(other)
        await db.flush()
        for user in (owner, other):
            db.add(SkyReport(user_id=user.id, at=NIGHT, lat=48.1, lon=14.4,
                             constellation="Cyg", limit_mag=5, sqm=19.3, clouds=False))  # fmt: skip
        await db.commit()
    page = (await client.get("/karte?layer=sqm")).text
    assert '"lat": 48.1, "lon": 14.4' in page
    assert "3 Meldungen" in page
    assert "b@example.org" not in page


async def test_reports_are_checked(
    client: httpx.AsyncClient, signup_on: MemoryMailer, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = await _reporter(client, signup_on, "c@example.org")
    auth = {"Authorization": f"Bearer {token}"}
    monkeypatch.setattr(app_reports, "_now", lambda: NIGHT)
    base = {"lat": 48.1, "lon": 14.4, "clouds": False}
    low = await client.post(
        "/api/v1/reports", json={**base, "constellation": "Cru", "limit_mag": 5}, headers=auth
    )
    assert low.status_code == 409  # the Southern Cross is below the horizon in Austria
    odd = await client.post(
        "/api/v1/reports", json={**base, "constellation": "Cyg", "limit_mag": 5.3}, headers=auth
    )
    assert odd.status_code == 422
    anonymous = {**base, "constellation": "Cyg", "limit_mag": 5}
    assert (await client.post("/api/v1/reports", json=anonymous)).status_code == 401


def test_limit_to_sky_brightness() -> None:
    assert report.sqm_from_limit(6.0) == pytest.approx(20.8, abs=0.05)
    assert report.sqm_from_limit(7.0) == 22.0  # never darker than the natural sky
    assert report.sky_class(21.8) == "sehr dunkler Himmel"
    assert report.sky_class(18.0, english=True) == "city sky"


async def test_reports_and_unconfirmed_accounts_expire(
    app: FastAPI, client: httpx.AsyncClient, signup_on: MemoryMailer, settings: Settings
) -> None:
    from allskyhub_server import maintenance

    await _sign_up(client, "late@example.org")  # never confirmed
    token = await _reporter(client, signup_on, "d@example.org")
    assert token
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        user = await db.scalar(select(User).where(User.email == "d@example.org"))
        assert user
        db.add(SkyReport(user_id=user.id, at=NIGHT, lat=48.1, lon=14.4, constellation="Cyg",
                         limit_mag=5, sqm=19.3, clouds=False))  # fmt: skip
        await db.commit()
        later = NIGHT + dt.timedelta(days=366)
        await maintenance.purge(db, app.state.images, settings, now=later)
        assert await db.scalar(select(SkyReport)) is None
        emails = set(await db.scalars(select(User.email)))
        assert "d@example.org" in emails  # confirmed accounts stay
        assert "late@example.org" not in emails
