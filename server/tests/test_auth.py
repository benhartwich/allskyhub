"""Accounts, sessions and CSRF in the web UI."""

from __future__ import annotations

import httpx
import pytest

from allskyhub_server.auth.accounts import AccountError
from tests.helpers import PASSWORD, csrf_from, invite, make_account


async def test_healthz(client: httpx.AsyncClient) -> None:
    r = await client.get("/healthz")
    assert r.json() == {"status": "ok"}


async def test_start_page_is_public(client: httpx.AsyncClient) -> None:
    r = await client.get("/")
    assert r.status_code == 200
    assert "Der ganze Himmel" in r.text
    assert "Meine Kameras" not in r.text


async def test_camera_pages_require_login(client: httpx.AsyncClient) -> None:
    r = await client.get("/cameras/pair")
    assert r.status_code == 303
    assert r.headers["location"] == "/login?next=/cameras/pair"


async def test_invitation_creates_account_and_signs_in(client: httpx.AsyncClient) -> None:
    await make_account(client)
    r = await client.get("/")
    assert r.status_code == 200
    assert "Noch keine Kamera gekoppelt" in r.text
    assert "default-src 'self'" in r.headers["content-security-policy"]


async def test_invitation_is_single_use(
    client: httpx.AsyncClient, new_client: httpx.AsyncClient
) -> None:
    email = "once@example.org"
    token = await invite(client, email)
    page = await client.get("/invite", params={"token": token})
    assert email in page.text
    data = {"token": token, "password": PASSWORD, "password2": PASSWORD}
    r = await client.post("/invite", data={**data, "csrf_token": csrf_from(page.text)})
    assert r.status_code == 303

    assert (await new_client.get("/invite", params={"token": token})).status_code == 404
    other = await new_client.get("/login")
    r = await new_client.post("/invite", data={**data, "csrf_token": csrf_from(other.text)})
    assert r.status_code == 400
    assert "ungültig oder abgelaufen" in r.text


async def test_new_invitation_replaces_open_one(client: httpx.AsyncClient) -> None:
    first = await invite(client, "twice@example.org")
    second = await invite(client, "twice@example.org")
    assert (await client.get("/invite", params={"token": first})).status_code == 404
    assert (await client.get("/invite", params={"token": second})).status_code == 200


async def test_invitation_rejects_short_or_mismatched_password(client: httpx.AsyncClient) -> None:
    token = await invite(client, "short@example.org")
    page = await client.get("/invite", params={"token": token})
    for pw, pw2 in (("short", "short"), (PASSWORD, PASSWORD + "x")):
        r = await client.post(
            "/invite",
            data={"token": token, "password": pw, "password2": pw2,
                  "csrf_token": csrf_from(page.text)},
        )  # fmt: skip
        assert r.status_code == 400
    # Still usable after the failed attempts.
    assert (await client.get("/invite", params={"token": token})).status_code == 200


async def test_no_invitation_for_existing_account(client: httpx.AsyncClient) -> None:
    email = await make_account(client)
    with pytest.raises(AccountError, match="schon ein Konto"):
        await invite(client, email.upper())


async def test_sign_up_is_gone(client: httpx.AsyncClient) -> None:
    assert (await client.get("/signup")).status_code == 404
    login = await client.get("/login")
    assert "nur auf Einladung" in login.text
    assert "/signup" not in (await client.get("/")).text


async def test_login_logout(client: httpx.AsyncClient) -> None:
    email = await make_account(client)
    home = await client.get("/")
    r = await client.post("/logout", data={"csrf_token": csrf_from(home.text)})
    assert r.status_code == 303
    assert "Meine Kameras" not in (await client.get("/")).text

    page = await client.get("/login")
    token = csrf_from(page.text)
    bad = await client.post(
        "/login", data={"email": email, "password": "wrong password", "csrf_token": token}
    )
    assert bad.status_code == 400
    ok = await client.post(
        "/login",
        data={"email": email, "password": PASSWORD, "csrf_token": token, "next": "//evil.org"},
    )
    assert ok.status_code == 303
    assert ok.headers["location"] == "/"
    assert "Meine Kameras" in (await client.get("/")).text


async def test_post_without_csrf_is_rejected(client: httpx.AsyncClient) -> None:
    await client.get("/login")
    r = await client.post("/login", data={"email": "a@example.org", "password": "x"})
    assert r.status_code == 403


async def test_foreign_origin_is_rejected(client: httpx.AsyncClient) -> None:
    page = await client.get("/login")
    r = await client.post(
        "/login",
        data={"email": "a@example.org", "password": "x", "csrf_token": csrf_from(page.text)},
        headers={"Origin": "https://evil.example"},
    )
    assert r.status_code == 403


async def test_legal_pages_are_public(client: httpx.AsyncClient) -> None:
    imprint = await client.get("/impressum")
    assert imprint.status_code == 200
    assert "<h1>Impressum</h1>" in imprint.text
    assert "ATU73183324" in imprint.text
    privacy = await client.get("/datenschutz")
    assert privacy.status_code == 200
    assert "Datenschutzbehörde" in privacy.text
    assert "netcup GmbH" in privacy.text
    assert "Entwurf" not in privacy.text
    assert "TODO" not in privacy.text + imprint.text
    home = await client.get("/")
    assert 'href="/impressum"' in home.text
    assert 'href="/datenschutz"' in home.text
